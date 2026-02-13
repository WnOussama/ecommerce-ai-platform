# =============================================================================
# DOCKERFILE - AI Sync Service
# Background workers pour tâches asynchrones (embeddings, sync, bulk ops)
# Utilise Redis Streams pour une queue FIABLE
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: Base Python
# -----------------------------------------------------------------------------
FROM python:3.11-slim as base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# Stage 2: Dependencies
# -----------------------------------------------------------------------------
FROM base as dependencies

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# -----------------------------------------------------------------------------
# Stage 3: Development
# -----------------------------------------------------------------------------
FROM dependencies as development

COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .

ENV ENVIRONMENT=development

# Use new reliable worker
CMD ["python", "-m", "app.workers.sync_worker"]

# -----------------------------------------------------------------------------
# Stage 4: Production
# -----------------------------------------------------------------------------
FROM dependencies as production

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser

COPY --chown=appuser:appuser . .

USER appuser

ENV ENVIRONMENT=production

# Worker service - uses Redis Streams with:
# - Consumer Groups for scaling
# - Automatic retry with exponential backoff
# - Dead Letter Queue for failed jobs
# - Message claiming for crash recovery
CMD ["python", "-m", "app.workers.sync_worker"]


