"""
Health Module - Production Grade Health Checking

Architecture:
- Thread-safe avec asyncio.Lock
- Pas d'imports circulaires (n'importe pas depuis config)
- Dependency Injection via FastAPI app.state
- Compatible scaling horizontal

Usage:
    from app.core.health import create_health_checker, HealthChecker

    # Dans lifespan FastAPI
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        checker = create_health_checker(
            version=settings.app_version,
            environment=settings.environment,
            redis_client=redis,
            db_session_factory=db_session,
        )
        app.state.health_checker = checker
        yield

    # Dans les endpoints
    @router.get("/health")
    async def health(request: Request):
        checker = request.app.state.health_checker
        return await checker.full_check()
"""

# Import depuis le nouveau module thread-safe
from app.core.health.checker import (
    # Types
    HealthStatus,
    ComponentHealth,
    HealthReport,

    # Checker
    HealthChecker,
    create_health_checker,
    get_health_checker_dependency,

    # Check factories
    create_redis_check,
    create_mysql_check,
    create_chromadb_check,
    create_llm_check,
)

__all__ = [
    # Types
    "HealthStatus",
    "ComponentHealth",
    "HealthReport",

    # Checker
    "HealthChecker",
    "create_health_checker",
    "get_health_checker_dependency",

    # Check factories
    "create_redis_check",
    "create_mysql_check",
    "create_chromadb_check",
    "create_llm_check",
]


