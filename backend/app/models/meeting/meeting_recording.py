"""Meeting recording & transcript ORM models."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class MtgMeetingRecording(Base):
    """A single audio recording uploaded for a meeting.

    Lifecycle: Uploaded -> Transcribing -> Transcribed | Failed.
    The raw file lives on disk under ``storage_path``; the DB only
    keeps metadata and bookkeeping.
    """

    __tablename__ = "mtg_meeting_recordings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mtg_meetings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    uploader_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="Uploaded", index=True
    )  # Uploaded/Transcribing/Transcribed/Failed
    progress_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    model_size: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    transcripts: Mapped[list[MtgMeetingTranscript]] = relationship(
        back_populates="recording",
        cascade="all, delete-orphan",
        order_by="MtgMeetingTranscript.segment_index",
    )


class MtgMeetingTranscript(Base):
    """A single timed segment produced by the Whisper transcription pipeline."""

    __tablename__ = "mtg_meeting_transcripts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    recording_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("mtg_meeting_recordings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    segment_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    start_time: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    end_time: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    speaker_label: Mapped[str | None] = mapped_column(String(64), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 阶段六 6.3: 章节切分 + 关键词
    chapter_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chapter_title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    keywords: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Relationships
    recording: Mapped[MtgMeetingRecording] = relationship(back_populates="transcripts")
