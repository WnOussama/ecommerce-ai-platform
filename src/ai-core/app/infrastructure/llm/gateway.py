"""
Circuit Breaker & LLM Gateway - Résilience et gestion des fallbacks
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# CIRCUIT BREAKER
# ============================================================================


class CircuitState(str, Enum):
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration du circuit breaker"""

    failure_threshold: int = 5  # Nombre d'échecs avant ouverture
    success_threshold: int = 2  # Succès requis pour fermer depuis half-open
    timeout_seconds: int = 30  # Temps avant passage en half-open
    half_open_max_calls: int = 3  # Appels max en half-open


@dataclass
class CircuitBreakerState:
    """État du circuit breaker"""

    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: Optional[datetime] = None
    last_state_change: datetime = field(default_factory=datetime.utcnow)
    half_open_calls: int = 0


class CircuitBreaker:
    """
    Circuit Breaker pour protéger contre les services défaillants.

    États:
    - CLOSED: Fonctionnement normal, comptage des erreurs
    - OPEN: Service défaillant, rejette les requêtes immédiatement
    - HALF_OPEN: Test de récupération avec quelques requêtes
    """

    def __init__(self, name: str, config: CircuitBreakerConfig = None):
        self.name = name
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitBreakerState()
        self._lock = asyncio.Lock()

    @property
    def is_closed(self) -> bool:
        return self.state.state == CircuitState.CLOSED

    @property
    def is_open(self) -> bool:
        return self.state.state == CircuitState.OPEN

    async def can_execute(self) -> bool:
        """Vérifie si une requête peut être exécutée"""
        async with self._lock:
            if self.state.state == CircuitState.CLOSED:
                return True

            if self.state.state == CircuitState.OPEN:
                # Vérifier si timeout écoulé
                if self._should_attempt_reset():
                    self._transition_to_half_open()
                    return True
                return False

            if self.state.state == CircuitState.HALF_OPEN:
                if self.state.half_open_calls < self.config.half_open_max_calls:
                    self.state.half_open_calls += 1
                    return True
                return False

            return False

    async def record_success(self):
        """Enregistre un succès"""
        async with self._lock:
            if self.state.state == CircuitState.HALF_OPEN:
                self.state.success_count += 1
                if self.state.success_count >= self.config.success_threshold:
                    self._transition_to_closed()
            elif self.state.state == CircuitState.CLOSED:
                # Reset failure count on success
                self.state.failure_count = 0

    async def record_failure(self, error: Exception = None):
        """Enregistre un échec"""
        async with self._lock:
            self.state.failure_count += 1
            self.state.last_failure_time = datetime.utcnow()

            logger.warning(
                f"Circuit breaker '{self.name}' recorded failure",
                extra={
                    "failure_count": self.state.failure_count,
                    "threshold": self.config.failure_threshold,
                    "error": str(error) if error else None,
                },
            )

            if self.state.state == CircuitState.HALF_OPEN:
                # Échec pendant test = retour à OPEN
                self._transition_to_open()
            elif self.state.state == CircuitState.CLOSED:
                if self.state.failure_count >= self.config.failure_threshold:
                    self._transition_to_open()

    def _should_attempt_reset(self) -> bool:
        """Vérifie si on doit tenter une récupération"""
        if not self.state.last_failure_time:
            return True

        elapsed = datetime.utcnow() - self.state.last_failure_time
        return elapsed.total_seconds() >= self.config.timeout_seconds

    def _transition_to_open(self):
        """Passage à l'état OPEN"""
        logger.error(f"Circuit breaker '{self.name}' OPENED")
        self.state.state = CircuitState.OPEN
        self.state.last_state_change = datetime.utcnow()
        self.state.success_count = 0
        self.state.half_open_calls = 0

    def _transition_to_half_open(self):
        """Passage à l'état HALF_OPEN"""
        logger.info(f"Circuit breaker '{self.name}' entering HALF_OPEN")
        self.state.state = CircuitState.HALF_OPEN
        self.state.last_state_change = datetime.utcnow()
        self.state.success_count = 0
        self.state.half_open_calls = 0

    def _transition_to_closed(self):
        """Passage à l'état CLOSED"""
        logger.info(f"Circuit breaker '{self.name}' CLOSED (recovered)")
        self.state.state = CircuitState.CLOSED
        self.state.last_state_change = datetime.utcnow()
        self.state.failure_count = 0
        self.state.success_count = 0
        self.state.half_open_calls = 0

    def get_status(self) -> Dict[str, Any]:
        """Retourne le statut du circuit breaker"""
        return {
            "name": self.name,
            "state": self.state.state.value,
            "failure_count": self.state.failure_count,
            "success_count": self.state.success_count,
            "last_failure": self.state.last_failure_time.isoformat()
            if self.state.last_failure_time
            else None,
            "last_state_change": self.state.last_state_change.isoformat(),
        }


class CircuitBreakerError(Exception):
    """Erreur levée quand le circuit est ouvert"""

    pass


# ============================================================================
# LLM GATEWAY
# ============================================================================


@dataclass
class LLMProviderConfig:
    """Configuration d'un provider LLM"""

    name: str
    priority: int = 0  # Plus bas = plus prioritaire
    is_enabled: bool = True
    max_retries: int = 3
    timeout_seconds: int = 30
    cost_multiplier: float = 1.0  # Pour tracking coûts
    circuit_breaker_config: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)


class LLMGateway:
    """
    Gateway LLM avec:
    - Fallback automatique entre providers
    - Circuit breaker par provider
    - Retry avec exponential backoff
    - Semantic caching
    - Cost tracking
    """

    def __init__(
        self,
        providers: Dict[str, Any],  # name -> LLMProvider instance
        configs: Dict[str, LLMProviderConfig] = None,
        cache=None,  # Redis pour semantic cache
        metrics_collector=None,  # Prometheus metrics
    ):
        self.providers = providers
        self.configs = configs or {}
        self.cache = cache
        self.metrics = metrics_collector

        # Circuit breakers par provider
        self.circuit_breakers: Dict[str, CircuitBreaker] = {}

        for name, config in self.configs.items():
            self.circuit_breakers[name] = CircuitBreaker(
                name=f"llm_{name}", config=config.circuit_breaker_config
            )

        # Ordre de fallback (trié par priorité)
        self._update_fallback_order()

    def _update_fallback_order(self):
        """Met à jour l'ordre de fallback basé sur les priorités"""
        enabled_configs = [
            (name, config) for name, config in self.configs.items() if config.is_enabled
        ]
        self.fallback_order = [
            name for name, _ in sorted(enabled_configs, key=lambda x: x[1].priority)
        ]

    async def generate(
        self,
        messages: List[Dict[str, str]],
        tenant_id: str,
        config: Dict[str, Any] = None,
        use_cache: bool = True,
        cache_ttl: int = 3600,
    ) -> Dict[str, Any]:
        """
        Génère une réponse avec fallback automatique.

        Returns:
            {
                "content": str,
                "provider": str,
                "model": str,
                "usage": {...},
                "latency_ms": int,
                "from_cache": bool
            }
        """
        config = config or {}

        # Vérifier le cache sémantique
        if use_cache and self.cache:
            cached = await self._check_cache(messages, config)
            if cached:
                logger.debug("LLM response served from cache")
                return {**cached, "from_cache": True}

        # Essayer les providers dans l'ordre
        last_error = None

        for provider_name in self.fallback_order:
            provider = self.providers.get(provider_name)
            circuit_breaker = self.circuit_breakers.get(provider_name)
            provider_config = self.configs.get(provider_name)

            if not provider:
                continue

            # Vérifier le circuit breaker
            if circuit_breaker and not await circuit_breaker.can_execute():
                logger.debug(f"Provider {provider_name} circuit is open, skipping")
                continue

            try:
                # Appel avec retry
                result = await self._call_with_retry(
                    provider=provider,
                    provider_name=provider_name,
                    messages=messages,
                    config=config,
                    max_retries=provider_config.max_retries if provider_config else 3,
                    timeout=provider_config.timeout_seconds if provider_config else 30,
                )

                # Succès
                if circuit_breaker:
                    await circuit_breaker.record_success()

                # Mettre en cache
                if use_cache and self.cache:
                    await self._store_cache(messages, config, result, cache_ttl)

                # Métriques
                if self.metrics:
                    self.metrics.record_llm_success(
                        provider=provider_name,
                        latency_ms=result.get("latency_ms", 0),
                        tokens=result.get("usage", {}).get("total_tokens", 0),
                    )

                return {**result, "from_cache": False}

            except Exception as e:
                last_error = e
                logger.warning(f"Provider {provider_name} failed", extra={"error": str(e)})

                if circuit_breaker:
                    await circuit_breaker.record_failure(e)

                if self.metrics:
                    self.metrics.record_llm_failure(provider=provider_name)

                # Continuer vers le prochain provider
                continue

        # Tous les providers ont échoué
        raise LLMGatewayError(f"All LLM providers failed. Last error: {last_error}")

    async def _call_with_retry(
        self,
        provider,
        provider_name: str,
        messages: List[Dict[str, str]],
        config: Dict[str, Any],
        max_retries: int,
        timeout: int,
    ) -> Dict[str, Any]:
        """Appelle un provider avec retry et timeout"""

        for attempt in range(max_retries):
            try:
                start_time = time.time()

                # Appel avec timeout
                response = await asyncio.wait_for(
                    provider.generate(messages, config), timeout=timeout
                )

                latency_ms = int((time.time() - start_time) * 1000)

                return {
                    "content": response.content,
                    "provider": provider_name,
                    "model": response.model,
                    "usage": {
                        "input_tokens": response.usage.input_tokens,
                        "output_tokens": response.usage.output_tokens,
                        "total_tokens": response.usage.total_tokens,
                    },
                    "latency_ms": latency_ms,
                    "finish_reason": response.finish_reason,
                }

            except asyncio.TimeoutError:
                logger.warning(
                    f"Provider {provider_name} timeout (attempt {attempt + 1}/{max_retries})"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(2**attempt)  # Exponential backoff
                else:
                    raise

            except Exception as e:
                logger.warning(
                    f"Provider {provider_name} error (attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(2**attempt)
                else:
                    raise

    async def _check_cache(
        self, messages: List[Dict[str, str]], config: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Vérifie si une réponse est en cache"""
        cache_key = self._compute_cache_key(messages, config)

        cached = await self.cache.get(f"llm_cache:{cache_key}")
        if cached:
            import json

            return json.loads(cached)
        return None

    async def _store_cache(
        self,
        messages: List[Dict[str, str]],
        config: Dict[str, Any],
        result: Dict[str, Any],
        ttl: int,
    ):
        """Stocke une réponse en cache"""
        cache_key = self._compute_cache_key(messages, config)

        import json

        await self.cache.set(f"llm_cache:{cache_key}", json.dumps(result), ex=ttl)

    def _compute_cache_key(self, messages: List[Dict[str, str]], config: Dict[str, Any]) -> str:
        """Calcule une clé de cache déterministe"""
        import hashlib
        import json

        # Exclure les éléments non-déterministes
        cache_input = {
            "messages": messages,
            "temperature": config.get("temperature", 0.7),
            "max_tokens": config.get("max_tokens", 1000),
        }

        content = json.dumps(cache_input, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:32]

    def get_status(self) -> Dict[str, Any]:
        """Retourne le statut de tous les providers"""
        return {
            "providers": {
                name: {
                    "enabled": self.configs.get(name, LLMProviderConfig(name=name)).is_enabled,
                    "circuit_breaker": cb.get_status()
                    if (cb := self.circuit_breakers.get(name))
                    else None,
                }
                for name in self.providers.keys()
            },
            "fallback_order": self.fallback_order,
        }

    async def disable_provider(self, name: str):
        """Désactive un provider"""
        if name in self.configs:
            self.configs[name].is_enabled = False
            self._update_fallback_order()
            logger.info(f"Provider {name} disabled")

    async def enable_provider(self, name: str):
        """Réactive un provider"""
        if name in self.configs:
            self.configs[name].is_enabled = True
            # Reset circuit breaker
            if name in self.circuit_breakers:
                self.circuit_breakers[name].state = CircuitBreakerState()
            self._update_fallback_order()
            logger.info(f"Provider {name} enabled")


class LLMGatewayError(Exception):
    """Erreur du gateway LLM"""

    pass


# ============================================================================
# COST LIMITER
# ============================================================================


class CostLimiter:
    """
    Limiteur de coûts LLM par tenant.
    Évite les dépassements de budget.
    """

    def __init__(self, cache, default_daily_limit: float = 50.0):
        self.cache = cache
        self.default_daily_limit = default_daily_limit

        # Limites par plan
        self.plan_limits = {"starter": 10.0, "professional": 50.0, "enterprise": 200.0}

    async def check_budget(
        self, tenant_id: str, plan: str = "starter", estimated_cost: float = 0.05
    ) -> bool:
        """
        Vérifie si le tenant peut effectuer l'appel.

        Returns:
            True si autorisé, False si budget dépassé
        """
        limit = self.plan_limits.get(plan, self.default_daily_limit)

        date_key = datetime.utcnow().strftime("%Y-%m-%d")
        cache_key = f"llm_cost:{tenant_id}:{date_key}"

        current_cost = await self.cache.get(cache_key)
        current_cost = float(current_cost) if current_cost else 0.0

        if current_cost + estimated_cost > limit:
            logger.warning(
                f"Budget limit reached for tenant {tenant_id}",
                extra={"current_cost": current_cost, "limit": limit, "plan": plan},
            )
            return False

        return True

    async def record_cost(self, tenant_id: str, cost: float):
        """Enregistre un coût"""
        date_key = datetime.utcnow().strftime("%Y-%m-%d")
        cache_key = f"llm_cost:{tenant_id}:{date_key}"

        # Increment avec expiration à minuit
        await self.cache.incrbyfloat(cache_key, cost)

        # TTL jusqu'à la fin de la journée
        ttl = 86400 - (datetime.utcnow().hour * 3600 + datetime.utcnow().minute * 60)
        await self.cache.expire(cache_key, ttl)

    async def get_usage(self, tenant_id: str) -> Dict[str, float]:
        """Récupère l'usage du tenant"""
        date_key = datetime.utcnow().strftime("%Y-%m-%d")
        cache_key = f"llm_cost:{tenant_id}:{date_key}"

        current_cost = await self.cache.get(cache_key)
        current_cost = float(current_cost) if current_cost else 0.0

        return {"date": date_key, "cost_usd": current_cost}
