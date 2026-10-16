"""Meeting module — service layer for audio recording, transcription,
and AI-driven minutes orchestration.

These services are intentionally router-agnostic and DB-session
agnostic. Routers inject ``AsyncSession`` and pass it explicitly.
"""

from .audio_storage import (
    ALLOWED_EXTENSIONS,
    MAX_FILE_BYTES,
    build_storage_path,
    delete_recording_file,
    safe_filename,
    save_recording_file,
    storage_disk_usage,
)
from .meeting_recording_service import MeetingRecordingService
from .minutes_orchestration_service import MinutesOrchestrationService
from .transcription_service import TranscriptionService

__all__ = [
    "ALLOWED_EXTENSIONS",
    "MAX_FILE_BYTES",
    "build_storage_path",
    "delete_recording_file",
    "save_recording_file",
    "safe_filename",
    "storage_disk_usage",
    "MeetingRecordingService",
    "TranscriptionService",
    "MinutesOrchestrationService",
]
