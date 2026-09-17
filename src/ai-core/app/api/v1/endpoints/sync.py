"""
Sync Endpoints - Catalog synchronisation from a real PrestaShop shop.

CatalogSyncService (app/services/catalog/sync_service.py) and
PrestaShopClient (app/infrastructure/external/prestashop/client.py) already
existed, fully covered by unit tests, with no production caller - the only
wired sync path is the queue-based CatalogSyncHandler in
app/services/sync_service.py, which imports app/infrastructure/external/
prestashop_adapter.py (and shopify_adapter.py, woocommerce_adapter.py),
none of which exist. That path raises ImportError for every platform, and
no worker process runs it in this stack anyway (saas_ai_core runs only
uvicorn).

This endpoint exposes the tested service directly instead of fixing the
dormant queue path: fewer moving parts, and it reuses code that already has
27 passing tests. Shop credentials are taken per-request rather than stored
on the tenant, since the Tenant model has no dedicated columns for them
(only a generic `settings` JSON column) - avoids a migration for a
demo-only endpoint.
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.connection import get_async_session
from app.infrastructure.external.prestashop.client import PrestaShopClientConfig
from app.infrastructure.external.prestashop.exceptions import PrestaShopError
from app.services.catalog.repository import ProductRepository
from app.services.catalog.sync_service import CatalogSyncService

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# SCHEMAS
# =============================================================================


class CatalogSyncRequest(BaseModel):
    shop_url: str = Field(..., description="Base URL of the PrestaShop shop, e.g. http://prestashop")
    api_key: str = Field(..., description="PrestaShop WebService API key")
    active_only: bool = True
    reindex: bool = Field(
        default=True,
        description="Re-index synced products into the RAG vector store after sync.",
    )


class CatalogSyncResponse(BaseModel):
    status: str
    total_fetched: int
    total_created: int
    total_updated: int
    total_unchanged: int
    total_failed: int
    duration_seconds: float
    errors: list
    indexed: Optional[int] = None


# =============================================================================
# HELPERS
# =============================================================================


def _require_tenant_id(request: Request) -> str:
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")
    return str(tenant_id)


# =============================================================================
# ENDPOINTS
# =============================================================================


@router.post("/catalog", response_model=CatalogSyncResponse)
async def sync_catalog(
    request: Request,
    body: CatalogSyncRequest,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Pull the product catalog from a real PrestaShop shop and upsert it,
    then (optionally) re-index the synced products into RAG - mirrors what
    scripts/seed_demo_data.py does for the seeded demo catalog.
    """
    tenant_id = _require_tenant_id(request)

    prestashop_config = PrestaShopClientConfig(
        shop_url=body.shop_url,
        api_key=body.api_key,
    )

    repo = ProductRepository(db)
    sync_service = CatalogSyncService(repository=repo)

    try:
        result = await sync_service.sync_products(
            tenant_id=tenant_id,
            prestashop_config=prestashop_config,
            active_only=body.active_only,
        )
    except PrestaShopError as e:
        logger.error("Catalog sync failed to reach PrestaShop", extra={"tenant_id": tenant_id})
        raise HTTPException(status_code=502, detail=f"PrestaShop connection failed: {e}") from e

    await db.commit()

    logger.info(
        "Catalog sync completed",
        extra={
            "tenant_id": tenant_id,
            "total_created": result.total_created,
            "total_updated": result.total_updated,
            "total_failed": result.total_failed,
        },
    )

    indexed = None
    if body.reindex and (result.total_created or result.total_updated):
        from app.services.rag.factory import get_embedding_service, get_vector_store
        from app.services.rag.product_indexer import ProductIndexer

        indexer = ProductIndexer(
            repository=repo,
            embedding_service=get_embedding_service(),
            vector_store=get_vector_store(),
        )
        index_result = await indexer.index_all_products(tenant_id=tenant_id)
        indexed = index_result.total_indexed
        logger.info(
            "Post-sync RAG re-index completed",
            extra={"tenant_id": tenant_id, "indexed": indexed},
        )

    return CatalogSyncResponse(
        status=result.status.value if hasattr(result.status, "value") else str(result.status),
        total_fetched=result.total_fetched,
        total_created=result.total_created,
        total_updated=result.total_updated,
        total_unchanged=result.total_unchanged,
        total_failed=result.total_failed,
        duration_seconds=result.duration_seconds,
        errors=[str(e) for e in result.errors],
        indexed=indexed,
    )
