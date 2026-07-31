"""
Chat Endpoints - Client AI Interactions with RAG Support
"""

import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.config.settings import settings
from app.core.monitoring import get_metrics_collector
from app.core.security.guardrails import GuardrailResult, guardrails
from app.infrastructure.llm import get_llm_provider
from app.services.rag.factory import get_retrieval_service

logger = logging.getLogger(__name__)

router = APIRouter()


# =============================================================================
# SCHEMAS
# =============================================================================


class ChatMessageRequest(BaseModel):
    """Request schema for sending a chat message"""

    message: str = Field(..., min_length=1, max_length=4096)
    conversation_id: Optional[str] = None
    customer_id: Optional[str] = None
    context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    use_rag: bool = Field(default=True, description="Enable RAG product search")
    top_k: int = Field(default=5, ge=1, le=20, description="Number of products to retrieve")

    class Config:
        json_schema_extra = {
            "example": {
                "message": "Je cherche un téléphone pas cher",
                "conversation_id": "conv_123",
                "customer_id": "cust_456",
                "context": {"current_page": "/category/smartphones", "cart_items": []},
                "use_rag": True,
                "top_k": 5,
            }
        }


class ChatAction(BaseModel):
    """An action suggested by the AI"""

    type: str  # "show_products", "generate_coupon", "redirect", etc.
    data: Dict[str, Any]


class ChatMessageResponse(BaseModel):
    """Response schema for chat message"""

    conversation_id: str
    message_id: str
    response: str
    intent: str
    confidence: float
    actions: List[ChatAction] = []
    suggestions: List[str] = []
    products: List[Dict[str, Any]] = Field(
        default_factory=list, description="Related products from RAG"
    )
    metadata: Dict[str, Any] = {}


class ConversationHistory(BaseModel):
    """Conversation history response"""

    conversation_id: str
    messages: List[Dict[str, Any]]
    started_at: datetime
    last_message_at: datetime
    status: str


class FeedbackRequest(BaseModel):
    """Feedback on AI response"""

    message_id: str
    rating: int = Field(..., ge=1, le=5)
    feedback_text: Optional[str] = None


# =============================================================================
# ENDPOINTS
# =============================================================================


@router.post("/message", response_model=ChatMessageResponse)
async def send_message(request: Request, body: ChatMessageRequest):
    """
    Send a message to the AI assistant and get a response.

    This endpoint handles:
    - Intent classification
    - Context retrieval (RAG) - searches relevant products
    - Response generation with product context
    - Action extraction
    """
    start_time = time.time()
    tenant_id = getattr(request.state, "tenant_id", None)

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    logger.info(
        "Processing chat message",
        extra={
            "tenant_id": tenant_id,
            "conversation_id": body.conversation_id,
            "message_length": len(body.message),
            "use_rag": body.use_rag,
        },
    )

    # Générer IDs
    conversation_id = body.conversation_id or f"conv_{uuid.uuid4().hex[:12]}"
    message_id = f"msg_{uuid.uuid4().hex[:12]}"

    metrics = get_metrics_collector()

    # =========================================================================
    # GUARDRAILS D'ENTRÉE - avant tout traitement (RAG, LLM)
    # =========================================================================
    input_report = await guardrails.check_input(body.message, context={"tenant_id": tenant_id})

    if not input_report.passed:
        for check in input_report.checks:
            if check.result == GuardrailResult.BLOCK:
                metrics.record_guardrail_trigger(tenant_id, check.category.value, "blocked")
                if check.category.value == "injection":
                    metrics.record_prompt_injection_attempt(tenant_id, "high", blocked=True)

        logger.warning(
            "Chat message blocked by input guardrails",
            extra={
                "tenant_id": tenant_id,
                "message_id": message_id,
                "block_reason": input_report.get_block_reason(),
            },
        )

        return ChatMessageResponse(
            conversation_id=conversation_id,
            message_id=message_id,
            response="Je ne peux pas traiter cette demande. Pouvez-vous reformuler votre question ?",
            intent="blocked",
            confidence=1.0,
            actions=[],
            suggestions=[],
            products=[],
            metadata={
                "guardrail_blocked": True,
                "processing_time_ms": int((time.time() - start_time) * 1000),
            },
        )

    # Variables pour le RAG
    products_context = ""
    retrieved_products = []
    rag_search_time_ms = 0

    try:
        # =====================================================================
        # ÉTAPE 1: RAG - Recherche de produits pertinents
        # =====================================================================
        if body.use_rag:
            try:
                retrieval_service = get_retrieval_service()
                with metrics.track_rag_query(tenant_id, "product"):
                    rag_result = await retrieval_service.search_products(
                        query=body.message,
                        tenant_id=tenant_id,
                        top_k=body.top_k,
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

                    logger.debug(
                        "RAG found relevant products",
                        extra={
                            "tenant_id": tenant_id,
                            "products_found": len(retrieved_products),
                            "search_time_ms": rag_search_time_ms,
                        },
                    )
                else:
                    logger.debug("RAG found no relevant products", extra={"tenant_id": tenant_id})

            except Exception as e:
                # Fallback gracieux - continuer sans RAG
                logger.warning(
                    "RAG search failed, continuing without product context",
                    extra={"tenant_id": tenant_id, "error": str(e)},
                )

        # =====================================================================
        # ÉTAPE 2: Obtenir le LLM provider
        # =====================================================================
        llm = get_llm_provider()

        # =====================================================================
        # ÉTAPE 3: Construire le contexte système avec produits
        # =====================================================================
        system_context = _build_system_context(
            tenant_id=tenant_id,
            products_context=products_context,
        )

        # =====================================================================
        # ÉTAPE 4: Générer la réponse
        # =====================================================================
        with metrics.track_llm_request(tenant_id, llm.get_model_name(), "chat"):
            response_text = await llm.chat(message=body.message, context=system_context)

        input_tokens = llm.count_tokens(body.message)
        output_tokens = llm.count_tokens(response_text)
        estimated_cost = (
            input_tokens / 1000 * settings.llm.cost_per_1k_input_tokens
            + output_tokens / 1000 * settings.llm.cost_per_1k_output_tokens
        )
        metrics.record_llm_tokens(
            tenant_id=tenant_id,
            model=llm.get_model_name(),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=estimated_cost,
        )

        # =====================================================================
        # GUARDRAILS DE SORTIE - PII masking, XSS, hallucination/confidence
        # =====================================================================
        output_context = {
            "retrieved_documents": [{"content": products_context}] if products_context else []
        }
        output_report = await guardrails.check_output(response_text, output_context)

        if output_report.sanitized_content:
            response_text = output_report.sanitized_content

        for check in output_report.checks:
            if check.result in (GuardrailResult.WARN, GuardrailResult.BLOCK):
                metrics.record_guardrail_trigger(
                    tenant_id,
                    check.category.value,
                    "warned" if check.result == GuardrailResult.WARN else "blocked",
                )

        # =====================================================================
        # ÉTAPE 5: Classifier l'intention
        # =====================================================================
        intent = _classify_intent(body.message)

        # =====================================================================
        # ÉTAPE 6: Générer suggestions contextuelles
        # =====================================================================
        suggestions = _generate_suggestions(intent, has_products=len(retrieved_products) > 0)

        confidence = 0.85
        metrics.record_response_confidence(tenant_id, confidence)

        processing_time_ms = int((time.time() - start_time) * 1000)

        logger.info(
            "Chat message processed successfully",
            extra={
                "tenant_id": tenant_id,
                "message_id": message_id,
                "intent": intent,
                "processing_time_ms": processing_time_ms,
                "rag_search_time_ms": rag_search_time_ms,
                "products_found": len(retrieved_products),
                "llm_provider": llm.get_model_name(),
            },
        )

        return ChatMessageResponse(
            conversation_id=conversation_id,
            message_id=message_id,
            response=response_text,
            intent=intent,
            confidence=confidence,
            actions=[],
            suggestions=suggestions,
            products=retrieved_products,
            metadata={
                "processing_time_ms": processing_time_ms,
                "rag_search_time_ms": rag_search_time_ms,
                "model": llm.get_model_name(),
                "rag_enabled": body.use_rag,
            },
        )

    except Exception as e:
        logger.exception(
            "Error processing chat message", extra={"tenant_id": tenant_id, "error": str(e)}
        )

        # Réponse de fallback en cas d'erreur
        return ChatMessageResponse(
            conversation_id=conversation_id,
            message_id=message_id,
            response="Je suis désolé, je rencontre un problème technique. Pouvez-vous reformuler votre question ?",
            intent="error",
            confidence=0.0,
            actions=[],
            suggestions=["Réessayer", "Contacter le support"],
            products=[],
            metadata={"processing_time_ms": int((time.time() - start_time) * 1000), "error": True},
        )


def _build_system_context(tenant_id: str, products_context: str = "") -> str:
    """
    Construit le contexte système pour le LLM.

    Args:
        tenant_id: ID du tenant
        products_context: Contexte produits formaté (peut être vide)

    Returns:
        Prompt système complet
    """
    base_context = f"""Tu es un assistant IA pour une boutique e-commerce.
Tenant: {tenant_id}
Sois concis, utile et professionnel. Utilise le vouvoiement.
"""

    if products_context:
        return f"""{base_context}
{products_context}

Utilise ces informations produits pour répondre à la question de l'utilisateur.
Si les produits ne sont pas pertinents pour la question, réponds normalement sans les mentionner.
"""

    return base_context


def _classify_intent(message: str) -> str:
    """Classification d'intention basique par règles."""
    message_lower = message.lower()

    intent_patterns = {
        "order_status": ["commande", "order", "suivi", "tracking", "colis"],
        "product_search": ["cherche", "recherche", "produit", "article", "trouver"],
        "price_inquiry": ["prix", "price", "coût", "tarif", "combien"],
        "shipping_info": ["livraison", "shipping", "délai", "expédition"],
        "return_request": ["retour", "rembours", "échange", "renvoyer"],
        "coupon_request": ["promo", "code", "réduction", "coupon", "remise"],
        "recommendation": ["recommand", "suggé", "conseil", "similaire"],
        "greeting": ["bonjour", "hello", "salut", "bonsoir"],
    }

    for intent, keywords in intent_patterns.items():
        if any(kw in message_lower for kw in keywords):
            return intent

    return "general"


def _generate_suggestions(intent: str, has_products: bool = False) -> List[str]:
    """Génère des suggestions basées sur l'intention et les produits trouvés."""
    suggestions_map = {
        "order_status": ["Suivre ma commande", "Contacter le support"],
        "product_search": ["Voir les promotions", "Filtrer par catégorie"],
        "price_inquiry": ["Comparer les prix", "Voir les offres"],
        "shipping_info": ["Options de livraison", "Frais de port"],
        "return_request": ["Politique de retour", "Formulaire de retour"],
        "coupon_request": ["Offres en cours", "Programme fidélité"],
        "recommendation": ["Meilleures ventes", "Nouveautés"],
        "greeting": ["Voir les produits", "Mes commandes"],
        "general": ["Parcourir le catalogue", "Aide"],
    }

    base_suggestions = suggestions_map.get(intent, ["Aide", "Catalogue"])

    # Ajouter des suggestions si des produits ont été trouvés
    if has_products:
        base_suggestions = ["Voir les détails", "Ajouter au panier"] + base_suggestions[:2]

    return base_suggestions[:4]  # Limiter à 4 suggestions


@router.get("/history/{conversation_id}", response_model=ConversationHistory)
async def get_conversation_history(request: Request, conversation_id: str, limit: int = 50):
    """
    Get the history of a conversation.
    """
    getattr(request.state, "tenant_id", None)

    # TODO: Fetch from database
    return ConversationHistory(
        conversation_id=conversation_id,
        messages=[],
        started_at=datetime.utcnow(),
        last_message_at=datetime.utcnow(),
        status="active",
    )


@router.post("/feedback")
async def submit_feedback(request: Request, body: FeedbackRequest):
    """
    Submit feedback on an AI response.
    Used for improving the model and tracking satisfaction.
    """
    tenant_id = getattr(request.state, "tenant_id", None)

    logger.info(
        "Feedback received",
        extra={"tenant_id": tenant_id, "message_id": body.message_id, "rating": body.rating},
    )

    # TODO: Store feedback
    return {"status": "received", "message_id": body.message_id}


@router.delete("/conversation/{conversation_id}")
async def end_conversation(request: Request, conversation_id: str):
    """
    End/close a conversation.
    """
    getattr(request.state, "tenant_id", None)

    # TODO: Update conversation status
    return {"status": "closed", "conversation_id": conversation_id}
