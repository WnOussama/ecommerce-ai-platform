"""
MySQL Read Replica Routing - Séparation Read/Write

Architecture:
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        READ REPLICA ARCHITECTURE                                 │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  Application                                                                     │
│       │                                                                          │
│       ▼                                                                          │
│  ┌──────────────────────────────────────────────────────────────────────────┐  │
│  │                     DATABASE ROUTER                                       │  │
│  │                                                                           │  │
│  │   ┌─────────────────────────────────────────────────────────────────┐   │  │
│  │   │  Query Analysis                                                  │   │  │
│  │   │  SELECT → Read Replica                                          │   │  │
│  │   │  INSERT/UPDATE/DELETE → Primary                                 │   │  │
│  │   │  Transaction → Primary (toutes les opérations)                  │   │  │
│  │   └─────────────────────────────────────────────────────────────────┘   │  │
│  │                                                                           │  │
│  └────────────────┬─────────────────────────────┬────────────────────────────┘  │
│                   │                             │                               │
│                   ▼                             ▼                               │
│  ┌─────────────────────────┐    ┌─────────────────────────────────────────────┐│
│  │       PRIMARY           │    │            READ REPLICAS                    ││
│  │      (Write)            │    │                                             ││
│  │                         │    │   ┌─────────┐  ┌─────────┐  ┌─────────┐   ││
│  │  - INSERT              │    │   │Replica 1│  │Replica 2│  │Replica N│   ││
│  │  - UPDATE              │◄───┼───│  (Read) │  │  (Read) │  │  (Read) │   ││
│  │  - DELETE              │    │   └─────────┘  └─────────┘  └─────────┘   ││
│  │  - Transactions        │    │                                             ││
│  └─────────────────────────┘    │   Load Balanced (Round Robin)              ││
│           │                     └─────────────────────────────────────────────┘│
│           │ Replication                                                         │
│           └────────────────────────────────────►                               │
│                                                                                  │
│  Avantages:                                                                      │
│  • Lecture scalable (80% des requêtes)                                          │
│  • Réduction charge sur Primary                                                 │
│  • Haute disponibilité en lecture                                               │
│  • Failover automatique                                                          │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable, TypeVar
from enum import Enum
from contextlib import asynccontextmanager
import logging
import random
import time

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, AsyncEngine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import event, text

logger = logging.getLogger(__name__)

T = TypeVar("T")


# =============================================================================
# CONFIGURATION
# =============================================================================

class DatabaseRole(str, Enum):
    """Rôle de la connexion DB"""
    PRIMARY = "primary"      # Write + Read critique
    REPLICA = "replica"      # Read only
    AUTO = "auto"            # Routing automatique


@dataclass
class DatabaseNode:
    """Configuration d'un nœud de base de données"""
    host: str
    port: int = 3306
    role: DatabaseRole = DatabaseRole.PRIMARY

    # Connection pool
    pool_size: int = 10
    max_overflow: int = 20
    pool_timeout: int = 30
    pool_recycle: int = 3600

    # Health
    is_healthy: bool = True
    last_health_check: float = 0
    consecutive_failures: int = 0

    @property
    def url(self) -> str:
        return f"{self.host}:{self.port}"


@dataclass
class DatabaseConfig:
    """Configuration complète de la base de données"""
    # Credentials
    database: str
    username: str
    password: str

    # Primary
    primary: DatabaseNode = None

    # Replicas
    replicas: List[DatabaseNode] = field(default_factory=list)

    # Routing
    read_from_primary_percent: int = 0  # % de lectures sur primary (pour données fraîches)
    replication_lag_threshold_ms: int = 1000  # Lag max acceptable

    # Health check
    health_check_interval_seconds: int = 30
    max_consecutive_failures: int = 3

    # Connection
    echo_sql: bool = False

    def get_primary_url(self) -> str:
        return (
            f"mysql+aiomysql://{self.username}:{self.password}"
            f"@{self.primary.host}:{self.primary.port}/{self.database}"
        )

    def get_replica_url(self, node: DatabaseNode) -> str:
        return (
            f"mysql+aiomysql://{self.username}:{self.password}"
            f"@{node.host}:{node.port}/{self.database}"
        )


# =============================================================================
# DATABASE ROUTER
# =============================================================================

class DatabaseRouter:
    """
    Router intelligent pour Read/Write splitting.

    - SELECT → Read Replica (load balanced)
    - INSERT/UPDATE/DELETE → Primary
    - Transactions → Primary uniquement
    - Failover automatique
    """

    def __init__(self, config: DatabaseConfig):
        self._config = config

        # Engines
        self._primary_engine: Optional[AsyncEngine] = None
        self._replica_engines: List[AsyncEngine] = []

        # Session makers
        self._primary_session_maker = None
        self._replica_session_makers: List = []

        # Round-robin counter
        self._replica_index = 0

        # Stats
        self._stats = {
            "primary_reads": 0,
            "primary_writes": 0,
            "replica_reads": 0,
            "failovers": 0,
        }

    async def initialize(self) -> None:
        """Initialise les connexions"""
        # Primary
        self._primary_engine = create_async_engine(
            self._config.get_primary_url(),
            pool_size=self._config.primary.pool_size,
            max_overflow=self._config.primary.max_overflow,
            pool_timeout=self._config.primary.pool_timeout,
            pool_recycle=self._config.primary.pool_recycle,
            echo=self._config.echo_sql,
        )

        self._primary_session_maker = sessionmaker(
            self._primary_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        # Replicas
        for replica in self._config.replicas:
            engine = create_async_engine(
                self._config.get_replica_url(replica),
                pool_size=replica.pool_size,
                max_overflow=replica.max_overflow,
                pool_timeout=replica.pool_timeout,
                pool_recycle=replica.pool_recycle,
                echo=self._config.echo_sql,
            )

            self._replica_engines.append(engine)
            self._replica_session_makers.append(
                sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            )

        logger.info(
            f"Database router initialized: 1 primary, {len(self._replica_engines)} replicas"
        )

    async def close(self) -> None:
        """Ferme toutes les connexions"""
        if self._primary_engine:
            await self._primary_engine.dispose()

        for engine in self._replica_engines:
            await engine.dispose()

    # =========================================================================
    # SESSION ROUTING
    # =========================================================================

    @asynccontextmanager
    async def session(
        self,
        role: DatabaseRole = DatabaseRole.AUTO,
        force_primary: bool = False,
    ):
        """
        Retourne une session avec routing intelligent.

        Args:
            role: Rôle forcé (PRIMARY, REPLICA, AUTO)
            force_primary: Force l'utilisation du primary (pour données fraîches)
        """
        if role == DatabaseRole.PRIMARY or force_primary:
            session = self._primary_session_maker()
            self._stats["primary_reads"] += 1
        elif role == DatabaseRole.REPLICA and self._replica_session_makers:
            session = self._get_replica_session()
            self._stats["replica_reads"] += 1
        else:
            # AUTO: utiliser replica si disponible
            if self._replica_session_makers and self._should_use_replica():
                session = self._get_replica_session()
                self._stats["replica_reads"] += 1
            else:
                session = self._primary_session_maker()
                self._stats["primary_reads"] += 1

        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    @asynccontextmanager
    async def write_session(self):
        """Session pour écriture (toujours sur Primary)"""
        session = self._primary_session_maker()
        self._stats["primary_writes"] += 1

        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    @asynccontextmanager
    async def read_session(self):
        """Session pour lecture (préfère Replica)"""
        async with self.session(role=DatabaseRole.REPLICA) as session:
            yield session

    @asynccontextmanager
    async def transaction(self):
        """
        Transaction explicite (toujours sur Primary).
        Toutes les opérations dans la transaction vont sur le Primary.
        """
        session = self._primary_session_maker()
        self._stats["primary_writes"] += 1

        try:
            async with session.begin():
                yield session
        finally:
            await session.close()

    def _get_replica_session(self) -> AsyncSession:
        """Retourne une session replica (round-robin avec health check)"""
        if not self._replica_session_makers:
            return self._primary_session_maker()

        # Round-robin sur les replicas healthy
        healthy_replicas = [
            (i, sm) for i, sm in enumerate(self._replica_session_makers)
            if self._config.replicas[i].is_healthy
        ]

        if not healthy_replicas:
            # Fallback sur primary si aucun replica healthy
            logger.warning("No healthy replicas, falling back to primary")
            self._stats["failovers"] += 1
            return self._primary_session_maker()

        # Round-robin
        self._replica_index = (self._replica_index + 1) % len(healthy_replicas)
        _, session_maker = healthy_replicas[self._replica_index]

        return session_maker()

    def _should_use_replica(self) -> bool:
        """Décide si on doit utiliser un replica"""
        if not self._config.replicas:
            return False

        # Certaines lectures peuvent aller sur le primary pour données fraîches
        if self._config.read_from_primary_percent > 0:
            if random.randint(1, 100) <= self._config.read_from_primary_percent:
                return False

        return True

    # =========================================================================
    # HEALTH CHECK
    # =========================================================================

    async def health_check(self) -> Dict[str, Any]:
        """Vérifie la santé de tous les nœuds"""
        results = {
            "primary": await self._check_node_health(self._primary_engine, self._config.primary),
            "replicas": [],
        }

        for i, (engine, node) in enumerate(zip(self._replica_engines, self._config.replicas)):
            replica_health = await self._check_node_health(engine, node)

            # Vérifier le lag de réplication
            if replica_health["healthy"]:
                lag = await self._check_replication_lag(engine)
                replica_health["replication_lag_ms"] = lag

                if lag > self._config.replication_lag_threshold_ms:
                    replica_health["healthy"] = False
                    replica_health["reason"] = f"Replication lag too high: {lag}ms"

            results["replicas"].append(replica_health)

            # Mettre à jour le statut du nœud
            node.is_healthy = replica_health["healthy"]
            node.last_health_check = time.time()

        return results

    async def _check_node_health(
        self,
        engine: AsyncEngine,
        node: DatabaseNode,
    ) -> Dict[str, Any]:
        """Vérifie la santé d'un nœud"""
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT 1"))
                result.fetchone()

            node.consecutive_failures = 0

            return {
                "host": node.url,
                "role": node.role.value,
                "healthy": True,
            }

        except Exception as e:
            node.consecutive_failures += 1

            return {
                "host": node.url,
                "role": node.role.value,
                "healthy": False,
                "reason": str(e),
                "consecutive_failures": node.consecutive_failures,
            }

    async def _check_replication_lag(self, engine: AsyncEngine) -> int:
        """Vérifie le lag de réplication (en ms)"""
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text(
                    "SHOW SLAVE STATUS"
                ))
                row = result.fetchone()

                if row:
                    # Seconds_Behind_Master
                    lag_seconds = row[32] if len(row) > 32 else 0
                    return int(lag_seconds * 1000) if lag_seconds else 0

                return 0

        except Exception:
            return 0

    # =========================================================================
    # STATS
    # =========================================================================

    def get_stats(self) -> Dict[str, Any]:
        """Retourne les statistiques du router"""
        total_reads = self._stats["primary_reads"] + self._stats["replica_reads"]

        return {
            **self._stats,
            "total_reads": total_reads,
            "replica_read_percent": (
                round(self._stats["replica_reads"] / total_reads * 100, 1)
                if total_reads > 0 else 0
            ),
            "healthy_replicas": sum(
                1 for r in self._config.replicas if r.is_healthy
            ),
            "total_replicas": len(self._config.replicas),
        }


# =============================================================================
# REPOSITORY WITH READ REPLICA SUPPORT
# =============================================================================

class ReadReplicaAwareRepository:
    """
    Repository de base avec support des Read Replicas.

    Les méthodes de lecture utilisent automatiquement les replicas.
    Les méthodes d'écriture utilisent le primary.
    """

    def __init__(self, router: DatabaseRouter):
        self._router = router

    async def execute_read(
        self,
        query,
        params: Optional[Dict] = None,
        force_primary: bool = False,
    ):
        """Exécute une requête de lecture"""
        async with self._router.session(force_primary=force_primary) as session:
            result = await session.execute(query, params or {})
            return result

    async def execute_write(
        self,
        query,
        params: Optional[Dict] = None,
    ):
        """Exécute une requête d'écriture"""
        async with self._router.write_session() as session:
            result = await session.execute(query, params or {})
            return result

    async def execute_in_transaction(
        self,
        operations: Callable,
    ):
        """Exécute plusieurs opérations dans une transaction"""
        async with self._router.transaction() as session:
            return await operations(session)


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    "DatabaseRole",
    "DatabaseNode",
    "DatabaseConfig",
    "DatabaseRouter",
    "ReadReplicaAwareRepository",
]

