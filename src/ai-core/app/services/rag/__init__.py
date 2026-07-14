"""
RAG Services - Indexation et recherche de produits

Ce module contient:
- ProductIndexer: Service d'indexation des produits dans ChromaDB
- ProductRetrievalService: Service de recherche sémantique
- EmbeddingService: Service de génération d'embeddings
"""

from app.services.rag.embedding_service import (
    EmbeddingService,
    EmbeddingServiceProtocol,
    MockEmbeddingService,
)
from app.services.rag.product_indexer import (
    IndexingError,
    IndexResult,
    IndexStatus,
    ProductIndexer,
)
from app.services.rag.retrieval_service import (
    ChromaSearchableVectorStore,
    InMemorySearchableVectorStore,
    ProductRetrievalService,
    RetrievalResult,
    RetrievedProduct,
)

__all__ = [
    # Indexer
    "ProductIndexer",
    "IndexResult",
    "IndexStatus",
    "IndexingError",
    # Retrieval
    "ProductRetrievalService",
    "RetrievalResult",
    "RetrievedProduct",
    "InMemorySearchableVectorStore",
    "ChromaSearchableVectorStore",
    # Embedding
    "EmbeddingService",
    "EmbeddingServiceProtocol",
    "MockEmbeddingService",
]
