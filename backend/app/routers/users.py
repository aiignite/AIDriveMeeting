"""User search for sharing."""
from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models.user import User
from app.services.auth_service import AuthService

router = APIRouter(prefix="/users", tags=["Users"])


class UserSearchOut(BaseModel):
    id: str
    email: str
    name: str


@router.get("/search", response_model=list[UserSearchOut])
async def search_users(
    q: str = Query(..., min_length=1),
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    users = await AuthService.search_users(db, q, limit=limit)
    return [
        UserSearchOut(id=str(u.id), email=u.email, name=u.name)
        for u in users
        if u.id != user.id
    ]


@router.get("/directory", response_model=list[UserSearchOut])
async def list_user_directory(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """全部有效用户目录（抄送/责任人下拉用）。"""
    from sqlalchemy import select

    result = await db.execute(
        select(User)
        .where(
            User.is_deleted == False,  # noqa: E712
            User.status == "Active",
        )
        .order_by(User.name)
    )
    return [
        UserSearchOut(id=str(u.id), email=u.email, name=u.name)
        for u in result.scalars().all()
    ]
