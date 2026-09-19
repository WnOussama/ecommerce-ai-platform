"""
AI Admin Service - Service dédié aux opérations administrateur
Point d'entrée pour: Analytics, Admin Commands, Reports, Tenant Management

Ce service est optimisé pour les opérations complexes et les requêtes analytiques.
"""

import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import make_asgi_app

from app.api.middleware.rate_limiter import RateLimiterMiddleware
from app.api.middleware.request_logging import RequestLoggingMiddleware
from app.api.middleware.tenant_context import TenantContextMiddleware
from app.core.config.settings import settings
from app.core.logging.config import setup_logging

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)

# Service identifier
SERVICE_NAME = "ai-admin-service"
SERVICE_VERSION = settings.app_version


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """
    Gestion du cycle de vie de l'Admin Service.
    Focus: Connexions optimisées pour requêtes analytiques
    """
    logger.info(
        f"Starting {SERVICE_NAME}...",
        extra={
            "service": SERVICE_NAME,
            "environment": settings.environment,
            "version": SERVICE_VERSION,
        },
    )

    # Startup: Initialiser les connexions
    # - MySQL (pool plus large pour requêtes analytiques)
    # - Redis (cache analytics, session admin)
    # - Queue client (pour déléguer les bulk operations)

    from app.services.message_queue.redis_queue import get_queue_client

    app.state.queue_client = get_queue_client(
        redis_url=settings.redis.url,  # inclut le mot de passe (Redis exige REDIS_PASSWORD)
        consumer_group="admin-service",
        consumer_name=f"admin-{os.getpid()}",
    )
    await app.state.queue_client.connect()

    logger.info(f"{SERVICE_NAME} started successfully")

    yield

    # Shutdown
    logger.info(f"Shutting down {SERVICE_NAME}...")
    await app.state.queue_client.disconnect()


def create_admin_service() -> FastAPI:
    """Factory pour créer l'Admin Service"""

    app = FastAPI(
        title=f"SaaS AI - {SERVICE_NAME}",
        version=SERVICE_VERSION,
        description="AI Admin Service - Administrative operations (Analytics, Commands, Reports)",
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
    )

    # =========================================================================
    # MIDDLEWARE
    # =========================================================================

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.security.cors_origins,
        allow_credentials=settings.security.cors_allow_credentials,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(TenantContextMiddleware)

    # Rate limiting plus strict pour admin
    app.add_middleware(
        RateLimiterMiddleware,
        # Override default limits for admin endpoints
    )

    # =========================================================================
    # EXCEPTION HANDLERS
    # =========================================================================

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "validation_error",
                "message": "Invalid request data",
                "details": exc.errors(),
            },
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.exception(
            "Unhandled exception in admin service",
            extra={"path": request.url.path, "method": request.method, "service": SERVICE_NAME},
        )

        if settings.is_production:
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={"error": "internal_error", "message": "An unexpected error occurred"},
            )

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "internal_error", "message": str(exc), "type": type(exc).__name__},
        )

    # =========================================================================
    # ROUTES - Admin-facing only
    # =========================================================================

    from app.api.v1.endpoints import admin, analytics, health, tenants

    api_prefix = settings.api_prefix

    # Health check
    app.include_router(health.router, prefix="/health", tags=["Health"])

    # Admin AI commands
    app.include_router(admin.router, prefix=f"{api_prefix}/admin", tags=["Admin AI"])

    # Analytics & Reports
    app.include_router(analytics.router, prefix=f"{api_prefix}/analytics", tags=["Analytics"])

    # Tenant management
    app.include_router(tenants.router, prefix=f"{api_prefix}/tenants", tags=["Tenant Management"])

    # Prometheus metrics
    if settings.monitoring.prometheus_enabled:
        metrics_app = make_asgi_app()
        app.mount("/metrics", metrics_app)

    # =========================================================================
    # INTERNAL ENDPOINTS (for bulk operation status)
    # =========================================================================

    @app.get(f"{api_prefix}/operations/{{operation_id}}/status")
    async def get_operation_status(operation_id: str, request: Request):
        """Vérifier le statut d'une opération bulk en cours"""
        # TODO: Implement status check via Redis
        return {
            "operation_id": operation_id,
            "status": "pending",
            "progress": 0,
            "message": "Operation in progress",
        }

    # =========================================================================
    # ROOT ENDPOINT
    # =========================================================================

    @app.get("/", include_in_schema=False)
    async def root():
        return {
            "service": SERVICE_NAME,
            "version": SERVICE_VERSION,
            "status": "running",
            "environment": settings.environment,
            "capabilities": ["analytics", "admin_commands", "reports", "tenant_management"],
        }

    return app


# Instance de l'application
app = create_admin_service()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.services.admin_service:app",
        host="0.0.0.0",  # nosec B104 NOSONAR - conteneurisé, le mapping de port est explicite
        port=int(os.getenv("ADMIN_SERVICE_PORT", "8002")),
        reload=settings.is_development,
        workers=1 if settings.is_development else 2,  # Moins de workers, requêtes plus lourdes
        log_level=settings.monitoring.log_level.lower(),
    )
