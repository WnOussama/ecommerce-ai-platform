"""
Product SQLAlchemy Model

Modèle pour stocker les produits synchronisés depuis PrestaShop.
Multi-tenant avec index optimisés.
Compatible PostgreSQL.
"""

from datetime import datetime
from uuid import uuid4
import uuid

from sqlalchemy import (
    Column, String, Integer, Float, Boolean, Text, JSON,
    DateTime, ForeignKey, Index, BigInteger
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func

from app.infrastructure.database.base import Base


def generate_uuid():
    return uuid.uuid4()


class ProductModel(Base):
    """
    Table des produits synchronisés.

    Chaque produit est lié à un tenant et identifié par son external_id
    (ID dans le système source comme PrestaShop).

    La clé unique est (tenant_id, external_id) pour permettre l'UPSERT.
    """
    __tablename__ = "products"

    # Clé primaire
    id = Column(UUID(as_uuid=True), primary_key=True, default=generate_uuid)

    # Multi-tenant
    tenant_id = Column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # ID externe (dans PrestaShop/Shopify/etc)
    external_id = Column(String(100), nullable=False)

    # Informations produit
    name = Column(String(500), nullable=False)
    reference = Column(String(100), nullable=True)
    ean13 = Column(String(13), nullable=True)

    # Descriptions
    description = Column(Text, nullable=True)
    description_short = Column(String(1000), nullable=True)

    # Prix
    price = Column(Float, nullable=False, default=0.0)
    price_tax_incl = Column(Float, nullable=True)

    # Stock
    quantity = Column(Integer, nullable=False, default=0)

    # Catégorisation
    category_id = Column(Integer, nullable=True)
    category_name = Column(String(255), nullable=True)

    # Fabricant
    manufacturer_name = Column(String(255), nullable=True)

    # Image principale
    image_url = Column(String(500), nullable=True)

    # État
    active = Column(Boolean, nullable=False, default=True)
    available_for_order = Column(Boolean, nullable=False, default=True)

    # Données additionnelles (flexibilité)
    extra_data = Column(JSON, nullable=True, default=dict)

    # Timestamps
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    synced_at = Column(DateTime, nullable=True)  # Dernière sync réussie

    __table_args__ = (
        # Clé unique pour UPSERT
        Index(
            "idx_product_tenant_external",
            "tenant_id",
            "external_id",
            unique=True
        ),
        # Recherche par catégorie
        Index("idx_product_tenant_category", "tenant_id", "category_id"),
        # Recherche par état
        Index("idx_product_tenant_active", "tenant_id", "active"),
        # Recherche par prix
        Index("idx_product_tenant_price", "tenant_id", "price"),
        # Recherche par nom
        Index("idx_product_name", "name"),
        # Recherche par référence
        Index("idx_product_reference", "reference"),
        {'extend_existing': True}
    )

    def __repr__(self) -> str:
        return f"<Product(id={self.id}, name={self.name}, tenant={self.tenant_id})>"

