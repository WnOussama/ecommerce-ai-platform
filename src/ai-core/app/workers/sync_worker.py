"""
Sync Worker - Worker pour les jobs de synchronisation et embeddings

Architecture:
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           SYNC WORKER                                            │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐             │
│  │ EMBEDDING WORKER│    │ CATALOG WORKER  │    │  BULK WORKER    │             │
│  │                 │    │                 │    │                 │             │
│  │ jobs:embeddings │    │ jobs:catalog    │    │ jobs:bulk_ops   │             │
│  │                 │    │                 │    │                 │             │
│  └────────┬────────┘    └────────┬────────┘    └────────┬────────┘             │
│           │                      │                      │                       │
│           └──────────────────────┼──────────────────────┘                       │
│                                  │                                              │
│                                  ▼                                              │
│                    ┌─────────────────────────────┐                              │
│                    │      SHARED SERVICES        │                              │
│                    │                             │                              │
│                    │  • RAGServiceV2 (ChromaDB)  │                              │
│                    │  • LLMGateway (embeddings)  │                              │
│                    │  • TenantService            │                              │
│                    │                             │                              │
│                    └─────────────────────────────┘                              │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘

Usage:
    # Démarrer le worker
    python -m app.workers.sync_worker

    # Ou via Docker
    docker-compose up sync-worker
"""

import asyncio
import logging
import signal
from typing import Dict, Any, Optional
from datetime import datetime

import redis.asyncio as redis

from app.services.message_queue.reliable_queue import (
    QueueWorker,
    QueueConfig,
    Job,
    StreamName,
    DLQManager,
)
from app.domain.services.shared.rag_service_v2 import RAGServiceV2, DocumentType
from app.domain.services.shared.llm_gateway import LLMGateway
from app.core.config.settings import settings

logger = logging.getLogger(__name__)


# =============================================================================
# JOB HANDLERS
# =============================================================================

class EmbeddingJobHandler:
    """Handler pour les jobs d'embedding"""

    def __init__(self, rag_service: RAGServiceV2):
        self._rag = rag_service

    async def handle(self, job: Job) -> Optional[Dict[str, Any]]:
        """Traite un job d'embedding"""
        tenant_id = job.tenant_id
        payload = job.payload

        document_type = DocumentType(payload.get("document_type", "product"))
        documents = payload.get("documents", [])
        operation = payload.get("operation", "upsert")

        if not documents:
            logger.warning(f"Empty documents list for job {job.id}")
            return {"status": "skipped", "reason": "empty_documents"}

        logger.info(
            f"Processing embedding job",
            extra={
                "job_id": job.id,
                "tenant_id": tenant_id,
                "document_type": document_type.value,
                "document_count": len(documents),
                "operation": operation,
            }
        )

        if operation == "upsert":
            count = await self._rag.index_documents(
                tenant_id=tenant_id,
                doc_type=document_type,
                documents=documents,
            )
            return {
                "status": "completed",
                "documents_indexed": count,
                "document_type": document_type.value,
            }

        elif operation == "delete":
            doc_ids = [d.get("id") for d in documents if d.get("id")]
            count = await self._rag.delete_documents(
                tenant_id=tenant_id,
                doc_type=document_type,
                document_ids=doc_ids,
            )
            return {
                "status": "completed",
                "documents_deleted": count,
            }

        else:
            raise ValueError(f"Unknown operation: {operation}")


class CatalogSyncJobHandler:
    """Handler pour les jobs de synchronisation catalogue"""

    def __init__(self, rag_service: RAGServiceV2):
        self._rag = rag_service

    async def handle(self, job: Job) -> Optional[Dict[str, Any]]:
        """Traite un job de sync catalogue"""
        tenant_id = job.tenant_id
        payload = job.payload

        platform = payload.get("platform", "prestashop")
        sync_type = payload.get("sync_type", "incremental")
        source_url = payload.get("source_url", "")

        logger.info(
            f"Processing catalog sync job",
            extra={
                "job_id": job.id,
                "tenant_id": tenant_id,
                "platform": platform,
                "sync_type": sync_type,
            }
        )

        # TODO: Implémenter la vraie synchronisation avec l'API PrestaShop/Shopify
        # Pour l'instant, simulation

        # 1. Récupérer les produits depuis l'API e-commerce
        # products = await self._fetch_products(platform, source_url, credentials)

        # 2. Indexer dans ChromaDB
        # await self._rag.index_documents(tenant_id, DocumentType.PRODUCT, products)

        return {
            "status": "completed",
            "platform": platform,
            "sync_type": sync_type,
            "message": "Catalog sync completed (simulated)",
        }


class BulkOperationJobHandler:
    """Handler pour les jobs d'opérations bulk"""

    async def handle(self, job: Job) -> Optional[Dict[str, Any]]:
        """Traite un job bulk"""
        tenant_id = job.tenant_id
        payload = job.payload

        operation_type = payload.get("operation_type", "")
        items = payload.get("items", [])

        logger.info(
            f"Processing bulk operation job",
            extra={
                "job_id": job.id,
                "tenant_id": tenant_id,
                "operation_type": operation_type,
                "item_count": len(items),
            }
        )

        # Dispatch selon le type d'opération
        if operation_type == "bulk_coupon_generation":
            return await self._handle_bulk_coupons(tenant_id, items, payload)

        elif operation_type == "bulk_price_update":
            return await self._handle_bulk_prices(tenant_id, items, payload)

        else:
            raise ValueError(f"Unknown bulk operation: {operation_type}")

    async def _handle_bulk_coupons(
        self,
        tenant_id: str,
        items: list,
        options: dict,
    ) -> Dict[str, Any]:
        """Génère des coupons en masse"""
        # TODO: Implémenter avec le service coupon
        return {
            "status": "completed",
            "coupons_generated": len(items),
        }

    async def _handle_bulk_prices(
        self,
        tenant_id: str,
        items: list,
        options: dict,
    ) -> Dict[str, Any]:
        """Met à jour les prix en masse"""
        # TODO: Implémenter avec l'API e-commerce
        return {
            "status": "completed",
            "prices_updated": len(items),
        }


# =============================================================================
# MAIN WORKER
# =============================================================================

class SyncWorkerManager:
    """
    Gestionnaire principal du worker de synchronisation.
    Lance plusieurs workers pour différentes queues.
    """

    def __init__(
        self,
        redis_url: str,
        llm_gateway: Optional[LLMGateway] = None,
    ):
        self._redis_url = redis_url
        self._redis_client: Optional[redis.Redis] = None
        self._workers: list[QueueWorker] = []
        self._running = False

        # Services partagés
        self._rag_service = RAGServiceV2()
        if llm_gateway:
            self._rag_service.set_llm_gateway(llm_gateway)

        # Handlers
        self._embedding_handler = EmbeddingJobHandler(self._rag_service)
        self._catalog_handler = CatalogSyncJobHandler(self._rag_service)
        self._bulk_handler = BulkOperationJobHandler()

    async def start(self) -> None:
        """Démarre tous les workers"""
        logger.info("Starting Sync Worker Manager")

        # Connexion Redis
        self._redis_client = redis.from_url(
            self._redis_url,
            decode_responses=True,
        )

        self._running = True

        # Configuration
        config = QueueConfig(
            max_retries=3,
            retry_delay_seconds=60,
            batch_size=10,
            block_timeout_ms=5000,
            claim_timeout_ms=30000,
        )

        # Créer les workers
        self._workers = [
            # Worker pour les embeddings (haute priorité)
            QueueWorker(
                redis_client=self._redis_client,
                stream=StreamName.EMBEDDINGS,
                handler=self._embedding_handler.handle,
                group_name="sync-workers",
                consumer_name=f"embedding-worker-{id(self)}",
                config=config,
            ),
            # Worker pour la sync catalogue
            QueueWorker(
                redis_client=self._redis_client,
                stream=StreamName.CATALOG_SYNC,
                handler=self._catalog_handler.handle,
                group_name="sync-workers",
                consumer_name=f"catalog-worker-{id(self)}",
                config=config,
            ),
            # Worker pour les opérations bulk
            QueueWorker(
                redis_client=self._redis_client,
                stream=StreamName.BULK_OPERATIONS,
                handler=self._bulk_handler.handle,
                group_name="sync-workers",
                consumer_name=f"bulk-worker-{id(self)}",
                config=config,
            ),
        ]

        # Lancer tous les workers en parallèle
        try:
            await asyncio.gather(
                *[worker.run() for worker in self._workers],
                return_exceptions=True,
            )
        except Exception as e:
            logger.error(f"Worker error: {e}")
        finally:
            await self.stop()

    async def stop(self) -> None:
        """Arrête tous les workers gracefully"""
        logger.info("Stopping Sync Worker Manager")
        self._running = False

        # Arrêter tous les workers
        for worker in self._workers:
            await worker.stop()

        # Fermer la connexion Redis
        if self._redis_client:
            await self._redis_client.close()

        logger.info("Sync Worker Manager stopped")

    def get_stats(self) -> Dict[str, Any]:
        """Retourne les stats de tous les workers"""
        return {
            "workers": [w.get_stats() for w in self._workers],
            "running": self._running,
        }


# =============================================================================
# ENTRYPOINT
# =============================================================================

async def main():
    """Point d'entrée du worker"""
    from app.core.logging.config import setup_logging

    setup_logging()
    logger.info("=" * 60)
    logger.info("SYNC WORKER STARTING")
    logger.info("=" * 60)

    # Configuration
    redis_url = getattr(settings, 'redis', None)
    if redis_url and hasattr(redis_url, 'url'):
        redis_url = redis_url.url
    else:
        redis_url = "redis://localhost:6379/0"

    # Créer le LLM Gateway pour les embeddings
    llm_gateway = None
    try:
        from app.core.config.settings import settings
        if settings.llm.openai_api_key:
            llm_gateway = LLMGateway(
                openai_api_key=settings.llm.openai_api_key,
            )
            logger.info("LLM Gateway initialized for embeddings")
    except Exception as e:
        logger.warning(f"Could not initialize LLM Gateway: {e}")

    # Créer le manager
    manager = SyncWorkerManager(
        redis_url=redis_url,
        llm_gateway=llm_gateway,
    )

    # Gestion des signaux pour arrêt graceful
    loop = asyncio.get_event_loop()

    def signal_handler():
        logger.info("Received shutdown signal")
        asyncio.create_task(manager.stop())

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    # Démarrer le manager
    await manager.start()


if __name__ == "__main__":
    asyncio.run(main())

