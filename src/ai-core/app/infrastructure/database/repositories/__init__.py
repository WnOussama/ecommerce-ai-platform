"""
Multi-Tenant Aware Repositories

Architecture:
- TenantAwareRepository: Base class imposant tenant_id sur TOUTES les queries
- Repositories concrets: CustomerRepository, ConversationRepository, etc.

Usage:
    from app.infrastructure.database.repositories import (
        TenantContext,
        CustomerRepository,
        RepositoryFactory,
    )

    ctx = TenantContext(tenant_id="xxx")
    repo = CustomerRepository(session, ctx)
    customers = await repo.get_all()  # Auto-filtré par tenant
"""

from app.infrastructure.database.repositories.base import (
    TenantAwareRepository,
    TenantContext,
    TenantIdMissingError,
    CrossTenantAccessError,
    TenantIsolationError,
    RepositoryFactory,
    TenantQueryValidator,
)

from app.infrastructure.database.repositories.repositories import (
    CustomerRepository,
    ConversationRepository,
    MessageRepository,
    CouponRepository,
    AdminActionRepository,
)

__all__ = [
    # Base
    "TenantAwareRepository",
    "TenantContext",
    "TenantIdMissingError",
    "CrossTenantAccessError",
    "TenantIsolationError",
    "RepositoryFactory",
    "TenantQueryValidator",

    # Repositories
    "CustomerRepository",
    "ConversationRepository",
    "MessageRepository",
    "CouponRepository",
    "AdminActionRepository",
]

