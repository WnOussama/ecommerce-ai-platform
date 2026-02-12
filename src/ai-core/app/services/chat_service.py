"""
AI Chat Service - Service dédié aux interactions client
Point d'entrée pour: Chat, Recommendations, Coupons, FAQ

Ce service est optimisé pour la latence et le throughput élevé.
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator
import logging
import os

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from prometheus_client import make_asgi_app

from app.core.config.settings import settings
from app.api.middleware.rate_limiter import RateLimiterMiddleware
from app.api.middleware.tenant_context import TenantContextMiddleware
from app.api.middleware.request_logging import RequestLoggingMiddleware
from app.core.logging.config import setup_logging

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)

# Service identifier
SERVICE_NAME = "ai-chat-service"
SERVICE_VERSION = settings.app_version


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """
    Gestion du cycle de vie du Chat Service.
    Focus: Connexions optimisées pour latence minimale
    """
    logger.info(f"Starting {SERVICE_NAME}...", extra={
        "service": SERVICE_NAME,
        "environment": settings.environment,
        "version": SERVICE_VERSION
    })

    # Startup: Initialiser les connexions critiques
    # - Redis (cache, rate limit, sessions)
    # - ChromaDB (RAG queries - READ ONLY)
    # - LLM clients (OpenAI, Claude)
    # Note: MySQL connection pool optimisé pour lectures rapides

    # Initialize queue client for async embedding requests
    from app.services.message_queue.redis_queue import get_queue_client
    app.state.queue_client = get_queue_client(
        redis_url=f"redis://{settings.redis.host}:{settings.redis.port}",
        consumer_group="chat-service",
        consumer_name=f"chat-{os.getpid()}"
    )
    await app.state.queue_client.connect()

    logger.info(f"{SERVICE_NAME} started successfully")

    yield

    # Shutdown
    logger.info(f"Shutting down {SERVICE_NAME}...")
    await app.state.queue_client.disconnect()


def create_chat_service() -> FastAPI:
    """Factory pour créer le Chat Service"""

    app = FastAPI(
        title=f"SaaS AI - {SERVICE_NAME}",
        version=SERVICE_VERSION,
        description="AI Chat Service - Customer-facing interactions (Chat, Recommendations, Coupons, FAQ)",
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
        lifespan=lifespan
    )

    # =========================================================================
    # MIDDLEWARE (optimisés pour latence)
    # =========================================================================

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.security.cors_origins,
        allow_credentials=settings.security.cors_allow_credentials,
        allow_methods=["GET", "POST", "OPTIONS"],  # Limité aux opérations nécessaires
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "X-RateLimit-Remaining"]
    )

    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(TenantContextMiddleware)
    app.add_middleware(RateLimiterMiddleware)

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
                "details": exc.errors()
            }
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception in chat service", extra={
            "path": request.url.path,
            "method": request.method,
            "service": SERVICE_NAME
        })

        if settings.is_production:
            return JSONResponse(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                content={
                    "error": "internal_error",
                    "message": "An unexpected error occurred"
                }
            )

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "internal_error",
                "message": str(exc),
                "type": type(exc).__name__
            }
        )

    # =========================================================================
    # ROUTES - Client-facing only
    # =========================================================================

    from app.api.v1.endpoints import chat, recommendations, coupons, faq, health

    api_prefix = settings.api_prefix

    # Health check
    app.include_router(
        health.router,
        prefix="/health",
        tags=["Health"]
    )

    # Chat endpoint (core feature)
    app.include_router(
        chat.router,
        prefix=f"{api_prefix}/chat",
        tags=["Chat"]
    )

    # Product recommendations
    app.include_router(
        recommendations.router,
        prefix=f"{api_prefix}/recommendations",
        tags=["Recommendations"]
    )

    # Coupon generation
    app.include_router(
        coupons.router,
        prefix=f"{api_prefix}/coupons",
        tags=["Coupons"]
    )

    # FAQ queries
    app.include_router(
        faq.router,
        prefix=f"{api_prefix}/faq",
        tags=["FAQ"]
    )

    # Prometheus metrics
    if settings.monitoring.prometheus_enabled:
        metrics_app = make_asgi_app()
        app.mount("/metrics", metrics_app)

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
            "capabilities": ["chat", "recommendations", "coupons", "faq"]
        }

    return app


# Instance de l'application
app = create_chat_service()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.services.chat_service:app",
        host="0.0.0.0",
        port=int(os.getenv("CHAT_SERVICE_PORT", "8001")),
        reload=settings.is_development,
        workers=1 if settings.is_development else 4,
        log_level=settings.monitoring.log_level.lower()
    )

