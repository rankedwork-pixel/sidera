"""Sidera FastAPI application.

Wires together all API routes, Slack event handling, Inngest function serving,
and middleware into a single FastAPI application ready to deploy on Railway.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.config import settings

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle for the application."""
    # --- Startup ---
    if settings.database_url:
        logger.info(
            "database.configured",
            url=settings.database_url[:30] + "...",
        )
    else:
        logger.warning(
            "database.not_configured",
            message="Running without database — data will not persist",
        )

    yield

    # --- Shutdown (graceful) ---
    logger.info("shutdown.start")
    try:
        from src.db.session import close_db

        await close_db()
    except Exception as exc:
        logger.warning("shutdown.db_close_failed", error=str(exc))

    try:
        from src.cache.redis_client import get_redis_client

        client = get_redis_client()
        if client and hasattr(client, "close"):
            await client.close()
            logger.info("shutdown.redis_closed")
    except Exception:
        pass  # Redis client may not be initialized

    logger.info("shutdown.complete")


def create_app() -> FastAPI:
    """Create and configure the Sidera FastAPI application."""

    # --- Structured logging (must be first) ---
    from src.middleware.logging_config import configure_logging

    configure_logging()

    # --- Sentry (no-op if sentry_dsn not configured) ---
    from src.middleware.sentry_setup import init_sentry

    init_sentry()

    app = FastAPI(
        title="Sidera",
        description="AI Agent Framework API",
        version="0.1.0",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # CORS — restrictive in production
    if settings.is_production:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[],
            allow_methods=["GET", "POST", "PUT", "DELETE"],
            allow_headers=["Authorization", "X-API-Key", "Content-Type"],
        )
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Rate limiting
    from src.middleware.rate_limiter import RateLimitMiddleware

    app.add_middleware(RateLimitMiddleware)

    # Structured request logging
    from src.middleware.request_logging import RequestLoggingMiddleware

    app.add_middleware(RequestLoggingMiddleware)

    # --- Health checks ---
    @app.get("/health")
    async def health_check():
        """Liveness probe — always returns 200 if the process is running."""
        return {
            "status": "healthy",
            "service": "sidera",
            "version": "0.1.0",
            "environment": settings.app_env,
        }

    @app.get("/health/ready")
    async def readiness_check():
        """Readiness probe — checks DB and Redis connectivity.

        Returns 200 only when all critical dependencies are reachable.
        Returns 503 with details if any dependency is down.
        """
        checks: dict[str, dict] = {}

        # Check PostgreSQL
        try:
            from sqlalchemy import text

            from src.db.session import get_db_session

            async with get_db_session() as session:
                await session.execute(text("SELECT 1"))
            checks["database"] = {"status": "ok"}
        except Exception as exc:
            checks["database"] = {"status": "error", "detail": str(exc)[:200]}

        # Check Redis
        try:
            from src.cache.redis_client import get_redis_client

            client = get_redis_client()
            if client:
                await client.ping()
                checks["redis"] = {"status": "ok"}
            else:
                checks["redis"] = {"status": "not_configured"}
        except Exception as exc:
            checks["redis"] = {"status": "error", "detail": str(exc)[:200]}

        all_ok = all(c.get("status") in ("ok", "not_configured") for c in checks.values())

        if not all_ok:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "degraded",
                    "checks": checks,
                },
            )

        return {"status": "ready", "checks": checks}

    @app.get("/")
    async def root():
        return {"service": "sidera", "status": "running"}

    # --- Org chart management routes (API key protected) ---
    from src.api.routes.org_chart import router as org_chart_router

    app.include_router(org_chart_router)

    # --- Stewardship routes (API key protected) ---
    from src.api.routes.stewardship import router as stewardship_router

    app.include_router(stewardship_router)

    # --- GDPR routes (admin only) ---
    from src.api.routes.gdpr import router as gdpr_router

    app.include_router(gdpr_router)

    # --- Slack routes ---
    try:
        from src.api.routes.slack import router as slack_router

        app.include_router(slack_router)
        logger.info("slack.router_mounted")
    except Exception as exc:
        logger.warning("slack.setup_failed", error=str(exc))

    # --- Inngest ---
    # Inngest serve() registers its own routes on the app
    try:
        from inngest.fast_api import serve as inngest_serve

        from src.workflows.daily_briefing import all_workflows
        from src.workflows.inngest_client import inngest_client

        inngest_serve(
            app=app,
            client=inngest_client,
            functions=all_workflows,
            serve_origin=settings.app_base_url if settings.is_production else None,
        )
        logger.info("inngest.configured", function_count=len(all_workflows))
    except Exception as exc:
        logger.warning("inngest.setup_failed", error=str(exc))

    # --- Global error handler ---
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        from src.middleware.sentry_setup import capture_exception

        capture_exception(exc)
        logger.error(
            "unhandled_exception",
            path=request.url.path,
            method=request.method,
            error=str(exc),
        )
        return JSONResponse(
            status_code=500,
            content={"detail": "Internal server error"},
        )

    logger.info(
        "app.created",
        environment=settings.app_env,
        routes=len(app.routes),
    )

    return app


# Module-level app for uvicorn: `uvicorn src.api.app:app`
app = create_app()
