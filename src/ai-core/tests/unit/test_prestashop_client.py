"""
Tests unitaires pour le client PrestaShop

Ces tests utilisent des mocks pour simuler l'API PrestaShop
et valider le comportement du client sans dépendance externe.
"""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

import httpx

from app.infrastructure.external.prestashop.client import (
    PrestaShopClient,
    PrestaShopClientConfig,
)
from app.infrastructure.external.prestashop.models import (
    Product,
    Category,
    ProductListResponse,
    CategoryListResponse,
    ProductStatus,
)
from app.infrastructure.external.prestashop.exceptions import (
    PrestaShopError,
    PrestaShopConnectionError,
    PrestaShopAuthenticationError,
    PrestaShopNotFoundError,
    PrestaShopRateLimitError,
    PrestaShopValidationError,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def client_config():
    """Configuration de test pour le client PrestaShop."""
    return PrestaShopClientConfig(
        shop_url="https://test-shop.prestashop.com",
        api_key="TEST_API_KEY_12345",
        timeout=10,
        language_id=1,
    )


@pytest.fixture
def tenant_id():
    """Tenant ID de test."""
    return "tenant_test_123"


@pytest.fixture
def mock_product_data():
    """Données brutes d'un produit PrestaShop."""
    return {
        "id": "42",
        "reference": "SKU-001",
        "ean13": "1234567890123",
        "name": {"language": {"@id": "1", "#text": "T-shirt Premium"}},
        "description": {"language": {"@id": "1", "#text": "<p>Description longue</p>"}},
        "description_short": {"language": {"@id": "1", "#text": "Description courte"}},
        "price": "29.99",
        "quantity": "100",
        "minimal_quantity": "1",
        "id_category_default": "5",
        "active": "1",
        "available_for_order": "1",
        "visibility": "both",
        "condition": "new",
        "weight": "0.5",
        "date_add": "2024-01-15 10:30:00",
        "date_upd": "2024-02-01 14:00:00",
        "manufacturer_name": "BrandX",
        "associations": {
            "images": [
                {"id": "101"},
                {"id": "102"},
            ],
        },
    }


@pytest.fixture
def mock_category_data():
    """Données brutes d'une catégorie PrestaShop."""
    return {
        "id": "5",
        "name": {"language": {"@id": "1", "#text": "Vêtements"}},
        "description": {"language": {"@id": "1", "#text": "Catégorie vêtements"}},
        "id_parent": "2",
        "level_depth": "2",
        "active": "1",
        "position": "1",
        "date_add": "2024-01-01 00:00:00",
    }


# =============================================================================
# CONFIG TESTS
# =============================================================================

class TestPrestaShopClientConfig:
    """Tests pour la configuration du client."""

    def test_config_creation(self, client_config):
        """La configuration doit être créée correctement."""
        assert client_config.shop_url == "https://test-shop.prestashop.com"
        assert client_config.api_key == "TEST_API_KEY_12345"
        assert client_config.timeout == 10
        assert client_config.language_id == 1

    def test_config_normalizes_url(self):
        """L'URL doit être normalisée (sans trailing slash)."""
        config = PrestaShopClientConfig(
            shop_url="https://shop.com/",
            api_key="KEY",
        )
        assert config.shop_url == "https://shop.com"

    def test_config_api_url(self, client_config):
        """api_url doit ajouter /api."""
        assert client_config.api_url == "https://test-shop.prestashop.com/api"

    def test_config_auth_header(self, client_config):
        """auth_header doit être un Basic auth valide."""
        import base64
        expected = base64.b64encode(b"TEST_API_KEY_12345:").decode()
        assert client_config.auth_header == f"Basic {expected}"


# =============================================================================
# CLIENT TESTS
# =============================================================================

class TestPrestaShopClient:
    """Tests pour le client PrestaShop."""

    @pytest.mark.asyncio
    async def test_client_context_manager(self, client_config, tenant_id):
        """Le client doit fonctionner comme context manager."""
        async with PrestaShopClient(client_config, tenant_id) as client:
            assert client._client is not None

        # Après sortie, le client doit être fermé
        assert client._client is None

    @pytest.mark.asyncio
    async def test_client_without_context_raises_error(self, client_config, tenant_id):
        """Utiliser le client sans context manager doit lever une erreur."""
        client = PrestaShopClient(client_config, tenant_id)

        with pytest.raises(PrestaShopError) as exc_info:
            await client.get_products()

        assert "not initialized" in str(exc_info.value).lower()


# =============================================================================
# PRODUCTS TESTS
# =============================================================================

class TestGetProducts:
    """Tests pour la récupération des produits."""

    @pytest.mark.asyncio
    async def test_get_products_success(self, client_config, tenant_id, mock_product_data):
        """get_products doit retourner une liste de produits."""
        async with PrestaShopClient(client_config, tenant_id) as client:
            # Mock la requête HTTP
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"products": [mock_product_data]}

            with patch.object(client._client, "request", new_callable=AsyncMock) as mock_request:
                mock_request.return_value = mock_response

                response = await client.get_products(limit=10)

        assert isinstance(response, ProductListResponse)
        assert len(response.products) == 1
        assert response.products[0].id == 42
        assert response.products[0].name == "T-shirt Premium"

    @pytest.mark.asyncio
    async def test_get_products_empty(self, client_config, tenant_id):
        """get_products doit gérer une liste vide."""
        async with PrestaShopClient(client_config, tenant_id) as client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"products": []}

            with patch.object(client._client, "request", new_callable=AsyncMock) as mock_request:
                mock_request.return_value = mock_response

                response = await client.get_products()

        assert response.products == []
        assert response.total == 0

    @pytest.mark.asyncio
    async def test_get_products_pagination(self, client_config, tenant_id, mock_product_data):
        """get_products doit supporter la pagination."""
        async with PrestaShopClient(client_config, tenant_id) as client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"products": [mock_product_data]}

            with patch.object(client._client, "request", new_callable=AsyncMock) as mock_request:
                mock_request.return_value = mock_response

                response = await client.get_products(limit=50, offset=100)

        assert response.limit == 50
        assert response.offset == 100

    @pytest.mark.asyncio
    async def test_get_products_limit_validation(self, client_config, tenant_id, mock_product_data):
        """get_products doit valider les limites min/max."""
        async with PrestaShopClient(client_config, tenant_id) as client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"products": []}

            with patch.object(client._client, "request", new_callable=AsyncMock) as mock_request:
                mock_request.return_value = mock_response

                # Limite trop haute devrait être réduite à 500
                response = await client.get_products(limit=1000)
                assert response.limit == 500

                # Limite négative devrait être 1
                response = await client.get_products(limit=-10)
                assert response.limit == 1


class TestGetProduct:
    """Tests pour la récupération d'un produit unique."""

    @pytest.mark.asyncio
    async def test_get_product_success(self, client_config, tenant_id, mock_product_data):
        """get_product doit retourner un produit."""
        async with PrestaShopClient(client_config, tenant_id) as client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"product": mock_product_data}

            with patch.object(client._client, "request", new_callable=AsyncMock) as mock_request:
                mock_request.return_value = mock_response

                product = await client.get_product(42)

        assert isinstance(product, Product)
        assert product.id == 42
        assert product.name == "T-shirt Premium"
        assert product.price == Decimal("29.99")
        assert product.quantity == 100
        assert product.manufacturer_name == "BrandX"

    @pytest.mark.asyncio
    async def test_get_product_not_found(self, client_config, tenant_id):
        """get_product doit lever PrestaShopNotFoundError si le produit n'existe pas."""
        async with PrestaShopClient(client_config, tenant_id) as client:
            mock_response = MagicMock()
            mock_response.status_code = 404

            with patch.object(client._client, "request", new_callable=AsyncMock) as mock_request:
                mock_request.return_value = mock_response

                with pytest.raises(PrestaShopNotFoundError) as exc_info:
                    await client.get_product(99999)

        assert exc_info.value.resource_type == "Product"
        assert exc_info.value.resource_id == 99999


# =============================================================================
# CATEGORIES TESTS
# =============================================================================

class TestGetCategories:
    """Tests pour la récupération des catégories."""

    @pytest.mark.asyncio
    async def test_get_categories_success(self, client_config, tenant_id, mock_category_data):
        """get_categories doit retourner une liste de catégories."""
        async with PrestaShopClient(client_config, tenant_id) as client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"categories": [mock_category_data]}

            with patch.object(client._client, "request", new_callable=AsyncMock) as mock_request:
                mock_request.return_value = mock_response

                response = await client.get_categories()

        assert isinstance(response, CategoryListResponse)
        assert len(response.categories) == 1
        assert response.categories[0].id == 5
        assert response.categories[0].name == "Vêtements"

    @pytest.mark.asyncio
    async def test_get_categories_excludes_root(self, client_config, tenant_id):
        """get_categories doit exclure la catégorie racine par défaut."""
        root_category = {
            "id": "1",
            "name": "Root",
            "level_depth": "0",
            "active": "1",
        }
        normal_category = {
            "id": "5",
            "name": {"language": {"@id": "1", "#text": "Clothes"}},
            "level_depth": "2",
            "active": "1",
        }

        async with PrestaShopClient(client_config, tenant_id) as client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"categories": [root_category, normal_category]}

            with patch.object(client._client, "request", new_callable=AsyncMock) as mock_request:
                mock_request.return_value = mock_response

                response = await client.get_categories(exclude_root=True)

        # Root devrait être exclu
        assert len(response.categories) == 1
        assert response.categories[0].name == "Clothes"


class TestGetCategory:
    """Tests pour la récupération d'une catégorie unique."""

    @pytest.mark.asyncio
    async def test_get_category_success(self, client_config, tenant_id, mock_category_data):
        """get_category doit retourner une catégorie."""
        async with PrestaShopClient(client_config, tenant_id) as client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"category": mock_category_data}

            with patch.object(client._client, "request", new_callable=AsyncMock) as mock_request:
                mock_request.return_value = mock_response

                category = await client.get_category(5)

        assert isinstance(category, Category)
        assert category.id == 5
        assert category.name == "Vêtements"
        assert category.parent_id == 2


# =============================================================================
# ERROR HANDLING TESTS
# =============================================================================

class TestErrorHandling:
    """Tests pour la gestion des erreurs."""

    @pytest.mark.asyncio
    async def test_authentication_error(self, client_config, tenant_id):
        """401 doit lever PrestaShopAuthenticationError."""
        async with PrestaShopClient(client_config, tenant_id) as client:
            mock_response = MagicMock()
            mock_response.status_code = 401

            with patch.object(client._client, "request", new_callable=AsyncMock) as mock_request:
                mock_request.return_value = mock_response

                with pytest.raises(PrestaShopAuthenticationError):
                    await client.get_products()

    @pytest.mark.asyncio
    async def test_rate_limit_error(self, client_config, tenant_id):
        """429 doit lever PrestaShopRateLimitError."""
        async with PrestaShopClient(client_config, tenant_id) as client:
            mock_response = MagicMock()
            mock_response.status_code = 429
            mock_response.headers = {"Retry-After": "60"}

            with patch.object(client._client, "request", new_callable=AsyncMock) as mock_request:
                mock_request.return_value = mock_response

                with pytest.raises(PrestaShopRateLimitError) as exc_info:
                    await client.get_products()

        assert exc_info.value.retry_after == 60

    @pytest.mark.asyncio
    async def test_connection_timeout(self, client_config, tenant_id):
        """Timeout doit lever PrestaShopConnectionError."""
        async with PrestaShopClient(client_config, tenant_id) as client:
            with patch.object(client._client, "request", new_callable=AsyncMock) as mock_request:
                mock_request.side_effect = httpx.TimeoutException("Request timed out")

                with pytest.raises(PrestaShopConnectionError) as exc_info:
                    await client.get_products()

        assert "timeout" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_connection_error(self, client_config, tenant_id):
        """Erreur de connexion doit lever PrestaShopConnectionError."""
        async with PrestaShopClient(client_config, tenant_id) as client:
            with patch.object(client._client, "request", new_callable=AsyncMock) as mock_request:
                mock_request.side_effect = httpx.ConnectError("Connection refused")

                with pytest.raises(PrestaShopConnectionError) as exc_info:
                    await client.get_products()

        assert "connect" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_server_error(self, client_config, tenant_id):
        """5xx doit lever PrestaShopServerError."""
        async with PrestaShopClient(client_config, tenant_id) as client:
            mock_response = MagicMock()
            mock_response.status_code = 503

            with patch.object(client._client, "request", new_callable=AsyncMock) as mock_request:
                mock_request.return_value = mock_response

                with pytest.raises(Exception) as exc_info:
                    await client.get_products()

        assert exc_info.value.status_code == 503


# =============================================================================
# MODEL TESTS
# =============================================================================

class TestProductModel:
    """Tests pour le modèle Product."""

    def test_product_is_in_stock(self):
        """is_in_stock doit retourner True si quantity > 0."""
        product = Product(
            id=1,
            name="Test",
            price=Decimal("10"),
            quantity=5,
        )
        assert product.is_in_stock is True

        product_no_stock = Product(
            id=2,
            name="Test",
            price=Decimal("10"),
            quantity=0,
        )
        assert product_no_stock.is_in_stock is False

    def test_product_is_on_sale(self):
        """is_on_sale doit détecter les promotions."""
        product_regular = Product(
            id=1,
            name="Test",
            price=Decimal("10"),
        )
        assert product_regular.is_on_sale is False

        product_percent = Product(
            id=2,
            name="Test",
            price=Decimal("10"),
            reduction_percent=Decimal("20"),
        )
        assert product_percent.is_on_sale is True

        product_amount = Product(
            id=3,
            name="Test",
            price=Decimal("10"),
            reduction_amount=Decimal("5"),
        )
        assert product_amount.is_on_sale is True

    def test_product_status(self):
        """status doit refléter l'état du produit."""
        # Actif et en stock
        product_active = Product(id=1, name="Test", price=Decimal("10"), quantity=5, active=True)
        assert product_active.status == ProductStatus.ACTIVE

        # Inactif
        product_inactive = Product(id=2, name="Test", price=Decimal("10"), active=False)
        assert product_inactive.status == ProductStatus.INACTIVE

        # En rupture
        product_oos = Product(id=3, name="Test", price=Decimal("10"), quantity=0, active=True)
        assert product_oos.status == ProductStatus.OUT_OF_STOCK

    def test_product_to_search_text(self):
        """to_search_text doit générer un texte pour l'indexation."""
        product = Product(
            id=1,
            name="T-shirt Premium",
            price=Decimal("29.99"),
            description_short="Un super t-shirt",
            category_name="Vêtements",
            manufacturer_name="BrandX",
            reference="SKU001",
            tags=["coton", "été"],
            features={"Couleur": "Bleu", "Taille": "M"},
        )

        text = product.to_search_text()

        assert "T-shirt Premium" in text
        assert "super t-shirt" in text
        assert "Vêtements" in text
        assert "BrandX" in text
        assert "SKU001" in text
        assert "coton" in text
        assert "Couleur: Bleu" in text

    def test_product_to_embedding_metadata(self):
        """to_embedding_metadata doit retourner les métadonnées pour ChromaDB."""
        product = Product(
            id=42,
            name="Test Product",
            price=Decimal("19.99"),
            price_tax_incl=Decimal("23.99"),
            quantity=10,
            category_id=5,
            category_name="Electronics",
            manufacturer_name="TestBrand",
            reference="REF001",
            active=True,
        )

        metadata = product.to_embedding_metadata()

        assert metadata["product_id"] == 42
        assert metadata["name"] == "Test Product"
        assert metadata["price"] == 19.99
        assert metadata["price_tax_incl"] == 23.99
        assert metadata["quantity"] == 10
        assert metadata["in_stock"] is True
        assert metadata["category_id"] == 5
        assert metadata["category_name"] == "Electronics"


class TestProductListResponse:
    """Tests pour ProductListResponse."""

    def test_has_more(self):
        """has_more doit indiquer s'il y a plus de résultats."""
        # Pas encore tous les résultats
        response = ProductListResponse(
            products=[Product(id=1, name="P1", price=Decimal("10"))],
            total=10,
            limit=5,
            offset=0,
        )
        assert response.has_more is True

        # Tous les résultats récupérés
        response_complete = ProductListResponse(
            products=[Product(id=1, name="P1", price=Decimal("10"))],
            total=1,
            limit=5,
            offset=0,
        )
        assert response_complete.has_more is False

    def test_next_offset(self):
        """next_offset doit calculer l'offset suivant."""
        response = ProductListResponse(
            products=[
                Product(id=1, name="P1", price=Decimal("10")),
                Product(id=2, name="P2", price=Decimal("20")),
            ],
            total=10,
            limit=2,
            offset=0,
        )
        assert response.next_offset == 2


class TestCategoryListResponse:
    """Tests pour CategoryListResponse."""

    def test_get_tree(self):
        """get_tree doit organiser les catégories par parent."""
        categories = [
            Category(id=1, name="Root", parent_id=None, level_depth=0),
            Category(id=2, name="Electronics", parent_id=1, level_depth=1),
            Category(id=3, name="Clothing", parent_id=1, level_depth=1),
            Category(id=4, name="Phones", parent_id=2, level_depth=2),
        ]

        response = CategoryListResponse(categories=categories, total=4)
        tree = response.get_tree()

        # Catégories sous root (parent_id=0 ou None)
        assert len(tree.get(0, [])) == 1  # Root
        assert len(tree.get(1, [])) == 2  # Electronics, Clothing
        assert len(tree.get(2, [])) == 1  # Phones


# =============================================================================
# HEALTH CHECK TESTS
# =============================================================================

class TestHealthCheck:
    """Tests pour la vérification de connexion."""

    @pytest.mark.asyncio
    async def test_check_connection_success(self, client_config, tenant_id):
        """check_connection doit retourner True si OK."""
        async with PrestaShopClient(client_config, tenant_id) as client:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {}

            with patch.object(client._client, "request", new_callable=AsyncMock) as mock_request:
                mock_request.return_value = mock_response

                result = await client.check_connection()

        assert result is True

    @pytest.mark.asyncio
    async def test_check_connection_failure(self, client_config, tenant_id):
        """check_connection doit retourner False si échec."""
        async with PrestaShopClient(client_config, tenant_id) as client:
            mock_response = MagicMock()
            mock_response.status_code = 401

            with patch.object(client._client, "request", new_callable=AsyncMock) as mock_request:
                mock_request.return_value = mock_response

                result = await client.check_connection()

        assert result is False


# =============================================================================
# EXCEPTION TESTS
# =============================================================================

class TestExceptions:
    """Tests pour les exceptions."""

    def test_prestashop_error_to_dict(self):
        """Les exceptions doivent être sérialisables."""
        error = PrestaShopError(
            message="Test error",
            status_code=400,
            tenant_id="tenant_123",
        )

        d = error.to_dict()

        assert d["error_type"] == "PrestaShopError"
        assert d["message"] == "Test error"
        assert d["status_code"] == 400
        assert d["tenant_id"] == "tenant_123"

    def test_not_found_error_message(self):
        """NotFoundError doit formater le message correctement."""
        error = PrestaShopNotFoundError(
            resource_type="Product",
            resource_id=42,
        )

        assert "Product" in str(error)
        assert "42" in str(error)

    def test_rate_limit_error_retry_after(self):
        """RateLimitError doit inclure retry_after."""
        error = PrestaShopRateLimitError(retry_after=60)

        assert error.retry_after == 60
        assert "60" in str(error)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

