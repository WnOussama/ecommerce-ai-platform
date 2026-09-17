"""
Tests — LLM Provider Factory (Groq-only)

Covers:
- Missing/invalid Groq key raises, no silent mock fallback
- Groq key validation
- Singleton behavior + reset
- Provider interface contract
- generate()/chat() parse a Groq (OpenAI-compatible) response without a
  real network call (AsyncOpenAI client is mocked)
"""

import os
from unittest.mock import AsyncMock, patch

import pytest

from app.infrastructure.llm.provider_factory import (
    BaseLLMProvider,
    GroqLLMProvider,
    LLMProviderFactory,
    get_llm_provider,
)

_VALID_GROQ_KEY = "gsk_" + "a" * 40


@pytest.fixture(autouse=True)
def reset_factory():
    """Reset singleton between tests - bypasses the conftest stub via force_new=True calls."""
    LLMProviderFactory.reset()
    yield
    LLMProviderFactory.reset()


class TestGroqKeyValidation:
    """Groq key validation must reject invalid keys."""

    @pytest.mark.parametrize(
        "key,expected",
        [
            ("", False),
            ("not-a-key", False),
            ("gsk_fake123456789012345678", False),
            ("gsk_test123456789012345678", False),
            ("gsk_short", False),
            (_VALID_GROQ_KEY, True),
        ],
        ids=["empty", "no_prefix", "fake", "test", "short", "valid_format"],
    )
    def test_key_validation(self, key, expected):
        assert LLMProviderFactory._is_valid_groq_key(key) is expected


class TestFailFastBehavior:
    """Missing/invalid Groq key must raise, never fall back to a mock."""

    @patch.dict(os.environ, {"LLM_GROQ_API_KEY": ""}, clear=False)
    def test_raises_when_no_key(self):
        with pytest.raises(RuntimeError, match="LLM_GROQ_API_KEY"):
            LLMProviderFactory.get_provider(force_new=True)

    @patch.dict(os.environ, {"LLM_GROQ_API_KEY": "gsk_fake_key_12345678901234"}, clear=False)
    def test_raises_when_fake_key(self):
        with pytest.raises(RuntimeError, match="LLM_GROQ_API_KEY"):
            LLMProviderFactory.get_provider(force_new=True)


class TestGroqProviderCreation:
    """A valid Groq key must produce a real GroqLLMProvider."""

    @patch.dict(os.environ, {"LLM_GROQ_API_KEY": _VALID_GROQ_KEY}, clear=False)
    def test_returns_groq_provider(self):
        provider = LLMProviderFactory.get_provider(force_new=True)
        assert isinstance(provider, GroqLLMProvider)

    @patch.dict(os.environ, {"LLM_GROQ_API_KEY": _VALID_GROQ_KEY}, clear=False)
    def test_provider_is_available(self):
        provider = LLMProviderFactory.get_provider(force_new=True)
        assert provider.is_available() is True

    @patch.dict(os.environ, {"LLM_GROQ_API_KEY": _VALID_GROQ_KEY}, clear=False)
    def test_provider_has_model_name(self):
        provider = LLMProviderFactory.get_provider(force_new=True)
        name = provider.get_model_name()
        assert isinstance(name, str)
        assert len(name) > 0

    @patch.dict(os.environ, {"LLM_GROQ_API_KEY": _VALID_GROQ_KEY}, clear=False)
    def test_provider_counts_tokens(self):
        provider = LLMProviderFactory.get_provider(force_new=True)
        count = provider.count_tokens("Hello world, this is a test")
        assert isinstance(count, int)
        assert count > 0

    @patch.dict(os.environ, {"LLM_GROQ_API_KEY": _VALID_GROQ_KEY}, clear=False)
    def test_singleton_behavior(self):
        p1 = LLMProviderFactory.get_provider(force_new=True)
        p2 = LLMProviderFactory.get_provider()
        assert p1 is p2

    @patch.dict(os.environ, {"LLM_GROQ_API_KEY": _VALID_GROQ_KEY}, clear=False)
    def test_force_new_creates_different_instance(self):
        p1 = LLMProviderFactory.get_provider(force_new=True)
        p2 = LLMProviderFactory.get_provider(force_new=True)
        assert p1 is not p2


class TestGroqProviderGenerateChat:
    """generate()/chat() must parse an OpenAI-compatible response with no real network call."""

    def _provider_with_mocked_client(self) -> GroqLLMProvider:
        provider = GroqLLMProvider(api_key=_VALID_GROQ_KEY, model="test-model")
        fake_response = AsyncMock()
        fake_response.choices = [AsyncMock(message=AsyncMock(content="Bonjour !"), finish_reason="stop")]
        fake_response.usage = AsyncMock(prompt_tokens=5, completion_tokens=3)
        provider.client.chat.completions.create = AsyncMock(return_value=fake_response)
        return provider

    @pytest.mark.asyncio
    async def test_generate_returns_parsed_response(self):
        provider = self._provider_with_mocked_client()
        result = await provider.generate([{"role": "user", "content": "Hello"}])
        assert result["content"] == "Bonjour !"
        assert result["usage"].input_tokens == 5
        assert result["usage"].output_tokens == 3

    @pytest.mark.asyncio
    async def test_chat_returns_string(self):
        provider = self._provider_with_mocked_client()
        result = await provider.chat("Bonjour")
        assert result == "Bonjour !"


class TestGetLLMProviderHelper:
    """get_llm_provider() convenience function."""

    @patch.dict(os.environ, {"LLM_GROQ_API_KEY": _VALID_GROQ_KEY}, clear=False)
    def test_returns_provider(self):
        LLMProviderFactory.get_provider(force_new=True)
        provider = get_llm_provider()
        assert isinstance(provider, BaseLLMProvider)
        assert hasattr(provider, "chat")
        assert hasattr(provider, "generate")
        assert hasattr(provider, "is_available")
