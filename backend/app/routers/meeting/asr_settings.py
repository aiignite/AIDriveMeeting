"""Meeting ASR settings REST API.

Endpoints expose the single-row ``mtg_asr_settings`` configuration in
camelCase for the frontend. The remote API key is masked on read and
encrypted on write.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.meeting import MtgAsrSettings
from app.auth import get_current_user
from app.exceptions import BadRequestException
from app.models.user import User
from app.services.meeting.asr_settings_service import MtgAsrSettingsService
from app.services.system.secret_crypto_service import mask_secret

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/meeting", tags=["Meeting ASR Settings"])


# ── Schemas ───────────────────────────────────────────────────────────────


class AsrSettingsBase(BaseModel):
    """Shared ASR settings fields (camelCase for the frontend)."""

    mode: str = "local"
    remote_url: str | None = None
    remote_api_key: str | None = None
    remote_timeout_seconds: int = Field(default=3600, ge=30)
    model: str = "medium"
    language: str = "zh"
    device: str = "cpu"
    compute_type: str = "int8"
    fallback_to_local: bool = False


class AsrSettingsUpdate(BaseModel):
    """Request body for saving ASR settings."""

    mode: str = "local"
    remote_url: str | None = None
    remote_api_key: str | None = None
    remote_timeout_seconds: int = Field(default=3600, ge=30)
    model: str = "medium"
    language: str = "zh"
    device: str = "cpu"
    compute_type: str = "int8"
    fallback_to_local: bool = False

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, value: str) -> str:
        normalized = (value or "local").strip().lower()
        if normalized not in {"local", "remote"}:
            raise ValueError("mode must be 'local' or 'remote'")
        return normalized

    @field_validator("remote_url")
    @classmethod
    def _validate_remote_url(cls, value: str | None, info: Any) -> str | None:
        mode = (info.data.get("mode") or "local").strip().lower()
        url = (value or "").strip() or None
        if mode == "remote" and not url:
            raise ValueError("remote_url is required when mode is 'remote'")
        return url


class AsrSettingsOut(BaseModel):
    """Response schema for ASR settings (read)."""

    model_config = {"from_attributes": True}

    id: UUID
    mode: str
    remote_url: str | None
    remote_api_key: str | None
    remote_timeout_seconds: int
    model: str
    language: str
    device: str
    compute_type: str
    fallback_to_local: bool
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    updated_by: str | None


class RemoteTestResult(BaseModel):
    """Response schema for the remote ASR health probe."""

    reachable: bool
    detail: str


# ── Helpers ───────────────────────────────────────────────────────────────


def _model_to_out(row: MtgAsrSettings) -> AsrSettingsOut:
    """Convert an ORM row to the camelCase response schema."""
    return AsrSettingsOut(
        id=row.id,
        mode=row.mode,
        remote_url=row.remote_url,
        remote_api_key=mask_secret(row.remote_api_key),
        remote_timeout_seconds=row.remote_timeout_seconds,
        model=row.model,
        language=row.language,
        device=row.device,
        compute_type=row.compute_type,
        fallback_to_local=row.fallback_to_local,
        created_at=row.created_at,
        updated_at=row.updated_at,
        created_by=row.created_by,
        updated_by=row.updated_by,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────


@router.get("/asr-settings", response_model=AsrSettingsOut)
async def get_asr_settings(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Return the effective ASR settings.

    If no row has been saved yet, a row seeded from environment defaults
    is returned so the frontend always has a complete configuration.
    """
    row = await MtgAsrSettingsService.get_settings(db)
    if row is None:
        defaults = MtgAsrSettingsService._env_defaults()
        # Build a transient model for serialization.
        row = MtgAsrSettings(
            id=UUID("00000000-0000-0000-0000-000000000000"),
            **defaults,
        )
        row.created_at = datetime.now(UTC)
        row.updated_at = row.created_at
    return _model_to_out(row)


@router.put("/asr-settings", response_model=AsrSettingsOut)
async def update_asr_settings(
    payload: AsrSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Save the ASR settings."""
    if payload.mode == "remote" and not (payload.remote_url or "").strip():
        raise BadRequestException("remote_url is required when mode is 'remote'")

    saved = await MtgAsrSettingsService.save_settings(
        db,
        data=payload.model_dump(),
        user_name=current_user.name,
    )
    return _model_to_out(saved)


@router.post("/asr-settings/test-remote", response_model=RemoteTestResult)
async def test_remote_asr(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Probe the configured remote ASR gateway."""
    return RemoteTestResult(**await MtgAsrSettingsService.test_remote_connection(db))
