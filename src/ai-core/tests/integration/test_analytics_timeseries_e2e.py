"""
Test de bout en bout: /api/v1/analytics/timeseries renvoie de vrais
agrégats par jour, calculés depuis conversations/messages - le champ
`charts` de /dashboard était vide car rien ne le calculait.
"""

import json
import os
import uuid
from datetime import datetime, timedelta

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
            "name": f"Timeseries Test Tenant {new_id.hex[:8]}",
            "slug": f"timeseries-test-{new_id.hex[:8]}",
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


async def _insert_conversation(db_session, tenant_id, created_at) -> uuid.UUID:
    conv_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO conversations (id, tenant_id, user_identifier, status, extra_data, created_at, updated_at) "
            "VALUES (:id, :tid, :uid, 'ACTIVE', '{}', :now, :now)"
        ),
        {"id": conv_id, "tid": tenant_id, "uid": f"user_{conv_id.hex[:6]}", "now": created_at},
    )
    return conv_id


async def _insert_message(
    db_session, tenant_id, conversation_id, role, extra_data, created_at, tokens_in=0, tokens_out=0
) -> None:
    await db_session.execute(
        text(
            "INSERT INTO messages (id, tenant_id, conversation_id, idempotency_key, role, content, "
            "extra_data, tokens_input, tokens_output, created_at, updated_at) "
            "VALUES (:id, :tid, :cid, :ik, :role, 'x', CAST(:extra AS JSONB), :ti, :to, :now, :now)"
        ),
        {
            "id": uuid.uuid4(),
            "tid": tenant_id,
            "cid": conversation_id,
            "ik": uuid.uuid4(),
            "role": role,
            "extra": json.dumps(extra_data),
            "ti": tokens_in,
            "to": tokens_out,
            "now": created_at,
        },
    )


@pytest.mark.asyncio
class TestAnalyticsTimeseriesEndToEnd:
    async def test_conversations_metric_counts_per_day_with_zero_filled_gaps(
        self, tenant_id, db_session: AsyncSession
    ):
        today = datetime.utcnow()
        yesterday = today - timedelta(days=1)
        two_days_ago = today - timedelta(days=2)  # left with zero conversations

        await _insert_conversation(db_session, tenant_id, today)
        await _insert_conversation(db_session, tenant_id, today)
        await _insert_conversation(db_session, tenant_id, yesterday)
        await db_session.commit()

        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/analytics/timeseries",
                params={"metric": "conversations", "time_range": "last_7_days"},
                headers=headers,
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        by_day = {p["label"]: p["value"] for p in body["points"]}

        assert by_day[today.date().isoformat()] == 2
        assert by_day[yesterday.date().isoformat()] == 1
        assert by_day[two_days_ago.date().isoformat()] == 0  # gap filled with zero, not omitted

    async def test_guardrail_blocks_metric(self, tenant_id, db_session: AsyncSession):
        today = datetime.utcnow()
        conv_id = await _insert_conversation(db_session, tenant_id, today)
        await _insert_message(
            db_session, tenant_id, conv_id, "ASSISTANT", {"guardrail_blocked": True}, today
        )
        await _insert_message(
            db_session, tenant_id, conv_id, "ASSISTANT", {"guardrail_blocked": True}, today
        )
        await _insert_message(
            db_session, tenant_id, conv_id, "ASSISTANT", {"intent": "greeting"}, today
        )
        await db_session.commit()

        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/analytics/timeseries",
                params={"metric": "guardrail_blocks", "time_range": "last_7_days"},
                headers=headers,
            )

        assert resp.status_code == 200, resp.text
        by_day = {p["label"]: p["value"] for p in resp.json()["points"]}
        assert by_day[today.date().isoformat()] == 2

    async def test_llm_cost_metric_matches_token_totals(self, tenant_id, db_session: AsyncSession):
        from app.core.config.settings import settings

        today = datetime.utcnow()
        conv_id = await _insert_conversation(db_session, tenant_id, today)
        await _insert_message(
            db_session, tenant_id, conv_id, "ASSISTANT", {}, today, tokens_in=1000, tokens_out=500
        )
        await db_session.commit()

        expected_cost = round(
            1000 / 1000 * settings.llm.cost_per_1k_input_tokens
            + 500 / 1000 * settings.llm.cost_per_1k_output_tokens,
            4,
        )

        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/analytics/timeseries",
                params={"metric": "llm_cost", "time_range": "last_7_days"},
                headers=headers,
            )

        assert resp.status_code == 200, resp.text
        by_day = {p["label"]: p["value"] for p in resp.json()["points"]}
        assert by_day[today.date().isoformat()] == expected_cost

    async def test_intent_distribution_metric_is_not_a_time_series(
        self, tenant_id, db_session: AsyncSession
    ):
        today = datetime.utcnow()
        conv_id = await _insert_conversation(db_session, tenant_id, today)
        for intent in ["product_search", "product_search", "greeting"]:
            await _insert_message(
                db_session, tenant_id, conv_id, "ASSISTANT", {"intent": intent}, today
            )
        await db_session.commit()

        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/analytics/timeseries",
                params={"metric": "intent_distribution", "time_range": "last_7_days"},
                headers=headers,
            )

        assert resp.status_code == 200, resp.text
        by_label = {p["label"]: p["value"] for p in resp.json()["points"]}
        assert by_label == {"product_search": 2, "greeting": 1}
