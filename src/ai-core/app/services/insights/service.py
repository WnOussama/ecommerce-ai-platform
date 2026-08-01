"""
InsightsService - Statistiques admin calculées sur des signaux réels.

Remplace les handlers _handle_sales_analytics/_handle_customer_analytics
de l'admin agent, qui renvoyaient des chiffres inventés (total_revenue,
order_count, "Product A"...). Il n'existe pas de table orders/sales dans
ce projet (voir docstring du module principal) - tout ce qui suit est
donc calculé à partir de ce qui existe réellement: messages,
conversations, products, coupons. Aucune ligne renvoyée ici n'est
inventée; chaque chiffre est traçable à une requête SQL sur une table
réelle.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.models.coupon import Coupon, CouponStatus
from app.infrastructure.database.models.message import Message, MessageRole
from app.infrastructure.database.models.product import ProductModel

logger = logging.getLogger(__name__)


@dataclass
class ProductDemand:
    external_id: str
    request_count: int
    name: Optional[str] = None
    price: Optional[float] = None
    quantity: Optional[int] = None


@dataclass
class UnmetSearch:
    message: str
    occurrences: int
    last_seen: datetime


@dataclass
class InsightsSummary:
    since: datetime
    most_requested_products: List[ProductDemand] = field(default_factory=list)
    unmet_demand: List[UnmetSearch] = field(default_factory=list)
    intent_distribution: Dict[str, int] = field(default_factory=dict)
    peak_hours: List[Dict[str, int]] = field(default_factory=list)
    coupon_conversion: Dict[str, Any] = field(default_factory=dict)
    low_stock_products: List[Dict[str, Any]] = field(default_factory=list)


class InsightsService:
    """
    Calcule des statistiques admin à partir des tables réelles du tenant.
    Toutes les méthodes sont scoped par tenant_id (comme les repositories).
    """

    def __init__(self, db: AsyncSession, tenant_id: UUID):
        self._db = db
        self._tenant_id = tenant_id

    async def most_requested_products(
        self, since: datetime, limit: int = 10
    ) -> List[ProductDemand]:
        """
        Produits les plus demandés, comptés à partir des `product_ids`
        (external_id PrestaShop) stockés sur chaque message assistant par
        le RAG - voir chat.py::_log_assistant_message. Jointure avec
        `products` pour le nom/prix/stock actuels (peut être None si le
        produit a depuis été supprimé du catalogue).
        """
        product_id_fn = func.jsonb_array_elements_text(
            Message.extra_data["product_ids"]
        ).table_valued("value", joins_implicitly=True)

        counts_stmt = (
            select(product_id_fn.c.value.label("external_id"), func.count().label("cnt"))
            .select_from(Message)
            .where(
                Message.tenant_id == self._tenant_id,
                Message.role == MessageRole.ASSISTANT,
                Message.created_at >= since,
            )
            .group_by(product_id_fn.c.value)
            .order_by(func.count().desc())
            .limit(limit)
        )
        rows = (await self._db.execute(counts_stmt)).all()
        if not rows:
            return []

        external_ids = [r.external_id for r in rows]
        products_stmt = select(ProductModel).where(
            ProductModel.tenant_id == self._tenant_id,
            ProductModel.external_id.in_(external_ids),
        )
        products_by_id = {
            p.external_id: p for p in (await self._db.execute(products_stmt)).scalars().all()
        }

        return [
            ProductDemand(
                external_id=r.external_id,
                request_count=r.cnt,
                name=products_by_id[r.external_id].name
                if r.external_id in products_by_id
                else None,
                price=products_by_id[r.external_id].price
                if r.external_id in products_by_id
                else None,
                quantity=products_by_id[r.external_id].quantity
                if r.external_id in products_by_id
                else None,
            )
            for r in rows
        ]

    async def unmet_demand(self, since: datetime, limit: int = 10) -> List[UnmetSearch]:
        """
        Recherches produit où le RAG n'a rien trouvé (products_found=0
        alors que use_rag=true) - un signal directement actionnable
        (catalogue à compléter) contrairement à une métrique de vente
        inventée. Groupé par texte de message pour faire ressortir les
        demandes récurrentes non satisfaites.
        """
        # Every chat request logs exactly one USER message immediately
        # followed by one ASSISTANT reply in the same conversation (see
        # chat.py::_log_user_message / _log_assistant_message) - so the
        # user query behind a zero-hit reply is the message ranked
        # immediately before it within its conversation, ordered by
        # created_at. row_number() gives that ordinal position so the
        # pairing can be a plain self-join rather than a fuzzy time match.
        ranked = (
            select(
                Message.conversation_id,
                Message.role,
                Message.content,
                Message.created_at,
                Message.extra_data,
                func.row_number()
                .over(partition_by=Message.conversation_id, order_by=Message.created_at)
                .label("rn"),
            )
            .where(Message.tenant_id == self._tenant_id, Message.created_at >= since)
            .subquery()
        )
        assistant_row = ranked.alias("assistant_row")
        user_row = ranked.alias("user_row")

        stmt = (
            select(
                user_row.c.content,
                func.count().label("cnt"),
                func.max(assistant_row.c.created_at).label("last_seen"),
            )
            .select_from(assistant_row)
            .join(
                user_row,
                (user_row.c.conversation_id == assistant_row.c.conversation_id)
                & (user_row.c.rn == assistant_row.c.rn - 1),
            )
            .where(
                assistant_row.c.role == MessageRole.ASSISTANT,
                user_row.c.role == MessageRole.USER,
                assistant_row.c.extra_data["rag_used"].astext == "true",
                assistant_row.c.extra_data["products_found"].astext == "0",
            )
            .group_by(user_row.c.content)
            .order_by(func.count().desc())
            .limit(limit)
        )
        rows = (await self._db.execute(stmt)).all()
        return [
            UnmetSearch(message=r.content, occurrences=r.cnt, last_seen=r.last_seen) for r in rows
        ]

    async def intent_distribution(self, since: datetime) -> Dict[str, int]:
        """Répartition des intentions classées par _classify_intent (chat.py)."""
        intent_expr = Message.extra_data["intent"].astext
        stmt = (
            select(intent_expr.label("intent"), func.count().label("cnt"))
            .where(
                Message.tenant_id == self._tenant_id,
                Message.role == MessageRole.ASSISTANT,
                Message.created_at >= since,
                Message.extra_data.has_key("intent"),  # noqa: W601 - JSONB operator
            )
            .group_by(intent_expr)
        )
        rows = (await self._db.execute(stmt)).all()
        return {r.intent: r.cnt for r in rows}

    async def peak_hours(self, since: datetime) -> List[Dict[str, int]]:
        """Nombre de messages utilisateur par heure de la journée (UTC), 0-23."""
        hour_col = cast(func.extract("hour", Message.created_at), Integer)
        stmt = (
            select(hour_col.label("hour"), func.count().label("cnt"))
            .where(
                Message.tenant_id == self._tenant_id,
                Message.role == MessageRole.USER,
                Message.created_at >= since,
            )
            .group_by(hour_col)
            .order_by(hour_col)
        )
        rows = (await self._db.execute(stmt)).all()
        counts_by_hour = {r.hour: r.cnt for r in rows}
        return [{"hour": h, "count": counts_by_hour.get(h, 0)} for h in range(24)]

    async def coupon_conversion(self, since: datetime) -> Dict[str, Any]:
        generated = (
            await self._db.scalar(
                select(func.count())
                .select_from(Coupon)
                .where(Coupon.tenant_id == self._tenant_id, Coupon.created_at >= since)
            )
            or 0
        )
        used = (
            await self._db.scalar(
                select(func.count())
                .select_from(Coupon)
                .where(
                    Coupon.tenant_id == self._tenant_id,
                    Coupon.created_at >= since,
                    Coupon.status == CouponStatus.USED,
                )
            )
            or 0
        )
        rule_generated = (
            await self._db.scalar(
                select(func.count())
                .select_from(Coupon)
                .where(
                    Coupon.tenant_id == self._tenant_id,
                    Coupon.created_at >= since,
                    Coupon.rule_id.is_not(None),
                )
            )
            or 0
        )
        return {
            "generated": generated,
            "used": used,
            "conversion_rate": round(used / generated, 3) if generated else 0.0,
            "generated_by_rule": rule_generated,
        }

    async def low_stock_products(self, threshold: int = 5, limit: int = 10) -> List[Dict[str, Any]]:
        stmt = (
            select(ProductModel)
            .where(
                ProductModel.tenant_id == self._tenant_id,
                ProductModel.active.is_(True),
                ProductModel.quantity <= threshold,
            )
            .order_by(ProductModel.quantity.asc())
            .limit(limit)
        )
        products = (await self._db.execute(stmt)).scalars().all()
        return [
            {
                "external_id": p.external_id,
                "name": p.name,
                "quantity": p.quantity,
                "price": p.price,
            }
            for p in products
        ]

    async def summary(self, since: datetime) -> InsightsSummary:
        """Bundle de toutes les métriques - utilisé par l'admin agent et la page insights."""
        return InsightsSummary(
            since=since,
            most_requested_products=await self.most_requested_products(since),
            unmet_demand=await self.unmet_demand(since),
            intent_distribution=await self.intent_distribution(since),
            peak_hours=await self.peak_hours(since),
            coupon_conversion=await self.coupon_conversion(since),
            low_stock_products=await self.low_stock_products(),
        )
