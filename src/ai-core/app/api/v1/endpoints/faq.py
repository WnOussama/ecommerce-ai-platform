"""
FAQ Endpoints - AI-powered FAQ Search
"""

import logging
from typing import List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# SCHEMAS
# =============================================================================


class FAQSearchRequest(BaseModel):
    """Request to search FAQ"""

    query: str = Field(..., min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)

    class Config:
        json_schema_extra = {"example": {"query": "Comment retourner un produit?", "limit": 5}}


class FAQItem(BaseModel):
    """A FAQ item"""

    id: str
    question: str
    answer: str
    category: str
    relevance_score: float


class FAQSearchResponse(BaseModel):
    """Response with FAQ results"""

    results: List[FAQItem]
    query: str
    total_found: int


# =============================================================================
# ENDPOINTS
# =============================================================================


@router.post("/search", response_model=FAQSearchResponse)
async def search_faq(request: Request, body: FAQSearchRequest):
    """
    FAQ search is not implemented - there is no FAQ table, no indexed FAQ
    content, and nothing populates one. This endpoint used to return the
    same hardcoded "Comment retourner un produit?" result for every query
    regardless of what was actually asked, and /categories invented count
    numbers (10/8/5) that were never backed by real data - both silently
    lied about having a working feature. Honestly reporting "not
    implemented" instead, matching this project's own policy for any
    metric/feature without a real data source (see README).
    """
    tenant_id = getattr(request.state, "tenant_id", None)

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    raise HTTPException(
        status_code=501,
        detail=(
            "FAQ search is not implemented - there is no FAQ content store "
            "for this tenant yet. Policy questions in chat are answered "
            "honestly by the LLM (it says it doesn't have that information) "
            "rather than a fabricated result here."
        ),
    )


@router.get("/{faq_id}")
async def get_faq_item(request: Request, faq_id: str):
    """Not implemented - see search_faq()."""
    if not getattr(request.state, "tenant_id", None):
        raise HTTPException(status_code=401, detail="Tenant context required")
    raise HTTPException(status_code=501, detail="FAQ is not implemented - no FAQ content store exists.")


@router.get("/categories")
async def get_faq_categories(request: Request):
    """Not implemented - see search_faq()."""
    if not getattr(request.state, "tenant_id", None):
        raise HTTPException(status_code=401, detail="Tenant context required")
    raise HTTPException(status_code=501, detail="FAQ is not implemented - no FAQ content store exists.")
