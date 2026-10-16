"""会议模板定义 — 21 种项目管理最佳实践（单一数据源）。

供 seed_meeting_templates 写入 tpl_templates，不再写入数据字典。
"""
from __future__ import annotations

import re
from typing import TypedDict


class MeetingTypeDefinition(TypedDict):
    label: str
    value: str
    remark: str
    agenda_items: list[str]
    minutes_skeleton: str


def plain_text_to_html(text: str) -> str:
    """将纯文本（按行）转为 Quill 兼容的简单 HTML。"""
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        return ""
    return "".join(f"<p>{line}</p>" for line in lines)


def parse_meeting_type_content(content: str) -> tuple[str, str]:
    """从 legacy 字典 content 拆分【议程】与【纪要模板】段。"""
    agenda = ""
    minutes = ""
    if "【议程】" in content:
        after_agenda = content.split("【议程】", 1)[1]
        if "【纪要模板】" in after_agenda:
            agenda_part, minutes_part = after_agenda.split("【纪要模板】", 1)
            agenda = agenda_part.strip()
            minutes = minutes_part.strip()
        else:
            agenda = after_agenda.strip()
    return agenda, minutes


def build_minutes_html(type_specific: str) -> str:
    """生成与 AI 纪要结构对齐的 minutesHtml，并保留类型专属字段。"""
    sections: list[tuple[str, str]] = [
        ("会议概览", ""),
        ("关键讨论", ""),
        ("决议事项", ""),
        ("行动项", "责任人 / 截止日期："),
        ("风险与待办", ""),
    ]
    parts: list[str] = []
    for title, placeholder in sections:
        parts.append(f"<h2>{title}</h2>")
        if placeholder:
            parts.append(f"<p>{placeholder}</p>")
        else:
            parts.append("<p><br></p>")

    skeleton = type_specific.strip()
    if skeleton:
        parts.append("<h2>类型专属字段</h2>")
        for line in skeleton.split("\n"):
            line = line.strip()
            if line:
                parts.append(f"<p>{line}</p>")
    return "".join(parts)


def definition_to_template_data(defn: MeetingTypeDefinition) -> dict[str, str]:
    """将定义转为 tpl_templates.template_data JSON。"""
    agenda_text = "\n".join(defn["agenda_items"])
    return {
        "meetingType": defn["value"],
        "agendaHtml": plain_text_to_html(agenda_text),
        "minutesHtml": build_minutes_html(defn["minutes_skeleton"]),
    }


MEETING_TYPE_DEFINITIONS: list[MeetingTypeDefinition] = [
    {
        "label": "例行会议",
        "value": "Regular",
        "remark": "定期例会，如周会/月会",
        "agenda_items": [
            "1. 上次会议决议执行情况",
            "2. 各模块当前进展汇报",
            "3. 遇到的风险与问题",
            "4. 下周/下阶段工作计划",
            "5. 需要协调事项",
            "6. 本次会议行动项",
        ],
        "minutes_skeleton": "决议：\n行动项：\n责任人/截止日期：\n下次会议：",
    },
    {
        "label": "评审会议",
        "value": "Review",
        "remark": "方案/设计/阶段评审",
        "agenda_items": [
            "1. 评审对象概述",
            "2. 关键指标与要求回顾",
            "3. 评审材料展示",
            "4. 评审意见收集",
            "5. 问题讨论与决议",
            "6. 遗留问题清单",
            "7. 下一步行动计划",
        ],
        "minutes_skeleton": "评审结论（通过/有条件通过/不通过）：\n主要问题清单：\n改进要求：\n关闭时间：",
    },
    {
        "label": "计划会议",
        "value": "Planning",
        "remark": "项目/阶段启动规划",
        "agenda_items": [
            "1. 项目背景与目标介绍",
            "2. 项目范围与交付物确认",
            "3. 团队分工与职责说明",
            "4. 里程碑计划与时间表",
            "5. 风险识别与应对措施",
            "6. 沟通机制与汇报频率",
            "7. 资源需求确认",
            "8. 会议行动项",
        ],
        "minutes_skeleton": "项目章程要点：\nWBS 概览：\n关键里程碑：\n资源承诺：\n启动决策：",
    },
    {
        "label": "复盘会议",
        "value": "Retrospective",
        "remark": "问题/阶段复盘总结",
        "agenda_items": [
            "1. 问题/阶段描述与背景",
            "2. 实际结果 vs 目标对比",
            "3. 做得好的方面（Keep）",
            "4. 需要改进的方面（Improve）",
            "5. 根本原因分析（5 Why）",
            "6. 改进措施与责任人",
            "7. 经验教训沉淀",
            "8. 后续跟踪计划",
        ],
        "minutes_skeleton": "根本原因：\nKeep（继续保持）：\nImprove（需要改进）：\nTry（下次实验）：\n行动项：",
    },
    {
        "label": "临时会议",
        "value": "Ad-hoc",
        "remark": "临时紧急或专题讨论",
        "agenda_items": [
            "1. 会议背景与目的",
            "2. 当前状况说明",
            "3. 核心问题讨论",
            "4. 决策与结论",
            "5. 行动项与负责人",
            "6. 跟进时间节点",
        ],
        "minutes_skeleton": "决策：\n行动项：\n负责人/截止日期：\n后续跟进：",
    },
    {
        "label": "供应商沟通",
        "value": "Supplier",
        "remark": "与供应商的沟通会议",
        "agenda_items": [
            "1. 供货进度跟踪",
            "2. 质量问题反馈与处理",
            "3. 技术变更确认",
            "4. 交期协调",
            "5. 价格/商务条款讨论",
            "6. 后续行动项",
        ],
        "minutes_skeleton": "供货承诺：\n质量问题处理：\n商务条款：\n下次对接：",
    },
    {
        "label": "设计评审",
        "value": "DesignReview",
        "remark": "产品/工艺设计专项评审",
        "agenda_items": [
            "1. 设计方案概述",
            "2. 关键技术指标说明",
            "3. DFMEA 分析结果",
            "4. 仿真/验证结果展示",
            "5. 设计评审检查清单",
            "6. 专家意见与建议",
            "7. 设计改进计划",
            "8. 验收条件确认",
        ],
        "minutes_skeleton": "评审检查清单（√/×）：\nDFMEA 关键项：\n专家建议：\n设计变更：\n验证状态：",
    },
    {
        "label": "立项会议",
        "value": "Kickoff",
        "remark": "项目正式立项启动，发布项目章程",
        "agenda_items": [
            "1. 项目背景与立项依据",
            "2. 项目目标（SMART）与范围",
            "3. 干系人识别与登记册",
            "4. 团队组建与角色职责（RACI）",
            "5. 初步 WBS 与里程碑",
            "6. 预算与资源确认",
            "7. 风险初识与应对预案",
            "8. 沟通管理计划",
            "9. 项目章程签署",
            "10. 下一步行动项",
        ],
        "minutes_skeleton": "项目章程要点：\n目标（SMART）：\n范围 In/Out：\n关键里程碑：\n预算上限：\n发起人承诺：\n启动决策：",
    },
    {
        "label": "每日站会",
        "value": "Standup",
        "remark": "敏捷 Scrum 每日 15 分钟站会",
        "agenda_items": [
            "1. 昨日我完成了什么",
            "2. 今日我计划做什么",
            "3. 遇到的障碍与需要的支持",
            "4. 看板更新（卡片流转）",
            "5. 阻塞项登记与升级",
        ],
        "minutes_skeleton": "进展：\n阻塞项：\n需要协调：\n备注：",
    },
    {
        "label": "迭代评审会",
        "value": "SprintReview",
        "remark": "Scrum 迭代结束向 PO 演示交付物",
        "agenda_items": [
            "1. 迭代目标回顾",
            "2. 已完成 Story 演示",
            "3. 未完成 Story 与原因",
            "4. 燃尽图/速率展示",
            "5. PO 验收与反馈",
            "6. Product Backlog 更新",
            "7. 下迭代候选范围",
            "8. 干系人意见收集",
        ],
        "minutes_skeleton": "迭代目标达成率：\n已完成 Story：\nPO 验收意见：\nBacklog 调整：\n下迭代范围：",
    },
    {
        "label": "迭代回顾会",
        "value": "SprintRetrospective",
        "remark": "Scrum 迭代结束团队自省改进",
        "agenda_items": [
            "1. Start（开始做）",
            "2. Stop（停止做）",
            "3. Continue（继续做）",
            "4. More of（多做）",
            "5. Less of（少做）",
            "6. 改进项优先级投票",
            "7. 行动项负责人与时限",
            "8. 改进项纳入下迭代",
        ],
        "minutes_skeleton": "Start：\nStop：\nContinue：\n改进项 TOP3：\n负责人：\n下迭代验证：",
    },
    {
        "label": "需求评审会",
        "value": "RequirementsReview",
        "remark": "需求基线前的多角色评审",
        "agenda_items": [
            "1. 需求概述与业务背景",
            "2. 用户故事与验收标准",
            "3. 需求追溯矩阵（RTM）",
            "4. 用例与流程图评审",
            "5. 非功能需求（性能/安全/可用性）",
            "6. 需求变更控制机制",
            "7. 风险与依赖识别",
            "8. 评审结论与基线决策",
        ],
        "minutes_skeleton": "需求基线结论：\n遗留问题：\n变更控制策略：\n追溯矩阵：\n下次评审：",
    },
    {
        "label": "技术方案评审会",
        "value": "TechReview",
        "remark": "技术架构与关键方案评审",
        "agenda_items": [
            "1. 业务背景与技术挑战",
            "2. 候选方案对比（≥2 个）",
            "3. 关键技术选型与依据",
            "4. 架构图与组件交互",
            "5. POC 验证结果",
            "6. 性能/可靠性/可维护性",
            "7. 安全与合规评估",
            "8. 技术风险与缓解",
            "9. 评审结论与决策",
        ],
        "minutes_skeleton": "选定方案：\n否决方案原因：\n关键风险：\nPOC 结果：\n后续验证项：",
    },
    {
        "label": "代码评审会",
        "value": "CodeReview",
        "remark": "关键模块代码走查与质量评审",
        "agenda_items": [
            "1. 模块功能与设计要点",
            "2. 核心逻辑走读",
            "3. 评审清单（命名/结构/异常/测试）",
            "4. 安全与性能热点",
            "5. 测试覆盖率",
            "6. 评审意见分级（阻塞/建议/讨论）",
            "7. 合并准入条件",
            "8. 后续重构计划",
        ],
        "minutes_skeleton": "评审意见：\n阻塞项：\n建议项：\n覆盖率：\n合并决策：",
    },
    {
        "label": "测试评审会",
        "value": "TestReview",
        "remark": "测试策略/用例/结果评审",
        "agenda_items": [
            "1. 测试范围与策略",
            "2. 测试用例覆盖率评审",
            "3. 自动化覆盖与回归策略",
            "4. 缺陷统计与趋势",
            "5. 严重缺陷与遗留缺陷",
            "6. 准入/准出标准符合度",
            "7. 测试环境与数据准备",
            "8. 评审结论与发布建议",
        ],
        "minutes_skeleton": "用例覆盖率：\n缺陷统计：\n遗留缺陷：\n准出决策：\n发布建议：",
    },
    {
        "label": "里程碑评审会",
        "value": "MilestoneReview",
        "remark": "里程碑准入/准出评审",
        "agenda_items": [
            "1. 里程碑准入条件回顾",
            "2. 阶段交付物检查清单",
            "3. 进度/成本/范围偏差分析",
            "4. 挣值分析（PV/EV/AC/CPI/SPI）",
            "5. 质量指标达成情况",
            "6. 风险状态更新",
            "7. 干系人意见",
            "8. 里程碑准出决策",
        ],
        "minutes_skeleton": "里程碑达成度：\n挣值指标：\n偏差分析：\n质量达成：\n准出决策：",
    },
    {
        "label": "风险评审会",
        "value": "RiskReview",
        "remark": "定期风险识别与应对评审",
        "agenda_items": [
            "1. 上期风险跟踪结果",
            "2. 新增风险识别",
            "3. TOP5 风险排序与影响",
            "4. 风险概率/影响矩阵更新",
            "5. 应对策略与责任人",
            "6. 应急储备评估",
            "7. 风险预警与升级",
            "8. 下期风险关注点",
        ],
        "minutes_skeleton": "TOP5 风险：\n新增风险：\n关闭风险：\n应急储备：\n预警项：",
    },
    {
        "label": "变更控制委员会",
        "value": "CCB",
        "remark": "ECR/CCB 评审与基线变更决策",
        "agenda_items": [
            "1. 变更申请概述（ECR）",
            "2. 影响分析（范围/进度/成本/质量）",
            "3. 备选方案对比",
            "4. 多方案风险评估",
            "5. 相关部门意见（设计/工艺/采购/质量/财务）",
            "6. 投票与表决",
            "7. 决策（批准/有条件批准/拒绝/延期）",
            "8. 基线更新与通知分发",
        ],
        "minutes_skeleton": "ECR 编号：\n影响结论：\n表决结果：\n决策：\n附加条件：\n基线更新：",
    },
    {
        "label": "项目验收会",
        "value": "Acceptance",
        "remark": "项目最终交付物验收",
        "agenda_items": [
            "1. 项目目标达成度回顾",
            "2. 验收清单核对",
            "3. 合同条款符合性",
            "4. 交付物完整性确认",
            "5. 培训与知识转移",
            "6. 售后与运维交接",
            "7. 客户/发起人签字",
            "8. 项目关闭决策",
        ],
        "minutes_skeleton": "验收清单：\n合同符合度：\n交付物清单：\n验收结论：\n签字方：",
    },
    {
        "label": "项目收尾复盘",
        "value": "PostMortem",
        "remark": "项目关闭后经验教训沉淀",
        "agenda_items": [
            "1. 项目整体回顾（目标/范围/进度/成本）",
            "2. 关键指标达成（准时/预算/质量/满意度）",
            "3. 成功要素（What Went Well）",
            "4. 失败要素（What Went Wrong）",
            "5. 偏差根本原因分析",
            "6. 经验教训沉淀（可复用资产）",
            "7. 过程资产归档",
            "8. 组织级改进建议",
        ],
        "minutes_skeleton": "项目健康度评分：\n关键成功因素：\n关键失败因素：\nTOP5 经验：\nTOP5 教训：\n组织级建议：",
    },
    {
        "label": "项目指导委员会",
        "value": "Steerco",
        "remark": "高层项目健康度与决策会议",
        "agenda_items": [
            "1. 项目健康度仪表盘（RAG）",
            "2. 进度/成本/范围/风险摘要",
            "3. 阶段交付物审批",
            "4. 重大变更与决策申请",
            "5. 资源与预算调整",
            "6. 战略对齐度评估",
            "7. 干系人反馈",
            "8. 委员会决议",
        ],
        "minutes_skeleton": "RAG 状态：\n关键指标：\n待决策项：\n决议：\n资源承诺：\n下次委员会：",
    },
]


def system_template_number(meeting_type: str) -> str:
    """固定编号，便于幂等 seed。"""
    safe = re.sub(r"[^A-Za-z0-9]", "", meeting_type)
    return f"MTG-TPL-{safe}"
