"""OptiReach ERP — FastAPI application factory.

Run locally with:  uvicorn app.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_v1_router
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.logging import configure_logging, get_logger, new_trace_id
from app.core.scheduler import shutdown_scheduler, start_scheduler
from app.core.websocket import ws_manager
from app.services.audit import request_ip_var

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.debug)
    if settings.scheduler_enabled:
        start_scheduler()
    try:
        await ws_manager.startup()
    except Exception:  # noqa: BLE001 — realtime is optional in local dev
        logger.warning("websocket_redis_unavailable", redis_url=settings.redis_url)
    # A restart is precisely the event that strands a Tally import mid-run: the
    # task executing it died with the old process, and nothing else will ever
    # move it off "Importing". Sweep before serving traffic.
    try:
        from app.services.migration.background import reap_stale_runs

        await reap_stale_runs()
    except Exception:  # noqa: BLE001 — never block startup on housekeeping
        logger.warning("migration_reaper_startup_failed")
    logger.info("app_started", environment=settings.environment)
    yield
    await ws_manager.shutdown()
    shutdown_scheduler()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="Modular ERP API — accounts, stock, buying, selling, CRM, "
        "manufacturing, projects, assets, quality and support.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        """Assign a trace_id and capture the client IP for the audit trail."""
        trace_id = new_trace_id()
        request_ip_var.set(request.client.host if request.client else None)
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.envelope())

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        loc = [str(p) for p in first.get("loc", []) if p != "body"]
        return JSONResponse(
            status_code=422,
            content={
                "detail": first.get("msg", "Validation error"),
                "code": "ERR_VALIDATION",
                "field": ".".join(loc) or None,
            },
        )

    @app.get("/health", tags=["meta"], summary="Liveness probe", description="Returns ok.")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(api_v1_router, prefix=settings.api_v1_prefix)
    return app


app = create_app()
