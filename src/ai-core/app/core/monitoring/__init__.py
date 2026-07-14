"""
Monitoring Module - AI Observability

Features:
- AI-specific metrics (tokens, latency, hallucinations, RAG hit rate)
- Multi-tenant cost tracking
- Grafana dashboards
- SLA alerting

Usage:
    from app.core.monitoring import setup_metrics, get_metrics_collector

    # Setup FastAPI app
    app = FastAPI()
    setup_metrics(app)

    # Manual metric recording
    metrics = get_metrics_collector()
    with metrics.track_llm_request(tenant_id, model, operation):
        response = await llm.generate(...)
    metrics.record_llm_tokens(tenant_id, model, input_tokens, output_tokens)
"""

from app.core.monitoring.ai_metrics import (
    REGISTRY,
    AIMetricsCollector,
    get_metrics_collector,
    get_metrics_endpoint,
)
from app.core.monitoring.metrics_middleware import (
    MetricsMiddleware,
    setup_metrics,
    track_llm_call,
    track_rag_query,
)

__all__ = [
    # Collector
    "AIMetricsCollector",
    "get_metrics_collector",
    "get_metrics_endpoint",
    "REGISTRY",
    # Middleware
    "MetricsMiddleware",
    "setup_metrics",
    "track_llm_call",
    "track_rag_query",
]
