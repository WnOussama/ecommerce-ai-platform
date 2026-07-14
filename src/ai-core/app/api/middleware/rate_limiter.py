"""
Middleware Rate Limiter - Protection contre les abus
Utilise Redis pour le comptage distribué
"""

import logging
import time
from typing import Callable, Optional

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config.settings import settings

logger = logging.getLogger(__name__)


class RateLimiterMiddleware(BaseHTTPMiddleware):
    """
    Middleware de rate limiting avec:
    - Limites par tenant
    - Limites par IP pour les routes publiques
    - Sliding window algorithm
    - Headers X-RateLimit-*
    """

    # Routes exemptées du rate limiting
    EXEMPT_PATHS = ["/health", "/metrics", "/docs", "/redoc", "/openapi.json"]

    def __init__(self, app, redis_client=None):
        super().__init__(app)
        self.redis = redis_client
        self.default_limit = settings.security.rate_limit_requests
        self.window_seconds = settings.security.rate_limit_window_seconds

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip pour les routes exemptées
        if any(request.url.path.startswith(path) for path in self.EXEMPT_PATHS):
            return await call_next(request)

        # Identifier le client (tenant_id ou IP)
        identifier = self._get_identifier(request)

        if not identifier:
            return await call_next(request)

        # Vérifier la limite
        limit = self._get_limit_for_request(request)
        allowed, remaining, reset_at = await self._check_rate_limit(identifier, limit)

        if not allowed:
            logger.warning(f"Rate limit exceeded for {identifier}")
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "error": "rate_limit_exceeded",
                    "message": "Too many requests. Please try again later.",
                    "retry_after": reset_at - int(time.time()),
                },
                headers={
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_at),
                    "Retry-After": str(reset_at - int(time.time())),
                },
            )

        # Exécuter la requête
        response = await call_next(request)

        # Ajouter les headers de rate limit
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_at)

        return response

    def _get_identifier(self, request: Request) -> Optional[str]:
        """Extrait l'identifiant pour le rate limiting"""
        # Priorité au tenant_id si disponible
        tenant_id = getattr(request.state, "tenant_id", None)
        if tenant_id:
            return f"tenant:{tenant_id}"

        # Fallback sur l'IP
        client_ip = self._get_client_ip(request)
        if client_ip:
            return f"ip:{client_ip}"

        return None

    def _get_client_ip(self, request: Request) -> Optional[str]:
        """Extrait l'IP client en tenant compte des proxies"""
        # X-Forwarded-For (derrière un proxy/load balancer)
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()

        # X-Real-IP (nginx)
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip

        # IP directe
        if request.client:
            return request.client.host

        return None

    def _get_limit_for_request(self, request: Request) -> int:
        """Détermine la limite pour cette requête"""
        # Limite spécifique au tenant si disponible
        tenant_rate_limit = getattr(request.state, "tenant_rate_limit", None)
        if tenant_rate_limit:
            return tenant_rate_limit

        # Limites différentes selon le type de route
        path = request.url.path

        if "/admin/" in path:
            return self.default_limit * 2  # Plus permissif pour admin
        elif "/chat/" in path:
            return self.default_limit  # Standard pour chat
        elif "/analytics/" in path:
            return self.default_limit // 2  # Plus restrictif pour analytics

        return self.default_limit

    async def _check_rate_limit(self, identifier: str, limit: int) -> tuple[bool, int, int]:
        """
        Vérifie et incrémente le compteur de rate limit.
        Retourne (allowed, remaining, reset_timestamp)
        """
        if not self.redis:
            # Pas de Redis = pas de rate limiting (dev mode)
            return True, limit, int(time.time()) + self.window_seconds

        now = int(time.time())
        window_start = now - self.window_seconds
        key = f"ratelimit:{identifier}"

        # Sliding window avec sorted set Redis
        pipe = self.redis.pipeline()

        # Supprimer les entrées expirées
        pipe.zremrangebyscore(key, 0, window_start)

        # Compter les requêtes dans la fenêtre
        pipe.zcard(key)

        # Ajouter la requête actuelle
        pipe.zadd(key, {str(now): now})

        # Définir l'expiration
        pipe.expire(key, self.window_seconds)

        results = await pipe.execute()
        request_count = results[1]

        remaining = max(0, limit - request_count - 1)
        reset_at = now + self.window_seconds

        if request_count >= limit:
            # Retirer la requête qu'on vient d'ajouter
            await self.redis.zrem(key, str(now))
            return False, 0, reset_at

        return True, remaining, reset_at


class RedisRateLimiter:
    """
    Rate limiter standalone pour usage dans les services.
    Plus flexible que le middleware.
    """

    def __init__(self, redis_client, default_limit: int = 60, window_seconds: int = 60):
        self.redis = redis_client
        self.default_limit = default_limit
        self.window_seconds = window_seconds

    async def check(self, identifier: str, action: str, limit: Optional[int] = None) -> bool:
        """
        Vérifie si l'action est autorisée.

        Args:
            identifier: ID unique (tenant_id, user_id, etc.)
            action: Type d'action (llm_request, embedding_request, etc.)
            limit: Limite spécifique (utilise default si None)

        Returns:
            True si autorisé, False sinon
        """
        limit = limit or self.default_limit
        key = f"ratelimit:{identifier}:{action}"

        now = int(time.time())
        window_start = now - self.window_seconds

        pipe = self.redis.pipeline()
        pipe.zremrangebyscore(key, 0, window_start)
        pipe.zcard(key)
        pipe.zadd(key, {str(now): now})
        pipe.expire(key, self.window_seconds)

        results = await pipe.execute()
        count = results[1]

        if count >= limit:
            await self.redis.zrem(key, str(now))
            return False

        return True

    async def get_remaining(self, identifier: str, action: str) -> int:
        """Retourne le nombre de requêtes restantes"""
        key = f"ratelimit:{identifier}:{action}"

        now = int(time.time())
        window_start = now - self.window_seconds

        await self.redis.zremrangebyscore(key, 0, window_start)
        count = await self.redis.zcard(key)

        return max(0, self.default_limit - count)
