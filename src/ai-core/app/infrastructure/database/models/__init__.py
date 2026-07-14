"""
Database Models - SQLAlchemy ORM Models

Ce module exporte tous les modèles SQLAlchemy pour l'application.

Architecture:
- FastAPI est le SEUL owner de cette base de données
- Laravel communique via REST API uniquement
- Tous les modèles utilisent UUID natif PostgreSQL
"""

# Base et mixins
from app.infrastructure.database.base import (
    Base,
    SoftDeleteMixin,
    TimestampMixin,
    UUIDMixin,
    generate_uuid,
)
from app.infrastructure.database.models.analytics import AnalyticsEvent
from app.infrastructure.database.models.conversation import Conversation, ConversationStatus
from app.infrastructure.database.models.coupon import Coupon, CouponStatus
from app.infrastructure.database.models.message import Message, MessageRole

# Legacy models (à migrer progressivement)
# Ces modèles seront dépréciés et remplacés par les nouveaux
from app.infrastructure.database.models.product import ProductModel
from app.infrastructure.database.models.rule import Rule

# Modèles principaux (nouveaux, propres)
from app.infrastructure.database.models.tenant import Tenant

__all__ = [
    # Base
    "Base",
    "UUIDMixin",
    "TimestampMixin",
    "SoftDeleteMixin",
    "generate_uuid",
    # New models
    "Tenant",
    "Conversation",
    "ConversationStatus",
    "Message",
    "MessageRole",
    "Rule",
    "Coupon",
    "CouponStatus",
    "AnalyticsEvent",
    # Legacy (deprecated)
    "ProductModel",
]
