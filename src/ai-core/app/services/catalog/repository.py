"""
Product Repository - Accès aux données produits multi-tenant

Ce repository gère les opérations CRUD sur les produits avec:
- Isolation par tenant
- UPSERT basé sur (tenant_id, external_id)
- Opérations bulk optimisées
- Filtrage et pagination
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any, Protocol, Sequence
from enum import Enum

from sqlalchemy import select, update, delete, and_, or_, func
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class ProductData:
    """
    Représentation d'un produit pour le repository.

    Utilisé comme DTO entre le service et le repository.
    Découplé du modèle PrestaShop et du modèle SQLAlchemy.
    """
    external_id: int
    name: str
    price: Decimal
    tenant_id: Optional[str] = None
    reference: Optional[str] = None
    ean13: Optional[str] = None
    description: Optional[str] = None
    description_short: Optional[str] = None
    price_tax_incl: Optional[Decimal] = None
    quantity: int = 0
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    manufacturer_name: Optional[str] = None
    image_url: Optional[str] = None
    active: bool = True
    available_for_order: bool = True

    # Métadonnées
    extra_data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convertit en dictionnaire pour insertion SQL."""
        return {
            "external_id": str(self.external_id),
            "name": self.name[:500] if self.name else "",  # Limit pour DB
            "reference": self.reference[:100] if self.reference else None,
            "ean13": self.ean13[:13] if self.ean13 else None,
            "description": self.description,
            "description_short": self.description_short[:1000] if self.description_short else None,
            "price": float(self.price),
            "price_tax_incl": float(self.price_tax_incl) if self.price_tax_incl else None,
            "quantity": self.quantity,
            "category_id": self.category_id,
            "category_name": self.category_name[:255] if self.category_name else None,
            "manufacturer_name": self.manufacturer_name[:255] if self.manufacturer_name else None,
            "image_url": self.image_url[:500] if self.image_url else None,
            "active": self.active,
            "available_for_order": self.available_for_order,
            "extra_data": self.extra_data,
        }


@dataclass
class ProductFilter:
    """Filtres pour la recherche de produits."""
    active_only: bool = True
    in_stock_only: bool = False
    category_id: Optional[int] = None
    search_query: Optional[str] = None
    min_price: Optional[Decimal] = None
    max_price: Optional[Decimal] = None
    limit: int = 100
    offset: int = 0


class ProductRepositoryProtocol(Protocol):
    """
    Protocol pour le repository produits.

    Permet l'injection de dépendances et le mocking pour les tests.
    """

    async def upsert_product(
        self,
        tenant_id: str,
        product: ProductData,
    ) -> tuple[bool, bool]:
        """
        Insère ou met à jour un produit.

        Returns:
            Tuple (created: bool, updated: bool)
        """
        ...

    async def upsert_products_bulk(
        self,
        tenant_id: str,
        products: List[ProductData],
    ) -> tuple[int, int]:
        """
        Insère ou met à jour plusieurs produits en batch.

        Returns:
            Tuple (created_count, updated_count)
        """
        ...

    async def get_product_by_external_id(
        self,
        tenant_id: str,
        external_id: int,
    ) -> Optional[ProductData]:
        """Récupère un produit par son ID externe."""
        ...

    async def get_products(
        self,
        tenant_id: str,
        filters: Optional[ProductFilter] = None,
    ) -> List[ProductData]:
        """Récupère les produits avec filtres."""
        ...

    async def count_products(
        self,
        tenant_id: str,
        filters: Optional[ProductFilter] = None,
    ) -> int:
        """Compte les produits."""
        ...

    async def delete_products_not_in_list(
        self,
        tenant_id: str,
        external_ids: List[int],
    ) -> int:
        """Supprime les produits qui ne sont plus dans la source."""
        ...


# =============================================================================
# SQLALCHEMY REPOSITORY
# =============================================================================

class ProductRepository:
    """
    Repository pour les produits avec SQLAlchemy async.

    Implémente les opérations CRUD avec:
    - Isolation stricte par tenant_id
    - UPSERT MySQL natif (INSERT ... ON DUPLICATE KEY UPDATE)
    - Opérations bulk optimisées
    - Logging structuré

    Usage:
        async with async_session() as session:
            repo = ProductRepository(session)
            created, updated = await repo.upsert_product(tenant_id, product_data)
    """

    def __init__(self, session: AsyncSession):
        """
        Initialise le repository.

        Args:
            session: Session SQLAlchemy async
        """
        self._session = session

    async def upsert_product(
        self,
        tenant_id: str,
        product: ProductData,
    ) -> tuple[bool, bool]:
        """
        Insère ou met à jour un produit.

        Utilise INSERT ... ON DUPLICATE KEY UPDATE pour MySQL.

        Args:
            tenant_id: ID du tenant
            product: Données du produit

        Returns:
            Tuple (created: bool, updated: bool)
            - (True, False) si créé
            - (False, True) si mis à jour
            - (False, False) si aucun changement
        """
        from app.infrastructure.database.models.product import ProductModel

        data = product.to_dict()
        data["tenant_id"] = tenant_id
        data["updated_at"] = datetime.utcnow()

        # Préparer l'INSERT
        insert_stmt = mysql_insert(ProductModel).values(**data)

        # Colonnes à mettre à jour en cas de conflit
        update_columns = {
            "name": insert_stmt.inserted.name,
            "reference": insert_stmt.inserted.reference,
            "description": insert_stmt.inserted.description,
            "description_short": insert_stmt.inserted.description_short,
            "price": insert_stmt.inserted.price,
            "price_tax_incl": insert_stmt.inserted.price_tax_incl,
            "quantity": insert_stmt.inserted.quantity,
            "category_id": insert_stmt.inserted.category_id,
            "category_name": insert_stmt.inserted.category_name,
            "manufacturer_name": insert_stmt.inserted.manufacturer_name,
            "image_url": insert_stmt.inserted.image_url,
            "active": insert_stmt.inserted.active,
            "available_for_order": insert_stmt.inserted.available_for_order,
            "extra_data": insert_stmt.inserted.extra_data,
            "updated_at": datetime.utcnow(),
        }

        upsert_stmt = insert_stmt.on_duplicate_key_update(**update_columns)

        result = await self._session.execute(upsert_stmt)
        await self._session.commit()

        # MySQL: rowcount = 1 si INSERT, 2 si UPDATE, 0 si pas de changement
        if result.rowcount == 1:
            return (True, False)  # Created
        elif result.rowcount == 2:
            return (False, True)  # Updated
        else:
            return (False, False)  # No change

    async def upsert_products_bulk(
        self,
        tenant_id: str,
        products: List[ProductData],
    ) -> tuple[int, int]:
        """
        Insère ou met à jour plusieurs produits en batch.

        Optimisé pour les grandes quantités avec un seul statement SQL.

        Args:
            tenant_id: ID du tenant
            products: Liste des produits

        Returns:
            Tuple (created_count, updated_count)
        """
        from app.infrastructure.database.models.product import ProductModel

        if not products:
            return (0, 0)

        # Compter les produits existants avant
        existing_ids = await self._get_existing_external_ids(tenant_id, products)

        # Préparer les données
        now = datetime.utcnow()
        values_list = []
        for product in products:
            data = product.to_dict()
            data["tenant_id"] = tenant_id
            data["updated_at"] = now
            values_list.append(data)

        # Bulk INSERT avec ON DUPLICATE KEY UPDATE
        insert_stmt = mysql_insert(ProductModel).values(values_list)

        update_columns = {
            "name": insert_stmt.inserted.name,
            "reference": insert_stmt.inserted.reference,
            "description": insert_stmt.inserted.description,
            "description_short": insert_stmt.inserted.description_short,
            "price": insert_stmt.inserted.price,
            "price_tax_incl": insert_stmt.inserted.price_tax_incl,
            "quantity": insert_stmt.inserted.quantity,
            "category_id": insert_stmt.inserted.category_id,
            "category_name": insert_stmt.inserted.category_name,
            "manufacturer_name": insert_stmt.inserted.manufacturer_name,
            "image_url": insert_stmt.inserted.image_url,
            "active": insert_stmt.inserted.active,
            "available_for_order": insert_stmt.inserted.available_for_order,
            "extra_data": insert_stmt.inserted.extra_data,
            "updated_at": now,
        }

        upsert_stmt = insert_stmt.on_duplicate_key_update(**update_columns)

        await self._session.execute(upsert_stmt)
        await self._session.commit()

        # Calculer created vs updated
        new_external_ids = {str(p.external_id) for p in products}
        created_count = len(new_external_ids - existing_ids)
        updated_count = len(new_external_ids & existing_ids)

        logger.debug(
            "Bulk upsert completed",
            extra={
                "tenant_id": tenant_id,
                "total": len(products),
                "created": created_count,
                "updated": updated_count,
            }
        )

        return (created_count, updated_count)

    async def _get_existing_external_ids(
        self,
        tenant_id: str,
        products: List[ProductData],
    ) -> set[str]:
        """Récupère les IDs externes existants pour un tenant."""
        from app.infrastructure.database.models.product import ProductModel

        external_ids = [str(p.external_id) for p in products]

        stmt = select(ProductModel.external_id).where(
            and_(
                ProductModel.tenant_id == tenant_id,
                ProductModel.external_id.in_(external_ids)
            )
        )

        result = await self._session.execute(stmt)
        return {row[0] for row in result.fetchall()}

    async def get_product_by_external_id(
        self,
        tenant_id: str,
        external_id: int,
    ) -> Optional[ProductData]:
        """
        Récupère un produit par son ID externe.

        Args:
            tenant_id: ID du tenant
            external_id: ID dans le système source (PrestaShop)

        Returns:
            ProductData ou None si non trouvé
        """
        from app.infrastructure.database.models.product import ProductModel

        stmt = select(ProductModel).where(
            and_(
                ProductModel.tenant_id == tenant_id,
                ProductModel.external_id == str(external_id)
            )
        )

        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()

        if not row:
            return None

        return self._model_to_data(row)

    async def get_products(
        self,
        tenant_id: str,
        filters: Optional[ProductFilter] = None,
    ) -> List[ProductData]:
        """
        Récupère les produits avec filtres.

        Args:
            tenant_id: ID du tenant
            filters: Filtres optionnels

        Returns:
            Liste de ProductData
        """
        from app.infrastructure.database.models.product import ProductModel

        filters = filters or ProductFilter()

        stmt = select(ProductModel).where(ProductModel.tenant_id == tenant_id)

        # Appliquer les filtres
        if filters.active_only:
            stmt = stmt.where(ProductModel.active == True)

        if filters.in_stock_only:
            stmt = stmt.where(ProductModel.quantity > 0)

        if filters.category_id:
            stmt = stmt.where(ProductModel.category_id == filters.category_id)

        if filters.search_query:
            search = f"%{filters.search_query}%"
            stmt = stmt.where(
                or_(
                    ProductModel.name.ilike(search),
                    ProductModel.reference.ilike(search),
                    ProductModel.description_short.ilike(search),
                )
            )

        if filters.min_price is not None:
            stmt = stmt.where(ProductModel.price >= float(filters.min_price))

        if filters.max_price is not None:
            stmt = stmt.where(ProductModel.price <= float(filters.max_price))

        # Pagination
        stmt = stmt.offset(filters.offset).limit(filters.limit)

        # Ordre par défaut
        stmt = stmt.order_by(ProductModel.name)

        result = await self._session.execute(stmt)
        rows = result.scalars().all()

        return [self._model_to_data(row) for row in rows]

    async def count_products(
        self,
        tenant_id: str,
        filters: Optional[ProductFilter] = None,
    ) -> int:
        """
        Compte les produits.

        Args:
            tenant_id: ID du tenant
            filters: Filtres optionnels

        Returns:
            Nombre de produits
        """
        from app.infrastructure.database.models.product import ProductModel

        filters = filters or ProductFilter()

        stmt = select(func.count(ProductModel.id)).where(
            ProductModel.tenant_id == tenant_id
        )

        if filters.active_only:
            stmt = stmt.where(ProductModel.active == True)

        if filters.in_stock_only:
            stmt = stmt.where(ProductModel.quantity > 0)

        if filters.category_id:
            stmt = stmt.where(ProductModel.category_id == filters.category_id)

        result = await self._session.execute(stmt)
        return result.scalar() or 0

    async def delete_products_not_in_list(
        self,
        tenant_id: str,
        external_ids: List[int],
    ) -> int:
        """
        Supprime les produits qui ne sont plus dans la source.

        Utile pour nettoyer les produits supprimés dans PrestaShop.

        Sécurités:
        - Filtre OBLIGATOIRE par tenant_id
        - Refuse de supprimer si liste vide
        - Refuse si cela supprimerait > 90% des produits

        Args:
            tenant_id: ID du tenant
            external_ids: Liste des IDs à conserver

        Returns:
            Nombre de produits supprimés
        """
        from app.infrastructure.database.models.product import ProductModel

        if not external_ids:
            # Si liste vide, ne rien supprimer (sécurité)
            logger.warning(
                "delete_products_not_in_list called with empty list, skipping",
                extra={"tenant_id": tenant_id}
            )
            return 0

        # Compter les produits actuels pour éviter suppression massive
        current_count = await self.count_products(tenant_id, filters=None)

        if current_count > 0:
            keep_count = len(external_ids)
            delete_ratio = (current_count - keep_count) / current_count

            # Refuser si on supprimerait plus de 90% des produits
            if delete_ratio > 0.9 and current_count > 10:
                logger.warning(
                    "Refusing to delete >90% of products - possible data issue",
                    extra={
                        "tenant_id": tenant_id,
                        "current_count": current_count,
                        "keep_count": keep_count,
                        "delete_ratio": round(delete_ratio * 100, 1),
                    }
                )
                return 0

        str_ids = [str(eid) for eid in external_ids]

        stmt = delete(ProductModel).where(
            and_(
                ProductModel.tenant_id == tenant_id,
                ProductModel.external_id.notin_(str_ids)
            )
        )

        result = await self._session.execute(stmt)
        await self._session.commit()

        deleted_count = result.rowcount

        if deleted_count > 0:
            logger.info(
                "Deleted stale products",
                extra={
                    "tenant_id": tenant_id,
                    "deleted_count": deleted_count,
                }
            )

        return deleted_count

    async def get_all_external_ids(self, tenant_id: str) -> List[str]:
        """
        Récupère tous les IDs externes d'un tenant.

        Utile pour la détection des produits supprimés.
        """
        from app.infrastructure.database.models.product import ProductModel

        stmt = select(ProductModel.external_id).where(
            ProductModel.tenant_id == tenant_id
        )

        result = await self._session.execute(stmt)
        return [row[0] for row in result.fetchall()]

    def _model_to_data(self, model: Any) -> ProductData:
        """Convertit un modèle SQLAlchemy en ProductData."""
        return ProductData(
            external_id=int(model.external_id),
            name=model.name,
            price=Decimal(str(model.price)),
            tenant_id=model.tenant_id,
            reference=model.reference,
            ean13=model.ean13,
            description=model.description,
            description_short=model.description_short,
            price_tax_incl=Decimal(str(model.price_tax_incl)) if model.price_tax_incl else None,
            quantity=model.quantity or 0,
            category_id=model.category_id,
            category_name=model.category_name,
            manufacturer_name=model.manufacturer_name,
            image_url=model.image_url,
            active=model.active,
            available_for_order=model.available_for_order,
            extra_data=model.extra_data or {},
        )


# =============================================================================
# IN-MEMORY REPOSITORY (FOR TESTING)
# =============================================================================

class InMemoryProductRepository:
    """
    Repository en mémoire pour les tests.

    Implémente le même protocol que ProductRepository
    mais stocke les données en mémoire.
    """

    def __init__(self):
        self._products: Dict[str, Dict[str, ProductData]] = {}  # tenant_id -> external_id -> data

    def reset(self) -> None:
        """Réinitialise le stockage."""
        self._products.clear()

    async def upsert_product(
        self,
        tenant_id: str,
        product: ProductData,
    ) -> tuple[bool, bool]:
        """Insère ou met à jour un produit."""
        if tenant_id not in self._products:
            self._products[tenant_id] = {}

        external_id = str(product.external_id)
        exists = external_id in self._products[tenant_id]

        product.tenant_id = tenant_id
        self._products[tenant_id][external_id] = product

        if exists:
            return (False, True)  # Updated
        return (True, False)  # Created

    async def upsert_products_bulk(
        self,
        tenant_id: str,
        products: List[ProductData],
    ) -> tuple[int, int]:
        """Insère ou met à jour plusieurs produits."""
        created = 0
        updated = 0

        for product in products:
            is_created, is_updated = await self.upsert_product(tenant_id, product)
            if is_created:
                created += 1
            elif is_updated:
                updated += 1

        return (created, updated)

    async def get_product_by_external_id(
        self,
        tenant_id: str,
        external_id: int,
    ) -> Optional[ProductData]:
        """Récupère un produit."""
        tenant_products = self._products.get(tenant_id, {})
        return tenant_products.get(str(external_id))

    async def get_products(
        self,
        tenant_id: str,
        filters: Optional[ProductFilter] = None,
    ) -> List[ProductData]:
        """Récupère les produits."""
        tenant_products = self._products.get(tenant_id, {})
        products = list(tenant_products.values())

        filters = filters or ProductFilter()

        # Appliquer les filtres
        if filters.active_only:
            products = [p for p in products if p.active]

        if filters.in_stock_only:
            products = [p for p in products if p.quantity > 0]

        if filters.category_id:
            products = [p for p in products if p.category_id == filters.category_id]

        # Pagination
        products = products[filters.offset:filters.offset + filters.limit]

        return products

    async def count_products(
        self,
        tenant_id: str,
        filters: Optional[ProductFilter] = None,
    ) -> int:
        """Compte les produits (sans pagination)."""
        tenant_products = self._products.get(tenant_id, {})
        products = list(tenant_products.values())

        filters = filters or ProductFilter()

        # Appliquer les filtres (sauf pagination)
        if filters.active_only:
            products = [p for p in products if p.active]

        if filters.in_stock_only:
            products = [p for p in products if p.quantity > 0]

        if filters.category_id:
            products = [p for p in products if p.category_id == filters.category_id]

        return len(products)

    async def delete_products_not_in_list(
        self,
        tenant_id: str,
        external_ids: List[int],
    ) -> int:
        """Supprime les produits obsolètes."""
        if tenant_id not in self._products:
            return 0

        # Sécurité: ne rien supprimer si liste vide
        if not external_ids:
            return 0

        str_ids = {str(eid) for eid in external_ids}
        to_delete = [
            eid for eid in self._products[tenant_id]
            if eid not in str_ids
        ]

        for eid in to_delete:
            del self._products[tenant_id][eid]

        return len(to_delete)

    async def get_all_external_ids(self, tenant_id: str) -> List[str]:
        """Récupère tous les IDs externes."""
        return list(self._products.get(tenant_id, {}).keys())




