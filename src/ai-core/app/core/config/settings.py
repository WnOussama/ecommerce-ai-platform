"""
Configuration centralisée Production-Ready
Utilise pydantic-settings pour validation et typage fort
"""

from enum import Enum
from functools import lru_cache
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from typing_extensions import Annotated


class Environment(str, Enum):
    """
    Environnements supportés par l'application.

    - development: Développement local avec debug activé
    - test: Exécution des tests (CI/CD)
    - staging: Pré-production pour validation
    - production: Production avec sécurité renforcée
    """

    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class DatabaseSettings(BaseSettings):
    """
    Configuration base de données PostgreSQL.

    FastAPI est le SEUL owner de cette base.
    Laravel communique via REST API uniquement.

    Pool configuration:
    - Development: pool_size=5, max_overflow=10
    - Production:  pool_size=20, max_overflow=40
    """

    model_config = SettingsConfigDict(
        env_prefix="DB_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    host: str = "localhost"
    port: int = 5432
    name: str = "saas_ecommerce"
    user: str = "saas_user"
    password: str = Field(..., min_length=8)

    # Pool configuration - configurable via env
    pool_size: int = Field(default=5, ge=1, le=100, description="Number of connections in pool")
    max_overflow: int = Field(
        default=10, ge=0, le=100, description="Max connections above pool_size"
    )
    pool_recycle: int = Field(
        default=3600, ge=300, description="Recycle connections after N seconds"
    )

    echo: bool = Field(default=False, description="Echo SQL queries (debug only)")

    @property
    def url(self) -> str:
        """URL async pour PostgreSQL (asyncpg)"""
        return (
            f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"
        )

    @property
    def sync_url(self) -> str:
        """URL sync pour Alembic (psycopg2)"""
        return (
            f"postgresql+psycopg2://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"
        )


class RedisSettings(BaseSettings):
    """Configuration Redis pour cache et rate limiting"""

    model_config = SettingsConfigDict(
        env_prefix="REDIS_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    host: str = "localhost"
    port: int = 6379
    db: int = 0
    password: Optional[str] = None
    max_connections: int = 50
    socket_timeout: int = 5

    # Séparation des DBs par usage
    cache_db: int = 0
    session_db: int = 1
    rate_limit_db: int = 2
    celery_db: int = 3

    @property
    def url(self) -> str:
        auth = f":{self.password}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


class VectorStoreSettings(BaseSettings):
    """Configuration ChromaDB pour embeddings"""

    model_config = SettingsConfigDict(
        env_prefix="CHROMA_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    persist_directory: str = "./data/chroma"
    collection_prefix: str = "tenant"  # tenant_{tenant_id}_{collection_type}
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    max_batch_size: int = 100


class LLMSettings(BaseSettings):
    """Configuration LLM avec support multi-provider"""

    model_config = SettingsConfigDict(
        env_prefix="LLM_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    provider: str = "mock"  # mock, openai, anthropic, azure

    # OpenAI
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-4-turbo-preview"
    openai_embedding_model: str = "text-embedding-3-small"

    # Anthropic (backup)
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-3-sonnet-20240229"

    # Paramètres génération
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1000, ge=100, le=4096)
    timeout: int = 30
    max_retries: int = 3

    # Rate limiting par tenant (requêtes/minute)
    default_rate_limit: int = 60
    max_rate_limit: int = 300

    # Coût tracking
    track_costs: bool = True
    cost_per_1k_input_tokens: float = 0.01  # GPT-4 Turbo
    cost_per_1k_output_tokens: float = 0.03


class SecuritySettings(BaseSettings):
    """Configuration sécurité"""

    model_config = SettingsConfigDict(
        env_prefix="SECURITY_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # JWT
    jwt_secret_key: str = Field(..., min_length=32)
    jwt_algorithm: str = "HS256"
    jwt_expiration_hours: int = 24
    jwt_refresh_expiration_days: int = 7

    # API Keys
    api_key_prefix: str = "sk_"
    api_key_length: int = 32

    # Rate Limiting
    rate_limit_requests: int = 100
    rate_limit_window_seconds: int = 60

    # CORS
    cors_origins: Annotated[List[str], NoDecode] = [
        "http://localhost:3000",
        "http://localhost:8080",
    ]
    cors_allow_credentials: bool = True

    # Content Security
    max_request_size_mb: int = 10
    allowed_file_types: Annotated[List[str], NoDecode] = [
        "image/jpeg",
        "image/png",
        "application/pdf",
    ]

    # Prompt Injection Protection
    prompt_injection_detection: bool = True
    sensitive_action_confirmation: bool = True
    admin_action_audit_log: bool = True

    @field_validator("cors_origins", "allowed_file_types", mode="before")
    @classmethod
    def split_comma_separated(cls, v):
        """Accepte soit du JSON (`["a","b"]`) soit une liste séparée par des
        virgules (`a,b`), comme documenté dans .env.example."""
        if isinstance(v, str) and not v.strip().startswith("["):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v


class MonitoringSettings(BaseSettings):
    """Configuration observabilité"""

    model_config = SettingsConfigDict(
        env_prefix="MONITORING_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Prometheus
    prometheus_enabled: bool = True
    prometheus_port: int = 9090

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"  # json ou text
    log_file: Optional[str] = None

    # Tracing
    tracing_enabled: bool = False
    jaeger_host: str = "localhost"
    jaeger_port: int = 6831

    # Health checks
    health_check_interval: int = 30


class EmailSettings(BaseSettings):
    """Configuration email (vérification tenant, notifications)"""

    model_config = SettingsConfigDict(
        env_prefix="EMAIL_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    provider: str = "mock"  # mock (dev/test, aucun envoi réel) ou smtp

    smtp_host: str = "localhost"
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_use_tls: bool = True

    from_address: str = "no-reply@example.com"
    from_name: str = "SaaS AI E-commerce Assistant"

    # Base URL publique de l'API (utilisée pour construire le lien de
    # vérification dans l'email) - doit inclure le schéma, sans slash final.
    public_base_url: str = "http://localhost:8000"


class TenantSettings(BaseSettings):
    """Configuration multi-tenant"""

    model_config = SettingsConfigDict(
        env_prefix="TENANT_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Isolation
    isolation_mode: str = "logical"  # logical (shared DB) ou physical (separate DBs)

    # Limites par défaut
    default_max_conversations_per_day: int = 1000
    default_max_products_indexed: int = 10000
    default_max_customers: int = 50000

    # Plans
    plans: dict = {
        "starter": {
            "max_conversations_per_day": 500,
            "max_products_indexed": 1000,
            "max_customers": 5000,
            "rate_limit_rpm": 30,
            "features": ["chatbot", "faq"],
        },
        "professional": {
            "max_conversations_per_day": 2000,
            "max_products_indexed": 10000,
            "max_customers": 25000,
            "rate_limit_rpm": 100,
            "features": ["chatbot", "faq", "recommendations", "coupons"],
        },
        "enterprise": {
            "max_conversations_per_day": 10000,
            "max_products_indexed": 100000,
            "max_customers": -1,  # Illimité
            "rate_limit_rpm": 300,
            "features": ["chatbot", "faq", "recommendations", "coupons", "admin_ai", "analytics"],
        },
    }


class Settings(BaseSettings):
    """Configuration principale agrégée"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Application
    app_name: str = "SaaS AI E-commerce Assistant"
    app_version: str = "1.0.0"
    environment: Environment = Field(default=Environment.DEVELOPMENT)
    debug: bool = False

    # API
    api_prefix: str = "/api/v1"
    docs_enabled: bool = True

    # Sub-configurations
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    vector_store: VectorStoreSettings = Field(default_factory=VectorStoreSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    monitoring: MonitoringSettings = Field(default_factory=MonitoringSettings)
    tenant: TenantSettings = Field(default_factory=TenantSettings)
    email: EmailSettings = Field(default_factory=EmailSettings)

    @field_validator("environment", mode="before")
    @classmethod
    def validate_environment(cls, v) -> Environment:
        """Convertit string en Environment Enum"""
        if isinstance(v, Environment):
            return v
        if isinstance(v, str):
            try:
                return Environment(v.lower())
            except ValueError:
                valid = [e.value for e in Environment]
                raise ValueError(f"Invalid environment '{v}'. Must be one of: {valid}")
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == Environment.PRODUCTION

    @property
    def is_development(self) -> bool:
        return self.environment == Environment.DEVELOPMENT

    @property
    def is_test(self) -> bool:
        return self.environment == Environment.TEST

    @property
    def is_staging(self) -> bool:
        return self.environment == Environment.STAGING


@lru_cache()
def get_settings() -> Settings:
    """Singleton cached pour les settings"""
    return Settings()


# Export pour faciliter l'import
settings = get_settings()
