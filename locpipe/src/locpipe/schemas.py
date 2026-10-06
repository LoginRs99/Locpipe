"""Phase 9. The LLM only ever sees `source` (never file/namespace/key)
and only ever returns {id, translation} pairs (never free text) — the
two constraints that make Phase 8's big batches possible at all.

The system prompt is built PER CATEGORY, not per batch, and is
identical for every batch that shares a category — that's what makes
it a stable prefix a caching-aware provider can reuse instead of
re-paying for it on every one of a project's batches (see
providers/gemini_provider.py).
It also uses the FULL glossary and character-voices file by default,
rather than per-batch pruning, when speakers=None -- pruning made
sense when every batch paid full price for those tokens; once a cache
picks up that cost after the first call in a category, sending the
whole thing once and reusing it is strictly cheaper for any project
with more than a couple of batches per category, which is every real
project. pipeline.py passes an actual (possibly per-batch) glossary
and speakers set instead for providers with no caching continuity
between calls -- see TranslationProvider.prefers_per_batch_context.

Assembled from a project's actual resource files at call time — this
module has no MindsEye-specific text in it, only assembly logic.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

import jsonschema

from .batcher import TranslationBatch
from .character_voices import load_character_voice_rows, prune_character_voices_for_batch
from .config import ProjectConfig
from .glossary import GlossaryTerm, format_for_prompt
from .models import Entry
from .prompt_builder import fill, load_template, toggle_section, get_register_instruction, get_placeholder_rules

RESPONSE_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "translation": {"type": "string"},
        },
        "required": ["id", "translation"],
        "additionalProperties": False,
    },
}


# TODO(human): consider a shorter lang-style.md/anti-fabrication-checklist.md variant for the no-cache path
@lru_cache(maxsize=None)
def _read(path: Optional[Path]) -> str:
    """Cached: lang-style.md / anti-fabrication-checklist.md are static
    project resources for the lifetime of a run -- re-reading them off
    disk on every single batch (this used to happen once per batch call,
    not once per category) was pure waste on a project with hundreds or
    thousands of batches.
    """
    if path is None or not path.exists():
        return "(none provided)"
    text = path.read_text(encoding="utf-8").strip()
    return text if text else "(none provided)"


def build_system_prompt_for_category(
    config: ProjectConfig,
    category_name: str,
    glossary: list[GlossaryTerm],
    speakers: Optional[set[str]] = None,
    correction_mode: bool = False,
) -> str:
    """speakers=None (the default) means "use the full character-voices
    file, unpruned" -- correct for a cache-capable provider, where the
    category-level system prompt is a stable string reused across every
    batch in that category and a cache picks up the repeat cost, same
    reasoning as the full glossary above. Pass an actual set (even an
    empty one) to prune the voice bible down to just those characters --
    what pipeline.py does for providers with no caching continuity
    between calls (see TranslationProvider.prefers_per_batch_context),
    where sending the whole cast's bible on every one-shot call would be
    paying full price for characters that aren't even in this batch.
    """
    rule = next((c for c in config.categories if c.name == category_name), None)
    needs_voice = bool(rule and rule.needs_character_voice)

    template = load_template("translate.md")
    is_software = (getattr(config, "project_type", "game") == "software")
    template = toggle_section(
        template, "%%SOFTWARE_MODE_SECTION_START%%", "%%SOFTWARE_MODE_SECTION_END%%", keep=is_software
    )
    template = toggle_section(
        template, "%%CHARACTER_VOICE_SECTION_START%%", "%%CHARACTER_VOICE_SECTION_END%%", keep=needs_voice
    )
    template = toggle_section(
        template, "%%CORRECTION_MODE_SECTION_START%%", "%%CORRECTION_MODE_SECTION_END%%", keep=correction_mode
    )
    gate_naturalness = getattr(config, "gate_naturalness", None)
    if gate_naturalness is None:
        gate_naturalness = not needs_voice
    keep_naturalness = needs_voice if gate_naturalness else True
    template = toggle_section(
        template, "%%NATURALNESS_SECTION_START%%", "%%NATURALNESS_SECTION_END%%", keep=keep_naturalness
    )

    character_voices = ""
    if needs_voice:
        cv_path = config.resources.get("character_voices")
        if speakers is not None:
            preamble, rows = load_character_voice_rows(cv_path)
            character_voices = prune_character_voices_for_batch(preamble, rows, speakers)
        else:
            character_voices = _read(cv_path)

    return fill(
        template,
        source_lang=config.source_lang,
        target_lang=config.target_lang,
        register_instruction=get_register_instruction(getattr(config, "target_register", "informal"), target_lang=config.target_lang),
        target_placeholder_rules=get_placeholder_rules(config.source_lang, config.target_lang),
        category=category_name,
        glossary=format_for_prompt(glossary),
        style_guide=_read(config.resources.get("lang_style")),
        anti_fabrication=_read(config.resources.get("anti_fabrication_checklist")),
        character_voices=character_voices,
    )


def _filter_notes_for_payload(notes: list[str]) -> list[str]:
    """Filter noise notes at the LLM payload boundary only.

    Drops notes with prefix 'asset:' or 'path:' (case-sensitive exact prefix with colon).
    Truncates surviving notes to 200 chars and caps at 5 notes per item.
    """
    filtered = []
    for note in notes:
        if note.startswith("asset:") or note.startswith("path:"):
            continue
        filtered.append(note[:200])
        if len(filtered) == 5:
            break
    return filtered


def build_user_payload(batch: TranslationBatch) -> str:
    items = []
    for i, e in enumerate(batch.representatives):
        item = {"id": i, "source": e.source}
        if e.speaker:
            item["speaker"] = e.speaker
        if e.max_length:
            item["max_length"] = e.max_length
        if e.notes:
            filtered = _filter_notes_for_payload(e.notes)
            if filtered:
                item["notes"] = filtered
        if e.preceding_context:
            item["preceding_context"] = e.preceding_context
        items.append(item)
    return json.dumps(items, ensure_ascii=False)


def build_retry_payload(entries: list[Entry], issues_by_key: dict[str, list[str]]) -> str:
    """Same shape as build_user_payload, plus previous_attempt/issue on
    each item -- see translate.md's CORRECTION MODE section. Used for
    Tier-1 deterministic-validation retries only (pipeline.py's
    _tier1_repair): a mechanical, no-judgment-required correction
    request that reuses the cheap bulk-translate call instead of routing
    straight to the expensive review agent for something a validator
    already pinned down exactly.
    """
    items = []
    for i, e in enumerate(entries):
        item = {"id": i, "source": e.source, "previous_attempt": e.target}
        if e.speaker:
            item["speaker"] = e.speaker
        if e.max_length:
            item["max_length"] = e.max_length
        if e.notes:
            filtered = _filter_notes_for_payload(e.notes)
            if filtered:
                item["notes"] = filtered
        issues = issues_by_key.get(e.key)
        if issues:
            item["issue"] = "; ".join(issues)
        items.append(item)
    return json.dumps(items, ensure_ascii=False)


class ParseResult(tuple):
    """2-tuple (parsed, error) with an extra `salvaged` boolean attribute.
    Unpacks as `parsed, error = parse_and_validate_response(text)` for full backward compatibility,
    while exposing `result.salvaged` to tell callers if bracket salvage fired on a truncated response.
    """
    parsed: Optional[list[dict]]
    error: Optional[str]
    salvaged: bool

    def __new__(cls, parsed: Optional[list[dict]], error: Optional[str], salvaged: bool = False):
        obj = super().__new__(cls, (parsed, error))
        obj.parsed = parsed
        obj.error = error
        obj.salvaged = salvaged
        return obj


def parse_and_validate_response(raw_text: str) -> ParseResult:
    """Returns ParseResult(parsed, error, salvaged). error is None on success."""
    import re
    text = raw_text.strip()
    if "```" in text:
        m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
        if m:
            text = m.group(1).strip()
        else:
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
    s_idx = text.find("[")
    e_idx = text.rfind("]")
    if s_idx != -1 and e_idx != -1 and e_idx > s_idx:
        text = text[s_idx : e_idx + 1]
    elif s_idx != -1:
        text = text[s_idx:]
    salvaged = False
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        text_clean = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", text)
        try:
            data = json.loads(text_clean, strict=False)
        except json.JSONDecodeError:
            # Fallback 1: repair unescaped trailing backslashes before closing quotes, e.g. "foo\" -> "foo\\"
            text_bs = re.sub(r'\\(?="[,\s\}\]])', r'\\\\', text_clean)
            try:
                data = json.loads(text_bs, strict=False)
            except json.JSONDecodeError:
                t_strip = text_bs.rstrip()
                if not t_strip.endswith("]"):
                    if t_strip.endswith("}"):
                        t_strip += "]"
                    elif t_strip.endswith('"'):
                        t_strip += "}]"
                    else:
                        t_strip += '"}]'
                try:
                    data = json.loads(t_strip, strict=False)
                    salvaged = True
                except json.JSONDecodeError as e:
                    return ParseResult(None, f"invalid JSON: {e}", False)
    try:
        jsonschema.validate(data, RESPONSE_SCHEMA)
    except jsonschema.ValidationError as e:
        return ParseResult(None, f"schema mismatch: {e.message}", False)
    ids = [item["id"] for item in data]
    if len(ids) != len(set(ids)):
        return ParseResult(None, "duplicate ids in response", False)
    return ParseResult(data, None, salvaged)
