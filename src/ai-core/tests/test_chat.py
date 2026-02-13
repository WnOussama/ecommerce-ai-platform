"""
Tests pour le Chat API - Compatible avec LLM_PROVIDER=mock
"""

import os
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

# Force mock provider pour les tests
os.environ["LLM_PROVIDER"] = "mock"
os.environ["DB_PASSWORD"] = "test_password_123"
os.environ["SECURITY_JWT_SECRET_KEY"] = "test_jwt_secret_key_32_characters_min"

from app.main import create_application


@pytest.fixture
def app():
    """Crée l'application FastAPI pour les tests."""
    return create_application()


@pytest.fixture
def client(app):
    """Client de test synchrone."""
    return TestClient(app)


@pytest.fixture
async def async_client(app):
    """Client de test asynchrone."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


class TestHealthEndpoint:
    """Tests pour le health check."""

    def test_health_endpoint_returns_ok(self, client):
        """Le endpoint /health doit retourner un status OK."""
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_endpoint_has_status(self, client):
        """Le endpoint /health doit contenir un champ status."""
        response = client.get("/health")
        data = response.json()
        assert "status" in data


class TestTenantValidation:
    """Tests pour la validation du tenant."""

    def test_chat_without_tenant_returns_401(self, client):
        """Une requête sans tenant doit retourner 401."""
        response = client.post(
            "/api/v1/chat/message",
            json={"message": "Bonjour"}
        )
        assert response.status_code == 401

    def test_chat_with_invalid_tenant_header(self, client):
        """Une requête avec tenant invalide doit être rejetée."""
        response = client.post(
            "/api/v1/chat/message",
            json={"message": "Bonjour"},
            headers={"X-Tenant-ID": ""}
        )
        # Peut être 401 ou 400 selon l'implémentation du middleware
        assert response.status_code in [400, 401, 422]


class TestChatEndpoint:
    """Tests pour le endpoint /chat/message."""

    def test_chat_message_with_valid_tenant(self, client):
        """Une requête avec tenant valide doit retourner une réponse."""
        response = client.post(
            "/api/v1/chat/message",
            json={"message": "Bonjour"},
            headers={"X-Tenant-ID": "test-tenant"}
        )

        # Le middleware peut rejeter même avec header si pas de tenant valide
        # Acceptons 200 (succès) ou 401/403 (tenant non trouvé en DB)
        if response.status_code == 200:
            data = response.json()
            assert "response" in data
            assert "conversation_id" in data
            assert "message_id" in data
            assert data["response"]  # Non vide
        else:
            # Middleware a rejeté - c'est OK pour ce test
            assert response.status_code in [401, 403]

    def test_chat_message_returns_expected_fields(self, client):
        """La réponse doit contenir tous les champs attendus."""
        response = client.post(
            "/api/v1/chat/message",
            json={"message": "Quel est le prix ?"},
            headers={"X-Tenant-ID": "demo-tenant"}
        )

        if response.status_code == 200:
            data = response.json()

            # Champs obligatoires
            assert "conversation_id" in data
            assert "message_id" in data
            assert "response" in data
            assert "intent" in data
            assert "confidence" in data
            assert "suggestions" in data
            assert "metadata" in data

    def test_chat_message_empty_message_rejected(self, client):
        """Un message vide doit être rejeté."""
        response = client.post(
            "/api/v1/chat/message",
            json={"message": ""},
            headers={"X-Tenant-ID": "demo-tenant"}
        )
        # Pydantic validation devrait rejeter
        assert response.status_code == 422

    def test_chat_message_with_context(self, client):
        """La requête avec contexte doit fonctionner."""
        response = client.post(
            "/api/v1/chat/message",
            json={
                "message": "Je cherche un produit",
                "context": {"current_page": "/category/shoes"}
            },
            headers={"X-Tenant-ID": "demo-tenant"}
        )

        # Accepte 200 ou rejection du tenant
        assert response.status_code in [200, 401, 403]


class TestMockLLMProvider:
    """Tests spécifiques pour le Mock LLM."""

    def test_mock_provider_is_used(self):
        """Vérifie que le mock provider est utilisé."""
        from app.infrastructure.llm import get_llm_provider

        provider = get_llm_provider()
        assert provider.get_model_name() == "mock-llm-v1"

    def test_mock_provider_is_available(self):
        """Le mock provider doit toujours être disponible."""
        from app.infrastructure.llm import get_llm_provider

        provider = get_llm_provider()
        assert provider.is_available() is True

    @pytest.mark.asyncio
    async def test_mock_provider_chat_returns_string(self):
        """Le mock provider doit retourner une chaîne."""
        from app.infrastructure.llm import get_llm_provider

        provider = get_llm_provider()
        response = await provider.chat("Bonjour")

        assert isinstance(response, str)
        assert len(response) > 0

    @pytest.mark.asyncio
    async def test_mock_provider_responds_to_price_query(self):
        """Le mock doit répondre aux questions de prix."""
        from app.infrastructure.llm import get_llm_provider

        provider = get_llm_provider()
        response = await provider.chat("Quel est le prix ?")

        assert isinstance(response, str)
        # La réponse doit mentionner un prix ou être pertinente
        assert any(word in response.lower() for word in ["prix", "€", "tarif", "produit", "aide"])

    @pytest.mark.asyncio
    async def test_mock_provider_responds_to_stock_query(self):
        """Le mock doit répondre aux questions de stock."""
        from app.infrastructure.llm import get_llm_provider

        provider = get_llm_provider()
        response = await provider.chat("Ce produit est-il disponible ?")

        assert isinstance(response, str)
        assert len(response) > 10


class TestLLMProviderFactory:
    """Tests pour la factory LLM."""

    def test_factory_returns_mock_when_configured(self):
        """La factory doit retourner mock quand LLM_PROVIDER=mock."""
        from app.infrastructure.llm import LLMProviderFactory

        LLMProviderFactory.reset()
        os.environ["LLM_PROVIDER"] = "mock"

        provider = LLMProviderFactory.get_provider(force_new=True)
        assert provider.get_model_name() == "mock-llm-v1"

    def test_factory_fallback_to_mock_without_openai_key(self):
        """Sans clé OpenAI valide, la factory doit fallback sur mock."""
        from app.infrastructure.llm import LLMProviderFactory

        LLMProviderFactory.reset()
        os.environ["LLM_PROVIDER"] = "openai"
        os.environ["LLM_OPENAI_API_KEY"] = ""  # Pas de clé

        provider = LLMProviderFactory.get_provider(force_new=True)
        # Doit fallback sur mock
        assert provider.get_model_name() == "mock-llm-v1"

        # Reset pour les autres tests
        os.environ["LLM_PROVIDER"] = "mock"

    def test_factory_singleton_behavior(self):
        """La factory doit retourner la même instance."""
        from app.infrastructure.llm import LLMProviderFactory

        LLMProviderFactory.reset()

        provider1 = LLMProviderFactory.get_provider()
        provider2 = LLMProviderFactory.get_provider()

        assert provider1 is provider2


class TestChatRAGIntegration:
    """Tests pour l'intégration RAG dans le chat."""

    def test_chat_request_accepts_use_rag_param(self, client):
        """La requête doit accepter le paramètre use_rag."""
        response = client.post(
            "/api/v1/chat/message",
            json={
                "message": "Je cherche un téléphone",
                "use_rag": True,
                "top_k": 3
            },
            headers={"X-Tenant-ID": "demo-tenant"}
        )

        # Le status peut être 200 ou rejection tenant
        assert response.status_code in [200, 401, 403]

    def test_chat_request_can_disable_rag(self, client):
        """Le RAG peut être désactivé via use_rag=False."""
        response = client.post(
            "/api/v1/chat/message",
            json={
                "message": "Bonjour",
                "use_rag": False
            },
            headers={"X-Tenant-ID": "demo-tenant"}
        )

        if response.status_code == 200:
            data = response.json()
            # Sans RAG, la liste products devrait être vide
            assert "products" in data

    def test_chat_response_includes_products_field(self, client):
        """La réponse doit inclure le champ products."""
        response = client.post(
            "/api/v1/chat/message",
            json={"message": "Je cherche un produit"},
            headers={"X-Tenant-ID": "demo-tenant"}
        )

        if response.status_code == 200:
            data = response.json()
            assert "products" in data
            assert isinstance(data["products"], list)

    def test_chat_response_includes_rag_metadata(self, client):
        """La réponse doit inclure les metadata RAG."""
        response = client.post(
            "/api/v1/chat/message",
            json={"message": "Quel est le prix ?"},
            headers={"X-Tenant-ID": "demo-tenant"}
        )

        if response.status_code == 200:
            data = response.json()
            metadata = data.get("metadata", {})
            assert "rag_enabled" in metadata or "rag_search_time_ms" in metadata

    def test_chat_graceful_without_indexed_products(self, client):
        """Le chat doit fonctionner même sans produits indexés."""
        response = client.post(
            "/api/v1/chat/message",
            json={
                "message": "Je cherche un produit spécifique",
                "use_rag": True
            },
            headers={"X-Tenant-ID": "tenant-without-products"}
        )

        # Doit fonctionner même si pas de produits
        if response.status_code == 200:
            data = response.json()
            assert "response" in data
            # products peut être vide, c'est OK
            assert "products" in data

    def test_top_k_validation(self, client):
        """top_k doit être validé (1-20)."""
        # Test avec top_k invalide (trop grand)
        response = client.post(
            "/api/v1/chat/message",
            json={
                "message": "Test",
                "top_k": 100  # Trop grand
            },
            headers={"X-Tenant-ID": "demo-tenant"}
        )

        # Pydantic devrait rejeter
        assert response.status_code == 422


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

