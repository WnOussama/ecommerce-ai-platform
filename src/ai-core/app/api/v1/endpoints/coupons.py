"""
Coupons Endpoints - Rule-based Coupon Generation

Chaque endpoint est maintenant réellement persisté en base (voir
CouponRepository) - avant ce correctif, generate_coupon renvoyait
toujours le même code littéral ("SAVE10-ABCD1234") pour tout client, et
validate_coupon acceptait n'importe quelle chaîne de caractères comme
code valide (aucune vérification réelle).
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.connection import get_async_session
from app.infrastructure.database.repositories.coupon_repo import CouponRepository

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# POLITIQUE DE REMISE - règles déterministes, pas un score ML
# =============================================================================

# (discount_percent, validity_days) par raison de génération.
_DISCOUNT_POLICY = {
    "cart_abandonment": (10, 2),  # urgence courte pour relancer le panier
    "loyalty": (15, 30),
    "winback": (20, 14),
}
_DEFAULT_POLICY = (10, 7)


def _policy_for(reason: Optional[str]) -> tuple[int, int]:
    return _DISCOUNT_POLICY.get(reason or "", _DEFAULT_POLICY)


# =============================================================================
# SCHEMAS
# =============================================================================


class CouponGenerateRequest(BaseModel):
    """Request to generate a personalized coupon"""

    customer_id: str
    context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    reason: Optional[str] = None  # "cart_abandonment", "loyalty", "winback"

    class Config:
        json_schema_extra = {
            "example": {
                "customer_id": "cust_123",
                "reason": "cart_abandonment",
                "context": {"cart_value": 150.00, "items_in_cart": 3},
            }
        }


class Coupon(BaseModel):
    """Generated coupon details"""

    code: str
    discount_type: str  # "percentage", "fixed_amount"
    discount_value: float
    min_purchase: Optional[float] = None
    max_discount: Optional[float] = None
    valid_from: datetime
    valid_until: datetime
    conditions: list[str] = []
    personalized_message: str


class CouponGenerateResponse(BaseModel):
    """Response with generated coupon"""

    coupon: Coupon
    strategy: str  # Which policy rule was applied


class CouponValidateRequest(BaseModel):
    """Request to validate a coupon"""

    code: str
    customer_id: str
    cart_value: float


class CouponValidateResponse(BaseModel):
    """Response from coupon validation"""

    valid: bool
    discount_amount: Optional[float] = None
    reason: Optional[str] = None


# =============================================================================
# HELPERS
# =============================================================================


def _require_tenant_id(request: Request) -> UUID:
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")
    return UUID(str(tenant_id))


_STRATEGY_MESSAGES = {
    "cart_abandonment": "Il vous reste des articles dans votre panier ! Voici {pct}% de réduction pour finaliser votre commande.",
    "loyalty": "Merci pour votre fidélité ! Voici {pct}% de réduction sur votre prochaine commande.",
    "winback": "Vous nous avez manqué ! Revenez avec {pct}% de réduction sur votre prochaine commande.",
}
_DEFAULT_MESSAGE = "Voici {pct}% de réduction sur votre prochaine commande."


# =============================================================================
# ENDPOINTS
# =============================================================================


@router.post("/generate", response_model=CouponGenerateResponse)
async def generate_coupon(
    request: Request,
    body: CouponGenerateRequest,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Generate a personalized coupon for a customer, persisted with a real
    unique code. Discount tiers are a deterministic rule table keyed by
    `reason` - not a trained model, and the response no longer claims one.
    """
    tenant_id = _require_tenant_id(request)
    discount_percent, validity_days = _policy_for(body.reason)

    repo = CouponRepository(db, tenant_id)
    code = repo.generate_code()

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=validity_days)

    db_coupon = await repo.create(
        code=code,
        discount_percent=discount_percent,
        discount_amount=None,
        min_purchase=None,
        expires_at=expires_at,
        reason=body.reason,
        customer_id=body.customer_id,
    )
    await db.commit()

    logger.info(
        "Coupon generated",
        extra={"tenant_id": str(tenant_id), "customer_id": body.customer_id, "reason": body.reason},
    )

    message_template = _STRATEGY_MESSAGES.get(body.reason or "", _DEFAULT_MESSAGE)

    return CouponGenerateResponse(
        coupon=Coupon(
            code=db_coupon.code,
            discount_type="percentage",
            discount_value=float(discount_percent),
            valid_from=now,
            valid_until=expires_at,
            conditions=["Valable une seule fois", "Non cumulable"],
            personalized_message=message_template.format(pct=discount_percent),
        ),
        strategy=body.reason or "default",
    )


@router.post("/validate", response_model=CouponValidateResponse)
async def validate_coupon(
    request: Request,
    body: CouponValidateRequest,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Validate a coupon code for a customer. A code that doesn't exist for
    this tenant, is expired, already used, or below its minimum purchase
    is rejected - previously every code was accepted unconditionally.
    """
    tenant_id = _require_tenant_id(request)
    repo = CouponRepository(db, tenant_id)

    coupon = await repo.get_by_code(body.code)

    if not coupon:
        return CouponValidateResponse(valid=False, reason="Code introuvable")

    if not coupon.is_valid:
        return CouponValidateResponse(valid=False, reason="Coupon expiré ou déjà utilisé")

    if coupon.min_purchase and body.cart_value * 100 < coupon.min_purchase:
        return CouponValidateResponse(
            valid=False, reason=f"Achat minimum de {coupon.min_purchase / 100:.2f}€ requis"
        )

    if coupon.discount_percent:
        discount_amount = round(body.cart_value * coupon.discount_percent / 100, 2)
    else:
        discount_amount = (coupon.discount_amount or 0) / 100

    await repo.mark_used(coupon)
    await db.commit()

    logger.info(
        "Coupon validated and marked used",
        extra={"tenant_id": str(tenant_id), "code": body.code},
    )

    return CouponValidateResponse(valid=True, discount_amount=discount_amount, reason=None)


@router.get("/customer/{customer_id}")
async def get_customer_coupons(
    request: Request,
    customer_id: str,
    active_only: bool = True,
    db: AsyncSession = Depends(get_async_session),
):
    """Get all coupons for a customer, from the database."""
    tenant_id = _require_tenant_id(request)
    repo = CouponRepository(db, tenant_id)

    coupons = await repo.get_for_customer(customer_id, active_only=active_only)

    total_savings = sum((c.discount_amount or 0) / 100 for c in coupons if c.discount_amount)

    return {
        "customer_id": customer_id,
        "coupons": [
            {
                "code": c.code,
                "status": c.status.value,
                "discount_percent": c.discount_percent,
                "discount_amount": (c.discount_amount / 100) if c.discount_amount else None,
                "expires_at": c.expires_at,
                "used_at": c.used_at,
            }
            for c in coupons
        ],
        "total_savings": total_savings,
    }
