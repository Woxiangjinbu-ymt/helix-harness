"""本机 Web 入口。业务资源在各自的路由内装配。"""

import sqlite3
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import rsi_api
from .coding import routes as coding_routes


@asynccontextmanager
async def lifespan(app):
    from .coding.store import data_dir

    if (data_dir() / "coding.sqlite").exists():
        try:
            coding_routes.manager().activate()
        except (OSError, sqlite3.DatabaseError):
            # 其他模块可独立启动，Coding 路由在访问时重试并报告存储状态。
            pass
    try:
        yield
    finally:
        if coding_routes.manager.cache_info().currsize:
            await coding_routes.manager().shutdown()
        await rsi_api.shutdown()


app = FastAPI(
    title="MewCode · Coding Workspace & RSI Center", version="1.0", lifespan=lifespan
)
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]"]
)


@app.middleware("http")
async def local_only(request, call_next):
    origin = request.headers.get("origin")
    if (
        origin and urlparse(origin).hostname not in ("localhost", "127.0.0.1", "::1")
    ) or (
        request.method not in ("GET", "HEAD", "OPTIONS")
        and request.headers.get("x-rsi-client") != "console"
    ):
        return JSONResponse({"detail": "Local console requests only"}, status_code=403)
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation(request, exc):
    if request.url.path.startswith("/api/coding/profiles"):
        return JSONResponse({"detail": "Provider 配置字段无效"}, status_code=422)
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(KeyError)
def not_found(request, exc):
    return JSONResponse({"detail": "Record not found"}, status_code=404)


@app.exception_handler(ValueError)
def bad_input(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=422)


@app.exception_handler(sqlite3.DatabaseError)
def storage_error(request, exc):
    return JSONResponse(
        {"detail": "进度存储不可用", "code": "storage_error"}, status_code=503
    )


@app.get("/api/coding/health")
def coding_health():
    return {"status": "ok"}


app.include_router(coding_routes.router)
app.include_router(rsi_api.router)
