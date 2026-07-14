"""
Catalog Services - Gestion du catalogue produits multi-tenant

Ce module contient:
- CatalogSyncService: Synchronisation depuis PrestaShop
- ProductRepository: Accès aux données produits
"""

from app.services.catalog.repository import (
    ProductFilter,
    ProductRepository,
)
from app.services.catalog.sync_service import (
    CatalogSyncService,
    SyncError,
    SyncResult,
    SyncStatus,
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
