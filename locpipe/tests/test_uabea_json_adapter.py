"""Unit tests for the UABEA JSON format adapter and protected-token validation.

Uses small inline fixtures rather than real game exports, so the suite is
self-contained and doesn't depend on files outside the repo.
"""

from pathlib import Path
import json
import shutil
import tempfile

from locpipe.adapters.uabea_json import UABEAJsonAdapter
from locpipe.adapters.registry import get_adapter
from locpipe.validators.protected_tokens import extract_protected_tokens, validate_protected_tokens, audit_entry_tokens

# Minimal UABEA-shaped m_Script CSV: header + one data row, EN source / HU target
# plus a couple of extra non-target language columns to exercise column preservation.
_SAMPLE_SCRIPT_CSV = (
    "Key,EN,HU,FR\r\n"
    "Consumables:Item001,Increased @primary attack@ {comma} {0}%,,Augmentation\r\n"
)

_SAMPLE_UABEA_JSON = {
    "m_Name": "Consumables",
    "m_Script": _SAMPLE_SCRIPT_CSV,
}


def _write_sample_uabea_json(path: Path) -> None:
    path.write_text(json.dumps(_SAMPLE_UABEA_JSON), encoding="utf-8")


def test_registry_uabea_json_adapter():
    adapter = get_adapter("uabea_json", {"target_column": "HU"})
    assert isinstance(adapter, UABEAJsonAdapter)
    assert adapter.target_col_name == "HU"


def test_extraction_uabea_json():
    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "Consumables.json"
        _write_sample_uabea_json(file_path)

        adapter = UABEAJsonAdapter({"source_column": "EN", "target_column": "HU"})
        entries = adapter.extract(file_path)

        assert len(entries) > 0
        first_entry = entries[0]
        assert first_entry.source != ""
        assert first_entry.key.startswith("Consumables:")
        assert "uabea_structure" in first_entry.extra
        assert first_entry.extra["uabea_structure"] == "csv_m_script"

        # Non-target language columns and the raw header must not leak into source text
        assert "FR" not in first_entry.source
        assert "EN_VER" not in first_entry.source


def test_noop_round_trip_uabea_json():
    """Verify that extract -> merge without changes produces a data-equivalent UABEA JSON."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        src_file = Path(tmp_dir) / "Consumables.json"
        _write_sample_uabea_json(src_file)

        adapter = UABEAJsonAdapter({"source_column": "EN", "target_column": "HU"})
        entries = adapter.extract(src_file)

        # Merge back without translating targets
        adapter.merge(src_file, entries)

        merged_data = json.loads(src_file.read_text(encoding="utf-8"))
        assert merged_data["m_Name"] == "Consumables"
        assert "m_Script" in merged_data


def test_simulated_translation_round_trip():
    """Verify in-place update of target HU column while preserving other columns & metadata."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_file = Path(tmp_dir) / "Consumables.json"
        _write_sample_uabea_json(tmp_file)

        adapter = UABEAJsonAdapter({"source_column": "EN", "target_column": "HU"})
        entries = adapter.extract(tmp_file)

        # Apply simulated Hungarian translation with protected token
        entries[0].target = "Idézés: @primary attack@ {comma} teszt"

        adapter.merge(tmp_file, entries)

        merged_data = json.loads(tmp_file.read_text(encoding="utf-8"))
        script_text = merged_data["m_Script"]

        import csv, io
        reader = list(csv.reader(io.StringIO(script_text)))
        header = reader[0]
        header_upper = [c.upper() for c in header]

        assert "HU" in header_upper
        hu_idx = header_upper.index("HU")
        en_idx = header_upper.index("EN")

        first_row = reader[1]
        assert first_row[hu_idx] == "Idézés: @primary attack@ {comma} teszt"
        # Source EN column remains untouched
        assert first_row[en_idx] == entries[0].source


def test_protected_tokens_extraction_and_validation():
    source_text = "Increased @primary attack@ @damage@ by {comma} {0}% with <color=#FF0000>fire</color>."

    tokens = extract_protected_tokens(source_text)
    assert "@primary attack@" in tokens
    assert "@damage@" in tokens
    assert "{comma}" in tokens
    assert "{0}" in tokens
    assert "<color=#FF0000>" in tokens
    assert "</color>" in tokens

    # Valid translation preserving exact protected tokens
    valid_target = "Növelt @primary attack@ @damage@ {comma} {0}% mértékben <color=#FF0000>tűzzel</color>."
    missing, modified = validate_protected_tokens(source_text, valid_target)
    assert len(missing) == 0
    assert len(modified) == 0

    issues = audit_entry_tokens(source_text, valid_target)
    assert len(issues) == 0

    # Invalid translation where token was translated/altered (@primary attack@ -> @támadás@)
    invalid_target = "Növelt @támadás@ @damage@ {comma} {0}%."
    missing_inv, modified_inv = validate_protected_tokens(source_text, invalid_target)
    assert len(missing_inv) > 0 or len(modified_inv) > 0

    issues_inv = audit_entry_tokens(source_text, invalid_target)
    assert len(issues_inv) > 0


# --- Case 2 (typetree walk): engine-noise filtering & path excludes ---

_SAMPLE_TYPETREE_JSON = {
    "m_Name": "LocalizedTextBank",
    "m_GameObject": {"m_FileID": 0, "m_PathID": 12345},  # IGNORED_UNITY_KEYS, always skipped
    "entries": {
        "Dodge": "You narrowly dodge the incoming blow!",
        "AmirKabir_greeting": "Welcome to the bazaar, traveler.",
        "internal_metadata": {
            "asset_guid": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
            "sprite_ref": "icons/dodge_icon.png",
        },
        "state_flags": {
            "combat_state": "GAME_STATE_PAUSED",
            "node_id": "weight_001",
        },
    },
}


def _write_sample_typetree_json(path: Path) -> None:
    path.write_text(json.dumps(_SAMPLE_TYPETREE_JSON), encoding="utf-8")


def test_typetree_extraction_keeps_narrative_text():
    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "LocalizedTextBank.json"
        _write_sample_typetree_json(file_path)

        adapter = UABEAJsonAdapter()
        entries = adapter.extract(file_path)
        sources = {e.source for e in entries}

        assert "You narrowly dodge the incoming blow!" in sources
        assert "Welcome to the bazaar, traveler." in sources


def test_typetree_extraction_filters_engine_noise_by_default():
    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "LocalizedTextBank.json"
        _write_sample_typetree_json(file_path)

        adapter = UABEAJsonAdapter()
        entries = adapter.extract(file_path)
        sources = {e.source for e in entries}

        # GUID, asset path, enum constant, indexed id -- none of these
        # should ever reach the LLM as "translatable" text.
        assert "3f2504e0-4f89-11d3-9a0c-0305e82c3301" not in sources
        assert "icons/dodge_icon.png" not in sources
        assert "GAME_STATE_PAUSED" not in sources
        assert "weight_001" not in sources


def test_noise_filter_can_be_disabled():
    """The escape hatch: noise_filter: false must restore the old
    walk-everything behavior exactly, for anyone who wants the raw
    extraction (e.g. to compare against a `locpipe audit` report)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "LocalizedTextBank.json"
        _write_sample_typetree_json(file_path)

        adapter = UABEAJsonAdapter({"noise_filter": False})
        entries = adapter.extract(file_path)
        sources = {e.source for e in entries}

        assert "3f2504e0-4f89-11d3-9a0c-0305e82c3301" in sources
        assert "weight_001" in sources


def test_uabea_json_path_exclude_skips_whole_subtree():
    """A project-specific regex exclude must drop the whole subtree it
    matches -- not just a single leaf -- so one pattern can silence an
    entire noisy branch (e.g. "internal_metadata") in one line."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "LocalizedTextBank.json"
        _write_sample_typetree_json(file_path)

        # Disable the built-in noise filter so we can prove the EXCLUDE
        # pattern itself is what's doing the filtering here, not noise_reason.
        adapter = UABEAJsonAdapter(
            {"noise_filter": False, "uabea_json_path_exclude": [r"^entries\.internal_metadata"]}
        )
        entries = adapter.extract(file_path)
        sources = {e.source for e in entries}

        assert "3f2504e0-4f89-11d3-9a0c-0305e82c3301" not in sources
        assert "icons/dodge_icon.png" not in sources
        # Sibling branches, unaffected by the exclude pattern, must survive.
        assert "You narrowly dodge the incoming blow!" in sources
        assert "GAME_STATE_PAUSED" in sources  # not filtered: noise_filter is off, no matching exclude


def test_typetree_audit_sink_records_kept_and_dropped_with_reasons():
    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "LocalizedTextBank.json"
        _write_sample_typetree_json(file_path)

        adapter = UABEAJsonAdapter()
        sink: list = []
        adapter.extract(file_path, audit_sink=sink)

        by_action = {(path, value): action for path, value, action in sink}
        assert by_action[("entries.Dodge", "You narrowly dodge the incoming blow!")] == "kept"
        assert by_action[
            ("entries.internal_metadata.asset_guid", "3f2504e0-4f89-11d3-9a0c-0305e82c3301")
        ] == "noise:guid"
        assert by_action[("entries.internal_metadata.sprite_ref", "icons/dodge_icon.png")] == "noise:asset-reference"
        assert by_action[("entries.state_flags.combat_state", "GAME_STATE_PAUSED")] == "noise:enum-constant"
        assert by_action[("entries.state_flags.node_id", "weight_001")] == "noise:indexed-identifier"


def test_i2_languagesource_extraction_and_merge():
    i2_sample = {
        "m_Name": "LanguageSource",
        "mLanguages": {
            "Array": [
                {"Name": "English", "Code": "en"},
                {"Name": "French", "Code": "fr"},
            ]
        },
        "mTerms": {
            "Array": [
                {
                    "Term": "general/about",
                    "TermType": 0,
                    "Description": "About the game",
                    "Languages": {"Array": ["About", "À propos"]},
                },
                {
                    "Term": "fonts/main",
                    "TermType": 1,
                    "Description": "Font asset",
                    "Languages": {"Array": ["font_roboto", "font_roboto"]},
                },
                {
                    "Term": "language/code",
                    "TermType": 0,
                    "Description": "",
                    "Languages": {"Array": ["en", "fr"]},
                },
            ]
        },
    }

    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "LanguageSource.json"
        file_path.write_text(json.dumps(i2_sample), encoding="utf-8")

        adapter = UABEAJsonAdapter({"source_column": "EN", "target_column": "HU"})
        sink: list = []
        entries = adapter.extract(file_path, audit_sink=sink)

        # Only the real text term should be extracted, fonts and language codes are filtered
        assert len(entries) == 1
        entry = entries[0]
        assert entry.source == "About"
        assert entry.key == "LanguageSource:general/about"
        assert "desc:About the game" in entry.notes
        assert entry.extra["uabea_structure"] == "i2_languagesource"

        # Test merge
        entry.target = "Névjegy"
        adapter.merge(file_path, [entry])

        merged_data = json.loads(file_path.read_text(encoding="utf-8"))
        # English slot updated to Névjegy, French untouched
        assert merged_data["mTerms"]["Array"][0]["Languages"]["Array"][0] == "Névjegy"
        assert merged_data["mTerms"]["Array"][0]["Languages"]["Array"][1] == "À propos"


def test_json_array_context_and_speaker_propagation():
    """Verify that JSON array items propagate context, speaker, and notes into Entry,
    allowing category regex matching against both key and notes.
    """
    sample_array = [
        {
            "id": 101,
            "source": "Shuraba is a demon-forged blade.",
            "target": "",
            "context": "mm/mmWPN001_us.dat:mmWPN001.mcd",
            "speaker": "Rodin",
            "comment": "weapon description",
        },
        {
            "id": 102,
            "source": "Don't be reckless, Cereza!",
            "target": "",
            "context": "evm_c010_us.dat",
            "speaker": "Jeanne",
        },
        {
            "id": 103,
            "source": "Options",
            "target": "",
        },
    ]

    with tempfile.TemporaryDirectory() as tmp_dir:
        file_path = Path(tmp_dir) / "batch_0000.json"
        file_path.write_text(json.dumps(sample_array), encoding="utf-8")

        adapter = UABEAJsonAdapter()
        entries = adapter.extract(file_path)

        assert len(entries) == 3

        # Entry 1: Weapon with context and speaker
        e1 = entries[0]
        assert e1.speaker == "Rodin"
        assert "context:mm/mmWPN001_us.dat:mmWPN001.mcd" in e1.notes
        assert "weapon description" in e1.notes
        assert "mmWPN001" in e1.key
        assert e1.extra["context"] == "mm/mmWPN001_us.dat:mmWPN001.mcd"
        assert e1.extra["speaker"] == "Rodin"

        # Verify CategoryRule matching against key and speaker
        from locpipe.config import CategoryRule
        rule_wpn = CategoryRule(name="weapons", match_key_regex=r"(:|^)(mmWPN)")
        assert rule_wpn.matches(e1) is True

        rule_speaker = CategoryRule(name="dialogue", match_speaker_present=True)
        assert rule_speaker.matches(e1) is True

        # Entry 2: Cutscene with evm_ context and Jeanne speaker
        e2 = entries[1]
        assert e2.speaker == "Jeanne"
        assert "evm_c010" in e2.key
        assert "context:evm_c010_us.dat" in e2.notes
        rule_cutscene = CategoryRule(name="cutscenes", match_key_regex=r"(:|^)(evm_)")
        assert rule_cutscene.matches(e2) is True

        # Entry 3: Fallback without context/speaker
        e3 = entries[2]
        assert e3.speaker is None
        assert e3.notes is None
        assert rule_wpn.matches(e3) is False
        assert rule_speaker.matches(e3) is False

        # Test merge round-trip
        e1.target = "A Shuraba egy démonkovácsolta kard."
        adapter.merge(file_path, [e1])
        merged = json.loads(file_path.read_text(encoding="utf-8"))
        assert merged[0]["target"] == "A Shuraba egy démonkovácsolta kard."
        assert merged[0]["context"] == "mm/mmWPN001_us.dat:mmWPN001.mcd"
        assert merged[0]["speaker"] == "Rodin"


