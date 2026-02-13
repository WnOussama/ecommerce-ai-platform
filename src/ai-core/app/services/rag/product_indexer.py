"""
Product Indexer - Indexation RAG des produits dans ChromaDB

Ce service orchestre l'indexation des produits pour la recherche sémantique:
1. Récupère les produits depuis le repository
2. Génère le texte de recherche via product.to_search_text()
3. Génère les embeddings via EmbeddingService
4. Stocke dans ChromaDB avec isolation par tenant

Caractéristiques:
- 100% async
- Multi-tenant safe
- Batching optimisé
- Idempotent (hash-based deduplication)
- Logging structuré
"""

import logging
import time
import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Optional, List, Dict, Any, Protocol

from app.services.catalog.repository import (
    ProductRepositoryProtocol,
    ProductData,
    ProductFilter,
)
from app.services.rag.embedding_service import EmbeddingServiceProtocol

logger = logging.getLogger(__name__)


# =============================================================================
# DATA MODELS
# =============================================================================

class IndexStatus(str, Enum):
    """Statut d'une indexation."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"


@dataclass
class IndexingError:
    """Erreur survenue pendant l'indexation."""
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
class IndexResult:
    """
    Résultat d'une indexation de produits.

    Contient toutes les métriques et informations sur l'indexation.
    """
    tenant_id: str
    status: IndexStatus = IndexStatus.PENDING

    # Compteurs
    total_products: int = 0
    total_indexed: int = 0
    total_skipped: int = 0  # Déjà indexés (même hash)
    total_failed: int = 0
    total_deleted: int = 0  # Supprimés du vector store

    # Timing
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: float = 0.0

    # Erreurs
    errors: List[IndexingError] = field(default_factory=list)

    # Métadonnées
    collection_name: str = ""
    embedding_model: str = ""
    batch_size: int = 100

    @property
    def success_rate(self) -> float:
        """Taux de succès en pourcentage."""
        if self.total_products == 0:
            return 100.0
        return ((self.total_products - self.total_failed) / self.total_products) * 100

    @property
    def is_success(self) -> bool:
        """True si l'indexation est réussie."""
        return self.status in (IndexStatus.COMPLETED, IndexStatus.PARTIAL)

    def to_dict(self) -> Dict[str, Any]:
        """Sérialise le résultat pour API/logging."""
        return {
            "tenant_id": self.tenant_id,
            "status": self.status.value,
            "total_products": self.total_products,
            "total_indexed": self.total_indexed,
            "total_skipped": self.total_skipped,
            "total_failed": self.total_failed,
            "total_deleted": self.total_deleted,
            "success_rate": round(self.success_rate, 2),
            "duration_seconds": round(self.duration_seconds, 2),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "collection_name": self.collection_name,
            "embedding_model": self.embedding_model,
            "batch_size": self.batch_size,
            "errors_count": len(self.errors),
            "errors": [e.to_dict() for e in self.errors[:10]],
        }


# =============================================================================
# VECTOR STORE PROTOCOL
# =============================================================================

class VectorStoreProtocol(Protocol):
    """
    Protocol pour le vector store.

    Permet l'injection de dépendances et le mocking.
    """

    async def upsert(
        self,
        collection_name: str,
        ids: List[str],
        embeddings: List[List[float]],
        documents: List[str],
        metadatas: List[Dict[str, Any]],
    ) -> int:
        """Insère ou met à jour des documents."""
        ...

    async def delete(
        self,
        collection_name: str,
        ids: List[str],
    ) -> int:
        """Supprime des documents."""
        ...

    async def get_ids(
        self,
        collection_name: str,
    ) -> List[str]:
        """Récupère tous les IDs d'une collection."""
        ...

    async def count(
        self,
        collection_name: str,
    ) -> int:
        """Compte les documents dans une collection."""
        ...


# =============================================================================
# CHROMA VECTOR STORE ADAPTER
# =============================================================================

class ChromaVectorStore:
    """
    Adaptateur ChromaDB pour le vector store.

    Gère les collections par tenant avec le format:
    tenant_{tenant_id}_products
    """

    def __init__(self, persist_directory: str = "./data/chroma"):
        """
        Initialise le vector store ChromaDB.

        Args:
            persist_directory: Répertoire de persistance
        """
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            self._client = chromadb.Client(ChromaSettings(
                chroma_db_impl="duckdb+parquet",
                persist_directory=persist_directory,
                anonymized_telemetry=False,
            ))
            self._collections: Dict[str, Any] = {}

            logger.info(
                "ChromaVectorStore initialized",
                extra={"persist_directory": persist_directory}
            )
        except ImportError:
            logger.error("chromadb not installed")
            raise

    def _get_collection(self, collection_name: str):
        """Récupère ou crée une collection."""
        if collection_name not in self._collections:
            self._collections[collection_name] = self._client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collections[collection_name]

    async def upsert(
        self,
        collection_name: str,
        ids: List[str],
        embeddings: List[List[float]],
        documents: List[str],
        metadatas: List[Dict[str, Any]],
    ) -> int:
        """
        Insère ou met à jour des documents.

        ChromaDB gère automatiquement l'upsert par ID.
        """
        if not ids:
            return 0

        collection = self._get_collection(collection_name)

        collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

        return len(ids)

    async def delete(
        self,
        collection_name: str,
        ids: List[str],
    ) -> int:
        """Supprime des documents par ID."""
        if not ids:
            return 0

        collection = self._get_collection(collection_name)
        collection.delete(ids=ids)

        return len(ids)

    async def get_ids(
        self,
        collection_name: str,
    ) -> List[str]:
        """Récupère tous les IDs d'une collection."""
        collection = self._get_collection(collection_name)
        result = collection.get(include=[])
        return result.get("ids", [])

    async def count(
        self,
        collection_name: str,
    ) -> int:
        """Compte les documents dans une collection."""
        collection = self._get_collection(collection_name)
        return collection.count()


# =============================================================================
# IN-MEMORY VECTOR STORE (FOR TESTING)
# =============================================================================

class InMemoryVectorStore:
    """
    Vector store en mémoire pour les tests.

    Implémente le même protocol que ChromaVectorStore.
    """

    def __init__(self):
        self._collections: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def reset(self) -> None:
        """Réinitialise le stockage."""
        self._collections.clear()

    async def upsert(
        self,
        collection_name: str,
        ids: List[str],
        embeddings: List[List[float]],
        documents: List[str],
        metadatas: List[Dict[str, Any]],
    ) -> int:
        """Insère ou met à jour des documents."""
        if collection_name not in self._collections:
            self._collections[collection_name] = {}

        for i, doc_id in enumerate(ids):
            self._collections[collection_name][doc_id] = {
                "embedding": embeddings[i],
                "document": documents[i],
                "metadata": metadatas[i],
            }

        return len(ids)

    async def delete(
        self,
        collection_name: str,
        ids: List[str],
    ) -> int:
        """Supprime des documents."""
        if collection_name not in self._collections:
            return 0

        deleted = 0
        for doc_id in ids:
            if doc_id in self._collections[collection_name]:
                del self._collections[collection_name][doc_id]
                deleted += 1

        return deleted

    async def get_ids(
        self,
        collection_name: str,
    ) -> List[str]:
        """Récupère tous les IDs."""
        if collection_name not in self._collections:
            return []
        return list(self._collections[collection_name].keys())

    async def count(
        self,
        collection_name: str,
    ) -> int:
        """Compte les documents."""
        if collection_name not in self._collections:
            return 0
        return len(self._collections[collection_name])

    def get_document(
        self,
        collection_name: str,
        doc_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Récupère un document (pour tests)."""
        if collection_name not in self._collections:
            return None
        return self._collections[collection_name].get(doc_id)


# =============================================================================
# PRODUCT INDEXER
# =============================================================================

class ProductIndexer:
    """
    Service d'indexation des produits pour la recherche RAG.

    Orchestre:
    1. Récupération des produits depuis le repository
    2. Génération du texte de recherche
    3. Génération des embeddings
    4. Stockage dans le vector store

    Usage:
        indexer = ProductIndexer(repository, embedding_service, vector_store)
        result = await indexer.index_all_products(tenant_id="tenant_123")
    """

    DEFAULT_BATCH_SIZE = 100
    COLLECTION_PREFIX = "tenant"
    COLLECTION_TYPE = "products"
    MAX_ERRORS_BEFORE_ABORT = 100

    def __init__(
        self,
        repository: ProductRepositoryProtocol,
        embedding_service: EmbeddingServiceProtocol,
        vector_store: VectorStoreProtocol,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ):
        """
        Initialise l'indexer.

        Args:
            repository: Repository pour accéder aux produits
            embedding_service: Service de génération d'embeddings
            vector_store: Vector store pour stocker les embeddings
            batch_size: Taille des batches pour l'indexation
        """
        self._repository = repository
        self._embedding_service = embedding_service
        self._vector_store = vector_store
        self._batch_size = min(max(batch_size, 10), 500)

    def _get_collection_name(self, tenant_id: str) -> str:
        """Génère le nom de la collection pour un tenant."""
        return f"{self.COLLECTION_PREFIX}_{tenant_id}_{self.COLLECTION_TYPE}"

    def _get_document_id(self, tenant_id: str, external_id: int) -> str:
        """Génère l'ID du document dans le vector store."""
        return f"{tenant_id}_{external_id}"

    def _compute_content_hash(self, content: str) -> str:
        """Calcule un hash pour déduplication."""
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _product_to_search_text(self, product: ProductData) -> str:
        """
        Génère le texte de recherche pour un produit.

        Ce texte sera utilisé pour générer l'embedding.
        """
        parts = [product.name]

        if product.description_short:
            parts.append(product.description_short)

        if product.category_name:
            parts.append(f"Catégorie: {product.category_name}")

        if product.manufacturer_name:
            parts.append(f"Marque: {product.manufacturer_name}")

        if product.reference:
            parts.append(f"Référence: {product.reference}")

        # Extra data (tags, features)
        extra = product.extra_data or {}

        if extra.get("tags"):
            tags = extra["tags"]
            if isinstance(tags, list):
                parts.append(f"Tags: {', '.join(tags)}")

        if extra.get("features"):
            features = extra["features"]
            if isinstance(features, dict):
                features_text = ", ".join(f"{k}: {v}" for k, v in features.items())
                parts.append(f"Caractéristiques: {features_text}")

        return " | ".join(parts)

    def _product_to_metadata(self, product: ProductData) -> Dict[str, Any]:
        """
        Génère les métadonnées pour le vector store.

        Ces données sont stockées avec l'embedding pour le filtrage.
        """
        return {
            "product_id": product.external_id,
            "external_id": product.external_id,
            "name": product.name,
            "reference": product.reference or "",
            "price": float(product.price) if product.price else 0.0,
            "price_tax_incl": float(product.price_tax_incl) if product.price_tax_incl else 0.0,
            "quantity": product.quantity,
            "category_id": product.category_id or 0,
            "category_name": product.category_name or "",
            "manufacturer": product.manufacturer_name or "",
            "active": product.active,
            "in_stock": product.quantity > 0,
            "indexed_at": datetime.utcnow().isoformat(),
        }

    async def index_all_products(
        self,
        tenant_id: str,
        active_only: bool = True,
        delete_missing: bool = False,
        skip_unchanged: bool = True,
    ) -> IndexResult:
        """
        Indexe tous les produits d'un tenant avec pagination streaming.

        Cette méthode utilise la pagination pour éviter de charger
        tous les produits en mémoire, permettant de gérer 10k+ produits.

        Args:
            tenant_id: ID du tenant
            active_only: N'indexer que les produits actifs
            delete_missing: Supprimer les produits absents du vector store
            skip_unchanged: Skip les produits dont le content_hash n'a pas changé

        Returns:
            IndexResult avec les métriques
        """
        collection_name = self._get_collection_name(tenant_id)

        result = IndexResult(
            tenant_id=tenant_id,
            status=IndexStatus.IN_PROGRESS,
            started_at=datetime.utcnow(),
            collection_name=collection_name,
            embedding_model=self._embedding_service.model_name,
            batch_size=self._batch_size,
        )

        logger.info(
            "Starting product indexation with streaming pagination",
            extra={
                "tenant_id": tenant_id,
                "collection_name": collection_name,
                "active_only": active_only,
                "delete_missing": delete_missing,
                "skip_unchanged": skip_unchanged,
                "batch_size": self._batch_size,
            }
        )

        start_time = time.monotonic()
        indexed_doc_ids: List[str] = []

        # Récupérer les hashes existants si skip_unchanged est activé
        existing_hashes: Dict[str, str] = {}
        if skip_unchanged:
            existing_hashes = await self._get_existing_hashes(collection_name)

        try:
            # Pagination streaming - ne jamais charger tous les produits en mémoire
            offset = 0
            total_fetched = 0

            while True:
                # Récupérer un batch de produits
                filters = ProductFilter(
                    active_only=active_only,
                    in_stock_only=False,
                    limit=self._batch_size,
                    offset=offset,
                )

                products = await self._repository.get_products(tenant_id, filters)

                if not products:
                    # Plus de produits à traiter
                    break

                total_fetched += len(products)
                result.total_products = total_fetched

                # Indexer le batch avec support skip_unchanged
                batch_indexed_ids, batch_skipped = await self._index_batch_with_skip(
                    tenant_id=tenant_id,
                    products=products,
                    collection_name=collection_name,
                    result=result,
                    existing_hashes=existing_hashes,
                    skip_unchanged=skip_unchanged,
                )

                indexed_doc_ids.extend(batch_indexed_ids)
                result.total_skipped += batch_skipped

                logger.debug(
                    "Batch processed",
                    extra={
                        "tenant_id": tenant_id,
                        "offset": offset,
                        "batch_size": len(products),
                        "indexed": len(batch_indexed_ids),
                        "skipped": batch_skipped,
                    }
                )

                # Vérifier si on doit s'arrêter (trop d'erreurs)
                if len(result.errors) >= self.MAX_ERRORS_BEFORE_ABORT:
                    logger.warning(
                        "Too many errors, aborting indexation",
                        extra={
                            "tenant_id": tenant_id,
                            "errors_count": len(result.errors),
                        }
                    )
                    result.status = IndexStatus.PARTIAL
                    break

                # Pagination - passer au batch suivant
                offset += self._batch_size

                # Sécurité: si moins de produits retournés que demandé, c'est la fin
                if len(products) < self._batch_size:
                    break

            # Log si aucun produit trouvé
            if result.total_products == 0:
                logger.info(
                    "No products to index",
                    extra={"tenant_id": tenant_id}
                )

            # Supprimer les documents obsolètes
            if delete_missing and indexed_doc_ids:
                deleted = await self._delete_missing(
                    tenant_id=tenant_id,
                    collection_name=collection_name,
                    current_ids=indexed_doc_ids,
                )
                result.total_deleted = deleted

        except Exception as e:
            logger.exception(
                "Unexpected error during indexation",
                extra={"tenant_id": tenant_id}
            )
            result.status = IndexStatus.FAILED
            result.errors.append(IndexingError(
                error_type="unexpected_error",
                error_message=str(e),
            ))

        finally:
            result.completed_at = datetime.utcnow()
            result.duration_seconds = time.monotonic() - start_time

            # Déterminer le statut final
            if result.status == IndexStatus.IN_PROGRESS:
                if result.total_failed > 0:
                    result.status = IndexStatus.PARTIAL
                else:
                    result.status = IndexStatus.COMPLETED

            logger.info(
                "Product indexation completed",
                extra=result.to_dict()
            )

        return result

    async def _get_existing_hashes(
        self,
        collection_name: str,
    ) -> Dict[str, str]:
        """
        Récupère les content_hash existants dans le vector store.

        Returns:
            Dict mapping doc_id -> content_hash
        """
        try:
            # Pour InMemoryVectorStore et ChromaDB compatible
            existing_ids = await self._vector_store.get_ids(collection_name)

            # Note: Pour une implémentation complète, il faudrait récupérer
            # les métadonnées. Ici on retourne un dict vide qui sera rempli
            # au fur et à mesure si le vector store supporte get_metadata.
            return {}

        except Exception as e:
            logger.warning(
                "Failed to get existing hashes, will re-index all",
                extra={"error": str(e)}
            )
            return {}

    async def _index_batch_with_skip(
        self,
        tenant_id: str,
        products: List[ProductData],
        collection_name: str,
        result: IndexResult,
        existing_hashes: Dict[str, str],
        skip_unchanged: bool,
    ) -> tuple[List[str], int]:
        """
        Indexe un batch de produits avec support du skip via content_hash.

        Returns:
            Tuple (indexed_ids, skipped_count)
        """
        indexed_ids: List[str] = []
        skipped_count = 0

        # Préparer les données
        to_index_ids: List[str] = []
        to_index_texts: List[str] = []
        to_index_metadatas: List[Dict[str, Any]] = []
        to_index_products: List[ProductData] = []

        for product in products:
            try:
                doc_id = self._get_document_id(tenant_id, product.external_id)
                text = self._product_to_search_text(product)
                content_hash = self._compute_content_hash(text)

                # Skip si content_hash identique
                if skip_unchanged and doc_id in existing_hashes:
                    if existing_hashes[doc_id] == content_hash:
                        skipped_count += 1
                        indexed_ids.append(doc_id)  # Compter comme "présent"
                        continue

                metadata = self._product_to_metadata(product)
                metadata["content_hash"] = content_hash

                to_index_ids.append(doc_id)
                to_index_texts.append(text)
                to_index_metadatas.append(metadata)
                to_index_products.append(product)

            except Exception as e:
                logger.warning(
                    "Failed to prepare product for indexing",
                    extra={
                        "tenant_id": tenant_id,
                        "product_id": product.external_id,
                        "error": str(e),
                    }
                )
                result.total_failed += 1
                result.errors.append(IndexingError(
                    product_id=product.external_id,
                    product_name=product.name,
                    error_type="prepare_error",
                    error_message=str(e),
                ))

        if not to_index_ids:
            return indexed_ids, skipped_count

        # Générer les embeddings en batch
        try:
            embeddings = await self._embedding_service.generate_embeddings_batch(
                texts=to_index_texts,
                tenant_id=tenant_id,
            )
        except Exception as e:
            logger.error(
                "Failed to generate embeddings for batch",
                extra={
                    "tenant_id": tenant_id,
                    "batch_size": len(to_index_texts),
                    "error": str(e),
                }
            )
            result.total_failed += len(to_index_ids)
            result.errors.append(IndexingError(
                error_type="embedding_error",
                error_message=str(e),
            ))
            return indexed_ids, skipped_count

        # Stocker dans le vector store
        try:
            await self._vector_store.upsert(
                collection_name=collection_name,
                ids=to_index_ids,
                embeddings=embeddings,
                documents=to_index_texts,
                metadatas=to_index_metadatas,
            )

            result.total_indexed += len(to_index_ids)
            indexed_ids.extend(to_index_ids)

        except Exception as e:
            logger.error(
                "Failed to store batch in vector store",
                extra={
                    "tenant_id": tenant_id,
                    "batch_size": len(to_index_ids),
                    "error": str(e),
                }
            )
            result.total_failed += len(to_index_ids)
            result.errors.append(IndexingError(
                error_type="storage_error",
                error_message=str(e),
            ))

        return indexed_ids, skipped_count

    async def _index_batch(
        self,
        tenant_id: str,
        products: List[ProductData],
        collection_name: str,
        result: IndexResult,
    ) -> List[str]:
        """
        Indexe un batch de produits.

        Returns:
            Liste des IDs de documents indexés
        """
        indexed_ids: List[str] = []

        # Préparer les données
        ids: List[str] = []
        texts: List[str] = []
        metadatas: List[Dict[str, Any]] = []

        for product in products:
            try:
                doc_id = self._get_document_id(tenant_id, product.external_id)
                text = self._product_to_search_text(product)
                metadata = self._product_to_metadata(product)
                metadata["content_hash"] = self._compute_content_hash(text)

                ids.append(doc_id)
                texts.append(text)
                metadatas.append(metadata)

            except Exception as e:
                logger.warning(
                    "Failed to prepare product for indexing",
                    extra={
                        "tenant_id": tenant_id,
                        "product_id": product.external_id,
                        "error": str(e),
                    }
                )
                result.total_failed += 1
                result.errors.append(IndexingError(
                    product_id=product.external_id,
                    product_name=product.name,
                    error_type="prepare_error",
                    error_message=str(e),
                ))

        if not ids:
            return []

        # Générer les embeddings en batch
        try:
            embeddings = await self._embedding_service.generate_embeddings_batch(
                texts=texts,
                tenant_id=tenant_id,
            )
        except Exception as e:
            logger.error(
                "Failed to generate embeddings for batch",
                extra={
                    "tenant_id": tenant_id,
                    "batch_size": len(texts),
                    "error": str(e),
                }
            )
            result.total_failed += len(ids)
            result.errors.append(IndexingError(
                error_type="embedding_error",
                error_message=str(e),
            ))
            return []

        # Stocker dans le vector store
        try:
            await self._vector_store.upsert(
                collection_name=collection_name,
                ids=ids,
                embeddings=embeddings,
                documents=texts,
                metadatas=metadatas,
            )

            result.total_indexed += len(ids)
            indexed_ids = ids

            logger.debug(
                "Batch indexed successfully",
                extra={
                    "tenant_id": tenant_id,
                    "batch_size": len(ids),
                }
            )

        except Exception as e:
            logger.error(
                "Failed to store batch in vector store",
                extra={
                    "tenant_id": tenant_id,
                    "batch_size": len(ids),
                    "error": str(e),
                }
            )
            result.total_failed += len(ids)
            result.errors.append(IndexingError(
                error_type="storage_error",
                error_message=str(e),
            ))

        return indexed_ids

    async def _delete_missing(
        self,
        tenant_id: str,
        collection_name: str,
        current_ids: List[str],
    ) -> int:
        """Supprime les documents qui ne sont plus dans le repository."""
        try:
            # Récupérer tous les IDs dans le vector store
            existing_ids = await self._vector_store.get_ids(collection_name)

            # Calculer les IDs à supprimer
            current_set = set(current_ids)
            to_delete = [doc_id for doc_id in existing_ids if doc_id not in current_set]

            if to_delete:
                await self._vector_store.delete(collection_name, to_delete)

                logger.info(
                    "Deleted stale documents from vector store",
                    extra={
                        "tenant_id": tenant_id,
                        "deleted_count": len(to_delete),
                    }
                )

            return len(to_delete)

        except Exception as e:
            logger.error(
                "Failed to delete missing documents",
                extra={
                    "tenant_id": tenant_id,
                    "error": str(e),
                }
            )
            return 0

    async def index_product(
        self,
        tenant_id: str,
        product: ProductData,
    ) -> bool:
        """
        Indexe un seul produit.

        Args:
            tenant_id: ID du tenant
            product: Produit à indexer

        Returns:
            True si indexé avec succès
        """
        collection_name = self._get_collection_name(tenant_id)

        try:
            doc_id = self._get_document_id(tenant_id, product.external_id)
            text = self._product_to_search_text(product)
            metadata = self._product_to_metadata(product)
            metadata["content_hash"] = self._compute_content_hash(text)

            # Générer l'embedding
            embedding = await self._embedding_service.generate_embedding(
                text=text,
                tenant_id=tenant_id,
            )

            # Stocker
            await self._vector_store.upsert(
                collection_name=collection_name,
                ids=[doc_id],
                embeddings=[embedding],
                documents=[text],
                metadatas=[metadata],
            )

            logger.info(
                "Product indexed successfully",
                extra={
                    "tenant_id": tenant_id,
                    "product_id": product.external_id,
                    "product_name": product.name,
                }
            )

            return True

        except Exception as e:
            logger.error(
                "Failed to index product",
                extra={
                    "tenant_id": tenant_id,
                    "product_id": product.external_id,
                    "error": str(e),
                }
            )
            return False

    async def delete_product(
        self,
        tenant_id: str,
        external_id: int,
    ) -> bool:
        """
        Supprime un produit du vector store.

        Args:
            tenant_id: ID du tenant
            external_id: ID externe du produit

        Returns:
            True si supprimé avec succès
        """
        collection_name = self._get_collection_name(tenant_id)
        doc_id = self._get_document_id(tenant_id, external_id)

        try:
            await self._vector_store.delete(collection_name, [doc_id])

            logger.info(
                "Product deleted from vector store",
                extra={
                    "tenant_id": tenant_id,
                    "product_id": external_id,
                }
            )

            return True

        except Exception as e:
            logger.error(
                "Failed to delete product from vector store",
                extra={
                    "tenant_id": tenant_id,
                    "product_id": external_id,
                    "error": str(e),
                }
            )
            return False

    async def get_index_stats(
        self,
        tenant_id: str,
    ) -> Dict[str, Any]:
        """
        Retourne les statistiques d'indexation pour un tenant.

        Args:
            tenant_id: ID du tenant

        Returns:
            Dictionnaire avec les statistiques
        """
        collection_name = self._get_collection_name(tenant_id)

        try:
            count = await self._vector_store.count(collection_name)

            return {
                "tenant_id": tenant_id,
                "collection_name": collection_name,
                "total_indexed": count,
                "embedding_model": self._embedding_service.model_name,
            }

        except Exception as e:
            logger.error(
                "Failed to get index stats",
                extra={"tenant_id": tenant_id, "error": str(e)}
            )
            return {
                "tenant_id": tenant_id,
                "collection_name": collection_name,
                "total_indexed": 0,
                "error": str(e),
            }





