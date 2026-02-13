"""
Catalog Services - Gestion du catalogue produits multi-tenant

Ce module contient:
- CatalogSyncService: Synchronisation depuis PrestaShop
- ProductRepository: Accès aux données produits
"""

from app.services.catalog.sync_service import (
    CatalogSyncService,
    SyncResult,
    SyncStatus,
    SyncError,
)
from app.services.catalog.repository import (
    ProductRepository,
    ProductFilter,
)

__all__ = [
    # Service
    "CatalogSyncService",
    "SyncResult",
    "SyncStatus",
    "SyncError",
    # Repository
    "ProductRepository",
    "ProductFilter",
]

