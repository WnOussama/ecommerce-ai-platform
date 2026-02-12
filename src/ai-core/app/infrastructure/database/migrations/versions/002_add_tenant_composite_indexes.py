"""
Migration: Ajout des index composites pour multi-tenant

Ces index sont CRITIQUES pour:
1. Performance des queries filtrées par tenant_id
2. Garantir l'isolation logique efficace
3. Éviter les full table scans

Indexes ajoutés:
- (tenant_id, id) sur toutes les tables - lookup rapide
- (tenant_id, created_at) - tri chronologique
- (tenant_id, status) - filtrage par état
- (tenant_id, foreign_key) - jointures efficaces
"""

from alembic import op
import sqlalchemy as sa


# Revision identifiers
revision = '002_add_tenant_composite_indexes'
down_revision = '001_initial'  # Ajuster selon votre migration précédente
branch_labels = None
depends_on = None


def upgrade():
    """Ajoute les index composites pour multi-tenant"""

    # =========================================================================
    # CUSTOMERS
    # =========================================================================

    # Index principal: tenant + id (pour get_by_id)
    op.create_index(
        'idx_customer_tenant_id',
        'customers',
        ['tenant_id', 'id'],
        unique=False,
    )

    # Index: tenant + created_at (pour listing chronologique)
    op.create_index(
        'idx_customer_tenant_created',
        'customers',
        ['tenant_id', 'created_at'],
        unique=False,
    )

    # Index: tenant + segment + loyalty_score (pour segmentation)
    op.create_index(
        'idx_customer_tenant_segment_loyalty',
        'customers',
        ['tenant_id', 'segment', 'loyalty_score'],
        unique=False,
    )

    # Index: tenant + last_order_date (pour clients à risque)
    op.create_index(
        'idx_customer_tenant_last_order',
        'customers',
        ['tenant_id', 'last_order_date'],
        unique=False,
    )

    # =========================================================================
    # CONVERSATIONS
    # =========================================================================

    # Index principal: tenant + id
    op.create_index(
        'idx_conv_tenant_id',
        'conversations',
        ['tenant_id', 'id'],
        unique=False,
    )

    # Index: tenant + customer_id (pour historique client)
    op.create_index(
        'idx_conv_tenant_customer',
        'conversations',
        ['tenant_id', 'customer_id'],
        unique=False,
    )

    # Index: tenant + status + created_at (pour conversations actives récentes)
    op.create_index(
        'idx_conv_tenant_status_date',
        'conversations',
        ['tenant_id', 'status', 'created_at'],
        unique=False,
    )

    # Index: tenant + resolved_by_ai (pour stats résolution)
    op.create_index(
        'idx_conv_tenant_resolved',
        'conversations',
        ['tenant_id', 'resolved_by_ai'],
        unique=False,
    )

    # =========================================================================
    # MESSAGES
    # =========================================================================

    # Index principal: tenant + id
    op.create_index(
        'idx_msg_tenant_id',
        'messages',
        ['tenant_id', 'id'],
        unique=False,
    )

    # Index: tenant + conversation_id + created_at (pour historique ordonné)
    op.create_index(
        'idx_msg_tenant_conv_date',
        'messages',
        ['tenant_id', 'conversation_id', 'created_at'],
        unique=False,
    )

    # =========================================================================
    # COUPONS
    # =========================================================================

    # Index principal: tenant + id
    op.create_index(
        'idx_coupon_tenant_id',
        'coupons',
        ['tenant_id', 'id'],
        unique=False,
    )

    # Index: tenant + code (lookup par code - doit être unique par tenant)
    op.create_index(
        'idx_coupon_tenant_code',
        'coupons',
        ['tenant_id', 'code'],
        unique=True,
    )

    # Index: tenant + customer_id (coupons par client)
    op.create_index(
        'idx_coupon_tenant_customer',
        'coupons',
        ['tenant_id', 'customer_id'],
        unique=False,
    )

    # Index: tenant + status + valid_until (coupons actifs valides)
    op.create_index(
        'idx_coupon_tenant_status_validity',
        'coupons',
        ['tenant_id', 'status', 'valid_until'],
        unique=False,
    )

    # =========================================================================
    # ADMIN_ACTIONS
    # =========================================================================

    # Index principal: tenant + id
    op.create_index(
        'idx_admin_action_tenant_id',
        'admin_actions',
        ['tenant_id', 'id'],
        unique=False,
    )

    # Index: tenant + status (actions pending)
    op.create_index(
        'idx_admin_action_tenant_status',
        'admin_actions',
        ['tenant_id', 'status'],
        unique=False,
    )

    # Index: tenant + action_type + created_at (historique par type)
    op.create_index(
        'idx_admin_action_tenant_type_date',
        'admin_actions',
        ['tenant_id', 'action_type', 'created_at'],
        unique=False,
    )

    # Index: tenant + executed_by (audit par utilisateur)
    op.create_index(
        'idx_admin_action_tenant_user',
        'admin_actions',
        ['tenant_id', 'executed_by'],
        unique=False,
    )

    # =========================================================================
    # LLM_USAGE (si existe)
    # =========================================================================

    # Vérifier si la table existe avant de créer l'index
    try:
        op.create_index(
            'idx_llm_usage_tenant_date',
            'llm_usage',
            ['tenant_id', 'created_at'],
            unique=False,
        )
    except Exception:
        pass  # Table n'existe peut-être pas


def downgrade():
    """Supprime les index composites"""

    # Customers
    op.drop_index('idx_customer_tenant_id', table_name='customers')
    op.drop_index('idx_customer_tenant_created', table_name='customers')
    op.drop_index('idx_customer_tenant_segment_loyalty', table_name='customers')
    op.drop_index('idx_customer_tenant_last_order', table_name='customers')

    # Conversations
    op.drop_index('idx_conv_tenant_id', table_name='conversations')
    op.drop_index('idx_conv_tenant_customer', table_name='conversations')
    op.drop_index('idx_conv_tenant_status_date', table_name='conversations')
    op.drop_index('idx_conv_tenant_resolved', table_name='conversations')

    # Messages
    op.drop_index('idx_msg_tenant_id', table_name='messages')
    op.drop_index('idx_msg_tenant_conv_date', table_name='messages')

    # Coupons
    op.drop_index('idx_coupon_tenant_id', table_name='coupons')
    op.drop_index('idx_coupon_tenant_code', table_name='coupons')
    op.drop_index('idx_coupon_tenant_customer', table_name='coupons')
    op.drop_index('idx_coupon_tenant_status_validity', table_name='coupons')

    # Admin Actions
    op.drop_index('idx_admin_action_tenant_id', table_name='admin_actions')
    op.drop_index('idx_admin_action_tenant_status', table_name='admin_actions')
    op.drop_index('idx_admin_action_tenant_type_date', table_name='admin_actions')
    op.drop_index('idx_admin_action_tenant_user', table_name='admin_actions')

    # LLM Usage
    try:
        op.drop_index('idx_llm_usage_tenant_date', table_name='llm_usage')
    except Exception:
        pass

