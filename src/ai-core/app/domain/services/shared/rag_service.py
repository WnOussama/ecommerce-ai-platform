"""
RAG Service - Retrieval Augmented Generation partagé
Gère la recherche de documents pertinents via ChromaDB
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, List
from uuid import UUID
from enum import Enum
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# TYPES
# =============================================================================

class DocumentType(str, Enum):
    """Types de documents indexés"""
    PRODUCT = "product"
    FAQ = "faq"
    POLICY = "policy"          # Retours, livraison, etc.
    CONVERSATION = "conversation"  # Historique pour contexte


@dataclass
class Document:
    """Document récupéré par RAG"""
    id: str
    content: str
    document_type: DocumentType
    metadata: Dict[str, Any] = field(default_factory=dict)
    relevance_score: float = 0.0

    # Métadonnées spécifiques
    title: Optional[str] = None
    category: Optional[str] = None
    source: Optional[str] = None


@dataclass
class RAGQuery:
    """Requête de recherche RAG"""
    query: str
    tenant_id: str
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


# =============================================================================
# RAG SERVICE
# =============================================================================

class RAGService:
    """
    Service RAG partagé entre Client et Admin agents.
    Recherche de documents pertinents pour enrichir le contexte.
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

    def _get_client(self):
        """Lazy initialization du client ChromaDB"""
        if self._client is None:
            import chromadb
            self._client = chromadb.HttpClient(
                host=self.chroma_host,
                port=self.chroma_port,
            )
        return self._client

    def set_llm_gateway(self, gateway):
        """Injecte le LLM Gateway pour les embeddings"""
        self._llm_gateway = gateway

    def _get_collection_name(self, tenant_id: str, doc_type: DocumentType) -> str:
        """Génère le nom de collection pour un tenant"""
        return f"tenant_{tenant_id}_{doc_type.value}s"

    async def search(self, query: RAGQuery) -> RAGResult:
        """
        Recherche des documents pertinents.
        """
        import time
        start_time = time.perf_counter()

        if not self._llm_gateway:
            raise RuntimeError("LLM Gateway not set. Call set_llm_gateway() first.")

        all_documents: List[Document] = []

        # Générer l'embedding de la requête
        query_embedding = await self._llm_gateway.generate_embeddings([query.query])
        query_vector = query_embedding[0]

        # Rechercher dans chaque type de document demandé
        doc_types = query.document_types or [DocumentType.PRODUCT, DocumentType.FAQ]

        client = self._get_client()

        for doc_type in doc_types:
            try:
                collection_name = self._get_collection_name(query.tenant_id, doc_type)
                collection = client.get_collection(name=collection_name)

                # Recherche par similarité
                results = collection.query(
                    query_embeddings=[query_vector],
                    n_results=query.top_k,
                    where=query.filters if query.filters else None,
                )

                # Convertir les résultats
                if results and results["documents"]:
                    for i, doc_content in enumerate(results["documents"][0]):
                        score = 1.0 - (results["distances"][0][i] if results["distances"] else 0)

                        if score >= query.min_relevance:
                            metadata = results["metadatas"][0][i] if results["metadatas"] else {}

                            all_documents.append(Document(
                                id=results["ids"][0][i],
                                content=doc_content,
                                document_type=doc_type,
                                metadata=metadata,
                                relevance_score=score,
                                title=metadata.get("title"),
                                category=metadata.get("category"),
                            ))

            except Exception as e:
                logger.warning(f"Error searching {doc_type.value}: {e}")
                continue

        # Trier par relevance
        all_documents.sort(key=lambda d: d.relevance_score, reverse=True)

        # Limiter au top_k total
        all_documents = all_documents[:query.top_k]

        search_time_ms = int((time.perf_counter() - start_time) * 1000)

        logger.info(
            "RAG search completed",
            extra={
                "tenant_id": query.tenant_id,
                "query_length": len(query.query),
                "documents_found": len(all_documents),
                "search_time_ms": search_time_ms,
            }
        )

        return RAGResult(
            documents=all_documents,
            query=query.query,
            total_found=len(all_documents),
            search_time_ms=search_time_ms,
        )

    async def search_products(
        self,
        tenant_id: str,
        query: str,
        top_k: int = 5,
        category: Optional[str] = None,
    ) -> List[Document]:
        """Recherche spécifique de produits"""
        filters = {}
        if category:
            filters["category"] = category

        result = await self.search(RAGQuery(
            query=query,
            tenant_id=tenant_id,
            document_types=[DocumentType.PRODUCT],
            top_k=top_k,
            filters=filters,
        ))

        return result.documents

    async def search_faqs(
        self,
        tenant_id: str,
        query: str,
        top_k: int = 3,
    ) -> List[Document]:
        """Recherche spécifique de FAQs"""
        result = await self.search(RAGQuery(
            query=query,
            tenant_id=tenant_id,
            document_types=[DocumentType.FAQ],
            top_k=top_k,
        ))

        return result.documents

    async def search_policies(
        self,
        tenant_id: str,
        query: str,
        top_k: int = 2,
    ) -> List[Document]:
        """Recherche de politiques (retour, livraison, etc.)"""
        result = await self.search(RAGQuery(
            query=query,
            tenant_id=tenant_id,
            document_types=[DocumentType.POLICY],
            top_k=top_k,
        ))

        return result.documents

    # =========================================================================
    # INDEXING METHODS
    # =========================================================================

    async def index_documents(
        self,
        tenant_id: str,
        doc_type: DocumentType,
        documents: List[Dict[str, Any]],
    ) -> int:
        """
        Indexe des documents dans ChromaDB.
        """
        if not self._llm_gateway:
            raise RuntimeError("LLM Gateway not set")

        if not documents:
            return 0

        client = self._get_client()
        collection_name = self._get_collection_name(tenant_id, doc_type)

        # Créer ou récupérer la collection
        collection = client.get_or_create_collection(
            name=collection_name,
            metadata={"tenant_id": tenant_id, "type": doc_type.value}
        )

        # Préparer les textes pour embedding
        texts = []
        ids = []
        metadatas = []

        for doc in documents:
            doc_id = str(doc.get("id", ""))

            # Construire le texte à indexer selon le type
            if doc_type == DocumentType.PRODUCT:
                text = f"{doc.get('name', '')} {doc.get('description', '')} {doc.get('category', '')}"
            elif doc_type == DocumentType.FAQ:
                text = f"{doc.get('question', '')} {doc.get('answer', '')}"
            elif doc_type == DocumentType.POLICY:
                text = f"{doc.get('title', '')} {doc.get('content', '')}"
            else:
                text = doc.get("content", "")

            texts.append(text)
            ids.append(doc_id)
            metadatas.append({
                k: v for k, v in doc.items()
                if k not in ("id", "embedding") and isinstance(v, (str, int, float, bool))
            })

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
            extra={"tenant_id": tenant_id}
        )

        return len(documents)

    async def delete_documents(
        self,
        tenant_id: str,
        doc_type: DocumentType,
        document_ids: List[str],
    ) -> int:
        """Supprime des documents de l'index"""
        client = self._get_client()
        collection_name = self._get_collection_name(tenant_id, doc_type)

        try:
            collection = client.get_collection(name=collection_name)
            collection.delete(ids=document_ids)

            logger.info(
                f"Deleted {len(document_ids)} documents",
                extra={"tenant_id": tenant_id, "type": doc_type.value}
            )

            return len(document_ids)

        except Exception as e:
            logger.error(f"Error deleting documents: {e}")
            return 0

    async def get_collection_stats(
        self,
        tenant_id: str,
    ) -> Dict[str, int]:
        """Retourne les statistiques des collections d'un tenant"""
        client = self._get_client()
        stats = {}

        for doc_type in DocumentType:
            collection_name = self._get_collection_name(tenant_id, doc_type)
            try:
                collection = client.get_collection(name=collection_name)
                stats[doc_type.value] = collection.count()
            except Exception:
                stats[doc_type.value] = 0

        return stats

