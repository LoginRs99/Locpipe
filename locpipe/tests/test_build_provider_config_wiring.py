"""cli._build_provider() previously silently dropped two project.yaml
settings on the floor: config.provider.max_retries was loaded from YAML
but never passed to either provider constructor (both providers just
used their own class-level default of 5 regardless of what the project
set), and there was no way at all to configure a sync-mode per-call
timeout from project.yaml -- config.provider.timeout_s exists, but is
wired ONLY to poll_batch()'s batch-mode job-wait timeout (correctly
defaulting to 24h for that), so a project.yaml author who set
`timeout_s: 60` expecting it to bound a single translate call would
have silently gotten no effect on sync mode at all.

Fixed by threading config.provider.max_retries and the new, distinctly
named config.provider.sync_call_timeout_s through to both provider
constructors. These tests confirm the values actually arrive.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from locpipe.cli import _build_provider, main
from locpipe.config import load_project


def _init_project(tmp_path: Path) -> Path:
    import os

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        main(["init", "test_proj"])
    finally:
        os.chdir(cwd)
    return tmp_path / "projects" / "test_proj"


def test_antigravity_cli_provider_gets_configured_sync_timeout(tmp_path: Path) -> None:
    project_dir = _init_project(tmp_path)
    config = load_project(project_dir)

    with patch("locpipe.providers.antigravity_cli_provider._BINARY", "/fake/agy"):
        default_provider = _build_provider(config, dry_run=False)
        assert default_provider.timeout_s == 300  # ProviderConfig.sync_call_timeout_s default

        config.provider.sync_call_timeout_s = 123
        custom_provider = _build_provider(config, dry_run=False)
        assert custom_provider.timeout_s == 123


def test_build_provider_default_dry_run_and_effort_override(tmp_path: Path) -> None:
    project_dir = _init_project(tmp_path)
    config = load_project(project_dir)

    with patch("locpipe.providers.antigravity_cli_provider._BINARY", "/fake/agy"):
        # Calling with just config must not raise TypeError (dry_run defaults to False)
        provider = _build_provider(config)
        assert provider.effort == "low"

        # Calling with effort_override as keyword arg must succeed
        provider_high = _build_provider(config, effort_override="high")
        assert provider_high.effort == "high"


def test_unsupported_provider_raises_on_config_load(tmp_path: Path) -> None:
    import pytest
    project_dir = _init_project(tmp_path)
    proj_yaml = project_dir / "project.yaml"
    proj_yaml.write_text(proj_yaml.read_text(encoding="utf-8").replace('name: antigravity_cli', 'name: gemini'), encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported provider 'gemini'"):
        load_project(project_dir)
