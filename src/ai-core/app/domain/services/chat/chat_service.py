"""
Chat Service - Orchestration des interactions chat

Ce service gère le flux complet d'une interaction chat :
1. Transaction 1 : Persistance message utilisateur
2. Appel LLM hors transaction (avec RAG si disponible)
3. Transaction 2 : Persistance réponse + analytics

Architecture:
- Aucune logique SQL directe
- Passage obligatoire par repositories
- Gestion transactionnelle via UnitOfWork
- Idempotency via idempotency_key
- Multi-tenant strict

FLUX TRANSACTIONNEL:
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│  Transaction 1 (UoW)                                                       │
│  ├── Valider tenant                                                        │
│  ├── Get or create conversation                                            │
│  ├── Vérifier idempotency_key (retourne si existe)                        │
│  ├── Créer Message user                                                    │
│  └── COMMIT                                                                │
│                                                                             │
│  [Hors transaction - Appel LLM]                                            │
│  ├── Charger historique conversation                                       │
│  ├── Appeler RAG pour contexte produits                                   │
│  ├── Appeler LLM avec contexte                                            │
│  └── Mesurer latence                                                       │
│                                                                             │
│  Transaction 2 (UoW)                                                       │
│  ├── Créer Message assistant                                               │
│  ├── Mettre à jour conversation.updated_at                                 │
│  ├── Créer AnalyticsEvent                                                  │
│  └── COMMIT                                                                │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
"""

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from app.domain.services.chat.dto import (
    ChatMessage,
    ChatResponse,
)
from app.domain.services.chat.exceptions import (
    ConversationNotFoundError,
    LLMError,
    LLMTimeoutError,
)
from app.infrastructure.database.models.message import MessageRole
from app.infrastructure.database.unit_of_work import UnitOfWork
from app.infrastructure.llm.provider_factory import BaseLLMProvider, get_llm_provider

logger = logging.getLogger(__name__)


class ChatService:
    """
    Service d'orchestration pour le chat.

    Gère le flux complet d'une interaction chat avec :
    - Multi-tenant isolation
    - Idempotency support
    - Persistance transactionnelle
    - Analytics tracking

    Usage:
        service = ChatService()
        response = await service.process_message(
            tenant_id=tenant_id,
            message="Bonjour",
            conversation_id=None,  # Crée nouvelle conversation
            idempotency_key=uuid4(),
            user_identifier="session_123",
        )
    """

    # Configuration
    MAX_HISTORY_MESSAGES = 20  # Messages à charger pour contexte LLM
    LLM_TIMEOUT_SECONDS = 30.0
    DEFAULT_SYSTEM_PROMPT = """Tu es un assistant virtuel pour une boutique en ligne.
Tu aides les clients avec leurs questions sur les produits, les commandes, les retours et la livraison.
Sois poli, concis et utile. Réponds en français."""

    def __init__(
        self,
        llm_provider: Optional[BaseLLMProvider] = None,
    ):
        """
        Initialise le service.

        Args:
            llm_provider: Provider LLM (si None, utilise le provider par défaut)
        """
        self._llm_provider = llm_provider

    @property
    def llm_provider(self) -> BaseLLMProvider:
        """Retourne le LLM provider (lazy initialization)."""
        if self._llm_provider is None:
            self._llm_provider = get_llm_provider()
        return self._llm_provider

    # =========================================================================
    # MAIN ENTRY POINT
    # =========================================================================

    async def process_message(
        self,
        tenant_id: UUID,
        message: str,
        user_identifier: str,
        idempotency_key: UUID,
        conversation_id: Optional[UUID] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> ChatResponse:
        """
        Traite un message utilisateur et génère une réponse.

        Flux:
        1. Transaction 1 : Persiste le message utilisateur
        2. Hors transaction : Appelle le LLM
        3. Transaction 2 : Persiste la réponse + analytics

        Args:
            tenant_id: UUID du tenant
            message: Message de l'utilisateur
            user_identifier: Identifiant utilisateur (session, customer, etc.)
            idempotency_key: UUID unique pour éviter double traitement
            conversation_id: UUID conversation existante (None = nouvelle)
            context: Contexte additionnel (page, produit, etc.)

        Returns:
            ChatResponse avec la réponse et métadonnées

        Raises:
            ChatServiceError: Erreur métier (tenant, conversation, etc.)
            LLMError: Erreur lors de l'appel LLM
            PersistenceError: Erreur de persistance DB
        """
        start_time = time.time()

        logger.info(
            "Processing chat message",
            extra={
                "tenant_id": str(tenant_id),
                "conversation_id": str(conversation_id) if conversation_id else "new",
                "idempotency_key": str(idempotency_key),
                "message_length": len(message),
            },
        )

        # =====================================================================
        # TRANSACTION 1 : Persistance message utilisateur
        # =====================================================================

        (
            conversation,
            user_message,
            conversation_created,
            message_created,
        ) = await self._persist_user_message(
            tenant_id=tenant_id,
            message=message,
            user_identifier=user_identifier,
            idempotency_key=idempotency_key,
            conversation_id=conversation_id,
            context=context,
        )

        # =====================================================================
        # IDEMPOTENCY SHORT-CIRCUIT : Éviter double appel LLM
        # =====================================================================

        if not message_created:
            # Le message user existait déjà (idempotency hit)
            # Chercher si une réponse assistant existe
            existing_response = await self._find_assistant_response(
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                after_message_id=user_message.id,
            )

            if existing_response:
                # Réponse existante trouvée - retourner immédiatement
                total_latency_ms = int((time.time() - start_time) * 1000)

                logger.info(
                    "Idempotency hit - returning cached response (no LLM call)",
                    extra={
                        "tenant_id": str(tenant_id),
                        "conversation_id": str(conversation.id),
                        "idempotency_key": str(idempotency_key),
                        "cached_message_id": str(existing_response.id),
                    },
                )

                return ChatResponse(
                    conversation_id=conversation.id,
                    message_id=existing_response.id,
                    response=existing_response.content,
                    created=False,
                    latency_ms=total_latency_ms,
                    metadata={
                        "llm_latency_ms": existing_response.latency_ms or 0,
                        "tokens_input": existing_response.tokens_input or 0,
                        "tokens_output": existing_response.tokens_output or 0,
                        "rag_products_found": 0,
                        "cached": True,  # Indique que c'est une réponse cachée
                    },
                )

            # Pas de réponse existante - cas de retry après échec LLM
            logger.info(
                "Idempotency hit but no assistant response - proceeding with LLM call (retry scenario)",
                extra={
                    "tenant_id": str(tenant_id),
                    "conversation_id": str(conversation.id),
                    "idempotency_key": str(idempotency_key),
                },
            )

        # =====================================================================
        # HORS TRANSACTION : Appel LLM
        # =====================================================================

        (
            llm_response,
            llm_latency_ms,
            tokens_input,
            tokens_output,
            rag_context,
        ) = await self._call_llm(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            user_message=message,
        )

        # =====================================================================
        # TRANSACTION 2 : Persistance réponse + analytics
        # =====================================================================

        assistant_message = await self._persist_assistant_response(
            tenant_id=tenant_id,
            conversation_id=conversation.id,
            response=llm_response,
            latency_ms=llm_latency_ms,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            rag_products_count=rag_context.get("products_count", 0) if rag_context else 0,
        )

        # =====================================================================
        # CONSTRUCTION RÉPONSE
        # =====================================================================

        total_latency_ms = int((time.time() - start_time) * 1000)

        response = ChatResponse(
            conversation_id=conversation.id,
            message_id=assistant_message.id,
            response=llm_response,
            created=conversation_created,
            latency_ms=total_latency_ms,
            metadata={
                "llm_latency_ms": llm_latency_ms,
                "tokens_input": tokens_input,
                "tokens_output": tokens_output,
                "rag_products_found": rag_context.get("products_count", 0) if rag_context else 0,
            },
        )

        logger.info(
            "Chat message processed successfully",
            extra={
                "tenant_id": str(tenant_id),
                "conversation_id": str(conversation.id),
                "message_id": str(assistant_message.id),
                "latency_ms": total_latency_ms,
                "llm_latency_ms": llm_latency_ms,
            },
        )

        return response

    # =========================================================================
    # TRANSACTION 1 : PERSIST USER MESSAGE
    # =========================================================================

    async def _persist_user_message(
        self,
        tenant_id: UUID,
        message: str,
        user_identifier: str,
        idempotency_key: UUID,
        conversation_id: Optional[UUID],
        context: Optional[Dict[str, Any]],
    ) -> tuple:
        """
        Transaction 1 : Persiste le message utilisateur.

        Returns:
            Tuple (conversation, user_message, conversation_created, message_created)
        """
        async with UnitOfWork(tenant_id) as uow:
            # 1. Get or create conversation
            if conversation_id:
                conversation = await uow.conversations.get_by_id(conversation_id)
                if not conversation:
                    raise ConversationNotFoundError(conversation_id, tenant_id)
                conversation_created = False
            else:
                conversation, conversation_created = await uow.conversations.get_or_create(
                    user_identifier=user_identifier,
                    extra_data=context,
                )

            # 2. Créer message user (idempotent)
            # Si idempotency_key existe déjà, retourne le message existant
            user_message, message_created = await uow.messages.create_idempotent(
                conversation_id=conversation.id,
                idempotency_key=idempotency_key,
                role=MessageRole.USER,
                content=message,
                extra_data=context,
            )

            # Si le message existait déjà, c'est un retry - on log mais on continue
            if not message_created:
                logger.info(
                    "Idempotency hit - user message already exists",
                    extra={
                        "tenant_id": str(tenant_id),
                        "idempotency_key": str(idempotency_key),
                        "message_id": str(user_message.id),
                    },
                )

            # 3. Log analytics si nouvelle conversation
            if conversation_created:
                await uow.analytics.create_event(
                    event_type="conversation_started",
                    entity_type="conversation",
                    entity_id=conversation.id,
                    payload={"user_identifier": user_identifier},
                )

            # 4. Commit
            await uow.commit()

            return conversation, user_message, conversation_created, message_created

    # =========================================================================
    # LLM CALL (HORS TRANSACTION)
    # =========================================================================

    async def _call_llm(
        self,
        tenant_id: UUID,
        conversation_id: UUID,
        user_message: str,
    ) -> tuple:
        """
        Appelle le LLM avec contexte conversation et RAG.

        Returns:
            Tuple (response, latency_ms, tokens_input, tokens_output, rag_context)
        """
        start_time = time.time()
        rag_context = None

        try:
            # 1. Charger historique conversation
            await self._load_conversation_history(tenant_id, conversation_id)

            # 2. Construire le contexte RAG (optionnel, skip si erreur)
            try:
                rag_context = await self._get_rag_context(tenant_id, user_message)
            except Exception as e:
                logger.warning(
                    "RAG context retrieval failed, continuing without",
                    extra={
                        "tenant_id": str(tenant_id),
                        "error": str(e),
                    },
                )
                rag_context = None

            # 4. Appeler le LLM
            response = await self.llm_provider.chat(
                message=user_message,
                context=rag_context.get("context_string") if rag_context else None,
            )

            latency_ms = int((time.time() - start_time) * 1000)

            # Le mock provider retourne juste une string
            if isinstance(response, str):
                return response, latency_ms, 0, 0, rag_context

            # Si response est un dict avec usage
            if isinstance(response, dict):
                return (
                    response.get("content", response),
                    latency_ms,
                    response.get("tokens_input", 0),
                    response.get("tokens_output", 0),
                    rag_context,
                )

            return str(response), latency_ms, 0, 0, rag_context

        except asyncio.TimeoutError:
            raise LLMTimeoutError(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                timeout_seconds=self.LLM_TIMEOUT_SECONDS,
            )
        except Exception as e:
            logger.error(
                "LLM call failed",
                extra={
                    "tenant_id": str(tenant_id),
                    "conversation_id": str(conversation_id),
                    "error": str(e),
                },
            )
            raise LLMError(
                message=f"LLM call failed: {str(e)}",
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                original_error=e,
            )

    async def _load_conversation_history(
        self,
        tenant_id: UUID,
        conversation_id: UUID,
    ) -> List[Dict[str, str]]:
        """Charge l'historique de conversation pour contexte LLM."""
        async with UnitOfWork(tenant_id) as uow:
            messages = await uow.messages.get_by_conversation(
                conversation_id=conversation_id,
                limit=self.MAX_HISTORY_MESSAGES,
                order_asc=True,
            )

            return [{"role": msg.role.value, "content": msg.content} for msg in messages]

    async def _find_assistant_response(
        self,
        tenant_id: UUID,
        conversation_id: UUID,
        after_message_id: UUID,
    ) -> Optional[Any]:
        """
        Cherche une réponse assistant existante pour un message user donné.

        Utilisé pour le short-circuit idempotency : si le user message existait
        déjà (idempotency hit), on cherche si une réponse a déjà été générée.

        Args:
            tenant_id: UUID du tenant
            conversation_id: UUID de la conversation
            after_message_id: ID du message user après lequel chercher

        Returns:
            Message assistant si trouvé, None sinon

        Note:
            Cette méthode passe par le repository pour respecter
            l'isolation multi-tenant et éviter le SQL direct.
        """
        async with UnitOfWork(tenant_id) as uow:
            # Récupérer les messages de la conversation (ordre chronologique)
            messages = await uow.messages.get_by_conversation(
                conversation_id=conversation_id,
                limit=10,  # On ne cherche que dans les messages récents
                order_asc=True,
            )

            # Chercher le premier message assistant après le user message
            found_user_message = False
            for msg in messages:
                if msg.id == after_message_id:
                    found_user_message = True
                    continue

                if found_user_message and msg.role == MessageRole.ASSISTANT:
                    logger.debug(
                        "Found existing assistant response",
                        extra={
                            "tenant_id": str(tenant_id),
                            "conversation_id": str(conversation_id),
                            "user_message_id": str(after_message_id),
                            "assistant_message_id": str(msg.id),
                        },
                    )
                    return msg

            return None

    async def _get_rag_context(
        self,
        tenant_id: UUID,
        query: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Récupère le contexte RAG pour enrichir la réponse.

        Returns:
            Dict avec context_string et products_count, ou None
        """
        # Pour l'instant, on retourne None car le RAG n'est pas encore connecté
        # TODO: Intégrer avec ProductRetrievalService
        return None

    def _build_llm_messages(
        self,
        history: List[Dict[str, str]],
        user_message: str,
        rag_context: Optional[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        """Construit la liste de messages pour le LLM."""
        messages = [{"role": "system", "content": self.DEFAULT_SYSTEM_PROMPT}]

        # Ajouter contexte RAG si disponible
        if rag_context and rag_context.get("context_string"):
            messages.append(
                {
                    "role": "system",
                    "content": f"Contexte produits:\n{rag_context['context_string']}",
                }
            )

        # Ajouter historique
        messages.extend(history)

        return messages

    # =========================================================================
    # TRANSACTION 2 : PERSIST ASSISTANT RESPONSE
    # =========================================================================

    async def _persist_assistant_response(
        self,
        tenant_id: UUID,
        conversation_id: UUID,
        response: str,
        latency_ms: int,
        tokens_input: int,
        tokens_output: int,
        rag_products_count: int,
    ):
        """
        Transaction 2 : Persiste la réponse assistant + analytics.

        Returns:
            Message assistant créé
        """
        # Générer un nouvel idempotency_key pour le message assistant
        assistant_idempotency_key = uuid4()

        async with UnitOfWork(tenant_id) as uow:
            # 1. Créer message assistant
            assistant_message, _ = await uow.messages.create_idempotent(
                conversation_id=conversation_id,
                idempotency_key=assistant_idempotency_key,
                role=MessageRole.ASSISTANT,
                content=response,
                latency_ms=latency_ms,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                extra_data={
                    "rag_products_count": rag_products_count,
                },
            )

            # 2. Mettre à jour conversation.updated_at
            conversation = await uow.conversations.get_by_id_for_update(conversation_id)
            if conversation:
                conversation.updated_at = datetime.now(timezone.utc)

            # 3. Créer analytics event
            await uow.analytics.create_chat_event(
                conversation_id=conversation_id,
                message_role="assistant",
                latency_ms=latency_ms,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
                rag_products_count=rag_products_count,
            )

            # 4. Commit
            await uow.commit()

            return assistant_message

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    async def get_conversation_history(
        self,
        tenant_id: UUID,
        conversation_id: UUID,
        limit: int = 50,
    ) -> List[ChatMessage]:
        """
        Récupère l'historique d'une conversation.

        Args:
            tenant_id: UUID du tenant
            conversation_id: UUID de la conversation
            limit: Nombre maximum de messages

        Returns:
            Liste de ChatMessage
        """
        async with UnitOfWork(tenant_id) as uow:
            conversation = await uow.conversations.get_by_id(conversation_id)
            if not conversation:
                raise ConversationNotFoundError(conversation_id, tenant_id)

            messages = await uow.messages.get_by_conversation(
                conversation_id=conversation_id,
                limit=limit,
                order_asc=True,
            )

            return [
                ChatMessage(
                    id=msg.id,
                    role=msg.role.value,
                    content=msg.content,
                    created_at=msg.created_at,
                    latency_ms=msg.latency_ms,
                    tokens_input=msg.tokens_input,
                    tokens_output=msg.tokens_output,
                )
                for msg in messages
            ]


# =============================================================================
# FACTORY FUNCTION
# =============================================================================


def get_chat_service() -> ChatService:
    """
    Factory pour obtenir une instance du ChatService.

    Returns:
        Instance de ChatService configurée
    """
    return ChatService()
