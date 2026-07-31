"""
Test de bout en bout: signup -> vérification email -> émission de clé API
-> authentification -> usage réel.

Ferme l'écart avec le cahier des charges §4.2.1 ("Connexion de la
boutique via une clé d'accès") - avant ce travail, aucune inscription
self-service ne fonctionnait : /api/v1/tenants était un stub qui ne
touchait jamais la base, et le seul chemin d'authentification qui
fonctionnait était le bypass X-Tenant-ID (dev/test uniquement).
"""

import os
import re

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


@pytest.fixture(autouse=True)
async def _reset_app_engine_pool():
    """
    app.main.create_application() uses the module-level async_engine from
    app.infrastructure.database.connection, created once at import time and
    bound to whatever event loop was current then. pytest-asyncio gives each
    test function its own event loop, so a pooled connection opened by an
    earlier test can outlive its loop and get reused under a different one
    ("attached to a different loop"). Dispose the pool before each test so
    fresh connections are opened under the current test's loop.
    """
    from app.infrastructure.database.connection import async_engine

    await async_engine.dispose()
    yield


@pytest.mark.asyncio
class TestTenantSignupEndToEnd:
    async def test_full_signup_verify_authenticate_flow(self, db_session: AsyncSession):
        from app.core.config.settings import Environment, settings
        from app.infrastructure.email import EmailProviderFactory, MockEmailProvider
        from app.main import create_application

        # Environnement de type production: pas de bypass X-Tenant-ID, tout
        # doit passer par le vrai flow signup -> vérification -> clé API.
        original_env = settings.environment
        settings.environment = Environment.STAGING
        EmailProviderFactory.reset()
        settings.email.provider = "mock"

        app = create_application()
        transport = ASGITransport(app=app)
        signup_email = "e2e-signup@example.com"

        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # 1. Signup
                signup_resp = await client.post(
                    "/api/v1/tenants",
                    json={"name": "E2E Signup Shop", "email": signup_email},
                )
                assert signup_resp.status_code == 201, signup_resp.text
                body = signup_resp.json()
                assert body["status"] == "pending_verification"

                # 2. Un email a réellement été "envoyé" contenant un lien de vérification
                mock_provider: MockEmailProvider = EmailProviderFactory.get_provider()
                assert len(mock_provider.sent_emails) == 1
                sent = mock_provider.sent_emails[0]
                assert sent["to"] == signup_email

                match = re.search(r"/api/v1/tenants/verify/([\w-]+)", sent["html_body"])
                assert match, "verification link not found in email body"
                raw_token = match.group(1)

                # 3. Avant vérification: pas encore de clé API, donc pas d'accès
                unverified_check = await client.get(
                    "/api/v1/tenants/current",
                    headers={"Authorization": "Bearer sk_not_issued_yet"},
                )
                assert unverified_check.status_code == 401

                # 4. Vérification du lien
                verify_resp = await client.get(f"/api/v1/tenants/verify/{raw_token}")
                assert verify_resp.status_code == 200
                key_match = re.search(r"sk_[\w-]+", verify_resp.text)
                assert key_match, "API key not found in verification page"
                raw_api_key = key_match.group(0)

                # 5. La clé émise authentifie réellement une requête
                current_resp = await client.get(
                    "/api/v1/tenants/current",
                    headers={"Authorization": f"Bearer {raw_api_key}"},
                )
                assert current_resp.status_code == 200, current_resp.text
                current = current_resp.json()
                assert current["email"] == signup_email
                assert current["is_verified"] is True
                assert current["plan"] == "starter"

                # 6. Les stats d'usage sont réelles (0, pas des chiffres inventés)
                usage_resp = await client.get(
                    "/api/v1/tenants/current/usage",
                    headers={"Authorization": f"Bearer {raw_api_key}"},
                )
                assert usage_resp.status_code == 200
                assert usage_resp.json()["usage"]["conversations"] == 0

                # 7. Rotation de clé: l'ancienne n'authentifie plus
                rotate_resp = await client.post(
                    "/api/v1/tenants/current/api-keys/rotate",
                    headers={"Authorization": f"Bearer {raw_api_key}"},
                )
                assert rotate_resp.status_code == 200
                new_key = rotate_resp.json()["api_key"]
                assert new_key != raw_api_key

                old_key_check = await client.get(
                    "/api/v1/tenants/current",
                    headers={"Authorization": f"Bearer {raw_api_key}"},
                )
                assert old_key_check.status_code == 401

                new_key_check = await client.get(
                    "/api/v1/tenants/current",
                    headers={"Authorization": f"Bearer {new_key}"},
                )
                assert new_key_check.status_code == 200
        finally:
            settings.environment = original_env
            EmailProviderFactory.reset()
            await db_session.execute(
                text("DELETE FROM tenants WHERE email = :email"), {"email": signup_email}
            )
            await db_session.commit()

    async def test_signup_rejects_duplicate_email(self, db_session: AsyncSession):
        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        email = "e2e-dup@example.com"

        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                first = await client.post(
                    "/api/v1/tenants", json={"name": "Shop One", "email": email}
                )
                assert first.status_code == 201

                second = await client.post(
                    "/api/v1/tenants", json={"name": "Shop Two", "email": email}
                )
                assert second.status_code == 409
        finally:
            await db_session.execute(
                text("DELETE FROM tenants WHERE email = :email"), {"email": email}
            )
            await db_session.commit()
