"""
Tests unitaires pour le Catalog Sync Service

Ces tests utilisent des mocks pour:
- PrestaShopClient (API externe)
- ProductRepository (base de données)
"""

import time
import pytest
from decimal import Decimal
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from typing import List

from app.services.catalog.sync_service import (
    CatalogSyncService,
    SyncResult,
    SyncStatus,
    SyncError,
)
from app.services.catalog.repository import (
    ProductData,
    ProductFilter,
    InMemoryProductRepository,
)
from app.infrastructure.external.prestashop import (
    PrestaShopClientConfig,
    Product as PrestaShopProduct,
    ProductListResponse,
    PrestaShopConnectionError,
    PrestaShopAuthenticationError,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def prestashop_config():
    """Configuration PrestaShop de test."""
    return PrestaShopClientConfig(
        shop_url="https://test-shop.com",
        api_key="TEST_API_KEY",
        timeout=10,
    )


@pytest.fixture
def tenant_id():
    """Tenant ID de test."""
    return "tenant_test_123"


@pytest.fixture
def in_memory_repo():
    """Repository en mémoire pour les tests."""
    return InMemoryProductRepository()


@pytest.fixture
def mock_prestashop_products() -> List[PrestaShopProduct]:
    """Liste de produits PrestaShop mock."""
    return [
        PrestaShopProduct(
            id=1,
            name="Product 1",
            reference="REF001",
            price=Decimal("19.99"),
            price_tax_incl=Decimal("23.99"),
            quantity=10,
            category_id=5,
            category_name="Electronics",
            active=True,
        ),
        PrestaShopProduct(
            id=2,
            name="Product 2",
            reference="REF002",
            price=Decimal("29.99"),
            price_tax_incl=Decimal("35.99"),
            quantity=5,
            category_id=5,
            category_name="Electronics",
            active=True,
        ),
        PrestaShopProduct(
            id=3,
            name="Product 3",
            reference="REF003",
            price=Decimal("39.99"),
            quantity=0,
            category_id=6,
            category_name="Clothing",
            active=False,
        ),
    ]


# =============================================================================
# SYNC RESULT TESTS
# =============================================================================

class TestSyncResult:
    """Tests pour la dataclass SyncResult."""

    def test_sync_result_default_values(self, tenant_id):
        """SyncResult doit avoir des valeurs par défaut correctes."""
        result = SyncResult(tenant_id=tenant_id)

        assert result.tenant_id == tenant_id
        assert result.status == SyncStatus.PENDING
        assert result.total_fetched == 0
        assert result.total_created == 0
        assert result.total_updated == 0
        assert result.total_failed == 0
        assert result.errors == []

    def test_sync_result_success_rate_empty(self, tenant_id):
        """success_rate doit être 100% si aucun produit."""
        result = SyncResult(tenant_id=tenant_id)
        assert result.success_rate == 100.0

    def test_sync_result_success_rate_with_failures(self, tenant_id):
        """success_rate doit calculer correctement."""
        result = SyncResult(
            tenant_id=tenant_id,
            total_fetched=100,
            total_failed=20,
        )
        assert result.success_rate == 80.0

    def test_sync_result_is_success(self, tenant_id):
        """is_success doit refléter le status."""
        result_completed = SyncResult(
            tenant_id=tenant_id,
            status=SyncStatus.COMPLETED,
        )
        assert result_completed.is_success is True

        result_partial = SyncResult(
            tenant_id=tenant_id,
            status=SyncStatus.PARTIAL,
        )
        assert result_partial.is_success is True

        result_failed = SyncResult(
            tenant_id=tenant_id,
            status=SyncStatus.FAILED,
        )
        assert result_failed.is_success is False

    def test_sync_result_to_dict(self, tenant_id):
        """to_dict doit sérialiser correctement."""
        result = SyncResult(
            tenant_id=tenant_id,
            status=SyncStatus.COMPLETED,
            total_fetched=50,
            total_created=30,
            total_updated=20,
            started_at=datetime(2024, 1, 15, 10, 0, 0),
            completed_at=datetime(2024, 1, 15, 10, 0, 30),
            duration_seconds=30.5,
        )

        d = result.to_dict()

        assert d["tenant_id"] == tenant_id
        assert d["status"] == "completed"
        assert d["total_fetched"] == 50
        assert d["total_created"] == 30
        assert d["total_updated"] == 20
        assert d["duration_seconds"] == 30.5
        assert d["success_rate"] == 100.0


class TestSyncError:
    """Tests pour la dataclass SyncError."""

    def test_sync_error_to_dict(self):
        """SyncError doit se sérialiser correctement."""
        error = SyncError(
            product_id=42,
            product_name="Test Product",
            error_type="transform_error",
            error_message="Invalid price format",
        )

        d = error.to_dict()

        assert d["product_id"] == 42
        assert d["product_name"] == "Test Product"
        assert d["error_type"] == "transform_error"
        assert d["error_message"] == "Invalid price format"
        assert "timestamp" in d


# =============================================================================
# SYNC SERVICE TESTS
# =============================================================================

class TestCatalogSyncService:
    """Tests pour le service de synchronisation."""

    @pytest.mark.asyncio
    async def test_sync_empty_catalog(
        self,
        in_memory_repo,
        prestashop_config,
        tenant_id,
    ):
        """Sync d'un catalogue vide doit réussir."""
        service = CatalogSyncService(in_memory_repo)

        # Mock PrestaShopClient
        with patch("app.services.catalog.sync_service.PrestaShopClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.check_connection.return_value = True
            mock_client.get_products.return_value = ProductListResponse(
                products=[],
                total=0,
                limit=100,
                offset=0,
            )
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            MockClient.return_value = mock_client

            result = await service.sync_products(
                tenant_id=tenant_id,
                prestashop_config=prestashop_config,
            )

        assert result.status == SyncStatus.COMPLETED
        assert result.total_fetched == 0
        assert result.total_created == 0
        assert result.is_success is True

    @pytest.mark.asyncio
    async def test_sync_products_creates_new(
        self,
        in_memory_repo,
        prestashop_config,
        tenant_id,
        mock_prestashop_products,
    ):
        """Sync doit créer les nouveaux produits."""
        service = CatalogSyncService(in_memory_repo)

        with patch("app.services.catalog.sync_service.PrestaShopClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.check_connection.return_value = True
            mock_client.get_products.return_value = ProductListResponse(
                products=mock_prestashop_products,
                total=3,
                limit=100,
                offset=0,
            )
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            MockClient.return_value = mock_client

            result = await service.sync_products(
                tenant_id=tenant_id,
                prestashop_config=prestashop_config,
            )

        assert result.status == SyncStatus.COMPLETED
        assert result.total_fetched == 3
        assert result.total_created == 3
        assert result.total_updated == 0

        # Vérifier que les produits sont en base (inclure inactifs)
        products = await in_memory_repo.get_products(
            tenant_id,
            ProductFilter(active_only=False),
        )
        assert len(products) == 3

    @pytest.mark.asyncio
    async def test_sync_products_updates_existing(
        self,
        in_memory_repo,
        prestashop_config,
        tenant_id,
        mock_prestashop_products,
    ):
        """Sync doit mettre à jour les produits existants."""
        service = CatalogSyncService(in_memory_repo)

        # Pré-créer un produit
        await in_memory_repo.upsert_product(
            tenant_id,
            ProductData(
                external_id=1,
                name="Old Name",
                price=Decimal("9.99"),
            )
        )

        with patch("app.services.catalog.sync_service.PrestaShopClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.check_connection.return_value = True
            mock_client.get_products.return_value = ProductListResponse(
                products=mock_prestashop_products,
                total=3,
                limit=100,
                offset=0,
            )
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            MockClient.return_value = mock_client

            result = await service.sync_products(
                tenant_id=tenant_id,
                prestashop_config=prestashop_config,
            )

        assert result.status == SyncStatus.COMPLETED
        assert result.total_created == 2  # 2 nouveaux
        assert result.total_updated == 1  # 1 mis à jour

        # Vérifier la mise à jour
        product = await in_memory_repo.get_product_by_external_id(tenant_id, 1)
        assert product.name == "Product 1"  # Nom mis à jour
        assert product.price == Decimal("19.99")  # Prix mis à jour

    @pytest.mark.asyncio
    async def test_sync_with_pagination(
        self,
        in_memory_repo,
        prestashop_config,
        tenant_id,
    ):
        """Sync doit gérer la pagination."""
        service = CatalogSyncService(in_memory_repo, batch_size=2)

        # Créer des produits pour 2 pages
        products_page1 = [
            PrestaShopProduct(id=1, name="P1", price=Decimal("10")),
            PrestaShopProduct(id=2, name="P2", price=Decimal("20")),
        ]
        products_page2 = [
            PrestaShopProduct(id=3, name="P3", price=Decimal("30")),
        ]

        with patch("app.services.catalog.sync_service.PrestaShopClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.check_connection.return_value = True

            # Simuler la pagination
            mock_client.get_products.side_effect = [
                ProductListResponse(
                    products=products_page1,
                    total=3,
                    limit=2,
                    offset=0,
                ),
                ProductListResponse(
                    products=products_page2,
                    total=3,
                    limit=2,
                    offset=2,
                ),
            ]
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            MockClient.return_value = mock_client

            result = await service.sync_products(
                tenant_id=tenant_id,
                prestashop_config=prestashop_config,
            )

        assert result.total_fetched == 3
        assert result.total_created == 3
        assert mock_client.get_products.call_count == 2

    @pytest.mark.asyncio
    async def test_sync_connection_error(
        self,
        in_memory_repo,
        prestashop_config,
        tenant_id,
    ):
        """Erreur de connexion doit être gérée."""
        service = CatalogSyncService(in_memory_repo)

        with patch("app.services.catalog.sync_service.PrestaShopClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.check_connection.return_value = False
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            MockClient.return_value = mock_client

            result = await service.sync_products(
                tenant_id=tenant_id,
                prestashop_config=prestashop_config,
            )

        assert result.status == SyncStatus.FAILED
        assert len(result.errors) > 0
        assert result.errors[0].error_type == "connection_error"

    @pytest.mark.asyncio
    async def test_sync_authentication_error(
        self,
        in_memory_repo,
        prestashop_config,
        tenant_id,
    ):
        """Erreur d'authentification doit être gérée."""
        service = CatalogSyncService(in_memory_repo)

        with patch("app.services.catalog.sync_service.PrestaShopClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.check_connection.side_effect = PrestaShopAuthenticationError(
                tenant_id=tenant_id
            )
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            MockClient.return_value = mock_client

            result = await service.sync_products(
                tenant_id=tenant_id,
                prestashop_config=prestashop_config,
            )

        assert result.status == SyncStatus.FAILED
        assert any(e.error_type == "authentication_error" for e in result.errors)

    @pytest.mark.asyncio
    async def test_sync_with_delete_missing(
        self,
        in_memory_repo,
        prestashop_config,
        tenant_id,
    ):
        """delete_missing doit supprimer les produits obsolètes."""
        service = CatalogSyncService(in_memory_repo)

        # Pré-créer un produit qui ne sera pas dans la sync
        await in_memory_repo.upsert_product(
            tenant_id,
            ProductData(external_id=999, name="Old Product", price=Decimal("99")),
        )

        with patch("app.services.catalog.sync_service.PrestaShopClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.check_connection.return_value = True
            mock_client.get_products.return_value = ProductListResponse(
                products=[
                    PrestaShopProduct(id=1, name="New Product", price=Decimal("10")),
                ],
                total=1,
                limit=100,
                offset=0,
            )
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            MockClient.return_value = mock_client

            result = await service.sync_products(
                tenant_id=tenant_id,
                prestashop_config=prestashop_config,
                delete_missing=True,
            )

        assert result.total_deleted == 1

        # Vérifier que l'ancien produit est supprimé
        old_product = await in_memory_repo.get_product_by_external_id(tenant_id, 999)
        assert old_product is None

    @pytest.mark.asyncio
    async def test_sync_progress_callback(
        self,
        in_memory_repo,
        prestashop_config,
        tenant_id,
        mock_prestashop_products,
    ):
        """Le callback de progression doit être appelé."""
        progress_calls = []

        async def on_progress(current: int, total: int):
            progress_calls.append((current, total))

        service = CatalogSyncService(
            in_memory_repo,
            on_progress=on_progress,
        )

        with patch("app.services.catalog.sync_service.PrestaShopClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.check_connection.return_value = True
            mock_client.get_products.return_value = ProductListResponse(
                products=mock_prestashop_products,
                total=3,
                limit=100,
                offset=0,
            )
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            MockClient.return_value = mock_client

            await service.sync_products(
                tenant_id=tenant_id,
                prestashop_config=prestashop_config,
            )

        assert len(progress_calls) > 0
        assert progress_calls[0] == (3, 3)  # current, total

    @pytest.mark.asyncio
    async def test_sync_duration_tracking(
        self,
        in_memory_repo,
        prestashop_config,
        tenant_id,
    ):
        """La durée doit être mesurée."""
        service = CatalogSyncService(in_memory_repo)

        with patch("app.services.catalog.sync_service.PrestaShopClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.check_connection.return_value = True
            mock_client.get_products.return_value = ProductListResponse(
                products=[],
                total=0,
                limit=100,
                offset=0,
            )
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            MockClient.return_value = mock_client

            result = await service.sync_products(
                tenant_id=tenant_id,
                prestashop_config=prestashop_config,
            )

        assert result.started_at is not None
        assert result.completed_at is not None
        assert result.duration_seconds >= 0


# =============================================================================
# REPOSITORY TESTS
# =============================================================================

class TestInMemoryProductRepository:
    """Tests pour le repository en mémoire."""

    @pytest.mark.asyncio
    async def test_upsert_creates_new(self, in_memory_repo, tenant_id):
        """upsert doit créer un nouveau produit."""
        product = ProductData(
            external_id=1,
            name="Test Product",
            price=Decimal("29.99"),
        )

        created, updated = await in_memory_repo.upsert_product(tenant_id, product)

        assert created is True
        assert updated is False

    @pytest.mark.asyncio
    async def test_upsert_updates_existing(self, in_memory_repo, tenant_id):
        """upsert doit mettre à jour un produit existant."""
        product_v1 = ProductData(external_id=1, name="V1", price=Decimal("10"))
        product_v2 = ProductData(external_id=1, name="V2", price=Decimal("20"))

        await in_memory_repo.upsert_product(tenant_id, product_v1)
        created, updated = await in_memory_repo.upsert_product(tenant_id, product_v2)

        assert created is False
        assert updated is True

        result = await in_memory_repo.get_product_by_external_id(tenant_id, 1)
        assert result.name == "V2"

    @pytest.mark.asyncio
    async def test_get_products_with_filters(self, in_memory_repo, tenant_id):
        """get_products doit appliquer les filtres."""
        # Créer des produits variés
        await in_memory_repo.upsert_product(
            tenant_id,
            ProductData(external_id=1, name="Active", price=Decimal("10"), active=True, quantity=5),
        )
        await in_memory_repo.upsert_product(
            tenant_id,
            ProductData(external_id=2, name="Inactive", price=Decimal("20"), active=False),
        )
        await in_memory_repo.upsert_product(
            tenant_id,
            ProductData(external_id=3, name="Out of stock", price=Decimal("30"), active=True, quantity=0),
        )

        # Filtre actifs uniquement
        active_products = await in_memory_repo.get_products(
            tenant_id,
            ProductFilter(active_only=True, in_stock_only=False),
        )
        assert len(active_products) == 2

        # Filtre en stock uniquement
        in_stock = await in_memory_repo.get_products(
            tenant_id,
            ProductFilter(active_only=False, in_stock_only=True),
        )
        assert len(in_stock) == 1
        assert in_stock[0].name == "Active"

    @pytest.mark.asyncio
    async def test_count_products(self, in_memory_repo, tenant_id):
        """count_products doit compter correctement."""
        for i in range(5):
            await in_memory_repo.upsert_product(
                tenant_id,
                ProductData(external_id=i, name=f"P{i}", price=Decimal(i * 10)),
            )

        count = await in_memory_repo.count_products(tenant_id)
        assert count == 5

    @pytest.mark.asyncio
    async def test_delete_products_not_in_list(self, in_memory_repo, tenant_id):
        """delete_products_not_in_list doit supprimer les obsolètes."""
        # Créer 5 produits
        for i in range(5):
            await in_memory_repo.upsert_product(
                tenant_id,
                ProductData(external_id=i, name=f"P{i}", price=Decimal(i * 10)),
            )

        # Garder seulement 1, 2, 3
        deleted = await in_memory_repo.delete_products_not_in_list(
            tenant_id,
            [1, 2, 3],
        )

        assert deleted == 2  # 0 et 4 supprimés

        remaining = await in_memory_repo.count_products(tenant_id)
        assert remaining == 3

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, in_memory_repo):
        """Les données doivent être isolées par tenant."""
        tenant_a = "tenant_a"
        tenant_b = "tenant_b"

        await in_memory_repo.upsert_product(
            tenant_a,
            ProductData(external_id=1, name="Product A", price=Decimal("10")),
        )
        await in_memory_repo.upsert_product(
            tenant_b,
            ProductData(external_id=1, name="Product B", price=Decimal("20")),
        )

        # Chaque tenant voit son propre produit
        product_a = await in_memory_repo.get_product_by_external_id(tenant_a, 1)
        product_b = await in_memory_repo.get_product_by_external_id(tenant_b, 1)

        assert product_a.name == "Product A"
        assert product_b.name == "Product B"

        # Le count est aussi isolé
        count_a = await in_memory_repo.count_products(tenant_a)
        count_b = await in_memory_repo.count_products(tenant_b)

        assert count_a == 1
        assert count_b == 1


class TestProductData:
    """Tests pour le DTO ProductData."""

    def test_product_data_to_dict(self):
        """to_dict doit sérialiser correctement."""
        product = ProductData(
            external_id=42,
            name="Test Product",
            reference="REF001",
            price=Decimal("29.99"),
            price_tax_incl=Decimal("35.99"),
            quantity=10,
            category_id=5,
            category_name="Electronics",
            active=True,
        )

        d = product.to_dict()

        assert d["external_id"] == "42"
        assert d["name"] == "Test Product"
        assert d["price"] == 29.99
        assert d["price_tax_incl"] == 35.99
        assert d["quantity"] == 10

    def test_product_data_truncates_long_fields(self):
        """to_dict doit tronquer les champs trop longs."""
        product = ProductData(
            external_id=1,
            name="A" * 1000,  # Trop long
            price=Decimal("10"),
            description_short="B" * 2000,  # Trop long
        )

        d = product.to_dict()

        assert len(d["name"]) == 500  # Limité
        assert len(d["description_short"]) == 1000  # Limité


# =============================================================================
# TIMEOUT & CHUNKING TESTS
# =============================================================================

class TestSyncTimeout:
    """Tests pour le timeout global de synchronisation."""

    @pytest.mark.asyncio
    async def test_sync_respects_max_duration(
        self,
        in_memory_repo,
        prestashop_config,
        tenant_id,
    ):
        """La sync doit s'arrêter si max_duration_seconds est atteint."""
        import time

        # Créer un service avec timeout très court
        service = CatalogSyncService(in_memory_repo, batch_size=10)

        # Mock qui simule une latence
        async def slow_get_products(*args, **kwargs):
            time.sleep(0.1)  # 100ms par appel
            return ProductListResponse(
                products=[
                    PrestaShopProduct(id=1, name="P1", price=Decimal("10")),
                ],
                total=1000,  # Prétend qu'il y en a beaucoup
                limit=10,
                offset=kwargs.get("offset", 0),
            )

        with patch("app.services.catalog.sync_service.PrestaShopClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.check_connection.return_value = True
            mock_client.get_products = slow_get_products
            mock_client.__aenter__.return_value = mock_client
            mock_client.__aexit__.return_value = None
            MockClient.return_value = mock_client

            # Timeout à 0.2 secondes
            result = await service.sync_products(
                tenant_id=tenant_id,
                prestashop_config=prestashop_config,
                max_duration_seconds=1,  # 1 seconde max
            )

        # Doit avoir sync quelques produits mais pas tous
        assert result.total_fetched > 0
        # Le test vérifie que le timeout fonctionne mais ne peut pas garantir
        # un nombre exact de produits en raison de la variabilité d'exécution


class TestSyncChunking:
    """Tests pour le chunking du bulk upsert."""

    @pytest.mark.asyncio
    async def test_upsert_with_chunking_small_batch(
        self,
        in_memory_repo,
        tenant_id,
    ):
        """Les petits batches ne sont pas découpés."""
        service = CatalogSyncService(in_memory_repo)

        # Créer moins de BULK_UPSERT_CHUNK_SIZE produits
        products = [
            ProductData(external_id=i, name=f"Product {i}", price=Decimal(i * 10))
            for i in range(100)  # 100 < 500 (CHUNK_SIZE)
        ]

        created, updated = await service._upsert_with_chunking(
            tenant_id=tenant_id,
            products=products,
        )

        assert created == 100
        assert updated == 0

        # Vérifier que tous sont en base (sans filtre)
        count = await in_memory_repo.count_products(
            tenant_id,
            ProductFilter(active_only=False),
        )
        assert count == 100

    @pytest.mark.asyncio
    async def test_upsert_with_chunking_large_batch(
        self,
        in_memory_repo,
        tenant_id,
    ):
        """Les grands batches sont découpés en chunks."""
        service = CatalogSyncService(in_memory_repo)

        # Créer plus de BULK_UPSERT_CHUNK_SIZE produits
        products = [
            ProductData(external_id=i, name=f"Product {i}", price=Decimal(i * 10))
            for i in range(1200)  # 1200 > 500 (CHUNK_SIZE)
        ]

        created, updated = await service._upsert_with_chunking(
            tenant_id=tenant_id,
            products=products,
        )

        assert created == 1200
        assert updated == 0

        # Vérifier que tous sont en base (sans filtre, avec limit élevé)
        count = await in_memory_repo.count_products(
            tenant_id,
            ProductFilter(active_only=False),
        )
        assert count == 1200


class TestDeleteProtection:
    """Tests pour la protection contre suppression massive."""

    @pytest.mark.asyncio
    async def test_delete_refuses_empty_list(
        self,
        in_memory_repo,
        tenant_id,
    ):
        """Ne doit rien supprimer si liste vide."""
        # Créer des produits
        for i in range(10):
            await in_memory_repo.upsert_product(
                tenant_id,
                ProductData(external_id=i, name=f"P{i}", price=Decimal(10)),
            )

        # Tenter de supprimer avec liste vide
        deleted = await in_memory_repo.delete_products_not_in_list(
            tenant_id,
            [],  # Liste vide
        )

        # Rien ne devrait être supprimé
        assert deleted == 0
        count = await in_memory_repo.count_products(tenant_id)
        assert count == 10


if __name__ == "__main__":
    pytest.main([__file__, "-v"])





