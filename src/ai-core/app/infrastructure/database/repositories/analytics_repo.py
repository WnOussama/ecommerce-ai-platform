"""
Analytics Repository - CRUD operations pour événements analytics.

Repository simple avec isolation multi-tenant obligatoire.
Table append-only optimisée pour le tracking haute performance.
"""

import logging
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.analytics import AnalyticsEvent

logger = logging.getLogger(__name__)


class AnalyticsRepository:
    """
    Repository pour les événements analytics.

    Table append-only : on ne modifie jamais, on ajoute uniquement.
    Toutes les queries sont filtrées par tenant_id.

    Usage:
        repo = AnalyticsRepository(session, tenant_id)
        await repo.create_event("message_sent", "conversation", conv_id, payload)
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
    # WRITE OPERATIONS (append-only)
    # =========================================================================

    async def create_event(
        self,
        event_type: str,
        entity_type: Optional[str] = None,
        entity_id: Optional[UUID] = None,
        payload: Optional[dict] = None,
    ) -> AnalyticsEvent:
        """
        Crée un nouvel événement analytics.

        Args:
            event_type: Type d'événement (ex: "message_sent", "coupon_generated")
            entity_type: Type d'entité (ex: "conversation", "coupon")
            entity_id: UUID de l'entité concernée
            payload: Données additionnelles (JSONB)

        Returns:
            AnalyticsEvent créé (non commité)
        """
        event = AnalyticsEvent(
            tenant_id=self._tenant_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload or {},
        )

        self._session.add(event)
        await self._session.flush()

        logger.debug(
            "Analytics event created",
            extra={
                "tenant_id": str(self._tenant_id),
                "event_id": str(event.id),
                "event_type": event_type,
                "entity_type": entity_type,
            },
        )

        return event

    async def create_chat_event(
        self,
        conversation_id: UUID,
        message_role: str,
        latency_ms: Optional[int] = None,
        tokens_input: Optional[int] = None,
        tokens_output: Optional[int] = None,
        rag_products_count: int = 0,
        model_used: Optional[str] = None,
        error: Optional[str] = None,
    ) -> AnalyticsEvent:
        """
        Crée un événement analytics spécifique au chat.

        Méthode de convenance pour les événements de chat fréquents.

        Args:
            conversation_id: UUID de la conversation
            message_role: "user" ou "assistant"
            latency_ms: Temps de réponse LLM
            tokens_input: Tokens en entrée
            tokens_output: Tokens en sortie
            rag_products_count: Nombre de produits trouvés par RAG
            model_used: Nom du modèle LLM
            error: Message d'erreur si applicable
        """
        event_type = "message_sent" if not error else "message_error"

        payload = {
            "conversation_id": str(conversation_id),
            "message_role": message_role,
        }

        if latency_ms is not None:
            payload["latency_ms"] = latency_ms
        if tokens_input is not None:
            payload["tokens_input"] = tokens_input
        if tokens_output is not None:
            payload["tokens_output"] = tokens_output
        if rag_products_count > 0:
            payload["rag_products_count"] = rag_products_count
        if model_used:
            payload["model_used"] = model_used
        if error:
            payload["error"] = error

        return await self.create_event(
            event_type=event_type,
            entity_type="conversation",
            entity_id=conversation_id,
            payload=payload,
        )

    # =========================================================================
    # READ OPERATIONS (pour reporting)
    # =========================================================================

    async def get_by_conversation(
        self,
        conversation_id: UUID,
        limit: int = 100,
    ) -> List[AnalyticsEvent]:
        """
        Récupère les événements d'une conversation.

        Args:
            conversation_id: UUID de la conversation
            limit: Nombre maximum de résultats

        Returns:
            Liste d'événements (ordre chronologique)
        """
        stmt = (
            select(AnalyticsEvent)
            .where(
                and_(
                    AnalyticsEvent.tenant_id == self._tenant_id,
                    AnalyticsEvent.entity_type == "conversation",
                    AnalyticsEvent.entity_id == conversation_id,
                )
            )
            .order_by(AnalyticsEvent.created_at.asc())
            .limit(limit)
        )

        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_by_type(
        self,
        event_type: str,
        limit: int = 50,
    ) -> List[AnalyticsEvent]:
        """
        Événements d'un type donné, les plus récents en premier - utilisé
        par GET /admin/actions (voir admin.py) pour lister l'historique des
        actions admin, faute de table dédiée pour celles-ci.
        """
        stmt = (
            select(AnalyticsEvent)
            .where(
                AnalyticsEvent.tenant_id == self._tenant_id,
                AnalyticsEvent.event_type == event_type,
            )
            .order_by(AnalyticsEvent.created_at.desc())
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def get_by_entity(
        self,
        entity_type: str,
        entity_id: UUID,
    ) -> Optional[AnalyticsEvent]:
        """Le dernier événement pour cette entité (ex: entity_type="admin_action")."""
        stmt = (
            select(AnalyticsEvent)
            .where(
                AnalyticsEvent.tenant_id == self._tenant_id,
                AnalyticsEvent.entity_type == entity_type,
                AnalyticsEvent.entity_id == entity_id,
            )
            .order_by(AnalyticsEvent.created_at.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def count_by_type(
        self,
        event_type: str,
        since: Optional[datetime] = None,
    ) -> int:
        """
        Compte les événements d'un type donné.

        Args:
            event_type: Type d'événement
            since: Date de début (optionnel)

        Returns:
            Nombre d'événements
        """
        stmt = select(func.count(AnalyticsEvent.id)).where(
            and_(
                AnalyticsEvent.tenant_id == self._tenant_id,
                AnalyticsEvent.event_type == event_type,
            )
        )

        if since:
            stmt = stmt.where(AnalyticsEvent.created_at >= since)

        result = await self._session.execute(stmt)
        return result.scalar() or 0
