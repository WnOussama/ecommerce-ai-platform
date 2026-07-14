"""
Chat Service Module - Orchestration des interactions chat

Ce module expose le ChatService et les DTOs associés.

Usage:
    from app.domain.services.chat import ChatService, ChatRequest, ChatResponse

    service = ChatService()
    response = await service.process_message(
        tenant_id=tenant_id,
        message="Bonjour",
        user_identifier="session_123",
        idempotency_key=uuid4(),
    )
"""

from app.domain.services.chat.chat_service import ChatService, get_chat_service
from app.domain.services.chat.dto import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
    ConversationHistory,
)
from app.domain.services.chat.exceptions import (
    ChatServiceError,
    ConversationNotFoundError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    MessageAlreadyProcessedError,
    PersistenceError,
    RAGError,
    TenantNotFoundError,
)

__all__ = [
    # Service
    "ChatService",
    "get_chat_service",
    # DTOs
    "ChatRequest",
    "ChatResponse",
    "ChatMessage",
    "ConversationHistory",
    # Exceptions
    "ChatServiceError",
    "TenantNotFoundError",
    "ConversationNotFoundError",
    "MessageAlreadyProcessedError",
    "LLMError",
    "LLMTimeoutError",
    "LLMRateLimitError",
    "RAGError",
    "PersistenceError",
]
