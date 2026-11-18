"""
Tests for review_effort default ("high"), category effort overrides,
and the `locpipe verify` post-run integrity command.
"""

from __future__ import annotations

import json
from pathlib import Path
import pytest
import yaml

from locpipe.config import load_project
from locpipe.cli import main as cli_main
from locpipe.pipeline import run
from locpipe.providers.mock import MockProvider


def test_review_effort_defaults_to_high(tmp_path: Path):
    proj_dir = tmp_path / "test_proj"
    proj_dir.mkdir()
    (proj_dir / "project.yaml").write_text(yaml.dump({
        "project": "test_proj",
        "source_lang": "en",
        "target_lang": "hu",
        "format": "uabea_json",
        "provider": {
            "name": "antigravity_cli",
            "model": "gemini-3.7-flash",
            "effort": "low",
        }
    }), encoding="utf-8")

    cfg = load_project(proj_dir)
    assert cfg.provider.effort == "low"
    assert cfg.provider.review_effort == "high"


def test_category_effort_override_parsed(tmp_path: Path):
    proj_dir = tmp_path / "test_proj"
    proj_dir.mkdir()
    (proj_dir / "project.yaml").write_text(yaml.dump({
        "project": "test_proj",
        "source_lang": "en",
        "target_lang": "hu",
        "format": "uabea_json",
        "categories": [
            {"name": "dialogue", "match_speaker_present": True, "effort": "high"},
            {"name": "ui", "default": True, "effort": "low"},
            {"name": "misc"}
        ],
        "provider": {
            "name": "antigravity_cli",
            "model": "gemini-3.7-flash",
            "effort": "low",
        }
    }), encoding="utf-8")

    cfg = load_project(proj_dir)
    cat_map = {c.name: c.effort for c in cfg.categories}
    assert cat_map["dialogue"] == "high"
    assert cat_map["ui"] == "low"
    assert cat_map["misc"] is None


def test_category_effort_passed_to_provider(tmp_path: Path):
    proj_dir = tmp_path / "test_proj"
    (proj_dir / "batches").mkdir(parents=True)
    
    # Create batch file
    batch_json = {
        "dialogue_line": {
            "m_Text": "Hello world"
        }
    }
    (proj_dir / "batches" / "b1.json").write_text(json.dumps(batch_json), encoding="utf-8")

    (proj_dir / "project.yaml").write_text(yaml.dump({
        "project": "test_proj",
        "source_lang": "en",
        "target_lang": "hu",
        "format": "uabea_json",
        "batches": {"glob": "batches/*.json"},
        "categories": [
            {"name": "dialogue", "match_key_regex": "dialogue_line", "effort": "high"},
            {"name": "ui", "default": True}
        ],
        "provider": {
            "name": "antigravity_cli",
            "model": "gemini-3.7-flash",
            "effort": "low",
        }
    }), encoding="utf-8")

    cfg = load_project(proj_dir)

    calls = []
    class TrackingMock(MockProvider):
        async def complete(self, system_prompt: str, user_payload: str, *, max_tokens: int = 8192, effort: str | None = None) -> str:
            calls.append(effort)
            return await super().complete(system_prompt, user_payload, max_tokens=max_tokens, effort=effort)

    provider = TrackingMock()
    run(cfg, provider, review_provider=provider, escalation_provider=provider)

    assert len(calls) > 0
    assert "high" in calls


def test_verify_no_snapshots_error(tmp_path: Path, capsys):
    proj_dir = tmp_path / "test_proj"
    (proj_dir / "batches").mkdir(parents=True)
    (proj_dir / "batches" / "b1.json").write_text(json.dumps({"text": "Hello"}), encoding="utf-8")

    (proj_dir / "project.yaml").write_text(yaml.dump({
        "project": "test_proj",
        "source_lang": "en",
        "target_lang": "hu",
        "format": "uabea_json",
        "batches": {"glob": "batches/*.json"}
    }), encoding="utf-8")

    exit_code = cli_main(["verify", "--project", str(proj_dir)])
    assert exit_code != 0
    out = capsys.readouterr().out
    assert "No pre-merge snapshots found" in out
    assert "cannot be retroactively verified" in out


def test_verify_clean_run_and_corrupted_anomaly_detection(tmp_path: Path, capsys):
    proj_dir = tmp_path / "test_proj"
    (proj_dir / "batches").mkdir(parents=True)
    
    # Original batch file with translatable, noise (GUID), and excluded paths
    batch_data = {
        "title": "Start Game",
        "asset_guid": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
        "internal_config": "DoNotTouch"
    }
    batch_path = proj_dir / "batches" / "b1.json"
    batch_path.write_text(json.dumps(batch_data, indent=2), encoding="utf-8")

    (proj_dir / "project.yaml").write_text(yaml.dump({
        "project": "test_proj",
        "source_lang": "en",
        "target_lang": "hu",
        "format": "uabea_json",
        "batches": {"glob": "batches/*.json"},
        "format_options": {
            "noise_filter": True,
            "uabea_json_path_exclude": ["^internal_config$"]
        }
    }), encoding="utf-8")

    cfg = load_project(proj_dir)
    provider = MockProvider()

    # 1. Run translation (takes snapshot and merges)
    run(cfg, provider, review_provider=provider, escalation_provider=provider)

    # Verify snapshot was created
    snapshot_file = proj_dir / "tm" / "pre_merge_snapshots" / "b1.json"
    assert snapshot_file.exists()

    # 2. Run verify on clean merged project
    capsys.readouterr()
    exit_code = cli_main(["verify", "--project", str(proj_dir)])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "Anomalies Found:                       0" in out
    assert "100% verified clean" in out

    # 3. Corrupt an excluded / noise path in the merged file
    corrupted_data = json.loads(batch_path.read_text(encoding="utf-8"))
    corrupted_data["internal_config"] = "CorruptedValue"
    batch_path.write_text(json.dumps(corrupted_data, indent=2), encoding="utf-8")

    # 4. Run verify again — must report anomaly and exit non-zero
    exit_code = cli_main(["verify", "--project", str(proj_dir)])
    out = capsys.readouterr().out
    assert exit_code != 0
    assert "ANOMALIES DETECTED" in out
    assert "internal_config" in out
    assert "CorruptedValue" in out

    # 5. Run auto on corrupted project — must halt with non-zero exit code
    exit_code_auto = cli_main(["auto", "--project", str(proj_dir), "--dry-run", "--skip-canary"])
    out_auto = capsys.readouterr().out
    assert exit_code_auto != 0
    assert "Integrity verification detected anomalies" in out_auto
