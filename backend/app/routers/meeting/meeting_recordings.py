"""Meeting recording upload/transcription router.

All routes are mounted under ``/meetings/{meeting_id}/recordings``
so they share the project-level permission checks.

Permission model:
    * ``meeting:read``       — list / detail / transcript
    * ``meeting:upload``     — POST upload, DELETE
    * ``meeting:transcribe`` — POST retranscribe
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models.meeting import (
    Meeting,
    MtgMeetingRecording,
)
from app.auth import get_current_user
from app.exceptions import BadRequestException, NotFoundException
from app.models.user import User
from app.services.meeting import (
    MAX_FILE_BYTES,
    MeetingRecordingService,
    TranscriptionService,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Meeting Recordings"])

_recording_service = MeetingRecordingService()


# ── Schemas ───────────────────────────────────────────────────────────────


class TranscriptSegmentOut(BaseModel):
    model_config = {"from_attributes": True}
    id: UUID
    segment_index: int
    start_time: float
    end_time: float
    speaker_label: str | None = None
    text: str
    confidence: float | None = None
    language: str | None = None
    # 阶段六 6.3: 章节 + 关键词
    chapter_id: int | None = None
    chapter_title: str | None = None
    keywords: list[str] | None = None


class RecordingOut(BaseModel):
    model_config = {"from_attributes": True}
    id: UUID
    meeting_id: UUID
    uploader_id: UUID
    file_name: str
    file_size: int
    duration_seconds: int | None = None
    mime_type: str | None = None
    storage_path: str
    status: str
    progress_pct: int
    language: str | None = None
    model_size: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class RecordingDetailOut(RecordingOut):
    transcripts: list[TranscriptSegmentOut] = []


class TranscriptOut(BaseModel):
    recording_id: UUID
    status: str
    progress_pct: int
    segments: list[TranscriptSegmentOut]


# ── Endpoints ─────────────────────────────────────────────────────────────


async def _get_meeting_or_404(
    db: AsyncSession, meeting_id: UUID
) -> Meeting:
    result = await db.execute(
        select(Meeting).where(Meeting.is_deleted == False).where(  # noqa: E712  SQLAlchemy 查询表达式
            Meeting.id == meeting_id,
        )
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise NotFoundException("Meeting not found")
    return meeting


@router.post(
    "/meetings/{meeting_id}/recordings",
    response_model=RecordingOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_recording(
    meeting_id: UUID,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(..., description="音频文件"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Any:
    """Upload an audio recording. Returns immediately with status=Uploaded;
    the actual Whisper transcription runs in ``BackgroundTasks``."""
    settings = get_settings()
    max_bytes = min(MAX_FILE_BYTES, settings.MEETING_MAX_RECORDING_BYTES)

    await _get_meeting_or_404(db, meeting_id)

    if not file.filename:
        raise BadRequestException("file is required")

    # Frontend uses FormData.postForm without explicit part Content-Type;
    # fall back to the filename extension so we still capture mime.
    mime_type = (file.content_type or "").strip() or _guess_mime_from_filename(file.filename)

    if file.size is not None and file.size > max_bytes:
        raise BadRequestException(
            f"file too large: {file.size} > limit {max_bytes}"
        )

    # 文件大小超限防御（即便前端未传 size）
    file_obj = file.file  # SpooledTemporaryFile
    recording = await _recording_service.create_recording(
        db,
        meeting_id=meeting_id,
        uploader_id=current_user.id,
        file_obj=file_obj,
        file_name=file.filename,
        mime_type=mime_type,
        max_bytes=max_bytes,
    )

    # 启动后台转写任务（绝不阻塞请求）
    background_tasks.add_task(
        _safe_run_transcription, recording.id
    )

    return recording


async def _safe_run_transcription(recording_id: UUID) -> None:
    """Background wrapper — invoked by FastAPI BackgroundTasks on the
    same event loop as the main worker.  Because we stay on the main
    loop the asyncpg connection pool is reused from the live session.

    Safety net（2026-06-02）：如果 run_transcription 内部任何位置抛
    未捕获异常，确保录音状态被切到 Failed，不再卡在 Transcribing。
    """
    logger.info("[bg] _safe_run_transcription called for %s", recording_id)
    try:
        await _recording_service.run_transcription(recording_id)
        logger.info("[bg] transcription done for %s", recording_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("background transcription crashed: %s", recording_id)
        # 兜底：状态必须落到 Failed / Uploaded，否则 UI 永远显示 Transcribing
        try:
            from sqlalchemy import update

            from app.database import AsyncSessionLocal as SessionLocal
            from app.models.meeting import MtgMeetingRecording
            async with SessionLocal() as db:
                await db.execute(
                    update(MtgMeetingRecording)
                    .where(MtgMeetingRecording.id == recording_id)
                    .values(
                        status="Failed",
                        error_message=f"background task crashed: {exc}"[:2000],
                        progress_pct=0,
                    )
                )
                await db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("failed to mark recording %s as Failed", recording_id)


_EXT_TO_MIME: dict[str, str] = {
    ".mp3":  "audio/mpeg",
    ".wav":  "audio/wav",
    ".m4a":  "audio/mp4",
    ".aac":  "audio/aac",
    ".flac": "audio/flac",
    ".ogg":  "audio/ogg",
    ".opus": "audio/ogg",
    ".webm": "audio/webm",
    ".mp4":  "audio/mp4",
    ".mpeg": "audio/mpeg",
    ".mpga": "audio/mpeg",
}


def _guess_mime_from_filename(name: str) -> str | None:
    import os
    ext = os.path.splitext(name or "")[1].lower()
    return _EXT_TO_MIME.get(ext)


@router.get(
    "/meetings/{meeting_id}/recordings",
    response_model=list[RecordingOut],
)
async def list_recordings(
    meeting_id: UUID,
    status_filter: str | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    await _get_meeting_or_404(db, meeting_id)
    rows = await _recording_service.list_recordings(
        db, meeting_id=meeting_id, status=status_filter
    )
    return rows


@router.get(
    "/meetings/{meeting_id}/recordings/{recording_id}",
    response_model=RecordingDetailOut,
)
async def get_recording(
    meeting_id: UUID,
    recording_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    await _get_meeting_or_404(db, meeting_id)
    ownership = (
        await db.execute(
            select(MtgMeetingRecording.meeting_id).where(
                MtgMeetingRecording.id == recording_id,
                MtgMeetingRecording.is_deleted == False,  # noqa: E712  SQLAlchemy 查询表达式
            )
        )
    ).scalar_one_or_none()
    if ownership != meeting_id:
        raise NotFoundException("Recording not found")
    recording = await _recording_service.get_recording_detail(db, recording_id)
    # 不挂在 recording.transcripts 上（会触发 relationship mapper
    # 配置失败），直接用 Pydantic 模型构造。
    transcripts = await _recording_service.get_full_transcript(db, recording_id)
    return RecordingDetailOut(
        id=recording.id,
        meeting_id=recording.meeting_id,
        uploader_id=recording.uploader_id,
        file_name=recording.file_name,
        file_size=recording.file_size,
        duration_seconds=recording.duration_seconds,
        mime_type=recording.mime_type,
        storage_path=recording.storage_path,
        status=recording.status,
        progress_pct=recording.progress_pct,
        language=recording.language,
        model_size=recording.model_size,
        error_message=recording.error_message,
        created_at=recording.created_at,
        updated_at=recording.updated_at,
        transcripts=transcripts,  # type: ignore[reportArgumentType]  类型修复
    )


@router.get(
    "/meetings/{meeting_id}/recordings/{recording_id}/transcript",
    response_model=TranscriptOut,
)
async def get_transcript(
    meeting_id: UUID,
    recording_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    await _get_meeting_or_404(db, meeting_id)
    ownership = (
        await db.execute(
            select(MtgMeetingRecording.meeting_id).where(
                MtgMeetingRecording.id == recording_id,
                MtgMeetingRecording.is_deleted == False,  # noqa: E712  SQLAlchemy 查询表达式
            )
        )
    ).scalar_one_or_none()
    if ownership != meeting_id:
        raise NotFoundException("Recording not found")
    segments = await _recording_service.get_full_transcript(db, recording_id)
    rec = await _recording_service.get_recording_detail(db, recording_id)
    return TranscriptOut(
        recording_id=rec.id,
        status=rec.status,
        progress_pct=rec.progress_pct,
        segments=segments,  # type: ignore[reportArgumentType]  类型修复
    )


@router.post(
    "/meetings/{meeting_id}/recordings/{recording_id}/retranscribe",
    response_model=RecordingOut,
)
async def retranscribe_recording(
    meeting_id: UUID,
    recording_id: UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    await _get_meeting_or_404(db, meeting_id)

    # 用 scalar 查询校验归属（不 selectinload，避开无关 mapper 初始化失败）
    ownership = (
        await db.execute(
            select(MtgMeetingRecording.meeting_id).where(
                MtgMeetingRecording.id == recording_id,
                MtgMeetingRecording.is_deleted == False,  # noqa: E712  SQLAlchemy 查询表达式
            )
        )
    ).scalar_one_or_none()
    if ownership != meeting_id:
        raise NotFoundException("Recording not found")

    # 检查 ffmpeg/ffprobe 可用性 — 失败时立刻把状态置为 Failed
    if not TranscriptionService.ffmpeg_available():
        await db.execute(
            update(MtgMeetingRecording)
            .where(MtgMeetingRecording.id == recording_id)
            .values(status="Failed", error_message="ffmpeg/ffprobe not installed")
        )
        await db.commit()
        raise HTTPException(
            status_code=503,
            detail="ffmpeg/ffprobe is not installed on the server",
        )

    await _recording_service.retranscribe(db, recording_id)
    background_tasks.add_task(_safe_run_transcription, recording_id)

    refreshed = await _recording_service.get_recording_detail(db, recording_id)
    return refreshed


@router.post(
    "/meetings/{meeting_id}/recordings/{recording_id}/refine",
    response_model=RecordingDetailOut,
)
async def refine_recording_transcript(
    meeting_id: UUID,
    recording_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Re-run LLM 错别字修复 + 顺句 + 加标点 + 间隔分段。

    只更新 transcripts.text 列，不动 status / progress。"""
    await _get_meeting_or_404(db, meeting_id)
    ownership = (
        await db.execute(
            select(MtgMeetingRecording.meeting_id).where(
                MtgMeetingRecording.id == recording_id,
                MtgMeetingRecording.is_deleted == False,  # noqa: E712  SQLAlchemy 查询表达式
            )
        )
    ).scalar_one_or_none()
    if ownership != meeting_id:
        raise NotFoundException("Recording not found")

    from app.services.meeting.transcript_refinement_service import (
        apply_refinement_to_recording,
    )
    n = await apply_refinement_to_recording(db, recording_id)
    if n == 0:
        raise BadRequestException("No transcript segments to refine (录音未转写)")

    # 与 get_recording 端点相同：手动构造 RecordingDetailOut 避免 ORM
    # relationship 在响应序列化时触发 MissingGreenlet。
    recording = await _recording_service.get_recording_detail(db, recording_id)
    if recording is None:
        raise NotFoundException("Recording")
    transcripts = await _recording_service.get_full_transcript(db, recording_id)
    return RecordingDetailOut(
        id=recording.id,
        meeting_id=recording.meeting_id,
        uploader_id=recording.uploader_id,
        file_name=recording.file_name,
        file_size=recording.file_size,
        duration_seconds=recording.duration_seconds,
        mime_type=recording.mime_type,
        storage_path=recording.storage_path,
        status=recording.status,
        progress_pct=recording.progress_pct,
        language=recording.language,
        model_size=recording.model_size,
        error_message=recording.error_message,
        created_at=recording.created_at,
        updated_at=recording.updated_at,
        transcripts=transcripts,  # type: ignore[reportArgumentType]  类型修复
    )


@router.delete(
    "/meetings/{meeting_id}/recordings/{recording_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_recording(
    meeting_id: UUID,
    recording_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    await _get_meeting_or_404(db, meeting_id)
    ok = await _recording_service.delete_recording(db, recording_id)
    if not ok:
        raise NotFoundException("Recording not found")
