"""
Tenants Endpoints - Tenant Management
"""

import logging
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# SCHEMAS
# =============================================================================


class TenantPlan(str, Enum):
    """Available tenant plans"""

    STARTER = "starter"
    PROFESSIONAL = "professional"
    ENTERPRISE = "enterprise"


class TenantCreate(BaseModel):
    """Request to create a new tenant"""

    name: str = Field(..., min_length=1, max_length=100)
    domain: str
    platform: str = "prestashop"
    plan: TenantPlan = TenantPlan.STARTER
    contact_email: str

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Ma Boutique",
                "domain": "maboutique.com",
                "platform": "prestashop",
                "plan": "professional",
                "contact_email": "admin@maboutique.com",
            }
        }


class TenantResponse(BaseModel):
    """Tenant details response"""

    id: str
    name: str
    domain: str
    platform: str
    plan: TenantPlan
    status: str
    created_at: datetime
    features: List[str]
    limits: Dict[str, Any]


class TenantUpdate(BaseModel):
    """Request to update a tenant"""

    name: Optional[str] = None
    plan: Optional[TenantPlan] = None
    contact_email: Optional[str] = None
    settings: Optional[Dict[str, Any]] = None


class APIKeyResponse(BaseModel):
    """API key response"""

    api_key: str
    created_at: datetime
    last_used: Optional[datetime] = None
    status: str


# =============================================================================
# ENDPOINTS
# =============================================================================


@router.post("", response_model=TenantResponse)
async def create_tenant(request: Request, body: TenantCreate):
    """
    Create a new tenant.
    Returns the tenant details and API key.
    """
    logger.info(
        "Creating new tenant",
        extra={"tenant_name": body.name, "domain": body.domain, "plan": body.plan},
    )

    # TODO: Implement tenant creation
    return TenantResponse(
        id="tenant_placeholder",
        name=body.name,
        domain=body.domain,
        platform=body.platform,
        plan=body.plan,
        status="active",
        created_at=datetime.utcnow(),
        features=["chatbot", "faq"],
        limits={"conversations_per_day": 500, "products_indexed": 1000, "api_calls_per_minute": 30},
    )


@router.get("/current", response_model=TenantResponse)
async def get_current_tenant(request: Request):
    """
    Get current tenant details (based on API key).
    """
    tenant_id = getattr(request.state, "tenant_id", None)

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    # TODO: Fetch tenant from database
    return TenantResponse(
        id=str(tenant_id),
        name="Current Tenant",
        domain="example.com",
        platform="prestashop",
        plan=TenantPlan.PROFESSIONAL,
        status="active",
        created_at=datetime.utcnow(),
        features=["chatbot", "recommendations", "coupons", "faq"],
        limits={
            "conversations_per_day": 2000,
            "products_indexed": 10000,
            "api_calls_per_minute": 100,
        },
    )


@router.put("/current", response_model=TenantResponse)
async def update_current_tenant(request: Request, body: TenantUpdate):
    """
    Update current tenant settings.
    """
    tenant_id = getattr(request.state, "tenant_id", None)

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    logger.info("Updating tenant", extra={"tenant_id": tenant_id})

    # TODO: Update tenant
    return TenantResponse(
        id=str(tenant_id),
        name=body.name or "Updated Tenant",
        domain="example.com",
        platform="prestashop",
        plan=body.plan or TenantPlan.PROFESSIONAL,
        status="active",
        created_at=datetime.utcnow(),
        features=["chatbot", "recommendations", "coupons", "faq"],
        limits={},
    )


@router.get("/current/usage")
async def get_tenant_usage(request: Request):
    """
    Get current tenant usage statistics.
    """
    tenant_id = getattr(request.state, "tenant_id", None)

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    return {
        "tenant_id": str(tenant_id),
        "period": "current_month",
        "usage": {
            "conversations": 850,
            "api_calls": 12500,
            "llm_tokens": 450000,
            "llm_cost_usd": 23.45,
            "products_indexed": 2300,
            "storage_mb": 125,
        },
        "limits": {"conversations": 2000, "products_indexed": 10000, "api_calls_per_minute": 100},
        "usage_percentage": {"conversations": 42.5, "products_indexed": 23.0},
    }


@router.post("/current/api-keys/rotate", response_model=APIKeyResponse)
async def rotate_api_key(request: Request):
    """
    Rotate the API key for the current tenant.
    Old key becomes invalid immediately.
    """
    tenant_id = getattr(request.state, "tenant_id", None)

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    logger.info("Rotating API key", extra={"tenant_id": tenant_id})

    # TODO: Generate new API key and invalidate old one
    return APIKeyResponse(
        api_key="sk_new_placeholder_key", created_at=datetime.utcnow(), status="active"
    )


@router.get("/current/settings")
async def get_tenant_settings(request: Request):
    """
    Get tenant configuration settings.
    """
    tenant_id = getattr(request.state, "tenant_id", None)

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    return {
        "tenant_id": str(tenant_id),
        "settings": {
            "chatbot_enabled": True,
            "recommendations_enabled": True,
            "coupons_enabled": True,
            "default_language": "fr",
            "welcome_message": "Bonjour! Comment puis-je vous aider?",
            "llm_model": "gpt-4",
            "max_response_tokens": 500,
        },
    }


@router.put("/current/settings")
async def update_tenant_settings(request: Request, settings: Dict[str, Any]):
    """
    Update tenant configuration settings.
    """
    tenant_id = getattr(request.state, "tenant_id", None)

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    logger.info("Updating tenant settings", extra={"tenant_id": tenant_id})

    # TODO: Validate and update settings
    return {"status": "updated", "settings": settings}
