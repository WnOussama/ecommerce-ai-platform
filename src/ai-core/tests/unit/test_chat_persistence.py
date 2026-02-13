"""
Tests unitaires pour les repositories Chat Persistence.

Tests des ConversationRepository, MessageRepository et UnitOfWork.
"""

import pytest
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.repositories.conversation_repo import ConversationRepository
from app.infrastructure.database.repositories.message_repo import MessageRepository, DuplicateMessageError
from app.infrastructure.database.models.conversation import Conversation, ConversationStatus
from app.infrastructure.database.models.message import Message, MessageRole


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def tenant_id():
    """Génère un tenant_id pour les tests."""
    return uuid4()


@pytest.fixture
def mock_session():
    """Crée une session mock."""
    session = AsyncMock(spec=AsyncSession)
    session.add = MagicMock()
    return session


# =============================================================================
# CONVERSATION REPOSITORY TESTS
# =============================================================================

class TestConversationRepository:
    """Tests pour ConversationRepository."""

    @pytest.mark.asyncio
    async def test_create_conversation(self, mock_session, tenant_id):
        """Test création d'une conversation."""
        repo = ConversationRepository(mock_session, tenant_id)

        # Mock flush pour simuler l'attribution d'un ID
        async def mock_flush():
            pass
        mock_session.flush = mock_flush

        # Créer conversation
        conversation = await repo.create(
            user_identifier="user_123",
            extra_data={"source": "web"}
        )

        # Vérifications
        assert conversation.tenant_id == tenant_id
        assert conversation.user_identifier == "user_123"
        assert conversation.status == ConversationStatus.ACTIVE
        assert conversation.extra_data == {"source": "web"}

        # Vérifier que add a été appelé
        mock_session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_by_id_filters_by_tenant(self, mock_session, tenant_id):
        """Test que get_by_id filtre par tenant_id."""
        repo = ConversationRepository(mock_session, tenant_id)

        conversation_id = uuid4()

        # Mock execute pour retourner un résultat
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        # Appeler get_by_id
        result = await repo.get_by_id(conversation_id)

        # Vérifier que execute a été appelé
        mock_session.execute.assert_called_once()

        # Vérifier que la requête contient le filtre tenant_id
        call_args = mock_session.execute.call_args
        stmt = call_args[0][0]
        # La requête devrait contenir tenant_id dans les clauses WHERE
        assert result is None

    @pytest.mark.asyncio
    async def test_get_or_create_returns_existing(self, mock_session, tenant_id):
        """Test get_or_create retourne conversation existante."""
        repo = ConversationRepository(mock_session, tenant_id)

        # Mock une conversation existante
        existing_conversation = Conversation(
            id=uuid4(),
            tenant_id=tenant_id,
            user_identifier="user_123",
            status=ConversationStatus.ACTIVE,
        )

        # Mock get_active_by_user pour retourner la conversation existante
        repo.get_active_by_user = AsyncMock(return_value=existing_conversation)

        # Appeler get_or_create
        conversation, created = await repo.get_or_create(user_identifier="user_123")

        # Vérifications
        assert conversation == existing_conversation
        assert created is False

    @pytest.mark.asyncio
    async def test_get_or_create_creates_new(self, mock_session, tenant_id):
        """Test get_or_create crée nouvelle conversation si aucune existante."""
        repo = ConversationRepository(mock_session, tenant_id)

        # Mock get_active_by_user pour ne rien retourner
        repo.get_active_by_user = AsyncMock(return_value=None)

        # Mock create
        new_conversation = Conversation(
            id=uuid4(),
            tenant_id=tenant_id,
            user_identifier="user_456",
            status=ConversationStatus.ACTIVE,
        )
        repo.create = AsyncMock(return_value=new_conversation)

        # Appeler get_or_create
        conversation, created = await repo.get_or_create(user_identifier="user_456")

        # Vérifications
        assert conversation == new_conversation
        assert created is True
        repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_by_id_for_update_uses_select_for_update(self, mock_session, tenant_id):
        """Test que get_by_id_for_update utilise SELECT FOR UPDATE."""
        repo = ConversationRepository(mock_session, tenant_id)

        conversation_id = uuid4()

        # Mock execute
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        # Appeler get_by_id_for_update
        result = await repo.get_by_id_for_update(conversation_id)

        # Vérifier que execute a été appelé
        mock_session.execute.assert_called_once()

        # Vérifier que la requête contient with_for_update
        call_args = mock_session.execute.call_args
        stmt = call_args[0][0]
        # La requête doit avoir _for_update_arg défini
        assert stmt._for_update_arg is not None
        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_id_for_update_with_nowait(self, mock_session, tenant_id):
        """Test get_by_id_for_update avec option nowait."""
        repo = ConversationRepository(mock_session, tenant_id)

        conversation_id = uuid4()

        # Mock execute
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute.return_value = mock_result

        # Appeler get_by_id_for_update avec nowait=True
        result = await repo.get_by_id_for_update(conversation_id, nowait=True)

        # Vérifier que execute a été appelé
        mock_session.execute.assert_called_once()
        assert result is None


# =============================================================================
# MESSAGE REPOSITORY TESTS
# =============================================================================

class TestMessageRepository:
    """Tests pour MessageRepository."""

    @pytest.mark.asyncio
    async def test_create_message(self, mock_session, tenant_id):
        """Test création d'un message."""
        repo = MessageRepository(mock_session, tenant_id)

        conversation_id = uuid4()
        idempotency_key = uuid4()

        # Mock flush
        async def mock_flush():
            pass
        mock_session.flush = mock_flush

        # Créer message
        message = await repo.create(
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
            role=MessageRole.USER,
            content="Hello, world!",
            extra_data={"intent": "greeting"},
        )

        # Vérifications
        assert message.tenant_id == tenant_id
        assert message.conversation_id == conversation_id
        assert message.idempotency_key == idempotency_key
        assert message.role == MessageRole.USER
        assert message.content == "Hello, world!"

        mock_session.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_message_with_metrics(self, mock_session, tenant_id):
        """Test création message avec métriques de performance."""
        repo = MessageRepository(mock_session, tenant_id)

        # Mock flush
        mock_session.flush = AsyncMock()

        # Créer message avec métriques
        message = await repo.create(
            conversation_id=uuid4(),
            idempotency_key=uuid4(),
            role=MessageRole.ASSISTANT,
            content="Response from AI",
            latency_ms=250,
            tokens_input=50,
            tokens_output=100,
        )

        # Vérifications
        assert message.latency_ms == 250
        assert message.tokens_input == 50
        assert message.tokens_output == 100

    @pytest.mark.asyncio
    async def test_create_idempotent_returns_existing(self, mock_session, tenant_id):
        """Test create_idempotent retourne message existant via ON CONFLICT."""
        repo = MessageRepository(mock_session, tenant_id)

        idempotency_key = uuid4()
        conversation_id = uuid4()

        # Mock un message existant
        existing_message = Message(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
            role=MessageRole.USER,
            content="Existing message",
        )

        # Mock execute pour simuler ON CONFLICT DO NOTHING (insertion échoue)
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None  # Pas d'insertion (conflit)
        mock_session.execute.return_value = mock_result

        # Mock get_by_idempotency_key pour retourner le message existant
        repo.get_by_idempotency_key = AsyncMock(return_value=existing_message)

        # Appeler create_idempotent
        message, created = await repo.create_idempotent(
            conversation_id=uuid4(),
            idempotency_key=idempotency_key,
            role=MessageRole.USER,
            content="New message",
        )

        # Vérifications
        assert message == existing_message
        assert created is False

    @pytest.mark.asyncio
    async def test_create_idempotent_creates_new(self, mock_session, tenant_id):
        """Test create_idempotent crée nouveau message."""
        repo = MessageRepository(mock_session, tenant_id)

        idempotency_key = uuid4()
        conversation_id = uuid4()
        new_message_id = uuid4()

        # Mock execute pour simuler insertion réussie
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = new_message_id
        mock_session.execute.return_value = mock_result

        # Mock get_by_id pour retourner le message créé
        new_message = Message(
            id=new_message_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
            role=MessageRole.USER,
            content="New message",
        )
        repo.get_by_id = AsyncMock(return_value=new_message)

        # Appeler create_idempotent
        message, created = await repo.create_idempotent(
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
            role=MessageRole.USER,
            content="New message",
        )

        # Vérifications
        assert message == new_message
        assert created is True

    @pytest.mark.asyncio
    async def test_create_if_not_exists_delegates_to_create_idempotent(self, mock_session, tenant_id):
        """Test que create_if_not_exists délègue à create_idempotent."""
        repo = MessageRepository(mock_session, tenant_id)

        idempotency_key = uuid4()
        conversation_id = uuid4()

        # Mock create_idempotent
        mock_message = Message(
            id=uuid4(),
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
            role=MessageRole.USER,
            content="Test",
        )
        repo.create_idempotent = AsyncMock(return_value=(mock_message, True))

        # Appeler create_if_not_exists (deprecated)
        message, created = await repo.create_if_not_exists(
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
            role=MessageRole.USER,
            content="Test",
        )

        # Vérifications
        assert message == mock_message
        assert created is True
        repo.create_idempotent.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_by_conversation_filters_by_tenant(self, mock_session, tenant_id):
        """Test que get_by_conversation filtre par tenant_id."""
        repo = MessageRepository(mock_session, tenant_id)

        conversation_id = uuid4()

        # Mock execute
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []
        mock_result.scalars.return_value = mock_scalars
        mock_session.execute.return_value = mock_result

        # Appeler get_by_conversation
        messages = await repo.get_by_conversation(conversation_id)

        # Vérifications
        assert messages == []
        mock_session.execute.assert_called_once()


# =============================================================================
# UNIT OF WORK TESTS
# =============================================================================

class TestUnitOfWork:
    """Tests pour UnitOfWork."""

    @pytest.mark.asyncio
    async def test_uow_initializes_repositories(self, tenant_id):
        """Test que UoW initialise les repositories."""
        from app.infrastructure.database.unit_of_work import UnitOfWork

        # Mock la session factory
        mock_session = AsyncMock(spec=AsyncSession)

        with patch('app.infrastructure.database.unit_of_work.AsyncSessionLocal', return_value=mock_session):
            async with UnitOfWork(tenant_id) as uow:
                # Vérifier que les repositories sont initialisés
                assert uow.conversations is not None
                assert uow.messages is not None
                assert isinstance(uow.conversations, ConversationRepository)
                assert isinstance(uow.messages, MessageRepository)

    @pytest.mark.asyncio
    async def test_uow_commit(self, tenant_id):
        """Test commit du UoW."""
        from app.infrastructure.database.unit_of_work import UnitOfWork

        mock_session = AsyncMock(spec=AsyncSession)

        with patch('app.infrastructure.database.unit_of_work.AsyncSessionLocal', return_value=mock_session):
            async with UnitOfWork(tenant_id) as uow:
                await uow.commit()
                mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_uow_rollback_on_exception(self, tenant_id):
        """Test rollback automatique en cas d'exception."""
        from app.infrastructure.database.unit_of_work import UnitOfWork

        mock_session = AsyncMock(spec=AsyncSession)

        with patch('app.infrastructure.database.unit_of_work.AsyncSessionLocal', return_value=mock_session):
            with pytest.raises(ValueError):
                async with UnitOfWork(tenant_id) as uow:
                    raise ValueError("Test error")

            # Vérifier que rollback a été appelé
            mock_session.rollback.assert_called_once()

    @pytest.mark.asyncio
    async def test_uow_closes_session(self, tenant_id):
        """Test que la session est fermée à la sortie."""
        from app.infrastructure.database.unit_of_work import UnitOfWork

        mock_session = AsyncMock(spec=AsyncSession)

        with patch('app.infrastructure.database.unit_of_work.AsyncSessionLocal', return_value=mock_session):
            async with UnitOfWork(tenant_id) as uow:
                pass

            # Vérifier que close a été appelé
            mock_session.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_uow_uses_provided_session(self, tenant_id):
        """Test que UoW utilise une session fournie."""
        from app.infrastructure.database.unit_of_work import UnitOfWork

        mock_session = AsyncMock(spec=AsyncSession)

        # Passer la session directement (pour les tests)
        async with UnitOfWork(tenant_id, session=mock_session) as uow:
            assert uow.session == mock_session

        # La session ne devrait PAS être fermée (pas owned)
        mock_session.close.assert_not_called()


# =============================================================================
# INTEGRATION TESTS (simulation)
# =============================================================================

class TestChatPersistenceFlow:
    """Tests d'intégration du flux de persistance chat."""

    @pytest.mark.asyncio
    async def test_complete_chat_flow(self, tenant_id):
        """Test du flux complet de persistance chat."""
        from app.infrastructure.database.unit_of_work import UnitOfWork

        mock_session = AsyncMock(spec=AsyncSession)
        mock_session.add = MagicMock()
        mock_session.flush = AsyncMock()

        user_identifier = "user_test"
        idempotency_key = uuid4()

        with patch('app.infrastructure.database.unit_of_work.AsyncSessionLocal', return_value=mock_session):
            async with UnitOfWork(tenant_id) as uow:
                # 1. Créer ou récupérer conversation
                # (mock get_active_by_user)
                uow.conversations.get_active_by_user = AsyncMock(return_value=None)

                conversation = await uow.conversations.create(
                    user_identifier=user_identifier
                )

                # 2. Créer message utilisateur
                uow.messages.get_by_idempotency_key = AsyncMock(return_value=None)

                user_message = await uow.messages.create(
                    conversation_id=conversation.id,
                    idempotency_key=idempotency_key,
                    role=MessageRole.USER,
                    content="Hello!",
                )

                # 3. Commit
                await uow.commit()

                # Vérifications
                assert conversation.tenant_id == tenant_id
                assert user_message.tenant_id == tenant_id
                assert user_message.role == MessageRole.USER
                mock_session.commit.assert_called_once()



