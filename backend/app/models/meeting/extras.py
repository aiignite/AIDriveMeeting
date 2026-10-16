"""Meeting platform extras: meeting templates and user common-terms library."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class MtgMeetingTemplate(Base):
    """会议模板（议程 HTML + 纪要骨架 HTML），对齐原 tpl_templates(template_type=meeting)。"""

    __tablename__ = "mtg_meeting_templates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    template_number: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    meeting_type: Mapped[str | None] = mapped_column(String(50))  # Regular/Review/Planning/...
    description: Mapped[str | None] = mapped_column(Text)
    version: Mapped[str | None] = mapped_column(String(20), default="1.0")
    template_data: Mapped[dict | None] = mapped_column(JSONB)
    # {meetingType, agendaHtml, minutesHtml}
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    tags: Mapped[str | None] = mapped_column(String(255))
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    created_by: Mapped[str | None] = mapped_column(String(128))
    updated_by: Mapped[str | None] = mapped_column(String(128))


class MtgCommonTerm(Base):
    """常用词库：用户维护的领域词条，转写润色与 AI 纪要生成时注入提示词（常用词投喂 AI）。"""

    __tablename__ = "mtg_common_terms"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    term: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    alias: Mapped[str | None] = mapped_column(String(255))  # 同音/易错写法提示，逗号分隔
    remark: Mapped[str | None] = mapped_column(Text)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    usage_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    created_by: Mapped[str | None] = mapped_column(String(128))
