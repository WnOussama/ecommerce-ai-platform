# =============================================================================
# Makefile - Development Commands
# =============================================================================

.PHONY: help dev test lint build clean migrate

# Default target
help:
	@echo "Available commands:"
	@echo "  make dev       - Start development environment"
	@echo "  make test      - Run tests locally"
	@echo "  make lint      - Run linters"
	@echo "  make build     - Build Docker image"
	@echo "  make migrate   - Run database migrations"
	@echo "  make clean     - Clean up containers and volumes"

# =============================================================================
# DEVELOPMENT
# =============================================================================

dev:
	@echo "Starting development environment..."
	cd infrastructure/docker && docker-compose -f docker-compose.dev.yml up -d postgres redis
	@echo "Waiting for services to be ready..."
	sleep 5
	@echo "Services ready! Run 'cd src/ai-core && uvicorn app.main:app --reload' to start the API"

dev-all:
	@echo "Starting all services including AI Core..."
	cd infrastructure/docker && docker-compose -f docker-compose.dev.yml up -d

dev-down:
	@echo "Stopping development environment..."
	cd infrastructure/docker && docker-compose -f docker-compose.dev.yml down

# =============================================================================
# TESTING
# =============================================================================

test:
	@echo "Running unit tests..."
	cd src/ai-core && \
	LLM_PROVIDER=mock \
	DB_PASSWORD=test_password_123 \
	SECURITY_JWT_SECRET_KEY=test_jwt_secret_key_32_characters_minimum \
	pytest tests/unit/ -v --tb=short

test-cov:
	@echo "Running tests with coverage..."
	cd src/ai-core && \
	LLM_PROVIDER=mock \
	DB_PASSWORD=test_password_123 \
	SECURITY_JWT_SECRET_KEY=test_jwt_secret_key_32_characters_minimum \
	pytest tests/unit/ -v --cov=app --cov-report=html --cov-report=term-missing

# =============================================================================
# LINTING
# =============================================================================

lint:
	@echo "Running linters..."
	cd src/ai-core && ruff check app/
	cd src/ai-core && ruff format --check app/

lint-fix:
	@echo "Fixing lint issues..."
	cd src/ai-core && ruff check app/ --fix
	cd src/ai-core && ruff format app/

# =============================================================================
# DATABASE
# =============================================================================

migrate:
	@echo "Running database migrations..."
	cd src/ai-core && alembic upgrade head

migrate-create:
	@echo "Creating new migration..."
	@read -p "Migration name: " name; \
	cd src/ai-core && alembic revision --autogenerate -m "$$name"

migrate-rollback:
	@echo "Rolling back last migration..."
	cd src/ai-core && alembic downgrade -1

# =============================================================================
# DOCKER
# =============================================================================

build:
	@echo "Building Docker image..."
	docker build -t ai-core:dev \
		-f infrastructure/docker/services/ai-core.Dockerfile \
		--target development \
		src/ai-core/

build-prod:
	@echo "Building production Docker image..."
	docker build -t ai-core:prod \
		-f infrastructure/docker/services/ai-core.Dockerfile \
		--target production \
		src/ai-core/

# =============================================================================
# CLEANUP
# =============================================================================

clean:
	@echo "Cleaning up..."
	cd infrastructure/docker && docker-compose -f docker-compose.dev.yml down -v
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

# =============================================================================
# CI SIMULATION
# =============================================================================

ci-local:
	@echo "Simulating CI pipeline locally..."
	@echo "\n=== LINT ===" && make lint
	@echo "\n=== TEST ===" && make test
	@echo "\n=== BUILD ===" && make build
	@echo "\n✅ CI simulation complete!"

