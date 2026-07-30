"""add_tenant_email_verification

Revision ID: 92b71ac1a0a2
Revises: 41a15dea72c1
Create Date: 2026-07-30 08:08:12.005119+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '92b71ac1a0a2'
down_revision: Union[str, None] = '41a15dea72c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("email", sa.String(length=255), nullable=True))
    op.add_column(
        "tenants",
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "tenants", sa.Column("verification_token_hash", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "tenants", sa.Column("verification_sent_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("tenants", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_unique_constraint("uq_tenants_email", "tenants", ["email"])
    op.create_index(
        "idx_tenant_verification_token_hash", "tenants", ["verification_token_hash"]
    )


def downgrade() -> None:
    op.drop_index("idx_tenant_verification_token_hash", table_name="tenants")
    op.drop_constraint("uq_tenants_email", "tenants", type_="unique")
    op.drop_column("tenants", "verified_at")
    op.drop_column("tenants", "verification_sent_at")
    op.drop_column("tenants", "verification_token_hash")
    op.drop_column("tenants", "is_verified")
    op.drop_column("tenants", "email")

