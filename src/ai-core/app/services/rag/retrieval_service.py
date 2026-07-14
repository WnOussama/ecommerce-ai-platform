"""
Product Retrieval Service - Recherche sémantique de produits pour RAG

Ce service gère la recherche de produits pertinents pour enrichir
les réponses du chatbot avec du contexte produit.

Caractéristiques:
- Recherche sémantique via embeddings
- Multi-tenant isolation
- Formatage du contexte pour injection LLM
- Fallback gracieux si aucun résultat
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol

from app.services.rag.embedding_service import EmbeddingServiceProtocol

logger = logging.getLogger(__name__)


# =============================================================================
# DATA MODELS
# =============================================================================


@dataclass
class RetrievedProduct:
    """Produit récupéré par la recherche sémantique."""

    product_id: int
    name: str
    price: float
    category: str = ""
    description: str = ""
    reference: str = ""
    in_stock: bool = True
    similarity_score: float = 0.0

    def to_context_string(self) -> str:
        """Formate le produit pour injection dans le prompt LLM."""
        lines = [
            f"- Nom: {self.name}",
            f"  Prix: {self.price:.2f}€",
        ]

        if self.category:
            lines.append(f"  Catégorie: {self.category}")

        if self.description:
            # Limiter la description à 200 caractères
            desc = self.description[:200]
            if len(self.description) > 200:
                desc += "..."
            lines.append(f"  Description: {desc}")

        if self.reference:
            lines.append(f"  Référence: {self.reference}")

        stock_status = "En stock" if self.in_stock else "Rupture de stock"
        lines.append(f"  Disponibilité: {stock_status}")

        return "\n".join(lines)


@dataclass
class RetrievalResult:
    """Résultat d'une recherche de produits."""

    query: str
    tenant_id: str
    products: List[RetrievedProduct] = field(default_factory=list)
    total_found: int = 0
    search_time_ms: float = 0.0

    @property
    def has_results(self) -> bool:
        """True si des produits ont été trouvés."""
        return len(self.products) > 0

    def to_context_string(self) -> str:
        """
        Génère le contexte formaté pour injection dans le prompt LLM.

        Returns:
            Chaîne formatée avec les produits pertinents
        """
        if not self.products:
            return ""

        lines = ["Produits pertinents trouvés dans le catalogue:"]
        lines.append("")

        for product in self.products:
            lines.append(product.to_context_string())
            lines.append("")

        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        """Sérialise pour API/logging."""
        return {
            "query": self.query,
            "tenant_id": self.tenant_id,
            "total_found": self.total_found,
            "products_returned": len(self.products),
            "search_time_ms": round(self.search_time_ms, 2),
            "products": [
                {
                    "product_id": p.product_id,
                    "name": p.name,
                    "price": p.price,
                    "similarity": round(p.similarity_score, 3),
                }
                for p in self.products
            ],
        }


# =============================================================================
# VECTOR STORE PROTOCOL FOR SEARCH
# =============================================================================


class SearchableVectorStoreProtocol(Protocol):
    """Protocol pour les opérations de recherche dans le vector store."""

    async def search(
        self,
        collection_name: str,
        query_embedding: List[float],
        top_k: int = 5,
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Recherche les documents les plus similaires.

        Returns:
            Liste de dicts avec 'id', 'document', 'metadata', 'distance'
        """
        ...

    async def count(
        self,
        collection_name: str,
    ) -> int:
        """Compte les documents dans une collection."""
        ...


# =============================================================================
# IN-MEMORY SEARCHABLE VECTOR STORE (FOR TESTING)
# =============================================================================


class InMemorySearchableVectorStore:
    """
    Vector store en mémoire avec support de recherche pour les tests.
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

    async def search(
        self,
        collection_name: str,
        query_embedding: List[float],
        top_k: int = 5,
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Recherche par similarité cosinus.
        """
        if collection_name not in self._collections:
            return []

        results = []

        for doc_id, doc_data in self._collections[collection_name].items():
            # Appliquer les filtres de metadata si présents
            if filter_metadata:
                metadata = doc_data.get("metadata", {})
                match = all(metadata.get(k) == v for k, v in filter_metadata.items())
                if not match:
                    continue

            # Calculer la similarité cosinus
            doc_embedding = doc_data["embedding"]
            similarity = self._cosine_similarity(query_embedding, doc_embedding)

            results.append(
                {
                    "id": doc_id,
                    "document": doc_data["document"],
                    "metadata": doc_data["metadata"],
                    "distance": 1 - similarity,  # ChromaDB retourne distance, pas similarité
                    "similarity": similarity,
                }
            )

        # Trier par similarité décroissante
        results.sort(key=lambda x: x["similarity"], reverse=True)

        return results[:top_k]

    async def count(
        self,
        collection_name: str,
    ) -> int:
        """Compte les documents."""
        if collection_name not in self._collections:
            return 0
        return len(self._collections[collection_name])

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calcule la similarité cosinus entre deux vecteurs."""
        if len(vec1) != len(vec2):
            return 0.0

        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = sum(a * a for a in vec1) ** 0.5
        norm2 = sum(b * b for b in vec2) ** 0.5

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return dot_product / (norm1 * norm2)


# =============================================================================
# CHROMA SEARCHABLE VECTOR STORE
# =============================================================================


class ChromaSearchableVectorStore:
    """
    Adaptateur ChromaDB avec support de recherche sémantique.
    """

    def __init__(self, persist_directory: str = "./data/chroma"):
        """Initialise le vector store ChromaDB."""
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings

            self._client = chromadb.Client(
                ChromaSettings(
                    chroma_db_impl="duckdb+parquet",
                    persist_directory=persist_directory,
                    anonymized_telemetry=False,
                )
            )
            self._collections: Dict[str, Any] = {}

            logger.info(
                "ChromaSearchableVectorStore initialized",
                extra={"persist_directory": persist_directory},
            )
        except ImportError:
            logger.warning("chromadb not installed, search will not work")
            self._client = None

    def _get_collection(self, collection_name: str):
        """Récupère ou crée une collection."""
        if self._client is None:
            return None

        if collection_name not in self._collections:
            self._collections[collection_name] = self._client.get_or_create_collection(
                name=collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collections[collection_name]

    async def search(
        self,
        collection_name: str,
        query_embedding: List[float],
        top_k: int = 5,
        filter_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Recherche les documents les plus similaires."""
        collection = self._get_collection(collection_name)
        if collection is None:
            return []

        try:
            # Construire les filtres
            where_clause = filter_metadata if filter_metadata else None

            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                where=where_clause,
                include=["documents", "metadatas", "distances"],
            )

            # Formater les résultats
            formatted = []

            if results["ids"] and results["ids"][0]:
                for i, doc_id in enumerate(results["ids"][0]):
                    distance = results["distances"][0][i] if results["distances"] else 0
                    formatted.append(
                        {
                            "id": doc_id,
                            "document": results["documents"][0][i] if results["documents"] else "",
                            "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                            "distance": distance,
                            "similarity": 1 - (distance / 2),  # Approximation pour cosine
                        }
                    )

            return formatted

        except Exception as e:
            logger.error(
                "ChromaDB search failed", extra={"collection": collection_name, "error": str(e)}
            )
            return []

    async def count(
        self,
        collection_name: str,
    ) -> int:
        """Compte les documents."""
        collection = self._get_collection(collection_name)
        if collection is None:
            return 0
        return collection.count()


# =============================================================================
# PRODUCT RETRIEVAL SERVICE
# =============================================================================


class ProductRetrievalService:
    """
    Service de recherche de produits pour le RAG.

    Orchestre:
    1. Génération de l'embedding de la requête
    2. Recherche dans le vector store
    3. Formatage des résultats pour le LLM

    Usage:
        service = ProductRetrievalService(embedding_service, vector_store)
        result = await service.search_products(
            query="téléphone pas cher",
            tenant_id="tenant_123",
            top_k=5
        )
    """

    COLLECTION_PREFIX = "tenant"
    COLLECTION_TYPE = "products"
    DEFAULT_TOP_K = 5
    MIN_SIMILARITY_THRESHOLD = 0.3  # Seuil minimum de pertinence

    def __init__(
        self,
        embedding_service: EmbeddingServiceProtocol,
        vector_store: SearchableVectorStoreProtocol,
        default_top_k: int = DEFAULT_TOP_K,
        min_similarity: float = MIN_SIMILARITY_THRESHOLD,
    ):
        """
        Initialise le service de retrieval.

        Args:
            embedding_service: Service de génération d'embeddings
            vector_store: Vector store avec support de recherche
            default_top_k: Nombre de résultats par défaut
            min_similarity: Seuil minimum de similarité
        """
        self._embedding_service = embedding_service
        self._vector_store = vector_store
        self._default_top_k = default_top_k
        self._min_similarity = min_similarity

    def _get_collection_name(self, tenant_id: str) -> str:
        """Génère le nom de la collection pour un tenant."""
        return f"{self.COLLECTION_PREFIX}_{tenant_id}_{self.COLLECTION_TYPE}"

    async def search_products(
        self,
        query: str,
        tenant_id: str,
        top_k: Optional[int] = None,
        filter_active_only: bool = True,
        filter_in_stock_only: bool = False,
    ) -> RetrievalResult:
        """
        Recherche des produits pertinents pour une requête.

        Args:
            query: Question ou recherche de l'utilisateur
            tenant_id: ID du tenant
            top_k: Nombre maximum de résultats (défaut: 5)
            filter_active_only: Ne retourner que les produits actifs
            filter_in_stock_only: Ne retourner que les produits en stock

        Returns:
            RetrievalResult avec les produits trouvés
        """
        import time

        start_time = time.monotonic()

        collection_name = self._get_collection_name(tenant_id)
        top_k = top_k or self._default_top_k

        result = RetrievalResult(
            query=query,
            tenant_id=tenant_id,
        )

        logger.debug(
            "Starting product search",
            extra={
                "tenant_id": tenant_id,
                "query": query[:100],
                "top_k": top_k,
            },
        )

        try:
            # Vérifier si la collection existe et a des documents
            doc_count = await self._vector_store.count(collection_name)

            if doc_count == 0:
                logger.info("No products indexed for tenant", extra={"tenant_id": tenant_id})
                result.search_time_ms = (time.monotonic() - start_time) * 1000
                return result

            # Générer l'embedding de la requête
            query_embedding = await self._embedding_service.generate_embedding(
                text=query,
                tenant_id=tenant_id,
            )

            # Construire les filtres
            filters = {}
            if filter_active_only:
                filters["active"] = True
            if filter_in_stock_only:
                filters["in_stock"] = True

            # Rechercher dans le vector store
            search_results = await self._vector_store.search(
                collection_name=collection_name,
                query_embedding=query_embedding,
                top_k=top_k,
                filter_metadata=filters if filters else None,
            )

            # Convertir les résultats
            for item in search_results:
                similarity = item.get("similarity", 1 - item.get("distance", 0))

                # Filtrer par seuil de similarité
                if similarity < self._min_similarity:
                    continue

                metadata = item.get("metadata", {})

                product = RetrievedProduct(
                    product_id=metadata.get("product_id", metadata.get("external_id", 0)),
                    name=metadata.get("name", ""),
                    price=metadata.get("price", 0.0),
                    category=metadata.get("category_name", ""),
                    description=item.get("document", "")[:500],  # Limiter
                    reference=metadata.get("reference", ""),
                    in_stock=metadata.get("in_stock", True),
                    similarity_score=similarity,
                )

                result.products.append(product)

            result.total_found = len(result.products)

        except Exception as e:
            logger.error(
                "Product search failed",
                extra={
                    "tenant_id": tenant_id,
                    "query": query[:100],
                    "error": str(e),
                },
            )
            # Retourner résultat vide en cas d'erreur (fallback gracieux)

        finally:
            result.search_time_ms = (time.monotonic() - start_time) * 1000

            logger.info("Product search completed", extra=result.to_dict())

        return result

    async def get_products_context(
        self,
        query: str,
        tenant_id: str,
        top_k: Optional[int] = None,
    ) -> str:
        """
        Méthode de convenance pour obtenir directement le contexte formaté.

        Args:
            query: Question de l'utilisateur
            tenant_id: ID du tenant
            top_k: Nombre de résultats

        Returns:
            Contexte formaté pour injection dans le prompt, ou chaîne vide
        """
        result = await self.search_products(
            query=query,
            tenant_id=tenant_id,
            top_k=top_k,
        )

        return result.to_context_string()

    async def has_indexed_products(self, tenant_id: str) -> bool:
        """
        Vérifie si le tenant a des produits indexés.

        Args:
            tenant_id: ID du tenant

        Returns:
            True si des produits sont indexés
        """
        collection_name = self._get_collection_name(tenant_id)

        try:
            count = await self._vector_store.count(collection_name)
            return count > 0
        except Exception:
            return False
