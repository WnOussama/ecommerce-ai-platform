"""
Domain Entities - Modèles de domaine purs (sans dépendance ORM)
Design DDD: Ces entités représentent le cœur métier
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4


# ============================================================================
# ENUMS
# ============================================================================

class TenantPlan(str, Enum):
    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class TenantStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    TRIAL = "trial"
    CANCELLED = "cancelled"


class CustomerSegment(str, Enum):
    NEW = "new"
    OCCASIONAL = "occasional"
    REGULAR = "regular"
    LOYAL = "loyal"
    VIP = "vip"
    AT_RISK = "at_risk"
    CHURNED = "churned"


class ConversationStatus(str, Enum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    ABANDONED = "abandoned"


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class IntentType(str, Enum):
    PRODUCT_SEARCH = "product_search"
    ORDER_STATUS = "order_status"
    SHIPPING_INFO = "shipping_info"
    RETURN_REQUEST = "return_request"
    COMPLAINT = "complaint"
    RECOMMENDATION = "recommendation"
    COUPON_REQUEST = "coupon_request"
    FAQ = "faq"
    GENERAL = "general"
    PURCHASE = "purchase"


class CouponStatus(str, Enum):
    ACTIVE = "active"
    USED = "used"
    EXPIRED = "expired"
    REVOKED = "revoked"


class AdminActionType(str, Enum):
    PRICE_UPDATE = "price_update"
    PRODUCT_UPDATE = "product_update"
    CAMPAIGN_CREATE = "campaign_create"
    COUPON_GENERATE = "coupon_generate"
    STRATEGY_GENERATE = "strategy_generate"
    ANALYTICS_QUERY = "analytics_query"


class AdminActionStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTED = "executed"
    FAILED = "failed"


# ============================================================================
# VALUE OBJECTS
# ============================================================================

@dataclass(frozen=True)
class Money:
    """Value Object pour les montants monétaires"""
    amount: float
    currency: str = "EUR"

    def __add__(self, other: "Money") -> "Money":
        if self.currency != other.currency:
            raise ValueError("Cannot add different currencies")
        return Money(self.amount + other.amount, self.currency)

    def __mul__(self, factor: float) -> "Money":
        return Money(self.amount * factor, self.currency)


@dataclass(frozen=True)
class LLMUsage:
    """Value Object pour tracking usage LLM"""
    input_tokens: int
    output_tokens: int
    model: str

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def calculate_cost(self, input_cost_per_1k: float, output_cost_per_1k: float) -> float:
        return (self.input_tokens / 1000 * input_cost_per_1k +
                self.output_tokens / 1000 * output_cost_per_1k)


@dataclass(frozen=True)
class EmbeddingMetadata:
    """Metadata pour un embedding vectoriel"""
    source_type: str  # product, faq, policy, conversation
    source_id: str
    tenant_id: str
    content_hash: str
    created_at: datetime


# ============================================================================
# ENTITIES
# ============================================================================

@dataclass
class Tenant:
    """Entité Tenant (locataire SaaS)"""
    id: UUID = field(default_factory=uuid4)
    name: str = ""
    domain: str = ""  # shop-name.myshopify.com ou URL PrestaShop
    platform: str = "prestashop"  # prestashop, shopify, woocommerce
    plan: TenantPlan = TenantPlan.STARTER
    status: TenantStatus = TenantStatus.TRIAL

    # API Access
    api_key_hash: str = ""
    webhook_secret: str = ""

    # Configuration
    settings: Dict[str, Any] = field(default_factory=dict)
    features_enabled: List[str] = field(default_factory=list)

    # Limites
    max_conversations_per_day: int = 500
    max_products_indexed: int = 1000
    rate_limit_rpm: int = 30

    # Dates
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    trial_ends_at: Optional[datetime] = None

    def can_use_feature(self, feature: str) -> bool:
        return feature in self.features_enabled

    def is_active(self) -> bool:
        return self.status in [TenantStatus.ACTIVE, TenantStatus.TRIAL]


@dataclass
class Customer:
    """Entité Customer (client final de la boutique)"""
    id: UUID = field(default_factory=uuid4)
    tenant_id: UUID = field(default_factory=uuid4)
    external_id: str = ""  # ID dans PrestaShop/Shopify

    # Identité
    email: str = ""
    first_name: str = ""
    last_name: str = ""
    phone: Optional[str] = None

    # Segmentation
    segment: CustomerSegment = CustomerSegment.NEW
    loyalty_score: int = 0  # 0-100
    lifetime_value: float = 0.0

    # Statistiques
    total_orders: int = 0
    total_spent: float = 0.0
    average_order_value: float = 0.0
    last_order_date: Optional[datetime] = None

    # Préférences
    preferred_categories: List[str] = field(default_factory=list)
    preferred_brands: List[str] = field(default_factory=list)
    communication_preferences: Dict[str, bool] = field(default_factory=dict)

    # Dates
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    def calculate_segment(self) -> CustomerSegment:
        """Calcule le segment basé sur le comportement"""
        if self.total_orders == 0:
            return CustomerSegment.NEW

        if self.loyalty_score >= 80:
            return CustomerSegment.VIP
        elif self.loyalty_score >= 60:
            return CustomerSegment.LOYAL
        elif self.loyalty_score >= 40:
            return CustomerSegment.REGULAR
        elif self.total_orders >= 2:
            return CustomerSegment.OCCASIONAL

        return CustomerSegment.NEW


@dataclass
class Conversation:
    """Entité Conversation"""
    id: UUID = field(default_factory=uuid4)
    tenant_id: UUID = field(default_factory=uuid4)
    customer_id: Optional[UUID] = None
    session_id: str = ""

    # État
    status: ConversationStatus = ConversationStatus.ACTIVE
    primary_intent: Optional[IntentType] = None
    resolved_by_ai: bool = False

    # Contexte
    context: Dict[str, Any] = field(default_factory=dict)  # Page, produit consulté, etc.
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Métriques
    message_count: int = 0
    llm_tokens_used: int = 0
    llm_cost: float = 0.0

    # Feedback
    satisfaction_rating: Optional[int] = None  # 1-5
    feedback_comment: Optional[str] = None

    # Dates
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None


@dataclass
class Message:
    """Entité Message"""
    id: UUID = field(default_factory=uuid4)
    conversation_id: UUID = field(default_factory=uuid4)

    role: MessageRole = MessageRole.USER
    content: str = ""

    # IA Metadata
    intent: Optional[IntentType] = None
    confidence: float = 0.0
    entities: Dict[str, Any] = field(default_factory=dict)  # Entités extraites

    # Actions générées
    actions: List[Dict[str, Any]] = field(default_factory=list)

    # Token tracking
    llm_usage: Optional[LLMUsage] = None

    # Dates
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Coupon:
    """Entité Coupon généré par IA"""
    id: UUID = field(default_factory=uuid4)
    tenant_id: UUID = field(default_factory=uuid4)
    customer_id: UUID = field(default_factory=uuid4)

    code: str = ""
    discount_type: str = "percent"  # percent, fixed
    discount_value: float = 0.0
    min_purchase_amount: Optional[float] = None

    status: CouponStatus = CouponStatus.ACTIVE

    # Génération IA
    generation_reason: str = ""  # loyalty_reward, cart_abandonment, complaint, etc.
    ai_generated: bool = True
    conversation_id: Optional[UUID] = None

    # Validité
    valid_from: datetime = field(default_factory=datetime.utcnow)
    valid_until: datetime = field(default_factory=datetime.utcnow)

    # Utilisation
    used_at: Optional[datetime] = None
    order_id: Optional[str] = None

    # Dates
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class AdminAction:
    """Entité Action Admin (audit log)"""
    id: UUID = field(default_factory=uuid4)
    tenant_id: UUID = field(default_factory=uuid4)

    action_type: AdminActionType = AdminActionType.ANALYTICS_QUERY
    status: AdminActionStatus = AdminActionStatus.PENDING

    # Requête
    request_payload: Dict[str, Any] = field(default_factory=dict)
    ai_reasoning: str = ""  # Explication de l'IA

    # Résultat
    result_payload: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None

    # Validation
    requires_approval: bool = False
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None

    # Exécution
    executed_at: Optional[datetime] = None
    execution_duration_ms: Optional[int] = None

    # Audit
    initiated_by: str = ""  # admin_user_id ou "ai_scheduled"
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None

    # Dates
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ProductEmbedding:
    """Metadata pour embedding produit"""
    id: UUID = field(default_factory=uuid4)
    tenant_id: UUID = field(default_factory=uuid4)
    product_external_id: str = ""

    # Contenu indexé
    name: str = ""
    description: str = ""
    category: str = ""
    brand: Optional[str] = None
    price: float = 0.0

    # Vector
    embedding_id: str = ""  # ID dans ChromaDB
    content_hash: str = ""  # Pour détecter les changements

    # Dates
    indexed_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

