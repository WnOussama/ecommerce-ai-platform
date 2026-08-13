"""
Test de bout en bout: /api/v1/recommendations/* renvoie de vraies données
au lieu d'une réponse vide/placeholder figée.

/similar/{product_id} et /trending patchent get_retrieval_service /
utilisent directement des données Postgres réelles - le calcul de
similarité vectorielle lui-même (embeddings, ChromaDB) est déjà couvert
par tests/unit/test_retrieval_service.py; ce test vérifie le câblage
HTTP -> repository/retrieval réel, pas les maths de similarité.
"""

import os
import uuid
from datetime import datetime
from unittest.mock import patch

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
            "name": f"Reco Test Tenant {new_id.hex[:8]}",
            "slug": f"reco-test-{new_id.hex[:8]}",
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


async def _insert_product(db_session, tenant_id, external_id, name, price) -> None:
    await db_session.execute(
        text(
            "INSERT INTO products (id, tenant_id, external_id, name, price, quantity, active, "
            "available_for_order, created_at, updated_at) "
            "VALUES (:id, :tenant_id, :external_id, :name, :price, 10, true, true, :now, :now)"
        ),
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "external_id": external_id,
            "name": name,
            "price": price,
            "now": datetime.utcnow(),
        },
    )


class _FakeRetrievalService:
    """Stands in for get_retrieval_service() - the vector-similarity math
    itself is covered by tests/unit/test_retrieval_service.py."""

    def __init__(self, products):
        self._products = products

    async def search_products(self, query, tenant_id, top_k=None, **kwargs):
        from app.services.rag.retrieval_service import RetrievalResult

        return RetrievalResult(
            query=query,
            tenant_id=tenant_id,
            products=self._products,
            total_found=len(self._products),
        )


@pytest.mark.asyncio
class TestRecommendationsEndToEnd:
    async def test_similar_products_excludes_the_source_product(
        self, tenant_id, db_session: AsyncSession
    ):
        from app.services.rag.retrieval_service import RetrievedProduct

        await _insert_product(db_session, tenant_id, "101", "Chaussure A", 89.99)
        await _insert_product(db_session, tenant_id, "102", "Chaussure B", 79.99)
        await db_session.commit()

        fake_service = _FakeRetrievalService(
            [
                RetrievedProduct(
                    product_id=101, name="Chaussure A", price=89.99, similarity_score=1.0
                ),
                RetrievedProduct(
                    product_id=102, name="Chaussure B", price=79.99, similarity_score=0.8
                ),
            ]
        )

        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        with patch(
            "app.api.v1.endpoints.recommendations.get_retrieval_service",
            return_value=fake_service,
        ):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/recommendations/similar/101", headers=headers)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["strategy"] == "content_based"
        product_ids = [r["product_id"] for r in body["recommendations"]]
        assert "101" not in product_ids
        assert "102" in product_ids

    async def test_similar_products_404_for_unknown_product(self, tenant_id):
        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/recommendations/similar/999999", headers=headers)

        assert resp.status_code == 404

    async def test_trending_reflects_real_chat_demand_counts(
        self, tenant_id, db_session: AsyncSession
    ):
        import json

        conv_id = uuid.uuid4()
        await db_session.execute(
            text(
                "INSERT INTO conversations (id, tenant_id, user_identifier, status, extra_data, created_at, updated_at) "
                "VALUES (:id, :tid, 'trending_test_user', 'ACTIVE', '{}', :now, :now)"
            ),
            {"id": conv_id, "tid": tenant_id, "now": datetime.utcnow()},
        )
        await _insert_product(db_session, tenant_id, "201", "Produit Star", 19.99)

        now = datetime.utcnow()
        for _ in range(5):
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
                        {"rag_used": True, "products_found": 1, "product_ids": ["201"]}
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
            resp = await client.get("/api/v1/recommendations/trending?days=1", headers=headers)

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["strategy"] == "popularity"
        assert len(body["recommendations"]) == 1
        top = body["recommendations"][0]
        assert top["product_id"] == "201"
        assert top["name"] == "Produit Star"
        assert "5 demande" in top["reason"]
