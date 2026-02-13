"""
Database Base - SQLAlchemy Declarative Base + Mixins

Ce module définit la Base commune et les mixins réutilisables
pour tous les modèles SQLAlchemy.

Architecture: FastAPI est le SEUL owner de cette base de données.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, declared_attr
from sqlalchemy.sql import func

# Base declarative commune pour tous les modèles
Base = declarative_base()


def generate_uuid() -> uuid.UUID:
    """Génère un UUID v4."""
    return uuid.uuid4()


class TimestampMixin:
    """
    Mixin pour ajouter created_at et updated_at automatiques.

    Usage:
        class MyModel(Base, TimestampMixin):
            ...
    """
    created_at = Column(
        DateTime(timezone=True),
        default=func.now(),
        nullable=False,
        index=True,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=func.now(),
        onupdate=func.now(),
        nullable=True,
    )


class UUIDMixin:
    """
    Mixin pour ajouter un UUID comme clé primaire.

    Usage:
        class MyModel(Base, UUIDMixin):
            ...
    """
    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=generate_uuid,
        nullable=False,
    )


class SoftDeleteMixin:
    """
    Mixin pour soft delete (marquer comme supprimé sans supprimer).

    Usage:
        class MyModel(Base, SoftDeleteMixin):
            ...
    """
    deleted_at = Column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


