"""
Test de bout en bout: le moteur de règles est réellement câblé dans
/api/v1/chat/message.

Régression: le modèle Rule (conditions/action JSONB, priority, is_active,
usage_count) et la table `rules` existaient déjà en base, mais aucun
repository, aucune évaluation, aucun endpoint ne les utilisait - une
règle créée en base n'avait strictement aucun effet sur le chat.
"""

import json
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
            "name": f"Rules Test Tenant {new_id.hex[:8]}",
            "slug": f"rules-test-{new_id.hex[:8]}",
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
    await db_session.execute(
        text("DELETE FROM analytics_events WHERE tenant_id = :id"), {"id": new_id}
    )
    await db_session.execute(text("DELETE FROM rules WHERE tenant_id = :id"), {"id": new_id})
    await db_session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": new_id})
    await db_session.commit()


async def _insert_rule(
    db_session: AsyncSession,
    tenant_id: uuid.UUID,
    name: str,
    conditions: dict,
    action: dict,
    priority: int = 0,
) -> uuid.UUID:
    rule_id = uuid.uuid4()
    now = datetime.utcnow()
    await db_session.execute(
        text(
            "INSERT INTO rules (id, tenant_id, name, conditions, action, priority, is_active, "
            "usage_count, created_at, updated_at) "
            "VALUES (:id, :tenant_id, :name, CAST(:conditions AS JSONB), CAST(:action AS JSONB), "
            ":priority, true, 0, :now, :now)"
        ),
        {
            "id": rule_id,
            "tenant_id": tenant_id,
            "name": name,
            "conditions": json.dumps(conditions),
            "action": json.dumps(action),
            "priority": priority,
            "now": now,
        },
    )
    await db_session.commit()
    return rule_id


class _SpyLLMProvider:
    """Enveloppe le vrai (mock) LLM provider et enregistre les contextes reçus."""

    def __init__(self, inner):
        self._inner = inner
        self.received_contexts = []
        self.call_count = 0

    async def chat(self, message, context=None, **kwargs):
        self.call_count += 1
        self.received_contexts.append(context)
        return await self._inner.chat(message, context=context, **kwargs)

    def get_model_name(self):
        return self._inner.get_model_name()

    def count_tokens(self, text_):
        return self._inner.count_tokens(text_)


@pytest.mark.asyncio
class TestRulesEndToEnd:
    async def test_canned_response_rule_short_circuits_the_llm(
        self, tenant_id, db_session: AsyncSession
    ):
        from app.infrastructure.llm import get_llm_provider

        canned_text = f"CANNED_RESPONSE_MARKER_{uuid.uuid4().hex[:8]}"
        await _insert_rule(
            db_session,
            tenant_id,
            name="Canned greeting",
            conditions={"intent": "greeting"},
            action={"type": "canned_response", "text": canned_text},
        )

        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        spy = _SpyLLMProvider(get_llm_provider())
        with patch("app.api.v1.endpoints.chat.get_llm_provider", return_value=spy):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/chat/message",
                    json={"message": "Bonjour !", "use_rag": False},
                    headers=headers,
                )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["response"] == canned_text
        assert body["metadata"]["rule_triggered"] is True

        # The LLM must never have been called - the rule short-circuited it.
        assert spy.call_count == 0

    async def test_inject_instruction_rule_reaches_the_system_prompt(
        self, tenant_id, db_session: AsyncSession
    ):
        from app.infrastructure.llm import get_llm_provider

        instruction_marker = f"INSTRUCTION_MARKER_{uuid.uuid4().hex[:8]}"
        await _insert_rule(
            db_session,
            tenant_id,
            name="Inject upsell instruction",
            conditions={"intent": "product_search"},
            action={"type": "inject_instruction", "instruction": instruction_marker},
        )

        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        spy = _SpyLLMProvider(get_llm_provider())
        with patch("app.api.v1.endpoints.chat.get_llm_provider", return_value=spy):
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/chat/message",
                    json={"message": "je cherche un produit", "use_rag": False},
                    headers=headers,
                )

        assert resp.status_code == 200, resp.text
        assert spy.call_count == 1
        assert instruction_marker in (spy.received_contexts[0] or "")

    async def test_generate_coupon_rule_creates_real_coupon_and_returns_action(
        self, tenant_id, db_session: AsyncSession
    ):
        rule_id = await _insert_rule(
            db_session,
            tenant_id,
            name="Coupon on request",
            conditions={"intent": "coupon_request"},
            action={"type": "generate_coupon", "discount_percent": 15, "validity_days": 3},
        )

        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/chat/message",
                json={
                    "message": "avez-vous un code promo ?",
                    "customer_id": "cust_rules_test",
                    "use_rag": False,
                },
                headers=headers,
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["actions"]) == 1
        action = body["actions"][0]
        assert action["type"] == "generate_coupon"
        code = action["data"]["code"]
        assert action["data"]["discount_percent"] == 15

        coupon_row = (
            await db_session.execute(
                text(
                    "SELECT tenant_id, rule_id, discount_percent, extra_data FROM coupons WHERE code = :code"
                ),
                {"code": code},
            )
        ).fetchone()
        assert coupon_row is not None
        assert coupon_row.tenant_id == tenant_id
        assert coupon_row.rule_id == rule_id
        assert coupon_row.discount_percent == 15

    async def test_usage_count_increments_on_trigger(self, tenant_id, db_session: AsyncSession):
        rule_id = await _insert_rule(
            db_session,
            tenant_id,
            name="Track usage",
            conditions={"intent": "greeting"},
            action={"type": "canned_response", "text": "Bonjour, comment puis-je vous aider ?"},
        )

        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(3):
                resp = await client.post(
                    "/api/v1/chat/message",
                    json={"message": "Bonjour", "use_rag": False},
                    headers=headers,
                )
                assert resp.status_code == 200, resp.text

        row = (
            await db_session.execute(
                text("SELECT usage_count FROM rules WHERE id = :id"), {"id": rule_id}
            )
        ).fetchone()
        assert row.usage_count == 3

        event_count = (
            await db_session.execute(
                text(
                    "SELECT COUNT(*) FROM analytics_events WHERE tenant_id = :tid "
                    "AND event_type = 'rule_triggered'"
                ),
                {"tid": tenant_id},
            )
        ).scalar()
        assert event_count == 3

    async def test_no_matching_rule_falls_through_to_normal_llm_flow(
        self, tenant_id, db_session: AsyncSession
    ):
        await _insert_rule(
            db_session,
            tenant_id,
            name="Only fires on greeting",
            conditions={"intent": "greeting"},
            action={"type": "canned_response", "text": "should not be used"},
        )

        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/chat/message",
                json={"message": "Quels sont vos délais de livraison ?", "use_rag": False},
                headers=headers,
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["response"] != "should not be used"
        assert body["metadata"].get("rule_triggered") is not True
