"""
Multi-Tenant Aware Repositories

Architecture:
- UnitOfWork: Gestion transactionnelle avec commit/rollback (RECOMMANDÉ)
- TenantAwareRepository: Base class imposant tenant_id sur TOUTES les queries

Usage recommandé (UnitOfWork):
    from app.infrastructure.database import UnitOfWork

    async with UnitOfWork(tenant_id) as uow:
        conversation = await uow.conversations.get_or_create(user_identifier)
        message = await uow.messages.create_if_not_exists(...)
        await uow.commit()
"""

from app.infrastructure.database.repositories.base import (
    CrossTenantAccessError,
    TenantAwareRepository,
    TenantContext,
    TenantIdMissingError,
    TenantIsolationError,
)

# Nouveaux repositories (nouveau style avec tenant_id direct)
from app.infrastructure.database.repositories.conversation_repo import (
    ConversationRepository,
)
from app.infrastructure.database.repositories.message_repo import (
    DuplicateMessageError,
    MessageRepository,
)

__all__ = [
    # Base
    "TenantAwareRepository",
    "TenantContext",
    "TenantIdMissingError",
    "CrossTenantAccessError",
    "TenantIsolationError",
    # New repositories (recommended)
    "ConversationRepository",
    "MessageRepository",
    "DuplicateMessageError",
]
