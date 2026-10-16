"""Meetings router."""
from __future__ import annotations

import logging
import re
import uuid
from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel

logger = logging.getLogger(__name__)
from sqlalchemy import select  # noqa: E402  FastAPI 惯例：app 初始化后再导入路由
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402  FastAPI 惯例：app 初始化后再导入路由
from sqlalchemy.orm import selectinload  # noqa: E402  FastAPI 惯例：app 初始化后再导入路由

from app.database import get_db  # noqa: E402  FastAPI 惯例：app 初始化后再导入路由
from app.models.meeting import (  # noqa: E402  FastAPI 惯例：app 初始化后再导入路由
    Meeting,
    MeetingActionItem,
    MeetingDecision,
    MeetingMinutes,
    MeetingVote,
)
from app.auth import get_current_user  # noqa: E402
from app.exceptions import (  # noqa: E402
    BadRequestException,
    NotFoundException,
)
from app.models.user import User  # noqa: E402
from app.services.meeting.minutes_action_item_service import (  # noqa: E402
    _parse_due,
    _resolve_owner,
)
from app.services.meeting.minutes_orchestration_service import (  # noqa: E402
    MeetingMinutesDraft,
    MinutesOrchestrationService,
)

router = APIRouter(tags=["Meetings"])


# ============ Meeting Schemas ============

class MeetingCreate(BaseModel):
    """创建会议请求 schema。"""
    title: str
    meeting_type: str | None = None
    template_key: str | None = None
    location: str | None = None
    online_link: str | None = None
    scheduled_start: datetime
    scheduled_end: datetime | None = None
    agenda: str | None = None
    recording_url: str | None = None
    recurrence_rule: str | None = None
    attendees_ids: list[str] = []


class MeetingUpdate(BaseModel):
    title: str | None = None
    meeting_type: str | None = None
    template_key: str | None = None
    location: str | None = None
    online_link: str | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    agenda: str | None = None
    recording_url: str | None = None
    recurrence_rule: str | None = None
    status: str | None = None
    attendees_ids: list[str] | None = None


class MeetingListOut(BaseModel):
    """列表接口轻量 DTO：不含纪要/行动项，避免首屏拉回大段 HTML。"""
    model_config = {"from_attributes": True}
    id: UUID
    title: str
    meeting_type: str | None = None
    template_key: str | None = None
    location: str | None = None
    online_link: str | None = None
    scheduled_start: datetime
    scheduled_end: datetime | None = None
    agenda: str | None = None
    status: str
    recording_url: str | None = None
    recurrence_rule: str | None = None
    organizer_id: UUID | None = None
    attendees_ids: list[str] = []
    cc_list: list[str] = []
    cc_note: str | None = None
    cc_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class MeetingOut(MeetingListOut):
    minutes: list[MinutesOut] = []
    action_items: list[ActionItemOut] = []


# ============ Minutes Schemas ============

class MinutesCreate(BaseModel):
    content: str
    decisions: dict | None = None


class MinutesUpdate(BaseModel):
    content: str | None = None
    decisions: dict | None = None
    # 状态机仅允许 Draft → Confirmed（AI 整理直接写到 content，不再走 ai_draft/ai_structured）
    status: str | None = None
    confirmed_at: datetime | None = None
    confirmed_by_id: UUID | None = None


# 状态机允许的迁移表
_MINUTES_STATUS_TRANSITIONS: dict[str, set[str]] = {
    "Draft": {"Confirmed"},
    "Confirmed": set(),  # 一旦确认，不再回退
}


class MinutesOut(BaseModel):
    model_config = {"from_attributes": True}
    id: UUID
    meeting_id: UUID
    content: str
    decisions: dict | None = None
    recorded_by: UUID | None = None
    status: str | None = None
    confirmed_at: datetime | None = None
    confirmed_by_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


# ============ Action Item Schemas ============

class ActionItemCreate(BaseModel):
    description: str
    assignee_id: UUID | None = None
    due_date: date | None = None
    priority: str = "Medium"
    notes: str | None = None


class ActionItemUpdate(BaseModel):
    description: str | None = None
    assignee_id: UUID | None = None
    due_date: date | None = None
    status: str | None = None
    priority: str | None = None
    notes: str | None = None
    completed_at: datetime | None = None


class ActionItemOut(BaseModel):
    model_config = {"from_attributes": True}
    id: UUID
    meeting_id: UUID
    description: str
    assignee_id: UUID | None = None
    due_date: date | None = None
    status: str
    priority: str
    notes: str | None = None
    completed_at: datetime | None = None
    created_at: datetime


# ============ Meeting Endpoints ============

@router.get(
    "/meetings",
    response_model=list[MeetingListOut],
)
async def list_meetings(
    active_only: bool = Query(False),
    keyword: str | None = Query(None, description="按标题/议程关键词搜索"),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """列出全部会议（独立应用无项目归属），支持 active_only 与关键词过滤。"""
    # 列表不 selectinload 纪要/行动项：纪要 content 常为大段 HTML，会拖慢首屏
    stmt = (
        select(Meeting).where(Meeting.is_deleted == False)  # noqa: E712
        )
    if active_only:
        stmt = stmt.where(Meeting.status.in_(["Scheduled", "In Progress"]))
    if keyword:
        pattern = f"%{keyword}%"
        from sqlalchemy import or_
        stmt = stmt.where(or_(Meeting.title.ilike(pattern), Meeting.agenda.ilike(pattern)))

    stmt = stmt.order_by(Meeting.scheduled_start.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post(
    "/meetings",
    response_model=MeetingOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_meeting(
    body: MeetingCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    meeting = Meeting(
        **body.model_dump(),
        organizer_id=current_user.id,
    )
    db.add(meeting)
    await db.commit()

    # Re-query with selectinload (禁止 db.refresh)
    stmt = (
        select(Meeting).where(Meeting.is_deleted == False)  # noqa: E712  SQLAlchemy 查询表达式
        .where(Meeting.id == meeting.id)
        .options(
            selectinload(Meeting.minutes),
            selectinload(Meeting.action_items)
        )
    )
    result = await db.execute(stmt)
    return result.scalar_one()


@router.get(
    "/meetings/{meeting_id}",
    response_model=MeetingOut,
)
async def get_meeting(
    meeting_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    stmt = (
        select(Meeting).where(Meeting.is_deleted == False)  # noqa: E712  SQLAlchemy 查询表达式
        .where(Meeting.id == meeting_id)
        .options(
            selectinload(Meeting.minutes),
            selectinload(Meeting.action_items)
        )
    )
    result = await db.execute(stmt)
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise NotFoundException("Meeting not found")
    return meeting


@router.patch(
    "/meetings/{meeting_id}",
    response_model=MeetingOut,
)
async def update_meeting(
    meeting_id: UUID,
    body: MeetingUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Meeting).where(Meeting.is_deleted == False).where(Meeting.id == meeting_id)  # noqa: E712  SQLAlchemy 查询表达式
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise NotFoundException("Meeting not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(meeting, k, v)
    await db.commit()

    # Reload with relations for MeetingOut serialization
    stmt = (
        select(Meeting).where(Meeting.is_deleted == False)  # noqa: E712  SQLAlchemy 查询表达式
        .where(Meeting.id == meeting_id)
        .options(
            selectinload(Meeting.minutes),
            selectinload(Meeting.action_items)
        )
    )
    result = await db.execute(stmt)
    return result.scalar_one()


@router.delete(
    "/meetings/{meeting_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_meeting(
    meeting_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id)
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise NotFoundException("Meeting not found")
    meeting.is_deleted = True
    await db.commit()


# ============ Minutes Endpoints ============

@router.get(
    "/meetings/{meeting_id}/minutes",
    response_model=list[MinutesOut],
)
async def get_minutes(
    meeting_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    # Verify meeting exists and belongs to project
    meeting_result = await db.execute(
        select(Meeting).where(Meeting.is_deleted == False).where(Meeting.id == meeting_id)  # noqa: E712  SQLAlchemy 查询表达式
    )
    if not meeting_result.scalar_one_or_none():
        raise NotFoundException("Meeting not found")

    result = await db.execute(
        select(MeetingMinutes).where(MeetingMinutes.is_deleted == False).where(MeetingMinutes.meeting_id == meeting_id)  # noqa: E712  SQLAlchemy 查询表达式
    )
    return result.scalars().all()


@router.post(
    "/meetings/{meeting_id}/minutes",
    response_model=MinutesOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_minutes(
    meeting_id: UUID,
    body: MinutesCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Verify meeting exists and belongs to project
    meeting_result = await db.execute(
        select(Meeting).where(Meeting.is_deleted == False).where(Meeting.id == meeting_id)  # noqa: E712  SQLAlchemy 查询表达式
    )
    if not meeting_result.scalar_one_or_none():
        raise NotFoundException("Meeting not found")

    minutes = MeetingMinutes(
        **body.model_dump(),
        meeting_id=meeting_id,
        recorded_by=current_user.id,
    )
    db.add(minutes)
    await db.commit()
    result = await db.execute(select(MeetingMinutes).where(MeetingMinutes.id == minutes.id))
    return result.scalar_one()


@router.patch(
    "/meetings/{meeting_id}/minutes/{minutes_id}",
    response_model=MinutesOut,
)
async def update_minutes(
    meeting_id: UUID,
    minutes_id: UUID,
    body: MinutesUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Verify meeting exists and belongs to project
    meeting_result = await db.execute(
        select(Meeting).where(Meeting.is_deleted == False).where(Meeting.id == meeting_id)  # noqa: E712  SQLAlchemy 查询表达式
    )
    if not meeting_result.scalar_one_or_none():
        raise NotFoundException("Meeting not found")

    result = await db.execute(
        select(MeetingMinutes).where(MeetingMinutes.is_deleted == False).where(  # noqa: E712  SQLAlchemy 查询表达式
            MeetingMinutes.id == minutes_id,
            MeetingMinutes.meeting_id == meeting_id,
        )
    )
    minutes = result.scalar_one_or_none()
    if not minutes:
        raise NotFoundException("Minutes not found")
    update_data = body.model_dump(exclude_unset=True)

    # 状态机校验：仅允许 Draft → Confirmed（不可回退，AI 整理直接写到 content）
    if "status" in update_data and update_data["status"] != minutes.status:
        new_status = update_data["status"]
        allowed = _MINUTES_STATUS_TRANSITIONS.get(minutes.status or "Draft", set())
        if new_status not in allowed:
            raise BadRequestException(
                f"Invalid minutes status transition: {minutes.status} → {new_status}"
            )
        # 进入 Confirmed 时必须由当前用户记录
        if new_status == "Confirmed":
            if "confirmed_by_id" not in update_data:
                update_data["confirmed_by_id"] = current_user.id
            if "confirmed_at" not in update_data:
                update_data["confirmed_at"] = datetime.utcnow()
        # 清空 Confirmed 字段当离开 Confirmed
        if minutes.status == "Confirmed" and new_status != "Confirmed":
            update_data["confirmed_at"] = None
            update_data["confirmed_by_id"] = None

    for k, v in update_data.items():
        setattr(minutes, k, v)
    await db.commit()

    result = await db.execute(select(MeetingMinutes).where(MeetingMinutes.id == minutes.id))
    return result.scalar_one()


# ============ Action Item Endpoints ============

@router.get(
    "/meetings/{meeting_id}/action-items",
    response_model=list[ActionItemOut],
)
async def list_action_items(
    meeting_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    # Verify meeting exists and belongs to project
    meeting_result = await db.execute(
        select(Meeting).where(Meeting.is_deleted == False).where(Meeting.id == meeting_id)  # noqa: E712  SQLAlchemy 查询表达式
    )
    if not meeting_result.scalar_one_or_none():
        raise NotFoundException("Meeting not found")

    result = await db.execute(
        select(MeetingActionItem).where(MeetingActionItem.is_deleted == False).where(MeetingActionItem.meeting_id == meeting_id)  # noqa: E712  SQLAlchemy 查询表达式
    )
    return result.scalars().all()


@router.post(
    "/meetings/{meeting_id}/action-items",
    response_model=ActionItemOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_action_item(
    meeting_id: UUID,
    body: ActionItemCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    # Verify meeting exists and belongs to project
    meeting_result = await db.execute(
        select(Meeting).where(Meeting.is_deleted == False).where(Meeting.id == meeting_id)  # noqa: E712  SQLAlchemy 查询表达式
    )
    if not meeting_result.scalar_one_or_none():
        raise NotFoundException("Meeting not found")

    action_item = MeetingActionItem(
        **body.model_dump(),
        meeting_id=meeting_id,
    )
    db.add(action_item)
    await db.commit()
    result = await db.execute(select(MeetingActionItem).where(MeetingActionItem.id == action_item.id))
    return result.scalar_one()


@router.patch(
    "/meetings/{meeting_id}/action-items/{item_id}",
    response_model=ActionItemOut,
)
async def update_action_item(
    meeting_id: UUID,
    item_id: UUID,
    body: ActionItemUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    # Verify meeting exists and belongs to project
    meeting_result = await db.execute(
        select(Meeting).where(Meeting.is_deleted == False).where(Meeting.id == meeting_id)  # noqa: E712  SQLAlchemy 查询表达式
    )
    if not meeting_result.scalar_one_or_none():
        raise NotFoundException("Meeting not found")

    result = await db.execute(
        select(MeetingActionItem).where(MeetingActionItem.is_deleted == False).where(  # noqa: E712  SQLAlchemy 查询表达式
            MeetingActionItem.id == item_id,
            MeetingActionItem.meeting_id == meeting_id,
        )
    )
    action_item = result.scalar_one_or_none()
    if not action_item:
        raise NotFoundException("Action item not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(action_item, k, v)
    await db.commit()
    result = await db.execute(select(MeetingActionItem).where(MeetingActionItem.id == action_item.id))
    return result.scalar_one()


@router.delete(
    "/meetings/{meeting_id}/action-items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_action_item(
    meeting_id: UUID,
    item_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    # Verify meeting exists and belongs to project
    meeting_result = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id)
    )
    if not meeting_result.scalar_one_or_none():
        raise NotFoundException("Meeting not found")

    result = await db.execute(
        select(MeetingActionItem).where(
            MeetingActionItem.id == item_id,
            MeetingActionItem.meeting_id == meeting_id,
        )
    )
    action_item = result.scalar_one_or_none()
    if not action_item:
        raise NotFoundException("Action item not found")
    action_item.is_deleted = True
    await db.commit()


# ============ Invite Attendees as Tasks ============

class InviteAsTasksBody(BaseModel):
    """Optional: override attendees; if empty, uses meeting.attendees_ids."""
    attendee_ids: list[str] = []
    due_date: date | None = None


class InviteTasksOut(BaseModel):
    created: int
    task_info_id: str | None = None
    message: str


@router.post(
    "/meetings/{meeting_id}/invite-tasks",
    response_model=InviteTasksOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_invite_tasks(
    meeting_id: UUID,
    body: InviteAsTasksBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """为会议参会人员创建「参加会议」行动项（幂等）。参会人为姓名字符串，
    若与系统用户姓名匹配则自动关联 assignee_id。"""

    result = await db.execute(
        select(Meeting).where(Meeting.is_deleted == False).where(Meeting.id == meeting_id)  # noqa: E712
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise NotFoundException("Meeting not found")

    attendee_ids: list[str] = body.attendee_ids or (meeting.attendees_ids or [])
    if not attendee_ids:
        raise BadRequestException("No attendees specified")

    due_date = body.due_date or (meeting.scheduled_start.date() if meeting.scheduled_start else None)
    items_created = 0
    for name in attendee_ids:
        attendee_name = str(name).strip()
        if not attendee_name:
            continue
        existing = await db.execute(
            select(MeetingActionItem).where(
                MeetingActionItem.is_deleted == False,  # noqa: E712
                MeetingActionItem.meeting_id == meeting_id,
                MeetingActionItem.description == f"参加会议：{meeting.title}",
                MeetingActionItem.notes == f"参会人：{attendee_name}",
            )
        )
        if existing.scalar_one_or_none():
            continue

        assignee_id = await _resolve_owner(db, attendee_name)
        db.add(MeetingActionItem(
            meeting_id=meeting.id,
            description=f"参加会议：{meeting.title}",
            assignee_id=assignee_id,
            due_date=due_date,
            priority="Medium",
            status="Open",
            notes=f"参会人：{attendee_name}",
        ))
        items_created += 1

    await db.commit()
    return InviteTasksOut(
        created=items_created,
        message=f"已为 {items_created} 位参会人员创建参会行动项",
    )


# ============ CC (抄送) Endpoint ============

class MeetingCCRequest(BaseModel):
    user_ids: list[UUID] = []
    names: list[str] = []
    message: str | None = None


class MeetingCCOut(BaseModel):
    sent: int
    message: str


@router.post(
    "/meetings/{meeting_id}/cc",
    response_model=MeetingCCOut,
    summary="抄送会议纪要（会议内记录）",
)
async def cc_meeting(
    meeting_id: UUID,
    body: MeetingCCRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """将抄送对象记录到会议的 cc_list（独立应用不外发系统通知）。"""
    result = await db.execute(select(Meeting).where(Meeting.is_deleted == False).where(Meeting.id == meeting_id))  # noqa: E712
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise NotFoundException("Meeting not found")

    recipients: list[str] = []
    if body.names:
        recipients.extend(n.strip() for n in body.names if n.strip())
    for uid in body.user_ids:
        user = (
            await db.execute(select(User).where(User.is_deleted == False, User.id == uid))  # noqa: E712
        ).scalar_one_or_none()
        if user:
            recipients.append(user.name)

    existing = list(meeting.cc_list or [])
    merged = existing + [n for n in recipients if n and n not in existing]
    meeting.cc_list = merged
    meeting.cc_note = body.message
    meeting.cc_at = datetime.utcnow()
    await db.commit()
    return MeetingCCOut(sent=len(recipients), message=f"已记录抄送给 {len(recipients)} 位对象")


# ── 阶段五任务2：从转写直接生成会议纪要草稿（不走 AI 助手 SSE） ──

class MeetingMinutesGenerateBody(BaseModel):
    recording_id: UUID | None = None
    merge_all_recordings: bool = True
    provider: str | None = None
    model: str | None = None
    base_content: str | None = None


class MeetingMinutesGenerateOut(BaseModel):
    draft: dict
    summary_html: str
    summary_markdown: str
    context_summary: dict | None = None
    minutes_id: UUID | None = None
    saved_content: str | None = None
    model_used: str | None = None


class ExtractTasksBody(BaseModel):
    provider: str | None = None
    model: str | None = None


class ExtractTaskItemOut(BaseModel):
    name: str
    description: str | None = None
    responsible: str | None = None
    responsible_user_id: UUID | None = None
    planned_start: date | None = None
    planned_end: date | None = None
    priority: str | None = "Medium"


class ExtractTasksOut(BaseModel):
    items: list[ExtractTaskItemOut]
    model_used: str | None = None


class ConfirmTaskItemIn(BaseModel):
    name: str
    description: str | None = None
    responsible_user_id: UUID | None = None
    responsible: str | None = None
    planned_start: date | None = None
    planned_end: date | None = None
    priority: str | None = "Medium"


class ConfirmTasksBody(BaseModel):
    items: list[ConfirmTaskItemIn]
    publish: bool = False


class ConfirmTasksOut(BaseModel):
    created: int
    published: int = 0
    task_info_id: UUID | None = None
    item_ids: list[UUID] = []


def _markdown_lines_to_html(text: str) -> list[str]:
    """Convert simple Markdown lines to Quill-friendly HTML."""
    parts: list[str] = []
    list_open = False
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            if list_open:
                parts.append("</ul>")
                list_open = False
            parts.append("<p></p>")
            continue
        if stripped.startswith("### "):
            if list_open:
                parts.append("</ul>")
                list_open = False
            parts.append(f"<h4>{stripped[4:].strip()}</h4>")
        elif stripped.startswith("## "):
            if list_open:
                parts.append("</ul>")
                list_open = False
            parts.append(f"<h3>{stripped[3:].strip()}</h3>")
        elif stripped.startswith("**") and stripped.endswith("**") and stripped.count("**") == 2:
            if list_open:
                parts.append("</ul>")
                list_open = False
            parts.append(f"<p><strong>{stripped[2:-2].strip()}</strong></p>")
        elif stripped.startswith("- "):
            if not list_open:
                parts.append("<ul>")
                list_open = True
            item = stripped[2:].strip()
            item = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", item)
            parts.append(f"<li>{item}</li>")
        else:
            if list_open:
                parts.append("</ul>")
                list_open = False
            body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", stripped)
            parts.append(f"<p>{body}</p>")
    if list_open:
        parts.append("</ul>")
    return parts


def _render_topics_html(topics: list[dict]) -> list[str]:
    """Render structured discussion_topics to HTML."""
    parts: list[str] = []
    if not topics:
        return parts
    parts.append("<p><strong>【议题讨论 · 分类整理】</strong></p>")
    for topic in topics:
        title = topic.get("title") or "未命名主题"
        parts.append(f"<h4>{title}</h4>")
        for label, key in (
            ("讨论要点", "key_points"),
            ("争议与风险", "concerns"),
            ("结论", "conclusions"),
            ("待确认", "open_questions"),
        ):
            items = topic.get(key) or []
            if not items:
                continue
            parts.append(f"<p><strong>{label}</strong></p>")
            parts.append("<ul>")
            for item in items:
                parts.append(f"<li>{item}</li>")
            parts.append("</ul>")
    return parts


def _render_summary_html(draft: MeetingMinutesDraft) -> str:
    """把 LLM 草稿渲染为可粘贴到 Quill 的 HTML（追加到富文本框使用）。"""
    parts: list[str] = []

    if draft.summary:
        parts.append("<p><strong>【会议概要】</strong></p>")
        parts.append(f"<p>{draft.summary}</p>")

    if draft.action_items:
        parts.append("<p><strong>【行动项 · 任务清单】</strong></p>")
        parts.append("<ul>")
        for ai in draft.action_items:
            desc = ai.get("description", "")
            owner = (
                ai.get("owner")
                or ai.get("assignee")
                or ai.get("assignee_name")
                or "待确认"
            )
            due = ai.get("due") or ai.get("due_date") or "待确认"
            priority = ai.get("priority") or "Medium"
            parts.append(
                f"<li><strong>{owner}</strong>：{desc}"
                f"（截止：{due}，优先级：{priority}）</li>"
            )
        parts.append("</ul>")

    if draft.discussion_topics:
        parts.extend(_render_topics_html(draft.discussion_topics))

    if draft.raw_text:
        parts.append("<p><strong>【完整会议纪要】</strong></p>")
        parts.extend(_markdown_lines_to_html(draft.raw_text))

    if draft.decisions:
        parts.append("<p><strong>【决议事项】</strong></p>")
        parts.append("<ul>")
        for d in draft.decisions:
            parts.append(f"<li>{d}</li>")
        parts.append("</ul>")

    return "\n".join(parts) if parts else ""


@router.post(
    "/meetings/{meeting_id}/minutes:generate-from-transcript",
    response_model=MeetingMinutesGenerateOut,
    summary="从会议录音转写生成会议纪要并自动落库",
)
async def generate_minutes_from_transcript(
    meeting_id: UUID,
    body: MeetingMinutesGenerateBody | None = None,
    recording_id: UUID | None = Query(None, description="可选：指定某条录音（兼容旧调用）"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """合并全部已转写录音（默认），调用 LLM 生成纪要 HTML，并自动 create/update mtg_minutes。"""
    payload = body or MeetingMinutesGenerateBody()
    chosen_recording_id = payload.recording_id or recording_id

    result = await db.execute(
        select(Meeting).where(Meeting.is_deleted == False).where(  # noqa: E712
            Meeting.id == meeting_id
        )
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise NotFoundException("Meeting not found")

    orchestrator = MinutesOrchestrationService()
    context = await orchestrator.assemble_context(
        db,
        meeting_id=meeting_id,
        recording_id=chosen_recording_id,
        use_full_transcript=True,
        merge_all_recordings=bool(payload.merge_all_recordings) and chosen_recording_id is None,
    )
    if not context:
        raise NotFoundException("Meeting not found or has no context")

    if not context.get("transcript_text"):
        raise BadRequestException("当前会议没有可用的转写，请先等待转写完成")

    import time
    t0 = time.perf_counter()
    draft: MeetingMinutesDraft = await orchestrator.generate_draft(
        context,
        provider=payload.provider,
        model=payload.model,
        db=db,
        user_id=current_user.id,
    )
    logger.info(
        "generate_minutes_from_transcript meeting=%s segments=%d recordings=%s elapsed=%.1fs model=%s",
        meeting_id,
        context.get("transcript_segments_count", 0),
        len(context.get("recordings") or []),
        time.perf_counter() - t0,
        draft.model,
    )
    summary_html = _render_summary_html(draft)
    summary_md = orchestrator._render_draft_markdown(draft)

    # 自动落库：追加到现有纪要或新建
    existing = (
        await db.execute(
            select(MeetingMinutes)
            .where(MeetingMinutes.is_deleted == False)  # noqa: E712
            .where(MeetingMinutes.meeting_id == meeting_id)
            .order_by(MeetingMinutes.created_at.asc())
        )
    ).scalars().first()

    base = (payload.base_content if payload.base_content is not None else (
        existing.content if existing else ""
    )) or ""
    sep = "<hr/>" if base.strip() else ""
    saved_content = f"{base}{sep}{summary_html}" if summary_html else base

    if existing:
        existing.content = saved_content
        await db.commit()
        minutes_id = existing.id
    else:
        minutes = MeetingMinutes(
            content=saved_content,
            meeting_id=meeting_id,
            recorded_by=current_user.id,
            status="Draft",
        )
        db.add(minutes)
        await db.commit()
        minutes_id = minutes.id

    return MeetingMinutesGenerateOut(
        draft=draft.to_dict(),
        summary_html=summary_html,
        summary_markdown=summary_md,
        minutes_id=minutes_id,
        saved_content=saved_content,
        model_used=draft.model,
        context_summary={
            "has_transcript": bool(context.get("transcript_text")),
            "segments_count": context.get("transcript_segments_count", 0),
            "recordings_count": len(context.get("recordings") or []),
            "existing_action_items": len(context.get("action_items") or []),
        },
    )


@router.post(
    "/meetings/{meeting_id}/tasks:extract-from-minutes",
    response_model=ExtractTasksOut,
    summary="根据会议纪要内容抽取任务候选列表",
)
async def extract_tasks_from_minutes(
    meeting_id: UUID,
    body: ExtractTasksBody | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    payload = body or ExtractTasksBody()
    meeting = (
        await db.execute(
            select(Meeting).where(Meeting.is_deleted == False).where(  # noqa: E712
                Meeting.id == meeting_id
            )
        )
    ).scalar_one_or_none()
    if not meeting:
        raise NotFoundException("Meeting not found")

    minutes = (
        await db.execute(
            select(MeetingMinutes)
            .where(MeetingMinutes.is_deleted == False)  # noqa: E712
            .where(MeetingMinutes.meeting_id == meeting_id)
            .order_by(MeetingMinutes.created_at.asc())
        )
    ).scalars().first()
    content = (minutes.content if minutes else "") or ""
    if not content.strip():
        raise BadRequestException("当前会议没有会议纪要内容")

    orchestrator = MinutesOrchestrationService()
    raw_items, model_used = await orchestrator.extract_tasks_from_content(
        minutes_content=content,
        meeting={
            "title": meeting.title,
            "scheduled_start": meeting.scheduled_start.isoformat() if meeting.scheduled_start else None,
            "scheduled_end": meeting.scheduled_end.isoformat() if meeting.scheduled_end else None,
        },
        provider=payload.provider,
        model=payload.model,
        db=db,
        user_id=current_user.id,
    )

    items: list[ExtractTaskItemOut] = []
    for raw in raw_items:
        owner_str = raw.get("responsible")
        uid = await _resolve_owner(db, owner_str) if owner_str else None
        start = _parse_due(raw.get("planned_start"))
        end = _parse_due(raw.get("planned_end"))
        items.append(
            ExtractTaskItemOut(
                name=raw["name"],
                description=raw.get("description") or raw["name"],
                responsible=owner_str,
                responsible_user_id=uid,
                planned_start=start,
                planned_end=end,
                priority=raw.get("priority") or "Medium",
            )
        )
    return ExtractTasksOut(items=items, model_used=model_used)


@router.post(
    "/meetings/{meeting_id}/tasks:confirm",
    response_model=ConfirmTasksOut,
    summary="确认会议任务清单并写入会议计划（可选发布）",
)
async def confirm_meeting_tasks(
    meeting_id: UUID,
    body: ConfirmTasksBody,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    meeting = (
        await db.execute(
            select(Meeting).where(Meeting.is_deleted == False).where(  # noqa: E712
                Meeting.id == meeting_id
            )
        )
    ).scalar_one_or_none()
    if not meeting:
        raise NotFoundException("Meeting not found")

    if not body.items:
        raise BadRequestException("任务列表不能为空")

    for idx, item in enumerate(body.items, start=1):
        if not (item.name or "").strip():
            raise BadRequestException(f"第 {idx} 条任务缺少名称")
        if not item.responsible_user_id:
            raise BadRequestException(f"第 {idx} 条任务缺少责任人")
        if not item.planned_start or not item.planned_end:
            raise BadRequestException(f"第 {idx} 条任务缺少计划开始/结束时间")

    # 写 mtg_action_items（会议内任务清单）
    created_ids: list[UUID] = []
    for item in body.items:
        assignee_id = item.responsible_user_id or (
            await _resolve_owner(db, item.responsible) if item.responsible else None
        )
        action = MeetingActionItem(
            meeting_id=meeting.id,
            description=item.name.strip(),
            assignee_id=assignee_id,
            due_date=item.planned_end,
            priority=(item.priority or "Medium"),
            status="Open",
            notes="从会议纪要生成",
            is_deleted=False,
        )
        db.add(action)
        await db.flush()
        created_ids.append(action.id)

    return ConfirmTasksOut(
        created=len(created_ids),
        published=0,
        task_info_id=None,
        item_ids=created_ids,
    )


# ============ Meeting Vote Schemas (SubTask 5b.6) ============

class MeetingVoteCreate(BaseModel):
    """创建会议投票请求 schema。

    承载参会人 + 角色 + 出席状态 + 投票结果 + 评论。``user_id`` 可空表示
    未指定具体用户的角色占位行 (如 CCB 会议骨架预置的 chairperson 待确认)。
    """

    user_id: UUID | None = None
    role: str
    attendance: str = "Invited"
    vote: str | None = None
    vote_comment: str | None = None


class MeetingVoteUpdate(BaseModel):
    """更新会议投票请求 schema (全部字段可选)。"""

    attendance: str | None = None
    vote: str | None = None
    vote_comment: str | None = None


class MeetingVoteOut(BaseModel):
    """会议投票响应 schema。"""

    model_config = {"from_attributes": True}
    id: UUID
    meeting_id: UUID
    user_id: UUID | None = None
    role: str
    attendance: str
    vote: str | None = None
    vote_comment: str | None = None
    created_at: datetime
    updated_at: datetime


# ============ Meeting Decision Schemas (SubTask 5b.6) ============

class MeetingDecisionCreate(BaseModel):
    """创建会议决议请求 schema。

    承载结构化评审结论：``decision_type`` (Approve/Reject/Conditional/Defer) +
    条件 + 行动项摘要 + 行动责任人/截止/状态。``action_item`` 作为结论附带的
    行动项摘要，可选同步为 ``mtg_action_items`` (不双写常驻)。
    """

    decision_type: str
    conditions: str | None = None
    action_item: str | None = None
    action_assignee_id: UUID | None = None
    action_due_date: date | None = None
    action_status: str | None = None


class MeetingDecisionUpdate(BaseModel):
    """更新会议决议请求 schema (全部字段可选)。"""

    decision_type: str | None = None
    conditions: str | None = None
    action_item: str | None = None
    action_assignee_id: UUID | None = None
    action_due_date: date | None = None
    action_status: str | None = None


class MeetingDecisionOut(BaseModel):
    """会议决议响应 schema。"""

    model_config = {"from_attributes": True}
    id: UUID
    meeting_id: UUID
    decision_type: str
    conditions: str | None = None
    action_item: str | None = None
    action_assignee_id: UUID | None = None
    action_due_date: date | None = None
    action_status: str | None = None
    created_at: datetime
    updated_at: datetime


# ============ Meeting Vote Endpoints (SubTask 5b.4) ============

@router.get(
    "/meetings/{meeting_id}/votes",
    response_model=list[MeetingVoteOut],
)
async def list_meeting_votes(
    meeting_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """列出指定会议的全部投票记录 (含未投票的参会人占位行)。"""
    # 校验会议存在且归属当前项目
    meeting_result = await db.execute(
        select(Meeting).where(Meeting.is_deleted == False).where(  # noqa: E712
            Meeting.id == meeting_id
        )
    )
    if not meeting_result.scalar_one_or_none():
        raise NotFoundException("Meeting not found")

    result = await db.execute(
        select(MeetingVote).where(MeetingVote.is_deleted == False)  # noqa: E712
        .where(MeetingVote.meeting_id == meeting_id)
        .order_by(MeetingVote.created_at.asc())
    )
    return result.scalars().all()


@router.post(
    "/meetings/{meeting_id}/votes",
    response_model=MeetingVoteOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_meeting_vote(
    meeting_id: UUID,
    body: MeetingVoteCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """为指定会议创建一条投票记录 (参会人 + 角色 + 投票结果)。"""
    # 校验会议存在且归属当前项目
    meeting_result = await db.execute(
        select(Meeting).where(Meeting.is_deleted == False).where(  # noqa: E712
            Meeting.id == meeting_id
        )
    )
    if not meeting_result.scalar_one_or_none():
        raise NotFoundException("Meeting not found")

    vote = MeetingVote(
        **body.model_dump(),
        meeting_id=meeting_id,
    )
    db.add(vote)
    await db.commit()
    result = await db.execute(select(MeetingVote).where(MeetingVote.id == vote.id))
    return result.scalar_one()


@router.patch(
    "/meetings/{meeting_id}/votes/{vote_id}",
    response_model=MeetingVoteOut,
)
async def update_meeting_vote(
    meeting_id: UUID,
    vote_id: UUID,
    body: MeetingVoteUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """更新指定会议的某条投票记录 (attendance / vote / vote_comment)。"""
    # 校验会议存在且归属当前项目
    meeting_result = await db.execute(
        select(Meeting).where(Meeting.is_deleted == False).where(  # noqa: E712
            Meeting.id == meeting_id
        )
    )
    if not meeting_result.scalar_one_or_none():
        raise NotFoundException("Meeting not found")

    result = await db.execute(
        select(MeetingVote).where(MeetingVote.is_deleted == False).where(  # noqa: E712
            MeetingVote.id == vote_id,
            MeetingVote.meeting_id == meeting_id,
        )
    )
    vote = result.scalar_one_or_none()
    if not vote:
        raise NotFoundException("Vote not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(vote, k, v)
    await db.commit()
    result = await db.execute(select(MeetingVote).where(MeetingVote.id == vote.id))
    return result.scalar_one()


@router.delete(
    "/meetings/{meeting_id}/votes/{vote_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_meeting_vote(
    meeting_id: UUID,
    vote_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """软删除指定会议的某条投票记录 (is_deleted=True)。"""
    # 校验会议存在且归属当前项目
    meeting_result = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id)
    )
    if not meeting_result.scalar_one_or_none():
        raise NotFoundException("Meeting not found")

    result = await db.execute(
        select(MeetingVote).where(
            MeetingVote.id == vote_id,
            MeetingVote.meeting_id == meeting_id,
        )
    )
    vote = result.scalar_one_or_none()
    if not vote:
        raise NotFoundException("Vote not found")
    vote.is_deleted = True
    await db.commit()


# ============ Meeting Decision Endpoints (SubTask 5b.5) ============

@router.get(
    "/meetings/{meeting_id}/decisions",
    response_model=list[MeetingDecisionOut],
)
async def list_meeting_decisions(
    meeting_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """列出指定会议的全部结构化决议。"""
    # 校验会议存在且归属当前项目
    meeting_result = await db.execute(
        select(Meeting).where(Meeting.is_deleted == False).where(  # noqa: E712
            Meeting.id == meeting_id
        )
    )
    if not meeting_result.scalar_one_or_none():
        raise NotFoundException("Meeting not found")

    result = await db.execute(
        select(MeetingDecision).where(MeetingDecision.is_deleted == False)  # noqa: E712
        .where(MeetingDecision.meeting_id == meeting_id)
        .order_by(MeetingDecision.created_at.asc())
    )
    return result.scalars().all()


@router.post(
    "/meetings/{meeting_id}/decisions",
    response_model=MeetingDecisionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_meeting_decision(
    meeting_id: UUID,
    body: MeetingDecisionCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """为指定会议创建一条结构化决议 (decision_type + conditions + action_item 等)。"""
    # 校验会议存在且归属当前项目
    meeting_result = await db.execute(
        select(Meeting).where(Meeting.is_deleted == False).where(  # noqa: E712
            Meeting.id == meeting_id
        )
    )
    if not meeting_result.scalar_one_or_none():
        raise NotFoundException("Meeting not found")

    decision = MeetingDecision(
        **body.model_dump(),
        meeting_id=meeting_id,
    )
    db.add(decision)
    await db.commit()
    result = await db.execute(select(MeetingDecision).where(MeetingDecision.id == decision.id))
    return result.scalar_one()


@router.patch(
    "/meetings/{meeting_id}/decisions/{decision_id}",
    response_model=MeetingDecisionOut,
)
async def update_meeting_decision(
    meeting_id: UUID,
    decision_id: UUID,
    body: MeetingDecisionUpdate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """更新指定会议的某条决议 (decision_type/conditions/action_item 等全部可选)。"""
    # 校验会议存在且归属当前项目
    meeting_result = await db.execute(
        select(Meeting).where(Meeting.is_deleted == False).where(  # noqa: E712
            Meeting.id == meeting_id
        )
    )
    if not meeting_result.scalar_one_or_none():
        raise NotFoundException("Meeting not found")

    result = await db.execute(
        select(MeetingDecision).where(MeetingDecision.is_deleted == False).where(  # noqa: E712
            MeetingDecision.id == decision_id,
            MeetingDecision.meeting_id == meeting_id,
        )
    )
    decision = result.scalar_one_or_none()
    if not decision:
        raise NotFoundException("Decision not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(decision, k, v)
    await db.commit()
    result = await db.execute(select(MeetingDecision).where(MeetingDecision.id == decision.id))
    return result.scalar_one()


@router.delete(
    "/meetings/{meeting_id}/decisions/{decision_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_meeting_decision(
    meeting_id: UUID,
    decision_id: UUID,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """软删除指定会议的某条决议 (is_deleted=True)。"""
    # 校验会议存在且归属当前项目
    meeting_result = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id)
    )
    if not meeting_result.scalar_one_or_none():
        raise NotFoundException("Meeting not found")

    result = await db.execute(
        select(MeetingDecision).where(
            MeetingDecision.id == decision_id,
            MeetingDecision.meeting_id == meeting_id,
        )
    )
    decision = result.scalar_one_or_none()
    if not decision:
        raise NotFoundException("Decision not found")
    decision.is_deleted = True
    await db.commit()

