"""
Test de bout en bout: la clé API réelle (production/staging) fonctionne.

Régression: TenantContextMiddleware n'était jamais instancié avec un
tenant_repository (toujours None), et même s'il l'avait été, le code
référençait des champs inexistants sur le modèle Tenant réel. Le chemin
d'authentification "production" par clé API n'avait donc JAMAIS
fonctionné - seul le bypass X-Tenant-ID (dev/test) fonctionnait. Ce test
force ENVIRONMENT=staging (donc pas de bypass) et prouve qu'une clé API
réellement émise via TenantRepository.activate_and_issue_api_key()
authentifie une requête HTTP réelle de bout en bout.
"""

import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _build_db_url() -> str:
    host = os.environ.get("DB_HOST", "localhost")
    port = os.environ.get("DB_PORT", "5432")
    name = os.environ.get("DB_NAME", "saas_ecommerce")
    user = os.environ.get("DB_USER", "saas_user")
    password = os.environ.get("DB_PASSWORD", "test_password_123")
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{name}"


async def _postgres_available(url: str) -> bool:
    try:
        engine = create_async_engine(url, pool_pre_ping=True)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()
        return True
    except Exception:
        return False


@pytest.fixture
async def db_session():
    url = _build_db_url()
    if not await _postgres_available(url):
        pytest.skip("PostgreSQL indisponible - test d'intégration ignoré")

    engine = create_async_engine(url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.mark.asyncio
class TestRealApiKeyAuthenticatesRequest:
    async def test_real_api_key_authenticates_outside_dev_bypass(
        self, db_session: AsyncSession, monkeypatch
    ):
        from app.core.config.settings import Environment, settings
        from app.infrastructure.database.repositories.tenant_repo import TenantRepository
        from app.main import create_application

        repo = TenantRepository(db_session)
        tenant, _ = await repo.create_pending(
            name="Real Auth Shop",
            email="real-auth-e2e@example.com",
            slug="real-auth-e2e-shop",
        )
        raw_api_key = await repo.activate_and_issue_api_key(tenant)
        await db_session.commit()

        # Force le chemin de prod: sans ça, is_development/is_test
        # activeraient le bypass X-Tenant-ID et ce test ne prouverait rien.
        monkeypatch.setattr(settings, "environment", Environment.STAGING)
        assert not settings.is_development and not settings.is_test

        app = create_application()
        transport = ASGITransport(app=app)

        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get(
                    "/api/v1/tenants/current",
                    headers={"Authorization": f"Bearer {raw_api_key}"},
                )
                assert response.status_code != 401, response.text

                unauthenticated = await client.get("/api/v1/tenants/current")
                assert unauthenticated.status_code == 401

                wrong_key = await client.get(
                    "/api/v1/tenants/current",
                    headers={"Authorization": "Bearer sk_totally_made_up_key"},
                )
                assert wrong_key.status_code == 401
        finally:
            await db_session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tenant.id})
            await db_session.commit()
