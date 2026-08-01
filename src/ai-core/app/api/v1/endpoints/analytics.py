"""
Analytics Endpoints - Business Analytics & Reports
"""

import logging
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import Float, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.settings import settings
from app.infrastructure.database.connection import get_async_session
from app.infrastructure.database.models.conversation import Conversation, ConversationStatus
from app.infrastructure.database.models.coupon import Coupon, CouponStatus
from app.infrastructure.database.models.message import Message, MessageRole
from app.services.insights.service import InsightsService

logger = logging.getLogger(__name__)

router = APIRouter()


def _since_for(time_range: "TimeRange") -> datetime:
    """Résout un TimeRange en borne de date - pas de gestion de fuseau
    horaire par tenant pour l'instant, tout est en UTC."""
    now = datetime.now(timezone.utc)
    days_by_range = {
        TimeRange.TODAY: 1,
        TimeRange.YESTERDAY: 2,
        TimeRange.LAST_7_DAYS: 7,
        TimeRange.LAST_30_DAYS: 30,
        TimeRange.THIS_MONTH: 31,
        TimeRange.LAST_MONTH: 62,
    }
    return now - timedelta(days=days_by_range.get(time_range, 7))


def _daily_labels(since: datetime) -> List[str]:
    """Full list of "YYYY-MM-DD" day labels from `since` to today (inclusive),
    so chart series have no gaps on days with zero activity."""
    start = since.date()
    end = datetime.now(timezone.utc).date()
    days = (end - start).days
    return [(start + timedelta(days=i)).isoformat() for i in range(days + 1)]


def _require_tenant_uuid(request: Request) -> UUID:
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Tenant context required")
    try:
        return UUID(str(tenant_id))
    except ValueError:
        # Bypass dev avec un identifiant humain (ex. "demo-tenant") - pas de
        # ligne réelle à agréger, donc pas d'erreur mais rien à mesurer.
        raise HTTPException(
            status_code=404,
            detail="Analytics require a real tenant (dev header bypass has no persisted data)",
        )


# =============================================================================
# SCHEMAS
# =============================================================================


class TimeRange(str, Enum):
    """Predefined time ranges"""

    TODAY = "today"
    YESTERDAY = "yesterday"
    LAST_7_DAYS = "last_7_days"
    LAST_30_DAYS = "last_30_days"
    THIS_MONTH = "this_month"
    LAST_MONTH = "last_month"
    CUSTOM = "custom"


class DashboardRequest(BaseModel):
    """Request for dashboard data"""

    time_range: TimeRange = TimeRange.LAST_7_DAYS
    start_date: Optional[date] = None
    end_date: Optional[date] = None


class MetricValue(BaseModel):
    """A single metric value"""

    name: str
    value: float
    unit: str
    change: Optional[float] = None  # Percentage change
    trend: Optional[str] = None  # "up", "down", "stable"


class DashboardResponse(BaseModel):
    """Dashboard data response"""

    metrics: List[MetricValue]
    charts: Dict[str, List[Dict[str, Any]]]
    insights: List[Dict[str, Any]]
    generated_at: datetime


class TimeseriesMetric(str, Enum):
    """Chart metrics backed by real per-day aggregates - see get_timeseries."""

    CONVERSATIONS = "conversations"
    LLM_COST = "llm_cost"
    GUARDRAIL_BLOCKS = "guardrail_blocks"
    INTENT_DISTRIBUTION = "intent_distribution"


class TimeseriesPoint(BaseModel):
    """One chart data point. `label` is a day ("2026-07-25") for every
    metric except intent_distribution, where it's the intent name -
    that one is a distribution, not a series over time."""

    label: str
    value: float


class TimeseriesResponse(BaseModel):
    metric: TimeseriesMetric
    time_range: TimeRange
    points: List[TimeseriesPoint]


class ReportRequest(BaseModel):
    """Request to generate a report"""

    report_type: str  # "sales", "customers", "ai_performance", "coupons"
    time_range: TimeRange = TimeRange.LAST_30_DAYS
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    format: str = "json"  # "json", "csv", "pdf"
    filters: Optional[Dict[str, Any]] = None


class ReportResponse(BaseModel):
    """Generated report response"""

    report_id: str
    type: str
    status: str  # "ready", "generating", "failed"
    data: Optional[Dict[str, Any]] = None
    download_url: Optional[str] = None


class ExpensiveMessage(BaseModel):
    """One assistant message, for the cost report's "most expensive
    recent messages" list."""

    message_id: str
    conversation_id: str
    content_preview: str
    tokens_input: int
    tokens_output: int
    cost_usd: float
    latency_ms: Optional[int] = None
    created_at: datetime


class CostReportResponse(BaseModel):
    """Per-tenant token/cost tracking - computed from messages.tokens_input/
    tokens_output/latency_ms, the same real columns _log_assistant_message
    writes on every chat turn."""

    time_range: TimeRange
    total_messages: int
    total_tokens_input: int
    total_tokens_output: int
    total_cost_usd: float
    avg_latency_ms: float
    conversation_count: int
    cost_per_conversation_usd: float
    recent_expensive_messages: List[ExpensiveMessage]


# =============================================================================
# ENDPOINTS
# =============================================================================


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    request: Request,
    time_range: TimeRange = TimeRange.LAST_7_DAYS,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Get main analytics dashboard data - computed from real conversations/
    messages, not fabricated. Metrics with no real signal yet (e.g. no
    conversation has ever been marked RESOLVED because nothing calls
    DELETE /chat/conversation/{id} in the current UI) will honestly show
    0 rather than an invented number.
    """
    tenant_id = _require_tenant_uuid(request)
    since = _since_for(time_range)

    conv_count = (
        await db.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.tenant_id == tenant_id, Conversation.created_at >= since)
        )
        or 0
    )

    resolved_count = (
        await db.scalar(
            select(func.count())
            .select_from(Conversation)
            .where(
                Conversation.tenant_id == tenant_id,
                Conversation.created_at >= since,
                Conversation.status == ConversationStatus.RESOLVED,
            )
        )
        or 0
    )
    resolution_rate = (resolved_count / conv_count * 100) if conv_count else 0.0

    avg_latency_ms = await db.scalar(
        select(func.avg(Message.latency_ms)).where(
            Message.tenant_id == tenant_id,
            Message.role == MessageRole.ASSISTANT,
            Message.created_at >= since,
        )
    )
    avg_response_seconds = round((avg_latency_ms or 0) / 1000, 2)

    avg_rating = await db.scalar(
        select(func.avg(func.cast(Message.extra_data["rating"].astext, Float))).where(
            Message.tenant_id == tenant_id,
            Message.created_at >= since,
            Message.extra_data.has_key("rating"),  # noqa: W601 - JSONB operator, not dict.has_key
        )
    )
    satisfaction_score = round(avg_rating, 2) if avg_rating is not None else 0.0

    return DashboardResponse(
        metrics=[
            MetricValue(name="Conversations", value=float(conv_count), unit="count"),
            MetricValue(name="Resolution Rate", value=round(resolution_rate, 1), unit="percent"),
            MetricValue(name="Avg Response Time", value=avg_response_seconds, unit="seconds"),
            MetricValue(name="Satisfaction Score", value=satisfaction_score, unit="score"),
        ],
        charts={"conversations_by_day": [], "intents_distribution": [], "satisfaction_trend": []},
        insights=[],
        generated_at=datetime.now(timezone.utc),
    )


@router.get("/timeseries", response_model=TimeseriesResponse)
async def get_timeseries(
    request: Request,
    metric: TimeseriesMetric,
    time_range: TimeRange = TimeRange.LAST_30_DAYS,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Real per-day (or per-intent) aggregates backing the backoffice's chart
    widgets. The `charts` field on /dashboard was always an empty dict
    because nothing ever computed it - this is that computation, exposed
    directly so each ChartWidget can request only the metric it needs.
    """
    tenant_id = _require_tenant_uuid(request)
    since = _since_for(time_range)

    if metric == TimeseriesMetric.INTENT_DISTRIBUTION:
        distribution = await InsightsService(db, tenant_id).intent_distribution(since)
        points = [
            TimeseriesPoint(label=intent, value=count) for intent, count in distribution.items()
        ]
        return TimeseriesResponse(metric=metric, time_range=time_range, points=points)

    message_day = func.to_char(func.date_trunc("day", Message.created_at), "YYYY-MM-DD")

    if metric == TimeseriesMetric.CONVERSATIONS:
        conversation_day = func.to_char(
            func.date_trunc("day", Conversation.created_at), "YYYY-MM-DD"
        )
        stmt = (
            select(conversation_day.label("day"), func.count().label("cnt"))
            .where(Conversation.tenant_id == tenant_id, Conversation.created_at >= since)
            .group_by(conversation_day)
        )
        rows = (await db.execute(stmt)).all()
        counts = {r.day: r.cnt for r in rows}
        points = [
            TimeseriesPoint(label=day, value=counts.get(day, 0)) for day in _daily_labels(since)
        ]

    elif metric == TimeseriesMetric.GUARDRAIL_BLOCKS:
        stmt = (
            select(message_day.label("day"), func.count().label("cnt"))
            .where(
                Message.tenant_id == tenant_id,
                Message.created_at >= since,
                Message.extra_data["guardrail_blocked"].astext == "true",
            )
            .group_by(message_day)
        )
        rows = (await db.execute(stmt)).all()
        counts = {r.day: r.cnt for r in rows}
        points = [
            TimeseriesPoint(label=day, value=counts.get(day, 0)) for day in _daily_labels(since)
        ]

    elif metric == TimeseriesMetric.LLM_COST:
        stmt = (
            select(
                message_day.label("day"),
                func.coalesce(func.sum(Message.tokens_input), 0).label("input_tokens"),
                func.coalesce(func.sum(Message.tokens_output), 0).label("output_tokens"),
            )
            .where(
                Message.tenant_id == tenant_id,
                Message.role == MessageRole.ASSISTANT,
                Message.created_at >= since,
            )
            .group_by(message_day)
        )
        rows = (await db.execute(stmt)).all()
        costs = {
            r.day: round(
                r.input_tokens / 1000 * settings.llm.cost_per_1k_input_tokens
                + r.output_tokens / 1000 * settings.llm.cost_per_1k_output_tokens,
                4,
            )
            for r in rows
        }
        points = [
            TimeseriesPoint(label=day, value=costs.get(day, 0.0)) for day in _daily_labels(since)
        ]

    else:
        raise HTTPException(status_code=400, detail=f"Unknown metric: {metric}")

    return TimeseriesResponse(metric=metric, time_range=time_range, points=points)


@router.get("/ai-performance")
async def get_ai_performance(
    request: Request,
    time_range: TimeRange = TimeRange.LAST_7_DAYS,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Get AI-specific performance metrics - computed from real assistant
    messages (see chat.py's _log_assistant_message for what gets stored
    in extra_data/tokens_input/tokens_output).
    """
    tenant_id = _require_tenant_uuid(request)
    since = _since_for(time_range)

    base_filter = (
        Message.tenant_id == tenant_id,
        Message.role == MessageRole.ASSISTANT,
        Message.created_at >= since,
    )

    total_assistant_msgs = await db.scalar(
        select(func.count()).select_from(Message).where(*base_filter)
    )

    if not total_assistant_msgs:
        return {
            "intent_accuracy": 0.0,
            "avg_confidence": 0.0,
            "hallucination_rate": 0.0,
            "guardrail_blocks": 0,
            "llm_cost_usd": 0.0,
            "avg_tokens_per_request": 0.0,
            "time_range": time_range.value,
            "note": "No assistant messages in this time range yet.",
        }

    # "intent_accuracy" nomme un score de précision au sens classique
    # (comparaison à une vérité terrain étiquetée), qui n'existe pas ici -
    # aucun pipeline d'évaluation humaine n'est en place. On calcule à la
    # place un proxy honnête et documenté: la part des messages où le
    # classifieur par règles (_classify_intent) a trouvé une intention
    # spécifique plutôt que de retomber sur "general".
    specific_intent_count = await db.scalar(
        select(func.count())
        .select_from(Message)
        .where(*base_filter, Message.extra_data["intent"].astext != "general")
    )
    intent_accuracy_proxy = (specific_intent_count or 0) / total_assistant_msgs

    avg_confidence = await db.scalar(
        select(func.avg(func.cast(Message.extra_data["confidence"].astext, Float))).where(
            *base_filter
        )
    )

    hallucination_count = await db.scalar(
        select(func.count())
        .select_from(Message)
        .where(*base_filter, Message.extra_data["hallucination_flagged"].astext == "true")
    )
    hallucination_rate = (hallucination_count or 0) / total_assistant_msgs

    guardrail_blocks = await db.scalar(
        select(func.count())
        .select_from(Message)
        .where(
            Message.tenant_id == tenant_id,
            Message.created_at >= since,
            Message.extra_data["guardrail_blocked"].astext == "true",
        )
    )

    total_input_tokens = (
        await db.scalar(select(func.sum(Message.tokens_input)).where(*base_filter)) or 0
    )
    total_output_tokens = (
        await db.scalar(select(func.sum(Message.tokens_output)).where(*base_filter)) or 0
    )
    llm_cost_usd = (
        total_input_tokens / 1000 * settings.llm.cost_per_1k_input_tokens
        + total_output_tokens / 1000 * settings.llm.cost_per_1k_output_tokens
    )
    avg_tokens_per_request = (total_input_tokens + total_output_tokens) / total_assistant_msgs

    return {
        "intent_accuracy": round(intent_accuracy_proxy, 3),
        "avg_confidence": round(avg_confidence, 3) if avg_confidence is not None else 0.0,
        "hallucination_rate": round(hallucination_rate, 3),
        "guardrail_blocks": guardrail_blocks or 0,
        "llm_cost_usd": round(llm_cost_usd, 4),
        "avg_tokens_per_request": round(avg_tokens_per_request, 1),
        "time_range": time_range.value,
    }


@router.get("/cost-report", response_model=CostReportResponse)
async def get_cost_report(
    request: Request,
    time_range: TimeRange = TimeRange.LAST_30_DAYS,
    limit: int = 10,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Per-tenant token/cost tracking for the backoffice's cost page: totals,
    avg latency, cost-per-conversation, and the most expensive recent
    messages - all computed from messages.tokens_input/tokens_output/
    latency_ms, real columns written on every chat turn (see
    chat.py::_log_assistant_message). Not a new data source, just a report
    over data that already existed and was never surfaced.
    """
    tenant_id = _require_tenant_uuid(request)
    since = _since_for(time_range)

    base_filter = (
        Message.tenant_id == tenant_id,
        Message.role == MessageRole.ASSISTANT,
        Message.created_at >= since,
    )

    total_messages = (
        await db.scalar(select(func.count()).select_from(Message).where(*base_filter)) or 0
    )

    if not total_messages:
        return CostReportResponse(
            time_range=time_range,
            total_messages=0,
            total_tokens_input=0,
            total_tokens_output=0,
            total_cost_usd=0.0,
            avg_latency_ms=0.0,
            conversation_count=0,
            cost_per_conversation_usd=0.0,
            recent_expensive_messages=[],
        )

    total_input_tokens = (
        await db.scalar(
            select(func.coalesce(func.sum(Message.tokens_input), 0)).where(*base_filter)
        )
        or 0
    )
    total_output_tokens = (
        await db.scalar(
            select(func.coalesce(func.sum(Message.tokens_output), 0)).where(*base_filter)
        )
        or 0
    )
    avg_latency_ms = await db.scalar(select(func.avg(Message.latency_ms)).where(*base_filter))

    total_cost_usd = (
        total_input_tokens / 1000 * settings.llm.cost_per_1k_input_tokens
        + total_output_tokens / 1000 * settings.llm.cost_per_1k_output_tokens
    )

    conversation_count = (
        await db.scalar(
            select(func.count(func.distinct(Message.conversation_id))).where(*base_filter)
        )
        or 0
    )
    cost_per_conversation_usd = (
        round(total_cost_usd / conversation_count, 4) if conversation_count else 0.0
    )

    cost_expr = func.coalesce(Message.tokens_input, 0) / 1000.0 * float(
        settings.llm.cost_per_1k_input_tokens
    ) + func.coalesce(Message.tokens_output, 0) / 1000.0 * float(
        settings.llm.cost_per_1k_output_tokens
    )
    expensive_stmt = (
        select(Message, cost_expr.label("cost"))
        .where(*base_filter)
        .order_by(cost_expr.desc())
        .limit(limit)
    )
    rows = (await db.execute(expensive_stmt)).all()

    recent_expensive_messages = [
        ExpensiveMessage(
            message_id=str(m.id),
            conversation_id=str(m.conversation_id),
            content_preview=(m.content or "")[:120],
            tokens_input=m.tokens_input or 0,
            tokens_output=m.tokens_output or 0,
            cost_usd=round(cost, 6),
            latency_ms=m.latency_ms,
            created_at=m.created_at,
        )
        for m, cost in rows
    ]

    return CostReportResponse(
        time_range=time_range,
        total_messages=total_messages,
        total_tokens_input=total_input_tokens,
        total_tokens_output=total_output_tokens,
        total_cost_usd=round(total_cost_usd, 4),
        avg_latency_ms=round(avg_latency_ms or 0, 1),
        conversation_count=conversation_count,
        cost_per_conversation_usd=cost_per_conversation_usd,
        recent_expensive_messages=recent_expensive_messages,
    )


@router.get("/customers")
async def get_customer_analytics(
    request: Request,
    time_range: TimeRange = TimeRange.LAST_30_DAYS,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Get customer analytics.

    NOTE: il n'existe pas de table `customers` dans ce projet - le chat
    identifie ses interlocuteurs par un `user_identifier` libre porté par
    la conversation (session_id, customer_id de la boutique, ou un
    identifiant anonyme). On compte donc des interlocuteurs distincts
    réels, pas des "clients" au sens CRM, et on ne renvoie ni
    segmentation (vip/at_risk/...) ni engagement_rate : ces notions
    n'ont aucune source de données ici et étaient purement inventées.
    """
    tenant_id = _require_tenant_uuid(request)
    since = _since_for(time_range)

    distinct_users = await db.scalar(
        select(func.count(func.distinct(Conversation.user_identifier))).where(
            Conversation.tenant_id == tenant_id
        )
    )
    active_users = await db.scalar(
        select(func.count(func.distinct(Conversation.user_identifier))).where(
            Conversation.tenant_id == tenant_id, Conversation.created_at >= since
        )
    )

    return {
        "total_identified_users": distinct_users or 0,
        "active_users": active_users or 0,
        "time_range": time_range.value,
    }


@router.get("/coupons")
async def get_coupon_analytics(
    request: Request,
    time_range: TimeRange = TimeRange.LAST_30_DAYS,
    db: AsyncSession = Depends(get_async_session),
):
    """
    Get coupon usage analytics.

    NOTE: pas de `avg_order_with/without_coupon` - le projet ne stocke
    aucune commande (pas de table orders), ces chiffres étaient inventés.
    """
    tenant_id = _require_tenant_uuid(request)
    since = _since_for(time_range)

    generated = await db.scalar(
        select(func.count())
        .select_from(Coupon)
        .where(Coupon.tenant_id == tenant_id, Coupon.created_at >= since)
    )
    used = await db.scalar(
        select(func.count())
        .select_from(Coupon)
        .where(
            Coupon.tenant_id == tenant_id,
            Coupon.created_at >= since,
            Coupon.status == CouponStatus.USED,
        )
    )

    return {
        "coupons_generated": generated or 0,
        "coupons_used": used or 0,
        "conversion_rate": round((used or 0) / generated, 3) if generated else 0.0,
        "time_range": time_range.value,
    }


@router.post("/report", response_model=ReportResponse)
async def generate_report(request: Request, body: ReportRequest):
    """
    Generate a custom report.

    NOT IMPLEMENTED. Cet endpoint renvoyait auparavant un
    report_id="rpt_placeholder" avec status="ready" et un contenu
    factice - un client suivant le contrat de l'API aurait cru un
    rapport disponible et n'aurait jamais rien reçu. Tant que la
    génération (probablement asynchrone via la file de messages
    existante) n'est pas construite, on renvoie explicitement 501
    plutôt que de simuler un succès.
    """
    tenant_id = getattr(request.state, "tenant_id", None)

    logger.info(
        "Report generation requested but not implemented",
        extra={"tenant_id": tenant_id, "report_type": body.report_type, "format": body.format},
    )

    raise HTTPException(
        status_code=501,
        detail="Report generation is not implemented yet. Use the /analytics/* endpoints instead.",
    )


@router.get("/report/{report_id}")
async def get_report_status(request: Request, report_id: str):
    """
    Get status of a generated report. NOT IMPLEMENTED - voir generate_report.
    """
    raise HTTPException(status_code=501, detail="Report generation is not implemented yet.")
