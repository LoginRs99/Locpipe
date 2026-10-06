"""Phase 11. Turns a ValidationResult + a few cheap heuristics into a
0-1 score. Everything below config.review_threshold goes to Phase 12's
review queue; everything above merges straight through. This is the
lever that keeps LLM review down to the ~5% that actually need it
instead of the 100% the old per-batch QA agent re-read every time.
"""

from __future__ import annotations

import re
from typing import Optional

from .models import Entry, ValidationResult

_HU_LOWER = "a-záéíóöőúüű"
_SUFFIX_NEAR_PLACEHOLDER_RE = re.compile(
    rf"(\{{[^{{\}}\n]+\}}|@[^@\n]+@|%(?:\d+\$)?[0-9\.\-\+]*[sdfuxXgGcping]|</?[a-zA-Z0-9_\-=\#\.\s\"]+>|\[[A-Za-z0-9_\-\:\.=#]+(?:\s+[A-Za-z0-9_\-\:\.=#]+)*\s*\])([{_HU_LOWER}]{{1,3}})(?=[^{_HU_LOWER}]|$)"
)


def has_suffix_near_placeholder(target: str) -> bool:
    """Heuristic detector: returns True if a protected token/placeholder is
    immediately followed by 1-3 lowercase Hungarian letters (a plausible case
    suffix like {item}t, {item}ban, {item}nak) without separating punctuation.
    """
    if not target:
        return False
    return bool(_SUFFIX_NEAR_PLACEHOLDER_RE.search(target))


def _expansion_ratio_limit(entry: Entry, config) -> float:
    """Project-wide confidence.max_expansion_ratio, overridden per-category
    if that category set its own. Respects language characteristics:
    Japanese source strings are dense (kanji/kana), naturally expanding 2.5x-4x in Latin scripts.
    """
    src_lang = str(getattr(config, "source_lang", "en")).lower() if config else "en"
    tgt_lang = str(getattr(config, "target_lang", "hu")).lower() if config else "hu"
    default_limit = 1.6
    if src_lang in ("ja", "japanese", "jpn"):
        default_limit = 3.5
    elif tgt_lang in ("ja", "japanese", "jpn"):
        default_limit = 1.1

    if config is None:
        return default_limit
    if entry.category:
        for rule in config.categories:
            if rule.name == entry.category and rule.max_expansion_ratio is not None:
                return rule.max_expansion_ratio
    return getattr(config, "max_expansion_ratio", default_limit)


FLAG_COUNTS: dict[str, int] = {}


def get_flag_counts() -> dict[str, int]:
    return dict(FLAG_COUNTS)


def reset_flag_counts() -> None:
    FLAG_COUNTS.clear()


def score(entry: Entry, validation: ValidationResult, config: Optional[object] = None) -> float:
    if validation.critical:
        return 0.0

    s = 1.0
    s -= 0.25 * len(validation.major)

    countable_minors = [
        issue for issue in validation.minor
        if getattr(issue, "code", None) != "HU_SPELLING"
        and not (isinstance(getattr(issue, "message", None), str) and ("hu_spelling" in getattr(issue, "message", "").lower() or "helyesírás" in getattr(issue, "message", "").lower()))
        and not (isinstance(issue, str) and ("hu_spelling" in issue.lower() or "helyesírás" in issue.lower()))
    ]
    s -= 0.05 * len(countable_minors)

    if entry.extra.get("_speaker_uncertain"):
        s -= 0.3  # category needed a character voice and none could be found

    if entry.extra.get("_disputed_glossary_term_used"):
        s -= 0.2  # e.g. "Network" — Hálózat/Tévéadó — needs a human or reviewer call

    tgt_lang = str(getattr(config, "target_lang", "hu")).lower() if config else "hu"
    if tgt_lang in ("hu", "hungarian"):
        if entry.extra.get("_suffix_near_placeholder") or has_suffix_near_placeholder(entry.target):
            s -= 0.2  # Hungarian case suffix attached directly to runtime placeholder

    if (
        entry.target.strip()
        and entry.target.strip() == entry.source.strip()
        and not entry.extra.get("_expected_identity")
        and not re.match(r"^[\W\d_]+$", entry.source.strip())
    ):
        s -= 0.4  # came back unchanged and nothing in the glossary says it should have

    if entry.max_length and len(entry.target) > entry.max_length:
        s -= 0.3  # a real, known hard limit was exceeded -- always penalize this

    src_len, tgt_len = len(entry.source.strip()), len(entry.target.strip())
    # Ratio math is noisy on short strings regardless of category: a single
    # extra syllable on a 2-7 char word ("OK" -> "Rendben") produces a huge
    # ratio despite being a completely normal translation, while the same
    # absolute overhead barely moves the ratio on a full sentence. Below this
    # length, only max_length (an absolute, known limit) is a meaningful
    # guard -- the ratio ceiling below is skipped entirely rather than
    # flagging routine short-word expansion as if it were bloat.
    _MIN_SOURCE_LEN_FOR_RATIO_CHECK = 10
    if src_len >= _MIN_SOURCE_LEN_FOR_RATIO_CHECK:
        ratio = tgt_len / src_len
        limit = _expansion_ratio_limit(entry, config)
        # Hungarian tends to run a bit longer than English, but not by huge
        # margins, and it should never come back empty or wildly padded.
        # The floor (0.3) stays fixed -- "much shorter than source" is a
        # fabrication/omission smell regardless of category; the ceiling is
        # the configurable, category-aware guard against UI-breaking bloat.
        if ratio < 0.3 or ratio > limit:
            s -= 0.2

    return max(0.0, min(1.0, s))


def needs_review(entry: Entry, validation: ValidationResult, threshold: float, config: Optional[object] = None) -> bool:
    return not validation.passed or score(entry, validation, config) < threshold


def confidence_flags(entry: Entry, config: Optional[object] = None) -> list[str]:
    """Human-readable reasons for every *heuristic* (non-validator) deduction
    score() applied -- speaker uncertainty, disputed glossary term, identity
    passthrough, max_length overrun, expansion-ratio overrun, placeholder suffix.

    These heuristics never produced a ValidationIssue, so before this an
    entry could land in the review queue with confidence 0.7 and an empty
    `issues` list -- correct behavior, but silent: the reviewer-LLM (or a
    human skimming review_queue.json) had no way to tell *why* without
    re-deriving it by eye. review_queue.py's ReviewItem.to_dict() surfaces
    this list alongside the validator issues so the reason is always
    explicit, whichever stage produced it.
    """
    flags: list[str] = []

    if entry.extra.get("_tier1_retry_exhausted"):
        FLAG_COUNTS["tier1_retry_exhausted"] = FLAG_COUNTS.get("tier1_retry_exhausted", 0) + 1
        flags.append(
            "Tier 1 (deterministic-validation retry) already tried once and failed to fix this "
            "mechanically -- see the issues list for what's still wrong. A second identical "
            "attempt is unlikely to help; consider whether the fix needs a different approach "
            "than what was already tried, not just another try at the same one."
        )

    if entry.extra.get("_speaker_uncertain"):
        FLAG_COUNTS["speaker_uncertain"] = FLAG_COUNTS.get("speaker_uncertain", 0) + 1
        flags.append("speaker/character voice could not be determined for this category")

    if entry.extra.get("_disputed_glossary_term_used"):
        FLAG_COUNTS["disputed_glossary_term_used"] = FLAG_COUNTS.get("disputed_glossary_term_used", 0) + 1
        flags.append("uses a context-dependent (⚠) glossary term — verify the sense applied is correct")

    if entry.extra.get("_suffix_near_placeholder") or has_suffix_near_placeholder(entry.target):
        FLAG_COUNTS["suffix_near_placeholder"] = FLAG_COUNTS.get("suffix_near_placeholder", 0) + 1
        flags.append(
            "placeholder is immediately followed by a Hungarian suffix (e.g. {item}t, {item}ban) — "
            "verify vowel harmony or rephrase to avoid attaching suffixes directly to runtime tokens"
        )

    if (
        entry.target.strip()
        and entry.target.strip() == entry.source.strip()
        and not entry.extra.get("_expected_identity")
    ):
        FLAG_COUNTS["identical_to_source"] = FLAG_COUNTS.get("identical_to_source", 0) + 1
        flags.append("translation is identical to source and nothing marks that as expected")

    if entry.max_length and len(entry.target) > entry.max_length:
        FLAG_COUNTS["max_length_exceeded"] = FLAG_COUNTS.get("max_length_exceeded", 0) + 1
        flags.append(f"exceeds max_length: {len(entry.target)} chars > limit {entry.max_length}")

    src_len, tgt_len = len(entry.source.strip()), len(entry.target.strip())
    if src_len >= 10:
        ratio = tgt_len / src_len
        limit = _expansion_ratio_limit(entry, config)
        if ratio > limit:
            FLAG_COUNTS["expansion_ratio_exceeded"] = FLAG_COUNTS.get("expansion_ratio_exceeded", 0) + 1
            flags.append(
                f"translation is {ratio:.1f}x the source length (limit {limit:.1f}x for "
                f"category '{entry.category or 'default'}') -- rephrase more concisely"
            )
        elif ratio < 0.3:
            FLAG_COUNTS["expansion_ratio_too_short"] = FLAG_COUNTS.get("expansion_ratio_too_short", 0) + 1
            flags.append(f"translation is only {ratio:.1f}x the source length -- looks truncated or incomplete")

    return flags
