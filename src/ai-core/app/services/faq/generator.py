"""
FAQ Generator - synthesizes FAQ question/answer pairs from a tenant's real
PrestaShop CMS pages using the LLM, never fabricated from nothing.

Replaces the old /faq endpoints, which used to return the same hardcoded
"How do I return a product?" result for every query and invented category
counts (10/8/5) never backed by real data - then, once that was found and
removed, honestly returned 501 (no FAQ content store existed at all). This
is the real implementation: each CMS page's stripped text is sent to the
LLM with an explicit instruction to generate FAQ pairs strictly grounded in
that text, and to return nothing rather than invented plausible-sounding
content when the page has no real informational substance (PrestaShop's
own default pages ship with Lorem Ipsum placeholder text on "Terms and
conditions" and "About us" - those must yield zero items, not a fabricated
policy).
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import List

from app.infrastructure.external.prestashop.models import CMSPage
from app.infrastructure.llm.provider_factory import BaseLLMProvider

logger = logging.getLogger(__name__)

# A CMS page with less real text than this can't ground any FAQ pair -
# skip the LLM call entirely rather than let it guess from near-nothing.
_MIN_CONTENT_CHARS = 40

_SYSTEM_PROMPT = """You generate customer-facing FAQ entries for an online store, strictly \
grounded in a single store policy page given to you below.

Rules:
- Every question and answer must be answerable using ONLY the text given - never add
  information that isn't there, never guess at a real merchant's actual policies.
- If the text is placeholder/filler content (e.g. "Lorem ipsum...") or otherwise has no
  real informational substance a customer could actually use, return an empty array - do
  NOT invent a plausible-sounding policy to fill the gap.
- Return ONLY a JSON array, no prose, no markdown code fences. Each item:
  {"question": "...", "answer": "..."}. 2 to 4 items, or an empty array if the content
  doesn't support any."""


@dataclass(frozen=True)
class GeneratedFAQItem:
    question: str
    answer: str
    category: str
    source_cms_id: int
    source_title: str


def _extract_json_array(text: str) -> list:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\[.*\]", text, flags=re.DOTALL)
    if not match:
        return []
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        logger.warning("FAQ generator: LLM output wasn't valid JSON, skipping this page")
        return []
    return parsed if isinstance(parsed, list) else []


async def generate_faq_from_cms_pages(
    pages: List[CMSPage], llm_provider: BaseLLMProvider
) -> List[GeneratedFAQItem]:
    """
    One LLM call per CMS page (not batched into a single prompt) - keeps
    each page's grounding isolated so the model can't blend one page's
    real content into another's answer, and a bad/placeholder page simply
    yields zero items instead of affecting the rest of the batch.
    """
    items: List[GeneratedFAQItem] = []

    for page in pages:
        if len(page.content_text) < _MIN_CONTENT_CHARS:
            logger.info(
                "Skipping CMS page for FAQ generation - too little content to ground anything",
                extra={"cms_id": page.id, "title": page.title},
            )
            continue

        user_message = f'Page title: "{page.title}"\n\nPage content:\n{page.content_text}'

        try:
            raw = await llm_provider.chat(message=user_message, context=_SYSTEM_PROMPT)
        except Exception as e:
            logger.warning(
                "FAQ generation failed for a CMS page, skipping it",
                extra={"cms_id": page.id, "error": str(e)},
            )
            continue

        for entry in _extract_json_array(raw):
            if not isinstance(entry, dict):
                continue
            question = str(entry.get("question", "")).strip()
            answer = str(entry.get("answer", "")).strip()
            if not question or not answer:
                continue
            items.append(
                GeneratedFAQItem(
                    question=question,
                    answer=answer,
                    category=page.title,
                    source_cms_id=page.id,
                    source_title=page.title,
                )
            )

    return items
