"""
Unit and Integration Test Suite for GameStringer Utilities.

Tests Font Checker (Unity/IL2CPP), Addressables CRC Fixer, Backup Management,
Quote Consistency Checker, and trimmed GameStringer CLI commands.
"""

import os
import sys
import json
import zlib
import tempfile
from pathlib import Path
from click.testing import CliRunner

# Add root directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from locpipe.preflight.font_check import check_game_fonts
from gamestringer.core.addressables_crc import calculate_crc32, fix_catalog_crc_command, auto_update_addressables_crc
from gamestringer.core.backup import create_backup, restore_backup, list_backups
from gamestringer.core.quote_checker import check_xliff_quotes
from gamestringer.cli import main


def test_font_checker_unsupported_engine():
    with tempfile.TemporaryDirectory() as tmpdir:
        res = check_game_fonts(tmpdir, "unreal")
        assert res["status"] == "unsupported"
        assert "not yet supported" in res["message"]

        res_renpy = check_game_fonts(tmpdir, "renpy")
        assert res_renpy["status"] == "unsupported"


def test_font_checker_unity_detection():
    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. Empty unity dir -> warning
        res_empty = check_game_fonts(tmpdir, "unity")
        assert res_empty["status"] == "warning"

        # 2. Add TTF font and hungarian text config
        with open(os.path.join(tmpdir, "game_font.ttf"), "wb") as f:
            f.write(b"\x00\x01\x00\x00")  # mock TTF header

        with open(os.path.join(tmpdir, "lang_config.json"), "w", encoding="utf-8") as f:
            f.write('{"language": "hungarian", "sample": "árvíztűrő fúrógép ő ű"}')

        res_detected = check_game_fonts(tmpdir, "unity")
        assert res_detected["status"] == "supported"
        assert "game_font.ttf" in res_detected["font_assets"]


def test_addressables_crc_calculation_and_fix():
    with tempfile.TemporaryDirectory() as tmpdir:
        bundle_file = os.path.join(tmpdir, "assets_all.bundle")
        bundle_content = b"MockUnityBundleContent_1234567890"
        with open(bundle_file, "wb") as f:
            f.write(bundle_content)

        crc = calculate_crc32(bundle_file)
        expected_crc = zlib.crc32(bundle_content) & 0xFFFFFFFF
        assert crc == expected_crc

        catalog_path = os.path.join(tmpdir, "catalog.json")
        cat_data = {
            "m_InternalIds": ["assets_all.bundle"],
            "m_Crcs": {"assets_all.bundle": 11111}
        }
        with open(catalog_path, "w", encoding="utf-8") as f:
            json.dump(cat_data, f)

        res = fix_catalog_crc_command(tmpdir)
        assert res["catalog_found"] is True
        assert "assets_all.bundle" in res["updated_files"]

        with open(catalog_path, "r", encoding="utf-8") as f:
            updated_data = json.load(f)
            assert updated_data["m_Crcs"]["assets_all.bundle"] == expected_crc


def test_auto_update_addressables_crc():
    with tempfile.TemporaryDirectory() as tmpdir:
        bundle_file = os.path.join(tmpdir, "levels.bundle")
        with open(bundle_file, "wb") as f:
            f.write(b"LevelData_Bundle_Test")

        cat_path = os.path.join(tmpdir, "catalog.json")
        with open(cat_path, "w", encoding="utf-8") as f:
            json.dump({"levels.bundle": 0}, f)

        logs = auto_update_addressables_crc(tmpdir, [bundle_file])
        assert len(logs) > 0
        assert "levels.bundle" in logs[0]


def test_backup_create_restore_list():
    with tempfile.TemporaryDirectory() as tmpdir:
        test_file = os.path.join(tmpdir, "config.json")
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("ORIGINAL_CONTENT")

        # 1. Create backup
        bak_path = create_backup(test_file)
        assert os.path.exists(bak_path)
        assert len(list_backups(test_file)) == 1

        # 2. Modify original
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("MODIFIED_CONTENT")
        assert open(test_file).read() == "MODIFIED_CONTENT"

        # 3. Restore backup
        restore_backup(bak_path, test_file)
        assert open(test_file).read() == "ORIGINAL_CONTENT"


def test_quote_checker():
    with tempfile.TemporaryDirectory() as tmpdir:
        xliff_path = os.path.join(tmpdir, "test_quotes.xliff")
        xliff_content = """<?xml version="1.0" encoding="utf-8"?>
<xliff xmlns="urn:oasis:names:tc:xliff:document:1.2" version="1.2">
<file source_language="en" target_language="hu" datatype="plaintext" original="test">
<body>
<trans-unit id="1"><source>"Hello"</source><target>"Szia"</target></trans-unit>
<trans-unit id="2"><source>"Hello"</source><target>„Szia</target></trans-unit>
<trans-unit id="3"><source>"Hello"</source><target>Szia</target></trans-unit>
<trans-unit id="4"><source>"Hello"</source><target>„Szia”</target></trans-unit>
</body>
</file>
</xliff>"""
        with open(xliff_path, "w", encoding="utf-8") as f:
            f.write(xliff_content)

        report_file = os.path.join(tmpdir, "report.json")
        res = check_xliff_quotes(xliff_path, report_file)
        assert res["total_checked"] == 4
        assert res["issues_found"] == 3
        assert os.path.exists(report_file)


def test_cli_commands():
    runner = CliRunner()

    with tempfile.TemporaryDirectory() as tmpdir:
        # Test check-fonts command
        res_font = runner.invoke(main, ["check-fonts", "--input", tmpdir, "--engine", "unity"])
        assert res_font.exit_code == 0

        # Test fix-catalog command
        res_crc = runner.invoke(main, ["fix-catalog", "--input", tmpdir])
        assert res_crc.exit_code == 0

        # Test help includes unified commands
        res_help = runner.invoke(main, ["--help"])
        assert res_help.exit_code == 0
        assert "plan" in res_help.output
        assert "run" in res_help.output
        assert "audit" in res_help.output
        assert "verify" in res_help.output
        assert "gui" in res_help.output


if __name__ == "__main__":
    test_font_checker_unsupported_engine()
    test_font_checker_unity_detection()
    test_addressables_crc_calculation_and_fix()
    test_auto_update_addressables_crc()
    test_backup_create_restore_list()
    test_quote_checker()
    test_cli_commands()
    print("\n✅ All GameStringer utility tests passed successfully!")
