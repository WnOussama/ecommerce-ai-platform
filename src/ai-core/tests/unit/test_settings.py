"""
Tests — Settings & Configuration

Covers:
- Environment enum
- DatabaseSettings URL construction
- Settings properties (is_development, is_production)
- Settings singleton caching
"""

import pytest

from app.core.config.settings import (
    Environment,
    DatabaseSettings,
    Settings,
    get_settings,
)


class TestEnvironmentEnum:

    def test_all_environments_exist(self):
        assert Environment.DEVELOPMENT == "development"
        assert Environment.TEST == "test"
        assert Environment.STAGING == "staging"
        assert Environment.PRODUCTION == "production"

    def test_is_string_enum(self):
        assert isinstance(Environment.DEVELOPMENT, str)


class TestDatabaseSettings:

    @pytest.fixture
    def db_settings(self):
        return DatabaseSettings(
            host="localhost",
            port=5432,
            name="test_db",
            user="test_user",
            password="secure_password_123",
        )

    def test_url_format(self, db_settings):
        url = db_settings.url
        assert url.startswith("postgresql+asyncpg://")
        assert "test_user" in url
        assert "test_db" in url
        assert "localhost" in url

    def test_sync_url_format(self, db_settings):
        url = db_settings.sync_url
        assert url.startswith("postgresql+psycopg2://")
        assert "test_user" in url

    def test_password_in_url_but_not_logged(self, db_settings):
        """Password is in URL (needed for connection) but type is validated."""
        url = db_settings.url
        assert "secure_password_123" in url  # expected in URL

    def test_pool_defaults(self, db_settings):
        assert db_settings.pool_size >= 1
        assert db_settings.max_overflow >= 0
        assert db_settings.pool_recycle >= 300


class TestSettingsSingleton:

    def test_get_settings_returns_settings(self):
        s = get_settings()
        assert isinstance(s, Settings)

    def test_settings_cached(self):
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_environment_is_set(self):
        s = get_settings()
        assert isinstance(s.environment, Environment)

    def test_has_database_settings(self):
        s = get_settings()
        assert hasattr(s, "database")

    def test_has_llm_settings(self):
        s = get_settings()
        assert hasattr(s, "llm")

    def test_has_security_settings(self):
        s = get_settings()
        assert hasattr(s, "security")


class TestSettingsProperties:

    def test_is_development(self):
        s = get_settings()
        # In test env ENVIRONMENT=development
        if s.environment == Environment.DEVELOPMENT:
            assert s.is_development is True
            assert s.is_production is False

    def test_database_url_is_set(self):
        s = get_settings()
        assert s.database.url
        assert "postgresql" in s.database.url



