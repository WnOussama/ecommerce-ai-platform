"""
Tenants Endpoints - gestion du tenant courant

Toutes les routes exigent une authentification tenant (clé API signée en
production, ou le bypass X-Tenant-ID en dev local). Il n'y a pas
d'inscription en libre-service : les tenants sont créés par un
administrateur (voir scripts/seed_demo_data.py).
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.settings import settings
from app.infrastructure.database.connection import get_async_session
from app.infrastructure.database.models.conversation import Conversation
from app.infrastructure.database.models.message import Message
from app.infrastructure.database.models.product import ProductModel
from app.infrastructure.database.repositories.tenant_repo import TenantRepository

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# SCHEMAS
# =============================================================================


class TenantResponse(BaseModel):
    id: str
    name: str
    slug: str
    email: Optional[str] = None
    plan: str
    is_verified: bool
    is_active: bool
    features: List[str]
    created_at: datetime


class TenantUpdate(BaseModel):
    name: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None


class APIKeyResponse(BaseModel):
    api_key: str
    hmac_secret: str
    message: str = (
        "Ni la clé ni le secret ne seront plus jamais affichés - stockez-les en lieu sûr. "
        "Chaque requête doit être signée avec le secret (voir APIClientSigner) : "
        "la clé seule ne suffit plus à s'authentifier."
    )


# =============================================================================
# HELPERS
# =============================================================================


def _tenant_to_response(tenant) -> TenantResponse:
    plan_name = (tenant.settings or {}).get("plan", "starter")
    plan = settings.tenant.plans.get(plan_name, settings.tenant.plans["starter"])
    return TenantResponse(
        id=str(tenant.id),
        name=tenant.name,
        slug=tenant.slug,
        email=tenant.email,
        plan=plan_name,
        is_verified=tenant.is_verified,
        is_active=tenant.is_active,
        features=plan["features"],
        created_at=tenant.created_at,
    )


def _require_tenant_id(request: Request) -> uuid.UUID:
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")
    return uuid.UUID(str(tenant_id))


# =============================================================================
# ENDPOINTS - GESTION DU TENANT COURANT (authentifié)
# =============================================================================


@router.get("/current", response_model=TenantResponse)
async def get_current_tenant(request: Request, db: AsyncSession = Depends(get_async_session)):
    tenant_id = _require_tenant_id(request)
    tenant = await TenantRepository(db).get_by_id(tenant_id)

    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    return _tenant_to_response(tenant)


@router.put("/current", response_model=TenantResponse)
async def update_current_tenant(
    request: Request, body: TenantUpdate, db: AsyncSession = Depends(get_async_session)
):
    tenant_id = _require_tenant_id(request)
    repo = TenantRepository(db)
    tenant = await repo.get_by_id(tenant_id)

    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if body.name:
        tenant.name = body.name
    if body.settings:
        await repo.update_settings(tenant, body.settings)

    await db.commit()
    await db.refresh(tenant)

    logger.info("Tenant updated", extra={"tenant_id": str(tenant_id)})

    return _tenant_to_response(tenant)


@router.get("/current/usage")
async def get_tenant_usage(request: Request, db: AsyncSession = Depends(get_async_session)):
    """
    Statistiques d'usage réelles du tenant courant (comptages en base -
    aucune valeur inventée).
    """
    tenant_id = _require_tenant_id(request)

    conversations_count = await db.scalar(
        select(func.count()).select_from(Conversation).where(Conversation.tenant_id == tenant_id)
    )
    messages_count = await db.scalar(
        select(func.count()).select_from(Message).where(Message.tenant_id == tenant_id)
    )
    products_count = await db.scalar(
        select(func.count()).select_from(ProductModel).where(ProductModel.tenant_id == tenant_id)
    )

    return {
        "tenant_id": str(tenant_id),
        "usage": {
            "conversations": conversations_count or 0,
            "messages": messages_count or 0,
            "products_indexed": products_count or 0,
        },
    }


@router.post("/current/api-keys/rotate", response_model=APIKeyResponse)
async def rotate_api_key(request: Request, db: AsyncSession = Depends(get_async_session)):
    """Révoque la clé API actuelle et en émet une nouvelle."""
    tenant_id = _require_tenant_id(request)
    repo = TenantRepository(db)
    tenant = await repo.get_by_id(tenant_id)

    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    raw_api_key, raw_hmac_secret = await repo.rotate_api_key(tenant)
    await db.commit()

    # L'ancienne clé reste valide jusqu'à ce que le cache en mémoire de la
    # middleware l'oublie - sans ça, une clé révoquée continuerait à
    # authentifier pendant toute sa durée de vie en cache.
    middleware = getattr(request.app.state, "tenant_context_middleware", None)
    if middleware:
        middleware.clear_cache(str(tenant_id))

    logger.info("API key rotated", extra={"tenant_id": str(tenant_id)})

    return APIKeyResponse(api_key=raw_api_key, hmac_secret=raw_hmac_secret)


@router.get("/current/settings")
async def get_tenant_settings(request: Request, db: AsyncSession = Depends(get_async_session)):
    tenant_id = _require_tenant_id(request)
    tenant = await TenantRepository(db).get_by_id(tenant_id)

    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    return {"tenant_id": str(tenant_id), "settings": tenant.settings or {}}


@router.put("/current/settings")
async def update_tenant_settings(
    request: Request, settings_update: Dict[str, Any], db: AsyncSession = Depends(get_async_session)
):
    tenant_id = _require_tenant_id(request)
    repo = TenantRepository(db)
    tenant = await repo.get_by_id(tenant_id)

    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    await repo.update_settings(tenant, settings_update)
    await db.commit()

    logger.info("Tenant settings updated", extra={"tenant_id": str(tenant_id)})

    return {"status": "updated", "settings": tenant.settings}
