"""
Middleware Tenant Context - Extraction et validation du tenant
"""

from typing import Callable, Optional
import logging
import hashlib

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config.settings import settings

logger = logging.getLogger(__name__)


class TenantContextMiddleware(BaseHTTPMiddleware):
    """
    Middleware pour:
    - Extraire le tenant de l'API key
    - Valider l'API key
    - Injecter le contexte tenant dans la requête
    """

    # Routes qui ne nécessitent pas d'authentification
    PUBLIC_PATHS = [
        "/health",
        "/metrics",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/"
    ]

    def __init__(self, app, tenant_repository=None):
        super().__init__(app)
        self.tenant_repo = tenant_repository
        self._tenant_cache = {}  # Cache simple en mémoire

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip pour les routes publiques
        if self._is_public_path(request.url.path):
            return await call_next(request)

        # Mode développement: accepter X-Tenant-ID directement
        if settings.is_development:
            tenant_id = request.headers.get("X-Tenant-ID")
            if tenant_id:
                # En dev, on accepte n'importe quel tenant ID
                request.state.tenant_id = tenant_id
                request.state.tenant_plan = "enterprise"  # Full access en dev
                request.state.tenant_features = [
                    "chatbot", "faq", "recommendations",
                    "coupons", "admin_ai", "analytics"
                ]
                request.state.tenant_rate_limit = 1000

                response = await call_next(request)
                response.headers["X-Tenant-ID"] = tenant_id
                return response

        # Extraire l'API key
        api_key = self._extract_api_key(request)

        if not api_key:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "error": "unauthorized",
                    "message": "API key required"
                },
                headers={"WWW-Authenticate": "Bearer"}
            )

        # Valider et récupérer le tenant
        tenant = await self._validate_and_get_tenant(api_key)

        if not tenant:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={
                    "error": "unauthorized",
                    "message": "Invalid API key"
                }
            )

        # Vérifier que le tenant est actif
        if not tenant.get("is_active", False):
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={
                    "error": "forbidden",
                    "message": "Tenant account is inactive"
                }
            )

        # Injecter le contexte dans la requête
        request.state.tenant_id = tenant["id"]
        request.state.tenant_plan = tenant["plan"]
        request.state.tenant_features = tenant.get("features", [])
        request.state.tenant_rate_limit = tenant.get("rate_limit_rpm", 60)

        # Ajouter header pour tracing
        response = await call_next(request)
        response.headers["X-Tenant-ID"] = tenant["id"]

        return response

    def _is_public_path(self, path: str) -> bool:
        """Vérifie si le path est public"""
        return any(path.startswith(p) for p in self.PUBLIC_PATHS)

    def _extract_api_key(self, request: Request) -> Optional[str]:
        """
        Extrait l'API key depuis:
        1. Header Authorization: Bearer <key>
        2. Header X-API-Key: <key>
        3. Query param api_key=<key>
        """
        # Authorization header
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer "):
            return auth_header[7:]

        # X-API-Key header
        api_key_header = request.headers.get("X-API-Key")
        if api_key_header:
            return api_key_header

        # Query parameter (moins sécurisé, pour debug)
        if not settings.is_production:
            api_key = request.query_params.get("api_key")
            if api_key:
                logger.warning(
                    "API key passed via query parameter - this is insecure and should not be used in production",
                    extra={"path": request.url.path}
                )
            return api_key

        return None

    async def _validate_and_get_tenant(self, api_key: str) -> Optional[dict]:
        """Valide l'API key et retourne les infos du tenant"""

        # Vérifier le format de l'API key
        if not api_key.startswith(settings.security.api_key_prefix):
            return None

        # Hash de l'API key pour comparaison
        api_key_hash = hashlib.sha256(api_key.encode()).hexdigest()

        # Vérifier le cache
        if api_key_hash in self._tenant_cache:
            return self._tenant_cache[api_key_hash]

        # Récupérer depuis la base de données
        if self.tenant_repo:
            tenant = await self.tenant_repo.get_by_api_key_hash(api_key_hash)

            if tenant:
                tenant_data = {
                    "id": str(tenant.id),
                    "plan": tenant.plan.value,
                    "features": tenant.features_enabled,
                    "rate_limit_rpm": tenant.rate_limit_rpm,
                    "is_active": tenant.is_active()
                }

                # Mettre en cache (5 minutes)
                self._tenant_cache[api_key_hash] = tenant_data

                return tenant_data

        return None

    def clear_cache(self, tenant_id: str = None):
        """Vide le cache (tout ou pour un tenant spécifique)"""
        if tenant_id:
            # Trouver et supprimer les entrées de ce tenant
            to_remove = [
                k for k, v in self._tenant_cache.items()
                if v.get("id") == tenant_id
            ]
            for k in to_remove:
                del self._tenant_cache[k]
        else:
            self._tenant_cache.clear()


def get_current_tenant(request: Request) -> dict:
    """
    Dependency pour récupérer le tenant courant.
    À utiliser dans les endpoints.
    """
    return {
        "id": getattr(request.state, "tenant_id", None),
        "plan": getattr(request.state, "tenant_plan", None),
        "features": getattr(request.state, "tenant_features", []),
        "rate_limit": getattr(request.state, "tenant_rate_limit", 60)
    }


def require_feature(feature: str):
    """
    Decorator/Dependency pour vérifier qu'un tenant a accès à une feature.

    Usage:
        @router.post("/admin/command")
        async def admin_command(
            tenant: dict = Depends(require_feature("admin_ai"))
        ):
            ...
    """
    async def check_feature(request: Request):
        features = getattr(request.state, "tenant_features", [])

        if feature not in features:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Feature '{feature}' not available in your plan"
            )

        return get_current_tenant(request)

    return check_feature

