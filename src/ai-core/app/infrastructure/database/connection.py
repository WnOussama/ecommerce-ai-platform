"""
Database Connection - Engine et Session Factory

Ce module gère la connexion à PostgreSQL avec SQLAlchemy.
Supporte les opérations async (asyncpg) et sync (psycopg2).

Architecture: FastAPI est le SEUL owner de cette base de données.
Laravel communique via REST API uniquement.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config.settings import settings

logger = logging.getLogger(__name__)

# =============================================================================
# CONSTANTS
# =============================================================================

HEALTH_CHECK_TIMEOUT_SECONDS = 5.0

# =============================================================================
# ASYNC ENGINE (pour l'application FastAPI)
# =============================================================================

async_engine = create_async_engine(
    settings.database.url,
    echo=settings.database.echo,
    pool_size=settings.database.pool_size,
    max_overflow=settings.database.max_overflow,
    pool_recycle=settings.database.pool_recycle,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


# =============================================================================
# SYNC ENGINE (pour Alembic migrations)
# =============================================================================

sync_engine = create_engine(
    settings.database.sync_url,
    echo=settings.database.echo,
    pool_size=5,
    max_overflow=10,
    pool_recycle=settings.database.pool_recycle,
    pool_pre_ping=True,
)

SyncSessionLocal = sessionmaker(
    bind=sync_engine,
    autocommit=False,
    autoflush=False,
)


# =============================================================================
# DEPENDENCY INJECTION
# =============================================================================


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency pour injecter une session async dans les endpoints FastAPI.

    IMPORTANT: Pas d'auto-commit. Le caller doit explicitement appeler
    session.commit() quand nécessaire.

    Usage:
        @router.get("/")
        async def endpoint(db: AsyncSession = Depends(get_async_session)):
            # Lecture - pas besoin de commit
            result = await db.execute(select(Model))

            # Écriture - commit explicite requis
            db.add(new_item)
            await db.commit()
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


def get_sync_session() -> Session:
    """
    Retourne une session sync pour les opérations qui ne peuvent pas être async.

    IMPORTANT: Le caller est responsable du commit et de la fermeture.

    Usage:
        session = get_sync_session()
        try:
            # operations...
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    """
    return SyncSessionLocal()


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager pour utilisation dans les services (pas les endpoints).

    IMPORTANT: Pas d'auto-commit. Le caller doit explicitement commiter.

    Usage:
        async with get_db_context() as db:
            result = await db.execute(...)
            await db.commit()  # Explicite
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# =============================================================================
# HEALTH CHECK
# =============================================================================


async def check_database_connection() -> dict:
    """
    Vérifie que la connexion à la base de données fonctionne.

    Timeout: 5 secondes max pour éviter de bloquer le health check.

    Returns:
        dict avec status et détails de la connexion
    """
    try:
        async with asyncio.timeout(HEALTH_CHECK_TIMEOUT_SECONDS):
            async with async_engine.connect() as conn:
                result = await conn.execute(text("SELECT version()"))
                version = result.scalar()

                return {
                    "status": "healthy",
                    "connected": True,
                    "database": settings.database.name,
                    "host": settings.database.host,
                    "port": settings.database.port,
                    "version": version,
                }
    except asyncio.TimeoutError:
        logger.error(
            "Database health check timed out",
            extra={
                "timeout_seconds": HEALTH_CHECK_TIMEOUT_SECONDS,
                "host": settings.database.host,
                "database": settings.database.name,
            },
        )
        return {
            "status": "unhealthy",
            "connected": False,
            "error": f"Connection timeout ({HEALTH_CHECK_TIMEOUT_SECONDS}s)",
            "database": settings.database.name,
            "host": settings.database.host,
            "port": settings.database.port,
        }
    except Exception as e:
        logger.error(
            "Database health check failed",
            extra={
                "error": str(e),
                "host": settings.database.host,
                "database": settings.database.name,
            },
        )
        return {
            "status": "unhealthy",
            "connected": False,
            "error": str(e),
            "database": settings.database.name,
            "host": settings.database.host,
            "port": settings.database.port,
        }


# =============================================================================
# INITIALIZATION
# =============================================================================


async def init_database() -> None:
    """
    Initialise la connexion à la base de données.

    Log les informations de connexion (sans le password).
    En production, utiliser Alembic pour les migrations.
    """
    logger.info(
        "Initializing database connection",
        extra={
            "host": settings.database.host,
            "port": settings.database.port,
            "database": settings.database.name,
            "pool_size": settings.database.pool_size,
            "max_overflow": settings.database.max_overflow,
            "pool_recycle": settings.database.pool_recycle,
        },
    )

    # Vérifier la connexion
    health = await check_database_connection()

    if health["connected"]:
        logger.info(
            "Database connection established",
            extra={
                "database": settings.database.name,
                "version": health.get("version", "unknown")[:50],
            },
        )
    else:
        logger.error(
            "Failed to establish database connection",
            extra={
                "error": health.get("error", "Unknown error"),
                "host": settings.database.host,
            },
        )


async def close_database() -> None:
    """Ferme proprement les connexions à la base de données."""
    logger.info("Closing database connections")
    await async_engine.dispose()
    sync_engine.dispose()
    logger.info("Database connections closed")
