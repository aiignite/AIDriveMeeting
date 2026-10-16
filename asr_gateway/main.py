"""PLM Meeting ASR Gateway — FastAPI entry (Windows / Linux GPU host).

Endpoints:
  GET  /health
  POST /api/v1/transcribe  (multipart: file, language?, model_size?)
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import tempfile
import time
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel

from transcription import TranscribeResult, TranscriptionEngine

logger = logging.getLogger("asr_gateway")

API_KEY = (os.getenv("ASR_API_KEY") or os.getenv("MEETING_ASR_REMOTE_API_KEY") or "").strip()
DEFAULT_MODEL = os.getenv("ASR_MODEL", "medium")
DEFAULT_LANGUAGE = os.getenv("ASR_LANGUAGE", "zh")
DEVICE = os.getenv("ASR_DEVICE", "cuda")
COMPUTE_TYPE = os.getenv("ASR_COMPUTE_TYPE", "float16")

app = FastAPI(title="PLM Meeting ASR Gateway", version="1.0.0")
_engine: TranscriptionEngine | None = None
# Serialize GPU jobs so overlapping requests don't deadlock CUDA / block forever.
_transcribe_lock = asyncio.Lock()


class HealthOut(BaseModel):
    status: str
    model: str
    device: str
    compute_type: str
    busy: bool = False


def _get_engine() -> TranscriptionEngine:
    global _engine
    if _engine is None:
        _engine = TranscriptionEngine(
            model_size=DEFAULT_MODEL,
            device=DEVICE,
            compute_type=COMPUTE_TYPE,
        )
    return _engine


async def verify_api_key(x_api_key: str | None = Header(None, alias="X-API-Key")) -> None:
    """API Key 校验 — fail-closed：密钥未配置时拒绝服务，防止网关裸奔。"""
    if not API_KEY:
        raise HTTPException(
            status_code=503,
            detail="ASR_API_KEY not configured; refusing unauthenticated access (fail-closed)",
        )
    if not x_api_key or not secrets.compare_digest(x_api_key, API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


@app.get("/health", response_model=HealthOut)
async def health(_: None = Depends(verify_api_key)) -> HealthOut:
    eng = _get_engine()
    return HealthOut(
        status="ok",
        model=eng.model_size,
        device=eng.device,
        compute_type=eng.compute_type,
        busy=_transcribe_lock.locked(),
    )


@app.post("/api/v1/transcribe", response_model=TranscribeResult)
async def transcribe(
    _: None = Depends(verify_api_key),
    file: UploadFile = File(...),
    language: str | None = Form(None),
    model_size: str | None = Form(None),
) -> TranscribeResult:
    suffix = Path(file.filename or "audio.bin").suffix or ".bin"
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="empty file")

    # Keep TemporaryDirectory alive for the whole job (including threadpool work).
    tmp = tempfile.TemporaryDirectory(prefix="asr_")
    try:
        path = Path(tmp.name) / f"upload{suffix}"
        path.write_bytes(raw)
        eng = _get_engine()
        lang = language or DEFAULT_LANGUAGE or None
        logger.info(
            "transcribe start name=%s bytes=%d lang=%s model=%s",
            file.filename,
            len(raw),
            lang,
            model_size or DEFAULT_MODEL,
        )
        t0 = time.perf_counter()
        async with _transcribe_lock:
            try:
                # CRITICAL: Whisper/ffmpeg are sync — must not block the asyncio loop,
                # or /health and subsequent requests hang (PLM UI stuck at 5%).
                result = await asyncio.to_thread(
                    eng.transcribe,
                    path,
                    language=lang,
                    model_size=model_size,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("transcribe failed: %s", exc)
                raise HTTPException(status_code=500, detail=str(exc)[:500]) from exc
        logger.info(
            "transcribe done segments=%d elapsed=%.1fs",
            len(result.segments),
            time.perf_counter() - t0,
        )
        return result
    finally:
        tmp.cleanup()


@app.on_event("startup")
def preload_model() -> None:
    # 密钥缺失时高亮告警：所有 API 请求将被 fail-closed 拒绝
    if not API_KEY:
        logger.critical(
            "ASR_API_KEY is NOT set — all API requests will be rejected (fail-closed). "
            "Set ASR_API_KEY (and MEETING_ASR_REMOTE_API_KEY on PLM side) to enable access."
        )
    # Warm the model so first request is not a multi-minute cold start.
    if os.getenv("ASR_PRELOAD", "1") not in ("0", "false", "False"):
        _get_engine().ensure_loaded()
