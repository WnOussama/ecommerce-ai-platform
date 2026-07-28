"""
Tests d'intégration pour ProductRepository contre un vrai PostgreSQL.

Régression: upsert_product / upsert_products_bulk utilisaient
sqlalchemy.dialects.mysql.insert (INSERT ... ON DUPLICATE KEY UPDATE),
une syntaxe MySQL, alors que la seule base de données réellement utilisée
par le projet (docker-compose, k8s, CI) est PostgreSQL. Le module
ProductRepository n'avait aucune couverture de test qui exécute du SQL
réel (les tests existants utilisent InMemoryProductRepository), donc
l'incompatibilité de dialecte n'était jamais détectée.

Ces tests s'exécutent contre une vraie instance PostgreSQL (variables
DB_* d'environnement, comme le job d'intégration CI) et sont skip si
aucune base n'est joignable.
"""

import os
import uuid
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services.catalog.repository import ProductData, ProductRepository


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


@pytest.fixture
async def tenant_id(db_session: AsyncSession):
    """Crée un tenant réel (contrainte FK products.tenant_id -> tenants.id)."""
    new_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO tenants (id, name, slug, is_active, settings, created_at, updated_at) "
            "VALUES (:id, :name, :slug, true, '{}', :now, :now)"
        ),
        {
            "id": new_id,
            "name": f"Test Tenant {new_id.hex[:8]}",
            "slug": f"test-tenant-{new_id.hex[:8]}",
            "now": datetime.utcnow(),
        },
    )
    await db_session.commit()

    yield new_id

    await db_session.execute(text("DELETE FROM products WHERE tenant_id = :id"), {"id": new_id})
    await db_session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": new_id})
    await db_session.commit()


def _make_product(external_id: int, name: str = "Produit Test") -> ProductData:
    return ProductData(
        external_id=external_id,
        name=name,
        price=Decimal("29.99"),
        reference=f"REF-{external_id}",
    )


@pytest.mark.asyncio
class TestProductRepositoryUpsertPostgres:
    async def test_upsert_product_insert_then_update(self, db_session, tenant_id):
        repo = ProductRepository(db_session)
        product_id = 12345

        created, updated = await repo.upsert_product(str(tenant_id), _make_product(product_id))
        assert (created, updated) == (True, False)

        created, updated = await repo.upsert_product(
            str(tenant_id), _make_product(product_id, name="Produit Test Modifié")
        )
        assert (created, updated) == (False, True)

        fetched = await repo.get_product_by_external_id(str(tenant_id), product_id)
        assert fetched is not None
        assert fetched.name == "Produit Test Modifié"

    async def test_upsert_products_bulk_insert_then_update(self, db_session, tenant_id):
        repo = ProductRepository(db_session)
        products = [_make_product(1001), _make_product(1002), _make_product(1003)]

        created_count, updated_count = await repo.upsert_products_bulk(str(tenant_id), products)
        assert (created_count, updated_count) == (3, 0)

        # Ré-upsert: 1001/1002 existent déjà, 1004 est nouveau
        second_batch = [
            _make_product(1001, name="Modifié"),
            _make_product(1002, name="Modifié"),
            _make_product(1004),
        ]
        created_count, updated_count = await repo.upsert_products_bulk(str(tenant_id), second_batch)
        assert (created_count, updated_count) == (1, 2)
