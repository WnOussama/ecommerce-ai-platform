"""
Rule Model - Business Rules Engine

Les règles définissent des automatisations :
- Si [conditions] alors [action]

Exemples:
- Si panier abandonné > 50€ → générer coupon 10%
- Si client fidèle → message personnalisé
"""

from sqlalchemy import Boolean, Column, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.base import (
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDMixin,
)


class Rule(Base, UUIDMixin, TimestampMixin, SoftDeleteMixin):
    """
    Table des règles business.

    Chaque règle appartient à un tenant et définit une automatisation
    basée sur des conditions et une action.

    Attributes:
        id: UUID unique de la règle
        tenant_id: UUID du tenant (FK)
        name: Nom descriptif de la règle
        description: Description détaillée (optionnel)
        conditions: Conditions d'activation (JSONB)
        action: Action à exécuter si conditions remplies (JSONB)
        priority: Ordre d'évaluation (plus petit = priorité haute)
        is_active: Règle active ou non
        usage_count: Nombre de fois déclenchée

    Exemple conditions JSONB:
        {
            "intent": "cart_abandon",
            "cart_value_min": 50,
            "customer_type": "returning"
        }

    Exemple action JSONB:
        {
            "type": "generate_coupon",
            "discount_percent": 10,
            "message": "Voici un code promo..."
        }
    """

    __tablename__ = "rules"

    # Relation tenant
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Identité
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)

    # Logique
    conditions = Column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )
    action = Column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    # Configuration
    priority = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)

    # Statistiques
    usage_count = Column(Integer, nullable=False, default=0)

    # Relations ORM
    # NOTE: Pas de lazy="dynamic" (deprecated SQLAlchemy 2.0)
    tenant = relationship("Tenant", back_populates="rules")
    coupons = relationship(
        "Coupon",
        back_populates="rule",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("idx_rule_tenant_id", "tenant_id"),
        Index("idx_rule_tenant_active", "tenant_id", "is_active"),
        Index("idx_rule_priority", "tenant_id", "priority"),
        {"extend_existing": True},
    )

    def __repr__(self) -> str:
        return f"<Rule(id={self.id}, name='{self.name}', is_active={self.is_active})>"
