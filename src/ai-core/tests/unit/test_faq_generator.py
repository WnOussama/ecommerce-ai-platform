"""
Tests for app/services/faq/generator.py - the honesty guarantees matter
more here than typical unit coverage: this generator must never fabricate
a FAQ answer for content that doesn't actually support one.
"""

from typing import Dict, List, Optional

import pytest

from app.infrastructure.external.prestashop.models import CMSPage
from app.infrastructure.llm.provider_factory import BaseLLMProvider
from app.services.faq.generator import generate_faq_from_cms_pages


class _ScriptedLLMProvider(BaseLLMProvider):
    """Returns a pre-scripted reply per call, in order - lets each test
    control exactly what the "LLM" says without a real Groq key."""

    def __init__(self, replies: List[str]):
        self._replies = list(replies)
        self.calls: List[str] = []

    async def generate(self, messages, temperature: float = 0.7, max_tokens: int = 1000, **kwargs):
        raise NotImplementedError("not used by generate_faq_from_cms_pages")

    async def chat(
        self,
        message: str,
        context: Optional[str] = None,
        history: Optional[List[Dict[str, str]]] = None,
        **kwargs,
    ) -> str:
        self.calls.append(message)
        return self._replies.pop(0)

    def count_tokens(self, text: str) -> int:
        return max(len(text) // 4, 1)

    def get_model_name(self) -> str:
        return "scripted-test-double"

    def is_available(self) -> bool:
        return True


def _page(id_: int, title: str, content: str) -> CMSPage:
    return CMSPage(id=id_, title=title, content_text=content, active=True)


@pytest.mark.asyncio
async def test_generates_items_grounded_in_real_content():
    llm = _ScriptedLLMProvider(
        [
            '[{"question": "How long does shipping take?", '
            '"answer": "Packages are dispatched within 2 days via UPS."}]'
        ]
    )
    pages = [
        _page(
            1,
            "Delivery",
            "Packages are generally dispatched within 2 days after receipt of payment "
            "and are shipped via UPS with tracking.",
        )
    ]

    items = await generate_faq_from_cms_pages(pages, llm)

    assert len(items) == 1
    assert items[0].source_cms_id == 1
    assert items[0].category == "Delivery"
    assert "UPS" in items[0].answer


@pytest.mark.asyncio
async def test_llm_refusing_placeholder_content_yields_nothing():
    """The LLM is instructed to return an empty array for Lorem-Ipsum-style
    filler content rather than invent a policy - this asserts that an empty
    array from the model produces zero persisted items, not a fallback
    fabrication."""
    llm = _ScriptedLLMProvider(["[]"])
    pages = [
        _page(
            3,
            "Terms and conditions of use",
            "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor.",
        )
    ]

    items = await generate_faq_from_cms_pages(pages, llm)

    assert items == []


@pytest.mark.asyncio
async def test_too_short_content_skips_the_llm_call_entirely():
    llm = _ScriptedLLMProvider([])  # would raise IndexError if called - proves it wasn't
    pages = [_page(4, "Empty page", "N/A")]

    items = await generate_faq_from_cms_pages(pages, llm)

    assert items == []
    assert llm.calls == []


@pytest.mark.asyncio
async def test_malformed_llm_output_is_skipped_not_crashed_on():
    llm = _ScriptedLLMProvider(["I'm not sure how to answer that in JSON, sorry!"])
    pages = [_page(5, "Secure payment", "With SSL. Using Visa/Mastercard/Paypal. " * 3)]

    items = await generate_faq_from_cms_pages(pages, llm)

    assert items == []


@pytest.mark.asyncio
async def test_llm_output_wrapped_in_markdown_fence_is_still_parsed():
    llm = _ScriptedLLMProvider(
        ['```json\n[{"question": "Is SSL used?", "answer": "Yes, all payments use SSL."}]\n```']
    )
    pages = [_page(5, "Secure payment", "With SSL. Using Visa/Mastercard/Paypal. " * 3)]

    items = await generate_faq_from_cms_pages(pages, llm)

    assert len(items) == 1
    assert items[0].question == "Is SSL used?"


@pytest.mark.asyncio
async def test_one_bad_page_does_not_affect_other_pages():
    llm = _ScriptedLLMProvider(
        [
            "[]",  # placeholder page yields nothing
            '[{"question": "What carrier is used?", "answer": "UPS."}]',
        ]
    )
    pages = [
        _page(1, "About us", "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do."),
        _page(2, "Delivery", "Packages are shipped via UPS with tracking. " * 3),
    ]

    items = await generate_faq_from_cms_pages(pages, llm)

    assert len(items) == 1
    assert items[0].source_cms_id == 2
