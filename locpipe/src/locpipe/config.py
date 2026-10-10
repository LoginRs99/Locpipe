"""Everything project-specific lives in one project.yaml, loaded here.

locpipe itself (everything under locpipe/) contains zero references to
any specific game, language pair, or format. "MindsEye" is just the
name of one directory under projects/ with a project.yaml in it.
Adding a second project — different game, different language pair,
different engine format — never requires touching pipeline code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml


@dataclass
class CategoryRule:
    """One rule in the classification cascade. First match wins.

    Mirrors what MindsEye's character-voice-schema.md already does by
    hand: try an explicit signal first (speaker field), then a textual
    signal (notes / key pattern), then fall back to a default — never
    guess silently.
    """

    name: str
    match_key_regex: Optional[str] = None
    match_notes_regex: Optional[str] = None
    match_source_regex: Optional[str] = None
    match_speaker_present: Optional[bool] = None
    needs_character_voice: bool = False
    batch_size: int = 100
    is_default: bool = False
    # Override the project-wide confidence.max_expansion_ratio for just this
    # category. None = use the project default. Point of this: a UI/button
    # category that actually gets clipped by a fixed-width control needs a
    # tighter cap than a dialogue category with a scrolling text box.
    max_expansion_ratio: Optional[float] = None
    # Static fallback applied when an adapter had no real per-row max_length
    # for this entry (e.g. Unity CSVs and .po files usually don't carry a
    # length-limit column at all). Set this from something you actually know
    # about the UI -- e.g. "buttons in this game are ~40 chars wide" -- not
    # a guess. None = no fallback, entry.max_length stays whatever the
    # adapter gave it (often None too, in which case only the expansion-
    # ratio check applies).
    default_max_length: Optional[int] = None
    # Keep entries that share this field's value together in the same
    # batch (or contiguous batches, if the group itself exceeds
    # batch_size) instead of chunking purely by count. Checked against
    # a matching Entry attribute first (e.g. "context_screen"), then
    # entry.extra. None = chunk by count only, no grouping.
    narrative_boundary_field: Optional[str] = None
    # How many immediately-preceding entries (within the same boundary
    # group, in original order) to attach as {speaker, source} context
    # narrative_context_window: 0 = disabled. Only meaningful alongside
    # narrative_boundary_field -- without a boundary, "preceding" would
    # mean "whatever happened to be extracted right before this",
    # which can cross scene/file boundaries for no good reason.
    narrative_context_window: int = 0
    # Per-category effort override for the bulk-translate pass (e.g. "low", "high").
    # None = use the project-global provider.effort default.
    effort: Optional[str] = None

    def matches(self, entry) -> bool:
        if self.match_speaker_present is not None:
            has_speaker = entry.speaker is not None and entry.speaker.strip() != ""
            if self.match_speaker_present != has_speaker:
                return False
            # speaker-presence alone is enough to match this rule
            if self.match_key_regex is None and self.match_notes_regex is None and self.match_source_regex is None:
                return True
        if self.match_key_regex and re.search(self.match_key_regex, entry.key or ""):
            return True
        if self.match_notes_regex:
            notes_text = " ".join(entry.notes or [])
            if re.search(self.match_notes_regex, notes_text):
                return True
        # Checked against the actual translatable text -- e.g. routing
        # Unreal's argument-modifier syntax ("{Num}|plural(one=...,other=...)",
        # "{Gender}|gender(...)") to its own category, since that's a signal
        # in the string content itself, not in a key/notes side-channel the
        # way msgctxt or a UABEA "cat:" note is.
        if self.match_source_regex and re.search(self.match_source_regex, entry.source or ""):
            return True
        return False


@dataclass
class ProviderConfig:
    # antigravity_cli is the default: it's the path that needs no separate
    # API key for people already authenticated via `agy auth login`.
    # gemini remains fully supported as an opt-in fallback (set name:
    # explicitly in project.yaml). See providers/antigravity_cli_provider.py
    # for the known headless-stdout caveat that comes with this choice.
    name: str = "antigravity_cli"
    model: str = "gemini-3.8-flash"
    mode: str = "sync"           # "sync" | "batch"
    max_concurrency: int = 5
    max_retries: int = 5
    # Was a hardcoded 8192 in three separate call sites (translate sync,
    # translate batch-mode, review). Real risk, not theoretical: a
    # category's batch_size entries all have to fit their translated
    # JSON output within this cap in ONE response, or the response gets
    # cut off mid-JSON, fails to parse, and retries the ENTIRE batch from
    # scratch -- burning a full extra input+output round trip for zero
    # usable output, up to max_retries times, before giving up. Rough
    # sizing: (batch_size * ~25-35 tokens/entry for translation + JSON
    # envelope overhead) should stay comfortably under this number --
    # if it doesn't, lower batch_size for that category rather than
    # relying on this being large enough to cover a mismatch.
    #
    # 16384 leaves real headroom under gemini-3.8-flash/3.1-pro's actual
    # 65536-token output ceiling (checked directly against their model
    # cards, not assumed) while still catching a truncation early rather
    # than letting one runaway batch eat most of the model's real budget
    # before failing. The default batch_size below (350) is sized against
    # THIS number, not the model's raw ceiling -- raising max_output_tokens
    # alone doesn't help if batch_size isn't raised together with it, and
    # the reverse mismatch (small max_output_tokens, large batch_size) is
    # exactly the bug this default used to ship with.
    max_output_tokens: int = 16384
    review_model: Optional[str] = None  # defaults to `model` if unset
    # --effort low|high, antigravity_cli only (ignored by other providers).
    # Flash/bulk-translate defaults to low for throughput; review_effort
    # defaults to high since Phase 13 repair is low-volume and benefits
    # more from the extra reasoning than it costs -- low is equally valid
    # if you'd rather optimize for speed/cost there instead.
    effort: str = "low"
    review_effort: Optional[str] = None  # defaults to "high" if unset
    escalation_model: Optional[str] = None  # defaults to `review_model` or `model` if unset
    escalation_effort: str = "high"
    escalation_enabled: bool = True
    poll_interval_s: int = 30
    # timeout_s: BATCH MODE ONLY -- how long poll_batch() waits for a
    # submitted Message Batch / Gemini Batch job to reach a terminal state
    # (up to 24-48h is normal for these). NOT the per-call timeout for a
    # single sync-mode translate/review request -- see sync_call_timeout_s
    # for that. Conflating the two used to mean the sync per-call timeout
    # wasn't configurable from project.yaml at all (each provider class
    # just used its own internal default, silently ignoring whatever was
    # set here) -- named and split apart so setting one can't be mistaken
    # for controlling the other.
    timeout_s: int = 24 * 60 * 60
    sync_call_timeout_s: int = 300
    batch_output_token_cap: Optional[int] = None
    context_window_tokens: Optional[int] = None


@dataclass
class PreflightConfig:
    """Optional pre-run font/glyph check -- see preflight/font_check.py.
    Disabled by default (font_check_asset_path=None) so projects without
    a local game-asset dump available are unaffected; the moment
    font_check_asset_path is set in project.yaml, cli.py's cmd_run/
    cmd_plan call it automatically -- no manual step, no GUI button.
    """
    font_check_enabled: bool = False
    font_check_engine: str = "unity"
    font_check_asset_path: Optional[str] = None


PROFILES: dict[str, dict[str, Any]] = {
    "fast": {
        "review_threshold": 0.65,
        "review_chunk_size": 50,
        "fidelity_sample_rate": 0.0,
        "escalation_sample_rate": 0.0,
        "escalation_enabled": False,
        "review_effort": "low",
    },
    "balanced": {
        "review_threshold": 0.70,
        "review_chunk_size": 30,
        "fidelity_sample_rate": 0.01,
        "escalation_sample_rate": 0.005,
        "escalation_enabled": False,
        "review_effort": "low",
    },
    "thorough": {
        "review_threshold": 0.75,
        "review_chunk_size": 30,
        "fidelity_sample_rate": 0.03,
        "escalation_sample_rate": 0.01,
        "escalation_enabled": True,
        "review_effort": "high",
    },
}


@dataclass
class ProjectConfig:
    project: str
    source_lang: str
    target_lang: str
    format: str
    root: Path
    batch_glob: str
    resources: dict[str, Optional[Path]]
    categories: list[CategoryRule]
    provider: ProviderConfig
    tm_db_path: Path
    project_type: str = "game"
    profile: str = "thorough"
    target_register: str = "informal"
    review_threshold: float = 0.75
    max_expansion_ratio: float = 1.6
    tier1_repair_attempts: int = 1
    format_options: dict[str, Any] = field(default_factory=dict)
    escalation_confidence_threshold: float = 0.30
    escalation_sample_rate: float = 0.01
    # fidelity_sample_rate: fraction of successfully-translated entries that get
    # an extra review call to verify faithfulness. Set to 0.0 to disable.
    # Default 0.03 = 3%, matching the previous hardcoded value.
    fidelity_sample_rate: float = 0.03
    max_placeholders_for_low: int = 3
    max_source_len_for_low: int = 250
    # review_chunk_size: how many flagged entries go into a single review LLM
    # call. Larger = fewer calls (less prompt-token overhead per item) but the
    # model may lose focus on items near the end of a very large chunk.
    # 30 is a good default for Gemini Flash; raise to 50 for larger models.
    review_chunk_size: int = 30
    # translate_file_window: in sync mode (provider.mode == "sync"), how many
    # pending batch files get extracted/deduped/batch-built together before
    # ONE concurrent translate pass covers all of their batches at once.
    # Previously this was implicitly 1 -- each file's batches were only ever
    # translated concurrently with each other, never with a sibling file's,
    # so a project made of many small files (common: one file per quest/
    # level/asset) left most of provider.max_concurrency idle. Bounded on
    # purpose rather than covering the whole project like batch mode does:
    # this keeps peak memory proportional to the window, not the project,
    # and means a project doesn't have to wait for every single file's
    # translation to finish before the first one gets validated/reviewed/
    # committed. 8 is a reasonable default for typical batch_size values
    # (~350 entries); raise it for projects with many small files, lower
    # it if memory is tight or you want to see files land sooner.
    translate_file_window: int = 8
    preflight: "PreflightConfig" = field(default_factory=lambda: PreflightConfig())
    gate_naturalness: bool = False
    allow_identical_proper_nouns: bool = True

    @property
    def batch_files(self) -> list[Path]:
        return sorted(self.root.glob(self.batch_glob))

    def default_category(self) -> CategoryRule:
        for c in self.categories:
            if c.is_default:
                return c
        return self.categories[-1]

    def classify(self, entry) -> CategoryRule:
        for rule in self.categories:
            if rule.is_default:
                continue
            if rule.matches(entry):
                return rule
        return self.default_category()


def _resolve(root: Path, value: Optional[str]) -> Optional[Path]:
    if not value:
        return None
    p = Path(value)
    return p if p.is_absolute() else (root / p)


def load_project(project_dir: str | Path) -> ProjectConfig:
    root = Path(project_dir).resolve()
    cfg_path = root / "project.yaml"
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"No project.yaml under {root}. Run `locpipe init {root.name}` first."
        )
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}

    resources_raw = raw.get("resources", {})
    resources = {
        "glossary": _resolve(root, resources_raw.get("glossary")),
        "lang_style": _resolve(root, resources_raw.get("lang_style")),
        "character_voices": _resolve(root, resources_raw.get("character_voices")),
        "anti_fabrication_checklist": _resolve(
            root, resources_raw.get("anti_fabrication_checklist")
        ),
    }

    categories_raw = raw.get("categories") or [
        {"name": "default", "default": True, "batch_size": 100}
    ]
    categories = [
        CategoryRule(
            name=c["name"],
            match_key_regex=c.get("match_key_regex"),
            match_source_regex=c.get("match_source_regex"),
            match_notes_regex=c.get("match_notes_regex"),
            match_speaker_present=c.get("match_speaker_present"),
            needs_character_voice=c.get("needs_character_voice", False),
            batch_size=c.get("batch_size", 100),
            is_default=c.get("default", False),
            max_expansion_ratio=c.get("max_expansion_ratio"),
            default_max_length=c.get("default_max_length"),
            narrative_boundary_field=c.get("narrative_boundary_field"),
            narrative_context_window=c.get("narrative_context_window", 0),
            effort=c.get("effort"),
        )
        for c in categories_raw
    ]
    if not any(c.is_default for c in categories):
        categories[-1].is_default = True

    raw_profile = str(raw.get("profile") or raw.get("speed_profile") or "thorough").lower().strip()
    profile_name = raw_profile if raw_profile in PROFILES else "thorough"
    prof = PROFILES[profile_name]

    provider_raw = raw.get("provider") or {}
    escalation_raw = raw.get("escalation") or raw.get("qa") or {}
    provider = ProviderConfig(
        name=provider_raw.get("name", "antigravity_cli"),
        model=provider_raw.get("model", "gemini-3.8-flash"),
        mode=provider_raw.get("mode", "sync"),
        max_concurrency=provider_raw.get("max_concurrency", 5),
        max_retries=provider_raw.get("max_retries", 5),
        max_output_tokens=provider_raw.get("max_output_tokens", 16384),
        review_model=provider_raw.get("review_model"),
        effort=provider_raw.get("effort", "low"),
        review_effort=provider_raw.get("review_effort", prof["review_effort"]),
        escalation_model=provider_raw.get("escalation_model", escalation_raw.get("model")),
        escalation_effort=provider_raw.get("escalation_effort", escalation_raw.get("effort", "high")),
        escalation_enabled=provider_raw.get("escalation_enabled", escalation_raw.get("enabled", prof["escalation_enabled"])),
        poll_interval_s=provider_raw.get("poll_interval_s", 30),
        timeout_s=provider_raw.get("timeout_s", 24 * 60 * 60),
        sync_call_timeout_s=provider_raw.get("sync_call_timeout_s", 300),
        batch_output_token_cap=provider_raw.get("batch_output_token_cap"),
        context_window_tokens=provider_raw.get("context_window_tokens"),
    )
    if provider.name != "antigravity_cli":
        raise ValueError(
            f"Unsupported provider '{provider.name}' in project.yaml. "
            "Only 'antigravity_cli' is supported."
        )

    tm_raw = raw.get("tm") or {}
    tm_db_path = _resolve(root, tm_raw.get("db_path", "tm/translation_memory.sqlite3"))
    confidence_raw = raw.get("confidence") or {}

    target_register = raw.get("target_register", "informal")
    if target_register not in ("informal", "formal"):
        raise ValueError(
            f"Invalid target_register '{target_register}' in project.yaml. "
            "Only 'informal' or 'formal' are supported."
        )

    preflight_font_raw = (raw.get("preflight") or {}).get("font_check") or {}
    preflight = PreflightConfig(
        font_check_enabled=preflight_font_raw.get("enabled", False),
        font_check_engine=preflight_font_raw.get("engine", "unity"),
        font_check_asset_path=preflight_font_raw.get("asset_path"),
    )

    project_type = str(raw.get("project_type", "game")).lower().strip()
    if project_type not in ("game", "software"):
        project_type = "game"

    return ProjectConfig(
        project=raw["project"],
        source_lang=raw["source_lang"],
        target_lang=raw["target_lang"],
        project_type=project_type,
        profile=profile_name,
        target_register=target_register,
        format=raw["format"],
        root=root,
        batch_glob=(raw.get("batches") or {}).get("glob", "batches/*.json"),
        resources=resources,
        categories=categories,
        provider=provider,
        tm_db_path=tm_db_path,
        review_threshold=float(confidence_raw.get("review_threshold", prof["review_threshold"])),
        max_expansion_ratio=float(confidence_raw.get("max_expansion_ratio", 1.6)),
        tier1_repair_attempts=int(confidence_raw.get("tier1_repair_attempts", 1)),
        format_options=raw.get("format_options") or {},
        escalation_confidence_threshold=float(confidence_raw.get("escalation_confidence_threshold", 0.30)),
        escalation_sample_rate=float(confidence_raw.get("escalation_sample_rate", escalation_raw.get("sample_rate", prof["escalation_sample_rate"]))),
        fidelity_sample_rate=float(confidence_raw.get("fidelity_sample_rate", prof["fidelity_sample_rate"])),
        max_placeholders_for_low=int(confidence_raw.get("max_placeholders_for_low", 3)),
        max_source_len_for_low=int(confidence_raw.get("max_source_len_for_low", 250)),
        review_chunk_size=int(confidence_raw.get("review_chunk_size", prof["review_chunk_size"])),
        translate_file_window=raw.get("translate_file_window", 8),
        preflight=preflight,
        gate_naturalness=raw.get("gate_naturalness", False),
        allow_identical_proper_nouns=bool(confidence_raw.get("allow_identical_proper_nouns", True)),
    )
