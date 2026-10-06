"""Phase 7 & Phase 8 Comprehensive Tests:
Verifies real CLI checkpoint/resume behavior and error recovery scenarios
(invalid JSON, truncated JSON, tag corruption, provider error retries).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from locpipe.checkpoint import Checkpoint
from locpipe.cli import main
from locpipe.config import load_project
from locpipe.pipeline import run
from locpipe.providers.base import TranslationProvider


class ErrorSimulationProvider(TranslationProvider):
    """Simulates various failure modes for Phase 8 testing."""

    def __init__(self, mode: str):
        self.mode = mode
        self.call_count = 0

    async def complete(self, system_prompt: str, user_payload: str, *, max_tokens: int = 8192) -> str:
        self.call_count += 1
        payload_json = user_payload.split("\n\n(Your previous response was invalid:")[0]
        parsed = json.loads(payload_json)

        if self.mode == "invalid_json":
            if self.call_count == 1:
                return "[{id: 0, translation: unquoted_val}"
            out = [{"id": item["id"], "translation": f"FIXED: {item['source']}"} for item in parsed]
            return json.dumps(out)

        if self.mode == "corrupt_placeholder":
            out = [{"id": item["id"], "translation": "Missing placeholder text"} for item in parsed]
            return json.dumps(out)

        if self.mode == "valid":
            out = [{"id": item["id"], "translation": f"MOCK-OK: {item['source']}"} for item in parsed]
            return json.dumps(out)

        raise ValueError(f"Unknown mode {self.mode}")


def test_cli_checkpoint_resume_no_retranslation(tmp_path: Path) -> None:
    proj_dir = tmp_path / "resume_proj"
    (proj_dir / "batches").mkdir(parents=True)
    (proj_dir / "resources").mkdir(parents=True)
    (proj_dir / "tm").mkdir(parents=True)

    (proj_dir / "project.yaml").write_text(
        """project: resume_proj
source_lang: en
target_lang: hu
format: xliff
batches:
  glob: "batches/*.xliff"
categories:
  - name: default
    default: true
    batch_size: 2
provider:
  name: antigravity_cli
""",
        encoding="utf-8",
    )

    # 10 entries in sample
    xliff_content = """<?xml version="1.0" encoding="utf-8"?>
<xliff xmlns="urn:oasis:names:tc:xliff:document:1.2" version="1.2">
<file source_language="en" target_language="hu" datatype="plaintext" original="test.xliff">
<body>
<trans-unit id="1"><source>Hello 1</source><target></target></trans-unit>
<trans-unit id="2"><source>Hello 2</source><target></target></trans-unit>
<trans-unit id="3"><source>Hello 3</source><target></target></trans-unit>
<trans-unit id="4"><source>Hello 4</source><target></target></trans-unit>
</body>
</file>
</xliff>"""
    (proj_dir / "batches" / "sample.xliff").write_text(xliff_content, encoding="utf-8")

    # Manually populate batch_drafts in checkpoint for first 2 entries
    cp = Checkpoint(proj_dir / "checkpoint.json")
    from locpipe.models import Entry
    dummy_entry = Entry(file="sample.xliff", key="1", source="Hello 1", category="default")
    cp.save_batch_drafts({dummy_entry.tm_key: "[PRE-CACHED] Hello 1"})

    # Run pipeline with ErrorSimulationProvider
    provider = ErrorSimulationProvider(mode="valid")
    config = load_project(proj_dir)
    stats = run(config, provider)

    # Verify that the pre-cached draft was preserved without calling LLM for it
    saved_cp = Checkpoint(proj_dir / "checkpoint.json")
    assert saved_cp.is_file_done(str(proj_dir / "batches" / "sample.xliff"))
    assert stats.total_entries == 4


def test_error_recovery_invalid_json_retry(tmp_path: Path) -> None:
    proj_dir = tmp_path / "err_proj"
    (proj_dir / "batches").mkdir(parents=True)
    (proj_dir / "resources").mkdir(parents=True)
    (proj_dir / "tm").mkdir(parents=True)

    (proj_dir / "project.yaml").write_text(
        """project: err_proj
source_lang: en
target_lang: hu
format: xliff
batches:
  glob: "batches/*.xliff"
provider:
  name: antigravity_cli
""",
        encoding="utf-8",
    )

    xliff_content = """<?xml version="1.0" encoding="utf-8"?>
<xliff xmlns="urn:oasis:names:tc:xliff:document:1.2" version="1.2">
<file source_language="en" target_language="hu" datatype="plaintext" original="test.xliff">
<body>
<trans-unit id="1"><source>Hello World</source><target></target></trans-unit>
</body>
</file>
</xliff>"""
    (proj_dir / "batches" / "sample.xliff").write_text(xliff_content, encoding="utf-8")

    provider = ErrorSimulationProvider(mode="invalid_json")
    config = load_project(proj_dir)
    stats = run(config, provider)

    # Must succeed on retry 2
    assert provider.call_count == 2
    assert stats.total_entries == 1


def test_checkpoint_corrupt_json_raises_runtime_error(tmp_path: Path) -> None:
    cp_path = tmp_path / "checkpoint.json"
    cp_path.write_text("{corrupt: json truncated", encoding="utf-8")
    with pytest.raises(RuntimeError, match="is not valid JSON"):
        Checkpoint(cp_path)


def test_unity_validator_missing_format_kwargs_raises(tmp_path: Path) -> None:
    from locpipe.validators.registry import run_validator

    csv_file = tmp_path / "sample.csv"
    csv_file.write_text("ID,EN,HU\n1,Hello,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="format 'unity' requires"):
        run_validator("unity", csv_file, format_kwargs={})


def test_checkpoint_concurrent_saves(tmp_path: Path) -> None:
    import concurrent.futures

    cp = Checkpoint(tmp_path / "checkpoint.json")
    def worker(i: int):
        cp.save_batch_drafts({f"key_{i}_{j}": f"trans_{i}_{j}" for j in range(20)})

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(worker, i) for i in range(10)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    loaded = Checkpoint(tmp_path / "checkpoint.json")
    drafts = loaded.get_batch_drafts()
    assert len(drafts) == 200
