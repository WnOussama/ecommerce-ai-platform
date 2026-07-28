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
            "/api/v1/tenants",
            "/api/v1/tenants/current",
            "/api/v1/coupons/generate",
            "/api/v1/recommendations",
        ],
    )
    def test_protected_paths_are_not_public(self, middleware, path):
        assert middleware._is_public_path(path) is False
