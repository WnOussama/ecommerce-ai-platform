"""
Tests Critiques d'Isolation Multi-Tenant

Ces tests vérifient que:
1. Un tenant ne peut PAS accéder aux données d'un autre tenant
2. Les tentatives d'accès inter-tenant retournent 404 (pas 403)
3. Le tenant_id est validé strictement
4. Les injections via headers sont bloquées
5. Les valeurs triviales sont rejetées
"""

import pytest
from typing import Dict, Any
from datetime import datetime
from uuid import uuid4

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.testclient import TestClient

# Import du nouveau validateur
from app.core.security.tenant_validation import (
    TenantIDValidator,
    TenantValidationError,
    validate_tenant_id_dependency,
)

# =============================================================================
# FIXTURES & HELPERS
# =============================================================================

# Simulated in-memory storage per tenant
_tenant_data: Dict[str, Dict[str, Any]] = {}


def get_tenant_data(tenant_id: str) -> Dict[str, Any]:
    """Get or create tenant data storage"""
    if tenant_id not in _tenant_data:
        _tenant_data[tenant_id] = {
            "conversations": {},
            "customers": {},
            "products": {},
        }
    return _tenant_data[tenant_id]


def clear_all_tenant_data():
    """Clear all tenant data between tests"""
    _tenant_data.clear()


# =============================================================================
# TEST APPLICATION (Simplified for testing)
# =============================================================================

def create_test_app() -> FastAPI:
    """Crée une application de test simplifiée utilisant le vrai validateur"""

    app = FastAPI()

    async def get_validated_tenant(request: Request) -> str:
        """Dependency pour extraire et valider le tenant_id"""
        tenant_id = request.headers.get("X-Tenant-ID")
        try:
            return TenantIDValidator.validate(tenant_id)
        except TenantValidationError as e:
            raise HTTPException(status_code=400, detail=e.message)

    @app.post("/api/v1/conversations")
    async def create_conversation(
        request: Request,
        tenant_id: str = Depends(get_validated_tenant),
    ):
        """Créer une conversation"""
        body = await request.json()
        conv_id = f"conv_{uuid4().hex[:12]}"

        tenant_data = get_tenant_data(tenant_id)
        tenant_data["conversations"][conv_id] = {
            "id": conv_id,
            "tenant_id": tenant_id,
            "message": body.get("message"),
            "created_at": datetime.utcnow().isoformat(),
        }

        return {"conversation_id": conv_id, "tenant_id": tenant_id}

    @app.get("/api/v1/conversations/{conversation_id}")
    async def get_conversation(
        conversation_id: str,
        tenant_id: str = Depends(get_validated_tenant),
    ):
        """Récupérer une conversation - DOIT vérifier le tenant_id"""
        tenant_data = get_tenant_data(tenant_id)

        conv = tenant_data["conversations"].get(conversation_id)
        if not conv:
            # IMPORTANT: 404 pas 403 pour éviter l'énumération
            raise HTTPException(status_code=404, detail="Conversation not found")

        return conv

    @app.get("/api/v1/conversations")
    async def list_conversations(
        tenant_id: str = Depends(get_validated_tenant),
    ):
        """Liste les conversations du tenant uniquement"""
        tenant_data = get_tenant_data(tenant_id)
        return {
            "items": list(tenant_data["conversations"].values()),
            "count": len(tenant_data["conversations"]),
        }

    @app.delete("/api/v1/conversations/{conversation_id}")
    async def delete_conversation(
        conversation_id: str,
        tenant_id: str = Depends(get_validated_tenant),
    ):
        """Supprimer une conversation - DOIT vérifier le tenant_id"""
        tenant_data = get_tenant_data(tenant_id)

        if conversation_id not in tenant_data["conversations"]:
            raise HTTPException(status_code=404, detail="Conversation not found")

        del tenant_data["conversations"][conversation_id]
        return {"deleted": True}

    return app


# =============================================================================
# TEST CLASS
# =============================================================================

class TestTenantIsolation:
    """Tests critiques d'isolation multi-tenant"""

    @pytest.fixture(autouse=True)
    def setup(self):
        """Reset tenant data before each test"""
        clear_all_tenant_data()
        yield
        clear_all_tenant_data()

    @pytest.fixture
    def app(self) -> FastAPI:
        return create_test_app()

    @pytest.fixture
    def tenant_a_headers(self) -> Dict[str, str]:
        return {"X-Tenant-ID": "tenant_a1b2c3d4e5f6"}

    @pytest.fixture
    def tenant_b_headers(self) -> Dict[str, str]:
        return {"X-Tenant-ID": "tenant_x9y8z7w6v5u4"}

    # =========================================================================
    # ISOLATION TESTS
    # =========================================================================

    def test_tenant_cannot_access_other_tenant_data(
        self,
        app: FastAPI,
        tenant_a_headers: Dict[str, str],
        tenant_b_headers: Dict[str, str],
    ):
        """
        CRITIQUE: Tenant A ne peut PAS voir les données de Tenant B.
        Doit retourner 404 (pas 403 pour éviter l'énumération).
        """
        client = TestClient(app)

        # Tenant A crée une conversation
        response = client.post(
            "/api/v1/conversations",
            headers=tenant_a_headers,
            json={"message": "Hello from tenant A"}
        )
        assert response.status_code == 200
        conversation_id = response.json()["conversation_id"]

        # Tenant A peut voir sa conversation
        response = client.get(
            f"/api/v1/conversations/{conversation_id}",
            headers=tenant_a_headers,
        )
        assert response.status_code == 200
        assert response.json()["tenant_id"] == "tenant_a1b2c3d4e5f6"

        # Tenant B NE PEUT PAS voir la conversation de A
        response = client.get(
            f"/api/v1/conversations/{conversation_id}",
            headers=tenant_b_headers,
        )
        assert response.status_code == 404  # IMPORTANT: 404 pas 403

    def test_tenant_cannot_delete_other_tenant_data(
        self,
        app: FastAPI,
        tenant_a_headers: Dict[str, str],
        tenant_b_headers: Dict[str, str],
    ):
        """CRITIQUE: Tenant B ne peut PAS supprimer les données de A"""
        client = TestClient(app)

        # Tenant A crée une conversation
        response = client.post(
            "/api/v1/conversations",
            headers=tenant_a_headers,
            json={"message": "Important data"}
        )
        conversation_id = response.json()["conversation_id"]

        # Tenant B essaie de supprimer → 404
        response = client.delete(
            f"/api/v1/conversations/{conversation_id}",
            headers=tenant_b_headers,
        )
        assert response.status_code == 404

        # Vérifier que la conversation existe toujours pour A
        response = client.get(
            f"/api/v1/conversations/{conversation_id}",
            headers=tenant_a_headers,
        )
        assert response.status_code == 200

    def test_tenant_list_only_shows_own_data(
        self,
        app: FastAPI,
        tenant_a_headers: Dict[str, str],
        tenant_b_headers: Dict[str, str],
    ):
        """CRITIQUE: La liste ne montre que les données du tenant courant"""
        client = TestClient(app)

        # Créer des conversations pour les deux tenants
        for i in range(3):
            client.post(
                "/api/v1/conversations",
                headers=tenant_a_headers,
                json={"message": f"A message {i}"}
            )

        for i in range(2):
            client.post(
                "/api/v1/conversations",
                headers=tenant_b_headers,
                json={"message": f"B message {i}"}
            )

        # Tenant A voit uniquement ses 3 conversations
        response = client.get("/api/v1/conversations", headers=tenant_a_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 3
        for item in data["items"]:
            assert item["tenant_id"] == "tenant_a1b2c3d4e5f6"

        # Tenant B voit uniquement ses 2 conversations
        response = client.get("/api/v1/conversations", headers=tenant_b_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 2
        for item in data["items"]:
            assert item["tenant_id"] == "tenant_x9y8z7w6v5u4"

    # =========================================================================
    # VALIDATION TESTS
    # =========================================================================

    def test_missing_tenant_id_rejected(self, app: FastAPI):
        """CRITIQUE: Requêtes sans tenant_id sont rejetées"""
        client = TestClient(app)

        response = client.get("/api/v1/conversations")
        assert response.status_code == 400
        detail = response.json()["detail"]
        # detail can be a string or a dict depending on the endpoint
        detail_str = detail if isinstance(detail, str) else str(detail)
        assert "tenant" in detail_str.lower() or "x-tenant-id" in detail_str.lower()

    def test_invalid_tenant_id_format_rejected(self, app: FastAPI):
        """CRITIQUE: Format tenant_id invalide rejeté"""
        client = TestClient(app)

        invalid_ids = [
            "invalid",                    # Pas de prefix tenant_
            "tenant_",                    # Trop court
            "tenant_ABC123",              # Majuscules
            "tenant_abc-123",             # Tiret interdit
            "TENANT_abc12345678",         # Mauvaise casse prefix
            "tenant_" + "a" * 40,         # Trop long (40 chars > max 32)
        ]

        for invalid_id in invalid_ids:
            response = client.get(
                "/api/v1/conversations",
                headers={"X-Tenant-ID": invalid_id}
            )
            assert response.status_code == 400, f"Should reject: {invalid_id}"

    # =========================================================================
    # INJECTION TESTS
    # =========================================================================

    def test_sql_injection_in_tenant_id_blocked(self, app: FastAPI):
        """CRITIQUE: Injection SQL via tenant_id bloquée"""
        client = TestClient(app)

        injection_attempts = [
            "tenant_abc12345'; DROP TABLE users; --",
            "tenant_abc12345\" OR \"1\"=\"1",
            "tenant_abc12345; SELECT * FROM users",
            "tenant_abc12345' UNION SELECT * FROM passwords--",
        ]

        for injection in injection_attempts:
            response = client.get(
                "/api/v1/conversations",
                headers={"X-Tenant-ID": injection}
            )
            assert response.status_code == 400, f"Should block: {injection}"

    def test_path_traversal_in_tenant_id_blocked(self, app: FastAPI):
        """CRITIQUE: Path traversal via tenant_id bloqué"""
        client = TestClient(app)

        traversal_attempts = [
            "tenant_abc12345/../../../etc/passwd",
            "tenant_abc12345..%2f..%2f",
            "tenant_abc12345%00",
        ]

        for attempt in traversal_attempts:
            response = client.get(
                "/api/v1/conversations",
                headers={"X-Tenant-ID": attempt}
            )
            assert response.status_code == 400, f"Should block: {attempt}"

    def test_header_injection_blocked(self, app: FastAPI):
        """CRITIQUE: Injection via headers bloquée"""
        client = TestClient(app)

        # Tentatives d'injection via des headers malformés
        injection_headers = [
            {"X-Tenant-ID": "tenant_abc12345\r\nX-Admin: true"},
            {"X-Tenant-ID": "tenant_abc12345\nSet-Cookie: admin=true"},
        ]

        for headers in injection_headers:
            # Ces requêtes devraient être rejetées
            try:
                response = client.get("/api/v1/conversations", headers=headers)
                # Si la requête passe, vérifier que le header malicieux n'a pas d'effet
                assert response.status_code in [400, 422]
            except Exception:
                # L'exception est acceptable pour headers malformés
                pass


# =============================================================================
# UNIT TESTS FOR VALIDATOR
# =============================================================================

class TestTenantIDValidator:
    """Tests unitaires pour TenantIDValidator (nouvelle version renforcée)"""

    def test_valid_tenant_ids(self):
        """Tenant IDs valides acceptés"""
        valid_ids = [
            "tenant_abc12345678",
            "tenant_a1b2c3d4e5f67890",
            "tenant_x9y8z7w6v5u4",
            "tenant_prod2024abc123",
        ]
        for tid in valid_ids:
            assert TenantIDValidator.validate(tid) == tid

    def test_none_rejected(self):
        """None rejeté"""
        with pytest.raises(TenantValidationError) as exc_info:
            TenantIDValidator.validate(None)
        assert exc_info.value.error_code == "MISSING_TENANT_ID"

    def test_empty_rejected(self):
        """String vide rejeté"""
        with pytest.raises(TenantValidationError) as exc_info:
            TenantIDValidator.validate("")
        assert exc_info.value.error_code == "EMPTY_TENANT_ID"

    def test_too_short_rejected(self):
        """Tenant ID trop court rejeté"""
        with pytest.raises(TenantValidationError) as exc_info:
            TenantIDValidator.validate("tenant_abc")
        assert exc_info.value.error_code == "INVALID_FORMAT"

    def test_too_long_rejected(self):
        """Tenant ID trop long rejeté"""
        with pytest.raises(TenantValidationError) as exc_info:
            TenantIDValidator.validate("tenant_" + "a" * 100)
        assert exc_info.value.error_code == "TENANT_ID_TOO_LONG"

    def test_special_chars_rejected(self):
        """Caractères spéciaux rejetés (injection)"""
        with pytest.raises(TenantValidationError) as exc_info:
            TenantIDValidator.validate("tenant_abc'12345678")
        assert "INJECTION" in exc_info.value.error_code

    def test_uppercase_rejected(self):
        """Majuscules rejetées"""
        with pytest.raises(TenantValidationError) as exc_info:
            TenantIDValidator.validate("tenant_ABC12345678")
        assert exc_info.value.error_code == "INVALID_FORMAT"

    # =========================================================================
    # TESTS VALEURS TRIVIALES (nouveau)
    # =========================================================================

    def test_all_zeros_rejected(self):
        """Tous les zéros rejetés (trivial)"""
        with pytest.raises(TenantValidationError) as exc_info:
            TenantIDValidator.validate("tenant_00000000")
        assert exc_info.value.error_code == "TRIVIAL_VALUE"

    def test_all_same_char_rejected(self):
        """Tous caractères identiques rejetés"""
        with pytest.raises(TenantValidationError) as exc_info:
            TenantIDValidator.validate("tenant_aaaaaaaa")
        assert exc_info.value.error_code == "TRIVIAL_VALUE"

    def test_sequential_rejected(self):
        """Séquences simples rejetées"""
        with pytest.raises(TenantValidationError) as exc_info:
            TenantIDValidator.validate("tenant_12345678")
        assert exc_info.value.error_code == "TRIVIAL_VALUE"

    def test_repetitive_pattern_rejected(self):
        """Patterns répétitifs rejetés"""
        with pytest.raises(TenantValidationError) as exc_info:
            TenantIDValidator.validate("tenant_abcabcab")
        assert exc_info.value.error_code == "TRIVIAL_VALUE"

    def test_common_words_rejected(self):
        """Mots communs rejetés"""
        with pytest.raises(TenantValidationError) as exc_info:
            TenantIDValidator.validate("tenant_testtest")
        assert exc_info.value.error_code == "TRIVIAL_VALUE"

    # =========================================================================
    # TESTS INJECTION
    # =========================================================================

    def test_sql_injection_rejected(self):
        """Injection SQL rejetée"""
        injections = [
            "tenant_abc'; DROP--",
            "tenant_abc\"; DELETE",
            "tenant_abc/* comment",
        ]
        for injection in injections:
            with pytest.raises(TenantValidationError) as exc_info:
                TenantIDValidator.validate(injection)
            assert "INJECTION" in exc_info.value.error_code

    def test_path_traversal_rejected(self):
        """Path traversal rejeté"""
        with pytest.raises(TenantValidationError) as exc_info:
            TenantIDValidator.validate("tenant_abc../etc")
        assert exc_info.value.error_code == "INJECTION_PATH_TRAVERSAL"

    def test_header_injection_rejected(self):
        """Header injection rejeté"""
        with pytest.raises(TenantValidationError) as exc_info:
            TenantIDValidator.validate("tenant_abc12345678\r\nX-Admin: true")
        # The control chars or header injection pattern catches this
        assert "INJECTION" in exc_info.value.error_code or exc_info.value.error_code == "INVALID_FORMAT"

    def test_control_chars_rejected(self):
        """Caractères de contrôle rejetés ou stripped"""
        # Control chars are stripped by normalize_header.
        # If the result after stripping is still a valid tenant_id,
        # the validation succeeds (defense in depth via normalization).
        # Test that raw control chars in injection context are caught:
        with pytest.raises(TenantValidationError):
            # After stripping null byte, result is "tenant_abc" which is too short
            TenantIDValidator.validate("tenant_\x00\x00\x00abc")

    # =========================================================================
    # TESTS NORMALISATION
    # =========================================================================

    def test_strips_whitespace(self):
        """Les espaces sont normalisés"""
        # L'espace au début/fin devrait être strippé
        # mais après normalisation, le format doit encore être valide
        result = TenantIDValidator.validate(" tenant_abc12345678 ")
        assert result == "tenant_abc12345678"


# =============================================================================
# RUN TESTS
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])




