"""
AnalyticsEvent Model - Event Tracking

Stocke tous les événements pour analytics et reporting.
Utilise une table append-only avec partitioning potentiel.
"""

from sqlalchemy import Column, String, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from app.infrastructure.database.base import (
    Base,
    UUIDMixin,
    TimestampMixin,
)


class AnalyticsEvent(Base, UUIDMixin, TimestampMixin):
    """
    Table des événements analytics.

    Table append-only pour tracking haute performance.
    Optimisée pour les requêtes de reporting.

    Attributes:
        id: UUID unique de l'événement
        tenant_id: UUID du tenant (FK)
        event_type: Type d'événement (conversation_started, coupon_generated, etc.)
        entity_type: Type d'entité concernée (conversation, coupon, product, etc.)
        entity_id: ID de l'entité concernée (optionnel)
        payload: Données de l'événement (JSONB)

    Event types courants:
        - conversation_started
        - conversation_ended
        - message_sent
        - coupon_generated
        - coupon_used
        - product_viewed
        - product_recommended
        - rule_triggered
        - error_occurred

    Exemple payload:
        {
            "conversation_id": "...",
            "intent": "product_search",
            "products_found": 5,
            "latency_ms": 234
        }
    """
    __tablename__ = "analytics_events"

    # Relation tenant
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Classification
    event_type = Column(String(100), nullable=False)
    entity_type = Column(String(50), nullable=True)
    entity_id = Column(UUID(as_uuid=True), nullable=True)

    # Données de l'événement
    payload = Column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    # Relations
    tenant = relationship("Tenant", back_populates="analytics_events")

    __table_args__ = (
        # Index pour queries analytics fréquentes
        Index("idx_analytics_tenant_type", "tenant_id", "event_type"),
        Index("idx_analytics_tenant_date", "tenant_id", "created_at"),
        Index("idx_analytics_entity", "tenant_id", "entity_type", "entity_id"),

        # Index pour time-series queries
        Index("idx_analytics_created_at", "created_at"),

        # Index GIN pour queries sur contenu JSONB (ex: payload->>'intent' = 'product_search')
        Index("idx_analytics_payload_gin", "payload", postgresql_using="gin"),

        # Commentaire pour partitioning futur
        # En production, considérer partitioning par mois:
        # PARTITION BY RANGE (created_at)
    )

    def __repr__(self) -> str:
        return f"<AnalyticsEvent(id={self.id}, type='{self.event_type}', tenant_id={self.tenant_id})>"


