"""
FAQ Endpoints - AI-generated FAQ, grounded in the tenant's real PrestaShop
CMS pages (see app/services/faq/generator.py).

Previously this returned the same hardcoded "How do I return a product?"
result for every query and invented category counts (10/8/5) never backed
by real data, then - once that was found and removed - honestly returned
501 (no FAQ content store existed at all). This is the real
implementation: /generate pulls the tenant's CMS pages via
PrestaShopClient.get_cms_pages() and has the LLM synthesize FAQ pairs
strictly grounded in that text (refusing placeholder/Lorem-Ipsum pages
rather than inventing a policy), persisted to faq_items; /search, /{id}
and /categories serve that real, regenerable data.
"""

import logging
from typing import List
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.infrastructure.database.unit_of_work import UnitOfWork
from app.infrastructure.external.prestashop.client import (
    PrestaShopClient,
    PrestaShopClientConfig,
)
from app.infrastructure.external.prestashop.exceptions import PrestaShopError
from app.infrastructure.llm import get_llm_provider
from app.services.faq.generator import generate_faq_from_cms_pages

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# SCHEMAS
# =============================================================================


class FAQGenerateRequest(BaseModel):
    """Same shop_url/api_key-per-request convention as /sync/catalog (see
    that endpoint's docstring) - the Tenant model has no dedicated columns
    to persist PrestaShop credentials on."""

    shop_url: str = Field(
        ..., description="Base URL of the PrestaShop shop, e.g. http://prestashop"
    )
    api_key: str = Field(..., description="PrestaShop WebService API key")


class FAQGenerateResponse(BaseModel):
    status: str
    cms_pages_fetched: int
    cms_pages_skipped: int
    faq_items_generated: int


class FAQSearchRequest(BaseModel):
    """Request to search FAQ"""

    query: str = Field(..., min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)

    class Config:
        json_schema_extra = {"example": {"query": "How do I return a product?", "limit": 5}}


class FAQItemResponse(BaseModel):
    """A FAQ item"""

    id: str
    question: str
    answer: str
    category: str


class FAQSearchResponse(BaseModel):
    """Response with FAQ results"""

    results: List[FAQItemResponse]
    query: str
    total_found: int


class FAQCategory(BaseModel):
    name: str
    count: int


# =============================================================================
# HELPERS
# =============================================================================


def _require_tenant(request: Request) -> UUID:
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")
    try:
        return UUID(str(tenant_id))
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail="FAQ requires a real tenant (dev header bypass has no persisted data)",
        )


# =============================================================================
# ENDPOINTS
# =============================================================================


@router.post("/generate", response_model=FAQGenerateResponse)
async def generate_faq(request: Request, body: FAQGenerateRequest):
    """
    (Re)generates the tenant's FAQ from their real PrestaShop CMS pages -
    replaces any previously generated FAQ entirely (see
    FAQRepository.replace_all).
    """
    tenant_id = _require_tenant(request)

    prestashop_config = PrestaShopClientConfig(shop_url=body.shop_url, api_key=body.api_key)

    try:
        async with PrestaShopClient(prestashop_config, tenant_id=str(tenant_id)) as client:
            pages = await client.get_cms_pages(active_only=True)
    except PrestaShopError as e:
        logger.error(
            "FAQ generation failed to reach PrestaShop", extra={"tenant_id": str(tenant_id)}
        )
        raise HTTPException(status_code=502, detail=f"PrestaShop connection failed: {e}") from e

    llm = get_llm_provider()
    generated = await generate_faq_from_cms_pages(pages, llm)

    skipped = len(pages) - len({item.source_cms_id for item in generated})

    async with UnitOfWork(tenant_id) as uow:
        await uow.faq.replace_all(
            [
                {
                    "question": item.question,
                    "answer": item.answer,
                    "category": item.category,
                    "source_cms_id": item.source_cms_id,
                    "source_title": item.source_title,
                }
                for item in generated
            ]
        )
        await uow.commit()

    logger.info(
        "FAQ generated",
        extra={
            "tenant_id": str(tenant_id),
            "cms_pages_fetched": len(pages),
            "faq_items_generated": len(generated),
        },
    )

    return FAQGenerateResponse(
        status="completed",
        cms_pages_fetched=len(pages),
        cms_pages_skipped=skipped,
        faq_items_generated=len(generated),
    )


@router.post("/search", response_model=FAQSearchResponse)
async def search_faq(request: Request, body: FAQSearchRequest):
    """Keyword search over the tenant's generated FAQ (see FAQRepository.search)."""
    tenant_id = _require_tenant(request)

    async with UnitOfWork(tenant_id) as uow:
        matches = await uow.faq.search(body.query, limit=body.limit)

    return FAQSearchResponse(
        results=[
            FAQItemResponse(id=str(m.id), question=m.question, answer=m.answer, category=m.category)
            for m in matches
        ],
        query=body.query,
        total_found=len(matches),
    )


@router.get("/categories", response_model=List[FAQCategory])
async def get_faq_categories(request: Request):
    """Real categories (derived from the source CMS page titles) with their
    real item counts - never the invented numbers this endpoint used to return."""
    tenant_id = _require_tenant(request)

    async with UnitOfWork(tenant_id) as uow:
        categories = await uow.faq.get_categories()

    return [FAQCategory(**c) for c in categories]


@router.get("/{faq_id}", response_model=FAQItemResponse)
async def get_faq_item(request: Request, faq_id: str):
    tenant_id = _require_tenant(request)

    try:
        faq_uuid = UUID(faq_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="FAQ item not found")

    async with UnitOfWork(tenant_id) as uow:
        item = await uow.faq.get_by_id(faq_uuid)

    if not item:
        raise HTTPException(status_code=404, detail="FAQ item not found")

    return FAQItemResponse(
        id=str(item.id), question=item.question, answer=item.answer, category=item.category
    )
