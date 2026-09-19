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
import os
from typing import Optional

from app.core.config.settings import settings
from app.services.rag.embedding_service import (
    EmbeddingService,
    LocalEmbeddingService,
)
from app.services.rag.retrieval_service import (
    ChromaSearchableVectorStore,
    ProductRetrievalService,
)

logger = logging.getLogger(__name__)

# Singletons
_embedding_service = None
_vector_store = None
_retrieval_service: Optional[ProductRetrievalService] = None


def get_embedding_service():
    """
    Retourne une instance du service d'embedding (toujours un vrai modèle).

    OpenAI si une clé est configurée, sinon le modèle local all-MiniLM-L6-v2
    (Groq n'a pas d'API d'embeddings). Pas de mock, pas de repli silencieux.
    """
    global _embedding_service

    if _embedding_service is None:
        if settings.llm.openai_api_key:
            logger.info("Using EmbeddingService with OpenAI")
            _embedding_service = EmbeddingService(
                api_key=settings.llm.openai_api_key,
                model=settings.llm.openai_embedding_model,
            )
        else:
            model_dir = os.path.join(
                os.path.dirname(settings.vector_store.persist_directory.rstrip("/")),
                "onnx_models",
                LocalEmbeddingService.MODEL_NAME,
            )
            logger.info("Using LocalEmbeddingService (all-MiniLM-L6-v2)")
            _embedding_service = LocalEmbeddingService(model_dir=model_dir)

    return _embedding_service


def get_vector_store():
    """
    Retourne le vector store ChromaDB (embarqué, persistant).

    Si ChromaDB est indisponible l'erreur remonte : pas de repli silencieux
    vers un store en mémoire qui perdrait les données au redémarrage.
    """
    global _vector_store

    if _vector_store is None:
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
