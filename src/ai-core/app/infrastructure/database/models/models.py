"""
Modèles SQLAlchemy - Multi-tenant avec isolation logique
"""

from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy import (
    Column, String, Integer, Float, Boolean, Text, JSON,
    DateTime, ForeignKey, Enum as SQLEnum, Index, BigInteger
)
from sqlalchemy.dialects.mysql import CHAR
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.sql import func

from app.domain.entities.models import (
    TenantPlan, TenantStatus, CustomerSegment,
    ConversationStatus, MessageRole, IntentType,
    CouponStatus, AdminActionType, AdminActionStatus
)

Base = declarative_base()


def generate_uuid():
    return str(uuid4())


# ============================================================================
# TENANT
# ============================================================================

class TenantModel(Base):
    """
    Table des tenants (locataires SaaS).
    Chaque boutique e-commerce = 1 tenant.
    """
    __tablename__ = "tenants"

    id = Column(CHAR(36), primary_key=True, default=generate_uuid)

    # Identité
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, nullable=False, index=True)
    domain = Column(String(255), nullable=False)  # URL de la boutique
    platform = Column(String(50), nullable=False, default="prestashop")

    # Plan et statut
    plan = Column(SQLEnum(TenantPlan), default=TenantPlan.STARTER, nullable=False)
    status = Column(SQLEnum(TenantStatus), default=TenantStatus.TRIAL, nullable=False)

    # Authentification
    api_key_hash = Column(String(255), nullable=True)
    webhook_secret = Column(String(255), nullable=True)

    # Configuration
    settings = Column(JSON, default=dict)
    features_enabled = Column(JSON, default=list)

    # Limites
    max_conversations_per_day = Column(Integer, default=500)
    max_products_indexed = Column(Integer, default=1000)
    rate_limit_rpm = Column(Integer, default=30)

    # Dates
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    trial_ends_at = Column(DateTime, nullable=True)

    # Relations
    customers = relationship("CustomerModel", back_populates="tenant", lazy="dynamic")
    conversations = relationship("ConversationModel", back_populates="tenant", lazy="dynamic")
    coupons = relationship("CouponModel", back_populates="tenant", lazy="dynamic")
    admin_actions = relationship("AdminActionModel", back_populates="tenant", lazy="dynamic")

    __table_args__ = (
        Index("idx_tenant_status", "status"),
        Index("idx_tenant_platform", "platform"),
    )


# ============================================================================
# CUSTOMER
# ============================================================================

class CustomerModel(Base):
    """
    Table des clients finaux.
    Synchronisée depuis PrestaShop/Shopify.
    """
    __tablename__ = "customers"

    id = Column(CHAR(36), primary_key=True, default=generate_uuid)
    tenant_id = Column(CHAR(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # ID externe (dans le système e-commerce)
    external_id = Column(String(100), nullable=False)

    # Identité
    email = Column(String(255), nullable=False)
    first_name = Column(String(100), nullable=True)
    last_name = Column(String(100), nullable=True)
    phone = Column(String(50), nullable=True)

    # Segmentation
    segment = Column(SQLEnum(CustomerSegment), default=CustomerSegment.NEW)
    loyalty_score = Column(Integer, default=0)
    lifetime_value = Column(Float, default=0.0)

    # Statistiques
    total_orders = Column(Integer, default=0)
    total_spent = Column(Float, default=0.0)
    average_order_value = Column(Float, default=0.0)
    last_order_date = Column(DateTime, nullable=True)

    # Préférences (JSON pour flexibilité)
    preferred_categories = Column(JSON, default=list)
    preferred_brands = Column(JSON, default=list)
    communication_preferences = Column(JSON, default=dict)

    # Dates
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    synced_at = Column(DateTime, nullable=True)  # Dernière sync

    # Relations
    tenant = relationship("TenantModel", back_populates="customers")
    conversations = relationship("ConversationModel", back_populates="customer", lazy="dynamic")
    coupons = relationship("CouponModel", back_populates="customer", lazy="dynamic")

    __table_args__ = (
        Index("idx_customer_tenant_external", "tenant_id", "external_id", unique=True),
        Index("idx_customer_email", "tenant_id", "email"),
        Index("idx_customer_segment", "tenant_id", "segment"),
        Index("idx_customer_loyalty", "tenant_id", "loyalty_score"),
    )


# ============================================================================
# CONVERSATION
# ============================================================================

class ConversationModel(Base):
    """
    Table des conversations chatbot.
    """
    __tablename__ = "conversations"

    id = Column(CHAR(36), primary_key=True, default=generate_uuid)
    tenant_id = Column(CHAR(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    customer_id = Column(CHAR(36), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True)

    # Session
    session_id = Column(String(100), nullable=False, index=True)

    # État
    status = Column(SQLEnum(ConversationStatus), default=ConversationStatus.ACTIVE)
    primary_intent = Column(SQLEnum(IntentType), nullable=True)
    resolved_by_ai = Column(Boolean, default=False)

    # Contexte (page, produit, etc.)
    context = Column(JSON, default=dict)
    metadata = Column(JSON, default=dict)

    # Métriques
    message_count = Column(Integer, default=0)
    llm_tokens_used = Column(Integer, default=0)
    llm_cost = Column(Float, default=0.0)

    # Feedback
    satisfaction_rating = Column(Integer, nullable=True)  # 1-5
    feedback_comment = Column(Text, nullable=True)

    # Dates
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    resolved_at = Column(DateTime, nullable=True)

    # Relations
    tenant = relationship("TenantModel", back_populates="conversations")
    customer = relationship("CustomerModel", back_populates="conversations")
    messages = relationship("MessageModel", back_populates="conversation", lazy="dynamic",
                          order_by="MessageModel.created_at")

    __table_args__ = (
        Index("idx_conv_tenant_session", "tenant_id", "session_id"),
        Index("idx_conv_tenant_status", "tenant_id", "status"),
        Index("idx_conv_tenant_date", "tenant_id", "created_at"),
    )


# ============================================================================
# MESSAGE
# ============================================================================

class MessageModel(Base):
    """
    Table des messages individuels.
    """
    __tablename__ = "messages"

    id = Column(CHAR(36), primary_key=True, default=generate_uuid)
    conversation_id = Column(CHAR(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)

    # Contenu
    role = Column(SQLEnum(MessageRole), nullable=False)
    content = Column(Text, nullable=False)

    # IA Metadata
    intent = Column(SQLEnum(IntentType), nullable=True)
    confidence = Column(Float, default=0.0)
    entities = Column(JSON, default=dict)  # Entités extraites

    # Actions générées par l'IA
    actions = Column(JSON, default=list)

    # Token tracking
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    model_used = Column(String(100), nullable=True)

    # Dates
    created_at = Column(DateTime, default=func.now())

    # Relations
    conversation = relationship("ConversationModel", back_populates="messages")

    __table_args__ = (
        Index("idx_message_conv_date", "conversation_id", "created_at"),
    )


# ============================================================================
# COUPON
# ============================================================================

class CouponModel(Base):
    """
    Table des coupons générés par l'IA.
    """
    __tablename__ = "coupons"

    id = Column(CHAR(36), primary_key=True, default=generate_uuid)
    tenant_id = Column(CHAR(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    customer_id = Column(CHAR(36), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True)

    # Code et valeur
    code = Column(String(50), nullable=False, unique=True)
    discount_type = Column(String(20), default="percent")  # percent, fixed
    discount_value = Column(Float, nullable=False)
    min_purchase_amount = Column(Float, nullable=True)

    status = Column(SQLEnum(CouponStatus), default=CouponStatus.ACTIVE)

    # Génération IA
    generation_reason = Column(String(100), nullable=False)
    ai_generated = Column(Boolean, default=True)
    conversation_id = Column(CHAR(36), nullable=True)

    # Validité
    valid_from = Column(DateTime, nullable=False)
    valid_until = Column(DateTime, nullable=False)

    # Utilisation
    used_at = Column(DateTime, nullable=True)
    order_external_id = Column(String(100), nullable=True)

    # Dates
    created_at = Column(DateTime, default=func.now())

    # Relations
    tenant = relationship("TenantModel", back_populates="coupons")
    customer = relationship("CustomerModel", back_populates="coupons")

    __table_args__ = (
        Index("idx_coupon_code", "code"),
        Index("idx_coupon_tenant_status", "tenant_id", "status"),
        Index("idx_coupon_customer", "customer_id"),
    )


# ============================================================================
# ADMIN ACTION (Audit Log)
# ============================================================================

class AdminActionModel(Base):
    """
    Table d'audit des actions administrateur.
    Critique pour la traçabilité et compliance.
    """
    __tablename__ = "admin_actions"

    id = Column(CHAR(36), primary_key=True, default=generate_uuid)
    tenant_id = Column(CHAR(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Type et statut
    action_type = Column(SQLEnum(AdminActionType), nullable=False)
    status = Column(SQLEnum(AdminActionStatus), default=AdminActionStatus.PENDING)

    # Requête
    request_payload = Column(JSON, nullable=False)
    ai_reasoning = Column(Text, nullable=True)

    # Résultat
    result_payload = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)

    # Validation
    requires_approval = Column(Boolean, default=False)
    approved_by = Column(String(100), nullable=True)
    approved_at = Column(DateTime, nullable=True)

    # Exécution
    executed_at = Column(DateTime, nullable=True)
    execution_duration_ms = Column(Integer, nullable=True)

    # Audit
    initiated_by = Column(String(100), nullable=False)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(500), nullable=True)

    # Dates
    created_at = Column(DateTime, default=func.now())

    # Relations
    tenant = relationship("TenantModel", back_populates="admin_actions")

    __table_args__ = (
        Index("idx_admin_tenant_type", "tenant_id", "action_type"),
        Index("idx_admin_tenant_status", "tenant_id", "status"),
        Index("idx_admin_tenant_date", "tenant_id", "created_at"),
    )


# ============================================================================
# LLM USAGE (Cost Tracking)
# ============================================================================

class LLMUsageModel(Base):
    """
    Table de suivi des usages LLM pour facturation.
    """
    __tablename__ = "llm_usage"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id = Column(CHAR(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Usage
    model = Column(String(100), nullable=False)
    input_tokens = Column(Integer, nullable=False)
    output_tokens = Column(Integer, nullable=False)

    # Coût calculé
    cost_usd = Column(Float, nullable=False)

    # Contexte
    intent = Column(SQLEnum(IntentType), nullable=True)
    conversation_id = Column(CHAR(36), nullable=True)

    # Date
    created_at = Column(DateTime, default=func.now())

    __table_args__ = (
        Index("idx_usage_tenant_date", "tenant_id", "created_at"),
        Index("idx_usage_tenant_model", "tenant_id", "model"),
    )


# ============================================================================
# PRODUCT EMBEDDING METADATA
# ============================================================================

class ProductEmbeddingModel(Base):
    """
    Metadata des embeddings produits (le vecteur est dans ChromaDB).
    """
    __tablename__ = "product_embeddings"

    id = Column(CHAR(36), primary_key=True, default=generate_uuid)
    tenant_id = Column(CHAR(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)

    # Produit
    product_external_id = Column(String(100), nullable=False)
    name = Column(String(500), nullable=False)
    category = Column(String(255), nullable=True)
    brand = Column(String(255), nullable=True)
    price = Column(Float, default=0.0)

    # Embedding ref
    embedding_id = Column(String(100), nullable=False)  # ID dans ChromaDB
    content_hash = Column(String(64), nullable=False)

    # Dates
    indexed_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("idx_embed_tenant_product", "tenant_id", "product_external_id", unique=True),
        Index("idx_embed_hash", "content_hash"),
    )

