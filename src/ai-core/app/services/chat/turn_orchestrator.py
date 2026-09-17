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
    "order_status": ["Track my order", "Contact support"],
    "product_search": ["View promotions", "Filter by category"],
    "price_inquiry": ["Compare prices", "View offers"],
    "shipping_info": ["Delivery options", "Shipping costs"],
    "return_request": ["Return policy", "Return form"],
    "coupon_request": ["Current offers", "Loyalty program"],
    "recommendation": ["Best sellers", "New arrivals"],
    "greeting": ["Browse products", "My orders"],
    DEFAULT_INTENT: ["Browse catalog", "Help"],
}


def _generate_suggestions(intent: str, has_products: bool = False) -> List[str]:
    base_suggestions = _SUGGESTIONS_BY_INTENT.get(intent, ["Help", "Catalog"])

    if has_products:
        base_suggestions = ["View details", "Add to cart"] + base_suggestions[:2]

    return base_suggestions[:4]


def _build_system_context(
    tenant_id: str,
    products_context: str = "",
    extra_instruction: Optional[str] = None,
    faq_context: str = "",
) -> str:
    # tenant_id (un UUID interne) a longtemps été injecté tel quel dans le
    # prompt ("Tenant: {tenant_id}") sans aucune utilité pour le LLM - pur
    # bruit qui pouvait même ressortir dans la réponse. Retiré ; rien dans
    # ce prompt ne dépend plus de tenant_id (gardé au paramètre pour ne pas
    # changer la signature côté appelant).
    #
    # English, not French: this platform's storefront (PrestaShop's default
    # language and all product/category content created for it) is English
    # - a French-speaking bot on an English shop was itself part of the
    # "feels irrelevant" complaint that prompted this rewrite.
    base_context = """You are the customer service assistant for this online store, \
available in the site's chat widget. Reply the way a real human agent would: \
naturally, in short sentences, no jargon, no robotic tone, and without \
introducing yourself in every message. Address the customer politely.

The conversation history (if any) is given to you before the latest \
message: use it, don't ask again for information you were already given, \
and stay consistent with what you said earlier in this exchange.

Never mention a specific product name, price, shipping cost or URL unless
it comes explicitly from the product context provided below. Likewise for
store policy questions (shipping, returns, payment, terms...): if no FAQ
context is provided below, or none of it actually answers the question,
say honestly that you don't have that information and invite the customer
to check the site or contact support - never invent a plausible-sounding
policy (a specific delivery window, a return deadline, a named condition)
that isn't grounded in the context given to you.
"""

    if extra_instruction:
        base_context = f"{base_context}\n{extra_instruction}\n"

    if faq_context:
        base_context = f"""{base_context}
{faq_context}

Use this real store policy information (generated from the shop's own policy
pages) to answer policy questions. If it doesn't cover what was asked, say so
honestly instead of guessing.
"""

    if products_context:
        return f"""{base_context}
{products_context}

Use this product information to answer the customer's question.
If the products aren't relevant to the question, answer normally without mentioning them.
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

    BLOCKED_RESPONSE = "I can't process this request. Could you rephrase your question?"
    ERROR_RESPONSE = "I'm sorry, I'm having a technical issue. Could you rephrase your question?"
    ERROR_SUGGESTIONS = ["Try again", "Contact support"]
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
        history: Optional[List[Dict[str, str]]] = None,
        faq_context: str = "",
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
        elif rule_match and rule_match.action.get("type") == "generate_coupon":
            # Le code réel n'existe pas encore ici (généré/persisté par
            # l'appelant après ce tour, voir CouponDecision plus bas et
            # chat.py::_generate_coupon_from_rule) - sans cette instruction,
            # le LLM ne sait pas qu'un coupon va être accordé et répond
            # souvent "je n'ai pas d'information sur les codes promo", texte
            # auquel le vrai code était ensuite mécaniquement accolé
            # (incohérent - bug confirmé en test navigateur). On lui dit
            # d'annoncer la réduction sans jamais inventer le code lui-même.
            discount_percent = rule_match.action.get("discount_percent")
            rule_instruction = (
                "You are about to grant the customer a discount "
                f"({discount_percent}% if given). Reply with a warm sentence "
                "announcing this discount; the exact code will be appended "
                "automatically to the end of your reply by the system, so "
                "never say you don't have a code available and never make "
                "up a code yourself."
            )

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
                faq_context=faq_context,
            )

            # =================================================================
            # ÉTAPE 3: Générer la réponse
            # =================================================================
            with metrics.track_llm_request(tenant_id, llm_provider.get_model_name(), "chat"):
                response_text = await llm_provider.chat(
                    message=message, context=system_context, history=history
                )

            history_text = " ".join(turn.get("content", "") for turn in (history or []))
            input_tokens = llm_provider.count_tokens(f"{history_text} {message}")
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
