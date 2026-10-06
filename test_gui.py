"""
Unit and Interaction Test Suite for GameStringer Desktop GUI (Editorial Restyle).
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from unittest.mock import patch, MagicMock

import pytest
import yaml

from gamestringer.desktop_gui.app import create_app, GameStringerApp, SETTINGS_FILE
from gamestringer.desktop_gui.glyph_strip import GlyphStrip
from gamestringer.desktop_gui.theme import (
    BG_BASE, BG_SURFACE, BG_INSET, FG_TEXT, FG_MUTED,
    ACCENT_INK, ACCENT_MOSS, ACCENT_PAPRIKA, ACCENT_AMBER
)
from gamestringer.desktop_gui.tooltip import ToolTip, create_tooltip
from gamestringer.desktop_gui.widgets import labeled_entry, labeled_combo, labeled_checkbutton
from gamestringer.desktop_gui.tabs.projects_tab import ProjectsTab, CategoryEditDialog
from gamestringer.desktop_gui.tabs.preflight_tab import PreflightTab
from gamestringer.desktop_gui.tabs.audit_tab import AuditTab
from gamestringer.desktop_gui.tabs.run_tab import RunTab


@pytest.fixture
def tk_root():
    try:
        root = tk.Tk()
        root.withdraw()
    except tk.TclError as e:
        pytest.skip(f"Tkinter unavailable (headless CI without X server): {e}")
    yield root
    try:
        root.destroy()
    except Exception:
        pass


def test_tooltip_creation_and_events(tk_root):
    btn = tk.Button(tk_root, text="Test")
    tip = create_tooltip(btn, "Helpful tooltip text", delay_ms=10)
    assert tip.text == "Helpful tooltip text"

    # Simulate enter and show
    tip._show_tip()
    assert tip.tip_window is not None
    assert tip.tip_window.winfo_exists()

    # Simulate hide
    tip._hide_tip()
    assert tip.tip_window is None


def test_glyph_strip_widget(tk_root):
    strip = GlyphStrip(tk_root)
    assert strip.lbl_glyphs.cget("text") == "ő  ű  Ő  Ű"
    assert strip.lbl_glyphs.cget("fg") == FG_MUTED

    # Supported result
    strip.update_result("supported", "Arial")
    assert strip.lbl_glyphs.cget("fg") == ACCENT_MOSS
    assert "Supported" in strip.lbl_status.cget("text")

    # Missing / Unsupported result
    strip.update_result("unsupported", None)
    assert strip.lbl_glyphs.cget("fg") == ACCENT_PAPRIKA
    assert "Missing" in strip.lbl_status.cget("text")


def test_projects_tab_json_validation_blocks_bad_save(tk_root, tmp_path):
    proj_dir = tmp_path / "locpipe" / "projects" / "test_game"
    proj_dir.mkdir(parents=True)
    (proj_dir / "project.yaml").write_text(yaml.dump({
        "project": "test_game",
        "source_lang": "en",
        "target_lang": "hu",
        "format": "uabea_json",
        "format_options": {"character_replacements": {"a": "b"}}
    }), encoding="utf-8")

    with patch("gamestringer.desktop_gui.tabs.projects_tab.get_default_projects_dir", return_value=tmp_path / "locpipe" / "projects"):
        tab = ProjectsTab(tk.Frame(tk_root), tk_root)
        tab._load_project_by_name("test_game")

        # Set invalid JSON
        tab.var_char_replacements.set("{bad_json: invalid")

        with patch("tkinter.messagebox.showerror") as mock_err:
            success = tab.save_project()
            assert success is False
            assert mock_err.called
            assert tab.var_char_replacements.get() == "{bad_json: invalid"


def test_projects_tab_dirty_tracking(tk_root, tmp_path):
    proj_dir = tmp_path / "locpipe" / "projects" / "test_game"
    proj_dir.mkdir(parents=True)
    (proj_dir / "project.yaml").write_text(yaml.dump({
        "project": "test_game",
        "source_lang": "en",
        "target_lang": "hu",
        "format": "uabea_json"
    }), encoding="utf-8")

    with patch("gamestringer.desktop_gui.tabs.projects_tab.get_default_projects_dir", return_value=tmp_path / "locpipe" / "projects"):
        tab = ProjectsTab(tk.Frame(tk_root), tk_root)
        tab._load_project_by_name("test_game")
        assert tab.is_dirty is False

        # Modify a field
        tab.var_target_lang.set("de")
        assert tab.is_dirty is True


def test_preflight_tab_engine_dropdown_options(tk_root):
    tab = PreflightTab(tk.Frame(tk_root), tk_root)
    assert tab.var_font_engine.get() in ["unity", "il2cpp"]


def test_sidebar_navigation_and_switching(tk_root, tmp_path):
    p1 = tmp_path / "locpipe" / "projects" / "proj_alpha"
    p2 = tmp_path / "locpipe" / "projects" / "proj_beta"
    p1.mkdir(parents=True)
    p2.mkdir(parents=True)
    (p1 / "project.yaml").write_text("project: proj_alpha\nsource_lang: en\ntarget_lang: hu\nformat: uabea_json\n", encoding="utf-8")
    (p2 / "project.yaml").write_text("project: proj_beta\nsource_lang: en\ntarget_lang: hu\nformat: uabea_json\n", encoding="utf-8")

    with patch("gamestringer.desktop_gui.tabs.projects_tab.get_default_projects_dir", return_value=tmp_path / "locpipe" / "projects"), \
         patch("gamestringer.desktop_gui.tabs.audit_tab.get_default_projects_dir", return_value=tmp_path / "locpipe" / "projects"), \
         patch("gamestringer.desktop_gui.tabs.run_tab.get_default_projects_dir", return_value=tmp_path / "locpipe" / "projects"):

        app = GameStringerApp(tk_root)
        assert len(app.nav_items) == 4
        assert app.current_tab_index == 0

        # Switch to Audit tab (index 2)
        app.switch_tab(2)
        assert app.current_tab_index == 2
        assert app.nav_items[2].is_active is True
        assert app.nav_items[0].is_active is False

        # Switch project via ProjectsTab
        app.tab_projects.select_project("proj_beta")
        assert app.shared_project_var.get() == "proj_beta"
        assert app.tab_audit.var_selected_project.get() == "proj_beta"
        assert app.tab_run.var_selected_project.get() == "proj_beta"
        assert "proj_beta" in app.lbl_active_proj.cget("text")


def test_run_tab_confirmation_dialog(tk_root, tmp_path):
    p1 = tmp_path / "locpipe" / "projects" / "proj_run_test"
    p1.mkdir(parents=True)
    (p1 / "project.yaml").write_text("project: proj_run_test\nsource_lang: en\ntarget_lang: hu\nformat: uabea_json\n", encoding="utf-8")

    with patch("gamestringer.desktop_gui.tabs.run_tab.get_default_projects_dir", return_value=tmp_path / "locpipe" / "projects"):
        tab = RunTab(tk.Frame(tk_root), tk_root)
        tab.select_project("proj_run_test")
        assert tab.var_max_api_calls.get() == "500"

        # When user clicks Cancel on confirmation, no process is started
        with patch("tkinter.messagebox.askyesno", return_value=False) as mock_confirm:
            tab._start_translation()
            assert mock_confirm.called
            msg = mock_confirm.call_args[0][1]
            assert "500 calls max" in msg
            assert tab.is_running is False
            assert tab.active_process is None


def test_run_tab_max_api_calls_unlimited_and_command(tk_root, tmp_path):
    p1 = tmp_path / "locpipe" / "projects" / "proj_run_test"
    p1.mkdir(parents=True)
    (p1 / "project.yaml").write_text("project: proj_run_test\nsource_lang: en\ntarget_lang: hu\nformat: uabea_json\n", encoding="utf-8")

    with patch("gamestringer.desktop_gui.tabs.run_tab.get_default_projects_dir", return_value=tmp_path / "locpipe" / "projects"):
        tab = RunTab(tk.Frame(tk_root), tk_root)
        tab.select_project("proj_run_test")

        # Test default passes --max-api-calls 500
        with patch("tkinter.messagebox.askyesno", return_value=True), \
             patch("threading.Thread", side_effect=lambda target, **kwargs: MagicMock(start=target)), \
             patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.stdout.readline.return_value = ""
            mock_proc.poll.return_value = 0
            mock_proc.returncode = 0
            mock_popen.return_value = mock_proc

            tab._start_translation()
            cmd_args = mock_popen.call_args[0][0]
            assert "--max-api-calls" in cmd_args
            idx = cmd_args.index("--max-api-calls")
            assert cmd_args[idx + 1] == "500"

            # Reset tab state
            tab.is_running = False

        # Test blank / unlimited
        tab.var_max_api_calls.set("")
        with patch("tkinter.messagebox.askyesno", return_value=True) as mock_confirm, \
             patch("threading.Thread", side_effect=lambda target, **kwargs: MagicMock(start=target)), \
             patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.stdout.readline.return_value = ""
            mock_proc.poll.return_value = 0
            mock_proc.returncode = 0
            mock_popen.return_value = mock_proc

            tab._start_translation()
            msg = mock_confirm.call_args[0][1]
            assert "UNLIMITED" in msg
            cmd_args = mock_popen.call_args[0][0]
            assert "--max-api-calls" not in cmd_args


def test_settings_persistence(tk_root, tmp_path):
    test_settings = tmp_path / "gamestringer_gui_settings.json"
    with patch("gamestringer.desktop_gui.app.SETTINGS_FILE", test_settings):
        app = GameStringerApp(tk_root)
        app.shared_project_var.set("saved_project_xyz")
        app.current_tab_index = 2
        app._save_settings()

        assert test_settings.exists()
        saved = json.loads(test_settings.read_text(encoding="utf-8"))
        assert saved["last_project"] == "saved_project_xyz"
        assert saved["last_tab"] == 2


def test_projects_tab_font_fallback_checkbox(tk_root, tmp_path):
    p1 = tmp_path / "locpipe" / "projects" / "proj_fb_test"
    p1.mkdir(parents=True)
    (p1 / "project.yaml").write_text("project: proj_fb_test\nsource_lang: en\ntarget_lang: hu\nformat: uabea_json\n", encoding="utf-8")

    with patch("gamestringer.desktop_gui.tabs.projects_tab.get_default_projects_dir", return_value=tmp_path / "locpipe" / "projects"):
        tab = ProjectsTab(tk.Frame(tk_root), tk_root)
        tab.select_project("proj_fb_test")

        # Initially empty/unchecked
        assert tab.var_char_replacements.get() == "{}"
        assert tab.var_char_fallback_toggle.get() is False

        # Check box -> fills default 4-key mapping
        tab.var_char_fallback_toggle.set(True)
        tab._on_char_fallback_toggled()
        parsed = json.loads(tab.var_char_replacements.get())
        assert parsed == {"ő": "ô", "ű": "û", "Ő": "Ô", "Ű": "Û"}

        # Uncheck box -> resets back to {}
        tab.var_char_fallback_toggle.set(False)
        tab._on_char_fallback_toggled()
        assert tab.var_char_replacements.get() == "{}"

        # Custom mapping -> checking does not overwrite
        custom = json.dumps({"ő": "o", "a": "b"}, ensure_ascii=False)
        tab.var_char_replacements.set(custom)
        tab.var_char_fallback_toggle.set(True)
        tab._on_char_fallback_toggled()
        assert tab.var_char_replacements.get() == custom


def test_projects_tab_provider_model_effort_config(tk_root, tmp_path):
    p1 = tmp_path / "locpipe" / "projects" / "proj_prov_test"
    p1.mkdir(parents=True)
    initial_yaml = (
        "project: proj_prov_test\n"
        "source_lang: en\n"
        "target_lang: hu\n"
        "format: uabea_json\n"
        "provider:\n"
        "  name: antigravity_cli\n"
        "  model: gemini-3.7-flash\n"
        "  effort: low\n"
        "  review_model: gemini-3.1-pro\n"
        "  review_effort: high\n"
    )
    (p1 / "project.yaml").write_text(initial_yaml, encoding="utf-8")

    with patch("gamestringer.desktop_gui.tabs.projects_tab.get_default_projects_dir", return_value=tmp_path / "locpipe" / "projects"):
        tab = ProjectsTab(tk.Frame(tk_root), tk_root)
        tab.select_project("proj_prov_test")

        # Verify loaded values
        assert tab.var_prov_model.get() == "gemini-3.7-flash"
        assert tab.var_prov_effort.get() == "low"
        assert tab.var_prov_review_model.get() == "gemini-3.1-pro"
        assert tab.var_prov_review_effort.get() == "high"
        assert tab.var_prov_escalation_model.get() == ""
        assert tab.var_prov_escalation_effort.get() == ""

        # Set escalation fields and save
        tab.var_prov_escalation_model.set("gemini-3.1-pro")
        tab.var_prov_escalation_effort.set("high")
        res = tab.save_project()
        assert res is True

        saved_data = yaml.safe_load((p1 / "project.yaml").read_text(encoding="utf-8"))
        assert saved_data["provider"]["escalation_model"] == "gemini-3.1-pro"
        assert saved_data["provider"]["escalation_effort"] == "high"

        # Clear escalation fields and save -> keys should be omitted
        tab.var_prov_escalation_model.set("")
        tab.var_prov_escalation_effort.set("")
        res = tab.save_project()
        assert res is True

        saved_data2 = yaml.safe_load((p1 / "project.yaml").read_text(encoding="utf-8"))
        assert "escalation_model" not in saved_data2["provider"]
        assert "escalation_effort" not in saved_data2["provider"]


def test_audit_tab_explainer_rendered(tk_root, tmp_path):
    p1 = tmp_path / "locpipe" / "projects" / "proj_audit_test"
    p1.mkdir(parents=True)
    (p1 / "project.yaml").write_text("project: proj_audit_test\nsource_lang: en\ntarget_lang: hu\nformat: uabea_json\n", encoding="utf-8")

    with patch("gamestringer.desktop_gui.tabs.audit_tab.get_default_projects_dir", return_value=tmp_path / "locpipe" / "projects"):
        tab = AuditTab(tk.Frame(tk_root), tk_root)
        
        def collect_texts(widget):
            texts = []
            if isinstance(widget, (tk.Label, ttk.Label)):
                texts.append(widget.cget("text"))
            for child in widget.winfo_children():
                texts.extend(collect_texts(child))
            return texts

        combined = " ".join(collect_texts(tab))
        assert "Scans project batch files with ZERO LLM calls/cost" in combined
        assert "[kept]" in combined
        assert "[noise:*]" in combined


def test_run_tab_plan_completion_with_dict(tk_root, tmp_path):
    from gamestringer.desktop_gui.tabs.run_tab import RunTab
    from locpipe.config import ProjectConfig, ProviderConfig

    p1 = tmp_path / "locpipe" / "projects" / "proj_plan_test"
    p1.mkdir(parents=True)
    (p1 / "project.yaml").write_text("project: proj_plan_test\nsource_lang: en\ntarget_lang: hu\nformat: uabea_json\n", encoding="utf-8")

    with patch("gamestringer.desktop_gui.tabs.run_tab.get_default_projects_dir", return_value=tmp_path / "locpipe" / "projects"):
        tab = RunTab(tk.Frame(tk_root), tk_root)
        tab.select_project("proj_plan_test")

        config = ProjectConfig(
            project="proj_plan_test",
            source_lang="en",
            target_lang="hu",
            format="uabea_json",
            root=p1,
            batch_glob="batches/*.json",
            resources={},
            categories=[],
            provider=ProviderConfig(),
            tm_db_path=p1 / "tm.sqlite3",
        )

        plan_dict = {
            "total_entries": 100,
            "already_translated": 10,
            "tm_hits": 20,
            "unique_strings_needing_translation": 50,
            "llm_calls_needed": 3,
            "calls_by_category": {"dialogue": 2, "ui": 1},
            "estimated_uncached_input_tokens": 1200,
            "estimated_cache_read_tokens": 800,
            "estimated_output_tokens": 600,
            "estimated_realistic_input_tokens": 2600,
            "caching_note": "Antigravity CLI note test",
            "pending_files_count": 2,
        }

        # Invoking _on_plan_complete with a dict must NOT raise AttributeError
        tab._on_plan_complete(config, plan_dict, None)

        log_content = tab.txt_log.get("1.0", tk.END)
        assert "Raw translatable entries:  100" in log_content
        assert "Unique strings:            50 (50.0% deduplication)" in log_content
        assert "Total Batches:             3" in log_content
        assert "Estimated Total Input Tokens (no caching — Antigravity CLI): ~2,600" in log_content
        assert "Antigravity CLI note test" in log_content
        assert tab.has_run_plan_in_session is True


def test_projects_tab_target_register(tk_root, tmp_path):
    p1 = tmp_path / "locpipe" / "projects" / "proj_reg_test"
    p1.mkdir(parents=True)
    (p1 / "project.yaml").write_text("project: proj_reg_test\nsource_lang: en\ntarget_lang: hu\ntarget_register: formal\nformat: uabea_json\n", encoding="utf-8")

    with patch("gamestringer.desktop_gui.tabs.projects_tab.get_default_projects_dir", return_value=tmp_path / "locpipe" / "projects"):
        tab = ProjectsTab(tk.Frame(tk_root), tk_root)
        tab.select_project("proj_reg_test")

        assert tab.var_target_register.get() == "formal"

        # Change to informal and save
        tab.var_target_register.set("informal")
        assert tab.save_project() is True

        saved_data = yaml.safe_load((p1 / "project.yaml").read_text(encoding="utf-8"))
        assert saved_data["target_register"] == "informal"


def test_audit_tab_force_retranslate(tk_root, tmp_path):
    from gamestringer.desktop_gui.tabs.audit_tab import AuditTab
    from locpipe.tm import TranslationMemory
    from locpipe.models import TMRecord
    from locpipe.normalize import content_hash, normalize_source

    p1 = tmp_path / "locpipe" / "projects" / "proj_audit_force_test"
    p1.mkdir(parents=True)
    (p1 / "project.yaml").write_text("project: proj_audit_force_test\nsource_lang: en\ntarget_lang: hu\nformat: uabea_json\n", encoding="utf-8")

    # Add a TM entry
    tm = TranslationMemory(p1 / "tm" / "translation_memory.sqlite3")
    src = "New Game Options"
    h = content_hash(normalize_source(src))
    rec = TMRecord(f"k_{h}", src, "Uj jatek beallitasok", "en", "hu", "ui", None, 1.0, "mt")
    tm.upsert(h, rec)
    assert tm.get(f"k_{h}") is not None
    tm.close()

    with patch("gamestringer.desktop_gui.tabs.audit_tab.get_default_projects_dir", return_value=tmp_path / "locpipe" / "projects"):
        tab = AuditTab(tk.Frame(tk_root), tk_root)
        tab.select_project("proj_audit_force_test")

        # Mock audit record and tree row
        tab.audit_records = [{"file": "file1", "path": "path.to.text", "action": "kept", "value": src}]
        tab._render_records()

        # Select the row in the treeview
        children = tab.tree.get_children()
        assert len(children) == 1
        tab.tree.selection_set(children[0])

        with patch("tkinter.messagebox.askyesno", return_value=True), \
             patch("tkinter.messagebox.showinfo"):
            tab.force_retranslate_selected()

        # TM entry should now be deleted/invalidated
        tm2 = TranslationMemory(p1 / "tm" / "translation_memory.sqlite3")
        assert tm2.get(f"k_{h}") is None
        tm2.close()


def test_projects_tab_lang_style_presets(tk_root, tmp_path):
    from locpipe.presets import LANG_STYLE_PRESETS

    p1 = tmp_path / "locpipe" / "projects" / "proj_preset_test"
    p1.mkdir(parents=True)
    res_dir = p1 / "resources"
    res_dir.mkdir()
    (p1 / "project.yaml").write_text("project: proj_preset_test\nsource_lang: en\ntarget_lang: hu\nformat: uabea_json\n", encoding="utf-8")

    with patch("gamestringer.desktop_gui.tabs.projects_tab.get_default_projects_dir", return_value=tmp_path / "locpipe" / "projects"):
        tab = ProjectsTab(tk.Frame(tk_root), tk_root)
        tab.select_project("proj_preset_test")

        # Select Modern preset
        preset_name = "Modern, laza (kortárs akció/kaland)"
        tab.var_lang_style_preset.set(preset_name)
        tab._on_lang_style_preset_selected()

        ls_file = res_dir / "lang-style.md"
        assert ls_file.exists()
        assert ls_file.read_text(encoding="utf-8") == LANG_STYLE_PRESETS[preset_name]

        # Select Fantasy preset without confirmation (reject overwrite)
        with patch("tkinter.messagebox.askyesno", return_value=False):
            tab.var_lang_style_preset.set("Fantasy/archaikus (RPG, epikus fantasy)")
            tab._on_lang_style_preset_selected()
            # File should still have Modern preset
            assert ls_file.read_text(encoding="utf-8") == LANG_STYLE_PRESETS[preset_name]
            assert tab.var_lang_style_preset.get() == preset_name

        # Select Fantasy preset with confirmation (accept overwrite)
        with patch("tkinter.messagebox.askyesno", return_value=True):
            tab.var_lang_style_preset.set("Fantasy/archaikus (RPG, epikus fantasy)")
            tab._on_lang_style_preset_selected()
            assert ls_file.read_text(encoding="utf-8") == LANG_STYLE_PRESETS["Fantasy/archaikus (RPG, epikus fantasy)"]


def test_projects_tab_batch_cap_and_naturalness(tk_root, tmp_path):
    p1 = tmp_path / "locpipe" / "projects" / "proj_cap_test"
    p1.mkdir(parents=True)
    initial_yaml = (
        "project: proj_cap_test\n"
        "source_lang: en\n"
        "target_lang: hu\n"
        "format: generic_kv\n"
        "provider:\n"
        "  name: antigravity_cli\n"
        "  model: gemini-3.8-flash\n"
        "  batch_output_token_cap: 14384\n"
        "confidence:\n"
        "  gate_naturalness: true\n"
    )
    (p1 / "project.yaml").write_text(initial_yaml, encoding="utf-8")

    with patch("gamestringer.desktop_gui.tabs.projects_tab.get_default_projects_dir", return_value=tmp_path / "locpipe" / "projects"):
        tab = ProjectsTab(tk.Frame(tk_root), tk_root)
        tab.select_project("proj_cap_test")

        # Verify loaded values
        assert tab.var_prov_batch_cap.get() == "14384"
        assert tab.var_gate_naturalness.get() is True

        # Modify values and save
        tab.var_prov_batch_cap.set("8000")
        tab.var_gate_naturalness.set(False)
        assert tab.save_project() is True

        # Check saved YAML
        saved_data = yaml.safe_load((p1 / "project.yaml").read_text(encoding="utf-8"))
        assert saved_data["provider"]["batch_output_token_cap"] == 8000
        assert "gate_naturalness" not in saved_data.get("confidence", {})

        # Clear batch cap and save
        tab.var_prov_batch_cap.set("")
        assert tab.save_project() is True
        saved_data2 = yaml.safe_load((p1 / "project.yaml").read_text(encoding="utf-8"))
        assert "batch_output_token_cap" not in saved_data2.get("provider", {})




