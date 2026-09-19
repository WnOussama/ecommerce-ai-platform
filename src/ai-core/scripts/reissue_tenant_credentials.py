"""
Réémet la clé API ET le secret de signature d'un tenant (récupération admin).

Un tenant dont la clé a été émise avant l'ajout de la signature HMAC n'a pas de
secret: toutes ses requêtes sont refusées, et il ne peut pas non plus appeler
/tenants/current/api-keys/rotate (cette route exige une requête authentifiée).
Ce script est le chemin de récupération, à lancer côté serveur:

    python -m scripts.reissue_tenant_credentials <tenant_id>

La nouvelle clé et le nouveau secret ne sont affichés qu'une fois. Le cache
d'authentification d'ai-core est en mémoire (sans TTL): redémarrez le service
pour que l'ancienne clé cesse d'être acceptée.
"""

import asyncio
import sys
import uuid

from app.infrastructure.database.connection import AsyncSessionLocal
from app.infrastructure.database.repositories.tenant_repo import TenantRepository


async def reissue(tenant_id: uuid.UUID) -> int:
    async with AsyncSessionLocal() as session:
        repo = TenantRepository(session)
        tenant = await repo.get_by_id(tenant_id)
        if tenant is None:
            print(f"Tenant {tenant_id} introuvable", file=sys.stderr)
            return 1

        raw_api_key, raw_secret = await repo.rotate_api_key(tenant)
        await session.commit()

    print(f"Tenant : {tenant.name} ({tenant.id})")
    print(f"API key: {raw_api_key}")
    print(f"Secret : {raw_secret}")
    print("Conservez-les maintenant: ils ne seront plus jamais affichés.")
    print("Redémarrez ai-core pour invalider l'ancienne clé encore en cache.")
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    try:
        tenant_id = uuid.UUID(sys.argv[1])
    except ValueError:
        print("tenant_id doit être un UUID", file=sys.stderr)
        return 2
    return asyncio.run(reissue(tenant_id))


if __name__ == "__main__":
    raise SystemExit(main())
