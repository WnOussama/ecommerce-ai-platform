"""
RAG Services Factory - Initialisation et singleton des services RAG

Ce module fournit des factories pour obtenir des instances
configurées des services RAG (embedding, retrieval).

Usage:
    from app.services.rag.factory import get_retrieval_service

    service = get_retrieval_service()
    result = await service.search_products("téléphone", "tenant_123")
"""

import logging
from typing import Optional

from app.core.config.settings import settings
from app.services.rag.embedding_service import (
    EmbeddingService,
    MockEmbeddingService,
)
from app.services.rag.retrieval_service import (
    ChromaSearchableVectorStore,
    InMemorySearchableVectorStore,
    ProductRetrievalService,
)

logger = logging.getLogger(__name__)

# Singletons
_embedding_service: Optional[EmbeddingService] = None
_vector_store = None
_retrieval_service: Optional[ProductRetrievalService] = None


def get_embedding_service() -> EmbeddingService:
    """
    Retourne une instance du service d'embedding.

    Utilise le mock si pas de clé OpenAI (Groq n'a pas d'API d'embeddings).
    """
    global _embedding_service

    if _embedding_service is None:
        # Vérifier si on doit utiliser le mock
        use_mock = not settings.llm.openai_api_key

        if use_mock:
            logger.info("Using MockEmbeddingService")
            _embedding_service = MockEmbeddingService(dimensions=384)
        else:
            logger.info("Using EmbeddingService with OpenAI")
            _embedding_service = EmbeddingService(
                api_key=settings.llm.openai_api_key,
                model=settings.llm.openai_embedding_model,
                fallback_to_mock=True,
            )

    return _embedding_service


def get_vector_store():
    """
    Retourne une instance du vector store.

    Utilise InMemory en mode dev/test, ChromaDB sinon.
    """
    global _vector_store

    if _vector_store is None:
        # En mode test/dev sans ChromaDB installé, utiliser InMemory
        use_in_memory = settings.is_development

        if use_in_memory:
            try:
                # Essayer d'utiliser ChromaDB même en dev
                _vector_store = ChromaSearchableVectorStore(
                    persist_directory=settings.vector_store.persist_directory
                )
                logger.info("Using ChromaSearchableVectorStore")
            except Exception as e:
                logger.warning(f"ChromaDB not available, using InMemory: {e}")
                _vector_store = InMemorySearchableVectorStore()
        else:
            _vector_store = ChromaSearchableVectorStore(
                persist_directory=settings.vector_store.persist_directory
            )
            logger.info("Using ChromaSearchableVectorStore")

    return _vector_store


def get_retrieval_service() -> ProductRetrievalService:
    """
    Retourne une instance du service de retrieval.

    Initialise automatiquement les dépendances (embedding, vector store).
    """
    global _retrieval_service

    if _retrieval_service is None:
        embedding_service = get_embedding_service()
        vector_store = get_vector_store()

        _retrieval_service = ProductRetrievalService(
            embedding_service=embedding_service,
            vector_store=vector_store,
            default_top_k=5,
            min_similarity=0.3,
        )

        logger.info("ProductRetrievalService initialized")

    return _retrieval_service


def reset_services():
    """
    Réinitialise tous les singletons.

    Utile pour les tests.
    """
    global _embedding_service, _vector_store, _retrieval_service

    _embedding_service = None
    _vector_store = None
    _retrieval_service = None

    logger.debug("RAG services reset")
