"""
Admin AI Safety - Double Confirmation, Dry Run, Human Approval

Architecture de Sécurité Admin AI:
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        ADMIN AI SAFETY LAYERS                                    │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  Commande Admin                                                                  │
│        │                                                                         │
│        ▼                                                                         │
│  ┌─────────────────┐                                                            │
│  │ 1. RISK         │  Classifier le risque de l'action                          │
│  │    ASSESSMENT   │  LOW / MEDIUM / HIGH / CRITICAL                            │
│  └────────┬────────┘                                                            │
│           │                                                                      │
│           ▼                                                                      │
│  ┌─────────────────┐                                                            │
│  │ 2. DRY RUN      │  Simuler l'action sans exécution                           │
│  │    PREVIEW      │  Montrer l'impact estimé                                   │
│  └────────┬────────┘                                                            │
│           │                                                                      │
│           ▼                                                                      │
│  ┌─────────────────┐  LOW/MEDIUM: Auto-execute avec confirmation                │
│  │ 3. CONFIRMATION │  HIGH: Double confirmation (token + délai)                 │
│  │    WORKFLOW     │  CRITICAL: Human approval obligatoire                      │
│  └────────┬────────┘                                                            │
│           │                                                                      │
│           ▼                                                                      │
│  ┌─────────────────┐                                                            │
│  │ 4. EXECUTION    │  Exécution avec rollback possible                          │
│  │    + ROLLBACK   │  Sauvegarde état avant modification                        │
│  └────────┬────────┘                                                            │
│           │                                                                      │
│           ▼                                                                      │
│  ┌─────────────────┐                                                            │
│  │ 5. AUDIT LOG    │  Log complet de l'action                                   │
│  │    + ALERT      │  Alerte si action sensible                                 │
│  └─────────────────┘                                                            │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
"""

import json
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

logger = logging.getLogger(__name__)


# =============================================================================
# TYPES
# =============================================================================


class RiskLevel(str, Enum):
    """Niveau de risque d'une action admin"""

    LOW = "low"  # Analytics, reports (auto-execute)
    MEDIUM = "medium"  # Suggestions, drafts (confirmation simple)
    HIGH = "high"  # Bulk ops, prix (double confirmation + délai)
    CRITICAL = "critical"  # Suppressions massives (human approval obligatoire)


class ActionStatus(str, Enum):
    """Statut d'une action admin"""

    DRAFT = "draft"  # Dry run créé
    PENDING_CONFIRMATION = "pending_confirmation"
    PENDING_HUMAN_APPROVAL = "pending_human_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"
    REJECTED = "rejected"
    EXPIRED = "expired"


class ApprovalType(str, Enum):
    """Type d'approbation requise"""

    NONE = "none"  # Pas d'approbation (LOW risk)
    SIMPLE = "simple"  # Confirmation simple (MEDIUM risk)
    DOUBLE = "double"  # Double confirmation + délai (HIGH risk)
    HUMAN = "human"  # Approbation humaine obligatoire (CRITICAL)


# =============================================================================
# ACTION DEFINITIONS
# =============================================================================


@dataclass
class ActionDefinition:
    """Définition d'une action admin avec ses contraintes de sécurité"""

    name: str
    description: str
    risk_level: RiskLevel

    # Limites
    max_affected_items: int = 100
    max_value_change_percent: float = 25.0
    cooldown_seconds: int = 0  # Délai entre deux exécutions

    # Approval
    approval_type: ApprovalType = ApprovalType.SIMPLE
    confirmation_delay_seconds: int = 0  # Délai obligatoire avant exécution
    requires_reason: bool = False

    # Rollback
    supports_rollback: bool = True
    rollback_window_hours: int = 24


# Définitions des actions avec leurs contraintes
ACTION_DEFINITIONS: Dict[str, ActionDefinition] = {
    # LOW RISK - Auto-execute
    "get_analytics": ActionDefinition(
        name="get_analytics",
        description="Récupère les analytics (lecture seule)",
        risk_level=RiskLevel.LOW,
        approval_type=ApprovalType.NONE,
        supports_rollback=False,
    ),
    "generate_report": ActionDefinition(
        name="generate_report",
        description="Génère un rapport (lecture seule)",
        risk_level=RiskLevel.LOW,
        approval_type=ApprovalType.NONE,
        supports_rollback=False,
    ),
    # MEDIUM RISK - Simple confirmation
    "suggest_marketing_strategy": ActionDefinition(
        name="suggest_marketing_strategy",
        description="Suggère une stratégie (pas de modification)",
        risk_level=RiskLevel.MEDIUM,
        approval_type=ApprovalType.SIMPLE,
        supports_rollback=False,
    ),
    "segment_customers": ActionDefinition(
        name="segment_customers",
        description="Re-segmente les clients",
        risk_level=RiskLevel.MEDIUM,
        approval_type=ApprovalType.SIMPLE,
        max_affected_items=10000,
    ),
    # HIGH RISK - Double confirmation + délai
    "generate_bulk_coupons": ActionDefinition(
        name="generate_bulk_coupons",
        description="Génère des coupons en masse",
        risk_level=RiskLevel.HIGH,
        approval_type=ApprovalType.DOUBLE,
        confirmation_delay_seconds=60,  # 1 min délai
        max_affected_items=1000,
        requires_reason=True,
        cooldown_seconds=3600,  # 1h entre deux bulk
    ),
    "update_product_prices": ActionDefinition(
        name="update_product_prices",
        description="Modifie les prix des produits",
        risk_level=RiskLevel.HIGH,
        approval_type=ApprovalType.DOUBLE,
        confirmation_delay_seconds=120,  # 2 min délai
        max_affected_items=100,
        max_value_change_percent=25.0,
        requires_reason=True,
    ),
    # CRITICAL RISK - Human approval obligatoire
    "delete_customer_data": ActionDefinition(
        name="delete_customer_data",
        description="Supprime des données client (GDPR)",
        risk_level=RiskLevel.CRITICAL,
        approval_type=ApprovalType.HUMAN,
        requires_reason=True,
        rollback_window_hours=72,
    ),
    "bulk_order_modification": ActionDefinition(
        name="bulk_order_modification",
        description="Modifie des commandes en masse",
        risk_level=RiskLevel.CRITICAL,
        approval_type=ApprovalType.HUMAN,
        max_affected_items=50,
        requires_reason=True,
    ),
}


# =============================================================================
# DRY RUN RESULT
# =============================================================================


@dataclass
class DryRunResult:
    """Résultat d'une simulation (dry run)"""

    action_id: str
    action_name: str

    # Impact estimé
    affected_items_count: int = 0
    affected_items_preview: List[Dict[str, Any]] = field(default_factory=list)

    # Changements prévus
    changes_summary: Dict[str, Any] = field(default_factory=dict)
    estimated_impact: Dict[str, Any] = field(default_factory=dict)

    # Warnings
    warnings: List[str] = field(default_factory=list)
    validation_errors: List[str] = field(default_factory=list)

    # Risk
    risk_level: RiskLevel = RiskLevel.MEDIUM
    approval_required: ApprovalType = ApprovalType.SIMPLE

    # Timestamps
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None

    @property
    def is_valid(self) -> bool:
        return len(self.validation_errors) == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_name": self.action_name,
            "affected_items_count": self.affected_items_count,
            "affected_items_preview": self.affected_items_preview[:10],  # Max 10 preview
            "changes_summary": self.changes_summary,
            "estimated_impact": self.estimated_impact,
            "warnings": self.warnings,
            "validation_errors": self.validation_errors,
            "risk_level": self.risk_level.value,
            "approval_required": self.approval_required.value,
            "is_valid": self.is_valid,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


# =============================================================================
# PENDING ACTION
# =============================================================================


@dataclass
class PendingAction:
    """Action en attente de confirmation/approbation"""

    id: str = field(default_factory=lambda: str(uuid4()))
    tenant_id: str = ""

    # Action
    action_name: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    dry_run: Optional[DryRunResult] = None

    # Status
    status: ActionStatus = ActionStatus.DRAFT
    risk_level: RiskLevel = RiskLevel.MEDIUM
    approval_type: ApprovalType = ApprovalType.SIMPLE

    # Confirmation
    confirmation_token: str = ""
    confirmation_count: int = 0  # Pour double confirmation
    confirmed_at: Optional[datetime] = None

    # Human approval
    approver_id: Optional[str] = None
    approval_reason: Optional[str] = None
    approved_at: Optional[datetime] = None

    # Execution
    initiated_by: str = ""
    reason: str = ""

    # Timestamps
    created_at: datetime = field(default_factory=datetime.utcnow)
    expires_at: Optional[datetime] = None
    can_execute_at: Optional[datetime] = None  # Après le délai de confirmation

    # Rollback
    rollback_data: Optional[Dict[str, Any]] = None
    rollback_expires_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Sérialise pour stockage Redis - voir AdminAISafetySystem._store_pending."""
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "action_name": self.action_name,
            "parameters": self.parameters,
            "dry_run": self.dry_run.to_dict() if self.dry_run else None,
            "status": self.status.value,
            "risk_level": self.risk_level.value,
            "approval_type": self.approval_type.value,
            "confirmation_token": self.confirmation_token,
            "confirmation_count": self.confirmation_count,
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else None,
            "approver_id": self.approver_id,
            "approval_reason": self.approval_reason,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "initiated_by": self.initiated_by,
            "reason": self.reason,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "can_execute_at": self.can_execute_at.isoformat() if self.can_execute_at else None,
            "rollback_data": self.rollback_data,
            "rollback_expires_at": self.rollback_expires_at.isoformat()
            if self.rollback_expires_at
            else None,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "PendingAction":
        dry_run_data = d.get("dry_run")
        dry_run = None
        if dry_run_data:
            dry_run = DryRunResult(
                action_id=dry_run_data["action_id"],
                action_name=dry_run_data["action_name"],
                affected_items_count=dry_run_data.get("affected_items_count", 0),
                affected_items_preview=dry_run_data.get("affected_items_preview", []),
                changes_summary=dry_run_data.get("changes_summary", {}),
                estimated_impact=dry_run_data.get("estimated_impact", {}),
                warnings=dry_run_data.get("warnings", []),
                validation_errors=dry_run_data.get("validation_errors", []),
                risk_level=RiskLevel(dry_run_data["risk_level"]),
                approval_required=ApprovalType(dry_run_data["approval_required"]),
                created_at=datetime.fromisoformat(dry_run_data["created_at"])
                if dry_run_data.get("created_at")
                else datetime.utcnow(),
                expires_at=datetime.fromisoformat(dry_run_data["expires_at"])
                if dry_run_data.get("expires_at")
                else None,
            )

        return PendingAction(
            id=d["id"],
            tenant_id=d["tenant_id"],
            action_name=d["action_name"],
            parameters=d.get("parameters", {}),
            dry_run=dry_run,
            status=ActionStatus(d["status"]),
            risk_level=RiskLevel(d["risk_level"]),
            approval_type=ApprovalType(d["approval_type"]),
            confirmation_token=d.get("confirmation_token", ""),
            confirmation_count=d.get("confirmation_count", 0),
            confirmed_at=datetime.fromisoformat(d["confirmed_at"])
            if d.get("confirmed_at")
            else None,
            approver_id=d.get("approver_id"),
            approval_reason=d.get("approval_reason"),
            approved_at=datetime.fromisoformat(d["approved_at"]) if d.get("approved_at") else None,
            initiated_by=d.get("initiated_by", ""),
            reason=d.get("reason", ""),
            created_at=datetime.fromisoformat(d["created_at"]),
            expires_at=datetime.fromisoformat(d["expires_at"]) if d.get("expires_at") else None,
            can_execute_at=datetime.fromisoformat(d["can_execute_at"])
            if d.get("can_execute_at")
            else None,
            rollback_data=d.get("rollback_data"),
            rollback_expires_at=datetime.fromisoformat(d["rollback_expires_at"])
            if d.get("rollback_expires_at")
            else None,
        )


# =============================================================================
# ADMIN AI SAFETY SYSTEM
# =============================================================================


class AdminAISafetySystem:
    """
    Système de sécurité pour les actions Admin AI.

    Fonctionnalités:
    - Dry run (simulation)
    - Double confirmation
    - Human approval pour actions critiques
    - Délai obligatoire avant exécution
    - Audit complet
    - Rollback
    """

    # TTL généreux côté Redis - la durée de vie réelle d'une action est
    # contrôlée par expires_at/rollback_expires_at (vérifiés à chaque
    # lecture), cette TTL n'est qu'un filet de sécurité pour ne pas
    # accumuler indéfiniment des actions oubliées. Couvre la plus longue
    # fenêtre de rollback existante (72h pour CRITICAL) avec de la marge.
    _PENDING_TTL_SECONDS = 7 * 24 * 3600

    def __init__(
        self,
        cache_client=None,
        db_repository=None,
        notification_service=None,
    ):
        self._cache = cache_client
        self._db = db_repository
        self._notifications = notification_service

        # Storage local - utilisé uniquement si aucun cache_client n'est
        # fourni. En production, sans cache_client, l'état ne survit pas à
        # un redémarrage ou n'est pas partagé entre workers - voir
        # _store_pending/_get_pending pour le chemin Redis réel.
        self._pending_actions: Dict[str, PendingAction] = {}
        self._human_approval_queue: List[str] = []

    # =========================================================================
    # STORAGE (Redis si cache_client fourni, sinon dict en mémoire)
    # =========================================================================

    def _pending_key(self, action_id: str) -> str:
        return f"admin_pending_action:{action_id}"

    def _human_queue_key(self, tenant_id: str) -> str:
        return f"admin_human_queue:{tenant_id}"

    async def _store_pending(self, pending: PendingAction) -> None:
        if self._cache:
            await self._cache.set(
                self._pending_key(pending.id),
                json.dumps(pending.to_dict()),
                ex=self._PENDING_TTL_SECONDS,
            )
        else:
            self._pending_actions[pending.id] = pending

    async def _get_pending(self, action_id: str) -> Optional[PendingAction]:
        if self._cache:
            data = await self._cache.get(self._pending_key(action_id))
            return PendingAction.from_dict(json.loads(data)) if data else None
        return self._pending_actions.get(action_id)

    async def _get_pending_for_tenant(
        self, action_id: str, tenant_id: str
    ) -> Optional[PendingAction]:
        """
        Charge une action en attente SEULEMENT si elle appartient à `tenant_id`.

        La clé Redis ne contient pas le tenant (l'id d'action est un UUID): sans
        ce contrôle, un tenant qui connaît l'id d'une action d'un autre pouvait
        la rejeter, l'approuver ou la confirmer. Une action d'un autre tenant
        est traitée exactement comme une action inexistante ("Action not found"),
        pour ne rien révéler.
        """
        pending = await self._get_pending(action_id)
        if pending is None:
            return None
        if str(pending.tenant_id) != str(tenant_id):
            logger.warning(
                "Cross tenant admin action access refused",
                extra={"action_id": action_id, "caller_tenant_id": str(tenant_id)},
            )
            return None
        return pending

    async def _delete_pending(self, action_id: str) -> None:
        if self._cache:
            await self._cache.delete(self._pending_key(action_id))
        else:
            self._pending_actions.pop(action_id, None)

    async def _add_to_human_queue(self, pending: PendingAction) -> None:
        if self._cache:
            await self._cache.lpush(self._human_queue_key(pending.tenant_id), pending.id)
        else:
            self._human_approval_queue.append(pending.id)

    async def _remove_from_human_queue(self, pending: PendingAction) -> None:
        if self._cache:
            await self._cache.lrem(self._human_queue_key(pending.tenant_id), 0, pending.id)
        elif pending.id in self._human_approval_queue:
            self._human_approval_queue.remove(pending.id)

    # =========================================================================
    # DRY RUN
    # =========================================================================

    async def create_dry_run(
        self,
        tenant_id: str,
        action_name: str,
        parameters: Dict[str, Any],
        initiated_by: str,
    ) -> DryRunResult:
        """
        Crée une simulation (dry run) de l'action.

        Montre l'impact sans exécuter.
        """
        action_id = str(uuid4())

        # Vérifier que l'action existe
        definition = ACTION_DEFINITIONS.get(action_name)
        if not definition:
            return DryRunResult(
                action_id=action_id,
                action_name=action_name,
                validation_errors=[f"Unknown action: {action_name}"],
            )

        # Calculer l'impact estimé
        dry_run = await self._simulate_action(
            action_id=action_id,
            tenant_id=tenant_id,
            action_name=action_name,
            parameters=parameters,
            definition=definition,
        )

        # Définir expiration du dry run (15 min)
        dry_run.expires_at = datetime.utcnow() + timedelta(minutes=15)

        logger.info(
            "Dry run created",
            extra={
                "action_id": action_id,
                "action_name": action_name,
                "tenant_id": tenant_id,
                "initiated_by": initiated_by,
                "affected_items": dry_run.affected_items_count,
                "risk_level": dry_run.risk_level.value,
            },
        )

        return dry_run

    @staticmethod
    def _numeric_param(parameters: Dict[str, Any], key: str, default: float = 0) -> float:
        """
        Coerces a parameter to a number.

        Parameters arrive over HTTP as JSON, and some callers (e.g. the
        backoffice's KeyValue form field, which only ever produces
        strings) send numeric-looking strings rather than real numbers -
        comparing those directly against int/float limits below raised
        TypeError, discovered by actually driving the admin UI rather
        than by mocking the request.
        """
        value = parameters.get(key, default)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    async def _simulate_action(
        self,
        action_id: str,
        tenant_id: str,
        action_name: str,
        parameters: Dict[str, Any],
        definition: ActionDefinition,
    ) -> DryRunResult:
        """Simule une action et calcule son impact"""
        warnings = []
        validation_errors = []
        affected_count = 0
        affected_preview = []
        changes_summary = {}
        estimated_impact = {}

        # Validation selon le type d'action
        if action_name == "generate_bulk_coupons":
            count = int(self._numeric_param(parameters, "max_count"))
            discount = self._numeric_param(parameters, "discount_percent")

            affected_count = count

            # Vérifier les limites
            if count > definition.max_affected_items:
                validation_errors.append(
                    f"Count {count} exceeds maximum {definition.max_affected_items}"
                )

            if discount > 30:
                warnings.append(f"High discount ({discount}%) may impact margins")

            # Estimer l'impact financier
            avg_order_value = 50  # Supposé
            estimated_cost = count * discount / 100 * avg_order_value

            changes_summary = {
                "coupons_to_generate": count,
                "discount_percent": discount,
                "target_segment": parameters.get("target_segment"),
            }

            estimated_impact = {
                "max_discount_cost": estimated_cost,
                "potential_orders_generated": int(count * 0.3),  # 30% utilisation
            }

            affected_preview = [
                {"type": "coupon", "discount": f"{discount}%", "status": "to_create"}
                for _ in range(min(5, count))
            ]

        elif action_name == "update_product_prices":
            product_ids = parameters.get("product_ids", [])
            adjustment = self._numeric_param(parameters, "adjustment_percent")

            affected_count = len(product_ids)

            if len(product_ids) > definition.max_affected_items:
                validation_errors.append(
                    f"Too many products ({len(product_ids)}), max {definition.max_affected_items}"
                )

            if abs(adjustment) > definition.max_value_change_percent:
                validation_errors.append(
                    f"Adjustment {adjustment}% exceeds max {definition.max_value_change_percent}%"
                )

            changes_summary = {
                "products_affected": len(product_ids),
                "price_adjustment": f"{adjustment:+.1f}%",
            }

            estimated_impact = {
                "revenue_impact": f"{adjustment:+.1f}% (estimated)",
            }

            affected_preview = [
                {
                    "product_id": pid,
                    "current_price": "XX.XX€",  # À récupérer de la DB
                    "new_price": "YY.YY€",
                    "change": f"{adjustment:+.1f}%",
                }
                for pid in product_ids[:5]
            ]

        elif action_name in ("get_analytics", "generate_report"):
            # Actions read-only
            affected_count = 0
            changes_summary = {"type": "read_only"}

        return DryRunResult(
            action_id=action_id,
            action_name=action_name,
            affected_items_count=affected_count,
            affected_items_preview=affected_preview,
            changes_summary=changes_summary,
            estimated_impact=estimated_impact,
            warnings=warnings,
            validation_errors=validation_errors,
            risk_level=definition.risk_level,
            approval_required=definition.approval_type,
        )

    # =========================================================================
    # CONFIRMATION WORKFLOW
    # =========================================================================

    async def request_confirmation(
        self,
        dry_run: DryRunResult,
        tenant_id: str,
        parameters: Dict[str, Any],
        initiated_by: str,
        reason: str = "",
    ) -> PendingAction:
        """
        Crée une demande de confirmation pour l'action.
        """
        definition = ACTION_DEFINITIONS.get(dry_run.action_name)

        # Vérifier que le dry run est valide
        if not dry_run.is_valid:
            raise ValueError(f"Invalid dry run: {dry_run.validation_errors}")

        # Vérifier l'expiration du dry run
        if dry_run.expires_at and datetime.utcnow() > dry_run.expires_at:
            raise ValueError("Dry run expired, please create a new one")

        # Vérifier que la raison est fournie si requise
        if definition.requires_reason and not reason:
            raise ValueError("Reason is required for this action")

        # Créer l'action pending
        pending = PendingAction(
            tenant_id=tenant_id,
            action_name=dry_run.action_name,
            parameters=parameters,
            dry_run=dry_run,
            risk_level=definition.risk_level,
            approval_type=definition.approval_type,
            confirmation_token=secrets.token_urlsafe(32),
            initiated_by=initiated_by,
            reason=reason,
        )

        # Définir les timestamps
        pending.expires_at = datetime.utcnow() + timedelta(minutes=30)

        if definition.confirmation_delay_seconds > 0:
            pending.can_execute_at = datetime.utcnow() + timedelta(
                seconds=definition.confirmation_delay_seconds
            )
        else:
            pending.can_execute_at = datetime.utcnow()

        # Status selon le type d'approbation
        if definition.approval_type == ApprovalType.HUMAN:
            pending.status = ActionStatus.PENDING_HUMAN_APPROVAL
            await self._add_to_human_queue(pending)

            # Notifier les admins
            await self._notify_human_approval_required(pending)
        else:
            pending.status = ActionStatus.PENDING_CONFIRMATION

        # Stocker
        await self._store_pending(pending)

        logger.info(
            "Confirmation requested",
            extra={
                "action_id": pending.id,
                "action_name": pending.action_name,
                "tenant_id": tenant_id,
                "approval_type": pending.approval_type.value,
                "can_execute_at": pending.can_execute_at.isoformat()
                if pending.can_execute_at
                else None,
            },
        )

        return pending

    async def confirm_action(
        self,
        action_id: str,
        confirmation_token: str,
        confirmed_by: str,
        *,
        tenant_id: str,
    ) -> Tuple[PendingAction, Optional[str]]:
        """
        Confirme une action.

        Pour HIGH risk: nécessite double confirmation.
        """
        pending = await self._get_pending_for_tenant(action_id, tenant_id)

        if not pending:
            return None, "Action not found"

        if pending.confirmation_token != confirmation_token:
            return None, "Invalid confirmation token"

        if pending.status not in (ActionStatus.PENDING_CONFIRMATION,):
            return None, f"Action cannot be confirmed (status: {pending.status.value})"

        if datetime.utcnow() > pending.expires_at:
            pending.status = ActionStatus.EXPIRED
            await self._store_pending(pending)
            return pending, "Action expired"

        # Incrémenter le compteur de confirmation
        pending.confirmation_count += 1
        pending.confirmed_at = datetime.utcnow()

        # Double confirmation pour HIGH risk
        if pending.approval_type == ApprovalType.DOUBLE:
            if pending.confirmation_count < 2:
                logger.info(
                    "First confirmation received, awaiting second",
                    extra={
                        "action_id": action_id,
                        "confirmation_count": pending.confirmation_count,
                    },
                )
                await self._store_pending(pending)
                return pending, None  # Attendre la 2ème confirmation

        # Vérifier le délai obligatoire
        if pending.can_execute_at and datetime.utcnow() < pending.can_execute_at:
            wait_seconds = (pending.can_execute_at - datetime.utcnow()).total_seconds()
            await self._store_pending(pending)
            return pending, f"Must wait {int(wait_seconds)} seconds before execution"

        # Action confirmée
        pending.status = ActionStatus.APPROVED
        await self._store_pending(pending)

        logger.info(
            "Action confirmed",
            extra={
                "action_id": action_id,
                "action_name": pending.action_name,
                "confirmed_by": confirmed_by,
            },
        )

        return pending, None

    # =========================================================================
    # HUMAN APPROVAL
    # =========================================================================

    async def approve_action(
        self,
        action_id: str,
        approver_id: str,
        approval_reason: str,
        *,
        tenant_id: str,
    ) -> Tuple[PendingAction, Optional[str]]:
        """
        Approbation humaine pour actions CRITICAL.

        Doit être fait par un admin différent de l'initiateur.
        """
        pending = await self._get_pending_for_tenant(action_id, tenant_id)

        if not pending:
            return None, "Action not found"

        if pending.status != ActionStatus.PENDING_HUMAN_APPROVAL:
            return None, f"Action is not pending human approval (status: {pending.status.value})"

        # Vérifier que l'approbateur est différent de l'initiateur
        if approver_id == pending.initiated_by:
            return None, "Approver must be different from initiator"

        # Approuver
        pending.approver_id = approver_id
        pending.approval_reason = approval_reason
        pending.approved_at = datetime.utcnow()
        pending.status = ActionStatus.APPROVED

        # Retirer de la queue
        await self._remove_from_human_queue(pending)
        await self._store_pending(pending)

        logger.info(
            "Action approved by human",
            extra={
                "action_id": action_id,
                "action_name": pending.action_name,
                "approver_id": approver_id,
                "initiated_by": pending.initiated_by,
            },
        )

        return pending, None

    async def reject_action(
        self,
        action_id: str,
        rejector_id: str,
        rejection_reason: str,
        *,
        tenant_id: str,
    ) -> Tuple[PendingAction, Optional[str]]:
        """Rejette une action en attente d'approbation"""
        pending = await self._get_pending_for_tenant(action_id, tenant_id)

        if not pending:
            return None, "Action not found"

        if pending.status not in (
            ActionStatus.PENDING_CONFIRMATION,
            ActionStatus.PENDING_HUMAN_APPROVAL,
        ):
            return None, "Action cannot be rejected"

        was_pending_human = pending.status == ActionStatus.PENDING_HUMAN_APPROVAL
        pending.status = ActionStatus.REJECTED
        pending.approval_reason = rejection_reason
        pending.approver_id = rejector_id
        await self._store_pending(pending)
        if was_pending_human:
            await self._remove_from_human_queue(pending)

        logger.warning(
            "Action rejected",
            extra={
                "action_id": action_id,
                "action_name": pending.action_name,
                "rejector_id": rejector_id,
                "reason": rejection_reason,
            },
        )

        return pending, None

    async def get_pending_approvals(self, tenant_id: str) -> List[PendingAction]:
        """Liste les actions en attente d'approbation humaine"""
        if self._cache:
            raw_ids = await self._cache.lrange(self._human_queue_key(tenant_id), 0, -1)
            results = []
            for raw_id in raw_ids:
                action_id = raw_id.decode() if isinstance(raw_id, bytes) else raw_id
                pending = await self._get_pending_for_tenant(action_id, tenant_id)
                if pending:
                    results.append(pending)
            return results

        return [
            self._pending_actions[aid]
            for aid in self._human_approval_queue
            if self._pending_actions.get(aid) and self._pending_actions[aid].tenant_id == tenant_id
        ]

    # =========================================================================
    # EXECUTION
    # =========================================================================

    async def execute_action(
        self,
        action_id: str,
        executor: Callable[[PendingAction], Awaitable[Dict[str, Any]]],
        *,
        tenant_id: str,
    ) -> Tuple[Dict[str, Any], Optional[str]]:
        """
        Exécute une action approuvée.

        Args:
            action_id: ID de l'action
            executor: Fonction qui exécute réellement l'action
        """
        pending = await self._get_pending_for_tenant(action_id, tenant_id)

        if not pending:
            return None, "Action not found"

        if pending.status != ActionStatus.APPROVED:
            return None, f"Action is not approved (status: {pending.status.value})"

        definition = ACTION_DEFINITIONS.get(pending.action_name)

        # Sauvegarder l'état pour rollback si supporté
        if definition.supports_rollback:
            pending.rollback_data = await self._save_rollback_state(pending)
            pending.rollback_expires_at = datetime.utcnow() + timedelta(
                hours=definition.rollback_window_hours
            )

        pending.status = ActionStatus.EXECUTING
        await self._store_pending(pending)

        try:
            # Exécuter l'action
            result = await executor(pending)

            pending.status = ActionStatus.COMPLETED
            await self._store_pending(pending)

            logger.info(
                "Action executed successfully",
                extra={
                    "action_id": action_id,
                    "action_name": pending.action_name,
                    "tenant_id": pending.tenant_id,
                },
            )

            return result, None

        except Exception as e:
            pending.status = ActionStatus.FAILED
            await self._store_pending(pending)

            logger.error(
                "Action execution failed",
                extra={
                    "action_id": action_id,
                    "action_name": pending.action_name,
                    "error": str(e),
                },
            )

            return None, str(e)

    # =========================================================================
    # ROLLBACK
    # =========================================================================

    async def rollback_action(
        self,
        action_id: str,
        rollback_by: str,
        reason: str,
        *,
        tenant_id: str,
    ) -> Tuple[bool, Optional[str]]:
        """
        Annule une action exécutée (si rollback supporté).
        """
        pending = await self._get_pending_for_tenant(action_id, tenant_id)

        if not pending:
            return False, "Action not found"

        if pending.status != ActionStatus.COMPLETED:
            return False, "Only completed actions can be rolled back"

        if not pending.rollback_data:
            return False, "Action does not support rollback"

        if pending.rollback_expires_at and datetime.utcnow() > pending.rollback_expires_at:
            return False, "Rollback window expired"

        try:
            # Effectuer le rollback
            await self._apply_rollback(pending)

            pending.status = ActionStatus.ROLLED_BACK
            await self._store_pending(pending)

            logger.warning(
                "Action rolled back",
                extra={
                    "action_id": action_id,
                    "action_name": pending.action_name,
                    "rollback_by": rollback_by,
                    "reason": reason,
                },
            )

            return True, None

        except Exception as e:
            return False, str(e)

    async def _save_rollback_state(self, pending: PendingAction) -> Dict[str, Any]:
        """Sauvegarde l'état actuel pour rollback"""
        # TODO: Implémenter selon le type d'action
        return {
            "saved_at": datetime.utcnow().isoformat(),
            "action_name": pending.action_name,
            "parameters": pending.parameters,
        }

    async def _apply_rollback(self, pending: PendingAction) -> None:
        """Applique le rollback"""
        # TODO: Implémenter selon le type d'action
        pass

    # =========================================================================
    # NOTIFICATIONS
    # =========================================================================

    async def _notify_human_approval_required(self, pending: PendingAction) -> None:
        """Notifie les admins qu'une approbation est requise"""
        if self._notifications:
            await self._notifications.send_admin_notification(
                tenant_id=pending.tenant_id,
                title="Action nécessitant approbation",
                message=f"L'action '{pending.action_name}' requiert une approbation humaine",
                data={
                    "action_id": pending.id,
                    "initiated_by": pending.initiated_by,
                    "risk_level": pending.risk_level.value,
                },
            )

        logger.info(
            "Human approval notification sent",
            extra={
                "action_id": pending.id,
                "tenant_id": pending.tenant_id,
            },
        )


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # Types
    "RiskLevel",
    "ActionStatus",
    "ApprovalType",
    # Models
    "ActionDefinition",
    "DryRunResult",
    "PendingAction",
    "ACTION_DEFINITIONS",
    # System
    "AdminAISafetySystem",
]
