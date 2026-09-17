"""add_faq_items

Revision ID: a1f4c7d92b3e
Revises: 5605aebc6eae
Create Date: 2026-09-17 19:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1f4c7d92b3e'
down_revision: Union[str, None] = '5605aebc6eae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'faq_items',
        sa.Column('tenant_id', sa.UUID(), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column('category', sa.String(length=100), nullable=False),
        sa.Column('source_cms_id', sa.Integer(), nullable=False),
        sa.Column('source_title', sa.String(length=255), nullable=False),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_faq_tenant_id', 'faq_items', ['tenant_id'], unique=False)
    op.create_index(
        'idx_faq_tenant_source', 'faq_items', ['tenant_id', 'source_cms_id'], unique=False
    )
    op.create_index(op.f('ix_faq_items_created_at'), 'faq_items', ['created_at'], unique=False)
    op.create_index(op.f('ix_faq_items_tenant_id'), 'faq_items', ['tenant_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_faq_items_tenant_id'), table_name='faq_items')
    op.drop_index(op.f('ix_faq_items_created_at'), table_name='faq_items')
    op.drop_index('idx_faq_tenant_source', table_name='faq_items')
    op.drop_index('idx_faq_tenant_id', table_name='faq_items')
    op.drop_table('faq_items')
