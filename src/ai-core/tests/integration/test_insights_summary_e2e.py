"""
Test de bout en bout: GET /api/v1/insights/summary expose directement
InsightsService.summary() - la même donnée réelle que le get_analytics
de l'agent admin, mais sans passer par le workflow de confirmation
(lecture seule, aucun effet de bord à protéger).
"""

import json
import os
import uuid
from datetime import datetime

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


@pytest.fixture
async def tenant_id(db_session: AsyncSession):
    new_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO tenants (id, name, slug, is_active, is_verified, settings, created_at, updated_at) "
            "VALUES (:id, :name, :slug, true, true, '{}', :now, :now)"
        ),
        {
            "id": new_id,
            "name": f"Insights Summary Test Tenant {new_id.hex[:8]}",
            "slug": f"insights-summary-test-{new_id.hex[:8]}",
            "now": datetime.utcnow(),
        },
    )
    await db_session.commit()

    yield new_id

    await db_session.execute(text("DELETE FROM messages WHERE tenant_id = :id"), {"id": new_id})
    await db_session.execute(
        text("DELETE FROM conversations WHERE tenant_id = :id"), {"id": new_id}
    )
    await db_session.execute(text("DELETE FROM products WHERE tenant_id = :id"), {"id": new_id})
    await db_session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": new_id})
    await db_session.commit()


@pytest.mark.asyncio
class TestInsightsSummaryEndToEnd:
    async def test_summary_reflects_real_seeded_data(self, tenant_id, db_session: AsyncSession):
        conv_id = uuid.uuid4()
        await db_session.execute(
            text(
                "INSERT INTO conversations (id, tenant_id, user_identifier, status, extra_data, created_at, updated_at) "
                "VALUES (:id, :tid, 'summary_test_user', 'ACTIVE', '{}', :now, :now)"
            ),
            {"id": conv_id, "tid": tenant_id, "now": datetime.utcnow()},
        )
        await db_session.execute(
            text(
                "INSERT INTO products (id, tenant_id, external_id, name, price, quantity, active, "
                "available_for_order, created_at, updated_at) "
                "VALUES (:id, :tid, 'p1', 'Produit Rare', 10.0, 2, true, true, :now, :now)"
            ),
            {"id": uuid.uuid4(), "tid": tenant_id, "now": datetime.utcnow()},
        )
        now = datetime.utcnow()
        for _ in range(3):
            await db_session.execute(
                text(
                    "INSERT INTO messages (id, tenant_id, conversation_id, idempotency_key, role, "
                    "content, extra_data, created_at, updated_at) "
                    "VALUES (:id, :tid, :cid, :ik, 'ASSISTANT', 'reply', CAST(:extra AS JSONB), :now, :now)"
                ),
                {
                    "id": uuid.uuid4(),
                    "tid": tenant_id,
                    "cid": conv_id,
                    "ik": uuid.uuid4(),
                    "extra": json.dumps(
                        {
                            "intent": "product_search",
                            "rag_used": True,
                            "products_found": 1,
                            "product_ids": ["p1"],
                        }
                    ),
                    "now": now,
                },
            )
        await db_session.commit()

        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/insights/summary",
                params={"time_range": "last_7_days"},
                headers=headers,
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["intent_distribution"] == {"product_search": 3}
        assert len(body["most_requested_products"]) == 1
        assert body["most_requested_products"][0]["external_id"] == "p1"
        assert body["most_requested_products"][0]["request_count"] == 3
        assert len(body["low_stock_products"]) == 1
        assert body["low_stock_products"][0]["quantity"] == 2

    async def test_empty_tenant_returns_real_zeros_not_fabricated_data(self, tenant_id):
        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/insights/summary",
                params={"time_range": "last_7_days"},
                headers=headers,
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["most_requested_products"] == []
        assert body["unmet_demand"] == []
        assert body["intent_distribution"] == {}
        assert body["low_stock_products"] == []
