"""Meditron: patient workspace."""

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Thread

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError, OperationalError

from app.db.models import SessionLocal, init_db
from app.db.seed import seed_if_empty
from app.product.api import router
from app.product.calendar import maintain
from app.product.models import seed_accounts
from app.product.patient import router as patient_router
from app.product.push import router as push_router

STATIC = Path(__file__).parent / "static"


def reminder_tick():
    from app.product.push import send_due

    with SessionLocal() as db:
        maintain(db)
        db.commit()
        send_due(db)


@asynccontextmanager
async def lifespan(app: FastAPI):

    init_db()
    with SessionLocal() as db:
        seed_if_empty(db)
        if os.getenv("MEDITRON_DEMO", "1") == "1":
            seed_accounts(db)
    if os.getenv("MEDITRON_LLM_PRELOAD", "0") == "1":
        from app.product.llm import preload

        Thread(target=preload, name="llm-preload", daemon=True).start()
    scheduler = BackgroundScheduler(timezone="UTC")
    if os.getenv("MEDITRON_SCHEDULER", "1") == "1":
        scheduler.add_job(
            reminder_tick, "interval", seconds=30, max_instances=1, coalesce=True
        )
        scheduler.start()
    try:
        yield
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=True)


app = FastAPI(title="Meditron — чат и календарь пациента", lifespan=lifespan)


@app.middleware("http")
async def request_boundary(request: Request, call_next):
    # Non-simple header and same-site session cookies; cross-origin CORS is disabled.
    if request.url.path.startswith("/api/") and request.method not in {
        "GET",
        "HEAD",
        "OPTIONS",
    }:
        if request.headers.get("X-Meditron-Request") != "1":
            return JSONResponse(
                {"detail": "Запрос должен быть отправлен из приложения"},
                status_code=403,
            )
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(IntegrityError)
async def conflict(request: Request, exc: IntegrityError):
    return JSONResponse(
        {
            "detail": "Данные уже изменены другим запросом. Обновите экран и повторите действие."
        },
        status_code=409,
    )


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    logging.getLogger("uvicorn.error").error(
        "Unhandled request error: %s %s",
        request.method,
        request.url.path,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    detail = (
        "Не удалось получить ответ помощника. Сообщение не отправлено — попробуйте ещё раз."
        if request.url.path == "/api/chat"
        else "Сервис не смог выполнить действие. Попробуйте ещё раз."
    )
    return JSONResponse({"detail": detail}, status_code=500)


@app.exception_handler(OperationalError)
async def database_error(request: Request, exc: OperationalError):
    code = getattr(exc.orig, "sqlite_errorcode", 0)
    if (code & 0xFF) in {5, 6} or "database is locked" in str(exc.orig).lower():
        logging.getLogger("uvicorn.error").warning(
            "SQLite is locked during %s %s", request.method, request.url.path
        )
        return JSONResponse(
            {
                "detail": "База данных занята другим приложением. Сохраните или отмените изменения в редакторе SQLite и закройте базу. Затем повторите запрос."
            },
            status_code=503,
        )
    return await unexpected_error(request, exc)


app.include_router(router)
app.include_router(patient_router)
app.include_router(push_router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/sw.js")
def service_worker():
    return FileResponse(
        STATIC / "sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


app.mount("/static", StaticFiles(directory=STATIC), name="static")
