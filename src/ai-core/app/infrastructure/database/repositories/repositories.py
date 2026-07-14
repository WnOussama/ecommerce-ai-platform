"""
Repositories concrets avec isolation multi-tenant automatique.

Tous les repositories héritent de TenantAwareRepository,
garantissant que TOUTES les queries incluent tenant_id.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, func, or_, select

from app.infrastructure.database.models.models import (
    AdminActionModel,
    ConversationModel,
    CouponModel,
    CustomerModel,
    MessageModel,
)
from app.infrastructure.database.repositories.base import (
    TenantAwareRepository,
)

# =============================================================================
# CUSTOMER REPOSITORY
# =============================================================================


class CustomerRepository(TenantAwareRepository[CustomerModel]):
    """Repository pour les clients avec isolation tenant"""

    model_class = CustomerModel

    async def get_by_external_id(self, external_id: str) -> Optional[CustomerModel]:
        """Trouve un client par son ID externe (e-commerce)"""
        return await self.find_one_by({"external_id": external_id})

    async def get_by_email(self, email: str) -> Optional[CustomerModel]:
        """Trouve un client par email"""
        return await self.find_one_by({"email": email})

    async def get_by_segment(
        self,
        segment: str,
        limit: int = 100,
    ) -> List[CustomerModel]:
        """Récupère les clients d'un segment"""
        return await self.find_by({"segment": segment}, limit=limit)

    async def get_vip_customers(self, min_score: int = 80) -> List[CustomerModel]:
        """Récupère les clients VIP (score élevé)"""
        query = (
            self._base_query()
            .where(CustomerModel.loyalty_score >= min_score)
            .order_by(CustomerModel.loyalty_score.desc())
        )

        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def get_at_risk_customers(
        self,
        days_inactive: int = 90,
    ) -> List[CustomerModel]:
        """Récupère les clients à risque (inactifs)"""
        cutoff_date = datetime.utcnow() - timedelta(days=days_inactive)

        query = self._base_query().where(
            or_(
                CustomerModel.last_order_date < cutoff_date,
                CustomerModel.last_order_date.is_(None),
            )
        )

        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def update_segment(
        self,
        customer_id: str,
        segment: str,
    ) -> bool:
        """Met à jour le segment d'un client"""
        return await self.update_by_id(customer_id, {"segment": segment})

    async def get_segment_counts(self) -> Dict[str, int]:
        """Compte les clients par segment"""
        query = (
            select(CustomerModel.segment, func.count())
            .where(CustomerModel.tenant_id == self._tenant_id)
            .group_by(CustomerModel.segment)
        )

        result = await self._session.execute(query)
        return {row[0].value if row[0] else "unknown": row[1] for row in result.all()}


# =============================================================================
# CONVERSATION REPOSITORY
# =============================================================================


class ConversationRepository(TenantAwareRepository[ConversationModel]):
    """Repository pour les conversations avec isolation tenant"""

    model_class = ConversationModel

    async def get_by_session_id(self, session_id: str) -> Optional[ConversationModel]:
        """Trouve une conversation par session ID"""
        return await self.find_one_by({"session_id": session_id})

    async def get_active_conversations(self) -> List[ConversationModel]:
        """Récupère les conversations actives"""
        return await self.find_by({"status": "active"})

    async def get_customer_conversations(
        self,
        customer_id: str,
        limit: int = 10,
    ) -> List[ConversationModel]:
        """Récupère les conversations d'un client"""
        query = (
            self._base_query()
            .where(ConversationModel.customer_id == customer_id)
            .order_by(ConversationModel.created_at.desc())
            .limit(limit)
        )

        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def get_recent_conversations(
        self,
        hours: int = 24,
        limit: int = 100,
    ) -> List[ConversationModel]:
        """Récupère les conversations récentes"""
        cutoff = datetime.utcnow() - timedelta(hours=hours)

        query = (
            self._base_query()
            .where(ConversationModel.created_at >= cutoff)
            .order_by(ConversationModel.created_at.desc())
            .limit(limit)
        )

        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def get_conversation_stats(
        self,
        days: int = 7,
    ) -> Dict[str, Any]:
        """Statistiques des conversations"""
        cutoff = datetime.utcnow() - timedelta(days=days)

        # Total conversations
        total_query = (
            select(func.count())
            .select_from(ConversationModel)
            .where(
                and_(
                    ConversationModel.tenant_id == self._tenant_id,
                    ConversationModel.created_at >= cutoff,
                )
            )
        )

        # Resolved by AI
        resolved_query = (
            select(func.count())
            .select_from(ConversationModel)
            .where(
                and_(
                    ConversationModel.tenant_id == self._tenant_id,
                    ConversationModel.created_at >= cutoff,
                    ConversationModel.resolved_by_ai.is_(True),
                )
            )
        )

        # Average satisfaction
        satisfaction_query = select(func.avg(ConversationModel.satisfaction_rating)).where(
            and_(
                ConversationModel.tenant_id == self._tenant_id,
                ConversationModel.created_at >= cutoff,
                ConversationModel.satisfaction_rating.isnot(None),
            )
        )

        total = (await self._session.execute(total_query)).scalar_one()
        resolved = (await self._session.execute(resolved_query)).scalar_one()
        avg_satisfaction = (await self._session.execute(satisfaction_query)).scalar_one()

        return {
            "total_conversations": total,
            "resolved_by_ai": resolved,
            "resolution_rate": resolved / total if total > 0 else 0,
            "average_satisfaction": float(avg_satisfaction) if avg_satisfaction else None,
            "period_days": days,
        }


# =============================================================================
# MESSAGE REPOSITORY
# =============================================================================


class MessageRepository(TenantAwareRepository[MessageModel]):
    """Repository pour les messages avec isolation tenant"""

    model_class = MessageModel

    async def get_conversation_messages(
        self,
        conversation_id: str,
        limit: int = 50,
    ) -> List[MessageModel]:
        """Récupère les messages d'une conversation"""
        query = (
            self._base_query()
            .where(MessageModel.conversation_id == conversation_id)
            .order_by(MessageModel.created_at.asc())
            .limit(limit)
        )

        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def get_recent_messages(
        self,
        conversation_id: str,
        count: int = 10,
    ) -> List[MessageModel]:
        """Récupère les N derniers messages"""
        query = (
            self._base_query()
            .where(MessageModel.conversation_id == conversation_id)
            .order_by(MessageModel.created_at.desc())
            .limit(count)
        )

        result = await self._session.execute(query)
        # Inverser pour ordre chronologique
        return list(reversed(result.scalars().all()))


# =============================================================================
# COUPON REPOSITORY
# =============================================================================


class CouponRepository(TenantAwareRepository[CouponModel]):
    """Repository pour les coupons avec isolation tenant"""

    model_class = CouponModel

    async def get_by_code(self, code: str) -> Optional[CouponModel]:
        """Trouve un coupon par son code"""
        return await self.find_one_by({"code": code})

    async def get_customer_coupons(
        self,
        customer_id: str,
        active_only: bool = True,
    ) -> List[CouponModel]:
        """Récupère les coupons d'un client"""
        filters = {"customer_id": customer_id}

        if active_only:
            filters["status"] = "active"

        return await self.find_by(filters)

    async def get_active_coupons(self) -> List[CouponModel]:
        """Récupère tous les coupons actifs"""
        now = datetime.utcnow()

        query = self._base_query().where(
            and_(
                CouponModel.status == "active",
                CouponModel.valid_from <= now,
                CouponModel.valid_until >= now,
            )
        )

        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def get_coupon_stats(self) -> Dict[str, Any]:
        """Statistiques des coupons"""
        # Total générés
        total_query = (
            select(func.count())
            .select_from(CouponModel)
            .where(CouponModel.tenant_id == self._tenant_id)
        )

        # Utilisés
        used_query = (
            select(func.count())
            .select_from(CouponModel)
            .where(
                and_(
                    CouponModel.tenant_id == self._tenant_id,
                    CouponModel.status == "used",
                )
            )
        )

        # Montant total remisé
        discount_query = select(func.sum(CouponModel.discount_value)).where(
            and_(
                CouponModel.tenant_id == self._tenant_id,
                CouponModel.status == "used",
            )
        )

        total = (await self._session.execute(total_query)).scalar_one()
        used = (await self._session.execute(used_query)).scalar_one()
        total_discount = (await self._session.execute(discount_query)).scalar_one()

        return {
            "total_generated": total,
            "total_used": used,
            "usage_rate": used / total if total > 0 else 0,
            "total_discount_given": float(total_discount) if total_discount else 0,
        }


# =============================================================================
# ADMIN ACTION REPOSITORY
# =============================================================================


class AdminActionRepository(TenantAwareRepository[AdminActionModel]):
    """Repository pour les actions admin avec isolation tenant"""

    model_class = AdminActionModel

    async def get_recent_actions(
        self,
        limit: int = 50,
    ) -> List[AdminActionModel]:
        """Récupère les actions récentes"""
        return await self.get_all(
            limit=limit,
            order_by="created_at",
            order_desc=True,
        )

    async def get_pending_actions(self) -> List[AdminActionModel]:
        """Récupère les actions en attente de confirmation"""
        return await self.find_by({"status": "pending_confirmation"})

    async def get_actions_by_type(
        self,
        action_type: str,
        limit: int = 50,
    ) -> List[AdminActionModel]:
        """Récupère les actions par type"""
        return await self.find_by({"action_type": action_type}, limit=limit)


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    "CustomerRepository",
    "ConversationRepository",
    "MessageRepository",
    "CouponRepository",
    "AdminActionRepository",
]
