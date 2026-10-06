"""Phase 8. One representative Entry per unique tm_key group goes into
a batch — never one request per string, never the whole project in
one request either. Batch size is per-category (project.yaml), not a
single constant, because a dialogue entry carrying character-voice
context costs more tokens per string than a one-word UI label.

When a category sets narrative_boundary_field (see narrative_context.py),
batches are built by packing whole boundary groups (e.g. a scene's
lines) rather than chunking by flat count — a scene only gets split
across batches if it's bigger than batch_size on its own, never just
because it happened to land on a chunk boundary. Categories without a
boundary field get the old flat-chunk behavior unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .config import ProjectConfig
from .models import Entry
from .narrative_context import group_key_for_batching


@dataclass
class TranslationBatch:
    category: str
    representatives: list[Entry]   # one entry per unique tm_key in this batch


def _entry_output_tokens(
    e: Entry,
    config: Optional[ProjectConfig] = None,
    expansion_ratio: Optional[float] = None,
) -> int:
    base = max(20, int(len(e.source) / 3.0)) + 15
    ratio = expansion_ratio
    if ratio is None and config is not None and getattr(config, "categories", None):
        for c in config.categories:
            if c.name == e.category and getattr(c, "max_expansion_ratio", None) is not None:
                ratio = c.max_expansion_ratio
                break
    if ratio is None and getattr(e, "extra", None) and e.extra.get("max_expansion_ratio") is not None:
        ratio = e.extra["max_expansion_ratio"]
    if ratio is None:
        ratio = 1.5
    return int(base * ratio)


def _entry_input_chars(e: Entry) -> int:
    return len(e.source) + len(e.speaker or "") + 35


def _split_oversized_group(
    group: list[Entry],
    max_entries: int,
    max_output_tokens: int,
    max_input_chars: Optional[int] = None,
    expansion_ratio: float = 1.5,
) -> list[list[Entry]]:
    subgroups: list[list[Entry]] = []
    current: list[Entry] = []
    curr_tokens = 0
    curr_chars = 0

    for e in group:
        e_tokens = _entry_output_tokens(e, expansion_ratio=expansion_ratio)
        e_chars = _entry_input_chars(e)
        if current and (
            len(current) + 1 > max_entries
            or curr_tokens + e_tokens > max_output_tokens
            or (max_input_chars is not None and curr_chars + e_chars > max_input_chars)
        ):
            subgroups.append(current)
            current = []
            curr_tokens = 0
            curr_chars = 0

        current.append(e)
        curr_tokens += e_tokens
        curr_chars += e_chars

    if current:
        subgroups.append(current)
    return subgroups


def _pack_groups_dynamically(
    ordered_groups: list[list[Entry]],
    max_entries: int,
    max_output_tokens: int = 8000,
    max_input_chars: Optional[int] = None,
    expansion_ratio: float = 1.5,
) -> list[list[Entry]]:
    """Dynamic bin-packing considering entry count, estimated output tokens,
    and provider input character limits when applicable.
    """
    batches: list[list[Entry]] = []
    current: list[Entry] = []
    curr_tokens = 0
    curr_chars = 0

    for group in ordered_groups:
        g_tokens = sum(_entry_output_tokens(e, expansion_ratio=expansion_ratio) for e in group)
        g_chars = sum(_entry_input_chars(e) for e in group)

        if (
            len(group) > max_entries
            or g_tokens > max_output_tokens
            or (max_input_chars is not None and g_chars > max_input_chars)
        ):
            if current:
                batches.append(current)
                current = []
                curr_tokens = 0
                curr_chars = 0
            subgroups = _split_oversized_group(group, max_entries, max_output_tokens, max_input_chars, expansion_ratio=expansion_ratio)
            batches.extend(subgroups)
            continue

        if current and (
            len(current) + len(group) > max_entries
            or curr_tokens + g_tokens > max_output_tokens
            or (max_input_chars is not None and curr_chars + g_chars > max_input_chars)
        ):
            batches.append(current)
            current = []
            curr_tokens = 0
            curr_chars = 0

        current.extend(group)
        curr_tokens += g_tokens
        curr_chars += g_chars

    if current:
        batches.append(current)
    return batches


def build_batches(
    unique_groups: dict[str, list[Entry]],
    config: ProjectConfig,
    provider: Optional[Any] = None,
) -> list[TranslationBatch]:
    by_category: dict[str, list[Entry]] = {}
    for group in unique_groups.values():
        rep = group[0]
        by_category.setdefault(rep.category or "default", []).append(rep)

    batches: list[TranslationBatch] = []
    max_output_tokens_cap = config.provider.batch_output_token_cap or min(8000, config.provider.max_output_tokens - 1000)
    provider_max_input_chars = provider.max_input_chars if provider is not None else 24000

    for category_name, reps in by_category.items():
        rule = next((c for c in config.categories if c.name == category_name), None)
        max_entries = rule.batch_size if rule else 2000
        category_ratio = (
            rule.max_expansion_ratio
            if (rule and getattr(rule, "max_expansion_ratio", None) is not None)
            else getattr(config, "max_expansion_ratio", 1.5)
        ) or 1.5

        if provider_max_input_chars is not None:
            try:
                from .schemas import build_system_prompt_for_category
                prompt_len = len(build_system_prompt_for_category(config, category_name, []))
            except Exception:
                prompt_len = 0
            effective_input_chars: Optional[int] = max(1000, provider_max_input_chars - prompt_len)
        else:
            effective_input_chars = None

        if rule and rule.narrative_boundary_field:
            boundary_groups: dict[str, list[Entry]] = {}
            for rep in reps:
                key = group_key_for_batching(rep, rule)
                boundary_groups.setdefault(key, []).append(rep)
            ordered_group_list = list(boundary_groups.values())
        else:
            ordered_group_list = [[r] for r in reps]

        chunks = _pack_groups_dynamically(
            ordered_group_list,
            max_entries=max_entries,
            max_output_tokens=max_output_tokens_cap,
            max_input_chars=effective_input_chars,
            expansion_ratio=category_ratio,
        )

        for chunk in chunks:
            batches.append(TranslationBatch(category=category_name, representatives=chunk))
    return batches

