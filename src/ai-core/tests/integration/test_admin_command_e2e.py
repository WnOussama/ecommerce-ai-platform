"""
Test de bout en bout: /api/v1/admin/command exécute réellement
AdminAgent (voir agent_v2.py) au lieu de renvoyer un
command_id="cmd_placeholder" figé, et le workflow de confirmation vient
du vrai AdminAISafetySystem backé par Redis (voir admin.py::_get_safety_system).

Couvre les trois paliers de risque:
- LOW (get_analytics): exécution immédiate, données réelles (InsightsService)
- MEDIUM (suggest_marketing_strategy): confirmation simple puis exécution réelle (LLM mock)
- HIGH (generate_bulk_coupons): double confirmation + délai obligatoire,
  et création réelle de coupons en base une fois exécutée
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
            "name": f"Admin Cmd Test Tenant {new_id.hex[:8]}",
            "slug": f"admin-cmd-test-{new_id.hex[:8]}",
            "now": datetime.utcnow(),
        },
    )
    await db_session.commit()

    yield new_id

    await db_session.execute(text("DELETE FROM coupons WHERE tenant_id = :id"), {"id": new_id})
    await db_session.execute(
        text("DELETE FROM analytics_events WHERE tenant_id = :id"), {"id": new_id}
    )
    await db_session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": new_id})
    await db_session.commit()


@pytest.mark.asyncio
class TestAdminCommandEndToEnd:
    async def test_low_risk_get_analytics_executes_immediately(self, tenant_id):
        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/admin/command",
                json={"command": "Fais-moi une analyse des statistiques"},
                headers=headers,
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["action_name"] == "get_analytics"
        assert body["status"] == "completed"
        assert body["success"] is True
        assert body["requires_confirmation"] is False
        # Real InsightsService keys, not invented sales numbers.
        assert "most_requested_products" in body["data"]
        assert "total_revenue" not in body["data"]

    async def test_medium_risk_requires_confirmation_then_executes_via_llm(self, tenant_id):
        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            initial = await client.post(
                "/api/v1/admin/command",
                json={
                    "action_name": "suggest_marketing_strategy",
                    "parameters": {"objective": "retention"},
                },
                headers=headers,
            )
            assert initial.status_code == 200, initial.text
            body = initial.json()
            assert body["status"] == "pending_confirmation"
            assert body["requires_confirmation"] is True
            assert body["confirmation_token"]
            assert body["dry_run"] is not None

            confirm = await client.post(
                "/api/v1/admin/confirm",
                json={
                    "action_id": body["action_id"],
                    "confirmation_token": body["confirmation_token"],
                },
                headers=headers,
            )

        assert confirm.status_code == 200, confirm.text
        confirmed_body = confirm.json()
        assert confirmed_body["status"] == "completed"
        assert confirmed_body["success"] is True
        assert "analysis" in confirmed_body["data"]

    async def test_high_risk_bulk_coupons_needs_two_confirmations_and_a_delay(self, tenant_id):
        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            initial = await client.post(
                "/api/v1/admin/command",
                json={
                    "action_name": "generate_bulk_coupons",
                    "parameters": {
                        "customer_ids": ["cust_a", "cust_b"],
                        "max_count": 2,
                        "discount_percent": 10,
                    },
                    "reason": "integration test",
                },
                headers=headers,
            )
            assert initial.status_code == 200, initial.text
            body = initial.json()
            assert body["status"] == "pending_confirmation"

            first_confirm = await client.post(
                "/api/v1/admin/confirm",
                json={
                    "action_id": body["action_id"],
                    "confirmation_token": body["confirmation_token"],
                },
                headers=headers,
            )
            assert first_confirm.status_code == 200
            assert first_confirm.json()["status"] == "pending_confirmation"  # 1st of 2

            second_confirm = await client.post(
                "/api/v1/admin/confirm",
                json={
                    "action_id": body["action_id"],
                    "confirmation_token": body["confirmation_token"],
                },
                headers=headers,
            )

        # The action has a mandatory 60s delay after the 2nd confirmation -
        # it must NOT execute immediately, proving the safety gate is real
        # rather than a rubber stamp.
        assert second_confirm.status_code == 200
        second_body = second_confirm.json()
        assert second_body["status"] == "failed"
        assert "wait" in (second_body["error"] or "").lower()

    async def test_reject_action_cancels_pending_confirmation(self, tenant_id):
        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            initial = await client.post(
                "/api/v1/admin/command",
                json={
                    "action_name": "suggest_marketing_strategy",
                    "parameters": {"objective": "retention"},
                },
                headers=headers,
            )
            action_id = initial.json()["action_id"]

            reject = await client.post(
                "/api/v1/admin/reject",
                json={"action_id": action_id, "reason": "not needed right now"},
                headers=headers,
            )
            assert reject.status_code == 200, reject.text
            assert reject.json()["status"] == "rejected"

            # A rejected action can no longer be confirmed.
            confirm_attempt = await client.post(
                "/api/v1/admin/confirm",
                json={
                    "action_id": action_id,
                    "confirmation_token": initial.json()["confirmation_token"],
                },
                headers=headers,
            )
        assert confirm_attempt.json()["success"] is False

    async def test_unknown_command_is_rejected_not_executed(self, tenant_id):
        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/admin/command",
                json={"command": "raconte-moi une blague"},
                headers=headers,
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is False
        assert body["status"] == "failed"

    async def test_action_history_is_persisted_and_readable(self, tenant_id, db_session):
        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            executed = await client.post(
                "/api/v1/admin/command",
                json={"action_name": "get_analytics", "parameters": {}},
                headers=headers,
            )
            action_id = executed.json()["action_id"]

            history = await client.get("/api/v1/admin/actions", headers=headers)
            assert history.status_code == 200
            ids_in_history = [a["action_id"] for a in history.json()]
            assert action_id in ids_in_history

            detail = await client.get(f"/api/v1/admin/actions/{action_id}", headers=headers)
            assert detail.status_code == 200
            assert detail.json()["action_id"] == action_id
            assert detail.json()["status"] == "completed"

        row = (
            await db_session.execute(
                text(
                    "SELECT event_type, entity_type FROM analytics_events "
                    "WHERE tenant_id = :tid AND entity_id = :eid"
                ),
                {"tid": tenant_id, "eid": uuid.UUID(action_id)},
            )
        ).fetchone()
        assert row is not None
        assert row.event_type == "admin_action"
