"""
Shared Core Services

Architecture:
┌─────────────────────────────────────────────────────────────────────────┐
│                          SHARED CORE                                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐         │
│  │   LLM Gateway   │  │   RAG Service   │  │ Security Service│         │
│  │                 │  │                 │  │                 │         │
│  │  • OpenAI       │  │  • ChromaDB     │  │  • Sanitization │         │
│  │  • Claude       │  │  • Embeddings   │  │  • Injection    │         │
│  │  • Retry logic  │  │  • Similarity   │  │  • Validation   │         │
│  │  • Cost track   │  │    search       │  │  • Guardrails   │         │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘         │
│                                                                          │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐         │
│  │ Tenant Service  │  │ Context Manager │  │ Prompt Registry │         │
│  │                 │  │                 │  │                 │         │
│  │  • Multi-tenant │  │  • Short-term   │  │  • Versioning   │         │
│  │  • Plans/limits │  │  • Working mem  │  │  • Templates    │         │
│  │  • Features     │  │  • Long-term    │  │  • A/B testing  │         │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘         │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┴───────────────────┐
          │                                       │
          ▼                                       ▼
┌─────────────────────┐               ┌─────────────────────┐
│    CLIENT AGENT     │               │     ADMIN AGENT     │
│                     │               │                     │
│ • Conversational    │               │ • Structured JSON   │
│ • Output guardrails │               │ • No free-text exec │
│ • Limited actions   │               │ • Confirmation req  │
│ • Natural language  │               │ • Audit mandatory   │
└─────────────────────┘               └─────────────────────┘
"""

from app.domain.services.shared.context_manager import (
    ContextWindow,
    MemoryType,
    WorkingMemory,
)

# Embedding Versioning
from app.domain.services.shared.embedding_versioning import (
    CURRENT_EMBEDDING_MODEL,
    CURRENT_EMBEDDING_VERSION,
    ContentHasher,
    DocumentVersion,
    EmbeddingVersionManager,
    SyncResult,
    SyncStatus,
)
from app.domain.services.shared.llm_gateway import (
    CostCalculator,
    LLMGateway,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ResponseFormat,
)

# Prompt Injection Defense
from app.domain.services.shared.prompt_defense import (
    ADMIN_AGENT_TOOLS,
    CLIENT_AGENT_TOOLS,
    ContextIsolator,
    DefenseLayer,
    InputSanitizer,
    InstructionLocker,
    OutputValidator,
    PromptInjectionDefense,
    PromptSegmentBuilder,
    ToolCallValidator,
    ToolSchema,
)
from app.domain.services.shared.prompt_registry import (
    PromptRegistry,
    PromptType,
    PromptVersion,
)
from app.domain.services.shared.rag_service import (
    Document,
    DocumentType,
    RAGQuery,
    RAGResult,
    RAGService,
)
from app.domain.services.shared.rag_service_v2 import (
    COLLECTION_NAMES,
    RAGServiceV2,
)

# Reranker
from app.domain.services.shared.reranker import (
    CrossEncoderReranker,
    LLMReranker,
    RankedDocument,
    RerankerConfig,
    RerankingPipeline,
    create_reranker,
)
from app.domain.services.shared.security_service import (
    OutputValidationResult,
    SecurityCheckResult,
    SecurityService,
    ThreatLevel,
    ThreatType,
)
from app.domain.services.shared.tenant_service import (
    Feature,
    Tenant,
    TenantContext,
    TenantLimits,
    TenantLLMConfig,
    TenantPlan,
    TenantService,
    TenantStatus,
)

__all__ = [
    # LLM Gateway
    "LLMGateway",
    "LLMRequest",
    "LLMResponse",
    "LLMProvider",
    "ResponseFormat",
    "CostCalculator",
    # RAG Service
    "RAGService",
    "RAGServiceV2",
    "RAGQuery",
    "RAGResult",
    "Document",
    "DocumentType",
    "COLLECTION_NAMES",
    # Security Service
    "SecurityService",
    "SecurityCheckResult",
    "OutputValidationResult",
    "ThreatLevel",
    "ThreatType",
    # Tenant Service
    "TenantService",
    "Tenant",
    "TenantContext",
    "TenantPlan",
    "TenantStatus",
    "Feature",
    "TenantLimits",
    "TenantLLMConfig",
    # Context Manager
    "ContextWindow",
    "WorkingMemory",
    "MemoryType",
    # Prompt Registry
    "PromptRegistry",
    "PromptVersion",
    "PromptType",
    # Reranker
    "RankedDocument",
    "RerankerConfig",
    "CrossEncoderReranker",
    "LLMReranker",
    "RerankingPipeline",
    "create_reranker",
    # Embedding Versioning
    "CURRENT_EMBEDDING_VERSION",
    "CURRENT_EMBEDDING_MODEL",
    "SyncStatus",
    "DocumentVersion",
    "SyncResult",
    "ContentHasher",
    "EmbeddingVersionManager",
    # Prompt Injection Defense
    "PromptInjectionDefense",
    "InputSanitizer",
    "PromptSegmentBuilder",
    "InstructionLocker",
    "ContextIsolator",
    "OutputValidator",
    "ToolCallValidator",
    "ToolSchema",
    "DefenseLayer",
    "CLIENT_AGENT_TOOLS",
    "ADMIN_AGENT_TOOLS",
]
