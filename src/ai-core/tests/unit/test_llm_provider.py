"""
Tests — LLM Provider Factory

Covers:
- Mock provider creation
- OpenAI key validation
- Fallback to mock when OpenAI unavailable
- Singleton behavior + reset
- Provider interface contract
- Edge cases (empty keys, fake keys)
"""

import os
import pytest
from unittest.mock import patch

from app.infrastructure.llm.provider_factory import (
    BaseLLMProvider,
    LLMProviderFactory,
    get_llm_provider,
)
from app.infrastructure.llm.mock_provider import MockLLMProvider


@pytest.fixture(autouse=True)
def reset_factory():
    """Reset singleton between tests."""
    LLMProviderFactory.reset()
    yield
    LLMProviderFactory.reset()


class TestMockProviderCreation:
    """Mock provider must be created when LLM_PROVIDER=mock."""

    def test_returns_mock_provider(self):
        provider = LLMProviderFactory.get_provider()
        assert isinstance(provider, MockLLMProvider)

    def test_mock_provider_is_available(self):
        provider = LLMProviderFactory.get_provider()
        assert provider.is_available() is True

    def test_mock_provider_has_model_name(self):
        provider = LLMProviderFactory.get_provider()
        name = provider.get_model_name()
        assert isinstance(name, str)
        assert len(name) > 0

    def test_mock_provider_counts_tokens(self):
        provider = LLMProviderFactory.get_provider()
        count = provider.count_tokens("Hello world, this is a test")
        assert isinstance(count, int)
        assert count > 0


class TestMockProviderChat:
    """Mock provider chat must return coherent responses."""

    @pytest.mark.asyncio
    async def test_chat_returns_string(self):
        provider = LLMProviderFactory.get_provider()
        result = await provider.chat("Bonjour")
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_chat_with_context(self):
        provider = LLMProviderFactory.get_provider()
        result = await provider.chat(
            "Quel est le prix?",
            context="Produit X coûte 29.99€"
        )
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_generate_returns_response(self):
        provider = LLMProviderFactory.get_provider()
        result = await provider.generate([
            {"role": "user", "content": "Hello"}
        ])
        assert result is not None
        assert hasattr(result, "content") or isinstance(result, dict)


class TestSingletonBehavior:
    """Provider factory implements singleton pattern."""

    def test_returns_same_instance(self):
        p1 = LLMProviderFactory.get_provider()
        p2 = LLMProviderFactory.get_provider()
        assert p1 is p2

    def test_force_new_creates_different_instance(self):
        p1 = LLMProviderFactory.get_provider()
        p2 = LLMProviderFactory.get_provider(force_new=True)
        assert p1 is not p2

    def test_reset_clears_instance(self):
        p1 = LLMProviderFactory.get_provider()
        LLMProviderFactory.reset()
        p2 = LLMProviderFactory.get_provider()
        assert p1 is not p2


class TestOpenAIKeyValidation:
    """OpenAI key validation must reject invalid keys."""

    @pytest.mark.parametrize("key,expected", [
        ("", False),
        ("not-a-key", False),
        ("sk-fake-123456789012345678", False),
        ("sk-test-123456789012345678", False),
        ("sk-your-openai-key-here", False),
        ("sk-short", False),
        ("sk-" + "a" * 40, True),
    ], ids=["empty", "no_prefix", "fake", "test", "placeholder", "short", "valid_format"])
    def test_key_validation(self, key, expected):
        result = LLMProviderFactory._is_valid_openai_key(key)
        assert result is expected


class TestFallbackBehavior:
    """When OpenAI is unavailable, must fallback to mock."""

    @patch.dict(os.environ, {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": ""})
    def test_fallback_when_no_key(self):
        provider = LLMProviderFactory.get_provider(force_new=True)
        assert isinstance(provider, MockLLMProvider)

    @patch.dict(os.environ, {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "sk-fake-key"})
    def test_fallback_when_fake_key(self):
        provider = LLMProviderFactory.get_provider(force_new=True)
        assert isinstance(provider, MockLLMProvider)

    @patch.dict(os.environ, {"LLM_PROVIDER": "unknown_provider"})
    def test_fallback_when_unknown_provider(self):
        provider = LLMProviderFactory.get_provider(force_new=True)
        assert isinstance(provider, MockLLMProvider)


class TestGetLLMProviderHelper:
    """get_llm_provider() convenience function."""

    def test_returns_provider(self):
        provider = get_llm_provider()
        assert provider is not None
        assert hasattr(provider, "chat")
        assert hasattr(provider, "generate")
        assert hasattr(provider, "is_available")

    def test_returns_same_as_factory(self):
        p1 = get_llm_provider()
        p2 = LLMProviderFactory.get_provider()
        assert p1 is p2



