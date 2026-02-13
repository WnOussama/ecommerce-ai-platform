"""
Exemple d'utilisation du client PrestaShop

Ce fichier montre comment utiliser le PrestaShopClient dans le contexte
d'un service multi-tenant.

Pour exécuter cet exemple:
    cd src/ai-core
    python -m app.infrastructure.external.prestashop.example
"""

import asyncio
import os
from decimal import Decimal

# Configuration de test - à remplacer par de vraies valeurs
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("DB_PASSWORD", "test_password")
os.environ.setdefault("SECURITY_JWT_SECRET_KEY", "test_secret_key_32_chars_minimum")

from app.infrastructure.external.prestashop import (
    PrestaShopClient,
    PrestaShopClientConfig,
    Product,
    Category,
    PrestaShopError,
    PrestaShopNotFoundError,
)


async def example_get_products():
    """Exemple: Récupérer les produits d'un tenant."""

    # Configuration par tenant (normalement stockée en base)
    config = PrestaShopClientConfig(
        shop_url="https://demo.prestashop.com",  # URL du shop PrestaShop
        api_key="YOUR_API_KEY",                  # Clé WebService
        timeout=30,
        language_id=1,  # Français
    )

    tenant_id = "tenant_demo_123"

    async with PrestaShopClient(config, tenant_id) as client:
        try:
            # Récupérer les 10 premiers produits
            response = await client.get_products(limit=10, offset=0)

            print(f"📦 {len(response.products)} produits récupérés")
            print(f"   Total estimé: {response.total}")
            print(f"   Encore des produits: {response.has_more}")
            print()

            for product in response.products:
                print(f"  [{product.id}] {product.name}")
                print(f"      Prix: {product.get_display_price()}€")
                print(f"      Stock: {product.quantity} ({product.status.value})")
                print(f"      Catégorie: {product.category_name}")
                print()

        except PrestaShopError as e:
            print(f"❌ Erreur PrestaShop: {e}")


async def example_get_single_product():
    """Exemple: Récupérer un produit spécifique."""

    config = PrestaShopClientConfig(
        shop_url="https://demo.prestashop.com",
        api_key="YOUR_API_KEY",
    )

    async with PrestaShopClient(config, "tenant_123") as client:
        try:
            product = await client.get_product(42)

            print(f"📦 Produit: {product.name}")
            print(f"   Référence: {product.reference}")
            print(f"   Prix HT: {product.price}€")
            print(f"   En stock: {product.is_in_stock}")
            print(f"   En promo: {product.is_on_sale}")
            print()

            # Texte pour indexation RAG
            print("📝 Texte pour indexation:")
            print(f"   {product.to_search_text()}")
            print()

            # Métadonnées pour ChromaDB
            print("🔍 Métadonnées embedding:")
            for key, value in product.to_embedding_metadata().items():
                print(f"   {key}: {value}")

        except PrestaShopNotFoundError:
            print("❌ Produit non trouvé")


async def example_get_categories():
    """Exemple: Récupérer les catégories."""

    config = PrestaShopClientConfig(
        shop_url="https://demo.prestashop.com",
        api_key="YOUR_API_KEY",
    )

    async with PrestaShopClient(config, "tenant_123") as client:
        response = await client.get_categories()

        print(f"📂 {len(response.categories)} catégories")
        print()

        # Afficher l'arborescence
        tree = response.get_tree()

        def print_category(cat: Category, indent: int = 0):
            prefix = "  " * indent
            status = "✅" if cat.active else "❌"
            print(f"{prefix}{status} [{cat.id}] {cat.name} (depth: {cat.level_depth})")

        # Afficher les catégories racines puis leurs enfants
        for cat in response.categories:
            if cat.level_depth == 1:  # Niveau 1 = enfants de root
                print_category(cat)
                # Afficher les sous-catégories
                for subcat in tree.get(cat.id, []):
                    print_category(subcat, indent=1)


async def example_pagination():
    """Exemple: Pagination pour récupérer tous les produits."""

    config = PrestaShopClientConfig(
        shop_url="https://demo.prestashop.com",
        api_key="YOUR_API_KEY",
    )

    async with PrestaShopClient(config, "tenant_123") as client:
        all_products = []
        offset = 0
        batch_size = 100

        while True:
            response = await client.get_products(limit=batch_size, offset=offset)
            all_products.extend(response.products)

            print(f"📥 Batch récupéré: {len(response.products)} produits (total: {len(all_products)})")

            if not response.has_more:
                break

            offset = response.next_offset

        print(f"\n✅ Total: {len(all_products)} produits récupérés")


async def example_health_check():
    """Exemple: Vérifier la connexion PrestaShop."""

    config = PrestaShopClientConfig(
        shop_url="https://demo.prestashop.com",
        api_key="YOUR_API_KEY",
    )

    async with PrestaShopClient(config, "tenant_123") as client:
        is_connected = await client.check_connection()

        if is_connected:
            print("✅ Connexion PrestaShop OK")
        else:
            print("❌ Connexion PrestaShop échouée")


def example_create_product_manually():
    """Exemple: Créer un objet Product manuellement (pour tests)."""

    product = Product(
        id=1,
        name="T-shirt Premium",
        reference="TSH-001",
        price=Decimal("29.99"),
        price_tax_incl=Decimal("35.99"),
        quantity=50,
        category_id=5,
        category_name="Vêtements",
        description_short="Un super t-shirt en coton bio",
        manufacturer_name="EcoWear",
        tags=["coton", "bio", "été"],
        features={"Couleur": "Bleu", "Taille": "M", "Matière": "Coton bio"},
        active=True,
    )

    print(f"📦 Produit créé: {product.name}")
    print(f"   En stock: {product.is_in_stock}")
    print(f"   Status: {product.status.value}")
    print()
    print("📝 Texte indexation:")
    print(f"   {product.to_search_text()}")


if __name__ == "__main__":
    print("=" * 60)
    print("EXEMPLES CLIENT PRESTASHOP")
    print("=" * 60)
    print()

    # Exemple synchrone
    print("1️⃣ Création manuelle de produit:")
    print("-" * 40)
    example_create_product_manually()
    print()

    # Note: Les exemples async nécessitent une vraie configuration PrestaShop
    print("2️⃣ Pour les exemples async, configurez:")
    print("   - shop_url: URL de votre PrestaShop")
    print("   - api_key: Clé API WebService")
    print()
    print("Puis décommentez et exécutez:")
    print("   asyncio.run(example_get_products())")
    print("   asyncio.run(example_get_categories())")
    print("   asyncio.run(example_health_check())")

