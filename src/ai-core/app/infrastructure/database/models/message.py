"""
Message Model - Chat Messages

Un message = une entrée dans une conversation.
Peut être de l'utilisateur, de l'assistant, ou du système.

ARCHITECTURE MULTI-TENANT:
- tenant_id est DÉNORMALISÉ volontairement (aussi présent sur Conversation)
- Raisons:
  1. Permet Row-Level Security directe sur cette table
  2. Évite JOIN pour queries analytics (SELECT WHERE tenant_id = ?)
  3. Prépare sharding futur par tenant
  4. Performance sur tables volumineuses (10M+ rows/an)
- La cohérence tenant_id == conversation.tenant_id est validée au service layer

IDEMPOTENCY:
- idempotency_key permet d'éviter les double insertions si timeout client
- Le client génère un UUID unique par requête
- Si retry avec même idempotency_key → on retourne le message existant
"""

from sqlalchemy import Column, String, Text, Integer, ForeignKey, Index, Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
import enum

from app.infrastructure.database.base import (
    Base,
    UUIDMixin,
    TimestampMixin,
)


class MessageRole(enum.Enum):
    """Rôle de l'émetteur du message."""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class Message(Base, UUIDMixin, TimestampMixin):
    """
    Table des messages de conversation.

    Chaque message appartient à une conversation ET à un tenant (dénormalisé).
    Stocke le contenu, les métriques de performance, et les données additionnelles.

    Attributes:
        id: UUID unique du message
        tenant_id: UUID du tenant (FK) - DÉNORMALISÉ pour performance
        conversation_id: UUID de la conversation (FK)
        idempotency_key: UUID unique pour éviter double insertion
        role: Rôle de l'émetteur (user, assistant, system)
        content: Contenu textuel du message
        extra_data: Données additionnelles (tool_calls, intent, etc.)
        latency_ms: Temps de réponse en millisecondes (pour assistant)
        tokens_input: Nombre de tokens en entrée
        tokens_output: Nombre de tokens en sortie
        created_at: Date de création (via TimestampMixin)
        updated_at: Date de modification (via TimestampMixin)
    """
    __tablename__ = "messages"

    # Relation tenant (DÉNORMALISÉ pour multi-tenant strict)
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Relation conversation
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Idempotency key pour éviter double insertion
    # Généré côté client, unique globalement
    idempotency_key = Column(
        UUID(as_uuid=True),
        nullable=False,
        unique=True,
        index=True,
    )

    # Contenu
    role = Column(
        SQLEnum(MessageRole, name="message_role"),
        nullable=False,
    )
    content = Column(Text, nullable=False)

    # Données additionnelles (intent détecté, tool_calls, products_found, etc.)
    extra_data = Column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    # Métriques de performance
    latency_ms = Column(Integer, nullable=True)
    tokens_input = Column(Integer, nullable=True)
    tokens_output = Column(Integer, nullable=True)

    # Relations ORM
    # Note: overlaps="messages" pour éviter warning SQLAlchemy
    tenant = relationship("Tenant", foreign_keys=[tenant_id], overlaps="messages")
    conversation = relationship("Conversation", back_populates="messages")

    __table_args__ = (
        # Index pour isolation multi-tenant
        Index("idx_message_tenant_id", "tenant_id"),
        Index("idx_message_tenant_created", "tenant_id", "created_at"),

        # Index pour queries conversation
        Index("idx_message_conversation_id", "conversation_id"),
        Index("idx_message_conversation_created", "conversation_id", "created_at"),

        # Index composite pour historique chat (query la plus fréquente)
        Index("idx_message_conv_role_created", "conversation_id", "role", "created_at"),

        # Index pour idempotency (éviter double insertion)
        Index("idx_message_idempotency_key", "idempotency_key", unique=True),
    )

    def __repr__(self) -> str:
        content_preview = self.content[:50] + "..." if len(self.content) > 50 else self.content
        return f"<Message(id={self.id}, role={self.role.value}, content='{content_preview}')>"





