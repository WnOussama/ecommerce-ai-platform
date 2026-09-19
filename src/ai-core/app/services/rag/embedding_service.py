"""
Embedding Service - Génération d'embeddings multi-provider

Ce service abstrait la génération d'embeddings pour:
- Modèle local réel (all-MiniLM-L6-v2, sans clé API)
- OpenAI (optionnel, si une clé est fournie)
- Batching optimisé
- Multi-tenant safe
"""

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import List, Optional, Protocol

logger = logging.getLogger(__name__)


# =============================================================================
# TYPES
# =============================================================================


@dataclass
class EmbeddingResult:
    """Résultat d'une génération d'embedding."""

    embedding: List[float]
    model: str
    dimensions: int
    tokens_used: int = 0


# =============================================================================
# PROTOCOL
# =============================================================================


class EmbeddingServiceProtocol(Protocol):
    """
    Protocol pour les services d'embedding.

    Permet l'injection de dépendances et le mocking.
    """

    async def generate_embedding(
        self,
        text: str,
        tenant_id: Optional[str] = None,
    ) -> List[float]:
        """Génère un embedding pour un texte."""
        ...

    async def generate_embeddings_batch(
        self,
        texts: List[str],
        tenant_id: Optional[str] = None,
    ) -> List[List[float]]:
        """Génère des embeddings pour plusieurs textes."""
        ...

    @property
    def dimensions(self) -> int:
        """Retourne la dimension des embeddings."""
        ...

    @property
    def model_name(self) -> str:
        """Retourne le nom du modèle."""
        ...


# =============================================================================
# LOCAL EMBEDDING SERVICE (real model, no API key)
# =============================================================================


class LocalEmbeddingService:
    """
    Vrai modèle d'embedding local : all-MiniLM-L6-v2 (ONNX, 384 dimensions),
    le même que celui livré avec ChromaDB. Aucune clé API : Groq n'a pas
    d'API d'embeddings, et OpenAI est optionnel.

    Le modèle (~80 Mo) est téléchargé une seule fois dans `model_dir`, puis
    chargé paresseusement au premier appel. L'inférence tourne dans un
    thread pour ne pas bloquer la boucle asyncio.
    """

    DEFAULT_DIMENSIONS = 384
    MODEL_NAME = "all-MiniLM-L6-v2"

    def __init__(self, model_dir: Optional[str] = None):
        self._model_dir = model_dir
        self._fn = None
        self._lock = threading.Lock()

    @property
    def dimensions(self) -> int:
        return self.DEFAULT_DIMENSIONS

    @property
    def model_name(self) -> str:
        return self.MODEL_NAME

    def _load(self):
        with self._lock:
            if self._fn is None:
                from chromadb.utils.embedding_functions import ONNXMiniLM_L6_V2

                if self._model_dir:
                    ONNXMiniLM_L6_V2.DOWNLOAD_PATH = self._model_dir
                self._fn = ONNXMiniLM_L6_V2()
                logger.info("Local embedding model ready", extra={"model": self.MODEL_NAME})
        return self._fn

    def _encode(self, texts: List[str]) -> List[List[float]]:
        return [[float(x) for x in vec] for vec in self._load()(texts)]

    async def generate_embedding(
        self,
        text: str,
        tenant_id: Optional[str] = None,
    ) -> List[float]:
        return (await self.generate_embeddings_batch([text], tenant_id))[0]

    async def generate_embeddings_batch(
        self,
        texts: List[str],
        tenant_id: Optional[str] = None,
    ) -> List[List[float]]:
        if not texts:
            return []
        return await asyncio.to_thread(self._encode, texts)


# =============================================================================
# OPENAI EMBEDDING SERVICE
# =============================================================================


class EmbeddingService:
    """
    Service d'embedding avec OpenAI.

    Supporte:
    - Génération d'embeddings unitaires
    - Batching optimisé
    - Rate limiting

    Usage:
        service = EmbeddingService(api_key="sk-...")
        embedding = await service.generate_embedding("Hello world")
    """

    MAX_BATCH_SIZE = 100  # Limite OpenAI
    DEFAULT_MODEL = "text-embedding-3-small"
    DEFAULT_DIMENSIONS = 1536

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        dimensions: int = DEFAULT_DIMENSIONS,
    ):
        """
        Initialise le service d'embedding.

        Args:
            api_key: Clé API OpenAI
            model: Modèle d'embedding à utiliser
            dimensions: Dimension des embeddings
        """
        self._api_key = api_key
        self._model = model
        self._dimensions = dimensions
        self._client = None

        if not api_key:
            raise ValueError("EmbeddingService requires an OpenAI API key")

        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key)
        logger.info(
            "EmbeddingService initialized with OpenAI",
            extra={"model": model, "dimensions": dimensions},
        )

    @property
    def dimensions(self) -> int:
        """Dimension des embeddings."""
        return self._dimensions

    @property
    def model_name(self) -> str:
        """Nom du modèle."""
        return self._model

    async def generate_embedding(
        self,
        text: str,
        tenant_id: Optional[str] = None,
    ) -> List[float]:
        """
        Génère un embedding pour un texte.

        Args:
            text: Texte à encoder
            tenant_id: ID du tenant (pour logging)

        Returns:
            Embedding sous forme de liste de floats
        """
        try:
            response = await self._client.embeddings.create(
                model=self._model,
                input=text,
            )
            return response.data[0].embedding

        except Exception as e:
            logger.error(
                "Failed to generate embedding", extra={"tenant_id": tenant_id, "error": str(e)}
            )

            raise

    async def generate_embeddings_batch(
        self,
        texts: List[str],
        tenant_id: Optional[str] = None,
    ) -> List[List[float]]:
        """
        Génère des embeddings pour plusieurs textes.

        Optimisé pour les grands volumes avec batching automatique.

        Args:
            texts: Liste de textes à encoder
            tenant_id: ID du tenant (pour logging)

        Returns:
            Liste d'embeddings
        """
        if not texts:
            return []

        # OpenAI avec batching
        all_embeddings: List[List[float]] = []

        for i in range(0, len(texts), self.MAX_BATCH_SIZE):
            batch = texts[i : i + self.MAX_BATCH_SIZE]

            try:
                response = await self._client.embeddings.create(
                    model=self._model,
                    input=batch,
                )

                # Extraire les embeddings dans l'ordre
                batch_embeddings = [item.embedding for item in response.data]
                all_embeddings.extend(batch_embeddings)

                logger.debug(
                    "Batch embeddings generated",
                    extra={
                        "tenant_id": tenant_id,
                        "batch_index": i // self.MAX_BATCH_SIZE,
                        "batch_size": len(batch),
                    },
                )

            except Exception as e:
                logger.error(
                    "Failed to generate batch embeddings",
                    extra={
                        "tenant_id": tenant_id,
                        "batch_index": i // self.MAX_BATCH_SIZE,
                        "error": str(e),
                    },
                )

                raise

        return all_embeddings
