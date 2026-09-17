"""
ChatTurnOrchestrator - décide la réponse à un message, sans effet de bord.

Extrait de app/api/v1/endpoints/chat.py::send_message (voir
docs/adr/0001-chat-turn-orchestrator-pure-decision-engine.md et la revue
d'architecture du 13/09/2026, Candidat 1). Regroupe derrière une seule
interface ce qui était inline dans l'endpoint : classification d'intention,
garde-fous, moteur de règles, RAG, appel LLM, construction du prompt
système, suggestions.

Ce module ne persiste rien et n'écrit rien en base - c'est un moteur de
décision pur (voir l'ADR 0001). L'appelant (l'endpoint) reçoit un
ChatTurnResult décrivant ce qui s'est passé et ce qu'il reste à faire
(persister le message assistant, exécuter la CouponDecision le cas
échéant, enregistrer l'usage de la règle), et l'exécute lui-même.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from app.core.config.settings import settings
from app.core.monitoring import get_metrics_collector
from app.core.security.guardrails import (
    GuardrailCategory,
    GuardrailResult,
    GuardrailsOrchestrator,
)
from app.core.security.guardrails import (
    guardrails as default_guardrails,
)
from app.infrastructure.llm.provider_factory import BaseLLMProvider
from app.services.chat.intent_classifier import DEFAULT_INTENT, IntentClassifier
from app.services.rag.retrieval_service import ProductRetrievalService
from app.services.rules.evaluator import RuleEvaluator, RuleLike, RuleMatch

logger = logging.getLogger(__name__)


# =============================================================================
# TYPES
# =============================================================================


class TurnOutcome(str, Enum):
    """Quelle branche de décision a produit ce ChatTurnResult - dit à
    l'endpoint quelle forme de persistance/métadonnées appliquer, sans
    qu'il ait besoin de ré-inspecter rule_match.action lui-même."""

    NORMAL = "normal"
    BLOCKED = "blocked"
    CANNED_RESPONSE = "canned_response"
    ERROR = "error"


@dataclass(frozen=True)
class CouponDecision:
    """Un coupon devrait être généré - à exécuter par l'appelant (écriture DB)."""

    rule_id: Any
    action: Dict[str, Any]


@dataclass
class ChatTurnResult:
    """Ce que le tour de conversation a décidé. Aucun champ ici n'implique
    une écriture DB - c'est à l'appelant de décider quoi persister."""

    outcome: TurnOutcome
    response: str
    intent: str
    confidence: float
    suggestions: List[str] = field(default_factory=list)
    products: List[Dict[str, Any]] = field(default_factory=list)
    rag_search_time_ms: float = 0.0
    model_name: Optional[str] = None
    rule_match: Optional[RuleMatch] = None
    coupon_decision: Optional[CouponDecision] = None
    hallucination_flagged: bool = False
    tokens_input: int = 0
    tokens_output: int = 0
    block_reason: Optional[str] = None


# =============================================================================
# SUGGESTIONS - mapping pur intention -> suggestions
# =============================================================================

_SUGGESTIONS_BY_INTENT: Dict[str, List[str]] = {
    "order_status": ["Suivre ma commande", "Contacter le support"],
    "product_search": ["Voir les promotions", "Filtrer par catégorie"],
    "price_inquiry": ["Comparer les prix", "Voir les offres"],
    "shipping_info": ["Options de livraison", "Frais de port"],
    "return_request": ["Politique de retour", "Formulaire de retour"],
    "coupon_request": ["Offres en cours", "Programme fidélité"],
    "recommendation": ["Meilleures ventes", "Nouveautés"],
    "greeting": ["Voir les produits", "Mes commandes"],
    DEFAULT_INTENT: ["Parcourir le catalogue", "Aide"],
}


def _generate_suggestions(intent: str, has_products: bool = False) -> List[str]:
    base_suggestions = _SUGGESTIONS_BY_INTENT.get(intent, ["Aide", "Catalogue"])

    if has_products:
        base_suggestions = ["Voir les détails", "Ajouter au panier"] + base_suggestions[:2]

    return base_suggestions[:4]


def _build_system_context(
    tenant_id: str, products_context: str = "", extra_instruction: Optional[str] = None
) -> str:
    base_context = f"""Tu es un assistant IA pour une boutique e-commerce.
Tenant: {tenant_id}
Sois concis, utile et professionnel. Utilise le vouvoiement.
Ne mentionne jamais de nom de produit, prix, tarif de livraison ou URL
spécifique sauf s'il provient explicitement du contexte produits fourni
ci-dessous. Si aucun contexte produits n'est fourni et que la question
porte sur des produits ou tarifs précis, dis que tu n'as pas cette
information et invite l'utilisateur à consulter le site ou le support,
plutôt que d'inventer une réponse plausible.
"""

    if extra_instruction:
        base_context = f"{base_context}\n{extra_instruction}\n"

    if products_context:
        return f"""{base_context}
{products_context}

Utilise ces informations produits pour répondre à la question de l'utilisateur.
Si les produits ne sont pas pertinents pour la question, réponds normalement sans les mentionner.
"""

    return base_context


# =============================================================================
# ORCHESTRATOR
# =============================================================================


class ChatTurnOrchestrator:
    """
    process_turn(...) -> ChatTurnResult.

    Les collaborateurs stables (garde-fous, évaluateur de règles,
    classifieur d'intention, métriques) sont acceptés au constructeur avec
    des valeurs par défaut réelles ; les collaborateurs qui varient par
    requête (service RAG, provider LLM, règles actives du tenant) sont des
    paramètres de process_turn - l'appelant les récupère et les injecte,
    l'orchestrateur ne va jamais les chercher lui-même (voir la revue
    d'architecture, Candidat 1, et l'ADR 0001).
    """

    BLOCKED_RESPONSE = (
        "Je ne peux pas traiter cette demande. Pouvez-vous reformuler votre question ?"
    )
    ERROR_RESPONSE = (
        "Je suis désolé, je rencontre un problème technique. "
        "Pouvez-vous reformuler votre question ?"
    )
    ERROR_SUGGESTIONS = ["Réessayer", "Contacter le support"]
    CONFIDENCE = 0.85  # Constant tant qu'aucun signal de confiance réel n'existe.

    def __init__(
        self,
        guardrails_orchestrator: GuardrailsOrchestrator = default_guardrails,
        rule_evaluator: Optional[RuleEvaluator] = None,
        intent_classifier: Optional[IntentClassifier] = None,
        metrics=None,
    ):
        self._guardrails = guardrails_orchestrator
        self._rule_evaluator = rule_evaluator or RuleEvaluator()
        self._intent_classifier = intent_classifier or IntentClassifier()
        self._metrics = metrics or get_metrics_collector()

    async def process_turn(
        self,
        tenant_id: str,
        message: str,
        rules: List[RuleLike],
        llm_provider: BaseLLMProvider,
        retrieval_service: Optional[ProductRetrievalService] = None,
        use_rag: bool = True,
        top_k: int = 5,
    ) -> ChatTurnResult:
        metrics = self._metrics

        # =====================================================================
        # GARDE-FOUS D'ENTRÉE - avant tout traitement (règles, RAG, LLM)
        # =====================================================================
        input_report = await self._guardrails.check_input(message, context={"tenant_id": tenant_id})

        if not input_report.passed:
            for check in input_report.checks:
                if check.result == GuardrailResult.BLOCK:
                    metrics.record_guardrail_trigger(tenant_id, check.category.value, "blocked")
                    if check.category.value == "injection":
                        metrics.record_prompt_injection_attempt(tenant_id, "high", blocked=True)

            logger.warning(
                "Chat message blocked by input guardrails",
                extra={"tenant_id": tenant_id, "block_reason": input_report.get_block_reason()},
            )

            return ChatTurnResult(
                outcome=TurnOutcome.BLOCKED,
                response=self.BLOCKED_RESPONSE,
                intent="blocked",
                confidence=1.0,
                block_reason=input_report.get_block_reason(),
            )

        # =====================================================================
        # RÈGLES TENANT - évaluées avant le RAG/LLM pour pouvoir court-
        # circuiter la génération (canned_response).
        # =====================================================================
        intent = self._intent_classifier.classify(message)
        rule_match = self._rule_evaluator.evaluate(rules, intent, message)

        if rule_match and rule_match.action.get("type") == "canned_response":
            response_text = rule_match.action.get("text") or rule_match.action.get("message") or ""

            return ChatTurnResult(
                outcome=TurnOutcome.CANNED_RESPONSE,
                response=response_text,
                intent=intent,
                confidence=1.0,
                suggestions=_generate_suggestions(intent, has_products=False),
                rule_match=rule_match,
            )

        rule_instruction: Optional[str] = None
        if rule_match and rule_match.action.get("type") == "inject_instruction":
            rule_instruction = rule_match.action.get("instruction")

        products_context = ""
        retrieved_products: List[Dict[str, Any]] = []
        rag_search_time_ms = 0.0

        try:
            # =================================================================
            # ÉTAPE 1: RAG - Recherche de produits pertinents
            # =================================================================
            if use_rag and retrieval_service is not None:
                try:
                    with metrics.track_rag_query(tenant_id, "product"):
                        rag_result = await retrieval_service.search_products(
                            query=message,
                            tenant_id=tenant_id,
                            top_k=top_k,
                        )
                    metrics.record_rag_result(
                        tenant_id=tenant_id,
                        doc_type="product",
                        documents_found=len(rag_result.products),
                    )

                    rag_search_time_ms = rag_result.search_time_ms

                    if rag_result.has_results:
                        products_context = rag_result.to_context_string()
                        retrieved_products = [
                            {
                                "id": p.product_id,
                                "name": p.name,
                                "price": p.price,
                                "category": p.category,
                                "in_stock": p.in_stock,
                                "similarity": round(p.similarity_score, 3),
                            }
                            for p in rag_result.products
                        ]
                except Exception as e:
                    # Fallback gracieux - continuer sans RAG
                    logger.warning(
                        "RAG search failed, continuing without product context",
                        extra={"tenant_id": tenant_id, "error": str(e)},
                    )

            # =================================================================
            # ÉTAPE 2: Construire le contexte système avec produits
            # =================================================================
            system_context = _build_system_context(
                tenant_id=tenant_id,
                products_context=products_context,
                extra_instruction=rule_instruction,
            )

            # =================================================================
            # ÉTAPE 3: Générer la réponse
            # =================================================================
            with metrics.track_llm_request(tenant_id, llm_provider.get_model_name(), "chat"):
                response_text = await llm_provider.chat(message=message, context=system_context)

            input_tokens = llm_provider.count_tokens(message)
            output_tokens = llm_provider.count_tokens(response_text)
            estimated_cost = (
                input_tokens / 1000 * settings.llm.cost_per_1k_input_tokens
                + output_tokens / 1000 * settings.llm.cost_per_1k_output_tokens
            )
            metrics.record_llm_tokens(
                tenant_id=tenant_id,
                model=llm_provider.get_model_name(),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost=estimated_cost,
            )

            # =================================================================
            # GARDE-FOUS DE SORTIE - PII masking, XSS, hallucination/confidence
            # =================================================================
            output_context = {
                "retrieved_documents": [{"content": products_context}] if products_context else []
            }
            output_report = await self._guardrails.check_output(response_text, output_context)

            if output_report.sanitized_content:
                response_text = output_report.sanitized_content

            hallucination_flagged = False
            for check in output_report.checks:
                if check.result in (GuardrailResult.WARN, GuardrailResult.BLOCK):
                    metrics.record_guardrail_trigger(
                        tenant_id,
                        check.category.value,
                        "warned" if check.result == GuardrailResult.WARN else "blocked",
                    )
                    if check.category == GuardrailCategory.HALLUCINATION:
                        hallucination_flagged = True

            # =================================================================
            # ÉTAPE 4: Suggestions contextuelles
            # =================================================================
            suggestions = _generate_suggestions(intent, has_products=len(retrieved_products) > 0)

            confidence = self.CONFIDENCE
            metrics.record_response_confidence(tenant_id, confidence)

            # =================================================================
            # ÉTAPE 5: Décision de coupon éventuelle (generate_coupon) -
            # l'exécution (écriture DB) revient à l'appelant.
            # =================================================================
            coupon_decision: Optional[CouponDecision] = None
            if rule_match and rule_match.action.get("type") == "generate_coupon":
                coupon_decision = CouponDecision(
                    rule_id=rule_match.rule.id, action=rule_match.action
                )

            return ChatTurnResult(
                outcome=TurnOutcome.NORMAL,
                response=response_text,
                intent=intent,
                confidence=confidence,
                suggestions=suggestions,
                products=retrieved_products,
                rag_search_time_ms=rag_search_time_ms,
                model_name=llm_provider.get_model_name(),
                rule_match=rule_match,
                coupon_decision=coupon_decision,
                hallucination_flagged=hallucination_flagged,
                tokens_input=input_tokens,
                tokens_output=output_tokens,
            )

        except Exception as e:
            logger.exception(
                "Error processing chat message", extra={"tenant_id": tenant_id, "error": str(e)}
            )

            return ChatTurnResult(
                outcome=TurnOutcome.ERROR,
                response=self.ERROR_RESPONSE,
                intent="error",
                confidence=0.0,
                suggestions=list(self.ERROR_SUGGESTIONS),
            )
