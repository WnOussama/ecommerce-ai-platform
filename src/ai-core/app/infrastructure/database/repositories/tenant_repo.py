"""
Tenant Repository - CRUD operations pour les tenants.

Contrairement aux autres repositories, celui-ci n'est PAS filtré par
tenant_id : c'est lui qui résout l'identité du tenant (par API key ou
par token de vérification), donc il opère avant qu'un tenant_id existe.
"""

import hashlib
import logging
import secrets
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config.settings import settings
from app.core.security.secret_box import encrypt_secret
from app.infrastructure.database.models.tenant import Tenant

logger = logging.getLogger(__name__)

API_KEY_RANDOM_BYTES = 32
VERIFICATION_TOKEN_BYTES = 32
HMAC_SECRET_BYTES = 48


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def generate_api_key() -> str:
    """Génère une clé API brute (jamais stockée telle quelle)."""
    return f"{settings.security.api_key_prefix}{secrets.token_urlsafe(API_KEY_RANDOM_BYTES)}"


def generate_hmac_secret() -> str:
    """
    Génère le secret HMAC brut utilisé pour signer les requêtes (voir
    app/core/security/api_key_security.py). Stocké chiffré (pas hashé) -
    la vérification d'une signature a besoin du secret en clair.
    """
    return secrets.token_urlsafe(HMAC_SECRET_BYTES)


def generate_verification_token() -> str:
    """Génère un token de vérification email brut (jamais stocké tel quel)."""
    return secrets.token_urlsafe(VERIFICATION_TOKEN_BYTES)


class TenantRepository:
    """Repository pour la table tenants (création, vérification, auth par clé API)."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, tenant_id: UUID) -> Optional[Tenant]:
        result = await self._session.execute(select(Tenant).where(Tenant.id == tenant_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Optional[Tenant]:
        result = await self._session.execute(select(Tenant).where(Tenant.email == email))
        return result.scalar_one_or_none()

    async def get_by_api_key_hash(self, api_key_hash: str) -> Optional[Tenant]:
        result = await self._session.execute(
            select(Tenant).where(Tenant.api_key_hash == api_key_hash)
        )
        return result.scalar_one_or_none()

    async def get_by_verification_token_hash(self, token_hash: str) -> Optional[Tenant]:
        result = await self._session.execute(
            select(Tenant).where(Tenant.verification_token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def create_pending(self, name: str, email: str, slug: str) -> tuple[Tenant, str]:
        """
        Crée un tenant en attente de vérification email.

        Returns:
            (tenant, raw_verification_token) - le token brut n'est jamais
            persisté, seul son hash l'est ; il doit être envoyé par email
            immédiatement par l'appelant.
        """
        raw_token = generate_verification_token()

        tenant = Tenant(
            name=name,
            slug=slug,
            email=email,
            is_verified=False,
            is_active=False,
            verification_token_hash=_hash(raw_token),
            verification_sent_at=datetime.now(timezone.utc),
            settings={"plan": "starter"},
        )

        self._session.add(tenant)
        await self._session.flush()
        await self._session.refresh(tenant)

        logger.info(
            "Pending tenant created, awaiting email verification",
            extra={"tenant_id": str(tenant.id), "slug": slug},
        )

        return tenant, raw_token

    async def activate_and_issue_api_key(self, tenant: Tenant) -> tuple[str, str]:
        """
        Vérifie l'email d'un tenant et émet sa clé API réelle + son secret HMAC.

        Returns:
            (raw_api_key, raw_hmac_secret) - affichés une seule fois, seuls
            leur hash / forme chiffrée sont stockés. The caller must sign
            requests with the secret (see APIClientSigner) - the API key
            alone is no longer sufficient to authenticate.
        """
        raw_api_key = generate_api_key()
        raw_hmac_secret = generate_hmac_secret()

        tenant.is_verified = True
        tenant.is_active = True
        tenant.verified_at = datetime.now(timezone.utc)
        tenant.api_key_hash = _hash(raw_api_key)
        tenant.hmac_secret_encrypted = encrypt_secret(raw_hmac_secret)
        tenant.verification_token_hash = None

        await self._session.flush()
        await self._session.refresh(tenant)

        logger.info(
            "Tenant email verified, API key issued",
            extra={"tenant_id": str(tenant.id)},
        )

        return raw_api_key, raw_hmac_secret

    async def rotate_api_key(self, tenant: Tenant) -> tuple[str, str]:
        """Génère une nouvelle clé API + secret HMAC pour un tenant déjà vérifié."""
        raw_api_key = generate_api_key()
        raw_hmac_secret = generate_hmac_secret()
        tenant.api_key_hash = _hash(raw_api_key)
        tenant.hmac_secret_encrypted = encrypt_secret(raw_hmac_secret)

        await self._session.flush()
        await self._session.refresh(tenant)

        logger.info("API key rotated", extra={"tenant_id": str(tenant.id)})

        return raw_api_key, raw_hmac_secret

    async def update_settings(self, tenant: Tenant, settings_update: dict) -> Tenant:
        merged = dict(tenant.settings or {})
        merged.update(settings_update)
        tenant.settings = merged

        await self._session.flush()
        await self._session.refresh(tenant)

        return tenant
