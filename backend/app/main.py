"""AIDriveMetting FastAPI application."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import AsyncSessionLocal
from app.exceptions import AppException
from app.routers.ai import router as ai_router
from app.routers.auth import router as auth_router
from app.routers.meeting import (
    asr_settings_router,
    common_terms_router,
    meeting_recordings_router,
    meeting_templates_router,
    meetings_router,
)
from app.routers.users import router as users_router
from app.services.ai.seed_service import AISeedService
from app.services.admin_bootstrap import AdminBootstrap
from app.services.meeting.seed_meeting_templates import seed_meeting_templates
import app.ai_tools  # noqa: F401 — register AI tools
import app.models.ai  # noqa: F401 — register AI tables
import app.models.meeting  # noqa: F401 — register meeting tables

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv("AIDRIVE_TESTING") != "1":
        async with AsyncSessionLocal() as db:
            await AdminBootstrap.ensure_configured_admin(db)
            await AISeedService.ensure_platform_seed(db)
            await seed_meeting_templates(db)
            await db.commit()
    yield


app = FastAPI(
    title=settings.APP_NAME,
    lifespan=lifespan,
    docs_url="/docs" if settings.API_DOCS_ENABLED else None,
    redoc_url="/docs" if settings.API_DOCS_ENABLED else None,
    openapi_url="/openapi.json" if settings.API_DOCS_ENABLED else None,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API_PREFIX = "/api/v1"
app.include_router(auth_router, prefix=API_PREFIX)
app.include_router(users_router, prefix=API_PREFIX)
app.include_router(ai_router, prefix=API_PREFIX)
app.include_router(meetings_router, prefix=API_PREFIX)
app.include_router(meeting_recordings_router, prefix=API_PREFIX)
app.include_router(asr_settings_router, prefix=API_PREFIX)
app.include_router(meeting_templates_router, prefix=API_PREFIX)
app.include_router(common_terms_router, prefix=API_PREFIX)

# 录音文件静态目录（与 AIIgnitePLM 一致：/static/uploads/meetings/{年}/{月}/）
STATIC_ROOT = Path(__file__).resolve().parent.parent / "static"
STATIC_ROOT.mkdir(parents=True, exist_ok=True)
(STATIC_ROOT / "uploads" / "meetings").mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_ROOT)), name="static")


@app.exception_handler(AppException)
async def app_exception_handler(_request, exc: AppException):
    from fastapi.responses import JSONResponse
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME}
