"""
Admin Endpoints - Admin AI Commands

/command exécute réellement AdminAgent.execute_command (voir
app/domain/services/admin/agent_v2.py) au lieu de renvoyer un
command_id="cmd_placeholder" figé. Le workflow de risque/confirmation
vient de AdminAISafetySystem (dry run, double confirmation, approbation
humaine, rollback), backé par Redis - donc partagé entre workers et
persistant à travers les redémarrages, contrairement à un simple dict en
mémoire. Il n'existe pas de table dédiée aux actions admin: l'historique
est persisté dans analytics_events (event_type="admin_action"), comme le
reste des événements analytics.
"""

import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.cache.redis_client import get_redis_cache_client
from app.core.security.admin_safety import AdminAISafetySystem
from app.domain.services.admin.agent_v2 import AdminAgent
from app.infrastructure.database.unit_of_work import UnitOfWork

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# SCHEMAS
# =============================================================================


class AdminCommandRequest(BaseModel):
    """
    Commande admin - soit texte libre (classifié vers une action
    prédéfinie, voir AdminCommandParser), soit structurée avec
    action_name explicite (nécessaire pour les actions qui prennent des
    paramètres réels, ex: generate_bulk_coupons).
    """

    command: Optional[str] = Field(None, min_length=1, max_length=2000)
    action_name: Optional[str] = None
    parameters: Dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    admin_user_id: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "command": "Analyse les demandes clients de la semaine",
            }
        }


class AdminCommandResponse(BaseModel):
    """Réponse réelle de AdminAgent.execute_command."""

    action_id: str
    action_name: str
    status: str
    success: bool
    data: Dict[str, Any] = {}
    error: Optional[str] = None
    requires_confirmation: bool = False
    confirmation_token: Optional[str] = None
    dry_run: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = {}


class ConfirmActionRequest(BaseModel):
    """Confirme (ou avance) une action en attente."""

    action_id: str
    confirmation_token: str
    admin_user_id: Optional[str] = None


class RejectActionRequest(BaseModel):
    """Rejette une action en attente de confirmation/approbation."""

    action_id: str
    reason: str = ""
    admin_user_id: Optional[str] = None


class AdminActionRecord(BaseModel):
    """Enregistrement d'une action admin passée, lu depuis analytics_events."""

    action_id: str
    action_name: str
    status: str
    executed_at: str
    admin_user_id: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


# =============================================================================
# HELPERS
# =============================================================================


def _require_tenant_uuid(request: Request) -> UUID:
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")
    try:
        return UUID(str(tenant_id))
    except ValueError:
        raise HTTPException(
            status_code=404,
            detail="Admin commands require a real tenant (dev header bypass has no persisted data)",
        )


def _admin_user_id(request: Request, provided: Optional[str]) -> str:
    if provided:
        return provided
    header_value = request.headers.get("X-Admin-User-Id")
    return header_value or "unknown_admin"


def _get_safety_system() -> AdminAISafetySystem:
    # AdminAISafetySystem itself is stateless (all state lives in Redis via
    # cache_client) so it's cheap to build per-request - this avoids caching
    # a second singleton on top of the already-lazy redis client below.
    return AdminAISafetySystem(cache_client=get_redis_cache_client())


async def _record_action(
    tenant_id: UUID,
    action_id: str,
    action_name: str,
    status: str,
    admin_user_id: str,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> None:
    """Persiste l'action dans analytics_events - best-effort, comme le
    reste des écritures analytics du chat (voir chat.py::_log_user_message)."""
    try:
        entity_id = UUID(action_id)
    except ValueError:
        entity_id = None

    try:
        async with UnitOfWork(tenant_id) as uow:
            await uow.analytics.create_event(
                event_type="admin_action",
                entity_type="admin_action",
                entity_id=entity_id,
                payload={
                    "action_id": action_id,
                    "action_name": action_name,
                    "status": status,
                    "admin_user_id": admin_user_id,
                    "result": result,
                    "error": error,
                },
            )
            await uow.commit()
    except Exception as e:
        logger.warning(
            "Skipping admin action persistence",
            extra={"tenant_id": str(tenant_id), "action_id": action_id, "error": str(e)},
        )


# =============================================================================
# ENDPOINTS
# =============================================================================


@router.post("/command", response_model=AdminCommandResponse)
async def execute_command(request: Request, body: AdminCommandRequest):
    """
    Execute an AI-powered admin command.

    Examples:
    - {"command": "Analyse les demandes clients de la semaine"} (LOW risk, auto-exécuté)
    - {"action_name": "suggest_marketing_strategy", "parameters": {"objective": "retention"}}
    - {"action_name": "generate_bulk_coupons", "parameters": {"customer_ids": [...], "max_count": N, "discount_percent": 10}, "reason": "..."}
    """
    tenant_id = _require_tenant_uuid(request)
    admin_user_id = _admin_user_id(request, body.admin_user_id)

    raw_command: Dict[str, Any] = {"parameters": body.parameters, "reason": body.reason}
    if body.action_name:
        raw_command["action_name"] = body.action_name
    elif body.command:
        raw_command["command"] = body.command
    else:
        raise HTTPException(status_code=422, detail="Provide either 'command' or 'action_name'")

    logger.info(
        "Executing admin command",
        extra={
            "tenant_id": str(tenant_id),
            "action_name": body.action_name,
            "admin_user_id": admin_user_id,
        },
    )

    async with UnitOfWork(tenant_id) as uow:
        agent = AdminAgent(uow.session, tenant_id, _get_safety_system())
        response = await agent.execute_command(raw_command, admin_user_id)
        await uow.commit()

    if response.status in ("completed", "failed"):
        await _record_action(
            tenant_id,
            response.action_id,
            response.action_name,
            response.status,
            admin_user_id,
            result=response.data or None,
            error=response.error,
        )

    return AdminCommandResponse(
        action_id=response.action_id,
        action_name=response.action_name,
        status=response.status,
        success=response.success,
        data=response.data,
        error=response.error,
        requires_confirmation=response.requires_confirmation,
        confirmation_token=response.confirmation_token,
        dry_run=response.dry_run,
        metadata={"processing_time_ms": response.processing_time_ms},
    )


@router.post("/confirm", response_model=AdminCommandResponse)
async def confirm_action(request: Request, body: ConfirmActionRequest):
    """
    Confirm (or advance) a pending action. HIGH risk actions require this
    to be called twice (double confirmation) - the response's `status`
    stays "pending_confirmation" after the first call.
    """
    tenant_id = _require_tenant_uuid(request)
    admin_user_id = _admin_user_id(request, body.admin_user_id)

    raw_command = {"action_id": body.action_id, "confirmation_token": body.confirmation_token}

    async with UnitOfWork(tenant_id) as uow:
        agent = AdminAgent(uow.session, tenant_id, _get_safety_system())
        response = await agent.execute_command(raw_command, admin_user_id)
        await uow.commit()

    if response.status in ("completed", "failed"):
        await _record_action(
            tenant_id,
            response.action_id,
            response.action_name,
            response.status,
            admin_user_id,
            result=response.data or None,
            error=response.error,
        )

    return AdminCommandResponse(
        action_id=response.action_id,
        action_name=response.action_name,
        status=response.status,
        success=response.success,
        data=response.data,
        error=response.error,
        requires_confirmation=response.requires_confirmation,
        confirmation_token=response.confirmation_token,
        dry_run=response.dry_run,
        metadata={"processing_time_ms": response.processing_time_ms},
    )


@router.post("/reject")
async def reject_action(request: Request, body: RejectActionRequest):
    """Reject a pending action (confirmation or human approval stage)."""
    tenant_id = _require_tenant_uuid(request)
    admin_user_id = _admin_user_id(request, body.admin_user_id)

    safety = _get_safety_system()
    pending, error = await safety.reject_action(body.action_id, admin_user_id, body.reason)
    if error:
        raise HTTPException(status_code=400, detail=error)

    await _record_action(
        tenant_id,
        pending.id,
        pending.action_name,
        "rejected",
        admin_user_id,
        error=body.reason or None,
    )

    return {"action_id": pending.id, "status": pending.status.value}


@router.get("/actions", response_model=List[AdminActionRecord])
async def get_action_history(request: Request, limit: int = 50):
    """Get history of completed/failed admin actions, from analytics_events."""
    tenant_id = _require_tenant_uuid(request)

    async with UnitOfWork(tenant_id) as uow:
        events = await uow.analytics.list_by_type("admin_action", limit=limit)

    return [
        AdminActionRecord(
            action_id=e.payload.get("action_id", str(e.entity_id) if e.entity_id else str(e.id)),
            action_name=e.payload.get("action_name", ""),
            status=e.payload.get("status", ""),
            executed_at=e.created_at.isoformat(),
            admin_user_id=e.payload.get("admin_user_id"),
            result=e.payload.get("result"),
            error=e.payload.get("error"),
        )
        for e in events
    ]


@router.get("/actions/{action_id}", response_model=AdminActionRecord)
async def get_action_detail(request: Request, action_id: str):
    """Get details of a specific admin action, from analytics_events."""
    tenant_id = _require_tenant_uuid(request)

    try:
        entity_id = UUID(action_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Action not found")

    async with UnitOfWork(tenant_id) as uow:
        event = await uow.analytics.get_by_entity("admin_action", entity_id)

    if not event:
        raise HTTPException(status_code=404, detail="Action not found")

    return AdminActionRecord(
        action_id=event.payload.get("action_id", action_id),
        action_name=event.payload.get("action_name", ""),
        status=event.payload.get("status", ""),
        executed_at=event.created_at.isoformat(),
        admin_user_id=event.payload.get("admin_user_id"),
        result=event.payload.get("result"),
        error=event.payload.get("error"),
    )
