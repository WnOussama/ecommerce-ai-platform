"""
Client Redis partagé (cache_db) - lazy comme le client du rate limiter
dans main.py (redis.asyncio ne se connecte qu'à la première commande),
donc sûr à créer même si Redis n'est pas encore joignable au démarrage.

Utilisé pour l'état persistant de AdminAISafetySystem (voir
app/core/security/admin_safety.py) - sans ça, les pending actions
vivaient dans un dict Python et disparaissaient à chaque redémarrage ou
étaient invisibles aux autres workers.
"""

from typing import Optional

import redis.asyncio as redis_asyncio

from app.core.config.settings import settings

_client: Optional[redis_asyncio.Redis] = None


def get_redis_cache_client() -> redis_asyncio.Redis:
    global _client
    if _client is None:
        _client = redis_asyncio.from_url(
            settings.redis.url,
            db=settings.redis.cache_db,
            socket_timeout=settings.redis.socket_timeout,
            max_connections=settings.redis.max_connections,
        )
    return _client


async def reset_redis_cache_client() -> None:
    """
    Ferme et oublie le client courant, pour repartir avec une connexion
    fraîche - même besoin que async_engine.dispose() dans
    tests/integration/conftest.py: chaque test pytest-asyncio a sa propre
    event loop, et une connexion pooled ouverte sous une loop précédente
    plante avec "Event loop is closed" si elle est réutilisée.
    """
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            # The old connection may belong to an event loop that's already
            # closed (e.g. between pytest-asyncio test functions) - nothing
            # to gracefully clean up in that case, just drop the reference.
            pass
        _client = None
