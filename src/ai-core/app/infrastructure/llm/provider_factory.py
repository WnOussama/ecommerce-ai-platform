"""
LLM Provider Factory - Groq uniquement.

Historique: ce module supportait avant un switch multi-provider
(mock/openai/anthropic/groq) qui tombait silencieusement sur un
MockLLMProvider dès qu'une clé manquait ou était invalide - en prod comme
en démo, une clé mal configurée donnait donc des réponses fabriquées sans
qu'aucune erreur ne remonte nulle part. Groq est le seul LLM réel utilisé
par ce projet (API gratuite, compatible OpenAI) ; une config invalide lève
maintenant une erreur explicite au lieu de dégrader silencieusement vers
du faux contenu.
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
    async def chat(
        self,
        message: str,
        context: Optional[str] = None,
        history: Optional[List[Dict[str, str]]] = None,
        **kwargs,
    ) -> str:
        """Interface simplifiée pour le chat. `history` (tours précédents de
        la conversation, `[{"role": "user"|"assistant", "content": ...}]`,
        ordre chronologique) est optionnel pour rester compatible avec les
        appels existants qui n'en fournissent pas (ex: admin agent)."""
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


class GroqLLMProvider(BaseLLMProvider):
    """
    Provider Groq (free tier). Sert des modèles open-weight (Llama, Gemma...)
    via une API compatible OpenAI - on réutilise donc le client `openai` avec
    un base_url différent plutôt que d'ajouter une dépendance dédiée.
    """

    def __init__(self, api_key: str, model: str = "openai/gpt-oss-120b"):
        from openai import AsyncOpenAI

        self.client = AsyncOpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
        self.model = model
        self._api_key = api_key

        logger.info(f"GroqLLMProvider initialized with model: {model}")

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

    async def chat(
        self,
        message: str,
        context: Optional[str] = None,
        history: Optional[List[Dict[str, str]]] = None,
        **kwargs,
    ) -> str:
        messages = []
        if context:
            messages.append({"role": "system", "content": context})
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": message})

        response = await self.generate(messages, **kwargs)
        return response["content"]

    def count_tokens(self, text: str) -> int:
        # Modèles open-weight servis par Groq (Llama, Gemma...) - pas de
        # tokenizer local applicable comme tiktoken pour OpenAI, donc
        # approximation. generate() récupère déjà les comptes réels via
        # response.usage sur chaque appel ; cette méthode ne sert qu'à une
        # estimation ponctuelle hors flux normal.
        return max(len(text) // 4, 1)

    def get_model_name(self) -> str:
        return self.model

    def is_available(self) -> bool:
        return bool(self._api_key and self._api_key.startswith("gsk_") and len(self._api_key) > 20)


class LLMProviderFactory:
    """
    Factory pour créer le LLM provider (Groq uniquement).

    Une clé Groq manquante ou invalide lève une erreur explicite - pas de
    fallback silencieux vers un mock.
    """

    _instance: Optional[BaseLLMProvider] = None

    @classmethod
    def get_provider(cls, force_new: bool = False) -> BaseLLMProvider:
        """
        Retourne une instance du LLM provider Groq.

        Args:
            force_new: Si True, crée une nouvelle instance

        Returns:
            Instance de GroqLLMProvider

        Raises:
            RuntimeError: si LLM_GROQ_API_KEY (ou GROQ_API_KEY) est absente
                ou ne ressemble pas à une vraie clé Groq.
        """
        if cls._instance is not None and not force_new:
            return cls._instance

        cls._instance = cls._create_groq_provider()
        return cls._instance

    @classmethod
    def _create_groq_provider(cls) -> BaseLLMProvider:
        """Crée un GroqLLMProvider. Lève une erreur si la clé est absente/invalide."""
        api_key = os.getenv("LLM_GROQ_API_KEY") or os.getenv("GROQ_API_KEY")

        if not api_key:
            api_key = getattr(settings.llm, "groq_api_key", None)

        if not api_key or not cls._is_valid_groq_key(api_key):
            raise RuntimeError(
                "LLM_GROQ_API_KEY manquante ou invalide. Ce projet n'utilise que Groq "
                "comme LLM (pas de mock, pas de fallback silencieux) - obtenez une clé "
                "gratuite sur https://console.groq.com/keys et configurez-la via "
                "LLM_GROQ_API_KEY dans le .env."
            )

        model = getattr(settings.llm, "groq_model", "openai/gpt-oss-120b")
        provider = GroqLLMProvider(api_key=api_key, model=model)
        logger.info(f"Groq provider initialized with model: {model}")
        return provider

    @classmethod
    def _is_valid_groq_key(cls, key: str) -> bool:
        """Vérifie si une clé Groq semble valide."""
        if not key:
            return False
        if key.startswith("gsk_fake") or key.startswith("gsk_test"):
            return False
        if not key.startswith("gsk_") or len(key) < 20:
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
