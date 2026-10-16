"""Speaker diarization service — multi-strategy adapter.

Three implementations are available, tried in order of capability
and external dependency cost:

1. ``QwenDiarizer``     — 阿里通义听悟 (paraformer-realtime-v2 + 说话人
   分离).  Best Chinese ASR + native speaker tags, needs
   ``DASHSCOPE_API_KEY`` env var.  Returns ``Speaker_NN`` labels
   and a confidence per turn.
2. ``PyannoteDiarizer``  — pyannote-audio pipeline 3.1.  Needs
   HF token + the pipeline model weights.  Currently **not
   installed by default**; if the user has it, the adapter
   activates, otherwise it falls back to strategy 3.
3. ``EnergyVADDiarizer`` — Zero-dep fallback.  Uses
   ``ffmpeg silencedetect`` to slice audio at silent boundaries
   and labels each non-silent slice with a running counter
   (Speaker 1, Speaker 2, …).  Cheap and deterministic.

All three return a list of :class:`SpeakerTurn` records with
``start``, ``end`` (seconds) and ``speaker_label`` (string).
Callers (mostly :mod:`TranscriptionService`) intersect these
turns with Whisper segments to fill in ``speaker_label``.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SpeakerTurn:
    """A contiguous interval owned by one speaker."""

    start: float
    end: float
    speaker_label: str  # "Speaker 1" or "user:<uuid>" once resolved
    confidence: float = 1.0  # 0..1; only set for cloud providers

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "speaker_label": self.speaker_label,
            "confidence": self.confidence,
        }


class Diarizer(Protocol):
    name: str

    async def diarize(
        self, storage_path: str, language: str | None = None
    ) -> list[SpeakerTurn]: ...


def _resolve_absolute(storage_path: str) -> Path:
    backend_root = Path(__file__).resolve().parents[3]
    return (backend_root / storage_path).resolve()


# ────────────────────────────────────────────────────────────────────
# 3) Energy / silence-based fallback (always available)
# ────────────────────────────────────────────────────────────────────


class EnergyVADDiarizer:
    """Coarse speaker segmentation via ffmpeg silencedetect.

    This is **not** true speaker diarization — it cannot tell *who*
    is talking, only that *someone* is talking.  It is the always-on
    fallback so the UI can still show "Speaker 1 / 2 / 3" colour
    stripes instead of empty labels.
    """

    name = "energy-vad"

    def __init__(self, *, min_turn_seconds: float = 1.0) -> None:
        self.min_turn_seconds = max(0.1, float(min_turn_seconds))

    async def diarize(
        self, storage_path: str, language: str | None = None
    ) -> list[SpeakerTurn]:
        if shutil.which("ffmpeg") is None:
            logger.warning("EnergyVADDiarizer: ffmpeg not available; emitting single Speaker 1 turn")
            return [SpeakerTurn(start=0.0, end=0.0, speaker_label="Speaker 1", confidence=0.0)]

        target = _resolve_absolute(storage_path)
        if not target.exists():
            return []

        # silencedetect writes silence_start / silence_end to stderr.
        # 解析沉默区间，再补出说话区间。
        try:
            result = subprocess.run(  # noqa: ASYNC221  async 中 subprocess 调用（框架限制）
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-nostats",
                    "-i",
                    str(target),
                    "-af",
                    "silencedetect=noise=-30dB:d=0.5",
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
            return [SpeakerTurn(start=0.0, end=0.0, speaker_label="Speaker 1", confidence=0.0)]

        output = (result.stderr or "") + (result.stdout or "")
        silences: list[tuple[float, float]] = []
        starts: list[float] = []
        ends: list[float] = []
        for line in output.splitlines():
            if "silence_start:" in line:
                try:
                    starts.append(float(line.rsplit(":", 1)[-1].strip()))
                except ValueError:
                    continue
            elif "silence_end:" in line:
                try:
                    head = line.rsplit("silence_end:", 1)[-1].strip()
                    head = head.split("|", 1)[0].strip()
                    ends.append(float(head))
                except ValueError:
                    continue

        for s, e in zip(starts, ends, strict=False):
            silences.append((s, e))

        # 估计总时长（用 ffprobe 拿真实 duration）
        total = self._probe_duration(target)

        # 沉默 → 说话区间
        speech: list[tuple[float, float]] = []
        cursor = 0.0
        for s, e in silences:
            if s - cursor >= 0.1:
                speech.append((cursor, s))
            cursor = max(cursor, e)
        if total - cursor >= 0.1:
            speech.append((cursor, total))

        if not speech:
            return [SpeakerTurn(0.0, total or 0.0, "Speaker 1", 0.0)]

        # 用"沉默长度 > 0.6s"作为切人标记 — 一个连续说话区间是同一个人
        # 这里没真说话人识别；只是给每段标个递增的 Speaker N
        turns: list[SpeakerTurn] = []
        idx = 1
        for s, e in speech:
            turns.append(SpeakerTurn(start=s, end=e, speaker_label=f"Speaker {idx}"))
            idx += 1
        return turns

    @staticmethod
    def _probe_duration(target: Path) -> float:
        try:
            r = subprocess.run(
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
                timeout=15,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return 0.0
        try:
            return float((r.stdout or "").strip())
        except ValueError:
            return 0.0


# ────────────────────────────────────────────────────────────────────
# 2) pyannote (optional, lazy)
# ────────────────────────────────────────────────────────────────────


class PyannoteDiarizer:
    """Speaker diarization via pyannote-audio 3.1.

    Requires:
        * ``pyannote.audio`` installed
        * HuggingFace token with access to ``pyannote/speaker-diarization-3.1``
        * Token via ``PYANNOTE_AUTH_TOKEN`` env var

    If any of the above is missing, the adapter raises
    :class:`DiarizationUnavailable` so the orchestrator can fall
    back to the next strategy.
    """

    name = "pyannote"

    def __init__(self, *, auth_token: str | None = None) -> None:
        self._auth_token = auth_token or os.getenv("PYANNOTE_AUTH_TOKEN")
        self._pipeline = None  # lazy

    def _ensure_pipeline(self):
        if self._pipeline is not None:
            return self._pipeline
        try:
            from pyannote.audio import Pipeline  # type: ignore[import-not-found]
        except ImportError as exc:
            raise DiarizationUnavailable("pyannote.audio not installed") from exc
        if not self._auth_token:
            raise DiarizationUnavailable("PYANNOTE_AUTH_TOKEN env var not set")
        self._pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=self._auth_token,
        )
        return self._pipeline

    async def diarize(
        self, storage_path: str, language: str | None = None
    ) -> list[SpeakerTurn]:
        # pyannote API is sync; off-load to thread to keep event loop free
        import asyncio
        target = _resolve_absolute(storage_path)
        if not target.exists():
            return []

        def _run() -> list[SpeakerTurn]:
            pipeline = self._ensure_pipeline()
            diarization = pipeline(str(target))
            turns: list[SpeakerTurn] = []
            for turn, _, speaker in diarization.itertracks(yield_label=True):
                turns.append(
                    SpeakerTurn(
                        start=float(turn.start),
                        end=float(turn.end),
                        speaker_label=f"pyannote:{speaker}",
                        confidence=0.9,
                    )
                )
            return turns

        return await asyncio.to_thread(_run)


# ────────────────────────────────────────────────────────────────────
# 1) Qwen / 通义听悟 (optional, cloud)
# ────────────────────────────────────────────────────────────────────


class QwenDiarizer:
    """Aliyun 通义听悟 — 阿里云 ASR + 说话人分离 (paraformer-realtime-v2).

    Requires ``DASHSCOPE_API_KEY`` env var.  Returns turns with
    speaker ids like ``S1`` / ``S2`` and per-segment text.
    """

    name = "qwen-tingwu"

    def __init__(self, *, api_key: str | None = None) -> None:
        self._api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        self._sdk = None  # lazy

    def _ensure_sdk(self):
        if self._sdk is not None:
            return self._sdk
        if not self._api_key:
            raise DiarizationUnavailable("DASHSCOPE_API_KEY env var not set")
        try:
            import dashscope  # type: ignore[import-not-found]
            from dashscope.audio.asr import Recognition  # type: ignore[import-not-found]
        except ImportError as exc:
            raise DiarizationUnavailable("dashscope SDK not installed") from exc
        dashscope.api_key = self._api_key
        self._sdk = dashscope
        self._recognition_cls = Recognition
        return dashscope

    async def diarize(
        self, storage_path: str, language: str | None = None
    ) -> list[SpeakerTurn]:
        # 通义支持本地文件直接传；这里走 file_urls 形式
        target = _resolve_absolute(storage_path)
        if not target.exists():
            return []
        sdk = self._ensure_sdk()
        return await _run_in_thread(self._diarize_sync, sdk, target, language)

    @staticmethod
    def _diarize_sync(sdk, target: Path, language: str | None) -> list[SpeakerTurn]:
        try:
            response = sdk.audio.asr.Recognition.call(
                model="paraformer-realtime-v2",
                file_urls=[f"file://{target}"],
                language_hints=[language] if language else [],
                diarization_enabled=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise DiarizationUnavailable(f"qwen call failed: {exc}") from exc
        if not response or getattr(response, "output", None) is None:
            return []
        out: list[SpeakerTurn] = []
        sentences = (response.output.sentences or []) if hasattr(response.output, "sentences") else []
        for s in sentences:
            speaker = getattr(s, "speaker_id", None) or "S1"
            text = getattr(s, "text", "")  # noqa: F841  保留赋值以备后续使用或避免副作用丢失
            start = float(getattr(s, "begin_time", 0) or 0) / 1000.0
            end = float(getattr(s, "end_time", 0) or 0) / 1000.0
            confidence = float(getattr(s, "confidence", 0) or 0)
            out.append(
                SpeakerTurn(
                    start=start,
                    end=end,
                    speaker_label=f"qwen:{speaker}",
                    confidence=confidence,
                )
            )
        return out


# ────────────────────────────────────────────────────────────────────
# Orchestrator
# ────────────────────────────────────────────────────────────────────


class DiarizationUnavailable(RuntimeError):  # noqa: N818  异常名无 Error 后缀（业务约定）
    """Raised when a specific diarizer cannot run (missing dep / key)."""


class DiarizationService:
    """Public entry point used by :mod:`TranscriptionService`.

    Resolution order: Qwen → Pyannote → EnergyVAD.  Each tier
    attempts to run, and if a tier reports
    :class:`DiarizationUnavailable` the next one is tried.  The
    final tier is always available because it depends only on
    ``ffmpeg``.
    """

    PREFERRED_ORDER = ("qwen", "pyannote", "energy-vad")

    def __init__(self) -> None:
        self._diarizers: dict[str, Diarizer] = {}
        self._register_default()

    def _register_default(self) -> None:
        # 总是注册能量 VAD fallback
        self._diarizers["energy-vad"] = EnergyVADDiarizer()
        # 按 key 注册，可选
        try:
            self._diarizers["pyannote"] = PyannoteDiarizer()
        except Exception:  # noqa: BLE001
            logger.debug("pyannote diarizer not registered (dep / token missing)")
        try:
            self._diarizers["qwen"] = QwenDiarizer()
        except Exception:  # noqa: BLE001
            logger.debug("qwen diarizer not registered (key / dep missing)")

    def register(self, key: str, diarizer: Diarizer) -> None:
        self._diarizers[key] = diarizer

    def available_strategies(self) -> list[str]:
        return [k for k in self.PREFERRED_ORDER if k in self._diarizers]

    async def diarize(
        self, storage_path: str, language: str | None = None
    ) -> tuple[list[SpeakerTurn], str]:
        """Run diarization, returning (turns, used_strategy)."""
        last_exc: BaseException | None = None
        for key in self.PREFERRED_ORDER:
            diarizer = self._diarizers.get(key)
            if diarizer is None:
                continue
            try:
                turns = await diarizer.diarize(storage_path, language=language)
                if not turns:
                    continue
                logger.info(
                    "DiarizationService: using %s for %s → %d turns",
                    key, storage_path, len(turns),
                )
                return turns, key
            except DiarizationUnavailable as exc:
                last_exc = exc
                logger.info("DiarizationService: %s unavailable: %s", key, exc)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                logger.warning("DiarizationService: %s failed: %s", key, exc)

        # 所有策略都失败时用占位标签
        if last_exc is not None:
            logger.warning("DiarizationService: all strategies failed, using single Speaker 1 placeholder")
        return [SpeakerTurn(0.0, 0.0, "Speaker 1", 0.0)], "energy-vad"


# ────────────────────────────────────────────────────────────────────
# Intersect helper
# ────────────────────────────────────────────────────────────────────


def assign_speaker_label(
    start: float,
    end: float,
    turns: Iterable[SpeakerTurn],
) -> str:
    """Return the speaker label whose [start,end] best overlaps ``[start,end]``."""
    best: SpeakerTurn | None = None
    best_overlap = 0.0
    for turn in turns:
        overlap = max(0.0, min(end, turn.end) - max(start, turn.start))
        if overlap > best_overlap:
            best_overlap = overlap
            best = turn
    return best.speaker_label if best else "Speaker ?"


async def _run_in_thread(func, *args):
    """Small helper to run sync code in a thread for async adapters."""
    import asyncio
    return await asyncio.to_thread(func, *args)
