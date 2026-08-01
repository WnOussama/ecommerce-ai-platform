"""
Test de bout en bout: GET /api/v1/chat/conversations liste réellement
les conversations du tenant - il n'existait auparavant aucun moyen de
lister les conversations, seulement de récupérer l'historique d'une
conversation dont on connaissait déjà l'ID (GET /history/{id}).
"""

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
            "name": f"Conv List Test Tenant {new_id.hex[:8]}",
            "slug": f"conv-list-test-{new_id.hex[:8]}",
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


async def _insert_conversation(
    db_session, tenant_id, user_identifier, created_at, status="ACTIVE"
) -> uuid.UUID:
    conv_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO conversations (id, tenant_id, user_identifier, status, extra_data, created_at, updated_at) "
            "VALUES (:id, :tid, :uid, :status, '{}', :now, :now)"
        ),
        {
            "id": conv_id,
            "tid": tenant_id,
            "uid": user_identifier,
            "status": status,
            "now": created_at,
        },
    )
    return conv_id


async def _insert_message(db_session, tenant_id, conversation_id, role, created_at) -> None:
    await db_session.execute(
        text(
            "INSERT INTO messages (id, tenant_id, conversation_id, idempotency_key, role, content, "
            "extra_data, created_at, updated_at) "
            "VALUES (:id, :tid, :cid, :ik, :role, 'x', '{}', :now, :now)"
        ),
        {
            "id": uuid.uuid4(),
            "tid": tenant_id,
            "cid": conversation_id,
            "ik": uuid.uuid4(),
            "role": role,
            "now": created_at,
        },
    )


@pytest.mark.asyncio
class TestConversationListEndToEnd:
    async def test_lists_conversations_most_recent_first_with_message_counts(
        self, tenant_id, db_session: AsyncSession
    ):
        now = datetime.utcnow()
        older = now - timedelta(hours=2)

        conv_old = await _insert_conversation(db_session, tenant_id, "user_a", older)
        conv_new = await _insert_conversation(db_session, tenant_id, "user_b", now)

        await _insert_message(db_session, tenant_id, conv_old, "USER", older)
        await _insert_message(db_session, tenant_id, conv_old, "ASSISTANT", older)
        await _insert_message(db_session, tenant_id, conv_new, "USER", now)
        await db_session.commit()

        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/chat/conversations", headers=headers)

        assert resp.status_code == 200, resp.text
        body = resp.json()

        assert body["total"] == 2
        ids_in_order = [c["conversation_id"] for c in body["conversations"]]
        assert ids_in_order == [str(conv_new), str(conv_old)]  # most recent first

        by_id = {c["conversation_id"]: c for c in body["conversations"]}
        assert by_id[str(conv_old)]["message_count"] == 2
        assert by_id[str(conv_new)]["message_count"] == 1
        assert by_id[str(conv_old)]["user_identifier"] == "user_a"

    async def test_pagination_limit_and_offset(self, tenant_id, db_session: AsyncSession):
        now = datetime.utcnow()
        for i in range(5):
            await _insert_conversation(
                db_session, tenant_id, f"user_{i}", now - timedelta(minutes=i)
            )
        await db_session.commit()

        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/chat/conversations",
                params={"limit": 2, "offset": 2},
                headers=headers,
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 5
        assert len(body["conversations"]) == 2
        assert body["limit"] == 2
        assert body["offset"] == 2

    async def test_status_filter(self, tenant_id, db_session: AsyncSession):
        now = datetime.utcnow()
        await _insert_conversation(db_session, tenant_id, "active_user", now, status="ACTIVE")
        await _insert_conversation(db_session, tenant_id, "resolved_user", now, status="RESOLVED")
        await db_session.commit()

        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/chat/conversations",
                params={"status": "resolved"},
                headers=headers,
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert body["conversations"][0]["user_identifier"] == "resolved_user"

    async def test_empty_for_new_tenant(self, tenant_id):
        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/chat/conversations", headers=headers)

        assert resp.status_code == 200, resp.text
        assert resp.json() == {"conversations": [], "total": 0, "limit": 50, "offset": 0}
