# Architecture Client/Admin Agents avec Shared Core

## Vue d'ensemble

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                              SHARED CORE                                          │
│                                                                                   │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐ │
│  │  LLM GATEWAY   │  │  RAG SERVICE   │  │   SECURITY     │  │    TENANT      │ │
│  │                │  │                │  │   SERVICE      │  │   SERVICE      │ │
│  │ • OpenAI       │  │ • ChromaDB     │  │                │  │                │ │
│  │ • Anthropic    │  │ • Embeddings   │  │ • Sanitization │  │ • Plans        │ │
│  │ • Retry/Fallback│ │ • Similarity   │  │ • Injection    │  │ • Limits       │ │
│  │ • Cost tracking│  │   search       │  │   detection    │  │ • Features     │ │
│  │                │  │                │  │ • Output       │  │ • Config       │ │
│  │                │  │                │  │   guardrails   │  │                │ │
│  └───────┬────────┘  └───────┬────────┘  └───────┬────────┘  └───────┬────────┘ │
│          │                   │                   │                   │          │
│          └───────────────────┴───────────────────┴───────────────────┘          │
│                                      │                                           │
└──────────────────────────────────────┼───────────────────────────────────────────┘
                                       │
                 ┌─────────────────────┴─────────────────────┐
                 │                                           │
                 ▼                                           ▼
┌────────────────────────────────────┐   ┌────────────────────────────────────┐
│          CLIENT AGENT              │   │           ADMIN AGENT              │
│                                    │   │                                    │
│  ┌──────────────────────────────┐  │   │  ┌──────────────────────────────┐  │
│  │      CARACTÉRISTIQUES        │  │   │  │      CARACTÉRISTIQUES        │  │
│  ├──────────────────────────────┤  │   │  ├──────────────────────────────┤  │
│  │                              │  │   │  │                              │  │
│  │  ✓ Ton CONVERSATIONNEL       │  │   │  │  ✓ Output JSON STRUCTURÉ     │  │
│  │    - Vouvoiement obligatoire │  │   │  │    - Pas de texte libre      │  │
│  │    - Naturel et amical       │  │   │  │    - Schéma strict           │  │
│  │    - Adapté au client        │  │   │  │    - Validation JSON         │  │
│  │                              │  │   │  │                              │  │
│  │  ✓ Output GUARDRAILS STRICTS │  │   │  │  ✓ PAS d'exécution libre     │  │
│  │    - Filtrage sensible       │  │   │  │    - Commandes prédéfinies   │  │
│  │    - Pas de data leak        │  │   │  │    - Parser strict           │  │
│  │    - Ton approprié           │  │   │  │    - Whitelist actions       │  │
│  │                              │  │   │  │                              │  │
│  │  ✓ Actions LIMITÉES          │  │   │  │  ✓ Confirmation OBLIGATOIRE  │  │
│  │    - respond                 │  │   │  │    - Token de confirmation   │  │
│  │    - show_products           │  │   │  │    - Expiration 30 min       │  │
│  │    - show_faq                │  │   │  │    - Impact estimé           │  │
│  │    - generate_coupon         │  │   │  │                              │  │
│  │    - redirect                │  │   │  │  ✓ Audit MANDATORY           │  │
│  │    - escalate                │  │   │  │    - Toutes actions loguées  │  │
│  │                              │  │   │  │    - Qui/Quoi/Quand          │  │
│  │                              │  │   │  │    - Résultat/Erreur         │  │
│  └──────────────────────────────┘  │   │  └──────────────────────────────┘  │
│                                    │   │                                    │
└────────────────────────────────────┘   └────────────────────────────────────┘
```

## Flux Client Agent

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          CLIENT AGENT FLOW                                       │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│   Message Client                                                                 │
│        │                                                                         │
│        ▼                                                                         │
│   ┌─────────────────┐                                                           │
│   │ 1. SECURITY     │  ← SecurityService.check_input()                          │
│   │    CHECK        │    - Sanitization                                          │
│   │                 │    - Prompt injection detection                            │
│   │                 │    - Si bloqué → réponse standard                          │
│   └────────┬────────┘                                                           │
│            │ Message sanitizé                                                    │
│            ▼                                                                     │
│   ┌─────────────────┐                                                           │
│   │ 2. FEATURE      │  ← TenantService.check_feature(CHATBOT)                   │
│   │    CHECK        │    - Plan vérifié                                          │
│   │                 │    - Limites vérifiées                                     │
│   └────────┬────────┘                                                           │
│            │                                                                     │
│            ▼                                                                     │
│   ┌─────────────────┐                                                           │
│   │ 3. INTENT       │  ← ClientIntentClassifier                                 │
│   │    CLASSIFICATION│   - Règles heuristiques                                   │
│   │                 │    - Patterns matching                                     │
│   │                 │    - Context aware                                         │
│   └────────┬────────┘                                                           │
│            │ (intent, confidence)                                                │
│            ▼                                                                     │
│   ┌─────────────────┐                                                           │
│   │ 4. RAG          │  ← RAGService.search()                                    │
│   │    RETRIEVAL    │    - Products si pertinent                                │
│   │                 │    - FAQs si pertinent                                    │
│   │                 │    - Policies si pertinent                                │
│   └────────┬────────┘                                                           │
│            │ Documents pertinents                                                │
│            ▼                                                                     │
│   ┌─────────────────┐                                                           │
│   │ 5. LLM          │  ← LLMGateway.generate()                                  │
│   │    GENERATION   │    - System prompt + context                              │
│   │                 │    - ResponseFormat.TEXT (conversationnel)                │
│   │                 │    - Cost tracking                                        │
│   └────────┬────────┘                                                           │
│            │ Response text                                                       │
│            ▼                                                                     │
│   ┌─────────────────┐                                                           │
│   │ 6. OUTPUT       │  ← ClientOutputGuardrails.validate_response()             │
│   │    GUARDRAILS   │    - Filtrage patterns interdits                          │
│   │                 │    - Limitation actions                                   │
│   │                 │    - Vouvoiement enforced                                 │
│   │                 │    - Max length                                           │
│   └────────┬────────┘                                                           │
│            │                                                                     │
│            ▼                                                                     │
│   ClientResponse {                                                               │
│     message: "Réponse conversationnelle...",                                    │
│     intent: "product_search",                                                   │
│     confidence: 0.85,                                                           │
│     actions: [RESPOND, SHOW_PRODUCTS],                                          │
│     suggestions: ["Voir plus", "Filtrer"]                                       │
│   }                                                                              │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Flux Admin Agent

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          ADMIN AGENT FLOW                                        │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                  │
│   Commande Admin (JSON obligatoire)                                             │
│   {                                                                              │
│     "command_type": "generate_bulk_coupons",                                    │
│     "parameters": {...}                                                         │
│   }                                                                              │
│        │                                                                         │
│        ▼                                                                         │
│   ┌─────────────────┐                                                           │
│   │ 1. PARSE        │  ← AdminCommandParser.parse()                             │
│   │    COMMAND      │    - Validation JSON strict                               │
│   │                 │    - Command type dans whitelist                          │
│   │                 │    - Parameters validés                                   │
│   │                 │    - ❌ Rejet si texte libre                               │
│   └────────┬────────┘                                                           │
│            │ AdminCommandRequest                                                 │
│            ▼                                                                     │
│   ┌─────────────────┐                                                           │
│   │ 2. FEATURE      │  ← TenantService.check_feature(ADMIN_AI)                  │
│   │    CHECK        │    - Plan Enterprise requis                               │
│   │                 │    - Limites admin vérifiées                              │
│   └────────┬────────┘                                                           │
│            │                                                                     │
│            ▼                                                                     │
│   ┌─────────────────┐                                                           │
│   │ 3. RISK LEVEL   │  ← COMMAND_DEFINITIONS[cmd].risk_level                    │
│   │    CHECK        │                                                           │
│   └────────┬────────┘                                                           │
│            │                                                                     │
│      ┌─────┴─────┐                                                              │
│      │           │                                                              │
│      ▼           ▼                                                              │
│   [LOW]       [HIGH]                                                            │
│      │           │                                                              │
│      │           ▼                                                              │
│      │    ┌─────────────────┐                                                   │
│      │    │ 4. CREATE       │  ← ConfirmationManager                            │
│      │    │    PENDING      │    - Token généré                                 │
│      │    │    ACTION       │    - Expiration 30 min                            │
│      │    │                 │    - Impact estimé                                │
│      │    └────────┬────────┘                                                   │
│      │             │                                                             │
│      │             ▼                                                             │
│      │    AdminResponse {                                                        │
│      │      status: "pending_confirmation",                                     │
│      │      requires_confirmation: true,                                        │
│      │      confirmation_token: "xxx",                                          │
│      │      confirmation_details: {...}                                         │
│      │    }                                                                      │
│      │                                                                           │
│      │    [Admin confirme avec token]                                           │
│      │             │                                                             │
│      │             ▼                                                             │
│      │    ┌─────────────────┐                                                   │
│      │    │ 5. VERIFY       │  ← ConfirmationManager.confirm_action()           │
│      │    │    CONFIRMATION │    - Token valide?                                │
│      │    │                 │    - Non expiré?                                  │
│      │    └────────┬────────┘                                                   │
│      │             │                                                             │
│      └─────────────┼─────────────────────────────────────────────────┐          │
│                    │                                                  │          │
│                    ▼                                                  │          │
│           ┌─────────────────┐                                        │          │
│           │ 6. EXECUTE      │  ← Command handlers                    │          │
│           │    COMMAND      │    - Pas d'exécution LLM libre        │          │
│           │                 │    - Handlers prédéfinis              │          │
│           └────────┬────────┘                                        │          │
│                    │                                                  │          │
│                    ▼                                                  │          │
│           ┌─────────────────┐                                        │          │
│           │ 7. AUDIT LOG    │  ← AdminAuditLogger.log_action()      │ MANDATORY│
│           │    (MANDATORY)  │    - Qui                               │          │
│           │                 │    - Quoi                              │          │
│           │                 │    - Quand                             │          │
│           │                 │    - Résultat/Erreur                   │          │
│           └────────┬────────┘                                        │          │
│                    │                                                  │          │
│                    ▼                                                             │
│           AdminResponse {                    # TOUJOURS JSON structuré          │
│             action_id: "...",                                                   │
│             command_type: "generate_bulk_coupons",                              │
│             status: "completed",                                                │
│             success: true,                                                      │
│             data: {                                                             │
│               coupons_generated: 500,                                           │
│               ...                                                               │
│             }                                                                   │
│           }                                                                     │
│                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────┘
```

## Commandes Admin Prédéfinies

| Commande | Risk Level | Confirmation | Description |
|----------|------------|--------------|-------------|
| `get_sales_analytics` | LOW | ❌ | Analytics ventes |
| `get_customer_analytics` | LOW | ❌ | Analytics clients |
| `generate_sales_report` | LOW | ❌ | Génère rapport |
| `suggest_marketing_strategy` | MEDIUM | ❌ | Suggestions (read-only) |
| `suggest_price_optimization` | MEDIUM | ❌ | Suggestions prix |
| `segment_customers` | MEDIUM | ✅ | Segmentation IA |
| `generate_bulk_coupons` | HIGH | ✅ | Coupons en masse |
| `update_product_prices` | HIGH | ✅ | Modification prix |

## Différences Clés

| Aspect | Client Agent | Admin Agent |
|--------|--------------|-------------|
| **Format output** | Texte conversationnel | JSON structuré strict |
| **Exécution** | Via LLM + guardrails | Commandes prédéfinies |
| **Ton** | Naturel, vouvoiement | Technique, factuel |
| **Actions** | 6 types limités | Whitelist de commandes |
| **Confirmation** | Non requise | Obligatoire si HIGH risk |
| **Audit** | Logging basique | Audit complet obligatoire |
| **Guardrails** | Output filtering | Input parsing strict |

## Fichiers

```
app/domain/services/
├── shared/
│   ├── __init__.py           # Exports
│   ├── llm_gateway.py        # LLM abstraction
│   ├── rag_service.py        # ChromaDB RAG
│   ├── security_service.py   # Input/Output security
│   ├── tenant_service.py     # Multi-tenant logic
│   ├── context_manager.py    # Conversation context
│   └── prompt_registry.py    # Prompt versioning
│
├── client/
│   ├── __init__.py
│   └── agent_v2.py           # Client Agent (conversationnel)
│
└── admin/
    ├── __init__.py
    └── agent_v2.py           # Admin Agent (JSON strict)
```

