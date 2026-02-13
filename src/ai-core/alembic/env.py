"""
Alembic Environment Configuration

Ce fichier configure l'environnement Alembic pour les migrations.
Charge automatiquement les settings depuis l'application.
"""

import os
import sys
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# Ajouter le répertoire parent au path pour importer l'app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ============================================================================
# CHARGER LES VARIABLES D'ENVIRONNEMENT AVANT LES IMPORTS
# ============================================================================
# Ces valeurs par défaut permettent d'exécuter alembic sans fichier .env
# En production, utiliser de vraies valeurs via variables d'environnement
os.environ.setdefault('DB_HOST', 'localhost')
os.environ.setdefault('DB_PORT', '5432')
os.environ.setdefault('DB_NAME', 'saas_ecommerce')
os.environ.setdefault('DB_USER', 'saas_user')
os.environ.setdefault('DB_PASSWORD', 'test_password_123')
os.environ.setdefault('SECURITY_JWT_SECRET_KEY', 'dev_jwt_secret_key_32_characters_minimum_for_alembic')
os.environ.setdefault('LLM_PROVIDER', 'mock')

# Import configuration et modèles (APRÈS avoir configuré les variables d'env)
from app.core.config.settings import settings
from app.infrastructure.database.base import Base

# Import tous les modèles pour qu'ils soient enregistrés dans Base.metadata
# Nouveaux modèles (propres)
from app.infrastructure.database.models import (
    Tenant,
    Conversation,
    Message,
    Rule,
    Coupon,
    AnalyticsEvent,
)

# Legacy models (pour compatibilité)
from app.infrastructure.database.models.product import ProductModel

# Configuration Alembic
config = context.config

# Configuration du logging depuis alembic.ini
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata pour autogenerate
target_metadata = Base.metadata


def get_url() -> str:
    """
    Retourne l'URL de la base de données.
    Priorité: variable d'environnement > settings
    """
    return os.environ.get("DATABASE_URL", settings.database.sync_url)


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well. By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.
    """
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

