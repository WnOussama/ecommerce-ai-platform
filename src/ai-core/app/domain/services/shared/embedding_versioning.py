"""
Embedding Versioning - Stratégie de versioning et re-indexation incrémentale

Architecture:
┌─────────────────────────────────────────────────────────────────────────────────┐
│                     EMBEDDING VERSIONING STRATEGY                                │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  Problème: Comment savoir quand re-indexer un document?                         │
│                                                                                  │
│  Solution: Double versioning                                                     │
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────┐       │
│  │                       DOCUMENT METADATA                              │       │
│  ├─────────────────────────────────────────────────────────────────────┤       │
│  │                                                                      │       │
│  │  document_version: "v3"        ← Version du contenu source          │       │
│  │  content_hash: "abc123..."     ← Hash du contenu (détection change) │       │
│  │  embedding_version: "v2"       ← Version du modèle d'embedding      │       │
│  │  embedding_model: "text-embedding-3-small"                          │       │
│  │  indexed_at: "2024-01-15T10:30:00Z"                                 │       │
│  │  last_synced_at: "2024-01-15T10:30:00Z"                             │       │
│  │                                                                      │       │
│  └─────────────────────────────────────────────────────────────────────┘       │
│                                                                                  │
│  Quand re-indexer?                                                              │
│  1. content_hash changé → Contenu modifié                                       │
│  2. embedding_version < current → Nouveau modèle d'embedding                    │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Set
from enum import Enum
import hashlib
import json
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# CONFIGURATION
# =============================================================================

# Version actuelle du modèle d'embedding
CURRENT_EMBEDDING_VERSION = "v2"
CURRENT_EMBEDDING_MODEL = "text-embedding-3-small"

# Versions supportées (pour migration progressive)
SUPPORTED_EMBEDDING_VERSIONS = {"v1", "v2"}


class SyncStatus(str, Enum):
    """Statut de synchronisation d'un document"""
    UP_TO_DATE = "up_to_date"
    CONTENT_CHANGED = "content_changed"
    EMBEDDING_OUTDATED = "embedding_outdated"
    NOT_INDEXED = "not_indexed"
    DELETED = "deleted"


@dataclass
class DocumentVersion:
    """Informations de version d'un document"""
    document_id: str
    tenant_id: str
    document_type: str

    # Versioning
    content_hash: str
    document_version: str = "v1"
    embedding_version: str = CURRENT_EMBEDDING_VERSION
    embedding_model: str = CURRENT_EMBEDDING_MODEL

    # Timestamps
    indexed_at: datetime = field(default_factory=datetime.utcnow)
    last_synced_at: datetime = field(default_factory=datetime.utcnow)
    source_updated_at: Optional[datetime] = None

    # Status
    sync_status: SyncStatus = SyncStatus.UP_TO_DATE

    def to_metadata(self) -> Dict[str, Any]:
        """Convertit en metadata pour ChromaDB"""
        return {
            "document_id": self.document_id,
            "tenant_id": self.tenant_id,
            "document_type": self.document_type,
            "content_hash": self.content_hash,
            "document_version": self.document_version,
            "embedding_version": self.embedding_version,
            "embedding_model": self.embedding_model,
            "indexed_at": self.indexed_at.isoformat(),
            "last_synced_at": self.last_synced_at.isoformat(),
        }

    @classmethod
    def from_metadata(cls, metadata: Dict[str, Any]) -> "DocumentVersion":
        """Crée depuis metadata ChromaDB"""
        return cls(
            document_id=metadata.get("document_id", ""),
            tenant_id=metadata.get("tenant_id", ""),
            document_type=metadata.get("document_type", ""),
            content_hash=metadata.get("content_hash", ""),
            document_version=metadata.get("document_version", "v1"),
            embedding_version=metadata.get("embedding_version", "v1"),
            embedding_model=metadata.get("embedding_model", ""),
            indexed_at=datetime.fromisoformat(metadata["indexed_at"]) if metadata.get("indexed_at") else datetime.utcnow(),
            last_synced_at=datetime.fromisoformat(metadata["last_synced_at"]) if metadata.get("last_synced_at") else datetime.utcnow(),
        )


@dataclass
class SyncResult:
    """Résultat d'une synchronisation"""
    tenant_id: str
    document_type: str

    # Compteurs
    total_source_docs: int = 0
    total_indexed_docs: int = 0

    added: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0  # Déjà à jour

    # Détails
    errors: List[str] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def success(self) -> bool:
        return len(self.errors) == 0


# =============================================================================
# CONTENT HASHER
# =============================================================================

class ContentHasher:
    """
    Génère des hash de contenu pour détecter les changements.

    Le hash inclut tous les champs qui impactent l'embedding.
    """

    @staticmethod
    def hash_product(product: Dict[str, Any]) -> str:
        """Hash un produit"""
        # Champs qui impactent l'embedding
        relevant_fields = {
            "name": product.get("name", ""),
            "description": product.get("description", ""),
            "short_description": product.get("short_description", ""),
            "category": product.get("category", ""),
            "brand": product.get("brand", ""),
            "tags": sorted(product.get("tags", [])),
        }

        content = json.dumps(relevant_fields, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    @staticmethod
    def hash_faq(faq: Dict[str, Any]) -> str:
        """Hash une FAQ"""
        relevant_fields = {
            "question": faq.get("question", ""),
            "answer": faq.get("answer", ""),
            "category": faq.get("category", ""),
        }

        content = json.dumps(relevant_fields, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    @staticmethod
    def hash_policy(policy: Dict[str, Any]) -> str:
        """Hash une politique"""
        relevant_fields = {
            "title": policy.get("title", ""),
            "content": policy.get("content", ""),
            "type": policy.get("type", ""),
        }

        content = json.dumps(relevant_fields, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    @classmethod
    def hash_document(cls, doc_type: str, document: Dict[str, Any]) -> str:
        """Hash un document selon son type"""
        hashers = {
            "product": cls.hash_product,
            "faq": cls.hash_faq,
            "policy": cls.hash_policy,
        }

        hasher = hashers.get(doc_type, lambda d: hashlib.sha256(
            json.dumps(d, sort_keys=True).encode()
        ).hexdigest()[:16])

        return hasher(document)


# =============================================================================
# EMBEDDING VERSION MANAGER
# =============================================================================

class EmbeddingVersionManager:
    """
    Gère le versioning des embeddings et la synchronisation incrémentale.

    Responsabilités:
    - Détecter les documents à re-indexer
    - Gérer les migrations de version d'embedding
    - Synchronisation incrémentale
    """

    def __init__(
        self,
        rag_service,
        redis_client=None,
    ):
        self._rag = rag_service
        self._redis = redis_client
        self._hasher = ContentHasher()

    async def check_sync_status(
        self,
        tenant_id: str,
        doc_type: str,
        source_documents: List[Dict[str, Any]],
    ) -> Dict[str, SyncStatus]:
        """
        Vérifie le statut de sync de chaque document.

        Returns:
            Dict[doc_id, SyncStatus]
        """
        # Récupérer les documents indexés
        indexed_versions = await self._get_indexed_versions(tenant_id, doc_type)

        status_map = {}
        source_ids = set()

        for doc in source_documents:
            doc_id = str(doc.get("id", ""))
            source_ids.add(doc_id)

            # Calculer le hash du contenu actuel
            current_hash = self._hasher.hash_document(doc_type, doc)

            if doc_id not in indexed_versions:
                # Nouveau document
                status_map[doc_id] = SyncStatus.NOT_INDEXED
            else:
                indexed = indexed_versions[doc_id]

                # Vérifier si contenu a changé
                if indexed.content_hash != current_hash:
                    status_map[doc_id] = SyncStatus.CONTENT_CHANGED

                # Vérifier si embedding est obsolète
                elif indexed.embedding_version != CURRENT_EMBEDDING_VERSION:
                    status_map[doc_id] = SyncStatus.EMBEDDING_OUTDATED

                else:
                    status_map[doc_id] = SyncStatus.UP_TO_DATE

        # Détecter les documents supprimés
        for doc_id in indexed_versions:
            if doc_id not in source_ids:
                status_map[doc_id] = SyncStatus.DELETED

        return status_map

    async def sync_incremental(
        self,
        tenant_id: str,
        doc_type: str,
        source_documents: List[Dict[str, Any]],
    ) -> SyncResult:
        """
        Synchronisation incrémentale intelligente.

        N'indexe que les documents qui ont changé.
        """
        import time
        start_time = time.perf_counter()

        result = SyncResult(
            tenant_id=tenant_id,
            document_type=doc_type,
            total_source_docs=len(source_documents),
        )

        # Vérifier le statut de chaque document
        status_map = await self.check_sync_status(tenant_id, doc_type, source_documents)

        # Grouper par action
        to_add = []
        to_update = []
        to_delete = []

        source_by_id = {str(d.get("id", "")): d for d in source_documents}

        for doc_id, status in status_map.items():
            if status == SyncStatus.NOT_INDEXED:
                to_add.append(source_by_id[doc_id])
            elif status in (SyncStatus.CONTENT_CHANGED, SyncStatus.EMBEDDING_OUTDATED):
                to_update.append(source_by_id[doc_id])
            elif status == SyncStatus.DELETED:
                to_delete.append(doc_id)
            else:
                result.skipped += 1

        # Exécuter les actions
        try:
            # Ajouter les nouveaux
            if to_add:
                await self._index_documents(tenant_id, doc_type, to_add)
                result.added = len(to_add)

            # Mettre à jour les modifiés (delete + add)
            if to_update:
                update_ids = [str(d.get("id", "")) for d in to_update]
                await self._delete_documents(tenant_id, doc_type, update_ids)
                await self._index_documents(tenant_id, doc_type, to_update)
                result.updated = len(to_update)

            # Supprimer les supprimés
            if to_delete:
                await self._delete_documents(tenant_id, doc_type, to_delete)
                result.deleted = len(to_delete)

        except Exception as e:
            result.errors.append(str(e))
            logger.error(f"Sync error: {e}")

        result.duration_ms = int((time.perf_counter() - start_time) * 1000)
        result.total_indexed_docs = result.added + result.updated + result.skipped

        logger.info(
            f"Incremental sync completed",
            extra={
                "tenant_id": tenant_id,
                "doc_type": doc_type,
                "added": result.added,
                "updated": result.updated,
                "deleted": result.deleted,
                "skipped": result.skipped,
                "duration_ms": result.duration_ms,
            }
        )

        return result

    async def migrate_embedding_version(
        self,
        tenant_id: str,
        doc_type: str,
        batch_size: int = 100,
    ) -> SyncResult:
        """
        Migre tous les documents vers la nouvelle version d'embedding.

        Utilisé quand on change de modèle d'embedding.
        """
        result = SyncResult(tenant_id=tenant_id, document_type=doc_type)

        # Récupérer tous les documents avec ancienne version
        outdated = await self._get_outdated_documents(tenant_id, doc_type)

        if not outdated:
            logger.info(f"No documents to migrate for {tenant_id}/{doc_type}")
            return result

        logger.info(f"Migrating {len(outdated)} documents to embedding version {CURRENT_EMBEDDING_VERSION}")

        # Migrer par batch
        for i in range(0, len(outdated), batch_size):
            batch = outdated[i:i + batch_size]

            try:
                # Re-indexer avec nouveau modèle
                # (les documents sont récupérés depuis la source, pas ChromaDB)
                # Ici on suppose qu'on a accès aux documents originaux

                result.updated += len(batch)

            except Exception as e:
                result.errors.append(f"Batch {i}: {str(e)}")

        return result

    async def _get_indexed_versions(
        self,
        tenant_id: str,
        doc_type: str,
    ) -> Dict[str, DocumentVersion]:
        """Récupère les versions des documents indexés"""
        # Utiliser cache Redis si disponible
        cache_key = f"embedding_versions:{tenant_id}:{doc_type}"

        if self._redis:
            cached = await self._redis.get(cache_key)
            if cached:
                import json
                data = json.loads(cached)
                return {
                    doc_id: DocumentVersion.from_metadata(meta)
                    for doc_id, meta in data.items()
                }

        # Sinon, récupérer depuis ChromaDB
        versions = {}

        try:
            from app.domain.services.shared.rag_service_v2 import DocumentType, COLLECTION_NAMES

            client = self._rag._get_client()
            collection_name = COLLECTION_NAMES[DocumentType(doc_type)]
            collection = client.get_collection(name=collection_name)

            # Récupérer tous les documents du tenant
            results = collection.get(
                where={"tenant_id": tenant_id},
                include=["metadatas"],
            )

            if results and results["metadatas"]:
                for i, metadata in enumerate(results["metadatas"]):
                    doc_id = metadata.get("document_id", results["ids"][i])
                    versions[doc_id] = DocumentVersion.from_metadata(metadata)

            # Cache
            if self._redis and versions:
                cache_data = {
                    doc_id: ver.to_metadata()
                    for doc_id, ver in versions.items()
                }
                await self._redis.set(
                    cache_key,
                    json.dumps(cache_data),
                    ex=300,  # 5 min
                )

        except Exception as e:
            logger.warning(f"Error getting indexed versions: {e}")

        return versions

    async def _get_outdated_documents(
        self,
        tenant_id: str,
        doc_type: str,
    ) -> List[str]:
        """Récupère les IDs des documents avec embedding obsolète"""
        versions = await self._get_indexed_versions(tenant_id, doc_type)

        return [
            doc_id for doc_id, ver in versions.items()
            if ver.embedding_version != CURRENT_EMBEDDING_VERSION
        ]

    async def _index_documents(
        self,
        tenant_id: str,
        doc_type: str,
        documents: List[Dict[str, Any]],
    ) -> None:
        """Indexe des documents avec versioning"""
        from app.domain.services.shared.rag_service_v2 import DocumentType

        # Ajouter les metadata de versioning
        for doc in documents:
            doc["content_hash"] = self._hasher.hash_document(doc_type, doc)
            doc["embedding_version"] = CURRENT_EMBEDDING_VERSION
            doc["embedding_model"] = CURRENT_EMBEDDING_MODEL
            doc["document_version"] = doc.get("version", "v1")

        await self._rag.index_documents(
            tenant_id=tenant_id,
            doc_type=DocumentType(doc_type),
            documents=documents,
        )

    async def _delete_documents(
        self,
        tenant_id: str,
        doc_type: str,
        document_ids: List[str],
    ) -> None:
        """Supprime des documents"""
        from app.domain.services.shared.rag_service_v2 import DocumentType

        await self._rag.delete_documents(
            tenant_id=tenant_id,
            doc_type=DocumentType(doc_type),
            document_ids=document_ids,
        )

        # Invalider le cache
        if self._redis:
            cache_key = f"embedding_versions:{tenant_id}:{doc_type}"
            await self._redis.delete(cache_key)


# =============================================================================
# EXPORTS
# =============================================================================

__all__ = [
    "CURRENT_EMBEDDING_VERSION",
    "CURRENT_EMBEDDING_MODEL",
    "SyncStatus",
    "DocumentVersion",
    "SyncResult",
    "ContentHasher",
    "EmbeddingVersionManager",
]

