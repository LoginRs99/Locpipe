"""Load a project's glossary.md and prune it down to what a given
batch actually needs before it goes anywhere near a prompt.

Table format (see glossary-schema.md):
| Source term | Target translation | Category | Confidence | Source/justification |

Categories: brand | lore | mechanic | ui | person
Confidence: high | medium | low
Dual/disputed entries (same source, two legitimate context-dependent
translations, e.g. "Network" -> "Hálózat" vs "Tévéadó") are marked
is_disputed=True and the full cell is kept as context_hint — the
pipeline flags these for the LLM to disambiguate using notes/context
rather than silently picking one, matching glossary-schema.md's own
"ha kétséges, jelöld Bizonytalanként, ne válts önkényesen" rule.
"""

import json
import re
from pathlib import Path
from typing import Optional

from .models import GlossaryTerm

_ROW_RE = re.compile(r"^\|(.+)\|\s*$")


def load_glossary(path: Optional[Path]) -> list[GlossaryTerm]:
    if path is None or not path.exists():
        return []
    terms: list[GlossaryTerm] = []

    if path.suffix.lower() == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as err:
            raise ValueError(f"Failed to parse JSON glossary '{path}': {err}") from err

        raw_list = None
        if isinstance(data, list):
            raw_list = data
        elif isinstance(data, dict):
            for key in ("terms", "glossary", "entries", "items"):
                if isinstance(data.get(key), list):
                    raw_list = data[key]
                    break

        if raw_list is None:
            raise ValueError(
                f"Invalid JSON glossary format in '{path}': expected a JSON array of term objects "
                f"or an object with a 'terms'/'glossary'/'entries' array."
            )

        for idx, item in enumerate(raw_list, start=1):
            if not isinstance(item, dict):
                raise ValueError(
                    f"Invalid item #{idx} in JSON glossary '{path}': expected an object, got {type(item).__name__}"
                )
            src = item.get("source") or item.get("source_term", "")
            tgt = item.get("target") or item.get("target_term", "")
            if src and tgt:
                justification = item.get("justification", "")
                is_disputed = (
                    bool(item.get("is_disputed", False))
                    or bool(item.get("is_dual", False))
                    or (" / " in tgt)
                    or ("⚠" in justification)
                )
                terms.append(
                    GlossaryTerm(
                        source_term=src,
                        target_term=tgt,
                        category=item.get("category", "mechanic"),
                        confidence=item.get("confidence", "high"),
                        justification=justification,
                        is_disputed=is_disputed,
                        context_hint=item.get("context_hint") or (tgt if is_disputed else None),
                    )
                )
        return terms
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _ROW_RE.match(line.strip())
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        if len(cells) < 4:
            continue
        source_term = cells[0]
        if not source_term or source_term.lower().startswith(("forrás", "source", "---", ":--")):
            continue
        if set(source_term) <= {"-", ":"}:
            continue
        target_term, category, confidence = cells[1], cells[2], cells[3]
        justification = cells[4] if len(cells) > 4 else ""
        is_disputed = " / " in target_term or "⚠" in justification
        terms.append(
            GlossaryTerm(
                source_term=source_term,
                target_term=target_term,
                category=category,
                confidence=confidence,
                justification=justification,
                is_disputed=is_disputed,
                context_hint=target_term if is_disputed else None,
            )
        )
    return terms


_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)


def _words(text: str) -> set[str]:
    return {w.lower() for w in _WORD_RE.findall(text)}


def prune_for_batch(
    glossary: list[GlossaryTerm], batch_source_texts: list[str]
) -> list[GlossaryTerm]:
    """Keep only glossary terms that actually appear in this batch.

    This uses prefix-anchored phrase matching rather than naive any-shared-word
    overlap -- false positives (an unrelated term sharing a common word) are
    avoided while keeping prefix matching without trailing boundaries so that
    Hungarian inflected forms (e.g. Network-től, Connect-tel) are preserved.
    Biased toward keeping: false negatives dropping a term are much worse than
    modest over-inclusion.
    """
    if not glossary:
        return []
    if not batch_source_texts:
        return list(glossary)
    batch_text = " ".join(batch_source_texts)
    if not batch_text.strip():
        return list(glossary)
    kept = []
    for term in glossary:
        if not term.source_term:
            continue
        pattern = r"(?<!\w)" + re.escape(term.source_term)
        if re.search(pattern, batch_text, re.IGNORECASE):
            kept.append(term)
    return kept


def format_for_prompt(terms: list[GlossaryTerm]) -> str:
    if not terms:
        return "(no glossary terms apply to this batch)"
    lines = []
    for t in terms:
        line = f"- {t.source_term} -> {t.target_term} [{t.category}, confidence: {t.confidence}]"
        if t.is_disputed:
            line += " ⚠ context-dependent — pick based on the entry's notes/category, do not guess"
        lines.append(line)
    return "\n".join(lines)


def flag_expected_identity_terms(entries: list, glossary: list[GlossaryTerm]) -> int:
    """Marks entries whose source exactly matches a glossary term that's
    genuinely supposed to come back unchanged (source_term == target_term,
    not disputed) -- e.g. a brand name the glossary says to keep in
    English. confidence.py uses this to tell that apart from a model
    that just echoed an ordinary string back untranslated, which is a
    real, cheap, distinct failure signal a plain "did validation pass"
    check doesn't catch on its own.
    """
    identity_terms = [t for t in glossary if not t.is_disputed and t.source_term.strip() == t.target_term.strip()]
    if not identity_terms:
        return 0
    identity_sources = {t.source_term.strip() for t in identity_terms}
    flagged = 0
    for entry in entries:
        if entry.source.strip() in identity_sources:
            entry.extra["_expected_identity"] = True
            flagged += 1
    return flagged


def flag_disputed_terms(entries: list, glossary: list[GlossaryTerm]) -> int:
    """confidence.py docks score for entry.extra['_disputed_glossary_term_used'] —
    this is what actually sets that flag. Without it the check in confidence.py
    was silently dead: it read a key nothing ever wrote, so a disputed term like
    "Network" could sail through at full confidence. Cheap word-boundary check,
    same tradeoff as prune_for_batch: false positives cost a review-queue entry,
    false negatives cost a silent mistranslation, so bias toward flagging.
    """
    disputed = [t for t in glossary if t.is_disputed]
    if not disputed:
        return 0
    flagged = 0
    for entry in entries:
        source_words = _words(entry.source)
        for term in disputed:
            if _words(term.source_term) & source_words:
                entry.extra["_disputed_glossary_term_used"] = True
                flagged += 1
                break
    return flagged
