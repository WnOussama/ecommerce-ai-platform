"""
Security Module - API Key, Rate Limiting, Admin AI Safety

Architecture:
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          SECURITY LAYERS                                         │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  1. API KEY SECURITY                                                             │
│     • HMAC signature validation                                                  │
│     • Expiration & rotation                                                      │
│     • IP whitelist                                                               │
│                                                                                  │
│  2. RATE LIMITING                                                                │
│     • Per IP (global DDoS protection)                                            │
│     • Per tenant (plan-based)                                                    │
│     • Per endpoint                                                               │
│     • Burst allowance                                                            │
│                                                                                  │
│  3. ADMIN AI SAFETY                                                              │
│     • Dry run preview                                                            │
│     • Double confirmation (HIGH risk)                                            │
│     • Human approval (CRITICAL risk)                                             │
│     • Rollback support                                                           │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
"""

# API Key Security
from app.core.security.api_key_security import (
    APIKeyType,
    APIKeyStatus,
    APIKeyConfig,
    APIKey,
    APIKeyWithSecret,
    APIKeyGenerator,
    HMACSignatureValidator,
    APIKeyManager,
    APIClientSigner,
)

# Rate Limiting
from app.core.security.rate_limiter import (
    RateLimitTier,
    RateLimitRule,
    RateLimitConfig,
    RateLimitResult,
    SlidingWindowCounter,
    AdvancedRateLimiter,
    RateLimitMiddleware,
)

# Admin AI Safety
from app.core.security.admin_safety import (
    RiskLevel,
    ActionStatus,
    ApprovalType,
    ActionDefinition,
    DryRunResult,
    PendingAction,
    ACTION_DEFINITIONS,
    AdminAISafetySystem,
)

# Guardrails (existing)
from app.core.security.guardrails import *

# Tenant Validation (new production-grade version)
from app.core.security.tenant_validation import (
    TenantValidationError,
    TenantIDValidator,
    validate_tenant_id_dependency,
    extract_and_validate_tenant_id,
    VALID_TENANT_PATTERN,
)

__all__ = [
    # API Key Security
    "APIKeyType",
    "APIKeyStatus",
    "APIKeyConfig",
    "APIKey",
    "APIKeyWithSecret",
    "APIKeyGenerator",
    "HMACSignatureValidator",
    "APIKeyManager",
    "APIClientSigner",

    # Rate Limiting
    "RateLimitTier",
    "RateLimitRule",
    "RateLimitConfig",
    "RateLimitResult",
    "SlidingWindowCounter",
    "AdvancedRateLimiter",
    "RateLimitMiddleware",

    # Admin AI Safety
    "RiskLevel",
    "ActionStatus",
    "ApprovalType",
    "ActionDefinition",
    "DryRunResult",
    "PendingAction",
    "ACTION_DEFINITIONS",
    "AdminAISafetySystem",

    # Tenant Validation
    "TenantValidationError",
    "TenantIDValidator",
    "validate_tenant_id_dependency",
    "extract_and_validate_tenant_id",
    "VALID_TENANT_PATTERN",
]


