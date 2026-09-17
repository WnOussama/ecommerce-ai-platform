"""
Chat Endpoints - Client AI Interactions with RAG Support
"""

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.infrastructure.database.models.conversation import ConversationStatus
from app.infrastructure.database.models.message import Message, MessageRole
from app.infrastructure.database.unit_of_work import UnitOfWork
from app.infrastructure.llm import get_llm_provider
from app.services.chat import ChatTurnOrchestrator, TurnOutcome
from app.services.rag.factory import get_retrieval_service
from app.services.rules.evaluator import RuleLike

logger = logging.getLogger(__name__)

router = APIRouter()

# Décide la réponse à un message - voir app/services/chat/turn_orchestrator.py
# et docs/adr/0001-chat-turn-orchestrator-pure-decision-engine.md. Instance
# partagée : ses seuls collaborateurs internes (garde-fous, évaluateur de
# règles, classifieur d'intention, métriques) sont des singletons déjà
# partagés ailleurs dans l'app, pas d'état par requête ici.
_chat_turn_orchestrator = ChatTurnOrchestrator()


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
    idempotency_key: Optional[str] = Field(
        default=None,
        description=(
            "Client-supplied UUID identifying this logical request. Send the "
            "same value on a retry (timeout, dropped connection) to get back "
            "the cached response instead of a second LLM call and a second "
            "generate_coupon action. Omit for a normal, non-retried message."
        ),
    )

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


class ConversationSummary(BaseModel):
    """One row in the conversation browser's list."""

    conversation_id: str
    user_identifier: str
    status: str
    message_count: int
    last_message_at: datetime
    created_at: datetime


class ConversationListResponse(BaseModel):
    conversations: List[ConversationSummary]
    total: int
    limit: int
    offset: int


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

    Decides what to say via ChatTurnOrchestrator (intent, guardrails,
    rules, RAG, LLM - see app/services/chat/turn_orchestrator.py); this
    endpoint's own job is transport and persistence: resolve the
    conversation, hand the orchestrator its per-request collaborators,
    then execute whatever the resulting ChatTurnResult calls for (log
    messages, generate a coupon, record rule usage).
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
    # utilisateur, de façon idempotente si body.idempotency_key est fourni
    # (best-effort - voir _log_user_message).
    user_log = await _log_user_message(tenant_id, body)
    conversation_id = user_log.conversation_id
    message_id = f"msg_{uuid.uuid4().hex[:12]}"

    # La clé de la réponse assistant est dérivée de celle du message
    # utilisateur (uuid5, déterministe) plutôt que tirée au hasard : deux
    # exécutions concurrentes du même tour (même idempotency_key côté
    # client) convergent alors vers la même clé assistant, et
    # create_idempotent() garantit qu'une seule des deux écrit réellement -
    # pas seulement le message utilisateur. None si aucun message réel
    # n'a été persisté (cas de repli) - pas de déduplication possible.
    assistant_idempotency_key: Optional[uuid.UUID] = None
    if user_log.message is not None:
        assistant_idempotency_key = uuid.uuid5(
            user_log.message.idempotency_key, "assistant-response"
        )

        if not user_log.created:
            # Idempotency hit : ce message utilisateur existait déjà. Si une
            # réponse a déjà été générée pour lui, on la renvoie directement
            # - pas de second appel LLM, pas de second coupon généré. Sinon
            # (retry après un échec avant que la réponse n'ait pu être
            # persistée), le pipeline se déroule normalement plus bas.
            existing_response = await _find_existing_assistant_response(
                tenant_id, conversation_id, user_log.message.id
            )
            if existing_response is not None:
                logger.info(
                    "Idempotency hit - returning cached response, no LLM call",
                    extra={
                        "tenant_id": tenant_id,
                        "conversation_id": conversation_id,
                        "cached_message_id": str(existing_response.id),
                    },
                )
                cached_extra = existing_response.extra_data or {}
                return ChatMessageResponse(
                    conversation_id=conversation_id,
                    message_id=message_id,
                    response=existing_response.content,
                    intent=cached_extra.get("intent", "general"),
                    confidence=cached_extra.get("confidence", 0.0),
                    actions=[],
                    suggestions=[],
                    products=[],
                    metadata={
                        "processing_time_ms": int((time.time() - start_time) * 1000),
                        "cached": True,
                    },
                )

    rules = await _load_active_rules(tenant_id)
    llm = get_llm_provider()
    retrieval_service = get_retrieval_service() if body.use_rag else None
    history = await _load_recent_history(
        tenant_id, conversation_id, user_log.message.id if user_log.message else None
    )

    turn = await _chat_turn_orchestrator.process_turn(
        tenant_id=tenant_id,
        message=body.message,
        rules=rules,
        llm_provider=llm,
        retrieval_service=retrieval_service,
        use_rag=body.use_rag,
        top_k=body.top_k,
        history=history,
    )

    processing_time_ms = int((time.time() - start_time) * 1000)

    # =========================================================================
    # BLOQUÉ PAR LES GARDE-FOUS D'ENTRÉE
    # =========================================================================
    if turn.outcome == TurnOutcome.BLOCKED:
        logger.warning(
            "Chat message blocked by input guardrails",
            extra={
                "tenant_id": tenant_id,
                "message_id": message_id,
                "block_reason": turn.block_reason,
            },
        )
        await _log_assistant_message(
            tenant_id,
            conversation_id,
            turn.response,
            extra_data={"guardrail_blocked": True, "block_reason": turn.block_reason},
            idempotency_key=assistant_idempotency_key,
        )
        return ChatMessageResponse(
            conversation_id=conversation_id,
            message_id=message_id,
            response=turn.response,
            intent=turn.intent,
            confidence=turn.confidence,
            actions=[],
            suggestions=[],
            products=[],
            metadata={"guardrail_blocked": True, "processing_time_ms": processing_time_ms},
        )

    # =========================================================================
    # RÉPONSE TOUTE FAITE (règle canned_response) - court-circuite le LLM
    # =========================================================================
    if turn.outcome == TurnOutcome.CANNED_RESPONSE:
        await _record_rule_triggered(
            tenant_id, turn.rule_match.rule, conversation_id, "canned_response"
        )
        await _log_assistant_message(
            tenant_id,
            conversation_id,
            turn.response,
            extra_data={
                "intent": turn.intent,
                "rule_triggered": str(turn.rule_match.rule.id),
                "rule_name": turn.rule_match.rule.name,
            },
            latency_ms=processing_time_ms,
            idempotency_key=assistant_idempotency_key,
        )
        return ChatMessageResponse(
            conversation_id=conversation_id,
            message_id=message_id,
            response=turn.response,
            intent=turn.intent,
            confidence=turn.confidence,
            actions=[],
            suggestions=turn.suggestions,
            products=[],
            metadata={
                "processing_time_ms": processing_time_ms,
                "rule_triggered": True,
                "rule_id": str(turn.rule_match.rule.id),
            },
        )

    # =========================================================================
    # ERREUR - fallback gracieux (RAG a déjà son propre fallback interne ;
    # ceci couvre l'échec du LLM ou des garde-fous de sortie)
    # =========================================================================
    if turn.outcome == TurnOutcome.ERROR:
        await _log_assistant_message(
            tenant_id,
            conversation_id,
            turn.response,
            extra_data={"error": True},
            idempotency_key=assistant_idempotency_key,
        )
        return ChatMessageResponse(
            conversation_id=conversation_id,
            message_id=message_id,
            response=turn.response,
            intent=turn.intent,
            confidence=turn.confidence,
            actions=[],
            suggestions=turn.suggestions,
            products=[],
            metadata={"processing_time_ms": processing_time_ms, "error": True},
        )

    # =========================================================================
    # NORMAL - exécute ce que l'orchestrateur a décidé : coupon éventuel,
    # usage de règle, persistance de la réponse.
    # =========================================================================
    actions: List[ChatAction] = []
    response_text = turn.response
    if turn.coupon_decision:
        coupon_data = await _generate_coupon_from_rule(
            tenant_id,
            turn.coupon_decision.rule_id,
            turn.coupon_decision.action,
            body.customer_id,
            conversation_id,
        )
        if coupon_data:
            actions.append(ChatAction(type="generate_coupon", data=coupon_data))
            # Real, confirmed live bug: the orchestrator generates response_text
            # BEFORE this coupon exists (turn_orchestrator.py's ChatTurnResult is
            # a pure decision, this endpoint does the actual DB write after) -
            # the LLM has no way to know the code, so it never appeared anywhere
            # the shopper could see it, not in the reply text nor in the
            # persisted conversation log, even though a real coupon WAS created
            # every time. Appending it here, in the same style as coupons.py's
            # own _STRATEGY_MESSAGES templates.
            response_text = (
                f"{response_text}\n\nYour code: {coupon_data['code']} "
                f"(-{coupon_data['discount_percent']}%, valid until "
                f"{coupon_data['expires_at'][:10]})."
            )

    if turn.rule_match:
        await _record_rule_triggered(
            tenant_id,
            turn.rule_match.rule,
            conversation_id,
            turn.rule_match.action.get("type", "unknown"),
        )

    logger.info(
        "Chat message processed successfully",
        extra={
            "tenant_id": tenant_id,
            "message_id": message_id,
            "intent": turn.intent,
            "processing_time_ms": processing_time_ms,
            "rag_search_time_ms": turn.rag_search_time_ms,
            "products_found": len(turn.products),
            "llm_provider": turn.model_name,
        },
    )

    await _log_assistant_message(
        tenant_id,
        conversation_id,
        response_text,
        extra_data={
            "intent": turn.intent,
            "rag_used": body.use_rag,
            "products_found": len(turn.products),
            "product_ids": [p["id"] for p in turn.products],
            "confidence": turn.confidence,
            "hallucination_flagged": turn.hallucination_flagged,
            **(
                {
                    "rule_triggered": str(turn.rule_match.rule.id),
                    "rule_action": turn.rule_match.action.get("type"),
                }
                if turn.rule_match
                else {}
            ),
        },
        latency_ms=processing_time_ms,
        tokens_input=turn.tokens_input,
        tokens_output=turn.tokens_output,
        idempotency_key=assistant_idempotency_key,
    )

    return ChatMessageResponse(
        conversation_id=conversation_id,
        message_id=message_id,
        response=response_text,
        intent=turn.intent,
        confidence=turn.confidence,
        actions=actions,
        suggestions=turn.suggestions,
        products=turn.products,
        metadata={
            "processing_time_ms": processing_time_ms,
            "rag_search_time_ms": turn.rag_search_time_ms,
            "model": turn.model_name,
            "rag_enabled": body.use_rag,
        },
    )


@dataclass
class _UserMessageLog:
    """Résultat de _log_user_message - ce qu'il faut pour décider, dans
    send_message, si un tour peut être servi depuis le cache
    (idempotency) avant d'appeler l'orchestrateur."""

    conversation_id: str
    message: Optional[Message] = None  # None dans les cas de repli (voir docstring)
    created: bool = True  # False = idempotency hit, message existant retourné


async def _log_user_message(tenant_id: str, body: ChatMessageRequest) -> _UserMessageLog:
    """
    Résout/crée la conversation persistée et y enregistre le message
    utilisateur - de façon idempotente (INSERT ... ON CONFLICT DO NOTHING,
    voir MessageRepository.create_idempotent) si le client a fourni
    body.idempotency_key ; sinon une clé aléatoire est générée à chaque
    appel, comme avant, et created est toujours True (rien à dédupliquer).

    Best-effort : si tenant_id n'est pas un vrai UUID (bypass dev avec un
    identifiant humain comme "demo-tenant") ou si la persistance échoue
    pour toute autre raison, on continue sans persister plutôt que de
    casser le chat - même principe de dégradation gracieuse que le
    fallback RAG plus haut dans ce fichier. Dans ces cas, message=None et
    created=True : pas de message réel à dédupliquer, le tour se déroule
    normalement.
    """
    fallback_id = body.conversation_id or f"conv_{uuid.uuid4().hex[:12]}"

    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except (ValueError, AttributeError, TypeError):
        return _UserMessageLog(conversation_id=fallback_id)

    try:
        idempotency_key = (
            uuid.UUID(body.idempotency_key) if body.idempotency_key else uuid.uuid4()
        )
    except ValueError:
        idempotency_key = uuid.uuid4()

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

            message, created = await uow.messages.create_idempotent(
                conversation_id=conversation.id,
                idempotency_key=idempotency_key,
                role=MessageRole.USER,
                content=body.message,
            )
            await uow.commit()

            # message.conversation_id, pas conversation.id : sur un conflit
            # d'idempotency_key (created=False), create_idempotent renvoie le
            # message déjà existant, qui peut appartenir à une conversation
            # différente de celle - potentiellement neuve - résolue ci-dessus
            # (pas de body.conversation_id/customer_id => un user_identifier
            # anonyme différent est généré à chaque appel). Utiliser
            # conversation.id ici renverrait un conversation_id où le message
            # dédupliqué n'existe pas, et casserait le court-circuit
            # idempotency dans send_message (recherche dans la mauvaise
            # conversation, toujours vide).
            return _UserMessageLog(
                conversation_id=str(message.conversation_id), message=message, created=created
            )
    except Exception as e:
        logger.warning(
            "Skipping conversation persistence for this request",
            extra={"tenant_id": tenant_id, "error": str(e)},
        )
        return _UserMessageLog(conversation_id=fallback_id)


async def _find_existing_assistant_response(
    tenant_id: str, conversation_id: str, after_message_id: uuid.UUID
) -> Optional[Message]:
    """
    Cherche, pour le court-circuit idempotency de send_message, une
    réponse assistant déjà générée à la suite du message utilisateur
    after_message_id. Fenêtre bornée (les deux messages d'un tour sont
    toujours consécutifs) plutôt qu'une requête dédiée - même dégradation
    best-effort que le reste de ce fichier : DB indisponible => on ne
    trouve rien, le tour se déroule normalement (pas de cache, pas de
    crash).
    """
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        conv_uuid = uuid.UUID(conversation_id)
    except (ValueError, AttributeError, TypeError):
        return None

    try:
        async with UnitOfWork(tenant_uuid) as uow:
            messages = await uow.messages.get_by_conversation(conv_uuid, limit=10, order_asc=True)
    except Exception as e:
        logger.warning(
            "Skipping idempotency response lookup for this request",
            extra={"tenant_id": tenant_id, "error": str(e)},
        )
        return None

    found_user_message = False
    for message in messages:
        if message.id == after_message_id:
            found_user_message = True
            continue
        if found_user_message and message.role == MessageRole.ASSISTANT:
            return message

    return None


async def _log_assistant_message(
    tenant_id: str,
    conversation_id: str,
    content: str,
    extra_data: Optional[Dict[str, Any]] = None,
    latency_ms: Optional[int] = None,
    tokens_input: Optional[int] = None,
    tokens_output: Optional[int] = None,
    idempotency_key: Optional[uuid.UUID] = None,
) -> None:
    """
    Enregistre le message de l'assistant - best-effort, mêmes garde-fous
    que _log_user_message. idempotency_key est dérivée par l'appelant
    (send_message) de celle du message utilisateur, pour que deux
    exécutions concurrentes du même tour (même idempotency_key client)
    convergent vers la même clé et que create_idempotent() n'en persiste
    qu'une seule ; None (cas de repli, pas de message utilisateur réel)
    retombe sur une clé aléatoire - pas de déduplication possible dans ce
    cas de toute façon.
    """
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        conv_uuid = uuid.UUID(conversation_id)
    except (ValueError, AttributeError, TypeError):
        return

    try:
        async with UnitOfWork(tenant_uuid) as uow:
            await uow.messages.create_idempotent(
                conversation_id=conv_uuid,
                idempotency_key=idempotency_key or uuid.uuid4(),
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


_MAX_HISTORY_MESSAGES = 20  # ~10 échanges - borne le coût/latence LLM


async def _load_recent_history(
    tenant_id: str, conversation_id: str, exclude_message_id: Optional[uuid.UUID]
) -> List[Dict[str, str]]:
    """
    Charge les derniers tours de la conversation pour les donner au LLM
    comme mémoire réelle du fil de discussion. Avant ce fix, chaque tour
    repartait de zéro (seul le message courant était envoyé au LLM, voir
    turn_orchestrator.py) alors même que la conversation entière était déjà
    persistée et affichée à l'utilisateur - le bot semblait donc amnésique
    et redemandait des informations déjà données. exclude_message_id est le
    message utilisateur qu'on vient de logger pour ce tour (déjà passé
    séparément comme `message` à l'orchestrateur) - pas de doublon. Best-
    effort - mêmes garde-fous que le reste de ce fichier.
    """
    try:
        tenant_uuid = uuid.UUID(tenant_id)
        conv_uuid = uuid.UUID(conversation_id)
    except (ValueError, AttributeError, TypeError):
        return []

    try:
        async with UnitOfWork(tenant_uuid) as uow:
            # order_asc=False (le plus récent d'abord) - avec order_asc=True
            # `limit` aurait gardé les N PREMIERS messages de la conversation
            # (les plus anciens) pour toujours, au lieu des N derniers, dès
            # qu'une conversation dépasse _MAX_HISTORY_MESSAGES messages.
            # Re-inversé ensuite pour redonner l'ordre chronologique attendu
            # par l'API chat des LLM.
            messages = await uow.messages.get_by_conversation(
                conv_uuid, limit=_MAX_HISTORY_MESSAGES + 1, order_asc=False
            )
    except Exception as e:
        logger.warning(
            "Skipping conversation history load for this request",
            extra={"tenant_id": tenant_id, "error": str(e)},
        )
        return []

    role_map = {MessageRole.USER: "user", MessageRole.ASSISTANT: "assistant"}
    history = [
        {"role": role_map[m.role], "content": m.content}
        for m in reversed(messages)
        if m.id != exclude_message_id and m.role in role_map
    ]
    return history[-_MAX_HISTORY_MESSAGES:]


async def _load_active_rules(tenant_id: str) -> List[RuleLike]:
    """
    Charge les règles actives du tenant - matching pur (RuleEvaluator)
    délégué à ChatTurnOrchestrator, cette fonction ne fait que le
    chargement DB. Best-effort - mêmes garde-fous que _log_user_message
    (pas de vrai tenant UUID, échec DB => pas de règles, le chat continue
    sans court-circuit possible plutôt que de casser).
    """
    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except (ValueError, AttributeError, TypeError):
        return []

    try:
        async with UnitOfWork(tenant_uuid) as uow:
            return await uow.rules.list_active()
    except Exception as e:
        logger.warning(
            "Skipping rule loading for this request",
            extra={"tenant_id": tenant_id, "error": str(e)},
        )
        return []


async def _record_rule_triggered(
    tenant_id: str, rule: Any, conversation_id: str, action_type: str
) -> None:
    """Incrémente usage_count et émet l'événement analytics rule_triggered. Best-effort."""
    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except (ValueError, AttributeError, TypeError):
        return

    try:
        async with UnitOfWork(tenant_uuid) as uow:
            fresh_rule = await uow.rules.get_by_id(rule.id)
            if fresh_rule:
                await uow.rules.increment_usage(fresh_rule)

            try:
                entity_id = uuid.UUID(conversation_id)
            except (ValueError, AttributeError, TypeError):
                entity_id = None

            await uow.analytics.create_event(
                event_type="rule_triggered",
                entity_type="rule",
                entity_id=entity_id or rule.id,
                payload={
                    "rule_id": str(rule.id),
                    "rule_name": rule.name,
                    "action_type": action_type,
                    "conversation_id": conversation_id,
                },
            )
            await uow.commit()
    except Exception as e:
        logger.warning(
            "Skipping rule usage tracking for this request",
            extra={
                "tenant_id": tenant_id,
                "rule_id": str(getattr(rule, "id", None)),
                "error": str(e),
            },
        )


async def _generate_coupon_from_rule(
    tenant_id: str,
    rule_id: Any,
    action: Dict[str, Any],
    customer_id: Optional[str],
    conversation_id: str,
) -> Optional[Dict[str, Any]]:
    """Génère un coupon réel à partir de l'action `generate_coupon` d'une règle. Best-effort."""
    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except (ValueError, AttributeError, TypeError):
        return None

    discount_percent = action.get("discount_percent", 10)
    validity_days = action.get("validity_days", 7)

    try:
        conv_uuid = uuid.UUID(conversation_id)
    except (ValueError, AttributeError, TypeError):
        conv_uuid = None

    try:
        async with UnitOfWork(tenant_uuid) as uow:
            code = uow.coupons.generate_code()
            now = datetime.now(timezone.utc)
            expires_at = now + timedelta(days=validity_days)

            coupon = await uow.coupons.create(
                code=code,
                discount_percent=discount_percent,
                discount_amount=None,
                min_purchase=None,
                expires_at=expires_at,
                reason="rule_triggered",
                customer_id=customer_id,
                conversation_id=conv_uuid,
                rule_id=rule_id,
            )
            await uow.commit()

            return {
                "code": coupon.code,
                "discount_percent": discount_percent,
                "expires_at": expires_at.isoformat(),
            }
    except Exception as e:
        logger.warning(
            "Skipping rule-triggered coupon generation for this request",
            extra={"tenant_id": tenant_id, "rule_id": str(rule_id), "error": str(e)},
        )
        return None


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    request: Request,
    limit: int = 50,
    offset: int = 0,
    status: Optional[str] = None,
):
    """
    List conversations for the tenant, most recent first - backs the
    backoffice's conversation browser. No such endpoint existed before;
    only a lookup by a known conversation_id (GET /history/{id}) did.
    """
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")

    try:
        tenant_uuid = uuid.UUID(tenant_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=404,
            detail="Conversations require a real tenant (dev header bypass has no persisted data)",
        )

    status_filter = None
    if status:
        try:
            status_filter = ConversationStatus(status)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Unknown status: {status}")

    async with UnitOfWork(tenant_uuid) as uow:
        conversations = await uow.conversations.list_recent(
            limit=limit, offset=offset, status=status_filter
        )
        total = await uow.conversations.count_all(status=status_filter)

        conversation_ids = [c.id for c in conversations]
        message_stats: Dict[Any, Any] = {}
        if conversation_ids:
            stmt = (
                select(
                    Message.conversation_id,
                    func.count().label("cnt"),
                    func.max(Message.created_at).label("last_message_at"),
                )
                .where(
                    Message.tenant_id == tenant_uuid,
                    Message.conversation_id.in_(conversation_ids),
                )
                .group_by(Message.conversation_id)
            )
            rows = (await uow.session.execute(stmt)).all()
            message_stats = {r.conversation_id: (r.cnt, r.last_message_at) for r in rows}

    return ConversationListResponse(
        conversations=[
            ConversationSummary(
                conversation_id=str(c.id),
                user_identifier=c.user_identifier,
                status=c.status.value,
                message_count=message_stats.get(c.id, (0, None))[0],
                last_message_at=message_stats.get(c.id, (0, c.created_at))[1] or c.created_at,
                created_at=c.created_at,
            )
            for c in conversations
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


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
