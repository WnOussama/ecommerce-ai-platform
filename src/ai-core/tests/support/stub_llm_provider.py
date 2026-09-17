"""
Test-only LLM double - NOT part of the app (app/infrastructure/llm has no
mock/fallback anymore, Groq is the only real provider, see
app/infrastructure/llm/provider_factory.py).

Deterministic and free: lets the test suite exercise the chat/admin flows
(guardrails, RAG wiring, persistence, ...) end-to-end without a real Groq
key or network access. Returns one fixed, neutral sentence regardless of
input - it deliberately never echoes back prices/products/claims, so it
can't trip the output guardrails' hallucination/confidence checks.
"""

from typing import Any, Dict, List, Optional

from app.domain.entities.models import LLMUsage
from app.infrastructure.llm.provider_factory import BaseLLMProvider

_STUB_REPLY = "Merci pour votre message, un conseiller peut vous aider si besoin."


class StubLLMProvider(BaseLLMProvider):
    def __init__(self) -> None:
        self.model = "stub-llm-test-double"

    async def generate(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 1000,
        **kwargs,
    ) -> Dict[str, Any]:
        return {
            "content": _STUB_REPLY,
            "usage": LLMUsage(input_tokens=1, output_tokens=1, model=self.model),
            "model": self.model,
            "finish_reason": "stop",
            "latency_ms": 0,
        }

    async def chat(self, message: str, context: Optional[str] = None, **kwargs) -> str:
        return _STUB_REPLY

    def count_tokens(self, text: str) -> int:
        return max(len(text) // 4, 1)

    def get_model_name(self) -> str:
        return self.model

    def is_available(self) -> bool:
        return True
