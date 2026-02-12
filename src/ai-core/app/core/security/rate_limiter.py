"""
Rate Limiter Avancé - Par Tenant + IP avec Sliding Window

Architecture:
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        RATE LIMITING ARCHITECTURE                                │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  Requête entrante                                                                │
│        │                                                                         │
│        ▼                                                                         │
│  ┌─────────────────┐                                                            │
│  │ 1. GLOBAL LIMIT │  Protection DDoS globale                                   │
│  │    (par IP)     │  1000 req/min par IP                                       │
│  └────────┬────────┘                                                            │
│           │                                                                      │
│           ▼                                                                      │
│  ┌─────────────────┐                                                            │
│  │ 2. TENANT LIMIT │  Limite selon le plan                                      │
│  │    (par tenant) │  Starter: 30/min, Pro: 100/min, Enterprise: 300/min       │
│  └────────┬────────┘                                                            │
│           │                                                                      │
│           ▼                                                                      │
│  ┌─────────────────┐                                                            │
│  │ 3. ENDPOINT     │  Limites spécifiques par endpoint                          │
│  │    LIMIT        │  /chat: 60/min, /admin: 10/min                            │
│  └────────┬────────┘                                                            │
│           │                                                                      │
│           ▼                                                                      │
│  ┌─────────────────┐                                                            │
│  │ 4. BURST        │  Permet des pics temporaires                               │
│  │    ALLOWANCE    │  +50% du limit pendant 10s                                │
│  └────────┬────────┘                                                            │
│           │                                                                      │
│           ▼                                                                      │
│  Requête autorisée ou 429 Too Many Requests                                     │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘

Algorithme: Sliding Window Log (précis) avec fallback Token Bucket (performant)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple
from enum import Enum
import time
import asyncio
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

class RateLimitTier(str, Enum):
    """Tiers de rate limiting"""
    GLOBAL = "global"       # Par IP (protection DDoS)
    TENANT = "tenant"       # Par tenant (plan)
    ENDPOINT = "endpoint"   # Par endpoint
    USER = "user"           # Par utilisateur (optionnel)


@dataclass
class RateLimitRule:
    """Règle de rate limiting"""
    tier: RateLimitTier
    limit: int              # Requêtes autorisées
    window_seconds: int     # Fenêtre de temps
    burst_limit: int = 0    # Limite de burst (0 = pas de burst)
    burst_window_seconds: int = 10  # Fenêtre de burst

    @property
    def requests_per_second(self) -> float:
        return self.limit / self.window_seconds


@dataclass
class RateLimitConfig:
    """Configuration complète du rate limiter"""

    # Limites globales (par IP)
    global_limit_per_minute: int = 1000
    global_burst_limit: int = 100

    # Limites par plan tenant
    tenant_limits: Dict[str, int] = field(default_factory=lambda: {
        "starter": 30,       # 30 req/min
        "professional": 100, # 100 req/min
        "enterprise": 300,   # 300 req/min
    })

    # Limites par endpoint (req/min)
    endpoint_limits: Dict[str, int] = field(default_factory=lambda: {
        "/api/v1/chat": 60,
        "/api/v1/recommend": 30,
        "/api/v1/admin/command": 10,
        "/api/v1/admin/analytics": 30,
        "/api/v1/sync": 5,
    })

    # Burst
    burst_multiplier: float = 1.5
    burst_window_seconds: int = 10

    # Comportement
    include_retry_after: bool = True
    log_exceeded: bool = True


@dataclass
class RateLimitResult:
    """Résultat d'une vérification de rate limit"""
    allowed: bool
    remaining: int          # Requêtes restantes
    limit: int              # Limite totale
    reset_at: datetime      # Quand le compteur reset
    retry_after_seconds: int = 0  # Secondes avant retry (si bloqué)
    tier_exceeded: Optional[RateLimitTier] = None  # Quel tier a bloqué

    def to_headers(self) -> Dict[str, str]:
        """Génère les headers de rate limit"""
        headers = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(0, self.remaining)),
            "X-RateLimit-Reset": str(int(self.reset_at.timestamp())),
        }

        if not self.allowed and self.retry_after_seconds > 0:
            headers["Retry-After"] = str(self.retry_after_seconds)

        return headers


# =============================================================================
# SLIDING WINDOW COUNTER
# =============================================================================

class SlidingWindowCounter:
    """
    Implémentation du Sliding Window Counter.

    Plus précis que le Token Bucket pour les limites strictes.
    Utilise Redis pour la persistance et le scaling horizontal.
    """

    def __init__(
        self,
        redis_client=None,
        precision_seconds: int = 1,
    ):
        self._redis = redis_client
        self._precision = precision_seconds
        self._local_cache: Dict[str, List[float]] = {}  # Fallback local

    async def increment(
        self,
        key: str,
        window_seconds: int,
        limit: int,
    ) -> Tuple[bool, int]:
        """
        Incrémente le compteur et vérifie la limite.

        Returns:
            (allowed, current_count)
        """
        now = time.time()
        window_start = now - window_seconds

        if self._redis:
            return await self._increment_redis(key, window_start, now, limit)
        else:
            return self._increment_local(key, window_start, now, limit)

    async def _increment_redis(
        self,
        key: str,
        window_start: float,
        now: float,
        limit: int,
    ) -> Tuple[bool, int]:
        """Implémentation Redis (production)"""
        # Utilise un sorted set avec timestamp comme score
        pipe = self._redis.pipeline()

        # Supprimer les anciennes entrées
        pipe.zremrangebyscore(key, 0, window_start)

        # Ajouter la nouvelle requête
        pipe.zadd(key, {f"{now}": now})

        # Compter les requêtes dans la fenêtre
        pipe.zcount(key, window_start, now)

        # Définir l'expiration
        pipe.expire(key, int(now - window_start) + 60)

        results = await pipe.execute()
        current_count = results[2]

        allowed = current_count <= limit

        return allowed, current_count

    def _increment_local(
        self,
        key: str,
        window_start: float,
        now: float,
        limit: int,
    ) -> Tuple[bool, int]:
        """Implémentation locale (fallback)"""
        if key not in self._local_cache:
            self._local_cache[key] = []

        # Nettoyer les anciennes entrées
        self._local_cache[key] = [
            ts for ts in self._local_cache[key]
            if ts > window_start
        ]

        # Compter
        current_count = len(self._local_cache[key])

        if current_count < limit:
            self._local_cache[key].append(now)
            return True, current_count + 1

        return False, current_count

    async def get_count(self, key: str, window_seconds: int) -> int:
        """Obtient le compteur actuel"""
        now = time.time()
        window_start = now - window_seconds

        if self._redis:
            return await self._redis.zcount(key, window_start, now)
        else:
            if key in self._local_cache:
                return len([ts for ts in self._local_cache[key] if ts > window_start])
            return 0


# =============================================================================
# ADVANCED RATE LIMITER
# =============================================================================

class AdvancedRateLimiter:
    """
    Rate Limiter avancé avec multiple tiers.

    Features:
    - Limites par IP (global)
    - Limites par tenant (selon plan)
    - Limites par endpoint
    - Burst allowance
    - Headers de rate limit
    """

    def __init__(
        self,
        redis_client=None,
        config: Optional[RateLimitConfig] = None,
    ):
        self._config = config or RateLimitConfig()
        self._counter = SlidingWindowCounter(redis_client)

    async def check(
        self,
        client_ip: str,
        tenant_id: Optional[str] = None,
        tenant_plan: str = "starter",
        endpoint: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> RateLimitResult:
        """
        Vérifie toutes les limites et retourne le résultat.

        Vérifie dans l'ordre:
        1. Limite globale (IP)
        2. Limite tenant (plan)
        3. Limite endpoint
        4. Limite utilisateur (optionnel)
        """
        now = datetime.utcnow()

        # 1. Vérifier limite globale (IP)
        global_result = await self._check_global_limit(client_ip)
        if not global_result.allowed:
            return global_result

        # 2. Vérifier limite tenant
        if tenant_id:
            tenant_result = await self._check_tenant_limit(tenant_id, tenant_plan)
            if not tenant_result.allowed:
                return tenant_result

        # 3. Vérifier limite endpoint
        if endpoint and tenant_id:
            endpoint_result = await self._check_endpoint_limit(tenant_id, endpoint)
            if not endpoint_result.allowed:
                return endpoint_result

        # 4. Vérifier limite utilisateur (optionnel)
        if user_id and tenant_id:
            user_result = await self._check_user_limit(tenant_id, user_id)
            if not user_result.allowed:
                return user_result

        # Toutes les limites OK
        # Retourner le résultat avec le remaining le plus bas
        remaining = min(
            global_result.remaining,
            tenant_result.remaining if tenant_id else float('inf'),
            endpoint_result.remaining if endpoint else float('inf'),
        )

        return RateLimitResult(
            allowed=True,
            remaining=int(remaining),
            limit=tenant_result.limit if tenant_id else global_result.limit,
            reset_at=now + timedelta(seconds=60),
        )

    async def _check_global_limit(self, client_ip: str) -> RateLimitResult:
        """Vérifie la limite globale par IP"""
        key = f"ratelimit:global:{client_ip}"
        limit = self._config.global_limit_per_minute
        window = 60  # 1 minute

        allowed, count = await self._counter.increment(key, window, limit)

        result = RateLimitResult(
            allowed=allowed,
            remaining=limit - count,
            limit=limit,
            reset_at=datetime.utcnow() + timedelta(seconds=window),
            tier_exceeded=RateLimitTier.GLOBAL if not allowed else None,
        )

        if not allowed:
            result.retry_after_seconds = self._calculate_retry_after(count, limit, window)

            if self._config.log_exceeded:
                logger.warning(
                    "Global rate limit exceeded",
                    extra={"client_ip": client_ip, "count": count, "limit": limit}
                )

        return result

    async def _check_tenant_limit(
        self,
        tenant_id: str,
        tenant_plan: str,
    ) -> RateLimitResult:
        """Vérifie la limite par tenant selon le plan"""
        key = f"ratelimit:tenant:{tenant_id}"
        limit = self._config.tenant_limits.get(tenant_plan, 30)
        window = 60  # 1 minute

        # Vérifier aussi le burst
        burst_allowed = await self._check_burst(tenant_id, limit)

        if burst_allowed:
            # Utiliser la limite avec burst
            effective_limit = int(limit * self._config.burst_multiplier)
        else:
            effective_limit = limit

        allowed, count = await self._counter.increment(key, window, effective_limit)

        result = RateLimitResult(
            allowed=allowed,
            remaining=effective_limit - count,
            limit=limit,  # Afficher la limite normale
            reset_at=datetime.utcnow() + timedelta(seconds=window),
            tier_exceeded=RateLimitTier.TENANT if not allowed else None,
        )

        if not allowed:
            result.retry_after_seconds = self._calculate_retry_after(count, limit, window)

            if self._config.log_exceeded:
                logger.warning(
                    "Tenant rate limit exceeded",
                    extra={
                        "tenant_id": tenant_id,
                        "plan": tenant_plan,
                        "count": count,
                        "limit": limit,
                    }
                )

        return result

    async def _check_endpoint_limit(
        self,
        tenant_id: str,
        endpoint: str,
    ) -> RateLimitResult:
        """Vérifie la limite par endpoint"""
        # Trouver la limite pour cet endpoint
        limit = self._config.endpoint_limits.get(endpoint)

        # Si pas de limite spécifique, utiliser une limite par défaut
        if limit is None:
            # Chercher un match partiel
            for ep_pattern, ep_limit in self._config.endpoint_limits.items():
                if endpoint.startswith(ep_pattern.rstrip('*')):
                    limit = ep_limit
                    break

        if limit is None:
            limit = 100  # Défaut généreux

        key = f"ratelimit:endpoint:{tenant_id}:{endpoint}"
        window = 60

        allowed, count = await self._counter.increment(key, window, limit)

        return RateLimitResult(
            allowed=allowed,
            remaining=limit - count,
            limit=limit,
            reset_at=datetime.utcnow() + timedelta(seconds=window),
            tier_exceeded=RateLimitTier.ENDPOINT if not allowed else None,
            retry_after_seconds=self._calculate_retry_after(count, limit, window) if not allowed else 0,
        )

    async def _check_user_limit(
        self,
        tenant_id: str,
        user_id: str,
    ) -> RateLimitResult:
        """Vérifie la limite par utilisateur (optionnel)"""
        key = f"ratelimit:user:{tenant_id}:{user_id}"
        limit = 30  # Limite par utilisateur
        window = 60

        allowed, count = await self._counter.increment(key, window, limit)

        return RateLimitResult(
            allowed=allowed,
            remaining=limit - count,
            limit=limit,
            reset_at=datetime.utcnow() + timedelta(seconds=window),
            tier_exceeded=RateLimitTier.USER if not allowed else None,
        )

    async def _check_burst(self, tenant_id: str, base_limit: int) -> bool:
        """Vérifie si le burst est autorisé (pas déjà utilisé récemment)"""
        burst_key = f"ratelimit:burst:{tenant_id}"
        window = self._config.burst_window_seconds

        count = await self._counter.get_count(burst_key, window)

        # Autoriser le burst si pas utilisé dans la fenêtre
        if count < 1:
            await self._counter.increment(burst_key, window, 1)
            return True

        return False

    def _calculate_retry_after(
        self,
        current_count: int,
        limit: int,
        window_seconds: int,
    ) -> int:
        """Calcule le temps d'attente recommandé"""
        if current_count <= limit:
            return 0

        # Estimer quand des slots seront libérés
        excess = current_count - limit
        rate = limit / window_seconds

        return max(1, int(excess / rate))

    async def get_usage(
        self,
        tenant_id: str,
        tenant_plan: str = "starter",
    ) -> Dict[str, Any]:
        """Retourne l'usage actuel du rate limit"""
        tenant_key = f"ratelimit:tenant:{tenant_id}"
        limit = self._config.tenant_limits.get(tenant_plan, 30)

        count = await self._counter.get_count(tenant_key, 60)

        return {
            "tenant_id": tenant_id,
            "plan": tenant_plan,
            "limit_per_minute": limit,
            "current_usage": count,
            "remaining": max(0, limit - count),
            "usage_percent": round((count / limit) * 100, 1) if limit > 0 else 0,
        }


# =============================================================================
# FASTAPI MIDDLEWARE
# =============================================================================

class RateLimitMiddleware:
    """
    Middleware FastAPI pour le rate limiting.

    Usage:
        app.add_middleware(RateLimitMiddleware, redis_client=redis)
    """

    def __init__(
        self,
        app,
        redis_client=None,
        config: Optional[RateLimitConfig] = None,
    ):
        self.app = app
        self.limiter = AdvancedRateLimiter(redis_client, config)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Extraire les informations de la requête
        client_ip = self._get_client_ip(scope)
        path = scope.get("path", "")

        # Extraire tenant_id depuis les headers ou state
        # (sera défini par le middleware d'authentification)
        tenant_id = None
        tenant_plan = "starter"

        # Dans un vrai cas, on récupérerait ces infos de la requête authentifiée
        # headers = dict(scope.get("headers", []))

        # Vérifier le rate limit
        result = await self.limiter.check(
            client_ip=client_ip,
            tenant_id=tenant_id,
            tenant_plan=tenant_plan,
            endpoint=path,
        )

        if not result.allowed:
            # Retourner 429 Too Many Requests
            await self._send_rate_limit_response(send, result)
            return

        # Continuer avec la requête
        # Ajouter les headers de rate limit à la réponse
        await self.app(scope, receive, send)

    def _get_client_ip(self, scope) -> str:
        """Extrait l'IP client (avec support proxy)"""
        headers = dict(scope.get("headers", []))

        # Check X-Forwarded-For
        forwarded = headers.get(b"x-forwarded-for", b"").decode()
        if forwarded:
            return forwarded.split(",")[0].strip()

        # Check X-Real-IP
        real_ip = headers.get(b"x-real-ip", b"").decode()
        if real_ip:
            return real_ip

        # Fallback to direct connection
        client = scope.get("client")
        if client:
            return client[0]

        return "unknown"

    async def _send_rate_limit_response(self, send, result: RateLimitResult):
        """Envoie une réponse 429"""
        headers = [
            (b"content-type", b"application/json"),
        ]

        for key, value in result.to_headers().items():
            headers.append((key.lower().encode(), str(value).encode()))

        await send({
            "type": "http.response.start",
            "status": 429,
            "headers": headers,
        })

        body = {
            "error": "rate_limit_exceeded",
            "message": f"Rate limit exceeded for {result.tier_exceeded.value if result.tier_exceeded else 'unknown'} tier",
            "retry_after_seconds": result.retry_after_seconds,
        }

        import json
        await send({
            "type": "http.response.body",
            "body": json.dumps(body).encode(),
        })


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # Types
    "RateLimitTier",
    "RateLimitRule",
    "RateLimitConfig",
    "RateLimitResult",

    # Core
    "SlidingWindowCounter",
    "AdvancedRateLimiter",

    # Middleware
    "RateLimitMiddleware",
]

