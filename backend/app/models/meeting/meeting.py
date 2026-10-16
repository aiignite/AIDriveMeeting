"""Meeting related models."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Meeting(Base):
    """Meeting model — standalone (no project binding)."""
    __tablename__ = "mtg_meetings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    meeting_type: Mapped[str | None] = mapped_column(String(50))  # Regular/Review/Planning/Retrospective/Ad-hoc
    template_key: Mapped[str | None] = mapped_column(String(50))  # 对应会议模板 key
    location: Mapped[str | None] = mapped_column(String(255))
    online_link: Mapped[str | None] = mapped_column(Text)
    scheduled_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    agenda: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="Scheduled")  # Scheduled/In Progress/Completed/Cancelled
    recording_url: Mapped[str | None] = mapped_column(Text)
    recurrence_rule: Mapped[str | None] = mapped_column(String(100))
    organizer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    attendees_ids: Mapped[list | None] = mapped_column(JSONB, default=list)  # 参会人员（姓名字符串列表）
    # 抄送记录：会议内记录被抄送人（姓名列表），不外发系统通知
    cc_list: Mapped[list | None] = mapped_column(JSONB, default=list)
    cc_note: Mapped[str | None] = mapped_column(Text)
    cc_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    minutes: Mapped[list[MeetingMinutes]] = relationship(back_populates="meeting", cascade="all, delete-orphan")
    action_items: Mapped[list[MeetingActionItem]] = relationship(back_populates="meeting", cascade="all, delete-orphan")
    recordings: Mapped[list[MtgMeetingRecording]] = relationship(  # type: ignore[name-defined]  # noqa: F821
        primaryjoin="and_(Meeting.id==MtgMeetingRecording.meeting_id, MtgMeetingRecording.is_deleted==False)",
        viewonly=True,
    )
    # CCB 结构化投票/决议 (对齐原 chg_meeting_attendees/decisions，FK 改为 mtg_meetings.id)
    votes: Mapped[list[MeetingVote]] = relationship(
        back_populates="meeting", cascade="all, delete-orphan",
    )
    decisions: Mapped[list[MeetingDecision]] = relationship(
        back_populates="meeting", cascade="all, delete-orphan",
    )


class MeetingMinutes(Base):
    """Meeting minutes model for recording meeting notes and decisions."""
    __tablename__ = "mtg_minutes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mtg_meetings.id", ondelete="CASCADE"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    decisions: Mapped[dict | None] = mapped_column(JSONB)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    # 状态机：仅 Draft → Confirmed（AI 整理直接落到 content，不再保留独立 ai_draft 字段）
    status: Mapped[str | None] = mapped_column(String(20), default="Draft")  # Draft/Confirmed
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_by_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    meeting: Mapped[Meeting] = relationship(back_populates="minutes")


class MeetingActionItem(Base):
    """Action items from meetings."""
    __tablename__ = "mtg_action_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mtg_meetings.id", ondelete="CASCADE"), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    due_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="Open")  # Open/In Progress/Completed/Cancelled
    priority: Mapped[str] = mapped_column(String(20), default="Medium")
    notes: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    meeting: Mapped[Meeting] = relationship(back_populates="action_items")


class MeetingVote(Base):
    """CCB 会议结构化投票记录 (对齐原 chg_meeting_attendees，FK 改为 mtg_meetings.id)。

    承载参会人 + 角色 + 出席状态 + 投票结果 (Approve/Reject/Abstain) + 评论，
    让所有会议类型都能用结构化投票，CCB 会议默认启用。
    """

    __tablename__ = "mtg_meeting_votes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mtg_meetings.id", ondelete="CASCADE"), nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True,
    )  # 可空：未指定具体用户的角色占位行
    role: Mapped[str] = mapped_column(String(50), nullable=False)
    # chairperson / quality_manager / project_manager / engineering_manager / ...
    attendance: Mapped[str] = mapped_column(String(20), default="Invited")
    # Invited / Attended / Absent / Proxy
    vote: Mapped[str | None] = mapped_column(String(20))
    # Approve / Reject / Abstain
    vote_comment: Mapped[str | None] = mapped_column(Text)

    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )

    # Relationships
    meeting: Mapped[Meeting] = relationship(back_populates="votes")


class MeetingDecision(Base):
    """CCB 会议结构化评审决议 (对齐原 chg_meeting_decisions，FK 改为 mtg_meetings.id)。

    承载决议类型 (Approve/Reject/Conditional/Defer) + 条件 + 行动项摘要 +
    行动责任人/截止/状态。``action_item`` 作为结论附带的行动项摘要，可选同步为
    ``mtg_action_items`` (不双写常驻)；``mtg_minutes.decisions`` (JSONB) 保持
    纪要自由文本，不参与结构化查询。
    """

    __tablename__ = "mtg_meeting_decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    meeting_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("mtg_meetings.id", ondelete="CASCADE"), nullable=False,
    )
    decision_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # Approve / Reject / Conditional / Defer
    conditions: Mapped[str | None] = mapped_column(Text)
    action_item: Mapped[str | None] = mapped_column(Text)  # 结论附带的行动项摘要
    action_assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True,
    )
    action_due_date: Mapped[date | None] = mapped_column(Date)
    action_status: Mapped[str | None] = mapped_column(String(20))
    # Open / InProgress / Completed

    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
    )

    # Relationships
    meeting: Mapped[Meeting] = relationship(back_populates="decisions")
