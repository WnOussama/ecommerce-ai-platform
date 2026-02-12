"""
Metrics Middleware - Intégration automatique des métriques FastAPI

Usage:
    from app.core.monitoring.metrics_middleware import setup_metrics

    app = FastAPI()
    setup_metrics(app)
"""

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Match
import time
import logging

from app.core.monitoring.ai_metrics import (
    get_metrics_collector,
    get_metrics_endpoint,
    api_requests_total,
    api_request_duration_seconds,
)

logger = logging.getLogger(__name__)


class MetricsMiddleware(BaseHTTPMiddleware):
    """
    Middleware pour collecter automatiquement les métriques API.

    Collecte:
    - Request count par endpoint/method/status
    - Request duration
    - Tenant attribution
    """

    def __init__(self, app, exclude_paths: list = None):
        super().__init__(app)
        self.exclude_paths = exclude_paths or ["/metrics", "/health", "/docs", "/openapi.json"]

    async def dispatch(self, request: Request, call_next):
        # Skip excluded paths
        if any(request.url.path.startswith(p) for p in self.exclude_paths):
            return await call_next(request)

        # Start timer
        start_time = time.perf_counter()

        # Extract tenant_id from headers, path, or state
        tenant_id = self._extract_tenant_id(request)

        # Get endpoint name (route pattern)
        endpoint = self._get_endpoint_pattern(request)
        method = request.method

        # Process request
        status_code = "500"
        try:
            response = await call_next(request)
            status_code = str(response.status_code)
            return response
        except Exception as e:
            status_code = "500"
            raise
        finally:
            # Record metrics
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

    def _extract_tenant_id(self, request: Request) -> str:
        """Extrait le tenant_id de la requête"""
        # Try header first
        tenant_id = request.headers.get("X-Tenant-ID")
        if tenant_id:
            return tenant_id

        # Try path parameter
        if hasattr(request, "path_params") and "tenant_id" in request.path_params:
            return request.path_params["tenant_id"]

        # Try state (set by auth middleware)
        if hasattr(request.state, "tenant_id"):
            return request.state.tenant_id

        return "unknown"

    def _get_endpoint_pattern(self, request: Request) -> str:
        """Retourne le pattern de route au lieu du path exact"""
        # Get the route pattern to avoid high cardinality
        for route in request.app.routes:
            match, _ = route.matches(request.scope)
            if match == Match.FULL:
                return route.path

        return request.url.path


def setup_metrics(app: FastAPI, exclude_paths: list = None):
    """
    Configure les métriques pour une application FastAPI.

    Args:
        app: FastAPI application
        exclude_paths: Paths to exclude from metrics
    """
    # Add middleware
    app.add_middleware(MetricsMiddleware, exclude_paths=exclude_paths)

    # Add metrics endpoint
    app.get("/metrics", include_in_schema=False)(get_metrics_endpoint())

    logger.info("Metrics middleware configured")


# =============================================================================
# DECORATOR FOR LLM CALLS
# =============================================================================

def track_llm_call(model: str = "unknown", operation: str = "chat"):
    """
    Décorateur pour tracker automatiquement les appels LLM.

    Usage:
        @track_llm_call(model="gpt-4", operation="chat")
        async def generate_response(tenant_id: str, prompt: str):
            ...
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            # Extract tenant_id from kwargs or first arg
            tenant_id = kwargs.get("tenant_id", "unknown")
            if not tenant_id and args:
                tenant_id = getattr(args[0], "tenant_id", "unknown")

            metrics = get_metrics_collector()

            with metrics.track_llm_request(tenant_id, model, operation):
                result = await func(*args, **kwargs)

            # If result has usage info, record it
            if hasattr(result, "usage"):
                metrics.record_llm_tokens(
                    tenant_id=tenant_id,
                    model=model,
                    input_tokens=getattr(result.usage, "prompt_tokens", 0),
                    output_tokens=getattr(result.usage, "completion_tokens", 0),
                )

            return result

        return wrapper
    return decorator


def track_rag_query(doc_type: str = "unknown"):
    """
    Décorateur pour tracker automatiquement les requêtes RAG.

    Usage:
        @track_rag_query(doc_type="product")
        async def search_products(tenant_id: str, query: str):
            ...
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            tenant_id = kwargs.get("tenant_id", "unknown")

            metrics = get_metrics_collector()

            with metrics.track_rag_query(tenant_id, doc_type):
                result = await func(*args, **kwargs)

            # Record result metrics
            if hasattr(result, "documents"):
                metrics.record_rag_result(
                    tenant_id=tenant_id,
                    doc_type=doc_type,
                    documents_found=len(result.documents),
                )

            return result

        return wrapper
    return decorator


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    "MetricsMiddleware",
    "setup_metrics",
    "track_llm_call",
    "track_rag_query",
]

