"""FastAPI application factory. Run: ``uvicorn app.main:app --port 8787``."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routers import auth, catalog, playback, sources, system
from app.container import Container, build_container, shutdown, startup
from app.infra.logging import configure_logging
from app.infra.settings import APP_VERSION, AppSettings
from app.services.context import is_https, public_base
from app.services.errors import ServiceError

log = logging.getLogger(__name__)
API_PREFIX = "/api/v1"


def _err(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"code": code, "message": message}, status_code=status, headers=headers)


def create_app(
    settings: AppSettings | None = None,
    *,
    container: Container | None = None,
    start_background: bool = True,
) -> FastAPI:
    settings = settings or AppSettings()
    configure_logging(settings.log_level, settings.log_format)
    c = container or build_container(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        await startup(c, start_background=start_background)
        log.info("StreamHub started", extra={"version": APP_VERSION})
        try:
            yield
        finally:
            await shutdown(c)

    app = FastAPI(
        title="StreamHub API",
        version=APP_VERSION,
        description="Unified streaming hub. Base path /api/v1.",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.container = c

    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
        host = request.headers.get("x-forwarded-host", request.headers.get("host", request.url.netloc))
        prefix = request.headers.get("x-forwarded-prefix", "").rstrip("/")
        t1 = public_base.set(f"{proto}://{host.split(',')[0].strip()}{prefix}")
        t2 = is_https.set(proto == "https")
        try:
            return await call_next(request)
        finally:
            public_base.reset(t1)
            is_https.reset(t2)

    @app.exception_handler(ServiceError)
    async def service_error(_req: Request, exc: ServiceError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return _err(exc.status, exc.code, exc.message, headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_req: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(x) for x in first.get("loc", []) if x != "body")
        return _err(422, "validation_error", f"{loc}: {first.get('msg', 'invalid request')}".strip(": "))

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_req: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _err(exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def unhandled(_req: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return _err(500, "internal_error", "internal server error")

    for r in (auth.router, sources.router, catalog.router, playback.router, system.router):
        app.include_router(r, prefix=API_PREFIX)

    _mount_web(app, settings.web_dist)
    return app


def _mount_web(app: FastAPI, dist: Path) -> None:
    """Serve the built web client at / with SPA fallback (only if the build exists)."""
    index = dist / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> Any:
        if full_path.startswith("api/"):
            return _err(404, "not_found", "no such endpoint")
        if not index.is_file():
            if full_path == "":
                return JSONResponse({"name": "StreamHub", "version": APP_VERSION, "api": "/api/docs"})
            return _err(404, "not_found", "web client not installed")
        root = dist.resolve()
        candidate = (dist / full_path).resolve()
        if full_path and candidate.is_file() and root in candidate.parents:
            cache = "public, max-age=31536000, immutable" if "/assets/" in f"/{full_path}" else "no-cache"
            return FileResponse(candidate, headers={"Cache-Control": cache})
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


_app: FastAPI | None = None


def __getattr__(name: str) -> Any:
    """``app.main:app`` is built on first access so importing this module has no side effects."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
