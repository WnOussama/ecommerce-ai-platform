"""
Vector Store Service - Abstraction ChromaDB avec support multi-tenant
"""

import hashlib
import logging
from datetime import datetime
from typing import Any, Dict, List
from uuid import UUID

import chromadb
from chromadb.config import Settings as ChromaSettings

from app.core.config.settings import settings

logger = logging.getLogger(__name__)


class VectorStoreService:
    """
    Service de stockage vectoriel avec:
    - Isolation par tenant
    - Collections par type de document
    - Metadata enrichies
    - Déduplication par hash
    """

    COLLECTION_TYPES = ["products", "faqs", "policies", "conversations"]

    def __init__(self, embedding_service):
        self.embeddings = embedding_service

        # Initialisation ChromaDB (client persistant - l'ancien Settings(chroma_db_impl=...)
        # est une configuration supprimée que chromadb refuse désormais au runtime)
        self.client = chromadb.PersistentClient(
            path=settings.vector_store.persist_directory,
            settings=ChromaSettings(anonymized_telemetry=False),
        )

        self._collections_cache: Dict[str, Any] = {}

    def _get_collection_name(self, tenant_id: UUID, collection_type: str) -> str:
        """Génère le nom de collection tenant-isolée"""
        return f"{settings.vector_store.collection_prefix}_{tenant_id}_{collection_type}"

    def _get_or_create_collection(self, tenant_id: UUID, collection_type: str):
        """Récupère ou crée une collection"""
        collection_name = self._get_collection_name(tenant_id, collection_type)

        if collection_name not in self._collections_cache:
            self._collections_cache[collection_name] = self.client.get_or_create_collection(
                name=collection_name,
                metadata={"tenant_id": str(tenant_id), "type": collection_type},
            )

        return self._collections_cache[collection_name]

    def _compute_content_hash(self, content: str) -> str:
        """Calcule un hash pour déduplication"""
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    async def add_document(
        self,
        tenant_id: UUID,
        collection_type: str,
        document_id: str,
        content: str,
        metadata: Dict[str, Any] = None,
    ) -> bool:
        """
        Ajoute un document au store.
        Retourne True si ajouté, False si déjà existant (même hash).
        """
        if collection_type not in self.COLLECTION_TYPES:
            raise ValueError(f"Invalid collection type: {collection_type}")

        collection = self._get_or_create_collection(tenant_id, collection_type)

        # Vérifier si le document existe déjà
        content_hash = self._compute_content_hash(content)
        existing = collection.get(where={"content_hash": content_hash})

        if existing and existing["ids"]:
            logger.debug(f"Document already exists with hash {content_hash}")
            return False

        # Générer l'embedding
        embedding = await self.embeddings.generate_embedding(content, tenant_id)

        # Metadata enrichies
        full_metadata = {
            "document_id": document_id,
            "content_hash": content_hash,
            "tenant_id": str(tenant_id),
            "indexed_at": datetime.utcnow().isoformat(),
            **(metadata or {}),
        }

        # Ajouter au store
        collection.add(
            ids=[document_id],
            embeddings=[embedding],
            documents=[content],
            metadatas=[full_metadata],
        )

        logger.info(
            f"Document added to {collection_type}",
            extra={"tenant_id": str(tenant_id), "document_id": document_id},
        )

        return True

    async def add_documents_batch(
        self, tenant_id: UUID, collection_type: str, documents: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        """
        Ajoute plusieurs documents en batch.
        documents: [{"id": str, "content": str, "metadata": dict}, ...]
        """
        collection = self._get_or_create_collection(tenant_id, collection_type)

        ids = []
        embeddings = []
        contents = []
        metadatas = []
        skipped = 0

        for doc in documents:
            content_hash = self._compute_content_hash(doc["content"])

            # Vérifier existence
            existing = collection.get(where={"content_hash": content_hash})
            if existing and existing["ids"]:
                skipped += 1
                continue

            ids.append(doc["id"])
            contents.append(doc["content"])

            # Générer embedding
            embedding = await self.embeddings.generate_embedding(doc["content"], tenant_id)
            embeddings.append(embedding)

            metadatas.append(
                {
                    "document_id": doc["id"],
                    "content_hash": content_hash,
                    "tenant_id": str(tenant_id),
                    "indexed_at": datetime.utcnow().isoformat(),
                    **(doc.get("metadata", {})),
                }
            )

        if ids:
            collection.add(ids=ids, embeddings=embeddings, documents=contents, metadatas=metadatas)

        return {"added": len(ids), "skipped": skipped}

    async def search(
        self,
        tenant_id: UUID,
        collection_type: str,
        query: str,
        limit: int = 5,
        filters: Dict[str, Any] = None,
        min_similarity: float = 0.5,
    ) -> List[Dict[str, Any]]:
        """
        Recherche sémantique dans une collection.
        """
        collection = self._get_or_create_collection(tenant_id, collection_type)

        # Générer l'embedding de la query
        query_embedding = await self.embeddings.generate_embedding(query, tenant_id)

        # Construire les filtres ChromaDB
        where_clause = {"tenant_id": str(tenant_id)}
        if filters:
            where_clause.update(filters)

        # Recherche
        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=limit,
            where=where_clause if len(where_clause) > 1 else None,
            include=["documents", "metadatas", "distances"],
        )

        # Formater les résultats
        formatted_results = []

        if results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                distance = results["distances"][0][i] if results["distances"] else 0
                similarity = 1 - (distance / 2)  # Convertir distance en similarité

                if similarity >= min_similarity:
                    formatted_results.append(
                        {
                            "id": doc_id,
                            "content": results["documents"][0][i],
                            "metadata": results["metadatas"][0][i],
                            "similarity": similarity,
                        }
                    )

        return formatted_results

    async def update_document(
        self,
        tenant_id: UUID,
        collection_type: str,
        document_id: str,
        content: str,
        metadata: Dict[str, Any] = None,
    ) -> bool:
        """Met à jour un document existant"""
        collection = self._get_or_create_collection(tenant_id, collection_type)

        # Générer nouvel embedding
        embedding = await self.embeddings.generate_embedding(content, tenant_id)

        # Metadata mises à jour
        full_metadata = {
            "document_id": document_id,
            "content_hash": self._compute_content_hash(content),
            "tenant_id": str(tenant_id),
            "updated_at": datetime.utcnow().isoformat(),
            **(metadata or {}),
        }

        collection.update(
            ids=[document_id],
            embeddings=[embedding],
            documents=[content],
            metadatas=[full_metadata],
        )

        return True

    async def delete_document(
        self, tenant_id: UUID, collection_type: str, document_id: str
    ) -> bool:
        """Supprime un document"""
        collection = self._get_or_create_collection(tenant_id, collection_type)
        collection.delete(ids=[document_id])
        return True

    async def delete_tenant_data(self, tenant_id: UUID):
        """Supprime toutes les données d'un tenant"""
        for collection_type in self.COLLECTION_TYPES:
            collection_name = self._get_collection_name(tenant_id, collection_type)
            try:
                self.client.delete_collection(collection_name)
                self._collections_cache.pop(collection_name, None)
            except Exception as e:
                logger.warning(f"Could not delete collection {collection_name}: {e}")

    def get_collection_stats(self, tenant_id: UUID) -> Dict[str, int]:
        """Retourne les statistiques des collections du tenant"""
        stats = {}

        for collection_type in self.COLLECTION_TYPES:
            try:
                collection = self._get_or_create_collection(tenant_id, collection_type)
                stats[collection_type] = collection.count()
            except Exception:
                stats[collection_type] = 0

        return stats
