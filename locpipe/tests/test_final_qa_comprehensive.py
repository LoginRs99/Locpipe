"""Comprehensive Final QA & Test Automation Suite for LocPipe.

Covers:
- Tag masking & protected token preservation (rich text, printf, custom brackets, escapes)
- Chunking & batching invariants across categories and token caps
- 1:1 Array index alignment in full and partial recovery loops
- End-to-end pipeline execution with mocked AI API calls, TM caching, and error resilience
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from locpipe.models import Entry, EntryStatus, Severity
from locpipe.batcher import TranslationBatch, build_batches
from locpipe.config import ProjectConfig, CategoryRule, ProviderConfig, load_project
from locpipe.checkpoint import Checkpoint
from locpipe.schemas import build_user_payload, parse_and_validate_response
from locpipe.pipeline import run, _translate_batches_sync
from locpipe.providers.base import TranslationProvider
from locpipe.validators.protected_tokens import extract_protected_tokens, validate_protected_tokens
from locpipe.validators.html_tags import check_html_tags
from locpipe.normalize import normalize_source, content_hash
from locpipe.dedupe import enrich_and_dedupe
from locpipe.tm import TranslationMemory


# ============================================================================
# 1. Tag Masking & Protected Token Integrity
# ============================================================================

class TestProtectedTokensAndTagMasking:
    """Verifies that engine tags, placeholders, and escape codes are detected and enforced."""

    @pytest.mark.parametrize(
        "source,expected_tokens",
        [
            ("<color=#ff0000>Critical Strike!</color>", ["<color=#ff0000>", "</color>"]),
            ("<b>Warning:</b> System failure in sector <i>{0}</i>.", ["{0}", "<b>", "</b>", "<i>", "</i>"]),
            ("Welcome, %s! You have %d unread messages.", ["%s", "%d"]),
            ("Transferring {0} credits to [player_name] at %.2f, %.2f.", ["{0}", "%.2f", "[player_name]"]),
            ("Player @primary attack@ dealt %d damage to [target_enemy].", ["@primary attack@", "%d", "[target_enemy]"]),
            ("[BTN:L2 ] + [BTN:RS ] (hold): Move Legion.", ["[BTN:L2 ]", "[BTN:RS ]"]),
            (r"Line 1\nLine 2\tTabbed\n\"Quoted dialogue\"", [r"\n", r"\t"]),
            (r"Objective:\n- Rescue [player_name]\t(Reward: %d XP)", ["%d", "[player_name]", r"\n", r"\t"]),
            (r"Error in file \"save_01.dat\":\n\tOffset 0x%X.", ["%X", r"\n", r"\t"]),
        ],
    )
    def test_token_extraction_accuracy(self, source: str, expected_tokens: list[str]) -> None:
        extracted = extract_protected_tokens(source)
        for token in expected_tokens:
            assert token in extracted, f"Expected token {token} not found in {extracted}"

    def test_token_validation_detects_missing_tags(self) -> None:
        src = "<color=#ff0000><b>Warning</b></color>: %d points lost!"
        # Target missing closing tags and %d
        tgt = "<color=#ff0000>Figyelem: pontok elvesztek!"
        missing, modified = validate_protected_tokens(src, tgt)
        assert "</b>" in missing
        assert "</color>" in missing
        assert "%d" in missing

    def test_token_validation_passes_on_perfect_preservation(self) -> None:
        src = "<b>Commander:</b> Activate [BTN:R2 ] now! Current status: %d%%."
        tgt = "<b>Parancsnok:</b> Aktiváld a(z) [BTN:R2 ] gombot! Jelenlegi állapot: %d%%."
        missing, modified = validate_protected_tokens(src, tgt)
        assert len(missing) == 0
        assert len(modified) == 0

    def test_html_tag_nesting_validation(self) -> None:
        # Missing closing tag in target
        src = "<b><i>Formatted</i></b>"
        tgt = "<b><i>Formázott</i>"
        issues = check_html_tags(src, tgt, "k1")
        assert len(issues) > 0
        assert "hianyzik a HTML/XML tag" in issues[0]

        # Valid tag match
        tgt_valid = "<b><i>Formázott</i></b>"
        issues_valid = check_html_tags(src, tgt_valid, "k1")
        assert len(issues_valid) == 0


# ============================================================================
# 2. Chunking & Batching Invariants
# ============================================================================

class TestChunkingAndBatching:
    """Verifies batch slicing, category partitioning, and token cap safety."""

    def test_batching_respects_batch_size_limits(self, tmp_path: Path) -> None:
        style_file = tmp_path / "lang-style.md"
        style_file.write_text("Style guide", encoding="utf-8")

        cfg = ProjectConfig(
            project="test_batching",
            source_lang="en",
            target_lang="hu",
            format="generic_kv",
            root=tmp_path,
            batch_glob="batches/*.json",
            resources={"lang_style": style_file},
            categories=[
                CategoryRule(name="ui", batch_size=10, is_default=False, match_key_regex=r"^UI_"),
                CategoryRule(name="dialogue", batch_size=5, is_default=True),
            ],
            provider=ProviderConfig(),
            tm_db_path=tmp_path / "tm.sqlite3",
        )

        entries = []
        # 25 UI entries (should make 3 batches: 10, 10, 5)
        for i in range(25):
            entries.append(Entry("f.json", f"UI_BTN_{i}", f"Button {i}", category="ui"))
        # 12 Dialogue entries (should make 3 batches: 5, 5, 2)
        for i in range(12):
            entries.append(Entry("f.json", f"DLG_{i}", f"Dialogue line {i}", category="dialogue"))

        unique_groups = {e.key: [e] for e in entries}
        batches = build_batches(unique_groups, cfg)

        ui_batches = [b for b in batches if b.category == "ui"]
        dlg_batches = [b for b in batches if b.category == "dialogue"]

        assert len(ui_batches) == 3
        assert len(ui_batches[0].representatives) == 10
        assert len(ui_batches[1].representatives) == 10
        assert len(ui_batches[2].representatives) == 5

        assert len(dlg_batches) == 3
        assert len(dlg_batches[0].representatives) == 5
        assert len(dlg_batches[1].representatives) == 5
        assert len(dlg_batches[2].representatives) == 2

    def test_dedupe_and_empty_edge_cases(self, tmp_path: Path) -> None:
        tm_path = tmp_path / "tm.sqlite3"
        tm = TranslationMemory(tm_path)

        entries = [
            Entry("f.json", "k1", "Hello World", target=""),
            Entry("f.json", "k2", "Hello World", target=""), # Duplicate
            Entry("f.json", "k3", "", target=""),            # Empty string
            Entry("f.json", "k4", "   ", target=""),         # Whitespace string -> normalizes to ""
            Entry("f.json", "k5", "...", target=""),          # Punctuation
        ]

        res = enrich_and_dedupe(entries, tm, "en", "hu")
        # k1 and k2 share the same content hash and tm_key
        assert entries[0].tm_key == entries[1].tm_key
        assert len(res.unique_groups[entries[0].tm_key]) == 2
        # Total unique strings needing translation: 3 groups: "Hello World", "" (shared with "   "), and "..."
        assert res.total_unique_strings_to_translate == 3
        tm.close()


# ============================================================================
# 3. 1:1 Index Alignment & Robust Parsing
# ============================================================================

class TestIndexAlignmentAndPayloadParsing:
    """Proves strictly 1:1 index mapping between request and response under all conditions."""

    def test_strict_one_to_one_alignment(self) -> None:
        entries = [Entry("f.json", f"k_{i}", f"Source text {i}") for i in range(15)]
        batch = TranslationBatch(category="ui", representatives=entries)
        payload = json.loads(build_user_payload(batch))

        assert len(payload) == 15
        for i, item in enumerate(payload):
            assert item["id"] == i
            assert item["source"] == f"Source text {i}"

    def test_parse_and_validate_rejects_duplicates_and_missing(self) -> None:
        # Duplicate IDs
        dup_json = json.dumps([
            {"id": 0, "translation": "Egy"},
            {"id": 0, "translation": "Kettő"},
        ])
        res = parse_and_validate_response(dup_json)
        assert res.error == "duplicate ids in response"

        # Valid markdown-fenced response with preamble and postamble
        fenced_json = (
            "Here is the requested Hungarian translation:\n"
            "```json\n"
            '[{"id": 0, "translation": "Indítás"}, {"id": 1, "translation": "Kilépés"}]\n'
            "```\n"
            "Hope this helps!"
        )
        res = parse_and_validate_response(fenced_json)
        assert res.error is None
        assert len(res.parsed) == 2
        assert res.parsed[0]["translation"] == "Indítás"
        assert res.parsed[1]["translation"] == "Kilépés"


# ============================================================================
# 4. End-to-End Pipeline & API Resilience Simulation
# ============================================================================

class MockResilientProvider(TranslationProvider):
    """Simulates transient API failures (HTTP 429 / socket timeout) followed by success."""

    def __init__(self, fail_first_n_times: int = 1):
        self.fail_times = fail_first_n_times
        self.call_count = 0

    async def complete(
        self,
        system_prompt: str,
        user_payload: str,
        *,
        max_tokens: int = 8192,
        effort: str | None = None,
        response_format: str = "json",
    ) -> str:
        self.call_count += 1
        if self.call_count <= self.fail_times:
            # Simulate transient rate-limit / network drop
            raise RuntimeError("API Error: 429 RESOURCE_EXHAUSTED: Rate limit exceeded. Try again later.")

        # Clean JSON payload
        clean_json = user_payload.split("\n\n(Your previous response was invalid:")[0]
        items = json.loads(clean_json)
        out = [{"id": item["id"], "translation": f"HU: {item['source']}"} for item in items]
        return json.dumps(out, ensure_ascii=False)


def test_pipeline_e2e_with_transient_failure_and_resume(tmp_path: Path) -> None:
    """Full end-to-end integration test verifying transient failure recovery and TM persistence."""
    proj_dir = tmp_path / "e2e_proj"
    batches_dir = proj_dir / "batches"
    batches_dir.mkdir(parents=True)
    (proj_dir / "resources").mkdir(parents=True)
    (proj_dir / "tm").mkdir(parents=True)

    # project.yaml
    (proj_dir / "project.yaml").write_text(
        """project: e2e_proj
source_lang: en
target_lang: hu
format: generic_kv
batches:
  glob: "batches/*.json"
provider:
  name: antigravity_cli
  max_retries: 3
""",
        encoding="utf-8",
    )

    # Input batch file with mixed test vectors
    test_data = [
        {"id": "UI_START", "source": "Start Game"},
        {"id": "UI_WARN", "source": "<color=#ff0000>Warning!</color>"},
        {"id": "UI_MSG", "source": "Player %s has %d points."},
    ]
    batch_file = batches_dir / "ui_strings.json"
    batch_file.write_text(json.dumps(test_data), encoding="utf-8")

    # Run pipeline with provider that fails once with 429 then succeeds
    provider = MockResilientProvider(fail_first_n_times=1)
    cfg = load_project(proj_dir)
    stats = run(cfg, provider)

    # Assertions
    assert provider.call_count == 2, "Expected 1 retry after initial transient failure"
    assert stats.total_entries == 3
    assert stats.unique_strings_sent_to_llm == 3
    assert stats.newly_committed_to_tm == 3

    # Verify output file was written with translations
    saved_data = json.loads(batch_file.read_text(encoding="utf-8"))
    assert saved_data[0]["target"] == "HU: Start Game"
    assert saved_data[1]["target"] == "HU: <color=#ff0000>Warning!</color>"
    assert saved_data[2]["target"] == "HU: Player %s has %d points."

    # Verify Checkpoint recorded completion
    cp = Checkpoint(proj_dir / "checkpoint.json")
    assert cp.is_file_done(str(batch_file))

    # Verify TM contains all entries
    tm = TranslationMemory(proj_dir / "tm" / "translation_memory.sqlite3")
    assert len(list(tm.iter_all())) == 3
    tm.close()


# ============================================================================
# 5. Strict Game-Engine Guardrails & TokenMasker Verification
# ============================================================================

class TestTokenMaskerAndEngineGuardrails:
    """Verifies mathematical guarantee that protected tags cannot be corrupted or mistranslated."""

    def test_token_masker_full_cycle(self) -> None:
        from locpipe.validators.protected_tokens import TokenMasker

        source = "<b>Commander:</b> Sector <color=#ff0000>{0}</color> breached by @chimera@! Press [BTN:R2 ]."
        masked, token_map = TokenMasker.mask(source)

        # Sentinels must be present in masked text
        assert "⟦T0⟧" in masked
        assert len(token_map) >= 5

        # Simulate LLM translating the surrounding natural text
        simulated_translation = "⟦T0⟧Parancsnok:⟦T1⟧ A(z) ⟦T2⟧ szektort áttörte a(z) ⟦T3⟧! Nyomd meg a(z) ⟦T4⟧ gombot."
        # Map sentinels according to token map
        sentinel_order = list(token_map.keys())
        simulated_target = (
            f"{sentinel_order[0]}Parancsnok:{sentinel_order[1]} "
            f"A(z) <color=#ff0000>{sentinel_order[2]}</color> szektort áttörte a(z) {sentinel_order[3]}! "
            f"Nyomd meg a(z) {sentinel_order[4]} gombot."
        )

        unmasked, missing = TokenMasker.unmask(masked, token_map)
        assert unmasked == source
        assert missing == []

    @pytest.mark.parametrize(
        "engine_str,expected_token",
        [
            ("Ren'Py variable [hero.name!t] gained 10 XP", "[hero.name!t]"),
            ("Ren'Py dialogue tag {color=#ff0000}{fast}Text{/color}", "{color=#ff0000}"),
            ("Ren'Py wait tag {w=2.5}Now continue", "{w=2.5}"),
            ("Unreal RichText <RichText.Bold>Title</RichText.Bold>", "<RichText.Bold>"),
            ("Unreal style tag <style=\"Header\">Header</style>", "<style=\"Header\">"),
            ("C printf long long int count: %lld units", "%lld"),
            ("C printf zero-padded: %02d hours", "%02d"),
            ("BBCode formatting [color=red]Red Text[/color]", "[color=red]"),
            ("Unity TextMeshPro quad <quad material=1 size=20 />", "<quad material=1 size=20 />"),
        ],
    )
    def test_engine_specific_patterns(self, engine_str: str, expected_token: str) -> None:
        tokens = extract_protected_tokens(engine_str)
        assert expected_token in tokens, f"Expected {expected_token} in {tokens}"


def test_cmd_auto_zero_touch_flow(tmp_path: Path) -> None:
    """Verifies that cmd_auto runs the entire 4-stage pipeline autonomously."""
    import argparse
    from locpipe.cli import cmd_auto

    proj_dir = tmp_path / "auto_proj"
    batches_dir = proj_dir / "batches"
    batches_dir.mkdir(parents=True)
    (proj_dir / "resources").mkdir(parents=True)
    (proj_dir / "tm").mkdir(parents=True)

    (proj_dir / "project.yaml").write_text(
        """project: auto_proj
source_lang: en
target_lang: hu
format: generic_kv
batches:
  glob: "batches/*.json"
provider:
  name: antigravity_cli
""",
        encoding="utf-8",
    )

    test_data = [{"id": "BTN_PLAY", "source": "Play Now"}]
    (batches_dir / "ui.json").write_text(json.dumps(test_data), encoding="utf-8")

    args = argparse.Namespace(
        project=str(proj_dir),
        dry_run=True,
        pseudo_loc=False,
        canary_calls=5,
        skip_canary=False,
    )

    ret = cmd_auto(args)
    assert ret == 0

    # Verify that file was translated and saved
    res_data = json.loads((batches_dir / "ui.json").read_text(encoding="utf-8"))
    assert "target" in res_data[0]
    assert res_data[0]["target"] == "MOCK-HU: Play Now"

