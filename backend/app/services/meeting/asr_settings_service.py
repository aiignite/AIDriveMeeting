"""Service layer for meeting ASR settings.

Provides persistence, retrieval, and runtime merging of the single-row
``mtg_asr_settings`` configuration with environment variable defaults.
Sensitive fields (remote API key) are encrypted at rest and only
returned to callers in masked form.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.meeting import MtgAsrSettings
from app.services.system.secret_crypto_service import (
    decrypt_secret,
    encrypt_secret,
    is_masked_secret_placeholder,
)

logger = logging.getLogger(__name__)


class MtgAsrSettingsService:
    """Static service class for the single meeting ASR settings row.

    The settings table is expected to contain at most one non-deleted
    record. When no record exists, environment variables from
    ``app.config`` act as the default values.
    """

    #: Fields that can be overridden by the database row at runtime.
    _OVERRIDE_FIELDS: tuple[str, ...] = (
        "mode",
        "remote_url",
        "remote_api_key",
        "remote_timeout_seconds",
        "model",
        "language",
        "device",
        "compute_type",
        "fallback_to_local",
    )

    @staticmethod
    def _base_query() -> select:  # type: ignore[reportGeneralTypeIssues]  类型修复
        """Return base query filtered to non-deleted rows."""
        return select(MtgAsrSettings).where(MtgAsrSettings.is_deleted == False)  # noqa: E712

    @staticmethod
    async def get_settings(db: AsyncSession) -> MtgAsrSettings | None:
        """Return the single non-deleted settings row, if any."""
        result = await db.execute(MtgAsrSettingsService._base_query())
        return result.scalar_one_or_none()

    @staticmethod
    def _env_defaults() -> dict[str, Any]:
        """Build default settings dict from environment variables."""
        settings = get_settings()
        return {
            "mode": (settings.MEETING_ASR_MODE or "local").strip().lower(),
            "remote_url": settings.MEETING_ASR_REMOTE_URL or None,
            "remote_api_key": settings.MEETING_ASR_REMOTE_API_KEY or None,
            "remote_timeout_seconds": max(30, int(settings.MEETING_ASR_REMOTE_TIMEOUT or 3600)),
            "model": settings.MEETING_WHISPER_MODEL or "medium",
            "language": settings.MEETING_WHISPER_LANGUAGE or "zh",
            "device": (settings.MEETING_WHISPER_DEVICE or "cpu").strip() or "cpu",
            "compute_type": (settings.MEETING_WHISPER_COMPUTE_TYPE or "int8").strip() or "int8",
            "fallback_to_local": bool(settings.MEETING_ASR_FALLBACK_LOCAL),
        }

    @staticmethod
    async def get_effective_settings(db: AsyncSession) -> dict[str, Any]:
        """Return merged settings with database values overriding env defaults.

        The returned ``remote_api_key`` is the encrypted stored value (or
        the env value); use :meth:`_decrypt_key` when the plaintext is
        required for outbound HTTP calls.
        """
        effective = MtgAsrSettingsService._env_defaults()
        row = await MtgAsrSettingsService.get_settings(db)
        if row is None:
            return effective

        for field in MtgAsrSettingsService._OVERRIDE_FIELDS:
            value = getattr(row, field, None)
            if field == "remote_api_key":
                # Empty/None DB key must not wipe env default (common after UI save with masked/blank key).
                if value is None or (isinstance(value, str) and not value.strip()):
                    continue
                effective[field] = value
                continue
            if value is not None or field == "remote_url":
                effective[field] = value
        # Enforce a reasonable minimum timeout even if the DB row is bad.
        effective["remote_timeout_seconds"] = max(
            30, int(effective.get("remote_timeout_seconds") or 3600)
        )
        return effective
    @staticmethod
    async def save_settings(
        db: AsyncSession,
        data: dict[str, Any],
        user_name: str | None = None,
    ) -> MtgAsrSettings:
        """Upsert the single settings row.

        ``remote_api_key`` is encrypted at rest. If the incoming value
        matches the masked placeholder, the existing key is preserved.
        """
        row = await MtgAsrSettingsService.get_settings(db)
        is_create = row is None
        if is_create:
            row = MtgAsrSettings()

        # When creating the row for the first time, seed any missing field
        # (including the masked API key placeholder echoed back by the
        # frontend) with the current environment value so the saved row
        # stays consistent with .env defaults.
        env_defaults = MtgAsrSettingsService._env_defaults()

        # Map camelCase input to snake_case model fields.
        field_map: dict[str, str] = {
            "mode": "mode",
            "remoteUrl": "remote_url",
            "remoteApiKey": "remote_api_key",
            "remoteTimeoutSeconds": "remote_timeout_seconds",
            "model": "model",
            "language": "language",
            "device": "device",
            "computeType": "compute_type",
            "fallbackToLocal": "fallback_to_local",
        }
        for camel, snake in field_map.items():
            if camel not in data and snake not in data:
                continue
            value = data.get(camel, data.get(snake))
            if snake == "remote_api_key":
                if is_masked_secret_placeholder(value):
                    if is_create:
                        # Preserve the env-configured key on first save.
                        value = encrypt_secret(env_defaults.get("remote_api_key"))
                    else:
                        # Preserve the existing encrypted key.
                        continue
                else:
                    value = encrypt_secret(value)
            elif snake in {
                "remote_timeout_seconds",
            }:
                value = max(30, int(value)) if value is not None else 3600
            elif snake == "fallback_to_local":
                value = bool(value)
            elif snake == "mode":
                value = (value or "local").strip().lower()
            elif snake in {"model", "language", "device", "compute_type"}:
                value = (value or "").strip()
            elif snake == "remote_url":
                value = (value or "").strip() or None
            setattr(row, snake, value)

        # Normalize mode to one of the supported values.
        if row.mode not in {"local", "remote"}:
            row.mode = "local"

        if is_create:
            row.created_by = user_name
            db.add(row)
        row.updated_by = user_name
        await db.commit()

        # Re-query to return a fresh instance.
        result = await db.execute(MtgAsrSettingsService._base_query())
        return result.scalar_one()

    @staticmethod
    def decrypt_remote_api_key(row: MtgAsrSettings | None) -> str | None:
        """Return the plaintext remote API key for outbound HTTP calls."""
        if row is None:
            return None
        return decrypt_secret(row.remote_api_key)

    @staticmethod
    async def resolve_plaintext_api_key(db: AsyncSession) -> str:
        """Prefer DB-stored key; fall back to env ``MEETING_ASR_REMOTE_API_KEY``."""
        row = await MtgAsrSettingsService.get_settings(db)
        key = MtgAsrSettingsService.decrypt_remote_api_key(row)
        if (key or "").strip():
            return str(key).strip()
        env_key = MtgAsrSettingsService._env_defaults().get("remote_api_key") or ""
        return str(env_key).strip()

    @staticmethod
    async def test_remote_connection(db: AsyncSession) -> dict[str, Any]:
        """Probe the configured remote ASR gateway and report reachability.

        Returns:
            ``{"reachable": bool, "detail": str}``
        """
        from app.services.meeting.asr_remote_client import AsrRemoteClient

        effective = await MtgAsrSettingsService.get_effective_settings(db)
        if effective.get("mode") != "remote":
            return {
                "reachable": False,
                "detail": "ASR mode is not remote",
            }
        url = effective.get("remote_url") or ""
        if not url.strip():
            return {
                "reachable": False,
                "detail": "remote_url is empty",
            }
        api_key = await MtgAsrSettingsService.resolve_plaintext_api_key(db)
        try:
            client = AsrRemoteClient(
                url=url,
                api_key=api_key,
                timeout_seconds=int(effective.get("remote_timeout_seconds") or 3600),
            )
            ok, detail = await client.health_check_detail()
            return {
                "reachable": ok,
                "detail": detail,
            }
        except Exception as exc:  # noqa: BLE001
            logger.debug("remote ASR connection test failed: %s", exc)
            return {
                "reachable": False,
                "detail": f"remote ASR connection test failed: {exc}",
            }
