"""
Additional comprehensive unit and integration tests for GameStringer core modules:
- gamestringer.core.ui_whitelist
- gamestringer.core.backup (directory backup/restore, custom dir, edge cases)
- gamestringer.core.addressables_crc (recursive JSON updates, hash file updates, error cases)
- gamestringer.cli (options, flags, version, exit codes)
"""

import os
import json
import zlib
import pytest
from pathlib import Path
from click.testing import CliRunner

from gamestringer.core.ui_whitelist import UI_WHITELIST
from gamestringer.core.backup import create_backup, restore_backup, list_backups
from gamestringer.core.addressables_crc import (
    calculate_crc32,
    auto_update_addressables_crc,
    fix_catalog_crc_command,
    _update_json_crc_entry,
)
from gamestringer.cli import main


# ==========================================
# UI Whitelist Tests
# ==========================================

def test_ui_whitelist_properties():
    assert isinstance(UI_WHITELIST, set)
    assert len(UI_WHITELIST) > 50

    # All items must be lowercased strings
    for word in UI_WHITELIST:
        assert isinstance(word, str)
        assert word == word.lower()

    # Essential UI navigation & gaming terms
    essential_words = [
        "ok", "cancel", "save", "load", "settings", "options", "inventory",
        "attack", "pause", "resume", "continue", "main menu", "new game",
        "game over", "victory", "defeat"
    ]
    for w in essential_words:
        assert w in UI_WHITELIST


# ==========================================
# Backup Management Edge Cases
# ==========================================

def test_backup_file_and_custom_dir(tmp_path):
    src_file = tmp_path / "game_data.bin"
    src_file.write_bytes(b"DATA_V1")

    # 1. Backup alongside file
    bak1 = create_backup(str(src_file))
    assert os.path.exists(bak1)
    assert Path(bak1).name.startswith("game_data.bin.bak_")
    assert Path(bak1).read_bytes() == b"DATA_V1"

    # 2. Backup to custom directory
    custom_backup_dir = tmp_path / "backups_archive"
    bak2 = create_backup(str(src_file), backup_dir=str(custom_backup_dir))
    assert os.path.exists(bak2)
    assert custom_backup_dir.exists()
    assert Path(bak2).parent == custom_backup_dir

    # 3. Nonexistent file raises FileNotFoundError
    with pytest.raises(FileNotFoundError):
        create_backup(str(tmp_path / "nonexistent.bin"))


def test_backup_directory_create_and_restore(tmp_path):
    src_dir = tmp_path / "SaveDir"
    src_dir.mkdir()
    (src_dir / "slot1.sav").write_text("Slot1Data", encoding="utf-8")
    sub_dir = src_dir / "meta"
    sub_dir.mkdir()
    (sub_dir / "profile.json").write_text('{"name": "Hero"}', encoding="utf-8")

    # 1. Directory backup
    bak_dir = create_backup(str(src_dir))
    assert os.path.isdir(bak_dir)
    assert os.path.exists(os.path.join(bak_dir, "slot1.sav"))
    assert os.path.exists(os.path.join(bak_dir, "meta", "profile.json"))

    # 2. Mutate original directory
    (src_dir / "slot1.sav").write_text("CorruptedData", encoding="utf-8")
    (src_dir / "slot2.sav").write_text("NewSlot", encoding="utf-8")

    # 3. Restore directory backup
    success = restore_backup(bak_dir, str(src_dir))
    assert success is True
    assert (src_dir / "slot1.sav").read_text(encoding="utf-8") == "Slot1Data"
    assert not (src_dir / "slot2.sav").exists()
    assert (src_dir / "meta" / "profile.json").read_text(encoding="utf-8") == '{"name": "Hero"}'

    # 4. Restore nonexistent backup raises FileNotFoundError
    with pytest.raises(FileNotFoundError):
        restore_backup(str(tmp_path / "missing_bak"), str(src_dir))


def test_list_backups_ordering_and_missing_dir(tmp_path):
    target = tmp_path / "assets.bundle"
    target.write_bytes(b"bundle")

    # Initially empty
    assert list_backups(str(target)) == []

    # Create multiple backups with distinct timestamps
    bak_a = tmp_path / "assets.bundle.bak_20260101_100000"
    bak_b = tmp_path / "assets.bundle.bak_20260102_100000"
    bak_c = tmp_path / "assets.bundle.bak"
    bak_a.write_bytes(b"a")
    bak_b.write_bytes(b"b")
    bak_c.write_bytes(b"c")

    backups = list_backups(str(target))
    assert len(backups) == 3
    # Newest timestamp first
    assert backups[0] == str(bak_b)
    assert backups[1] == str(bak_a)

    # Missing parent dir returns []
    assert list_backups(str(tmp_path / "nonexistent_sub" / "target.txt")) == []


# ==========================================
# Addressables CRC Fixer Tests
# ==========================================

def test_calculate_crc32_empty_and_content(tmp_path):
    empty_file = tmp_path / "empty.bin"
    empty_file.write_bytes(b"")
    assert calculate_crc32(str(empty_file)) == 0

    content_file = tmp_path / "content.bin"
    test_bytes = b"UnityAssetBundleMagicBytes_123456789\x00\xFF"
    content_file.write_bytes(test_bytes)
    assert calculate_crc32(str(content_file)) == (zlib.crc32(test_bytes) & 0xFFFFFFFF)


def test_update_json_crc_entry_recursive():
    new_crc = 0xDEADBEEF

    # 1. In m_Crcs dict
    cat1 = {"m_Crcs": {"data.bundle": 123, "other.bundle": 456}}
    assert _update_json_crc_entry(cat1, "data.bundle", new_crc) is True
    assert cat1["m_Crcs"]["data.bundle"] == new_crc
    assert cat1["m_Crcs"]["other.bundle"] == 456

    # 2. In list of dicts (nested Addressables structure)
    cat2 = [
        {"name": "root"},
        [
            {"Crc": {"textures.bundle": 999}},
            {"item": "asset"}
        ]
    ]
    assert _update_json_crc_entry(cat2, "textures.bundle", new_crc) is True
    assert cat2[1][0]["Crc"]["textures.bundle"] == new_crc

    # 3. Direct key matching filename
    cat3 = {"audio_voice.bundle": 0}
    assert _update_json_crc_entry(cat3, "audio_voice.bundle", new_crc) is True
    assert cat3["audio_voice.bundle"] == new_crc

    # 4. Non-matching returns False
    cat4 = {"m_Crcs": {"unrelated.bundle": 123}}
    assert _update_json_crc_entry(cat4, "missing.bundle", new_crc) is False


def test_auto_update_addressables_crc_with_hashes(tmp_path):
    # Nonexistent dir returns empty list
    assert auto_update_addressables_crc(str(tmp_path / "missing_dir"), []) == []

    # Create bundle, catalog.json, and *.hash
    bundle_file = tmp_path / "stage1.bundle"
    bundle_content = b"Stage1_Asset_Content_Alpha"
    bundle_file.write_bytes(bundle_content)
    expected_crc = calculate_crc32(str(bundle_file))

    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text(json.dumps({"m_Crcs": {"stage1.bundle": 1}}), encoding="utf-8")

    hash_file = tmp_path / "stage1.hash"
    hash_file.write_text("0", encoding="utf-8")

    updates = auto_update_addressables_crc(str(tmp_path), [str(bundle_file)])
    assert len(updates) == 2
    assert any("Updated CRC32" in u and "stage1.bundle" in u for u in updates)
    assert any("Updated CRC32 hash file" in u and "stage1.hash" in u for u in updates)

    # Verify files updated
    new_cat = json.loads(catalog_file.read_text(encoding="utf-8"))
    assert new_cat["m_Crcs"]["stage1.bundle"] == expected_crc
    assert hash_file.read_text(encoding="utf-8") == str(expected_crc)


def test_fix_catalog_crc_command_edge_cases(tmp_path):
    # Nonexistent path raises ValueError
    with pytest.raises(ValueError):
        fix_catalog_crc_command(str(tmp_path / "does_not_exist"))

    # Empty dir with no catalog or hash files
    res_empty = fix_catalog_crc_command(str(tmp_path))
    assert res_empty["catalog_found"] is False
    assert res_empty["updated_files"] == []
    assert "No Addressables" in res_empty["message"]

    # Dir with catalog and sharedassets
    asset_file = tmp_path / "sharedassets0.assets"
    asset_file.write_bytes(b"SharedAssetsBytes")
    cat_file = tmp_path / "catalog_main.json"
    cat_file.write_text(json.dumps({"m_Crcs": {"sharedassets0.assets": 100}}), encoding="utf-8")

    res_found = fix_catalog_crc_command(str(tmp_path))
    assert res_found["catalog_found"] is True
    assert "sharedassets0.assets" in res_found["updated_files"]
    assert len(res_found["catalogs"]) == 1


# ==========================================
# CLI Flag & Command Tests
# ==========================================

def test_cli_flags_and_version():
    runner = CliRunner()

    # --version
    res_ver = runner.invoke(main, ["--version"])
    assert res_ver.exit_code == 0
    assert "2.0.0" in res_ver.output

    # --help
    res_help = runner.invoke(main, ["--help"])
    assert res_help.exit_code == 0
    assert "check-fonts" in res_help.output
    assert "fix-catalog" in res_help.output


def test_cli_fix_catalog_success_and_warning(tmp_path):
    runner = CliRunner()

    # 1. Warning when no catalog found
    res_warn = runner.invoke(main, ["fix-catalog", "--input", str(tmp_path)])
    assert res_warn.exit_code == 0
    assert "[WARNING]" in res_warn.output

    # 2. Success when catalog found and updated
    bundle = tmp_path / "data.bundle"
    bundle.write_bytes(b"BundleContent")
    cat = tmp_path / "catalog.json"
    cat.write_text(json.dumps({"data.bundle": 1}), encoding="utf-8")

    res_ok = runner.invoke(main, ["--verbose", "fix-catalog", "--input", str(tmp_path)])
    assert res_ok.exit_code == 0
    assert "[SUCCESS]" in res_ok.output


# ==========================================
# Quote Checker Tests (Hungarian vs Curly)
# ==========================================

def test_quote_checker_distinguishes_hungarian_and_curly():
    from gamestringer.core.quote_checker import _analyze_quote_pair
    from locpipe.validators.quote_balance import audit_quote_pair

    # 1. Hungarian quotes: „...”
    hu_issues = _analyze_quote_pair('Attack "now"', 'Támadj „most”')
    assert any("Hungarian quotes" in rec for _, rec in hu_issues)
    assert not any("curly quotes" in rec for _, rec in hu_issues)

    # 2. English curly quotes: “...”
    curly_issues = _analyze_quote_pair('Attack "now"', 'Attack “now”')
    assert any("curly quotes" in rec for _, rec in curly_issues)
    assert not any("Hungarian quotes" in rec for _, rec in curly_issues)

    # 3. Same check in locpipe validator audit_quote_pair
    val_hu = audit_quote_pair('Attack "now"', 'Támadj „most”')
    assert any("Hungarian quotes" in iss.message for iss in val_hu)

    val_curly = audit_quote_pair('Attack "now"', 'Attack “now”')
    assert any("curly quotes" in iss.message for iss in val_curly)
    assert not any("Hungarian quotes" in iss.message for iss in val_curly)
