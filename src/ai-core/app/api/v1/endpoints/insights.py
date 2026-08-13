"""
Insights Endpoints - Read-only admin demand analytics.

InsightsService already computes real numbers from messages/conversations/
products/coupons (see app/services/insights/service.py) and was wired
into the admin agent's get_analytics action - but that path requires
going through AdminAgent.execute_command for what is genuinely a
read-only, no-side-effect report. This exposes the same computation
directly for a passive dashboard page, without the admin-command/
safety-confirmation machinery that exists to gate actions with real
side effects.
"""

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.analytics import TimeRange, _since_for
from app.infrastructure.database.connection import get_async_session
from app.services.insights.service import InsightsService

logger = logging.getLogger(__name__)

router = APIRouter()


def _require_tenant_uuid(request: Request) -> UUID:
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")
    try:
        return UUID(str(tenant_id))
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail="Insights require a real tenant (dev header bypass has no persisted data)",
        )


# =============================================================================
# SCHEMAS
# =============================================================================


class ProductDemandOut(BaseModel):
    external_id: str
    request_count: int
    name: Optional[str] = None
    price: Optional[float] = None
    quantity: Optional[int] = None


class UnmetSearchOut(BaseModel):
    message: str
    occurrences: int
    last_seen: str


class InsightsSummaryResponse(BaseModel):
    time_range: TimeRange
    since: str
    most_requested_products: List[ProductDemandOut]
    unmet_demand: List[UnmetSearchOut]
    intent_distribution: Dict[str, int]
    peak_hours: List[Dict[str, int]]
    coupon_conversion: Dict[str, Any]
    low_stock_products: List[Dict[str, Any]]


# =============================================================================
# ENDPOINTS
# =============================================================================


@router.get("/summary", response_model=InsightsSummaryResponse)
async def get_insights_summary(
    request: Request,
    time_range: TimeRange = TimeRange.LAST_30_DAYS,
    db: AsyncSession = Depends(get_async_session),
):
    """Real demand analytics for the period - same computation as the
    admin agent's get_analytics action, exposed directly since it has no
    side effects to gate."""
    tenant_id = _require_tenant_uuid(request)
    since = _since_for(time_range)

    summary = await InsightsService(db, tenant_id).summary(since)

    return InsightsSummaryResponse(
        time_range=time_range,
        since=summary.since.isoformat(),
        most_requested_products=[
            ProductDemandOut(
                external_id=p.external_id,
                request_count=p.request_count,
                name=p.name,
                price=p.price,
                quantity=p.quantity,
            )
            for p in summary.most_requested_products
        ],
        unmet_demand=[
            UnmetSearchOut(
                message=u.message, occurrences=u.occurrences, last_seen=u.last_seen.isoformat()
            )
            for u in summary.unmet_demand
        ],
        intent_distribution=summary.intent_distribution,
        peak_hours=summary.peak_hours,
        coupon_conversion=summary.coupon_conversion,
        low_stock_products=summary.low_stock_products,
    )
