"""
Shared fixtures for tests/integration/.

app.main.create_application() uses the module-level async_engine from
app.infrastructure.database.connection, created once at import time and
bound to whatever event loop was current then. pytest-asyncio gives each
test function its own event loop, so a pooled connection opened by an
earlier test can outlive its loop and get reused under a different one
("attached to a different loop"). Dispose the pool before every
integration test so fresh connections are opened under the current
test's loop - this must be here (not duplicated per-file) so it applies
consistently regardless of which integration test happens to run first.

Same problem, same fix, for the lazy Redis client behind
AdminAISafetySystem (app/core/cache/redis_client.py).
"""

import pytest


@pytest.fixture(autouse=True)
async def _reset_app_engine_pool():
    from app.infrastructure.database.connection import async_engine

    await async_engine.dispose()
    yield


@pytest.fixture(autouse=True)
async def _reset_redis_cache_client():
    from app.core.cache.redis_client import reset_redis_cache_client

    await reset_redis_cache_client()
    yield
