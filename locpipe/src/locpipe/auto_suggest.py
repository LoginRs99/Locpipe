"""AI-powered auto-discovery for project style presets, character voices, and glossary."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional

from .adapters.registry import get_adapter
from .config import ProjectConfig
from .models import Entry
from .presets import LANG_STYLE_PRESETS
from .providers.base import TranslationProvider


@dataclass
class SuggestionResult:
    recommended_preset: str
    preset_rationale: str
    style_guide_content: str
    glossary_items: list[dict[str, str]] = field(default_factory=list)
    character_voices: list[dict[str, str]] = field(default_factory=list)
    suggested_path_excludes: list[str] = field(default_factory=list)
    has_detected_speakers: bool = False
    recommended_profile: str = "fast"


def is_safe_path_exclude(pattern_str: str, entries: List[Entry]) -> bool:
    """Conservative safety check: ensure an exclusion regex does NOT match actual player dialogue."""
    try:
        rx = re.compile(pattern_str)
    except re.error:
        return False

    for e in entries:
        json_path_str = (
            ".".join(str(p) for p in e.extra.get("json_path", []))
            if e.extra.get("json_path")
            else e.key
        )
        if rx.search(json_path_str):
            src = e.source.strip()
            # If the candidate path holds multi-word narrative dialogue, reject it to be safe
            if len(src.split()) >= 4 or (src.endswith((".", "!", "?")) and len(src.split()) >= 2):
                return False
    return True


async def analyze_project_and_suggest(
    config: ProjectConfig,
    provider: TranslationProvider,
    sample_size: int = 40,
) -> SuggestionResult:
    adapter = get_adapter(config.format, config.format_options)
    all_entries: List[Entry] = []

    for path in config.batch_files:
        try:
            entries = adapter.extract(path)
            all_entries.extend(entries)
        except Exception:
            continue

    if not all_entries:
        raise ValueError(f"No entries could be extracted from batch files in '{config.project}'.")

    # Sample representative strings with paths
    sample_entries = all_entries[:sample_size]
    sample_texts = [
        {
            "path": (
                ".".join(str(p) for p in e.extra.get("json_path", []))
                if e.extra.get("json_path")
                else e.key
            ),
            "source": e.source,
            "notes": e.notes,
            "category": e.category,
        }
        for e in sample_entries
        if e.source and len(e.source.strip()) > 1
    ]

    preset_names = list(LANG_STYLE_PRESETS.keys())
    preset_list_str = "\n".join(f"- {name}" for name in preset_names)

    system_prompt = (
        "You are an expert video game localization director for English -> Hungarian.\n"
        "Analyze the provided sample game strings, JSON paths, and game title to determine optimal localization resources.\n\n"
        "Available Language Style Presets:\n"
        f"{preset_list_str}\n\n"
        "Respond ONLY with a JSON object with this exact schema:\n"
        "{\n"
        '  "recommended_preset": "Exact name of one of the available presets above",\n'
        '  "preset_rationale": "Short explanation of why this preset fits the game",\n'
        '  "glossary": [\n'
        '    {"source": "English term/name", "target": "Hungarian translation/proper noun", "category": "Proper noun/UI/Mechanics", "note": "Context"}\n'
        "  ],\n"
        '  "character_voices": [\n'
        '    {"character": "Character name", "register": "informal/formal", "traits": "Short description of speech style"}\n'
        "  ],\n"
        '  "suggested_path_excludes": [\n'
        '    "^regex_pattern_for_internal_metadata_or_debug_paths_only"\n'
        "  ]\n"
        "}\n\n"
        "IMPORTANT FOR PATH EXCLUDES:\n"
        "Only suggest path regexes for branches that are UNAMBIGUOUSLY internal developer metadata, debug tools, telemetry, or foreign language array indices.\n"
        "NEVER suggest excluding paths that contain player-facing dialogue, UI labels, subtitles, or quests."
    )

    user_payload = json.dumps(
        {
            "game_title": config.project,
            "sample_strings": sample_texts,
        },
        ensure_ascii=False,
        indent=2,
    )

    raw_response = await provider.complete(system_prompt, user_payload, effort="low")

    text = raw_response.strip().strip("`")
    if text.startswith("json"):
        text = text[4:].strip()

    start_idx = text.find("{")
    end_idx = text.rfind("}")
    if start_idx != -1 and end_idx != -1:
        text = text[start_idx : end_idx + 1]

    data = json.loads(text)

    rec_preset = data.get("recommended_preset", preset_names[0])
    if rec_preset not in LANG_STYLE_PRESETS:
        # Match closest
        for name in preset_names:
            if name.split()[0].lower() in rec_preset.lower():
                rec_preset = name
                break
        else:
            rec_preset = preset_names[0]

    style_guide_content = LANG_STYLE_PRESETS[rec_preset]

    # Validate suggested path excludes for safety
    raw_excludes = data.get("suggested_path_excludes", [])
    safe_excludes = []
    if isinstance(raw_excludes, list):
        for pattern in raw_excludes:
            if isinstance(pattern, str) and pattern.strip():
                p_str = pattern.strip()
                if is_safe_path_exclude(p_str, all_entries):
                    safe_excludes.append(p_str)

    # Detect whether the game dump has explicit speaker info or character names in keys
    has_speaker_field = any(bool(e.speaker and e.speaker.strip()) for e in all_entries[:500])
    has_speaker_notes = any(any(isinstance(n, str) and "speaker:" in n.lower() for n in (e.notes or [])) for e in all_entries[:500])

    char_names = {c.get("character", "").lower().strip() for c in data.get("character_voices", []) if c.get("character")}
    has_char_keys = False
    if char_names:
        for e in all_entries[:500]:
            k_lower = (e.key or "").lower()
            if any(cn in k_lower for cn in char_names if len(cn) >= 3):
                has_char_keys = True
                break

    has_detected_speakers = bool(has_speaker_field or has_speaker_notes or has_char_keys)

    return SuggestionResult(
        recommended_preset=rec_preset,
        preset_rationale=data.get("preset_rationale", ""),
        style_guide_content=style_guide_content,
        glossary_items=data.get("glossary", []),
        character_voices=data.get("character_voices", []),
        suggested_path_excludes=safe_excludes,
        has_detected_speakers=has_detected_speakers,
        recommended_profile="fast",
    )
