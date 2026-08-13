"""
Application FastAPI principale - Production Ready
"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import redis.asyncio as redis_asyncio
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.middleware.rate_limiter import RateLimiterMiddleware
from app.api.middleware.request_logging import RequestLoggingMiddleware
from app.api.middleware.tenant_context import TenantContextMiddleware
from app.api.v1.endpoints import (
    admin,
    analytics,
    chat,
    coupons,
    faq,
    health,
    insights,
    recommendations,
    rules,
    tenants,
)
from app.core.config.settings import settings
from app.core.logging.config import setup_logging
from app.core.monitoring import setup_metrics
from app.infrastructure.database.connection import AsyncSessionLocal

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """
    Gestion du cycle de vie de l'application.
    Startup: Initialisation des connexions
    Shutdown: Cleanup propre
    """
    # Startup
    logger.info(
        "Starting AI Agent...",
        extra={"environment": settings.environment, "version": settings.app_version},
    )

    # Initialiser les connexions
    # await init_database()
    # await init_redis()
    # await init_vector_store()

    logger.info("AI Agent started successfully")

    yield

    # Shutdown
    logger.info("Shutting down AI Agent...")
    # await close_database()
    # await close_redis()


def create_application() -> FastAPI:
    """Factory pour créer l'application FastAPI"""

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="SaaS AI Assistant for E-commerce - Production API",
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan,
    )

    # =========================================================================
    # MIDDLEWARE (ordre important: dernier ajouté = premier exécuté)
    # =========================================================================

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.security.cors_origins,
        allow_credentials=settings.security.cors_allow_credentials,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-RateLimit-Remaining"],
    )

    # Request Logging
    app.add_middleware(RequestLoggingMiddleware)

    # Tenant Context (extrait le tenant de l'API key - hors dev/test, où un
    # header X-Tenant-ID suffit)
    app.add_middleware(TenantContextMiddleware, session_factory=AsyncSessionLocal)

    # Rate Limiting - lazy client (redis.asyncio doesn't connect until the
    # first command), so this is safe even if Redis isn't reachable yet at
    # startup; the middleware itself fails open on connection errors.
    redis_client = redis_asyncio.from_url(
        settings.redis.url,
        db=settings.redis.rate_limit_db,
        socket_timeout=settings.redis.socket_timeout,
        max_connections=settings.redis.max_connections,
    )
    app.add_middleware(RateLimiterMiddleware, redis_client=redis_client)

    # =========================================================================
    # EXCEPTION HANDLERS
    # =========================================================================

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """Handler pour erreurs de validation"""
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
        """Handler global pour erreurs non gérées"""
        logger.exception(
            "Unhandled exception", extra={"path": request.url.path, "method": request.method}
        )

        # En production, ne pas exposer les détails
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
    # ROUTES
    # =========================================================================

    # API v1
    api_prefix = settings.api_prefix

    app.include_router(health.router, prefix="/health", tags=["Health"])

    app.include_router(chat.router, prefix=f"{api_prefix}/chat", tags=["Chat - Client AI"])

    app.include_router(
        recommendations.router, prefix=f"{api_prefix}/recommendations", tags=["Recommendations"]
    )

    app.include_router(coupons.router, prefix=f"{api_prefix}/coupons", tags=["Coupons"])

    app.include_router(rules.router, prefix=f"{api_prefix}/rules", tags=["Rules"])

    app.include_router(insights.router, prefix=f"{api_prefix}/insights", tags=["Insights"])

    app.include_router(faq.router, prefix=f"{api_prefix}/faq", tags=["FAQ"])

    app.include_router(analytics.router, prefix=f"{api_prefix}/analytics", tags=["Analytics"])

    app.include_router(admin.router, prefix=f"{api_prefix}/admin", tags=["Admin AI"])

    app.include_router(tenants.router, prefix=f"{api_prefix}/tenants", tags=["Tenant Management"])

    # Prometheus metrics: per-request (api_requests_total, latency, tenant
    # attribution) via MetricsMiddleware, plus AI-specific metrics recorded
    # directly in RAG/LLM/guardrail code paths - see app/core/monitoring/.
    if settings.monitoring.prometheus_enabled:
        setup_metrics(app)

    # =========================================================================
    # ROOT ENDPOINT
    # =========================================================================

    @app.get("/", include_in_schema=False)
    async def root():
        return {
            "service": settings.app_name,
            "version": settings.app_version,
            "status": "running",
            "environment": settings.environment,
        }

    return app


# Instance de l'application
app = create_application()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # nosec B104 NOSONAR - conteneurisé, le mapping de port est explicite
        port=8000,
        reload=settings.is_development,
        workers=1 if settings.is_development else 4,
        log_level=settings.monitoring.log_level.lower(),
    )
