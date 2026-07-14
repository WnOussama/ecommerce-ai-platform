"""
RAG Service V2 - Single Collection avec Metadata Filter

Architecture SCALABLE pour multi-tenant:
┌─────────────────────────────────────────────────────────────────────────────────┐
│                     CHROMADB SINGLE COLLECTION PATTERN                          │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ❌ AVANT (1 collection par tenant):                                            │
│     tenant_001_products, tenant_001_faqs, tenant_002_products, ...              │
│     → Explosion à 1000+ collections si 100 tenants × 10 types                   │
│     → Performance dégradée                                                       │
│     → Gestion complexe                                                           │
│                                                                                  │
│  ✅ APRÈS (Single collection + metadata filter):                                │
│     products_collection: [                                                       │
│       {id: "1", tenant_id: "A", content: "...", ...},                           │
│       {id: "2", tenant_id: "B", content: "...", ...},                           │
│     ]                                                                            │
│     → 4 collections max (products, faqs, policies, conversations)               │
│     → Filtre par metadata: {"tenant_id": "A"}                                   │
│     → Scalable à 1000+ tenants                                                  │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# TYPES
# =============================================================================


class DocumentType(str, Enum):
    """Types de documents indexés"""

    PRODUCT = "product"
    FAQ = "faq"
    POLICY = "policy"
    CONVERSATION = "conversation"


# Noms des collections (FIXES, pas par tenant)
COLLECTION_NAMES = {
    DocumentType.PRODUCT: "saas_products",
    DocumentType.FAQ: "saas_faqs",
    DocumentType.POLICY: "saas_policies",
    DocumentType.CONVERSATION: "saas_conversations",
}


@dataclass
class Document:
    """Document récupéré par RAG"""

    id: str
    content: str
    document_type: DocumentType
    tenant_id: str  # OBLIGATOIRE
    metadata: Dict[str, Any] = field(default_factory=dict)
    relevance_score: float = 0.0
    title: Optional[str] = None
    category: Optional[str] = None


@dataclass
class RAGQuery:
    """Requête de recherche RAG"""

    query: str
    tenant_id: str  # OBLIGATOIRE - filtre automatique
    document_types: List[DocumentType] = field(default_factory=list)
    top_k: int = 5
    min_relevance: float = 0.5
    filters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RAGResult:
    """Résultat de recherche RAG"""

    documents: List[Document]
    query: str
    total_found: int
    search_time_ms: int = 0
    tenant_id: str = ""


# =============================================================================
# RAG SERVICE V2 (SCALABLE)
# =============================================================================


class RAGServiceV2:
    """
    Service RAG SCALABLE avec Single Collection pattern.

    Architecture:
    - 4 collections globales (products, faqs, policies, conversations)
    - Filtrage par tenant_id dans les metadata
    - Isolation garantie via where filter
    """

    def __init__(
        self,
        chroma_host: str = "localhost",
        chroma_port: int = 8000,
        embedding_dimension: int = 1536,
    ):
        self.chroma_host = chroma_host
        self.chroma_port = chroma_port
        self.embedding_dimension = embedding_dimension
        self._client = None
        self._llm_gateway = None
        self._collections: Dict[DocumentType, Any] = {}

    def _get_client(self):
        """Lazy init du client ChromaDB"""
        if self._client is None:
            import chromadb

            self._client = chromadb.HttpClient(
                host=self.chroma_host,
                port=self.chroma_port,
            )
            logger.info(f"Connected to ChromaDB at {self.chroma_host}:{self.chroma_port}")
        return self._client

    def set_llm_gateway(self, gateway):
        """Injecte le LLM Gateway pour les embeddings"""
        self._llm_gateway = gateway

    def _get_collection(self, doc_type: DocumentType):
        """Obtient ou crée une collection globale"""
        if doc_type not in self._collections:
            client = self._get_client()
            collection_name = COLLECTION_NAMES[doc_type]

            self._collections[doc_type] = client.get_or_create_collection(
                name=collection_name,
                metadata={
                    "description": f"Global {doc_type.value} collection for all tenants",
                    "hnsw:space": "cosine",  # Cosine similarity
                },
            )
            logger.info(f"Initialized collection: {collection_name}")

        return self._collections[doc_type]

    # =========================================================================
    # SEARCH (avec isolation tenant OBLIGATOIRE)
    # =========================================================================

    async def search(self, query: RAGQuery) -> RAGResult:
        """
        Recherche des documents avec ISOLATION TENANT automatique.
        Le tenant_id est TOUJOURS inclus dans le filtre.
        """
        import time

        start_time = time.perf_counter()

        if not query.tenant_id:
            raise ValueError("tenant_id is required for RAG search")

        if not self._llm_gateway:
            raise RuntimeError("LLM Gateway not set. Call set_llm_gateway() first.")

        # Générer l'embedding de la requête
        query_embedding = await self._llm_gateway.generate_embeddings([query.query])
        query_vector = query_embedding[0]

        all_documents: List[Document] = []
        doc_types = query.document_types or [DocumentType.PRODUCT, DocumentType.FAQ]

        for doc_type in doc_types:
            try:
                collection = self._get_collection(doc_type)

                # FILTRE TENANT OBLIGATOIRE
                where_filter = {"tenant_id": query.tenant_id}

                # Ajouter filtres additionnels
                if query.filters:
                    for key, value in query.filters.items():
                        if key != "tenant_id":  # Ne pas écraser
                            where_filter[key] = value

                # Recherche avec filtre tenant
                results = collection.query(
                    query_embeddings=[query_vector],
                    n_results=query.top_k,
                    where=where_filter,
                    include=["documents", "metadatas", "distances"],
                )

                # Convertir les résultats
                if results and results["documents"] and results["documents"][0]:
                    for i, doc_content in enumerate(results["documents"][0]):
                        # Calculer le score (1 - distance pour cosine)
                        distance = results["distances"][0][i] if results["distances"] else 0
                        score = 1.0 - distance

                        if score >= query.min_relevance:
                            metadata = results["metadatas"][0][i] if results["metadatas"] else {}

                            # Vérifier que le document appartient bien au tenant
                            doc_tenant = metadata.get("tenant_id")
                            if doc_tenant != query.tenant_id:
                                logger.error(
                                    "Cross-tenant document detected in RAG results!",
                                    extra={
                                        "expected_tenant": query.tenant_id,
                                        "actual_tenant": doc_tenant,
                                        "doc_id": results["ids"][0][i],
                                    },
                                )
                                continue  # Skip ce document

                            all_documents.append(
                                Document(
                                    id=results["ids"][0][i],
                                    content=doc_content,
                                    document_type=doc_type,
                                    tenant_id=query.tenant_id,
                                    metadata=metadata,
                                    relevance_score=score,
                                    title=metadata.get("title") or metadata.get("name"),
                                    category=metadata.get("category"),
                                )
                            )

            except Exception as e:
                logger.warning(f"Error searching {doc_type.value}: {e}")
                continue

        # Trier par relevance
        all_documents.sort(key=lambda d: d.relevance_score, reverse=True)
        all_documents = all_documents[: query.top_k]

        search_time_ms = int((time.perf_counter() - start_time) * 1000)

        logger.info(
            "RAG search completed",
            extra={
                "tenant_id": query.tenant_id,
                "query_length": len(query.query),
                "documents_found": len(all_documents),
                "search_time_ms": search_time_ms,
                "doc_types": [dt.value for dt in doc_types],
            },
        )

        return RAGResult(
            documents=all_documents,
            query=query.query,
            total_found=len(all_documents),
            search_time_ms=search_time_ms,
            tenant_id=query.tenant_id,
        )

    # Convenience methods
    async def search_products(
        self,
        tenant_id: str,
        query: str,
        top_k: int = 5,
        category: Optional[str] = None,
    ) -> List[Document]:
        """Recherche de produits"""
        filters = {}
        if category:
            filters["category"] = category

        result = await self.search(
            RAGQuery(
                query=query,
                tenant_id=tenant_id,
                document_types=[DocumentType.PRODUCT],
                top_k=top_k,
                filters=filters,
            )
        )
        return result.documents

    async def search_faqs(
        self,
        tenant_id: str,
        query: str,
        top_k: int = 3,
    ) -> List[Document]:
        """Recherche de FAQs"""
        result = await self.search(
            RAGQuery(
                query=query,
                tenant_id=tenant_id,
                document_types=[DocumentType.FAQ],
                top_k=top_k,
            )
        )
        return result.documents

    async def search_policies(
        self,
        tenant_id: str,
        query: str,
        top_k: int = 2,
    ) -> List[Document]:
        """Recherche de politiques"""
        result = await self.search(
            RAGQuery(
                query=query,
                tenant_id=tenant_id,
                document_types=[DocumentType.POLICY],
                top_k=top_k,
            )
        )
        return result.documents

    # =========================================================================
    # INDEXING (avec tenant_id OBLIGATOIRE)
    # =========================================================================

    async def index_documents(
        self,
        tenant_id: str,
        doc_type: DocumentType,
        documents: List[Dict[str, Any]],
    ) -> int:
        """
        Indexe des documents dans la collection globale.
        Le tenant_id est OBLIGATOIREMENT ajouté aux metadata.
        """
        if not tenant_id:
            raise ValueError("tenant_id is required for indexing")

        if not self._llm_gateway:
            raise RuntimeError("LLM Gateway not set")

        if not documents:
            return 0

        collection = self._get_collection(doc_type)

        # Préparer les données
        texts = []
        ids = []
        metadatas = []

        for doc in documents:
            doc_id = f"{tenant_id}_{doc_type.value}_{doc.get('id', '')}"

            # Construire le texte selon le type
            if doc_type == DocumentType.PRODUCT:
                text = (
                    f"{doc.get('name', '')} {doc.get('description', '')} {doc.get('category', '')}"
                )
            elif doc_type == DocumentType.FAQ:
                text = f"{doc.get('question', '')} {doc.get('answer', '')}"
            elif doc_type == DocumentType.POLICY:
                text = f"{doc.get('title', '')} {doc.get('content', '')}"
            else:
                text = doc.get("content", "")

            # Metadata avec tenant_id OBLIGATOIRE
            metadata = {
                "tenant_id": tenant_id,  # CRITIQUE
                "doc_type": doc_type.value,
                "indexed_at": datetime.utcnow().isoformat(),
            }

            # Ajouter autres metadata (sauf tenant_id qui est déjà défini)
            for k, v in doc.items():
                if k not in ("id", "embedding", "tenant_id") and isinstance(
                    v, (str, int, float, bool)
                ):
                    metadata[k] = v

            texts.append(text)
            ids.append(doc_id)
            metadatas.append(metadata)

        # Générer les embeddings
        embeddings = await self._llm_gateway.generate_embeddings(texts)

        # Upsert dans ChromaDB
        collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=texts,
            metadatas=metadatas,
        )

        logger.info(
            f"Indexed {len(documents)} {doc_type.value} documents",
            extra={"tenant_id": tenant_id, "count": len(documents)},
        )

        return len(documents)

    async def delete_documents(
        self,
        tenant_id: str,
        doc_type: DocumentType,
        document_ids: List[str],
    ) -> int:
        """Supprime des documents (avec validation tenant)"""
        if not tenant_id:
            raise ValueError("tenant_id is required")

        collection = self._get_collection(doc_type)

        # Construire les IDs avec préfixe tenant
        full_ids = [f"{tenant_id}_{doc_type.value}_{doc_id}" for doc_id in document_ids]

        try:
            collection.delete(ids=full_ids)
            logger.info(
                f"Deleted {len(document_ids)} documents",
                extra={"tenant_id": tenant_id, "doc_type": doc_type.value},
            )
            return len(document_ids)
        except Exception as e:
            logger.error(f"Error deleting documents: {e}")
            return 0

    async def delete_tenant_documents(
        self,
        tenant_id: str,
        doc_type: Optional[DocumentType] = None,
    ) -> int:
        """
        Supprime TOUS les documents d'un tenant.
        Utile pour suppression de compte ou nettoyage.
        """
        if not tenant_id:
            raise ValueError("tenant_id is required")

        doc_types = [doc_type] if doc_type else list(DocumentType)
        total_deleted = 0

        for dt in doc_types:
            collection = self._get_collection(dt)

            try:
                # Supprimer par filtre metadata
                collection.delete(where={"tenant_id": tenant_id})
                logger.info(
                    f"Deleted all {dt.value} documents for tenant", extra={"tenant_id": tenant_id}
                )
                total_deleted += 1  # ChromaDB ne retourne pas le count
            except Exception as e:
                logger.error(f"Error deleting tenant documents: {e}")

        return total_deleted

    # =========================================================================
    # STATS
    # =========================================================================

    async def get_tenant_stats(self, tenant_id: str) -> Dict[str, int]:
        """Statistiques des documents d'un tenant"""
        if not tenant_id:
            raise ValueError("tenant_id is required")

        stats = {}

        for doc_type in DocumentType:
            try:
                collection = self._get_collection(doc_type)

                # Compter les documents du tenant
                results = collection.get(
                    where={"tenant_id": tenant_id},
                    include=[],  # Pas besoin du contenu
                )

                stats[doc_type.value] = len(results["ids"]) if results["ids"] else 0

            except Exception as e:
                logger.warning(f"Error getting stats for {doc_type.value}: {e}")
                stats[doc_type.value] = 0

        return stats

    async def get_global_stats(self) -> Dict[str, Any]:
        """Statistiques globales (admin only)"""
        stats = {}

        for doc_type in DocumentType:
            try:
                collection = self._get_collection(doc_type)
                stats[doc_type.value] = collection.count()
            except Exception:
                stats[doc_type.value] = 0

        stats["total"] = sum(stats.values())
        return stats


# =============================================================================
# EXPORTS
# =============================================================================

# Alias pour compatibilité
RAGService = RAGServiceV2

__all__ = [
    "RAGServiceV2",
    "RAGService",
    "RAGQuery",
    "RAGResult",
    "Document",
    "DocumentType",
    "COLLECTION_NAMES",
]
