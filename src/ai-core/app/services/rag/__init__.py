"""
RAG Services - Indexation et recherche de produits

Ce module contient:
- ProductIndexer: Service d'indexation des produits dans ChromaDB
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

__all__ = [
    # Indexer
    "ProductIndexer",
    "IndexResult",
    "IndexStatus",
    "IndexingError",
    # Embedding
    "EmbeddingService",
    "EmbeddingServiceProtocol",
    "MockEmbeddingService",
]


