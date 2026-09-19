"""
FAQItem Repository - CRUD operations pour les FAQ générées automatiquement.

Repository simple avec isolation multi-tenant obligatoire (même pattern
que CouponRepository/RuleRepository).
"""

import logging
from typing import List, Optional
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.faq import FAQItem

logger = logging.getLogger(__name__)


class FAQRepository:
    """Repository pour les FAQItem. Toutes les queries sont filtrées par tenant_id."""

    def __init__(self, session: AsyncSession, tenant_id: UUID):
        self._session = session
        self._tenant_id = tenant_id

    async def replace_all(self, items: List[dict]) -> List[FAQItem]:
        """
        Remplace toute la FAQ du tenant par `items` - une regénération part
        d'un état propre plutôt que d'accumuler des doublons/entrées
        obsolètes d'une page CMS depuis supprimée ou modifiée. Pas de
        soft-delete ici (voir app/infrastructure/database/models/faq.py) :
        ce n'est pas une table transactionnelle, c'est un cache régénérable.
        """
        await self._session.execute(delete(FAQItem).where(FAQItem.tenant_id == self._tenant_id))

        created = []
        for item in items:
            faq_item = FAQItem(tenant_id=self._tenant_id, **item)
            self._session.add(faq_item)
            created.append(faq_item)

        await self._session.flush()
        for faq_item in created:
            await self._session.refresh(faq_item)

        logger.info(
            "FAQ regenerated", extra={"tenant_id": str(self._tenant_id), "count": len(created)}
        )
        return created

    async def get_all(self) -> List[FAQItem]:
        """
        All of the tenant's generated FAQ - used by the chat pipeline (see
        app/api/v1/endpoints/chat.py::_load_faq_context) to ground policy
        answers instead of the LLM inventing a plausible-sounding one.
        Cheap and simple rather than a proper search here: a tenant's
        generated FAQ is a handful of items (one CMS page yields 2-4), so
        "fetch everything, let the LLM pick what's relevant" beats a
        keyword/embedding search that could miss a real match on
        conversational phrasing the /search endpoint's ILIKE wouldn't (e.g.
        "what's your delivery policy?" vs a stored question about dispatch
        times - no shared substring).
        """
        result = await self._session.execute(
            select(FAQItem)
            .where(FAQItem.tenant_id == self._tenant_id)
            .order_by(FAQItem.category, FAQItem.created_at)
        )
        return list(result.scalars().all())

    async def search(self, query: str, limit: int = 5) -> List[FAQItem]:
        """
        Recherche par sous-chaîne (insensible à la casse) sur question+answer -
        pas d'embeddings ici : le volume de FAQ par tenant est faible (une
        poignée d'entrées par page CMS), une recherche sémantique serait un
        surcoût d'infrastructure sans bénéfice mesurable à cette échelle.
        """
        pattern = f"%{query.strip()}%"
        result = await self._session.execute(
            select(FAQItem)
            .where(
                and_(
                    FAQItem.tenant_id == self._tenant_id,
                    or_(FAQItem.question.ilike(pattern), FAQItem.answer.ilike(pattern)),
                )
            )
            .order_by(FAQItem.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_by_id(self, faq_id: UUID) -> Optional[FAQItem]:
        result = await self._session.execute(
            select(FAQItem).where(and_(FAQItem.tenant_id == self._tenant_id, FAQItem.id == faq_id))
        )
        return result.scalar_one_or_none()

    async def get_categories(self) -> List[dict]:
        """Catégories réelles (dérivées des titres de pages CMS sources) avec
        leur nombre d'entrées réel - jamais les chiffres inventés que
        renvoyait l'ancien endpoint 501."""
        result = await self._session.execute(
            select(FAQItem.category, func.count())
            .where(FAQItem.tenant_id == self._tenant_id)
            .group_by(FAQItem.category)
            .order_by(FAQItem.category)
        )
        return [{"name": category, "count": count} for category, count in result.all()]
