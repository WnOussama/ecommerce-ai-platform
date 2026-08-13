"""
Conversation Repository - CRUD operations pour conversations.

Repository simple avec isolation multi-tenant obligatoire.
Pas de logique métier - uniquement des opérations CRUD.
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.conversation import Conversation, ConversationStatus

logger = logging.getLogger(__name__)


class ConversationRepository:
    """
    Repository pour les conversations.

    TOUTES les queries sont filtrées par tenant_id.
    Impossible d'accéder aux données d'un autre tenant.

    Usage:
        repo = ConversationRepository(session, tenant_id)
        conversation = await repo.get_by_id(conversation_id)
        conversations = await repo.get_by_user(user_identifier)
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

    async def get_by_id(self, conversation_id: UUID) -> Optional[Conversation]:
        """
        Récupère une conversation par ID.

        Args:
            conversation_id: UUID de la conversation

        Returns:
            Conversation ou None si non trouvée
        """
        stmt = select(Conversation).where(
            and_(
                Conversation.tenant_id == self._tenant_id,
                Conversation.id == conversation_id,
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_recent(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[ConversationStatus] = None,
    ) -> List[Conversation]:
        """Conversations du tenant, les plus récentes en premier - pour le
        navigateur de conversations du backoffice (aucun endpoint de liste
        n'existait jusqu'ici, seulement get_by_id)."""
        stmt = select(Conversation).where(Conversation.tenant_id == self._tenant_id)
        if status is not None:
            stmt = stmt.where(Conversation.status == status)
        stmt = stmt.order_by(Conversation.created_at.desc()).limit(limit).offset(offset)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_all(self, status: Optional[ConversationStatus] = None) -> int:
        stmt = (
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.tenant_id == self._tenant_id)
        )
        if status is not None:
            stmt = stmt.where(Conversation.status == status)

        result = await self._session.execute(stmt)
        return result.scalar() or 0

    async def get_by_id_for_update(
        self,
        conversation_id: UUID,
        nowait: bool = False,
    ) -> Optional[Conversation]:
        """
        Récupère une conversation avec lock pessimiste (SELECT FOR UPDATE).

        Utiliser pour les mises à jour concurrentes sur la même conversation.
        Garantit qu'une seule transaction peut modifier la conversation à la fois.

        USAGE RECOMMANDÉ:
        - Mise à jour de conversation.updated_at après ajout de message
        - Changement de statut concurrent
        - Toute opération nécessitant une lecture-puis-écriture atomique

        Args:
            conversation_id: UUID de la conversation
            nowait: Si True, échoue immédiatement si lock non disponible.
                   Si False (défaut), attend que le lock soit libéré.

        Returns:
            Conversation lockée ou None si non trouvée

        Raises:
            sqlalchemy.exc.OperationalError: Si nowait=True et lock non disponible

        Example:
            async with UnitOfWork(tenant_id) as uow:
                # Lock la conversation pour update
                conv = await uow.conversations.get_by_id_for_update(conv_id)
                if conv:
                    conv.updated_at = datetime.now(timezone.utc)
                    await uow.commit()  # Lock libéré au commit
        """
        stmt = (
            select(Conversation)
            .where(
                and_(
                    Conversation.tenant_id == self._tenant_id,
                    Conversation.id == conversation_id,
                )
            )
            .with_for_update(nowait=nowait)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_user(
        self,
        user_identifier: str,
        status: Optional[ConversationStatus] = None,
        limit: int = 10,
    ) -> List[Conversation]:
        """
        Récupère les conversations d'un utilisateur.

        Args:
            user_identifier: Identifiant de l'utilisateur
            status: Filtre optionnel par statut
            limit: Nombre maximum de résultats

        Returns:
            Liste de conversations (plus récentes d'abord)
        """
        stmt = select(Conversation).where(
            and_(
                Conversation.tenant_id == self._tenant_id,
                Conversation.user_identifier == user_identifier,
            )
        )

        if status:
            stmt = stmt.where(Conversation.status == status)

        stmt = stmt.order_by(Conversation.created_at.desc()).limit(limit)

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_active_by_user(self, user_identifier: str) -> Optional[Conversation]:
        """
        Récupère la conversation active d'un utilisateur.

        Args:
            user_identifier: Identifiant de l'utilisateur

        Returns:
            Conversation active ou None
        """
        conversations = await self.get_by_user(
            user_identifier=user_identifier,
            status=ConversationStatus.ACTIVE,
            limit=1,
        )
        return conversations[0] if conversations else None

    # =========================================================================
    # WRITE OPERATIONS
    # =========================================================================

    async def create(
        self,
        user_identifier: str,
        status: ConversationStatus = ConversationStatus.ACTIVE,
        extra_data: Optional[dict] = None,
    ) -> Conversation:
        """
        Crée une nouvelle conversation.

        Args:
            user_identifier: Identifiant de l'utilisateur
            status: Statut initial (ACTIVE par défaut)
            extra_data: Métadonnées optionnelles

        Returns:
            Conversation créée (non commitée)
        """
        conversation = Conversation(
            tenant_id=self._tenant_id,
            user_identifier=user_identifier,
            status=status,
            extra_data=extra_data or {},
        )

        self._session.add(conversation)
        await self._session.flush()  # Pour obtenir l'ID

        logger.debug(
            "Conversation created",
            extra={
                "tenant_id": str(self._tenant_id),
                "conversation_id": str(conversation.id),
                "user_identifier": user_identifier,
            },
        )

        return conversation

    async def get_or_create(
        self,
        user_identifier: str,
        extra_data: Optional[dict] = None,
    ) -> tuple[Conversation, bool]:
        """
        Récupère la conversation active ou en crée une nouvelle.

        Args:
            user_identifier: Identifiant de l'utilisateur
            extra_data: Métadonnées pour la nouvelle conversation

        Returns:
            Tuple (conversation, created) où created=True si nouvelle
        """
        # Chercher conversation active existante
        conversation = await self.get_active_by_user(user_identifier)

        if conversation:
            return conversation, False

        # Créer nouvelle conversation
        conversation = await self.create(
            user_identifier=user_identifier,
            extra_data=extra_data,
        )
        return conversation, True

    async def update_status(
        self,
        conversation_id: UUID,
        status: ConversationStatus,
    ) -> Optional[Conversation]:
        """
        Met à jour le statut d'une conversation.

        Args:
            conversation_id: UUID de la conversation
            status: Nouveau statut

        Returns:
            Conversation mise à jour ou None si non trouvée
        """
        conversation = await self.get_by_id(conversation_id)

        if not conversation:
            return None

        conversation.status = status
        conversation.updated_at = datetime.utcnow()

        await self._session.flush()

        logger.debug(
            "Conversation status updated",
            extra={
                "tenant_id": str(self._tenant_id),
                "conversation_id": str(conversation_id),
                "new_status": status.value,
            },
        )

        return conversation

    async def update_extra_data(
        self,
        conversation_id: UUID,
        extra_data: dict,
        merge: bool = True,
    ) -> Optional[Conversation]:
        """
        Met à jour les métadonnées d'une conversation.

        Args:
            conversation_id: UUID de la conversation
            extra_data: Nouvelles métadonnées
            merge: Si True, fusionne avec existant. Si False, remplace.

        Returns:
            Conversation mise à jour ou None si non trouvée
        """
        conversation = await self.get_by_id(conversation_id)

        if not conversation:
            return None

        if merge:
            # Fusionner avec métadonnées existantes
            current_data = conversation.extra_data or {}
            conversation.extra_data = {**current_data, **extra_data}
        else:
            conversation.extra_data = extra_data

        conversation.updated_at = datetime.now(timezone.utc)

        await self._session.flush()

        return conversation
