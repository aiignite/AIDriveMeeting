"""Meeting AI tools — search meetings, fetch transcript, generate
minutes draft (no persistence — caller decides what to write).

All tools return a dict with a ``success`` boolean so the LLM can
self-correct on errors.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.meeting import (
    Meeting,
    MtgMeetingRecording,
    MtgMeetingTranscript,
)
from app.ai_tools._utils import validate_uuid
from app.ai_tools.registry import ToolRegistry
from app.services.meeting.minutes_orchestration_service import (
    MinutesOrchestrationService,
)

logger = logging.getLogger(__name__)

CATEGORY = "meeting"
MAX_ITEMS = 20


# ── handlers ──────────────────────────────────────────────────────────────


async def _search_meetings(
    db: AsyncSession,
    user_id: UUID,
    *,
    keyword: str | None = None,
    status: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    stmt = (
        select(Meeting)
        .where(Meeting.is_deleted == False)  # noqa: E712  SQLAlchemy 查询表达式
        .order_by(Meeting.scheduled_start.desc())
        .limit(min(limit, MAX_ITEMS))
    )
    if keyword:
        pattern = f"%{keyword}%"
        stmt = stmt.where(
            or_(
                Meeting.title.ilike(pattern),
                Meeting.agenda.ilike(pattern),
            )
        )
    if status:
        stmt = stmt.where(Meeting.status == status)
    result = await db.execute(stmt)
    rows = list(result.scalars().all())
    return {
        "success": True,
        "count": len(rows),
        "meetings": [
            {
                "id": str(m.id),
                "title": m.title,
                "type": m.meeting_type,
                "status": m.status,
                "scheduled_start": m.scheduled_start.isoformat() if m.scheduled_start else None,
                "location": m.location,
                "agenda_preview": (m.agenda or "")[:200],
            }
            for m in rows
        ],
    }


async def _get_meeting_transcript(
    db: AsyncSession,
    user_id: UUID,
    *,
    recording_id: str,
) -> dict[str, Any]:
    parsed, err = validate_uuid(recording_id, "recording_id")
    if err:
        return err

    rec = await db.execute(
        select(MtgMeetingRecording)
        .where(MtgMeetingRecording.is_deleted == False)  # noqa: E712  SQLAlchemy 查询表达式
        .where(MtgMeetingRecording.id == parsed)
    )
    recording = rec.scalar_one_or_none()
    if not recording:
        return {"success": False, "error": f"录音不存在: {recording_id}"}

    segs = await db.execute(
        select(MtgMeetingTranscript)
        .where(MtgMeetingTranscript.is_deleted == False)  # noqa: E712  SQLAlchemy 查询表达式
        .where(MtgMeetingTranscript.recording_id == parsed)
        .order_by(MtgMeetingTranscript.segment_index.asc())
    )
    segments = list(segs.scalars().all())
    full_text = "\n".join(
        f"[{int(s.start_time // 60):02d}:{s.start_time - int(s.start_time // 60) * 60:05.2f}] {s.text}"
        for s in segments
    )
    return {
        "success": True,
        "recording": {
            "id": str(recording.id),
            "meeting_id": str(recording.meeting_id),
            "file_name": recording.file_name,
            "status": recording.status,
            "language": recording.language,
            "model_size": recording.model_size,
            "duration_seconds": recording.duration_seconds,
        },
        "segments_count": len(segments),
        "transcript": full_text,
    }


async def _generate_meeting_minutes(
    db: AsyncSession,
    user_id: UUID,
    *,
    meeting_id: str,
    recording_id: str | None = None,
    use_full_transcript: bool = True,
) -> dict[str, Any]:
    """Generate a minutes draft. **Does NOT write to the database**.

    The caller (typically the frontend) decides whether to write
    the draft to ``mtg_minutes.content`` (the AI summary is rendered
    as HTML and appended to the rich-text editor; the user can then
    save the minutes via PATCH).
    """
    parsed, err = validate_uuid(meeting_id, "meeting_id")
    if err:
        return err

    rec_uuid: UUID | None = None
    if recording_id:
        rec_uuid, err = validate_uuid(recording_id, "recording_id")
        if err:
            return err

    orchestrator = MinutesOrchestrationService()
    context = await orchestrator.assemble_context(
        db,
        meeting_id=parsed,  # type: ignore[reportArgumentType]  类型修复
        recording_id=rec_uuid,
        use_full_transcript=use_full_transcript,
    )
    if not context:
        return {"success": False, "error": f"会议不存在或已删除: {meeting_id}"}

    draft = await orchestrator.generate_draft(
        context,
        db=db,
        user_id=user_id,
    )
    return {
        "success": True,
        "message": (
            "草稿已生成（**未持久化**）。前端会把 summary_html 追加到富文本编辑器末尾，"
            "用户编辑满意后点击「保存纪要」即可写入 mtg_minutes.content。"
            "点击「确认纪要」才会将状态置为 Confirmed。"
        ),
        "draft": draft.to_dict(),
        "context_summary": {
            "has_transcript": bool(context.get("transcript_text")),
            "segments_count": context.get("transcript_segments_count", 0),
            "existing_action_items": len(context.get("action_items") or []),
        },
        "confirm_payload": MinutesOrchestrationService.build_confirm_payload(),
    }


# ── registration ──────────────────────────────────────────────────────────


def register_meeting_tools() -> None:
    ToolRegistry.register(
        "search_meetings",
        {
            "name": "search_meetings",
            "description": (
                "查询会议列表。支持按标题/议程关键词搜索和按状态筛选。"
                "返回会议基础信息（id、标题、类型、状态、时间）。"
                "不返回转写全文；如需全文请配合 get_meeting_transcript 使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "可选的标题/议程关键词",
                    },
                    "status": {
                        "type": "string",
                        "description": "可选的状态过滤：Scheduled/In Progress/Completed/Cancelled",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回的最大条目数，默认 20，最大 20",
                    },
                },
            },
        },
        handler=_search_meetings,
        category=CATEGORY,
        label_zh="搜索会议",
    )

    ToolRegistry.register(
        "get_meeting_transcript",
        {
            "name": "get_meeting_transcript",
            "description": (
                "获取指定录音的完整转写文本（按时间顺序拼接，带时间戳前缀）。"
                "若会议尚未转写完成（status != Transcribed），会返回成功但 transcript 为空字符串。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "recording_id": {
                        "type": "string",
                        "description": "录音 UUID（mtg_meeting_recordings.id）",
                    },
                },
                "required": ["recording_id"],
            },
        },
        handler=_get_meeting_transcript,
        category=CATEGORY,
        label_zh="获取会议转写",
    )

    ToolRegistry.register(
        "generate_meeting_minutes",
        {
            "name": "generate_meeting_minutes",
            "description": (
                "根据会议上下文（基础信息 + 议程 + 参会人 + 转写全文）生成结构化纪要草稿。"
                "**该工具只返回草稿，不写库**。返回的 draft 包含 summary/decisions/action_items/raw_text，"
                "前端会把 summary_html 追加到富文本编辑器末尾，"
                "调用本工具后请提示用户编辑满意后点击「保存纪要」即可持久化到 mtg_minutes.content。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "meeting_id": {
                        "type": "string",
                        "description": "会议 UUID 或标题（系统自动解析）",
                    },
                    "recording_id": {
                        "type": "string",
                        "description": "可选：指定某条录音；不传则自动选取最新已转写的录音",
                    },
                    "use_full_transcript": {
                        "type": "boolean",
                        "description": "是否使用完整转写（>20 段时仍会带上头尾 + 中段省略标记）",
                        "default": True,
                    },
                },
                "required": ["meeting_id"],
            },
        },
        handler=_generate_meeting_minutes,
        category=CATEGORY,
        label_zh="生成会议纪要草稿",
    )


# 模块导入时即注册
register_meeting_tools()
