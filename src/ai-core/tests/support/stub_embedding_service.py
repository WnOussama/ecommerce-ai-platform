"""
Test-only embedding double - NOT part of the app (the runtime uses
LocalEmbeddingService or OpenAI, see app/services/rag/factory.py).

Deterministic and free: lets RAG unit tests run without downloading a model
or calling an API. The vectors carry no semantic meaning.
"""

import hashlib
import logging
import random
from typing import List, Optional

logger = logging.getLogger(__name__)


class StubEmbeddingService:
    """
    Service d'embedding mock pour développement et tests.

    Génère des embeddings déterministes basés sur le hash du texte.
    Cela permet d'avoir des embeddings reproductibles pour les tests.

    Usage:
        service = StubEmbeddingService()
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
        self._model_name = "stub-embedding-v1"

        logger.info("StubEmbeddingService initialized", extra={"dimensions": dimensions})

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
