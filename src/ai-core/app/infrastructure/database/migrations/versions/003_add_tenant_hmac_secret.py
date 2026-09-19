"""
Migration: Secret HMAC chiffré par tenant (signature des requêtes API)

Ajoute tenants.hmac_secret_encrypted, généré par
TenantRepository.activate_and_issue_api_key / rotate_api_key et vérifié
par TenantContextMiddleware (voir app/core/security/api_key_security.py).
Chiffré (pas hashé) car la vérification d'une signature HMAC a besoin du
secret en clair.
"""

import sqlalchemy as sa

from alembic import op

# Revision identifiers
revision = "003_add_tenant_hmac_secret"
down_revision = "002_add_tenant_composite_indexes"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "tenants",
        sa.Column("hmac_secret_encrypted", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column("tenants", "hmac_secret_encrypted")
