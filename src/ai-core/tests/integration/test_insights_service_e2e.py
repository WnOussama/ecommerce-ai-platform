"""
Test de bout en bout: InsightsService calcule des chiffres réels et
exacts à partir de messages/conversations/products/coupons connus.

Remplace la vérification "envoie 4 messages => l'endpoint doit dire 4"
déjà utilisée pour /analytics/* (voir cceb581) - même approche pour les
handlers admin qui renvoyaient auparavant des chiffres inventés
(total_revenue: 15420.50, "Product A"...).
"""

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
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
            "name": f"Insights Test Tenant {new_id.hex[:8]}",
            "slug": f"insights-test-{new_id.hex[:8]}",
            "now": datetime.utcnow(),
        },
    )
    await db_session.commit()

    yield new_id

    await db_session.execute(text("DELETE FROM coupons WHERE tenant_id = :id"), {"id": new_id})
    await db_session.execute(text("DELETE FROM messages WHERE tenant_id = :id"), {"id": new_id})
    await db_session.execute(
        text("DELETE FROM conversations WHERE tenant_id = :id"), {"id": new_id}
    )
    await db_session.execute(text("DELETE FROM products WHERE tenant_id = :id"), {"id": new_id})
    await db_session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": new_id})
    await db_session.commit()


@pytest.fixture
async def conversation_id(db_session: AsyncSession, tenant_id):
    conv_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO conversations (id, tenant_id, user_identifier, status, extra_data, created_at, updated_at) "
            "VALUES (:id, :tenant_id, :user_identifier, 'ACTIVE', '{}', :now, :now)"
        ),
        {
            "id": conv_id,
            "tenant_id": tenant_id,
            "user_identifier": "insights_test_user",
            "now": datetime.utcnow(),
        },
    )
    await db_session.commit()
    return conv_id


async def _insert_message(
    db_session: AsyncSession,
    tenant_id,
    conversation_id,
    role: str,
    content: str,
    extra_data: dict,
    created_at,
) -> None:
    import json

    await db_session.execute(
        text(
            "INSERT INTO messages (id, tenant_id, conversation_id, idempotency_key, role, content, "
            "extra_data, created_at, updated_at) "
            "VALUES (:id, :tenant_id, :conversation_id, :idempotency_key, :role, :content, "
            "CAST(:extra_data AS JSONB), :created_at, :created_at)"
        ),
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "conversation_id": conversation_id,
            "idempotency_key": uuid.uuid4(),
            "role": role,
            "content": content,
            "extra_data": json.dumps(extra_data),
            "created_at": created_at,
        },
    )


async def _insert_product(
    db_session: AsyncSession, tenant_id, external_id: str, name: str, price: float, quantity: int
) -> None:
    await db_session.execute(
        text(
            "INSERT INTO products (id, tenant_id, external_id, name, price, quantity, active, "
            "available_for_order, created_at, updated_at) "
            "VALUES (:id, :tenant_id, :external_id, :name, :price, :quantity, true, true, :now, :now)"
        ),
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "external_id": external_id,
            "name": name,
            "price": price,
            "quantity": quantity,
            "now": datetime.utcnow(),
        },
    )


async def _insert_coupon(db_session: AsyncSession, tenant_id, status: str, rule_id=None) -> None:
    await db_session.execute(
        text(
            "INSERT INTO coupons (id, tenant_id, code, status, discount_percent, rule_id, "
            "extra_data, expires_at, created_at, updated_at) "
            "VALUES (:id, :tenant_id, :code, :status, 10, :rule_id, '{}', :expires_at, :now, :now)"
        ),
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "code": f"INS-{uuid.uuid4().hex[:10].upper()}",
            "status": status,
            "rule_id": rule_id,
            "expires_at": datetime.utcnow() + timedelta(days=7),
            "now": datetime.utcnow(),
        },
    )


@pytest.mark.asyncio
class TestInsightsServiceEndToEnd:
    async def test_most_requested_products_counts_and_joins_catalog(
        self, tenant_id, conversation_id, db_session: AsyncSession
    ):
        from app.services.insights.service import InsightsService

        await _insert_product(db_session, tenant_id, "ext-1", "Chaussure A", 89.99, 10)
        await _insert_product(db_session, tenant_id, "ext-2", "Chaussure B", 49.99, 3)
        await db_session.commit()

        now = datetime.utcnow()
        # ext-1 requested 3 times, ext-2 requested 1 time
        for i in range(3):
            await _insert_message(
                db_session,
                tenant_id,
                conversation_id,
                "ASSISTANT",
                f"reply {i}",
                {"rag_used": True, "products_found": 1, "product_ids": ["ext-1"]},
                now,
            )
        await _insert_message(
            db_session,
            tenant_id,
            conversation_id,
            "ASSISTANT",
            "reply mixed",
            {"rag_used": True, "products_found": 2, "product_ids": ["ext-1", "ext-2"]},
            now,
        )
        await db_session.commit()

        service = InsightsService(db_session, tenant_id)
        results = await service.most_requested_products(since=now - timedelta(hours=1))

        by_id = {r.external_id: r for r in results}
        assert by_id["ext-1"].request_count == 4
        assert by_id["ext-1"].name == "Chaussure A"
        assert by_id["ext-2"].request_count == 1

    async def test_unmet_demand_counts_zero_result_searches(
        self, tenant_id, conversation_id, db_session: AsyncSession
    ):
        from app.services.insights.service import InsightsService

        base = datetime.utcnow()
        # Every request logs a USER message immediately followed by its
        # ASSISTANT reply - use strictly increasing timestamps to reflect
        # that real ordering (see InsightsService.unmet_demand docstring).
        t = iter(base + timedelta(seconds=i) for i in range(10))
        for _ in range(2):
            await _insert_message(
                db_session, tenant_id, conversation_id, "USER", "avez-vous des tongs ?", {}, next(t)
            )
            await _insert_message(
                db_session,
                tenant_id,
                conversation_id,
                "ASSISTANT",
                "Désolé, rien trouvé",
                {"rag_used": True, "products_found": 0, "product_ids": []},
                next(t),
            )
        # A search that DID find something must not count as unmet demand.
        await _insert_message(
            db_session, tenant_id, conversation_id, "USER", "chaussures de running", {}, next(t)
        )
        await _insert_message(
            db_session,
            tenant_id,
            conversation_id,
            "ASSISTANT",
            "Voici nos chaussures",
            {"rag_used": True, "products_found": 1, "product_ids": ["ext-1"]},
            next(t),
        )
        await db_session.commit()

        service = InsightsService(db_session, tenant_id)
        results = await service.unmet_demand(since=base - timedelta(hours=1))

        assert len(results) == 1
        assert results[0].message == "avez-vous des tongs ?"
        assert results[0].occurrences == 2

    async def test_intent_distribution_counts_by_intent(
        self, tenant_id, conversation_id, db_session: AsyncSession
    ):
        from app.services.insights.service import InsightsService

        now = datetime.utcnow()
        intents = ["product_search", "product_search", "greeting"]
        for intent in intents:
            await _insert_message(
                db_session,
                tenant_id,
                conversation_id,
                "ASSISTANT",
                "reply",
                {"intent": intent},
                now,
            )
        await db_session.commit()

        service = InsightsService(db_session, tenant_id)
        distribution = await service.intent_distribution(since=now - timedelta(hours=1))

        assert distribution == {"product_search": 2, "greeting": 1}

    async def test_coupon_conversion_and_rule_attribution(
        self, tenant_id, db_session: AsyncSession
    ):
        from app.services.insights.service import InsightsService

        rule_id = uuid.uuid4()
        await db_session.execute(
            text(
                "INSERT INTO rules (id, tenant_id, name, conditions, action, priority, is_active, "
                "usage_count, created_at, updated_at) "
                "VALUES (:id, :tenant_id, 'r', '{}', '{}', 0, true, 0, :now, :now)"
            ),
            {"id": rule_id, "tenant_id": tenant_id, "now": datetime.utcnow()},
        )
        await _insert_coupon(db_session, tenant_id, "ACTIVE")
        await _insert_coupon(db_session, tenant_id, "USED")
        await _insert_coupon(db_session, tenant_id, "USED", rule_id=rule_id)
        await db_session.commit()

        service = InsightsService(db_session, tenant_id)
        result = await service.coupon_conversion(since=datetime.utcnow() - timedelta(hours=1))

        assert result["generated"] == 3
        assert result["used"] == 2
        assert result["generated_by_rule"] == 1
        assert result["conversion_rate"] == round(2 / 3, 3)

        await db_session.execute(text("DELETE FROM rules WHERE id = :id"), {"id": rule_id})
        await db_session.commit()

    async def test_low_stock_products_ordered_ascending(self, tenant_id, db_session: AsyncSession):
        from app.services.insights.service import InsightsService

        await _insert_product(db_session, tenant_id, "low-1", "Presque épuisé", 10.0, 2)
        await _insert_product(db_session, tenant_id, "low-2", "Très bas", 10.0, 1)
        await _insert_product(db_session, tenant_id, "plenty", "En stock", 10.0, 50)
        await db_session.commit()

        service = InsightsService(db_session, tenant_id)
        results = await service.low_stock_products(threshold=5)

        assert [r["external_id"] for r in results] == ["low-2", "low-1"]

    async def test_peak_hours_returns_24_buckets(
        self, tenant_id, conversation_id, db_session: AsyncSession
    ):
        from app.services.insights.service import InsightsService

        fixed_time = datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc).replace(tzinfo=None)
        await _insert_message(
            db_session, tenant_id, conversation_id, "USER", "bonjour", {}, fixed_time
        )
        await db_session.commit()

        service = InsightsService(db_session, tenant_id)
        results = await service.peak_hours(since=fixed_time - timedelta(hours=1))

        assert len(results) == 24
        by_hour = {r["hour"]: r["count"] for r in results}
        assert by_hour[14] == 1
        assert by_hour[15] == 0
