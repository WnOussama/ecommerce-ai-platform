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
from app.core.security.api_key_security import HMACSignatureValidator
from app.core.security.secret_box import decrypt_secret

logger = logging.getLogger(__name__)

_signature_validator = HMACSignatureValidator()

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
    ]
    # Chemins publics uniquement en correspondance EXACTE (pas de préfixe).
    PUBLIC_EXACT_PATHS = ["/"]

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

        # Bypass explicite (dev/CI uniquement): accepter X-Tenant-ID sans clé
        # API. Gardé derrière un flag dédié plutôt que is_development/is_test
        # - ENVIRONMENT vaut "development" par défaut (settings.py, compose
        # files), donc lier ce bypass à l'environnement l'active partout où
        # quelqu'un a simplement oublié de positionner ENVIRONMENT=production.
        if settings.security.allow_dev_tenant_header:
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

        # La clé seule ne suffit plus : la requête doit être signée avec le
        # secret HMAC du tenant (voir app/core/security/api_key_security.py).
        # Sans ça, une clé API qui fuite serait rejouable indéfiniment par
        # quiconque l'intercepte - exactement le problème que ce système
        # existe pour fermer.
        sig_error = await self._verify_signature(request, tenant)
        if sig_error:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"error": "unauthorized", "message": sig_error},
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
                hmac_secret = self._decrypt_tenant_secret(tenant)
                tenant_data = {
                    "id": str(tenant.id),
                    "plan": plan_name,
                    "features": plan["features"],
                    "rate_limit_rpm": plan["rate_limit_rpm"],
                    "is_active": tenant.is_active,
                    "hmac_secret": hmac_secret,
                    "hmac_secret_undecryptable": bool(tenant.hmac_secret_encrypted)
                    and hmac_secret is None,
                }

                # Mise en cache: pas de TTL, invalidée explicitement par
                # clear_cache() sur rotation/révocation de clé (voir
                # rotate_api_key dans tenants.py) - sans quoi une clé
                # révoquée continuerait à authentifier indéfiniment.
                self._tenant_cache[api_key_hash] = tenant_data

                return tenant_data

        return None

    @staticmethod
    def _decrypt_tenant_secret(tenant) -> Optional[str]:
        """
        Déchiffre le secret HMAC du tenant. Une clé de chiffrement erronée ou
        changée ne doit pas faire remonter une exception hors du middleware
        (500 générique sans indice pour l'opérateur): on journalise l'erreur et
        le tenant est refusé en 401.
        """
        if not tenant.hmac_secret_encrypted:
            return None
        try:
            return decrypt_secret(tenant.hmac_secret_encrypted)
        except ValueError:
            logger.error(
                "Cannot decrypt tenant HMAC secret - check SECURITY_API_KEY_ENCRYPTION_KEY "
                "(was it changed?)",
                extra={"tenant_id": str(tenant.id)},
            )
            return None

    async def _verify_signature(self, request: Request, tenant: dict) -> Optional[str]:
        """
        Vérifie la signature HMAC de la requête (voir
        app/core/security/api_key_security.py et APIClientSigner côté client).

        Returns:
            Un message d'erreur si la requête doit être rejetée, sinon None.
        """
        secret = tenant.get("hmac_secret")
        if not secret and tenant.get("hmac_secret_undecryptable"):
            # Détail réservé aux logs (voir _decrypt_tenant_secret): la réponse ne
            # révèle rien sur la configuration du chiffrement.
            return "Request signature could not be verified"
        if not secret:
            # Ne devrait pas arriver pour une clé émise après l'ajout de ce
            # système (activate_and_issue_api_key / rotate_api_key génèrent
            # toujours les deux ensemble) - mais pas de fallback silencieux
            # qui réintroduirait le problème que ça résout.
            logger.error(
                "Tenant has an API key but no HMAC secret configured",
                extra={"tenant_id": tenant["id"]},
            )
            return "API key has no signing secret configured - rotate your API key"

        timestamp_header = request.headers.get("X-Timestamp")
        signature_header = request.headers.get("X-Signature")

        if not timestamp_header or not signature_header:
            return "Request signature required (X-Timestamp, X-Signature headers)"

        try:
            timestamp = int(timestamp_header)
        except ValueError:
            return "Invalid X-Timestamp header"

        # BaseHTTPMiddleware caches the body on first read (Request._body),
        # so call_next() downstream still sees the full body afterwards.
        body = await request.body()

        is_valid, error = _signature_validator.validate_signature(
            provided_signature=signature_header,
            secret=secret,
            timestamp=timestamp,
            method=request.method,
            path=self._request_target(request),
            body=body or None,
        )

        if not is_valid:
            logger.warning(
                "Invalid request signature",
                extra={"tenant_id": tenant["id"], "path": request.url.path, "reason": error},
            )
            return error

        return None

    @staticmethod
    def _request_target(request: Request) -> str:
        """Chemin + query string: c'est ce que le client signe."""
        query = request.url.query
        return f"{request.url.path}?{query}" if query else request.url.path

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
