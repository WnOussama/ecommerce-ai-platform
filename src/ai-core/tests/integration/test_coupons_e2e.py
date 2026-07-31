"""
Test de bout en bout: génération et validation réelle de coupons.

Régression: /api/v1/coupons/generate renvoyait toujours le même code
littéral ("SAVE10-ABCD1234") pour n'importe quel client, et
/api/v1/coupons/validate acceptait n'importe quelle chaîne comme code
valide - aucune vérification réelle n'existait. Ces tests exercent le
endpoint HTTP réel de bout en bout contre une vraie base Postgres.
"""

import os

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
    import uuid
    from datetime import datetime

    new_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO tenants (id, name, slug, is_active, is_verified, settings, created_at, updated_at) "
            "VALUES (:id, :name, :slug, true, true, '{}', :now, :now)"
        ),
        {
            "id": new_id,
            "name": f"Coupon Test Tenant {new_id.hex[:8]}",
            "slug": f"coupon-test-{new_id.hex[:8]}",
            "now": datetime.utcnow(),
        },
    )
    await db_session.commit()

    yield new_id

    await db_session.execute(text("DELETE FROM coupons WHERE tenant_id = :id"), {"id": new_id})
    await db_session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": new_id})
    await db_session.commit()


@pytest.mark.asyncio
class TestCouponsEndToEnd:
    async def test_generate_then_validate_then_reject_reuse(self, tenant_id):
        from app.main import create_application

        app = create_application()
        transport = ASGITransport(app=app)
        headers = {"X-Tenant-ID": str(tenant_id)}

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            gen_resp = await client.post(
                "/api/v1/coupons/generate",
                json={"customer_id": "cust_e2e_1", "reason": "cart_abandonment"},
                headers=headers,
            )
            assert gen_resp.status_code == 200, gen_resp.text
            coupon = gen_resp.json()["coupon"]
            assert coupon["code"] != "SAVE10-ABCD1234"  # not the old hardcoded value
            assert coupon["discount_value"] == 10.0

            # A second generation must NOT collide with the first.
            gen_resp_2 = await client.post(
                "/api/v1/coupons/generate",
                json={"customer_id": "cust_e2e_2", "reason": "loyalty"},
                headers=headers,
            )
            assert gen_resp_2.status_code == 200
            assert gen_resp_2.json()["coupon"]["code"] != coupon["code"]
            assert gen_resp_2.json()["coupon"]["discount_value"] == 15.0

            # Validating the real code succeeds and computes a real discount.
            validate_resp = await client.post(
                "/api/v1/coupons/validate",
                json={"code": coupon["code"], "customer_id": "cust_e2e_1", "cart_value": 100.0},
                headers=headers,
            )
            assert validate_resp.status_code == 200
            body = validate_resp.json()
            assert body["valid"] is True
            assert body["discount_amount"] == 10.0  # 10% of 100

            # The same code cannot be used twice.
            reuse_resp = await client.post(
                "/api/v1/coupons/validate",
                json={"code": coupon["code"], "customer_id": "cust_e2e_1", "cart_value": 100.0},
                headers=headers,
            )
            assert reuse_resp.status_code == 200
            assert reuse_resp.json()["valid"] is False

            # An arbitrary made-up code must be rejected - this is the core
            # regression: previously ANY string validated successfully.
            fake_resp = await client.post(
                "/api/v1/coupons/validate",
                json={"code": "TOTALLY-MADE-UP", "customer_id": "cust_e2e_1", "cart_value": 100.0},
                headers=headers,
            )
            assert fake_resp.status_code == 200
            assert fake_resp.json()["valid"] is False

            # Customer listing reflects real persisted state.
            listing = await client.get("/api/v1/coupons/customer/cust_e2e_2", headers=headers)
            assert listing.status_code == 200
            codes = [c["code"] for c in listing.json()["coupons"]]
            assert gen_resp_2.json()["coupon"]["code"] in codes

    async def test_coupon_from_another_tenant_is_not_valid(self, tenant_id, db_session):
        import uuid
        from datetime import datetime

        from app.main import create_application

        other_tenant_id = uuid.uuid4()
        await db_session.execute(
            text(
                "INSERT INTO tenants (id, name, slug, is_active, is_verified, settings, created_at, updated_at) "
                "VALUES (:id, :name, :slug, true, true, '{}', :now, :now)"
            ),
            {
                "id": other_tenant_id,
                "name": "Other Tenant",
                "slug": f"other-tenant-{other_tenant_id.hex[:8]}",
                "now": datetime.utcnow(),
            },
        )
        await db_session.commit()

        app = create_application()
        transport = ASGITransport(app=app)

        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                gen_resp = await client.post(
                    "/api/v1/coupons/generate",
                    json={"customer_id": "cust_cross_tenant"},
                    headers={"X-Tenant-ID": str(tenant_id)},
                )
                code = gen_resp.json()["coupon"]["code"]

                cross_tenant_validate = await client.post(
                    "/api/v1/coupons/validate",
                    json={"code": code, "customer_id": "cust_cross_tenant", "cart_value": 50.0},
                    headers={"X-Tenant-ID": str(other_tenant_id)},
                )
                assert cross_tenant_validate.json()["valid"] is False
        finally:
            await db_session.execute(
                text("DELETE FROM tenants WHERE id = :id"), {"id": other_tenant_id}
            )
            await db_session.commit()
