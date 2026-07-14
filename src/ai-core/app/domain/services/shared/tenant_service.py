"""
Tenant Service - Gestion multi-tenant partagée
Gère les plans, limites, features et configuration par tenant
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


# =============================================================================
# TYPES
# =============================================================================


class TenantPlan(str, Enum):
    """Plans disponibles"""

    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class TenantStatus(str, Enum):
    """Statut du tenant"""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    TRIAL = "trial"
    CANCELLED = "cancelled"


class Feature(str, Enum):
    """Features disponibles"""

    CHATBOT = "chatbot"
    FAQ = "faq"
    RECOMMENDATIONS = "recommendations"
    COUPONS = "coupons"
    ADMIN_AI = "admin_ai"
    ANALYTICS = "analytics"
    BULK_OPERATIONS = "bulk_operations"
    CUSTOM_PROMPTS = "custom_prompts"
    PRIORITY_SUPPORT = "priority_support"


# =============================================================================
# PLAN DEFINITIONS
# =============================================================================

PLAN_DEFINITIONS: Dict[TenantPlan, Dict[str, Any]] = {
    TenantPlan.STARTER: {
        "name": "Starter",
        "features": [Feature.CHATBOT, Feature.FAQ],
        "limits": {
            "conversations_per_day": 500,
            "products_indexed": 1000,
            "customers": 5000,
            "api_calls_per_minute": 30,
            "llm_tokens_per_day": 100000,
            "max_message_length": 1000,
        },
        "llm_config": {
            "model": "gpt-4o-mini",  # Modèle économique
            "max_tokens": 500,
            "temperature": 0.7,
        },
        "price_monthly_usd": 29,
    },
    TenantPlan.PROFESSIONAL: {
        "name": "Professional",
        "features": [Feature.CHATBOT, Feature.FAQ, Feature.RECOMMENDATIONS, Feature.COUPONS],
        "limits": {
            "conversations_per_day": 2000,
            "products_indexed": 10000,
            "customers": 50000,
            "api_calls_per_minute": 100,
            "llm_tokens_per_day": 500000,
            "max_message_length": 2000,
            "coupons_per_day": 100,
        },
        "llm_config": {
            "model": "gpt-4o",  # Modèle standard
            "max_tokens": 1000,
            "temperature": 0.7,
        },
        "price_monthly_usd": 99,
    },
    TenantPlan.ENTERPRISE: {
        "name": "Enterprise",
        "features": [
            Feature.CHATBOT,
            Feature.FAQ,
            Feature.RECOMMENDATIONS,
            Feature.COUPONS,
            Feature.ADMIN_AI,
            Feature.ANALYTICS,
            Feature.BULK_OPERATIONS,
            Feature.CUSTOM_PROMPTS,
            Feature.PRIORITY_SUPPORT,
        ],
        "limits": {
            "conversations_per_day": 10000,
            "products_indexed": 100000,
            "customers": -1,  # Illimité
            "api_calls_per_minute": 300,
            "llm_tokens_per_day": 2000000,
            "max_message_length": 4000,
            "coupons_per_day": 1000,
            "admin_commands_per_day": 50,
        },
        "llm_config": {
            "model": "gpt-4-turbo",  # Meilleur modèle
            "max_tokens": 2000,
            "temperature": 0.7,
        },
        "price_monthly_usd": 299,
    },
}


# =============================================================================
# DATA CLASSES
# =============================================================================


@dataclass
class TenantLimits:
    """Limites du tenant"""

    conversations_per_day: int = 500
    products_indexed: int = 1000
    customers: int = 5000
    api_calls_per_minute: int = 30
    llm_tokens_per_day: int = 100000
    max_message_length: int = 1000
    coupons_per_day: int = 0
    admin_commands_per_day: int = 0


@dataclass
class TenantLLMConfig:
    """Configuration LLM du tenant"""

    model: str = "gpt-4o-mini"
    max_tokens: int = 500
    temperature: float = 0.7
    fallback_model: Optional[str] = None


@dataclass
class TenantUsage:
    """Usage actuel du tenant"""

    tenant_id: str
    period: str  # "daily" ou "monthly"

    conversations: int = 0
    api_calls: int = 0
    llm_tokens: int = 0
    coupons_generated: int = 0
    admin_commands: int = 0

    # Coûts
    llm_cost_usd: float = 0.0

    # Timestamps
    period_start: datetime = field(default_factory=datetime.utcnow)
    last_updated: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Tenant:
    """Entité Tenant complète"""

    id: UUID = field(default_factory=uuid4)
    name: str = ""
    domain: str = ""
    platform: str = "prestashop"

    # Plan et statut
    plan: TenantPlan = TenantPlan.STARTER
    status: TenantStatus = TenantStatus.TRIAL

    # Configuration
    features: List[Feature] = field(default_factory=list)
    limits: TenantLimits = field(default_factory=TenantLimits)
    llm_config: TenantLLMConfig = field(default_factory=TenantLLMConfig)

    # Personnalisation
    settings: Dict[str, Any] = field(default_factory=dict)
    custom_prompts: Dict[str, str] = field(default_factory=dict)

    # Authentification
    api_key_hash: str = ""

    # Dates
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    trial_ends_at: Optional[datetime] = None


@dataclass
class TenantContext:
    """Contexte tenant injecté dans les requêtes"""

    tenant_id: str
    tenant_name: str
    plan: TenantPlan
    status: TenantStatus
    features: List[Feature]
    limits: TenantLimits
    llm_config: TenantLLMConfig
    settings: Dict[str, Any]


# =============================================================================
# TENANT SERVICE
# =============================================================================


class TenantService:
    """
    Service de gestion des tenants.
    Partagé entre tous les agents pour:
    - Vérification des features
    - Enforcement des limites
    - Configuration LLM par tenant
    """

    def __init__(self, cache_client=None, db_repository=None):
        self._cache = cache_client
        self._db = db_repository
        self._tenant_cache: Dict[str, Tenant] = {}  # In-memory cache

    # =========================================================================
    # TENANT LOADING
    # =========================================================================

    async def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """Récupère un tenant par ID"""
        # 1. Check in-memory cache
        if tenant_id in self._tenant_cache:
            return self._tenant_cache[tenant_id]

        # 2. Check Redis cache
        if self._cache:
            cached = await self._cache.get(f"tenant:{tenant_id}")
            if cached:
                tenant = self._deserialize_tenant(cached)
                self._tenant_cache[tenant_id] = tenant
                return tenant

        # 3. Load from database
        if self._db:
            tenant = await self._db.get_tenant(tenant_id)
            if tenant:
                self._tenant_cache[tenant_id] = tenant
                if self._cache:
                    await self._cache.set(
                        f"tenant:{tenant_id}",
                        self._serialize_tenant(tenant),
                        ex=300,  # 5 min TTL
                    )
                return tenant

        return None

    async def get_tenant_by_api_key(self, api_key_hash: str) -> Optional[Tenant]:
        """Récupère un tenant par hash de clé API"""
        if self._db:
            return await self._db.get_tenant_by_api_key(api_key_hash)
        return None

    async def get_tenant_context(self, tenant_id: str) -> Optional[TenantContext]:
        """Crée un contexte tenant pour injection"""
        tenant = await self.get_tenant(tenant_id)
        if not tenant:
            return None

        return TenantContext(
            tenant_id=str(tenant.id),
            tenant_name=tenant.name,
            plan=tenant.plan,
            status=tenant.status,
            features=tenant.features,
            limits=tenant.limits,
            llm_config=tenant.llm_config,
            settings=tenant.settings,
        )

    # =========================================================================
    # FEATURE CHECK
    # =========================================================================

    def has_feature(self, tenant: Tenant, feature: Feature) -> bool:
        """Vérifie si le tenant a accès à une feature"""
        return feature in tenant.features

    def check_feature(
        self,
        tenant: Tenant,
        feature: Feature,
    ) -> tuple[bool, Optional[str]]:
        """
        Vérifie l'accès à une feature.
        Returns: (allowed, error_message)
        """
        # Vérifier le statut
        if tenant.status == TenantStatus.SUSPENDED:
            return False, "Account suspended"

        if tenant.status == TenantStatus.CANCELLED:
            return False, "Account cancelled"

        # Vérifier la feature
        if not self.has_feature(tenant, feature):
            plan_name = PLAN_DEFINITIONS[tenant.plan]["name"]
            return False, f"Feature '{feature.value}' not available in {plan_name} plan"

        return True, None

    # =========================================================================
    # LIMIT CHECK
    # =========================================================================

    async def check_limit(
        self,
        tenant: Tenant,
        limit_type: str,
        increment: int = 1,
    ) -> tuple[bool, Optional[str], int]:
        """
        Vérifie si une limite est atteinte.
        Returns: (allowed, error_message, remaining)
        """
        # Récupérer la limite
        limit_value = getattr(tenant.limits, limit_type, None)
        if limit_value is None:
            return True, None, -1

        if limit_value == -1:  # Illimité
            return True, None, -1

        # Récupérer l'usage actuel
        usage = await self._get_current_usage(str(tenant.id), limit_type)

        remaining = limit_value - usage

        if remaining < increment:
            return False, f"Limit reached: {limit_type}", remaining

        return True, None, remaining - increment

    async def increment_usage(
        self,
        tenant_id: str,
        limit_type: str,
        increment: int = 1,
    ) -> None:
        """Incrémente l'usage d'une limite"""
        if self._cache:
            key = f"usage:{tenant_id}:{limit_type}:{self._get_period_key()}"
            await self._cache.incrby(key, increment)
            await self._cache.expire(key, 86400)  # 24h TTL

    async def _get_current_usage(self, tenant_id: str, limit_type: str) -> int:
        """Récupère l'usage actuel"""
        if self._cache:
            key = f"usage:{tenant_id}:{limit_type}:{self._get_period_key()}"
            value = await self._cache.get(key)
            return int(value) if value else 0
        return 0

    def _get_period_key(self) -> str:
        """Clé de période pour l'usage quotidien"""
        return datetime.utcnow().strftime("%Y-%m-%d")

    # =========================================================================
    # LLM CONFIG
    # =========================================================================

    def get_llm_config(self, tenant: Tenant) -> TenantLLMConfig:
        """Retourne la configuration LLM pour le tenant"""
        return tenant.llm_config

    def get_allowed_model(self, tenant: Tenant, requested_model: Optional[str] = None) -> str:
        """
        Retourne le modèle LLM autorisé.
        Si le modèle demandé n'est pas autorisé, retourne le modèle par défaut du plan.
        """
        default_model = tenant.llm_config.model

        if not requested_model:
            return default_model

        # Liste des modèles autorisés par plan
        ALLOWED_MODELS = {
            TenantPlan.STARTER: ["gpt-4o-mini", "gpt-3.5-turbo"],
            TenantPlan.PROFESSIONAL: [
                "gpt-4o-mini",
                "gpt-3.5-turbo",
                "gpt-4o",
                "claude-3-haiku-20240307",
            ],
            TenantPlan.ENTERPRISE: [
                "gpt-4o-mini",
                "gpt-3.5-turbo",
                "gpt-4o",
                "gpt-4-turbo",
                "claude-3-haiku-20240307",
                "claude-3-sonnet-20240229",
                "claude-3-5-sonnet-20240620",
            ],
        }

        allowed = ALLOWED_MODELS.get(tenant.plan, [])

        if requested_model in allowed:
            return requested_model

        logger.warning(
            f"Model {requested_model} not allowed for plan {tenant.plan.value}, using {default_model}"
        )
        return default_model

    # =========================================================================
    # SERIALIZATION
    # =========================================================================

    def _serialize_tenant(self, tenant: Tenant) -> str:
        """Sérialise un tenant pour cache"""
        import json

        return json.dumps(
            {
                "id": str(tenant.id),
                "name": tenant.name,
                "domain": tenant.domain,
                "platform": tenant.platform,
                "plan": tenant.plan.value,
                "status": tenant.status.value,
                "features": [f.value for f in tenant.features],
                "limits": tenant.limits.__dict__,
                "llm_config": tenant.llm_config.__dict__,
                "settings": tenant.settings,
            }
        )

    def _deserialize_tenant(self, data: str) -> Tenant:
        """Désérialise un tenant depuis cache"""
        import json

        d = json.loads(data)
        return Tenant(
            id=UUID(d["id"]),
            name=d["name"],
            domain=d["domain"],
            platform=d["platform"],
            plan=TenantPlan(d["plan"]),
            status=TenantStatus(d["status"]),
            features=[Feature(f) for f in d["features"]],
            limits=TenantLimits(**d["limits"]),
            llm_config=TenantLLMConfig(**d["llm_config"]),
            settings=d["settings"],
        )

    # =========================================================================
    # PLAN UPGRADE
    # =========================================================================

    def get_plan_features(self, plan: TenantPlan) -> List[Feature]:
        """Retourne les features d'un plan"""
        return PLAN_DEFINITIONS[plan]["features"]

    def get_plan_limits(self, plan: TenantPlan) -> TenantLimits:
        """Retourne les limites d'un plan"""
        limits_dict = PLAN_DEFINITIONS[plan]["limits"]
        return TenantLimits(**limits_dict)

    def get_plan_llm_config(self, plan: TenantPlan) -> TenantLLMConfig:
        """Retourne la config LLM d'un plan"""
        config_dict = PLAN_DEFINITIONS[plan]["llm_config"]
        return TenantLLMConfig(**config_dict)
