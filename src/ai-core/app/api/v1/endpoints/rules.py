"""
Rules Endpoints - Tenant-configurable Business Rules CRUD

Le modèle Rule (conditions/action JSONB, priority, is_active, usage_count)
existait déjà en base sans jamais être exposé - voir RuleRepository et
RuleEvaluator pour la logique de matching, et chat.py pour l'évaluation
au fil de la conversation.
"""

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.database.connection import get_async_session
from app.infrastructure.database.models.rule import Rule
from app.infrastructure.database.repositories.rule_repo import RuleRepository

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# SCHEMAS
# =============================================================================


class RuleCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    conditions: Dict[str, Any] = Field(default_factory=dict)
    action: Dict[str, Any] = Field(default_factory=dict)
    priority: int = 0
    is_active: bool = True

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Relance panier abandonné",
                "conditions": {"intent": "coupon_request", "keywords_any": ["panier", "abandonné"]},
                "action": {"type": "generate_coupon", "discount_percent": 10, "validity_days": 2},
                "priority": 0,
                "is_active": True,
            }
        }


class RuleUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    conditions: Optional[Dict[str, Any]] = None
    action: Optional[Dict[str, Any]] = None
    priority: Optional[int] = None
    is_active: Optional[bool] = None


class RuleResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    conditions: Dict[str, Any]
    action: Dict[str, Any]
    priority: int
    is_active: bool
    usage_count: int

    @staticmethod
    def from_model(rule: Rule) -> "RuleResponse":
        return RuleResponse(
            id=str(rule.id),
            name=rule.name,
            description=rule.description,
            conditions=rule.conditions or {},
            action=rule.action or {},
            priority=rule.priority,
            is_active=rule.is_active,
            usage_count=rule.usage_count,
        )


# =============================================================================
# HELPERS
# =============================================================================


def _require_tenant_id(request: Request) -> UUID:
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")
    try:
        return UUID(str(tenant_id))
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail="Rules require a real tenant (dev header bypass has no persisted data)",
        )


# =============================================================================
# ENDPOINTS
# =============================================================================


@router.get("", response_model=List[RuleResponse])
async def list_rules(
    request: Request,
    db: AsyncSession = Depends(get_async_session),
):
    """List all rules for the tenant (active and inactive), ordered by priority."""
    tenant_id = _require_tenant_id(request)
    repo = RuleRepository(db, tenant_id)
    rules = await repo.list_all()
    return [RuleResponse.from_model(r) for r in rules]


@router.post("", response_model=RuleResponse, status_code=201)
async def create_rule(
    request: Request,
    body: RuleCreateRequest,
    db: AsyncSession = Depends(get_async_session),
):
    """Create a new tenant rule."""
    tenant_id = _require_tenant_id(request)
    repo = RuleRepository(db, tenant_id)

    rule = await repo.create(
        name=body.name,
        conditions=body.conditions,
        action=body.action,
        description=body.description,
        priority=body.priority,
        is_active=body.is_active,
    )
    await db.commit()

    logger.info("Rule created", extra={"tenant_id": str(tenant_id), "rule_id": str(rule.id)})

    return RuleResponse.from_model(rule)


@router.get("/{rule_id}", response_model=RuleResponse)
async def get_rule(
    request: Request,
    rule_id: UUID,
    db: AsyncSession = Depends(get_async_session),
):
    """Get a single rule by id."""
    tenant_id = _require_tenant_id(request)
    repo = RuleRepository(db, tenant_id)

    rule = await repo.get_by_id(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    return RuleResponse.from_model(rule)


@router.put("/{rule_id}", response_model=RuleResponse)
async def update_rule(
    request: Request,
    rule_id: UUID,
    body: RuleUpdateRequest,
    db: AsyncSession = Depends(get_async_session),
):
    """Update a rule. Only fields present in the request body are changed."""
    tenant_id = _require_tenant_id(request)
    repo = RuleRepository(db, tenant_id)

    rule = await repo.get_by_id(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    updates = body.model_dump(exclude_unset=True)
    rule = await repo.update(rule, updates)
    await db.commit()

    logger.info("Rule updated", extra={"tenant_id": str(tenant_id), "rule_id": str(rule.id)})

    return RuleResponse.from_model(rule)


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(
    request: Request,
    rule_id: UUID,
    db: AsyncSession = Depends(get_async_session),
):
    """Soft-delete a rule."""
    tenant_id = _require_tenant_id(request)
    repo = RuleRepository(db, tenant_id)

    rule = await repo.get_by_id(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    await repo.delete(rule)
    await db.commit()

    logger.info("Rule deleted", extra={"tenant_id": str(tenant_id), "rule_id": str(rule_id)})
