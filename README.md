# SaaS AI E-commerce Assistant

## 🎯 Vue d'Ensemble

Solution SaaS complète pour sites e-commerce intégrant:
- **Chatbot AI** intelligent pour le support client 24/7
- **Système de recommandations** personnalisées
- **Génération de coupons** intelligente basée sur la fidélité
- **FAQ dynamique** générée par IA
- **Dashboard Analytics** pour administrateurs
- **Agent IA Admin** pour commandes marketing avancées

**Plateformes supportées:** PrestaShop (v1.0), Shopify & WooCommerce (roadmap)

## 📁 Structure du Projet

```
sass-app/
├── src/
│   ├── ai-core/                    # 🧠 Backend AI (FastAPI/Python)
│   │   ├── app/
│   │   │   ├── api/                # Endpoints REST
│   │   │   │   ├── v1/endpoints/   # Chat, Recommendations, Coupons, Admin
│   │   │   │   ├── middleware/     # Rate limiting, Auth, Logging
│   │   │   │   └── dependencies/   # Injection de dépendances
│   │   │   ├── domain/             # Logique métier (DDD)
│   │   │   │   ├── entities/       # Modèles de domaine
│   │   │   │   ├── services/       # Client Agent, Admin Agent
│   │   │   │   └── repositories/   # Interfaces repositories
│   │   │   ├── infrastructure/     # Implémentations
│   │   │   │   ├── database/       # SQLAlchemy models, migrations
│   │   │   │   ├── cache/          # Redis client
│   │   │   │   ├── vector_store/   # ChromaDB service
│   │   │   │   └── llm/            # OpenAI/Anthropic service
│   │   │   └── core/               # Configuration, Sécurité
│   │   └── tests/                  # Tests unitaires, intégration, AI eval
│   │
│   ├── platform-adapters/          # 🔌 Plugins e-commerce
│   │   ├── prestashop/             # Plugin PrestaShop (PHP)
│   │   ├── shopify/                # (Future) App Shopify
│   │   └── woocommerce/            # (Future) Plugin WordPress
│   │
│   └── backoffice/                 # 📊 Dashboard Admin (Laravel)
│
├── infrastructure/
│   ├── docker/                     # 🐳 Configuration Docker
│   │   ├── docker-compose.yml      # Orchestration services
│   │   ├── services/               # Dockerfiles par service
│   │   ├── monitoring/             # Prometheus, Grafana, Loki
│   │   └── nginx/                  # Reverse proxy config
│   ├── ci-cd/                      # 🚀 GitHub Actions workflows
│   └── scripts/                    # Scripts utilitaires
│
└── docs/                           # 📚 Documentation
    ├── architecture/               # Diagrammes, décisions techniques
    ├── api/                        # Spécifications OpenAPI
    └── deployment/                 # Guides de déploiement
```

## 🚀 Démarrage Rapide

### Prérequis

- Docker & Docker Compose v2+
- Python 3.11+ (développement local)
- Clé API OpenAI

### Installation

```bash
# 1. Cloner le repository
git clone https://github.com/your-org/saas-ai-ecommerce.git
cd saas-ai-ecommerce

# 2. Copier les fichiers d'environnement
cp infrastructure/docker/.env.example infrastructure/docker/.env

# 3. Configurer les variables (éditer .env)

# 4. Lancer les services
cd infrastructure/docker
docker-compose up -d
```

### Services

| Service | URL | Description |
|---------|-----|-------------|
| AI Core API | http://localhost:8000 | API principale |
| API Docs | http://localhost:8000/docs | Swagger |
| Grafana | http://localhost:3000 | Monitoring |

## 🧪 Tests

```bash
cd src/ai-core

# Tests unitaires
pytest tests/unit/ -v

# Tests intégration
pytest tests/integration/ -v

# Tests évaluation IA
pytest tests/ai_evaluation/ -v
```

## 📖 Documentation

- [Architecture Technique](docs/architecture/ARCHITECTURE.md)

## 📝 Licence

MIT License

---

**Projet PFE** - 2025-2026

