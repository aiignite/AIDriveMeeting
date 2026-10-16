"""GPU / CPU Whisper transcription for asr_gateway."""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class SegmentOut(BaseModel):
    start: float
    end: float
    text: str
    confidence: float | None = None
    language: str | None = None


class TranscribeResult(BaseModel):
    segments: list[SegmentOut] = Field(default_factory=list)


class TranscriptionEngine:
    def __init__(
        self,
        *,
        model_size: str = "medium",
        device: str = "cuda",
        compute_type: str = "float16",
    ) -> None:
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._models: dict[str, object] = {}

    def ensure_loaded(self, model_size: str | None = None) -> None:
        size = model_size or self.model_size
        self._get_model(size)

    def _get_model(self, model_size: str):
        if model_size not in self._models:
            from faster_whisper import WhisperModel

            # Prefer CUDA; fall back to CPU int8 if GPU unavailable
            device = self.device
            compute = self.compute_type
            try:
                self._models[model_size] = WhisperModel(
                    model_size, device=device, compute_type=compute
                )
            except Exception as exc:  # noqa: BLE001
                if device != "cpu":
                    logger.warning(
                        "CUDA load failed (%s); falling back to cpu/int8", exc
                    )
                    device = "cpu"
                    compute = "int8"
                    self.device = device
                    self.compute_type = compute
                    self._models[model_size] = WhisperModel(
                        model_size, device=device, compute_type=compute
                    )
                else:
                    raise
            logger.info(
                "WhisperModel loaded: %s device=%s compute=%s",
                model_size,
                device,
                compute,
            )
        return self._models[model_size]

    def transcribe(
        self,
        path: Path,
        *,
        language: str | None = "zh",
        model_size: str | None = None,
    ) -> TranscribeResult:
        size = model_size or self.model_size
        model = self._get_model(size)

        # Prefer 16k mono wav. Without ffmpeg, non-wav (webm/opus) often hangs or fails.
        work = path
        tmp_wav: Path | None = None
        if path.suffix.lower() not in {".wav"}:
            if not shutil.which("ffmpeg"):
                raise RuntimeError(
                    f"ffmpeg not found; cannot decode {path.suffix}. "
                    "Install ffmpeg on this host, or upload WAV from PLM."
                )
            tmp_wav = path.with_suffix(".16k.wav")
            try:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-y",
                        "-i",
                        str(path),
                        "-ar",
                        "16000",
                        "-ac",
                        "1",
                        str(tmp_wav),
                    ],
                    check=True,
                    capture_output=True,
                    timeout=600,
                )
                work = tmp_wav
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"ffmpeg convert failed: {exc}") from exc

        segments_iter, info = model.transcribe(  # type: ignore[union-attr]
            str(work),
            language=language or None,
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        lang = getattr(info, "language", language)
        out: list[SegmentOut] = []
        for seg in segments_iter:
            text = (seg.text or "").strip()
            if not text:
                continue
            conf = None
            if hasattr(seg, "avg_logprob") and seg.avg_logprob is not None:
                # Map logprob (~-1..0) to a soft 0..1 score
                conf = max(0.0, min(1.0, 1.0 + float(seg.avg_logprob)))
            out.append(
                SegmentOut(
                    start=float(seg.start),
                    end=float(seg.end),
                    text=text,
                    confidence=conf,
                    language=lang,
                )
            )
        if tmp_wav and tmp_wav.exists():
            try:
                tmp_wav.unlink()
            except OSError:
                pass
        return TranscribeResult(segments=out)
