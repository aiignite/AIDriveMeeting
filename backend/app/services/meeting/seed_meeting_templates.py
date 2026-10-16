"""Seed 21 套系统预置会议模板到 mtg_meeting_templates.

幂等：按 template_number（MTG-TPL-{type}）跳过已存在记录。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.meeting import MtgMeetingTemplate
from app.services.meeting.meeting_template_definitions import (
    MEETING_TYPE_DEFINITIONS,
    definition_to_template_data,
    system_template_number,
)


async def seed_meeting_templates(db: AsyncSession) -> list[MtgMeetingTemplate]:
    """插入 21 套 meeting 系统模板（幂等）。"""
    created: list[MtgMeetingTemplate] = []
    for defn in MEETING_TYPE_DEFINITIONS:
        template_number = system_template_number(defn["value"])
        existing = await db.execute(
            select(MtgMeetingTemplate).where(
                MtgMeetingTemplate.template_number == template_number,
                MtgMeetingTemplate.is_deleted == False,  # noqa: E712
            )
        )
        if existing.scalar_one_or_none() is not None:
            continue

        template = MtgMeetingTemplate(
            template_number=template_number,
            name=defn["label"],
            meeting_type=defn["value"],
            description=defn.get("remark") or f"系统预置: {defn['label']}",
            version="1.0",
            template_data=definition_to_template_data(defn),
            is_system=True,
            is_active=True,
            tags="会议,系统预置",
        )
        db.add(template)
        created.append(template)

    return created
