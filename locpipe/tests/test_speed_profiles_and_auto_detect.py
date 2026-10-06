import json
import pytest
import yaml
from pathlib import Path

from locpipe.config import load_project, ProjectConfig, PROFILES
from locpipe.models import Entry
from locpipe.auto_suggest import analyze_project_and_suggest, SuggestionResult
from locpipe.providers.mock import MockProvider


def test_profile_fast_defaults(tmp_path: Path):
    proj_dir = tmp_path / "test_fast"
    proj_dir.mkdir()
    (proj_dir / "project.yaml").write_text(yaml.dump({
        "project": "test_fast",
        "source_lang": "en",
        "target_lang": "hu",
        "format": "generic_kv",
        "profile": "fast",
        "provider": {"name": "antigravity_cli", "model": "gemini-3.8-flash"}
    }), encoding="utf-8")

    cfg = load_project(proj_dir)
    assert cfg.profile == "fast"
    assert cfg.review_threshold == 0.65
    assert cfg.review_chunk_size == 50
    assert cfg.fidelity_sample_rate == 0.0
    assert cfg.escalation_sample_rate == 0.0
    assert cfg.provider.review_effort == "low"
    assert cfg.provider.escalation_enabled is False


def test_profile_balanced_defaults(tmp_path: Path):
    proj_dir = tmp_path / "test_balanced"
    proj_dir.mkdir()
    (proj_dir / "project.yaml").write_text(yaml.dump({
        "project": "test_balanced",
        "source_lang": "en",
        "target_lang": "hu",
        "format": "generic_kv",
        "profile": "balanced",
        "provider": {"name": "antigravity_cli", "model": "gemini-3.8-flash"}
    }), encoding="utf-8")

    cfg = load_project(proj_dir)
    assert cfg.profile == "balanced"
    assert cfg.review_threshold == 0.70
    assert cfg.review_chunk_size == 30
    assert cfg.provider.review_effort == "low"
    assert cfg.provider.escalation_enabled is False


def test_profile_thorough_or_default_omitted(tmp_path: Path):
    proj_dir = tmp_path / "test_default"
    proj_dir.mkdir()
    (proj_dir / "project.yaml").write_text(yaml.dump({
        "project": "test_default",
        "source_lang": "en",
        "target_lang": "hu",
        "format": "generic_kv",
        "provider": {"name": "antigravity_cli", "model": "gemini-3.8-flash"}
    }), encoding="utf-8")

    cfg = load_project(proj_dir)
    assert cfg.profile == "thorough"
    assert cfg.review_threshold == 0.75
    assert cfg.provider.review_effort == "high"
    assert cfg.provider.escalation_enabled is True


def test_profile_explicit_overrides_win(tmp_path: Path):
    proj_dir = tmp_path / "test_overrides"
    proj_dir.mkdir()
    (proj_dir / "project.yaml").write_text(yaml.dump({
        "project": "test_overrides",
        "source_lang": "en",
        "target_lang": "hu",
        "format": "generic_kv",
        "profile": "fast",
        "confidence": {
            "review_threshold": 0.58,
            "review_chunk_size": 42,
        },
        "provider": {
            "name": "antigravity_cli",
            "model": "gemini-3.8-flash",
            "review_effort": "high",
            "escalation_enabled": True,
        }
    }), encoding="utf-8")

    cfg = load_project(proj_dir)
    assert cfg.profile == "fast"
    assert cfg.review_threshold == 0.58
    assert cfg.review_chunk_size == 42
    assert cfg.provider.review_effort == "high"
    assert cfg.provider.escalation_enabled is True


@pytest.mark.anyio
async def test_auto_suggest_detects_speakers_and_profile(tmp_path: Path):
    proj_dir = tmp_path / "test_detect"
    proj_dir.mkdir()
    batches_dir = proj_dir / "batches"
    batches_dir.mkdir()
    (batches_dir / "batch_01.json").write_text(
        json.dumps([
            {"id": "k1", "source": "Hello world"},
            {"id": "k2", "source": "Goodbye"},
            {"id": "k3", "source": "Take this item"}
        ]),
        encoding="utf-8"
    )
    (proj_dir / "project.yaml").write_text(yaml.dump({
        "project": "test_detect",
        "source_lang": "en",
        "target_lang": "hu",
        "format": "generic_kv",
        "batches": {"glob": "batches/*.json"},
        "provider": {"name": "antigravity_cli", "model": "gemini-3.8-flash"}
    }), encoding="utf-8")

    cfg = load_project(proj_dir)
    mock_prov = MockProvider()

    sugg = await analyze_project_and_suggest(cfg, mock_prov)
    assert sugg.recommended_profile == "fast"
    # No speaker fields or character keys in batch_01.json -> has_detected_speakers is False
    assert sugg.has_detected_speakers is False
