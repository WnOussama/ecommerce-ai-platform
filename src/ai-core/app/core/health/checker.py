"""
Health Checker - Production Grade (Thread-Safe, No Circular Imports)

Architecture:
- Pas d'import depuis app.core.config (évite imports circulaires)
- Thread-safe avec asyncio.Lock
- Injection de dépendances via lifespan FastAPI
- Compatible avec scaling horizontal

Endpoints Kubernetes:
- /health/live  - Le process est-il vivant?
- /health/ready - Le service peut-il recevoir du trafic?
- /health/full  - État détaillé de toutes les dépendances
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Optional, TYPE_CHECKING

# Note: Aucun import depuis app.core.config ou autres modules internes
# pour éviter les imports circulaires. Les dépendances sont injectées.

logger = logging.getLogger(__name__)


# =============================================================================
# TYPES
# =============================================================================

class HealthStatus(str, Enum):
    """Statut de santé d'un composant"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class ComponentHealth:
    """État de santé d'un composant individuel"""
    name: str
    status: HealthStatus
    latency_ms: float = -1
    message: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    last_check: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "latency_ms": round(self.latency_ms, 2) if self.latency_ms >= 0 else None,
            "message": self.message,
            "details": self.details if self.details else None,
            "last_check": self.last_check.isoformat(),
        }


@dataclass
class HealthReport:
    """Rapport de santé complet"""
    status: HealthStatus
    components: Dict[str, ComponentHealth]
    version: str
    environment: str
    uptime_seconds: float
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "version": self.version,
            "environment": self.environment,
            "uptime_seconds": round(self.uptime_seconds, 2),
            "timestamp": self.timestamp.isoformat(),
            "components": {
                name: comp.to_dict()
                for name, comp in self.components.items()
            },
        }


# =============================================================================
# HEALTH CHECKER (Thread-Safe)
# =============================================================================

class HealthChecker:
    """
    Vérificateur de santé thread-safe pour toutes les dépendances.

    IMPORTANT: Cette classe n'importe RIEN depuis app.core.config
    pour éviter les imports circulaires. Les valeurs sont injectées.

    Thread-Safety:
    - Utilise asyncio.Lock pour protéger les modifications
    - Safe pour utilisation concurrente dans FastAPI
    - Compatible avec multiple workers (uvicorn --workers N)

    Usage (via Dependency Injection):
        # Dans lifespan de FastAPI
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            checker = HealthChecker(version="1.0.0", environment="production")
            checker.register_check("redis", create_redis_check(redis))
            app.state.health_checker = checker
            yield

        # Dans les endpoints
        @router.get("/health")
        async def health(request: Request):
            checker = request.app.state.health_checker
            return await checker.full_check()
    """

    __slots__ = (
        '_version', '_environment', '_check_timeout', '_start_time',
        '_checks', '_last_results', '_critical_components', '_lock',
        '_initialized'
    )

    def __init__(
        self,
        version: str = "0.0.0",
        environment: str = "unknown",
        check_timeout_seconds: float = 5.0,
    ):
        self._version = version
        self._environment = environment
        self._check_timeout = check_timeout_seconds
        self._start_time = datetime.utcnow()
        self._checks: Dict[str, Callable[[], Awaitable[ComponentHealth]]] = {}
        self._last_results: Dict[str, ComponentHealth] = {}
        self._critical_components: set = set()
        self._lock = asyncio.Lock()
        self._initialized = False

    @property
    def version(self) -> str:
        return self._version

    @property
    def environment(self) -> str:
        return self._environment

    @property
    def uptime_seconds(self) -> float:
        """Retourne le temps écoulé depuis le démarrage"""
        return (datetime.utcnow() - self._start_time).total_seconds()

    @property
    def is_initialized(self) -> bool:
        """Vérifie si le checker a été initialisé avec des checks"""
        return self._initialized

    async def register_check(
        self,
        name: str,
        check_func: Callable[[], Awaitable[ComponentHealth]],
        critical: bool = False,
    ) -> None:
        """
        Enregistre une fonction de vérification de santé.
        Thread-safe avec asyncio.Lock.

        Args:
            name: Nom du composant
            check_func: Fonction async retournant ComponentHealth
            critical: Si True, composant requis pour readiness
        """
        async with self._lock:
            self._checks[name] = check_func
            if critical:
                self._critical_components.add(name)
            self._initialized = True
            logger.debug(f"Registered health check: {name} (critical={critical})")

    def register_check_sync(
        self,
        name: str,
        check_func: Callable[[], Awaitable[ComponentHealth]],
        critical: bool = False,
    ) -> None:
        """
        Version synchrone pour utilisation dans __init__ ou setup.
        À utiliser UNIQUEMENT au démarrage, avant les requêtes.
        """
        self._checks[name] = check_func
        if critical:
            self._critical_components.add(name)
        self._initialized = True
        logger.debug(f"Registered health check (sync): {name} (critical={critical})")

    async def set_critical_components(self, components: set) -> None:
        """Définit les composants critiques pour readiness (thread-safe)"""
        async with self._lock:
            self._critical_components = components

    # =========================================================================
    # KUBERNETES PROBES
    # =========================================================================

    async def liveness(self) -> bool:
        """
        Probe de liveness pour Kubernetes.

        Vérifie que le process est vivant et capable de répondre.
        NE vérifie PAS les dépendances externes.

        Returns:
            True si le service est vivant
        """
        # Le simple fait de pouvoir exécuter ce code prouve que le process est vivant
        return True

    async def readiness(self) -> bool:
        """
        Probe de readiness pour Kubernetes.

        Vérifie que le service peut recevoir du trafic.
        Les composants CRITIQUES doivent être healthy.

        Returns:
            True si le service peut recevoir du trafic
        """
        if not self._checks:
            return True

        # Copier les références pour éviter les modifications pendant l'exécution
        async with self._lock:
            critical_checks = {
                name: func
                for name, func in self._checks.items()
                if name in self._critical_components
            }

        if not critical_checks:
            return True

        try:
            results = await self._run_checks(critical_checks)

            # Tous les composants critiques doivent être healthy ou degraded
            for name, health in results.items():
                if health.status == HealthStatus.UNHEALTHY:
                    logger.warning(
                        f"Readiness check failed: {name} is unhealthy",
                        extra={"component": name, "status": health.status.value}
                    )
                    return False

            return True

        except Exception as e:
            logger.error(f"Readiness check error: {e}")
            return False

    async def full_check(self) -> HealthReport:
        """
        Vérification complète de tous les composants.

        Returns:
            HealthReport avec l'état de tous les composants
        """
        components: Dict[str, ComponentHealth] = {}

        # Copier les checks pour éviter les modifications pendant l'exécution
        async with self._lock:
            checks_copy = dict(self._checks)
            critical_copy = set(self._critical_components)

        if checks_copy:
            components = await self._run_checks(checks_copy)

        # Déterminer le statut global
        if not components:
            overall_status = HealthStatus.HEALTHY
        else:
            statuses = [c.status for c in components.values()]

            if all(s == HealthStatus.HEALTHY for s in statuses):
                overall_status = HealthStatus.HEALTHY
            elif any(s == HealthStatus.UNHEALTHY for s in statuses):
                # Si un composant critique est unhealthy
                critical_unhealthy = any(
                    components[name].status == HealthStatus.UNHEALTHY
                    for name in critical_copy
                    if name in components
                )
                overall_status = HealthStatus.UNHEALTHY if critical_unhealthy else HealthStatus.DEGRADED
            else:
                overall_status = HealthStatus.DEGRADED

        return HealthReport(
            status=overall_status,
            components=components,
            version=self._version,
            environment=self._environment,
            uptime_seconds=self.uptime_seconds,
        )

    async def _run_checks(
        self,
        checks: Dict[str, Callable[[], Awaitable[ComponentHealth]]],
    ) -> Dict[str, ComponentHealth]:
        """Exécute les checks en parallèle avec timeout"""

        async def run_single_check(name: str, func: Callable) -> ComponentHealth:
            try:
                result = await asyncio.wait_for(
                    func(),
                    timeout=self._check_timeout
                )
                # Stocker le dernier résultat (thread-safe car dict assignment est atomique)
                self._last_results[name] = result
                return result
            except asyncio.TimeoutError:
                return ComponentHealth(
                    name=name,
                    status=HealthStatus.UNHEALTHY,
                    message=f"Health check timed out after {self._check_timeout}s",
                )
            except Exception as e:
                logger.error(f"Health check error for {name}: {e}")
                return ComponentHealth(
                    name=name,
                    status=HealthStatus.UNHEALTHY,
                    message=str(e),
                )

        tasks = [
            run_single_check(name, func)
            for name, func in checks.items()
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        return {
            check.name: check
            for check in results
            if isinstance(check, ComponentHealth)
        }

    def get_last_result(self, component_name: str) -> Optional[ComponentHealth]:
        """Retourne le dernier résultat de check pour un composant"""
        return self._last_results.get(component_name)


# =============================================================================
# CHECK FUNCTIONS FACTORY
# =============================================================================

def create_redis_check(redis_client) -> Callable[[], Awaitable[ComponentHealth]]:
    """
    Crée une fonction de check pour Redis.

    Args:
        redis_client: Client Redis (aioredis ou redis.asyncio)
    """
    async def check() -> ComponentHealth:
        start = time.perf_counter()
        try:
            # PING Redis
            await redis_client.ping()

            # Obtenir des infos supplémentaires
            info = await redis_client.info("server")

            latency = (time.perf_counter() - start) * 1000

            return ComponentHealth(
                name="redis",
                status=HealthStatus.HEALTHY,
                latency_ms=latency,
                details={
                    "version": info.get("redis_version"),
                    "connected_clients": info.get("connected_clients"),
                }
            )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return ComponentHealth(
                name="redis",
                status=HealthStatus.UNHEALTHY,
                latency_ms=latency,
                message=str(e),
            )

    return check


def create_mysql_check(session_factory) -> Callable[[], Awaitable[ComponentHealth]]:
    """
    Crée une fonction de check pour MySQL.

    Args:
        session_factory: AsyncSession factory (sqlalchemy)
    """
    async def check() -> ComponentHealth:
        start = time.perf_counter()
        try:
            # Import local pour éviter dépendance au niveau module
            from sqlalchemy import text

            async with session_factory() as session:
                # Simple query pour vérifier la connexion
                result = await session.execute(text("SELECT 1 as health, VERSION() as version"))
                row = result.fetchone()

                latency = (time.perf_counter() - start) * 1000

                return ComponentHealth(
                    name="mysql",
                    status=HealthStatus.HEALTHY,
                    latency_ms=latency,
                    details={
                        "version": row.version if row else None,
                    }
                )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return ComponentHealth(
                name="mysql",
                status=HealthStatus.UNHEALTHY,
                latency_ms=latency,
                message=str(e),
            )

    return check


def create_chromadb_check(chroma_client) -> Callable[[], Awaitable[ComponentHealth]]:
    """
    Crée une fonction de check pour ChromaDB.

    Args:
        chroma_client: Client ChromaDB
    """
    async def check() -> ComponentHealth:
        start = time.perf_counter()
        try:
            # ChromaDB heartbeat (sync, mais rapide)
            # Exécuter dans un thread pour ne pas bloquer l'event loop
            loop = asyncio.get_event_loop()
            heartbeat = await loop.run_in_executor(None, chroma_client.heartbeat)
            collections = await loop.run_in_executor(None, chroma_client.list_collections)

            latency = (time.perf_counter() - start) * 1000

            return ComponentHealth(
                name="chromadb",
                status=HealthStatus.HEALTHY,
                latency_ms=latency,
                details={
                    "heartbeat": heartbeat,
                    "collections_count": len(collections),
                }
            )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return ComponentHealth(
                name="chromadb",
                status=HealthStatus.UNHEALTHY,
                latency_ms=latency,
                message=str(e),
            )

    return check


def create_llm_check(llm_client, model: str = "gpt-4") -> Callable[[], Awaitable[ComponentHealth]]:
    """
    Crée une fonction de check pour le LLM provider.
    Vérifie que l'API est accessible (sans consommer de tokens).

    Args:
        llm_client: Client OpenAI/Anthropic
        model: Modèle à vérifier
    """
    async def check() -> ComponentHealth:
        start = time.perf_counter()
        try:
            # Vérifier que le client peut lister les modèles (pas de tokens)
            models = await llm_client.models.list()
            model_exists = any(m.id == model for m in models.data)

            latency = (time.perf_counter() - start) * 1000

            if model_exists:
                return ComponentHealth(
                    name="llm",
                    status=HealthStatus.HEALTHY,
                    latency_ms=latency,
                    details={"model": model, "available": True}
                )
            else:
                return ComponentHealth(
                    name="llm",
                    status=HealthStatus.DEGRADED,
                    latency_ms=latency,
                    message=f"Model {model} not found",
                    details={"model": model, "available": False}
                )
        except Exception as e:
            latency = (time.perf_counter() - start) * 1000
            return ComponentHealth(
                name="llm",
                status=HealthStatus.UNHEALTHY,
                latency_ms=latency,
                message=str(e),
            )

    return check


# =============================================================================
# FACTORY FUNCTION (Dependency Injection Pattern)
# =============================================================================

def create_health_checker(
    version: str,
    environment: str,
    redis_client=None,
    db_session_factory=None,
    chroma_client=None,
    llm_client=None,
    check_timeout_seconds: float = 5.0,
) -> HealthChecker:
    """
    Factory function pour créer un HealthChecker configuré.

    Utilise le pattern Dependency Injection au lieu d'un singleton global.
    Le HealthChecker créé doit être stocké dans app.state.

    Args:
        version: Version de l'application
        environment: Environnement (development, staging, production)
        redis_client: Client Redis optionnel
        db_session_factory: Factory de session SQLAlchemy optionnel
        chroma_client: Client ChromaDB optionnel
        llm_client: Client LLM optionnel
        check_timeout_seconds: Timeout pour chaque check

    Returns:
        HealthChecker configuré

    Usage:
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            checker = create_health_checker(
                version=settings.app_version,
                environment=settings.environment,
                redis_client=redis,
            )
            app.state.health_checker = checker
            yield
    """
    checker = HealthChecker(
        version=version,
        environment=environment,
        check_timeout_seconds=check_timeout_seconds,
    )

    # Enregistrer les checks disponibles (sync car au démarrage)
    if redis_client:
        checker.register_check_sync(
            "redis",
            create_redis_check(redis_client),
            critical=True,
        )

    if db_session_factory:
        checker.register_check_sync(
            "mysql",
            create_mysql_check(db_session_factory),
            critical=True,
        )

    if chroma_client:
        checker.register_check_sync(
            "chromadb",
            create_chromadb_check(chroma_client),
            critical=False,
        )

    if llm_client:
        checker.register_check_sync(
            "llm",
            create_llm_check(llm_client),
            critical=False,
        )

    logger.info(
        "Health checker created",
        extra={
            "version": version,
            "environment": environment,
            "checks": list(checker._checks.keys()),
            "critical": list(checker._critical_components),
        }
    )

    return checker


# =============================================================================
# FASTAPI DEPENDENCY (Alternative au singleton)
# =============================================================================

def get_health_checker_dependency():
    """
    Crée une FastAPI dependency pour accéder au HealthChecker.

    Usage:
        from app.core.health import get_health_checker_dependency

        get_checker = get_health_checker_dependency()

        @router.get("/health")
        async def health(checker: HealthChecker = Depends(get_checker)):
            return await checker.full_check()
    """
    async def dependency(request) -> HealthChecker:
        checker = getattr(request.app.state, 'health_checker', None)
        if checker is None:
            # Retourner un checker par défaut si non initialisé
            return HealthChecker(version="unknown", environment="unknown")
        return checker

    return dependency


# =============================================================================
# EXPORTS
# =============================================================================

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

