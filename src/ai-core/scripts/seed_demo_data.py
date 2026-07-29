"""
Script de peuplement pour l'environnement de démonstration.

Crée un tenant réaliste, un petit catalogue produits, et indexe le
catalogue dans le vector store RAG (ChromaDB en conteneur, InMemory en
fallback natif si ChromaDB n'est pas importable dans l'environnement
courant - voir app/services/rag/factory.py::get_vector_store).

Usage (depuis src/ai-core, dans le conteneur ai-core ou avec les
variables DB_* pointant vers une instance Postgres accessible):

    python -m scripts.seed_demo_data
"""

import asyncio
import logging
import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.database.connection import async_engine
from app.services.catalog.repository import ProductData, ProductRepository
from app.services.rag.factory import get_retrieval_service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("seed_demo_data")


DEMO_TENANT = {
    "name": "Boutique Running Démo",
    "slug": "boutique-running-demo",
}

DEMO_PRODUCTS = [
    ProductData(
        external_id=1,
        name="Chaussure de running Pulse X",
        price=Decimal("89.99"),
        quantity=42,
        reference="RUN-PULSE-X",
        description_short="Chaussure de running légère, amorti réactif, pour asphalte.",
        category_name="Chaussures running",
        manufacturer_name="Velocia",
        extra_data={"tailles": ["40", "41", "42", "43", "44"], "tags": ["running", "route"]},
    ),
    ProductData(
        external_id=2,
        name="Chaussure de trail Ridge Pro",
        price=Decimal("119.00"),
        quantity=17,
        reference="TRAIL-RIDGE-PRO",
        description_short="Chaussure de trail à crampons profonds, protection renforcée.",
        category_name="Chaussures trail",
        manufacturer_name="Velocia",
        extra_data={"tailles": ["41", "42", "43", "44", "45"], "tags": ["trail", "montagne"]},
    ),
    ProductData(
        external_id=3,
        name="Short de running ventilé",
        price=Decimal("34.50"),
        quantity=60,
        reference="SHORT-VENT-01",
        description_short="Short léger avec doublure intégrée et poche zippée.",
        category_name="Vêtements running",
        manufacturer_name="Velocia",
        extra_data={"tailles": ["S", "M", "L", "XL"], "tags": ["running", "été"]},
    ),
    ProductData(
        external_id=4,
        name="Montre GPS multisport Pace 5",
        price=Decimal("249.00"),
        quantity=8,
        reference="WATCH-PACE-5",
        description_short="Montre GPS avec suivi fréquence cardiaque et VO2max.",
        category_name="Accessoires",
        manufacturer_name="Chronos",
        extra_data={"tags": ["gps", "montre", "cardio"]},
    ),
    ProductData(
        external_id=5,
        name="Ceinture porte-gourdes hydratation",
        price=Decimal("29.90"),
        quantity=25,
        reference="BELT-HYDRO-02",
        description_short="Ceinture réglable avec 2 flasques souples 250ml incluses.",
        category_name="Accessoires",
        manufacturer_name="Velocia",
        extra_data={"tags": ["hydratation", "trail", "ultra"]},
    ),
]


async def seed_tenant(session: AsyncSession) -> str:
    """Crée (ou réutilise) le tenant de démonstration et retourne son id."""
    from sqlalchemy import select

    from app.infrastructure.database.models.tenant import Tenant

    existing = await session.execute(select(Tenant).where(Tenant.slug == DEMO_TENANT["slug"]))
    tenant = existing.scalar_one_or_none()

    if tenant is not None:
        logger.info("Tenant démo déjà présent: %s", tenant.id)
        return str(tenant.id)

    tenant = Tenant(
        id=uuid.uuid4(),
        name=DEMO_TENANT["name"],
        slug=DEMO_TENANT["slug"],
        is_active=True,
        settings={},
    )
    session.add(tenant)
    await session.commit()
    logger.info("Tenant démo créé: %s (%s)", tenant.id, tenant.name)
    return str(tenant.id)


async def seed_products(session: AsyncSession, tenant_id: str) -> None:
    repo = ProductRepository(session)
    created, updated = await repo.upsert_products_bulk(tenant_id, DEMO_PRODUCTS)
    logger.info("Produits: %d créés, %d mis à jour", created, updated)


async def index_catalog(session: AsyncSession, tenant_id: str) -> None:
    from app.services.rag.factory import get_embedding_service, get_vector_store
    from app.services.rag.product_indexer import ProductIndexer

    repo = ProductRepository(session)
    indexer = ProductIndexer(
        repository=repo,
        embedding_service=get_embedding_service(),
        vector_store=get_vector_store(),
    )
    result = await indexer.index_all_products(tenant_id=tenant_id)
    logger.info(
        "Indexation terminée: statut=%s total=%d indexés=%d skippés=%d erreurs=%d",
        result.status,
        result.total_products,
        result.total_indexed,
        result.total_skipped,
        len(result.errors),
    )


async def verify_retrieval(tenant_id: str) -> None:
    """
    Vérifie que le pipeline embed -> store -> query -> rank fonctionne
    de bout en bout.

    NOTE: en mode LLM_PROVIDER=mock, MockEmbeddingService hashe la chaîne
    complète pour dériver un vecteur pseudo-aléatoire (voir
    embedding_service.py::_generate_deterministic_embedding). Ce vecteur
    n'a AUCUN sens sémantique - deux textes proches en sens n'ont pas de
    vecteurs proches, seul un texte identique produit un vecteur identique.
    On interroge donc avec le texte de recherche EXACT du produit "Ridge
    Pro" (voir ProductIndexer._product_to_search_text) pour prouver que
    la mécanique de bout en bout fonctionne - PAS que la recherche est
    sémantiquement pertinente. Une vraie démonstration de pertinence
    nécessite un service d'embedding réel (OpenAI ou un modèle local),
    absent en mode mock.
    """
    retrieval_service = get_retrieval_service()
    exact_text_query = (
        "Chaussure de trail Ridge Pro | Chaussure de trail à crampons profonds, "
        "protection renforcée. | Catégorie: Chaussures trail | Marque: Velocia | "
        "Référence: TRAIL-RIDGE-PRO | Tags: trail, montagne"
    )
    rag_result = await retrieval_service.search_products(
        query=exact_text_query,
        tenant_id=tenant_id,
        top_k=3,
    )
    logger.info(
        "Vérification pipeline RAG (requête = texte exact indexé): %d résultat(s)",
        len(rag_result.products),
    )
    for p in rag_result.products:
        logger.info("  - %s (score=%.3f)", p.name, p.similarity_score)
    if not rag_result.products:
        logger.warning(
            "Pipeline RAG: 0 résultat même avec le texte exact - vérifier le vector store"
        )


async def main() -> None:
    session_factory = async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        tenant_id = await seed_tenant(session)
        await seed_products(session, tenant_id)
        await index_catalog(session, tenant_id)

    await verify_retrieval(tenant_id)

    print(f"\nTenant de démonstration prêt: {tenant_id}")
    print(f'Header pour tester l\'API en dev: -H "X-Tenant-ID: {tenant_id}"')


if __name__ == "__main__":
    asyncio.run(main())
