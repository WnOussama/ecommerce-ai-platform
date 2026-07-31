"""add_tenant_api_key_hash_index

Revision ID: 5605aebc6eae
Revises: 92b71ac1a0a2
Create Date: 2026-07-31 02:37:17.532452+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5605aebc6eae'
down_revision: Union[str, None] = '92b71ac1a0a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Every authenticated request looks up
    # WHERE api_key_hash = :hash (TenantContextMiddleware._validate_and_get_tenant)
    # - without an index this is a full table scan on every single API call.
    op.create_index("idx_tenant_api_key_hash", "tenants", ["api_key_hash"])


def downgrade() -> None:
    op.drop_index("idx_tenant_api_key_hash", table_name="tenants")

