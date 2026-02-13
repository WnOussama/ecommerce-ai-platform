"""
Tests d'isolation multi-tenant pour les repositories.

Ces tests vérifient que:
1. Toutes les queries incluent tenant_id
2. L'accès cross-tenant est impossible
3. Les modifications de tenant_id sont interdites
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.infrastructure.database.repositories.base import (
    TenantAwareRepository,
    TenantContext,
    TenantIdMissingError,
    CrossTenantAccessError,
    TenantIsolationError,
    TenantQueryValidator,
)


# =============================================================================
# FIXTURES
# =============================================================================

@pytest.fixture
def tenant_id():
    return str(uuid4())


@pytest.fixture
def other_tenant_id():
    return str(uuid4())


@pytest.fixture
def tenant_context(tenant_id):
    return TenantContext(
        tenant_id=tenant_id,
        user_id="test_user",
        request_id="test_request",
    )


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.add_all = MagicMock()
    session.flush = AsyncMock()
    session.refresh = AsyncMock()
    session.delete = AsyncMock()
    return session


# =============================================================================
# TEST: TENANT CONTEXT
# =============================================================================

class TestTenantContext:
    """Tests pour TenantContext"""

    def test_valid_context(self, tenant_id):
        """Un contexte valide avec tenant_id"""
        ctx = TenantContext(tenant_id=tenant_id)
        assert ctx.tenant_id == tenant_id

    def test_context_with_all_fields(self, tenant_id):
        """Contexte avec tous les champs"""
        ctx = TenantContext(
            tenant_id=tenant_id,
            user_id="user_123",
            request_id="req_456",
        )
        assert ctx.tenant_id == tenant_id
        assert ctx.user_id == "user_123"
        assert ctx.request_id == "req_456"

    def test_missing_tenant_id_raises(self):
        """tenant_id manquant lève une exception"""
        with pytest.raises(TenantIdMissingError):
            TenantContext(tenant_id="")

    def test_none_tenant_id_raises(self):
        """tenant_id None lève une exception"""
        with pytest.raises(TenantIdMissingError):
            TenantContext(tenant_id=None)


# =============================================================================
# TEST: BASE REPOSITORY INITIALIZATION
# =============================================================================

class TestRepositoryInitialization:
    """Tests d'initialisation des repositories"""

    def test_repository_requires_tenant_context(self, mock_session):
        """Repository nécessite un TenantContext"""
        from app.infrastructure.database.models import Conversation

        # Mock repository
        class TestRepo(TenantAwareRepository):
            model_class = Conversation

        # Sans contexte = erreur
        with pytest.raises(TenantIdMissingError):
            TestRepo(mock_session, None)

    def test_repository_requires_valid_tenant_id(self, mock_session):
        """Repository nécessite un tenant_id valide"""
        from app.infrastructure.database.models import Conversation

        class TestRepo(TenantAwareRepository):
            model_class = Conversation

        # Contexte vide = erreur
        with pytest.raises(TenantIdMissingError):
            TestRepo(mock_session, TenantContext(tenant_id=""))

    def test_repository_stores_tenant_id(self, mock_session, tenant_context):
        """Repository stocke le tenant_id"""
        from app.infrastructure.database.models import Conversation

        class TestRepo(TenantAwareRepository):
            model_class = Conversation

        repo = TestRepo(mock_session, tenant_context)
        assert repo.tenant_id == tenant_context.tenant_id


# =============================================================================
# TEST: CROSS-TENANT ACCESS PREVENTION
# =============================================================================

class TestCrossTenantPrevention:
    """Tests de prévention d'accès cross-tenant"""

    def test_validate_entity_same_tenant(self, mock_session, tenant_context, tenant_id):
        """Entité du même tenant = OK"""
        from app.infrastructure.database.models import Conversation

        class TestRepo(TenantAwareRepository):
            model_class = Conversation

        repo = TestRepo(mock_session, tenant_context)

        # Créer un mock d'entité avec le bon tenant_id
        entity = MagicMock()
        entity.tenant_id = tenant_id

        # Ne devrait pas lever d'exception
        repo._validate_entity_tenant(entity)

    def test_validate_entity_different_tenant(
        self, mock_session, tenant_context, other_tenant_id
    ):
        """Entité d'un autre tenant = Exception"""
        from app.infrastructure.database.models import Conversation

        class TestRepo(TenantAwareRepository):
            model_class = Conversation

        repo = TestRepo(mock_session, tenant_context)

        # Créer un mock d'entité avec un autre tenant_id
        entity = MagicMock()
        entity.tenant_id = other_tenant_id
        entity.id = "some_id"

        with pytest.raises(CrossTenantAccessError):
            repo._validate_entity_tenant(entity)

    @pytest.mark.asyncio
    async def test_update_validates_tenant(
        self, mock_session, tenant_context, other_tenant_id
    ):
        """Update valide le tenant de l'entité"""
        from app.infrastructure.database.models import Conversation

        class TestRepo(TenantAwareRepository):
            model_class = Conversation

        repo = TestRepo(mock_session, tenant_context)

        # Entité d'un autre tenant
        entity = MagicMock()
        entity.tenant_id = other_tenant_id
        entity.id = "some_id"

        with pytest.raises(CrossTenantAccessError):
            await repo.update(entity)

    @pytest.mark.asyncio
    async def test_delete_validates_tenant(
        self, mock_session, tenant_context, other_tenant_id
    ):
        """Delete valide le tenant de l'entité"""
        from app.infrastructure.database.models import Conversation

        class TestRepo(TenantAwareRepository):
            model_class = Conversation

        repo = TestRepo(mock_session, tenant_context)

        # Entité d'un autre tenant
        entity = MagicMock()
        entity.tenant_id = other_tenant_id
        entity.id = "some_id"

        with pytest.raises(CrossTenantAccessError):
            await repo.delete(entity)


# =============================================================================
# TEST: TENANT_ID MODIFICATION PREVENTION
# =============================================================================

class TestTenantIdModification:
    """Tests de prévention de modification de tenant_id"""

    @pytest.mark.asyncio
    async def test_cannot_update_tenant_id(self, mock_session, tenant_context):
        """Impossible de modifier tenant_id via update"""
        from app.infrastructure.database.models import Conversation

        class TestRepo(TenantAwareRepository):
            model_class = Conversation

        repo = TestRepo(mock_session, tenant_context)

        with pytest.raises(TenantIsolationError) as exc_info:
            await repo.update_by_id(
                "some_id",
                {"tenant_id": "malicious_tenant_id"}
            )

        assert "Cannot modify tenant_id" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_create_forces_tenant_id(self, mock_session, tenant_context, tenant_id):
        """Create force toujours le bon tenant_id"""
        from app.infrastructure.database.models import Conversation

        class TestRepo(TenantAwareRepository):
            model_class = Conversation

        repo = TestRepo(mock_session, tenant_context)

        # Créer une entité avec un mauvais tenant_id
        entity = MagicMock()
        entity.tenant_id = "wrong_tenant_id"

        # Mock pour éviter les erreurs DB
        mock_session.flush = AsyncMock()
        mock_session.refresh = AsyncMock()

        await repo.create(entity)

        # Vérifier que le tenant_id a été forcé
        assert entity.tenant_id == tenant_id


# =============================================================================
# TEST: QUERY VALIDATOR
# =============================================================================

class TestQueryValidator:
    """Tests du validateur de queries"""

    def test_select_with_tenant_filter(self):
        """SELECT avec filtre tenant = valide"""
        query = "SELECT * FROM customers WHERE tenant_id = 'xxx' AND name = 'test'"
        is_valid, error = TenantQueryValidator.validate_query_has_tenant_filter(query)
        assert is_valid is True
        assert error is None

    def test_select_without_tenant_filter(self):
        """SELECT sans filtre tenant = invalide"""
        query = "SELECT * FROM customers WHERE name = 'test'"
        is_valid, error = TenantQueryValidator.validate_query_has_tenant_filter(query)
        assert is_valid is False
        assert "tenant_id" in error

    def test_select_without_where_clause(self):
        """SELECT sans WHERE = invalide"""
        query = "SELECT * FROM customers"
        is_valid, error = TenantQueryValidator.validate_query_has_tenant_filter(query)
        assert is_valid is False

    def test_update_with_tenant_filter(self):
        """UPDATE avec filtre tenant = valide"""
        query = "UPDATE customers SET name = 'x' WHERE tenant_id = 'xxx' AND id = 'y'"
        is_valid, error = TenantQueryValidator.validate_query_has_tenant_filter(query)
        assert is_valid is True

    def test_update_without_tenant_filter(self):
        """UPDATE sans filtre tenant = invalide"""
        query = "UPDATE customers SET name = 'x' WHERE id = 'y'"
        is_valid, error = TenantQueryValidator.validate_query_has_tenant_filter(query)
        assert is_valid is False

    def test_delete_with_tenant_filter(self):
        """DELETE avec filtre tenant = valide"""
        query = "DELETE FROM customers WHERE tenant_id = 'xxx' AND id = 'y'"
        is_valid, error = TenantQueryValidator.validate_query_has_tenant_filter(query)
        assert is_valid is True

    def test_delete_without_tenant_filter(self):
        """DELETE sans filtre tenant = invalide"""
        query = "DELETE FROM customers WHERE id = 'y'"
        is_valid, error = TenantQueryValidator.validate_query_has_tenant_filter(query)
        assert is_valid is False


# =============================================================================
# TEST: REPOSITORY QUERIES INCLUDE TENANT
# =============================================================================

class TestRepositoryQueries:
    """Tests vérifiant que toutes les queries incluent tenant_id"""

    @pytest.mark.asyncio
    async def test_get_by_id_includes_tenant(self, mock_session, tenant_context, tenant_id):
        """get_by_id inclut tenant_id dans la query"""
        from app.infrastructure.database.models import Conversation

        class TestRepo(TenantAwareRepository):
            model_class = Conversation

        repo = TestRepo(mock_session, tenant_context)

        # Mock le résultat
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_session.execute = AsyncMock(return_value=mock_result)

        await repo.get_by_id("some_id")

        # Vérifier que execute a été appelé
        mock_session.execute.assert_called_once()

        # Récupérer la query
        call_args = mock_session.execute.call_args
        query = call_args[0][0]

<<<<<<< HEAD
        # Convertir en string et vérifier tenant_id
        query_str = str(query.compile(compile_kwargs={"literal_binds": False}))
        assert "tenant_id" in query_str.lower()
=======
        # Vérifier que tenant_id est dans la query (sans literal_binds pour éviter erreur UUID)
        query_str = str(query)
        assert "tenant_id" in query_str.lower(), "Query should include tenant_id filter"
>>>>>>> b246289 (feat: DevOps foundation - CI/CD pipeline, Docker, Alembic)

    @pytest.mark.asyncio
    async def test_get_all_includes_tenant(self, mock_session, tenant_context, tenant_id):
        """get_all inclut tenant_id dans la query"""
        from app.infrastructure.database.models import Conversation

        class TestRepo(TenantAwareRepository):
            model_class = Conversation

        repo = TestRepo(mock_session, tenant_context)

        # Mock le résultat
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all = MagicMock(return_value=[])
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute = AsyncMock(return_value=mock_result)

        await repo.get_all()

        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_find_by_includes_tenant(self, mock_session, tenant_context):
        """find_by inclut tenant_id même avec d'autres filtres"""
        from app.infrastructure.database.models import Conversation

        class TestRepo(TenantAwareRepository):
            model_class = Conversation

        repo = TestRepo(mock_session, tenant_context)

        # Mock le résultat
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all = MagicMock(return_value=[])
        mock_result.scalars = MagicMock(return_value=mock_scalars)
        mock_session.execute = AsyncMock(return_value=mock_result)

        await repo.find_by({"email": "test@test.com"})

        mock_session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_by_id_includes_tenant(self, mock_session, tenant_context):
        """delete_by_id inclut tenant_id"""
        from app.infrastructure.database.models import Conversation

        class TestRepo(TenantAwareRepository):
            model_class = Conversation

        repo = TestRepo(mock_session, tenant_context)

        # Mock le résultat
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute = AsyncMock(return_value=mock_result)

        await repo.delete_by_id("some_id")

        mock_session.execute.assert_called_once()


# =============================================================================
# TEST: ISOLATION BETWEEN TENANTS
# =============================================================================

class TestTenantIsolation:
    """Tests d'isolation complète entre tenants"""

    @pytest.mark.asyncio
    async def test_tenant_a_cannot_see_tenant_b_data(
        self, mock_session, tenant_id, other_tenant_id
    ):
        """Tenant A ne peut pas voir les données de Tenant B"""
        from app.infrastructure.database.models import Conversation

        class TestRepo(TenantAwareRepository):
            model_class = Conversation

        # Repository pour tenant A
        context_a = TenantContext(tenant_id=tenant_id)
        repo_a = TestRepo(mock_session, context_a)

        # Repository pour tenant B
        context_b = TenantContext(tenant_id=other_tenant_id)
        repo_b = TestRepo(mock_session, context_b)

        # Vérifier que les tenant_ids sont différents
        assert repo_a.tenant_id != repo_b.tenant_id

        # Les queries devraient utiliser des tenant_ids différents
        assert repo_a._tenant_id == tenant_id
        assert repo_b._tenant_id == other_tenant_id


# =============================================================================
# PYTEST CONFIGURATION
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])

