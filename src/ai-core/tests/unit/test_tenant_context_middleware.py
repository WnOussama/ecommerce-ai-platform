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
        from app.core.config.settings import Environment, settings

        # Le piège: ENVIRONMENT vaut "development" par défaut. On le force ici
        # (la CI utilise un autre ENVIRONMENT) pour prouver que l'environnement
        # seul n'ouvre plus le bypass.
        monkeypatch.setattr(settings, "environment", Environment.DEVELOPMENT)
        monkeypatch.setattr(settings.security, "allow_dev_tenant_header", False)
        assert settings.is_development

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


def _make_request(
    headers: dict,
    method: str = "GET",
    path: str = "/api/v1/tenants/current",
    query_string: str = "",
):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query_string.encode(),
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

    async def test_non_ascii_signature_is_a_401_not_a_500(self, middleware):
        """compare_digest lève TypeError sur une str non ASCII: ça devait être rejeté proprement."""
        import time

        request = _make_request({"X-Timestamp": str(int(time.time())), "X-Signature": "sïgnature"})
        error = await middleware._verify_signature(request, {"id": "t1", "hmac_secret": "sekret"})
        assert error == "Invalid signature"

    async def test_query_string_is_part_of_what_is_signed(self, middleware):
        from app.core.security.api_key_security import APIClientSigner

        signer = APIClientSigner("sk_test", "sekret")
        headers = signer.sign_request("GET", "/api/v1/tenants/current?limit=10")
        tenant = {"id": "t1", "hmac_secret": "sekret"}

        same = _make_request(headers, query_string="limit=10")
        assert await middleware._verify_signature(same, tenant) is None

        # même signature rejouée avec d'autres paramètres: refusée
        tampered = _make_request(headers, query_string="limit=10000&admin=1")
        assert await middleware._verify_signature(tampered, tenant) == "Invalid signature"

        # et une requête signée sans query rejouée AVEC une query: refusée aussi
        no_query_headers = signer.sign_request("GET", "/api/v1/tenants/current")
        added = _make_request(no_query_headers, query_string="limit=10")
        assert await middleware._verify_signature(added, tenant) == "Invalid signature"

    async def test_undecryptable_secret_is_rejected_without_leaking_why(self, middleware):
        request = _make_request({"X-Timestamp": "123", "X-Signature": "abc"})
        error = await middleware._verify_signature(
            request, {"id": "t1", "hmac_secret": None, "hmac_secret_undecryptable": True}
        )
        assert error == "Request signature could not be verified"
        assert "encrypt" not in error.lower() and "key" not in error.lower()

    async def test_epoch_timestamp_does_not_depend_on_the_server_timezone(self, monkeypatch):
        """datetime.utcnow().timestamp() décalait l'heure du fuseau du serveur (Europe/Paris:
        1 à 2 h), donc un client qui envoie time() était rejeté."""
        import time

        from app.core.security.api_key_security import APIClientSigner, HMACSignatureValidator

        monkeypatch.setenv("TZ", "Asia/Tokyo")
        time.tzset()
        try:
            headers = APIClientSigner("sk_test", "sekret").sign_request("GET", "/x")
            assert abs(int(headers["X-Timestamp"]) - time.time()) < 3

            client_ts = int(time.time())  # ce que PHP time() / JS Date.now()/1000 enverraient
            sig = HMACSignatureValidator().create_signature("sekret", client_ts, "GET", "/x")
            ok, err = HMACSignatureValidator().validate_signature(
                sig, "sekret", client_ts, "GET", "/x"
            )
            assert ok, err
        finally:
            monkeypatch.delenv("TZ", raising=False)
            time.tzset()

    async def test_wrong_encryption_key_is_logged_and_does_not_raise(self, caplog):
        import logging
        from types import SimpleNamespace

        from app.core.security.secret_box import encrypt_secret

        tenant = SimpleNamespace(id="t1", hmac_secret_encrypted=encrypt_secret("abc"))
        # simule un secret chiffré avec une AUTRE clé
        tenant.hmac_secret_encrypted = "gAAAAAB-not-a-valid-token-for-this-key"

        with caplog.at_level(logging.ERROR):
            assert TenantContextMiddleware._decrypt_tenant_secret(tenant) is None
        assert "SECURITY_API_KEY_ENCRYPTION_KEY" in caplog.text

    async def test_malformed_timestamp_rejected(self, middleware):
        request = _make_request({"X-Timestamp": "not-a-number", "X-Signature": "abc"})
        error = await middleware._verify_signature(request, {"id": "t1", "hmac_secret": "sekret"})
        assert error is not None
