"""Phases 3, 4, 5 of the redesign, merged into one pass because they
share the same key: (content_hash, category, context_key).

Never translate the same (source, category, context) twice — reused
from TM if it's been translated before (any project run), deduped
within this run if it appears many times now (Phase 4), and skipped
entirely next run if the source string hasn't changed (Phase 5,
via content_hash alone — TM already gives us that for free since a
changed source produces a different content_hash and therefore a
TM miss).
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from .context_key import build_tm_key
from .models import Entry, EntryStatus, TMRecord
from .normalize import content_hash, normalize_source
from .tm import TranslationMemory


def _simple_norm(text: str) -> str:
    """Lowercase and whitespace-collapsed text for similarity comparison."""
    return re.sub(r"\s+", " ", text.lower().strip())


@dataclass
class DedupeResult:
    tm_hits: int
    unique_groups: dict[str, list[Entry]]   # tm_key -> entries sharing it
    total_entries_needing_mt: int           # == sum(len) below, kept for readability
    total_unique_strings_to_translate: int  # == len(unique_groups)
    fuzzy_tm_hits: int = 0


def enrich_and_dedupe(
    entries: list[Entry], tm: TranslationMemory, source_lang: str, target_lang: str
) -> DedupeResult:
    # entries with an already-approved target (e.g. seeded from previously
    # QA_PASSED batches) don't need MT — they populate the TM instead.
    to_process = [e for e in entries if e.is_empty_or_stub]

    for e in to_process:
        normalized = normalize_source(e.source)
        e.content_hash = content_hash(normalized)
        e.tm_key = build_tm_key(e.content_hash, e.category or "default", e.context_key)

    tm_keys = {e.tm_key for e in to_process}
    tm_matches = tm.get_many(tm_keys)

    tm_hits = 0
    fuzzy_tm_hits = 0
    groups: dict[str, list[Entry]] = {}
    hit_keys: list[str] = []
    unmatched: list[Entry] = []

    for e in to_process:
        record = tm_matches.get(e.tm_key)
        if record is not None:
            e.target = record.translation
            e.status = EntryStatus.TM_REUSED
            e.origin = "tm"
            hit_keys.append(e.tm_key)
            tm_hits += 1
        else:
            unmatched.append(e)

    if unmatched:
        import bisect

        unmatched_cats = {e.category or "default" for e in unmatched}
        tm_by_category: dict[str, list[tuple[TMRecord, str, int]]] = {}
        for _, rec in tm.iter_all():
            cat = rec.category or "default"
            if cat in unmatched_cats and rec.translation and rec.translation.strip():
                rec_norm = _simple_norm(rec.source)
                if rec_norm:
                    tm_by_category.setdefault(cat, []).append((rec, rec_norm, len(rec_norm)))

        # Sort candidates by normalized length and precompute lengths for fast range slicing via bisect
        cand_lens_by_category: dict[str, list[int]] = {}
        for cat_name, cat_list in tm_by_category.items():
            cat_list.sort(key=lambda x: x[2])
            cand_lens_by_category[cat_name] = [c[2] for c in cat_list]

        fuzzy_keys: list[str] = []
        # Cache fuzzy match outcomes across duplicate source strings in the batch
        fuzzy_cache: dict[tuple[str, str, str | None], tuple[int, float, TMRecord] | None] = {}

        for e in unmatched:
            cat = e.category or "default"
            candidates = tm_by_category.get(cat, [])
            if not candidates:
                groups.setdefault(e.tm_key, []).append(e)
                continue

            src_norm = _simple_norm(e.source)
            src_len = len(src_norm)
            if not src_norm:
                groups.setdefault(e.tm_key, []).append(e)
                continue

            cache_key = (cat, src_norm, e.context_key)
            if cache_key in fuzzy_cache:
                best_match = fuzzy_cache[cache_key]
            else:
                cand_lens = cand_lens_by_category[cat]
                min_len = int(src_len * 0.85)
                max_len = int(src_len * 1.15) + 1
                left_idx = bisect.bisect_left(cand_lens, min_len)
                right_idx = bisect.bisect_right(cand_lens, max_len)

                best_match = None
                for rec, cand_norm, cand_len in candidates[left_idx:right_idx]:
                    matcher = difflib.SequenceMatcher(None, src_norm, cand_norm)
                    if matcher.quick_ratio() < 0.9:
                        continue

                    ratio = matcher.ratio()
                    if ratio >= 0.9:
                        ctx_priority = 2 if (e.context_key and rec.context_key == e.context_key) else (
                            1 if (not e.context_key and not rec.context_key) else 0
                        )
                        score_tuple = (ctx_priority, ratio)
                        if best_match is None or score_tuple > (best_match[0], best_match[1]):
                            best_match = (ctx_priority, ratio, rec)
                fuzzy_cache[cache_key] = best_match

            if best_match is not None:
                _, best_ratio, best_rec = best_match
                e.target = best_rec.translation
                # Keep status as MT_DRAFT so it still runs through the per-file validators
                e.status = EntryStatus.MT_DRAFT
                e.origin = "fuzzy_tm"
                e.extra["_fuzzy_tm_match"] = {
                    "similarity": best_ratio,
                    "matched_source": best_rec.source,
                    "matched_tm_key": best_rec.tm_key,
                }
                fuzzy_keys.append(best_rec.tm_key)
                fuzzy_tm_hits += 1
            else:
                groups.setdefault(e.tm_key, []).append(e)

        if fuzzy_keys:
            tm.mark_used_many(fuzzy_keys)

    if hit_keys:
        tm.mark_used_many(hit_keys)

    total_needing_mt = sum(len(v) for v in groups.values())
    return DedupeResult(
        tm_hits=tm_hits,
        unique_groups=groups,
        total_entries_needing_mt=total_needing_mt,
        total_unique_strings_to_translate=len(groups),
        fuzzy_tm_hits=fuzzy_tm_hits,
    )


def commit_to_tm(
    entries: list[Entry], tm: TranslationMemory, source_lang: str, target_lang: str, origin: str = "human"
) -> int:
    """Write entries into the TM. Two callers, same logic:

    1. Pre-existing translations found when a project's batches are
       first read (e.g. MindsEye's batches 1-3, QA_PASSED under the
       old workflow) -- origin="human", full confidence, since these
       were already approved before locpipe ever touched them.

    2. Fresh MT/reviewed results at the end of a real run() -- this is
       the half of "translation memory" that was missing until this
       was caught in testing: dedupe.enrich_and_dedupe() *reads* the
       TM, but nothing was writing this run's own output back into it,
       so a string translated in batch 1 would be translated all over
       again if it showed up in batch 50 next week. See run()'s call
       site for which entries qualify (VALIDATED/REVIEWED only --
       NEEDS_REVIEW/BLOCKED entries are exactly the ones that shouldn't
       poison future reuse with an unresolved answer).

    Uses each entry's own computed confidence as the stored
    quality_score when it has one (case 2) rather than always claiming
    full confidence -- a translation that barely cleared the review
    threshold shouldn't look as trustworthy in the TM as a
    human-approved one.
    """
    seeded = 0
    to_upsert: list[tuple[str, "TMRecord"]] = []
    for e in entries:
        if e.is_empty_or_stub:
            continue
        normalized = normalize_source(e.source)
        e.content_hash = content_hash(normalized)
        e.tm_key = build_tm_key(e.content_hash, e.category or "default", e.context_key)
        from .models import TMRecord

        to_upsert.append(
            (
                e.content_hash,
                TMRecord(
                    tm_key=e.tm_key,
                    source=normalized,
                    translation=e.target,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    category=e.category or "default",
                    context_key=e.context_key,
                    quality_score=e.confidence if e.confidence is not None else 1.0,
                    origin=origin,
                ),
            )
        )
        seeded += 1
    # One transaction for the whole file instead of one commit (one fsync)
    # per entry -- a big already-translated dump (a large Unity/UABEA
    # export, say) used to mean one disk sync per string here.
    tm.upsert_many(to_upsert)
    return seeded
