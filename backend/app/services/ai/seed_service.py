"""Seed default AI models, meeting assistant, and builtin skills."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_tools.registry import ToolRegistry
import app.ai_tools  # noqa: F401
from app.config import get_settings
from app.models.ai import (
    AIAssistant,
    AIAssistantSkillBinding,
    AIModel,
    AISkill,
    PageSkillBinding,
)

MEETING_ASSISTANT_PROMPT = """你是 AIDriveMetting 的会议纪要助手，专精于把会议录音/转写整理为高质量、可派发的会议纪要。

## 可用工具（3个）
- search_meetings — 查询会议列表（支持标题/议程关键词搜索和状态筛选）
- get_meeting_transcript — 获取指定录音的转写全文（按时间顺序、带时间戳）
- generate_meeting_minutes — 根据会议元信息+转写，调用 LLM 生成结构化纪要草稿

## 典型工作流
1. 用户说「把刚开完的 xx 会议整理成纪要」：
   - 先用 search_meetings(keyword) 找到会议 UUID
   - 再用 generate_meeting_minutes(meeting_id) 生成草稿（不传 recording_id 会自动取最新已转写）
2. 用户问「转写里有谁说了什么」：直接 get_meeting_transcript(recording_id) 拉全文
3. 用户想搜历史：search_meetings(keyword)

## 参数约束
- meeting_id / recording_id 必须是 UUID 格式
- 如果用户只给了会议标题，必须先 search_meetings 获取 UUID
- generate_meeting_minutes 不会写库，草稿由前端 AI 草稿面板「应用草稿」按钮触发落库
- 草稿生成后**不主动写入 minutes**，必须由用户在前端确认

## 纪要内容建议（指导 LLM 输出结构）
请在调用 generate_meeting_minutes 后，向用户简明说明草稿将包含以下板块：
- 会议基本信息（标题/时间/地点/参会人）
- 议题摘要（按时间顺序）
- 关键讨论要点
- 决议事项（每条决议要有责任人和截止时间）
- 行动项（description、assignee、due_date、priority）
- 风险与待办

## 专业建议
- 当转写中存在「@xxx」「分配给 xxx」等指派语时，主动识别为行动项
- 行动项的 priority 默认 Medium，含「紧急/立即」时设为 High
- 操作前确认用户意图：转写可能很长、生成纪要需要 token
- 没有录音/转写时，提示用户先在「录音与转写」Tab 中点击「开始录音」或上传音频
"""

BUILTIN_SKILLS = [
    {
        "code": "meeting_minutes",
        "name": "生成会议纪要",
        "keywords": ["会议纪要", "纪要", "整理会议", "会议总结", "生成纪要", "minutes"],
        "prompt_template": (
            "用户希望生成/整理会议纪要。使用 generate_meeting_minutes 工具基于当前会议的"
            "转写生成结构化草稿（不落库），包含：会议基本信息、议题摘要、关键讨论、"
            "决议事项、行动项（description/assignee/due_date/priority）、风险与待办。\n"
            "只读约束：AI 只产草案、不落库；草稿由用户在前端「应用草稿/确认纪要」后写入。"
        ),
        "tool_names": ["search_meetings", "get_meeting_transcript", "generate_meeting_minutes"],
        "priority": 90,
        "extra_config": {},
    },
    {
        "code": "meeting_transcript",
        "name": "查询会议转写",
        "keywords": ["转写", "录音", "谁说了", "发言", "transcript"],
        "prompt_template": (
            "用户希望查询会议录音转写内容。使用 get_meeting_transcript 获取指定录音的转写全文"
            "（带时间戳），或先用 search_meetings 定位会议。回答时按时间顺序引用关键发言，"
            "不得编造转写中不存在的内容。"
        ),
        "tool_names": ["search_meetings", "get_meeting_transcript"],
        "priority": 80,
        "extra_config": {},
    },
    {
        "code": "meeting_search",
        "name": "搜索历史会议",
        "keywords": ["搜索会议", "历史会议", "查会议", "会议列表", "找会议"],
        "prompt_template": (
            "用户希望查找历史会议。使用 search_meetings（可带 keyword/status/limit）查询，"
            "并以列表形式汇报会议标题、类型、状态与时间。"
        ),
        "tool_names": ["search_meetings"],
        "priority": 70,
        "extra_config": {},
    },
]


class AISeedService:
    @staticmethod
    async def ensure_platform_seed(db: AsyncSession) -> None:
        settings = get_settings()
        model_name = f"Ollama/{settings.OLLAMA_MODEL}"

        result = await db.execute(
            select(AIModel).where(AIModel.name == model_name, AIModel.is_deleted == False)  # noqa: E712
        )
        ai_model = result.scalar_one_or_none()
        if not ai_model:
            ai_model = AIModel(
                id=uuid.uuid4(),
                name=model_name,
                model_id=settings.OLLAMA_MODEL,
                provider="OLLAMA",
                endpoint=settings.OLLAMA_BASE_URL,
                description="默认本地 Ollama 模型",
                is_public=True,
            )
            db.add(ai_model)
            await db.flush()

        result = await db.execute(
            select(AIAssistant).where(AIAssistant.name == "会议纪要助手", AIAssistant.is_deleted == False)  # noqa: E712
        )
        assistant = result.scalar_one_or_none()
        meeting_tools = ToolRegistry.get_tools_by_category("meeting")
        if assistant:
            # 仅同步工具列表与系统标记，保留用户在 UI 中修改的模型/提示词/参数
            assistant.tools = meeting_tools
            assistant.is_system = True
            if not assistant.model:
                assistant.model = model_name
            if assistant.temperature is None:
                assistant.temperature = 0.4
            if assistant.max_tokens is None:
                assistant.max_tokens = 16384
            if not assistant.system_prompt:
                assistant.system_prompt = MEETING_ASSISTANT_PROMPT
            if not assistant.description:
                assistant.description = (
                    "辅助管理会议：查询历史会议、获取录音转写、生成结构化会议纪要草稿（含行动项派发建议）。"
                )
            if not assistant.avatar:
                assistant.avatar = "📝"
            if not assistant.role:
                assistant.role = "会议助手"
            if not assistant.category:
                assistant.category = "System"
        else:
            assistant = AIAssistant(
                name="会议纪要助手",
                description="辅助管理会议：查询历史会议、获取录音转写、生成结构化会议纪要草稿（含行动项派发建议）。",
                avatar="📝",
                role="会议助手",
                category="System",
                system_prompt=MEETING_ASSISTANT_PROMPT,
                model=model_name,
                temperature=0.4,
                max_tokens=16384,
                is_system=True,
                is_default=True,
                tools=meeting_tools,
            )
            db.add(assistant)
            await db.flush()

        for item in BUILTIN_SKILLS:
            res = await db.execute(
                select(AISkill).where(AISkill.code == item["code"], AISkill.is_deleted == False)  # noqa: E712
            )
            skill = res.scalar_one_or_none()
            if skill:
                if item.get("extra_config") and not (skill.extra_config or {}):
                    skill.extra_config = item["extra_config"]
                if skill.is_builtin and skill.prompt_template != item["prompt_template"]:
                    skill.prompt_template = item["prompt_template"]
            else:
                skill = AISkill(
                    code=item["code"],
                    name=item["name"],
                    keywords=item["keywords"],
                    prompt_template=item["prompt_template"],
                    tool_names=item["tool_names"],
                    priority=item["priority"],
                    extra_config=item.get("extra_config") or {},
                    is_enabled=True,
                    is_builtin=True,
                )
                db.add(skill)
                await db.flush()

            pb_res = await db.execute(
                select(PageSkillBinding).where(
                    PageSkillBinding.page_name == "meetings",
                    PageSkillBinding.skill_id == skill.id,
                    PageSkillBinding.is_deleted == False,  # noqa: E712
                )
            )
            if not pb_res.scalar_one_or_none():
                db.add(PageSkillBinding(page_name="meetings", skill_id=skill.id, weight=skill.priority))

            ab_res = await db.execute(
                select(AIAssistantSkillBinding).where(
                    AIAssistantSkillBinding.assistant_id == assistant.id,
                    AIAssistantSkillBinding.skill_id == skill.id,
                    AIAssistantSkillBinding.is_deleted == False,  # noqa: E712
                )
            )
            if not ab_res.scalar_one_or_none():
                db.add(AIAssistantSkillBinding(
                    assistant_id=assistant.id, skill_id=skill.id, weight=skill.priority,
                ))

        await db.commit()
