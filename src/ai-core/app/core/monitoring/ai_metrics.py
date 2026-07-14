"""
AI Metrics - Métriques spécifiques IA pour Prometheus

Métriques collectées:
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          AI OBSERVABILITY METRICS                                │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  LLM METRICS                           RAG METRICS                               │
│  ├── Token usage (input/output)        ├── Query latency (p50/p95/p99)          │
│  ├── Cost per tenant                   ├── Hit rate (found relevant docs)       │
│  ├── Latency (p50/p95/p99)            ├── Rerank improvement                    │
│  ├── Error rate                        ├── Embedding queue backlog              │
│  └── Model distribution                └── Index size per tenant                │
│                                                                                  │
│  QUALITY METRICS                       BUSINESS METRICS                          │
│  ├── Hallucination rate (detected)     ├── Conversations per tenant             │
│  ├── Guardrail triggers                ├── Messages per conversation            │
│  ├── Prompt injection attempts         ├── Conversion rate                      │
│  └── Output validation failures        └── Response satisfaction                │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
"""

import logging
import time
from contextlib import contextmanager
from typing import Dict, Optional

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Summary,
    generate_latest,
)

logger = logging.getLogger(__name__)

# =============================================================================
# REGISTRY
# =============================================================================

# Utiliser le registry par défaut ou créer un custom
REGISTRY = CollectorRegistry(auto_describe=True)


# =============================================================================
# LLM METRICS
# =============================================================================

# Token usage
llm_tokens_total = Counter(
    "llm_tokens_total",
    "Total tokens used by LLM",
    ["tenant_id", "model", "token_type"],  # token_type: input, output
    registry=REGISTRY,
)

llm_cost_total = Counter(
    "llm_cost_dollars_total",
    "Total cost in dollars for LLM usage",
    ["tenant_id", "model"],
    registry=REGISTRY,
)

# Latency
llm_request_duration_seconds = Histogram(
    "llm_request_duration_seconds",
    "LLM request latency in seconds",
    ["tenant_id", "model", "operation"],  # operation: chat, embedding, rerank
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
    registry=REGISTRY,
)

llm_request_latency_summary = Summary(
    "llm_request_latency_seconds",
    "LLM request latency summary (quantiles)",
    ["tenant_id", "model"],
    registry=REGISTRY,
)

# Errors
llm_errors_total = Counter(
    "llm_errors_total",
    "Total LLM errors",
    ["tenant_id", "model", "error_type"],  # error_type: timeout, rate_limit, invalid_response, etc.
    registry=REGISTRY,
)

# Model usage distribution
llm_requests_total = Counter(
    "llm_requests_total",
    "Total LLM requests",
    ["tenant_id", "model", "status"],  # status: success, error
    registry=REGISTRY,
)


# =============================================================================
# RAG METRICS
# =============================================================================

# Query latency
rag_query_duration_seconds = Histogram(
    "rag_query_duration_seconds",
    "RAG query latency in seconds",
    ["tenant_id", "doc_type", "stage"],  # stage: retrieval, rerank, total
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
    registry=REGISTRY,
)

# Hit rate (found relevant documents)
rag_queries_total = Counter(
    "rag_queries_total",
    "Total RAG queries",
    ["tenant_id", "doc_type", "result"],  # result: hit, miss, partial
    registry=REGISTRY,
)

rag_documents_retrieved = Histogram(
    "rag_documents_retrieved",
    "Number of documents retrieved per query",
    ["tenant_id", "doc_type"],
    buckets=[0, 1, 2, 3, 5, 10, 20],
    registry=REGISTRY,
)

# Rerank improvement
rag_rerank_improvement = Histogram(
    "rag_rerank_score_improvement",
    "Score improvement after reranking (rerank_score - retrieval_score)",
    ["tenant_id"],
    buckets=[-0.1, 0, 0.1, 0.2, 0.3, 0.5, 1.0],
    registry=REGISTRY,
)

# Embedding queue
embedding_queue_backlog = Gauge(
    "embedding_queue_backlog",
    "Number of pending embedding jobs",
    ["queue_name"],
    registry=REGISTRY,
)

embedding_queue_processing_time = Histogram(
    "embedding_queue_processing_seconds",
    "Time to process embedding jobs",
    ["tenant_id", "doc_type"],
    buckets=[0.5, 1, 2, 5, 10, 30, 60, 120],
    registry=REGISTRY,
)

# Index size
rag_index_documents_total = Gauge(
    "rag_index_documents_total",
    "Total documents in RAG index",
    ["tenant_id", "doc_type"],
    registry=REGISTRY,
)


# =============================================================================
# QUALITY METRICS
# =============================================================================

# Hallucination detection
ai_hallucination_detected_total = Counter(
    "ai_hallucination_detected_total",
    "Number of detected hallucinations",
    ["tenant_id", "detection_method"],  # detection_method: factual_check, confidence_low, etc.
    registry=REGISTRY,
)

ai_response_confidence = Histogram(
    "ai_response_confidence",
    "AI response confidence score (0-1)",
    ["tenant_id"],
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
    registry=REGISTRY,
)

# Guardrails
guardrail_triggers_total = Counter(
    "guardrail_triggers_total",
    "Number of guardrail triggers",
    ["tenant_id", "guardrail_type", "action"],  # action: blocked, modified, allowed
    registry=REGISTRY,
)

# Prompt injection
prompt_injection_attempts_total = Counter(
    "prompt_injection_attempts_total",
    "Number of prompt injection attempts detected",
    ["tenant_id", "threat_level", "blocked"],
    registry=REGISTRY,
)

# Output validation
output_validation_failures_total = Counter(
    "output_validation_failures_total",
    "Number of output validation failures",
    ["tenant_id", "failure_type"],  # failure_type: format, content, length, etc.
    registry=REGISTRY,
)


# =============================================================================
# BUSINESS METRICS
# =============================================================================

# Conversations
conversations_total = Counter(
    "conversations_total",
    "Total conversations started",
    ["tenant_id"],
    registry=REGISTRY,
)

conversations_active = Gauge(
    "conversations_active",
    "Currently active conversations",
    ["tenant_id"],
    registry=REGISTRY,
)

messages_per_conversation = Histogram(
    "messages_per_conversation",
    "Number of messages per conversation",
    ["tenant_id"],
    buckets=[1, 2, 3, 5, 10, 20, 50],
    registry=REGISTRY,
)

conversation_duration_seconds = Histogram(
    "conversation_duration_seconds",
    "Duration of conversations",
    ["tenant_id", "outcome"],  # outcome: completed, abandoned, converted
    buckets=[30, 60, 120, 300, 600, 1800, 3600],
    registry=REGISTRY,
)

# Conversion
conversion_events_total = Counter(
    "conversion_events_total",
    "Conversion events from AI interactions",
    ["tenant_id", "event_type"],  # event_type: coupon_used, product_clicked, purchased
    registry=REGISTRY,
)

# Satisfaction
response_feedback_total = Counter(
    "response_feedback_total",
    "User feedback on responses",
    ["tenant_id", "feedback"],  # feedback: positive, negative, neutral
    registry=REGISTRY,
)


# =============================================================================
# ADMIN AI METRICS
# =============================================================================

admin_actions_total = Counter(
    "admin_ai_actions_total",
    "Total admin AI actions",
    ["tenant_id", "action_type", "status"],  # status: approved, rejected, pending
    registry=REGISTRY,
)

admin_action_execution_time = Histogram(
    "admin_ai_action_execution_seconds",
    "Time to execute admin actions",
    ["tenant_id", "action_type"],
    buckets=[1, 5, 10, 30, 60, 120, 300],
    registry=REGISTRY,
)

admin_human_approvals_pending = Gauge(
    "admin_ai_human_approvals_pending",
    "Pending human approvals",
    ["tenant_id"],
    registry=REGISTRY,
)


# =============================================================================
# SYSTEM METRICS
# =============================================================================

api_requests_total = Counter(
    "api_requests_total",
    "Total API requests",
    ["tenant_id", "endpoint", "method", "status_code"],
    registry=REGISTRY,
)

api_request_duration_seconds = Histogram(
    "api_request_duration_seconds",
    "API request duration",
    ["tenant_id", "endpoint", "method"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
    registry=REGISTRY,
)

rate_limit_exceeded_total = Counter(
    "rate_limit_exceeded_total",
    "Rate limit exceeded events",
    ["tenant_id", "tier"],  # tier: global, tenant, endpoint
    registry=REGISTRY,
)


# =============================================================================
# METRICS COLLECTOR CLASS
# =============================================================================


class AIMetricsCollector:
    """
    Collecteur centralisé de métriques IA.

    Usage:
        metrics = AIMetricsCollector()

        # LLM
        with metrics.track_llm_request(tenant_id, model, "chat"):
            response = await llm.generate(...)
        metrics.record_llm_tokens(tenant_id, model, input_tokens, output_tokens)

        # RAG
        with metrics.track_rag_query(tenant_id, "product"):
            results = await rag.search(...)
        metrics.record_rag_hit(tenant_id, "product", hit=True)

        # Quality
        metrics.record_hallucination_detected(tenant_id, "factual_check")
        metrics.record_guardrail_trigger(tenant_id, "profanity", "blocked")
    """

    def __init__(self):
        self._start_times: Dict[str, float] = {}

    # =========================================================================
    # LLM METRICS
    # =========================================================================

    @contextmanager
    def track_llm_request(
        self,
        tenant_id: str,
        model: str,
        operation: str = "chat",
    ):
        """Context manager pour tracker une requête LLM"""
        start_time = time.perf_counter()
        status = "success"

        try:
            yield
        except Exception as e:
            status = "error"
            error_type = type(e).__name__
            llm_errors_total.labels(
                tenant_id=tenant_id,
                model=model,
                error_type=error_type,
            ).inc()
            raise
        finally:
            duration = time.perf_counter() - start_time

            llm_request_duration_seconds.labels(
                tenant_id=tenant_id,
                model=model,
                operation=operation,
            ).observe(duration)

            llm_request_latency_summary.labels(
                tenant_id=tenant_id,
                model=model,
            ).observe(duration)

            llm_requests_total.labels(
                tenant_id=tenant_id,
                model=model,
                status=status,
            ).inc()

    def record_llm_tokens(
        self,
        tenant_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost: float = 0.0,
    ):
        """Enregistre l'usage de tokens"""
        llm_tokens_total.labels(
            tenant_id=tenant_id,
            model=model,
            token_type="input",
        ).inc(input_tokens)

        llm_tokens_total.labels(
            tenant_id=tenant_id,
            model=model,
            token_type="output",
        ).inc(output_tokens)

        if cost > 0:
            llm_cost_total.labels(
                tenant_id=tenant_id,
                model=model,
            ).inc(cost)

    def record_llm_error(
        self,
        tenant_id: str,
        model: str,
        error_type: str,
    ):
        """Enregistre une erreur LLM"""
        llm_errors_total.labels(
            tenant_id=tenant_id,
            model=model,
            error_type=error_type,
        ).inc()

    # =========================================================================
    # RAG METRICS
    # =========================================================================

    @contextmanager
    def track_rag_query(
        self,
        tenant_id: str,
        doc_type: str,
        stage: str = "total",
    ):
        """Context manager pour tracker une requête RAG"""
        start_time = time.perf_counter()

        try:
            yield
        finally:
            duration = time.perf_counter() - start_time

            rag_query_duration_seconds.labels(
                tenant_id=tenant_id,
                doc_type=doc_type,
                stage=stage,
            ).observe(duration)

    def record_rag_result(
        self,
        tenant_id: str,
        doc_type: str,
        documents_found: int,
        min_relevance: float = 0.5,
    ):
        """Enregistre le résultat d'une requête RAG"""
        # Déterminer si c'est un hit, miss ou partial
        if documents_found == 0:
            result = "miss"
        elif documents_found >= 3:
            result = "hit"
        else:
            result = "partial"

        rag_queries_total.labels(
            tenant_id=tenant_id,
            doc_type=doc_type,
            result=result,
        ).inc()

        rag_documents_retrieved.labels(
            tenant_id=tenant_id,
            doc_type=doc_type,
        ).observe(documents_found)

    def record_rerank_improvement(
        self,
        tenant_id: str,
        retrieval_score: float,
        rerank_score: float,
    ):
        """Enregistre l'amélioration du reranking"""
        improvement = rerank_score - retrieval_score

        rag_rerank_improvement.labels(
            tenant_id=tenant_id,
        ).observe(improvement)

    def update_embedding_queue_backlog(
        self,
        queue_name: str,
        backlog: int,
    ):
        """Met à jour le backlog de la queue d'embedding"""
        embedding_queue_backlog.labels(
            queue_name=queue_name,
        ).set(backlog)

    def record_embedding_job(
        self,
        tenant_id: str,
        doc_type: str,
        processing_time: float,
    ):
        """Enregistre le traitement d'un job d'embedding"""
        embedding_queue_processing_time.labels(
            tenant_id=tenant_id,
            doc_type=doc_type,
        ).observe(processing_time)

    def update_index_size(
        self,
        tenant_id: str,
        doc_type: str,
        document_count: int,
    ):
        """Met à jour la taille de l'index"""
        rag_index_documents_total.labels(
            tenant_id=tenant_id,
            doc_type=doc_type,
        ).set(document_count)

    # =========================================================================
    # QUALITY METRICS
    # =========================================================================

    def record_hallucination_detected(
        self,
        tenant_id: str,
        detection_method: str,
    ):
        """Enregistre une hallucination détectée"""
        ai_hallucination_detected_total.labels(
            tenant_id=tenant_id,
            detection_method=detection_method,
        ).inc()

    def record_response_confidence(
        self,
        tenant_id: str,
        confidence: float,
    ):
        """Enregistre le score de confiance d'une réponse"""
        ai_response_confidence.labels(
            tenant_id=tenant_id,
        ).observe(confidence)

    def record_guardrail_trigger(
        self,
        tenant_id: str,
        guardrail_type: str,
        action: str,
    ):
        """Enregistre un déclenchement de guardrail"""
        guardrail_triggers_total.labels(
            tenant_id=tenant_id,
            guardrail_type=guardrail_type,
            action=action,
        ).inc()

    def record_prompt_injection_attempt(
        self,
        tenant_id: str,
        threat_level: str,
        blocked: bool,
    ):
        """Enregistre une tentative d'injection"""
        prompt_injection_attempts_total.labels(
            tenant_id=tenant_id,
            threat_level=threat_level,
            blocked=str(blocked).lower(),
        ).inc()

    def record_output_validation_failure(
        self,
        tenant_id: str,
        failure_type: str,
    ):
        """Enregistre un échec de validation d'output"""
        output_validation_failures_total.labels(
            tenant_id=tenant_id,
            failure_type=failure_type,
        ).inc()

    # =========================================================================
    # BUSINESS METRICS
    # =========================================================================

    def record_conversation_started(self, tenant_id: str):
        """Enregistre le début d'une conversation"""
        conversations_total.labels(tenant_id=tenant_id).inc()
        conversations_active.labels(tenant_id=tenant_id).inc()

    def record_conversation_ended(
        self,
        tenant_id: str,
        message_count: int,
        duration_seconds: float,
        outcome: str = "completed",
    ):
        """Enregistre la fin d'une conversation"""
        conversations_active.labels(tenant_id=tenant_id).dec()

        messages_per_conversation.labels(
            tenant_id=tenant_id,
        ).observe(message_count)

        conversation_duration_seconds.labels(
            tenant_id=tenant_id,
            outcome=outcome,
        ).observe(duration_seconds)

    def record_conversion_event(
        self,
        tenant_id: str,
        event_type: str,
    ):
        """Enregistre un événement de conversion"""
        conversion_events_total.labels(
            tenant_id=tenant_id,
            event_type=event_type,
        ).inc()

    def record_feedback(
        self,
        tenant_id: str,
        feedback: str,
    ):
        """Enregistre un feedback utilisateur"""
        response_feedback_total.labels(
            tenant_id=tenant_id,
            feedback=feedback,
        ).inc()

    # =========================================================================
    # ADMIN METRICS
    # =========================================================================

    def record_admin_action(
        self,
        tenant_id: str,
        action_type: str,
        status: str,
        execution_time: Optional[float] = None,
    ):
        """Enregistre une action admin"""
        admin_actions_total.labels(
            tenant_id=tenant_id,
            action_type=action_type,
            status=status,
        ).inc()

        if execution_time is not None:
            admin_action_execution_time.labels(
                tenant_id=tenant_id,
                action_type=action_type,
            ).observe(execution_time)

    def update_pending_approvals(
        self,
        tenant_id: str,
        count: int,
    ):
        """Met à jour le nombre d'approbations en attente"""
        admin_human_approvals_pending.labels(
            tenant_id=tenant_id,
        ).set(count)

    # =========================================================================
    # API METRICS
    # =========================================================================

    @contextmanager
    def track_api_request(
        self,
        tenant_id: str,
        endpoint: str,
        method: str,
    ):
        """Context manager pour tracker une requête API"""
        start_time = time.perf_counter()
        status_code = "200"

        try:
            yield lambda code: setattr(self, "_status_code", code)
            status_code = getattr(self, "_status_code", "200")
        except Exception:
            status_code = "500"
            raise
        finally:
            duration = time.perf_counter() - start_time

            api_requests_total.labels(
                tenant_id=tenant_id,
                endpoint=endpoint,
                method=method,
                status_code=status_code,
            ).inc()

            api_request_duration_seconds.labels(
                tenant_id=tenant_id,
                endpoint=endpoint,
                method=method,
            ).observe(duration)

    def record_rate_limit_exceeded(
        self,
        tenant_id: str,
        tier: str,
    ):
        """Enregistre un dépassement de rate limit"""
        rate_limit_exceeded_total.labels(
            tenant_id=tenant_id,
            tier=tier,
        ).inc()


# =============================================================================
# SINGLETON INSTANCE
# =============================================================================

_metrics_collector: Optional[AIMetricsCollector] = None


def get_metrics_collector() -> AIMetricsCollector:
    """Retourne l'instance singleton du collecteur de métriques"""
    global _metrics_collector
    if _metrics_collector is None:
        _metrics_collector = AIMetricsCollector()
    return _metrics_collector


# =============================================================================
# FASTAPI INTEGRATION
# =============================================================================


def get_metrics_endpoint():
    """Endpoint pour exposer les métriques Prometheus"""
    from fastapi import Response

    async def metrics():
        return Response(
            content=generate_latest(REGISTRY),
            media_type=CONTENT_TYPE_LATEST,
        )

    return metrics


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # Collector
    "AIMetricsCollector",
    "get_metrics_collector",
    # FastAPI
    "get_metrics_endpoint",
    # Registry
    "REGISTRY",
]
