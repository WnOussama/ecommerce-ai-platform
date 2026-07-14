"""
Service LLM - Abstraction multi-provider avec fallback, retry, et cost tracking
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

import tiktoken
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from app.core.config.settings import settings
from app.domain.entities.models import IntentType, LLMUsage

logger = logging.getLogger(__name__)


# ============================================================================
# TYPES
# ============================================================================


@dataclass
class LLMResponse:
    """Réponse standardisée du LLM"""

    content: str
    usage: LLMUsage
    model: str
    finish_reason: str
    latency_ms: int


@dataclass
class LLMConfig:
    """Configuration pour un appel LLM"""

    temperature: float = 0.7
    max_tokens: int = 1000
    top_p: float = 1.0
    frequency_penalty: float = 0.0
    presence_penalty: float = 0.0
    stop: Optional[List[str]] = None


# ============================================================================
# PROVIDER INTERFACE
# ============================================================================


class LLMProvider(ABC):
    """Interface abstraite pour les providers LLM"""

    @abstractmethod
    async def generate(self, messages: List[Dict[str, str]], config: LLMConfig) -> LLMResponse:
        """Génère une réponse à partir des messages"""
        pass

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """Compte les tokens d'un texte"""
        pass

    @abstractmethod
    def get_model_name(self) -> str:
        """Retourne le nom du modèle"""
        pass


# ============================================================================
# OPENAI PROVIDER
# ============================================================================


class OpenAIProvider(LLMProvider):
    """Provider OpenAI avec support GPT-4"""

    def __init__(self, api_key: str, model: str = "gpt-4-turbo-preview"):
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model
        self._encoder = tiktoken.encoding_for_model("gpt-4")

    async def generate(self, messages: List[Dict[str, str]], config: LLMConfig) -> LLMResponse:
        start_time = time.time()

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            top_p=config.top_p,
            frequency_penalty=config.frequency_penalty,
            presence_penalty=config.presence_penalty,
            stop=config.stop,
        )

        latency_ms = int((time.time() - start_time) * 1000)

        usage = LLMUsage(
            input_tokens=response.usage.prompt_tokens,
            output_tokens=response.usage.completion_tokens,
            model=self.model,
        )

        return LLMResponse(
            content=response.choices[0].message.content,
            usage=usage,
            model=self.model,
            finish_reason=response.choices[0].finish_reason,
            latency_ms=latency_ms,
        )

    def count_tokens(self, text: str) -> int:
        return len(self._encoder.encode(text))

    def get_model_name(self) -> str:
        return self.model


# ============================================================================
# ANTHROPIC PROVIDER
# ============================================================================


class AnthropicProvider(LLMProvider):
    """Provider Anthropic avec support Claude"""

    def __init__(self, api_key: str, model: str = "claude-3-sonnet-20240229"):
        self.client = AsyncAnthropic(api_key=api_key)
        self.model = model

    async def generate(self, messages: List[Dict[str, str]], config: LLMConfig) -> LLMResponse:
        start_time = time.time()

        # Convertir le format OpenAI vers Anthropic
        system_message = ""
        formatted_messages = []

        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                formatted_messages.append({"role": msg["role"], "content": msg["content"]})

        response = await self.client.messages.create(
            model=self.model,
            max_tokens=config.max_tokens,
            system=system_message,
            messages=formatted_messages,
        )

        latency_ms = int((time.time() - start_time) * 1000)

        usage = LLMUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            model=self.model,
        )

        return LLMResponse(
            content=response.content[0].text,
            usage=usage,
            model=self.model,
            finish_reason=response.stop_reason,
            latency_ms=latency_ms,
        )

    def count_tokens(self, text: str) -> int:
        # Estimation approximative pour Claude
        return len(text.split()) * 1.3

    def get_model_name(self) -> str:
        return self.model


# ============================================================================
# LLM SERVICE PRINCIPAL
# ============================================================================


class LLMService:
    """
    Service LLM principal avec:
    - Support multi-provider
    - Fallback automatique
    - Retry avec exponential backoff
    - Rate limiting par tenant
    - Cost tracking
    - Métriques et logging
    """

    def __init__(
        self,
        primary_provider: LLMProvider,
        fallback_provider: Optional[LLMProvider] = None,
        rate_limiter=None,  # RedisRateLimiter
        cost_tracker=None,  # CostTracker
    ):
        self.primary = primary_provider
        self.fallback = fallback_provider
        self.rate_limiter = rate_limiter
        self.cost_tracker = cost_tracker

        self.default_config = LLMConfig(
            temperature=settings.llm.temperature, max_tokens=settings.llm.max_tokens
        )

    async def generate(
        self,
        messages: List[Dict[str, str]],
        tenant_id: UUID,
        intent: Optional[IntentType] = None,
        config: Optional[LLMConfig] = None,
    ) -> LLMResponse:
        """
        Génère une réponse avec gestion complète des erreurs.
        """
        config = config or self.default_config

        # Rate limiting
        if self.rate_limiter:
            allowed = await self.rate_limiter.check(str(tenant_id), "llm_request")
            if not allowed:
                raise RateLimitExceeded(f"Rate limit exceeded for tenant {tenant_id}")

        # Tentative avec retry
        response = await self._generate_with_retry(messages, config)

        # Cost tracking
        if self.cost_tracker:
            await self.cost_tracker.record(tenant_id=tenant_id, usage=response.usage, intent=intent)

        # Métriques
        self._record_metrics(response, tenant_id, intent)

        return response

    async def _generate_with_retry(
        self, messages: List[Dict[str, str]], config: LLMConfig, max_retries: int = 3
    ) -> LLMResponse:
        """Génère avec retry et fallback"""

        last_error = None

        # Essayer le provider principal
        for attempt in range(max_retries):
            try:
                return await self.primary.generate(messages, config)
            except Exception as e:
                last_error = e
                logger.warning(
                    f"LLM primary provider failed (attempt {attempt + 1}/{max_retries}): {e}"
                )

                if attempt < max_retries - 1:
                    await asyncio.sleep(2**attempt)  # Exponential backoff

        # Fallback vers provider secondaire
        if self.fallback:
            logger.info("Falling back to secondary LLM provider")
            try:
                return await self.fallback.generate(messages, config)
            except Exception as e:
                logger.error(f"LLM fallback provider also failed: {e}")
                raise LLMError(f"All LLM providers failed. Last error: {last_error}")

        raise LLMError(f"LLM generation failed after {max_retries} retries: {last_error}")

    async def generate_with_function_calling(
        self, messages: List[Dict[str, str]], functions: List[Dict[str, Any]], tenant_id: UUID
    ) -> Dict[str, Any]:
        """Génération avec function calling pour actions structurées"""
        # Implémentation spécifique OpenAI function calling
        pass

    async def generate_embedding(self, text: str, tenant_id: UUID) -> List[float]:
        """Génère un embedding pour un texte"""
        if not isinstance(self.primary, OpenAIProvider):
            raise NotImplementedError("Embeddings only supported with OpenAI")

        # Rate limiting
        if self.rate_limiter:
            await self.rate_limiter.check(str(tenant_id), "embedding_request")

        response = await self.primary.client.embeddings.create(
            model=settings.llm.openai_embedding_model, input=text
        )

        return response.data[0].embedding

    async def generate_strategy(
        self,
        objective: str,
        analytics: Dict[str, Any],
        segments: Dict[str, Any],
        tenant_settings: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Génère une stratégie marketing structurée"""

        system_prompt = """Tu es un expert en marketing e-commerce.
Analyse les données fournies et génère une stratégie marketing détaillée.

DONNÉES ANALYTICS:
{analytics}

SEGMENTS CLIENTS:
{segments}

OBJECTIF:
{objective}

Génère une réponse JSON structurée avec:
{{
    "strategy_name": "Nom de la stratégie",
    "executive_summary": "Résumé exécutif",
    "target_segments": ["segment1", "segment2"],
    "actions": [
        {{
            "action_type": "type",
            "description": "description",
            "priority": "high/medium/low",
            "estimated_impact": "description",
            "implementation_steps": ["step1", "step2"]
        }}
    ],
    "kpis_to_track": ["kpi1", "kpi2"],
    "timeline": "durée estimée",
    "estimated_roi": "estimation"
}}"""

        messages = [
            {
                "role": "system",
                "content": system_prompt.format(
                    analytics=str(analytics), segments=str(segments), objective=objective
                ),
            },
            {"role": "user", "content": f"Génère une stratégie pour: {objective}"},
        ]

        config = LLMConfig(temperature=0.3, max_tokens=2000)
        response = await self._generate_with_retry(messages, config)

        # Parser la réponse JSON
        import json

        try:
            return json.loads(response.content)
        except json.JSONDecodeError:
            # Fallback si pas de JSON valide
            return {"strategy_name": "Stratégie générée", "raw_response": response.content}

    def _record_metrics(self, response: LLMResponse, tenant_id: UUID, intent: Optional[IntentType]):
        """Enregistre les métriques pour monitoring"""
        # Implémentation avec Prometheus
        logger.info(
            "LLM request completed",
            extra={
                "tenant_id": str(tenant_id),
                "model": response.model,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "latency_ms": response.latency_ms,
                "intent": intent.value if intent else None,
            },
        )


# ============================================================================
# EXCEPTIONS
# ============================================================================


class LLMError(Exception):
    """Erreur générique LLM"""

    pass


class RateLimitExceeded(LLMError):
    """Rate limit dépassé"""

    pass


# ============================================================================
# COST TRACKER
# ============================================================================


class CostTracker:
    """
    Suivi des coûts LLM par tenant.
    Critique pour la facturation et les alertes.
    """

    def __init__(self, redis_client, db_repository):
        self.redis = redis_client
        self.db = db_repository

        # Coûts par modèle (par 1000 tokens)
        self.costs = {
            "gpt-4-turbo-preview": {"input": 0.01, "output": 0.03},
            "gpt-4": {"input": 0.03, "output": 0.06},
            "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
            "claude-3-sonnet-20240229": {"input": 0.003, "output": 0.015},
            "claude-3-opus-20240229": {"input": 0.015, "output": 0.075},
        }

    async def record(self, tenant_id: UUID, usage: LLMUsage, intent: Optional[IntentType] = None):
        """Enregistre l'usage et le coût"""

        model_costs = self.costs.get(usage.model, {"input": 0.01, "output": 0.03})
        cost = usage.calculate_cost(model_costs["input"], model_costs["output"])

        # Incrémenter le compteur Redis pour le suivi temps réel
        date_key = datetime.utcnow().strftime("%Y-%m-%d")
        await self.redis.incrbyfloat(f"llm_cost:{tenant_id}:{date_key}", cost)
        await self.redis.incrby(f"llm_tokens:{tenant_id}:{date_key}", usage.total_tokens)

        # Vérifier les alertes de budget
        await self._check_budget_alerts(tenant_id, date_key)

        # Enregistrement détaillé en base (async)
        # await self.db.record_usage(tenant_id, usage, cost, intent)

    async def get_daily_cost(self, tenant_id: UUID, date: str = None) -> float:
        """Récupère le coût journalier"""
        date_key = date or datetime.utcnow().strftime("%Y-%m-%d")
        cost = await self.redis.get(f"llm_cost:{tenant_id}:{date_key}")
        return float(cost) if cost else 0.0

    async def get_monthly_cost(self, tenant_id: UUID) -> float:
        """Récupère le coût mensuel"""
        # Agrégation des coûts journaliers
        pass

    async def _check_budget_alerts(self, tenant_id: UUID, date_key: str):
        """Vérifie si le budget est dépassé et envoie des alertes"""
        daily_cost = await self.get_daily_cost(tenant_id, date_key)

        # Seuils d'alerte (à configurer par tenant)
        warning_threshold = 10.0  # $10/day
        critical_threshold = 50.0  # $50/day

        if daily_cost >= critical_threshold:
            logger.critical(
                f"CRITICAL: Tenant {tenant_id} LLM cost exceeded ${critical_threshold}",
                extra={"tenant_id": str(tenant_id), "daily_cost": daily_cost},
            )
            # Envoyer alerte
        elif daily_cost >= warning_threshold:
            logger.warning(
                f"WARNING: Tenant {tenant_id} LLM cost approaching limit",
                extra={"tenant_id": str(tenant_id), "daily_cost": daily_cost},
            )
