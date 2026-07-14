"""
Security Settings - Configuration sécurisée avec validation stricte

IMPORTANT: L'application ÉCHOUE AU BOOT si les secrets ne sont pas valides.
Aucune valeur par défaut pour les secrets critiques.
"""

import logging
import os
import sys
from typing import List, Optional, Set

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class SecretValidationError(Exception):
    """Erreur levée quand un secret est invalide ou manquant"""

    pass


class StrictSecuritySettings(BaseSettings):
    """
    Configuration sécurité STRICTE - Production Grade

    TOUTES les valeurs sensibles DOIVENT être définies via variables d'environnement.
    L'application refuse de démarrer si:
    - Un secret requis est manquant
    - Un secret est trop court
    - Un secret a une valeur par défaut connue (changeme, secret, etc.)
    """

    model_config = SettingsConfigDict(
        env_prefix="SECURITY_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # =========================================================================
    # SECRETS CRITIQUES (AUCUNE VALEUR PAR DÉFAUT)
    # =========================================================================

    # JWT - OBLIGATOIRE
    jwt_secret_key: str = Field(
        ...,  # Required, no default
        description='JWT signing secret. Generate with: python -c "import secrets; print(secrets.token_hex(32))"',
    )

    # API Key Encryption - OBLIGATOIRE
    api_key_encryption_key: str = Field(
        ...,  # Required, no default
        description="Key for encrypting API keys at rest",
    )

    # Database encryption key - OBLIGATOIRE en production
    db_encryption_key: Optional[str] = Field(
        default=None, description="Key for encrypting sensitive data in DB"
    )

    # =========================================================================
    # CONFIGURATION JWT
    # =========================================================================

    jwt_algorithm: str = Field(default="HS256", pattern="^(HS256|HS384|HS512|RS256|RS384|RS512)$")
    jwt_expiration_hours: int = Field(default=24, ge=1, le=168)  # Max 1 week
    jwt_refresh_expiration_days: int = Field(default=7, ge=1, le=30)

    # =========================================================================
    # API KEYS
    # =========================================================================

    api_key_prefix: str = Field(default="sk_live_", pattern="^[a-z_]+$")
    api_key_min_length: int = Field(default=32, ge=24)
    api_key_max_age_days: int = Field(default=365, ge=30, le=730)

    # =========================================================================
    # RATE LIMITING
    # =========================================================================

    rate_limit_enabled: bool = True
    rate_limit_requests_per_minute: int = Field(default=60, ge=10, le=1000)
    rate_limit_burst_multiplier: float = Field(default=1.5, ge=1.0, le=3.0)

    # =========================================================================
    # CORS
    # =========================================================================

    cors_origins: List[str] = Field(default_factory=list)
    cors_allow_credentials: bool = True
    cors_max_age: int = Field(default=600, ge=0, le=86400)

    # =========================================================================
    # CONTENT SECURITY
    # =========================================================================

    max_request_size_mb: int = Field(default=10, ge=1, le=100)
    allowed_content_types: List[str] = Field(default=["application/json", "multipart/form-data"])

    # =========================================================================
    # AI SECURITY
    # =========================================================================

    prompt_injection_detection_enabled: bool = True
    output_guardrails_enabled: bool = True
    admin_action_confirmation_required: bool = True
    admin_action_audit_enabled: bool = True
    max_llm_tokens_per_request: int = Field(default=4096, ge=100, le=128000)

    # =========================================================================
    # MULTI-TENANT SECURITY
    # =========================================================================

    tenant_id_header: str = "X-Tenant-ID"
    tenant_id_strict_validation: bool = True
    tenant_isolation_enforced: bool = True

    # =========================================================================
    # VALEURS INTERDITES (blacklist)
    # =========================================================================

    _FORBIDDEN_SECRET_VALUES: Set[str] = {
        "changeme",
        "change_me",
        "change-me",
        "secret",
        "mysecret",
        "my_secret",
        "my-secret",
        "password",
        "mypassword",
        "my_password",
        "test",
        "testing",
        "dev",
        "development",
        "xxx",
        "yyy",
        "zzz",
        "abc",
        "123",
        "please_change_me",
        "replace_me",
        "your_secret_here",
        "your-secret-here",
        "example",
        "sample",
        "demo",
    }

    # =========================================================================
    # VALIDATORS
    # =========================================================================

    @field_validator("jwt_secret_key", "api_key_encryption_key", mode="before")
    @classmethod
    def validate_required_secrets(cls, v: Optional[str], info) -> str:
        """Valide que les secrets requis sont définis et valides"""
        field_name = info.field_name

        # Vérifier que la valeur existe
        if not v:
            raise SecretValidationError(
                f"{field_name} is REQUIRED. "
                f"Set it via environment variable SECURITY_{field_name.upper()}. "
                f'Generate with: python -c "import secrets; print(secrets.token_hex(32))"'
            )

        # Vérifier la longueur minimale
        if len(v) < 32:
            raise SecretValidationError(
                f"{field_name} must be at least 32 characters. Current length: {len(v)}"
            )

        # Vérifier que ce n'est pas une valeur interdite
        v_lower = v.lower().strip()
        for forbidden in cls._FORBIDDEN_SECRET_VALUES:
            if forbidden in v_lower:
                raise SecretValidationError(
                    f"{field_name} contains forbidden value '{forbidden}'. "
                    f"Please use a secure random value."
                )

        return v

    @field_validator("db_encryption_key", mode="before")
    @classmethod
    def validate_optional_secret(cls, v: Optional[str], info) -> Optional[str]:
        """Valide les secrets optionnels s'ils sont fournis"""
        if v is None:
            return None

        field_name = info.field_name

        if len(v) < 32:
            raise SecretValidationError(f"{field_name} must be at least 32 characters if provided")

        v_lower = v.lower().strip()
        for forbidden in cls._FORBIDDEN_SECRET_VALUES:
            if forbidden in v_lower:
                raise SecretValidationError(f"{field_name} contains forbidden value '{forbidden}'")

        return v

    @field_validator("cors_origins", mode="before")
    @classmethod
    def validate_cors_origins(cls, v):
        """Parse CORS origins depuis string ou list"""
        if isinstance(v, str):
            # Support pour "origin1,origin2" format
            return [o.strip() for o in v.split(",") if o.strip()]
        return v or []

    @model_validator(mode="after")
    def validate_production_requirements(self) -> "StrictSecuritySettings":
        """Validations supplémentaires pour la production"""
        env = os.getenv("ENVIRONMENT", "development").lower()

        if env == "production":
            # En production, db_encryption_key est obligatoire
            if not self.db_encryption_key:
                raise SecretValidationError(
                    "db_encryption_key is REQUIRED in production environment"
                )

            # En production, CORS doit être configuré explicitement
            if not self.cors_origins:
                logger.warning(
                    "CORS origins not configured in production. "
                    "All cross-origin requests will be blocked."
                )

            # Vérifier que les guardrails sont activés
            if not self.prompt_injection_detection_enabled:
                raise SecretValidationError(
                    "prompt_injection_detection must be enabled in production"
                )

            if not self.output_guardrails_enabled:
                raise SecretValidationError("output_guardrails must be enabled in production")

        return self


def validate_security_settings() -> StrictSecuritySettings:
    """
    Charge et valide les settings de sécurité.

    IMPORTANT: Cette fonction fait ÉCHOUER l'application si les secrets
    ne sont pas valides. Appelée au démarrage de l'application.

    Returns:
        StrictSecuritySettings validés

    Raises:
        SecretValidationError: Si un secret est invalide
        SystemExit: L'application s'arrête si la validation échoue
    """
    try:
        settings = StrictSecuritySettings()
        logger.info(
            "Security settings validated successfully",
            extra={
                "jwt_algorithm": settings.jwt_algorithm,
                "rate_limit_enabled": settings.rate_limit_enabled,
                "prompt_injection_detection": settings.prompt_injection_detection_enabled,
                "tenant_isolation": settings.tenant_isolation_enforced,
            },
        )
        return settings

    except SecretValidationError as e:
        logger.critical(
            f"SECURITY CONFIGURATION ERROR: {e}", extra={"error_type": "security_config_validation"}
        )
        print(f"\n{'=' * 60}", file=sys.stderr)
        print("FATAL: Security configuration error", file=sys.stderr)
        print(f"{'=' * 60}", file=sys.stderr)
        print(f"\n{e}\n", file=sys.stderr)
        print("The application cannot start with invalid security settings.", file=sys.stderr)
        print(f"{'=' * 60}\n", file=sys.stderr)
        sys.exit(1)

    except Exception as e:
        logger.critical(
            f"Unexpected error loading security settings: {e}",
            extra={"error_type": "security_config_error"},
        )
        sys.exit(1)


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    "StrictSecuritySettings",
    "SecretValidationError",
    "validate_security_settings",
]
