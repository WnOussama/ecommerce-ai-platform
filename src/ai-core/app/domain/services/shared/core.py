"""
Shared Core - Services partagés entre Client Agent et Admin Agent

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
│  │  • Cost track   │  │    search       │  │                 │         │
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

from app.domain.services.shared.llm_gateway import LLMGateway
from app.domain.services.shared.rag_service import RAGService
from app.domain.services.shared.security_service import SecurityService
from app.domain.services.shared.tenant_service import TenantService
from app.domain.services.shared.context_manager import ContextManager
from app.domain.services.shared.prompt_registry import PromptRegistry

__all__ = [
    "LLMGateway",
    "RAGService",
    "SecurityService",
    "TenantService",
    "ContextManager",
    "PromptRegistry",
]

