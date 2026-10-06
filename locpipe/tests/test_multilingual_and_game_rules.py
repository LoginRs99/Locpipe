"""Tests for Step 1 (Multilingual support: JA, EN, HU) and Step 2 (Game engine rules,
control/escape codes, physical character length limits, and Ruby/Furigana).
"""

from pathlib import Path
import tempfile
import csv

from locpipe.models import Entry, EntryStatus, Severity, ValidationResult
from locpipe.prompt_builder import get_register_instruction
from locpipe.validators.protected_tokens import extract_protected_tokens, validate_protected_tokens, audit_entry_tokens
from locpipe.validators.quote_balance import audit_quote_pair
from locpipe.confidence import score, confidence_flags
from locpipe.config import ProjectConfig, ProviderConfig, CategoryRule
from locpipe.pipeline import _run_all_validators
from locpipe.adapters.unity import UnityCSVAdapter
from locpipe.adapters.uabea_json import UABEAJsonAdapter


def test_register_instructions_multilingual():
    # Hungarian
    assert "tegez" in get_register_instruction("informal", "hu")
    assert "magáz" in get_register_instruction("formal", "hu")

    # Japanese
    ja_informal = get_register_instruction("informal", "ja")
    assert "普通体" in ja_informal or "タメ口" in ja_informal
    ja_formal = get_register_instruction("formal", "ja")
    assert "丁寧語" in ja_formal or "敬語" in ja_formal

    # English
    en_informal = get_register_instruction("informal", "en")
    assert "conversational" in en_informal or "natural" in en_informal
    en_formal = get_register_instruction("formal", "en")
    assert "formal" in en_formal


def test_japanese_quote_balance_and_mapping():
    # Japanese corner brackets to English double quotes
    issues_en = audit_quote_pair("「ようこそ、冒険者よ！」", '"Welcome, adventurer!"')
    assert len(issues_en) == 0

    # Japanese corner brackets to Hungarian quotes
    issues_hu = audit_quote_pair("「ようこそ、冒険者よ！」", '„Üdvözöllek, kalandor!”')
    assert len(issues_hu) == 0

    # Japanese double corner brackets 『...』 to quotes
    issues_book = audit_quote_pair("『失われた古代史』", '"Lost Ancient History"')
    assert len(issues_book) == 0


def test_japanese_expansion_ratio_confidence():
    cfg_ja = ProjectConfig(
        project="test_ja",
        source_lang="ja",
        target_lang="hu",
        format="generic_kv",
        provider=ProviderConfig(name="mock", model="dummy"),
        batch_glob="batches/*.json",
        resources={},
        categories=[],
        tm_db_path=Path("tm.sqlite3"),
        root=Path("."),
    )

    # 4 kanji characters expanding to a 24-character Hungarian phrase
    e = Entry(
        file="dummy",
        key="k1",
        source="新規開始設定",
        target="Új játék indítási beállítások",
        status=EntryStatus.MT_DRAFT,
    )
    vr = ValidationResult(entry_key="k1")
    conf = score(e, vr, cfg_ja)
    # Should not be penalized as an abnormal length explosion because source is Japanese
    assert conf >= 0.85
    flags = confidence_flags(e, cfg_ja)
    assert not any("expansion-ratio" in f for f in flags)


def test_game_engine_protected_tokens_and_escapes():
    source = r"Health: \C[2]{0}\C[0] HP.\nJump to \I[12] portal, then press @attack@!"
    tokens = extract_protected_tokens(source)

    assert r"\C[2]" in tokens
    assert r"\C[0]" in tokens
    assert "{0}" in tokens
    assert r"\n" in tokens
    assert r"\I[12]" in tokens
    assert "@attack@" in tokens

    # Valid translation preserving all escape codes and tokens
    target_valid = r"Életerő: \C[2]{0}\C[0] HP.\nUgorj a \I[12] portálhoz, majd nyomd meg a @attack@-ot!"
    issues = audit_entry_tokens(source, target_valid)
    assert len(issues) == 0

    # Missing \n and modified \C[2]
    target_invalid = r"Életerő: \C[3]{0}\C[0] HP. Ugorj a \I[12] portálhoz, majd nyomd meg a @támadás@-ot!"
    issues_bad = audit_entry_tokens(source, target_invalid)
    issue_codes = {i.code for i in issues_bad}
    assert "PROTECTED_TOKEN_MISSING" in issue_codes or "PROTECTED_TOKEN_MODIFIED" in issue_codes
    assert any(r"\n" in i.message for i in issues_bad)


def test_console_bracket_tokens_with_whitespace():
    source = "[BTN:L2 ] + [BTN:RS ] (hold): Move Legion to [HUDTEXT:MISSION_NAME ]. [COLOR:RED ]Alert![COLOR:WHITE ]"
    tokens = extract_protected_tokens(source)
    assert "[BTN:L2 ]" in tokens
    assert "[BTN:RS ]" in tokens
    assert "[HUDTEXT:MISSION_NAME ]" in tokens
    assert "[COLOR:RED ]" in tokens
    assert "[COLOR:WHITE ]" in tokens

    # Valid translation preserving tokens exactly
    target_valid = "[BTN:L2 ] + [BTN:RS ] (tartva): Legion mozgatása ide: [HUDTEXT:MISSION_NAME ]. [COLOR:RED ]Riadó![COLOR:WHITE ]"
    issues = audit_entry_tokens(source, target_valid)
    assert len(issues) == 0

    # Invalid translation where [BTN:L2 ] had space stripped and [COLOR:RED ] was translated
    target_invalid = "[BTN:L2] + [BTN:RS ] (tartva): Legion mozgatása ide: [HUDTEXT:MISSION_NAME ]. [SZÍN:PIROS ]Riadó![COLOR:WHITE ]"
    issues_bad = audit_entry_tokens(source, target_invalid)
    codes = {i.code for i in issues_bad}
    assert "PROTECTED_TOKEN_MISSING" in codes or "PROTECTED_TOKEN_MODIFIED" in codes


def test_protected_token_multiplicity_count():
    source = "Item {0} connects to {0} slot."
    # Target only preserved one {0}
    target = "A(z) {0} elem a foglalatba kapcsolódik."
    issues = audit_entry_tokens(source, target)
    assert any(i.code == "PROTECTED_TOKEN_COUNT_MISMATCH" and "{0}" in i.message for i in issues)


def test_gender_slot_markers_translation_preservation():
    source = "You met {ms|his father}{fs|her mother} in town."
    # Both gender slots preserved with translated content
    target_valid = "Találkoztál {ms|az apjával}{fs|az anyjával} a városban."
    issues = audit_entry_tokens(source, target_valid)
    assert len(issues) == 0

    # Missing {fs| slot
    target_invalid = "Találkoztál {ms|az apjával} a városban."
    issues_bad = audit_entry_tokens(source, target_invalid)
    assert any("fs" in i.message for i in issues_bad)


def test_physical_max_length_enforcement_in_pipeline(tmp_path: Path):
    cfg = ProjectConfig(
        project="test_len",
        source_lang="en",
        target_lang="hu",
        format="generic_kv",
        provider=ProviderConfig(name="mock", model="dummy"),
        batch_glob="batches/*.json",
        resources={},
        categories=[],
        tm_db_path=tmp_path / "tm.sqlite3",
        root=tmp_path,
    )

    batch_file = tmp_path / "batch.json"
    batch_file.write_text("[]", encoding="utf-8")

    entries = [
        Entry(file=str(batch_file), key="ui_btn", source="Save", target="Mentés", max_length=10),
        Entry(file=str(batch_file), key="ui_btn_over", source="Save", target="Mentés és Kilépés a Főmenübe", max_length=15),
    ]

    per_entry = _run_all_validators(batch_file, entries, cfg, format_kwargs={})
    assert per_entry["ui_btn"].passed is True
    assert per_entry["ui_btn_over"].passed is False
    assert any(i.code == "MAX_LENGTH_EXCEEDED" for i in per_entry["ui_btn_over"].major)


def test_unity_csv_multilingual_cross_translation(tmp_path: Path):
    csv_file = tmp_path / "japanese_game.csv"
    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "keyname", "Japanese", "English"])
        w.writerow(["101", "menu_start", "ゲーム開始", ""])
        w.writerow(["102", "menu_exit", "終了", ""])

    adapter = UnityCSVAdapter(source_lang="ja", target_lang="en")
    entries = adapter.extract(csv_file)
    assert len(entries) == 2
    assert entries[0].source == "ゲーム開始"
    assert entries[1].source == "終了"

    entries[0].target = "Start Game"
    entries[1].target = "Exit"
    adapter.merge(csv_file, entries)

    with open(csv_file, "r", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    assert rows[1][3] == "Start Game"
    assert rows[2][3] == "Exit"
