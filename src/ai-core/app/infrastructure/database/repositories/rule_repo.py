"""
Rule Repository - CRUD operations pour les règles business.

Repository simple avec isolation multi-tenant obligatoire (même pattern
que CouponRepository/ConversationRepository).
"""

import logging
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.rule import Rule

logger = logging.getLogger(__name__)


class RuleRepository:
    """
    Repository pour les règles.

    TOUTES les queries sont filtrées par tenant_id - une règle d'un autre
    tenant ne doit jamais pouvoir être lue, modifiée ou évaluée.
    """

    def __init__(self, session: AsyncSession, tenant_id: UUID):
        self._session = session
        self._tenant_id = tenant_id

    async def list_active(self) -> List[Rule]:
        """Règles actives (et non supprimées) du tenant, par priorité croissante (0 = en premier)."""
        result = await self._session.execute(
            select(Rule)
            .where(
                Rule.tenant_id == self._tenant_id,
                Rule.is_active.is_(True),
                Rule.deleted_at.is_(None),
            )
            .order_by(Rule.priority.asc())
        )
        return list(result.scalars().all())

    async def list_all(self) -> List[Rule]:
        result = await self._session.execute(
            select(Rule)
            .where(Rule.tenant_id == self._tenant_id, Rule.deleted_at.is_(None))
            .order_by(Rule.priority.asc())
        )
        return list(result.scalars().all())

    async def get_by_action_reason(self, reason: str) -> Optional[Rule]:
        """
        Première règle active dont l'action est `generate_coupon` pour la
        `reason` donnée (ex: "cart_abandonment") - utilisé par
        /coupons/generate, qui reçoit une raison explicite plutôt qu'un
        message à faire matcher par RuleEvaluator.
        """
        result = await self._session.execute(
            select(Rule)
            .where(
                Rule.tenant_id == self._tenant_id,
                Rule.is_active.is_(True),
                Rule.deleted_at.is_(None),
                Rule.action["type"].astext == "generate_coupon",
                Rule.action["reason"].astext == reason,
            )
            .order_by(Rule.priority.asc())
        )
        return result.scalars().first()

    async def get_by_id(self, rule_id: UUID) -> Optional[Rule]:
        result = await self._session.execute(
            select(Rule).where(
                and_(
                    Rule.tenant_id == self._tenant_id,
                    Rule.id == rule_id,
                    Rule.deleted_at.is_(None),
                )
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        name: str,
        conditions: dict,
        action: dict,
        description: Optional[str] = None,
        priority: int = 0,
        is_active: bool = True,
    ) -> Rule:
        rule = Rule(
            tenant_id=self._tenant_id,
            name=name,
            description=description,
            conditions=conditions,
            action=action,
            priority=priority,
            is_active=is_active,
        )
        self._session.add(rule)
        await self._session.flush()
        await self._session.refresh(rule)

        logger.info(
            "Rule created", extra={"tenant_id": str(self._tenant_id), "rule_id": str(rule.id)}
        )

        return rule

    async def update(self, rule: Rule, updates: dict) -> Rule:
        for key, value in updates.items():
            if hasattr(rule, key) and key not in ("id", "tenant_id"):
                setattr(rule, key, value)

        await self._session.flush()
        await self._session.refresh(rule)
        return rule

    async def delete(self, rule: Rule) -> None:
        """Soft delete - Rule utilise SoftDeleteMixin, on ne fait jamais de DELETE réel."""
        rule.deleted_at = datetime.now(timezone.utc)
        await self._session.flush()

    async def increment_usage(self, rule: Rule) -> None:
        rule.usage_count += 1
        await self._session.flush()
