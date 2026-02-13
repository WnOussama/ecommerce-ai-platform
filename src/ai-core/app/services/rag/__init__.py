"""
RAG Services - Indexation et recherche de produits

Ce module contient:
- ProductIndexer: Service d'indexation des produits dans ChromaDB
- ProductRetrievalService: Service de recherche sémantique
- EmbeddingService: Service de génération d'embeddings
"""

from app.services.rag.product_indexer import (
    ProductIndexer,
    IndexResult,
    IndexStatus,
    IndexingError,
)
from app.services.rag.embedding_service import (
    EmbeddingService,
    EmbeddingServiceProtocol,
    MockEmbeddingService,
)
from app.services.rag.retrieval_service import (
    ProductRetrievalService,
    RetrievalResult,
    RetrievedProduct,
    InMemorySearchableVectorStore,
    ChromaSearchableVectorStore,
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


