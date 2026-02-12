"""
Message Queue - Redis Streams based reliable queue system

Architecture:
┌─────────────────────────────────────────────────────────────────────────────────┐
│                      RELIABLE QUEUE SYSTEM                                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ✅ Redis Streams (fiable):                                                     │
│     • Messages persistés                                                        │
│     • Consumer Groups (plusieurs workers)                                       │
│     • Acknowledgment (message traité = ACK)                                     │
│     • Retry automatique avec compteur                                           │
│     • Dead Letter Queue pour échecs                                             │
│     • Claiming de messages abandonnés                                           │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘

Usage:
    from app.services.message_queue import (
        ReliableQueue, QueueWorker, StreamName,
        Job, EmbeddingJob, CatalogSyncJob
    )

    # Producer
    queue = ReliableQueue(redis_client)
    await queue.enqueue(StreamName.EMBEDDINGS, EmbeddingJob(...))

    # Consumer
    worker = QueueWorker(redis_client, StreamName.EMBEDDINGS, handler)
    await worker.run()
"""

# Legacy exports (for backwards compatibility)
from app.services.message_queue.redis_queue import (
    QueueName,
    MessageType,
    MessagePriority,
    QueueMessage,
    RedisQueueClient,
    get_queue_client,
)

# New reliable queue exports (recommended)
from app.services.message_queue.reliable_queue import (
    # Core
    ReliableQueue,
    QueueWorker,
    QueueConfig,

    # Jobs
    Job,
    JobStatus,
    JobPriority,
    JobResult,
    EmbeddingJob,
    CatalogSyncJob,
    BulkOperationJob,
    ReportJob,

    # Stream Names
    StreamName,

    # DLQ
    DLQManager,

    # Factory
    create_queue_system,
)

__all__ = [
    # Legacy
    "QueueName",
    "MessageType",
    "MessagePriority",
    "QueueMessage",
    "RedisQueueClient",
    "get_queue_client",

    # New (recommended)
    "ReliableQueue",
    "QueueWorker",
    "QueueConfig",
    "Job",
    "JobStatus",
    "JobPriority",
    "JobResult",
    "EmbeddingJob",
    "CatalogSyncJob",
    "BulkOperationJob",
    "ReportJob",
    "StreamName",
    "DLQManager",
    "create_queue_system",
]
