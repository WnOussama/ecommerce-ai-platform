"""
Tests unitaires pour le ChatService.

Tests de l'orchestration chat avec mocks des dépendances.
"""

import pytest
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone

from app.domain.services.chat import (
    ChatService,
    ChatResponse,
    ConversationNotFoundError,
    LLMError,
)
from app.infrastructure.database.models.message import Message, MessageRole
from app.infrastructure.database.models.conversation import Conversation, ConversationStatus


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def tenant_id():
    """Génère un tenant_id pour les tests."""
    return uuid4()


@pytest.fixture
def mock_llm_provider():
    """Crée un mock du LLM provider."""
    provider = MagicMock()
    provider.chat = AsyncMock(return_value="Bonjour ! Comment puis-je vous aider ?")
    provider.get_model_name = MagicMock(return_value="mock-llm")
    return provider


@pytest.fixture
def chat_service(mock_llm_provider):
    """Crée une instance du ChatService avec mock LLM."""
    return ChatService(llm_provider=mock_llm_provider)


# =============================================================================
# UNIT TESTS
# =============================================================================

class TestChatServiceInit:
    """Tests d'initialisation du ChatService."""

    def test_create_with_llm_provider(self, mock_llm_provider):
        """Test création avec LLM provider fourni."""
        service = ChatService(llm_provider=mock_llm_provider)
        assert service._llm_provider == mock_llm_provider

    def test_create_without_llm_provider(self):
        """Test création sans LLM provider (lazy init)."""
        service = ChatService()
        assert service._llm_provider is None


class TestChatServiceProcessMessage:
    """Tests de la méthode process_message."""

    @pytest.mark.asyncio
    async def test_process_message_new_conversation(self, tenant_id, chat_service):
        """Test traitement message avec nouvelle conversation."""
        user_identifier = "test_user"
        message = "Bonjour"
        idempotency_key = uuid4()

        # Mock le UnitOfWork et ses repositories
        mock_conversation = Conversation(
            id=uuid4(),
            tenant_id=tenant_id,
            user_identifier=user_identifier,
            status=ConversationStatus.ACTIVE,
        )

        mock_user_message = Message(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=mock_conversation.id,
            idempotency_key=idempotency_key,
            role=MessageRole.USER,
            content=message,
        )

        mock_assistant_message = Message(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=mock_conversation.id,
            idempotency_key=uuid4(),
            role=MessageRole.ASSISTANT,
            content="Bonjour ! Comment puis-je vous aider ?",
        )

        # Patch le UnitOfWork
        with patch('app.domain.services.chat.chat_service.UnitOfWork') as MockUoW:
            # Setup mock UoW context manager
            mock_uow_instance = AsyncMock()
            mock_uow_instance.__aenter__ = AsyncMock(return_value=mock_uow_instance)
            mock_uow_instance.__aexit__ = AsyncMock(return_value=None)

            # Mock conversations repo
            mock_uow_instance.conversations.get_or_create = AsyncMock(
                return_value=(mock_conversation, True)
            )
            mock_uow_instance.conversations.get_by_id = AsyncMock(
                return_value=mock_conversation
            )
            mock_uow_instance.conversations.get_by_id_for_update = AsyncMock(
                return_value=mock_conversation
            )

            # Mock messages repo
            mock_uow_instance.messages.create_idempotent = AsyncMock(
                side_effect=[
                    (mock_user_message, True),  # Premier appel : user message
                    (mock_assistant_message, True),  # Deuxième appel : assistant message
                ]
            )
            mock_uow_instance.messages.get_by_conversation = AsyncMock(
                return_value=[]  # Pas d'historique
            )

            # Mock analytics repo
            mock_uow_instance.analytics.create_event = AsyncMock()
            mock_uow_instance.analytics.create_chat_event = AsyncMock()

            # Mock commit
            mock_uow_instance.commit = AsyncMock()

            MockUoW.return_value = mock_uow_instance

            # Exécuter
            response = await chat_service.process_message(
                tenant_id=tenant_id,
                message=message,
                user_identifier=user_identifier,
                idempotency_key=idempotency_key,
            )

            # Vérifications
            assert isinstance(response, ChatResponse)
            assert response.conversation_id == mock_conversation.id
            assert response.created is True
            assert response.response == "Bonjour ! Comment puis-je vous aider ?"
            assert response.latency_ms >= 0  # Peut être 0 avec les mocks

    @pytest.mark.asyncio
    async def test_process_message_existing_conversation(self, tenant_id, chat_service):
        """Test traitement message avec conversation existante."""
        conversation_id = uuid4()
        user_identifier = "test_user"
        message = "Quel est le prix ?"
        idempotency_key = uuid4()

        mock_conversation = Conversation(
            id=conversation_id,
            tenant_id=tenant_id,
            user_identifier=user_identifier,
            status=ConversationStatus.ACTIVE,
        )

        mock_user_message = Message(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
            role=MessageRole.USER,
            content=message,
        )

        mock_assistant_message = Message(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            idempotency_key=uuid4(),
            role=MessageRole.ASSISTANT,
            content="Le prix est de 29.99€",
        )

        with patch('app.domain.services.chat.chat_service.UnitOfWork') as MockUoW:
            mock_uow_instance = AsyncMock()
            mock_uow_instance.__aenter__ = AsyncMock(return_value=mock_uow_instance)
            mock_uow_instance.__aexit__ = AsyncMock(return_value=None)

            mock_uow_instance.conversations.get_by_id = AsyncMock(
                return_value=mock_conversation
            )
            mock_uow_instance.conversations.get_by_id_for_update = AsyncMock(
                return_value=mock_conversation
            )

            mock_uow_instance.messages.create_idempotent = AsyncMock(
                side_effect=[
                    (mock_user_message, True),
                    (mock_assistant_message, True),
                ]
            )
            mock_uow_instance.messages.get_by_conversation = AsyncMock(return_value=[])

            mock_uow_instance.analytics.create_chat_event = AsyncMock()
            mock_uow_instance.commit = AsyncMock()

            MockUoW.return_value = mock_uow_instance

            response = await chat_service.process_message(
                tenant_id=tenant_id,
                message=message,
                user_identifier=user_identifier,
                idempotency_key=idempotency_key,
                conversation_id=conversation_id,
            )

            assert response.conversation_id == conversation_id
            assert response.created is False

    @pytest.mark.asyncio
    async def test_process_message_conversation_not_found(self, tenant_id, chat_service):
        """Test erreur si conversation non trouvée."""
        conversation_id = uuid4()

        with patch('app.domain.services.chat.chat_service.UnitOfWork') as MockUoW:
            mock_uow_instance = AsyncMock()
            mock_uow_instance.__aenter__ = AsyncMock(return_value=mock_uow_instance)
            mock_uow_instance.__aexit__ = AsyncMock(return_value=None)

            # Conversation non trouvée
            mock_uow_instance.conversations.get_by_id = AsyncMock(return_value=None)

            MockUoW.return_value = mock_uow_instance

            with pytest.raises(ConversationNotFoundError):
                await chat_service.process_message(
                    tenant_id=tenant_id,
                    message="Test",
                    user_identifier="user",
                    idempotency_key=uuid4(),
                    conversation_id=conversation_id,
                )

    @pytest.mark.asyncio
    async def test_process_message_idempotency_hit(self, tenant_id, chat_service):
        """Test idempotency - message déjà traité."""
        idempotency_key = uuid4()
        conversation_id = uuid4()

        mock_conversation = Conversation(
            id=conversation_id,
            tenant_id=tenant_id,
            user_identifier="user",
            status=ConversationStatus.ACTIVE,
        )

        # Message existant (idempotency hit)
        existing_message = Message(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
            role=MessageRole.USER,
            content="Test",
        )

        mock_assistant_message = Message(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            idempotency_key=uuid4(),
            role=MessageRole.ASSISTANT,
            content="Response",
        )

        with patch('app.domain.services.chat.chat_service.UnitOfWork') as MockUoW:
            mock_uow_instance = AsyncMock()
            mock_uow_instance.__aenter__ = AsyncMock(return_value=mock_uow_instance)
            mock_uow_instance.__aexit__ = AsyncMock(return_value=None)

            mock_uow_instance.conversations.get_or_create = AsyncMock(
                return_value=(mock_conversation, False)
            )
            mock_uow_instance.conversations.get_by_id = AsyncMock(
                return_value=mock_conversation
            )
            mock_uow_instance.conversations.get_by_id_for_update = AsyncMock(
                return_value=mock_conversation
            )

            # Premier appel retourne message existant (created=False)
            mock_uow_instance.messages.create_idempotent = AsyncMock(
                side_effect=[
                    (existing_message, False),  # Idempotency hit
                    (mock_assistant_message, True),
                ]
            )
            mock_uow_instance.messages.get_by_conversation = AsyncMock(return_value=[])

            mock_uow_instance.analytics.create_chat_event = AsyncMock()
            mock_uow_instance.commit = AsyncMock()

            MockUoW.return_value = mock_uow_instance

            # L'appel devrait réussir malgré l'idempotency hit
            response = await chat_service.process_message(
                tenant_id=tenant_id,
                message="Test",
                user_identifier="user",
                idempotency_key=idempotency_key,
            )

            assert response is not None


class TestChatServiceLLM:
    """Tests des appels LLM."""

    @pytest.mark.asyncio
    async def test_llm_error_handling(self, tenant_id):
        """Test gestion erreur LLM."""
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(side_effect=Exception("LLM unavailable"))

        service = ChatService(llm_provider=mock_llm)

        with patch('app.domain.services.chat.chat_service.UnitOfWork') as MockUoW:
            mock_uow_instance = AsyncMock()
            mock_uow_instance.__aenter__ = AsyncMock(return_value=mock_uow_instance)
            mock_uow_instance.__aexit__ = AsyncMock(return_value=None)

            mock_conversation = Conversation(
                id=uuid4(),
                tenant_id=tenant_id,
                user_identifier="user",
                status=ConversationStatus.ACTIVE,
            )

            mock_message = Message(
                id=uuid4(),
                tenant_id=tenant_id,
                conversation_id=mock_conversation.id,
                idempotency_key=uuid4(),
                role=MessageRole.USER,
                content="Test",
            )

            mock_uow_instance.conversations.get_or_create = AsyncMock(
                return_value=(mock_conversation, True)
            )
            mock_uow_instance.messages.create_idempotent = AsyncMock(
                return_value=(mock_message, True)
            )
            mock_uow_instance.messages.get_by_conversation = AsyncMock(return_value=[])
            mock_uow_instance.analytics.create_event = AsyncMock()
            mock_uow_instance.commit = AsyncMock()

            MockUoW.return_value = mock_uow_instance

            with pytest.raises(LLMError) as exc_info:
                await service.process_message(
                    tenant_id=tenant_id,
                    message="Test",
                    user_identifier="user",
                    idempotency_key=uuid4(),
                )

            assert "LLM call failed" in str(exc_info.value.message)


class TestChatServiceHistory:
    """Tests de l'historique conversation."""

    @pytest.mark.asyncio
    async def test_get_conversation_history(self, tenant_id, chat_service):
        """Test récupération historique."""
        conversation_id = uuid4()

        mock_conversation = Conversation(
            id=conversation_id,
            tenant_id=tenant_id,
            user_identifier="user",
            status=ConversationStatus.ACTIVE,
        )

        mock_messages = [
            Message(
                id=uuid4(),
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                idempotency_key=uuid4(),
                role=MessageRole.USER,
                content="Bonjour",
                created_at=datetime.now(timezone.utc),
            ),
            Message(
                id=uuid4(),
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                idempotency_key=uuid4(),
                role=MessageRole.ASSISTANT,
                content="Bonjour !",
                created_at=datetime.now(timezone.utc),
                latency_ms=150,
            ),
        ]

        with patch('app.domain.services.chat.chat_service.UnitOfWork') as MockUoW:
            mock_uow_instance = AsyncMock()
            mock_uow_instance.__aenter__ = AsyncMock(return_value=mock_uow_instance)
            mock_uow_instance.__aexit__ = AsyncMock(return_value=None)

            mock_uow_instance.conversations.get_by_id = AsyncMock(
                return_value=mock_conversation
            )
            mock_uow_instance.messages.get_by_conversation = AsyncMock(
                return_value=mock_messages
            )

            MockUoW.return_value = mock_uow_instance

            history = await chat_service.get_conversation_history(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
            )

            assert len(history) == 2
            assert history[0].role == "user"
            assert history[1].role == "assistant"
            assert history[1].latency_ms == 150

    @pytest.mark.asyncio
    async def test_get_conversation_history_not_found(self, tenant_id, chat_service):
        """Test erreur si conversation non trouvée."""
        conversation_id = uuid4()

        with patch('app.domain.services.chat.chat_service.UnitOfWork') as MockUoW:
            mock_uow_instance = AsyncMock()
            mock_uow_instance.__aenter__ = AsyncMock(return_value=mock_uow_instance)
            mock_uow_instance.__aexit__ = AsyncMock(return_value=None)

            mock_uow_instance.conversations.get_by_id = AsyncMock(return_value=None)

            MockUoW.return_value = mock_uow_instance

            with pytest.raises(ConversationNotFoundError):
                await chat_service.get_conversation_history(
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                )



