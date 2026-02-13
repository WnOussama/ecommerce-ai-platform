"""
Database Infrastructure - PostgreSQL + SQLAlchemy

Ce module exporte les composants essentiels pour la base de données.

Usage recommandé (UnitOfWork):
    from app.infrastructure.database import UnitOfWork

    async with UnitOfWork(tenant_id) as uow:
        conversation = await uow.conversations.get_or_create(user_id)
        message = await uow.messages.create_if_not_exists(...)
        await uow.commit()
"""

from app.infrastructure.database.base import Base
from app.infrastructure.database.connection import (
    async_engine,
    sync_engine,
    AsyncSessionLocal,
    SyncSessionLocal,
    get_async_session,
    get_sync_session,
    get_db_context,
    check_database_connection,
    init_database,
    close_database,
)
from app.infrastructure.database.unit_of_work import UnitOfWork

__all__ = [
    # Base
    "Base",

    # Connection
    "async_engine",
    "sync_engine",
    "AsyncSessionLocal",
    "SyncSessionLocal",
    "get_async_session",
    "get_sync_session",
    "get_db_context",
    "check_database_connection",
    "init_database",
    "close_database",

    # Unit of Work (recommended)
    "UnitOfWork",
]
