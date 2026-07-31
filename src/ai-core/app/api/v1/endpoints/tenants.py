"""
Tenants Endpoints - Tenant Management

Signup + vérification email : `POST /` et `GET /verify/{token}` sont
publics (voir TenantContextMiddleware.PUBLIC_EXACT_PATHS /
PUBLIC_PATH_PREFIXES) - un prospect n'a pas encore de clé API. Tout le
reste nécessite une authentification tenant (dev bypass ou clé API réelle).
"""

import hashlib
import logging
import re
import unicodedata
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.settings import settings
from app.infrastructure.database.connection import get_async_session
from app.infrastructure.database.models.conversation import Conversation
from app.infrastructure.database.models.message import Message
from app.infrastructure.database.models.product import ProductModel
from app.infrastructure.database.repositories.tenant_repo import TenantRepository
from app.infrastructure.email import get_email_provider

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# SCHEMAS
# =============================================================================


class TenantSignupRequest(BaseModel):
    """Demande d'inscription d'une nouvelle boutique."""

    name: str = Field(..., min_length=1, max_length=100)
    email: EmailStr

    class Config:
        json_schema_extra = {"example": {"name": "Ma Boutique", "email": "admin@maboutique.com"}}


class TenantSignupResponse(BaseModel):
    id: str
    name: str
    email: str
    status: str = "pending_verification"
    message: str = "Un email de vérification a été envoyé."


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
    message: str = "Cette clé ne sera plus jamais affichée - stockez-la en lieu sûr."


# =============================================================================
# HELPERS
# =============================================================================


def _slugify(name: str) -> str:
    """Convertit un nom de boutique en slug URL-safe unique (suffixe aléatoire)."""
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    base = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-") or "shop"
    return f"{base}-{uuid.uuid4().hex[:8]}"


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


async def _send_verification_email(to: str, name: str, raw_token: str) -> None:
    verify_url = f"{settings.email.public_base_url}/api/v1/tenants/verify/{raw_token}"
    html = (
        f"<p>Bonjour {name},</p>"
        f"<p>Merci de vous être inscrit. Cliquez sur le lien ci-dessous pour "
        f"vérifier votre email et recevoir votre clé API :</p>"
        f'<p><a href="{verify_url}">{verify_url}</a></p>'
    )
    text = f"Bonjour {name},\n\nVérifiez votre email : {verify_url}"
    await get_email_provider().send(
        to=to, subject="Vérifiez votre boutique", html_body=html, text_body=text
    )


# =============================================================================
# ENDPOINTS - SIGNUP (public, pas d'authentification)
# =============================================================================


@router.post("", response_model=TenantSignupResponse, status_code=201)
async def signup(
    request: Request,
    body: TenantSignupRequest,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Inscription d'une nouvelle boutique. Crée un tenant en attente de
    vérification email et envoie un lien de vérification - aucune clé API
    n'est émise avant que l'email ne soit confirmé.
    """
    repo = TenantRepository(db)

    if await repo.get_by_email(body.email):
        raise HTTPException(status_code=409, detail="Cet email est déjà utilisé")

    tenant, raw_token = await repo.create_pending(
        name=body.name, email=body.email, slug=_slugify(body.name)
    )
    await db.commit()

    await _send_verification_email(body.email, body.name, raw_token)

    logger.info("Tenant signup", extra={"tenant_id": str(tenant.id), "tenant_name": body.name})

    return TenantSignupResponse(id=str(tenant.id), name=tenant.name, email=tenant.email)


@router.get("/verify/{token}", response_class=HTMLResponse)
async def verify_email(token: str, db: AsyncSession = Depends(get_async_session)):
    """
    Vérifie l'email d'un tenant et émet sa clé API réelle.
    La clé n'est affichée qu'une seule fois, sur cette page.
    """
    repo = TenantRepository(db)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    tenant = await repo.get_by_verification_token_hash(token_hash)

    if not tenant:
        raise HTTPException(status_code=404, detail="Lien de vérification invalide ou expiré")

    raw_api_key = await repo.activate_and_issue_api_key(tenant)
    await db.commit()

    return f"""
    <html><body style="font-family: sans-serif; max-width: 560px; margin: 40px auto;">
      <h2>Boutique vérifiée avec succès</h2>
      <p>Votre clé API (à conserver - elle ne sera plus jamais affichée) :</p>
      <pre style="background:#f4f4f4; padding:16px; border-radius:8px; word-break:break-all;">{raw_api_key}</pre>
      <p>Utilisez-la dans l'en-tête <code>Authorization: Bearer {{clé}}</code> de vos requêtes.</p>
    </body></html>
    """


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

    raw_api_key = await repo.rotate_api_key(tenant)
    await db.commit()

    # L'ancienne clé reste valide jusqu'à ce que le cache en mémoire de la
    # middleware l'oublie - sans ça, une clé révoquée continuerait à
    # authentifier pendant toute sa durée de vie en cache.
    middleware = getattr(request.app.state, "tenant_context_middleware", None)
    if middleware:
        middleware.clear_cache(str(tenant_id))

    logger.info("API key rotated", extra={"tenant_id": str(tenant_id)})

    return APIKeyResponse(api_key=raw_api_key)


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
