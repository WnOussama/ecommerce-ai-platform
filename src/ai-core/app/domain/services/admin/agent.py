"""
Admin AI Agent - Agent pour les opérations administrateur
Inclut validation stricte, audit logging, et confirmation des actions sensibles
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID, uuid4

from app.domain.entities.models import (
    AdminAction,
    AdminActionStatus,
    AdminActionType,
    LLMUsage,
    Tenant,
)

logger = logging.getLogger(__name__)


# ============================================================================
# TYPES
# ============================================================================


class RiskLevel(str, Enum):
    LOW = "low"  # Analytics, rapports
    MEDIUM = "medium"  # Génération coupons, suggestions
    HIGH = "high"  # Modifications prix, produits
    CRITICAL = "critical"  # Suppressions, actions bulk


@dataclass
class AdminCommand:
    """Commande admin parsée et validée"""

    command_type: AdminActionType
    parameters: Dict[str, Any]
    risk_level: RiskLevel
    requires_confirmation: bool
    estimated_impact: Dict[str, Any]
    ai_reasoning: str


@dataclass
class AdminResponse:
    """Réponse structurée de l'agent admin"""

    success: bool
    action_id: UUID
    command_type: AdminActionType
    status: AdminActionStatus

    # Résultat
    result: Optional[Dict[str, Any]] = None
    message: str = ""

    # Pour actions nécessitant confirmation
    requires_confirmation: bool = False
    confirmation_details: Optional[Dict[str, Any]] = None
    confirmation_expires_at: Optional[datetime] = None

    # Métriques
    execution_time_ms: Optional[int] = None
    llm_usage: Optional[LLMUsage] = None


# ============================================================================
# COMMAND VALIDATOR
# ============================================================================


class AdminCommandValidator:
    """
    Validateur strict des commandes admin.
    Applique des règles de sécurité et des limites.
    """

    # Limites par type d'action
    ACTION_LIMITS = {
        AdminActionType.PRICE_UPDATE: {
            "max_products_per_request": 100,
            "max_price_change_percent": 50,
            "min_price_change_percent": -50,
        },
        AdminActionType.COUPON_GENERATE: {
            "max_coupons_per_request": 50,
            "max_discount_percent": 30,
            "max_validity_days": 90,
        },
        AdminActionType.PRODUCT_UPDATE: {
            "max_products_per_request": 50,
        },
    }

    # Actions nécessitant confirmation obligatoire
    REQUIRES_CONFIRMATION = [
        AdminActionType.PRICE_UPDATE,
        AdminActionType.PRODUCT_UPDATE,
        AdminActionType.CAMPAIGN_CREATE,
    ]

    def validate(
        self, command_type: AdminActionType, parameters: Dict[str, Any], tenant: Tenant
    ) -> Tuple[bool, List[str]]:
        """
        Valide une commande admin.
        Retourne (is_valid, list_of_errors)
        """
        errors = []

        # Vérifier que le tenant a accès à cette fonctionnalité
        if not self._tenant_has_feature(tenant, command_type):
            errors.append(f"Feature not enabled for tenant plan: {tenant.plan.value}")
            return False, errors

        # Validation spécifique par type
        limits = self.ACTION_LIMITS.get(command_type, {})

        if command_type == AdminActionType.PRICE_UPDATE:
            errors.extend(self._validate_price_update(parameters, limits))

        elif command_type == AdminActionType.COUPON_GENERATE:
            errors.extend(self._validate_coupon_generate(parameters, limits))

        elif command_type == AdminActionType.PRODUCT_UPDATE:
            errors.extend(self._validate_product_update(parameters, limits))

        return len(errors) == 0, errors

    def _tenant_has_feature(self, tenant: Tenant, action_type: AdminActionType) -> bool:
        """Vérifie si le tenant a accès à cette fonctionnalité"""
        feature_map = {
            AdminActionType.ANALYTICS_QUERY: "analytics",
            AdminActionType.PRICE_UPDATE: "admin_ai",
            AdminActionType.PRODUCT_UPDATE: "admin_ai",
            AdminActionType.CAMPAIGN_CREATE: "admin_ai",
            AdminActionType.COUPON_GENERATE: "coupons",
            AdminActionType.STRATEGY_GENERATE: "admin_ai",
        }
        required_feature = feature_map.get(action_type, "admin_ai")
        return tenant.can_use_feature(required_feature)

    def _validate_price_update(self, params: Dict[str, Any], limits: Dict) -> List[str]:
        """Valide une demande de mise à jour de prix"""
        errors = []

        product_ids = params.get("product_ids", [])
        if len(product_ids) > limits.get("max_products_per_request", 100):
            errors.append(f"Too many products: max {limits['max_products_per_request']}")

        change_percent = params.get("change_percent", 0)
        if change_percent > limits.get("max_price_change_percent", 50):
            errors.append(f"Price increase too high: max {limits['max_price_change_percent']}%")
        if change_percent < limits.get("min_price_change_percent", -50):
            errors.append(f"Price decrease too high: min {limits['min_price_change_percent']}%")

        return errors

    def _validate_coupon_generate(self, params: Dict[str, Any], limits: Dict) -> List[str]:
        """Valide une demande de génération de coupons"""
        errors = []

        count = params.get("count", 1)
        if count > limits.get("max_coupons_per_request", 50):
            errors.append(f"Too many coupons: max {limits['max_coupons_per_request']}")

        discount = params.get("discount_percent", 0)
        if discount > limits.get("max_discount_percent", 30):
            errors.append(f"Discount too high: max {limits['max_discount_percent']}%")

        validity_days = params.get("validity_days", 30)
        if validity_days > limits.get("max_validity_days", 90):
            errors.append(f"Validity too long: max {limits['max_validity_days']} days")

        return errors

    def _validate_product_update(self, params: Dict[str, Any], limits: Dict) -> List[str]:
        """Valide une demande de mise à jour de produits"""
        errors = []

        product_ids = params.get("product_ids", [])
        if len(product_ids) > limits.get("max_products_per_request", 50):
            errors.append(f"Too many products: max {limits['max_products_per_request']}")

        return errors

    def get_risk_level(self, command_type: AdminActionType) -> RiskLevel:
        """Détermine le niveau de risque d'une action"""
        risk_mapping = {
            AdminActionType.ANALYTICS_QUERY: RiskLevel.LOW,
            AdminActionType.STRATEGY_GENERATE: RiskLevel.LOW,
            AdminActionType.COUPON_GENERATE: RiskLevel.MEDIUM,
            AdminActionType.CAMPAIGN_CREATE: RiskLevel.MEDIUM,
            AdminActionType.PRICE_UPDATE: RiskLevel.HIGH,
            AdminActionType.PRODUCT_UPDATE: RiskLevel.HIGH,
        }
        return risk_mapping.get(command_type, RiskLevel.MEDIUM)

    def requires_confirmation(self, command_type: AdminActionType) -> bool:
        """Vérifie si l'action nécessite une confirmation"""
        return command_type in self.REQUIRES_CONFIRMATION


# ============================================================================
# AUDIT LOGGER
# ============================================================================


class AdminAuditLogger:
    """
    Logger d'audit pour toutes les actions admin.
    Critique pour la compliance et le debugging.
    """

    def __init__(self, action_repository):
        self.action_repo = action_repository

    async def log_action(
        self,
        tenant_id: UUID,
        action_type: AdminActionType,
        request_payload: Dict[str, Any],
        ai_reasoning: str,
        initiated_by: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
        requires_approval: bool = False,
    ) -> AdminAction:
        """Log une action admin et retourne l'entité créée"""

        action = AdminAction(
            id=uuid4(),
            tenant_id=tenant_id,
            action_type=action_type,
            status=AdminActionStatus.PENDING,
            request_payload=request_payload,
            ai_reasoning=ai_reasoning,
            requires_approval=requires_approval,
            initiated_by=initiated_by,
            ip_address=ip_address,
            user_agent=user_agent,
            created_at=datetime.utcnow(),
        )

        await self.action_repo.save(action)

        logger.info(
            "Admin action logged",
            extra={
                "action_id": str(action.id),
                "tenant_id": str(tenant_id),
                "action_type": action_type.value,
                "initiated_by": initiated_by,
                "requires_approval": requires_approval,
            },
        )

        return action

    async def log_approval(
        self,
        action_id: UUID,
        approved: bool,
        approved_by: str,
        rejection_reason: Optional[str] = None,
    ):
        """Log l'approbation ou le rejet d'une action"""
        action = await self.action_repo.get(action_id)
        if not action:
            raise ValueError(f"Action not found: {action_id}")

        if approved:
            action.status = AdminActionStatus.APPROVED
            action.approved_by = approved_by
            action.approved_at = datetime.utcnow()
        else:
            action.status = AdminActionStatus.REJECTED
            action.error_message = rejection_reason

        await self.action_repo.save(action)

        logger.info(
            f"Admin action {'approved' if approved else 'rejected'}",
            extra={"action_id": str(action_id), "approved_by": approved_by, "approved": approved},
        )

    async def log_execution(
        self,
        action_id: UUID,
        success: bool,
        result: Optional[Dict[str, Any]] = None,
        error_message: Optional[str] = None,
        execution_time_ms: Optional[int] = None,
    ):
        """Log l'exécution d'une action"""
        action = await self.action_repo.get(action_id)
        if not action:
            raise ValueError(f"Action not found: {action_id}")

        if success:
            action.status = AdminActionStatus.EXECUTED
            action.result_payload = result
        else:
            action.status = AdminActionStatus.FAILED
            action.error_message = error_message

        action.executed_at = datetime.utcnow()
        action.execution_duration_ms = execution_time_ms

        await self.action_repo.save(action)

        logger.info(
            f"Admin action {'executed' if success else 'failed'}",
            extra={
                "action_id": str(action_id),
                "success": success,
                "execution_time_ms": execution_time_ms,
            },
        )


# ============================================================================
# ADMIN AI AGENT
# ============================================================================


class AdminAIAgent:
    """
    Agent IA pour les opérations administrateur.

    Responsabilités:
    - Analytics et insights
    - Génération de stratégies marketing
    - Optimisation de prix (avec validation)
    - Gestion de campagnes
    - Recommandations opérationnelles
    """

    def __init__(
        self,
        llm_service,
        vector_store,
        analytics_service,
        campaign_service,
        pricing_service,
        tenant_repo,
        action_repo,
    ):
        self.llm = llm_service
        self.vector_store = vector_store
        self.analytics = analytics_service
        self.campaigns = campaign_service
        self.pricing = pricing_service
        self.tenants = tenant_repo

        self.validator = AdminCommandValidator()
        self.audit_logger = AdminAuditLogger(action_repo)

    async def execute_command(
        self,
        tenant_id: UUID,
        command: str,
        parameters: Dict[str, Any],
        user_id: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AdminResponse:
        """
        Point d'entrée principal pour exécuter une commande admin.

        Pipeline:
        1. Parse et classification de la commande
        2. Validation stricte
        3. Audit log
        4. Si confirmation requise: retour avec détails
        5. Sinon: exécution et log résultat
        """
        start_time = datetime.utcnow()

        # 1. Récupérer tenant
        tenant = await self.tenants.get(tenant_id)
        if not tenant or not tenant.is_active():
            return AdminResponse(
                success=False,
                action_id=uuid4(),
                command_type=AdminActionType.ANALYTICS_QUERY,
                status=AdminActionStatus.FAILED,
                message="Tenant not found or inactive",
            )

        # 2. Parser la commande
        parsed_command = await self._parse_command(command, parameters, tenant)

        # 3. Valider
        is_valid, errors = self.validator.validate(
            parsed_command.command_type, parsed_command.parameters, tenant
        )

        if not is_valid:
            return AdminResponse(
                success=False,
                action_id=uuid4(),
                command_type=parsed_command.command_type,
                status=AdminActionStatus.REJECTED,
                message=f"Validation failed: {'; '.join(errors)}",
            )

        # 4. Log audit
        action = await self.audit_logger.log_action(
            tenant_id=tenant_id,
            action_type=parsed_command.command_type,
            request_payload=parsed_command.parameters,
            ai_reasoning=parsed_command.ai_reasoning,
            initiated_by=user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            requires_approval=parsed_command.requires_confirmation,
        )

        # 5. Si confirmation requise
        if parsed_command.requires_confirmation:
            return AdminResponse(
                success=True,
                action_id=action.id,
                command_type=parsed_command.command_type,
                status=AdminActionStatus.PENDING,
                message="Action requires confirmation",
                requires_confirmation=True,
                confirmation_details={
                    "action_type": parsed_command.command_type.value,
                    "risk_level": parsed_command.risk_level.value,
                    "estimated_impact": parsed_command.estimated_impact,
                    "ai_reasoning": parsed_command.ai_reasoning,
                    "parameters": parsed_command.parameters,
                },
                confirmation_expires_at=datetime.utcnow() + timedelta(hours=24),
            )

        # 6. Exécution
        try:
            result = await self._execute_action(parsed_command, tenant)

            execution_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)

            await self.audit_logger.log_execution(
                action.id, success=True, result=result, execution_time_ms=execution_time
            )

            return AdminResponse(
                success=True,
                action_id=action.id,
                command_type=parsed_command.command_type,
                status=AdminActionStatus.EXECUTED,
                result=result,
                message="Action executed successfully",
                execution_time_ms=execution_time,
            )

        except Exception as e:
            logger.error(f"Action execution failed: {str(e)}", exc_info=True)

            await self.audit_logger.log_execution(action.id, success=False, error_message=str(e))

            return AdminResponse(
                success=False,
                action_id=action.id,
                command_type=parsed_command.command_type,
                status=AdminActionStatus.FAILED,
                message=f"Execution failed: {str(e)}",
            )

    async def confirm_action(
        self, action_id: UUID, approved: bool, user_id: str, rejection_reason: Optional[str] = None
    ) -> AdminResponse:
        """Confirme ou rejette une action en attente"""

        await self.audit_logger.log_approval(action_id, approved, user_id, rejection_reason)

        if not approved:
            return AdminResponse(
                success=True,
                action_id=action_id,
                command_type=AdminActionType.ANALYTICS_QUERY,  # Will be updated
                status=AdminActionStatus.REJECTED,
                message=f"Action rejected: {rejection_reason or 'No reason provided'}",
            )

        # Récupérer et exécuter l'action
        action = await self.audit_logger.action_repo.get(action_id)

        # Reconstruire la commande depuis l'action
        parsed_command = AdminCommand(
            command_type=action.action_type,
            parameters=action.request_payload,
            risk_level=self.validator.get_risk_level(action.action_type),
            requires_confirmation=False,  # Already confirmed
            estimated_impact={},
            ai_reasoning=action.ai_reasoning,
        )

        tenant = await self.tenants.get(action.tenant_id)

        try:
            result = await self._execute_action(parsed_command, tenant)

            await self.audit_logger.log_execution(action_id, success=True, result=result)

            return AdminResponse(
                success=True,
                action_id=action_id,
                command_type=action.action_type,
                status=AdminActionStatus.EXECUTED,
                result=result,
                message="Action executed successfully after confirmation",
            )

        except Exception as e:
            await self.audit_logger.log_execution(action_id, success=False, error_message=str(e))

            return AdminResponse(
                success=False,
                action_id=action_id,
                command_type=action.action_type,
                status=AdminActionStatus.FAILED,
                message=f"Execution failed: {str(e)}",
            )

    async def _parse_command(
        self, command: str, parameters: Dict[str, Any], tenant: Tenant
    ) -> AdminCommand:
        """Parse une commande en utilisant le LLM si nécessaire"""

        # Si le type de commande est explicite
        if "command_type" in parameters:
            command_type = AdminActionType(parameters["command_type"])
        else:
            # Utiliser le LLM pour classifier
            command_type = await self._classify_command(command)

        risk_level = self.validator.get_risk_level(command_type)
        requires_confirmation = self.validator.requires_confirmation(command_type)

        # Générer le raisonnement IA
        ai_reasoning = await self._generate_reasoning(command, command_type, parameters)

        # Estimer l'impact
        estimated_impact = await self._estimate_impact(command_type, parameters, tenant)

        return AdminCommand(
            command_type=command_type,
            parameters=parameters,
            risk_level=risk_level,
            requires_confirmation=requires_confirmation,
            estimated_impact=estimated_impact,
            ai_reasoning=ai_reasoning,
        )

    async def _classify_command(self, command: str) -> AdminActionType:
        """Classifie la commande via LLM"""
        # Implémentation avec prompt spécifique
        return AdminActionType.ANALYTICS_QUERY

    async def _generate_reasoning(
        self, command: str, command_type: AdminActionType, parameters: Dict[str, Any]
    ) -> str:
        """Génère une explication du raisonnement de l'IA"""
        # Implémentation avec LLM
        return f"Executing {command_type.value} based on request: {command[:100]}"

    async def _estimate_impact(
        self, command_type: AdminActionType, parameters: Dict[str, Any], tenant: Tenant
    ) -> Dict[str, Any]:
        """Estime l'impact de l'action"""

        if command_type == AdminActionType.PRICE_UPDATE:
            product_count = len(parameters.get("product_ids", []))
            change = parameters.get("change_percent", 0)
            return {
                "products_affected": product_count,
                "price_change": f"{change:+.1f}%",
                "estimated_revenue_impact": "Requires analysis",
            }

        elif command_type == AdminActionType.COUPON_GENERATE:
            return {
                "coupons_to_create": parameters.get("count", 1),
                "discount": f"{parameters.get('discount_percent', 0)}%",
                "validity": f"{parameters.get('validity_days', 30)} days",
            }

        return {}

    async def _execute_action(self, command: AdminCommand, tenant: Tenant) -> Dict[str, Any]:
        """Exécute une action validée"""

        if command.command_type == AdminActionType.ANALYTICS_QUERY:
            return await self._execute_analytics(command.parameters, tenant)

        elif command.command_type == AdminActionType.STRATEGY_GENERATE:
            return await self._execute_strategy_generation(command.parameters, tenant)

        elif command.command_type == AdminActionType.PRICE_UPDATE:
            return await self._execute_price_update(command.parameters, tenant)

        elif command.command_type == AdminActionType.COUPON_GENERATE:
            return await self._execute_coupon_generation(command.parameters, tenant)

        elif command.command_type == AdminActionType.CAMPAIGN_CREATE:
            return await self._execute_campaign_creation(command.parameters, tenant)

        raise ValueError(f"Unknown command type: {command.command_type}")

    async def _execute_analytics(
        self, parameters: Dict[str, Any], tenant: Tenant
    ) -> Dict[str, Any]:
        """Exécute une requête analytics"""
        query_type = parameters.get("query_type", "overview")
        period_days = parameters.get("period_days", 30)

        if query_type == "overview":
            return await self.analytics.get_overview(tenant.id, period_days)
        elif query_type == "top_products":
            return await self.analytics.get_top_products(tenant.id, period_days)
        elif query_type == "customer_segments":
            return await self.analytics.get_customer_segments(tenant.id)
        elif query_type == "chatbot_performance":
            return await self.analytics.get_chatbot_metrics(tenant.id, period_days)

        return {"error": "Unknown query type"}

    async def _execute_strategy_generation(
        self, parameters: Dict[str, Any], tenant: Tenant
    ) -> Dict[str, Any]:
        """Génère une stratégie marketing via IA"""
        objective = parameters.get("objective", "increase_sales")

        # Collecter les données pour le contexte
        analytics_data = await self.analytics.get_overview(tenant.id, 30)
        segments_data = await self.analytics.get_customer_segments(tenant.id)

        # Générer via LLM avec contexte riche
        strategy = await self.llm.generate_strategy(
            objective=objective,
            analytics=analytics_data,
            segments=segments_data,
            tenant_settings=tenant.settings,
        )

        return strategy

    async def _execute_price_update(
        self, parameters: Dict[str, Any], tenant: Tenant
    ) -> Dict[str, Any]:
        """Exécute une mise à jour de prix"""
        return await self.pricing.update_prices(
            tenant_id=tenant.id,
            product_ids=parameters["product_ids"],
            change_percent=parameters.get("change_percent"),
            new_prices=parameters.get("new_prices"),
            reason=parameters.get("reason", "admin_command"),
        )

    async def _execute_coupon_generation(
        self, parameters: Dict[str, Any], tenant: Tenant
    ) -> Dict[str, Any]:
        """Génère des coupons"""
        # Implémentation via coupon service
        return {"generated": parameters.get("count", 1)}

    async def _execute_campaign_creation(
        self, parameters: Dict[str, Any], tenant: Tenant
    ) -> Dict[str, Any]:
        """Crée une campagne marketing"""
        return await self.campaigns.create(
            tenant_id=tenant.id,
            name=parameters["name"],
            campaign_type=parameters["type"],
            settings=parameters.get("settings", {}),
        )
