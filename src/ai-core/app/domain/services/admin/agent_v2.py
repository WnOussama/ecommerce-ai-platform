"""
Admin Agent - exécution de commandes admin prédéfinies sur des données réelles.

Anciennement dépendant d'un stack parallèle jamais câblé en production
(LLMGateway/RAGService/SecurityService/TenantService de
domain/services/shared/, avec son propre dataclass Tenant, sans mode
mock, sans aucun test) et d'un ConfirmationManager maison qui
dupliquait - en plus faible - AdminAISafetySystem (dry run, double
confirmation, approbation humaine, rollback, déjà réel et déjà testé,
voir app/core/security/admin_safety.py).

Ce module utilise maintenant:
- get_llm_provider() (app.infrastructure.llm) - le même provider Groq que
  le chat client, sans mock ni fallback silencieux.
- InsightsService - les mêmes statistiques réelles que le dashboard,
  pas de chiffres inventés (total_revenue, "Product A", segments
  vip/at_risk fantômes...).
- AdminAISafetySystem comme unique moteur de risque/confirmation - plus
  de double définition de risque (COMMAND_DEFINITIONS ici vs
  ACTION_DEFINITIONS dans admin_safety.py).

Les actions PRÉDÉFINIES sont celles de admin_safety.ACTION_DEFINITIONS.
Toute commande qui ne s'y mappe pas est rejetée - pas d'exécution libre
en langage naturel.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security.admin_safety import (
    ACTION_DEFINITIONS,
    ActionStatus,
    AdminAISafetySystem,
    ApprovalType,
    PendingAction,
)
from app.infrastructure.database.repositories.coupon_repo import CouponRepository
from app.infrastructure.llm import get_llm_provider
from app.services.insights.service import InsightsService

logger = logging.getLogger(__name__)


class ActionNotImplementedError(Exception):
    """Levée par _dispatch quand action_name n'a pas de handler réel.

    Doit toujours se propager jusqu'à admin_safety.execute_action ou
    _run_and_record, qui la mappent (comme toute exception) vers
    ActionStatus.FAILED / status="failed" - jamais retournée comme un
    dict de statut normal, sous peine d'être traitée comme un succès
    (c'était le bug : "not_implemented" renvoyé en tant que résultat
    normal, donc marqué "completed" par l'appelant).
    """


# =============================================================================
# COMMAND REQUEST/RESPONSE
# =============================================================================


@dataclass
class AdminCommandRequest:
    """Une commande admin résolue vers une action prédéfinie."""

    action_name: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    confirmation_token: Optional[str] = None
    action_id: Optional[str] = None
    reason: str = ""


@dataclass
class AdminResponse:
    """Réponse structurée - toujours JSON, jamais de texte libre."""

    action_id: str = field(default_factory=lambda: str(uuid4()))
    action_name: str = ""
    status: str = "failed"
    success: bool = False
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    requires_confirmation: bool = False
    confirmation_token: Optional[str] = None
    dry_run: Optional[Dict[str, Any]] = None
    processing_time_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_name": self.action_name,
            "status": self.status,
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "requires_confirmation": self.requires_confirmation,
            "confirmation_token": self.confirmation_token,
            "dry_run": self.dry_run,
            "processing_time_ms": self.processing_time_ms,
        }


# =============================================================================
# COMMAND PARSER - mappe une commande vers une action PRÉDÉFINIE uniquement
# =============================================================================


class AdminCommandParser:
    """
    Résout une commande admin vers l'une des actions de
    admin_safety.ACTION_DEFINITIONS - jamais d'exécution de texte libre.

    Deux formats acceptés:
    - structuré: {"action_name": "generate_bulk_coupons", "parameters": {...}}
    - texte libre: {"command": "Génère des coupons pour ces clients"} ->
      classifié par mots-clés vers une action_name (comme
      chat.py::_classify_intent), avec parameters vide (une action
      HIGH/CRITICAL risk qui a besoin de paramètres réels doit passer
      par le format structuré).
    """

    _KEYWORDS: Dict[str, List[str]] = {
        "generate_bulk_coupons": ["coupon", "code promo", "réduction en masse", "bulk"],
        "suggest_marketing_strategy": ["stratégie", "marketing", "campagne", "recommand"],
        "update_product_prices": ["prix", "tarif"],
        "segment_customers": ["segment"],
        "delete_customer_data": ["supprim", "rgpd", "gdpr"],
        "generate_report": ["rapport", "report"],
        "get_analytics": ["analyse", "analytics", "statistique", "chiffres", "kpi", "insight"],
    }

    def parse(
        self, raw_command: Union[str, Dict[str, Any]]
    ) -> Tuple[Optional[AdminCommandRequest], List[str]]:
        if isinstance(raw_command, dict):
            action_id = raw_command.get("action_id")
            confirmation_token = raw_command.get("confirmation_token")
            action_name = raw_command.get("action_name")

            if action_id and confirmation_token:
                # Continuing a pending action (confirming, or the second of a
                # double confirmation) - AdminAISafetySystem already knows
                # this action's name from when the dry run was created, so
                # action_name isn't required here.
                return (
                    AdminCommandRequest(
                        action_name=action_name or "",
                        parameters=raw_command.get("parameters", {}),
                        confirmation_token=confirmation_token,
                        action_id=action_id,
                        reason=raw_command.get("reason", ""),
                    ),
                    [],
                )

            if action_name:
                if action_name not in ACTION_DEFINITIONS:
                    return None, [f"Unknown action: {action_name}"]
                return (
                    AdminCommandRequest(
                        action_name=action_name,
                        parameters=raw_command.get("parameters", {}),
                        reason=raw_command.get("reason", ""),
                    ),
                    [],
                )
            raw_text = raw_command.get("command", "")
        else:
            raw_text = raw_command

        action_name = self._classify(raw_text)
        if not action_name:
            return None, [
                "Could not map this command to a supported action. Supported: "
                + ", ".join(ACTION_DEFINITIONS)
            ]
        return AdminCommandRequest(action_name=action_name, parameters={}), []

    def _classify(self, text: str) -> Optional[str]:
        text_lower = text.lower()
        for action_name, keywords in self._KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                return action_name
        return None


# =============================================================================
# AUDIT LOGGER
# =============================================================================


class AdminAuditLogger:
    """Logging d'audit MANDATORY pour toutes les actions admin."""

    @staticmethod
    def log_action(
        action_id: str,
        action_name: str,
        status: str,
        tenant_id: str,
        admin_user_id: str,
        parameters: Dict[str, Any],
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        logger.info(
            "ADMIN_AUDIT",
            extra={
                "audit_type": "admin_action",
                "action_id": action_id,
                "action_name": action_name,
                "status": status,
                "tenant_id": tenant_id,
                "admin_user_id": admin_user_id,
                "parameters": parameters,
                "result": result,
                "error": error,
                "timestamp": datetime.utcnow().isoformat(),
            },
        )


# =============================================================================
# ADMIN AGENT
# =============================================================================


class AdminAgent:
    """
    Exécute une commande admin prédéfinie.

    PROCESSUS:
    1. Parse (action prédéfinie uniquement)
    2. Dry run (AdminAISafetySystem)
    3. LOW risk -> exécution immédiate. MEDIUM/HIGH/CRITICAL -> demande de
       confirmation, puis exécution seulement après confirmation/approbation.
    4. Audit log (toujours)
    """

    def __init__(self, db: AsyncSession, tenant_id: UUID, safety: AdminAISafetySystem):
        self.db = db
        self.tenant_id = tenant_id
        self.safety = safety
        self.parser = AdminCommandParser()
        self.audit = AdminAuditLogger()

    async def execute_command(
        self,
        raw_command: Union[str, Dict[str, Any]],
        admin_user_id: str,
    ) -> AdminResponse:
        start = time.perf_counter()
        command, errors = self.parser.parse(raw_command)

        if errors or not command:
            return AdminResponse(
                status="failed",
                success=False,
                error="; ".join(errors) or "Invalid command",
                processing_time_ms=self._elapsed_ms(start),
            )

        # Confirming (or advancing) an already-pending action.
        if command.action_id and command.confirmation_token:
            return await self._continue_pending(command, admin_user_id, start)

        definition = ACTION_DEFINITIONS[command.action_name]

        dry_run = await self.safety.create_dry_run(
            str(self.tenant_id), command.action_name, command.parameters, admin_user_id
        )
        if not dry_run.is_valid:
            self.audit.log_action(
                dry_run.action_id,
                command.action_name,
                "failed",
                str(self.tenant_id),
                admin_user_id,
                command.parameters,
                error="; ".join(dry_run.validation_errors),
            )
            return AdminResponse(
                action_name=command.action_name,
                status="failed",
                success=False,
                error="; ".join(dry_run.validation_errors),
                dry_run=dry_run.to_dict(),
                processing_time_ms=self._elapsed_ms(start),
            )

        if definition.approval_type == ApprovalType.NONE:
            return await self._run_and_record(command, admin_user_id, start)

        try:
            pending = await self.safety.request_confirmation(
                dry_run,
                str(self.tenant_id),
                command.parameters,
                admin_user_id,
                reason=command.reason,
            )
        except ValueError as e:
            return AdminResponse(
                action_name=command.action_name,
                status="failed",
                success=False,
                error=str(e),
                processing_time_ms=self._elapsed_ms(start),
            )

        self.audit.log_action(
            pending.id,
            command.action_name,
            pending.status.value,
            str(self.tenant_id),
            admin_user_id,
            command.parameters,
        )

        return AdminResponse(
            action_id=pending.id,
            action_name=command.action_name,
            status=pending.status.value,
            success=True,
            requires_confirmation=True,
            confirmation_token=pending.confirmation_token,
            dry_run=dry_run.to_dict(),
            processing_time_ms=self._elapsed_ms(start),
        )

    async def _continue_pending(
        self, command: AdminCommandRequest, admin_user_id: str, start: float
    ) -> AdminResponse:
        pending, error = await self.safety.confirm_action(
            command.action_id, command.confirmation_token, admin_user_id
        )
        if error:
            return AdminResponse(
                action_id=command.action_id or "",
                action_name=command.action_name,
                status="failed",
                success=False,
                error=error,
                processing_time_ms=self._elapsed_ms(start),
            )

        if pending.status != ActionStatus.APPROVED:
            # Still waiting (e.g. first of a double confirmation, or
            # mandatory delay not elapsed yet).
            return AdminResponse(
                action_id=pending.id,
                action_name=pending.action_name,
                status=pending.status.value,
                success=True,
                requires_confirmation=True,
                confirmation_token=pending.confirmation_token,
                processing_time_ms=self._elapsed_ms(start),
            )

        async def executor(p: PendingAction) -> Dict[str, Any]:
            return await self._dispatch(p.action_name, p.parameters)

        result, exec_error = await self.safety.execute_action(pending.id, executor)

        self.audit.log_action(
            pending.id,
            pending.action_name,
            "completed" if not exec_error else "failed",
            str(self.tenant_id),
            admin_user_id,
            pending.parameters,
            result=result,
            error=exec_error,
        )

        if exec_error:
            return AdminResponse(
                action_id=pending.id,
                action_name=pending.action_name,
                status="failed",
                success=False,
                error=exec_error,
                processing_time_ms=self._elapsed_ms(start),
            )

        return AdminResponse(
            action_id=pending.id,
            action_name=pending.action_name,
            status="completed",
            success=True,
            data=result or {},
            processing_time_ms=self._elapsed_ms(start),
        )

    async def _run_and_record(
        self, command: AdminCommandRequest, admin_user_id: str, start: float
    ) -> AdminResponse:
        try:
            data = await self._dispatch(command.action_name, command.parameters)
        except Exception as e:
            self.audit.log_action(
                str(uuid4()),
                command.action_name,
                "failed",
                str(self.tenant_id),
                admin_user_id,
                command.parameters,
                error=str(e),
            )
            return AdminResponse(
                action_name=command.action_name,
                status="failed",
                success=False,
                error=str(e),
                processing_time_ms=self._elapsed_ms(start),
            )

        action_id = str(uuid4())
        self.audit.log_action(
            action_id,
            command.action_name,
            "completed",
            str(self.tenant_id),
            admin_user_id,
            command.parameters,
            result=data,
        )
        return AdminResponse(
            action_id=action_id,
            action_name=command.action_name,
            status="completed",
            success=True,
            data=data,
            processing_time_ms=self._elapsed_ms(start),
        )

    @staticmethod
    def _elapsed_ms(start: float) -> int:
        return int((time.perf_counter() - start) * 1000)

    # =========================================================================
    # ACTION HANDLERS - données réelles uniquement
    # =========================================================================

    async def _dispatch(self, action_name: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        handlers = {
            "get_analytics": self._handle_get_analytics,
            "generate_report": self._handle_get_analytics,
            "suggest_marketing_strategy": self._handle_marketing_strategy,
            "generate_bulk_coupons": self._handle_bulk_coupons,
        }
        handler = handlers.get(action_name)
        if not handler:
            raise ActionNotImplementedError(
                f"'{action_name}' has no real execution path yet - only the "
                "confirmation/dry-run/audit workflow is wired for it."
            )
        return await handler(parameters)

    async def _handle_get_analytics(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Statistiques réelles (InsightsService) - remplace les anciens
        total_revenue/order_count/"Product A" inventés."""
        days = params.get("days", 7)
        since = datetime.utcnow() - timedelta(days=days)
        insights = InsightsService(self.db, self.tenant_id)
        summary = await insights.summary(since)

        return {
            "since": summary.since.isoformat(),
            "most_requested_products": [
                {
                    "external_id": p.external_id,
                    "name": p.name,
                    "request_count": p.request_count,
                    "price": p.price,
                    "quantity": p.quantity,
                }
                for p in summary.most_requested_products
            ],
            "unmet_demand": [
                {
                    "message": u.message,
                    "occurrences": u.occurrences,
                    "last_seen": u.last_seen.isoformat(),
                }
                for u in summary.unmet_demand
            ],
            "intent_distribution": summary.intent_distribution,
            "peak_hours": summary.peak_hours,
            "coupon_conversion": summary.coupon_conversion,
            "low_stock_products": summary.low_stock_products,
        }

    async def _handle_marketing_strategy(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Stratégie marketing générée par le LLM à partir de signaux réels
        (InsightsService), pas de params bruts non vérifiés."""
        insights = InsightsService(self.db, self.tenant_id)
        summary = await insights.summary(datetime.utcnow() - timedelta(days=30))

        objective = params.get("objective", "retention")
        top_products = (
            ", ".join((p.name or p.external_id) for p in summary.most_requested_products[:5])
            or "aucun signal de demande sur cette période"
        )
        unmet = ", ".join(u.message for u in summary.unmet_demand[:5]) or "aucune"
        low_stock = ", ".join(p["name"] for p in summary.low_stock_products[:5]) or "aucun"

        data_context = (
            f"Produits les plus demandés (30 derniers jours): {top_products}. "
            f"Recherches sans résultat (opportunités catalogue): {unmet}. "
            f"Répartition des intentions client: {summary.intent_distribution}. "
            f"Taux de conversion des coupons: {summary.coupon_conversion.get('conversion_rate', 0)}. "
            f"Produits bientôt en rupture: {low_stock}."
        )
        system_prompt = (
            "Tu es un assistant d'analyse pour administrateurs e-commerce. "
            f"L'objectif est: {objective}. À partir des données réelles fournies, "
            "propose 2 à 3 actions marketing concrètes, chacune justifiée en une "
            "phrase par une donnée du contexte. Réponds en français, en texte simple."
        )

        llm = get_llm_provider()
        analysis = await llm.chat(message=data_context, context=system_prompt)

        return {"objective": objective, "analysis": analysis, "based_on": data_context}

    async def _handle_bulk_coupons(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Génération réelle de coupons persistés - remplace le
        `{"status": "generated"}` fabriqué qui ne créait jamais rien."""
        customer_ids: List[str] = params.get("customer_ids", [])
        # Parameters arrive over HTTP as JSON - some callers (e.g. the
        # backoffice's KeyValue form field) only ever produce strings, so
        # coerce defensively rather than pass a string into an Integer
        # column / timedelta(days=...).
        discount_percent = int(float(params.get("discount_percent", 10) or 10))
        validity_days = int(float(params.get("validity_days", 7) or 7))

        repo = CouponRepository(self.db, self.tenant_id)
        codes: List[str] = []
        for customer_id in customer_ids:
            code = repo.generate_code()
            expires_at = datetime.utcnow() + timedelta(days=validity_days)
            coupon = await repo.create(
                code=code,
                discount_percent=discount_percent,
                discount_amount=None,
                min_purchase=None,
                expires_at=expires_at,
                reason="admin_bulk",
                customer_id=customer_id,
            )
            codes.append(coupon.code)
        await self.db.commit()

        return {
            "coupons_generated": len(codes),
            "codes": codes,
            "discount_percent": discount_percent,
            "validity_days": validity_days,
        }
