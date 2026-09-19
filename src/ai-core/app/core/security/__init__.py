"""
Security Module - API Key, Rate Limiting, Admin AI Safety

Architecture:
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          SECURITY LAYERS                                         │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  1. API KEY SECURITY                                                             │
│     • HMAC request signing, verified in TenantContextMiddleware                  │
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
# Admin AI Safety
from app.core.security.admin_safety import (
    ACTION_DEFINITIONS,
    ActionDefinition,
    ActionStatus,
    AdminAISafetySystem,
    ApprovalType,
    DryRunResult,
    PendingAction,
    RiskLevel,
)
from app.core.security.api_key_security import (
    APIClientSigner,
    APIKeyConfig,
    HMACSignatureValidator,
)

# Guardrails (existing)
from app.core.security.guardrails import *  # noqa: F403

# Rate Limiting
from app.core.security.rate_limiter import (
    AdvancedRateLimiter,
    RateLimitConfig,
    RateLimitMiddleware,
    RateLimitResult,
    RateLimitRule,
    RateLimitTier,
    SlidingWindowCounter,
)

# Tenant Validation (new production-grade version)
from app.core.security.tenant_validation import (
    VALID_TENANT_PATTERN,
    TenantIDValidator,
    TenantValidationError,
    extract_and_validate_tenant_id,
    validate_tenant_id_dependency,
)

__all__ = [
    # API Key Security
    "APIKeyConfig",
    "HMACSignatureValidator",
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
