"""
Database Base - SQLAlchemy Declarative Base

Ce module définit la Base commune pour tous les modèles SQLAlchemy.
Centralisé pour éviter les imports circulaires.
"""

from sqlalchemy.orm import declarative_base

# Base declarative commune pour tous les modèles
Base = declarative_base()

