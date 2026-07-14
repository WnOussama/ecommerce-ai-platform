"""
Message Repository - CRUD operations pour messages.

Repository simple avec isolation multi-tenant obligatoire.
Supporte idempotency pour éviter double insertion via INSERT ON CONFLICT.

GARANTIES IDEMPOTENCY:
- Utilise INSERT ... ON CONFLICT DO NOTHING (PostgreSQL)
- Zéro race condition même avec requêtes concurrentes
- Retourne toujours le message (existant ou créé)
- Aucun IntegrityError exposé au service layer
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.message import Message, MessageRole

logger = logging.getLogger(__name__)


class DuplicateMessageError(Exception):
    """Exception levée si un message avec la même idempotency_key existe déjà."""

    pass


class MessageRepository:
    """
    Repository pour les messages.

    TOUTES les queries sont filtrées par tenant_id.
    Supporte idempotency via idempotency_key unique.

    Usage:
        repo = MessageRepository(session, tenant_id)
        message = await repo.create(conversation_id, role, content, idempotency_key)
        messages = await repo.get_by_conversation(conversation_id)
    """

    def __init__(self, session: AsyncSession, tenant_id: UUID):
        """
        Initialise le repository.

        Args:
            session: Session SQLAlchemy async
            tenant_id: UUID du tenant (obligatoire)
        """
        self._session = session
        self._tenant_id = tenant_id

    @property
    def tenant_id(self) -> UUID:
        """Retourne le tenant_id (read-only)."""
        return self._tenant_id

    # =========================================================================
    # READ OPERATIONS
    # =========================================================================

    async def get_by_id(self, message_id: UUID) -> Optional[Message]:
        """
        Récupère un message par ID.

        Args:
            message_id: UUID du message

        Returns:
            Message ou None si non trouvé
        """
        stmt = select(Message).where(
            and_(
                Message.tenant_id == self._tenant_id,
                Message.id == message_id,
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_idempotency_key(self, idempotency_key: UUID) -> Optional[Message]:
        """
        Récupère un message par idempotency_key.

        Utilisé pour vérifier si un message existe déjà avant création.

        Args:
            idempotency_key: UUID d'idempotence

        Returns:
            Message existant ou None
        """
        stmt = select(Message).where(
            and_(
                Message.tenant_id == self._tenant_id,
                Message.idempotency_key == idempotency_key,
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_conversation(
        self,
        conversation_id: UUID,
        limit: int = 50,
        offset: int = 0,
        order_asc: bool = True,
    ) -> List[Message]:
        """
        Récupère les messages d'une conversation.

        Args:
            conversation_id: UUID de la conversation
            limit: Nombre maximum de résultats
            offset: Décalage pour pagination
            order_asc: Si True, ordre chronologique (ancien → récent)

        Returns:
            Liste de messages
        """
        stmt = select(Message).where(
            and_(
                Message.tenant_id == self._tenant_id,
                Message.conversation_id == conversation_id,
            )
        )

        if order_asc:
            stmt = stmt.order_by(Message.created_at.asc())
        else:
            stmt = stmt.order_by(Message.created_at.desc())

        stmt = stmt.offset(offset).limit(limit)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_conversation(self, conversation_id: UUID) -> int:
        """
        Compte les messages d'une conversation.

        Args:
            conversation_id: UUID de la conversation

        Returns:
            Nombre de messages
        """
        from sqlalchemy import func

        stmt = select(func.count(Message.id)).where(
            and_(
                Message.tenant_id == self._tenant_id,
                Message.conversation_id == conversation_id,
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar() or 0

    # =========================================================================
    # WRITE OPERATIONS
    # =========================================================================

    async def create(
        self,
        conversation_id: UUID,
        idempotency_key: UUID,
        role: MessageRole,
        content: str,
        extra_data: Optional[dict] = None,
        latency_ms: Optional[int] = None,
        tokens_input: Optional[int] = None,
        tokens_output: Optional[int] = None,
    ) -> Message:
        """
        Crée un nouveau message.

        Args:
            conversation_id: UUID de la conversation
            idempotency_key: UUID unique pour éviter double insertion
            role: Rôle de l'émetteur (USER, ASSISTANT, SYSTEM)
            content: Contenu du message
            extra_data: Métadonnées optionnelles
            latency_ms: Temps de réponse (pour ASSISTANT)
            tokens_input: Tokens en entrée
            tokens_output: Tokens en sortie

        Returns:
            Message créé (non commité)

        Raises:
            DuplicateMessageError: Si idempotency_key existe déjà
        """
        message = Message(
            tenant_id=self._tenant_id,
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
            role=role,
            content=content,
            extra_data=extra_data or {},
            latency_ms=latency_ms,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
        )

        try:
            self._session.add(message)
            await self._session.flush()  # Pour obtenir l'ID et détecter les doublons
        except IntegrityError as e:
            await self._session.rollback()
            if "idempotency_key" in str(e):
                raise DuplicateMessageError(
                    f"Message with idempotency_key {idempotency_key} already exists"
                ) from e
            raise

        logger.debug(
            "Message created",
            extra={
                "tenant_id": str(self._tenant_id),
                "message_id": str(message.id),
                "conversation_id": str(conversation_id),
                "role": role.value,
                "idempotency_key": str(idempotency_key),
            },
        )

        return message

    async def create_if_not_exists(
        self,
        conversation_id: UUID,
        idempotency_key: UUID,
        role: MessageRole,
        content: str,
        extra_data: Optional[dict] = None,
        latency_ms: Optional[int] = None,
        tokens_input: Optional[int] = None,
        tokens_output: Optional[int] = None,
    ) -> tuple[Message, bool]:
        """
        DEPRECATED: Utiliser create_idempotent() à la place.
        Cette méthode a une race condition potentielle.
        """
        return await self.create_idempotent(
            conversation_id=conversation_id,
            idempotency_key=idempotency_key,
            role=role,
            content=content,
            extra_data=extra_data,
            latency_ms=latency_ms,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
        )

    async def create_idempotent(
        self,
        conversation_id: UUID,
        idempotency_key: UUID,
        role: MessageRole,
        content: str,
        extra_data: Optional[dict] = None,
        latency_ms: Optional[int] = None,
        tokens_input: Optional[int] = None,
        tokens_output: Optional[int] = None,
    ) -> tuple[Message, bool]:
        """
        Crée un message de manière idempotente (100% safe en concurrence).

        Utilise INSERT ... ON CONFLICT DO NOTHING (PostgreSQL).
        Garantit qu'une seule requête crée le message même si N requêtes
        arrivent simultanément avec le même idempotency_key.

        ALGORITHME:
        1. INSERT avec ON CONFLICT DO NOTHING
        2. Si insertion réussie → retourne (message, True)
        3. Si conflit → SELECT le message existant, retourne (message, False)

        Args:
            conversation_id: UUID de la conversation
            idempotency_key: UUID unique (généré côté client)
            role: Rôle de l'émetteur (USER, ASSISTANT, SYSTEM)
            content: Contenu du message
            extra_data: Métadonnées optionnelles
            latency_ms: Temps de réponse (pour ASSISTANT)
            tokens_input: Tokens en entrée
            tokens_output: Tokens en sortie

        Returns:
            tuple[Message, bool]: (message, created)
                - created=True si nouveau message créé
                - created=False si message existant retourné

        Raises:
            RuntimeError: Si conflit détecté mais message non trouvé (cas très rare)
        """
        # Préparer les valeurs pour l'insertion
        now = datetime.now(timezone.utc)
        values = {
            "tenant_id": self._tenant_id,
            "conversation_id": conversation_id,
            "idempotency_key": idempotency_key,
            "role": role,
            "content": content,
            "extra_data": extra_data or {},
            "latency_ms": latency_ms,
            "tokens_input": tokens_input,
            "tokens_output": tokens_output,
            "created_at": now,
            "updated_at": now,
        }

        # Étape 1: INSERT avec ON CONFLICT DO NOTHING
        # Si idempotency_key existe déjà, l'insertion est ignorée silencieusement
        stmt = (
            pg_insert(Message)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(Message.id)
        )

        result = await self._session.execute(stmt)
        inserted_id = result.scalar_one_or_none()

        if inserted_id is not None:
            # Insertion réussie : récupérer l'objet complet avec refresh
            # Note: On doit faire un SELECT car RETURNING ne donne que l'ID
            message = await self.get_by_id(inserted_id)

            if message is None:
                # Ne devrait jamais arriver
                raise RuntimeError(f"Message {inserted_id} created but not found")

            logger.debug(
                "Message created (idempotent)",
                extra={
                    "tenant_id": str(self._tenant_id),
                    "message_id": str(inserted_id),
                    "conversation_id": str(conversation_id),
                    "idempotency_key": str(idempotency_key),
                    "role": role.value,
                },
            )
            return message, True

        # Étape 2: Conflit détecté (idempotency_key existe), récupérer le message existant
        existing = await self.get_by_idempotency_key(idempotency_key)

        if existing is None:
            # Cas très rare : le message a été supprimé entre l'insert et le select
            # Ou problème de transaction
            raise RuntimeError(
                f"Idempotency conflict but message not found: {idempotency_key}. "
                "This should not happen unless message was deleted concurrently."
            )

        logger.debug(
            "Message already exists (idempotency hit)",
            extra={
                "tenant_id": str(self._tenant_id),
                "message_id": str(existing.id),
                "idempotency_key": str(idempotency_key),
            },
        )
        return existing, False

    async def update_extra_data(
        self,
        message_id: UUID,
        extra_data: dict,
        merge: bool = True,
    ) -> Optional[Message]:
        """
        Met à jour les métadonnées d'un message.

        Args:
            message_id: UUID du message
            extra_data: Nouvelles métadonnées
            merge: Si True, fusionne avec existant

        Returns:
            Message mis à jour ou None si non trouvé
        """
        message = await self.get_by_id(message_id)

        if not message:
            return None

        if merge:
            current_data = message.extra_data or {}
            message.extra_data = {**current_data, **extra_data}
        else:
            message.extra_data = extra_data

        message.updated_at = datetime.now(timezone.utc)

        await self._session.flush()

        return message
