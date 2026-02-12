"""
Tenant ID Validation - Validation stricte des identifiants tenant

Sécurité:
- Format strict: tenant_[a-z0-9]{8,32}
- Protection contre injection SQL
- Protection contre path traversal
- Protection contre header injection
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class TenantValidationError(Exception):
    """Exception levée quand un tenant_id est invalide"""

    def __init__(self, message: str, tenant_id: Optional[str] = None):
        self.message = message
        self.tenant_id = tenant_id
        super().__init__(message)


class TenantIDValidator:
    """
    Validateur strict de tenant_id.

    Règles:
    - Format: tenant_[a-z0-9]{8,32}
    - Pas de caractères spéciaux
    - Pas d'injection SQL
    - Pas d'injection de path

    Usage:
        try:
            validated_id = TenantIDValidator.validate(tenant_id)
        except TenantValidationError as e:
            raise HTTPException(400, str(e))
    """

    # Pattern strict pour tenant_id
    # Format: tenant_ suivi de 8 à 32 caractères alphanumériques minuscules
    VALID_PATTERN = re.compile(r'^tenant_[a-z0-9]{8,32}$')

    # Longueur maximale (protection DoS)
    MAX_LENGTH = 50

    # Patterns dangereux à bloquer
    INJECTION_PATTERNS = [
        (r"['\";]", "SQL injection characters"),
        (r"\.\.", "Path traversal"),
        (r"<[^>]*>", "HTML/XML injection"),
        (r"\$\{", "Template injection"),
        (r"\{\{", "Template injection"),
        (r"--", "SQL comment"),
        (r"/\*", "SQL comment"),
        (r"\\x[0-9a-fA-F]", "Hex encoding"),
        (r"%[0-9a-fA-F]{2}", "URL encoding"),
        (r"[\r\n]", "Header injection"),
        (r"[\x00-\x1f]", "Control characters"),
    ]

    @classmethod
    def validate(cls, tenant_id: Optional[str]) -> str:
        """
        Valide et retourne le tenant_id, ou lève une exception.

        Args:
            tenant_id: ID du tenant à valider

        Returns:
            tenant_id validé (identique à l'entrée si valide)

        Raises:
            TenantValidationError: Si le tenant_id est invalide
        """
        # Vérifier que la valeur existe
        if tenant_id is None:
            raise TenantValidationError(
                "X-Tenant-ID header is required",
                tenant_id=None
            )

        if not isinstance(tenant_id, str):
            raise TenantValidationError(
                "X-Tenant-ID must be a string",
                tenant_id=str(tenant_id)[:20]
            )

        if not tenant_id.strip():
            raise TenantValidationError(
                "X-Tenant-ID cannot be empty",
                tenant_id=""
            )

        # Vérifier la longueur d'abord (évite DoS avec très long string)
        if len(tenant_id) > cls.MAX_LENGTH:
            raise TenantValidationError(
                f"X-Tenant-ID too long (max {cls.MAX_LENGTH} characters)",
                tenant_id=tenant_id[:20] + "..."
            )

        # Vérifier les patterns d'injection AVANT le format
        # Cela garantit que même un tenant_id malformé mais dangereux est loggé
        for pattern, description in cls.INJECTION_PATTERNS:
            if re.search(pattern, tenant_id):
                # Log l'tentative d'injection (sans le contenu complet)
                logger.warning(
                    "Potential injection attempt detected in tenant_id",
                    extra={
                        "pattern_type": description,
                        "tenant_id_prefix": tenant_id[:10] if tenant_id else None,
                    }
                )
                raise TenantValidationError(
                    f"X-Tenant-ID contains forbidden characters: {description}",
                    tenant_id=tenant_id[:20] + "..." if len(tenant_id) > 20 else tenant_id
                )

        # Vérifier le format
        if not cls.VALID_PATTERN.match(tenant_id):
            raise TenantValidationError(
                "Invalid X-Tenant-ID format. Expected: tenant_[a-z0-9]{8,32}",
                tenant_id=tenant_id[:20] + "..." if len(tenant_id) > 20 else tenant_id
            )

        return tenant_id

    @classmethod
    def is_valid(cls, tenant_id: Optional[str]) -> bool:
        """
        Vérifie si un tenant_id est valide sans lever d'exception.

        Args:
            tenant_id: ID du tenant à vérifier

        Returns:
            True si valide, False sinon
        """
        try:
            cls.validate(tenant_id)
            return True
        except TenantValidationError:
            return False

    @classmethod
    def sanitize_for_logging(cls, tenant_id: Optional[str]) -> str:
        """
        Retourne une version safe du tenant_id pour le logging.

        Args:
            tenant_id: ID du tenant

        Returns:
            Version tronquée et sécurisée pour les logs
        """
        if tenant_id is None:
            return "<none>"

        if not isinstance(tenant_id, str):
            return "<invalid_type>"

        # Tronquer et remplacer les caractères non-alphanum
        safe = re.sub(r'[^a-zA-Z0-9_]', '?', tenant_id[:30])

        if len(tenant_id) > 30:
            safe += "..."

        return safe


# =============================================================================
# FASTAPI DEPENDENCY
# =============================================================================

async def get_validated_tenant_id(request) -> str:
    """
    FastAPI Dependency pour extraire et valider le tenant_id.

    Usage:
        @router.get("/resource")
        async def get_resource(tenant_id: str = Depends(get_validated_tenant_id)):
            ...
    """
    from fastapi import HTTPException, Request

    tenant_id = request.headers.get("X-Tenant-ID")

    try:
        return TenantIDValidator.validate(tenant_id)
    except TenantValidationError as e:
        logger.warning(
            "Tenant validation failed",
            extra={
                "error": e.message,
                "tenant_id_safe": TenantIDValidator.sanitize_for_logging(tenant_id),
                "path": request.url.path,
                "method": request.method,
            }
        )
        raise HTTPException(status_code=400, detail=e.message)


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    "TenantValidationError",
    "TenantIDValidator",
    "get_validated_tenant_id",
]

