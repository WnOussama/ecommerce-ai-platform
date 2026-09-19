"""
Coupon Repository - CRUD operations pour les coupons.

Repository simple avec isolation multi-tenant obligatoire (même pattern
que ConversationRepository/MessageRepository).
"""

import logging
import secrets
from datetime import datetime, timezone
from typing import List, Optional
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.coupon import Coupon, CouponStatus

logger = logging.getLogger(__name__)

CODE_RANDOM_CHARS = 8


class CouponRepository:
    """
    Repository pour les coupons.

    TOUTES les queries sont filtrées par tenant_id - un coupon d'un autre
    tenant ne doit jamais pouvoir être validé ou consulté.
    """

    def __init__(self, session: AsyncSession, tenant_id: UUID):
        self._session = session
        self._tenant_id = tenant_id

    @staticmethod
    def generate_code(prefix: str = "SAVE") -> str:
        """Génère un code coupon unique et imprévisible (pas un compteur séquentiel)."""
        random_part = secrets.token_hex(CODE_RANDOM_CHARS // 2).upper()
        return f"{prefix}-{random_part}"

    async def create(
        self,
        code: str,
        discount_percent: Optional[int],
        discount_amount: Optional[int],
        min_purchase: Optional[int],
        expires_at: datetime,
        reason: Optional[str] = None,
        customer_id: Optional[str] = None,
        conversation_id: Optional[UUID] = None,
        rule_id: Optional[UUID] = None,
    ) -> Coupon:
        coupon = Coupon(
            tenant_id=self._tenant_id,
            code=code,
            discount_percent=discount_percent,
            discount_amount=discount_amount,
            min_purchase=min_purchase,
            expires_at=expires_at,
            reason=reason,
            conversation_id=conversation_id,
            rule_id=rule_id,
            extra_data={"customer_id": customer_id} if customer_id else {},
        )
        self._session.add(coupon)
        await self._session.flush()
        await self._session.refresh(coupon)

        logger.info(
            "Coupon created",
            extra={"tenant_id": str(self._tenant_id), "code": code, "reason": reason},
        )

        return coupon

    async def latest_for_visitor(
        self,
        rule_id: UUID,
        customer_id: Optional[str],
        conversation_id: Optional[UUID],
    ) -> Optional[Coupon]:
        """
        Dernier coupon émis par `rule_id` à ce visiteur : même customer_id
        OU même conversation (les deux comptent, pour qu'ouvrir une nouvelle
        conversation ne contourne pas la limite quand le customer_id est connu).
        Sans customer_id ni conversation, aucune identité: retourne None.
        """
        scope = []
        if customer_id:
            scope.append(Coupon.extra_data["customer_id"].astext == customer_id)
        if conversation_id:
            scope.append(Coupon.conversation_id == conversation_id)
        if not scope:
            return None

        result = await self._session.execute(
            select(Coupon)
            .where(
                and_(
                    Coupon.tenant_id == self._tenant_id,
                    Coupon.rule_id == rule_id,
                    or_(*scope),
                )
            )
            .order_by(Coupon.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def count_for_rule_since(self, rule_id: UUID, since: datetime) -> int:
        """Nombre de coupons émis par `rule_id` pour ce tenant depuis `since`."""
        result = await self._session.execute(
            select(func.count())
            .select_from(Coupon)
            .where(
                and_(
                    Coupon.tenant_id == self._tenant_id,
                    Coupon.rule_id == rule_id,
                    Coupon.created_at >= since,
                )
            )
        )
        return int(result.scalar_one())

    async def get_by_code(self, code: str) -> Optional[Coupon]:
        result = await self._session.execute(
            select(Coupon).where(and_(Coupon.tenant_id == self._tenant_id, Coupon.code == code))
        )
        return result.scalar_one_or_none()

    async def mark_used(self, coupon: Coupon) -> Coupon:
        coupon.status = CouponStatus.USED
        coupon.used_at = datetime.now(timezone.utc)
        await self._session.flush()
        await self._session.refresh(coupon)
        return coupon

    async def get_for_customer(self, customer_id: str, active_only: bool = True) -> List[Coupon]:
        query = select(Coupon).where(
            and_(
                Coupon.tenant_id == self._tenant_id,
                Coupon.extra_data["customer_id"].astext == customer_id,
            )
        )
        if active_only:
            query = query.where(Coupon.status == CouponStatus.ACTIVE)

        query = query.order_by(Coupon.created_at.desc())
        result = await self._session.execute(query)
        return list(result.scalars().all())
