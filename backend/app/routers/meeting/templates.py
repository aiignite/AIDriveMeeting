"""Meeting templates REST API (议程/纪要模板 CRUD).

Seeded system templates (21 套) come from
``app.services.meeting.seed_meeting_templates`` at startup.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.exceptions import BadRequestException, NotFoundException
from app.models.meeting import MtgMeetingTemplate
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/meeting-templates", tags=["Meeting Templates"])


class MeetingTemplateCreate(BaseModel):
    name: str
    meeting_type: str | None = None
    description: str | None = None
    template_data: dict | None = None


class MeetingTemplateUpdate(BaseModel):
    name: str | None = None
    meeting_type: str | None = None
    description: str | None = None
    version: str | None = None
    template_data: dict | None = None
    is_active: bool | None = None
    tags: str | None = None


class MeetingTemplateOut(BaseModel):
    model_config = {"from_attributes": True}
    id: UUID
    template_number: str
    name: str
    meeting_type: str | None = None
    description: str | None = None
    version: str | None = None
    template_data: dict | None = None
    is_system: bool
    is_active: bool
    tags: str | None = None
    created_at: datetime
    updated_at: datetime


async def _next_template_number(db: AsyncSession) -> str:
    prefix = "MTG-TPL-CUSTOM-"
    result = await db.execute(
        select(MtgMeetingTemplate.template_number)
        .where(MtgMeetingTemplate.is_deleted == False)  # noqa: E712
        .order_by(MtgMeetingTemplate.created_at.desc())
    )
    max_seq = 0
    for (no,) in result.all():
        if no and no.startswith(prefix):
            try:
                max_seq = max(max_seq, int(no.removeprefix(prefix)))
            except ValueError:
                continue
    return f"{prefix}{max_seq + 1:04d}"


@router.get("", response_model=list[MeetingTemplateOut])
async def list_templates(
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    stmt = select(MtgMeetingTemplate).where(MtgMeetingTemplate.is_deleted == False)  # noqa: E712
    if not include_inactive:
        stmt = stmt.where(MtgMeetingTemplate.is_active == True)  # noqa: E712
    stmt = stmt.order_by(MtgMeetingTemplate.is_system.desc(), MtgMeetingTemplate.created_at.asc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post(
    "",
    response_model=MeetingTemplateOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_template(
    body: MeetingTemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not (body.name or "").strip():
        raise BadRequestException("模板名称不能为空")
    template = MtgMeetingTemplate(
        template_number=await _next_template_number(db),
        name=body.name.strip(),
        meeting_type=body.meeting_type,
        description=body.description,
        version="1.0",
        template_data=body.template_data or {},
        is_system=False,
        is_active=True,
        created_by=current_user.name,
        updated_by=current_user.name,
    )
    db.add(template)
    await db.commit()
    result = await db.execute(select(MtgMeetingTemplate).where(MtgMeetingTemplate.id == template.id))
    return result.scalar_one()


@router.patch("/{template_id}", response_model=MeetingTemplateOut)
async def update_template(
    template_id: UUID,
    body: MeetingTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(MtgMeetingTemplate).where(
            MtgMeetingTemplate.id == template_id,
            MtgMeetingTemplate.is_deleted == False,  # noqa: E712
        )
    )
    template = result.scalar_one_or_none()
    if not template:
        raise NotFoundException("Meeting template not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(template, key, value)
    template.updated_by = current_user.name
    await db.commit()
    result = await db.execute(select(MtgMeetingTemplate).where(MtgMeetingTemplate.id == template.id))
    return result.scalar_one()


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(MtgMeetingTemplate).where(
            MtgMeetingTemplate.id == template_id,
            MtgMeetingTemplate.is_deleted == False,  # noqa: E712
        )
    )
    template = result.scalar_one_or_none()
    if not template:
        raise NotFoundException("Meeting template not found")
    if template.is_system:
        raise BadRequestException("系统预置模板不可删除")
    template.is_deleted = True
    await db.commit()


async def get_template_data(db: AsyncSession, template_id: UUID) -> dict[str, Any] | None:
    template = (
        await db.execute(
            select(MtgMeetingTemplate).where(
                MtgMeetingTemplate.id == template_id,
                MtgMeetingTemplate.is_deleted == False,  # noqa: E712
            )
        )
    ).scalar_one_or_none()
    return (template.template_data or {}) if template else None
