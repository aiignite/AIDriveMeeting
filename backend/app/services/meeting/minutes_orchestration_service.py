"""Meeting minutes orchestration — assemble context, ask LLM,
parse structured draft. Pure service layer; never touches the DB
to persist, never reaches into routers or AI tool handlers.

The service exposes two clearly separated actions:
    * ``assemble_context``  — gather everything LLM needs into a
      plain dict.
    * ``generate_draft``    — build the prompt, call the provider,
      parse the JSON, return the draft (no DB write).
    * ``build_confirm_payload`` — build the canonical dict the AI tool
      (or router) needs to PATCH onto ``mtg_minutes``.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.models.meeting import (
    Meeting,
    MtgCommonTerm,
    MtgMeetingRecording,
    MtgMeetingTranscript,
)
from app.models.user import User

logger = logging.getLogger(__name__)

_CONTENT_FILTER_MARKERS = (
    "new_sensitive",
    "1027",
    "output_sensitive",
    "input_sensitive",
    "content_filter",
    "content filter",
)


def _is_content_filter_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(marker in msg for marker in _CONTENT_FILTER_MARKERS)


def _is_provider_unavailable_error(exc: BaseException) -> bool:
    """Missing API key / client not init / Ollama down — try next model."""
    msg = str(exc).lower()
    markers = (
        "client not initialized",
        "api key",
        "not configured",
        "ollama service unavailable",
        "provider unavailable",
        "has no attribute 'client'",
    )
    return any(m in msg for m in markers)


# ── Pydantic-light result dataclasses (no extra dependency) ──────────────


@dataclass(slots=True)
class MeetingMinutesDraft:
    """Structured minutes draft returned by the LLM."""

    summary: str
    decisions: list[str]
    action_items: list[dict[str, Any]]
    raw_text: str
    discussion_topics: list[dict[str, Any]] | None = None
    model: str | None = None
    language: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "decisions": self.decisions,
            "action_items": self.action_items,
            "raw_text": self.raw_text,
            "discussion_topics": self.discussion_topics or [],
            "model": self.model,
            "language": self.language,
        }


# ── Public service ────────────────────────────────────────────────────────


class MinutesOrchestrationService:
    """Context assembler + LLM caller + payload builder for minutes."""

    # 上限控制：防止超长转写把上下文撑爆
    MAX_TRANSCRIPT_CHARS = 24_000
    MAX_AGENDA_CHARS = 4_000
    MAX_TEMPLATE_CHARS = 4_000
    MAX_PERSONNEL_NAMES = 400  # 人员管理名单写入 prompt 的上限

    # ── context assembly ───────────────────────────────────────────────

    async def assemble_context(
        self,
        db: AsyncSession,
        *,
        meeting_id: UUID,
        recording_id: UUID | None = None,
        use_full_transcript: bool = True,
        merge_all_recordings: bool = True,
    ) -> dict[str, Any]:
        meeting = await self._load_meeting(db, meeting_id)
        if not meeting:
            return {}

        transcripts: list[MtgMeetingTranscript] = []
        chosen_recording: MtgMeetingRecording | None = None
        merged_blocks: list[str] = []
        recording_meta: list[dict[str, Any]] = []

        if recording_id:
            # 显式指定单条录音
            chosen_recording = await self._load_recording(db, recording_id)
            if chosen_recording is not None:
                transcripts = await self._load_transcripts(db, chosen_recording.id)
                transcript_text = self._join_transcripts(
                    transcripts, full=use_full_transcript
                )
            else:
                transcript_text = ""
        elif merge_all_recordings:
            # 合并会议下全部已转写录音（按创建时间）
            recordings = sorted(
                [r for r in (meeting.recordings or []) if r.status == "Transcribed"],
                key=lambda r: r.created_at or r.updated_at,
            )
            for idx, rec in enumerate(recordings, start=1):
                segs = await self._load_transcripts(db, rec.id)
                joined = self._join_transcripts(segs, full=use_full_transcript)
                if not joined.strip():
                    continue
                transcripts.extend(segs)
                merged_blocks.append(f"## 录音 {idx}：{rec.file_name}\n{joined}")
                recording_meta.append(
                    {
                        "id": str(rec.id),
                        "file_name": rec.file_name,
                        "status": rec.status,
                        "segments": len(segs),
                    }
                )
            if recordings:
                chosen_recording = recordings[-1]
            transcript_text = "\n\n".join(merged_blocks)
        else:
            # 兼容旧行为：取最新一条已转写录音
            if meeting.recordings:
                chosen_recording = next(
                    (r for r in meeting.recordings if r.status == "Transcribed"),
                    meeting.recordings[0],
                )
            if chosen_recording is not None:
                transcripts = await self._load_transcripts(db, chosen_recording.id)
                transcript_text = self._join_transcripts(
                    transcripts, full=use_full_transcript
                )
            else:
                transcript_text = ""

        if len(transcript_text) > self.MAX_TRANSCRIPT_CHARS:
            transcript_text = transcript_text[: self.MAX_TRANSCRIPT_CHARS] + "\n…(已截断)"

        attendees_ids = [str(uid) for uid in (meeting.attendees_ids or []) if uid]
        attendees, personnel_roster = await self._load_personnel_name_context(
            db,
            attendees_ids=attendees_ids,
            organizer_id=meeting.organizer_id,
        )
        # 常用词投喂 AI：加载启用的常用词库词条
        terms_result = await db.execute(
            select(MtgCommonTerm)
            .where(
                MtgCommonTerm.is_deleted == False,  # noqa: E712
                MtgCommonTerm.is_enabled == True,  # noqa: E712
            )
            .order_by(MtgCommonTerm.usage_count.desc(), MtgCommonTerm.created_at.asc())
        )
        common_terms = [t.term for t in terms_result.scalars().all() if t.term]

        return {
            "meeting": {
                "id": str(meeting.id),
                "title": meeting.title,
                "type": meeting.meeting_type or "",
                "agenda": (meeting.agenda or "")[: self.MAX_AGENDA_CHARS],
                "location": meeting.location or "",
                "online_link": meeting.online_link or "",
                "scheduled_start": meeting.scheduled_start.isoformat() if meeting.scheduled_start else None,
                "scheduled_end": meeting.scheduled_end.isoformat() if meeting.scheduled_end else None,
                "status": meeting.status,
                "attendees_ids": attendees_ids,
                "attendees": attendees,
                "organizer_id": str(meeting.organizer_id) if meeting.organizer_id else None,
            },
            "personnel_roster": personnel_roster,
            "common_terms": common_terms,
            "action_items": [
                {
                    "id": str(ai.id),
                    "description": ai.description,
                    "status": ai.status,
                    "priority": ai.priority,
                    "assignee_id": str(ai.assignee_id) if ai.assignee_id else None,
                    "due_date": ai.due_date.isoformat() if ai.due_date else None,
                }
                for ai in meeting.action_items
            ],
            "recording": {
                "id": str(chosen_recording.id),
                "file_name": chosen_recording.file_name,
                "duration_seconds": chosen_recording.duration_seconds,
                "status": chosen_recording.status,
                "model_size": chosen_recording.model_size,
                "language": chosen_recording.language,
            }
            if chosen_recording
            else None,
            "recordings": recording_meta,
            "transcript_text": transcript_text,
            "transcript_segments_count": len(transcripts),
        }

    # ── LLM prompt + draft generation ──────────────────────────────────

    async def generate_draft(
        self,
        context: dict[str, Any],
        *,
        provider: str | None = None,
        model: str | None = None,
        db: AsyncSession | None = None,
        user_id: UUID | None = None,
    ) -> MeetingMinutesDraft:
        """Run the LLM to produce a structured minutes draft.

        Returns the parsed draft. **No DB write happens here** —
        persistence is the caller's responsibility (the AI tool or
        the router's "应用草稿" handler).
        """
        system_prompt = self._build_system_prompt()
        user_prompt = self._build_user_prompt(context)

        try:
            raw_text, used_model = await self._call_provider(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                provider=provider,
                model=model,
                db=db,
                user_id=user_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("LLM call failed: %s", exc)
            reason = (
                "内容安全策略拦截，已尝试切换备用 AI 提供商仍失败"
                if _is_content_filter_error(exc)
                else str(exc)
            )
            return self._fallback_draft(context, reason=reason, model=model)

        return self._parse_draft(raw_text, used_model, context)

    # ── payload builders (used by AI tools / router) ───────────────────

    @staticmethod
    def build_confirm_payload() -> dict[str, Any]:
        """Payload to PATCH when the user clicks '确认纪要'."""
        from datetime import datetime
        return {
            "status": "Confirmed",
            "confirmed_at": datetime.now(UTC).isoformat(),
        }

    # ── internal helpers ───────────────────────────────────────────────

    async def _load_meeting(self, db: AsyncSession, meeting_id: UUID) -> Meeting | None:
        stmt = (
            select(Meeting)
            .where(Meeting.is_deleted == False)  # noqa: E712  SQLAlchemy 查询表达式
            .where(Meeting.id == meeting_id)
            .options(
                selectinload(Meeting.minutes),
                selectinload(Meeting.action_items),
                selectinload(Meeting.recordings),
            )
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def _load_recording(
        self, db: AsyncSession, recording_id: UUID
    ) -> MtgMeetingRecording | None:
        stmt = (
            select(MtgMeetingRecording)
            .where(MtgMeetingRecording.is_deleted == False)  # noqa: E712  SQLAlchemy 查询表达式
            .where(MtgMeetingRecording.id == recording_id)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    async def _load_transcripts(
        self, db: AsyncSession, recording_id: UUID
    ) -> list[MtgMeetingTranscript]:
        stmt = (
            select(MtgMeetingTranscript)
            .where(MtgMeetingTranscript.is_deleted == False)  # noqa: E712
            .where(MtgMeetingTranscript.recording_id == recording_id)
            .order_by(MtgMeetingTranscript.segment_index.asc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    def _user_display_name(user: User) -> str:
        """Account display name for minutes cross-checking."""
        return (user.name or "").strip()

    async def _load_personnel_name_context(
        self,
        db: AsyncSession,
        *,
        attendees_ids: list[str],
        organizer_id: UUID | None,
    ) -> tuple[list[dict[str, str]], list[str]]:
        """Resolve attendee names + active user roster for LLM.

        attendees_ids 为参会人姓名字符串列表（独立会议应用无项目成员目录）。
        Roster comes from 用户表（姓名），用于转写人名与参会人名的二次确认比对。
        """
        attendees = [
            {"name": str(raw).strip(), "role": "attendee"}
            for raw in attendees_ids
            if str(raw).strip()
        ]
        if organizer_id is not None:
            org_result = await db.execute(
                select(User).where(
                    User.is_deleted == False,  # noqa: E712
                    User.id == organizer_id,
                )
            )
            org = org_result.scalar_one_or_none()
            if org:
                attendees.insert(0, {
                    "id": str(org.id),
                    "name": self._user_display_name(org),
                    "account": (org.name or "").strip(),
                    "role": "organizer",
                })

        roster_result = await db.execute(
            select(User)
            .where(
                User.is_deleted == False,  # noqa: E712
                User.status == "Active",
            )
            .order_by(User.name.asc())
            .limit(self.MAX_PERSONNEL_NAMES)
        )
        seen: set[str] = set()
        personnel_roster: list[str] = []
        for user in roster_result.scalars().all():
            display = self._user_display_name(user)
            if display and display not in seen:
                seen.add(display)
                personnel_roster.append(display)

        return attendees, personnel_roster

    @staticmethod
    def _format_time_range(start: float, end: float) -> str:
        def _fmt(seconds: float) -> str:
            mm = int(seconds // 60)
            ss = int(seconds - mm * 60)
            return f"{mm:02d}:{ss:02d}"
        return f"{_fmt(start)}-{_fmt(end)}"

    @classmethod
    def _join_transcripts(
        cls,
        segments: list[MtgMeetingTranscript],
        *,
        full: bool = True,
    ) -> str:
        if not segments:
            return ""
        chosen = segments
        middle_note = ""
        if not full and len(segments) > 20:
            head = segments[:10]
            tail = segments[-5:]
            middle_note = f"\n... (省略 {len(segments) - 15} 段) ...\n"
            chosen = head + tail

        # 按 chapter_id 分组，输出长段落格式
        chapters: dict[int, list[MtgMeetingTranscript]] = {}
        chapter_titles: dict[int, str] = {}
        for seg in chosen:
            ch_id = int(seg.chapter_id or 0)
            chapters.setdefault(ch_id, []).append(seg)
            if seg.chapter_title and ch_id not in chapter_titles:
                chapter_titles[ch_id] = seg.chapter_title

        blocks: list[str] = []
        for ch_idx, (ch_id, segs) in enumerate(
            sorted(chapters.items(), key=lambda x: x[1][0].segment_index), start=1
        ):
            start = segs[0].start_time
            end = segs[-1].end_time
            title = chapter_titles.get(ch_id, f"讨论段落 {ch_idx}")
            body = "".join((s.text or "").strip() for s in segs if (s.text or "").strip())
            keywords = segs[0].keywords if segs else None
            kw_line = ""
            if keywords:
                kw_line = f"\n关键词：{', '.join(keywords[:8])}"
            blocks.append(
                f"【{title} · {cls._format_time_range(start, end)}】\n{body}{kw_line}"
            )
        return "\n\n".join(blocks) + middle_note

    def _build_system_prompt(self, *, compact: bool = False) -> str:
        compact_note = (
            "\n\n请使用客观、中性的制造业技术表述，专注工程事实与任务，避免夸张或情绪性措辞。"
            if compact
            else ""
        )
        return (
            "你是制造业项目会议秘书。根据**已有转写文本**整理结构化纪要（不再听录音）。\n\n"
            "**人名二次确认（必须遵守）**：\n"
            "- 转写中的人名常有 ASR 谐音/错字；必须与「参会人员」及「人员管理名单」比对后改写为标准姓名\n"
            "- action_items.owner、discussion_topics 中的人名优先使用名单中的标准姓名；无法匹配则写「待确认」\n"
            "- 禁止编造名单外的人名；账号名与真实姓名同时给出时优先用真实姓名\n\n"
            "**严格 JSON 输出**，字段：\n"
            "1) summary: 80~120 字概览\n"
            "2) discussion_topics: 数组（至少 2 个主题），每项：\n"
            '   {"title":"主题名","key_points":["保留技术细节与观点"],"concerns":["争议/风险"],'
            '"conclusions":["已共识"],"open_questions":["待确认"]}\n'
            "3) decisions: 已拍板决议（字符串数组）\n"
            "4) action_items: 从转写提取的可执行任务，每项：\n"
            '   {"description":"动词开头","owner":"标准姓名或待确认","due":null,"priority":"High|Medium|Low"}\n'
            "   出现「设计进去/预留/确认/考虑/选型」等口语必须提取为任务\n\n"
            "**不要输出 raw_text 字段**（服务端会根据 discussion_topics 自动生成完整 Markdown）。\n"
            "禁止编造人名/日期；无法推断写「待确认」。直接输出 JSON，不要 ```json 包裹。"
            f"{compact_note}"
        )

    def _build_user_prompt(self, context: dict[str, Any]) -> str:
        meeting = context.get("meeting") or {}
        recording = context.get("recording") or {}
        action_items = context.get("action_items") or []
        transcript = context.get("transcript_text") or ""
        attendees = meeting.get("attendees") or context.get("attendees") or []
        personnel_roster = context.get("personnel_roster") or []

        parts: list[str] = []
        parts.append(f"【会议】{meeting.get('title', '')}")
        if meeting.get("type"):
            parts.append(f"类型：{meeting['type']}")
        if meeting.get("scheduled_start"):
            parts.append(f"时间：{meeting['scheduled_start']} ~ {meeting.get('scheduled_end') or ''}")
        if meeting.get("location"):
            parts.append(f"地点：{meeting['location']}")
        if meeting.get("agenda"):
            parts.append(f"\n【议程】\n{meeting['agenda']}")

        if attendees:
            parts.append("\n【参会人员】（优先按下列标准姓名写入纪要）")
            for person in attendees:
                name = (person.get("name") or "").strip()
                account = (person.get("account") or "").strip()
                role = person.get("role") or "attendee"
                role_label = "组织者" if role == "organizer" else "参会人"
                if account and account != name:
                    parts.append(f"- {name}（账号：{account}，{role_label}）")
                elif name:
                    parts.append(f"- {name}（{role_label}）")

        if personnel_roster:
            # 控制 prompt 体积：名单用顿号拼接
            roster_text = "、".join(personnel_roster[: self.MAX_PERSONNEL_NAMES])
            parts.append(
                "\n【人员管理名单】（用于转写人名二次确认；谐音/错字请纠正为名单中的标准姓名）\n"
                f"{roster_text}"
            )

        common_terms = context.get("common_terms") or []
        if common_terms:
            parts.append(
                "\n【术语提示】（常用词库，转写同音词请优先按这些专有词理解）\n"
                + "、".join(common_terms)
            )

        if action_items:
            parts.append("\n【已有行动项】")
            for ai in action_items:
                parts.append(f"- {ai.get('description', '')} ({ai.get('status', '')})")

        if transcript:
            parts.append(
                f"\n【录音转写】（共 {context.get('transcript_segments_count', 0)} 段"
                f"，模型 {recording.get('model_size', '') or '?'}，"
                f"语言 {recording.get('language', '') or 'auto'}）\n"
                "请按主题分类整理，保留技术细节，穷尽提取行动项；"
                "出现的人名请与上方参会人员/人员管理名单二次确认后再写入。\n"
                f"{transcript}"
            )
        else:
            parts.append("\n【无录音转写】仅基于议程与上下文生成纪要。")

        return "\n".join(parts)

    async def _call_provider(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        provider: str | None,
        model: str | None,
        db: AsyncSession | None = None,
        user_id: UUID | None = None,
    ) -> tuple[str, str | None]:
        """Dispatch to the AI platform (DB models → env Ollama fallback)."""
        from app.services.meeting.llm_dispatch import dispatch_llm_with_retry

        return await dispatch_llm_with_retry(
            db,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            user_id=user_id,
            model=model,
        )

    def _parse_draft(
        self,
        raw_text: str,
        used_model: str | None,
        context: dict[str, Any],
    ) -> MeetingMinutesDraft:
        json_text = self._extract_json(raw_text)
        if not json_text:
            return self._fallback_draft(context, reason="LLM 输出无法解析", model=used_model)
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError:
            return self._fallback_draft(context, reason="LLM 输出不是合法 JSON", model=used_model)

        discussion_topics = [
            {
                "title": str(item.get("title") or "").strip(),
                "key_points": [
                    str(x).strip() for x in (item.get("key_points") or []) if str(x).strip()
                ],
                "concerns": [
                    str(x).strip() for x in (item.get("concerns") or []) if str(x).strip()
                ],
                "conclusions": [
                    str(x).strip() for x in (item.get("conclusions") or []) if str(x).strip()
                ],
                "open_questions": [
                    str(x).strip()
                    for x in (item.get("open_questions") or [])
                    if str(x).strip()
                ],
            }
            for item in (data.get("discussion_topics") or [])
            if isinstance(item, dict) and str(item.get("title") or "").strip()
        ]

        parsed_action_items = [
            {
                "description": str(item.get("description") or "").strip(),
                "owner": (item.get("owner") or "").strip() or None,
                "due": (item.get("due") or "").strip() or None,
                "priority": (item.get("priority") or "Medium").strip(),
            }
            for item in (data.get("action_items") or [])
            if isinstance(item, dict) and item.get("description")
        ]
        parsed_decisions = [
            str(x).strip() for x in (data.get("decisions") or []) if str(x).strip()
        ]
        summary = str(data.get("summary") or "").strip()

        # raw_text 由服务端从结构化字段组装，避免 LLM 重复生成长 Markdown（显著提速）
        raw_text = self._build_raw_text_from_topics(
            discussion_topics,
            decisions=parsed_decisions,
            action_items=parsed_action_items,
            summary=summary,
        )
        if not raw_text.strip():
            raw_text = str(data.get("raw_text") or "").strip()

        return MeetingMinutesDraft(
            summary=summary,
            discussion_topics=discussion_topics,
            decisions=parsed_decisions,
            action_items=parsed_action_items,
            raw_text=raw_text,
            model=used_model,
            language=(context.get("recording") or {}).get("language") or "zh",
        )

    @staticmethod
    def _build_raw_text_from_topics(
        topics: list[dict[str, Any]],
        *,
        decisions: list[str],
        action_items: list[dict[str, Any]],
        summary: str,
    ) -> str:
        """Fallback: assemble raw_text Markdown from structured topics."""
        lines: list[str] = ["## 一、会议概要", summary or "（无）", "", "## 二、议题讨论"]
        for idx, topic in enumerate(topics, start=1):
            title = topic.get("title") or f"主题{idx}"
            lines.append(f"### 2.{idx} {title}")
            if topic.get("key_points"):
                lines.append("**讨论要点**")
                lines.extend(f"- {p}" for p in topic["key_points"])
            if topic.get("concerns"):
                lines.append("**争议与风险**")
                lines.extend(f"- {c}" for c in topic["concerns"])
            if topic.get("conclusions"):
                lines.append("**结论**")
                lines.extend(f"- {c}" for c in topic["conclusions"])
            if topic.get("open_questions"):
                lines.append("**待确认**")
                lines.extend(f"- {q}" for q in topic["open_questions"])
            lines.append("")
        lines.append("## 三、决议清单")
        lines.extend(f"- {d}" for d in decisions) if decisions else lines.append("- （无）")
        lines.append("")
        lines.append("## 四、行动项（任务清单）")
        if action_items:
            for ai in action_items:
                owner = ai.get("owner") or "待确认"
                due = ai.get("due") or "待确认"
                prio = ai.get("priority") or "Medium"
                desc = ai.get("description") or ""
                lines.append(f"- 【{owner}】{desc}（截止：{due}，优先级：{prio}）")
        else:
            lines.append("- （无明确行动项）")
        return "\n".join(lines).strip()

    @staticmethod
    def _extract_json(text: str) -> str | None:
        """Pull the first JSON object out of a possibly-noisy LLM reply."""
        if not text:
            return None
        cleaned = text.strip()
        # 去掉 ```json 包裹
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```\s*$", "", cleaned)
        # 直接就是 JSON
        if cleaned.startswith("{") and cleaned.endswith("}"):
            return cleaned
        # 否则尝试找首对匹配的 {}
        start = cleaned.find("{")
        if start < 0:
            return None
        depth = 0
        for i in range(start, len(cleaned)):
            ch = cleaned[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return cleaned[start : i + 1]
        return None

    def _fallback_draft(
        self,
        context: dict[str, Any],
        *,
        reason: str,
        model: str | None,
    ) -> MeetingMinutesDraft:
        meeting = context.get("meeting") or {}
        transcript = context.get("transcript_text") or ""  # noqa: F841  保留赋值以备后续使用或避免副作用丢失
        user_reason = reason
        if _is_content_filter_error(Exception(reason)):
            user_reason = (
                "AI 输出被内容安全策略拦截（MiniMax 错误码 1027）。"
                "系统已按「大模型列表」中的配置依次尝试其他模型仍失败。"
                "请在「系统设置 → AI 模型」中增加可用备用模型，"
                "或根据下方转写人工补全。"
            )
        summary = (
            f"（AI 自动整理失败：{user_reason}）\n"
            f"会议标题：{meeting.get('title', '')}\n"
            f"请根据议程与转写人工补全纪要。"
        )
        raw_text = self._build_transcript_scaffold(context, summary=summary)
        return MeetingMinutesDraft(
            summary=summary,
            decisions=[],
            action_items=[],
            raw_text=raw_text,
            model=model,
            language=(context.get("recording") or {}).get("language") or "zh",
        )

    @classmethod
    def _build_transcript_scaffold(
        cls,
        context: dict[str, Any],
        *,
        summary: str,
    ) -> str:
        """Provide an editable minutes skeleton when LLM generation fails."""
        meeting = context.get("meeting") or {}
        transcript = context.get("transcript_text") or ""
        lines = [
            "## 一、会议概要",
            summary,
            "",
            "## 二、议题讨论",
            "- （待人工整理）",
            "",
            "## 三、决议清单",
            "- （待人工整理）",
            "",
            "## 四、行动项（任务清单）",
            "- （待人工整理）",
        ]
        if meeting.get("agenda"):
            lines.extend(["", "## 议程参考", str(meeting["agenda"]).strip()])
        if transcript:
            lines.extend(["", "## 录音转写（供人工整理参考）", transcript])
        return "\n".join(lines).strip()

    @staticmethod
    def _render_draft_markdown(draft: MeetingMinutesDraft) -> str:
        lines: list[str] = []
        if draft.raw_text:
            return draft.raw_text
        lines.append("## 会议摘要")
        lines.append(draft.summary or "（无）")
        if draft.discussion_topics:
            lines.append("\n## 议题讨论")
            for topic in draft.discussion_topics:
                lines.append(f"### {topic.get('title', '')}")
                for label, key in (
                    ("讨论要点", "key_points"),
                    ("争议与风险", "concerns"),
                    ("结论", "conclusions"),
                    ("待确认", "open_questions"),
                ):
                    items = topic.get(key) or []
                    if items:
                        lines.append(f"**{label}**")
                        lines.extend(f"- {x}" for x in items)
        if draft.decisions:
            lines.append("\n## 决议事项")
            for d in draft.decisions:
                lines.append(f"- {d}")
        if draft.action_items:
            lines.append("\n## 行动项")
            for ai in draft.action_items:
                bits = [str(ai.get("description") or "")]
                meta = []
                if ai.get("owner"):
                    meta.append(f"负责人：{ai['owner']}")
                if ai.get("due"):
                    meta.append(f"截止：{ai['due']}")
                if ai.get("priority"):
                    meta.append(f"优先级：{ai['priority']}")
                if meta:
                    bits.append(f"（{' / '.join(meta)}）")
                lines.append("- " + " ".join(bits))
        if draft.raw_text:
            lines.append("\n## 原始纪要")
            lines.append(draft.raw_text)
        return "\n".join(lines).strip()

    async def extract_tasks_from_content(
        self,
        *,
        minutes_content: str,
        meeting: dict[str, Any] | None = None,
        provider: str | None = None,
        model: str | None = None,
        db: AsyncSession | None = None,
        user_id: UUID | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """从会议纪要正文抽取任务候选列表（不写库）。"""
        content = (minutes_content or "").strip()
        if not content:
            return [], None

        # 去掉过重 HTML 标签，保留文本结构
        plain = re.sub(r"<br\s*/?>", "\n", content, flags=re.I)
        plain = re.sub(r"</p\s*>", "\n", plain, flags=re.I)
        plain = re.sub(r"<[^>]+>", "", plain)
        plain = plain[: self.MAX_TRANSCRIPT_CHARS]

        meeting = meeting or {}
        system_prompt = (
            "你是项目会议秘书。根据会议纪要正文提取可执行任务清单。\n"
            "严格输出 JSON：{\"tasks\":[{\"name\":\"动词开头的任务名\","
            "\"owner\":\"负责人姓名或待确认\",\"due\":\"YYYY-MM-DD或null\","
            "\"planned_start\":\"YYYY-MM-DD或null\",\"priority\":\"High|Medium|Low\"}]}\n"
            "禁止编造不存在的人；无法推断的 owner/due 用 null 或「待确认」。不要 ``` 包裹。"
        )
        user_prompt = (
            f"【会议】{meeting.get('title') or ''}\n"
            f"时间：{meeting.get('scheduled_start') or ''} ~ {meeting.get('scheduled_end') or ''}\n\n"
            f"【会议纪要】\n{plain}"
        )
        raw_text, used_model = await self._call_provider(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            provider=provider,
            model=model,
            db=db,
            user_id=user_id,
        )
        json_text = self._extract_json(raw_text)
        data: dict[str, Any] = {}
        if json_text:
            try:
                parsed = json.loads(json_text)
                if isinstance(parsed, dict):
                    data = parsed
            except json.JSONDecodeError:
                data = {}
        tasks_raw = data.get("tasks") or data.get("action_items") or []
        items: list[dict[str, Any]] = []
        for raw in tasks_raw:
            if not isinstance(raw, dict):
                continue
            name = (raw.get("name") or raw.get("description") or "").strip()
            if not name:
                continue
            owner = (raw.get("owner") or raw.get("assignee") or "").strip() or None
            if owner and owner in {"待确认", "未知", "null", "None"}:
                owner = None
            items.append(
                {
                    "name": name,
                    "description": name,
                    "responsible": owner,
                    "planned_start": raw.get("planned_start") or raw.get("start") or None,
                    "planned_end": raw.get("due") or raw.get("due_date") or raw.get("planned_end") or None,
                    "priority": raw.get("priority") or "Medium",
                }
            )
        return items, used_model
