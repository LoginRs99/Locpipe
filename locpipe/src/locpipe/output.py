"""Phase 15. What the redesign's "Metrics" section asked for, computed
from objects the pipeline already built rather than re-derived.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from typing import Iterable
from .models import TMRecord
from .review_queue import ReviewItem


@dataclass
class RunStats:
    total_entries: int
    already_translated: int
    tm_hits: int
    unique_strings_sent_to_llm: int
    llm_calls_made: int
    validation_failures: int
    review_queue_size: int
    reviewed_and_repaired: int
    tier1_repaired: int = 0
    fidelity_samples: int = 0
    fidelity_failures: int = 0
    newly_committed_to_tm: int = 0
    # Full-payload retries caused by a truncated/invalid response -- each
    # one resends the ENTIRE batch's input for zero usable output. 0 on a
    # healthy run. If this is ever non-trivial relative to llm_calls_made,
    # your batch_size for the affected category is very likely too large
    # for provider.max_output_tokens -- see config.py's ProviderConfig
    # docstring for the sizing math.
    wasted_retry_attempts: int = 0
    partial_recovery_calls: int = 0
    avg_translation_latency_s: float = 0.0
    cache_stats: dict = field(default_factory=dict)
    low_qa_calls: int = 0
    low_qa_repairs: int = 0
    low_qa_failures: int = 0
    high_qa_calls: int = 0
    high_qa_repairs: int = 0
    high_qa_failures: int = 0
    escalated_to_high_count: int = 0
    escalation_reasons: dict[str, int] = field(default_factory=dict)
    # Entries that exhausted Tier 1 (mechanical repair), Tier 2 (low-QA
    # review), and Tier 3 (high-QA/escalation review) and still failed
    # validation. With no human review loop, these ship as explicit
    # source-language passthrough (origin="untranslated_fallback") rather
    # than a blank or broken string -- see pipeline.py's _finalize_file.
    # Non-zero here is a signal worth checking after every run, not a
    # silent fallback: it means these specific strings need a human's
    # attention even though the pipeline itself never blocks on them.
    still_blocked_after_all_tiers: int = 0

    @property
    def strings_saved_by_dedup_and_tm(self) -> int:
        return self.tm_hits + max(
            0,
            (self.total_entries - self.already_translated)
            - self.unique_strings_sent_to_llm
            - self.tm_hits,
        )

    def summary(self) -> str:
        base = (
            f"{self.total_entries} entries total | "
            f"{self.already_translated} already translated | "
            f"{self.tm_hits} filled from TM | "
            f"{self.unique_strings_sent_to_llm} unique strings sent to the LLM "
            f"(in {self.llm_calls_made} calls, {self.strings_saved_by_dedup_and_tm} more "
            f"saved by dedup+TM reuse) | "
            f"{self.validation_failures} validation failures "
            f"({self.tier1_repaired} auto-repaired without a review call) | "
            f"{self.review_queue_size} routed to review, {self.reviewed_and_repaired} repaired | "
            f"Fidelity sampling: {self.fidelity_samples} sampled, {self.fidelity_failures} repaired | "
            f"{self.newly_committed_to_tm} new entries committed to TM for future runs"
        )
        if self.low_qa_calls or self.high_qa_calls:
            base += (
                f" | QA: Low={self.low_qa_calls} (repaired={self.low_qa_repairs}), "
                f"High={self.high_qa_calls} (repaired={self.high_qa_repairs}, escalated={self.escalated_to_high_count})"
            )
        if self.avg_translation_latency_s:
            base += f" | avg {self.avg_translation_latency_s:.1f}s/call"
        if self.wasted_retry_attempts:
            base += (
                f" | ⚠ {self.wasted_retry_attempts} wasted full-payload retry attempt(s) -- "
                f"a batch_size is likely too large for max_output_tokens, see project.yaml"
            )
        if self.partial_recovery_calls:
            base += f" | {self.partial_recovery_calls} partial recovery call(s)"
        if self.still_blocked_after_all_tiers:
            base += (
                f" | ⚠ {self.still_blocked_after_all_tiers} entries shipped as source-language "
                f"passthrough (origin=untranslated_fallback) after exhausting every QA tier -- "
                f"review these specifically before calling the title done"
            )
        if self.cache_stats:
            base += f" | cache: {self.cache_stats}"
        return base


def write_stats(stats: RunStats, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(stats)
    data["strings_saved_by_dedup_and_tm"] = stats.strings_saved_by_dedup_and_tm
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def write_review_report(items: list[ReviewItem], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Review report — {len(items)} items flagged", ""]
    for item in items:
        lines.append(f"## {item.entry.key} (confidence {item.confidence:.2f})")
        lines.append(f"- source: {item.entry.source!r}")
        lines.append(f"- translation: {item.entry.target!r}")
        for issue in item.validation.all_issues:
            lines.append(f"  - [{issue.severity.value}] {issue.message}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_full_bilingual_report(
    records: Iterable[tuple[str, TMRecord] | TMRecord],
    path: Path,
    source_lang: str = "en",
    target_lang: str = "hu",
) -> None:
    """Write complete bilingual review report covering every translated string in the TM."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[TMRecord] = []
    for item in records:
        rec = item[1] if isinstance(item, tuple) else item
        rows.append(rec)

    # Sort deterministically by category, source
    rows.sort(key=lambda r: (r.category or "", r.source))

    lines = [
        f"# Full Bilingual Translation Export — {len(rows)} unique strings",
        "",
        f"**Source Language**: `{source_lang}` | **Target Language**: `{target_lang}`",
        "",
        "| Source | Target | Category | Origin | Quality |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        src = r.source.replace("\n", " ").replace("|", "\\|")
        tgt = r.translation.replace("\n", " ").replace("|", "\\|")
        cat = (r.category or "").replace("|", "\\|")
        origin = (r.origin or "").replace("|", "\\|")
        quality = f"{r.quality_score:.2f}" if r.quality_score is not None else "-"
        lines.append(f"| {src} | {tgt} | {cat} | {origin} | {quality} |")

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_consistency_report(issues: list[dict], path: Path) -> None:
    """Write fuzzy translation consistency report (zero-cost difflib scan)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Translation Consistency Report — {len(issues)} potential inconsistencies flagged",
        "",
        "> [!NOTE]",
        "> This report is advisory only (zero API cost, computed via difflib similarity).",
        "> Highly similar source strings with different translations are flagged for human review.",
        "> Variations may be intentional depending on character voice, context, or category.",
        "",
        "| Similarity | Source A | Target A | Source B | Target B | Categories |",
        "|---|---|---|---|---|---|",
    ]
    for iss in issues:
        sim_pct = f"{iss['similarity'] * 100:.1f}%"
        s_a = iss["source_a"].replace("\n", " ").replace("|", "\\|")
        t_a = iss["target_a"].replace("\n", " ").replace("|", "\\|")
        s_b = iss["source_b"].replace("\n", " ").replace("|", "\\|")
        t_b = iss["target_b"].replace("\n", " ").replace("|", "\\|")
        cats = f"{iss.get('category_a', '')} / {iss.get('category_b', '')}".replace("|", "\\|")
        lines.append(f"| {sim_pct} | {s_a} | {t_a} | {s_b} | {t_b} | {cats} |")

    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
