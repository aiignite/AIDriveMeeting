"""Common terms library (常用词库) REST API.

用户维护的领域词条，转写润色与 AI 纪要生成时注入提示词（常用词投喂 AI）。
"""
from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.exceptions import BadRequestException, NotFoundException
from app.models.meeting import MtgCommonTerm
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/common-terms", tags=["Common Terms"])


class CommonTermCreate(BaseModel):
    term: str = Field(min_length=1, max_length=64)
    alias: str | None = None
    remark: str | None = None
    is_enabled: bool = True


class CommonTermUpdate(BaseModel):
    term: str | None = Field(default=None, min_length=1, max_length=64)
    alias: str | None = None
    remark: str | None = None
    is_enabled: bool | None = None


class CommonTermOut(BaseModel):
    model_config = {"from_attributes": True}
    id: UUID
    term: str
    alias: str | None = None
    remark: str | None = None
    is_enabled: bool
    usage_count: int
    created_at: datetime
    updated_at: datetime


@router.get("", response_model=list[CommonTermOut])
async def list_terms(
    keyword: str | None = Query(None, description="按词条/别名关键词过滤"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    stmt = select(MtgCommonTerm).where(MtgCommonTerm.is_deleted == False)  # noqa: E712
    if keyword:
        pattern = f"%{keyword}%"
        stmt = stmt.where(
            (MtgCommonTerm.term.ilike(pattern)) | (MtgCommonTerm.alias.ilike(pattern))
        )
    stmt = stmt.order_by(MtgCommonTerm.is_enabled.desc(), MtgCommonTerm.created_at.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=CommonTermOut, status_code=status.HTTP_201_CREATED)
async def create_term(
    body: CommonTermCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    term_value = body.term.strip()
    if not term_value:
        raise BadRequestException("词条不能为空")
    dup = (
        await db.execute(
            select(MtgCommonTerm).where(
                MtgCommonTerm.is_deleted == False,  # noqa: E712
                MtgCommonTerm.term == term_value,
            )
        )
    ).scalar_one_or_none()
    if dup:
        raise BadRequestException(f"词条已存在：{term_value}")

    item = MtgCommonTerm(
        term=term_value,
        alias=(body.alias or "").strip() or None,
        remark=body.remark,
        is_enabled=body.is_enabled,
        created_by=current_user.name,
    )
    db.add(item)
    await db.commit()
    result = await db.execute(select(MtgCommonTerm).where(MtgCommonTerm.id == item.id))
    return result.scalar_one()


@router.patch("/{term_id}", response_model=CommonTermOut)
async def update_term(
    term_id: UUID,
    body: CommonTermUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(MtgCommonTerm).where(
            MtgCommonTerm.id == term_id,
            MtgCommonTerm.is_deleted == False,  # noqa: E712
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise NotFoundException("Common term not found")
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    await db.commit()
    result = await db.execute(select(MtgCommonTerm).where(MtgCommonTerm.id == item.id))
    return result.scalar_one()


@router.delete("/{term_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_term(
    term_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(MtgCommonTerm).where(
            MtgCommonTerm.id == term_id,
            MtgCommonTerm.is_deleted == False,  # noqa: E712
        )
    )
    item = result.scalar_one_or_none()
    if not item:
        raise NotFoundException("Common term not found")
    item.is_deleted = True
    await db.commit()
