"""Meeting recording service — DB-aware orchestration around audio
upload, transcription state machine, and transcript persistence.

States:
    Uploaded    — file stored, ready to transcribe
    Transcribing — Whisper pipeline running
    Transcribed  — segments written, ready to consume
    Failed       — pipeline errored; ``error_message`` is populated

All public methods are async and accept a ``AsyncSession`` so the
router layer stays thin. The service uses its own ``asyncio.Semaphore``
to ensure at most one transcription pipeline runs at a time.

.. note::
   2026-06-02: 转写进度持久化修复。

   旧实现把 ``progress_callback`` 缓存到 ``_latest_progress`` 字典，
   但**不主动写库**——所以 UI 一直卡在 1% 直到转写完才一次性跳到 80-100%。
   修复：用 ``asyncio.run_coroutine_threadsafe`` 把进度写库任务调度到
   主事件循环，Whisper 推理线程不阻塞，进度每 ~200ms 真实刷一次。
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, BinaryIO

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.meeting import (
    MtgMeetingRecording,
    MtgMeetingTranscript,
)
from app.services.meeting.audio_storage import (
    build_storage_path,
    delete_recording_file,
    save_recording_file,
)
from app.services.meeting.transcription_service import (
    TranscriptionError,
    TranscriptionService,
)

logger = logging.getLogger(__name__)

# 模块级串行信号量 — 同进程内至多 1 个并发转写任务
_GLOBAL_TRANSCRIPTION_SEMAPHORE = asyncio.Semaphore(1)


class MeetingRecordingService:
    """Stateful façade for meeting recording lifecycle.

    Instances are cheap to create; the heavy state (Whisper model,
    semaphore) lives at module/class level so it survives across
    requests within a worker process.
    """

    def __init__(self) -> None:
        # 全局并发上限由配置控制；保留服务级锁作为兼容占位
        self._semaphore = _GLOBAL_TRANSCRIPTION_SEMAPHORE
        # 最新进度缓存（key=recording_id），供转写线程在段间安全地写库
        self._latest_progress: dict[uuid.UUID, int] = {}
        # 主事件循环引用：在第一次 transcribe 任务调度时捕获，
        # 用于把 progress_callback 从 worker thread 桥接到主 async loop。
        self._main_loop: asyncio.AbstractEventLoop | None = None
        # TranscriptionService 在运行时才需要；按任务创建以读取当前 DB 设置。
        self._transcriber: TranscriptionService | None = None
        # 当前任务使用的语言，供后续写 transcript 复用。
        self._current_language: str | None = None

    # ── 1. create recording ─────────────────────────────────────────────

    async def create_recording(
        self,
        db: AsyncSession,
        *,
        meeting_id: uuid.UUID,
        uploader_id: uuid.UUID,
        file_obj: BinaryIO,
        file_name: str,
        mime_type: str | None,
        max_bytes: int,
    ) -> MtgMeetingRecording:
        """Persist a new recording in ``Uploaded`` state and stream the
        file to disk. Caller is responsible for kicking off the
        background transcription task."""
        recording_id = uuid.uuid4()
        storage_path = build_storage_path(str(recording_id), file_name)

        # 1) stream to disk (raises ValueError on size cap)
        save_recording_file(file_obj, storage_path, max_bytes=max_bytes)

        # 2) probe duration
        try:
            duration = TranscriptionService.probe_duration_seconds(storage_path)
        except Exception:  # noqa: BLE001
            duration = -1
        size = 0
        try:
            from app.services.meeting.audio_storage import absolute_path
            size = absolute_path(storage_path).stat().st_size
        except OSError:
            size = 0

        # 录音元数据使用环境默认值；run_transcription 时会按当前 DB 设置重新解析。
        from app.services.meeting.asr_settings_service import MtgAsrSettingsService

        default_settings = MtgAsrSettingsService._env_defaults()
        recording = MtgMeetingRecording(
            id=recording_id,
            meeting_id=meeting_id,
            uploader_id=uploader_id,
            file_name=file_name,
            file_size=size,
            duration_seconds=duration if duration and duration > 0 else None,
            mime_type=mime_type,
            storage_path=storage_path,
            status="Uploaded",
            progress_pct=0,
            model_size=default_settings.get("model"),
            language=default_settings.get("language") or None,
            is_deleted=False,
        )
        db.add(recording)
        await db.commit()

        # Re-query to honor the "no db.refresh" rule
        return await self.get_recording_detail(db, recording.id)  # type: ignore[reportReturnType]  类型修复

    # ── 2. background transcription ─────────────────────────────────────

    async def run_transcription(
        self,
        recording_id: uuid.UUID,
    ) -> None:
        """Heavy work: fetch file, run Whisper, write transcripts.

        Designed to be invoked from ``BackgroundTasks``.

        会话生命周期（2026-06-02 修复：长 ffmpeg+Whisper 任务期间 asyncpg
        连接被 server/pool 关闭 → 状态卡在 Transcribing 永远不更新）：

            1) 短会话 #1：状态检查 + 切到 Transcribing + 取 storage_path，**然后关闭**
            2) 跑 ffmpeg + Whisper（不持任何 DB 会话，允许任意时长）
            3) 短会话 #2：写 transcripts + LLM 润色 + 切到 Transcribed

        这样无论转写任务跑 3 分钟还是 30 分钟，DB 连接都不会被长任务
        拖到 idle timeout。
        """
        # Late import to avoid hard dependency at module import
        from app.database import AsyncSessionLocal as SessionLocal

        # ── 0) 捕获主事件循环：供 progress_callback 跨线程写库 ──
        self._main_loop = asyncio.get_running_loop()

        # ── 1) 短会话：状态切到 Transcribing + 拿 storage_path ────────
        storage_path: str | None = None
        async with SessionLocal() as db:
            current_status = (
                await db.execute(
                    select(MtgMeetingRecording.status).where(MtgMeetingRecording.id == recording_id)
                )
            ).scalar_one_or_none()
            if current_status is None:
                logger.warning("run_transcription: recording %s missing", recording_id)
                return
            if current_status not in {"Uploaded", "Failed"}:
                logger.info(
                    "run_transcription: skip recording %s in status %s",
                    recording_id,
                    current_status,
                )
                return

            await db.execute(
                update(MtgMeetingRecording)
                .where(MtgMeetingRecording.id == recording_id)
                .values(
                    status="Transcribing",
                    progress_pct=1,
                    error_message=None,
                )
            )
            await db.commit()

            storage_path = (
                await db.execute(
                    select(MtgMeetingRecording.storage_path).where(MtgMeetingRecording.id == recording_id)
                )
            ).scalar_one_or_none()
        # ←── 会话 #1 关闭，连接归还 pool，不再被长任务持有

        if not storage_path:
            async with SessionLocal() as db:
                await self._mark_failed(db, recording_id, "storage_path missing")
            return

        # ── 2) 创建转写服务（读取当前 DB ASR 设置）后关闭会话，再跑长任务
        async with SessionLocal() as settings_db:
            self._transcriber = await TranscriptionService.create(settings_db)
            self._current_language = self._transcriber._language

        # ── 3) 长任务：ffmpeg + Whisper（无 DB 会话，连接安全归还 pool）──
        try:
            async with self._semaphore:
                if self._transcriber is None:
                    raise TranscriptionError("transcription service not initialized")
                segments = await self._transcriber.transcribe(
                    storage_path,
                    progress_callback=lambda pct: self._update_progress_sync(
                        recording_id, pct
                    ),
                )
        except TranscriptionError as exc:
            logger.warning("transcription pipeline error: %s", exc)
            async with SessionLocal() as db:
                await self._mark_failed(db, recording_id, str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("transcription crashed: %s", exc)
            async with SessionLocal() as db:
                await self._mark_failed(db, recording_id, f"unexpected: {exc}")
            return

        from app.services.meeting.transcript_refinement_service import merge_whisper_segments
        segments = merge_whisper_segments(segments)

        # ── 3) 短会话 #2：写 transcripts + LLM 润色 + 切到 Transcribed ─
        async with SessionLocal() as db:
            try:
                # 3a) 清空旧 transcript 并写入新段落
                await self._replace_transcripts(db, recording_id, segments)

                # 3b) LLM 错别字修复 + 顺句 + 加标点 + 去重 + 简繁转换
                # 改用新路径 apply_refinement_to_recording：含 prompt "步骤 0 去重" +
                # 章节重建（应用最新的已简化文本）+ 简繁转换三级管道
                # （zhconv → 港台用词 → 字符级 fallback）。
                # 不再使用旧的 _cluster_by_pause 路径（PAUSE_THRESHOLD=2.0
                # 会导致过度分段，见 2026-06-02 修复）。
                # 注意：apply_refinement_to_recording 内部会再调一次
                # apply_chapters_and_keywords，所以这里不再单独调。
                from app.services.meeting.transcript_refinement_service import (
                    apply_refinement_to_recording,
                )
                n = await apply_refinement_to_recording(db, recording_id)
                logger.info(
                    "auto-refined %d segments for %s after transcription",
                    n, recording_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("post-transcript write failed: %s", exc)
                # 落库失败也要把状态切到 Transcribed（transcripts 已写入），
                # 不然 UI 永远卡在 Transcribing。
                final_progress = self._latest_progress.pop(recording_id, 100)
                try:
                    await db.rollback()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    await db.execute(
                        update(MtgMeetingRecording)
                        .where(MtgMeetingRecording.id == recording_id)
                        .values(
                            status="Transcribed",
                            progress_pct=max(80, min(100, int(final_progress))),
                            error_message=f"refinement failed: {exc}",
                        )
                    )
                    await db.commit()
                except Exception:  # noqa: BLE001
                    logger.exception("post-transcript final-status write failed")
                return

            final_progress = self._latest_progress.pop(recording_id, 100)
            await db.execute(
                update(MtgMeetingRecording)
                .where(MtgMeetingRecording.id == recording_id)
                .values(
                    status="Transcribed",
                    progress_pct=max(80, min(100, int(final_progress))),
                    error_message=None,
                )
            )
            await db.commit()

    def _update_progress_sync(self, recording_id: uuid.UUID, pct: int) -> None:
        """Best-effort progress update that actually writes to DB.

        Called from:
        - Whisper worker thread (local mode) → bridge via run_coroutine_threadsafe
        - async remote path / heartbeat on the main loop → create_task
        """
        pct = max(0, min(100, int(pct)))
        # 写本地缓存（最终回退用）
        self._latest_progress[recording_id] = pct
        # 拿到主事件循环引用
        try:
            loop = self._main_loop
        except AttributeError:
            loop = None
        if loop is None or loop.is_closed():
            # 没有可用 loop：仅缓存，等转写完一次性写
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        try:
            if running is loop:
                # Already on the main loop (remote ASR / heartbeat)
                loop.create_task(self._update_progress(recording_id, pct))
            else:
                # Worker thread → schedule onto main loop
                asyncio.run_coroutine_threadsafe(
                    self._update_progress(recording_id, pct), loop
                )
        except RuntimeError:
            # loop 关闭中，忽略
            pass

    async def _update_progress(self, recording_id: uuid.UUID, pct: int) -> None:
        from app.database import AsyncSessionLocal as SessionLocal

        async with SessionLocal() as db:
            await db.execute(
                update(MtgMeetingRecording)
                .where(MtgMeetingRecording.id == recording_id)
                .values(progress_pct=max(0, min(100, int(pct))))
            )
            await db.commit()

    async def _mark_failed(
        self, db: AsyncSession, recording_id: uuid.UUID, message: str
    ) -> None:
        await db.execute(
            update(MtgMeetingRecording)
            .where(MtgMeetingRecording.id == recording_id)
            .values(
                status="Failed",
                error_message=message[:2000],
                progress_pct=0,
            )
        )
        await db.commit()

    async def _replace_transcripts(
        self,
        db: AsyncSession,
        recording_id: uuid.UUID,
        segments: list[Any],
    ) -> None:
        # 软删旧 segments（避免硬删 → 触发 selectinload 反复读取）
        from sqlalchemy import delete as sql_delete  # local alias to avoid shadow

        await db.execute(
            sql_delete(MtgMeetingTranscript)
            .where(MtgMeetingTranscript.recording_id == recording_id)
        )
        await db.flush()

        from app.services.meeting.transcript_refinement_service import (
            _simplify_chinese,
        )

        for idx, seg in enumerate(segments):
            db.add(
                MtgMeetingTranscript(
                    id=uuid.uuid4(),
                    recording_id=recording_id,
                    segment_index=idx,
                    start_time=float(seg.start),
                    end_time=float(seg.end),
                    speaker_label=None,
                    text=_simplify_chinese(str(seg.text or "").strip()),
                    confidence=seg.confidence,
                    language=self._current_language,
                )
            )
        await db.commit()

    # ── 3. queries ───────────────────────────────────────────────────────

    async def get_recording_detail(
        self, db: AsyncSession, recording_id: uuid.UUID
    ) -> MtgMeetingRecording | None:
        # 不 selectinload transcripts —— 避免触发其他无关 ORM mapper
        # 初始化失败 (ProjectRole → ProjectMember 等)。需要 transcripts
        # 的端点请改用 get_full_transcript。
        stmt = (
            select(MtgMeetingRecording)
            .where(MtgMeetingRecording.is_deleted == False)  # noqa: E712  SQLAlchemy 查询表达式
            .where(MtgMeetingRecording.id == recording_id)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def list_recordings(
        self,
        db: AsyncSession,
        *,
        meeting_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
    ) -> list[MtgMeetingRecording]:
        stmt = (
            select(MtgMeetingRecording)
            .where(MtgMeetingRecording.is_deleted == False)  # noqa: E712  SQLAlchemy 查询表达式
            .where(MtgMeetingRecording.meeting_id == meeting_id)
            .order_by(MtgMeetingRecording.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if status:
            stmt = stmt.where(MtgMeetingRecording.status == status)
        result = await db.execute(stmt)
        return list(result.scalars().unique().all())

    async def get_full_transcript(
        self,
        db: AsyncSession,
        recording_id: uuid.UUID,
    ) -> list[MtgMeetingTranscript]:
        stmt = (
            select(MtgMeetingTranscript)
            .where(MtgMeetingTranscript.is_deleted == False)  # noqa: E712  SQLAlchemy 查询表达式
            .where(MtgMeetingTranscript.recording_id == recording_id)
            .order_by(MtgMeetingTranscript.segment_index.asc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    # ── 4. delete / retry ───────────────────────────────────────────────

    async def delete_recording(
        self,
        db: AsyncSession,
        recording_id: uuid.UUID,
    ) -> bool:
        recording = await self.get_recording_detail(db, recording_id)
        if not recording:
            return False
        recording.is_deleted = True
        await db.commit()
        # 清理原始文件
        try:
            delete_recording_file(recording.storage_path)
        except Exception:  # noqa: BLE001
            logger.exception("delete file failed for recording %s", recording_id)
        return True

    async def retranscribe(
        self,
        db: AsyncSession,
        recording_id: uuid.UUID,
    ) -> MtgMeetingRecording | None:
        """Reset to Uploaded and clear transcripts, ready for re-run."""
        recording = await self.get_recording_detail(db, recording_id)
        if not recording:
            return None
        recording.status = "Uploaded"
        recording.progress_pct = 0
        recording.error_message = None
        await db.commit()

        # 清空旧 transcript 行
        from sqlalchemy import delete as sql_delete

        await db.execute(
            sql_delete(MtgMeetingTranscript)
            .where(MtgMeetingTranscript.recording_id == recording_id)
        )
        await db.commit()
        return await self.get_recording_detail(db, recording_id)



# ── 阶段六 6.4: LLM 润色后用"沉默间隔"重新分段 ──────────────────

PAUSE_THRESHOLD = 2.0  # 沉默超过 2.0s 视为段边界（用户要求 ≥2s）
MIN_PARAGRAPH_CHARS = 15  # 与 prompt 一致：每段至少 15 字避免碎片
MAX_PARAGRAPH_CHARS = 50  # 与 prompt 一致：每段最长 50 字


def _cluster_by_pause(
    originals: list,
    refined: list,
) -> list[dict]:
    """按"沉默间隔"对 (original segment, refined text) 聚类成段。

    Returns: [{start, end, text, original_segments, refined_text}, ...]
    """
    if not originals or not refined:
        return []
    # 让 refined 按顺序与 originals 对齐
    pairs = list(zip(originals, refined), strict=False)  # noqa: B905  显式声明不检查长度  # type: ignore[reportCallIssue]  类型修复
    clusters: list[dict] = []
    cur_origs: list = []
    cur_texts: list[str] = []
    cur_start = float(pairs[0][0].start_time)
    cur_end = float(pairs[0][0].end_time)
    for orig, ref in pairs:
        if not cur_origs:
            cur_start = float(orig.start_time)
        # 沉默间隔 >= 0.7s → 段边界
        if cur_origs and (float(orig.start_time) - cur_end) >= PAUSE_THRESHOLD:
            # 关闭当前 cluster
            clusters.append({
                "start": cur_start,
                "end": cur_end,
                "text": "".join(cur_texts).strip(),
                "originals": cur_origs,
            })
            cur_origs = []
            cur_texts = []
            cur_start = float(orig.start_time)
        cur_origs.append(orig)
        cur_texts.append(ref.text)
        cur_end = float(orig.end_time)
    if cur_origs:
        clusters.append({
            "start": cur_start,
            "end": cur_end,
            "text": "".join(cur_texts).strip(),
            "originals": cur_origs,
        })
    return clusters


def _persist_refined_clusters(
    db: AsyncSession,
    rows: list,
    clusters: list[dict],
) -> None:
    """将 cluster 的 refined text 写回原 rows（首条 segment 拿全部
    text，后续清空避免重复），并把 speaker_label 沿用首条。
    """
    for cluster in clusters:
        segs = cluster["originals"]
        if not segs:
            continue
        segs[0].text = cluster["text"]
        segs[0].start_time = round(cluster["start"], 1)
        segs[0].end_time = round(cluster["end"], 1)
        for extra in segs[1:]:
            extra.text = ""

