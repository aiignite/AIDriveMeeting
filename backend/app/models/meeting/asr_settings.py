"""Meeting ASR (Automatic Speech Recognition) settings model.

This module defines the single-row table ``mtg_asr_settings`` used to
persist configurable transcription parameters. The table is designed to
hold exactly one non-deleted record; environment variables in
``app.config`` serve as fallback defaults when no record exists.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MtgAsrSettings(Base):
    """Single-row configuration for meeting transcription ASR engine.

    Attributes:
        id: Primary key UUID.
        mode: ASR mode, either ``local`` (faster-whisper on this host) or
            ``remote`` (LAN GPU ASR gateway).
        remote_url: HTTP endpoint of the remote ASR gateway.
        remote_api_key: Encrypted API key for remote gateway authentication.
        remote_timeout_seconds: Maximum seconds to wait for a remote
            transcription job to complete.
        model: Whisper model size, e.g. ``tiny``, ``base``, ``small``,
            ``medium``, ``large-v3``.
        language: ISO language code; empty string lets Whisper auto-detect.
        device: Compute device for local mode, e.g. ``cpu`` or ``cuda``.
        compute_type: Quantization type for local mode, e.g. ``int8`` or
            ``float16``.
        fallback_to_local: Whether to fall back to local Whisper when the
            remote gateway fails.
        is_deleted: Soft-delete flag.
        created_at: Row creation timestamp.
        updated_at: Row last-modified timestamp.
        created_by: Display name of the user who created the record.
        updated_by: Display name of the user who last modified the record.
    """

    __tablename__ = "mtg_asr_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    mode: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="local",
    )
    remote_url: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
    )
    remote_api_key: Mapped[str | None] = mapped_column(
        String(2048),
        nullable=True,
    )
    remote_timeout_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=3600,
    )
    model: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="medium",
    )
    language: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="zh",
    )
    device: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="cpu",
    )
    compute_type: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="int8",
    )
    fallback_to_local: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    is_deleted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    created_by: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    updated_by: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
