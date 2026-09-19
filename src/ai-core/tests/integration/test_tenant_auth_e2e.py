"""
Test de bout en bout: la clé API réelle (production/staging) fonctionne.

Régression #1: TenantContextMiddleware n'était jamais instancié avec un
tenant_repository (toujours None), et même s'il l'avait été, le code
référençait des champs inexistants sur le modèle Tenant réel. Le chemin
d'authentification "production" par clé API n'avait donc JAMAIS
fonctionné - seul le bypass X-Tenant-ID (dev/test) fonctionnait.

Régression #2: la clé API seule authentifiait indéfiniment - une clé qui
fuite pouvait être rejouée par quiconque l'intercepte, sans expiration ni
preuve de possession du secret. Chaque requête doit maintenant être signée
avec le secret HMAC du tenant (voir app/core/security/api_key_security.py).

Ce test force ENVIRONMENT=staging (donc pas de bypass dev) et prouve
qu'une clé + secret réellement émis via
TenantRepository.activate_and_issue_api_key() authentifient une requête
HTTP réelle de bout en bout - et que la clé seule, sans signature, ne
suffit plus.
"""

import os
import time

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security.api_key_security import APIClientSigner


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
    async def test_real_api_key_requires_valid_signature_outside_dev_bypass(
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
        raw_api_key, raw_secret = await repo.activate_and_issue_api_key(tenant)
        await db_session.commit()

        # Force le chemin de prod: sans ça, le bypass X-Tenant-ID (activé par
        # défaut dans conftest.py pour le reste de la suite) s'appliquerait
        # et ce test ne prouverait rien.
        monkeypatch.setattr(settings, "environment", Environment.STAGING)
        monkeypatch.setattr(settings.security, "allow_dev_tenant_header", False)
        assert not settings.is_development and not settings.is_test
        assert not settings.security.allow_dev_tenant_header

        app = create_application()
        transport = ASGITransport(app=app)
        signer = APIClientSigner(raw_api_key, raw_secret)

        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                path = "/api/v1/tenants/current"

                # Clé + signature valides -> authentifié.
                headers = signer.sign_request("GET", path)
                response = await client.get(path, headers=headers)
                assert response.status_code != 401, response.text

                # Aucune credential -> rejeté.
                unauthenticated = await client.get(path)
                assert unauthenticated.status_code == 401

                # Clé bidon -> rejetée avant même de vérifier une signature.
                wrong_key = await client.get(
                    path, headers={"Authorization": "Bearer sk_totally_made_up_key"}
                )
                assert wrong_key.status_code == 401

                # Régression #2: la clé réelle SEULE (comme avant ce système,
                # sans X-Timestamp/X-Signature) ne doit plus suffire - c'est
                # exactement le "leaked static bearer key works forever" que
                # ce système existe pour fermer.
                bearer_only = await client.get(
                    path, headers={"Authorization": f"Bearer {raw_api_key}"}
                )
                assert bearer_only.status_code == 401, bearer_only.text

                # Signature calculée avec le mauvais secret -> rejetée.
                forged = APIClientSigner(raw_api_key, "wrong-secret-entirely").sign_request(
                    "GET", path
                )
                tampered = await client.get(path, headers=forged)
                assert tampered.status_code == 401

                # Timestamp hors fenêtre (>5 min) -> rejeté (anti-replay).
                stale_headers = dict(headers)
                stale_timestamp = int(time.time()) - 3600
                stale_headers["X-Timestamp"] = str(stale_timestamp)
                stale_headers["X-Signature"] = signer._validator.create_signature(
                    raw_secret, stale_timestamp, "GET", path
                )
                stale = await client.get(path, headers=stale_headers)
                assert stale.status_code == 401
        finally:
            await db_session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tenant.id})
            await db_session.commit()
