"""
Test de bout en bout: un administrateur provisionne un tenant (clé API +
secret de signature) -> authentification signée -> usage réel -> rotation.

L'inscription en libre-service par email a été retirée : aucune route
publique ne peut créer un tenant ni émettre une clé API. Les tenants sont
créés côté serveur (repository / scripts/seed_demo_data.py).

Chaque requête authentifiée par clé API doit être signée (voir
app/core/security/api_key_security.py) - la clé seule ne suffit pas.
"""

import os

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
class TestTenantProvisioningEndToEnd:
    async def test_provisioned_tenant_authenticates_and_rotates(
        self, db_session: AsyncSession, monkeypatch
    ):
        from app.core.config.settings import Environment, settings
        from app.infrastructure.database.repositories.tenant_repo import TenantRepository
        from app.main import create_application

        # Environnement de type production: pas de bypass X-Tenant-ID.
        # monkeypatch restaure toujours les réglages, même si le test échoue
        # avant son bloc finally (sinon les tests suivants héritent d'un
        # environnement "staging" sans bypass).
        monkeypatch.setattr(settings, "environment", Environment.STAGING)
        monkeypatch.setattr(settings.security, "allow_dev_tenant_header", False)

        email = "e2e-provisioned@example.com"
        repo = TenantRepository(db_session)
        tenant, _ = await repo.create_pending(
            name="E2E Provisioned Shop", email=email, slug="e2e-provisioned-shop"
        )
        raw_api_key, raw_secret = await repo.activate_and_issue_api_key(tenant)
        await db_session.commit()

        app = create_application()
        transport = ASGITransport(app=app)
        signer = APIClientSigner(raw_api_key, raw_secret)

        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # 1. Une clé inconnue n'ouvre rien
                unknown = await client.get(
                    "/api/v1/tenants/current",
                    headers={"Authorization": "Bearer sk_not_issued"},
                )
                assert unknown.status_code == 401

                # 2. La clé + la signature émises authentifient réellement une requête
                current_resp = await client.get(
                    "/api/v1/tenants/current",
                    headers=signer.sign_request("GET", "/api/v1/tenants/current"),
                )
                assert current_resp.status_code == 200, current_resp.text
                current = current_resp.json()
                assert current["email"] == email
                assert current["is_verified"] is True
                assert current["plan"] == "starter"

                # 3. Les stats d'usage sont réelles (0, pas des chiffres inventés)
                usage_resp = await client.get(
                    "/api/v1/tenants/current/usage",
                    headers=signer.sign_request("GET", "/api/v1/tenants/current/usage"),
                )
                assert usage_resp.status_code == 200
                assert usage_resp.json()["usage"]["conversations"] == 0

                # 4. Rotation de clé: l'ancienne n'authentifie plus
                rotate_path = "/api/v1/tenants/current/api-keys/rotate"
                rotate_resp = await client.post(
                    rotate_path, headers=signer.sign_request("POST", rotate_path)
                )
                assert rotate_resp.status_code == 200
                rotated = rotate_resp.json()
                assert rotated["api_key"] != raw_api_key
                assert rotated["hmac_secret"] != raw_secret

                old_key_check = await client.get(
                    "/api/v1/tenants/current",
                    headers=signer.sign_request("GET", "/api/v1/tenants/current"),
                )
                assert old_key_check.status_code == 401

                new_signer = APIClientSigner(rotated["api_key"], rotated["hmac_secret"])
                new_key_check = await client.get(
                    "/api/v1/tenants/current",
                    headers=new_signer.sign_request("GET", "/api/v1/tenants/current"),
                )
                assert new_key_check.status_code == 200
        finally:
            await db_session.execute(
                text("DELETE FROM tenants WHERE email = :email"), {"email": email}
            )
            await db_session.commit()

    async def test_public_signup_and_verify_routes_are_closed(
        self, db_session: AsyncSession, monkeypatch
    ):
        """Nobody can create a tenant or receive an API key without authenticating."""
        from app.core.config.settings import Environment, settings
        from app.main import create_application

        monkeypatch.setattr(settings, "environment", Environment.STAGING)
        monkeypatch.setattr(settings.security, "allow_dev_tenant_header", False)

        app = create_application()
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            signup = await client.post(
                "/api/v1/tenants", json={"name": "Intruder Shop", "email": "x@example.com"}
            )
            assert signup.status_code == 401

            verify = await client.get("/api/v1/tenants/verify/any-token")
            assert verify.status_code == 401
