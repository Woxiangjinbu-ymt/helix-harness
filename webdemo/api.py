"""本机 Web 入口。业务资源在各自的路由内装配。"""
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import rsi_api


@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        await rsi_api.shutdown()


app = FastAPI(title="Helix · RSI Observatory", version="1.0", lifespan=lifespan)
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


@app.exception_handler(KeyError)
def not_found(request, exc):
    return JSONResponse({"detail": "Record not found"}, status_code=404)


@app.exception_handler(ValueError)
def bad_input(request, exc):
    return JSONResponse({"detail": str(exc)}, status_code=422)


@app.get("/api/coding/health")
def coding_health():
    return {"status": "ok"}


app.include_router(rsi_api.router)
