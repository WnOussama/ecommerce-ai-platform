"""
Tests pour TenantContextMiddleware._is_public_path.

Régression: PUBLIC_PATHS contenait "/" comparé avec startswith(), ce qui
rendait TOUTE route publique (tout chemin commence par "/"). Le middleware
sautait alors l'authentification pour toutes les requêtes, y compris les
endpoints admin sensibles.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.api.middleware.tenant_context import TenantContextMiddleware


async def _echo_tenant_id(request):
    """Trivial handler with no DB dependency - just echoes what the
    middleware put in request.state, so these tests exercise only the
    middleware itself, not the full app (no shared DB engine, no risk of
    the asyncpg cross-event-loop error that a real DB-backed endpoint
    reused across many pytest-asyncio test functions can hit)."""
    return JSONResponse({"tenant_id": getattr(request.state, "tenant_id", None)})


def _middleware_only_app():
    return Starlette(
        routes=[Route("/api/v1/tenants/current", _echo_tenant_id)],
        middleware=[Middleware(TenantContextMiddleware, session_factory=None)],
    )


@pytest.fixture
def middleware():
    return TenantContextMiddleware.__new__(TenantContextMiddleware)


class TestIsPublicPath:
    @pytest.mark.parametrize(
        "path",
        [
            "/health",
            "/health/",
            "/health/db",
            "/metrics",
            "/metrics/",
            "/docs",
            "/redoc",
            "/openapi.json",
            "/",
        ],
    )
    def test_known_public_paths_are_public(self, middleware, path):
        assert middleware._is_public_path(path) is True

    @pytest.mark.parametrize(
        "path",
        [
            "/api/v1/admin/confirm",
            "/api/v1/admin/command",
            "/api/v1/admin/actions",
            "/api/v1/analytics/dashboard",
            "/api/v1/chat/message",
            "/api/v1/tenants/current",
            "/api/v1/coupons/generate",
            "/api/v1/recommendations",
        ],
    )
    def test_protected_paths_are_not_public(self, middleware, path):
        assert middleware._is_public_path(path) is False

    def test_no_tenant_route_is_public(self, middleware):
        """Self service signup and email verification were removed: tenants are
        provisioned by an administrator, so nothing under /tenants may be public
        (a public route here would let anyone mint API keys)."""
        assert middleware._is_public_path("/api/v1/tenants") is False
        assert middleware._is_public_path("/api/v1/tenants/verify/sometoken123") is False
        assert middleware._is_public_path("/api/v1/tenants/current") is False
        assert middleware._is_public_path("/api/v1/tenants/current/usage") is False
        assert middleware._is_public_path("/api/v1/tenants/current/settings") is False


@pytest.mark.asyncio
class TestDevTenantHeaderBypassRequiresExplicitOptIn:
    """
    Régression: le bypass X-Tenant-ID était activé par `is_development or
    is_test`, et ENVIRONMENT vaut "development" par défaut partout (settings.py,
    docker-compose.yml, .env.example) - donc n'importe quel déploiement qui
    oublie de positionner ENVIRONMENT=production laissait n'importe qui lire/
    écrire les données de n'importe quel tenant avec un simple header, sans
    aucune credential. Le bypass doit maintenant dépendre uniquement de
    security.allow_dev_tenant_header (opt-in explicite, jamais dérivé de
    l'environnement).
    """

    async def test_header_alone_is_rejected_when_flag_is_off(self, monkeypatch):
        from app.core.config.settings import settings

        monkeypatch.setattr(settings.security, "allow_dev_tenant_header", False)
        assert settings.is_development  # le défaut - c'est justement le piège

        app = _middleware_only_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/tenants/current",
                headers={"X-Tenant-ID": "3478e866-016e-4f72-a77f-e564bde1ca24"},
            )
        assert response.status_code == 401

    async def test_header_alone_authenticates_when_flag_is_explicitly_on(self, monkeypatch):
        from app.core.config.settings import settings

        monkeypatch.setattr(settings.security, "allow_dev_tenant_header", True)

        app = _middleware_only_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/api/v1/tenants/current",
                headers={"X-Tenant-ID": "00000000-0000-0000-0000-000000000001"},
            )
        assert response.status_code == 200
        assert response.json()["tenant_id"] == "00000000-0000-0000-0000-000000000001"


def _make_request(headers: dict, method: str = "GET", path: str = "/api/v1/tenants/current"):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(scope, receive=receive)


@pytest.mark.asyncio
class TestVerifySignature:
    """
    Régression: une API key seule authentifiait indéfiniment - un secret
    qui fuite pouvait être rejoué par quiconque l'intercepte. Ces tests
    exercisent directement _verify_signature (logique pure, sans DB) pour
    verrouiller: signature manquante/invalide/hors-fenêtre rejetée, secret
    absent rejeté (pas de fallback silencieux), signature valide acceptée.
    """

    @pytest.fixture
    def middleware(self):
        return TenantContextMiddleware.__new__(TenantContextMiddleware)

    async def test_missing_signature_headers_rejected(self, middleware):
        request = _make_request({})
        error = await middleware._verify_signature(request, {"id": "t1", "hmac_secret": "sekret"})
        assert error is not None

    async def test_no_secret_configured_rejected(self, middleware):
        request = _make_request({"X-Timestamp": "123", "X-Signature": "abc"})
        error = await middleware._verify_signature(request, {"id": "t1", "hmac_secret": None})
        assert error is not None

    async def test_valid_signature_accepted(self, middleware):
        from app.core.security.api_key_security import APIClientSigner

        signer = APIClientSigner("sk_test", "sekret")
        headers = signer.sign_request("GET", "/api/v1/tenants/current")
        request = _make_request(headers)
        error = await middleware._verify_signature(request, {"id": "t1", "hmac_secret": "sekret"})
        assert error is None

    async def test_tampered_signature_rejected(self, middleware):
        from app.core.security.api_key_security import APIClientSigner

        signer = APIClientSigner("sk_test", "wrong-secret")
        headers = signer.sign_request("GET", "/api/v1/tenants/current")
        request = _make_request(headers)
        error = await middleware._verify_signature(request, {"id": "t1", "hmac_secret": "sekret"})
        assert error is not None

    async def test_expired_timestamp_rejected(self, middleware):
        import time

        from app.core.security.api_key_security import HMACSignatureValidator

        secret = "sekret"
        stale_timestamp = int(time.time()) - 3600
        signature = HMACSignatureValidator().create_signature(
            secret, stale_timestamp, "GET", "/api/v1/tenants/current"
        )
        request = _make_request({"X-Timestamp": str(stale_timestamp), "X-Signature": signature})
        error = await middleware._verify_signature(request, {"id": "t1", "hmac_secret": secret})
        assert error is not None

    async def test_malformed_timestamp_rejected(self, middleware):
        request = _make_request({"X-Timestamp": "not-a-number", "X-Signature": "abc"})
        error = await middleware._verify_signature(request, {"id": "t1", "hmac_secret": "sekret"})
        assert error is not None
