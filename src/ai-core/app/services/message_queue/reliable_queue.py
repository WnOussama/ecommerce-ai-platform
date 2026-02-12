"""
Redis Streams Queue - Production-Ready Message Queue

Architecture FIABLE pour jobs critiques:
┌─────────────────────────────────────────────────────────────────────────────────┐
│                      REDIS STREAMS vs PUB/SUB                                    │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ❌ PUB/SUB (non fiable):                                                       │
│     • Message perdu si subscriber down                                          │
│     • Pas de persistance                                                        │
│     • Pas de retry                                                              │
│                                                                                  │
│  ✅ REDIS STREAMS (fiable):                                                     │
│     • Messages persistés                                                        │
│     • Consumer Groups (plusieurs workers)                                       │
│     • Acknowledgment (message traité = ACK)                                     │
│     • Retry automatique avec compteur                                           │
│     • Dead Letter Queue pour échecs                                             │
│     • Claiming de messages abandonnés                                           │
│     • Métriques et monitoring                                                   │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘

Usage:
    # Producer
    queue = ReliableQueue(redis_client)
    await queue.enqueue(
        stream="jobs:embeddings",
        job=EmbeddingJob(tenant_id="xxx", documents=[...])
    )

    # Consumer (Worker)
    worker = QueueWorker(redis_client, "jobs:embeddings", handler)
    await worker.run()
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, TypeVar, Generic, Awaitable
from uuid import uuid4
from enum import Enum
import json
import asyncio
import logging
import traceback

import redis.asyncio as redis

logger = logging.getLogger(__name__)

T = TypeVar("T")


# =============================================================================
# CONFIGURATION
# =============================================================================

@dataclass
class QueueConfig:
    """Configuration de la queue"""
    # Retry
    max_retries: int = 3
    retry_delay_seconds: int = 60  # Délai avant retry

    # Consumer
    batch_size: int = 10
    block_timeout_ms: int = 5000
    claim_timeout_ms: int = 30000  # Timeout avant claiming de messages abandonnés

    # Cleanup
    max_stream_length: int = 100000  # Max messages dans le stream
    dlq_retention_days: int = 7

    # Monitoring
    metrics_enabled: bool = True
    log_level: str = "INFO"


# =============================================================================
# JOB TYPES
# =============================================================================

class JobStatus(str, Enum):
    """Statut d'un job"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"
    DEAD = "dead"  # Dans DLQ


class JobPriority(int, Enum):
    """Priorité des jobs"""
    LOW = 1
    NORMAL = 5
    HIGH = 10
    CRITICAL = 20


@dataclass
class Job:
    """Job de base"""
    id: str = field(default_factory=lambda: str(uuid4()))
    type: str = ""
    tenant_id: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    priority: JobPriority = JobPriority.NORMAL

    # Metadata
    created_at: datetime = field(default_factory=datetime.utcnow)
    scheduled_at: Optional[datetime] = None  # Pour jobs différés

    # Retry tracking
    retry_count: int = 0
    max_retries: int = 3
    last_error: Optional[str] = None

    # Tracing
    correlation_id: Optional[str] = None
    initiated_by: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Sérialise le job pour Redis"""
        return {
            "id": self.id,
            "type": self.type,
            "tenant_id": self.tenant_id,
            "payload": json.dumps(self.payload),
            "priority": str(self.priority.value),
            "created_at": self.created_at.isoformat(),
            "scheduled_at": self.scheduled_at.isoformat() if self.scheduled_at else "",
            "retry_count": str(self.retry_count),
            "max_retries": str(self.max_retries),
            "last_error": self.last_error or "",
            "correlation_id": self.correlation_id or "",
            "initiated_by": self.initiated_by or "",
        }

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "Job":
        """Désérialise un job depuis Redis"""
        return cls(
            id=data["id"],
            type=data["type"],
            tenant_id=data["tenant_id"],
            payload=json.loads(data["payload"]),
            priority=JobPriority(int(data["priority"])),
            created_at=datetime.fromisoformat(data["created_at"]),
            scheduled_at=datetime.fromisoformat(data["scheduled_at"]) if data.get("scheduled_at") else None,
            retry_count=int(data.get("retry_count", 0)),
            max_retries=int(data.get("max_retries", 3)),
            last_error=data.get("last_error") or None,
            correlation_id=data.get("correlation_id") or None,
            initiated_by=data.get("initiated_by") or None,
        )


@dataclass
class JobResult:
    """Résultat d'un job"""
    job_id: str
    status: JobStatus
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    processing_time_ms: int = 0
    completed_at: datetime = field(default_factory=datetime.utcnow)


# =============================================================================
# STREAM NAMES
# =============================================================================

class StreamName:
    """Noms des streams Redis"""

    # Jobs principaux
    EMBEDDINGS = "jobs:embeddings"
    CATALOG_SYNC = "jobs:catalog_sync"
    BULK_OPERATIONS = "jobs:bulk_ops"
    REPORTS = "jobs:reports"

    # Events (notification, pas critique)
    EVENTS_AUDIT = "events:audit"
    EVENTS_METRICS = "events:metrics"

    # Infrastructure
    DLQ = "jobs:dlq"
    SCHEDULED = "jobs:scheduled"

    @classmethod
    def all_job_streams(cls) -> List[str]:
        return [cls.EMBEDDINGS, cls.CATALOG_SYNC, cls.BULK_OPERATIONS, cls.REPORTS]


# =============================================================================
# RELIABLE QUEUE (PRODUCER)
# =============================================================================

class ReliableQueue:
    """
    Queue FIABLE basée sur Redis Streams.

    Garanties:
    - Messages persistés (pas de perte)
    - Ordre FIFO
    - Retry automatique
    - Dead Letter Queue
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        config: Optional[QueueConfig] = None,
    ):
        self._redis = redis_client
        self._config = config or QueueConfig()

    async def enqueue(
        self,
        stream: str,
        job: Job,
    ) -> str:
        """
        Ajoute un job à la queue.
        Retourne le stream ID.
        """
        # Valider le job
        if not job.tenant_id:
            raise ValueError("Job must have tenant_id")

        # Ajouter au stream
        stream_id = await self._redis.xadd(
            stream,
            job.to_dict(),
            maxlen=self._config.max_stream_length,
        )

        logger.info(
            f"Job enqueued",
            extra={
                "stream": stream,
                "job_id": job.id,
                "job_type": job.type,
                "tenant_id": job.tenant_id,
                "stream_id": stream_id,
            }
        )

        # Métriques
        if self._config.metrics_enabled:
            await self._increment_metric(f"queue:{stream}:enqueued")

        return stream_id

    async def enqueue_many(
        self,
        stream: str,
        jobs: List[Job],
    ) -> List[str]:
        """Ajoute plusieurs jobs en batch"""
        if not jobs:
            return []

        async with self._redis.pipeline(transaction=True) as pipe:
            for job in jobs:
                pipe.xadd(
                    stream,
                    job.to_dict(),
                    maxlen=self._config.max_stream_length,
                )
            results = await pipe.execute()

        logger.info(f"Enqueued {len(jobs)} jobs to {stream}")

        return list(results)

    async def schedule(
        self,
        stream: str,
        job: Job,
        delay_seconds: int,
    ) -> str:
        """
        Schedule un job pour exécution différée.
        Utilise un sorted set pour le scheduling.
        """
        job.scheduled_at = datetime.utcnow() + timedelta(seconds=delay_seconds)

        # Stocker dans le sorted set avec score = timestamp d'exécution
        score = job.scheduled_at.timestamp()
        await self._redis.zadd(
            StreamName.SCHEDULED,
            {json.dumps({"stream": stream, "job": job.to_dict()}): score},
        )

        logger.info(
            f"Job scheduled for {job.scheduled_at}",
            extra={"job_id": job.id, "stream": stream, "delay_seconds": delay_seconds}
        )

        return job.id

    async def get_queue_length(self, stream: str) -> int:
        """Nombre de messages dans la queue"""
        return await self._redis.xlen(stream)

    async def get_queue_info(self, stream: str) -> Dict[str, Any]:
        """Informations détaillées sur une queue"""
        length = await self._redis.xlen(stream)

        try:
            groups = await self._redis.xinfo_groups(stream)
        except redis.ResponseError:
            groups = []

        try:
            stream_info = await self._redis.xinfo_stream(stream)
            first_entry = stream_info.get("first-entry")
            last_entry = stream_info.get("last-entry")
        except redis.ResponseError:
            first_entry = None
            last_entry = None

        return {
            "stream": stream,
            "length": length,
            "consumer_groups": [
                {
                    "name": g["name"],
                    "consumers": g["consumers"],
                    "pending": g["pending"],
                    "last_delivered_id": g.get("last-delivered-id"),
                }
                for g in groups
            ],
            "first_entry_id": first_entry[0] if first_entry else None,
            "last_entry_id": last_entry[0] if last_entry else None,
        }

    async def _increment_metric(self, key: str) -> None:
        """Incrémente un compteur de métrique"""
        await self._redis.incr(key)


# =============================================================================
# QUEUE WORKER (CONSUMER)
# =============================================================================

JobHandler = Callable[[Job], Awaitable[Optional[Dict[str, Any]]]]


class QueueWorker:
    """
    Worker FIABLE pour consommer les jobs.

    Features:
    - Consumer Groups (plusieurs workers)
    - Acknowledgment
    - Retry automatique
    - Dead Letter Queue
    - Claiming de messages abandonnés
    - Graceful shutdown
    """

    def __init__(
        self,
        redis_client: redis.Redis,
        stream: str,
        handler: JobHandler,
        group_name: str = "workers",
        consumer_name: Optional[str] = None,
        config: Optional[QueueConfig] = None,
    ):
        self._redis = redis_client
        self._stream = stream
        self._handler = handler
        self._group_name = group_name
        self._consumer_name = consumer_name or f"worker-{uuid4().hex[:8]}"
        self._config = config or QueueConfig()

        self._running = False
        self._processed_count = 0
        self._error_count = 0

    async def run(self) -> None:
        """
        Démarre le worker.
        Boucle infinie jusqu'à stop().
        """
        await self._ensure_consumer_group()

        self._running = True
        logger.info(
            f"Worker started",
            extra={
                "stream": self._stream,
                "group": self._group_name,
                "consumer": self._consumer_name,
            }
        )

        # Lancer les tâches parallèles
        await asyncio.gather(
            self._consume_loop(),
            self._claim_abandoned_loop(),
            self._process_scheduled_loop(),
            return_exceptions=True,
        )

    async def stop(self) -> None:
        """Arrête le worker gracefully"""
        logger.info(f"Stopping worker {self._consumer_name}")
        self._running = False

    async def _ensure_consumer_group(self) -> None:
        """Crée le consumer group si nécessaire"""
        try:
            await self._redis.xgroup_create(
                self._stream,
                self._group_name,
                id="0",
                mkstream=True,
            )
            logger.info(f"Created consumer group {self._group_name} for {self._stream}")
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    async def _consume_loop(self) -> None:
        """Boucle principale de consommation"""
        while self._running:
            try:
                # Lire les nouveaux messages
                messages = await self._redis.xreadgroup(
                    groupname=self._group_name,
                    consumername=self._consumer_name,
                    streams={self._stream: ">"},
                    count=self._config.batch_size,
                    block=self._config.block_timeout_ms,
                )

                if not messages:
                    continue

                for stream_name, stream_messages in messages:
                    for stream_id, data in stream_messages:
                        await self._process_message(stream_id, data)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Consumer loop error: {e}")
                await asyncio.sleep(1)

    async def _process_message(self, stream_id: str, data: Dict[str, str]) -> None:
        """Traite un message individuel"""
        import time
        start_time = time.perf_counter()

        try:
            job = Job.from_dict(data)

            logger.debug(
                f"Processing job {job.id}",
                extra={"stream_id": stream_id, "job_type": job.type}
            )

            # Exécuter le handler
            result = await self._handler(job)

            # ACK le message
            await self._redis.xack(self._stream, self._group_name, stream_id)

            processing_time = int((time.perf_counter() - start_time) * 1000)

            self._processed_count += 1

            logger.info(
                f"Job completed",
                extra={
                    "job_id": job.id,
                    "job_type": job.type,
                    "tenant_id": job.tenant_id,
                    "processing_time_ms": processing_time,
                }
            )

            # Métriques
            if self._config.metrics_enabled:
                await self._redis.incr(f"queue:{self._stream}:completed")
                await self._redis.incrby(
                    f"queue:{self._stream}:processing_time_ms",
                    processing_time
                )

        except Exception as e:
            self._error_count += 1
            await self._handle_error(stream_id, data, e)

    async def _handle_error(
        self,
        stream_id: str,
        data: Dict[str, str],
        error: Exception,
    ) -> None:
        """Gère une erreur de traitement"""
        retry_count = int(data.get("retry_count", 0))
        max_retries = int(data.get("max_retries", self._config.max_retries))

        error_msg = f"{type(error).__name__}: {str(error)}"

        logger.warning(
            f"Job failed (attempt {retry_count + 1}/{max_retries})",
            extra={
                "job_id": data.get("id"),
                "error": error_msg,
                "stream_id": stream_id,
            }
        )

        if retry_count < max_retries:
            # RETRY: Réajouter au stream avec délai
            data["retry_count"] = str(retry_count + 1)
            data["last_error"] = error_msg

            # Délai exponentiel: 60s, 120s, 240s...
            delay = self._config.retry_delay_seconds * (2 ** retry_count)

            # Ajouter au stream scheduled pour retry différé
            scheduled_at = datetime.utcnow() + timedelta(seconds=delay)
            await self._redis.zadd(
                StreamName.SCHEDULED,
                {json.dumps({"stream": self._stream, "job": data}): scheduled_at.timestamp()},
            )

            logger.info(
                f"Job scheduled for retry in {delay}s",
                extra={"job_id": data.get("id")}
            )

        else:
            # DLQ: Max retries atteint
            data["last_error"] = error_msg
            data["failed_at"] = datetime.utcnow().isoformat()
            data["original_stream"] = self._stream
            data["stack_trace"] = traceback.format_exc()

            await self._redis.xadd(StreamName.DLQ, data)

            logger.error(
                f"Job moved to DLQ after {max_retries} attempts",
                extra={
                    "job_id": data.get("id"),
                    "error": error_msg,
                }
            )

            if self._config.metrics_enabled:
                await self._redis.incr(f"queue:{self._stream}:dead")

        # ACK le message original
        await self._redis.xack(self._stream, self._group_name, stream_id)

    async def _claim_abandoned_loop(self) -> None:
        """
        Réclame les messages abandonnés (worker crash).
        Un message est considéré abandonné si son idle time dépasse le timeout.
        """
        while self._running:
            try:
                # Attendre avant de checker
                await asyncio.sleep(self._config.claim_timeout_ms / 1000)

                if not self._running:
                    break

                # Récupérer les messages pending depuis trop longtemps
                pending = await self._redis.xpending_range(
                    self._stream,
                    self._group_name,
                    min="-",
                    max="+",
                    count=100,
                )

                for msg in pending:
                    msg_id = msg["message_id"]
                    idle_time = msg["time_since_delivered"]

                    if idle_time > self._config.claim_timeout_ms:
                        # Réclamer le message
                        claimed = await self._redis.xclaim(
                            self._stream,
                            self._group_name,
                            self._consumer_name,
                            min_idle_time=self._config.claim_timeout_ms,
                            message_ids=[msg_id],
                        )

                        if claimed:
                            logger.warning(
                                f"Claimed abandoned message {msg_id}",
                                extra={"idle_time_ms": idle_time}
                            )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Claim loop error: {e}")
                await asyncio.sleep(5)

    async def _process_scheduled_loop(self) -> None:
        """
        Traite les jobs schedulés (retry différé).
        """
        while self._running:
            try:
                await asyncio.sleep(10)  # Check toutes les 10s

                if not self._running:
                    break

                now = datetime.utcnow().timestamp()

                # Récupérer les jobs prêts à être exécutés
                ready_jobs = await self._redis.zrangebyscore(
                    StreamName.SCHEDULED,
                    min=0,
                    max=now,
                    start=0,
                    num=100,
                )

                for job_data in ready_jobs:
                    try:
                        parsed = json.loads(job_data)
                        stream = parsed["stream"]
                        job_dict = parsed["job"]

                        # Réajouter au stream original
                        await self._redis.xadd(stream, job_dict)

                        # Supprimer du scheduled set
                        await self._redis.zrem(StreamName.SCHEDULED, job_data)

                        logger.info(
                            f"Scheduled job moved to {stream}",
                            extra={"job_id": job_dict.get("id")}
                        )

                    except Exception as e:
                        logger.error(f"Error processing scheduled job: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Scheduled loop error: {e}")
                await asyncio.sleep(5)

    def get_stats(self) -> Dict[str, Any]:
        """Retourne les stats du worker"""
        return {
            "consumer_name": self._consumer_name,
            "stream": self._stream,
            "group": self._group_name,
            "processed_count": self._processed_count,
            "error_count": self._error_count,
            "running": self._running,
        }


# =============================================================================
# SPECIFIC JOB TYPES
# =============================================================================

@dataclass
class EmbeddingJob(Job):
    """Job de génération d'embeddings"""

    def __post_init__(self):
        self.type = "embedding"

    @property
    def document_type(self) -> str:
        return self.payload.get("document_type", "")

    @property
    def documents(self) -> List[Dict[str, Any]]:
        return self.payload.get("documents", [])

    @property
    def operation(self) -> str:
        return self.payload.get("operation", "upsert")


@dataclass
class CatalogSyncJob(Job):
    """Job de synchronisation catalogue"""

    def __post_init__(self):
        self.type = "catalog_sync"

    @property
    def platform(self) -> str:
        return self.payload.get("platform", "")

    @property
    def sync_type(self) -> str:
        return self.payload.get("sync_type", "incremental")


@dataclass
class BulkOperationJob(Job):
    """Job d'opération bulk"""

    def __post_init__(self):
        self.type = "bulk_operation"

    @property
    def operation_type(self) -> str:
        return self.payload.get("operation_type", "")

    @property
    def items(self) -> List[Dict[str, Any]]:
        return self.payload.get("items", [])


@dataclass
class ReportJob(Job):
    """Job de génération de rapport"""

    def __post_init__(self):
        self.type = "report"

    @property
    def report_type(self) -> str:
        return self.payload.get("report_type", "")

    @property
    def format(self) -> str:
        return self.payload.get("format", "json")


# =============================================================================
# DLQ MANAGER
# =============================================================================

class DLQManager:
    """
    Gestionnaire de la Dead Letter Queue.
    Permet de visualiser et retraiter les jobs échoués.
    """

    def __init__(self, redis_client: redis.Redis):
        self._redis = redis_client

    async def get_dead_jobs(
        self,
        count: int = 100,
        tenant_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Récupère les jobs dans la DLQ"""
        messages = await self._redis.xrange(
            StreamName.DLQ,
            count=count,
        )

        jobs = []
        for stream_id, data in messages:
            if tenant_id and data.get("tenant_id") != tenant_id:
                continue

            jobs.append({
                "stream_id": stream_id,
                "job_id": data.get("id"),
                "type": data.get("type"),
                "tenant_id": data.get("tenant_id"),
                "error": data.get("last_error"),
                "failed_at": data.get("failed_at"),
                "original_stream": data.get("original_stream"),
                "retry_count": int(data.get("retry_count", 0)),
            })

        return jobs

    async def retry_job(self, stream_id: str) -> bool:
        """Retente un job de la DLQ"""
        # Récupérer le job
        messages = await self._redis.xrange(
            StreamName.DLQ,
            min=stream_id,
            max=stream_id,
            count=1,
        )

        if not messages:
            return False

        _, data = messages[0]
        original_stream = data.get("original_stream")

        if not original_stream:
            return False

        # Reset retry count
        data["retry_count"] = "0"
        data.pop("failed_at", None)
        data.pop("original_stream", None)
        data.pop("stack_trace", None)

        # Réajouter au stream original
        await self._redis.xadd(original_stream, data)

        # Supprimer de la DLQ
        await self._redis.xdel(StreamName.DLQ, stream_id)

        logger.info(f"Retried job {data.get('id')} from DLQ")
        return True

    async def delete_job(self, stream_id: str) -> bool:
        """Supprime un job de la DLQ"""
        result = await self._redis.xdel(StreamName.DLQ, stream_id)
        return result > 0

    async def purge(self, older_than_days: int = 7) -> int:
        """Purge les jobs de la DLQ plus anciens que N jours"""
        cutoff = datetime.utcnow() - timedelta(days=older_than_days)

        messages = await self._redis.xrange(StreamName.DLQ)
        deleted = 0

        for stream_id, data in messages:
            failed_at = data.get("failed_at")
            if failed_at:
                failed_date = datetime.fromisoformat(failed_at)
                if failed_date < cutoff:
                    await self._redis.xdel(StreamName.DLQ, stream_id)
                    deleted += 1

        logger.info(f"Purged {deleted} jobs from DLQ")
        return deleted

    async def get_stats(self) -> Dict[str, Any]:
        """Statistiques de la DLQ"""
        length = await self._redis.xlen(StreamName.DLQ)

        # Count par type d'erreur
        messages = await self._redis.xrange(StreamName.DLQ, count=1000)
        error_types: Dict[str, int] = {}

        for _, data in messages:
            error = data.get("last_error", "unknown")
            error_type = error.split(":")[0] if ":" in error else error
            error_types[error_type] = error_types.get(error_type, 0) + 1

        return {
            "total_dead_jobs": length,
            "error_types": error_types,
        }


# =============================================================================
# FACTORY
# =============================================================================

async def create_queue_system(
    redis_url: str,
) -> tuple[ReliableQueue, redis.Redis]:
    """
    Factory pour créer le système de queue.

    Usage:
        queue, redis_client = await create_queue_system("redis://localhost")

        # Producer
        await queue.enqueue(StreamName.EMBEDDINGS, job)

        # Consumer (dans un autre process)
        worker = QueueWorker(redis_client, StreamName.EMBEDDINGS, handler)
        await worker.run()
    """
    client = redis.from_url(redis_url, decode_responses=True)
    queue = ReliableQueue(client)

    return queue, client


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    # Core
    "ReliableQueue",
    "QueueWorker",
    "QueueConfig",

    # Jobs
    "Job",
    "JobStatus",
    "JobPriority",
    "JobResult",
    "EmbeddingJob",
    "CatalogSyncJob",
    "BulkOperationJob",
    "ReportJob",

    # Stream Names
    "StreamName",

    # DLQ
    "DLQManager",

    # Factory
    "create_queue_system",
]

