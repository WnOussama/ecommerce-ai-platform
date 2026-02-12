"""
PrestaShop Data Models

Modèles Pydantic pour représenter les données PrestaShop.
Ces modèles servent de contrat entre l'API PrestaShop et notre système.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any
from enum import Enum

from pydantic import BaseModel, Field, field_validator, ConfigDict


class ProductStatus(str, Enum):
    """Status d'un produit PrestaShop."""
    ACTIVE = "active"
    INACTIVE = "inactive"
    OUT_OF_STOCK = "out_of_stock"


class ProductImage(BaseModel):
    """Image associée à un produit."""

    model_config = ConfigDict(frozen=True)

    id: int = Field(..., description="ID de l'image PrestaShop")
    position: int = Field(default=0, description="Position dans la galerie")
    url: str = Field(..., description="URL complète de l'image")
    alt: Optional[str] = Field(default=None, description="Texte alternatif")
    is_cover: bool = Field(default=False, description="Image principale")


class Category(BaseModel):
    """Catégorie de produit PrestaShop."""

    model_config = ConfigDict(frozen=True)

    id: int = Field(..., description="ID PrestaShop de la catégorie")
    name: str = Field(..., min_length=1, max_length=255, description="Nom de la catégorie")
    description: Optional[str] = Field(default=None, description="Description de la catégorie")
    parent_id: Optional[int] = Field(default=None, description="ID de la catégorie parente")
    level_depth: int = Field(default=0, ge=0, description="Profondeur dans l'arborescence")
    active: bool = Field(default=True, description="Catégorie active")
    position: int = Field(default=0, ge=0, description="Position d'affichage")

    # Métadonnées
    date_add: Optional[datetime] = Field(default=None, description="Date de création")
    date_upd: Optional[datetime] = Field(default=None, description="Date de mise à jour")

    # SEO
    link_rewrite: Optional[str] = Field(default=None, description="URL slug")
    meta_title: Optional[str] = Field(default=None, description="Meta title SEO")
    meta_description: Optional[str] = Field(default=None, description="Meta description SEO")

    def get_full_path(self, separator: str = " > ") -> str:
        """Retourne le chemin complet pour affichage."""
        return self.name  # Simplifié, le chemin complet nécessite les parents


class Product(BaseModel):
    """
    Produit PrestaShop complet.

    Ce modèle représente un produit tel que retourné par l'API WebService.
    Il est conçu pour être facilement indexable dans ChromaDB.
    """

    model_config = ConfigDict(frozen=True)

    # Identifiants
    id: int = Field(..., description="ID PrestaShop du produit")
    reference: Optional[str] = Field(default=None, max_length=64, description="Référence SKU")
    ean13: Optional[str] = Field(default=None, max_length=13, description="Code EAN13")
    upc: Optional[str] = Field(default=None, max_length=12, description="Code UPC")

    # Informations principales
    name: str = Field(..., min_length=1, max_length=255, description="Nom du produit")
    description: Optional[str] = Field(default=None, description="Description longue HTML")
    description_short: Optional[str] = Field(default=None, description="Description courte")

    # Prix
    price: Decimal = Field(..., ge=0, description="Prix HT")
    price_tax_incl: Optional[Decimal] = Field(default=None, ge=0, description="Prix TTC")
    wholesale_price: Optional[Decimal] = Field(default=None, ge=0, description="Prix d'achat")
    reduction_percent: Optional[Decimal] = Field(default=None, ge=0, le=100, description="Réduction en %")
    reduction_amount: Optional[Decimal] = Field(default=None, ge=0, description="Réduction en valeur")

    # Stock
    quantity: int = Field(default=0, ge=0, description="Quantité en stock")
    minimal_quantity: int = Field(default=1, ge=1, description="Quantité minimum de commande")
    out_of_stock_behavior: int = Field(default=2, ge=0, le=2, description="0=deny, 1=allow, 2=default")

    # Catégorisation
    category_id: Optional[int] = Field(default=None, description="ID catégorie principale")
    category_name: Optional[str] = Field(default=None, description="Nom catégorie principale")
    categories: List[int] = Field(default_factory=list, description="IDs de toutes les catégories")

    # État
    active: bool = Field(default=True, description="Produit actif")
    available_for_order: bool = Field(default=True, description="Disponible à la commande")
    visibility: str = Field(default="both", description="Visibilité: both, catalog, search, none")
    condition: str = Field(default="new", description="État: new, used, refurbished")

    # Médias
    images: List[ProductImage] = Field(default_factory=list, description="Images du produit")
    cover_image_url: Optional[str] = Field(default=None, description="URL image principale")

    # Dimensions/Poids
    weight: Optional[Decimal] = Field(default=None, ge=0, description="Poids en kg")
    width: Optional[Decimal] = Field(default=None, ge=0, description="Largeur en cm")
    height: Optional[Decimal] = Field(default=None, ge=0, description="Hauteur en cm")
    depth: Optional[Decimal] = Field(default=None, ge=0, description="Profondeur en cm")

    # SEO
    link_rewrite: Optional[str] = Field(default=None, description="URL slug")
    meta_title: Optional[str] = Field(default=None, description="Meta title SEO")
    meta_description: Optional[str] = Field(default=None, description="Meta description SEO")

    # Métadonnées
    manufacturer_name: Optional[str] = Field(default=None, description="Nom du fabricant")
    supplier_name: Optional[str] = Field(default=None, description="Nom du fournisseur")
    date_add: Optional[datetime] = Field(default=None, description="Date de création")
    date_upd: Optional[datetime] = Field(default=None, description="Date de mise à jour")

    # Tags et attributs personnalisés
    tags: List[str] = Field(default_factory=list, description="Tags du produit")
    features: Dict[str, str] = Field(default_factory=dict, description="Caractéristiques clé-valeur")

    @field_validator("price", "price_tax_incl", "wholesale_price", mode="before")
    @classmethod
    def coerce_decimal(cls, v: Any) -> Optional[Decimal]:
        """Convertit les valeurs en Decimal."""
        if v is None:
            return None
        if isinstance(v, Decimal):
            return v
        try:
            return Decimal(str(v))
        except (ValueError, TypeError):
            return Decimal("0")

    @property
    def is_in_stock(self) -> bool:
        """Vérifie si le produit est en stock."""
        return self.quantity > 0

    @property
    def is_on_sale(self) -> bool:
        """Vérifie si le produit est en promotion."""
        return bool(
            (self.reduction_percent and self.reduction_percent > 0) or
            (self.reduction_amount and self.reduction_amount > 0)
        )

    @property
    def status(self) -> ProductStatus:
        """Détermine le status du produit."""
        if not self.active:
            return ProductStatus.INACTIVE
        if not self.is_in_stock:
            return ProductStatus.OUT_OF_STOCK
        return ProductStatus.ACTIVE

    def get_display_price(self) -> Decimal:
        """Retourne le prix à afficher (TTC si disponible, sinon HT)."""
        return self.price_tax_incl if self.price_tax_incl else self.price

    def to_search_text(self) -> str:
        """
        Génère un texte optimisé pour l'indexation et la recherche.
        Utilisé par ChromaDB pour le RAG.
        """
        parts = [self.name]

        if self.description_short:
            # Nettoyer le HTML basique
            clean_desc = self.description_short.replace("<br>", " ").replace("<br/>", " ")
            # Supprimer les tags HTML simples
            import re
            clean_desc = re.sub(r"<[^>]+>", "", clean_desc)
            parts.append(clean_desc)

        if self.category_name:
            parts.append(f"Catégorie: {self.category_name}")

        if self.manufacturer_name:
            parts.append(f"Marque: {self.manufacturer_name}")

        if self.reference:
            parts.append(f"Référence: {self.reference}")

        if self.tags:
            parts.append(f"Tags: {', '.join(self.tags)}")

        if self.features:
            features_text = ", ".join(f"{k}: {v}" for k, v in self.features.items())
            parts.append(f"Caractéristiques: {features_text}")

        return " | ".join(parts)

    def to_embedding_metadata(self) -> Dict[str, Any]:
        """
        Génère les métadonnées pour ChromaDB.
        Ces données sont stockées avec le vecteur pour le filtrage.
        """
        return {
            "product_id": self.id,
            "name": self.name,
            "price": float(self.price),
            "price_tax_incl": float(self.price_tax_incl) if self.price_tax_incl else float(self.price),
            "quantity": self.quantity,
            "in_stock": self.is_in_stock,
            "on_sale": self.is_on_sale,
            "category_id": self.category_id,
            "category_name": self.category_name or "",
            "manufacturer": self.manufacturer_name or "",
            "reference": self.reference or "",
            "active": self.active,
        }


class ProductListResponse(BaseModel):
    """Réponse paginée pour la liste des produits."""

    products: List[Product] = Field(default_factory=list, description="Liste des produits")
    total: int = Field(default=0, ge=0, description="Nombre total de produits")
    limit: int = Field(default=100, ge=1, description="Limite par page")
    offset: int = Field(default=0, ge=0, description="Offset de pagination")

    @property
    def has_more(self) -> bool:
        """Vérifie s'il reste des produits à récupérer."""
        return (self.offset + len(self.products)) < self.total

    @property
    def next_offset(self) -> int:
        """Calcule l'offset pour la page suivante."""
        return self.offset + len(self.products)


class CategoryListResponse(BaseModel):
    """Réponse pour la liste des catégories."""

    categories: List[Category] = Field(default_factory=list, description="Liste des catégories")
    total: int = Field(default=0, ge=0, description="Nombre total de catégories")

    def get_tree(self) -> Dict[int, List[Category]]:
        """Organise les catégories en arborescence par parent_id."""
        tree: Dict[int, List[Category]] = {}
        for cat in self.categories:
            parent_id = cat.parent_id or 0
            if parent_id not in tree:
                tree[parent_id] = []
            tree[parent_id].append(cat)
        return tree

