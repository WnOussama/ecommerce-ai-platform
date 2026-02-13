"""
Conversation Model - Chat Sessions

Une conversation = une session de chat avec un utilisateur.
Contient plusieurs messages.
"""

from sqlalchemy import Column, String, ForeignKey, Index, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
import enum

from app.infrastructure.database.base import (
    Base,
    UUIDMixin,
    TimestampMixin,
)


class ConversationStatus(enum.Enum):
    """Statut d'une conversation."""
    ACTIVE = "active"
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    ABANDONED = "abandoned"


class Conversation(Base, UUIDMixin, TimestampMixin):
    """
    Table des conversations (sessions de chat).

    Chaque conversation appartient à un tenant et peut contenir
    plusieurs messages.

    Attributes:
        id: UUID unique de la conversation
        tenant_id: UUID du tenant (FK)
        user_identifier: Identifiant utilisateur (session_id, customer_id, etc.)
        status: Statut de la conversation
        extra_data: Données contextuelles (JSONB)
        ended_at: Date de fin (null si active)
    """
    __tablename__ = "conversations"

    # Relation tenant
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Identification utilisateur (flexible)
    user_identifier = Column(String(255), nullable=False)

    # Statut
    status = Column(
        SQLEnum(ConversationStatus, name="conversation_status"),
        nullable=False,
        default=ConversationStatus.ACTIVE,
    )

    # Métadonnées contextuelles
    extra_data = Column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    # Relations ORM
    # NOTE: Pas de lazy="dynamic" (deprecated SQLAlchemy 2.0)
    # NOTE: Pas de cascade ORM - DB est source de vérité (ondelete="CASCADE")
    tenant = relationship("Tenant", back_populates="conversations")
    messages = relationship(
        "Message",
        back_populates="conversation",
        passive_deletes=True,
        order_by="Message.created_at",
    )

    __table_args__ = (
        Index("idx_conversation_tenant_id", "tenant_id"),
        Index("idx_conversation_user_identifier", "tenant_id", "user_identifier"),
        Index("idx_conversation_status", "tenant_id", "status"),
        Index("idx_conversation_created_at", "tenant_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Conversation(id={self.id}, tenant_id={self.tenant_id}, status={self.status.value})>"




