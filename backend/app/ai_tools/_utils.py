"""Shared helpers for AI tool modules."""
from __future__ import annotations

from typing import Any
from uuid import UUID


def validate_uuid(
    value: str, field_name: str
) -> tuple[UUID, None] | tuple[None, dict[str, Any]]:
    """Validate *value* is a valid UUID string.

    Returns ``(uuid_obj, None)`` on success, or
    ``(None, error_dict)`` on failure — ready to be returned
    directly from a tool handler.

    返回类型使用 Union tuple 形式，便于 pyright 在 ``if err: return`` 后
    正确推断 uuid_obj 为 ``UUID`` 而非 ``UUID | None``。
    """
    try:
        return UUID(value), None
    except (ValueError, AttributeError):
        return None, {
            "success": False,
            "error": (
                f"{field_name} 必须是有效的 UUID 格式"
                f"（如 '550e8400-e29b-41d4-a716-446655440000'），"
                f"但收到的是 '{value}'。"
                f"请先调用对应的列表查询工具获取正确的 UUID。"
            ),
        }
