"""
Configuration pytest globale pour les tests
"""

import os
import sys
import pytest

# Configuration de l'environnement de test AVANT tout import de l'app
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("DB_PASSWORD", "test_password_123")
os.environ.setdefault("SECURITY_JWT_SECRET_KEY", "test_jwt_secret_key_32_characters_minimum")
os.environ.setdefault("REDIS_HOST", "localhost")
os.environ.setdefault("DB_HOST", "localhost")


def pytest_configure(config):
    """Configuration pytest."""
    # Ajouter le chemin de l'app au PYTHONPATH
    app_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if app_path not in sys.path:
        sys.path.insert(0, app_path)


@pytest.fixture(scope="session", autouse=True)
def setup_test_environment():
    """Configure l'environnement de test une fois par session."""
    # S'assurer que le mock LLM est utilisé
    os.environ["LLM_PROVIDER"] = "mock"

    yield

    # Cleanup si nécessaire


@pytest.fixture
def mock_tenant_id():
    """Retourne un tenant ID de test."""
    return "test-tenant-123"


@pytest.fixture
def valid_chat_request():
    """Retourne une requête de chat valide."""
    return {
        "message": "Bonjour, je cherche un produit",
        "conversation_id": None,
        "customer_id": None,
        "context": {}
    }


@pytest.fixture
def auth_headers(mock_tenant_id):
    """Retourne les headers d'authentification de test."""
    return {
        "X-Tenant-ID": mock_tenant_id,
        "Content-Type": "application/json"
    }

