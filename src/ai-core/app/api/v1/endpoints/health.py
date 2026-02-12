"""
Health Check Endpoints - Production Ready (No Circular Imports)

Endpoints Kubernetes:
- /health/live  - Liveness probe (process alive?)
- /health/ready - Readiness probe (can receive traffic?)
- /health/full  - Full health report (all components)

Architecture:
- Utilise Dependency Injection via app.state
- Pas d'import depuis config (évite circular imports)
- Thread-safe
"""

from datetime import datetime
import logging

from fastapi import APIRouter, Request, Response, status

from app.core.health import (
    HealthChecker,
    HealthStatus,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_checker(request: Request) -> HealthChecker:
    """
    Récupère le HealthChecker depuis app.state.
    Retourne un checker par défaut si non initialisé.
    """
    checker = getattr(request.app.state, 'health_checker', None)
    if checker is None:
        # Fallback: créer un checker minimal
        # Cela ne devrait pas arriver en production
        logger.warning("HealthChecker not initialized in app.state")
        return HealthChecker(version="unknown", environment="unknown")
    return checker


@router.get("")
@router.get("/")
async def health_check(request: Request):
    """
    Basic health check endpoint.
    Returns service status and timestamp.
    """
    checker = _get_checker(request)
    report = await checker.full_check()

    return {
        "status": report.status.value,
        "timestamp": datetime.utcnow().isoformat(),
        "version": report.version,
        "uptime_seconds": report.uptime_seconds,
    }


@router.get("/live")
async def liveness_check(request: Request, response: Response):
    """
    Liveness probe for Kubernetes.

    Checks if the process is alive and can respond.
    Does NOT check external dependencies.

    Returns:
        200 if alive
        503 if dead (should never happen if this responds)
    """
    checker = _get_checker(request)
    is_alive = await checker.liveness()

    if is_alive:
        return {"status": "alive", "timestamp": datetime.utcnow().isoformat()}

    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "dead"}


@router.get("/ready")
async def readiness_check(request: Request, response: Response):
    """
    Readiness probe for Kubernetes.

    Checks if the service can receive traffic.
    All CRITICAL dependencies must be healthy.

    Returns:
        200 if ready to receive traffic
        503 if not ready (Kubernetes will stop sending traffic)
    """
    checker = _get_checker(request)
    is_ready = await checker.readiness()

    if is_ready:
        return {
            "status": "ready",
            "timestamp": datetime.utcnow().isoformat(),
        }

    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    logger.warning("Readiness check failed - service not ready")

    return {
        "status": "not_ready",
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/full")
async def full_health_check(request: Request, response: Response):
    """
    Full health check with all components.

    Returns detailed status of:
    - Redis
    - MySQL
    - ChromaDB
    - LLM Provider

    Returns:
        200 if all healthy
        200 with degraded status if non-critical components unhealthy
        503 if critical components unhealthy
    """
    checker = _get_checker(request)
    report = await checker.full_check()

    # Set appropriate status code
    if report.status == HealthStatus.UNHEALTHY:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return report.to_dict()



