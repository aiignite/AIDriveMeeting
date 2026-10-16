"""Whisper-based audio transcription service.

Responsibilities:
    * Lazy-load ``faster-whisper`` (do not import at module import time
      so the app boots even when the dependency is missing).
    * Use ``ffprobe`` to read audio duration.
    * Use ``ffmpeg silencedetect`` to split long audio into chunks
      at silence boundaries, with a hard 60s fallback.
    * Run Whisper on each chunk via ``asyncio.to_thread`` to avoid
      blocking the event loop.
    * Return a normalised segment list of
      ``{start, end, text, confidence, language}``.

This module never touches the database or routers — pure CPU/IO work.

.. note::
   2026-06-02: 切换 ``openai-whisper`` → ``faster-whisper``。
   faster-whisper 基于 CTranslate2，在 CPU 上相对原版提速 4-6×；
   模型权重与 openai-whisper 同源（首次调用会从 HF 下载到
   ``~/.cache/huggingface/hub/models--Systran--faster-whisper-large-v3``）。
"""
from __future__ import annotations

import asyncio
import json
import logging

# Imported lazily inside methods to avoid circular imports at module load time.
# 2026-06-02: 兜底国内镜像。
# faster-whisper 第一次启动会从 HuggingFace 拉模型权重，国内经常
# ConnectTimeout。这里同时支持：
#   1) ``HF_ENDPOINT`` 环境变量（最优先，.env / start.sh 注入）
#   2) 默认走 hf-mirror.com（避免完全离线环境拿不到模型）
# 2026-07-17: HF Xet/CAS（cas-server.xethub.hf.co）常对匿名/镜像拉取返回
# 401「File reconstruction error」。必须在 import huggingface_hub 之前禁用 Xet，
# 强制走经典 HTTP 下载（配合 HF_ENDPOINT 镜像）。
import os as _os
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from app.config import get_settings

if not _os.environ.get("HF_ENDPOINT"):
    _os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
_os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
# 拉大模型时默认 10s 太短，调到 60s
_os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TranscriptSegment:
    """One Whisper segment, normalised for our schema."""

    start: float
    end: float
    text: str
    confidence: float | None = None
    speaker_label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TranscriptionError(RuntimeError):
    """Raised when the transcription pipeline cannot proceed."""


class TranscriptionService:
    """Stateless facade around Whisper + ffmpeg.

    A single instance can be reused across requests. The Whisper model
    is cached on first use; ``transcribe`` is async-friendly because
    the heavy lifting runs in ``asyncio.to_thread``.
    """

    def __init__(
        self,
        model_size: str | None = None,
        language: str | None = None,
        db: Any | None = None,
    ) -> None:
        settings = get_settings()
        self._model_size = model_size or settings.MEETING_WHISPER_MODEL
        self._language = (language or settings.MEETING_WHISPER_LANGUAGE) or None
        self._max_concurrent = max(1, settings.MEETING_WHISPER_MAX_CONCURRENT)
        self._silence_db = float(settings.MEETING_SPLIT_SILENCE_DB)
        self._silence_duration = float(settings.MEETING_SPLIT_SILENCE_DURATION)
        self._max_segment_seconds = max(5, int(settings.MEETING_SPLIT_MAX_SEGMENT_SECONDS))
        # 2026-06-02: 最小窗口。silencedetect 经常切出 0.5-1s 的"非沉默"碎片，
        # 送进 Whisper 必然返回空，导致 >3min 长录音末尾的语音被切碎漏掉。
        self._min_segment_seconds = max(0.5, float(settings.MEETING_SPLIT_MIN_SEGMENT_SECONDS))
        self._enable_diarization = bool(settings.MEETING_ENABLE_SPEAKER_DIARIZATION)
        self._asr_mode = (settings.MEETING_ASR_MODE or "local").strip().lower()
        self._fallback_local = bool(settings.MEETING_ASR_FALLBACK_LOCAL)
        self._device = (settings.MEETING_WHISPER_DEVICE or "cpu").strip() or "cpu"
        self._compute_type = (settings.MEETING_WHISPER_COMPUTE_TYPE or "int8").strip() or "int8"
        self._remote_url = settings.MEETING_ASR_REMOTE_URL or ""
        self._remote_api_key = settings.MEETING_ASR_REMOTE_API_KEY or ""
        self._remote_timeout = max(30, int(settings.MEETING_ASR_REMOTE_TIMEOUT or 3600))
        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        self._whisper = None  # lazy
        self._db = db

        # Runtime database settings take precedence over environment defaults.
        # ``__init__`` is synchronous; if an async session is supplied while
        # no event loop is running we can block to load settings. In async
        # contexts callers should use :meth:`create` instead.
        if db is not None:
            try:
                self._apply_db_settings_sync(db)
            except Exception as exc:  # noqa: BLE001
                logger.warning("failed to apply DB ASR settings, using env defaults: %s", exc)

    @classmethod
    async def create(
        cls,
        db: Any,
        model_size: str | None = None,
        language: str | None = None,
    ) -> TranscriptionService:
        """Async factory that applies database settings before returning.

        Use this when constructing the service inside an async function.
        """
        instance = cls(model_size=model_size, language=language)
        await instance._apply_db_settings_async(db)
        return instance

    def _apply_db_settings_sync(self, db: Any) -> None:
        """Synchronous entry point for applying DB settings.

        Only works when no event loop is currently running.
        """
        import asyncio

        from app.services.meeting.asr_settings_service import MtgAsrSettingsService

        row = MtgAsrSettingsService.get_settings(db)
        if hasattr(row, "__await__"):
            loop = asyncio.get_event_loop()
            if loop.is_running():
                raise RuntimeError("async session requires async factory TranscriptionService.create")
            row = loop.run_until_complete(row)
        if row is None:
            return
        self._apply_row_settings(row, db)

    async def _apply_db_settings_async(self, db: Any) -> None:
        """Asynchronous entry point for applying DB settings."""
        from app.services.meeting.asr_settings_service import MtgAsrSettingsService

        row = await MtgAsrSettingsService.get_settings(db)
        if row is None:
            return
        effective = await MtgAsrSettingsService.get_effective_settings(db)
        self._apply_effective_settings(row, effective)

    def _apply_row_settings(self, row: Any, db: Any) -> None:
        """Apply resolved DB row values over environment defaults.

        This synchronous path may block to resolve ``get_effective_settings``.
        """
        from app.services.meeting.asr_settings_service import MtgAsrSettingsService

        effective = MtgAsrSettingsService.get_effective_settings(db)
        if hasattr(effective, "__await__"):
            import asyncio

            loop = asyncio.get_event_loop()
            if loop.is_running():
                raise RuntimeError("async session requires async factory TranscriptionService.create")
            effective = loop.run_until_complete(effective)
        self._apply_effective_settings(row, effective)  # type: ignore[reportArgumentType]  类型修复

    def _apply_effective_settings(
        self, row: Any, effective: dict[str, Any]
    ) -> None:
        """Apply resolved effective settings over environment defaults."""
        from app.services.meeting.asr_settings_service import MtgAsrSettingsService

        self._model_size = effective.get("model") or self._model_size
        self._language = effective.get("language") or self._language
        self._asr_mode = effective.get("mode") or self._asr_mode
        self._remote_url = effective.get("remote_url") or self._remote_url
        self._remote_api_key = (
            MtgAsrSettingsService.decrypt_remote_api_key(row) or self._remote_api_key
        )
        self._remote_timeout = max(
            30, int(effective.get("remote_timeout_seconds") or self._remote_timeout)
        )
        self._fallback_local = bool(
            effective.get("fallback_to_local")
            if effective.get("fallback_to_local") is not None
            else self._fallback_local
        )
        self._device = effective.get("device") or self._device
        self._compute_type = effective.get("compute_type") or self._compute_type

    # ── public api ──────────────────────────────────────────────────────

    async def transcribe(
        self,
        storage_path: str,
        progress_callback=None,
    ) -> list[TranscriptSegment]:
        """Transcribe a stored recording. Returns ordered segments."""
        target = self._resolve_absolute(storage_path)
        if not target.exists():
            raise TranscriptionError(f"recording file not found: {storage_path}")

        if self._asr_mode == "remote":
            try:
                async with self._semaphore:
                    return await self._transcribe_remote(target, progress_callback)
            except Exception as exc:  # noqa: BLE001
                if not self._fallback_local:
                    if isinstance(exc, TranscriptionError):
                        raise
                    raise TranscriptionError(f"remote ASR failed: {exc}") from exc
                logger.warning(
                    "remote ASR failed, falling back to local: %s", exc
                )

        if not self._ffmpeg_available():
            raise TranscriptionError(
                "ffmpeg/ffprobe not installed — cannot run transcription"
            )

        async with self._semaphore:
            return await self._transcribe_locked(target, progress_callback)

    async def _transcribe_remote(
        self,
        target: Path,
        progress_callback,
    ) -> list[TranscriptSegment]:
        import asyncio

        from app.services.meeting.asr_remote_client import AsrRemoteClient, AsrRemoteError

        client = AsrRemoteClient(
            url=self._remote_url,
            api_key=self._remote_api_key,
            timeout_seconds=self._remote_timeout,
        )

        def _pct(pct: int) -> None:
            if progress_callback is None:
                return
            try:
                progress_callback(pct)
            except Exception:  # noqa: BLE001
                logger.debug("progress_callback raised", exc_info=True)

        _pct(5)
        # Windows GPU host often lacks ffmpeg; convert webm/mp4 → 16k wav here first.
        upload_path = target
        tmp_wav: Path | None = None
        if target.suffix.lower() != ".wav":
            if not self._ffmpeg_available():
                raise TranscriptionError(
                    "ffmpeg required to convert recording before remote ASR upload"
                )
            tmp_wav = target.with_suffix(".remote-16k.wav")
            await asyncio.to_thread(self._ffmpeg_to_wav_16k, target, tmp_wav)
            if not tmp_wav.exists() or tmp_wav.stat().st_size == 0:
                raise TranscriptionError("ffmpeg conversion to wav failed for remote ASR")
            upload_path = tmp_wav
            _pct(10)

        logger.info(
            "remote ASR upload start: %s (%.1f MB) -> %s",
            upload_path.name,
            upload_path.stat().st_size / (1024 * 1024),
            self._remote_url,
        )

        # Heartbeat while waiting on GPU host (remote path has no chunk progress).
        stop_hb = asyncio.Event()

        async def _heartbeat() -> None:
            pct = 12
            while not stop_hb.is_set():
                try:
                    await asyncio.wait_for(stop_hb.wait(), timeout=15.0)
                    return
                except TimeoutError:
                    pct = min(90, pct + 3)
                    _pct(pct)

        hb_task = asyncio.create_task(_heartbeat())
        try:
            try:
                raw = await client.transcribe_file(
                    upload_path,
                    language=self._language,
                    model_size=self._model_size,
                )
            except AsrRemoteError as exc:
                raise TranscriptionError(str(exc)) from exc
        finally:
            stop_hb.set()
            try:
                await hb_task
            except Exception:  # noqa: BLE001
                pass
            if tmp_wav is not None:
                try:
                    tmp_wav.unlink(missing_ok=True)
                except OSError:
                    pass

        _pct(95)

        segments = [
            TranscriptSegment(
                start=round(float(item.get("start", 0)), 3),
                end=round(float(item.get("end", 0)), 3),
                text=str(item.get("text") or "").strip(),
                confidence=item.get("confidence"),
            )
            for item in raw
            if str(item.get("text") or "").strip()
        ]
        if not segments:
            raise TranscriptionError("remote ASR returned no text segments")
        _pct(100)
        logger.info(
            "remote ASR done: %d segments (file=%s)",
            len(segments),
            target.name,
        )
        return segments

    @staticmethod
    def asr_mode(db: Any | None = None) -> str:
        """Return the effective ASR mode, preferring DB settings when available.

        If ``db`` is provided, the method attempts to read the saved mode.
        Because this method is synchronous, an async session can only be
        awaited when no event loop is currently running; in async contexts
        the call safely falls back to environment defaults.
        """
        if db is not None:
            try:
                from app.services.meeting.asr_settings_service import MtgAsrSettingsService

                row = MtgAsrSettingsService.get_settings(db)
                # row is an awaitable when db is an async session
                if hasattr(row, "__await__"):
                    import asyncio

                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            # Cannot synchronously await inside a running loop.
                            raise RuntimeError("async session requires async caller")
                        row = loop.run_until_complete(row)
                    except RuntimeError:
                        row = None
                if row is not None:
                    return row.mode
            except Exception as exc:  # noqa: BLE001
                logger.debug("failed to read ASR mode from DB: %s", exc)
        return (get_settings().MEETING_ASR_MODE or "local").strip().lower()

    @staticmethod
    async def remote_asr_reachable(db: Any | None = None) -> bool:
        """Best-effort health probe when mode=remote.

        If ``db`` is provided, the configured remote URL/API key from the
        database are used; otherwise environment variables are used.
        """
        from app.services.meeting.asr_settings_service import MtgAsrSettingsService

        mode = "local"
        url = ""
        api_key = ""
        timeout = 3600
        if db is not None:
            try:
                effective = await MtgAsrSettingsService.get_effective_settings(db)
                mode = effective.get("mode", "local")
                url = effective.get("remote_url") or ""
                api_key = effective.get("remote_api_key") or ""
                timeout = int(effective.get("remote_timeout_seconds") or 3600)
                # Decrypt if the key came from the encrypted DB column.
                row = await MtgAsrSettingsService.get_settings(db)
                decrypted = MtgAsrSettingsService.decrypt_remote_api_key(row)
                if decrypted:
                    api_key = decrypted
            except Exception as exc:  # noqa: BLE001
                logger.debug("failed to read remote ASR settings from DB: %s", exc)

        if mode != "remote":
            settings = get_settings()
            mode = (settings.MEETING_ASR_MODE or "local").strip().lower()
            url = settings.MEETING_ASR_REMOTE_URL or ""
            api_key = settings.MEETING_ASR_REMOTE_API_KEY or ""
            timeout = max(30, int(settings.MEETING_ASR_REMOTE_TIMEOUT or 3600))

        if mode != "remote":
            return True
        if not url.strip():
            return False
        from app.services.meeting.asr_remote_client import AsrRemoteClient

        try:
            client = AsrRemoteClient(
                url=url,
                api_key=api_key,
                timeout_seconds=timeout,
            )
            return await client.health_check()
        except Exception:  # noqa: BLE001
            return False

    # ── preflight helpers ───────────────────────────────────────────────

    @staticmethod
    def ffmpeg_available() -> bool:
        """Whether both ffmpeg and ffprobe are on PATH."""
        return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None

    def _ffmpeg_available(self) -> bool:
        return self.ffmpeg_available()

    @staticmethod
    def probe_duration_seconds(storage_path: str) -> int:
        """Return audio duration in seconds, or -1 on failure."""
        target = TranscriptionService._resolve_absolute(storage_path)
        if not target.exists():
            return -1
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(target),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return -1
        if result.returncode != 0:
            return -1
        try:
            return int(float((result.stdout or "").strip()))
        except (TypeError, ValueError):
            return -1

    @staticmethod
    def _resolve_absolute(storage_path: str) -> Path:
        backend_root = Path(__file__).resolve().parents[3]
        return (backend_root / storage_path).resolve()

    # ── core pipeline ───────────────────────────────────────────────────

    async def _transcribe_locked(self, target: Path, progress_callback) -> list[TranscriptSegment]:
        duration = await asyncio.to_thread(self.probe_duration_seconds, str(target))
        if duration is None or duration < 0:
            duration = 0

        chunks: list[tuple[float, float, Path]] = []
        with tempfile.TemporaryDirectory(prefix="meeting_chunks_") as tmp:
            tmp_dir = Path(tmp)
            chunks = await asyncio.to_thread(
                self._split_audio_into_chunks, target, tmp_dir, duration
            )
            if not chunks:
                # Fallback: a single chunk spanning the whole file
                chunks = [(0.0, float(duration or 0), target)]

            segments: list[TranscriptSegment] = []
            total = len(chunks) or 1
            chunk_errors: list[str] = []
            # ── 说话人识别（默认关闭；伪 VAD 会产生大量 Speaker N）──
            turns: list = []
            if self._enable_diarization:
                try:
                    from app.services.meeting.diarization_service import DiarizationService
                    turns, used = await DiarizationService().diarize(
                        str(target), language=self._language,
                    )
                    logger.info("diarization used=%s → %d turns", used, len(turns))
                except Exception as exc:  # noqa: BLE001
                    logger.warning("diarization skipped: %s", exc)

            for idx, (start, _end, chunk_path) in enumerate(chunks):
                try:
                    chunk_segments = await asyncio.to_thread(
                        self._whisper_transcribe_chunk, chunk_path
                    )
                except TranscriptionError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning("whisper chunk %d failed: %s", idx, exc)
                    chunk_errors.append(str(exc))
                    continue

                # Re-base timestamps to original timeline + 写 speaker_label
                for seg in chunk_segments:
                    abs_start = start + float(seg.get("start", 0))
                    abs_end = start + float(seg.get("end", 0))
                    label = None
                    if turns:
                        from app.services.meeting.diarization_service import (
                            assign_speaker_label,
                        )
                        label = assign_speaker_label(abs_start, abs_end, turns)
                    segments.append(
                        TranscriptSegment(
                            start=round(abs_start, 3),
                            end=round(abs_end, 3),
                            text=str(seg.get("text") or "").strip(),
                            confidence=seg.get("confidence"),
                            speaker_label=label,
                        )
                    )

                if progress_callback is not None:
                    try:
                        progress_callback(int((idx + 1) * 100 / total))
                    except Exception:  # noqa: BLE001
                        logger.debug("progress_callback raised", exc_info=True)

        # Drop empty segments
        if not segments and chunk_errors:
            raise TranscriptionError(
                f"all {len(chunks)} whisper chunks failed: {chunk_errors[0][:200]}"
            )
        return [s for s in segments if s.text]

    # ── ffmpeg helpers (sync) ───────────────────────────────────────────

    def _split_audio_into_chunks(
        self, target: Path, work_dir: Path, total_duration: int
    ) -> list[tuple[float, float, Path]]:
        """Use silencedetect to compute split points, then ``ffmpeg`` to slice."""
        silence_points = self._detect_silence_points(target)
        windows = self._build_windows(silence_points, total_duration)
        chunks: list[tuple[float, float, Path]] = []
        for idx, (start, end) in enumerate(windows):
            chunk_path = work_dir / f"chunk_{idx:04d}.wav"
            self._ffmpeg_extract_segment(target, chunk_path, start, end)
            if chunk_path.exists() and chunk_path.stat().st_size > 0:
                chunks.append((start, end, chunk_path))
        return chunks

    def _detect_silence_points(self, target: Path) -> list[float]:
        """Return a list of (mid) timestamps where silence >= threshold occurs."""
        try:
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-nostats",
                    "-i",
                    str(target),
                    "-af",
                    f"silencedetect=noise={self._silence_db}dB:d={self._silence_duration}",
                    "-f",
                    "null",
                    "-",
                ],
                capture_output=True,
                text=True,
                timeout=600,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            logger.warning("silencedetect failed: %s", exc)
            return []

        output = (result.stderr or "") + (result.stdout or "")
        # 解析 silencedetect 输出的 silence_start / silence_end
        points: list[float] = []
        starts: list[float] = []
        ends: list[float] = []
        for line in output.splitlines():
            if "silence_start:" in line:
                try:
                    starts.append(float(line.rsplit(":", 1)[-1].strip()))
                except ValueError:
                    continue
            elif "silence_end:" in line:
                # 形如: "[silencedetect @ 0x...] silence_end: 12.345 | silence_duration: 0.6"
                try:
                    head = line.rsplit("silence_end:", 1)[-1].strip()
                    head = head.split("|", 1)[0].strip()
                    ends.append(float(head))
                except ValueError:
                    continue

        for s, e in zip(starts, ends, strict=False):
            points.append((s + e) / 2.0)
        return points

    def _build_windows(self, silence_points: list[float], total_duration: int) -> list[tuple[float, float]]:
        """Compose non-overlapping windows around silence midpoints,
        enforcing a hard ``max_segment_seconds`` ceiling and a
        ``min_segment_seconds`` floor (tiny fragments are merged
        forward into the previous window so Whisper gets useful input).
        """
        total = float(total_duration or 0)
        if total <= 0:
            # 退化为单窗口
            return [(0.0, max(float(self._max_segment_seconds), 1.0))]

        # Build split boundaries
        boundaries = [0.0]
        for mid in silence_points:
            if 0.0 < mid < total:
                boundaries.append(mid)
        boundaries.append(total)
        boundaries.sort()

        # Compose raw windows from pairs of boundaries
        raw_windows: list[tuple[float, float]] = []
        for a, b in zip(boundaries[:-1], boundaries[1:], strict=False):
            if b - a <= 0.05:
                continue
            # 强制切分到 max_segment_seconds 以内
            sub_start = a
            while sub_start < b:
                sub_end = min(sub_start + self._max_segment_seconds, b)
                raw_windows.append((sub_start, sub_end))
                sub_start = sub_end

        # 2026-06-02: 合并短于 _min_segment_seconds 的窗口到上一个，
        # 避免 silencedetect 切出的 0.5-1s 碎片进 Whisper 后被丢成空段。
        # 边界情况：第一个窗口就太短 → 与 [0, total] 单窗口退化为整段。
        if not raw_windows:
            return [(0.0, total)]

        merged: list[tuple[float, float]] = [raw_windows[0]]
        for cur in raw_windows[1:]:
            a, b = merged[-1]
            ca, cb = cur
            if (cb - ca) < self._min_segment_seconds:
                # 当前窗口太短 → 把它并入上一个
                merged[-1] = (a, max(b, cb))
            else:
                merged.append(cur)

        # 如果合并后第一个窗口还是太短（极端：silence 列表把开头切碎），
        # 退化到整段单窗口，Whisper 自行 VAD。
        if merged and (merged[0][1] - merged[0][0]) < self._min_segment_seconds:
            return [(0.0, total)]

        return merged

    def _ffmpeg_to_wav_16k(self, src: Path, dst: Path) -> None:
        """Convert whole file to 16 kHz mono PCM WAV (for remote ASR upload)."""
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-nostats",
                    "-i",
                    str(src),
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(dst),
                ],
                capture_output=True,
                text=True,
                timeout=600,
                check=True,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
            logger.warning("ffmpeg wav convert failed (%s): %s", src.name, exc)
            if dst.exists():
                try:
                    dst.unlink()
                except OSError:
                    pass

    def _ffmpeg_extract_segment(
        self, src: Path, dst: Path, start: float, end: float
    ) -> None:
        duration = max(end - start, 0.05)
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-hide_banner",
                    "-nostats",
                    "-ss",
                    f"{start:.3f}",
                    "-i",
                    str(src),
                    "-t",
                    f"{duration:.3f}",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-c:a",
                    "pcm_s16le",
                    str(dst),
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            logger.warning("ffmpeg extract failed (%s): %s", dst.name, exc)
            return
        if not dst.exists() or dst.stat().st_size == 0:
            logger.debug("ffmpeg produced empty chunk: %s", dst)

    # ── whisper helpers (sync, run in thread) ────────────────────────────

    def _get_whisper(self):  # type: ignore[no-untyped-def]
        """Lazy-load the faster-whisper model.

        faster-whisper returns a :class:`WhisperModel` whose
        :meth:`transcribe` returns ``(segments_iter, info)``. The
        segments iterator yields objects with ``start``, ``end``,
        ``text`` and ``avg_logprob`` (used as confidence proxy).
        """
        if self._whisper is None:
            try:
                from faster_whisper import WhisperModel  # type: ignore[import-not-found]
            except ImportError as exc:  # pragma: no cover - depends on env
                raise TranscriptionError(
                    "faster-whisper is not installed; run `pip install -U faster-whisper`"
                ) from exc

            # CPU + int8 在精度几乎无损下显著降低内存并加速
            # GPU 机可设 MEETING_WHISPER_DEVICE=cuda / MEETING_WHISPER_COMPUTE_TYPE=float16
            self._whisper = WhisperModel(
                self._model_size,
                device=self._device,
                compute_type=self._compute_type,
            )
            logger.info(
                "faster-whisper model loaded: %s (device=%s, compute_type=%s)",
                self._model_size, self._device, self._compute_type,
            )
        return self._whisper

    def _whisper_transcribe_chunk(self, chunk_path: Path) -> list[dict[str, Any]]:
        model = self._get_whisper()
        # faster-whisper 调用约定：
        #   segments_iter, info = model.transcribe(audio, ...)
        # 我们已经做过 silencedetect 切分，所以 vad_filter=False 避免重复
        # beam_size=1 提速明显，对中文会议场景精度损失可接受
        # condition_on_previous_text=False 避免长会议重复上下文导致 attention 变慢
        segments_iter, _info = model.transcribe(  # type: ignore[union-attr]
            str(chunk_path),
            language=self._language,
            beam_size=1,
            vad_filter=False,
            condition_on_previous_text=False,
        )
        out: list[dict[str, Any]] = []
        for seg in segments_iter:
            text = (seg.text or "").strip()
            if not text:
                continue
            out.append(
                {
                    "start": float(seg.start or 0),
                    "end": float(seg.end or 0),
                    "text": text,
                    # faster-whisper 的 avg_logprob 越大越好（典型 -1.0 ~ 0.0）
                    "confidence": self._map_logprob_to_conf(getattr(seg, "avg_logprob", None)),
                }
            )
        return out

    @staticmethod
    def _map_logprob_to_conf(avg_logprob: float | None) -> float | None:
        """faster-whisper 没有 segment confidence；用 avg_logprob 估算。

        经验映射：avg_logprob ∈ [-1.0, 0] → confidence ∈ [0, 1]。
        """
        if avg_logprob is None:
            return None
        try:
            v = float(avg_logprob)
        except (TypeError, ValueError):
            return None
        return round(max(0.0, min(1.0, v + 1.0)), 4)

    # ── serialisation ───────────────────────────────────────────────────

    @staticmethod
    def segments_to_json(segments: list[TranscriptSegment]) -> str:
        return json.dumps([s.to_dict() for s in segments], ensure_ascii=False)
