"""
PrestaShop WebService Client

Client asynchrone pour interagir avec l'API PrestaShop WebService.
Supporte le multi-tenant avec configuration par tenant.

L'API PrestaShop WebService utilise:
- Authentification HTTP Basic avec API key comme username
- Format XML par défaut, JSON disponible via &output_format=JSON
- Ressources RESTful: /api/products, /api/categories, etc.

Documentation: https://devdocs.prestashop-project.org/8/webservice/
"""

import base64
import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

import httpx

from app.infrastructure.external.prestashop.exceptions import (
    PrestaShopAuthenticationError,
    PrestaShopConnectionError,
    PrestaShopError,
    PrestaShopNotFoundError,
    PrestaShopRateLimitError,
    PrestaShopServerError,
    PrestaShopValidationError,
)
from app.infrastructure.external.prestashop.models import (
    Category,
    CategoryListResponse,
    Product,
    ProductImage,
    ProductListResponse,
)

logger = logging.getLogger(__name__)


@dataclass
class PrestaShopClientConfig:
    """
    Configuration du client PrestaShop par tenant.

    Attributes:
        shop_url: URL de base du shop PrestaShop (ex: https://myshop.com)
        api_key: Clé API WebService PrestaShop
        timeout: Timeout des requêtes en secondes
        language_id: ID de la langue par défaut (1 = langue principale)
        verify_ssl: Vérifier le certificat SSL
    """

    shop_url: str
    api_key: str
    timeout: int = 30
    language_id: int = 1
    verify_ssl: bool = True

    def __post_init__(self):
        """Normalise l'URL du shop."""
        self.shop_url = self.shop_url.rstrip("/")

    @property
    def api_url(self) -> str:
        """URL de base de l'API WebService."""
        return f"{self.shop_url}/api"

    @property
    def auth_header(self) -> str:
        """Header d'authentification Basic (API key comme username, sans password)."""
        credentials = base64.b64encode(f"{self.api_key}:".encode()).decode()
        return f"Basic {credentials}"


class PrestaShopClient:
    """
    Client asynchrone pour l'API PrestaShop WebService.

    Ce client permet d'interagir avec les ressources PrestaShop de manière
    asynchrone et thread-safe. Il gère automatiquement l'authentification,
    la pagination et la conversion des données.

    Usage:
        config = PrestaShopClientConfig(
            shop_url="https://myshop.com",
            api_key="ABCD1234..."
        )
        async with PrestaShopClient(config, tenant_id="tenant_123") as client:
            products = await client.get_products(limit=50)
    """

    def __init__(self, config: PrestaShopClientConfig, tenant_id: str):
        """
        Initialise le client PrestaShop.

        Args:
            config: Configuration du client
            tenant_id: Identifiant du tenant pour le logging et l'isolation
        """
        self.config = config
        self.tenant_id = tenant_id
        self._client: Optional[httpx.AsyncClient] = None

        logger.info(
            "PrestaShopClient initialized",
            extra={
                "tenant_id": tenant_id,
                "shop_url": config.shop_url,
                "timeout": config.timeout,
            },
        )

    async def __aenter__(self) -> "PrestaShopClient":
        """Crée le client HTTP à l'entrée du context manager."""
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.config.timeout),
            verify=self.config.verify_ssl,
            headers={
                "Authorization": self.config.auth_header,
                "Accept": "application/json",
            },
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Ferme le client HTTP à la sortie du context manager."""
        if self._client:
            await self._client.aclose()
            self._client = None

    def _get_client(self) -> httpx.AsyncClient:
        """Retourne le client HTTP ou lève une erreur si non initialisé."""
        if self._client is None:
            raise PrestaShopError(
                "Client not initialized. Use 'async with PrestaShopClient(...) as client:'",
                tenant_id=self.tenant_id,
            )
        return self._client

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Effectue une requête HTTP vers l'API PrestaShop.

        Args:
            method: Méthode HTTP (GET, POST, etc.)
            endpoint: Endpoint relatif (ex: /products)
            params: Paramètres de query string

        Returns:
            Réponse JSON parsée

        Raises:
            PrestaShopConnectionError: Erreur de connexion
            PrestaShopAuthenticationError: Authentification échouée
            PrestaShopNotFoundError: Ressource non trouvée
            PrestaShopRateLimitError: Rate limit atteint
            PrestaShopServerError: Erreur serveur
        """
        client = self._get_client()
        url = f"{self.config.api_url}{endpoint}"

        # Ajouter output_format=JSON à tous les appels
        params = params or {}
        params["output_format"] = "JSON"

        logger.debug(
            "PrestaShop API request",
            extra={
                "method": method,
                "url": url,
                "params": params,
                "tenant_id": self.tenant_id,
            },
        )

        try:
            response = await client.request(method, url, params=params)

            # Log de la réponse
            logger.debug(
                "PrestaShop API response",
                extra={
                    "status_code": response.status_code,
                    "tenant_id": self.tenant_id,
                },
            )

            # Gestion des erreurs HTTP
            if response.status_code == 401:
                raise PrestaShopAuthenticationError(
                    tenant_id=self.tenant_id,
                )

            if response.status_code == 404:
                raise PrestaShopNotFoundError(
                    resource_type="resource",
                    message=f"Resource not found: {endpoint}",
                    tenant_id=self.tenant_id,
                )

            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                raise PrestaShopRateLimitError(
                    retry_after=int(retry_after) if retry_after else None,
                    tenant_id=self.tenant_id,
                )

            if response.status_code >= 500:
                raise PrestaShopServerError(
                    message=f"Server error: HTTP {response.status_code}",
                    status_code=response.status_code,
                    tenant_id=self.tenant_id,
                )

            if response.status_code >= 400:
                raise PrestaShopError(
                    message=f"Request failed: HTTP {response.status_code}",
                    status_code=response.status_code,
                    tenant_id=self.tenant_id,
                )

            # Parse JSON
            try:
                return response.json()
            except Exception as e:
                raise PrestaShopValidationError(
                    message=f"Invalid JSON response: {str(e)}",
                    tenant_id=self.tenant_id,
                )

        except httpx.TimeoutException as e:
            logger.error(
                "PrestaShop request timeout",
                extra={"tenant_id": self.tenant_id, "url": url, "error": str(e)},
            )
            raise PrestaShopConnectionError(
                message="Request timeout",
                original_error=e,
                tenant_id=self.tenant_id,
            )

        except httpx.ConnectError as e:
            logger.error(
                "PrestaShop connection error",
                extra={"tenant_id": self.tenant_id, "url": url, "error": str(e)},
            )
            raise PrestaShopConnectionError(
                message="Failed to connect",
                original_error=e,
                tenant_id=self.tenant_id,
            )

        except PrestaShopError:
            # Re-raise nos propres exceptions
            raise

        except Exception as e:
            logger.error(
                "Unexpected PrestaShop error",
                extra={"tenant_id": self.tenant_id, "url": url, "error": str(e)},
            )
            raise PrestaShopConnectionError(
                message="Unexpected error",
                original_error=e,
                tenant_id=self.tenant_id,
            )

    # =========================================================================
    # PRODUCTS
    # =========================================================================

    async def get_products(
        self,
        limit: int = 100,
        offset: int = 0,
        active_only: bool = True,
        sort: str = "id_ASC",
    ) -> ProductListResponse:
        """
        Récupère la liste des produits depuis PrestaShop.

        Args:
            limit: Nombre maximum de produits à récupérer (1-500)
            offset: Index de départ pour la pagination
            active_only: Ne récupérer que les produits actifs
            sort: Tri des résultats (ex: id_ASC, name_DESC, price_ASC)

        Returns:
            ProductListResponse contenant les produits et infos de pagination

        Example:
            response = await client.get_products(limit=50, offset=0)
            for product in response.products:
                print(f"{product.name}: {product.price}€")
        """
        # Validation des paramètres
        limit = max(1, min(limit, 500))
        offset = max(0, offset)

        # Construction des paramètres
        params: Dict[str, Any] = {
            "display": "full",
            "limit": f"{offset},{limit}",
            "sort": f"[{sort}]",
        }

        # Filtre produits actifs
        if active_only:
            params["filter[active]"] = "1"

        logger.info(
            "Fetching products from PrestaShop",
            extra={
                "tenant_id": self.tenant_id,
                "limit": limit,
                "offset": offset,
                "active_only": active_only,
            },
        )

        # Appel API
        data = await self._request("GET", "/products", params=params)

        # Parse des produits
        products_data = data.get("products", [])
        if not isinstance(products_data, list):
            products_data = [products_data] if products_data else []

        products = []
        for p in products_data:
            try:
                product = self._parse_product(p)
                products.append(product)
            except Exception as e:
                logger.warning(
                    "Failed to parse product",
                    extra={
                        "tenant_id": self.tenant_id,
                        "product_id": p.get("id"),
                        "error": str(e),
                    },
                )

        # Note: L'API PrestaShop ne retourne pas toujours le total
        # On estime s'il y a plus de résultats
        total = offset + len(products)
        if len(products) == limit:
            total += 1  # Indique qu'il y a potentiellement plus

        logger.info(
            "Products fetched successfully",
            extra={
                "tenant_id": self.tenant_id,
                "count": len(products),
                "offset": offset,
            },
        )

        return ProductListResponse(
            products=products,
            total=total,
            limit=limit,
            offset=offset,
        )

    async def get_product(self, product_id: int) -> Product:
        """
        Récupère un produit spécifique par son ID.

        Args:
            product_id: ID PrestaShop du produit

        Returns:
            Product avec toutes les informations

        Raises:
            PrestaShopNotFoundError: Si le produit n'existe pas

        Example:
            product = await client.get_product(42)
            print(f"Prix: {product.get_display_price()}€")
        """
        logger.info(
            "Fetching single product",
            extra={
                "tenant_id": self.tenant_id,
                "product_id": product_id,
            },
        )

        params = {"display": "full"}

        try:
            data = await self._request("GET", f"/products/{product_id}", params=params)
        except PrestaShopNotFoundError:
            raise PrestaShopNotFoundError(
                resource_type="Product",
                resource_id=product_id,
                tenant_id=self.tenant_id,
            )

        product_data = data.get("product", data)

        try:
            product = self._parse_product(product_data)

            logger.info(
                "Product fetched successfully",
                extra={
                    "tenant_id": self.tenant_id,
                    "product_id": product_id,
                    "product_name": product.name,
                },
            )

            return product

        except Exception as e:
            raise PrestaShopValidationError(
                message=f"Failed to parse product {product_id}: {str(e)}",
                tenant_id=self.tenant_id,
            )

    def _parse_product(self, data: Dict[str, Any]) -> Product:
        """Parse les données brutes d'un produit PrestaShop."""

        # Extraction du nom (peut être un dict avec id_lang)
        name = self._get_localized_value(data.get("name", "Unknown"))
        description = self._get_localized_value(data.get("description", ""))
        description_short = self._get_localized_value(data.get("description_short", ""))

        # Prix
        price = Decimal(str(data.get("price", 0)))
        price_tax_incl = None
        if "price_tax_incl" in data:
            price_tax_incl = Decimal(str(data.get("price_tax_incl", 0)))

        # Stock
        quantity = int(data.get("quantity", 0))
        if isinstance(quantity, dict):
            quantity = 0

        # Catégorie
        category_id = data.get("id_category_default")
        if isinstance(category_id, dict):
            category_id = None
        elif category_id:
            category_id = int(category_id)

        # Images
        images = self._parse_images(data.get("associations", {}).get("images", []))
        cover_url = None
        for img in images:
            if img.is_cover:
                cover_url = img.url
                break
        if not cover_url and images:
            cover_url = images[0].url

        # Features
        features = self._parse_features(data.get("associations", {}).get("product_features", []))

        # Tags
        tags = self._parse_tags(data.get("associations", {}).get("tags", []))

        return Product(
            id=int(data.get("id")),
            reference=data.get("reference") or None,
            ean13=data.get("ean13") or None,
            upc=data.get("upc") or None,
            name=name,
            description=description or None,
            description_short=description_short or None,
            price=price,
            price_tax_incl=price_tax_incl,
            wholesale_price=Decimal(str(data.get("wholesale_price", 0)))
            if data.get("wholesale_price")
            else None,
            quantity=quantity,
            minimal_quantity=int(data.get("minimal_quantity", 1)),
            category_id=category_id,
            active=str(data.get("active", "1")) == "1",
            available_for_order=str(data.get("available_for_order", "1")) == "1",
            visibility=data.get("visibility", "both"),
            condition=data.get("condition", "new"),
            images=images,
            cover_image_url=cover_url,
            weight=Decimal(str(data.get("weight", 0))) if data.get("weight") else None,
            width=Decimal(str(data.get("width", 0))) if data.get("width") else None,
            height=Decimal(str(data.get("height", 0))) if data.get("height") else None,
            depth=Decimal(str(data.get("depth", 0))) if data.get("depth") else None,
            link_rewrite=self._get_localized_value(data.get("link_rewrite", "")),
            meta_title=self._get_localized_value(data.get("meta_title", "")),
            meta_description=self._get_localized_value(data.get("meta_description", "")),
            manufacturer_name=data.get("manufacturer_name"),
            date_add=self._parse_datetime(data.get("date_add")),
            date_upd=self._parse_datetime(data.get("date_upd")),
            tags=tags,
            features=features,
        )

    # =========================================================================
    # CATEGORIES
    # =========================================================================

    async def get_categories(
        self,
        active_only: bool = True,
        exclude_root: bool = True,
    ) -> CategoryListResponse:
        """
        Récupère toutes les catégories depuis PrestaShop.

        Args:
            active_only: Ne récupérer que les catégories actives
            exclude_root: Exclure la catégorie racine (id=1 ou 2)

        Returns:
            CategoryListResponse avec toutes les catégories

        Example:
            response = await client.get_categories()
            for cat in response.categories:
                print(f"{cat.name} (depth: {cat.level_depth})")
        """
        params: Dict[str, Any] = {
            "display": "full",
        }

        if active_only:
            params["filter[active]"] = "1"

        logger.info(
            "Fetching categories from PrestaShop",
            extra={"tenant_id": self.tenant_id, "active_only": active_only},
        )

        data = await self._request("GET", "/categories", params=params)

        categories_data = data.get("categories", [])
        if not isinstance(categories_data, list):
            categories_data = [categories_data] if categories_data else []

        categories = []
        for c in categories_data:
            try:
                category = self._parse_category(c)

                # Exclure root si demandé
                if exclude_root and category.id in (1, 2) and category.level_depth == 0:
                    continue

                categories.append(category)
            except Exception as e:
                logger.warning(
                    "Failed to parse category",
                    extra={
                        "tenant_id": self.tenant_id,
                        "category_id": c.get("id"),
                        "error": str(e),
                    },
                )

        logger.info(
            "Categories fetched successfully",
            extra={
                "tenant_id": self.tenant_id,
                "count": len(categories),
            },
        )

        return CategoryListResponse(
            categories=categories,
            total=len(categories),
        )

    async def get_category(self, category_id: int) -> Category:
        """
        Récupère une catégorie spécifique par son ID.

        Args:
            category_id: ID PrestaShop de la catégorie

        Returns:
            Category avec toutes les informations

        Raises:
            PrestaShopNotFoundError: Si la catégorie n'existe pas
        """
        logger.info(
            "Fetching single category",
            extra={"tenant_id": self.tenant_id, "category_id": category_id},
        )

        params = {"display": "full"}

        try:
            data = await self._request("GET", f"/categories/{category_id}", params=params)
        except PrestaShopNotFoundError:
            raise PrestaShopNotFoundError(
                resource_type="Category",
                resource_id=category_id,
                tenant_id=self.tenant_id,
            )

        category_data = data.get("category", data)
        return self._parse_category(category_data)

    def _parse_category(self, data: Dict[str, Any]) -> Category:
        """Parse les données brutes d'une catégorie PrestaShop."""

        name = self._get_localized_value(data.get("name", "Unknown"))
        description = self._get_localized_value(data.get("description", ""))

        parent_id = data.get("id_parent")
        if isinstance(parent_id, dict):
            parent_id = None
        elif parent_id:
            parent_id = int(parent_id)
            if parent_id == 0:
                parent_id = None

        return Category(
            id=int(data.get("id")),
            name=name,
            description=description or None,
            parent_id=parent_id,
            level_depth=int(data.get("level_depth", 0)),
            active=str(data.get("active", "1")) == "1",
            position=int(data.get("position", 0)),
            date_add=self._parse_datetime(data.get("date_add")),
            date_upd=self._parse_datetime(data.get("date_upd")),
            link_rewrite=self._get_localized_value(data.get("link_rewrite", "")),
            meta_title=self._get_localized_value(data.get("meta_title", "")),
            meta_description=self._get_localized_value(data.get("meta_description", "")),
        )

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _get_localized_value(self, value: Any) -> str:
        """
        Extrait la valeur localisée d'un champ PrestaShop.

        PrestaShop peut retourner:
        - Une string simple
        - Un dict {"language": [{"@id": "1", "#text": "value"}]}
        - Une liste [{"id": "1", "value": "text"}]
        """
        if value is None:
            return ""

        if isinstance(value, str):
            return value

        if isinstance(value, dict):
            # Format: {"language": {"@id": "1", "#text": "value"}}
            # ou {"language": [{"@id": "1", "#text": "value"}, ...]}
            lang = value.get("language", value)
            if isinstance(lang, list) and lang:
                # Prendre la première langue (ou chercher language_id)
                for item in lang:
                    if isinstance(item, dict):
                        if item.get("@id") == str(self.config.language_id):
                            return item.get("#text", item.get("value", ""))
                # Fallback sur le premier
                first = lang[0]
                if isinstance(first, dict):
                    return first.get("#text", first.get("value", ""))
            elif isinstance(lang, dict):
                return lang.get("#text", lang.get("value", ""))

        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, dict):
                return first.get("value", first.get("#text", ""))
            return str(first)

        return str(value) if value else ""

    def _parse_datetime(self, value: Any) -> Optional[datetime]:
        """Parse une date PrestaShop."""
        if not value or value == "0000-00-00 00:00:00":
            return None

        try:
            if isinstance(value, datetime):
                return value
            return datetime.fromisoformat(str(value).replace(" ", "T"))
        except (ValueError, TypeError):
            return None

    def _parse_images(self, images_data: Any) -> List[ProductImage]:
        """Parse les images d'un produit."""
        images = []

        if not images_data:
            return images

        if isinstance(images_data, dict):
            images_data = [images_data]

        for idx, img in enumerate(images_data):
            if not isinstance(img, dict):
                continue

            img_id = img.get("id")
            if not img_id:
                continue

            # Construire l'URL de l'image
            # Format: {shop_url}/{id_product}-{id_image}.jpg
            # Note: simplifié, l'URL réelle dépend de la config PrestaShop
            url = f"{self.config.shop_url}/img/p/{img_id}.jpg"

            images.append(
                ProductImage(
                    id=int(img_id),
                    position=idx,
                    url=url,
                    is_cover=(idx == 0),
                )
            )

        return images

    def _parse_features(self, features_data: Any) -> Dict[str, str]:
        """Parse les caractéristiques d'un produit."""
        features = {}

        if not features_data:
            return features

        if isinstance(features_data, dict):
            features_data = [features_data]

        for feat in features_data:
            if not isinstance(feat, dict):
                continue

            name = self._get_localized_value(feat.get("name", ""))
            value = self._get_localized_value(feat.get("value", ""))

            if name and value:
                features[name] = value

        return features

    def _parse_tags(self, tags_data: Any) -> List[str]:
        """Parse les tags d'un produit."""
        tags = []

        if not tags_data:
            return tags

        if isinstance(tags_data, dict):
            tags_data = [tags_data]

        for tag in tags_data:
            if isinstance(tag, str):
                tags.append(tag)
            elif isinstance(tag, dict):
                name = self._get_localized_value(tag.get("name", ""))
                if name:
                    tags.append(name)

        return tags

    # =========================================================================
    # HEALTH CHECK
    # =========================================================================

    async def check_connection(self) -> bool:
        """
        Vérifie que la connexion à PrestaShop est fonctionnelle.

        Returns:
            True si la connexion est OK, False sinon
        """
        try:
            # Appel simple pour vérifier l'auth
            await self._request("GET", "/", params={"display": "id"})
            return True
        except PrestaShopError as e:
            logger.warning(
                "PrestaShop connection check failed",
                extra={"tenant_id": self.tenant_id, "error": str(e)},
            )
            return False
