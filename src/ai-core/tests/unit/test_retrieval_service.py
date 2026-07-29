"""
Tests unitaires pour le Product Retrieval Service

Ces tests utilisent des mocks pour:
- EmbeddingService
- VectorStore
"""

import pytest
from decimal import Decimal
from typing import List, Dict, Any

from app.services.rag.retrieval_service import (
    ProductRetrievalService,
    RetrievalResult,
    RetrievedProduct,
    InMemorySearchableVectorStore,
)
from app.services.rag.embedding_service import MockEmbeddingService


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
    """Vector store en mémoire avec support de recherche."""
    return InMemorySearchableVectorStore()


@pytest.fixture
def retrieval_service(mock_embedding_service, in_memory_vector_store):
    """Service de retrieval configuré."""
    return ProductRetrievalService(
        embedding_service=mock_embedding_service,
        vector_store=in_memory_vector_store,
        default_top_k=5,
        min_similarity=0.0,  # Pas de seuil pour les tests
    )


@pytest.fixture
def indexed_products(mock_embedding_service, in_memory_vector_store, tenant_id):
    """Indexe des produits de test dans le vector store (sync wrapper)."""
    import asyncio

    async def _index():
        collection_name = f"tenant_{tenant_id}_products"

        products = [
            {
                "id": f"{tenant_id}_1",
                "document": "iPhone 15 Pro smartphone Apple téléphone haut de gamme",
                "metadata": {
                    "product_id": 1,
                    "external_id": 1,
                    "name": "iPhone 15 Pro",
                    "price": 1199.0,
                    "category_name": "Smartphones",
                    "reference": "IPH15PRO",
                    "active": True,
                    "in_stock": True,
                },
            },
            {
                "id": f"{tenant_id}_2",
                "document": "Samsung Galaxy S24 smartphone Android téléphone",
                "metadata": {
                    "product_id": 2,
                    "external_id": 2,
                    "name": "Samsung Galaxy S24",
                    "price": 899.0,
                    "category_name": "Smartphones",
                    "reference": "SAMS24",
                    "active": True,
                    "in_stock": True,
                },
            },
            {
                "id": f"{tenant_id}_3",
                "document": "MacBook Pro ordinateur portable Apple laptop",
                "metadata": {
                    "product_id": 3,
                    "external_id": 3,
                    "name": "MacBook Pro 14",
                    "price": 2499.0,
                    "category_name": "Ordinateurs",
                    "reference": "MBP14",
                    "active": True,
                    "in_stock": False,
                },
            },
            {
                "id": f"{tenant_id}_4",
                "document": "AirPods Pro écouteurs sans fil Apple audio",
                "metadata": {
                    "product_id": 4,
                    "external_id": 4,
                    "name": "AirPods Pro",
                    "price": 279.0,
                    "category_name": "Audio",
                    "reference": "AIRPRO",
                    "active": False,  # Inactif
                    "in_stock": True,
                },
            },
        ]

        # Générer les embeddings et indexer
        ids = []
        embeddings = []
        documents = []
        metadatas = []

        for product in products:
            ids.append(product["id"])
            documents.append(product["document"])
            metadatas.append(product["metadata"])

            embedding = await mock_embedding_service.generate_embedding(product["document"])
            embeddings.append(embedding)

        await in_memory_vector_store.upsert(
            collection_name=collection_name,
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

        return products

    # Exécuter l'async - compatible Python 3.14
    try:
        loop = asyncio.get_running_loop()
        # Si on est dans une boucle async, utiliser create_task
        import nest_asyncio
        nest_asyncio.apply()
        return loop.run_until_complete(_index())
    except RuntimeError:
        # Pas de boucle en cours, utiliser asyncio.run
        return asyncio.run(_index())


# =============================================================================
# RETRIEVED PRODUCT TESTS
# =============================================================================

class TestRetrievedProduct:
    """Tests pour la dataclass RetrievedProduct."""

    def test_to_context_string_basic(self):
        """to_context_string doit formater correctement."""
        product = RetrievedProduct(
            product_id=1,
            name="iPhone 15 Pro",
            price=1199.0,
            category="Smartphones",
            in_stock=True,
        )

        context = product.to_context_string()

        assert "iPhone 15 Pro" in context
        assert "1199.00€" in context
        assert "Smartphones" in context
        assert "En stock" in context

    def test_to_context_string_out_of_stock(self):
        """Le statut rupture doit être affiché."""
        product = RetrievedProduct(
            product_id=1,
            name="Test Product",
            price=99.0,
            in_stock=False,
        )

        context = product.to_context_string()

        assert "Rupture de stock" in context

    def test_to_context_string_with_description(self):
        """La description doit être incluse et tronquée."""
        long_description = "A" * 300
        product = RetrievedProduct(
            product_id=1,
            name="Test",
            price=10.0,
            description=long_description,
        )

        context = product.to_context_string()

        assert "..." in context  # Tronqué
        assert len(context) < 400  # Pas trop long


# =============================================================================
# RETRIEVAL RESULT TESTS
# =============================================================================

class TestRetrievalResult:
    """Tests pour la dataclass RetrievalResult."""

    def test_has_results_empty(self):
        """has_results doit être False si pas de produits."""
        result = RetrievalResult(query="test", tenant_id="t1")

        assert result.has_results is False

    def test_has_results_with_products(self):
        """has_results doit être True avec des produits."""
        result = RetrievalResult(
            query="test",
            tenant_id="t1",
            products=[RetrievedProduct(product_id=1, name="P1", price=10.0)],
        )

        assert result.has_results is True

    def test_to_context_string_empty(self):
        """to_context_string doit retourner chaîne vide si pas de produits."""
        result = RetrievalResult(query="test", tenant_id="t1")

        assert result.to_context_string() == ""

    def test_to_context_string_with_products(self):
        """to_context_string doit formater les produits."""
        result = RetrievalResult(
            query="test",
            tenant_id="t1",
            products=[
                RetrievedProduct(product_id=1, name="Product 1", price=10.0),
                RetrievedProduct(product_id=2, name="Product 2", price=20.0),
            ],
        )

        context = result.to_context_string()

        assert "Produits pertinents" in context
        assert "Product 1" in context
        assert "Product 2" in context

    def test_to_dict(self):
        """to_dict doit sérialiser correctement."""
        result = RetrievalResult(
            query="test",
            tenant_id="t1",
            products=[RetrievedProduct(product_id=1, name="P1", price=10.0, similarity_score=0.95)],
            total_found=1,
            search_time_ms=50.5,
        )

        d = result.to_dict()

        assert d["query"] == "test"
        assert d["tenant_id"] == "t1"
        assert d["total_found"] == 1
        assert d["search_time_ms"] == 50.5
        assert len(d["products"]) == 1
        assert d["products"][0]["similarity"] == 0.95


# =============================================================================
# PRODUCT RETRIEVAL SERVICE TESTS
# =============================================================================

class TestProductRetrievalService:
    """Tests pour le service de retrieval."""

    @pytest.mark.asyncio
    async def test_search_empty_collection(
        self,
        retrieval_service,
        tenant_id,
    ):
        """Recherche dans collection vide doit retourner résultat vide."""
        result = await retrieval_service.search_products(
            query="téléphone",
            tenant_id=tenant_id,
        )

        assert result.has_results is False
        assert result.total_found == 0
        assert result.search_time_ms >= 0

    @pytest.mark.asyncio
    async def test_search_finds_relevant_products(
        self,
        retrieval_service,
        tenant_id,
        indexed_products,
    ):
        """Recherche doit trouver des produits pertinents."""
        # Utiliser exactement le même texte qu'un produit indexé
        # pour garantir un match avec le mock embedding deterministe
        result = await retrieval_service.search_products(
            query="iPhone 15 Pro smartphone Apple téléphone haut de gamme",
            tenant_id=tenant_id,
        )

        assert result.has_results is True
        assert result.total_found > 0

        # Vérifier que l'iPhone est trouvé
        product_names = [p.name for p in result.products]
        assert "iPhone 15 Pro" in product_names

    @pytest.mark.asyncio
    async def test_search_respects_top_k(
        self,
        retrieval_service,
        tenant_id,
        indexed_products,
    ):
        """Recherche doit respecter le paramètre top_k."""
        result = await retrieval_service.search_products(
            query="produit",
            tenant_id=tenant_id,
            top_k=2,
        )

        assert len(result.products) <= 2

    @pytest.mark.asyncio
    async def test_search_filters_active_only(
        self,
        retrieval_service,
        tenant_id,
        indexed_products,
    ):
        """Recherche avec filter_active_only doit exclure les inactifs."""
        result = await retrieval_service.search_products(
            query="AirPods écouteurs",
            tenant_id=tenant_id,
            filter_active_only=True,
        )

        # AirPods est inactif, ne devrait pas apparaître
        product_names = [p.name for p in result.products]
        assert "AirPods Pro" not in product_names

    @pytest.mark.asyncio
    async def test_search_filters_in_stock_only(
        self,
        retrieval_service,
        tenant_id,
        indexed_products,
    ):
        """Recherche avec filter_in_stock_only doit exclure ruptures."""
        result = await retrieval_service.search_products(
            query="MacBook ordinateur",
            tenant_id=tenant_id,
            filter_in_stock_only=True,
        )

        # MacBook est en rupture, ne devrait pas apparaître
        product_names = [p.name for p in result.products]
        assert "MacBook Pro 14" not in product_names

    @pytest.mark.asyncio
    async def test_get_products_context(
        self,
        retrieval_service,
        tenant_id,
        indexed_products,
    ):
        """get_products_context doit retourner contexte formaté."""
        context = await retrieval_service.get_products_context(
            query="smartphone",
            tenant_id=tenant_id,
            top_k=3,
        )

        assert isinstance(context, str)
        if context:  # Si des produits trouvés
            assert "Produits pertinents" in context

    @pytest.mark.asyncio
    async def test_has_indexed_products_true(
        self,
        retrieval_service,
        tenant_id,
        indexed_products,
    ):
        """has_indexed_products doit retourner True si produits indexés."""
        has_products = await retrieval_service.has_indexed_products(tenant_id)

        assert has_products is True

    @pytest.mark.asyncio
    async def test_has_indexed_products_false(
        self,
        retrieval_service,
    ):
        """has_indexed_products doit retourner False si pas de produits."""
        has_products = await retrieval_service.has_indexed_products("unknown_tenant")

        assert has_products is False


# =============================================================================
# MULTI-TENANT ISOLATION TESTS
# =============================================================================

class TestMultiTenantIsolation:
    """Tests pour l'isolation multi-tenant."""

    @pytest.mark.asyncio
    async def test_tenant_isolation(
        self,
        mock_embedding_service,
        in_memory_vector_store,
    ):
        """Les produits d'un tenant ne doivent pas être visibles pour un autre."""
        tenant_a = "tenant_a"
        tenant_b = "tenant_b"

        retrieval_service = ProductRetrievalService(
            embedding_service=mock_embedding_service,
            vector_store=in_memory_vector_store,
            min_similarity=0.0,
        )

        # Indexer un produit pour tenant_a
        collection_a = f"tenant_{tenant_a}_products"
        embedding_a = await mock_embedding_service.generate_embedding("Produit A unique")

        await in_memory_vector_store.upsert(
            collection_name=collection_a,
            ids=["1"],
            embeddings=[embedding_a],
            documents=["Produit A unique"],
            metadatas=[{
                "product_id": 1,
                "name": "Produit A",
                "price": 100.0,
                "active": True,
                "in_stock": True,
            }],
        )

        # Indexer un produit pour tenant_b
        collection_b = f"tenant_{tenant_b}_products"
        embedding_b = await mock_embedding_service.generate_embedding("Produit B différent")

        await in_memory_vector_store.upsert(
            collection_name=collection_b,
            ids=["1"],
            embeddings=[embedding_b],
            documents=["Produit B différent"],
            metadatas=[{
                "product_id": 1,
                "name": "Produit B",
                "price": 200.0,
                "active": True,
                "in_stock": True,
            }],
        )

        # Rechercher pour tenant_a
        result_a = await retrieval_service.search_products(
            query="Produit unique",
            tenant_id=tenant_a,
        )

        # Rechercher pour tenant_b
        result_b = await retrieval_service.search_products(
            query="Produit différent",
            tenant_id=tenant_b,
        )

        # Vérifier l'isolation
        if result_a.has_results:
            assert all(p.name == "Produit A" for p in result_a.products)

        if result_b.has_results:
            assert all(p.name == "Produit B" for p in result_b.products)


# =============================================================================
# IN-MEMORY VECTOR STORE TESTS
# =============================================================================

class TestInMemorySearchableVectorStore:
    """Tests pour le vector store en mémoire."""

    @pytest.mark.asyncio
    async def test_search_empty_collection(self, in_memory_vector_store):
        """Recherche dans collection vide doit retourner liste vide."""
        results = await in_memory_vector_store.search(
            collection_name="empty",
            query_embedding=[0.1] * 10,
            top_k=5,
        )

        assert results == []

    @pytest.mark.asyncio
    async def test_search_returns_top_k(self, in_memory_vector_store, mock_embedding_service):
        """Search doit retourner au max top_k résultats."""
        # Indexer 10 documents
        embeddings = []
        for i in range(10):
            emb = await mock_embedding_service.generate_embedding(f"Document {i}")
            embeddings.append(emb)

        await in_memory_vector_store.upsert(
            collection_name="test",
            ids=[str(i) for i in range(10)],
            embeddings=embeddings,
            documents=[f"Document {i}" for i in range(10)],
            metadatas=[{"id": i} for i in range(10)],
        )

        query_emb = await mock_embedding_service.generate_embedding("Document")

        results = await in_memory_vector_store.search(
            collection_name="test",
            query_embedding=query_emb,
            top_k=3,
        )

        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_search_with_metadata_filter(self, in_memory_vector_store, mock_embedding_service):
        """Search doit respecter les filtres de metadata."""
        emb1 = await mock_embedding_service.generate_embedding("Product Active")
        emb2 = await mock_embedding_service.generate_embedding("Product Inactive")

        await in_memory_vector_store.upsert(
            collection_name="test",
            ids=["1", "2"],
            embeddings=[emb1, emb2],
            documents=["Product Active", "Product Inactive"],
            metadatas=[
                {"active": True},
                {"active": False},
            ],
        )

        query_emb = await mock_embedding_service.generate_embedding("Product")

        # Rechercher uniquement les actifs
        results = await in_memory_vector_store.search(
            collection_name="test",
            query_embedding=query_emb,
            top_k=10,
            filter_metadata={"active": True},
        )

        assert len(results) == 1
        assert results[0]["metadata"]["active"] is True


# =============================================================================
# FALLBACK TESTS
# =============================================================================

class TestFallbackBehavior:
    """Tests pour le comportement de fallback."""

    @pytest.mark.asyncio
    async def test_graceful_fallback_on_empty_results(
        self,
        retrieval_service,
        tenant_id,
    ):
        """Service doit retourner résultat vide gracieusement."""
        result = await retrieval_service.search_products(
            query="produit inexistant xyz123",
            tenant_id=tenant_id,
        )

        assert result is not None
        assert isinstance(result, RetrievalResult)
        assert result.to_context_string() == ""


# =============================================================================
# CHROMA VECTOR STORE (real chromadb, skip si non importable)
# =============================================================================

try:
    import chromadb  # noqa: F401

    _CHROMADB_IMPORTABLE = True
except Exception:  # pragma: no cover - ex: pydantic.v1 incompatible avec Python 3.14+
    _CHROMADB_IMPORTABLE = False

pytestmark_chromadb = pytest.mark.skipif(
    not _CHROMADB_IMPORTABLE,
    reason="chromadb non importable dans cet environnement (voir Python 3.14 vs 3.11 dans le runbook)",
)


@pytestmark_chromadb
class TestChromaSearchableVectorStoreUpsert:
    """
    Régression: ChromaSearchableVectorStore n'exposait ni upsert() ni
    get_ids(), alors que ProductIndexer en a besoin pour indexer via le
    vector store renvoyé par app.services.rag.factory.get_vector_store().
    Sans cette méthode, toute tentative d'indexation contre un vrai ChromaDB
    échouait silencieusement (AttributeError capturée et journalisée comme
    une simple "Failed to store batch").

    Utilise un vrai chromadb.PersistentClient (pas de mock) pour prouver que
    l'intégration fonctionne réellement, pas seulement l'appel de méthode.
    """

    @pytest.fixture
    def store(self, tmp_path):
        from app.services.rag.retrieval_service import ChromaSearchableVectorStore

        return ChromaSearchableVectorStore(persist_directory=str(tmp_path / "chroma"))

    @pytest.mark.asyncio
    async def test_upsert_then_search_finds_document(self, store):
        embedding = [0.1] * 384
        await store.upsert(
            collection_name="test_collection",
            ids=["prod_1"],
            embeddings=[embedding],
            documents=["Chaussure de trail"],
            metadatas=[{"name": "Chaussure de trail"}],
        )

        results = await store.search(
            collection_name="test_collection",
            query_embedding=embedding,
            top_k=3,
        )

        assert len(results) == 1
        assert results[0]["id"] == "prod_1"
        assert results[0]["metadata"]["name"] == "Chaussure de trail"

    @pytest.mark.asyncio
    async def test_upsert_then_get_ids(self, store):
        await store.upsert(
            collection_name="test_collection",
            ids=["prod_1", "prod_2"],
            embeddings=[[0.1] * 384, [0.2] * 384],
            documents=["doc1", "doc2"],
            metadatas=[{"name": "doc1"}, {"name": "doc2"}],
        )

        ids = await store.get_ids("test_collection")

        assert set(ids) == {"prod_1", "prod_2"}

    @pytest.mark.asyncio
    async def test_data_persists_across_new_client_instance(self, tmp_path):
        """Preuve de persistance réelle: un second client, même chemin, doit
        retrouver les données écrites par le premier."""
        from app.services.rag.retrieval_service import ChromaSearchableVectorStore

        persist_dir = str(tmp_path / "chroma")
        store1 = ChromaSearchableVectorStore(persist_directory=persist_dir)
        await store1.upsert(
            collection_name="test_collection",
            ids=["prod_1"],
            embeddings=[[0.1] * 384],
            documents=["doc1"],
            metadatas=[{"name": "doc1"}],
        )

        store2 = ChromaSearchableVectorStore(persist_directory=persist_dir)
        count = await store2.count("test_collection")

        assert count == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])




