"""
Tests d'intégration pour TenantRepository contre un vrai PostgreSQL.

Régression: TenantContextMiddleware._validate_and_get_tenant() référençait
tenant.plan, tenant.features_enabled, tenant.rate_limit_rpm et appelait
tenant.is_active() comme une méthode - aucun de ces éléments n'existe sur
le modèle Tenant réel, et le middleware était de toute façon instancié
sans tenant_repository (toujours None). Le chemin d'authentification API
key "production" n'avait donc jamais fonctionné, ni été testé contre une
vraie base. Ces tests exercent le repository réel + le flow complet
signup -> vérification -> émission de clé API contre PostgreSQL.
"""

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.infrastructure.database.repositories.tenant_repo import TenantRepository


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
        await session.rollback()

    await engine.dispose()


@pytest.fixture
def unique_slug():
    return f"test-shop-{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
class TestTenantRepositorySignupFlow:
    async def test_create_pending_tenant_is_unverified_and_inactive(
        self, db_session: AsyncSession, unique_slug
    ):
        repo = TenantRepository(db_session)

        tenant, raw_token = await repo.create_pending(
            name="Ma Boutique", email=f"{unique_slug}@example.com", slug=unique_slug
        )

        assert tenant.is_verified is False
        assert tenant.is_active is False
        assert tenant.api_key_hash is None
        assert tenant.verification_token_hash is not None
        assert raw_token not in (tenant.verification_token_hash or "")

        await db_session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tenant.id})
        await db_session.commit()

    async def test_verification_token_can_be_looked_up_by_hash(
        self, db_session: AsyncSession, unique_slug
    ):
        repo = TenantRepository(db_session)
        tenant, raw_token = await repo.create_pending(
            name="Ma Boutique", email=f"{unique_slug}@example.com", slug=unique_slug
        )
        await db_session.commit()

        import hashlib

        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        found = await repo.get_by_verification_token_hash(token_hash)

        assert found is not None
        assert found.id == tenant.id

        await db_session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tenant.id})
        await db_session.commit()

    async def test_activate_issues_api_key_and_activates_tenant(
        self, db_session: AsyncSession, unique_slug
    ):
        repo = TenantRepository(db_session)
        tenant, _ = await repo.create_pending(
            name="Ma Boutique", email=f"{unique_slug}@example.com", slug=unique_slug
        )
        await db_session.commit()

        raw_api_key = await repo.activate_and_issue_api_key(tenant)
        await db_session.commit()

        assert raw_api_key.startswith("sk_")
        assert tenant.is_verified is True
        assert tenant.is_active is True
        assert tenant.api_key_hash is not None
        assert tenant.verification_token_hash is None

        # La clé émise doit permettre de retrouver le tenant par son hash,
        # exactement comme le fait TenantContextMiddleware._validate_and_get_tenant.
        import hashlib

        key_hash = hashlib.sha256(raw_api_key.encode()).hexdigest()
        found = await repo.get_by_api_key_hash(key_hash)
        assert found is not None
        assert found.id == tenant.id

        await db_session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tenant.id})
        await db_session.commit()

    async def test_rotate_api_key_invalidates_old_key(self, db_session: AsyncSession, unique_slug):
        repo = TenantRepository(db_session)
        tenant, _ = await repo.create_pending(
            name="Ma Boutique", email=f"{unique_slug}@example.com", slug=unique_slug
        )
        old_key = await repo.activate_and_issue_api_key(tenant)
        await db_session.commit()

        new_key = await repo.rotate_api_key(tenant)
        await db_session.commit()

        assert new_key != old_key

        import hashlib

        old_hash = hashlib.sha256(old_key.encode()).hexdigest()
        new_hash = hashlib.sha256(new_key.encode()).hexdigest()

        assert await repo.get_by_api_key_hash(old_hash) is None
        assert (await repo.get_by_api_key_hash(new_hash)).id == tenant.id

        await db_session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": tenant.id})
        await db_session.commit()
