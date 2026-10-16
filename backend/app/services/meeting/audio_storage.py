"""Meeting audio recording file storage helpers.

Pure filesystem layer — does not touch the database, Whisper, or
background queues. Files are organised as
``static/uploads/meetings/{year}/{month}/{recording_id}.{ext}``.
"""
from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import BinaryIO, Final

# 项目根目录: backend/app/services/meeting/audio_storage.py → 4 级回退
_BACKEND_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_STATIC_ROOT: Final[Path] = _BACKEND_ROOT / "static" / "uploads" / "meetings"

# 文件名安全化：仅保留字母/数字/点/下划线/连字符/中文
_SAFE_NAME_RE: Final[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9._\-一-鿿]+")

# 支持的扩展名（白名单，防止上传到服务器后被改名为 .py 等可执行类型）
ALLOWED_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".mp3",
        ".wav",
        ".m4a",
        ".aac",
        ".flac",
        ".ogg",
        ".opus",
        ".webm",
        ".mp4",
        ".mpeg",
        ".mpga",
    }
)

MAX_FILE_BYTES: Final[int] = 524_288_000  # 500MB，与 config 同步兜底


def build_storage_path(recording_id: str, file_name: str, now: datetime | None = None) -> str:
    """Build a deterministic storage path for a recording.

    Returns a path *relative* to ``backend/`` (forward-slash style)
    so the same string is usable in URLs and in `os.path.join` calls.
    """
    ext = Path(file_name).suffix.lower() or ".bin"
    safe_ext = ext if ext in ALLOWED_EXTENSIONS else ".bin"

    moment = now or datetime.utcnow()
    return f"static/uploads/meetings/{moment.year:04d}/{moment.month:02d}/{recording_id}{safe_ext}"


def absolute_path(storage_path: str) -> Path:
    """Resolve a storage path to an absolute filesystem path."""
    return _BACKEND_ROOT / storage_path


def save_recording_file(
    file_obj: BinaryIO,
    storage_path: str,
    max_bytes: int = MAX_FILE_BYTES,
    chunk_size: int = 1024 * 1024,
) -> int:
    """Stream a file-like object to disk under ``storage_path``.

    Raises ValueError when the upload exceeds ``max_bytes``.
    Returns the number of bytes actually written.
    """
    target = absolute_path(storage_path)
    target.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    try:
        with target.open("wb") as out:
            while True:
                chunk = file_obj.read(chunk_size)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    # 截断已写入内容，回滚
                    out.close()
                    try:
                        target.unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise ValueError(
                        f"recording file exceeds max size {max_bytes} bytes"
                    )
                out.write(chunk)
    except Exception:
        # 写失败时清理半成品
        if target.exists():
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        raise

    return written


def delete_recording_file(storage_path: str) -> bool:
    """Remove a recording file. Returns True if a file was deleted."""
    target = absolute_path(storage_path)
    if not target.exists() or not target.is_file():
        return False
    try:
        target.unlink()
        return True
    except OSError:
        return False


def safe_filename(name: str) -> str:
    """Return a sanitised version of an upload filename (display only)."""
    base = Path(name).name  # strip any path traversal component
    cleaned = _SAFE_NAME_RE.sub("_", base).strip("._-") or "recording"
    return cleaned[:200]


def probe_audio_file(storage_path: str) -> tuple[int, str | None]:
    """Best-effort probe using ffprobe for duration + format.

    Returns ``(duration_seconds, mime_type)``. ``duration_seconds`` is
    ``-1`` when ffprobe is missing or the file cannot be decoded.

    Implementation is intentionally tiny — full probing happens in the
    transcription service; this only does a quick pre-flight.
    """
    import json
    import subprocess

    target = absolute_path(storage_path)
    if not target.exists():
        return -1, None

    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_name",
                "-of",
                "json",
                str(target),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return -1, None

    if result.returncode != 0:
        return -1, None

    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return -1, None

    duration = -1
    fmt = data.get("format") or {}
    dur_raw = fmt.get("duration")
    if dur_raw is not None:
        try:
            duration = int(float(dur_raw))
        except (TypeError, ValueError):
            duration = -1

    mime: str | None = None
    streams = data.get("streams") or []
    if streams and isinstance(streams[0], dict):
        codec = streams[0].get("codec_name")
        if codec:
            mime = f"audio/{codec}"

    return duration, mime


def ensure_directory(storage_path: str) -> None:
    """Idempotently create the parent directory for a storage path."""
    absolute_path(storage_path).parent.mkdir(parents=True, exist_ok=True)


def storage_disk_usage() -> int:
    """Total bytes used by all meeting recordings (best-effort)."""
    if not _STATIC_ROOT.exists():
        return 0
    total = 0
    for dirpath, _dirnames, filenames in os.walk(_STATIC_ROOT):
        for f in filenames:
            try:
                total += (Path(dirpath) / f).stat().st_size
            except OSError:
                continue
    return total


def move_recording_file(storage_path: str, new_storage_path: str) -> bool:
    """Move/rename a stored file. Used by retry/cleanup utilities."""
    src = absolute_path(storage_path)
    dst = absolute_path(new_storage_path)
    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(src), str(dst))
        return True
    except OSError:
        return False
