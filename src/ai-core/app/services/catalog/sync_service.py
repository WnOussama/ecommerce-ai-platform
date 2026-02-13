"""
Catalog Sync Service - Synchronisation catalogue depuis PrestaShop

Ce service orchestre la synchronisation des produits:
1. Récupère les produits via PrestaShopClient (pagination automatique)
2. Transforme vers le format interne
3. Effectue l'UPSERT en base MySQL
4. Retourne un résumé détaillé

Caractéristiques:
- 100% async
- Multi-tenant
- Idempotent
- Logging structuré
- Gestion d'erreurs robuste
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, List, Dict, Any, Callable, Awaitable

from app.infrastructure.external.prestashop import (
    PrestaShopClient,
    PrestaShopClientConfig,
    Product as PrestaShopProduct,
    PrestaShopError,
    PrestaShopConnectionError,
    PrestaShopAuthenticationError,
)
from app.services.catalog.repository import (
    ProductRepository,
    ProductData,
    ProductRepositoryProtocol,
)

logger = logging.getLogger(__name__)


# =============================================================================
# DATA MODELS
# =============================================================================

class SyncStatus(str, Enum):
    """Statut d'une synchronisation."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"  # Certains produits ont échoué


@dataclass
class SyncError:
    """Erreur survenue pendant la synchronisation."""
    product_id: Optional[int] = None
    product_name: Optional[str] = None
    error_type: str = ""
    error_message: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "product_id": self.product_id,
            "product_name": self.product_name,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class SyncResult:
    """
    Résultat d'une synchronisation catalogue.

    Contient toutes les métriques et informations sur la sync.
    """
    tenant_id: str
    status: SyncStatus = SyncStatus.PENDING

    # Compteurs
    total_fetched: int = 0
    total_created: int = 0
    total_updated: int = 0
    total_unchanged: int = 0
    total_failed: int = 0
    total_deleted: int = 0  # Produits supprimés (plus dans la source)

    # Timing
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: float = 0.0

    # Erreurs
    errors: List[SyncError] = field(default_factory=list)

    # Métadonnées
    source: str = "prestashop"
    batch_size: int = 100

    @property
    def success_rate(self) -> float:
        """Taux de succès en pourcentage."""
        if self.total_fetched == 0:
            return 100.0
        return ((self.total_fetched - self.total_failed) / self.total_fetched) * 100

    @property
    def is_success(self) -> bool:
        """True si la sync est réussie (complète ou partielle sans erreur critique)."""
        return self.status in (SyncStatus.COMPLETED, SyncStatus.PARTIAL)

    def to_dict(self) -> Dict[str, Any]:
        """Sérialise le résultat pour API/logging."""
        return {
            "tenant_id": self.tenant_id,
            "status": self.status.value,
            "total_fetched": self.total_fetched,
            "total_created": self.total_created,
            "total_updated": self.total_updated,
            "total_unchanged": self.total_unchanged,
            "total_failed": self.total_failed,
            "total_deleted": self.total_deleted,
            "success_rate": round(self.success_rate, 2),
            "duration_seconds": round(self.duration_seconds, 2),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "source": self.source,
            "batch_size": self.batch_size,
            "errors_count": len(self.errors),
            "errors": [e.to_dict() for e in self.errors[:10]],  # Limiter à 10 erreurs
        }


# =============================================================================
# SYNC SERVICE
# =============================================================================

class CatalogSyncService:
    """
    Service de synchronisation du catalogue produits.

    Orchestre la récupération des produits depuis PrestaShop et leur
    persistence en base MySQL avec gestion de la pagination et des erreurs.

    Usage:
        service = CatalogSyncService(repository)
        result = await service.sync_products(
            tenant_id="tenant_123",
            prestashop_config=PrestaShopClientConfig(...)
        )
    """

    DEFAULT_BATCH_SIZE = 100
    MAX_ERRORS_BEFORE_ABORT = 50  # Arrêter si trop d'erreurs
    MAX_SYNC_DURATION_SECONDS = 3600  # 1 heure max
    BULK_UPSERT_CHUNK_SIZE = 500  # Taille max pour un bulk upsert

    def __init__(
        self,
        repository: ProductRepositoryProtocol,
        batch_size: int = DEFAULT_BATCH_SIZE,
        on_progress: Optional[Callable[[int, int], Awaitable[None]]] = None,
    ):
        """
        Initialise le service de synchronisation.

        Args:
            repository: Repository pour la persistence des produits
            batch_size: Taille des batches pour la pagination
            on_progress: Callback optionnel (current, total) pour le suivi
        """
        self._repository = repository
        self._batch_size = min(max(batch_size, 10), 500)  # Entre 10 et 500
        self._on_progress = on_progress

    async def sync_products(
        self,
        tenant_id: str,
        prestashop_config: PrestaShopClientConfig,
        active_only: bool = True,
        delete_missing: bool = False,
        max_duration_seconds: Optional[int] = None,
    ) -> SyncResult:
        """
        Synchronise les produits depuis PrestaShop.

        Processus:
        1. Connexion à PrestaShop
        2. Récupération paginée des produits
        3. Transformation et UPSERT par batch
        4. Optionnel: suppression des produits absents

        Args:
            tenant_id: ID du tenant
            prestashop_config: Configuration PrestaShop du tenant
            active_only: Ne synchroniser que les produits actifs
            delete_missing: Supprimer les produits qui ne sont plus dans PrestaShop
            max_duration_seconds: Durée max de sync (défaut: MAX_SYNC_DURATION_SECONDS)

        Returns:
            SyncResult avec toutes les métriques
        """
        max_duration = max_duration_seconds or self.MAX_SYNC_DURATION_SECONDS

        result = SyncResult(
            tenant_id=tenant_id,
            status=SyncStatus.IN_PROGRESS,
            started_at=datetime.utcnow(),
            batch_size=self._batch_size,
        )

        logger.info(
            "Starting catalog sync",
            extra={
                "tenant_id": tenant_id,
                "active_only": active_only,
                "delete_missing": delete_missing,
                "batch_size": self._batch_size,
            }
        )

        start_time = time.monotonic()
        synced_external_ids: List[int] = []

        try:
            async with PrestaShopClient(prestashop_config, tenant_id) as client:
                # Vérifier la connexion
                if not await client.check_connection():
                    raise PrestaShopConnectionError(
                        message="Failed to connect to PrestaShop",
                        tenant_id=tenant_id,
                    )

                # Synchroniser par batches
                offset = 0

                while True:
                    # Récupérer un batch de produits
                    try:
                        response = await client.get_products(
                            limit=self._batch_size,
                            offset=offset,
                            active_only=active_only,
                        )
                    except PrestaShopError as e:
                        logger.error(
                            "Failed to fetch products batch",
                            extra={
                                "tenant_id": tenant_id,
                                "offset": offset,
                                "error": str(e),
                            }
                        )
                        result.errors.append(SyncError(
                            error_type="fetch_error",
                            error_message=str(e),
                        ))
                        break

                    if not response.products:
                        break

                    # Traiter le batch
                    batch_result = await self._process_batch(
                        tenant_id=tenant_id,
                        products=response.products,
                        result=result,
                    )

                    # Collecter les IDs synchronisés
                    synced_external_ids.extend([p.id for p in response.products])

                    # Callback de progression
                    if self._on_progress:
                        await self._on_progress(
                            result.total_fetched,
                            response.total,
                        )

                    # Vérifier si on doit s'arrêter (trop d'erreurs)
                    if len(result.errors) >= self.MAX_ERRORS_BEFORE_ABORT:
                        logger.warning(
                            "Too many errors, aborting sync",
                            extra={
                                "tenant_id": tenant_id,
                                "errors_count": len(result.errors),
                            }
                        )
                        result.status = SyncStatus.PARTIAL
                        break

                    # Vérifier timeout global
                    elapsed = time.monotonic() - start_time
                    if elapsed >= max_duration:
                        logger.warning(
                            "Sync timeout reached, stopping",
                            extra={
                                "tenant_id": tenant_id,
                                "elapsed_seconds": round(elapsed, 2),
                                "max_duration": max_duration,
                                "products_synced": result.total_fetched,
                            }
                        )
                        result.status = SyncStatus.PARTIAL
                        result.errors.append(SyncError(
                            error_type="timeout",
                            error_message=f"Sync stopped after {round(elapsed)}s (max: {max_duration}s)",
                        ))
                        break

                    # Pagination
                    if not response.has_more:
                        break

                    offset = response.next_offset

                # Supprimer les produits manquants si demandé
                if delete_missing and synced_external_ids:
                    deleted = await self._repository.delete_products_not_in_list(
                        tenant_id=tenant_id,
                        external_ids=synced_external_ids,
                    )
                    result.total_deleted = deleted

                    if deleted > 0:
                        logger.info(
                            "Deleted missing products",
                            extra={
                                "tenant_id": tenant_id,
                                "deleted_count": deleted,
                            }
                        )

        except PrestaShopAuthenticationError as e:
            logger.error(
                "PrestaShop authentication failed",
                extra={"tenant_id": tenant_id, "error": str(e)}
            )
            result.status = SyncStatus.FAILED
            result.errors.append(SyncError(
                error_type="authentication_error",
                error_message="Invalid PrestaShop API key",
            ))

        except PrestaShopConnectionError as e:
            logger.error(
                "PrestaShop connection failed",
                extra={"tenant_id": tenant_id, "error": str(e)}
            )
            result.status = SyncStatus.FAILED
            result.errors.append(SyncError(
                error_type="connection_error",
                error_message=str(e),
            ))

        except Exception as e:
            logger.exception(
                "Unexpected error during sync",
                extra={"tenant_id": tenant_id}
            )
            result.status = SyncStatus.FAILED
            result.errors.append(SyncError(
                error_type="unexpected_error",
                error_message=str(e),
            ))

        finally:
            # Finaliser le résultat
            result.completed_at = datetime.utcnow()
            result.duration_seconds = time.monotonic() - start_time

            # Déterminer le statut final
            if result.status == SyncStatus.IN_PROGRESS:
                if result.total_failed > 0:
                    result.status = SyncStatus.PARTIAL
                else:
                    result.status = SyncStatus.COMPLETED

            # Log final
            logger.info(
                "Catalog sync completed",
                extra=result.to_dict()
            )

        return result

    async def _process_batch(
        self,
        tenant_id: str,
        products: List[PrestaShopProduct],
        result: SyncResult,
    ) -> None:
        """
        Traite un batch de produits.

        Transforme les produits PrestaShop et les persiste en base.
        """
        result.total_fetched += len(products)

        # Transformer les produits
        product_data_list: List[ProductData] = []

        for ps_product in products:
            try:
                product_data = self._transform_product(ps_product)
                product_data_list.append(product_data)
            except Exception as e:
                logger.warning(
                    "Failed to transform product",
                    extra={
                        "tenant_id": tenant_id,
                        "product_id": ps_product.id,
                        "error": str(e),
                    }
                )
                result.total_failed += 1
                result.errors.append(SyncError(
                    product_id=ps_product.id,
                    product_name=ps_product.name,
                    error_type="transform_error",
                    error_message=str(e),
                ))

        # Persister en bulk avec chunking
        if product_data_list:
            try:
                created, updated = await self._upsert_with_chunking(
                    tenant_id=tenant_id,
                    products=product_data_list,
                )

                result.total_created += created
                result.total_updated += updated
                result.total_unchanged += len(product_data_list) - created - updated

                logger.debug(
                    "Batch processed",
                    extra={
                        "tenant_id": tenant_id,
                        "batch_size": len(product_data_list),
                        "created": created,
                        "updated": updated,
                    }
                )

            except Exception as e:
                logger.error(
                    "Failed to persist batch",
                    extra={
                        "tenant_id": tenant_id,
                        "batch_size": len(product_data_list),
                        "error": str(e),
                    }
                )
                result.total_failed += len(product_data_list)
                result.errors.append(SyncError(
                    error_type="persist_error",
                    error_message=str(e),
                ))

    async def _upsert_with_chunking(
        self,
        tenant_id: str,
        products: List[ProductData],
    ) -> tuple[int, int]:
        """
        Effectue l'upsert avec chunking pour éviter les problèmes mémoire.

        Args:
            tenant_id: ID du tenant
            products: Liste des produits à upserter

        Returns:
            Tuple (total_created, total_updated)
        """
        total_created = 0
        total_updated = 0

        # Découper en chunks
        for i in range(0, len(products), self.BULK_UPSERT_CHUNK_SIZE):
            chunk = products[i:i + self.BULK_UPSERT_CHUNK_SIZE]

            created, updated = await self._repository.upsert_products_bulk(
                tenant_id=tenant_id,
                products=chunk,
            )

            total_created += created
            total_updated += updated

            if len(products) > self.BULK_UPSERT_CHUNK_SIZE:
                logger.debug(
                    "Chunk upserted",
                    extra={
                        "tenant_id": tenant_id,
                        "chunk_index": i // self.BULK_UPSERT_CHUNK_SIZE,
                        "chunk_size": len(chunk),
                        "created": created,
                        "updated": updated,
                    }
                )

        return total_created, total_updated

    def _transform_product(self, ps_product: PrestaShopProduct) -> ProductData:
        """
        Transforme un produit PrestaShop vers le format interne.

        Args:
            ps_product: Produit PrestaShop

        Returns:
            ProductData pour persistence
        """
        return ProductData(
            external_id=ps_product.id,
            name=ps_product.name,
            reference=ps_product.reference,
            ean13=ps_product.ean13,
            description=ps_product.description,
            description_short=ps_product.description_short,
            price=ps_product.price,
            price_tax_incl=ps_product.price_tax_incl,
            quantity=ps_product.quantity,
            category_id=ps_product.category_id,
            category_name=ps_product.category_name,
            manufacturer_name=ps_product.manufacturer_name,
            image_url=ps_product.cover_image_url,
            active=ps_product.active,
            available_for_order=ps_product.available_for_order,
            extra_data={
                "tags": ps_product.tags,
                "features": ps_product.features,
                "weight": float(ps_product.weight) if ps_product.weight else None,
                "condition": ps_product.condition,
                "visibility": ps_product.visibility,
            },
        )

    async def get_sync_status(
        self,
        tenant_id: str,
    ) -> Dict[str, Any]:
        """
        Retourne le statut actuel du catalogue d'un tenant.

        Args:
            tenant_id: ID du tenant

        Returns:
            Dictionnaire avec les statistiques du catalogue
        """
        total = await self._repository.count_products(tenant_id)
        active = await self._repository.count_products(
            tenant_id,
            filters=type('obj', (object,), {'active_only': True, 'in_stock_only': False, 'category_id': None})(),
        )

        return {
            "tenant_id": tenant_id,
            "total_products": total,
            "active_products": active,
            "inactive_products": total - active,
        }





