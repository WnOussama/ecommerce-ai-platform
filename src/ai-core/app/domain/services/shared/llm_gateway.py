"""
LLM Gateway - Service partagé d'accès aux LLMs
Supporte OpenAI et Anthropic avec retry, fallback et cost tracking
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List, AsyncGenerator
from uuid import UUID, uuid4
from enum import Enum
import logging
import asyncio

logger = logging.getLogger(__name__)


# =============================================================================
# TYPES
# =============================================================================

class LLMProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class ResponseFormat(str, Enum):
    """Format de réponse attendu"""
    TEXT = "text"           # Texte libre (Client Agent)
    JSON = "json"           # JSON structuré (Admin Agent)
    STREAMING = "streaming" # Streaming pour chat temps réel


@dataclass
class LLMRequest:
    """Requête vers le LLM"""
    messages: List[Dict[str, str]]
    model: Optional[str] = None
    temperature: float = 0.7
    max_tokens: int = 1000
    response_format: ResponseFormat = ResponseFormat.TEXT
    json_schema: Optional[Dict[str, Any]] = None  # Pour validation JSON

    # Métadonnées pour tracking
    tenant_id: Optional[str] = None
    request_id: str = field(default_factory=lambda: str(uuid4()))
    agent_type: str = "unknown"  # "client" ou "admin"


@dataclass
class LLMResponse:
    """Réponse du LLM"""
    content: str
    model: str
    provider: LLMProvider

    # Métriques
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    latency_ms: int = 0

    # Coût estimé
    cost_usd: float = 0.0

    # Métadonnées
    request_id: str = ""
    finish_reason: str = ""

    # Pour réponses JSON
    parsed_json: Optional[Dict[str, Any]] = None
    json_valid: bool = True


@dataclass
class LLMUsage:
    """Usage cumulé pour tracking"""
    tenant_id: str
    period: str  # "daily", "monthly"
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    request_count: int = 0


# =============================================================================
# COST CALCULATOR
# =============================================================================

class CostCalculator:
    """Calcul des coûts par modèle"""

    # Prix par 1000 tokens (USD) - Mis à jour régulièrement
    PRICING = {
        # OpenAI
        "gpt-4-turbo-preview": {"input": 0.01, "output": 0.03},
        "gpt-4-turbo": {"input": 0.01, "output": 0.03},
        "gpt-4": {"input": 0.03, "output": 0.06},
        "gpt-4o": {"input": 0.005, "output": 0.015},
        "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
        "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},

        # Anthropic
        "claude-3-opus-20240229": {"input": 0.015, "output": 0.075},
        "claude-3-sonnet-20240229": {"input": 0.003, "output": 0.015},
        "claude-3-haiku-20240307": {"input": 0.00025, "output": 0.00125},
        "claude-3-5-sonnet-20240620": {"input": 0.003, "output": 0.015},

        # Embeddings
        "text-embedding-3-small": {"input": 0.00002, "output": 0.0},
        "text-embedding-3-large": {"input": 0.00013, "output": 0.0},
    }

    @classmethod
    def calculate(cls, model: str, input_tokens: int, output_tokens: int) -> float:
        """Calcule le coût en USD"""
        pricing = cls.PRICING.get(model, {"input": 0.01, "output": 0.03})
        cost = (input_tokens * pricing["input"] / 1000) + \
               (output_tokens * pricing["output"] / 1000)
        return round(cost, 6)


# =============================================================================
# LLM CLIENT INTERFACE
# =============================================================================

class BaseLLMClient(ABC):
    """Interface abstraite pour les clients LLM"""

    @abstractmethod
    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Génère une réponse"""
        pass

    @abstractmethod
    async def generate_stream(
        self, request: LLMRequest
    ) -> AsyncGenerator[str, None]:
        """Génère une réponse en streaming"""
        pass

    @abstractmethod
    async def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Génère des embeddings"""
        pass


# =============================================================================
# OPENAI CLIENT
# =============================================================================

class OpenAIClient(BaseLLMClient):
    """Client OpenAI"""

    def __init__(self, api_key: str, default_model: str = "gpt-4-turbo-preview"):
        self.api_key = api_key
        self.default_model = default_model
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=self.api_key)
        return self._client

    async def generate(self, request: LLMRequest) -> LLMResponse:
        import time
        start_time = time.perf_counter()

        client = self._get_client()
        model = request.model or self.default_model

        # Préparer les paramètres
        params = {
            "model": model,
            "messages": request.messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }

        # JSON mode si demandé
        if request.response_format == ResponseFormat.JSON:
            params["response_format"] = {"type": "json_object"}

        try:
            response = await client.chat.completions.create(**params)

            latency_ms = int((time.perf_counter() - start_time) * 1000)

            content = response.choices[0].message.content or ""
            input_tokens = response.usage.prompt_tokens
            output_tokens = response.usage.completion_tokens

            # Parser JSON si demandé
            parsed_json = None
            json_valid = True
            if request.response_format == ResponseFormat.JSON:
                import json
                try:
                    parsed_json = json.loads(content)
                except json.JSONDecodeError:
                    json_valid = False
                    logger.warning(f"Invalid JSON response: {content[:100]}")

            return LLMResponse(
                content=content,
                model=model,
                provider=LLMProvider.OPENAI,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                latency_ms=latency_ms,
                cost_usd=CostCalculator.calculate(model, input_tokens, output_tokens),
                request_id=request.request_id,
                finish_reason=response.choices[0].finish_reason,
                parsed_json=parsed_json,
                json_valid=json_valid,
            )

        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            raise

    async def generate_stream(
        self, request: LLMRequest
    ) -> AsyncGenerator[str, None]:
        client = self._get_client()
        model = request.model or self.default_model

        stream = await client.chat.completions.create(
            model=model,
            messages=request.messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            stream=True,
        )

        async for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    async def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        client = self._get_client()

        response = await client.embeddings.create(
            model="text-embedding-3-small",
            input=texts,
        )

        return [item.embedding for item in response.data]


# =============================================================================
# ANTHROPIC CLIENT
# =============================================================================

class AnthropicClient(BaseLLMClient):
    """Client Anthropic Claude"""

    def __init__(self, api_key: str, default_model: str = "claude-3-sonnet-20240229"):
        self.api_key = api_key
        self.default_model = default_model
        self._client = None

    def _get_client(self):
        if self._client is None:
            from anthropic import AsyncAnthropic
            self._client = AsyncAnthropic(api_key=self.api_key)
        return self._client

    async def generate(self, request: LLMRequest) -> LLMResponse:
        import time
        start_time = time.perf_counter()

        client = self._get_client()
        model = request.model or self.default_model

        # Convertir format messages (OpenAI -> Anthropic)
        system_message = ""
        messages = []
        for msg in request.messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                messages.append({
                    "role": msg["role"],
                    "content": msg["content"]
                })

        try:
            response = await client.messages.create(
                model=model,
                max_tokens=request.max_tokens,
                system=system_message,
                messages=messages,
            )

            latency_ms = int((time.perf_counter() - start_time) * 1000)

            content = response.content[0].text
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens

            # Parser JSON si demandé
            parsed_json = None
            json_valid = True
            if request.response_format == ResponseFormat.JSON:
                import json
                try:
                    parsed_json = json.loads(content)
                except json.JSONDecodeError:
                    json_valid = False

            return LLMResponse(
                content=content,
                model=model,
                provider=LLMProvider.ANTHROPIC,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                latency_ms=latency_ms,
                cost_usd=CostCalculator.calculate(model, input_tokens, output_tokens),
                request_id=request.request_id,
                finish_reason=response.stop_reason,
                parsed_json=parsed_json,
                json_valid=json_valid,
            )

        except Exception as e:
            logger.error(f"Anthropic API error: {e}")
            raise

    async def generate_stream(
        self, request: LLMRequest
    ) -> AsyncGenerator[str, None]:
        client = self._get_client()
        model = request.model or self.default_model

        system_message = ""
        messages = []
        for msg in request.messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                messages.append({"role": msg["role"], "content": msg["content"]})

        async with client.messages.stream(
            model=model,
            max_tokens=request.max_tokens,
            system=system_message,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text

    async def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        # Anthropic n'a pas d'API embeddings native
        # On utilise OpenAI pour les embeddings même avec Claude
        raise NotImplementedError("Use OpenAI for embeddings")


# =============================================================================
# LLM GATEWAY (MAIN ENTRY POINT)
# =============================================================================

class LLMGateway:
    """
    Gateway principal pour les LLMs.
    Gère:
    - Sélection du provider
    - Retry avec fallback
    - Cost tracking
    - Rate limiting
    """

    def __init__(
        self,
        openai_api_key: Optional[str] = None,
        anthropic_api_key: Optional[str] = None,
        primary_provider: LLMProvider = LLMProvider.OPENAI,
        enable_fallback: bool = True,
        max_retries: int = 3,
    ):
        self.primary_provider = primary_provider
        self.enable_fallback = enable_fallback
        self.max_retries = max_retries

        # Initialiser les clients
        self._clients: Dict[LLMProvider, BaseLLMClient] = {}

        if openai_api_key:
            self._clients[LLMProvider.OPENAI] = OpenAIClient(openai_api_key)

        if anthropic_api_key:
            self._clients[LLMProvider.ANTHROPIC] = AnthropicClient(anthropic_api_key)

        if not self._clients:
            raise ValueError("At least one LLM API key must be provided")

    async def generate(
        self,
        request: LLMRequest,
        provider: Optional[LLMProvider] = None,
    ) -> LLMResponse:
        """
        Génère une réponse avec retry et fallback.
        """
        target_provider = provider or self.primary_provider

        # Ordre des providers à essayer
        providers_to_try = [target_provider]
        if self.enable_fallback:
            for p in LLMProvider:
                if p != target_provider and p in self._clients:
                    providers_to_try.append(p)

        last_error = None

        for current_provider in providers_to_try:
            client = self._clients.get(current_provider)
            if not client:
                continue

            for attempt in range(self.max_retries):
                try:
                    response = await client.generate(request)

                    # Log usage
                    logger.info(
                        "LLM request completed",
                        extra={
                            "provider": current_provider.value,
                            "model": response.model,
                            "tokens": response.total_tokens,
                            "cost_usd": response.cost_usd,
                            "latency_ms": response.latency_ms,
                            "tenant_id": request.tenant_id,
                            "agent_type": request.agent_type,
                        }
                    )

                    return response

                except Exception as e:
                    last_error = e
                    logger.warning(
                        f"LLM request failed (attempt {attempt + 1}/{self.max_retries})",
                        extra={
                            "provider": current_provider.value,
                            "error": str(e),
                        }
                    )

                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(2 ** attempt)  # Exponential backoff

            logger.warning(f"Falling back from {current_provider.value}")

        raise RuntimeError(f"All LLM providers failed. Last error: {last_error}")

    async def generate_stream(
        self,
        request: LLMRequest,
        provider: Optional[LLMProvider] = None,
    ) -> AsyncGenerator[str, None]:
        """Génère une réponse en streaming"""
        target_provider = provider or self.primary_provider
        client = self._clients.get(target_provider)

        if not client:
            raise ValueError(f"Provider {target_provider.value} not configured")

        async for chunk in client.generate_stream(request):
            yield chunk

    async def generate_embeddings(
        self,
        texts: List[str],
        batch_size: int = 100,
    ) -> List[List[float]]:
        """Génère des embeddings (toujours via OpenAI)"""
        openai_client = self._clients.get(LLMProvider.OPENAI)
        if not openai_client:
            raise ValueError("OpenAI client required for embeddings")

        all_embeddings = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            embeddings = await openai_client.generate_embeddings(batch)
            all_embeddings.extend(embeddings)

        return all_embeddings

    def get_available_providers(self) -> List[LLMProvider]:
        """Retourne les providers disponibles"""
        return list(self._clients.keys())

