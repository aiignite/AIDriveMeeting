from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = Field(..., min_length=1)
    DATABASE_URL_SYNC: str = Field(..., min_length=1)

    SECRET_KEY: str = Field(..., min_length=32)
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14

    APP_NAME: str = "AIDriveMetting"
    DEBUG: bool = False
    CORS_ORIGINS: str = "http://localhost:3330"
    API_DOCS_ENABLED: bool = True

    OLLAMA_BASE_URL: str = "http://localhost:11434"
    OLLAMA_MODEL: str = "qwen2.5"
    AI_PROVIDER: str = "ollama"

    ANTHROPIC_API_KEY: str | None = None
    OPENAI_API_KEY: str | None = None
    MINIMAX_API_KEY: str | None = None
    GEMINI_API_KEY: str | None = None
    GOOGLE_API_KEY: str | None = None
    AI_ALLOW_MOCK: bool = True

    ADMIN_EMAIL: str | None = None

    SSO_ENABLED: bool = False
    SSO_SECRET_KEY: str | None = None
    SSO_ISSUER: str = "aidriveall"
    SSO_COOKIE_NAME: str = "aidrive_token"

    # ── 会议语音转写（faster-whisper / 远程 asr_gateway）──
    # 机器较弱可在 .env 改 MEETING_WHISPER_MODEL=small
    MEETING_WHISPER_MODEL: str = "medium"
    # 转写主语言；留空则由 Whisper 自动检测
    MEETING_WHISPER_LANGUAGE: str = "zh"
    # 并发转写任务上限；默认 1（串行，避免 CPU 峰值）
    MEETING_WHISPER_MAX_CONCURRENT: int = 1
    # ASR 模式：local（本机 faster-whisper）| remote（局域网 GPU asr_gateway）
    MEETING_ASR_MODE: str = "local"
    # 远程 ASR HTTP 端点，例如 http://172.16.17.66:8090/api/v1/transcribe
    MEETING_ASR_REMOTE_URL: str = ""
    # 与 asr_gateway 共享的 X-API-Key（空则不发鉴权头）
    MEETING_ASR_REMOTE_API_KEY: str = ""
    # 远程转写超时（秒）；长会议需调大
    MEETING_ASR_REMOTE_TIMEOUT: int = 3600
    # 远程失败时是否降级到本机 CPU 转写
    MEETING_ASR_FALLBACK_LOCAL: bool = False
    # local 模式设备/精度
    MEETING_WHISPER_DEVICE: str = "cpu"
    MEETING_WHISPER_COMPUTE_TYPE: str = "int8"
    # 静音切分阈值 (dB)
    MEETING_SPLIT_SILENCE_DB: float = -40
    # 静音持续判定 (秒)
    MEETING_SPLIT_SILENCE_DURATION: float = 0.5
    # 单段硬切上限 (秒)；超过则强制切分
    MEETING_SPLIT_MAX_SEGMENT_SECONDS: int = 60
    # 最小分块秒数：silencedetect 切出的 < 该值的窗口会合并到上一个
    MEETING_SPLIT_MIN_SEGMENT_SECONDS: float = 3.0
    # 单个录音文件大小上限 (bytes) — 默认 500MB
    MEETING_MAX_RECORDING_BYTES: int = 524_288_000
    # 说话人分离（默认关闭）
    MEETING_ENABLE_SPEAKER_DIARIZATION: bool = False
    # 转写段落合并参数（规则层，LLM 润色前后各执行一次）
    MEETING_MERGE_MIN_PARAGRAPH_CHARS: int = 80
    MEETING_MERGE_MAX_PARAGRAPH_CHARS: int = 400
    MEETING_MERGE_SOFT_PAUSE_SECONDS: float = 3.0
    MEETING_MERGE_HARD_PAUSE_SECONDS: float = 4.0
    # 会议 AI 配置（润色/纪要），为空则走 AI 平台模型解析
    MEETING_AI_PROVIDER: str = ""
    MEETING_AI_MODEL: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
