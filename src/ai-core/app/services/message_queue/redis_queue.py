"""
Redis Queue Service - Communication inter-services
Utilise Redis Streams pour une communication fiable et ordonnée
"""

import asyncio
import json
import logging
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

import redis.asyncio as redis
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# =============================================================================
# QUEUE NAMES
# =============================================================================


class QueueName(str, Enum):
    """Noms des queues Redis pour chaque service"""

    # Chat Service -> Sync Service
    EMBEDDING_REQUESTS = "queue:embedding:requests"

    # Sync Service -> Chat/Admin Services
    EMBEDDING_COMPLETED = "queue:embedding:completed"

    # Admin Service -> Sync Service
    CATALOG_SYNC = "queue:catalog:sync"

    # Events (Pub/Sub style via Streams)
    AUDIT_EVENTS = "queue:events:audit"
    METRICS_EVENTS = "queue:events:metrics"

    # Dead Letter Queue
    DLQ = "queue:dlq"


# =============================================================================
# MESSAGE TYPES
# =============================================================================


class MessageType(str, Enum):
    """Types de messages inter-services"""

    # Embedding tasks
    GENERATE_PRODUCT_EMBEDDINGS = "generate_product_embeddings"
    GENERATE_FAQ_EMBEDDINGS = "generate_faq_embeddings"
    DELETE_EMBEDDINGS = "delete_embeddings"

    # Sync tasks
    SYNC_CATALOG = "sync_catalog"
    SYNC_CUSTOMERS = "sync_customers"
    SYNC_ORDERS = "sync_orders"

    # Admin tasks (deferred)
    BULK_COUPON_GENERATION = "bulk_coupon_generation"
    BULK_PRICE_UPDATE = "bulk_price_update"
    GENERATE_REPORT = "generate_report"

    # Events
    CONVERSATION_COMPLETED = "conversation_completed"
    ADMIN_ACTION_EXECUTED = "admin_action_executed"


class MessagePriority(int, Enum):
    """Priorité des messages"""

    LOW = 1
    NORMAL = 5
    HIGH = 10
    CRITICAL = 20


# =============================================================================
# MESSAGE SCHEMAS
# =============================================================================


class QueueMessage(BaseModel):
    """Message de base pour la queue"""

    id: str = Field(default_factory=lambda: str(uuid4()))
    type: MessageType
    priority: MessagePriority = MessagePriority.NORMAL
    tenant_id: str
    payload: Dict[str, Any]
    created_at: datetime = Field(default_factory=datetime.utcnow)
    retry_count: int = 0
    max_retries: int = 3

    class Config:
        use_enum_values = True


class EmbeddingRequest(BaseModel):
    """Requête de génération d'embeddings"""

    tenant_id: str
    document_type: str  # "product", "faq", "policy"
    documents: List[Dict[str, Any]]
    collection_name: str
    operation: str = "upsert"  # "upsert", "delete"


class CatalogSyncRequest(BaseModel):
    """Requête de synchronisation catalogue"""

    tenant_id: str
    platform: str  # "prestashop", "shopify", "woocommerce"
    sync_type: str  # "full", "incremental"
    source_url: str
    api_credentials: Dict[str, str]
    filters: Optional[Dict[str, Any]] = None


class BulkOperationRequest(BaseModel):
    """Requête d'opération bulk"""

    tenant_id: str
    operation_type: str
    items: List[Dict[str, Any]]
    options: Dict[str, Any] = {}
    initiated_by: str  # admin user id
    action_id: str  # for tracking


# =============================================================================
# QUEUE CLIENT
# =============================================================================


class RedisQueueClient:
    """
    Client Redis pour la gestion des queues.
    Utilise Redis Streams pour garantir:
    - Durabilité des messages
    - Ordre FIFO
    - Consumer groups pour scaling
    - Acknowledgment
    """

    def __init__(self, redis_url: str, consumer_group: str, consumer_name: str):
        self.redis_url = redis_url
        self.consumer_group = consumer_group
        self.consumer_name = consumer_name
        self._client: Optional[redis.Redis] = None
        self._running = False

    async def connect(self) -> None:
        """Établir la connexion Redis"""
        if self._client is None:
            self._client = redis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)
            logger.info(f"Connected to Redis queue: {self.redis_url}")

    async def disconnect(self) -> None:
        """Fermer la connexion Redis"""
        if self._client:
            await self._client.close()
            self._client = None
            logger.info("Disconnected from Redis queue")

    async def _ensure_consumer_group(self, queue: QueueName) -> None:
        """Créer le consumer group s'il n'existe pas"""
        try:
            await self._client.xgroup_create(
                queue.value, self.consumer_group, id="0", mkstream=True
            )
            logger.info(f"Created consumer group {self.consumer_group} for {queue.value}")
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
            # Group already exists, that's fine

    # =========================================================================
    # PRODUCER METHODS
    # =========================================================================

    async def publish(self, queue: QueueName, message: QueueMessage) -> str:
        """
        Publier un message dans une queue.
        Retourne l'ID du message dans le stream.
        """
        await self.connect()

        message_data = {
            "id": message.id,
            "type": message.type,
            "priority": str(message.priority),
            "tenant_id": message.tenant_id,
            "payload": json.dumps(message.payload),
            "created_at": message.created_at.isoformat(),
            "retry_count": str(message.retry_count),
            "max_retries": str(message.max_retries),
        }

        stream_id = await self._client.xadd(queue.value, message_data)

        logger.info(
            f"Published message to {queue.value}",
            extra={
                "message_id": message.id,
                "stream_id": stream_id,
                "type": message.type,
                "tenant_id": message.tenant_id,
            },
        )

        return stream_id

    async def publish_batch(self, queue: QueueName, messages: List[QueueMessage]) -> List[str]:
        """Publier plusieurs messages en batch"""
        stream_ids = []
        async with self._client.pipeline(transaction=True) as pipe:
            for msg in messages:
                message_data = {
                    "id": msg.id,
                    "type": msg.type,
                    "priority": str(msg.priority),
                    "tenant_id": msg.tenant_id,
                    "payload": json.dumps(msg.payload),
                    "created_at": msg.created_at.isoformat(),
                    "retry_count": str(msg.retry_count),
                    "max_retries": str(msg.max_retries),
                }
                pipe.xadd(queue.value, message_data)

            results = await pipe.execute()
            stream_ids = list(results)

        logger.info(f"Published {len(messages)} messages to {queue.value}")
        return stream_ids

    # =========================================================================
    # CONSUMER METHODS
    # =========================================================================

    async def consume(
        self,
        queue: QueueName,
        handler: Callable[[QueueMessage], Any],
        batch_size: int = 10,
        block_ms: int = 5000,
    ) -> None:
        """
        Consommer les messages d'une queue.
        Utilise consumer groups pour permettre le scaling horizontal.
        """
        await self.connect()
        await self._ensure_consumer_group(queue)

        self._running = True
        logger.info(
            f"Starting consumer for {queue.value}",
            extra={"consumer_group": self.consumer_group, "consumer_name": self.consumer_name},
        )

        while self._running:
            try:
                # Lire les messages du stream
                messages = await self._client.xreadgroup(
                    groupname=self.consumer_group,
                    consumername=self.consumer_name,
                    streams={queue.value: ">"},
                    count=batch_size,
                    block=block_ms,
                )

                if not messages:
                    continue

                for stream_name, stream_messages in messages:
                    for stream_id, data in stream_messages:
                        try:
                            # Reconstruire le message
                            queue_message = QueueMessage(
                                id=data["id"],
                                type=MessageType(data["type"]),
                                priority=MessagePriority(int(data["priority"])),
                                tenant_id=data["tenant_id"],
                                payload=json.loads(data["payload"]),
                                created_at=datetime.fromisoformat(data["created_at"]),
                                retry_count=int(data["retry_count"]),
                                max_retries=int(data["max_retries"]),
                            )

                            # Traiter le message
                            await handler(queue_message)

                            # Acknowledge le message
                            await self._client.xack(queue.value, self.consumer_group, stream_id)

                            logger.debug(
                                f"Processed message {queue_message.id}",
                                extra={"stream_id": stream_id},
                            )

                        except Exception as e:
                            logger.error(
                                f"Error processing message: {e}", extra={"stream_id": stream_id}
                            )
                            await self._handle_failed_message(queue, stream_id, data, e)

            except asyncio.CancelledError:
                logger.info("Consumer cancelled")
                break
            except Exception as e:
                logger.error(f"Consumer error: {e}")
                await asyncio.sleep(1)  # Back-off

    async def stop(self) -> None:
        """Arrêter le consumer"""
        self._running = False

    async def _handle_failed_message(
        self, queue: QueueName, stream_id: str, data: Dict, error: Exception
    ) -> None:
        """Gérer un message en échec (retry ou DLQ)"""
        retry_count = int(data.get("retry_count", 0))
        max_retries = int(data.get("max_retries", 3))

        if retry_count < max_retries:
            # Retry: republier avec compteur incrémenté
            data["retry_count"] = str(retry_count + 1)
            await self._client.xadd(queue.value, data)
            logger.warning(
                f"Message retried ({retry_count + 1}/{max_retries})", extra={"stream_id": stream_id}
            )
        else:
            # Move to DLQ
            data["error"] = str(error)
            data["failed_at"] = datetime.utcnow().isoformat()
            data["original_queue"] = queue.value
            await self._client.xadd(QueueName.DLQ.value, data)
            logger.error(
                f"Message moved to DLQ after {max_retries} retries", extra={"stream_id": stream_id}
            )

        # Acknowledge original message
        await self._client.xack(queue.value, self.consumer_group, stream_id)

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    async def get_queue_length(self, queue: QueueName) -> int:
        """Obtenir le nombre de messages dans une queue"""
        await self.connect()
        return await self._client.xlen(queue.value)

    async def get_pending_count(self, queue: QueueName) -> int:
        """Obtenir le nombre de messages pending pour ce consumer group"""
        await self.connect()
        try:
            info = await self._client.xpending(queue.value, self.consumer_group)
            return info["pending"] if info else 0
        except redis.ResponseError:
            return 0

    async def get_queue_stats(self, queue: QueueName) -> Dict[str, Any]:
        """Obtenir les statistiques d'une queue"""
        await self.connect()

        length = await self._client.xlen(queue.value)

        try:
            groups = await self._client.xinfo_groups(queue.value)
        except redis.ResponseError:
            groups = []

        return {
            "queue": queue.value,
            "length": length,
            "consumer_groups": len(groups),
            "groups": [
                {"name": g["name"], "consumers": g["consumers"], "pending": g["pending"]}
                for g in groups
            ],
        }


# =============================================================================
# SINGLETON INSTANCES
# =============================================================================

_queue_client: Optional[RedisQueueClient] = None


def get_queue_client(redis_url: str, consumer_group: str, consumer_name: str) -> RedisQueueClient:
    """Factory pour obtenir le client queue"""
    global _queue_client
    if _queue_client is None:
        _queue_client = RedisQueueClient(
            redis_url=redis_url, consumer_group=consumer_group, consumer_name=consumer_name
        )
    return _queue_client
