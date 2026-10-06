"""Unit test suite for Step 3 optimizations:
- P2: Provider capabilities, batch caps, and AntigravityCLIProvider invocation regression test
- P12: Lightweight plan(include_totals=False)
- P11: Fixture suite for batch translation retries and invariants
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from locpipe.models import Entry, EntryStatus
from locpipe.batcher import TranslationBatch, build_batches
from locpipe.config import ProjectConfig, CategoryRule, ProviderConfig
from locpipe.providers.base import TranslationProvider
from locpipe.providers.antigravity_cli_provider import AntigravityCLIProvider
from locpipe.checkpoint import Checkpoint
from locpipe.pipeline import plan, _translate_batches_sync


# ---------------------------------------------------------------------------
# P2: Provider-owned capabilities & AntigravityCLIProvider invocation regression
# ---------------------------------------------------------------------------

def test_p2_antigravity_cli_invocation_uses_temp_file(tmp_path):
    """Regression test locking required transport behavior:
    agy --print <path.txt>, no full prompt in argv, temp file contains prompt.
    """
    with patch("locpipe.providers.antigravity_cli_provider._BINARY", "/fake/agy"):
        provider = AntigravityCLIProvider(model="gemini-3.8-flash")
    test_prompt = "Translate these 100 game dialogue lines with specific characters: " + ("abc " * 50)

    with patch("subprocess.run") as mock_run:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = b'[{"id": 0, "translation": "teszt"}]'
        mock_proc.stderr = b""
        mock_run.return_value = mock_proc

        result = provider._run_agy(test_prompt)
        assert result == '[{"id": 0, "translation": "teszt"}]'

        assert mock_run.called
        args = mock_run.call_args[0][0]
        # args[0] is binary, args[1] is --print, args[2] is temp file path
        assert args[1] == "--print"
        temp_file_arg = args[2]
        assert temp_file_arg.endswith(".txt")
        # Assert the full prompt appears in NO element of args
        for arg in args:
            assert test_prompt not in arg

        # Note: _run_agy cleans up the temp file in its finally block,
        # but while running it wrote the full prompt into that file.


def test_p2_provider_capabilities_attributes():
    base = TranslationProvider
    assert base.max_input_chars == 24000
    assert base.context_window_tokens is None

    with patch("locpipe.providers.antigravity_cli_provider._BINARY", "/fake/agy"):
        agy = AntigravityCLIProvider(model="gemini-3.8-flash")
    assert agy.max_input_chars is None
    assert agy.context_window_tokens is None


def test_p2_batcher_caps_respects_provider(tmp_path):
    style_file = tmp_path / "lang-style.md"
    style_file.write_text("Style text", encoding="utf-8")

    cfg = ProjectConfig(
        project="test_p2",
        source_lang="en",
        target_lang="hu",
        format="generic_kv",
        root=tmp_path,
        batch_glob="batches/*.json",
        resources={"lang_style": style_file},
        categories=[CategoryRule(name="ui", batch_size=50, is_default=True)],
        provider=ProviderConfig(max_output_tokens=16384),
        tm_db_path=tmp_path / "tm.sqlite3",
    )

    # 1. State A: batch_output_token_cap is None -> legacy min(4500, max_output_tokens - 1000) = 4500
    entries = [Entry("f.json", f"k{i}", "A" * 150, category="ui") for i in range(100)]
    unique_groups = {e.key: [e] for e in entries}

    batches_default = build_batches(unique_groups, cfg, provider=None)

    # 2. State B: operator sets custom batch_output_token_cap = 14000
    cfg_state_b = ProjectConfig(
        project="test_p2_b",
        source_lang="en",
        target_lang="hu",
        format="generic_kv",
        root=tmp_path,
        batch_glob="batches/*.json",
        resources={"lang_style": style_file},
        categories=[CategoryRule(name="ui", batch_size=100, is_default=True)],
        provider=ProviderConfig(max_output_tokens=16384, batch_output_token_cap=14000),
        tm_db_path=tmp_path / "tm.sqlite3",
    )
    with patch("locpipe.providers.antigravity_cli_provider._BINARY", "/fake/agy"):
        agy_prov = AntigravityCLIProvider(model="gemini-3.8-flash")
    batches_b = build_batches(unique_groups, cfg_state_b, provider=agy_prov)

    # With higher cap and higher batch_size, batches_b packs into fewer batches
    assert len(batches_b) <= len(batches_default)


# ---------------------------------------------------------------------------
# P12: Lightweight plan(include_totals=False)
# ---------------------------------------------------------------------------

def test_p12_lightweight_plan(tmp_path):
    batches_dir = tmp_path / "batches"
    batches_dir.mkdir(parents=True)
    b_file = batches_dir / "b1.json"
    b_file.write_text(json.dumps([
        {"id": "k1", "source": "Hello"},
        {"id": "k2", "source": "World"},
    ]), encoding="utf-8")

    cfg = ProjectConfig(
        project="test_p12",
        source_lang="en",
        target_lang="hu",
        format="generic_kv",
        root=tmp_path,
        batch_glob="batches/*.json",
        resources={},
        categories=[CategoryRule(name="ui", is_default=True)],
        provider=ProviderConfig(),
        tm_db_path=tmp_path / "tm.sqlite3",
    )

    full_plan = plan(cfg, include_totals=True)
    assert full_plan["total_entries"] == 2
    assert full_plan["llm_calls_needed"] >= 1

    light_plan = plan(cfg, include_totals=False)
    # Pass 1 skipped: total_entries is 0
    assert light_plan["total_entries"] == 0
    assert light_plan["already_translated"] == 0
    # LLM calls needed is identical
    assert light_plan["llm_calls_needed"] == full_plan["llm_calls_needed"]


# ---------------------------------------------------------------------------
# P11: Fixture suite for translation retry behaviors
# ---------------------------------------------------------------------------

class SequencedMockProvider(TranslationProvider):
    """Mock provider returning a predefined sequence of raw responses."""
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.call_count = 0
        self.received_payloads: list[str] = []

    async def complete(self, system_prompt, user_payload, *, max_tokens, effort=None, response_format="json") -> str:
        self.received_payloads.append(user_payload)
        if self.call_count < len(self.responses):
            resp = self.responses[self.call_count]
        else:
            resp = self.responses[-1]
        self.call_count += 1
        return resp


def test_p11_fixture_1_all_ids_returned(tmp_path):
    batch = TranslationBatch(
        category="ui",
        representatives=[
            Entry("f.po", "k0", "Start", tm_key="k0"),
            Entry("f.po", "k1", "Options", tm_key="k1"),
        ],
    )
    provider = SequencedMockProvider([
        json.dumps([{"id": 0, "translation": "Indítás"}, {"id": 1, "translation": "Beállítások"}])
    ])
    cfg = ProjectConfig(
        project="t", source_lang="en", target_lang="hu", format="generic_kv",
        root=tmp_path, batch_glob="", resources={},
        categories=[CategoryRule(name="ui", is_default=True)],
        provider=ProviderConfig(max_retries=3), tm_db_path=tmp_path / "tm.sqlite3",
    )
    cp = Checkpoint(tmp_path / "cp.json")

    failed, latencies, wasted, *extra = asyncio.run(_translate_batches_sync([batch], cfg, [], provider, cp))
    assert len(failed) == 0
    assert wasted == 0
    assert batch.representatives[0].target == "Indítás"
    assert batch.representatives[1].target == "Beállítások"


def test_p11_fixture_2_one_id_missing_then_repaired(tmp_path):
    batch = TranslationBatch(
        category="ui",
        representatives=[
            Entry("f.po", "k0", "Start", tm_key="k0"),
            Entry("f.po", "k1", "Options", tm_key="k1"),
        ],
    )
    # Attempt 1 misses id 1.
    # Attempt 2 receives ONLY item 1 remapped as id 0.
    provider = SequencedMockProvider([
        json.dumps([{"id": 0, "translation": "Indítás"}]),
        json.dumps([{"id": 0, "translation": "Beállítások"}]),
    ])
    cfg = ProjectConfig(
        project="t", source_lang="en", target_lang="hu", format="generic_kv",
        root=tmp_path, batch_glob="", resources={},
        categories=[CategoryRule(name="ui", is_default=True)],
        provider=ProviderConfig(max_retries=3), tm_db_path=tmp_path / "tm.sqlite3",
    )
    cp = Checkpoint(tmp_path / "cp.json")

    failed, latencies, wasted, *extra = asyncio.run(_translate_batches_sync([batch], cfg, [], provider, cp))
    assert len(failed) == 0
    assert wasted == 0  # 0 wasted full-payload retries
    assert batch.representatives[0].target == "Indítás"
    assert batch.representatives[1].target == "Beállítások"
    assert "Start" in provider.received_payloads[0]
    assert "Options" in provider.received_payloads[0]
    # Follow-up request contains ONLY item 1 remapped as id 0
    assert "Options" in provider.received_payloads[1]
    assert "Start" not in provider.received_payloads[1]


def test_p11_fixture_3_multiple_ids_missing_exhausts_retries(tmp_path):
    batch = TranslationBatch(
        category="ui",
        representatives=[
            Entry("f.po", f"k{i}", f"Text {i}", tm_key=f"k{i}") for i in range(5)
        ],
    )
    # Every attempt only provides id 0
    provider = SequencedMockProvider([
        json.dumps([{"id": 0, "translation": "Szöveg 0"}])
    ])
    cfg = ProjectConfig(
        project="t", source_lang="en", target_lang="hu", format="generic_kv",
        root=tmp_path, batch_glob="", resources={},
        categories=[CategoryRule(name="ui", is_default=True)],
        provider=ProviderConfig(max_retries=2), tm_db_path=tmp_path / "tm.sqlite3",
    )
    cp = Checkpoint(tmp_path / "cp.json")

    failed, latencies, wasted, *extra = asyncio.run(_translate_batches_sync([batch], cfg, [], provider, cp))
    assert len(failed) == 1
    assert wasted > 0
    # TM and Checkpoint must NOT contain partial drafts
    assert cp.get_batch_drafts() == {}


def test_p11_fixture_4_duplicate_returned_id_then_repaired(tmp_path):
    batch = TranslationBatch(
        category="ui",
        representatives=[
            Entry("f.po", "k0", "Start", tm_key="k0"),
            Entry("f.po", "k1", "Options", tm_key="k1"),
        ],
    )
    # Attempt 1 returns duplicate id 0 -> parse failure ("duplicate ids in response")
    # Attempt 2 retries full payload and returns both items cleanly
    provider = SequencedMockProvider([
        json.dumps([{"id": 0, "translation": "Indítás"}, {"id": 0, "translation": "Indítás 2"}]),
        json.dumps([{"id": 0, "translation": "Indítás"}, {"id": 1, "translation": "Beállítások"}]),
    ])
    cfg = ProjectConfig(
        project="t", source_lang="en", target_lang="hu", format="generic_kv",
        root=tmp_path, batch_glob="", resources={},
        categories=[CategoryRule(name="ui", is_default=True)],
        provider=ProviderConfig(max_retries=3), tm_db_path=tmp_path / "tm.sqlite3",
    )
    cp = Checkpoint(tmp_path / "cp.json")

    failed, latencies, wasted, *extra = asyncio.run(_translate_batches_sync([batch], cfg, [], provider, cp))
    assert len(failed) == 0
    assert wasted == 1  # 1 wasted retry due to duplicate ID parse failure on attempt 1
    assert batch.representatives[0].target == "Indítás"
    assert batch.representatives[1].target == "Beállítások"


def test_p11_fixture_5_unexpected_id_fails_or_retries(tmp_path):
    batch = TranslationBatch(
        category="ui",
        representatives=[
            Entry("f.po", "k0", "Start", tm_key="k0"),
        ],
    )
    # Attempt 1 returns unexpected id 99
    provider = SequencedMockProvider([
        json.dumps([{"id": 99, "translation": "Valami"}]),
        json.dumps([{"id": 0, "translation": "Indítás"}]),
    ])
    cfg = ProjectConfig(
        project="t", source_lang="en", target_lang="hu", format="generic_kv",
        root=tmp_path, batch_glob="", resources={},
        categories=[CategoryRule(name="ui", is_default=True)],
        provider=ProviderConfig(max_retries=3), tm_db_path=tmp_path / "tm.sqlite3",
    )
    cp = Checkpoint(tmp_path / "cp.json")

    failed, latencies, wasted, *extra = asyncio.run(_translate_batches_sync([batch], cfg, [], provider, cp))
    assert len(failed) == 0
    assert batch.representatives[0].target == "Indítás"


def test_p11_fixture_6_malformed_response_then_repaired(tmp_path):
    batch = TranslationBatch(
        category="ui",
        representatives=[
            Entry("f.po", "k0", "Start", tm_key="k0"),
        ],
    )
    provider = SequencedMockProvider([
        "NOT JSON AT ALL",
        json.dumps([{"id": 0, "translation": "Indítás"}]),
    ])
    cfg = ProjectConfig(
        project="t", source_lang="en", target_lang="hu", format="generic_kv",
        root=tmp_path, batch_glob="", resources={},
        categories=[CategoryRule(name="ui", is_default=True)],
        provider=ProviderConfig(max_retries=3), tm_db_path=tmp_path / "tm.sqlite3",
    )
    cp = Checkpoint(tmp_path / "cp.json")

    failed, latencies, wasted, *extra = asyncio.run(_translate_batches_sync([batch], cfg, [], provider, cp))
    assert len(failed) == 0
    assert batch.representatives[0].target == "Indítás"


def test_p11_fixture_7_empty_response_then_repaired(tmp_path):
    batch = TranslationBatch(
        category="ui",
        representatives=[
            Entry("f.po", "k0", "Start", tm_key="k0"),
        ],
    )
    provider = SequencedMockProvider([
        "",
        json.dumps([{"id": 0, "translation": "Indítás"}]),
    ])
    cfg = ProjectConfig(
        project="t", source_lang="en", target_lang="hu", format="generic_kv",
        root=tmp_path, batch_glob="", resources={},
        categories=[CategoryRule(name="ui", is_default=True)],
        provider=ProviderConfig(max_retries=3), tm_db_path=tmp_path / "tm.sqlite3",
    )
    cp = Checkpoint(tmp_path / "cp.json")

    failed, latencies, wasted, *extra = asyncio.run(_translate_batches_sync([batch], cfg, [], provider, cp))
    assert len(failed) == 0
    assert batch.representatives[0].target == "Indítás"


def test_p11_fixture_8_progressive_partial_recovery(tmp_path):
    # 5 items total:
    # Attempt 0 returns items 0, 1
    # Attempt 1 (requesting 2, 3, 4 remapped as 0, 1, 2) returns sub-ids 0, 1 (items 2, 3)
    # Attempt 2 (requesting 4 remapped as 0) returns sub-id 0 (item 4)
    batch = TranslationBatch(
        category="dialogue",
        representatives=[
            Entry("f.po", f"k{i}", f"Line {i}", tm_key=f"k{i}") for i in range(5)
        ],
    )
    provider = SequencedMockProvider([
        json.dumps([{"id": 0, "translation": "Sor 0"}, {"id": 1, "translation": "Sor 1"}]),
        json.dumps([{"id": 0, "translation": "Sor 2"}, {"id": 1, "translation": "Sor 3"}]),
        json.dumps([{"id": 0, "translation": "Sor 4"}]),
    ])
    cfg = ProjectConfig(
        project="t", source_lang="en", target_lang="hu", format="generic_kv",
        root=tmp_path, batch_glob="", resources={},
        categories=[CategoryRule(name="dialogue", is_default=True)],
        provider=ProviderConfig(max_retries=3), tm_db_path=tmp_path / "tm.sqlite3",
    )
    cp = Checkpoint(tmp_path / "cp.json")

    failed, latencies, wasted, *extra = asyncio.run(_translate_batches_sync([batch], cfg, [], provider, cp))
    assert len(failed) == 0
    assert wasted == 0  # 0 wasted retries: every call made progress
    for i in range(5):
        assert batch.representatives[i].target == f"Sor {i}"
    # Verify sub-payload shrinkage across calls
    assert "Line 0" in provider.received_payloads[0] and "Line 4" in provider.received_payloads[0]
    assert "Line 0" not in provider.received_payloads[1] and "Line 2" in provider.received_payloads[1]
    assert "Line 2" not in provider.received_payloads[2] and "Line 4" in provider.received_payloads[2]


def test_p11_fixture_9_partial_then_failed_followup_then_success(tmp_path):
    # Attempt 0 returns item 0 of [0, 1]
    # Attempt 1 for item 1 fails to parse (NOT JSON) -> wasted retry
    # Attempt 2 for item 1 succeeds
    batch = TranslationBatch(
        category="ui",
        representatives=[
            Entry("f.po", "k0", "Start", tm_key="k0"),
            Entry("f.po", "k1", "Options", tm_key="k1"),
        ],
    )
    provider = SequencedMockProvider([
        json.dumps([{"id": 0, "translation": "Indítás"}]),
        "CORRUPTED NETWORK RESPONSE",
        json.dumps([{"id": 0, "translation": "Beállítások"}]),
    ])
    cfg = ProjectConfig(
        project="t", source_lang="en", target_lang="hu", format="generic_kv",
        root=tmp_path, batch_glob="", resources={},
        categories=[CategoryRule(name="ui", is_default=True)],
        provider=ProviderConfig(max_retries=3), tm_db_path=tmp_path / "tm.sqlite3",
    )
    cp = Checkpoint(tmp_path / "cp.json")

    failed, latencies, wasted, *extra = asyncio.run(_translate_batches_sync([batch], cfg, [], provider, cp))
    assert len(failed) == 0
    assert wasted == 1  # Exactly 1 wasted retry from the corrupted attempt
    assert batch.representatives[0].target == "Indítás"
    assert batch.representatives[1].target == "Beállítások"
    assert "(Your previous response was invalid:" in provider.received_payloads[2]


def test_p11_fixture_10_truncated_json_recovery(tmp_path):
    # Truncated JSON on attempt 0 cut off mid-translation-string on item 1:
    # Bracket salvage appends "}] to salvage the array.
    # Under F2, translate_one discards the cut-off final item ("Beáll") and retains item 0 ("Indítás").
    # The follow-up request recovers item 1 cleanly with full text ("Beállítások").
    batch = TranslationBatch(
        category="ui",
        representatives=[
            Entry("f.po", "k0", "Start", tm_key="k0"),
            Entry("f.po", "k1", "Options", tm_key="k1"),
        ],
    )
    truncated_resp = '[{"id": 0, "translation": "Indítás"}, {"id": 1, "translation": "Beáll'
    provider = SequencedMockProvider([
        truncated_resp,
        json.dumps([{"id": 0, "translation": "Beállítások"}]),
    ])
    cfg = ProjectConfig(
        project="t", source_lang="en", target_lang="hu", format="generic_kv",
        root=tmp_path, batch_glob="", resources={},
        categories=[CategoryRule(name="ui", is_default=True)],
        provider=ProviderConfig(max_retries=3), tm_db_path=tmp_path / "tm.sqlite3",
    )
    cp = Checkpoint(tmp_path / "cp.json")

    failed, latencies, wasted, partial = asyncio.run(_translate_batches_sync([batch], cfg, [], provider, cp))
    assert len(failed) == 0
    assert wasted == 0
    assert partial == 1  # 1 partial recovery call made to retrieve item 1 cleanly
    assert batch.representatives[0].target == "Indítás"
    assert batch.representatives[1].target == "Beállítások"  # Proves "Beáll" was discarded
