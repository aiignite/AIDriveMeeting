"""HTTP client for LAN GPU ASR gateway (MEETING_ASR_MODE=remote).

Uploads the full recording as multipart/form-data and maps the
gateway JSON response into TranscriptionService segment dicts.
"""
from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class AsrRemoteError(RuntimeError):
    """Remote ASR gateway call failed."""


class AsrRemoteClient:
    """Thin httpx wrapper around ``POST /api/v1/transcribe``."""

    def __init__(
        self,
        *,
        url: str,
        api_key: str = "",
        timeout_seconds: int = 3600,
    ) -> None:
        self._url = (url or "").strip()
        if not self._url:
            raise AsrRemoteError("MEETING_ASR_REMOTE_URL is empty")
        self._api_key = (api_key or "").strip()
        self._timeout = max(30, int(timeout_seconds or 3600))

    @property
    def health_url(self) -> str:
        """Derive ``.../health`` from the transcribe URL when possible."""
        base = self._url.rstrip("/")
        if base.endswith("/api/v1/transcribe"):
            return base[: -len("/api/v1/transcribe")] + "/health"
        if base.endswith("/transcribe"):
            return base[: -len("/transcribe")] + "/health"
        # Fallback: same host, path /health
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(self._url)
        return urlunparse((parsed.scheme, parsed.netloc, "/health", "", "", ""))

    async def health_check(self) -> bool:
        """Return True if the gateway responds 2xx on /health."""
        ok, _ = await self.health_check_detail()
        return ok

    async def health_check_detail(self) -> tuple[bool, str]:
        """Probe ``/health`` and return ``(ok, human_detail)``."""
        url = self.health_url
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url, headers=self._headers())
            if 200 <= resp.status_code < 300:
                return True, f"remote ASR gateway responded ({resp.status_code}) at {url}"
            if resp.status_code in (401, 403):
                return False, (
                    f"remote ASR auth failed ({resp.status_code}) at {url}; "
                    "请确认页面 API Key 与 Windows ASR_API_KEY / .env.prod 中 MEETING_ASR_REMOTE_API_KEY 一致后重新保存"
                )
            body = (resp.text or "")[:120]
            return False, f"remote ASR health check failed HTTP {resp.status_code} at {url}: {body}"
        except httpx.TimeoutException:
            return False, f"remote ASR health check timeout (5s) connecting to {url}"
        except httpx.HTTPError as exc:
            return False, f"remote ASR connection error to {url}: {exc}"
        except Exception as exc:  # noqa: BLE001
            logger.debug("ASR remote health check failed: %s", exc)
            return False, f"remote ASR health check failed: {exc}"

    async def transcribe_file(
        self,
        file_path: Path,
        *,
        language: str | None = None,
        model_size: str | None = None,
    ) -> list[dict[str, Any]]:
        """Upload ``file_path`` and return raw segment dicts.

        Each dict: ``{start, end, text, confidence?, language?}``.
        """
        if not file_path.exists():  # noqa: ASYNC240  async 中 os.path 调用（框架限制）
            raise AsrRemoteError(f"recording file not found: {file_path}")

        mime, _ = mimetypes.guess_type(str(file_path))
        mime = mime or "application/octet-stream"
        data: dict[str, str] = {}
        if language:
            data["language"] = language
        if model_size:
            data["model_size"] = model_size

        size_mb = file_path.stat().st_size / (1024 * 1024)  # noqa: ASYNC240  async 中 os.path 调用（框架限制）
        logger.info(
            "POST remote ASR %s (%.2f MB, timeout=%ss)",
            self._url,
            size_mb,
            self._timeout,
        )
        # Separate connect vs read: connect failures fail fast; GPU work uses read timeout.
        timeout = httpx.Timeout(
            connect=30.0,
            read=float(self._timeout),
            write=120.0,
            pool=30.0,
        )
        try:
            # Read into memory for small/medium meetings so the multipart body is
            # fully owned by httpx (avoids blocking file IO edge cases).
            payload = file_path.read_bytes()  # noqa: ASYNC240  async 中 os.path 调用（框架限制）
            async with httpx.AsyncClient(timeout=timeout) as client:
                files = {"file": (file_path.name, payload, mime)}
                resp = await client.post(
                    self._url,
                    headers=self._headers(),
                    data=data,
                    files=files,
                )
        except httpx.TimeoutException as exc:
            raise AsrRemoteError(
                f"remote ASR timeout after {self._timeout}s: {self._url}"
            ) from exc
        except httpx.HTTPError as exc:
            raise AsrRemoteError(f"remote ASR connection error: {exc}") from exc
        logger.info(
            "remote ASR HTTP %s in response (%d bytes body)",
            resp.status_code,
            len(resp.content or b""),
        )

        if resp.status_code in (401, 403):
            raise AsrRemoteError(
                f"remote ASR auth failed ({resp.status_code}); check MEETING_ASR_REMOTE_API_KEY"
            )
        if resp.status_code >= 400:
            body = (resp.text or "")[:300]
            raise AsrRemoteError(
                f"remote ASR HTTP {resp.status_code}: {body}"
            )

        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise AsrRemoteError("remote ASR returned non-JSON body") from exc

        return self._parse_segments(payload)

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            return {}
        return {"X-API-Key": self._api_key}

    @staticmethod
    def _parse_segments(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            raw_list = payload
        elif isinstance(payload, dict):
            raw_list = payload.get("segments") or payload.get("result") or []
        else:
            raise AsrRemoteError("remote ASR response shape unsupported")

        if not isinstance(raw_list, list):
            raise AsrRemoteError("remote ASR segments is not a list")

        out: list[dict[str, Any]] = []
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            try:
                start = float(item.get("start") or 0)
                end = float(item.get("end") or start)
            except (TypeError, ValueError):
                continue
            conf = item.get("confidence")
            try:
                confidence = float(conf) if conf is not None else None
            except (TypeError, ValueError):
                confidence = None
            out.append(
                {
                    "start": start,
                    "end": end,
                    "text": text,
                    "confidence": confidence,
                    "language": item.get("language"),
                }
            )
        return out
