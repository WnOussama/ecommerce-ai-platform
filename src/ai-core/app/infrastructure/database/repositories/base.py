"""
Base Repository avec isolation multi-tenant OBLIGATOIRE.

Ce module impose que TOUTES les queries passent par tenant_id.
Impossible d'oublier le filtrage tenant.

Architecture:
┌─────────────────────────────────────────────────────────────────────────────────┐
│                     MULTI-TENANT REPOSITORY PATTERN                              │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│  ❌ INTERDIT: session.query(Customer).filter(Customer.id == id)                 │
│                                                                                  │
│  ✅ OBLIGATOIRE: repository.get_by_id(id)  # tenant_id auto-injecté             │
│                                                                                  │
│  Avantages:                                                                      │
│  • Impossible d'oublier tenant_id                                               │
│  • Logging automatique des accès                                                │
│  • Validation à chaque query                                                    │
│  • Tests unitaires plus simples                                                  │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
"""

import logging
from abc import ABC
from dataclasses import dataclass
from typing import Any, Dict, Generic, List, Optional, Tuple, Type, TypeVar

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Type variable pour le modèle SQLAlchemy
T = TypeVar("T")


# =============================================================================
# EXCEPTIONS
# =============================================================================


class TenantIsolationError(Exception):
    """Erreur d'isolation multi-tenant"""

    pass


class TenantIdMissingError(TenantIsolationError):
    """tenant_id manquant dans une opération"""

    pass


class CrossTenantAccessError(TenantIsolationError):
    """Tentative d'accès à des données d'un autre tenant"""

    pass


# =============================================================================
# TENANT CONTEXT
# =============================================================================


@dataclass
class TenantContext:
    """Contexte tenant pour les opérations repository"""

    tenant_id: str
    user_id: Optional[str] = None  # Pour audit
    request_id: Optional[str] = None  # Pour tracing

    def __post_init__(self):
        if not self.tenant_id:
            raise TenantIdMissingError("tenant_id is required")


# =============================================================================
# BASE REPOSITORY
# =============================================================================


class TenantAwareRepository(Generic[T], ABC):
    """
    Repository de base avec isolation multi-tenant OBLIGATOIRE.

    TOUTES les queries sont automatiquement filtrées par tenant_id.
    Il est IMPOSSIBLE d'exécuter une query sans tenant_id.

    Usage:
        class CustomerRepository(TenantAwareRepository[CustomerModel]):
            model_class = CustomerModel

        repo = CustomerRepository(session, tenant_context)
        customers = await repo.get_all()  # Automatiquement filtré par tenant
    """

    # À définir dans les sous-classes
    model_class: Type[T]

    # Nom de la colonne tenant_id (par défaut "tenant_id")
    tenant_id_column: str = "tenant_id"

    def __init__(
        self,
        session: AsyncSession,
        tenant_context: TenantContext,
    ):
        """
        Initialise le repository avec une session et un contexte tenant.

        Args:
            session: Session SQLAlchemy async
            tenant_context: Contexte contenant le tenant_id (OBLIGATOIRE)
        """
        if not tenant_context or not tenant_context.tenant_id:
            raise TenantIdMissingError(
                f"TenantContext with tenant_id is required for {self.__class__.__name__}"
            )

        self._session = session
        self._tenant_context = tenant_context
        self._tenant_id = tenant_context.tenant_id

    @property
    def tenant_id(self) -> str:
        """Retourne le tenant_id courant (read-only)"""
        return self._tenant_id

    # =========================================================================
    # QUERY BUILDING (avec tenant_id automatique)
    # =========================================================================

    def _base_query(self):
        """
        Crée une query de base TOUJOURS filtrée par tenant_id.
        C'est le point d'entrée pour TOUTES les queries.
        """
        return select(self.model_class).where(
            getattr(self.model_class, self.tenant_id_column) == self._tenant_id
        )

    def _apply_tenant_filter(self, query):
        """Applique le filtre tenant à une query existante"""
        return query.where(getattr(self.model_class, self.tenant_id_column) == self._tenant_id)

    def _validate_entity_tenant(self, entity: T) -> None:
        """
        Valide qu'une entité appartient au tenant courant.
        Lève une exception si ce n'est pas le cas.
        """
        entity_tenant_id = getattr(entity, self.tenant_id_column, None)

        if entity_tenant_id != self._tenant_id:
            logger.error(
                "Cross-tenant access attempt detected",
                extra={
                    "expected_tenant": self._tenant_id,
                    "actual_tenant": entity_tenant_id,
                    "entity_type": self.model_class.__name__,
                    "entity_id": getattr(entity, "id", "unknown"),
                },
            )
            raise CrossTenantAccessError(
                f"Entity belongs to tenant {entity_tenant_id}, not {self._tenant_id}"
            )

    # =========================================================================
    # CRUD OPERATIONS
    # =========================================================================

    async def get_by_id(self, entity_id: str) -> Optional[T]:
        """
        Récupère une entité par ID (filtré par tenant).
        """
        query = self._base_query().where(getattr(self.model_class, "id") == entity_id)

        result = await self._session.execute(query)
        entity = result.scalar_one_or_none()

        if entity:
            self._log_access("get_by_id", entity_id)

        return entity

    async def get_all(
        self,
        offset: int = 0,
        limit: int = 100,
        order_by: Optional[str] = None,
        order_desc: bool = False,
    ) -> List[T]:
        """
        Récupère toutes les entités du tenant.
        """
        query = self._base_query()

        # Ordering
        if order_by:
            order_col = getattr(self.model_class, order_by, None)
            if order_col is not None:
                query = query.order_by(order_col.desc() if order_desc else order_col)

        # Pagination
        query = query.offset(offset).limit(limit)

        result = await self._session.execute(query)
        entities = result.scalars().all()

        self._log_access("get_all", f"count={len(entities)}")

        return list(entities)

    async def get_by_ids(self, entity_ids: List[str]) -> List[T]:
        """
        Récupère plusieurs entités par leurs IDs.
        """
        if not entity_ids:
            return []

        query = self._base_query().where(getattr(self.model_class, "id").in_(entity_ids))

        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def find_by(
        self,
        filters: Dict[str, Any],
        offset: int = 0,
        limit: int = 100,
    ) -> List[T]:
        """
        Recherche avec filtres (tenant_id automatiquement ajouté).

        Args:
            filters: Dict de {column_name: value}
        """
        query = self._base_query()

        for column_name, value in filters.items():
            column = getattr(self.model_class, column_name, None)
            if column is not None:
                if isinstance(value, list):
                    query = query.where(column.in_(value))
                else:
                    query = query.where(column == value)

        query = query.offset(offset).limit(limit)

        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def find_one_by(self, filters: Dict[str, Any]) -> Optional[T]:
        """Trouve une seule entité par filtres"""
        results = await self.find_by(filters, limit=1)
        return results[0] if results else None

    async def count(self, filters: Optional[Dict[str, Any]] = None) -> int:
        """Compte les entités (avec filtres optionnels)"""
        query = (
            select(func.count())
            .select_from(self.model_class)
            .where(getattr(self.model_class, self.tenant_id_column) == self._tenant_id)
        )

        if filters:
            for column_name, value in filters.items():
                column = getattr(self.model_class, column_name, None)
                if column is not None:
                    query = query.where(column == value)

        result = await self._session.execute(query)
        return result.scalar_one()

    async def exists(self, entity_id: str) -> bool:
        """Vérifie si une entité existe"""
        query = (
            select(func.count())
            .select_from(self.model_class)
            .where(
                and_(
                    getattr(self.model_class, self.tenant_id_column) == self._tenant_id,
                    getattr(self.model_class, "id") == entity_id,
                )
            )
        )

        result = await self._session.execute(query)
        return result.scalar_one() > 0

    # =========================================================================
    # WRITE OPERATIONS
    # =========================================================================

    async def create(self, entity: T) -> T:
        """
        Crée une nouvelle entité.
        Le tenant_id est automatiquement défini.
        """
        # Forcer le tenant_id
        setattr(entity, self.tenant_id_column, self._tenant_id)

        self._session.add(entity)
        await self._session.flush()
        await self._session.refresh(entity)

        self._log_access("create", getattr(entity, "id", "new"))

        return entity

    async def create_many(self, entities: List[T]) -> List[T]:
        """Crée plusieurs entités"""
        for entity in entities:
            setattr(entity, self.tenant_id_column, self._tenant_id)

        self._session.add_all(entities)
        await self._session.flush()

        for entity in entities:
            await self._session.refresh(entity)

        self._log_access("create_many", f"count={len(entities)}")

        return entities

    async def update(self, entity: T) -> T:
        """
        Met à jour une entité.
        Valide que l'entité appartient au tenant.
        """
        # Validation tenant
        self._validate_entity_tenant(entity)

        await self._session.flush()
        await self._session.refresh(entity)

        self._log_access("update", getattr(entity, "id", "unknown"))

        return entity

    async def update_by_id(
        self,
        entity_id: str,
        updates: Dict[str, Any],
    ) -> bool:
        """
        Met à jour une entité par ID (avec validation tenant).
        Retourne True si l'entité a été mise à jour.
        """
        # Interdire la modification du tenant_id
        if self.tenant_id_column in updates:
            raise TenantIsolationError("Cannot modify tenant_id")

        stmt = (
            update(self.model_class)
            .where(
                and_(
                    getattr(self.model_class, self.tenant_id_column) == self._tenant_id,
                    getattr(self.model_class, "id") == entity_id,
                )
            )
            .values(**updates)
        )

        result = await self._session.execute(stmt)

        self._log_access("update_by_id", entity_id)

        return result.rowcount > 0

    async def delete(self, entity: T) -> bool:
        """
        Supprime une entité.
        Valide que l'entité appartient au tenant.
        """
        self._validate_entity_tenant(entity)

        await self._session.delete(entity)

        self._log_access("delete", getattr(entity, "id", "unknown"))

        return True

    async def delete_by_id(self, entity_id: str) -> bool:
        """
        Supprime une entité par ID (avec validation tenant).
        """
        stmt = delete(self.model_class).where(
            and_(
                getattr(self.model_class, self.tenant_id_column) == self._tenant_id,
                getattr(self.model_class, "id") == entity_id,
            )
        )

        result = await self._session.execute(stmt)

        self._log_access("delete_by_id", entity_id)

        return result.rowcount > 0

    async def delete_many(self, filters: Dict[str, Any]) -> int:
        """
        Supprime plusieurs entités par filtres.
        Retourne le nombre d'entités supprimées.
        """
        stmt = delete(self.model_class).where(
            getattr(self.model_class, self.tenant_id_column) == self._tenant_id
        )

        for column_name, value in filters.items():
            column = getattr(self.model_class, column_name, None)
            if column is not None:
                stmt = stmt.where(column == value)

        result = await self._session.execute(stmt)

        self._log_access("delete_many", f"count={result.rowcount}")

        return result.rowcount

    # =========================================================================
    # LOGGING
    # =========================================================================

    def _log_access(self, operation: str, details: str) -> None:
        """Log les accès pour audit"""
        logger.debug(
            f"Repository access: {operation}",
            extra={
                "repository": self.__class__.__name__,
                "operation": operation,
                "details": details,
                "tenant_id": self._tenant_id,
                "user_id": self._tenant_context.user_id,
                "request_id": self._tenant_context.request_id,
            },
        )


# =============================================================================
# REPOSITORY FACTORY
# =============================================================================


class RepositoryFactory:
    """
    Factory pour créer des repositories avec le bon contexte tenant.
    Garantit que tous les repositories partagent la même session et contexte.
    """

    def __init__(
        self,
        session: AsyncSession,
        tenant_context: TenantContext,
    ):
        self._session = session
        self._tenant_context = tenant_context
        self._repositories: Dict[Type, TenantAwareRepository] = {}

    def get(self, repository_class: Type[TenantAwareRepository]) -> TenantAwareRepository:
        """
        Obtient une instance de repository.
        Les instances sont réutilisées dans la même transaction.
        """
        if repository_class not in self._repositories:
            self._repositories[repository_class] = repository_class(
                self._session,
                self._tenant_context,
            )

        return self._repositories[repository_class]


# =============================================================================
# QUERY VALIDATOR (pour tests)
# =============================================================================


class TenantQueryValidator:
    """
    Validateur pour s'assurer que toutes les queries incluent tenant_id.
    Utilisé dans les tests pour détecter les queries non filtrées.
    """

    @staticmethod
    def validate_query_has_tenant_filter(
        query_str: str,
        tenant_id_column: str = "tenant_id",
    ) -> Tuple[bool, Optional[str]]:
        """
        Valide qu'une query SQL inclut un filtre sur tenant_id.

        Returns:
            (is_valid, error_message)
        """
        query_lower = query_str.lower()

        # Queries SELECT doivent avoir WHERE tenant_id
        if query_lower.startswith("select"):
            if tenant_id_column not in query_lower:
                return False, f"SELECT query missing {tenant_id_column} filter"

            # Vérifier qu'il est dans une clause WHERE, pas juste en SELECT
            if "where" not in query_lower:
                return False, "SELECT query missing WHERE clause"

        # Queries UPDATE doivent avoir WHERE tenant_id
        if query_lower.startswith("update"):
            if tenant_id_column not in query_lower:
                return False, f"UPDATE query missing {tenant_id_column} filter"

        # Queries DELETE doivent avoir WHERE tenant_id
        if query_lower.startswith("delete"):
            if tenant_id_column not in query_lower:
                return False, f"DELETE query missing {tenant_id_column} filter"

        return True, None
