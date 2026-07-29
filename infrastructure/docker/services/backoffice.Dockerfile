# =============================================================================
# Backoffice Service - Laravel/Filament Admin Dashboard
# Consomme l'API REST de l'AI Core - aucun accès direct à sa base de données
# =============================================================================

# -----------------------------------------------------------------------------
# Stage 1: Base PHP
# -----------------------------------------------------------------------------
FROM php:8.4-cli AS base

ENV COMPOSER_ALLOW_SUPERUSER=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    unzip \
    curl \
    libicu-dev \
    libzip-dev \
    && docker-php-ext-install intl zip \
    && rm -rf /var/lib/apt/lists/*

COPY --from=composer:2 /usr/bin/composer /usr/bin/composer

# -----------------------------------------------------------------------------
# Stage 2: Development
# -----------------------------------------------------------------------------
FROM base AS development

COPY composer.json composer.lock ./
RUN composer install --no-interaction --no-scripts --prefer-dist

COPY . .
RUN composer dump-autoload --optimize

RUN chmod -R 775 storage bootstrap/cache

EXPOSE 8000

# Crée/migre la base sqlite locale du panel (users, sessions, cache) au
# démarrage du conteneur - jamais bakée dans l'image (voir .dockerignore),
# pour que chaque conteneur reparte d'une base propre et migrée.
CMD ["sh", "-c", "touch database/database.sqlite && php artisan migrate --force && php artisan serve --host=0.0.0.0 --port=8000"]
