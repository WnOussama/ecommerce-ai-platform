"""
RAG Reranker - Cross-encoder reranking pour améliorer la pertinence

Architecture:
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         RAG PIPELINE AVEC RERANKING                              │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  Query                                                                           │
│    │                                                                             │
│    ▼                                                                             │
│  ┌─────────────────┐                                                            │
│  │ 1. RETRIEVAL    │  ChromaDB bi-encoder (rapide)                              │
│  │    (top K=20)   │  Embedding similarity search                               │
│  └────────┬────────┘                                                            │
│           │ 20 candidats                                                        │
│           ▼                                                                      │
│  ┌─────────────────┐                                                            │
│  │ 2. RERANKING    │  Cross-encoder (précis)                                    │
│  │    (top N=5)    │  Score query-document pair                                 │
│  └────────┬────────┘                                                            │
│           │ 5 documents pertinents                                              │
│           ▼                                                                      │
│  ┌─────────────────┐                                                            │
│  │ 3. LLM          │  Génération avec contexte optimisé                         │
│  │    GENERATION   │                                                            │
│  └─────────────────┘                                                            │
│                                                                                  │
│  Pourquoi reranking?                                                            │
│  • Bi-encoder: encode query et doc séparément → approximatif                    │
│  • Cross-encoder: encode (query, doc) ensemble → précis mais lent              │
│  • Solution: bi-encoder pour recall, cross-encoder pour precision              │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# TYPES
# =============================================================================


@dataclass
class RankedDocument:
    """Document avec scores de retrieval et reranking"""

    id: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Scores
    retrieval_score: float = 0.0  # Score bi-encoder (embedding similarity)
    rerank_score: float = 0.0  # Score cross-encoder (reranking)
    final_score: float = 0.0  # Score combiné

    # Ranking positions
    retrieval_rank: int = 0
    rerank_rank: int = 0


@dataclass
class RerankerConfig:
    """Configuration du reranker"""

    model_name: str = "BAAI/bge-reranker-base"  # Léger et efficace
    top_k_retrieval: int = 20  # Candidats du bi-encoder
    top_n_rerank: int = 5  # Résultats finaux après reranking
    min_score: float = 0.1  # Score minimum pour garder un document
    batch_size: int = 32  # Batch size pour le reranker
    use_gpu: bool = False  # GPU pour le reranker
    cache_enabled: bool = True  # Cache des scores


# =============================================================================
# RERANKER INTERFACE
# =============================================================================


class BaseReranker(ABC):
    """Interface abstraite pour les rerankers"""

    @abstractmethod
    async def rerank(
        self,
        query: str,
        documents: List[RankedDocument],
        top_n: int = 5,
    ) -> List[RankedDocument]:
        """Rerank les documents par pertinence"""
        pass


# =============================================================================
# CROSS-ENCODER RERANKER
# =============================================================================


class CrossEncoderReranker(BaseReranker):
    """
    Reranker basé sur un modèle cross-encoder.

    Cross-encoder = encode (query, document) ensemble
    → Score de pertinence précis mais plus lent que bi-encoder

    Modèles recommandés:
    - BAAI/bge-reranker-base (léger, bon pour PFE)
    - BAAI/bge-reranker-large (meilleur mais plus lourd)
    - cross-encoder/ms-marco-MiniLM-L-6-v2 (très léger)
    """

    def __init__(self, config: Optional[RerankerConfig] = None):
        self._config = config or RerankerConfig()
        self._model = None
        self._tokenizer = None
        self._cache: Dict[str, float] = {}  # Cache: hash(query+doc) -> score

    def _load_model(self):
        """Lazy loading du modèle"""
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder

                device = "cuda" if self._config.use_gpu else "cpu"
                self._model = CrossEncoder(
                    self._config.model_name,
                    device=device,
                    max_length=512,
                )
                logger.info(f"Loaded reranker model: {self._config.model_name}")

            except ImportError:
                logger.warning(
                    "sentence-transformers not installed. "
                    "Install with: pip install sentence-transformers"
                )
                raise

    async def rerank(
        self,
        query: str,
        documents: List[RankedDocument],
        top_n: int = 5,
    ) -> List[RankedDocument]:
        """
        Rerank les documents avec le cross-encoder.
        """
        if not documents:
            return []

        self._load_model()

        # Préparer les paires (query, document)
        pairs = []
        cache_keys = []
        uncached_indices = []

        for i, doc in enumerate(documents):
            cache_key = self._get_cache_key(query, doc.content)
            cache_keys.append(cache_key)

            if self._config.cache_enabled and cache_key in self._cache:
                doc.rerank_score = self._cache[cache_key]
            else:
                pairs.append((query, doc.content))
                uncached_indices.append(i)

        # Calculer les scores pour les documents non cachés
        if pairs:
            # Run in thread pool car sentence-transformers est synchrone
            loop = asyncio.get_event_loop()
            scores = await loop.run_in_executor(
                None, lambda: self._model.predict(pairs, batch_size=self._config.batch_size)
            )

            # Assigner les scores
            for idx, score in zip(uncached_indices, scores):
                documents[idx].rerank_score = float(score)

                # Cache
                if self._config.cache_enabled:
                    self._cache[cache_keys[idx]] = float(score)

        # Filtrer par score minimum
        filtered_docs = [doc for doc in documents if doc.rerank_score >= self._config.min_score]

        # Trier par score de reranking
        filtered_docs.sort(key=lambda d: d.rerank_score, reverse=True)

        # Assigner les rangs et scores finaux
        for rank, doc in enumerate(filtered_docs):
            doc.rerank_rank = rank + 1
            # Score final = combinaison pondérée
            doc.final_score = 0.3 * doc.retrieval_score + 0.7 * doc.rerank_score

        # Retourner top N
        result = filtered_docs[:top_n]

        logger.info(
            f"Reranked {len(documents)} docs -> {len(result)} results",
            extra={
                "query_length": len(query),
                "top_score": result[0].rerank_score if result else 0,
            },
        )

        return result

    def _get_cache_key(self, query: str, content: str) -> str:
        """Génère une clé de cache pour une paire query-doc"""
        import hashlib

        combined = f"{query[:100]}:{content[:200]}"
        # Clé de cache uniquement (pas d'usage cryptographique/sécurité)
        return hashlib.md5(combined.encode(), usedforsecurity=False).hexdigest()

    def clear_cache(self):
        """Vide le cache"""
        self._cache.clear()


# =============================================================================
# LLM-BASED RERANKER (Alternative)
# =============================================================================


class LLMReranker(BaseReranker):
    """
    Reranker utilisant un LLM pour scorer les documents.

    Plus flexible mais plus coûteux.
    Utile quand on n'a pas de modèle cross-encoder local.
    """

    RERANK_PROMPT = """Rate the relevance of the following document to the query.
Return ONLY a number between 0 and 1.

Query: {query}

Document: {document}

Relevance score (0-1):"""

    def __init__(self, llm_gateway):
        self._llm = llm_gateway

    async def rerank(
        self,
        query: str,
        documents: List[RankedDocument],
        top_n: int = 5,
    ) -> List[RankedDocument]:
        """Rerank avec LLM"""
        if not documents:
            return []

        # Score chaque document (en parallèle)
        tasks = [self._score_document(query, doc) for doc in documents]

        await asyncio.gather(*tasks)

        # Trier et retourner top N
        documents.sort(key=lambda d: d.rerank_score, reverse=True)

        for rank, doc in enumerate(documents[:top_n]):
            doc.rerank_rank = rank + 1
            doc.final_score = doc.rerank_score

        return documents[:top_n]

    async def _score_document(self, query: str, doc: RankedDocument) -> None:
        """Score un document avec le LLM"""
        from app.domain.services.shared.llm_gateway import LLMRequest

        prompt = self.RERANK_PROMPT.format(
            query=query[:500],
            document=doc.content[:1000],
        )

        try:
            response = await self._llm.generate(
                LLMRequest(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0,
                    max_tokens=10,
                )
            )

            # Parser le score
            score_text = response.content.strip()
            doc.rerank_score = float(score_text)
            doc.rerank_score = max(0, min(1, doc.rerank_score))  # Clamp 0-1

        except Exception as e:
            logger.warning(f"LLM reranking failed: {e}")
            doc.rerank_score = doc.retrieval_score  # Fallback


# =============================================================================
# RERANKING PIPELINE
# =============================================================================


class RerankingPipeline:
    """
    Pipeline complet de retrieval + reranking.

    Usage:
        pipeline = RerankingPipeline(rag_service, reranker)
        results = await pipeline.search(query, tenant_id)
    """

    def __init__(
        self,
        rag_service,
        reranker: BaseReranker,
        config: Optional[RerankerConfig] = None,
    ):
        self._rag = rag_service
        self._reranker = reranker
        self._config = config or RerankerConfig()

    async def search(
        self,
        query: str,
        tenant_id: str,
        document_types: Optional[List[str]] = None,
        top_n: int = 5,
    ) -> List[RankedDocument]:
        """
        Recherche avec reranking.

        1. Retrieval: ChromaDB bi-encoder (top K=20)
        2. Reranking: Cross-encoder (top N=5)
        """
        from app.domain.services.shared.rag_service_v2 import DocumentType, RAGQuery

        # 1. RETRIEVAL - Top K candidats
        doc_types = [DocumentType(dt) for dt in (document_types or ["product", "faq"])]

        rag_result = await self._rag.search(
            RAGQuery(
                query=query,
                tenant_id=tenant_id,
                document_types=doc_types,
                top_k=self._config.top_k_retrieval,
                min_relevance=0.3,  # Seuil bas pour recall
            )
        )

        # Convertir en RankedDocument
        candidates = []
        for rank, doc in enumerate(rag_result.documents):
            candidates.append(
                RankedDocument(
                    id=doc.id,
                    content=doc.content,
                    metadata=doc.metadata,
                    retrieval_score=doc.relevance_score,
                    retrieval_rank=rank + 1,
                )
            )

        if not candidates:
            return []

        # 2. RERANKING - Top N résultats
        reranked = await self._reranker.rerank(
            query=query,
            documents=candidates,
            top_n=top_n,
        )

        logger.info(
            f"Search completed: {len(rag_result.documents)} retrieved, {len(reranked)} after rerank",
            extra={
                "tenant_id": tenant_id,
                "query_length": len(query),
            },
        )

        return reranked


# =============================================================================
# FACTORY
# =============================================================================


def create_reranker(
    model_type: str = "cross-encoder",
    config: Optional[RerankerConfig] = None,
    llm_gateway=None,
) -> BaseReranker:
    """
    Factory pour créer un reranker.

    Args:
        model_type: "cross-encoder" ou "llm"
        config: Configuration du reranker
        llm_gateway: Requis si model_type="llm"
    """
    if model_type == "cross-encoder":
        return CrossEncoderReranker(config)
    elif model_type == "llm":
        if not llm_gateway:
            raise ValueError("llm_gateway required for LLM reranker")
        return LLMReranker(llm_gateway)
    else:
        raise ValueError(f"Unknown reranker type: {model_type}")


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    "RankedDocument",
    "RerankerConfig",
    "BaseReranker",
    "CrossEncoderReranker",
    "LLMReranker",
    "RerankingPipeline",
    "create_reranker",
]
