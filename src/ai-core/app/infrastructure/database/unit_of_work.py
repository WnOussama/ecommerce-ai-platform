"""
Unit of Work - Transaction Management

Gère le lifecycle des transactions et expose les repositories.
Pattern inspiré de Martin Fowler's "Patterns of Enterprise Application Architecture".

FLUX TRANSACTIONNEL:
┌────────────────────────────────────────────────────────────────────────┐
│                                                                        │
│  async with UnitOfWork() as uow:                                      │
│      # Session ouverte automatiquement                                │
│      │                                                                 │
│      # Accès aux repositories (tous partagent la même session)        │
│      conversation = await uow.conversations.get_by_id(...)            │
│      message = await uow.messages.create(...)                         │
│      │                                                                 │
│      # Commit MANUEL (pas d'auto-commit)                              │
│      await uow.commit()                                               │
│      │                                                                 │
│  # Session fermée automatiquement à la sortie                         │
│  # Rollback automatique si exception                                  │
│                                                                        │
└────────────────────────────────────────────────────────────────────────┘

AVANTAGES:
- Transaction unique partagée entre repositories
- Commit/rollback explicite
- Impossible d'oublier de fermer la session
- Testabilité (injection de session mock)
"""

import logging
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.connection import AsyncSessionLocal
from app.infrastructure.database.repositories.analytics_repo import AnalyticsRepository
from app.infrastructure.database.repositories.conversation_repo import ConversationRepository
from app.infrastructure.database.repositories.coupon_repo import CouponRepository
from app.infrastructure.database.repositories.faq_repo import FAQRepository
from app.infrastructure.database.repositories.message_repo import MessageRepository
from app.infrastructure.database.repositories.rule_repo import RuleRepository

logger = logging.getLogger(__name__)


class UnitOfWork:
    """
    Unit of Work pour gestion transactionnelle.

    Usage:
        async with UnitOfWork(tenant_id) as uow:
            conversation = await uow.conversations.get_or_create(...)
            message = await uow.messages.create(...)
            await uow.commit()

    Notes:
        - Le commit est MANUEL (pas d'auto-commit)
        - Rollback automatique si exception
        - Session fermée automatiquement à la sortie
        - Tous les repositories partagent la même session
    """

    def __init__(self, tenant_id: UUID | str, session: Optional[AsyncSession] = None):
        """
        Initialise le Unit of Work.

        Args:
            tenant_id: UUID du tenant (obligatoire pour multi-tenant)
            session: Session optionnelle (pour tests). Si None, crée une nouvelle session.
        """
        self._tenant_id = UUID(tenant_id) if isinstance(tenant_id, str) else tenant_id
        self._session: Optional[AsyncSession] = session
        self._owns_session = session is None  # True si on a créé la session

        # Repositories (initialisés dans __aenter__)
        self._conversations: Optional[ConversationRepository] = None
        self._messages: Optional[MessageRepository] = None
        self._analytics: Optional[AnalyticsRepository] = None
        self._rules: Optional[RuleRepository] = None
        self._coupons: Optional[CouponRepository] = None
        self._faq: Optional[FAQRepository] = None

    @property
    def tenant_id(self) -> UUID:
        """Retourne le tenant_id (read-only)."""
        return self._tenant_id

    @property
    def session(self) -> AsyncSession:
        """Retourne la session (pour cas avancés)."""
        if not self._session:
            raise RuntimeError("UnitOfWork not initialized. Use 'async with' context manager.")
        return self._session

    @property
    def conversations(self) -> ConversationRepository:
        """Retourne le repository Conversation."""
        if not self._conversations:
            raise RuntimeError("UnitOfWork not initialized. Use 'async with' context manager.")
        return self._conversations

    @property
    def messages(self) -> MessageRepository:
        """Retourne le repository Message."""
        if not self._messages:
            raise RuntimeError("UnitOfWork not initialized. Use 'async with' context manager.")
        return self._messages

    @property
    def analytics(self) -> AnalyticsRepository:
        """Retourne le repository Analytics."""
        if not self._analytics:
            raise RuntimeError("UnitOfWork not initialized. Use 'async with' context manager.")
        return self._analytics

    @property
    def rules(self) -> RuleRepository:
        """Retourne le repository Rule."""
        if not self._rules:
            raise RuntimeError("UnitOfWork not initialized. Use 'async with' context manager.")
        return self._rules

    @property
    def coupons(self) -> CouponRepository:
        """Retourne le repository Coupon."""
        if not self._coupons:
            raise RuntimeError("UnitOfWork not initialized. Use 'async with' context manager.")
        return self._coupons

    @property
    def faq(self) -> FAQRepository:
        """Retourne le repository FAQItem."""
        if not self._faq:
            raise RuntimeError("UnitOfWork not initialized. Use 'async with' context manager.")
        return self._faq

    async def __aenter__(self) -> "UnitOfWork":
        """Entre dans le contexte - ouvre la session et initialise les repositories."""
        # Créer session si pas fournie
        if self._session is None:
            self._session = AsyncSessionLocal()
            self._owns_session = True

        # Initialiser les repositories avec la session partagée
        self._conversations = ConversationRepository(self._session, self._tenant_id)
        self._messages = MessageRepository(self._session, self._tenant_id)
        self._analytics = AnalyticsRepository(self._session, self._tenant_id)
        self._rules = RuleRepository(self._session, self._tenant_id)
        self._coupons = CouponRepository(self._session, self._tenant_id)
        self._faq = FAQRepository(self._session, self._tenant_id)

        logger.debug("UnitOfWork started", extra={"tenant_id": str(self._tenant_id)})

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Sort du contexte - rollback si erreur, puis ferme la session."""
        try:
            if exc_type is not None:
                # Exception levée - rollback
                await self.rollback()
                logger.warning(
                    "UnitOfWork rolled back due to exception",
                    extra={
                        "tenant_id": str(self._tenant_id),
                        "exception_type": exc_type.__name__ if exc_type else None,
                        "exception": str(exc_val) if exc_val else None,
                    },
                )
        finally:
            # Toujours fermer la session si on l'a créée
            if self._owns_session and self._session:
                await self._session.close()
                logger.debug("UnitOfWork session closed", extra={"tenant_id": str(self._tenant_id)})

    async def commit(self) -> None:
        """
        Commit la transaction.

        IMPORTANT: Le commit est MANUEL. Vous devez appeler cette méthode
        explicitement pour persister les changements.
        """
        if not self._session:
            raise RuntimeError("No active session to commit")

        await self._session.commit()
        logger.debug("UnitOfWork committed", extra={"tenant_id": str(self._tenant_id)})

    async def rollback(self) -> None:
        """Rollback la transaction."""
        if self._session:
            await self._session.rollback()
            logger.debug("UnitOfWork rolled back", extra={"tenant_id": str(self._tenant_id)})

    async def flush(self) -> None:
        """
        Flush les changements vers la DB sans commit.

        Utile pour obtenir les IDs générés avant le commit final.
        """
        if self._session:
            await self._session.flush()
