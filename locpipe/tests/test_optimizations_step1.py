"""Unit test suite for Step 1 optimizations:
- P1 & P6: Review glossary scoping and prefix-anchored glossary prune
- P3 & P4: Template dead-weight reduction (correction mode toggle, placeholder rules)
- P5: Noise notes filtering at payload boundary
- P9: Glossary check loading cache by (path, mtime)
"""

import json
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from locpipe.models import Entry, GlossaryTerm, ValidationResult
from locpipe.review_queue import ReviewItem
from locpipe.reviewer import build_review_payload
from locpipe.glossary import prune_for_batch, format_for_prompt
from locpipe.prompt_builder import get_placeholder_rules
from locpipe.schemas import (
    build_system_prompt_for_category,
    build_user_payload,
    build_retry_payload,
    _filter_notes_for_payload,
)
from locpipe.batcher import TranslationBatch
from locpipe.config import ProjectConfig, CategoryRule, ProviderConfig
from locpipe.classify import classify_entries
from locpipe.validators.glossary_terms import load_glossary_for_check, _GLOSSARY_CHECK_CACHE


# ---------------------------------------------------------------------------
# P1: Review glossary scoping
# ---------------------------------------------------------------------------

def test_p1_disjoint_terms_exact_union_and_deterministic_order():
    t1 = GlossaryTerm("Sword", "Kard", "mechanic", "high")
    t2 = GlossaryTerm("Shield", "Pajzs", "mechanic", "high")
    t3 = GlossaryTerm("Potion", "Főzet", "mechanic", "high")

    e1 = Entry("file.po", "k1", "Pick up Sword and Shield", "")
    item1 = ReviewItem(
        entry=e1,
        validation=ValidationResult(entry_key="k1"),
        confidence=0.5,
        relevant_glossary_terms=[t1, t2],
    )

    e2 = Entry("file.po", "k2", "Drink Potion and Shield yourself", "")
    item2 = ReviewItem(
        entry=e2,
        validation=ValidationResult(entry_key="k2"),
        confidence=0.5,
        relevant_glossary_terms=[t2, t3],  # t2 is duplicate across items
    )

    fallback_glossary = [t1, t2, t3, GlossaryTerm("Bow", "Íj", "mechanic", "high")]

    payload_json1 = build_review_payload([item1, item2], fallback_glossary)
    payload_json2 = build_review_payload([item1, item2], fallback_glossary)

    # Determinism across repeated calls
    assert payload_json1 == payload_json2

    data = json.loads(payload_json1)
    glossary_str = data["glossary"]
    # t1, t2, t3 should be present in first-seen order
    assert "- Sword -> Kard" in glossary_str
    assert "- Shield -> Pajzs" in glossary_str
    assert "- Potion -> Főzet" in glossary_str
    assert "- Bow -> Íj" not in glossary_str
    # Exact union of 3 unique terms, deduplicated
    lines = [line for line in glossary_str.splitlines() if line.startswith("- ")]
    assert len(lines) == 3
    assert "Sword" in lines[0]
    assert "Shield" in lines[1]
    assert "Potion" in lines[2]


def test_p1_all_items_termless_emits_fallback_string():
    e1 = Entry("file.po", "k1", "Hello world", "")
    item1 = ReviewItem(
        entry=e1,
        validation=ValidationResult(entry_key="k1"),
        confidence=0.5,
        relevant_glossary_terms=[],
    )
    fallback_glossary = [GlossaryTerm("Sword", "Kard", "mechanic", "high")]
    payload_json = build_review_payload([item1], fallback_glossary)
    data = json.loads(payload_json)
    assert data["glossary"] == "(no glossary terms apply to this batch)"


def test_p1_fallback_to_passed_glossary_if_items_do_not_carry_terms():
    e1 = Entry("file.po", "k1", "Hello world", "")
    item1 = ReviewItem(
        entry=e1,
        validation=ValidationResult(entry_key="k1"),
        confidence=0.5,
        relevant_glossary_terms=None,  # Legacy item without carried terms
    )
    fallback_glossary = [GlossaryTerm("Sword", "Kard", "mechanic", "high")]
    payload_json = build_review_payload([item1], fallback_glossary)
    data = json.loads(payload_json)
    assert "- Sword -> Kard" in data["glossary"]


# ---------------------------------------------------------------------------
# P6: Glossary prune matrix
# ---------------------------------------------------------------------------

def test_p6_prune_matrix():
    g = [
        GlossaryTerm("Network", "Hálózat", "ui", "high"),
        GlossaryTerm("Health Potion", "Életerő Főzet", "mechanic", "high"),
        GlossaryTerm("Health", "Életerő", "mechanic", "high"),
        GlossaryTerm("Loot", "Zsákmány", "mechanic", "high"),
        GlossaryTerm("Action Point", "Akciópont", "mechanic", "high"),
        GlossaryTerm("Point of Interest", "Érdekes hely", "lore", "high"),
        GlossaryTerm("DisputedTerm", "Form1 / Form2", "mechanic", "high", justification="⚠ context dependent", is_disputed=True),
    ]

    # 1. Exact single-word term
    kept = prune_for_batch(g, ["Connect to the Network please"])
    assert [t.source_term for t in kept] == ["Network"]

    # 2. Multi-word term: full phrase present -> kept; only one word present -> dropped
    kept = prune_for_batch(g, ["Drink a Health Potion now"])
    assert "Health Potion" in [t.source_term for t in kept]
    assert "Health" in [t.source_term for t in kept]

    kept = prune_for_batch(g, ["Your Health is declining"])
    assert "Health" in [t.source_term for t in kept]
    assert "Health Potion" not in [t.source_term for t in kept]

    # 3. Inflected form (Hungarian suffix: Network-től, Connect-tel)
    kept = prune_for_batch(g, ["Csatlakozva a Network-től érkező jelekhez"])
    assert "Network" in [t.source_term for t in kept]

    # 4. Punctuation adjacency: (Loot) -> Loot kept
    kept = prune_for_batch(g, ["Open chest (Loot)"])
    assert "Loot" in [t.source_term for t in kept]

    # 5. Case differences: network -> Network kept
    kept = prune_for_batch(g, ["Check your network connection"])
    assert "Network" in [t.source_term for t in kept]

    # 6. Terms sharing a common word but semantically unrelated:
    # "Action Point" vs "Point of Interest"
    kept = prune_for_batch(g, ["You spent an Action Point"])
    terms = [t.source_term for t in kept]
    assert "Action Point" in terms
    assert "Point of Interest" not in terms

    # 7. Disputed (⚠) term surfaced when present
    kept = prune_for_batch(g, ["Notice the DisputedTerm here"])
    assert "DisputedTerm" in [t.source_term for t in kept]

    # 8. Empty input
    assert prune_for_batch([], ["some text"]) == []
    assert len(prune_for_batch(g, [])) == len(g)


# ---------------------------------------------------------------------------
# P3 & P4: Template dead weight reduction
# ---------------------------------------------------------------------------

def test_p3_p4_template_toggles_and_placeholder_rules(tmp_path):
    style_file = tmp_path / "lang-style.md"
    style_file.write_text("Style text", encoding="utf-8")
    anti_fab = tmp_path / "anti-fab.md"
    anti_fab.write_text("Anti fab text", encoding="utf-8")

    cfg = ProjectConfig(
        project="test_p3_p4",
        source_lang="en",
        target_lang="hu",
        format="generic_kv",
        root=tmp_path,
        batch_glob="batches/*.json",
        resources={"lang_style": style_file, "anti_fabrication_checklist": anti_fab},
        categories=[CategoryRule(name="ui", is_default=True)],
        provider=ProviderConfig(),
        tm_db_path=tmp_path / "tm.sqlite3",
    )

    # Test default: CORRECTION MODE absent
    prompt_default = build_system_prompt_for_category(cfg, "ui", [], correction_mode=False)
    assert "--- CORRECTION MODE ---" not in prompt_default
    assert "%%CORRECTION_MODE_SECTION" not in prompt_default

    # Test retry mode: CORRECTION MODE present
    prompt_retry = build_system_prompt_for_category(cfg, "ui", [], correction_mode=True)
    assert "--- CORRECTION MODE ---" in prompt_retry
    assert "%%CORRECTION_MODE_SECTION" not in prompt_retry

    # Test placeholder rules: exactly one language's rules present
    # en -> hu
    assert "Hungarian marks grammatical case" in prompt_default
    assert "Japanese attaches particles" not in prompt_default
    assert "Preserve standard English word order" not in prompt_default
    assert "%%TARGET_PLACEHOLDER_RULES%%" not in prompt_default

    # en -> ja
    cfg_ja = ProjectConfig(
        project="test_ja",
        source_lang="en",
        target_lang="ja",
        format="generic_kv",
        root=tmp_path,
        batch_glob="batches/*.json",
        resources={"lang_style": style_file, "anti_fabrication_checklist": anti_fab},
        categories=[CategoryRule(name="ui", is_default=True)],
        provider=ProviderConfig(),
        tm_db_path=tmp_path / "tm.sqlite3",
    )
    prompt_ja = build_system_prompt_for_category(cfg_ja, "ui", [])
    assert "Japanese attaches particles" in prompt_ja
    assert "Hungarian marks grammatical case" not in prompt_ja
    assert "Preserve standard English word order" not in prompt_ja

    # ja -> en (should have both English target rule and Japanese source rule)
    cfg_ja_en = ProjectConfig(
        project="test_ja_en",
        source_lang="ja",
        target_lang="en",
        format="generic_kv",
        root=tmp_path,
        batch_glob="batches/*.json",
        resources={"lang_style": style_file, "anti_fabrication_checklist": anti_fab},
        categories=[CategoryRule(name="ui", is_default=True)],
        provider=ProviderConfig(),
        tm_db_path=tmp_path / "tm.sqlite3",
    )
    prompt_ja_en = build_system_prompt_for_category(cfg_ja_en, "ui", [])
    assert "Japanese corner brackets" in prompt_ja_en
    assert "Preserve standard English word order" in prompt_ja_en
    assert "Hungarian marks grammatical case" not in prompt_ja_en

    # ja -> hu (should have both Hungarian target rule and Japanese source rule)
    cfg_ja_hu = ProjectConfig(
        project="test_ja_hu",
        source_lang="ja",
        target_lang="hu",
        format="generic_kv",
        root=tmp_path,
        batch_glob="batches/*.json",
        resources={"lang_style": style_file, "anti_fabrication_checklist": anti_fab},
        categories=[CategoryRule(name="ui", is_default=True)],
        provider=ProviderConfig(),
        tm_db_path=tmp_path / "tm.sqlite3",
    )
    prompt_ja_hu = build_system_prompt_for_category(cfg_ja_hu, "ui", [])
    assert "Hungarian marks grammatical case" in prompt_ja_hu
    assert "Japanese corner brackets" in prompt_ja_hu
    assert "Preserve standard English word order" not in prompt_ja_hu


# ---------------------------------------------------------------------------
# P5: Notes filtering at payload boundary
# ---------------------------------------------------------------------------

def test_p5_notes_filtering():
    raw_notes = [
        "asset:CAB-1234567890",
        "path:Assets/UI/MainMenu.prefab",
        "desc:Button to save game",
        "pathfinding: use the north route",  # Not "path:" prefix!
        "type:UI_BUTTON",
        "cat:menu_action",
        "fld:label_text",
        "term:save_slot",
        "extra_note: should be capped",
    ]

    filtered = _filter_notes_for_payload(raw_notes)

    # asset: and path: dropped
    assert not any(n.startswith("asset:") for n in filtered)
    assert not any(n.startswith("path:") for n in filtered)

    # "pathfinding:..." preserved because it does not start with "path:"
    assert "pathfinding: use the north route" in filtered
    assert "desc:Button to save game" in filtered
    assert "type:UI_BUTTON" in filtered

    # Capped at 5 items
    assert len(filtered) == 5

    # Truncation to 200 chars
    long_note = "desc:" + "a" * 300
    filtered_long = _filter_notes_for_payload([long_note])
    assert len(filtered_long[0]) == 200


def test_p5_classification_unaffected(tmp_path):
    # Ensure notes filtering is strictly at payload boundary; Entry.notes remains intact for classify
    entry = Entry(
        file="dump.json",
        key="btn_save",
        source="Save",
        notes=["asset:CAB-9999", "path:Assets/Dialogs/Scene1.asset", "cat:dialogue", "speaker:John"],
    )
    categories = [
        CategoryRule(name="dialogue", match_notes_regex=r"cat:dialogue"),
        CategoryRule(name="ui", is_default=True),
    ]

    # Internal classification still sees "cat:dialogue"
    matched_rule = next((c for c in categories if c.matches(entry)), categories[-1])
    assert matched_rule.name == "dialogue"

    # User payload drops asset: and path:
    batch = TranslationBatch(category="dialogue", representatives=[entry])
    payload = json.loads(build_user_payload(batch))
    assert len(payload) == 1
    assert "asset:CAB-9999" not in payload[0]["notes"]
    assert "path:Assets/Dialogs/Scene1.asset" not in payload[0]["notes"]
    assert "cat:dialogue" in payload[0]["notes"]
    assert "speaker:John" in payload[0]["notes"]

    # Entry.notes was NOT modified in-place
    assert len(entry.notes) == 4
    assert entry.notes[0] == "asset:CAB-9999"


# ---------------------------------------------------------------------------
# P9: Glossary check caching by (path, mtime)
# ---------------------------------------------------------------------------

def test_p9_load_glossary_for_check_caching(tmp_path):
    glossary_file = tmp_path / "glossary.md"
    glossary_file.write_text(
        "# Glossary\n\n"
        "| Source term | Target translation | Category | Confidence | Source/justification |\n"
        "|---|---|---|---|---|\n"
        "| Fireball | Tűzgolyó | mechanic | high | spell |\n",
        encoding="utf-8",
    )

    _GLOSSARY_CHECK_CACHE.clear()

    # (a) First load parses from disk
    entries1 = load_glossary_for_check(str(glossary_file))
    assert len(entries1) == 1
    assert entries1[0]["source"] == "Fireball"

    # (b) Second load with same path and mtime uses cache (spy on parse_glossary)
    with patch("locpipe.validators.glossary_terms.parse_glossary") as mock_parse:
        entries2 = load_glossary_for_check(str(glossary_file))
        assert not mock_parse.called
        assert entries1 == entries2

    # (c) Modified mtime causes cache miss and fresh parse
    time.sleep(0.05)
    glossary_file.write_text(
        "# Glossary\n\n"
        "| Source term | Target translation | Category | Confidence | Source/justification |\n"
        "|---|---|---|---|---|\n"
        "| Fireball | Tűzgolyó | mechanic | high | spell |\n"
        "| Icebolt | Jégnyíl | mechanic | high | spell |\n",
        encoding="utf-8",
    )

    entries3 = load_glossary_for_check(str(glossary_file))
    assert len(entries3) == 2
    assert entries3[1]["source"] == "Icebolt"
