import csv
import pytest
from pathlib import Path

from locpipe.config import ProjectConfig, CategoryRule, ProviderConfig
from locpipe.schemas import build_system_prompt_for_category
from locpipe.validators.registry import run_validator
from locpipe.validators import validate_unity_csv
from locpipe.models import Severity


def test_p13_gate_naturalness_default_off(tmp_path):
    # Default: gate_naturalness is False, NATURALNESS is present for all categories
    cfg = ProjectConfig(
        project="test_p13",
        source_lang="en",
        target_lang="hu",
        format="generic_kv",
        root=tmp_path,
        batch_glob="",
        resources={},
        categories=[
            CategoryRule(name="ui", needs_character_voice=False),
            CategoryRule(name="dialogue", needs_character_voice=True),
        ],
        provider=ProviderConfig(),
        tm_db_path=tmp_path / "tm.sqlite3",
        gate_naturalness=False,
    )
    prompt_ui = build_system_prompt_for_category(cfg, "ui", [])
    prompt_dlg = build_system_prompt_for_category(cfg, "dialogue", [])
    assert "--- NATURALNESS ---" in prompt_ui
    assert "--- NATURALNESS ---" in prompt_dlg


def test_p13_gate_naturalness_opt_in_on(tmp_path):
    # Opt-in: gate_naturalness is True, NATURALNESS is gated by needs_character_voice
    cfg = ProjectConfig(
        project="test_p13",
        source_lang="en",
        target_lang="hu",
        format="generic_kv",
        root=tmp_path,
        batch_glob="",
        resources={},
        categories=[
            CategoryRule(name="ui", needs_character_voice=False),
            CategoryRule(name="dialogue", needs_character_voice=True),
        ],
        provider=ProviderConfig(),
        tm_db_path=tmp_path / "tm.sqlite3",
        gate_naturalness=True,
    )
    prompt_ui = build_system_prompt_for_category(cfg, "ui", [])
    prompt_dlg = build_system_prompt_for_category(cfg, "dialogue", [])
    assert "--- NATURALNESS ---" not in prompt_ui
    assert "--- NATURALNESS ---" in prompt_dlg


def test_p14_unity_validator_direct_import(tmp_path):
    csv_file = tmp_path / "test.csv"
    with open(csv_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Key", "Id", "en", "hu"])
        writer.writerow(["BTN_START", "1", "Start {0}", "Indítás {0}"])
        writer.writerow(["BTN_QUIT", "2", "Quit {0}", "Kilépés {1}"])  # token mismatch

    vr = run_validator(
        "unity",
        csv_file,
        format_kwargs={"source_col": "en", "target_col": "hu"},
    )
    assert not vr.passed
    assert len(vr.major) == 1
    assert "token-keszlet" in vr.major[0].message


def test_p14_unity_validator_fallback_to_subprocess(tmp_path, monkeypatch):
    csv_file = tmp_path / "test.csv"
    with open(csv_file, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Key", "Id", "en", "hu"])
        writer.writerow(["BTN_START", "1", "Start {0}", "Indítás {0}"])
        writer.writerow(["BTN_QUIT", "2", "Quit {0}", "Kilépés {1}"])

    # Force direct execution to raise an exception to trigger subprocess fallback
    def failing_validate_file(*args, **kwargs):
        raise RuntimeError("Simulated in-process failure")

    monkeypatch.setattr(validate_unity_csv, "validate_file", failing_validate_file)

    vr = run_validator(
        "unity",
        csv_file,
        format_kwargs={"source_col": "en", "target_col": "hu"},
    )
    # Subprocess fallback should run cleanly and produce the same result
    assert not vr.passed
    assert len(vr.major) == 1
    assert "token-keszlet" in vr.major[0].message
