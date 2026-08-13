"""
Test de bout en bout: /api/v1/analytics/cost-report calcule de vrais
totaux de coût/tokens à partir de messages réels - répond directement à
la question "quel est le coût par tenant/message" en s'appuyant sur des
colonnes (tokens_input, tokens_output, latency_ms) déjà écrites par le
chat mais jamais exposées.
"""

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
            "name": f"Cost Report Test Tenant {new_id.hex[:8]}",
            "slug": f"cost-report-test-{new_id.hex[:8]}",
            "now": datetime.utcnow(),
        },
    )
    await db_session.commit()

    yield new_id

    await db_session.execute(text("DELETE FROM messages WHERE tenant_id = :id"), {"id": new_id})
    await db_session.execute(
        text("DELETE FROM conversations WHERE tenant_id = :id"), {"id": new_id}
    )
    await db_session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": new_id})
    await db_session.commit()


async def _insert_conversation(db_session, tenant_id) -> uuid.UUID:
    conv_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO conversations (id, tenant_id, user_identifier, status, extra_data, created_at, updated_at) "
            "VALUES (:id, :tid, :uid, 'ACTIVE', '{}', :now, :now)"
        ),
        {
            "id": conv_id,
            "tid": tenant_id,
            "uid": f"user_{conv_id.hex[:6]}",
            "now": datetime.utcnow(),
        },
    )
    return conv_id


async def _insert_assistant_message(
    db_session, tenant_id, conversation_id, content, tokens_in, tokens_out, latency_ms
) -> None:
    await db_session.execute(
        text(
            "INSERT INTO messages (id, tenant_id, conversation_id, idempotency_key, role, content, "
            "extra_data, tokens_input, tokens_output, latency_ms, created_at, updated_at) "
            "VALUES (:id, :tid, :cid, :ik, 'ASSISTANT', :content, '{}', :ti, :to, :lat, :now, :now)"
        ),
        {
            "id": uuid.uuid4(),
            "tid": tenant_id,
            "cid": conversation_id,
            "ik": uuid.uuid4(),
            "content": content,
            "ti": tokens_in,
            "to": tokens_out,
            "lat": latency_ms,
            "now": datetime.utcnow(),
        },
    )


@pytest.mark.asyncio
class TestCostReportEndToEnd:
    async def test_totals_and_cost_per_conversation_match_seeded_messages(
        self, tenant_id, db_session: AsyncSession
    ):
        from app.core.config.settings import settings

        conv_a = await _insert_conversation(db_session, tenant_id)
        conv_b = await _insert_conversation(db_session, tenant_id)

        await _insert_assistant_message(db_session, tenant_id, conv_a, "reply 1", 1000, 500, 200)
        await _insert_assistant_message(db_session, tenant_id, conv_a, "reply 2", 2000, 1000, 400)
        await _insert_assistant_message(db_session, tenant_id, conv_b, "reply 3", 500, 250, 100)
        await db_session.commit()

        expected_input = 1000 + 2000 + 500
        expected_output = 500 + 1000 + 250
        expected_cost = round(
            expected_input / 1000 * settings.llm.cost_per_1k_input_tokens
            + expected_output / 1000 * settings.llm.cost_per_1k_output_tokens,
            4,
        )

        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/analytics/cost-report",
                params={"time_range": "last_7_days"},
                headers=headers,
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["total_messages"] == 3
        assert body["total_tokens_input"] == expected_input
        assert body["total_tokens_output"] == expected_output
        assert body["total_cost_usd"] == expected_cost
        assert body["conversation_count"] == 2
        assert body["avg_latency_ms"] == round((200 + 400 + 100) / 3, 1)
        assert body["cost_per_conversation_usd"] == round(expected_cost / 2, 4)

    async def test_recent_expensive_messages_ordered_by_cost_desc(
        self, tenant_id, db_session: AsyncSession
    ):
        conv = await _insert_conversation(db_session, tenant_id)

        await _insert_assistant_message(db_session, tenant_id, conv, "cheap", 10, 5, 50)
        await _insert_assistant_message(db_session, tenant_id, conv, "expensive", 5000, 3000, 900)
        await _insert_assistant_message(db_session, tenant_id, conv, "medium", 500, 200, 150)
        await db_session.commit()

        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/analytics/cost-report",
                params={"time_range": "last_7_days", "limit": 2},
                headers=headers,
            )

        assert resp.status_code == 200, resp.text
        messages = resp.json()["recent_expensive_messages"]

        assert len(messages) == 2
        assert messages[0]["content_preview"] == "expensive"
        assert messages[1]["content_preview"] == "medium"
        assert messages[0]["cost_usd"] > messages[1]["cost_usd"]

    async def test_empty_period_returns_zeroes_not_an_error(self, tenant_id):
        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/analytics/cost-report",
                params={"time_range": "last_7_days"},
                headers=headers,
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_messages"] == 0
        assert body["total_cost_usd"] == 0.0
        assert body["recent_expensive_messages"] == []
