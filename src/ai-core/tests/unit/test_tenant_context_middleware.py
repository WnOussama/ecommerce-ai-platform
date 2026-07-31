"""
Tests pour TenantContextMiddleware._is_public_path.

Régression: PUBLIC_PATHS contenait "/" comparé avec startswith(), ce qui
rendait TOUTE route publique (tout chemin commence par "/"). Le middleware
sautait alors l'authentification pour toutes les requêtes, y compris les
endpoints admin sensibles.
"""

import pytest

from app.api.middleware.tenant_context import TenantContextMiddleware


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

    def test_tenant_signup_is_public_but_only_exact_root(self, middleware):
        """POST /api/v1/tenants (signup) is public - a prospect has no API key yet."""
        assert middleware._is_public_path("/api/v1/tenants") is True
        # But this must NOT leak into a prefix match covering authenticated
        # sub-routes - exactly the class of bug this file already regresses.
        assert middleware._is_public_path("/api/v1/tenants/current") is False
        assert middleware._is_public_path("/api/v1/tenants/current/usage") is False
        assert middleware._is_public_path("/api/v1/tenants/current/settings") is False

    def test_tenant_verify_is_public(self, middleware):
        assert middleware._is_public_path("/api/v1/tenants/verify/sometoken123") is True
