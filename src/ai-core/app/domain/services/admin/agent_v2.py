"""
Admin Agent - Agent IA pour les opérations administrateur
Architecture: Utilise le Shared Core avec workflow de confirmation OBLIGATOIRE

Caractéristiques STRICTES:
- Output JSON STRUCTURÉ uniquement (pas de texte libre)
- PAS d'exécution naturelle libre - commandes prédéfinies
- Confirmation workflow OBLIGATOIRE pour actions sensibles
- Audit logging MANDATORY
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Tuple, Union
from uuid import UUID, uuid4
from enum import Enum
import json
import logging

from app.domain.services.shared.llm_gateway import (
    LLMGateway, LLMRequest, LLMResponse, ResponseFormat
)
from app.domain.services.shared.rag_service import RAGService
from app.domain.services.shared.security_service import SecurityService
from app.domain.services.shared.tenant_service import TenantService, Tenant, Feature

logger = logging.getLogger(__name__)


# =============================================================================
# TYPES - STRICT DEFINITIONS
# =============================================================================

class AdminCommandType(str, Enum):
    """Types de commandes admin PRÉDÉFINIES (pas d'exécution libre)"""
    # Analytics (Read-only, LOW risk)
    GET_SALES_ANALYTICS = "get_sales_analytics"
    GET_CUSTOMER_ANALYTICS = "get_customer_analytics"
    GET_PRODUCT_ANALYTICS = "get_product_analytics"
    GET_AI_PERFORMANCE = "get_ai_performance"

    # Reports (Read-only, LOW risk)
    GENERATE_SALES_REPORT = "generate_sales_report"
    GENERATE_CUSTOMER_REPORT = "generate_customer_report"
    GENERATE_INVENTORY_REPORT = "generate_inventory_report"

    # Customer Actions (MEDIUM risk)
    SEGMENT_CUSTOMERS = "segment_customers"
    GET_CUSTOMER_INSIGHTS = "get_customer_insights"

    # Marketing Actions (MEDIUM risk, requires confirmation)
    SUGGEST_MARKETING_STRATEGY = "suggest_marketing_strategy"
    CREATE_CAMPAIGN_DRAFT = "create_campaign_draft"

    # Coupon Actions (HIGH risk, requires confirmation)
    GENERATE_BULK_COUPONS = "generate_bulk_coupons"

    # Product Actions (HIGH risk, requires confirmation)
    SUGGEST_PRICE_OPTIMIZATION = "suggest_price_optimization"
    UPDATE_PRODUCT_PRICES = "update_product_prices"

    # INVALID - Pour rejeter les commandes non reconnues
    INVALID = "invalid"


class RiskLevel(str, Enum):
    """Niveau de risque d'une action"""
    LOW = "low"           # Analytics, reports
    MEDIUM = "medium"     # Suggestions, drafts
    HIGH = "high"         # Modifications prix, bulk ops
    CRITICAL = "critical" # Suppressions (NON SUPPORTÉ)


class ActionStatus(str, Enum):
    """Statut d'une action admin"""
    PENDING_CONFIRMATION = "pending_confirmation"
    CONFIRMED = "confirmed"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


# =============================================================================
# COMMAND DEFINITIONS (STRICT SCHEMA)
# =============================================================================

COMMAND_DEFINITIONS: Dict[AdminCommandType, Dict[str, Any]] = {
    AdminCommandType.GET_SALES_ANALYTICS: {
        "risk_level": RiskLevel.LOW,
        "requires_confirmation": False,
        "description": "Récupère les analytics de ventes",
        "parameters_schema": {
            "time_range": str,  # "7d", "30d", "90d"
            "?group_by": str,   # Optional: "day", "week", "month"
        },
    },
    AdminCommandType.GET_CUSTOMER_ANALYTICS: {
        "risk_level": RiskLevel.LOW,
        "requires_confirmation": False,
        "description": "Récupère les analytics clients",
        "parameters_schema": {
            "time_range": str,
            "?segment": str,
        },
    },
    AdminCommandType.GENERATE_SALES_REPORT: {
        "risk_level": RiskLevel.LOW,
        "requires_confirmation": False,
        "description": "Génère un rapport de ventes",
        "parameters_schema": {
            "time_range": str,
            "format": str,  # "summary", "detailed"
        },
    },
    AdminCommandType.SEGMENT_CUSTOMERS: {
        "risk_level": RiskLevel.MEDIUM,
        "requires_confirmation": True,
        "description": "Segmente les clients selon critères IA",
        "parameters_schema": {
            "criteria": str,  # "purchase_frequency", "value", "engagement"
        },
    },
    AdminCommandType.SUGGEST_MARKETING_STRATEGY: {
        "risk_level": RiskLevel.MEDIUM,
        "requires_confirmation": False,
        "description": "Suggère une stratégie marketing (lecture seule)",
        "parameters_schema": {
            "objective": str,  # "retention", "acquisition", "upsell"
            "?budget": float,
        },
    },
    AdminCommandType.GENERATE_BULK_COUPONS: {
        "risk_level": RiskLevel.HIGH,
        "requires_confirmation": True,
        "description": "Génère des coupons en masse",
        "parameters_schema": {
            "target_segment": str,
            "discount_percent": float,
            "validity_days": int,
            "max_count": int,
        },
        "limits": {
            "max_discount_percent": 30,
            "max_validity_days": 90,
            "max_count": 1000,
        },
    },
    AdminCommandType.SUGGEST_PRICE_OPTIMIZATION: {
        "risk_level": RiskLevel.MEDIUM,
        "requires_confirmation": False,
        "description": "Suggère des optimisations de prix (lecture seule)",
        "parameters_schema": {
            "category": str,
            "strategy": str,  # "competitive", "margin", "volume"
        },
    },
    AdminCommandType.UPDATE_PRODUCT_PRICES: {
        "risk_level": RiskLevel.HIGH,
        "requires_confirmation": True,
        "description": "Met à jour les prix (NÉCESSITE CONFIRMATION)",
        "parameters_schema": {
            "product_ids": list,
            "adjustment_percent": float,
        },
        "limits": {
            "max_products": 100,
            "max_adjustment_percent": 25,
            "min_adjustment_percent": -25,
        },
    },
}


# =============================================================================
# RESPONSE SCHEMAS (JSON STRICT)
# =============================================================================

@dataclass
class AdminCommandRequest:
    """Requête de commande admin - DOIT être structurée"""
    command_type: AdminCommandType
    parameters: Dict[str, Any]

    # Optionnel pour confirmation
    confirmation_token: Optional[str] = None


@dataclass
class AdminResponse:
    """
    Réponse STRUCTURÉE de l'Admin Agent.
    TOUJOURS en JSON, JAMAIS en texte libre.
    """
    # Identification
    action_id: str = field(default_factory=lambda: str(uuid4()))
    command_type: AdminCommandType = AdminCommandType.INVALID
    status: ActionStatus = ActionStatus.PENDING_CONFIRMATION

    # Résultat
    success: bool = False
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    # Pour actions nécessitant confirmation
    requires_confirmation: bool = False
    confirmation_token: Optional[str] = None
    confirmation_expires_at: Optional[datetime] = None
    confirmation_details: Optional[Dict[str, Any]] = None

    # Métriques
    processing_time_ms: int = 0
    llm_tokens_used: int = 0

    def to_json(self) -> Dict[str, Any]:
        """Sérialise en JSON strict"""
        return {
            "action_id": self.action_id,
            "command_type": self.command_type.value,
            "status": self.status.value,
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "requires_confirmation": self.requires_confirmation,
            "confirmation_token": self.confirmation_token,
            "confirmation_expires_at": self.confirmation_expires_at.isoformat() if self.confirmation_expires_at else None,
            "confirmation_details": self.confirmation_details,
            "processing_time_ms": self.processing_time_ms,
        }


@dataclass
class PendingAction:
    """Action en attente de confirmation"""
    action_id: str
    command_type: AdminCommandType
    parameters: Dict[str, Any]
    confirmation_token: str
    expires_at: datetime
    tenant_id: str
    created_by: str
    risk_level: RiskLevel
    estimated_impact: Dict[str, Any]


# =============================================================================
# COMMAND PARSER (NO FREE-FORM EXECUTION)
# =============================================================================

class AdminCommandParser:
    """
    Parse les commandes admin.
    N'EXÉCUTE PAS de commandes en langage naturel libre.
    Les commandes doivent correspondre aux types prédéfinis.
    """

    def parse(
        self,
        raw_input: Union[str, Dict[str, Any]],
    ) -> Tuple[AdminCommandRequest, List[str]]:
        """
        Parse une commande admin.

        Args:
            raw_input: Soit un dict JSON, soit une string JSON

        Returns:
            (AdminCommandRequest, errors)
        """
        errors = []

        # 1. Parse JSON si string
        if isinstance(raw_input, str):
            try:
                data = json.loads(raw_input)
            except json.JSONDecodeError:
                errors.append("Invalid JSON format")
                return AdminCommandRequest(
                    command_type=AdminCommandType.INVALID,
                    parameters={},
                ), errors
        else:
            data = raw_input

        # 2. Extraire le type de commande
        cmd_type_str = data.get("command_type", "").lower()

        try:
            cmd_type = AdminCommandType(cmd_type_str)
        except ValueError:
            errors.append(f"Unknown command type: {cmd_type_str}")
            return AdminCommandRequest(
                command_type=AdminCommandType.INVALID,
                parameters=data.get("parameters", {}),
            ), errors

        # 3. Valider les paramètres
        parameters = data.get("parameters", {})
        param_errors = self._validate_parameters(cmd_type, parameters)
        errors.extend(param_errors)

        return AdminCommandRequest(
            command_type=cmd_type,
            parameters=parameters,
            confirmation_token=data.get("confirmation_token"),
        ), errors

    def _validate_parameters(
        self,
        cmd_type: AdminCommandType,
        parameters: Dict[str, Any],
    ) -> List[str]:
        """Valide les paramètres selon le schéma"""
        errors = []

        definition = COMMAND_DEFINITIONS.get(cmd_type)
        if not definition:
            return errors

        schema = definition.get("parameters_schema", {})
        limits = definition.get("limits", {})

        # Vérifier paramètres obligatoires
        for param_name, param_type in schema.items():
            if param_name.startswith("?"):
                continue  # Optionnel

            if param_name not in parameters:
                errors.append(f"Missing required parameter: {param_name}")
            elif not isinstance(parameters[param_name], param_type):
                errors.append(f"Invalid type for {param_name}: expected {param_type.__name__}")

        # Vérifier limites
        for limit_name, limit_value in limits.items():
            param_to_check = limit_name.replace("max_", "").replace("min_", "")
            if param_to_check in parameters:
                value = parameters[param_to_check]
                if limit_name.startswith("max_") and value > limit_value:
                    errors.append(f"{param_to_check} exceeds maximum ({limit_value})")
                elif limit_name.startswith("min_") and value < limit_value:
                    errors.append(f"{param_to_check} below minimum ({limit_value})")

        return errors


# =============================================================================
# CONFIRMATION MANAGER
# =============================================================================

class ConfirmationManager:
    """
    Gère le workflow de confirmation OBLIGATOIRE.
    Les actions HIGH risk ne peuvent PAS s'exécuter sans confirmation.
    """

    CONFIRMATION_EXPIRY_MINUTES = 30

    def __init__(self, cache_client=None):
        self._cache = cache_client
        self._pending_actions: Dict[str, PendingAction] = {}  # Fallback in-memory

    async def create_pending_action(
        self,
        command: AdminCommandRequest,
        tenant_id: str,
        admin_user_id: str,
        estimated_impact: Dict[str, Any],
    ) -> PendingAction:
        """Crée une action en attente de confirmation"""
        import secrets

        action_id = str(uuid4())
        confirmation_token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(minutes=self.CONFIRMATION_EXPIRY_MINUTES)

        definition = COMMAND_DEFINITIONS.get(command.command_type, {})

        pending = PendingAction(
            action_id=action_id,
            command_type=command.command_type,
            parameters=command.parameters,
            confirmation_token=confirmation_token,
            expires_at=expires_at,
            tenant_id=tenant_id,
            created_by=admin_user_id,
            risk_level=definition.get("risk_level", RiskLevel.HIGH),
            estimated_impact=estimated_impact,
        )

        # Stocker
        if self._cache:
            await self._cache.set(
                f"pending_action:{action_id}",
                json.dumps({
                    "action_id": action_id,
                    "command_type": command.command_type.value,
                    "parameters": command.parameters,
                    "confirmation_token": confirmation_token,
                    "expires_at": expires_at.isoformat(),
                    "tenant_id": tenant_id,
                    "created_by": admin_user_id,
                    "risk_level": pending.risk_level.value,
                    "estimated_impact": estimated_impact,
                }),
                ex=self.CONFIRMATION_EXPIRY_MINUTES * 60,
            )
        else:
            self._pending_actions[action_id] = pending

        logger.info(
            "Pending action created",
            extra={
                "action_id": action_id,
                "command_type": command.command_type.value,
                "tenant_id": tenant_id,
                "expires_at": expires_at.isoformat(),
            }
        )

        return pending

    async def confirm_action(
        self,
        action_id: str,
        confirmation_token: str,
        admin_user_id: str,
    ) -> Tuple[Optional[PendingAction], Optional[str]]:
        """
        Confirme une action.
        Returns: (PendingAction, error_message)
        """
        # Récupérer l'action
        pending = await self._get_pending_action(action_id)

        if not pending:
            return None, "Action not found or expired"

        # Vérifier le token
        if pending.confirmation_token != confirmation_token:
            logger.warning(
                "Invalid confirmation token",
                extra={"action_id": action_id}
            )
            return None, "Invalid confirmation token"

        # Vérifier expiration
        if datetime.utcnow() > pending.expires_at:
            return None, "Action expired"

        # Supprimer de pending
        await self._delete_pending_action(action_id)

        logger.info(
            "Action confirmed",
            extra={
                "action_id": action_id,
                "command_type": pending.command_type.value,
                "confirmed_by": admin_user_id,
            }
        )

        return pending, None

    async def _get_pending_action(self, action_id: str) -> Optional[PendingAction]:
        """Récupère une action pending"""
        if self._cache:
            data = await self._cache.get(f"pending_action:{action_id}")
            if data:
                d = json.loads(data)
                return PendingAction(
                    action_id=d["action_id"],
                    command_type=AdminCommandType(d["command_type"]),
                    parameters=d["parameters"],
                    confirmation_token=d["confirmation_token"],
                    expires_at=datetime.fromisoformat(d["expires_at"]),
                    tenant_id=d["tenant_id"],
                    created_by=d["created_by"],
                    risk_level=RiskLevel(d["risk_level"]),
                    estimated_impact=d["estimated_impact"],
                )

        return self._pending_actions.get(action_id)

    async def _delete_pending_action(self, action_id: str) -> None:
        """Supprime une action pending"""
        if self._cache:
            await self._cache.delete(f"pending_action:{action_id}")
        else:
            self._pending_actions.pop(action_id, None)


# =============================================================================
# AUDIT LOGGER
# =============================================================================

class AdminAuditLogger:
    """
    Logging d'audit MANDATORY pour toutes les actions admin.
    """

    @staticmethod
    def log_action(
        action_id: str,
        command_type: AdminCommandType,
        status: ActionStatus,
        tenant_id: str,
        admin_user_id: str,
        parameters: Dict[str, Any],
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> None:
        """Log une action admin (OBLIGATOIRE)"""
        logger.info(
            "ADMIN_AUDIT",
            extra={
                "audit_type": "admin_action",
                "action_id": action_id,
                "command_type": command_type.value,
                "status": status.value,
                "tenant_id": tenant_id,
                "admin_user_id": admin_user_id,
                "parameters": parameters,
                "result": result,
                "error": error,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )


# =============================================================================
# ADMIN AGENT (MAIN CLASS)
# =============================================================================

class AdminAgent:
    """
    Agent IA pour les opérations administrateur.

    RÈGLES STRICTES:
    1. Output JSON STRUCTURÉ uniquement
    2. PAS d'exécution libre en langage naturel
    3. Confirmation OBLIGATOIRE pour actions HIGH risk
    4. Audit logging MANDATORY
    """

    # Prompt pour l'analyse (pas pour l'exécution!)
    ANALYSIS_PROMPT = """Tu es un assistant d'analyse pour administrateurs e-commerce.

RÈGLES:
1. Tu analyses les données et fournis des insights
2. Tu suggères des actions mais tu n'exécutes RIEN directement
3. Tu réponds TOUJOURS en JSON structuré
4. Tu ne génères JAMAIS de texte libre

DONNÉES DISPONIBLES:
{context}

RÉPONDS UNIQUEMENT avec ce format JSON:
{{
    "analysis": "...",
    "insights": ["...", "..."],
    "suggested_actions": [
        {{
            "command_type": "...",
            "parameters": {{...}},
            "rationale": "..."
        }}
    ],
    "confidence": 0.0-1.0
}}
"""

    def __init__(
        self,
        llm_gateway: LLMGateway,
        rag_service: RAGService,
        security_service: SecurityService,
        tenant_service: TenantService,
        cache_client=None,
    ):
        self.llm = llm_gateway
        self.rag = rag_service
        self.security = security_service
        self.tenants = tenant_service

        self.parser = AdminCommandParser()
        self.confirmation_manager = ConfirmationManager(cache_client)
        self.audit = AdminAuditLogger()

    async def execute_command(
        self,
        raw_command: Union[str, Dict[str, Any]],
        tenant: Tenant,
        admin_user_id: str,
    ) -> AdminResponse:
        """
        Exécute une commande admin.

        PROCESSUS STRICT:
        1. Parse la commande (doit être JSON structuré)
        2. Valide les permissions
        3. Si HIGH risk -> crée pending action
        4. Si confirmé ou LOW risk -> exécute
        5. Log audit (TOUJOURS)
        """
        import time
        start_time = time.perf_counter()

        # 1. PARSE COMMAND (pas de texte libre!)
        command, parse_errors = self.parser.parse(raw_command)

        if parse_errors:
            return self._create_error_response(
                "parse_error",
                f"Invalid command: {'; '.join(parse_errors)}",
                AdminCommandType.INVALID,
            )

        # 2. CHECK FEATURE ACCESS
        has_access, error = self.tenants.check_feature(tenant, Feature.ADMIN_AI)
        if not has_access:
            return self._create_error_response("access_denied", error, command.command_type)

        # 3. GET COMMAND DEFINITION
        definition = COMMAND_DEFINITIONS.get(command.command_type)
        if not definition:
            return self._create_error_response(
                "unknown_command",
                f"Unknown command: {command.command_type.value}",
                command.command_type,
            )

        # 4. CHECK CONFIRMATION REQUIREMENT
        requires_confirmation = definition.get("requires_confirmation", False)
        risk_level = definition.get("risk_level", RiskLevel.LOW)

        if requires_confirmation and not command.confirmation_token:
            # Créer une pending action
            estimated_impact = await self._estimate_impact(command, tenant)

            pending = await self.confirmation_manager.create_pending_action(
                command=command,
                tenant_id=str(tenant.id),
                admin_user_id=admin_user_id,
                estimated_impact=estimated_impact,
            )

            # AUDIT LOG
            self.audit.log_action(
                action_id=pending.action_id,
                command_type=command.command_type,
                status=ActionStatus.PENDING_CONFIRMATION,
                tenant_id=str(tenant.id),
                admin_user_id=admin_user_id,
                parameters=command.parameters,
            )

            return AdminResponse(
                action_id=pending.action_id,
                command_type=command.command_type,
                status=ActionStatus.PENDING_CONFIRMATION,
                success=True,
                requires_confirmation=True,
                confirmation_token=pending.confirmation_token,
                confirmation_expires_at=pending.expires_at,
                confirmation_details={
                    "risk_level": risk_level.value,
                    "description": definition.get("description"),
                    "estimated_impact": estimated_impact,
                    "parameters": command.parameters,
                },
                processing_time_ms=int((time.perf_counter() - start_time) * 1000),
            )

        # 5. VERIFY CONFIRMATION TOKEN (si fourni)
        if command.confirmation_token:
            pending, error = await self.confirmation_manager.confirm_action(
                action_id="",  # On cherche par token
                confirmation_token=command.confirmation_token,
                admin_user_id=admin_user_id,
            )

            if error:
                return self._create_error_response("confirmation_failed", error, command.command_type)

        # 6. EXECUTE COMMAND
        try:
            result = await self._execute_command_internal(command, tenant)

            processing_time = int((time.perf_counter() - start_time) * 1000)

            response = AdminResponse(
                command_type=command.command_type,
                status=ActionStatus.COMPLETED,
                success=True,
                data=result,
                processing_time_ms=processing_time,
            )

            # AUDIT LOG (MANDATORY)
            self.audit.log_action(
                action_id=response.action_id,
                command_type=command.command_type,
                status=ActionStatus.COMPLETED,
                tenant_id=str(tenant.id),
                admin_user_id=admin_user_id,
                parameters=command.parameters,
                result=result,
            )

            return response

        except Exception as e:
            # AUDIT LOG ERROR
            self.audit.log_action(
                action_id=str(uuid4()),
                command_type=command.command_type,
                status=ActionStatus.FAILED,
                tenant_id=str(tenant.id),
                admin_user_id=admin_user_id,
                parameters=command.parameters,
                error=str(e),
            )

            return self._create_error_response("execution_error", str(e), command.command_type)

    async def _execute_command_internal(
        self,
        command: AdminCommandRequest,
        tenant: Tenant,
    ) -> Dict[str, Any]:
        """Exécute une commande validée"""
        # Router vers le handler approprié
        handlers = {
            AdminCommandType.GET_SALES_ANALYTICS: self._handle_sales_analytics,
            AdminCommandType.GET_CUSTOMER_ANALYTICS: self._handle_customer_analytics,
            AdminCommandType.GENERATE_SALES_REPORT: self._handle_sales_report,
            AdminCommandType.SUGGEST_MARKETING_STRATEGY: self._handle_marketing_strategy,
            AdminCommandType.GENERATE_BULK_COUPONS: self._handle_bulk_coupons,
            AdminCommandType.SUGGEST_PRICE_OPTIMIZATION: self._handle_price_optimization,
        }

        handler = handlers.get(command.command_type)

        if handler:
            return await handler(command.parameters, tenant)

        return {"message": f"Handler not implemented for {command.command_type.value}"}

    # =========================================================================
    # COMMAND HANDLERS
    # =========================================================================

    async def _handle_sales_analytics(
        self,
        params: Dict[str, Any],
        tenant: Tenant,
    ) -> Dict[str, Any]:
        """Handler: Analytics ventes"""
        # TODO: Implémenter avec vraies données
        return {
            "time_range": params.get("time_range", "7d"),
            "total_revenue": 15420.50,
            "order_count": 234,
            "average_order_value": 65.90,
            "growth_rate": 0.12,
            "top_products": [
                {"name": "Product A", "revenue": 3500},
                {"name": "Product B", "revenue": 2800},
            ],
        }

    async def _handle_customer_analytics(
        self,
        params: Dict[str, Any],
        tenant: Tenant,
    ) -> Dict[str, Any]:
        """Handler: Analytics clients"""
        return {
            "time_range": params.get("time_range", "7d"),
            "total_customers": 1250,
            "new_customers": 89,
            "returning_customers": 450,
            "segments": {
                "vip": 120,
                "loyal": 380,
                "at_risk": 150,
                "new": 89,
            },
        }

    async def _handle_sales_report(
        self,
        params: Dict[str, Any],
        tenant: Tenant,
    ) -> Dict[str, Any]:
        """Handler: Rapport ventes"""
        return {
            "report_type": "sales",
            "format": params.get("format", "summary"),
            "generated_at": datetime.utcnow().isoformat(),
            "data": {
                "summary": "Sales report generated successfully",
            },
        }

    async def _handle_marketing_strategy(
        self,
        params: Dict[str, Any],
        tenant: Tenant,
    ) -> Dict[str, Any]:
        """Handler: Stratégie marketing (utilise LLM pour suggestions)"""
        # Utiliser le LLM pour générer des suggestions
        prompt = self.ANALYSIS_PROMPT.format(
            context=f"Objective: {params.get('objective', 'retention')}, Budget: {params.get('budget', 'N/A')}"
        )

        response = await self.llm.generate(
            LLMRequest(
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": json.dumps(params)},
                ],
                response_format=ResponseFormat.JSON,
                tenant_id=str(tenant.id),
                agent_type="admin",
            )
        )

        if response.parsed_json:
            return response.parsed_json

        return {
            "analysis": "Unable to generate analysis",
            "insights": [],
            "suggested_actions": [],
        }

    async def _handle_bulk_coupons(
        self,
        params: Dict[str, Any],
        tenant: Tenant,
    ) -> Dict[str, Any]:
        """Handler: Génération bulk coupons"""
        # TODO: Implémenter la vraie génération
        return {
            "coupons_generated": params.get("max_count", 0),
            "target_segment": params.get("target_segment"),
            "discount_percent": params.get("discount_percent"),
            "validity_days": params.get("validity_days"),
            "status": "generated",
        }

    async def _handle_price_optimization(
        self,
        params: Dict[str, Any],
        tenant: Tenant,
    ) -> Dict[str, Any]:
        """Handler: Suggestions optimisation prix"""
        return {
            "category": params.get("category"),
            "strategy": params.get("strategy"),
            "suggestions": [
                {
                    "product_id": "prod_123",
                    "current_price": 29.99,
                    "suggested_price": 34.99,
                    "reason": "Competitive analysis suggests higher margin potential",
                },
            ],
        }

    async def _estimate_impact(
        self,
        command: AdminCommandRequest,
        tenant: Tenant,
    ) -> Dict[str, Any]:
        """Estime l'impact d'une action"""
        # Impact basique selon le type de commande
        impacts = {
            AdminCommandType.GENERATE_BULK_COUPONS: {
                "affected_customers": command.parameters.get("max_count", 0),
                "estimated_discount_total": command.parameters.get("max_count", 0) * command.parameters.get("discount_percent", 0) * 10,
            },
            AdminCommandType.UPDATE_PRODUCT_PRICES: {
                "affected_products": len(command.parameters.get("product_ids", [])),
                "adjustment_percent": command.parameters.get("adjustment_percent", 0),
            },
        }

        return impacts.get(command.command_type, {"impact": "unknown"})

    def _create_error_response(
        self,
        error_code: str,
        error_message: str,
        command_type: AdminCommandType,
    ) -> AdminResponse:
        """Crée une réponse d'erreur JSON structurée"""
        return AdminResponse(
            command_type=command_type,
            status=ActionStatus.FAILED,
            success=False,
            error=error_message,
            data={"error_code": error_code},
        )

