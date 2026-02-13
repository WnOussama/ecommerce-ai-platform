"""
Embedding Service - Génération d'embeddings multi-provider

Ce service abstrait la génération d'embeddings pour:
- Support mock (développement/tests)
- Support OpenAI (production)
- Batching optimisé
- Multi-tenant safe
"""

import logging
import random
import hashlib
from abc import abstractmethod
from typing import List, Optional, Protocol
from dataclasses import dataclass

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
# MOCK EMBEDDING SERVICE
# =============================================================================

class MockEmbeddingService:
    """
    Service d'embedding mock pour développement et tests.

    Génère des embeddings déterministes basés sur le hash du texte.
    Cela permet d'avoir des embeddings reproductibles pour les tests.

    Usage:
        service = MockEmbeddingService()
        embedding = await service.generate_embedding("Hello world")
    """

    DEFAULT_DIMENSIONS = 384  # Similaire à sentence-transformers

    def __init__(
        self,
        dimensions: int = DEFAULT_DIMENSIONS,
        simulate_latency: bool = False,
    ):
        """
        Initialise le service mock.

        Args:
            dimensions: Dimension des embeddings générés
            simulate_latency: Simuler une latence réseau
        """
        self._dimensions = dimensions
        self._simulate_latency = simulate_latency
        self._model_name = "mock-embedding-v1"

        logger.info(
            "MockEmbeddingService initialized",
            extra={"dimensions": dimensions}
        )

    @property
    def dimensions(self) -> int:
        """Dimension des embeddings."""
        return self._dimensions

    @property
    def model_name(self) -> str:
        """Nom du modèle."""
        return self._model_name

    async def generate_embedding(
        self,
        text: str,
        tenant_id: Optional[str] = None,
    ) -> List[float]:
        """
        Génère un embedding déterministe pour un texte.

        L'embedding est basé sur un hash du texte, ce qui garantit
        que le même texte produira toujours le même embedding.

        Args:
            text: Texte à encoder
            tenant_id: ID du tenant (ignoré pour mock)

        Returns:
            Liste de floats représentant l'embedding
        """
        if self._simulate_latency:
            import asyncio
            await asyncio.sleep(random.uniform(0.01, 0.05))

        return self._generate_deterministic_embedding(text)

    async def generate_embeddings_batch(
        self,
        texts: List[str],
        tenant_id: Optional[str] = None,
    ) -> List[List[float]]:
        """
        Génère des embeddings pour plusieurs textes.

        Args:
            texts: Liste de textes à encoder
            tenant_id: ID du tenant (ignoré pour mock)

        Returns:
            Liste d'embeddings
        """
        if self._simulate_latency:
            import asyncio
            # Latence proportionnelle au nombre de textes
            await asyncio.sleep(random.uniform(0.01, 0.02) * len(texts))

        return [self._generate_deterministic_embedding(text) for text in texts]

    def _generate_deterministic_embedding(self, text: str) -> List[float]:
        """
        Génère un embedding déterministe basé sur le hash du texte.

        Utilise SHA256 comme seed pour un générateur pseudo-aléatoire,
        garantissant des résultats reproductibles.
        """
        # Hash du texte comme seed
        text_hash = hashlib.sha256(text.encode()).hexdigest()
        seed = int(text_hash[:8], 16)

        # Générateur avec seed fixe
        rng = random.Random(seed)

        # Générer des valeurs normalisées entre -1 et 1
        embedding = [rng.gauss(0, 0.3) for _ in range(self._dimensions)]

        # Normaliser le vecteur (L2 norm)
        norm = sum(x * x for x in embedding) ** 0.5
        if norm > 0:
            embedding = [x / norm for x in embedding]

        return embedding


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
    - Fallback vers mock si OpenAI indisponible

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
        fallback_to_mock: bool = True,
    ):
        """
        Initialise le service d'embedding.

        Args:
            api_key: Clé API OpenAI
            model: Modèle d'embedding à utiliser
            dimensions: Dimension des embeddings
            fallback_to_mock: Utiliser mock si OpenAI indisponible
        """
        self._api_key = api_key
        self._model = model
        self._dimensions = dimensions
        self._fallback_to_mock = fallback_to_mock
        self._client = None
        self._mock_service: Optional[MockEmbeddingService] = None

        # Initialiser le client OpenAI si clé fournie
        if api_key:
            try:
                from openai import AsyncOpenAI
                self._client = AsyncOpenAI(api_key=api_key)
                logger.info(
                    "EmbeddingService initialized with OpenAI",
                    extra={"model": model, "dimensions": dimensions}
                )
            except ImportError:
                logger.warning("OpenAI not installed, using mock")
                self._init_mock()
        else:
            logger.info("No API key provided, using mock embedding service")
            self._init_mock()

    def _init_mock(self) -> None:
        """Initialise le service mock."""
        self._mock_service = MockEmbeddingService(dimensions=self._dimensions)

    @property
    def dimensions(self) -> int:
        """Dimension des embeddings."""
        return self._dimensions

    @property
    def model_name(self) -> str:
        """Nom du modèle."""
        if self._mock_service:
            return self._mock_service.model_name
        return self._model

    @property
    def is_mock(self) -> bool:
        """True si utilise le mock."""
        return self._mock_service is not None

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
        # Utiliser mock si disponible
        if self._mock_service:
            return await self._mock_service.generate_embedding(text, tenant_id)

        # OpenAI
        try:
            response = await self._client.embeddings.create(
                model=self._model,
                input=text,
            )
            return response.data[0].embedding

        except Exception as e:
            logger.error(
                "Failed to generate embedding",
                extra={"tenant_id": tenant_id, "error": str(e)}
            )

            # Fallback to mock
            if self._fallback_to_mock:
                if not self._mock_service:
                    self._init_mock()
                return await self._mock_service.generate_embedding(text, tenant_id)

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

        # Utiliser mock si disponible
        if self._mock_service:
            return await self._mock_service.generate_embeddings_batch(texts, tenant_id)

        # OpenAI avec batching
        all_embeddings: List[List[float]] = []

        for i in range(0, len(texts), self.MAX_BATCH_SIZE):
            batch = texts[i:i + self.MAX_BATCH_SIZE]

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
                    }
                )

            except Exception as e:
                logger.error(
                    "Failed to generate batch embeddings",
                    extra={
                        "tenant_id": tenant_id,
                        "batch_index": i // self.MAX_BATCH_SIZE,
                        "error": str(e),
                    }
                )

                # Fallback to mock for this batch
                if self._fallback_to_mock:
                    if not self._mock_service:
                        self._init_mock()
                    batch_embeddings = await self._mock_service.generate_embeddings_batch(
                        batch, tenant_id
                    )
                    all_embeddings.extend(batch_embeddings)
                else:
                    raise

        return all_embeddings

