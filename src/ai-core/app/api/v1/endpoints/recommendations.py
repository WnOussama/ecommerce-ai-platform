"""
Recommendations Endpoints - Product Recommendations

/similar/{product_id} was a stub returning an always-empty list; it now
runs a real content-based vector search against ChromaDB (same
retrieval pipeline as the chat's RAG). /trending was a stub too; it now
comes from real chat-demand counts (InsightsService, same signal as the
admin insights). There is no purchase/order history in this project (no
orders table), so a genuine collaborative-filtering "based on your
history" strategy isn't possible - POST / only ever delegates to one of
the two real strategies above, honestly, rather than fabricating one.
"""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.connection import get_async_session
from app.services.catalog.repository import ProductRepository
from app.services.insights.service import InsightsService
from app.services.rag.factory import get_retrieval_service

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# SCHEMAS
# =============================================================================


class RecommendationRequest(BaseModel):
    """Request for product recommendations"""

    customer_id: Optional[str] = None
    product_id: Optional[str] = None  # For "similar products"
    category: Optional[str] = None
    context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    limit: int = Field(default=5, ge=1, le=20)

    class Config:
        json_schema_extra = {
            "example": {"customer_id": "cust_123", "product_id": "prod_456", "limit": 5}
        }


class ProductRecommendation(BaseModel):
    """A recommended product"""

    product_id: str
    name: str
    price: float
    image_url: Optional[str] = None
    score: float  # Relevance score 0-1
    reason: str  # Why this was recommended


class RecommendationResponse(BaseModel):
    """Response with product recommendations"""

    recommendations: List[ProductRecommendation]
    strategy: str  # "content_based", "popularity", "not_available"
    metadata: Dict[str, Any] = {}


# =============================================================================
# HELPERS
# =============================================================================


def _require_tenant_id(request: Request) -> str:
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")
    return str(tenant_id)


def _require_tenant_uuid(request: Request) -> UUID:
    try:
        return UUID(_require_tenant_id(request))
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail="Recommendations require a real tenant (dev header bypass has no persisted data)",
        )


async def _similar_products(
    tenant_id: str, product_id: str, limit: int, db: AsyncSession
) -> RecommendationResponse:
    repo = ProductRepository(db)
    try:
        external_id = int(product_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Product not found")

    source = await repo.get_product_by_external_id(tenant_id, external_id)
    if not source:
        raise HTTPException(status_code=404, detail="Product not found")

    search_text = " ".join(
        part
        for part in [
            source.name,
            source.description_short,
            f"Catégorie: {source.category_name}" if source.category_name else None,
            f"Marque: {source.manufacturer_name}" if source.manufacturer_name else None,
        ]
        if part
    )

    retrieval_service = get_retrieval_service()
    result = await retrieval_service.search_products(
        query=search_text,
        tenant_id=tenant_id,
        top_k=limit + 1,  # the source product itself is typically the top hit
    )

    recommendations = [
        ProductRecommendation(
            product_id=str(p.product_id),
            name=p.name,
            price=p.price,
            score=round(p.similarity_score, 3),
            reason="Similaire par contenu (nom, catégorie, description)",
        )
        for p in result.products
        if str(p.product_id) != str(external_id)
    ][:limit]

    return RecommendationResponse(
        recommendations=recommendations,
        strategy="content_based",
        metadata={"source_product_id": product_id},
    )


async def _trending_products(
    tenant_id: UUID, limit: int, days: int, db: AsyncSession
) -> RecommendationResponse:
    insights = InsightsService(db, tenant_id)
    since = datetime.utcnow() - timedelta(days=days)
    demand = await insights.most_requested_products(since=since, limit=limit)

    recommendations = [
        ProductRecommendation(
            product_id=p.external_id,
            name=p.name or p.external_id,
            price=p.price or 0.0,
            score=0.0,  # not a similarity score - request_count is in metadata
            reason=f"{p.request_count} demande(s) client sur les {days} derniers jours",
        )
        for p in demand
    ]

    return RecommendationResponse(
        recommendations=recommendations,
        strategy="popularity",
        metadata={
            "time_window_days": days,
            "request_counts": {p.external_id: p.request_count for p in demand},
        },
    )


# =============================================================================
# ENDPOINTS
# =============================================================================


@router.post("", response_model=RecommendationResponse)
async def get_recommendations(
    request: Request,
    body: RecommendationRequest,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Get AI-powered product recommendations.

    Delegates to a real strategy: content-based similarity if
    `product_id` is given, otherwise trending/popularity. There is no
    purchase history in this project, so `customer_id`-only requests
    honestly return an empty, "not_available" response rather than a
    fabricated personalized list.
    """
    tenant_id = _require_tenant_id(request)

    if body.product_id:
        return await _similar_products(tenant_id, body.product_id, body.limit, db)

    if body.category:
        # No dedicated category endpoint exists - trending is the closest
        # real signal available without inventing a category-ranking model.
        return await _trending_products(_require_tenant_uuid(request), body.limit, 30, db)

    if body.customer_id:
        return RecommendationResponse(
            recommendations=[],
            strategy="not_available",
            metadata={
                "reason": "No purchase/order history is stored in this project - "
                "personalized recommendations from customer_id alone aren't possible yet."
            },
        )

    return await _trending_products(_require_tenant_uuid(request), body.limit, 30, db)


@router.get("/similar/{product_id}", response_model=RecommendationResponse)
async def get_similar_products(
    request: Request,
    product_id: str,
    limit: int = 5,
    db: AsyncSession = Depends(get_async_session),
):
    """Get products similar to a given product, via real ChromaDB vector similarity."""
    tenant_id = _require_tenant_id(request)
    return await _similar_products(tenant_id, product_id, limit, db)


@router.get("/trending", response_model=RecommendationResponse)
async def get_trending_products(
    request: Request,
    limit: int = 10,
    days: int = 7,
    db: AsyncSession = Depends(get_async_session),
):
    """Get trending products, from real chat-demand counts (InsightsService)."""
    tenant_id = _require_tenant_uuid(request)
    return await _trending_products(tenant_id, limit, days, db)
