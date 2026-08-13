"""
AI Sync Service - Background Workers pour tâches asynchrones
Responsabilités:
- Synchronisation catalogue (PrestaShop, Shopify, WooCommerce)
- Génération d'embeddings
- Opérations bulk
- Traitement des événements

Ce service est découplé des services temps-réel pour éviter l'impact sur la latence.
"""

import asyncio
import logging
import os
import signal
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable, Dict

from app.core.config.settings import settings
from app.core.logging.config import setup_logging
from app.services.message_queue.redis_queue import (
    MessageType,
    QueueMessage,
    QueueName,
    RedisQueueClient,
    get_queue_client,
)

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)

# Service identifier
SERVICE_NAME = "ai-sync-service"
SERVICE_VERSION = settings.app_version


# =============================================================================
# MESSAGE HANDLERS
# =============================================================================


class EmbeddingHandler:
    """Handler pour les tâches de génération d'embeddings"""

    def __init__(self):
        # Lazy import pour éviter les dépendances circulaires
        pass

    async def handle_product_embeddings(self, message: QueueMessage) -> None:
        """Générer les embeddings pour des produits"""
        from app.infrastructure.llm.gateway import LLMGateway
        from app.infrastructure.vector_store.service import VectorStoreService

        tenant_id = message.tenant_id
        payload = message.payload
        documents = payload.get("documents", [])

        logger.info(
            f"Generating embeddings for {len(documents)} products",
            extra={"tenant_id": tenant_id, "message_id": message.id},
        )

        try:
            # Initialize services
            llm_gateway = LLMGateway()
            vector_store = VectorStoreService()

            # Generate embeddings batch
            texts = [
                f"{doc.get('name', '')} {doc.get('description', '')} {doc.get('category', '')}"
                for doc in documents
            ]

            embeddings = await llm_gateway.generate_embeddings(texts)

            # Store in ChromaDB
            collection_name = f"tenant_{tenant_id}_products"
            await vector_store.upsert_documents(
                collection_name=collection_name,
                documents=documents,
                embeddings=embeddings,
                ids=[str(doc.get("id")) for doc in documents],
            )

            logger.info(
                f"Successfully generated embeddings for {len(documents)} products",
                extra={"tenant_id": tenant_id},
            )

        except Exception as e:
            logger.error(
                f"Failed to generate product embeddings: {e}",
                extra={"tenant_id": tenant_id, "message_id": message.id},
            )
            raise

    async def handle_faq_embeddings(self, message: QueueMessage) -> None:
        """Générer les embeddings pour des FAQs"""
        from app.infrastructure.llm.gateway import LLMGateway
        from app.infrastructure.vector_store.service import VectorStoreService

        tenant_id = message.tenant_id
        payload = message.payload
        documents = payload.get("documents", [])

        logger.info(
            f"Generating embeddings for {len(documents)} FAQs", extra={"tenant_id": tenant_id}
        )

        try:
            llm_gateway = LLMGateway()
            vector_store = VectorStoreService()

            texts = [f"{doc.get('question', '')} {doc.get('answer', '')}" for doc in documents]

            embeddings = await llm_gateway.generate_embeddings(texts)

            collection_name = f"tenant_{tenant_id}_faqs"
            await vector_store.upsert_documents(
                collection_name=collection_name,
                documents=documents,
                embeddings=embeddings,
                ids=[str(doc.get("id")) for doc in documents],
            )

            logger.info(f"Successfully generated embeddings for {len(documents)} FAQs")

        except Exception as e:
            logger.error(f"Failed to generate FAQ embeddings: {e}")
            raise

    async def handle_delete_embeddings(self, message: QueueMessage) -> None:
        """Supprimer des embeddings"""
        from app.infrastructure.vector_store.service import VectorStoreService

        tenant_id = message.tenant_id
        payload = message.payload
        document_ids = payload.get("document_ids", [])
        document_type = payload.get("document_type", "products")

        logger.info(
            f"Deleting {len(document_ids)} embeddings",
            extra={"tenant_id": tenant_id, "document_type": document_type},
        )

        try:
            vector_store = VectorStoreService()
            collection_name = f"tenant_{tenant_id}_{document_type}"

            await vector_store.delete_documents(collection_name=collection_name, ids=document_ids)

            logger.info(f"Successfully deleted {len(document_ids)} embeddings")

        except Exception as e:
            logger.error(f"Failed to delete embeddings: {e}")
            raise


class CatalogSyncHandler:
    """Handler pour la synchronisation du catalogue"""

    async def handle_sync_catalog(self, message: QueueMessage) -> None:
        """Synchroniser le catalogue depuis la plateforme e-commerce"""
        tenant_id = message.tenant_id
        payload = message.payload
        platform = payload.get("platform", "prestashop")
        sync_type = payload.get("sync_type", "incremental")

        logger.info(
            "Starting catalog sync",
            extra={"tenant_id": tenant_id, "platform": platform, "sync_type": sync_type},
        )

        try:
            # Import the appropriate adapter
            if platform == "prestashop":
                from app.infrastructure.external.prestashop_adapter import PrestaShopAdapter

                adapter = PrestaShopAdapter(payload.get("api_credentials", {}))
            elif platform == "shopify":
                from app.infrastructure.external.shopify_adapter import ShopifyAdapter

                adapter = ShopifyAdapter(payload.get("api_credentials", {}))
            elif platform == "woocommerce":
                from app.infrastructure.external.woocommerce_adapter import WooCommerceAdapter

                adapter = WooCommerceAdapter(payload.get("api_credentials", {}))
            else:
                raise ValueError(f"Unsupported platform: {platform}")

            # Fetch products
            if sync_type == "full":
                products = await adapter.fetch_all_products()
            else:
                last_sync = payload.get("last_sync_at")
                products = await adapter.fetch_updated_products(since=last_sync)

            logger.info(f"Fetched {len(products)} products from {platform}")

            # Trigger embedding generation for new/updated products
            if products:
                from app.services.message_queue.redis_queue import (
                    MessageType,
                    QueueMessage,
                    get_queue_client,
                )

                queue_client = get_queue_client(
                    redis_url=f"redis://{settings.redis.host}:{settings.redis.port}",
                    consumer_group="sync-service",
                    consumer_name=f"sync-{os.getpid()}",
                )

                # Batch products for embedding generation
                batch_size = 50
                for i in range(0, len(products), batch_size):
                    batch = products[i : i + batch_size]
                    embedding_message = QueueMessage(
                        type=MessageType.GENERATE_PRODUCT_EMBEDDINGS,
                        tenant_id=tenant_id,
                        payload={"documents": batch},
                    )
                    await queue_client.publish(QueueName.EMBEDDING_REQUESTS, embedding_message)

                logger.info(f"Queued {len(products)} products for embedding generation")

            # Update last sync timestamp
            # TODO: Update tenant record with last_sync_at

        except Exception as e:
            logger.error(f"Catalog sync failed: {e}")
            raise

    async def handle_sync_customers(self, message: QueueMessage) -> None:
        """Synchroniser les clients depuis la plateforme"""
        tenant_id = message.tenant_id

        logger.info("Starting customer sync", extra={"tenant_id": tenant_id})

        # TODO: Implement customer sync logic
        pass


class BulkOperationHandler:
    """Handler pour les opérations bulk"""

    async def handle_bulk_coupon_generation(self, message: QueueMessage) -> None:
        """Générer des coupons en bulk"""
        tenant_id = message.tenant_id
        payload = message.payload
        action_id = payload.get("action_id")

        logger.info(
            "Starting bulk coupon generation",
            extra={"tenant_id": tenant_id, "action_id": action_id},
        )

        try:
            from app.infrastructure.database.unit_of_work import UnitOfWork

            customers = payload.get("customers", [])
            coupon_config = payload.get("coupon_config", {})
            discount_percent = coupon_config.get("discount_percent", 10)
            validity_days = coupon_config.get("validity_days", 7)
            reason = coupon_config.get("reason")

            tenant_uuid = uuid.UUID(str(tenant_id))
            generated = 0
            failed = 0

            for customer in customers:
                try:
                    async with UnitOfWork(tenant_uuid) as uow:
                        code = uow.coupons.generate_code()
                        expires_at = datetime.utcnow() + timedelta(days=validity_days)
                        await uow.coupons.create(
                            code=code,
                            discount_percent=discount_percent,
                            discount_amount=None,
                            min_purchase=None,
                            expires_at=expires_at,
                            reason=reason,
                            customer_id=customer["id"],
                        )
                        await uow.commit()
                    generated += 1
                except Exception as e:
                    logger.warning(f"Failed to generate coupon for customer {customer['id']}: {e}")
                    failed += 1

            # Update operation status
            await self._update_operation_status(
                action_id=action_id,
                status="completed",
                result={"generated": generated, "failed": failed, "total": len(customers)},
            )

            logger.info(
                "Bulk coupon generation completed",
                extra={"tenant_id": tenant_id, "generated": generated, "failed": failed},
            )

        except Exception as e:
            logger.error(f"Bulk coupon generation failed: {e}")
            await self._update_operation_status(action_id=action_id, status="failed", error=str(e))
            raise

    async def handle_bulk_price_update(self, message: QueueMessage) -> None:
        """Mise à jour des prix en bulk"""
        tenant_id = message.tenant_id
        payload = message.payload
        action_id = payload.get("action_id")

        logger.info(
            "Starting bulk price update", extra={"tenant_id": tenant_id, "action_id": action_id}
        )

        # TODO: Implement bulk price update
        pass

    async def _update_operation_status(
        self, action_id: str, status: str, result: Dict[str, Any] = None, error: str = None
    ) -> None:
        """Mettre à jour le statut d'une opération dans Redis"""
        import redis.asyncio as redis

        client = redis.from_url(f"redis://{settings.redis.host}:{settings.redis.port}")

        status_data = {"status": status, "updated_at": datetime.utcnow().isoformat()}
        if result:
            status_data["result"] = result
        if error:
            status_data["error"] = error

        import json

        await client.set(
            f"operation:{action_id}:status",
            json.dumps(status_data),
            ex=86400,  # 24h TTL
        )
        await client.close()


# =============================================================================
# WORKER
# =============================================================================


class SyncServiceWorker:
    """
    Worker principal pour le Sync Service.
    Écoute plusieurs queues et route les messages vers les handlers appropriés.
    """

    def __init__(self):
        self.queue_client: RedisQueueClient = None
        self._running = False

        # Handlers
        self.embedding_handler = EmbeddingHandler()
        self.catalog_handler = CatalogSyncHandler()
        self.bulk_handler = BulkOperationHandler()

        # Message type to handler mapping
        self.handlers: Dict[MessageType, Callable] = {
            MessageType.GENERATE_PRODUCT_EMBEDDINGS: self.embedding_handler.handle_product_embeddings,
            MessageType.GENERATE_FAQ_EMBEDDINGS: self.embedding_handler.handle_faq_embeddings,
            MessageType.DELETE_EMBEDDINGS: self.embedding_handler.handle_delete_embeddings,
            MessageType.SYNC_CATALOG: self.catalog_handler.handle_sync_catalog,
            MessageType.SYNC_CUSTOMERS: self.catalog_handler.handle_sync_customers,
            MessageType.BULK_COUPON_GENERATION: self.bulk_handler.handle_bulk_coupon_generation,
            MessageType.BULK_PRICE_UPDATE: self.bulk_handler.handle_bulk_price_update,
        }

    async def start(self) -> None:
        """Démarrer le worker"""
        logger.info(f"Starting {SERVICE_NAME} worker...")

        self.queue_client = get_queue_client(
            redis_url=f"redis://{settings.redis.host}:{settings.redis.port}",
            consumer_group="sync-service",
            consumer_name=f"sync-worker-{os.getpid()}",
        )
        await self.queue_client.connect()

        self._running = True

        # Démarrer les consumers pour chaque queue
        tasks = [
            asyncio.create_task(
                self.queue_client.consume(QueueName.EMBEDDING_REQUESTS, self._handle_message)
            ),
            asyncio.create_task(
                self.queue_client.consume(QueueName.CATALOG_SYNC, self._handle_message)
            ),
        ]

        logger.info(f"{SERVICE_NAME} worker started, listening on queues...")

        # Attendre que tous les consumers terminent
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Worker cancelled, shutting down...")
            raise

    async def stop(self) -> None:
        """Arrêter le worker proprement"""
        logger.info(f"Stopping {SERVICE_NAME} worker...")
        self._running = False

        if self.queue_client:
            await self.queue_client.stop()
            await self.queue_client.disconnect()

        logger.info(f"{SERVICE_NAME} worker stopped")

    async def _handle_message(self, message: QueueMessage) -> None:
        """Router le message vers le bon handler"""
        handler = self.handlers.get(message.type)

        if handler is None:
            logger.warning(
                f"No handler for message type: {message.type}", extra={"message_id": message.id}
            )
            return

        logger.info(
            "Processing message",
            extra={"message_id": message.id, "type": message.type, "tenant_id": message.tenant_id},
        )

        start_time = datetime.utcnow()

        try:
            await handler(message)

            duration = (datetime.utcnow() - start_time).total_seconds()
            logger.info(
                "Message processed successfully",
                extra={"message_id": message.id, "duration_seconds": duration},
            )

        except Exception as e:
            duration = (datetime.utcnow() - start_time).total_seconds()
            logger.error(
                f"Message processing failed: {e}",
                extra={"message_id": message.id, "duration_seconds": duration},
            )
            raise


# =============================================================================
# MAIN
# =============================================================================


async def main():
    """Point d'entrée principal"""

    worker = SyncServiceWorker()

    # Setup signal handlers
    loop = asyncio.get_event_loop()

    # asyncio.create_task() only keeps a weak reference to the task - if
    # nothing else references it, the task can be garbage-collected mid-run.
    # Keep a strong reference here and let it self-clean on completion.
    background_tasks: set[asyncio.Task] = set()

    def signal_handler():
        task = asyncio.create_task(worker.stop())
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    try:
        await worker.start()
    except KeyboardInterrupt:
        pass
    finally:
        await worker.stop()


if __name__ == "__main__":
    asyncio.run(main())
