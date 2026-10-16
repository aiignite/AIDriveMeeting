"""Unified LLM dispatch for meeting services (refinement / minutes).

Resolves a provider/model through the AIDriveMetting AI platform
(``app.services.ai.llm_router.LLMRouter`` — DB models first, then the
``OLLAMA_*`` env fallback) and performs a single non-streaming
completion via the shared provider factory.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.services.ai.llm_router import LLMRouter
from app.services.ai_providers.base import (
    AIProviderConfig,
    ChatMessage,
    ChatOptions,
)
from app.services.ai_providers.factory import AIProviderFactory

logger = logging.getLogger(__name__)


def _is_content_filter_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "content filter" in text or "content_filter" in text or "sensitive" in text


def _is_provider_unavailable_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "unavailable" in text or "connection" in text or "timeout" in text


async def dispatch_llm(
    db: AsyncSession | None,
    *,
    system_prompt: str,
    user_prompt: str,
    user_id: Any = None,
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.2,
) -> tuple[str, str | None]:
    """Run one completion. Returns ``(text, model_name)``.

    Resolution cascade (LLMRouter): explicit ``model`` → user default →
    first DB model → env Ollama fallback. Raises on total failure.
    """
    settings = get_settings()
    resolution = await LLMRouter.resolve(
        db,  # type: ignore[argType]
        user_id,  # type: ignore[argType]
        request_model=model or settings.MEETING_AI_MODEL or None,
        temperature=temperature,
    )
    provider = AIProviderFactory.create(
        resolution.provider,
        AIProviderConfig(
            model=resolution.model_id,
            base_url=resolution.endpoint,
            api_key=resolution.api_key,
            temperature=temperature,
        ),
    )
    messages = [
        ChatMessage(role="system", content=system_prompt),
        ChatMessage(role="user", content=user_prompt),
    ]
    options = ChatOptions(
        model=resolution.model_id,
        temperature=temperature,
        max_tokens=max_tokens,
        base_url=resolution.endpoint,
        api_key=resolution.api_key,
    )
    response = await provider.chat_with_tools(messages, options)
    text = (response.get("content") or "").strip()
    logger.info(
        "meeting LLM done (provider=%s, model=%s, prompt_chars=%d)",
        resolution.provider,
        resolution.model_name,
        len(system_prompt) + len(user_prompt),
    )
    return text, resolution.model_name


async def dispatch_llm_with_retry(
    db: AsyncSession | None,
    *,
    system_prompt: str,
    user_prompt: str,
    user_id: Any = None,
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.2,
) -> tuple[str, str | None]:
    """dispatch_llm + compact retry on content-filter errors."""
    try:
        return await dispatch_llm(
            db,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            user_id=user_id,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    except Exception as exc:  # noqa: BLE001
        if not _is_content_filter_error(exc):
            raise

    compact_user = user_prompt
    if len(compact_user) > 12_000:
        compact_user = compact_user[:12_000] + "\n…(已截断，请基于可见转写整理)"
    return await dispatch_llm(
        db,
        system_prompt=system_prompt,
        user_prompt=compact_user,
        user_id=user_id,
        model=model,
        max_tokens=2048,
        temperature=temperature,
    )
