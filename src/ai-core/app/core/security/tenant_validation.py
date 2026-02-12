"""
Tenant ID Validation - Version renforcée Production-Grade

Sécurité:
- Format strict: tenant_[a-z0-9]{8,32} avec entropie minimale
- Blacklist de valeurs triviales
- Normalisation des headers
- Protection contre toutes les injections
- Validation AVANT toute requête DB

IMPORTANT: Ce module n'importe RIEN depuis d'autres modules app.*
pour éviter les imports circulaires.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from typing import Optional, Set, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# EXCEPTIONS
# =============================================================================

class TenantValidationError(Exception):
    """Exception levée quand un tenant_id est invalide"""

    def __init__(
        self,
        message: str,
        tenant_id: Optional[str] = None,
        error_code: str = "INVALID_TENANT",
    ):
        self.message = message
        self.tenant_id = tenant_id
        self.error_code = error_code
        super().__init__(message)


# =============================================================================
# CONSTANTS
# =============================================================================

# Valeurs triviales interdites (après le préfixe tenant_)
TRIVIAL_VALUES: Set[str] = frozenset({
    # Séquences répétitives
    "00000000", "11111111", "22222222", "33333333",
    "44444444", "55555555", "66666666", "77777777",
    "88888888", "99999999", "aaaaaaaa", "bbbbbbbb",
    "cccccccc", "dddddddd", "eeeeeeee", "ffffffff",
    "abcdefgh", "12345678", "87654321",

    # Mots communs
    "testtest", "devdevdev", "prodprod",
    "demodemod", "samplesam", "exampleex",
    "adminadmi", "useruser", "guestgues",

    # Patterns simples
    "abcd1234", "1234abcd", "test1234", "demo1234",
    "a1b2c3d4", "0a0b0c0d",
})

# Patterns d'injection à bloquer (compilés pour performance)
INJECTION_PATTERNS: Tuple[Tuple[re.Pattern, str], ...] = (
    (re.compile(r"['\";]"), "SQL_INJECTION"),
    (re.compile(r"\.\."), "PATH_TRAVERSAL"),
    (re.compile(r"<[^>]*>"), "HTML_INJECTION"),
    (re.compile(r"\$\{"), "TEMPLATE_INJECTION"),
    (re.compile(r"\{\{"), "TEMPLATE_INJECTION"),
    (re.compile(r"--"), "SQL_COMMENT"),
    (re.compile(r"/\*"), "SQL_COMMENT"),
    (re.compile(r"\\x[0-9a-fA-F]"), "HEX_ENCODING"),
    (re.compile(r"%[0-9a-fA-F]{2}"), "URL_ENCODING"),
    (re.compile(r"[\r\n]"), "HEADER_INJECTION"),
    (re.compile(r"[\x00-\x1f\x7f]"), "CONTROL_CHARS"),
    (re.compile(r"\\[nrtbfv0]"), "ESCAPE_SEQUENCE"),
)

# Pattern de validation principal
VALID_TENANT_PATTERN = re.compile(r'^tenant_[a-z0-9]{8,32}$')

# Limites
MAX_TENANT_ID_LENGTH = 50
MIN_SUFFIX_LENGTH = 8
MAX_SUFFIX_LENGTH = 32
PREFIX = "tenant_"


# =============================================================================
# VALIDATOR
# =============================================================================

class TenantIDValidator:
    """
    Validateur strict et thread-safe de tenant_id.

    Toutes les méthodes sont statiques ou de classe pour éviter
    tout état mutable et garantir la thread-safety.

    Règles de validation:
    1. Header normalisé (strip, pas de caractères invisibles)
    2. Longueur dans les limites
    3. Pas de patterns d'injection
    4. Format exact: tenant_[a-z0-9]{8,32}
    5. Pas de valeur triviale (entropie minimale)

    Usage:
        try:
            validated_id = TenantIDValidator.validate(tenant_id)
        except TenantValidationError as e:
            raise HTTPException(400, detail=e.message)
    """

    @classmethod
    def normalize_header(cls, value: Optional[str]) -> Optional[str]:
        """
        Normalise une valeur de header HTTP.

        - Strip des espaces
        - Suppression des caractères invisibles Unicode
        - Normalisation Unicode NFKC

        Args:
            value: Valeur brute du header

        Returns:
            Valeur normalisée ou None si invalide
        """
        if value is None:
            return None

        if not isinstance(value, str):
            return None

        # Normaliser Unicode (NFKC pour compatibilité maximale)
        try:
            normalized = unicodedata.normalize('NFKC', value)
        except (TypeError, ValueError):
            return None

        # Strip et supprimer les caractères invisibles
        normalized = normalized.strip()

        # Supprimer les caractères de contrôle et invisibles
        normalized = ''.join(
            char for char in normalized
            if unicodedata.category(char) not in ('Cc', 'Cf', 'Co', 'Cs')
        )

        return normalized if normalized else None

    @classmethod
    def validate(cls, tenant_id: Optional[str]) -> str:
        """
        Valide et retourne le tenant_id normalisé.

        Ordre des validations (du moins coûteux au plus coûteux):
        1. Existence et type
        2. Normalisation header
        3. Longueur
        4. Patterns d'injection
        5. Format regex
        6. Valeurs triviales

        Args:
            tenant_id: ID du tenant à valider

        Returns:
            tenant_id validé et normalisé

        Raises:
            TenantValidationError: Si le tenant_id est invalide
        """
        # 1. Vérifier existence
        if tenant_id is None:
            raise TenantValidationError(
                message="X-Tenant-ID header is required",
                tenant_id=None,
                error_code="MISSING_TENANT_ID",
            )

        if not isinstance(tenant_id, str):
            raise TenantValidationError(
                message="X-Tenant-ID must be a string",
                tenant_id=str(tenant_id)[:20],
                error_code="INVALID_TYPE",
            )

        # 2. Normaliser le header
        normalized = cls.normalize_header(tenant_id)

        if not normalized:
            raise TenantValidationError(
                message="X-Tenant-ID cannot be empty",
                tenant_id="<empty>",
                error_code="EMPTY_TENANT_ID",
            )

        # 3. Vérifier la longueur (protection DoS)
        if len(normalized) > MAX_TENANT_ID_LENGTH:
            raise TenantValidationError(
                message=f"X-Tenant-ID too long (max {MAX_TENANT_ID_LENGTH} characters)",
                tenant_id=normalized[:20] + "...",
                error_code="TENANT_ID_TOO_LONG",
            )

        # 4. Vérifier les patterns d'injection AVANT le format
        injection_type = cls._detect_injection(normalized)
        if injection_type:
            # Log l'tentative (sans exposer le contenu complet)
            logger.warning(
                "Injection attempt detected in tenant_id",
                extra={
                    "injection_type": injection_type,
                    "tenant_id_prefix": normalized[:8] if normalized else None,
                    "tenant_id_length": len(normalized) if normalized else 0,
                }
            )
            raise TenantValidationError(
                message=f"X-Tenant-ID contains forbidden characters",
                tenant_id=cls.sanitize_for_logging(normalized),
                error_code=f"INJECTION_{injection_type}",
            )

        # 5. Vérifier le format
        if not VALID_TENANT_PATTERN.match(normalized):
            raise TenantValidationError(
                message="Invalid X-Tenant-ID format. Expected: tenant_[a-z0-9]{8,32}",
                tenant_id=cls.sanitize_for_logging(normalized),
                error_code="INVALID_FORMAT",
            )

        # 6. Vérifier les valeurs triviales
        suffix = normalized[len(PREFIX):]
        if cls._is_trivial(suffix):
            logger.warning(
                "Trivial tenant_id rejected",
                extra={"tenant_id_safe": cls.sanitize_for_logging(normalized)}
            )
            raise TenantValidationError(
                message="X-Tenant-ID value is too simple/predictable",
                tenant_id=cls.sanitize_for_logging(normalized),
                error_code="TRIVIAL_VALUE",
            )

        return normalized

    @classmethod
    def _detect_injection(cls, value: str) -> Optional[str]:
        """
        Détecte les tentatives d'injection.

        Returns:
            Type d'injection détecté ou None
        """
        for pattern, injection_type in INJECTION_PATTERNS:
            if pattern.search(value):
                return injection_type
        return None

    @classmethod
    def _is_trivial(cls, suffix: str) -> bool:
        """
        Vérifie si le suffixe est trivial (faible entropie).

        Critères:
        - Dans la blacklist
        - Tous les caractères identiques
        - Pattern répétitif (abc abc abc)
        """
        # Blacklist exacte
        if suffix in TRIVIAL_VALUES:
            return True

        # Tous caractères identiques
        if len(set(suffix)) == 1:
            return True

        # Très faible diversité (max 2 caractères uniques)
        if len(suffix) >= 8 and len(set(suffix)) <= 2:
            return True

        # Pattern répétitif (ex: "abcabc", "xyzxyz")
        for repeat_len in range(2, len(suffix) // 2 + 1):
            pattern = suffix[:repeat_len]
            if pattern * (len(suffix) // len(pattern)) == suffix[:len(pattern) * (len(suffix) // len(pattern))]:
                if len(suffix) / repeat_len >= 2:  # Au moins 2 répétitions
                    return True

        return False

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
        Ne jamais logger un tenant_id non sanitizé!

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
# FASTAPI INTEGRATION
# =============================================================================

async def validate_tenant_id_dependency(request) -> str:
    """
    FastAPI Dependency pour extraire et valider le tenant_id.

    Cette fonction est une coroutine pour compatibilité FastAPI,
    mais la validation elle-même est synchrone (CPU-bound, rapide).

    Usage:
        from app.core.security.tenant_validation import validate_tenant_id_dependency

        @router.get("/resource")
        async def get_resource(
            tenant_id: str = Depends(validate_tenant_id_dependency)
        ):
            # tenant_id est garanti valide ici
            ...

    Raises:
        HTTPException 400 si le tenant_id est invalide
    """
    # Import local pour éviter dépendance au niveau module
    from fastapi import HTTPException

    # Extraire le header (case-insensitive dans HTTP)
    raw_tenant_id = request.headers.get("X-Tenant-ID")

    try:
        return TenantIDValidator.validate(raw_tenant_id)
    except TenantValidationError as e:
        logger.warning(
            "Tenant validation failed",
            extra={
                "error_code": e.error_code,
                "error_message": e.message,
                "tenant_id_safe": TenantIDValidator.sanitize_for_logging(raw_tenant_id),
                "path": str(request.url.path),
                "method": request.method,
                "client_ip": request.client.host if request.client else "unknown",
            }
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_tenant_id",
                "code": e.error_code,
                "message": e.message,
            }
        )


# =============================================================================
# MIDDLEWARE HELPER
# =============================================================================

def extract_and_validate_tenant_id(headers: dict) -> Tuple[Optional[str], Optional[TenantValidationError]]:
    """
    Extrait et valide le tenant_id depuis les headers.
    Utile pour les middlewares qui ne peuvent pas lever d'exception.

    Args:
        headers: Dict des headers HTTP

    Returns:
        Tuple (tenant_id validé ou None, erreur ou None)
    """
    # Headers HTTP sont case-insensitive
    raw_tenant_id = headers.get("X-Tenant-ID") or headers.get("x-tenant-id")

    try:
        validated = TenantIDValidator.validate(raw_tenant_id)
        return validated, None
    except TenantValidationError as e:
        return None, e


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    "TenantValidationError",
    "TenantIDValidator",
    "validate_tenant_id_dependency",
    "extract_and_validate_tenant_id",
    "VALID_TENANT_PATTERN",
]

