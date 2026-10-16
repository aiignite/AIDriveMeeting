"""Persist AI-drafted action items into ``mtg_action_items``.

This service is invoked by the meeting-minutes router when a user
clicks "应用草稿" on the :class:`MeetingMinutesAIDraftPanel`.
It parses the AI-drafted ``action_items`` list (each item is a
``{description, owner?, due?, priority?}`` dict) and writes one
row to ``mtg_action_items`` for each, fuzzy-matching owner strings
against the ``users`` table.

Best-effort: persistence of minutes is **not** rolled back if a
later step fails — we prefer partial success (草稿先落地).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.meeting import (
    Meeting,
    MeetingActionItem,
    MeetingMinutes,
)
from app.models.user import User

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ActionItemPersistResult:
    created: int = 0
    matched_users: int = 0
    unmatched_owners: list[str] = None  # type: ignore[assignment]
    dispatched_tasks: int = 0
    dispatch_failures: list[str] = None  # type: ignore[assignment]

    def to_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "matched_users": self.matched_users,
            "unmatched_owners": list(self.unmatched_owners or []),
            "dispatched_tasks": self.dispatched_tasks,
            "dispatch_failures": list(self.dispatch_failures or []),
        }


# ── helpers ────────────────────────────────────────────────────────────

_DATE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})"),
    re.compile(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})"),
]


def _parse_due(value: Any) -> date | None:
    """Best-effort due-date parser.

    Accepts ISO 8601 (``YYYY-MM-DD``), ``YYYY/MM/DD``, ``MM/DD/YYYY``
    and Python ``date`` / ``datetime`` instances.  Returns ``None``
    when nothing matches.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s:
        return None
    for pat in _DATE_PATTERNS:
        m = pat.search(s)
        if not m:
            continue
        try:
            if len(m.groups()) == 3 and len(m.group(1)) == 4:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            else:
                mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            return date(y, mo, d)
        except ValueError:
            continue
    return None


def _normalise_priority(value: Any) -> str:
    p = str(value or "").strip().lower()
    if p in {"high", "h", "1", "p0", "p1"}:
        return "High"
    if p in {"low", "l", "3", "p3"}:
        return "Low"
    return "Medium"


async def _resolve_owner(
    db: AsyncSession, owner_str: str | None
) -> UUID | None:
    """Fuzzy match an owner string against sys_users.

    Tries:
        1. exact email match
        2. exact ``name`` match
        3. case-insensitive contains on ``name`` or ``email``
    """
    if not owner_str:
        return None
    needle = owner_str.strip()
    if not needle:
        return None

    # 1) exact email
    user = (
        await db.execute(select(User).where(User.email == needle))
    ).scalar_one_or_none()
    if user:
        return user.id

    # 2) exact name
    user = (
        await db.execute(select(User).where(User.name == needle))
    ).scalar_one_or_none()
    if user:
        return user.id

    # 3) ILIKE contains on name / email
    pat = f"%{needle}%"
    user = (
        await db.execute(
            select(User)
            .where((User.name.ilike(pat)) | (User.email.ilike(pat)))
            .limit(1)
        )
    ).scalar_one_or_none()
    return user.id if user else None


# ── public API ──────────────────────────────────────────────────────────


class MinutesActionItemService:
    """Apply an AI minutes-draft's action_items to PLM tables."""

    @staticmethod
    async def persist_from_draft(
        db: AsyncSession,
        minutes_id: UUID,
        action_items: list[dict[str, Any]],
        *,
        current_user: User | None = None,
        dispatch_to_task_info: bool = True,
    ) -> ActionItemPersistResult:
        if not action_items:
            return ActionItemPersistResult(unmatched_owners=[], dispatch_failures=[])

        # 1) 找 minutes / meeting
        minutes = (
            await db.execute(
                select(MeetingMinutes).where(MeetingMinutes.id == minutes_id)
            )
        ).scalar_one_or_none()
        if minutes is None:
            raise ValueError(f"MeetingMinutes {minutes_id} not found")

        meeting = (
            await db.execute(
                select(Meeting).where(Meeting.id == minutes.meeting_id)
            )
        ).scalar_one_or_none()
        if meeting is None:
            raise ValueError(f"Meeting {minutes.meeting_id} not found")

        result = ActionItemPersistResult(unmatched_owners=[], dispatch_failures=[])

        # 2) 解析 + 写 mtg_action_items
        for raw in action_items:
            description = (raw.get("description") or "").strip()
            if not description:
                continue

            owner_str = raw.get("owner") or raw.get("assignee") or None
            assignee_id = await _resolve_owner(db, owner_str)
            if owner_str and not assignee_id:
                result.unmatched_owners.append(owner_str)
            else:
                result.matched_users += 1 if assignee_id else 0

            item = MeetingActionItem(
                meeting_id=meeting.id,
                description=description,
                assignee_id=assignee_id,
                due_date=_parse_due(raw.get("due") or raw.get("due_date")),
                priority=_normalise_priority(raw.get("priority")),
                status="Open",
                is_deleted=False,
            )
            db.add(item)
            await db.flush()  # 拿 id
            result.created += 1

        await db.commit()
        return result
