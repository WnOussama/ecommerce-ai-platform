"""
Test configuration — sets environment defaults BEFORE any app import.
"""
import os

import pytest

# Must be set before any app module is imported
os.environ.setdefault("DB_PASSWORD", "test_password_123")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "5432")
os.environ.setdefault("DB_NAME", "test_db")
os.environ.setdefault("DB_USER", "test_user")
os.environ.setdefault("SECURITY_JWT_SECRET_KEY", "test_jwt_secret_key_32_chars_minimum_length_here")
os.environ.setdefault("ENVIRONMENT", "development")


@pytest.fixture(autouse=True)
def _stub_llm_provider():
    """
    Seeds LLMProviderFactory's singleton with a deterministic test double
    (tests/support/stub_llm_provider.py) so the suite never needs a real
    LLM_GROQ_API_KEY or network access. Only affects get_provider() without
    force_new=True - tests/unit/test_llm_provider.py exercises the real
    Groq construction/validation logic via force_new=True, bypassing this.
    """
    from app.infrastructure.llm.provider_factory import LLMProviderFactory
    from tests.support.stub_llm_provider import StubLLMProvider

    LLMProviderFactory.reset()
    LLMProviderFactory._instance = StubLLMProvider()
    yield
    LLMProviderFactory.reset()
