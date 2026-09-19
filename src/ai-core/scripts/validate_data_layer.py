#!/usr/bin/env python3
"""
Script de validation du Data Layer avant migration Alembic.
Exécuter avec les variables d'environnement configurées.
"""
import os
os.environ.setdefault('DB_PASSWORD', 'test_password_123')
os.environ.setdefault('SECURITY_JWT_SECRET_KEY', 'test_jwt_secret_key_32_characters_minimum')

from app.infrastructure.database.models import (
    Base, Tenant, Conversation, Message, Rule, Coupon, AnalyticsEvent
)

def validate_models():
    """Valide que tous les modèles sont correctement configurés."""
    errors = []

    # 1. Vérifier que Message a tenant_id
    if 'tenant_id' not in [c.name for c in Message.__table__.columns]:
        errors.append("❌ Message: tenant_id manquant")
    else:
        print("✅ Message: tenant_id présent")

    # 2. Vérifier les FK sur Message
    msg_fks = [fk.column.table.name for fk in Message.__table__.foreign_keys]
    if 'tenants' not in msg_fks:
        errors.append("❌ Message: FK vers tenants manquante")
    else:
        print("✅ Message: FK vers tenants présente")
    if 'conversations' not in msg_fks:
        errors.append("❌ Message: FK vers conversations manquante")
    else:
        print("✅ Message: FK vers conversations présente")

    # 3. Vérifier les index sur Message
    msg_indexes = [idx.name for idx in Message.__table__.indexes]
    required_indexes = ['idx_message_tenant_id', 'idx_message_conversation_created']
    for idx in required_indexes:
        if idx in msg_indexes:
            print(f"✅ Message: index {idx} présent")
        else:
            errors.append(f"❌ Message: index {idx} manquant")

    # 4. Vérifier CHECK constraint sur Message
    msg_constraints = [c.name for c in Message.__table__.constraints if c.name]
    if 'ck_message_role' in msg_constraints:
        print("✅ Message: CHECK constraint ck_message_role présente")
    else:
        errors.append("❌ Message: CHECK constraint ck_message_role manquante")

    # 5. Vérifier unique constraint sur Coupon
    coupon_indexes = [idx.name for idx in Coupon.__table__.indexes]
    if 'idx_coupon_code' in coupon_indexes:
        print("✅ Coupon: unique index tenant_id+code présent")
    else:
        errors.append("❌ Coupon: unique index tenant_id+code manquant")

    # 6. Vérifier index GIN sur AnalyticsEvent
    analytics_indexes = [idx.name for idx in AnalyticsEvent.__table__.indexes]
    if 'idx_analytics_payload_gin' in analytics_indexes:
        print("✅ AnalyticsEvent: index GIN sur payload présent")
    else:
        errors.append("❌ AnalyticsEvent: index GIN sur payload manquant")

    # 7. Vérifier que tous les modèles ont tenant_id
    models_with_tenant = []
    for model_name, model in [('Conversation', Conversation), ('Message', Message),
                               ('Rule', Rule), ('Coupon', Coupon), ('AnalyticsEvent', AnalyticsEvent)]:
        cols = [c.name for c in model.__table__.columns]
        if 'tenant_id' in cols:
            models_with_tenant.append(model_name)
            print(f"✅ {model_name}: tenant_id présent")
        else:
            errors.append(f"❌ {model_name}: tenant_id manquant")

    # Résumé
    print("\n" + "="*50)
    if errors:
        print("❌ VALIDATION ÉCHOUÉE")
        for err in errors:
            print(f"  {err}")
        return False
    else:
        print("✅ VALIDATION RÉUSSIE - Prêt pour migration Alembic")
        return True

if __name__ == "__main__":
    validate_models()

