from __future__ import annotations

import json
from pathlib import Path
import pytest

from locpipe.config import load_project
from locpipe.pipeline import plan, run
from locpipe.providers.base import TranslationProvider
from locpipe.checkpoint import Checkpoint


class _RecordingMockProvider(TranslationProvider):
    def __init__(self, fail_for_source: str | None = None):
        self.calls: list[str] = []
        self.translate_calls: list[str] = []
        self.review_calls: list[str] = []
        self.fail_for_source = fail_for_source

    async def complete(self, system_prompt: str, user_payload: str, *, max_tokens: int = 8192) -> str:
        self.calls.append(user_payload)
        parsed = json.loads(user_payload)
        if isinstance(parsed, dict) and "items" in parsed:
            self.review_calls.append(user_payload)
            out = []
            for it in parsed["items"]:
                out.append({
                    "key": it["key"],
                    "translation": it.get("current_translation") or "HU: text",
                    "flag_for_human": False,
                })
            return json.dumps(out, ensure_ascii=False)

        self.translate_calls.append(user_payload)
        if self.fail_for_source and self.fail_for_source in user_payload:
            raise RuntimeError(f"Simulated failure for source: {self.fail_for_source}")

        out = []
        for item in parsed:
            src = item["source"]
            item_id = item["id"]
            out.append({"id": item_id, "translation": f"HU: {src}"})
        return json.dumps(out, ensure_ascii=False)


def _setup_multi_file_project(tmp_path: Path, window_size: int = 8, batch_size: int = 50) -> Path:
    proj_dir = tmp_path / "pooling_project"
    (proj_dir / "batches").mkdir(parents=True)
    (proj_dir / "resources").mkdir(parents=True)
    (proj_dir / "tm").mkdir(parents=True)

    (proj_dir / "project.yaml").write_text(
        f"""project: pooling_project
source_lang: en
target_lang: hu
format: generic_kv

translate_file_window: {window_size}
cross_file_pooling: true

batches:
  glob: "batches/*.json"

resources:
  glossary: resources/glossary.md

categories:
  - name: dialogue
    match_speaker_present: true
    needs_character_voice: false
    batch_size: {batch_size}
  - name: ui
    default: true
    needs_character_voice: false
    batch_size: {batch_size}

fidelity_sample_rate: 0.0

confidence:
  review_threshold: 0.0

provider:
  name: antigravity_cli
  mode: sync
""",
        encoding="utf-8",
    )
    (proj_dir / "resources" / "glossary.md").write_text("# Glossary\n", encoding="utf-8")
    return proj_dir


def test_cross_file_batch_pooling_collapses_micro_files(tmp_path: Path) -> None:
    proj_dir = _setup_multi_file_project(tmp_path, window_size=8, batch_size=50)

    # Create 4 small files with 2 UI lines each (total 8 lines)
    for i in range(1, 5):
        data = [
            {"id": f"key_{i}_1", "source": f"Button {i}-A", "target": ""},
            {"id": f"key_{i}_2", "source": f"Button {i}-B", "target": ""},
        ]
        (proj_dir / "batches" / f"file_{i}.json").write_text(json.dumps(data), encoding="utf-8")

    cfg = load_project(proj_dir)

    # 1. Plan should predict 1 LLM call, not 4
    plan_res = plan(cfg)
    assert plan_res["total_entries"] == 8
    assert plan_res["unique_strings_needing_translation"] == 8
    assert plan_res["llm_calls_needed"] == 1, f"Expected 1 pooled batch, got {plan_res['llm_calls_needed']}"

    # 2. Run should execute exactly 1 LLM call
    prov = _RecordingMockProvider()
    stats = run(cfg, prov)
    assert stats.llm_calls_made == 1, f"Expected 1 LLM call, got {stats.llm_calls_made}"
    assert stats.unique_strings_sent_to_llm == 8
    assert len(prov.calls) == 1

    # Verify that all 4 files were updated with translations
    cp = Checkpoint(proj_dir / "checkpoint.json")
    for i in range(1, 5):
        file_path = proj_dir / "batches" / f"file_{i}.json"
        assert cp.is_file_done(str(file_path))
        loaded = json.loads(file_path.read_text(encoding="utf-8"))
        entries = {item["id"]: item["target"] for item in loaded}
        assert entries[f"key_{i}_1"] == f"HU: Button {i}-A"
        assert entries[f"key_{i}_2"] == f"HU: Button {i}-B"


def test_cross_file_deduplication_within_window(tmp_path: Path) -> None:
    proj_dir = _setup_multi_file_project(tmp_path, window_size=8, batch_size=50)

    # file 1 and file 2 have identical strings "Save" and "Cancel"
    (proj_dir / "batches" / "file_1.json").write_text(
        json.dumps([
            {"id": "btn_save", "source": "Save", "target": ""},
            {"id": "btn_cancel", "source": "Cancel", "target": ""},
        ]),
        encoding="utf-8",
    )
    (proj_dir / "batches" / "file_2.json").write_text(
        json.dumps([
            {"id": "menu_save", "source": "Save", "target": ""},
            {"id": "menu_cancel", "source": "Cancel", "target": ""},
        ]),
        encoding="utf-8",
    )

    cfg = load_project(proj_dir)
    plan_res = plan(cfg)
    assert plan_res["total_entries"] == 4
    # Cross-file dedup should mean only 2 unique strings needed: "Save" and "Cancel"
    assert plan_res["unique_strings_needing_translation"] == 2
    assert plan_res["llm_calls_needed"] == 1

    prov = _RecordingMockProvider()
    stats = run(cfg, prov)
    assert stats.llm_calls_made == 1
    assert stats.unique_strings_sent_to_llm == 2

    # Check payload sent to LLM: only 2 items
    payload = json.loads(prov.calls[0])
    assert len(payload) == 2
    sources = {item["source"] for item in payload}
    assert sources == {"Save", "Cancel"}

    # Both files must have translations written back
    f1 = {it["id"]: it["target"] for it in json.loads((proj_dir / "batches" / "file_1.json").read_text(encoding="utf-8"))}
    f2 = {it["id"]: it["target"] for it in json.loads((proj_dir / "batches" / "file_2.json").read_text(encoding="utf-8"))}
    assert f1["btn_save"] == "HU: Save"
    assert f1["btn_cancel"] == "HU: Cancel"
    assert f2["menu_save"] == "HU: Save"
    assert f2["menu_cancel"] == "HU: Cancel"


def test_window_boundary_respects_translate_file_window(tmp_path: Path) -> None:
    # Set window size to 2
    proj_dir = _setup_multi_file_project(tmp_path, window_size=2, batch_size=50)

    # Create 4 files with 1 unique line each
    for i in range(1, 5):
        (proj_dir / "batches" / f"file_{i}.json").write_text(
            json.dumps([{"id": f"k_{i}", "source": f"Text {i}", "target": ""}]),
            encoding="utf-8",
        )

    cfg = load_project(proj_dir)
    # Window size 2 means:
    # Window 1: file 1 & 2 -> 1 batch
    # Window 2: file 3 & 4 -> 1 batch
    # Total: 2 batches (not 4)
    plan_res = plan(cfg)
    assert plan_res["llm_calls_needed"] == 2

    prov = _RecordingMockProvider()
    stats = run(cfg, prov)
    assert stats.llm_calls_made == 2
    assert len(prov.translate_calls) == 2


def test_cross_file_failure_isolation_and_resume(tmp_path: Path) -> None:
    proj_dir = _setup_multi_file_project(tmp_path, window_size=8, batch_size=50)

    # File 1: UI strings only
    (proj_dir / "batches" / "file_ui.json").write_text(
        json.dumps([{"id": "ui_1", "source": "Inventory", "target": ""}]),
        encoding="utf-8",
    )
    # File 2: Dialogue string with speaker
    (proj_dir / "batches" / "file_dlg.json").write_text(
        json.dumps([{"id": "dlg_1", "source": "Greetings hero!", "target": "", "speaker": "Hero"}]),
        encoding="utf-8",
    )

    cfg = load_project(proj_dir)

    # First run fails on dialogue string
    failing_prov = _RecordingMockProvider(fail_for_source="Greetings hero!")
    run(cfg, failing_prov)

    cp = Checkpoint(proj_dir / "checkpoint.json")
    # File 1 (UI) should have succeeded and been marked done
    assert cp.is_file_done(str(proj_dir / "batches" / "file_ui.json"))
    # File 2 (Dialogue) should NOT be done
    assert not cp.is_file_done(str(proj_dir / "batches" / "file_dlg.json"))

    # Second run with working provider resumes and only translates File 2
    working_prov = _RecordingMockProvider()
    stats2 = run(cfg, working_prov)

    cp2 = Checkpoint(proj_dir / "checkpoint.json")
    assert cp2.is_file_done(str(proj_dir / "batches" / "file_ui.json"))
    assert cp2.is_file_done(str(proj_dir / "batches" / "file_dlg.json"))
    # Only 1 LLM call made on resume (for file_dlg.json)
    assert stats2.llm_calls_made == 1

