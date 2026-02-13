"""
Tenant Model - Multi-tenant SaaS

Chaque boutique e-commerce = 1 tenant.
Toutes les autres entités sont liées à un tenant.
"""

from sqlalchemy import Column, String, Boolean, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.infrastructure.database.base import (
    Base,
    UUIDMixin,
    TimestampMixin,
    SoftDeleteMixin,
)


class Tenant(Base, UUIDMixin, TimestampMixin, SoftDeleteMixin):
    """
    Table des tenants (locataires SaaS).

    Chaque boutique e-commerce = 1 tenant.
    Toutes les données sont isolées par tenant_id.

    Attributes:
        id: UUID unique du tenant
        name: Nom affiché de la boutique
        slug: Identifiant URL-safe unique
        api_key_hash: Hash de la clé API (jamais en clair)
        settings: Configuration JSONB flexible
        is_active: Tenant actif ou suspendu
    """
    __tablename__ = "tenants"

    # Identité
    name = Column(String(255), nullable=False)
    slug = Column(String(100), unique=True, nullable=False)

    # Authentification
    api_key_hash = Column(String(255), nullable=True)

    # Configuration flexible (JSONB pour queries performantes)
    settings = Column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    # Statut
    is_active = Column(Boolean, nullable=False, default=True)

    # Relations ORM
    # NOTE: Pas de lazy="dynamic" (deprecated SQLAlchemy 2.0)
    # NOTE: Pas de cascade ORM - on utilise ondelete="CASCADE" côté DB (source de vérité)
    # Pour charger les relations, utiliser selectinload() ou joinedload() dans les queries
    conversations = relationship(
        "Conversation",
        back_populates="tenant",
        passive_deletes=True,
    )
    rules = relationship(
        "Rule",
        back_populates="tenant",
        passive_deletes=True,
    )
    coupons = relationship(
        "Coupon",
        back_populates="tenant",
        passive_deletes=True,
    )
    analytics_events = relationship(
        "AnalyticsEvent",
        back_populates="tenant",
        passive_deletes=True,
    )
    messages = relationship(
        "Message",
        foreign_keys="Message.tenant_id",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("idx_tenant_slug", "slug"),
        Index("idx_tenant_is_active", "is_active"),
        Index("idx_tenant_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Tenant(id={self.id}, name='{self.name}', slug='{self.slug}')>"


