"""
Coupon Model - Discount Codes

Les coupons sont générés par l'AI Agent ou les règles.
Peuvent être synchronisés avec PrestaShop.
"""

import enum
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from app.infrastructure.database.base import (
    Base,
    TimestampMixin,
    UUIDMixin,
)


class CouponStatus(enum.Enum):
    """Statut d'un coupon."""

    ACTIVE = "active"
    USED = "used"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class Coupon(Base, UUIDMixin, TimestampMixin):
    """
    Table des coupons de réduction.

    Chaque coupon appartient à un tenant et peut être lié à :
    - Une conversation (généré pendant le chat)
    - Une règle (généré automatiquement)

    Attributes:
        id: UUID unique du coupon
        tenant_id: UUID du tenant (FK)
        conversation_id: UUID conversation source (FK, optionnel)
        rule_id: UUID règle source (FK, optionnel)
        code: Code promo unique
        discount_percent: Pourcentage de réduction (1-100)
        discount_amount: Montant fixe de réduction (alternative)
        min_purchase: Achat minimum requis
        status: Statut du coupon
        reason: Raison de génération
        prestashop_id: ID dans PrestaShop (si synchronisé)
        expires_at: Date d'expiration
        used_at: Date d'utilisation
        extra_data: Données additionnelles
    """

    __tablename__ = "coupons"

    # Relations
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    rule_id = Column(
        UUID(as_uuid=True),
        ForeignKey("rules.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Identité
    code = Column(String(50), nullable=False)

    # Réduction
    discount_percent = Column(Integer, nullable=True)  # 1-100
    discount_amount = Column(Integer, nullable=True)  # En centimes
    min_purchase = Column(Integer, nullable=True)  # En centimes

    # Statut
    status = Column(
        SQLEnum(CouponStatus, name="coupon_status"),
        nullable=False,
        default=CouponStatus.ACTIVE,
    )
    reason = Column(String(100), nullable=True)

    # Sync PrestaShop
    prestashop_id = Column(Integer, nullable=True)

    # Dates
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)

    # Métadonnées
    extra_data = Column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    # Relations ORM
    tenant = relationship("Tenant", back_populates="coupons", overlaps="tenant,coupons")
    # NOTE: conversation_id exists as FK column but relationship is NOT defined here
    # to avoid conflict with models.py which also defines coupons table.
    # Use explicit queries via conversation_id if needed.
    rule = relationship("Rule", back_populates="coupons")

    __table_args__ = (
        Index("idx_coupon_tenant_id", "tenant_id"),
        Index("idx_coupon_code", "tenant_id", "code", unique=True),
        Index("idx_coupon_status", "tenant_id", "status"),
        Index("idx_coupon_expires_at", "tenant_id", "expires_at"),
        Index("idx_coupon_created_at", "tenant_id", "created_at"),
        {"extend_existing": True},
    )

    def __repr__(self) -> str:
        return f"<Coupon(id={self.id}, code='{self.code}', status={self.status.value})>"

    @property
    def is_valid(self) -> bool:
        """Vérifie si le coupon est encore valide."""
        if self.status != CouponStatus.ACTIVE:
            return False
        if self.expires_at and self.expires_at < datetime.now(timezone.utc):
            return False
        return True
