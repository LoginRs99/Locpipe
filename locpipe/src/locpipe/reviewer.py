"""Phase 13. This is deliberately the slow, expensive, careful path —
that's fine, because confidence.py's whole job was making sure only a
small minority of entries ever reach it. The priority order below
(structural integrity first, style last) mirrors how MindsEye's
original loc-qa-reviewer skill already ranked issues; this step is
its natural home in the new pipeline, just invoked on ~5% of entries
instead of 100%.
"""

from __future__ import annotations

import asyncio
import json

from .glossary import GlossaryTerm, format_for_prompt
from .prompt_builder import fill, load_template, get_register_instruction
from .providers.base import TranslationProvider
from .review_queue import ReviewItem


def build_review_payload(items: list[ReviewItem], glossary: list[GlossaryTerm]) -> str:
    payload = []
    for item in items:
        payload.append(
            {
                "key": item.entry.key,
                "source": item.entry.source,
                "current_translation": item.entry.target,
                "speaker": item.entry.speaker,
                "category": item.entry.category,
                "issues": [i.message for i in item.validation.all_issues],
                "confidence_flags": item.confidence_flags,
            }
        )

    # Scoped glossary: union of item.relevant_glossary_terms across the chunk.
    # Dedupe by (source_term, target_term), preserving first-seen order.
    # If no item carries terms (attribute None or absent), fallback to provided glossary.
    has_carried_terms = any(getattr(item, "relevant_glossary_terms", None) is not None for item in items)
    if has_carried_terms:
        seen: set[tuple[str, str]] = set()
        scoped_terms: list[GlossaryTerm] = []
        for item in items:
            for term in (getattr(item, "relevant_glossary_terms", None) or []):
                key = (term.source_term, term.target_term)
                if key not in seen:
                    seen.add(key)
                    scoped_terms.append(term)
        glossary_text = format_for_prompt(scoped_terms)
    else:
        glossary_text = format_for_prompt(glossary)

    return json.dumps(
        {"glossary": glossary_text, "items": payload}, ensure_ascii=False
    )


async def review_batch(
    items: list[ReviewItem],
    glossary: list[GlossaryTerm],
    provider: TranslationProvider,
    source_lang: str,
    target_lang: str,
    target_register: str = "informal",
    chunk_size: int = 20,
    max_output_tokens: int = 16384,
) -> list[dict]:
    if not items:
        return []
    system_prompt = fill(
        load_template("review.md"),
        source_lang=source_lang,
        target_lang=target_lang,
        register_instruction=get_register_instruction(target_register, target_lang=target_lang),
    )

    async def _review_chunk(chunk: list[ReviewItem]) -> list[dict]:
        user_payload = build_review_payload(chunk, glossary)
        for attempt in range(3):
            try:
                raw = await provider.complete(system_prompt, user_payload, max_tokens=max_output_tokens)
                text = raw.strip()
                if "```" in text:
                    import re
                    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
                    if m:
                        text = m.group(1).strip()
                    else:
                        text = text.strip("`")
                        if text.startswith("json"):
                            text = text[4:].strip()
                start_idx = text.find("[")
                end_idx = text.rfind("]")
                if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                    text = text[start_idx : end_idx + 1]
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    return parsed
            except Exception as e:
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt + 1)
                else:
                    import logging
                    logging.getLogger(__name__).warning("Review chunk of %d items failed after 3 attempts: %s", len(chunk), e)
        return []

    # Concurrent, not sequential: each chunk is an independent network call,
    # and the provider already owns its own concurrency limit (see e.g.
    # AntigravityCLIProvider's internal asyncio.Semaphore) -- awaiting chunks
    # one at a time here just added idle wall-clock time on top of that for
    # no benefit, since nothing about chunk N's review depends on chunk N-1.
    chunks = [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]
    total_chunks = len(chunks)
    completed = 0

    async def _tracked_review_chunk(idx: int, chunk: list[ReviewItem]) -> list[dict]:
        nonlocal completed
        repairs = await _review_chunk(chunk)
        completed += 1
        print(f"  [QA Review {completed}/{total_chunks}] {len(chunk)} item(s) reviewed ({len(repairs)} repaired)")
        return repairs

    results = await asyncio.gather(*(_tracked_review_chunk(idx, c) for idx, c in enumerate(chunks)))
    all_repairs: list[dict] = []
    for r in results:
        all_repairs.extend(r)
    return all_repairs
