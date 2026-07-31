"""
Middleware Tenant Context - Extraction et validation du tenant
"""

import hashlib
import logging
from typing import Callable, Optional

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config.settings import settings

logger = logging.getLogger(__name__)

DEFAULT_PLAN = "starter"


def _plan_config(plan_name: str) -> dict:
    """Résout un plan vers sa config (features, rate_limit_rpm) via settings.tenant.plans."""
    plans = settings.tenant.plans
    return plans.get(plan_name, plans[DEFAULT_PLAN])


class TenantContextMiddleware(BaseHTTPMiddleware):
    """
    Middleware pour:
    - Extraire le tenant de l'API key
    - Valider l'API key
    - Injecter le contexte tenant dans la requête
    """

    # Routes qui ne nécessitent pas d'authentification (préfixes, sauf "/" qui est exact)
    PUBLIC_PATH_PREFIXES = [
        "/health",
        "/metrics",
        "/docs",
        "/redoc",
        "/openapi.json",
        # Un prospect n'a pas encore de clé API pour vérifier son email.
        f"{settings.api_prefix}/tenants/verify/",
    ]
    # Chemins publics uniquement en correspondance EXACTE (pas de préfixe -
    # POST {api_prefix}/tenants est le signup public, mais
    # {api_prefix}/tenants/current etc. doivent rester protégés).
    PUBLIC_EXACT_PATHS = ["/", f"{settings.api_prefix}/tenants"]

    def __init__(self, app, session_factory=None):
        """
        Args:
            session_factory: callable retournant une session SQLAlchemy async
                (ex. AsyncSessionLocal). Une nouvelle session est ouverte à
                chaque validation de clé API - le middleware est instancié
                une seule fois pour toute la durée de vie de l'app, il ne
                peut donc pas garder une session ouverte en permanence.
        """
        super().__init__(app)
        self._session_factory = session_factory
        self._tenant_cache = {}  # Cache simple en mémoire

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Auto-enregistrement pour que les endpoints puissent invalider le
        # cache après une rotation/révocation de clé (ex. rotate_api_key) -
        # request.app est toujours la vraie instance FastAPI, contrairement
        # à self.app qui est la couche ASGI suivante dans la chaîne.
        request.app.state.tenant_context_middleware = self

        # Skip pour les routes publiques
        if self._is_public_path(request.url.path):
            return await call_next(request)

        # Mode développement/test: accepter X-Tenant-ID directement (pas
        # d'infrastructure API key/tenant réelle en CI - Environment.TEST
        # est explicitement documenté comme "Exécution des tests (CI/CD)")
        if settings.is_development or settings.is_test:
            tenant_id = request.headers.get("X-Tenant-ID")
            if tenant_id:
                # En dev, on accepte n'importe quel tenant ID
                dev_plan = _plan_config("enterprise")  # Full access en dev
                request.state.tenant_id = tenant_id
                request.state.tenant_plan = "enterprise"
                request.state.tenant_features = dev_plan["features"]
                request.state.tenant_rate_limit = dev_plan["rate_limit_rpm"]

                response = await call_next(request)
                response.headers["X-Tenant-ID"] = tenant_id
                return response

        # Extraire l'API key
        api_key = self._extract_api_key(request)

        if not api_key:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "unauthorized", "message": "API key required"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Valider et récupérer le tenant
        tenant = await self._validate_and_get_tenant(api_key)

        if not tenant:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "unauthorized", "message": "Invalid API key"},
            )

        # Vérifier que le tenant est actif
        if not tenant.get("is_active", False):
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"error": "forbidden", "message": "Tenant account is inactive"},
            )

        # Injecter le contexte dans la requête
        request.state.tenant_id = tenant["id"]
        request.state.tenant_plan = tenant["plan"]
        request.state.tenant_features = tenant["features"]
        request.state.tenant_rate_limit = tenant["rate_limit_rpm"]

        # Ajouter header pour tracing
        response = await call_next(request)
        response.headers["X-Tenant-ID"] = tenant["id"]

        return response

    def _is_public_path(self, path: str) -> bool:
        """Vérifie si le path est public"""
        if path in self.PUBLIC_EXACT_PATHS:
            return True
        return any(path.startswith(p) for p in self.PUBLIC_PATH_PREFIXES)

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
                    extra={"path": request.url.path},
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
        if self._session_factory:
            from app.infrastructure.database.repositories.tenant_repo import TenantRepository

            async with self._session_factory() as session:
                tenant = await TenantRepository(session).get_by_api_key_hash(api_key_hash)

            if tenant:
                plan_name = (tenant.settings or {}).get("plan", DEFAULT_PLAN)
                plan = _plan_config(plan_name)
                tenant_data = {
                    "id": str(tenant.id),
                    "plan": plan_name,
                    "features": plan["features"],
                    "rate_limit_rpm": plan["rate_limit_rpm"],
                    "is_active": tenant.is_active,
                }

                # Mettre en cache (5 minutes)
                self._tenant_cache[api_key_hash] = tenant_data

                return tenant_data

        return None

    def clear_cache(self, tenant_id: str = None):
        """Vide le cache (tout ou pour un tenant spécifique)"""
        if tenant_id:
            # Trouver et supprimer les entrées de ce tenant
            to_remove = [k for k, v in self._tenant_cache.items() if v.get("id") == tenant_id]
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
        "rate_limit": getattr(request.state, "tenant_rate_limit", 60),
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
                detail=f"Feature '{feature}' not available in your plan",
            )

        return get_current_tenant(request)

    return check_feature
