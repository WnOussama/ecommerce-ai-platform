"""
Chat Endpoints - Client AI Interactions
"""

import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.infrastructure.llm import get_llm_provider

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

    class Config:
        json_schema_extra = {
            "example": {
                "message": "Je cherche un téléphone pas cher",
                "conversation_id": "conv_123",
                "customer_id": "cust_456",
                "context": {
                    "current_page": "/category/smartphones",
                    "cart_items": []
                }
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
async def send_message(
    request: Request,
    body: ChatMessageRequest
):
    """
    Send a message to the AI assistant and get a response.

    This endpoint handles:
    - Intent classification
    - Context retrieval (RAG)
    - Response generation
    - Action extraction
    """
    start_time = time.time()
    tenant_id = getattr(request.state, 'tenant_id', None)

    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    logger.info(
        "Processing chat message",
        extra={
            "tenant_id": tenant_id,
            "conversation_id": body.conversation_id,
            "message_length": len(body.message)
        }
    )

    # Générer IDs
    conversation_id = body.conversation_id or f"conv_{uuid.uuid4().hex[:12]}"
    message_id = f"msg_{uuid.uuid4().hex[:12]}"

    try:
        # Obtenir le LLM provider (mock ou real)
        llm = get_llm_provider()

        # Construire le contexte système
        system_context = f"""Tu es un assistant IA pour une boutique e-commerce.
Tenant: {tenant_id}
Sois concis, utile et professionnel. Utilise le vouvoiement."""

        # Générer la réponse
        response_text = await llm.chat(
            message=body.message,
            context=system_context
        )

        # Classifier l'intention (basique)
        intent = _classify_intent(body.message)

        # Générer suggestions contextuelles
        suggestions = _generate_suggestions(intent)

        processing_time_ms = int((time.time() - start_time) * 1000)

        logger.info(
            "Chat message processed successfully",
            extra={
                "tenant_id": tenant_id,
                "message_id": message_id,
                "intent": intent,
                "processing_time_ms": processing_time_ms,
                "llm_provider": llm.get_model_name()
            }
        )

        return ChatMessageResponse(
            conversation_id=conversation_id,
            message_id=message_id,
            response=response_text,
            intent=intent,
            confidence=0.85,
            actions=[],
            suggestions=suggestions,
            metadata={
                "processing_time_ms": processing_time_ms,
                "model": llm.get_model_name()
            }
        )

    except Exception as e:
        logger.exception(
            "Error processing chat message",
            extra={
                "tenant_id": tenant_id,
                "error": str(e)
            }
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
            metadata={
                "processing_time_ms": int((time.time() - start_time) * 1000),
                "error": True
            }
        )


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


def _generate_suggestions(intent: str) -> List[str]:
    """Génère des suggestions basées sur l'intention."""
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

    return suggestions_map.get(intent, ["Aide", "Catalogue"])


@router.get("/history/{conversation_id}", response_model=ConversationHistory)
async def get_conversation_history(
    request: Request,
    conversation_id: str,
    limit: int = 50
):
    """
    Get the history of a conversation.
    """
    tenant_id = getattr(request.state, 'tenant_id', None)

    # TODO: Fetch from database
    return ConversationHistory(
        conversation_id=conversation_id,
        messages=[],
        started_at=datetime.utcnow(),
        last_message_at=datetime.utcnow(),
        status="active"
    )


@router.post("/feedback")
async def submit_feedback(
    request: Request,
    body: FeedbackRequest
):
    """
    Submit feedback on an AI response.
    Used for improving the model and tracking satisfaction.
    """
    tenant_id = getattr(request.state, 'tenant_id', None)

    logger.info(
        "Feedback received",
        extra={
            "tenant_id": tenant_id,
            "message_id": body.message_id,
            "rating": body.rating
        }
    )

    # TODO: Store feedback
    return {"status": "received", "message_id": body.message_id}


@router.delete("/conversation/{conversation_id}")
async def end_conversation(
    request: Request,
    conversation_id: str
):
    """
    End/close a conversation.
    """
    tenant_id = getattr(request.state, 'tenant_id', None)

    # TODO: Update conversation status
    return {"status": "closed", "conversation_id": conversation_id}

