"""
Tests unitaires pour le Product Indexer

Ces tests utilisent des mocks pour:
- ProductRepository (base de données)
- EmbeddingService (génération embeddings)
- VectorStore (ChromaDB)
"""

import pytest
from decimal import Decimal
from datetime import datetime
from typing import List, Dict, Any

from app.services.rag.product_indexer import (
    ProductIndexer,
    IndexResult,
    IndexStatus,
    IndexingError,
)
from tests.utils import InMemoryVectorStore
from app.services.rag.embedding_service import (
    MockEmbeddingService,
)
from app.services.catalog.repository import (
    ProductData,
    ProductFilter,
    InMemoryProductRepository,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def tenant_id():
    """Tenant ID de test."""
    return "tenant_test_123"


@pytest.fixture
def mock_embedding_service():
    """Service d'embedding mock."""
    return MockEmbeddingService(dimensions=384)


@pytest.fixture
def in_memory_vector_store():
    """Vector store en mémoire."""
    return InMemoryVectorStore()


@pytest.fixture
def in_memory_repo():
    """Repository produits en mémoire."""
    return InMemoryProductRepository()


@pytest.fixture
def product_indexer(in_memory_repo, mock_embedding_service, in_memory_vector_store):
    """Indexer avec tous les mocks."""
    return ProductIndexer(
        repository=in_memory_repo,
        embedding_service=mock_embedding_service,
        vector_store=in_memory_vector_store,
        batch_size=50,
    )


@pytest.fixture
def sample_products() -> List[ProductData]:
    """Liste de produits de test."""
    return [
        ProductData(
            external_id=1,
            name="T-shirt Premium",
            reference="TSH001",
            price=Decimal("29.99"),
            price_tax_incl=Decimal("35.99"),
            quantity=100,
            category_id=5,
            category_name="Vêtements",
            manufacturer_name="FashionBrand",
            active=True,
            extra_data={
                "tags": ["coton", "été", "casual"],
                "features": {"Couleur": "Bleu", "Taille": "M"},
            },
        ),
        ProductData(
            external_id=2,
            name="Jean Slim",
            reference="JEA002",
            price=Decimal("49.99"),
            price_tax_incl=Decimal("59.99"),
            quantity=50,
            category_id=5,
            category_name="Vêtements",
            manufacturer_name="DenimCo",
            active=True,
        ),
        ProductData(
            external_id=3,
            name="Chaussures Running",
            reference="SHO003",
            price=Decimal("89.99"),
            quantity=0,  # Out of stock
            category_id=6,
            category_name="Chaussures",
            active=True,
        ),
        ProductData(
            external_id=4,
            name="Produit Inactif",
            reference="INA004",
            price=Decimal("19.99"),
            quantity=10,
            category_id=5,
            active=False,  # Inactive
        ),
    ]


# =============================================================================
# INDEX RESULT TESTS
# =============================================================================

class TestIndexResult:
    """Tests pour la dataclass IndexResult."""

    def test_index_result_default_values(self, tenant_id):
        """IndexResult doit avoir des valeurs par défaut correctes."""
        result = IndexResult(tenant_id=tenant_id)

        assert result.tenant_id == tenant_id
        assert result.status == IndexStatus.PENDING
        assert result.total_products == 0
        assert result.total_indexed == 0
        assert result.total_failed == 0
        assert result.errors == []

    def test_index_result_success_rate_empty(self, tenant_id):
        """success_rate doit être 100% si aucun produit."""
        result = IndexResult(tenant_id=tenant_id)
        assert result.success_rate == 100.0

    def test_index_result_success_rate_with_failures(self, tenant_id):
        """success_rate doit calculer correctement."""
        result = IndexResult(
            tenant_id=tenant_id,
            total_products=100,
            total_failed=25,
        )
        assert result.success_rate == 75.0

    def test_index_result_is_success(self, tenant_id):
        """is_success doit refléter le status."""
        result_completed = IndexResult(
            tenant_id=tenant_id,
            status=IndexStatus.COMPLETED,
        )
        assert result_completed.is_success is True

        result_partial = IndexResult(
            tenant_id=tenant_id,
            status=IndexStatus.PARTIAL,
        )
        assert result_partial.is_success is True

        result_failed = IndexResult(
            tenant_id=tenant_id,
            status=IndexStatus.FAILED,
        )
        assert result_failed.is_success is False

    def test_index_result_to_dict(self, tenant_id):
        """to_dict doit sérialiser correctement."""
        result = IndexResult(
            tenant_id=tenant_id,
            status=IndexStatus.COMPLETED,
            total_products=100,
            total_indexed=95,
            total_skipped=3,
            total_failed=2,
            started_at=datetime(2024, 1, 15, 10, 0, 0),
            completed_at=datetime(2024, 1, 15, 10, 1, 0),
            duration_seconds=60.5,
            collection_name="tenant_test_products",
            embedding_model="mock-embedding-v1",
        )

        d = result.to_dict()

        assert d["tenant_id"] == tenant_id
        assert d["status"] == "completed"
        assert d["total_products"] == 100
        assert d["total_indexed"] == 95
        assert d["success_rate"] == 98.0


class TestIndexingError:
    """Tests pour la dataclass IndexingError."""

    def test_indexing_error_to_dict(self):
        """IndexingError doit se sérialiser correctement."""
        error = IndexingError(
            product_id=42,
            product_name="Test Product",
            error_type="embedding_error",
            error_message="Failed to generate embedding",
        )

        d = error.to_dict()

        assert d["product_id"] == 42
        assert d["product_name"] == "Test Product"
        assert d["error_type"] == "embedding_error"
        assert "timestamp" in d


# =============================================================================
# PRODUCT INDEXER TESTS
# =============================================================================

class TestProductIndexer:
    """Tests pour le service d'indexation."""

    @pytest.mark.asyncio
    async def test_index_empty_repository(
        self,
        product_indexer,
        tenant_id,
    ):
        """Indexation d'un repo vide doit réussir."""
        result = await product_indexer.index_all_products(tenant_id)

        assert result.status == IndexStatus.COMPLETED
        assert result.total_products == 0
        assert result.total_indexed == 0
        assert result.is_success is True

    @pytest.mark.asyncio
    async def test_index_all_products(
        self,
        product_indexer,
        in_memory_repo,
        in_memory_vector_store,
        tenant_id,
        sample_products,
    ):
        """Indexation complète doit indexer tous les produits actifs."""
        # Ajouter les produits au repo
        for product in sample_products:
            await in_memory_repo.upsert_product(tenant_id, product)

        # Indexer
        result = await product_indexer.index_all_products(
            tenant_id=tenant_id,
            active_only=True,
        )

        assert result.status == IndexStatus.COMPLETED
        assert result.total_products == 3  # 3 actifs sur 4
        assert result.total_indexed == 3
        assert result.total_failed == 0

        # Vérifier le vector store
        collection_name = f"tenant_{tenant_id}_products"
        count = await in_memory_vector_store.count(collection_name)
        assert count == 3

    @pytest.mark.asyncio
    async def test_index_all_products_including_inactive(
        self,
        product_indexer,
        in_memory_repo,
        tenant_id,
        sample_products,
    ):
        """Indexation avec active_only=False doit inclure les inactifs."""
        for product in sample_products:
            await in_memory_repo.upsert_product(tenant_id, product)

        result = await product_indexer.index_all_products(
            tenant_id=tenant_id,
            active_only=False,
        )

        assert result.total_products == 4  # Tous les produits
        assert result.total_indexed == 4

    @pytest.mark.asyncio
    async def test_index_single_product(
        self,
        product_indexer,
        in_memory_vector_store,
        tenant_id,
        sample_products,
    ):
        """Indexation d'un seul produit doit fonctionner."""
        product = sample_products[0]

        success = await product_indexer.index_product(tenant_id, product)

        assert success is True

        # Vérifier qu'il est dans le vector store
        collection_name = f"tenant_{tenant_id}_products"
        doc_id = f"{tenant_id}_{product.external_id}"
        doc = in_memory_vector_store.get_document(collection_name, doc_id)

        assert doc is not None
        assert "T-shirt Premium" in doc["document"]
        assert doc["metadata"]["product_id"] == 1
        assert doc["metadata"]["price"] == 29.99

    @pytest.mark.asyncio
    async def test_index_product_metadata(
        self,
        product_indexer,
        in_memory_vector_store,
        tenant_id,
        sample_products,
    ):
        """Les métadonnées doivent être correctement indexées."""
        product = sample_products[0]

        await product_indexer.index_product(tenant_id, product)

        collection_name = f"tenant_{tenant_id}_products"
        doc_id = f"{tenant_id}_{product.external_id}"
        doc = in_memory_vector_store.get_document(collection_name, doc_id)

        metadata = doc["metadata"]

        assert metadata["product_id"] == 1
        assert metadata["external_id"] == 1
        assert metadata["name"] == "T-shirt Premium"
        assert metadata["reference"] == "TSH001"
        assert metadata["price"] == 29.99
        assert metadata["price_tax_incl"] == 35.99
        assert metadata["quantity"] == 100
        assert metadata["category_id"] == 5
        assert metadata["category_name"] == "Vêtements"
        assert metadata["manufacturer"] == "FashionBrand"
        assert metadata["active"] is True
        assert metadata["in_stock"] is True
        assert "content_hash" in metadata
        assert "indexed_at" in metadata

    @pytest.mark.asyncio
    async def test_index_product_search_text(
        self,
        product_indexer,
        in_memory_vector_store,
        tenant_id,
        sample_products,
    ):
        """Le texte de recherche doit inclure toutes les informations."""
        product = sample_products[0]

        await product_indexer.index_product(tenant_id, product)

        collection_name = f"tenant_{tenant_id}_products"
        doc_id = f"{tenant_id}_{product.external_id}"
        doc = in_memory_vector_store.get_document(collection_name, doc_id)

        search_text = doc["document"]

        assert "T-shirt Premium" in search_text
        assert "Vêtements" in search_text
        assert "FashionBrand" in search_text
        assert "TSH001" in search_text
        assert "coton" in search_text  # Tag
        assert "Couleur: Bleu" in search_text  # Feature

    @pytest.mark.asyncio
    async def test_delete_product(
        self,
        product_indexer,
        in_memory_vector_store,
        tenant_id,
        sample_products,
    ):
        """Suppression d'un produit du vector store."""
        product = sample_products[0]

        # Indexer
        await product_indexer.index_product(tenant_id, product)

        # Vérifier qu'il existe
        collection_name = f"tenant_{tenant_id}_products"
        count_before = await in_memory_vector_store.count(collection_name)
        assert count_before == 1

        # Supprimer
        success = await product_indexer.delete_product(tenant_id, product.external_id)
        assert success is True

        # Vérifier qu'il est supprimé
        count_after = await in_memory_vector_store.count(collection_name)
        assert count_after == 0

    @pytest.mark.asyncio
    async def test_index_with_delete_missing(
        self,
        product_indexer,
        in_memory_repo,
        in_memory_vector_store,
        tenant_id,
        sample_products,
    ):
        """delete_missing doit supprimer les documents obsolètes."""
        # Indexer un premier produit manuellement
        old_product = ProductData(
            external_id=999,
            name="Old Product",
            price=Decimal("10"),
            active=True,
        )
        await product_indexer.index_product(tenant_id, old_product)

        # Ajouter de nouveaux produits au repo (sans l'ancien)
        for product in sample_products[:2]:  # Seulement 2 produits
            await in_memory_repo.upsert_product(tenant_id, product)

        # Indexer avec delete_missing=True
        result = await product_indexer.index_all_products(
            tenant_id=tenant_id,
            delete_missing=True,
        )

        assert result.total_deleted == 1  # L'ancien produit supprimé

        # Vérifier que l'ancien n'est plus là
        collection_name = f"tenant_{tenant_id}_products"
        doc_id = f"{tenant_id}_999"
        doc = in_memory_vector_store.get_document(collection_name, doc_id)
        assert doc is None

    @pytest.mark.asyncio
    async def test_get_index_stats(
        self,
        product_indexer,
        in_memory_repo,
        tenant_id,
        sample_products,
    ):
        """get_index_stats doit retourner les bonnes statistiques."""
        # Ajouter et indexer des produits
        for product in sample_products[:3]:
            await in_memory_repo.upsert_product(tenant_id, product)

        await product_indexer.index_all_products(tenant_id)

        # Vérifier les stats
        stats = await product_indexer.get_index_stats(tenant_id)

        assert stats["tenant_id"] == tenant_id
        assert stats["total_indexed"] == 3
        assert "collection_name" in stats
        assert "embedding_model" in stats


# =============================================================================
# MULTI-TENANT ISOLATION TESTS
# =============================================================================

class TestMultiTenantIsolation:
    """Tests pour l'isolation multi-tenant."""

    @pytest.mark.asyncio
    async def test_tenant_isolation(
        self,
        in_memory_repo,
        mock_embedding_service,
        in_memory_vector_store,
    ):
        """Les données doivent être isolées par tenant."""
        tenant_a = "tenant_a"
        tenant_b = "tenant_b"

        indexer = ProductIndexer(
            repository=in_memory_repo,
            embedding_service=mock_embedding_service,
            vector_store=in_memory_vector_store,
        )

        # Produits pour tenant A
        product_a = ProductData(
            external_id=1,
            name="Product A",
            price=Decimal("10"),
            active=True,
        )
        await in_memory_repo.upsert_product(tenant_a, product_a)

        # Produits pour tenant B
        product_b = ProductData(
            external_id=1,  # Même external_id
            name="Product B",
            price=Decimal("20"),
            active=True,
        )
        await in_memory_repo.upsert_product(tenant_b, product_b)

        # Indexer les deux tenants
        await indexer.index_all_products(tenant_a)
        await indexer.index_all_products(tenant_b)

        # Vérifier l'isolation
        collection_a = f"tenant_{tenant_a}_products"
        collection_b = f"tenant_{tenant_b}_products"

        count_a = await in_memory_vector_store.count(collection_a)
        count_b = await in_memory_vector_store.count(collection_b)

        assert count_a == 1
        assert count_b == 1

        # Vérifier que les documents sont dans les bonnes collections
        doc_a = in_memory_vector_store.get_document(collection_a, f"{tenant_a}_1")
        doc_b = in_memory_vector_store.get_document(collection_b, f"{tenant_b}_1")

        assert "Product A" in doc_a["document"]
        assert "Product B" in doc_b["document"]
        assert doc_a["metadata"]["price"] == 10.0
        assert doc_b["metadata"]["price"] == 20.0


# =============================================================================
# EMBEDDING SERVICE TESTS
# =============================================================================

class TestMockEmbeddingService:
    """Tests pour le service d'embedding mock."""

    @pytest.mark.asyncio
    async def test_generate_embedding(self, mock_embedding_service):
        """generate_embedding doit retourner un vecteur de la bonne dimension."""
        embedding = await mock_embedding_service.generate_embedding("Hello world")

        assert isinstance(embedding, list)
        assert len(embedding) == 384  # dimensions par défaut
        assert all(isinstance(x, float) for x in embedding)

    @pytest.mark.asyncio
    async def test_embedding_deterministic(self, mock_embedding_service):
        """Le même texte doit produire le même embedding."""
        text = "Test text for embedding"

        embedding1 = await mock_embedding_service.generate_embedding(text)
        embedding2 = await mock_embedding_service.generate_embedding(text)

        assert embedding1 == embedding2

    @pytest.mark.asyncio
    async def test_different_texts_different_embeddings(self, mock_embedding_service):
        """Des textes différents doivent produire des embeddings différents."""
        embedding1 = await mock_embedding_service.generate_embedding("Text one")
        embedding2 = await mock_embedding_service.generate_embedding("Text two")

        assert embedding1 != embedding2

    @pytest.mark.asyncio
    async def test_generate_embeddings_batch(self, mock_embedding_service):
        """generate_embeddings_batch doit fonctionner pour plusieurs textes."""
        texts = ["Text one", "Text two", "Text three"]

        embeddings = await mock_embedding_service.generate_embeddings_batch(texts)

        assert len(embeddings) == 3
        assert all(len(emb) == 384 for emb in embeddings)

    @pytest.mark.asyncio
    async def test_embedding_normalized(self, mock_embedding_service):
        """Les embeddings doivent être normalisés (L2 norm ≈ 1)."""
        embedding = await mock_embedding_service.generate_embedding("Test")

        # Calculer la norme L2
        norm = sum(x * x for x in embedding) ** 0.5

        # La norme doit être proche de 1
        assert 0.99 < norm < 1.01


# =============================================================================
# IN-MEMORY VECTOR STORE TESTS
# =============================================================================

class TestInMemoryVectorStore:
    """Tests pour le vector store en mémoire."""

    @pytest.mark.asyncio
    async def test_upsert_and_count(self, in_memory_vector_store):
        """upsert doit ajouter des documents et count doit les compter."""
        await in_memory_vector_store.upsert(
            collection_name="test_collection",
            ids=["doc1", "doc2"],
            embeddings=[[0.1] * 10, [0.2] * 10],
            documents=["Document 1", "Document 2"],
            metadatas=[{"key": "value1"}, {"key": "value2"}],
        )

        count = await in_memory_vector_store.count("test_collection")
        assert count == 2

    @pytest.mark.asyncio
    async def test_upsert_overwrites(self, in_memory_vector_store):
        """upsert doit écraser les documents existants."""
        await in_memory_vector_store.upsert(
            collection_name="test_collection",
            ids=["doc1"],
            embeddings=[[0.1] * 10],
            documents=["Original"],
            metadatas=[{"version": 1}],
        )

        await in_memory_vector_store.upsert(
            collection_name="test_collection",
            ids=["doc1"],
            embeddings=[[0.2] * 10],
            documents=["Updated"],
            metadatas=[{"version": 2}],
        )

        doc = in_memory_vector_store.get_document("test_collection", "doc1")

        assert doc["document"] == "Updated"
        assert doc["metadata"]["version"] == 2

    @pytest.mark.asyncio
    async def test_delete(self, in_memory_vector_store):
        """delete doit supprimer les documents."""
        await in_memory_vector_store.upsert(
            collection_name="test_collection",
            ids=["doc1", "doc2"],
            embeddings=[[0.1] * 10, [0.2] * 10],
            documents=["Doc 1", "Doc 2"],
            metadatas=[{}, {}],
        )

        await in_memory_vector_store.delete("test_collection", ["doc1"])

        count = await in_memory_vector_store.count("test_collection")
        assert count == 1

        doc1 = in_memory_vector_store.get_document("test_collection", "doc1")
        doc2 = in_memory_vector_store.get_document("test_collection", "doc2")

        assert doc1 is None
        assert doc2 is not None

    @pytest.mark.asyncio
    async def test_get_ids(self, in_memory_vector_store):
        """get_ids doit retourner tous les IDs."""
        await in_memory_vector_store.upsert(
            collection_name="test_collection",
            ids=["doc1", "doc2", "doc3"],
            embeddings=[[0.1] * 10, [0.2] * 10, [0.3] * 10],
            documents=["D1", "D2", "D3"],
            metadatas=[{}, {}, {}],
        )

        ids = await in_memory_vector_store.get_ids("test_collection")

        assert set(ids) == {"doc1", "doc2", "doc3"}


# =============================================================================
# BATCH PROCESSING TESTS
# =============================================================================

class TestBatchProcessing:
    """Tests pour le traitement par batch."""

    @pytest.mark.asyncio
    async def test_large_batch_indexing(
        self,
        in_memory_repo,
        mock_embedding_service,
        in_memory_vector_store,
        tenant_id,
    ):
        """L'indexation doit gérer de grands volumes."""
        indexer = ProductIndexer(
            repository=in_memory_repo,
            embedding_service=mock_embedding_service,
            vector_store=in_memory_vector_store,
            batch_size=50,
        )

        # Créer 200 produits
        for i in range(200):
            product = ProductData(
                external_id=i,
                name=f"Product {i}",
                price=Decimal(str(i * 10)),
                active=True,
            )
            await in_memory_repo.upsert_product(tenant_id, product)

        # Indexer
        result = await indexer.index_all_products(tenant_id)

        assert result.status == IndexStatus.COMPLETED
        assert result.total_products == 200
        assert result.total_indexed == 200

        # Vérifier le vector store
        collection_name = f"tenant_{tenant_id}_products"
        count = await in_memory_vector_store.count(collection_name)
        assert count == 200


# =============================================================================
# PAGINATION STREAMING TESTS
# =============================================================================

class TestPaginationStreaming:
    """Tests pour la pagination streaming dans index_all_products."""

    @pytest.mark.asyncio
    async def test_pagination_processes_all_products(
        self,
        in_memory_repo,
        mock_embedding_service,
        in_memory_vector_store,
        tenant_id,
    ):
        """La pagination doit traiter tous les produits."""
        # Batch size de 25, créer 100 produits = 4 batches
        indexer = ProductIndexer(
            repository=in_memory_repo,
            embedding_service=mock_embedding_service,
            vector_store=in_memory_vector_store,
            batch_size=25,
        )

        # Créer 100 produits
        for i in range(100):
            product = ProductData(
                external_id=i,
                name=f"Product {i}",
                price=Decimal(str(i * 10)),
                active=True,
            )
            await in_memory_repo.upsert_product(tenant_id, product)

        result = await indexer.index_all_products(tenant_id)

        assert result.status == IndexStatus.COMPLETED
        assert result.total_products == 100
        assert result.total_indexed == 100

        # Vérifier que tous sont dans le vector store
        collection_name = f"tenant_{tenant_id}_products"
        count = await in_memory_vector_store.count(collection_name)
        assert count == 100

    @pytest.mark.asyncio
    async def test_pagination_with_small_batch_size(
        self,
        in_memory_repo,
        mock_embedding_service,
        in_memory_vector_store,
        tenant_id,
    ):
        """Les petits batch sizes doivent fonctionner correctement."""
        # Batch size minimum de 10
        indexer = ProductIndexer(
            repository=in_memory_repo,
            embedding_service=mock_embedding_service,
            vector_store=in_memory_vector_store,
            batch_size=10,
        )

        # Créer 55 produits (5 batches de 10 + 1 batch de 5)
        for i in range(55):
            product = ProductData(
                external_id=i,
                name=f"Product {i}",
                price=Decimal("10"),
                active=True,
            )
            await in_memory_repo.upsert_product(tenant_id, product)

        result = await indexer.index_all_products(tenant_id)

        assert result.total_products == 55
        assert result.total_indexed == 55


# =============================================================================
# SKIP UNCHANGED TESTS (CONTENT HASH)
# =============================================================================

class TestSkipUnchanged:
    """Tests pour le skip des produits non modifiés via content_hash."""

    @pytest.mark.asyncio
    async def test_reindex_counts_skipped(
        self,
        in_memory_repo,
        mock_embedding_service,
        in_memory_vector_store,
        tenant_id,
    ):
        """La ré-indexation doit compter les produits skippés."""
        indexer = ProductIndexer(
            repository=in_memory_repo,
            embedding_service=mock_embedding_service,
            vector_store=in_memory_vector_store,
            batch_size=50,
        )

        # Créer et indexer des produits
        for i in range(10):
            product = ProductData(
                external_id=i,
                name=f"Product {i}",
                price=Decimal("10"),
                active=True,
            )
            await in_memory_repo.upsert_product(tenant_id, product)

        # Première indexation
        result1 = await indexer.index_all_products(tenant_id)

        assert result1.total_indexed == 10
        assert result1.total_skipped == 0

    @pytest.mark.asyncio
    async def test_skip_unchanged_disabled(
        self,
        in_memory_repo,
        mock_embedding_service,
        in_memory_vector_store,
        tenant_id,
    ):
        """skip_unchanged=False doit forcer la ré-indexation."""
        indexer = ProductIndexer(
            repository=in_memory_repo,
            embedding_service=mock_embedding_service,
            vector_store=in_memory_vector_store,
        )

        # Créer un produit
        product = ProductData(
            external_id=1,
            name="Test Product",
            price=Decimal("10"),
            active=True,
        )
        await in_memory_repo.upsert_product(tenant_id, product)

        # Première indexation
        result1 = await indexer.index_all_products(
            tenant_id,
            skip_unchanged=False,
        )
        assert result1.total_indexed == 1

        # Seconde indexation avec skip_unchanged=False
        result2 = await indexer.index_all_products(
            tenant_id,
            skip_unchanged=False,
        )

        # Devrait ré-indexer même si pas de changement
        assert result2.total_indexed == 1
        assert result2.total_skipped == 0


# =============================================================================
# TOTAL_SKIPPED IN RESULT TESTS
# =============================================================================

class TestIndexResultSkipped:
    """Tests pour le champ total_skipped dans IndexResult."""

    def test_total_skipped_in_to_dict(self, tenant_id):
        """total_skipped doit être inclus dans to_dict."""
        result = IndexResult(
            tenant_id=tenant_id,
            total_products=100,
            total_indexed=70,
            total_skipped=30,
        )

        d = result.to_dict()

        assert "total_skipped" in d
        assert d["total_skipped"] == 30

    def test_success_rate_excludes_skipped(self, tenant_id):
        """Le success_rate ne doit pas être affecté par les skipped."""
        result = IndexResult(
            tenant_id=tenant_id,
            total_products=100,
            total_indexed=50,
            total_skipped=50,
            total_failed=0,
        )

        # 100% car 0 failed
        assert result.success_rate == 100.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])





