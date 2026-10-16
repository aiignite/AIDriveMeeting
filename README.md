# AIDriveMetting

AI 驱动的会议管理全栈应用（AIDrive 家族成员）。架构与 [AIDriveNote](../AIDriveNote) 保持一致，会议功能自 [AIIgnitePLM](../../AIIgnite/AIIgnitePLM) 一比一移植并去项目化。

## 功能总览

| 模块 | 说明 |
|---|---|
| 登录/注册 | JWT + 双 Token（access/refresh），管理员引导，组件式路由守卫 |
| 会议清单 | 列表/搜索/状态与类型筛选、创建（模板预填议程）、编辑、删除 |
| 录音与转写 | 浏览器录音（MediaRecorder）+ 拖拽上传（≤500MB），2 秒轮询转写进度，重转写、LLM 润色、删除、回放 |
| 会议纪要 | Quill 富文本纪要、AI 一键生成结构化纪要并自动落库（假进度条 + 模型下拉）、保存、确认 |
| 常用词投喂 AI | 用户维护常用词库（含同音/易错写法），转写润色与 AI 纪要生成时自动注入提示词 |
| 会议任务 | 从纪要提取任务候选 → 确认弹窗 → 写入会议行动项；参会人一键转「参加会议」行动项 |
| CCB 投票/决议 | 参会人投票（Approve/Reject/Abstain）+ 结构化决议（Approve/Reject/Conditional/Defer） |
| 抄送 | 会议内抄送记录（不外发系统通知） |
| 导出 | PDF（jspdf + 中文字体）/ Excel（xlsx） |
| 会议模板 | 21 套系统预置会议类型模板（议程 HTML + 纪要骨架），支持 CRUD 与复制 |
| AI 助手与技能 | 完整 AI 平台（模型/助手/技能/会话/SSE 流式/附件/function calling）；会议纪要助手 + 3 个会议工具（search_meetings / get_meeting_transcript / generate_meeting_minutes）+ 3 个会议技能 |
| ASR 设置 | local（本机 faster-whisper）/ remote（局域网 GPU 网关）双模式、模型/语言/设备/精度、远程 Key 加密存储与连通性测试 |

## 技术栈

- **前端** `frontend/`（端口 **3330**）：React 19 · TypeScript · Vite 6 · Tailwind CSS 4 · React Router 7 · react-hot-toast · react-quill-new（纪要/议程富文本）· jspdf/xlsx（导出）
- **后端** `backend/`（端口 **3335**）：FastAPI · SQLAlchemy 2 async · PostgreSQL · Alembic · JWT · faster-whisper（转写）
- **远程转写网关** `asr_gateway/`：独立 FastAPI + faster-whisper，可部署到局域网 GPU 机器（X-API-Key 鉴权）

## 快速开始

```bash
# 1. 创建数据库（首次）
createdb -h 127.0.0.1 -U postgres ai_drive_meeting

# 2. 安装后端依赖 + 迁移
cd backend
pip install -r requirements.txt
alembic upgrade head

# 3. 一键启动（前后端）
cd ..
./start.sh
```

- 前端：http://localhost:3330
- 后端 API Docs：http://localhost:3335/docs
- 首次启动自动种子：管理员（`ADMIN_EMAIL`，密码见 `backend/.env` 首次生成逻辑 / AdminBootstrap）、默认 Ollama 模型、会议纪要助手、3 个会议技能、21 套会议模板。

## 环境变量（backend/.env）

核心项见 `.env.example`。会议转写相关：

- `MEETING_ASR_MODE`: `local`（本机 faster-whisper，需系统安装 ffmpeg）| `remote`（局域网 GPU 网关）
- `MEETING_ASR_REMOTE_URL` / `MEETING_ASR_REMOTE_API_KEY`: 远程网关地址与共享密钥
- `MEETING_WHISPER_MODEL`: tiny/base/small/medium/large-v3
- `MEETING_AI_MODEL`: 润色/纪要默认走 AI 平台模型解析（DB 模型 → env Ollama 兜底），可显式指定

## 转写链路

1. 上传/录音完成 → `POST /api/v1/meetings/{id}/recordings`（BackgroundTasks 异步转写）
2. 静音点检测切分 → ffmpeg 转 16k wav → faster-whisper 分片转写（远程优先/本机降级）
3. LLM 润色（常用词库 + 会议上下文术语提示注入）→ 章节切分 + 关键词抽取
4. 前端 2 秒轮询 `status`/`progress_pct`，完成后拉取转写全文

## 目录结构

```
AIDriveMetting/
├── start.sh              # 一键启动（端口清理 + 依赖检查）
├── backend/
│   ├── app/
│   │   ├── main.py       # FastAPI 入口（路由挂载 + /static + lifespan 种子）
│   │   ├── routers/      # auth / users / ai / meeting（meetings, recordings, asr, templates, common_terms）
│   │   ├── services/
│   │   │   ├── meeting/  # 转写/录音/润色/章节/纪要编排/模板定义等 13 个服务
│   │   │   ├── ai/       # AI 平台（chat/llm_router/seed）
│   │   │   └── ai_providers/
│   │   ├── ai_tools/     # ToolRegistry + meeting_tools（3 个会议工具）
│   │   └── models/
│   ├── alembic/
│   └── static/uploads/meetings/   # 录音落盘
├── frontend/
│   └── src/
│       ├── pages/        # MeetingsPage（5 Tab 主页）/ LoginPage / settings/*
│       ├── components/meeting/  # AudioRecorder/Uploader/Player/StatusBadge/TranscriptView/MinutesAIDraftPanel/TaskConfirmModal
│       ├── hooks/        # useMeetingRecording（2s 轮询）/ useMeetingTemplates / useAIChat
│       └── services/     # client.ts（camel↔snake + 401 刷新）/ meeting.ts / template.ts / ai
└── asr_gateway/          # 远程 GPU 转写网关（可选部署）
```

## API 概览（/api/v1）

- `POST /auth/register|login|refresh`，`GET /auth/me`
- `GET|POST|PATCH|DELETE /meetings[/{id}]`；`/meetings/{id}/minutes`；`POST /meetings/{id}/minutes:generate-from-transcript`
- `POST /meetings/{id}/tasks:extract-from-minutes`、`/tasks:confirm`、`/invite-tasks`、`/cc`
- `GET|POST /meetings/{id}/recordings`；`/{rid}/transcript|retranscribe|refine`
- `GET|PUT /meeting/asr-settings`、`POST /meeting/asr-settings/test-remote`
- `GET|POST|PATCH|DELETE /meeting-templates`（系统模板只读）
- `GET|POST|PATCH|DELETE /common-terms`（常用词库）
- `POST /ai/chat/stream`（SSE，会议页注入 meeting 上下文与技能）
