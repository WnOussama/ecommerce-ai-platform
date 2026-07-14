"""
Chat Exceptions - Exceptions métier pour le Chat Service

Hiérarchie d'exceptions pour une gestion d'erreur propre et typée.
Chaque exception contient les informations nécessaires pour le logging
et la réponse API appropriée.
"""

from typing import Any, Dict, Optional
from uuid import UUID


class ChatServiceError(Exception):
    """
    Exception de base pour toutes les erreurs du Chat Service.

    Attributes:
        message: Message d'erreur
        tenant_id: ID du tenant concerné
        details: Détails additionnels pour logging
    """

    def __init__(
        self,
        message: str,
        tenant_id: Optional[UUID] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        self.message = message
        self.tenant_id = tenant_id
        self.details = details or {}
        super().__init__(message)

    def to_dict(self) -> Dict[str, Any]:
        """Sérialise l'exception pour logging/API."""
        return {
            "error_type": self.__class__.__name__,
            "message": self.message,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "details": self.details,
        }


class TenantNotFoundError(ChatServiceError):
    """Tenant non trouvé ou inactif."""

    def __init__(self, tenant_id: UUID):
        super().__init__(
            message=f"Tenant not found or inactive: {tenant_id}",
            tenant_id=tenant_id,
        )


class ConversationNotFoundError(ChatServiceError):
    """Conversation non trouvée pour ce tenant."""

    def __init__(self, conversation_id: UUID, tenant_id: UUID):
        super().__init__(
            message=f"Conversation not found: {conversation_id}",
            tenant_id=tenant_id,
            details={"conversation_id": str(conversation_id)},
        )
        self.conversation_id = conversation_id


class MessageAlreadyProcessedError(ChatServiceError):
    """
    Message déjà traité (idempotency hit).

    Ce n'est pas vraiment une erreur - permet de retourner le résultat existant.
    """

    def __init__(
        self,
        idempotency_key: UUID,
        tenant_id: UUID,
        existing_message_id: UUID,
    ):
        super().__init__(
            message=f"Message already processed with idempotency_key: {idempotency_key}",
            tenant_id=tenant_id,
            details={
                "idempotency_key": str(idempotency_key),
                "existing_message_id": str(existing_message_id),
            },
        )
        self.idempotency_key = idempotency_key
        self.existing_message_id = existing_message_id


class LLMError(ChatServiceError):
    """Erreur lors de l'appel au LLM."""

    def __init__(
        self,
        message: str,
        tenant_id: UUID,
        conversation_id: Optional[UUID] = None,
        original_error: Optional[Exception] = None,
    ):
        super().__init__(
            message=message,
            tenant_id=tenant_id,
            details={
                "conversation_id": str(conversation_id) if conversation_id else None,
                "original_error": str(original_error) if original_error else None,
            },
        )
        self.conversation_id = conversation_id
        self.original_error = original_error


class LLMTimeoutError(LLMError):
    """Timeout lors de l'appel au LLM."""

    def __init__(
        self,
        tenant_id: UUID,
        conversation_id: Optional[UUID] = None,
        timeout_seconds: float = 30.0,
    ):
        super().__init__(
            message=f"LLM request timed out after {timeout_seconds}s",
            tenant_id=tenant_id,
            conversation_id=conversation_id,
        )
        self.timeout_seconds = timeout_seconds


class LLMRateLimitError(LLMError):
    """Rate limit atteint sur le LLM."""

    def __init__(
        self,
        tenant_id: UUID,
        conversation_id: Optional[UUID] = None,
        retry_after_seconds: Optional[int] = None,
    ):
        super().__init__(
            message="LLM rate limit exceeded",
            tenant_id=tenant_id,
            conversation_id=conversation_id,
        )
        self.retry_after_seconds = retry_after_seconds


class RAGError(ChatServiceError):
    """Erreur lors de la recherche RAG."""

    def __init__(
        self,
        message: str,
        tenant_id: UUID,
        original_error: Optional[Exception] = None,
    ):
        super().__init__(
            message=message,
            tenant_id=tenant_id,
            details={
                "original_error": str(original_error) if original_error else None,
            },
        )
        self.original_error = original_error


class PersistenceError(ChatServiceError):
    """Erreur lors de la persistance en base de données."""

    def __init__(
        self,
        message: str,
        tenant_id: UUID,
        operation: str,  # "create_message", "update_conversation", etc.
        original_error: Optional[Exception] = None,
    ):
        super().__init__(
            message=message,
            tenant_id=tenant_id,
            details={
                "operation": operation,
                "original_error": str(original_error) if original_error else None,
            },
        )
        self.operation = operation
        self.original_error = original_error
