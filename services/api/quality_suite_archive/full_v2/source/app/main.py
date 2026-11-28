"""FastAPI 应用入口。"""
from __future__ import annotations

import logging
import traceback
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.router import api_router
from .config import settings
from .db import init_db
from .llm.service import AIServiceError
from .v2.contracts import DomainError
from .v2 import storage as _v2_storage  # register durable metadata before create_all

logger = logging.getLogger("aiedu")
if not logger.handlers:
    # uvicorn 只配置自己的 logger，应用日志默认落到未配置的 root（INFO 会被丢弃）。
    # 显式挂一个 handler：启动时的库路径、后台线程失败原因才看得见。
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     [%(name)s] %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


def _log_unhandled(exc: BaseException) -> None:
    """把未捕获异常写入 data/errors.log，便于事后定位 500 根因。"""
    try:
        log_dir = Path(getattr(settings, "backup_dir", "data/backups")).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "errors.log"
        with path.open("a", encoding="utf-8") as fh:
            fh.write("=" * 60 + "\n")
            fh.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
            fh.write("\n")
    except OSError:
        pass
    logger.exception("未捕获异常", exc_info=exc)


def _sweep_stale_jobs() -> None:
    """启动时把上次进程中断遗留的排队/运行中任务标记为失败，避免界面永久转圈。"""
    from .db import SessionLocal
    from .models import GenerationJob

    db = SessionLocal()
    try:
        stale = (
            db.query(GenerationJob)
            .filter(GenerationJob.status.in_(["queued", "running"]), GenerationJob.job_type != "task_v2")
            .all()
        )
        for job in stale:
            job.status = "failed"
            job.error_code = "server_restart"
        if stale:
            db.commit()
    except Exception:  # noqa: BLE001  清理失败不阻塞启动
        db.rollback()
    finally:
        db.close()


def _ensure_format_specs() -> None:
    from .db import SessionLocal
    from .services.format_specs import ensure_builtin_specs

    db = SessionLocal()
    try:
        ensure_builtin_specs(db)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    _ensure_format_specs()
    from .db import SessionLocal
    from .services.proposition_knowledge import ensure_framework, migrate_legacy_evidence, pending_proposition_analyses, sweep_stale_analyses
    with SessionLocal() as db:
        ensure_framework(db)
        migrate_legacy_evidence(db)
        sweep_stale_analyses(db)
    # 把实际使用的库路径打进日志：从不同目录启动时不会再悄悄换库
    logger.info("数据库：%s", settings.database_url)
    _sweep_stale_jobs()
    import os
    import threading
    from .v2.worker import dispatcher
    stop = threading.Event()
    thread = None
    if os.getenv("AIEDU_START_WORKER", "1") == "1":
        thread = threading.Thread(target=dispatcher, args=(stop,), name="aiedu-v2-dispatcher", daemon=True)
        thread.start()
    yield
    stop.set()
    if thread:
        thread.join(timeout=2)
    # Legacy regression workers own separate DB sessions; drain before a test
    # fixture may rebuild schema. No network request is awaited under a DB lock.
    import time
    from .services.tutorials import pending_tutorial_workers
    deadline = time.monotonic() + 30
    while pending_tutorial_workers() and time.monotonic() < deadline:
        for pending in pending_tutorial_workers():
            pending.join(timeout=min(0.5, max(0, deadline-time.monotonic())))
    # 等待持有独立会话的分析线程，避免测试下一次drop_all读取中间状态。
    for thread in pending_proposition_analyses():
        thread.join(timeout=30)


app = FastAPI(title="AI Edu API", version="0.1.0", lifespan=lifespan)


@app.middleware("http")
async def legacy_boundary(request: Request, call_next):
    path = request.url.path
    legacy = path.startswith("/api/") and not (path == "/api/v2" or path.startswith("/api/v2/"))
    if legacy and (not settings.enable_legacy_api or settings.v2_allow_paid):
        # Never let old generation, exports, bulk imports or config writes bypass
        # the v2 gates. Health remains public/read-only for local startup probes.
        if not (request.method in {"GET", "HEAD"} and path == "/api/health"):
            return JSONResponse(status_code=410, content={"code": "LEGACY_API_QUARANTINED", "message": "Use /api/v2"})
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.exception_handler(DomainError)
async def v2_error(request: Request, exc: DomainError):
    return JSONResponse(status_code=exc.status, content={"code": exc.code, "message": exc.code,
                        "retryable": exc.code in {"REVISION_STALE", "RESOURCE_BACKPRESSURE", "FIFO_WAIT"},
                        "task_id": request.path_params.get("task_id"), "run_id": request.path_params.get("run_id"),
                        "current_revision": exc.details.get("current_revision"), "details": exc.details})


@app.exception_handler(AIServiceError)
async def ai_error_handler(_: Request, exc: AIServiceError):
    # 模型调用失败（欠费 / 认证 / 限流 / 超时…）统一 502。此前这里静默返回，
    # errors.log 里查不到任何痕迹，用户只看到客户端一句通用报错——补一行日志。
    logger.warning("模型调用失败（502）：%s", exc)
    return JSONResponse(status_code=502, content={"code": "llm_error", "message": str(exc)})


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception):
    """未捕获异常：写完整堆栈到 data/errors.log，并返回可读错误（前端不再只显示 500）。"""
    _log_unhandled(exc)
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": "internal_error",
                "message": f"服务内部错误（{exc.__class__.__name__}），请检查本机诊断记录。",
            }
        },
    )
