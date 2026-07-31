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
from app.infrastructure.database.models.conversation import ConversationStatus
from app.infrastructure.database.models.message import MessageRole
from app.infrastructure.database.unit_of_work import UnitOfWork
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

    # Résout/crée la conversation persistée et y enregistre le message
    # utilisateur (best-effort - voir _log_user_message).
    conversation_id = await _log_user_message(tenant_id, body)
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

        blocked_response = (
            "Je ne peux pas traiter cette demande. Pouvez-vous reformuler votre question ?"
        )
        await _log_assistant_message(
            tenant_id,
            conversation_id,
            blocked_response,
            extra_data={"guardrail_blocked": True, "block_reason": input_report.get_block_reason()},
        )

        return ChatMessageResponse(
            conversation_id=conversation_id,
            message_id=message_id,
            response=blocked_response,
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

        await _log_assistant_message(
            tenant_id,
            conversation_id,
            response_text,
            extra_data={"intent": intent, "products_found": len(retrieved_products)},
            latency_ms=processing_time_ms,
            tokens_input=input_tokens,
            tokens_output=output_tokens,
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
        error_response = (
            "Je suis désolé, je rencontre un problème technique. "
            "Pouvez-vous reformuler votre question ?"
        )
        await _log_assistant_message(
            tenant_id, conversation_id, error_response, extra_data={"error": True}
        )

        return ChatMessageResponse(
            conversation_id=conversation_id,
            message_id=message_id,
            response=error_response,
            intent="error",
            confidence=0.0,
            actions=[],
            suggestions=["Réessayer", "Contacter le support"],
            products=[],
            metadata={"processing_time_ms": int((time.time() - start_time) * 1000), "error": True},
        )


async def _log_user_message(tenant_id: str, body: ChatMessageRequest) -> str:
    """
    Résout/crée la conversation persistée et y enregistre le message
    utilisateur. Best-effort : si tenant_id n'est pas un vrai UUID (bypass
    dev avec un identifiant humain comme "demo-tenant") ou si la
    persistance échoue pour toute autre raison, on continue sans
    persister plutôt que de casser le chat - même principe de dégradation
    gracieuse que le fallback RAG plus haut dans ce fichier.

    Returns:
        L'UUID de la conversation persistée (str), ou un identifiant de
        secours si la persistance n'a pas pu avoir lieu.
    """
    fallback_id = body.conversation_id or f"conv_{uuid.uuid4().hex[:12]}"

    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except (ValueError, AttributeError, TypeError):
        return fallback_id

    try:
        async with UnitOfWork(tenant_uuid) as uow:
            conversation = None

            if body.conversation_id:
                try:
                    conversation = await uow.conversations.get_by_id(
                        uuid.UUID(body.conversation_id)
                    )
                except ValueError:
                    conversation = None

            if not conversation:
                user_identifier = body.customer_id or f"anon_{uuid.uuid4().hex[:12]}"
                conversation, _ = await uow.conversations.get_or_create(user_identifier)

            await uow.messages.create(
                conversation_id=conversation.id,
                idempotency_key=uuid.uuid4(),
                role=MessageRole.USER,
                content=body.message,
            )
            await uow.commit()

            return str(conversation.id)
    except Exception as e:
        logger.warning(
            "Skipping conversation persistence for this request",
            extra={"tenant_id": tenant_id, "error": str(e)},
        )
        return fallback_id


async def _log_assistant_message(
    tenant_id: str,
    conversation_id: str,
    content: str,
    extra_data: Optional[Dict[str, Any]] = None,
    latency_ms: Optional[int] = None,
    tokens_input: Optional[int] = None,
    tokens_output: Optional[int] = None,
) -> None:
    """Enregistre le message de l'assistant - best-effort, mêmes garde-fous que _log_user_message."""
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        conv_uuid = uuid.UUID(conversation_id)
    except (ValueError, AttributeError, TypeError):
        return

    try:
        async with UnitOfWork(tenant_uuid) as uow:
            await uow.messages.create(
                conversation_id=conv_uuid,
                idempotency_key=uuid.uuid4(),
                role=MessageRole.ASSISTANT,
                content=content,
                extra_data=extra_data or {},
                latency_ms=latency_ms,
                tokens_input=tokens_input,
                tokens_output=tokens_output,
            )
            await uow.commit()
    except Exception as e:
        logger.warning(
            "Skipping assistant message persistence for this request",
            extra={"tenant_id": tenant_id, "error": str(e)},
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
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    try:
        tenant_uuid = uuid.UUID(tenant_id)
        conv_uuid = uuid.UUID(conversation_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=404, detail="Conversation not found")

    async with UnitOfWork(tenant_uuid) as uow:
        conversation = await uow.conversations.get_by_id(conv_uuid)
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")

        db_messages = await uow.messages.get_by_conversation(conv_uuid, limit=limit)

    return ConversationHistory(
        conversation_id=conversation_id,
        messages=[
            {
                "id": str(m.id),
                "role": m.role.value.lower(),
                "content": m.content,
                "created_at": m.created_at.isoformat(),
            }
            for m in db_messages
        ],
        started_at=conversation.created_at,
        last_message_at=db_messages[-1].created_at if db_messages else conversation.created_at,
        status=conversation.status.value,
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

    try:
        tenant_uuid = uuid.UUID(tenant_id)
        message_uuid = uuid.UUID(body.message_id)
    except (ValueError, AttributeError, TypeError):
        # message_id du bypass dev ou d'une réponse dégradée (pas de vrai
        # UUID persisté) - rien à mettre à jour, mais on ne casse pas l'appel.
        return {"status": "received", "message_id": body.message_id}

    async with UnitOfWork(tenant_uuid) as uow:
        updated = await uow.messages.update_extra_data(
            message_uuid,
            {"rating": body.rating, "feedback_text": body.feedback_text},
        )
        if updated:
            await uow.commit()

    return {"status": "received", "message_id": body.message_id}


@router.delete("/conversation/{conversation_id}")
async def end_conversation(request: Request, conversation_id: str):
    """
    End/close a conversation.
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    try:
        tenant_uuid = uuid.UUID(tenant_id)
        conv_uuid = uuid.UUID(conversation_id)
    except (ValueError, AttributeError, TypeError):
        return {"status": "closed", "conversation_id": conversation_id}

    async with UnitOfWork(tenant_uuid) as uow:
        updated = await uow.conversations.update_status(conv_uuid, ConversationStatus.RESOLVED)
        if updated:
            await uow.commit()

    return {"status": "closed", "conversation_id": conversation_id}
