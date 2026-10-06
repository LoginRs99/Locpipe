"""The whole point of this file: replace loc-orchestrator/SKILL.md's
conversational "spawn a subagent per batch, track progress in a
markdown file, restart the session every 20-30 batches" loop with a
control flow that never talks to the LLM about anything except
translating strings and repairing flagged ones.

No project-specific logic lives here — see projects/<name>/project.yaml.
"""

from __future__ import annotations

import asyncio
import re
import time
import zlib
from pathlib import Path

from .adapters.registry import get_adapter
from .batcher import TranslationBatch, build_batches
from .checkpoint import Checkpoint, fingerprint_batches
from .classify import classify_entries
from .config import ProjectConfig
from .confidence import confidence_flags, needs_review, score
from .context_key import build_tm_key
from .dedupe import commit_to_tm, enrich_and_dedupe
from .glossary import flag_disputed_terms, flag_expected_identity_terms, load_glossary, prune_for_batch
from .merge import merge_all
from .models import Entry, EntryStatus, Severity, ValidationIssue, ValidationResult
from .narrative_context import attach_narrative_context
from .normalize import content_hash, normalize_source
from .consistency import find_consistency_issues
from .output import (
    RunStats,
    write_consistency_report,
    write_full_bilingual_report,
    write_review_report,
    write_stats,
)
from .providers.base import TranslationProvider
from .review_queue import ReviewItem, write_review_queue
from .reviewer import review_batch
from .schemas import build_retry_payload, build_system_prompt_for_category, build_user_payload, parse_and_validate_response
from .tm import TranslationMemory
from .validators.registry import run_validator
from .validators.protected_tokens import audit_entry_tokens
from .validators.quote_balance import audit_quote_pair
from .validators.glossary_terms import load_glossary_for_check
from .validators.validate_hu_spelling import build_glossary_word_whitelist, spellcheck_target

_CHAR_ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|")


def load_known_characters(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    names = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _CHAR_ROW_RE.match(line.strip())
        if not m:
            continue
        name = m.group(1).strip()
        if not name or name.startswith("*") or name.lower() in ("szereplő", "character", "---"):
            continue
        if set(name) <= {"-", ":"}:
            continue
        names.add(name)
    return names


_ID_RE = re.compile(r"(?:id=['\"]?([^'\",:]+)['\"]?|^\[([^\]]+)\])")


def _attribute_issues(
    file_validation: ValidationResult, file_entries: list[Entry]
) -> dict[str, ValidationResult]:
    by_key = {e.key: ValidationResult(entry_key=e.key) for e in file_entries}
    unattributed = ValidationResult(entry_key="<file-level>")
    for issue in file_validation.all_issues:
        m = _ID_RE.search(issue.message)
        key = (m.group(1) or m.group(2)) if m else None
        target = by_key.get(key, unattributed) if key else unattributed
        getattr(target, issue.severity.value.lower()).append(issue)
    if unattributed.all_issues:
        for vr in by_key.values():
            vr.critical += unattributed.critical
            vr.major += unattributed.major
    return by_key


def _run_all_validators(
    path: Path,
    file_entries: list[Entry],
    config: ProjectConfig,
    format_kwargs: dict,
) -> dict[str, ValidationResult]:
    """Single validation pass for a file: format-specific checks
    (run_validator, dispatched by config.format) PLUS two format-agnostic
    checks that apply to every adapter regardless of per-format coverage --
    audit_entry_tokens (placeholder/tag/printf-spec preservation) and
    audit_quote_pair (quote balance/style). Before this helper existed,
    protected-token checking only ran for uabea_json and hu_spelling --
    unity/po_gettext/ue4_5_po/xliff projects had zero placeholder
    protection. Called from both _finalize_file (initial validation) and
    _tier1_repair (post-repair re-validation) so a repair attempt is
    re-checked against the exact same rules it was originally flagged
    against, not a narrower subset.
    """
    file_validation = run_validator(
        config.format, path, config.resources.get("glossary"), entry_key=str(path), format_kwargs=format_kwargs
    )
    per_entry = _attribute_issues(file_validation, file_entries)

    # Loaded once per file (matches run_validator's own not-cached convention
    # above), not once per entry.
    glossary_path = config.resources.get("glossary")
    glossary_words = build_glossary_word_whitelist(
        load_glossary_for_check(str(glossary_path)) if glossary_path else []
    )

    for e in file_entries:
        vr = per_entry[e.key]
        if e.target and e.max_length is not None and len(e.target) > e.max_length:
            vr.major.append(
                ValidationIssue(
                    severity=Severity.MAJOR,
                    code="MAX_LENGTH_EXCEEDED",
                    message=f"Target text length ({len(e.target)} chars) exceeds maximum allowed limit of {e.max_length} chars.",
                )
            )
        for issue in audit_entry_tokens(e.source, e.target):
            if any("placeholder" in x.message.lower() or "darabszam" in x.message.lower() for x in vr.all_issues):
                continue
            getattr(vr, issue.severity.value.lower()).append(issue)
        for issue in audit_quote_pair(e.source, e.target):
            getattr(vr, issue.severity.value.lower()).append(issue)
        if config.target_lang.lower() in ("hu", "hungarian"):
            for issue in spellcheck_target(e.target, glossary_words):
                getattr(vr, issue.severity.value.lower()).append(issue)
    return per_entry


async def _call_complete(
    provider: TranslationProvider,
    system_prompt: str,
    user_payload: str,
    max_tokens: int,
    effort: str | None = None,
) -> str:
    import inspect
    sig = inspect.signature(provider.complete)
    kwargs = {"max_tokens": max_tokens}
    if "effort" in sig.parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        kwargs["effort"] = effort
    return await provider.complete(system_prompt, user_payload, **kwargs)


async def _translate_batches_sync(batches, config, glossary, provider: TranslationProvider, checkpoint, max_api_calls: int | None = None):
    """asyncio.as_completed, not asyncio.gather -- deliberately. gather()
    is all-or-nothing: one failing batch cancels the rest and nothing
    gets persisted, which is a bad failure mode with tens of batches
    and a network that hiccups occasionally. Here, each batch draft is
    persisted to checkpoint.json the moment it succeeds, independent of
    whatever the others are doing, so an interrupted run never retranslates
    completed batches on restart.

    Returns (list of batches that failed, per-attempt latencies including
    failed attempts, total wasted retry attempts across all batches --
    0 on a run where nothing ever needed a retry).
    """
    api_call_count = [0]

    async def translate_one(batch):
        saved_drafts = checkpoint.get_batch_drafts()
        if saved_drafts and all(e.tm_key in saved_drafts for e in batch.representatives):
            for e in batch.representatives:
                e.target = saved_drafts[e.tm_key]
                e.status = EntryStatus.MT_DRAFT
                e.origin = "mt"
            return batch

        if getattr(provider, "prefers_per_batch_context", False):
            batch_glossary = prune_for_batch(glossary, [e.source for e in batch.representatives])
            batch_speakers = {e.speaker for e in batch.representatives if e.speaker}
        else:
            batch_glossary = glossary
            batch_speakers = None
        # Determine category-level effort override if specified on the CategoryRule
        category_rule = next((c for c in config.categories if c.name == batch.category), None)
        category_effort = category_rule.effort if category_rule and category_rule.effort else None

        system_prompt = build_system_prompt_for_category(config, batch.category, batch_glossary, batch_speakers)

        accumulated: dict[int, str] = {}
        if saved_drafts:
            for idx, rep in enumerate(batch.representatives):
                if rep.tm_key in saved_drafts:
                    accumulated[idx] = saved_drafts[rep.tm_key]

        last_error = None
        wasted_this_batch = 0

        for attempt in range(config.provider.max_retries):
            missing_indices = [i for i in range(len(batch.representatives)) if i not in accumulated]
            if not missing_indices:
                break

            if max_api_calls is not None and api_call_count[0] >= max_api_calls:
                raise RuntimeError(f"Reached --max-api-calls budget limit ({max_api_calls}). Stopping cleanly.")
            api_call_count[0] += 1

            sub_reps = [batch.representatives[i] for i in missing_indices]
            sub_batch = TranslationBatch(category=batch.category, representatives=sub_reps)
            user_payload = build_user_payload(sub_batch)
            if last_error:
                user_payload += f"\n\n(Your previous response was invalid: {last_error}. Return ONLY the JSON array.)"

            start = time.monotonic()
            try:
                raw = await _call_complete(
                    provider,
                    system_prompt,
                    user_payload,
                    max_tokens=config.provider.max_output_tokens,
                    effort=category_effort,
                )
            except Exception as e:
                latencies.append(time.monotonic() - start)
                last_error = f"API call failed ({type(e).__name__}): {e}"
                wasted_this_batch += 1
                if attempt < config.provider.max_retries - 1:
                    backoff = min(2 * (2 ** attempt), 20)
                    print(f"  [{batch.category}] API call error on attempt {attempt + 1}/{config.provider.max_retries}: {e}. Retrying in {backoff}s...")
                    await asyncio.sleep(backoff)
                continue
            latencies.append(time.monotonic() - start)
            if attempt > 0 and len(accumulated) > 0:
                partial_recovery_calls[0] += 1

            parsed_res = parse_and_validate_response(raw)
            parsed, error = parsed_res[0], parsed_res[1]
            salvaged = getattr(parsed_res, "salvaged", False)

            if parsed is None:
                last_error = error
                wasted_this_batch += 1
                continue

            # If bracket-append salvage fired on a truncated response, the final item's
            # translation string may be cut mid-sentence. Exclude that final item from
            # accumulated so it stays in missing_indices and gets retried with full context.
            # Note: assumes the truncation boundary is in the tail element (the dominant
            # case when hitting token limits). If truncated cleanly between elements, the
            # tail item is complete and retranslating it costs one extra recovery call
            # rather than risking committing a truncated string.
            usable_items = parsed[:-1] if (salvaged and len(parsed) > 0) else parsed

            # Map returned sub-batch IDs back to original representative indices
            newly_recovered = 0
            for item in usable_items:
                sub_id = item.get("id")
                if isinstance(sub_id, int) and 0 <= sub_id < len(missing_indices):
                    orig_idx = missing_indices[sub_id]
                    accumulated[orig_idx] = item["translation"]
                    newly_recovered += 1

            if newly_recovered == 0:
                last_error = f"response covered 0 usable items (salvaged={salvaged})"
                wasted_this_batch += 1
                continue

            last_error = None
            if len(accumulated) == len(batch.representatives):
                wasted_retries[0] += wasted_this_batch
                if wasted_this_batch > 0:
                    print(
                        f"  [{batch.category}] succeeded on attempt {attempt + 1}/{config.provider.max_retries} "
                        f"-- {wasted_this_batch} wasted retry/retries before this. If this category keeps "
                        f"needing retries, its batch_size (currently {len(batch.representatives)} entries this "
                        f"batch) is likely too large for max_output_tokens "
                        f"({config.provider.max_output_tokens}) -- lower batch_size for it in project.yaml."
                    )
                elif attempt > 0:
                    print(
                        f"  [{batch.category}] succeeded after {attempt + 1} requests via partial batch recovery "
                        f"-- 0 wasted full-payload retries."
                    )
                drafts = {}
                for idx, rep in enumerate(batch.representatives):
                    rep.target = accumulated[idx]
                    rep.status = EntryStatus.MT_DRAFT
                    rep.origin = "mt"
                    drafts[rep.tm_key] = rep.target
                await asyncio.to_thread(checkpoint.save_batch_drafts, drafts)
                return batch

        wasted_retries[0] += max(wasted_this_batch, config.provider.max_retries)
        raise RuntimeError(
            f"batch in category '{batch.category}' failed after retries: "
            f"{last_error or f'incomplete: covered {len(accumulated)}/{len(batch.representatives)} items'}"
        )

    failed: list[str] = []
    latencies: list[float] = []
    wasted_retries = [0]  # mutable cell so translate_one's closures can accumulate into it
    partial_recovery_calls = [0]
    tasks = [asyncio.create_task(translate_one(b)) for b in batches]
    total = len(tasks)
    done_count = 0
    for coro in asyncio.as_completed(tasks):
        done_count += 1
        try:
            batch = await coro
        except Exception as e:
            print(f"  [{done_count}/{total}] FAILED: {e}")
            failed.append(str(e))
            continue
        checkpoint.mark_batch_done(batch.category, len(batch.representatives))
        print(f"  [{done_count}/{total}] {batch.category}: {len(batch.representatives)} unique strings translated")
    return failed, latencies, wasted_retries[0], partial_recovery_calls[0]


async def _tier1_repair(
    path: Path,
    file_entries: list[Entry],
    per_entry: dict[str, ValidationResult],
    adapter,
    config: ProjectConfig,
    glossary,
    provider: TranslationProvider,
    format_kwargs: dict,
) -> tuple[dict[str, ValidationResult], int]:
    """Tier 1 of the QA loop: a validator failure (critical/major --
    format, tags, placeholders, protected terms) is a concrete,
    mechanical defect a deterministic checker already pinned down
    exactly, in its own message. Retrying the CHEAP bulk-translate call
    with that exact message attached usually fixes it in one shot, for
    a fraction of what routing straight to the expensive review agent
    (Tier 3, review_provider + agents/review.md) would cost -- and this
    never touches review_provider at all.

    Bounded to config.tier1_repair_attempts (default 1) specifically so
    this can't become the unbounded self-correction loop the whole
    3-tier split exists to prevent. Whatever still fails after that many
    attempts gets entry.extra['_tier1_retry_exhausted'] = True and falls
    through to the normal review-queue routing in _finalize_file,
    unchanged from before this function existed -- Tier 1 either saves
    a review call or it doesn't, it never blocks one.

    Re-validates by re-merging to disk and re-running the real
    validator after each attempt (cheap, deterministic, no LLM cost)
    rather than trusting the model's own claim that it fixed something.

    Returns (updated per_entry validation results, count actually fixed).
    """
    repaired_count = 0

    for _attempt in range(config.tier1_repair_attempts):
        failing = [e for e in file_entries if e.status == EntryStatus.MT_DRAFT and not per_entry[e.key].passed]
        if not failing:
            break

        by_category: dict[str, list[Entry]] = {}
        for e in failing:
            by_category.setdefault(e.category or "default", []).append(e)

        async def _repair_category(category_name: str, entries: list[Entry]) -> None:
            issues_by_key = {
                e.key: [i.message for i in per_entry[e.key].critical + per_entry[e.key].major] for e in entries
            }
            if getattr(provider, "prefers_per_batch_context", False):
                batch_glossary = prune_for_batch(glossary, [e.source for e in entries])
                batch_speakers = {e.speaker for e in entries if e.speaker}
            else:
                batch_glossary = glossary
                batch_speakers = None
            category_rule = next((c for c in config.categories if c.name == category_name), None)
            category_effort = category_rule.effort if category_rule and category_rule.effort else None
            system_prompt = build_system_prompt_for_category(
                config, category_name, batch_glossary, batch_speakers, correction_mode=True
            )
            user_payload = build_retry_payload(entries, issues_by_key)
            try:
                raw = await _call_complete(
                    provider,
                    system_prompt,
                    user_payload,
                    max_tokens=config.provider.max_output_tokens,
                    effort=category_effort,
                )
            except Exception as e:
                print(f"  [tier1] retry call failed for category '{category_name}': {e}")
                return
            parsed, error = parse_and_validate_response(raw)
            if parsed is None:
                print(f"  [tier1] retry response unusable for category '{category_name}': {error}")
                return
            for item in parsed:
                idx = item["id"]
                if 0 <= idx < len(entries):
                    entries[idx].target = item["translation"]

        # Concurrent across categories -- each category's repair call is
        # independent (different entries, no shared mutable state besides
        # each entry's own .target), so awaiting them one at a time here
        # bought nothing but extra wall-clock time. The provider's own
        # concurrency limit still applies underneath.
        await asyncio.gather(*(_repair_category(name, entries) for name, entries in by_category.items()))

        merge_all(file_entries, adapter)
        new_per_entry = _run_all_validators(path, file_entries, config, format_kwargs)
        fixed_this_attempt = sum(
            1 for e in failing if per_entry[e.key].critical or per_entry[e.key].major
            if new_per_entry[e.key].passed
        )
        repaired_count += fixed_this_attempt
        per_entry = new_per_entry
        if fixed_this_attempt:
            print(f"  [tier1] {fixed_this_attempt}/{len(failing)} mechanical issue(s) fixed without an LLM review call")

    for e in file_entries:
        if e.status == EntryStatus.MT_DRAFT and not per_entry[e.key].passed:
            e.extra["_tier1_retry_exhausted"] = True

    return per_entry, repaired_count


def _snapshot_before_merge(path: Path, config: ProjectConfig):
    """Save a pre-merge source snapshot for post-run integrity verification diffs."""
    snapshot_dir = config.root / "tm" / "pre_merge_snapshots"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    batches_dir = config.root / "batches"
    try:
        rel = path.relative_to(batches_dir)
    except ValueError:
        try:
            rel = path.relative_to(config.root)
        except ValueError:
            rel = Path(path.name)
    snapshot_file = snapshot_dir / rel
    snapshot_file.parent.mkdir(parents=True, exist_ok=True)
    if not snapshot_file.exists() and path.exists():
        import shutil
        shutil.copy2(path, snapshot_file)


def should_escalate_to_high(item: ReviewItem, config: ProjectConfig) -> tuple[bool, str]:
    if not config.provider.escalation_enabled:
        return False, "escalation_disabled"

    entry = item.entry

    num_placeholders = item.entry.source.count("{") + item.entry.source.count("%") + item.entry.source.count("<")
    if num_placeholders > config.max_placeholders_for_low:
        return True, "high_structural_complexity"

    if len(item.entry.source) > config.max_source_len_for_low:
        return True, "long_dialogue"

    if item.confidence < config.escalation_confidence_threshold:
        return True, "low_confidence"

    if config.escalation_sample_rate > 0:
        bucket = zlib.crc32(entry.key.encode("utf-8")) % 10000
        threshold_bucket = int(config.escalation_sample_rate * 10000)
        if bucket < threshold_bucket:
            return True, "sampled_qa_escalation"

    return False, "low_qa_default"


def _finalize_file(
    path: Path,
    file_entries: list[Entry],
    adapter,
    config: ProjectConfig,
    glossary,
    provider: TranslationProvider,
    review_provider: TranslationProvider,
    tm: TranslationMemory,
    escalation_provider: TranslationProvider | None = None,
) -> dict:
    if escalation_provider is None:
        escalation_provider = review_provider

    _snapshot_before_merge(path, config)
    merge_all(file_entries, adapter)

    format_kwargs = {}
    if config.format == "unity":
        format_kwargs["source_col"] = (config.format_options.get("source_column_names") or ["source"])[0]
        format_kwargs["target_col"] = (config.format_options.get("target_column_names") or ["target"])[0]

    per_entry = _run_all_validators(path, file_entries, config, format_kwargs)

    tier1_repaired = 0
    if config.tier1_repair_attempts > 0 and any(
        e.status == EntryStatus.MT_DRAFT and not per_entry[e.key].passed for e in file_entries
    ):
        per_entry, tier1_repaired = asyncio.run(
            _tier1_repair(path, file_entries, per_entry, adapter, config, glossary, provider, format_kwargs)
        )

    review_items: list[ReviewItem] = []
    validation_failures = 0
    fidelity_candidates: dict[str, list[tuple[Entry, ValidationResult, float]]] = {}
    for e in file_entries:
        if e.status not in (EntryStatus.MT_DRAFT, EntryStatus.TM_REUSED):
            continue
        vr = per_entry[e.key]
        e.confidence = score(e, vr, config)
        if not vr.passed:
            validation_failures += 1
        if needs_review(e, vr, config.review_threshold, config):
            e.status = EntryStatus.NEEDS_REVIEW
            review_items.append(
                ReviewItem(
                    entry=e,
                    validation=vr,
                    confidence=e.confidence,
                    relevant_glossary_terms=prune_for_batch(glossary, [e.source]),
                    confidence_flags=confidence_flags(e, config),
                )
            )
        else:
            if e.status == EntryStatus.MT_DRAFT:
                cat = e.category or "default"
                fidelity_candidates.setdefault(cat, []).append((e, vr, e.confidence))
            else:
                e.status = EntryStatus.VALIDATED

    fidelity_samples = 0
    fidelity_bucket_threshold = int(config.fidelity_sample_rate * 100)
    for cat, cands in fidelity_candidates.items():
        if fidelity_bucket_threshold <= 0:
            break  # sampling disabled for this run
        sampled_for_cat = []
        for e, vr, conf in cands:
            bucket = zlib.crc32(e.key.encode('utf-8')) % 100
            if bucket < fidelity_bucket_threshold:
                sampled_for_cat.append((e, vr, conf))
        if len(sampled_for_cat) > 50:
            sampled_for_cat = sorted(sampled_for_cat, key=lambda x: zlib.crc32(x[0].key.encode('utf-8')))[:50]

        fidelity_samples += len(sampled_for_cat)
        sampled_keys = {e.key for e, vr, conf in sampled_for_cat}

        for e, vr, conf in cands:
            if e.key in sampled_keys:
                e.status = EntryStatus.NEEDS_REVIEW
                e.extra['sampled_fidelity'] = True
                review_items.append(
                    ReviewItem(
                        entry=e,
                        validation=vr,
                        confidence=conf,
                        relevant_glossary_terms=prune_for_batch(glossary, [e.source]),
                    )
                )
            else:
                e.status = EntryStatus.VALIDATED

    reviewed = 0
    fidelity_failures = 0
    low_qa_calls = 0
    low_qa_repairs = 0
    low_qa_failures = 0
    high_qa_calls = 0
    high_qa_repairs = 0
    high_qa_failures = 0
    escalated_to_high_count = 0
    escalation_reasons: dict[str, int] = {}

    if review_items:
        low_items: list[ReviewItem] = []
        high_items: list[tuple[ReviewItem, str]] = []

        for item in review_items:
            escalate, reason = should_escalate_to_high(item, config)
            if escalate:
                high_items.append((item, reason))
                escalated_to_high_count += 1
                escalation_reasons[reason] = escalation_reasons.get(reason, 0) + 1
            else:
                low_items.append(item)

        # 1. Low QA execution (Tier 1 QA)
        if low_items:
            print(f"  [{path.name}] Starting QA review for {len(low_items)} flagged item(s)...")
            low_qa_calls += len(low_items)
            low_repairs = asyncio.run(
                review_batch(
                    low_items,
                    glossary,
                    review_provider,
                    config.source_lang,
                    config.target_lang,
                    target_register=config.target_register,
                    chunk_size=config.review_chunk_size,
                    max_output_tokens=config.provider.max_output_tokens,
                )
            )
            repairs_by_key = {r["key"]: r for r in low_repairs}
            for item in low_items:
                repair = repairs_by_key.get(item.entry.key)
                if repair and not repair.get("flag_for_human"):
                    actually_changed = item.entry.target.strip() != repair["translation"].strip()
                    item.entry.target = repair["translation"]
                    item.entry.status = EntryStatus.REVIEWED
                    item.entry.origin = "reviewed"
                    reviewed += 1
                    low_qa_repairs += 1
                    if item.entry.extra.get('sampled_fidelity') and actually_changed:
                        fidelity_failures += 1
                else:
                    if config.provider.escalation_enabled and (escalation_provider != review_provider):
                        high_items.append((item, "low_qa_repair_failed"))
                        escalated_to_high_count += 1
                        escalation_reasons["low_qa_repair_failed"] = escalation_reasons.get("low_qa_repair_failed", 0) + 1
                    else:
                        item.entry.status = EntryStatus.BLOCKED
                        low_qa_failures += 1

        # 2. High QA execution (Tier 2 Escalation QA)
        if high_items:
            high_review_items = [item for item, _ in high_items]
            high_qa_calls += len(high_review_items)
            high_repairs = asyncio.run(
                review_batch(
                    high_review_items,
                    glossary,
                    escalation_provider,
                    config.source_lang,
                    config.target_lang,
                    target_register=config.target_register,
                    chunk_size=config.review_chunk_size,
                    max_output_tokens=config.provider.max_output_tokens,
                )
            )
            repairs_by_key = {r["key"]: r for r in high_repairs}
            for item, reason in high_items:
                repair = repairs_by_key.get(item.entry.key)
                if repair and not repair.get("flag_for_human"):
                    actually_changed = item.entry.target.strip() != repair["translation"].strip()
                    item.entry.target = repair["translation"]
                    item.entry.status = EntryStatus.REVIEWED
                    item.entry.origin = "reviewed"
                    reviewed += 1
                    high_qa_repairs += 1
                    if item.entry.extra.get('sampled_fidelity') and actually_changed:
                        fidelity_failures += 1
                else:
                    item.entry.status = EntryStatus.BLOCKED
                    high_qa_failures += 1

        merge_all([i.entry for i in review_items], adapter)

        reviewed_keys = {i.entry.key for i in review_items if i.entry.status == EntryStatus.REVIEWED}
        if reviewed_keys:
            post_review_per_entry = _run_all_validators(path, file_entries, config, format_kwargs)
            still_failing = 0
            for item in review_items:
                if item.entry.key not in reviewed_keys:
                    continue
                new_vr = post_review_per_entry[item.entry.key]
                if not new_vr.passed:
                    item.entry.status = EntryStatus.BLOCKED
                    item.entry.extra["_review_output_still_failed_validation"] = True
                    item.validation = new_vr  # needs_review.json should show what's STILL wrong, not the pre-review issue list
                    item.confidence_flags = item.confidence_flags + [
                        "Tier 3 review's own repair still failed deterministic validation after "
                        "applying it -- both automatic self-correction stages have now failed on "
                        "this string; needs direct human attention rather than another automatic pass."
                    ]
                    reviewed -= 1
                    still_failing += 1
            if still_failing:
                print(
                    f"  [tier3] {still_failing} review repair(s) still failed validation after applying "
                    "-- downgraded to BLOCKED rather than committed as reviewed"
                )

    # Zero-human-loop terminal handling: an entry that is still BLOCKED here
    # has exhausted every automatic tier (Tier 1 mechanical repair, Tier 2
    # low-QA, Tier 3 high-QA/escalation) and would otherwise ship with
    # whatever half-finished value entry.target holds -- possibly text that
    # already failed a CRITICAL check. With no human reviewer to resolve
    # review_report.md, that's not acceptable: explicit source-language
    # passthrough instead, never a blank or broken string. Status is left as
    # BLOCKED (not VALIDATED/REVIEWED) on purpose, so commit_to_tm below --
    # which only looks at VALIDATED/REVIEWED entries -- never writes this
    # fallback into the TM, where it would poison future reuse of the same
    # source string with an untranslated answer.
    still_blocked = [e for e in file_entries if e.status == EntryStatus.BLOCKED]
    for e in still_blocked:
        e.target = e.source
        e.origin = "untranslated_fallback"
    if still_blocked:
        merge_all(still_blocked, adapter)
        print(f"  [fallback] {len(still_blocked)} entr(y/ies) shipped as source-language passthrough (see stats.json)")

    # Write half of "translation memory" -- only for THIS file, THIS call.
    # Only resolved statuses qualify: NEEDS_REVIEW/BLOCKED are exactly the
    # ones that shouldn't propagate an unresolved answer into future reuse.
    # Gated on provider.persists_to_tm: see TranslationProvider's docstring
    # for that flag -- a dry run must not leave a lasting mark on the TM
    # a later real run could reuse.
    validated_this_file = [e for e in file_entries if e.status == EntryStatus.VALIDATED]
    reviewed_this_file = [e for e in file_entries if e.status == EntryStatus.REVIEWED]
    if getattr(provider, "persists_to_tm", True):
        newly_committed = commit_to_tm(validated_this_file, tm, config.source_lang, config.target_lang, origin="mt")
        newly_committed += commit_to_tm(reviewed_this_file, tm, config.source_lang, config.target_lang, origin="reviewed")
    else:
        newly_committed = 0

    return {
        "review_items": review_items,
        "validation_failures": validation_failures,
        "fidelity_samples": fidelity_samples,
        "reviewed": reviewed,
        "fidelity_failures": fidelity_failures,
        "newly_committed": newly_committed,
        "tier1_repaired": tier1_repaired,
        "still_blocked": len(still_blocked),
        "low_qa_calls": low_qa_calls,
        "low_qa_repairs": low_qa_repairs,
        "low_qa_failures": low_qa_failures,
        "high_qa_calls": high_qa_calls,
        "high_qa_repairs": high_qa_repairs,
        "high_qa_failures": high_qa_failures,
        "escalated_to_high_count": escalated_to_high_count,
        "escalation_reasons": escalation_reasons,
    }


def run(
    config: ProjectConfig,
    provider: TranslationProvider,
    *,
    review_provider: TranslationProvider | None = None,
    escalation_provider: TranslationProvider | None = None,
    limit_batches: int | None = None,
    max_api_calls: int | None = None,
) -> RunStats:
    """review_provider lets the review/repair step (Phase 13) use a
    different model tier than bulk translation -- e.g. a fast/cheap
    model for the mechanical bulk of a run and a stronger one for the
    ~5% that actually need careful reasoning. Defaults to `provider`
    itself if not given. `config.provider.review_model` is where this
    is configured in project.yaml; cli.py builds the second provider
    instance from it.

    Checkpointing granularity is one input batch file. Each file goes
    through extract -> translate -> validate -> review/repair -> merge
    -> commit-to-TM as one unit before the next file starts (see
    _finalize_file above and checkpoint.mark_file_done/is_file_done).
    A crash or exhausted-retries failure leaves every already-finished
    file committed and skipped on the next run's is_file_done() check;
    only the file that was in flight (and anything after it) gets
    reprocessed. Within a single file, a partially-failed batch still
    means the whole file is retried next run -- committing unvalidated
    MT output to the TM early, just to save that sub-file work, would
    let a translation that hasn't passed validation yet get reused by
    completely unrelated future strings, which is a worse trade.
    Batch-mode (config.provider.mode == "batch") is the one exception to
    per-file granularity on the submission side: one job still covers
    every pending file, because waiting out a job's up-to-24-48h
    resolution window once per file, serially, would defeat the point
    of batch mode entirely. Everything after that job resolves --
    validate/review/merge/commit -- still happens per file.
    """
    if review_provider is None:
        review_provider = provider
    if escalation_provider is None:
        escalation_provider = review_provider

    adapter = get_adapter(config.format, {**config.format_options, "source_lang": config.source_lang, "target_lang": config.target_lang})
    tm = TranslationMemory(config.tm_db_path)
    glossary = load_glossary(config.resources.get("glossary"))
    known_characters = load_known_characters(config.resources.get("character_voices"))
    checkpoint = Checkpoint(config.root / "checkpoint.json")

    batch_files = config.batch_files
    if limit_batches:
        batch_files = batch_files[:limit_batches]

    pending_files = [p for p in batch_files if not checkpoint.is_file_done(str(p))]
    if not pending_files:
        print(f"All {len(batch_files)} file(s) already fully committed in a previous run -- skipping.")
        return RunStats(
            total_entries=0,
            already_translated=0,
            tm_hits=0,
            unique_strings_sent_to_llm=0,
            llm_calls_made=0,
            validation_failures=0,
            review_queue_size=0,
            reviewed_and_repaired=0,
        )

    total_entries = 0
    already_translated_count = 0
    tm_hits = 0
    unique_sent_to_llm = 0
    llm_calls_made = 0
    validation_failures = 0
    tier1_repaired = 0
    all_review_items: list[ReviewItem] = []
    fidelity_samples = 0
    reviewed = 0
    fidelity_failures = 0
    newly_committed = 0
    files_left_unfinished = 0
    sync_latencies: list[float] = []
    wasted_retry_attempts = 0
    partial_recovery_calls = 0
    low_qa_calls = 0
    low_qa_repairs = 0
    low_qa_failures = 0
    high_qa_calls = 0
    high_qa_repairs = 0
    high_qa_failures = 0
    escalated_to_high_count = 0
    escalation_reasons: dict[str, int] = {}
    still_blocked_after_all_tiers = 0

    if config.provider.mode == "batch" and hasattr(provider, "submit_batch"):
        entries_by_file: dict[str, list[Entry]] = {}
        dedupe_result_by_file: dict[str, object] = {}
        all_batches = []
        for path in pending_files:
            try:
                file_entries = adapter.extract(path)
                classify_entries(file_entries, config, known_characters)
                flag_disputed_terms(file_entries, glossary)
                flag_expected_identity_terms(file_entries, glossary)
                attach_narrative_context(file_entries, config)
                entries_by_file[str(path)] = file_entries

                total_entries += len(file_entries)
                file_already_translated = [e for e in file_entries if not e.is_empty_or_stub]
                already_translated_count += len(file_already_translated)
                commit_to_tm(file_already_translated, tm, config.source_lang, config.target_lang, origin="human")

                dedupe_result = enrich_and_dedupe(file_entries, tm, config.source_lang, config.target_lang)
                tm_hits += dedupe_result.tm_hits
                unique_sent_to_llm += dedupe_result.total_unique_strings_to_translate
                dedupe_result_by_file[str(path)] = dedupe_result
                all_batches.extend(build_batches(dedupe_result.unique_groups, config, provider))
            except Exception as e:
                # Circuit breaker, extraction phase: a malformed input file
                # (bad JSON, unreadable encoding, whatever) must not stop
                # every OTHER file's batches from being built and submitted
                # in the same job. Excluded from entries_by_file entirely --
                # it's as if this file wasn't in pending_files this run;
                # next run retries it fresh (still not marked done).
                files_left_unfinished += 1
                print(
                    f"[{path.name}] unexpected error during extraction, file left unfinished, "
                    f"will retry on the next run: {type(e).__name__}: {e}"
                )
                continue

        if all_batches:
            print(f"{len(all_batches)} LLM call(s) needed ({checkpoint.progress_summary()} already on record)")

        prompt_by_category: dict[str, str] = {}
        requests = []
        for b in all_batches:
            if b.category not in prompt_by_category:
                prompt_by_category[b.category] = build_system_prompt_for_category(config, b.category, glossary)
            requests.append((prompt_by_category[b.category], build_user_payload(b)))

        fingerprint = fingerprint_batches(all_batches)
        pending = checkpoint.get_pending_job()
        if pending and pending["fingerprint"] == fingerprint:
            print(f"Reattaching to pending batch job {pending['job_id']} from an earlier run instead of resubmitting.")
            job_id = pending["job_id"]
        elif pending:
            raise RuntimeError(
                f"checkpoint.json has a pending batch job ({pending['job_id']}) but its fingerprint "
                "doesn't match what this run would submit now -- something changed (batch files "
                "edited, or the TM changed) since it was submitted. Resolve by hand: check that "
                "job's status with your provider directly, then either wait for it and clear "
                "checkpoint.json's pending_job field, or cancel the job before re-running."
            )
        elif requests:
            job_id = provider.submit_batch(requests, max_tokens=config.provider.max_output_tokens)
            checkpoint.set_pending_job(job_id, config.provider.name, fingerprint, len(requests))
            print(
                f"Submitted batch job {job_id} ({len(requests)} requests) — can take up to "
                f"{config.provider.timeout_s // 3600}h. Safe to stop this process now: re-running "
                "`locpipe run` will reattach instead of resubmitting."
            )
        else:
            job_id = None

        if job_id:
            provider.poll_batch(job_id, poll_interval_s=config.provider.poll_interval_s, timeout_s=config.provider.timeout_s)
            raw_results = provider.retrieve_batch_results(job_id, len(requests))
            checkpoint.clear_pending_job()
            for batch, raw in zip(all_batches, raw_results):
                if raw is None:
                    continue
                parsed, error = parse_and_validate_response(raw)
                if parsed is None:
                    continue
                for item in parsed:
                    batch.representatives[item["id"]].target = item["translation"]
                    batch.representatives[item["id"]].status = EntryStatus.MT_DRAFT
                    batch.representatives[item["id"]].origin = "mt"
                checkpoint.mark_batch_done(batch.category, len(batch.representatives))

        llm_calls_made = len(all_batches)

        for path in pending_files:
            if str(path) not in entries_by_file:
                continue  # already logged and counted as unfinished in the extraction loop above

            file_entries = entries_by_file[str(path)]
            dedupe_result = dedupe_result_by_file[str(path)]
            for tm_key, group in dedupe_result.unique_groups.items():
                rep = group[0]
                for e in group[1:]:
                    e.target = rep.target
                    e.status = rep.status
                    e.origin = rep.origin

            # is_empty_or_stub excludes entries that were already translated
            # (human-provided) at extraction time -- those never entered
            # enrich_and_dedupe's to_process list, so they never got a
            # status update off the dataclass's NOT_STARTED default, even
            # though they already have a perfectly good target. Checking
            # status alone here would (and, before this fix, did) mark a
            # file "unfinished" forever whenever it mixed already-translated
            # entries with new ones, since those entries can never NOT be
            # NOT_STARTED. Only an entry that actually needed MT and still
            # has no result counts as unresolved.
            unresolved = [e for e in file_entries if e.is_empty_or_stub and e.status == EntryStatus.NOT_STARTED]
            if unresolved:
                files_left_unfinished += 1
                print(
                    f"[{path.name}] {len(unresolved)} string(s) never got a translation result "
                    "(job didn't cover them or a response failed to parse) -- file left unfinished, "
                    "will retry on the next run."
                )
                continue

            try:
                result = _finalize_file(path, file_entries, adapter, config, glossary, provider, review_provider, tm, escalation_provider)
            except Exception as e:
                # Circuit breaker, finalize phase: same principle as the
                # extraction loop above -- an unexpected failure validating,
                # reviewing, or merging ONE file must not stop every other
                # already-translated file in this job from being finalized
                # and committed. Not marked done; retried fresh next run.
                files_left_unfinished += 1
                print(
                    f"[{path.name}] unexpected error finalizing (validate/review/merge/commit), file "
                    f"left unfinished, will retry on the next run: {type(e).__name__}: {e}"
                )
                continue
            validation_failures += result["validation_failures"]
            all_review_items.extend(result["review_items"])
            fidelity_samples += result["fidelity_samples"]
            reviewed += result["reviewed"]
            fidelity_failures += result["fidelity_failures"]
            newly_committed += result["newly_committed"]
            tier1_repaired += result["tier1_repaired"]
            low_qa_calls += result.get("low_qa_calls", 0)
            low_qa_repairs += result.get("low_qa_repairs", 0)
            high_qa_calls += result.get("high_qa_calls", 0)
            high_qa_repairs += result.get("high_qa_repairs", 0)
            escalated_to_high_count += result.get("escalated_to_high_count", 0)
            still_blocked_after_all_tiers += result.get("still_blocked", 0)
            for k, v in result.get("escalation_reasons", {}).items():
                escalation_reasons[k] = escalation_reasons.get(k, 0) + v
            checkpoint.mark_file_done(str(path))
    else:
        # Sync mode, windowed: batches from up to translate_file_window
        # pending files are pooled and translated in ONE concurrent pass
        # (see ProjectConfig.translate_file_window) instead of one file's
        # batches at a time. A project made of many small files (one per
        # quest/level/asset -- common) previously left most of
        # provider.max_concurrency idle, because concurrency only ever
        # applied within a single file's own batches.
        #
        # Correctness note: a batch that fails leaves its entries at
        # EntryStatus.NOT_STARTED (see _translate_batches_sync) while
        # every OTHER batch's entries -- in this file or any sibling file
        # in the same window -- still land normally. So attributing
        # failure via the per-file `unresolved` check below (same check
        # the batch-mode branch above already uses) correctly isolates
        # just the file(s) whose batch actually failed, not the whole
        # window -- one bad batch in file A must not take file B down
        # with it just because they shared a translate call.
        window_size = max(1, config.translate_file_window)
        for w_start in range(0, len(pending_files), window_size):
            window_files = pending_files[w_start : w_start + window_size]

            window_batches = []
            file_entries_by_path: dict[str, list[Entry]] = {}
            dedupe_by_path: dict[str, object] = {}

            for path in window_files:
                try:
                    file_entries = adapter.extract(path)
                    classify_entries(file_entries, config, known_characters)
                    flag_disputed_terms(file_entries, glossary)
                    flag_expected_identity_terms(file_entries, glossary)
                    attach_narrative_context(file_entries, config)

                    total_entries += len(file_entries)
                    file_already_translated = [e for e in file_entries if not e.is_empty_or_stub]
                    already_translated_count += len(file_already_translated)
                    commit_to_tm(file_already_translated, tm, config.source_lang, config.target_lang, origin="human")

                    dedupe_result = enrich_and_dedupe(file_entries, tm, config.source_lang, config.target_lang)
                    tm_hits += dedupe_result.tm_hits
                    unique_sent_to_llm += dedupe_result.total_unique_strings_to_translate

                    file_batches = build_batches(dedupe_result.unique_groups, config, provider)
                    file_entries_by_path[str(path)] = file_entries
                    dedupe_by_path[str(path)] = dedupe_result
                    window_batches.extend(file_batches)
                except Exception as e:
                    # Same circuit-breaker principle as the batch-mode branch
                    # above: a malformed file must not stop this window's
                    # other files from being extracted and translated.
                    files_left_unfinished += 1
                    print(
                        f"[{path.name}] unexpected error during extraction, file left unfinished, "
                        f"will retry on the next run: {type(e).__name__}: {e}"
                    )
                    continue

            if window_batches:
                print(
                    f"{len(window_batches)} LLM call(s) needed for this window "
                    f"({len(file_entries_by_path)} file(s))"
                )

            failed, lat, wasted, part_calls = asyncio.run(
                _translate_batches_sync(window_batches, config, glossary, provider, checkpoint, max_api_calls=max_api_calls)
            )
            sync_latencies.extend(lat)
            llm_calls_made += len(window_batches)
            wasted_retry_attempts += wasted
            partial_recovery_calls += part_calls

            for path in window_files:
                key = str(path)
                if key not in file_entries_by_path:
                    continue  # already logged and counted as unfinished above

                file_entries = file_entries_by_path[key]
                dedupe_result = dedupe_by_path[key]
                for tm_key, group in dedupe_result.unique_groups.items():
                    rep = group[0]
                    for e in group[1:]:
                        e.target = rep.target
                        e.status = rep.status
                        e.origin = rep.origin

                unresolved = [e for e in file_entries if e.is_empty_or_stub and e.status == EntryStatus.NOT_STARTED]
                if unresolved:
                    files_left_unfinished += 1
                    print(
                        f"[{path.name}] {len(unresolved)} string(s) never got a translation result "
                        "(a batch from this file failed after retries) -- file left unfinished, "
                        "will retry on the next run (anything that DID translate successfully in "
                        "this file is not yet in the TM, so it'll be retranslated too -- committing "
                        "an unvalidated partial result early isn't a safe trade)."
                    )
                    continue

                try:
                    result = _finalize_file(path, file_entries, adapter, config, glossary, provider, review_provider, tm, escalation_provider)
                except Exception as e:
                    # Circuit breaker: an unexpected failure anywhere in this
                    # file's processing -- extraction, translation, or the
                    # validate/review/merge/commit chain in _finalize_file --
                    # must not stop every OTHER file in this project from being
                    # processed in the same run. Not marked done; picked back up
                    # fresh (not resumed mid-way -- see _finalize_file's own
                    # docstring on why partial state isn't committed early) on
                    # the next `locpipe run`. This is the actual autonomy
                    # guarantee: one bad file degrades a run's coverage, it
                    # doesn't halt it.
                    files_left_unfinished += 1
                    print(
                        f"[{path.name}] unexpected error, file left unfinished, will retry on the "
                        f"next run: {type(e).__name__}: {e}"
                    )
                    continue
                validation_failures += result["validation_failures"]
                all_review_items.extend(result["review_items"])
                fidelity_samples += result["fidelity_samples"]
                reviewed += result["reviewed"]
                fidelity_failures += result["fidelity_failures"]
                newly_committed += result["newly_committed"]
                tier1_repaired += result["tier1_repaired"]
                low_qa_calls += result.get("low_qa_calls", 0)
                low_qa_repairs += result.get("low_qa_repairs", 0)
                high_qa_calls += result.get("high_qa_calls", 0)
                high_qa_repairs += result.get("high_qa_repairs", 0)
                escalated_to_high_count += result.get("escalated_to_high_count", 0)
                still_blocked_after_all_tiers += result.get("still_blocked", 0)
                for k, v in result.get("escalation_reasons", {}).items():
                    escalation_reasons[k] = escalation_reasons.get(k, 0) + v
                checkpoint.mark_file_done(str(path))

    write_review_queue(all_review_items, config.root / "review" / "needs_review.json")
    write_review_report(all_review_items, config.root / "review" / "review_report.md")

    if getattr(provider, "persists_to_tm", True):
        write_full_bilingual_report(
            tm.iter_all(),
            config.root / "review" / "full_bilingual_report.md",
            source_lang=config.source_lang,
            target_lang=config.target_lang,
        )
        consistency_issues = find_consistency_issues(tm.iter_all())
        write_consistency_report(
            consistency_issues,
            config.root / "review" / "consistency_report.md",
        )

    if files_left_unfinished:
        print(
            f"{files_left_unfinished} file(s) left unfinished this run -- re-run `locpipe run` "
            "to pick up exactly those (and only those; everything else stays committed)."
        )

    avg_latency = sum(sync_latencies) / len(sync_latencies) if sync_latencies else 0.0
    cache_stats = provider.cache_stats() if hasattr(provider, "cache_stats") else {}

    stats = RunStats(
        total_entries=total_entries,
        already_translated=already_translated_count,
        tm_hits=tm_hits,
        unique_strings_sent_to_llm=unique_sent_to_llm,
        llm_calls_made=llm_calls_made,
        validation_failures=validation_failures,
        tier1_repaired=tier1_repaired,
        review_queue_size=len(all_review_items),
        reviewed_and_repaired=reviewed,
        fidelity_samples=fidelity_samples,
        fidelity_failures=fidelity_failures,
        newly_committed_to_tm=newly_committed,
        wasted_retry_attempts=wasted_retry_attempts,
        partial_recovery_calls=partial_recovery_calls,
        avg_translation_latency_s=avg_latency,
        cache_stats=cache_stats,
        low_qa_calls=low_qa_calls,
        low_qa_repairs=low_qa_repairs,
        high_qa_calls=high_qa_calls,
        high_qa_repairs=high_qa_repairs,
        escalated_to_high_count=escalated_to_high_count,
        escalation_reasons=escalation_reasons,
        still_blocked_after_all_tiers=still_blocked_after_all_tiers,
    )
    write_stats(stats, config.root / "stats.json")
    tm.close()
    return stats


def plan(
    config: ProjectConfig,
    *,
    provider: Any = None,
    limit_batches: int | None = None,
    include_totals: bool = True,
) -> dict:
    """Read-only: no LLM calls, no writes to the TM or batch files on disk.
    Runs the exact same per-file extract -> normalize -> classify -> dedupe ->
    TM-lookup -> batch-planning steps run() does, then stops, so you
    get real numbers for a project before spending anything —
    how much your actual duplicate ratio buys you, how many calls a
    run will really take, and a ballpark token estimate.
    When include_totals=False, skips Pass 1 full-file extraction for lightweight
    call budgeting.
    """
    adapter = get_adapter(config.format, {**config.format_options, "source_lang": config.source_lang, "target_lang": config.target_lang})
    glossary = load_glossary(config.resources.get("glossary"))
    known_characters = load_known_characters(config.resources.get("character_voices"))
    checkpoint = Checkpoint(config.root / "checkpoint.json")

    batch_files = config.batch_files
    if limit_batches:
        batch_files = batch_files[:limit_batches]

    pending_files = [p for p in batch_files if not checkpoint.is_file_done(str(p))]

    # Read-only TM — open_readonly() enforces the "no writes to the TM"
    # docstring promise at the SQLite level (URI ?mode=ro), not just by
    # convention. The old conditional (real TM when db exists, :memory:
    # otherwise) silently wrote pre-existing translations into the real DB
    # via commit_to_tm(), which violated the read-only contract.
    tm = TranslationMemory.open_readonly(config.tm_db_path)

    total_entries = 0
    already_translated_count = 0
    tm_hits = 0
    unique_strings_needing_translation = 0
    all_batches = []

    try:
        if include_totals:
            # Pass 1: ALL files — compute total_entries / already_translated for the
            # full-project summary, and seed the in-memory TM so that cross-file TM
            # hits are counted correctly in pass 2 even for already-finished files.
            for path in batch_files:
                file_entries = adapter.extract(path)
                total_entries += len(file_entries)
                classify_entries(file_entries, config, known_characters)
                flag_disputed_terms(file_entries, glossary)
                flag_expected_identity_terms(file_entries, glossary)
                attach_narrative_context(file_entries, config)
                file_already_translated = [e for e in file_entries if not e.is_empty_or_stub]
                already_translated_count += len(file_already_translated)
                commit_to_tm(file_already_translated, tm, config.source_lang, config.target_lang, origin="human")

        # Pass 2: PENDING files only — these are what a real run() would actually
        # process, so the LLM call count and token estimates reflect remaining work.
        for path in pending_files:
            file_entries = adapter.extract(path)
            classify_entries(file_entries, config, known_characters)
            flag_disputed_terms(file_entries, glossary)
            flag_expected_identity_terms(file_entries, glossary)
            attach_narrative_context(file_entries, config)

            dedupe_result = enrich_and_dedupe(file_entries, tm, config.source_lang, config.target_lang)
            tm_hits += dedupe_result.tm_hits
            unique_strings_needing_translation += dedupe_result.total_unique_strings_to_translate

            file_batches = build_batches(dedupe_result.unique_groups, config, provider)
            all_batches.extend(file_batches)
    finally:
        tm.close()

    def est_tokens(text: str) -> int:
        return max(1, len(text) // 4)

    prompt_tokens_by_category: dict[str, int] = {}
    calls_by_category: dict[str, int] = {}
    source_tokens_by_category: dict[str, int] = {}
    for b in all_batches:
        if b.category not in prompt_tokens_by_category:
            prompt_tokens_by_category[b.category] = est_tokens(
                build_system_prompt_for_category(config, b.category, glossary)
            )
        calls_by_category[b.category] = calls_by_category.get(b.category, 0) + 1
        source_tokens_by_category[b.category] = source_tokens_by_category.get(
            b.category, 0
        ) + sum(est_tokens(e.source) for e in b.representatives)

    uncached_prompt_tokens = sum(prompt_tokens_by_category.values())
    cached_read_tokens = sum(
        prompt_tokens_by_category[cat] * max(0, calls - 1) for cat, calls in calls_by_category.items()
    )
    unique_source_tokens = sum(source_tokens_by_category.values())
    estimated_output_tokens = int(unique_source_tokens * 1.3)

    estimated_realistic_input_tokens = uncached_prompt_tokens + cached_read_tokens + unique_source_tokens
    caching_note = "Antigravity CLI runs as isolated subprocesses without persistent prompt-caching, so each batch pays full system prompt tokens."

    ctx_limit = getattr(config.provider, "context_window_tokens", None) or getattr(provider, "context_window_tokens", None)
    context_window_warning = None
    if ctx_limit is not None:
        for b in all_batches:
            p_tok = prompt_tokens_by_category.get(b.category, 0)
            s_tok = sum(est_tokens(e.source) for e in b.representatives)
            if p_tok + s_tok > ctx_limit:
                context_window_warning = (
                    f"Warning: Batch in category '{b.category}' estimated at ~{p_tok + s_tok} tokens, "
                    f"which exceeds declared context_window_tokens limit of {ctx_limit}."
                )
                break

    return {
        "total_entries": total_entries,
        "already_translated": already_translated_count,
        "tm_hits": tm_hits,
        "unique_strings_needing_translation": unique_strings_needing_translation,
        "llm_calls_needed": len(all_batches),
        "calls_by_category": calls_by_category,
        "estimated_uncached_input_tokens": uncached_prompt_tokens + unique_source_tokens,
        "estimated_cache_read_tokens": cached_read_tokens,
        "estimated_output_tokens": estimated_output_tokens,
        "estimated_realistic_input_tokens": estimated_realistic_input_tokens,
        "caching_note": caching_note,
        "context_window_warning": context_window_warning,
        "pending_files_count": len(pending_files),
    }
