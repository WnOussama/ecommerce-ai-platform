"""
PrestaShop Integration - Client API WebService

Ce module fournit un client async pour interagir avec l'API PrestaShop.
Compatible multi-tenant avec configuration par tenant.
"""

from app.infrastructure.external.prestashop.client import (
    PrestaShopClient,
    PrestaShopClientConfig,
)
from app.infrastructure.external.prestashop.models import (
    Product,
    ProductImage,
    Category,
    ProductListResponse,
    CategoryListResponse,
)
from app.infrastructure.external.prestashop.exceptions import (
    PrestaShopError,
    PrestaShopConnectionError,
    PrestaShopAuthenticationError,
    PrestaShopNotFoundError,
    PrestaShopRateLimitError,
    PrestaShopValidationError,
)

__all__ = [
    # Client
    "PrestaShopClient",
    "PrestaShopClientConfig",
    # Models
    "Product",
    "ProductImage",
    "Category",
    "ProductListResponse",
    "CategoryListResponse",
    # Exceptions
    "PrestaShopError",
    "PrestaShopConnectionError",
    "PrestaShopAuthenticationError",
    "PrestaShopNotFoundError",
    "PrestaShopRateLimitError",
    "PrestaShopValidationError",
]

