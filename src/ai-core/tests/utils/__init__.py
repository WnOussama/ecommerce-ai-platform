"""
Test Utilities - Mocks and helpers for testing

Ce module contient les implémentations mock pour les tests:
- InMemoryVectorStore: Vector store en mémoire
"""

from typing import Optional, List, Dict, Any


class InMemoryVectorStore:
    """
    Vector store en mémoire pour les tests.

    Implémente le même protocol que ChromaVectorStore
    mais stocke les données en mémoire.

    Usage:
        store = InMemoryVectorStore()
        await store.upsert(
            collection_name="test",
            ids=["doc1"],
            embeddings=[[0.1, 0.2]],
            documents=["Hello"],
            metadatas=[{"key": "value"}],
        )
    """

    def __init__(self):
        self._collections: Dict[str, Dict[str, Dict[str, Any]]] = {}

    def reset(self) -> None:
        """Réinitialise le stockage."""
        self._collections.clear()

    async def upsert(
        self,
        collection_name: str,
        ids: List[str],
        embeddings: List[List[float]],
        documents: List[str],
        metadatas: List[Dict[str, Any]],
    ) -> int:
        """Insère ou met à jour des documents."""
        if collection_name not in self._collections:
            self._collections[collection_name] = {}

        for i, doc_id in enumerate(ids):
            self._collections[collection_name][doc_id] = {
                "embedding": embeddings[i],
                "document": documents[i],
                "metadata": metadatas[i],
            }

        return len(ids)

    async def delete(
        self,
        collection_name: str,
        ids: List[str],
    ) -> int:
        """Supprime des documents."""
        if collection_name not in self._collections:
            return 0

        deleted = 0
        for doc_id in ids:
            if doc_id in self._collections[collection_name]:
                del self._collections[collection_name][doc_id]
                deleted += 1

        return deleted

    async def get_ids(
        self,
        collection_name: str,
    ) -> List[str]:
        """Récupère tous les IDs."""
        if collection_name not in self._collections:
            return []
        return list(self._collections[collection_name].keys())

    async def count(
        self,
        collection_name: str,
    ) -> int:
        """Compte les documents."""
        if collection_name not in self._collections:
            return 0
        return len(self._collections[collection_name])

    def get_document(
        self,
        collection_name: str,
        doc_id: str,
    ) -> Optional[Dict[str, Any]]:
        """Récupère un document (pour tests)."""
        if collection_name not in self._collections:
            return None
        return self._collections[collection_name].get(doc_id)

    def get_all_documents(
        self,
        collection_name: str,
    ) -> Dict[str, Dict[str, Any]]:
        """Récupère tous les documents (pour tests)."""
        if collection_name not in self._collections:
            return {}
        return self._collections[collection_name].copy()

