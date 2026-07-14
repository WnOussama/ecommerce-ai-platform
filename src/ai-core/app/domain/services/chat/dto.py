"""
Chat DTOs - Data Transfer Objects pour le Chat Service

Définit les structures de données pour les requêtes et réponses du chat.
Validation via Pydantic pour garantir l'intégrité des données.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChatRequest(BaseModel):
    """
    Requête de chat entrante.

    Attributes:
        message: Message de l'utilisateur (1-4096 caractères)
        conversation_id: ID conversation existante (optionnel, crée nouvelle si absent)
        idempotency_key: UUID unique pour éviter double traitement
        user_identifier: Identifiant utilisateur (session_id, customer_id, etc.)
        context: Contexte additionnel (page courante, produit consulté, etc.)
    """

    message: str = Field(..., min_length=1, max_length=4096)
    conversation_id: Optional[UUID] = None
    idempotency_key: UUID
    user_identifier: str = Field(..., min_length=1, max_length=255)
    context: Optional[Dict[str, Any]] = None

    @field_validator("message")
    @classmethod
    def validate_message_not_empty(cls, v: str) -> str:
        """Vérifie que le message n'est pas juste des espaces."""
        if not v.strip():
            raise ValueError("Message cannot be empty or whitespace only")
        return v.strip()


class ChatMessage(BaseModel):
    """
    Message dans une conversation.

    Utilisé pour représenter l'historique des messages.
    """

    id: UUID
    role: str  # "user", "assistant", "system"
    content: str
    created_at: datetime
    latency_ms: Optional[int] = None
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None


class ChatResponse(BaseModel):
    """
    Réponse du chat.

    Attributes:
        conversation_id: ID de la conversation (existante ou nouvellement créée)
        message_id: ID du message assistant créé
        response: Contenu de la réponse
        created: True si nouvelle conversation créée
        latency_ms: Temps de traitement total en ms
        metadata: Informations additionnelles (produits trouvés, intent, etc.)
    """

    conversation_id: UUID
    message_id: UUID
    response: str
    created: bool = False  # True si nouvelle conversation
    latency_ms: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "conversation_id": "123e4567-e89b-12d3-a456-426614174000",
                "message_id": "223e4567-e89b-12d3-a456-426614174001",
                "response": "Bonjour ! Comment puis-je vous aider ?",
                "created": True,
                "latency_ms": 250,
                "metadata": {"intent": "greeting", "products_found": 0},
            }
        }
    )


class ConversationHistory(BaseModel):
    """
    Historique d'une conversation.

    Utilisé pour construire le contexte LLM.
    """

    conversation_id: UUID
    tenant_id: UUID
    user_identifier: str
    messages: List[ChatMessage] = Field(default_factory=list)
    created_at: datetime
    updated_at: Optional[datetime] = None

    @property
    def message_count(self) -> int:
        """Nombre de messages dans la conversation."""
        return len(self.messages)

    def to_llm_messages(self, max_messages: int = 20) -> List[Dict[str, str]]:
        """
        Convertit l'historique en format LLM (OpenAI style).

        Args:
            max_messages: Nombre maximum de messages à inclure

        Returns:
            Liste de dicts {"role": "...", "content": "..."}
        """
        # Prendre les N derniers messages
        recent_messages = self.messages[-max_messages:]

        return [{"role": msg.role, "content": msg.content} for msg in recent_messages]
