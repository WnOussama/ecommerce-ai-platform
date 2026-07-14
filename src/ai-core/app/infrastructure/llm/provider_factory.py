"""
LLM Provider Factory - Abstraction multi-provider avec fallback automatique
"""

import logging
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from app.core.config.settings import settings
from app.domain.entities.models import LLMUsage

logger = logging.getLogger(__name__)


class BaseLLMProvider(ABC):
    """Interface abstraite pour tous les LLM providers."""

    @abstractmethod
    async def generate(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 1000,
        **kwargs,
    ) -> Any:
        """Génère une réponse à partir des messages."""
        pass

    @abstractmethod
    async def chat(self, message: str, context: Optional[str] = None, **kwargs) -> str:
        """Interface simplifiée pour le chat."""
        pass

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """Compte les tokens d'un texte."""
        pass

    @abstractmethod
    def get_model_name(self) -> str:
        """Retourne le nom du modèle."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Vérifie si le provider est disponible."""
        pass


class OpenAILLMProvider(BaseLLMProvider):
    """Provider OpenAI avec support GPT-4."""

    def __init__(self, api_key: str, model: str = "gpt-4-turbo-preview"):
        import tiktoken
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model
        self._api_key = api_key

        try:
            self._encoder = tiktoken.encoding_for_model("gpt-4")
        except Exception:
            self._encoder = tiktoken.get_encoding("cl100k_base")

        logger.info(f"OpenAILLMProvider initialized with model: {model}")

    async def generate(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 1000,
        **kwargs,
    ) -> Any:
        import time

        start_time = time.time()

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

        latency_ms = int((time.time() - start_time) * 1000)

        return {
            "content": response.choices[0].message.content,
            "usage": LLMUsage(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
                model=self.model,
            ),
            "model": self.model,
            "finish_reason": response.choices[0].finish_reason,
            "latency_ms": latency_ms,
        }

    async def chat(self, message: str, context: Optional[str] = None, **kwargs) -> str:
        messages = []
        if context:
            messages.append({"role": "system", "content": context})
        messages.append({"role": "user", "content": message})

        response = await self.generate(messages, **kwargs)
        return response["content"]

    def count_tokens(self, text: str) -> int:
        return len(self._encoder.encode(text))

    def get_model_name(self) -> str:
        return self.model

    def is_available(self) -> bool:
        return bool(self._api_key and self._api_key.startswith("sk-") and len(self._api_key) > 20)


class LLMProviderFactory:
    """
    Factory pour créer le bon LLM provider selon la configuration.

    Stratégie:
    1. Si LLM_PROVIDER=mock → MockLLMProvider
    2. Si LLM_PROVIDER=openai ET clé valide → OpenAILLMProvider
    3. Sinon → Fallback vers MockLLMProvider
    """

    _instance: Optional[BaseLLMProvider] = None

    @classmethod
    def get_provider(cls, force_new: bool = False) -> BaseLLMProvider:
        """
        Retourne une instance du LLM provider.

        Args:
            force_new: Si True, crée une nouvelle instance

        Returns:
            Instance de BaseLLMProvider
        """
        if cls._instance is not None and not force_new:
            return cls._instance

        provider_type = cls._get_provider_type()

        if provider_type == "mock":
            cls._instance = cls._create_mock_provider()
        elif provider_type == "openai":
            cls._instance = cls._create_openai_provider()
        else:
            logger.warning(f"Unknown provider type: {provider_type}, falling back to mock")
            cls._instance = cls._create_mock_provider()

        return cls._instance

    @classmethod
    def _get_provider_type(cls) -> str:
        """Détermine le type de provider à utiliser."""
        # Priorité: variable d'environnement > settings
        provider = os.getenv("LLM_PROVIDER", "").lower()

        if not provider:
            provider = getattr(settings.llm, "provider", "mock").lower()

        return provider

    @classmethod
    def _create_mock_provider(cls) -> BaseLLMProvider:
        """Crée un MockLLMProvider."""
        from app.infrastructure.llm.mock_provider import MockLLMProvider

        logger.info("Creating MockLLMProvider")
        return MockLLMProvider(simulate_latency=True)

    @classmethod
    def _create_openai_provider(cls) -> BaseLLMProvider:
        """Crée un OpenAILLMProvider ou fallback vers mock."""
        api_key = os.getenv("LLM_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY")

        if not api_key:
            api_key = getattr(settings.llm, "openai_api_key", None)

        # Validation de la clé
        if not api_key or not cls._is_valid_openai_key(api_key):
            logger.warning("Invalid or missing OpenAI API key, falling back to mock provider")
            return cls._create_mock_provider()

        try:
            model = getattr(settings.llm, "openai_model", "gpt-4-turbo-preview")
            provider = OpenAILLMProvider(api_key=api_key, model=model)

            if provider.is_available():
                logger.info(f"OpenAI provider initialized with model: {model}")
                return provider
            else:
                logger.warning("OpenAI provider not available, falling back to mock")
                return cls._create_mock_provider()

        except ImportError as e:
            logger.warning(f"OpenAI package not installed: {e}. Falling back to mock provider.")
            return cls._create_mock_provider()
        except ValueError as e:
            logger.warning(f"Invalid OpenAI configuration: {e}. Falling back to mock provider.")
            return cls._create_mock_provider()
        except (TypeError, AttributeError) as e:
            logger.warning(
                f"OpenAI provider initialization error: {e}. Falling back to mock provider."
            )
            return cls._create_mock_provider()

    @classmethod
    def _is_valid_openai_key(cls, key: str) -> bool:
        """Vérifie si une clé OpenAI semble valide."""
        if not key:
            return False
        if (
            key.startswith("sk-fake")
            or key.startswith("sk-test")
            or key == "sk-your-openai-key-here"
        ):
            return False
        if not key.startswith("sk-") or len(key) < 20:
            return False
        return True

    @classmethod
    def reset(cls):
        """Reset le singleton (utile pour les tests)."""
        cls._instance = None


def get_llm_provider() -> BaseLLMProvider:
    """
    Fonction utilitaire pour obtenir le LLM provider.
    Point d'entrée principal pour les autres services.

    Returns:
        Instance de BaseLLMProvider
    """
    return LLMProviderFactory.get_provider()
