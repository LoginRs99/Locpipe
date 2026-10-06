"""
Projects Tab — View, create, configure, and manage LocPipe translation projects.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from typing import Any, Callable, Dict, List, Optional
import yaml

from gamestringer.desktop_gui.theme import (
    BG_BASE, BG_SURFACE, BG_INSET, FG_TEXT, FG_MUTED,
    ACCENT_INK, ACCENT_MOSS, ACCENT_PAPRIKA, ACCENT_AMBER,
    FONT_TITLE, FONT_HEADING, FONT_BODY, FONT_MONO, FONT_FAMILY
)
from gamestringer.desktop_gui.tooltip import create_tooltip
from gamestringer.desktop_gui.widgets import (
    section_frame, labeled_entry, labeled_combo, labeled_checkbutton, action_button, progress_bar
)
from locpipe.presets import LANG_STYLE_PRESETS, DEFAULT_LANG_STYLE_HEADER


_DEFAULT_CHAR_FALLBACK = {"ő": "ô", "ű": "û", "Ő": "Ô", "Ű": "Û"}


def open_folder(path: Path):
    if sys.platform == "win32":
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def get_default_projects_dir() -> Path:
    cwd = Path.cwd()
    candidates = [
        cwd / "locpipe" / "projects",
        cwd / "projects",
        Path(__file__).resolve().parent.parent.parent.parent / "locpipe" / "projects",
    ]
    for c in candidates:
        if c.exists():
            return c
    target = cwd / "locpipe" / "projects"
    target.mkdir(parents=True, exist_ok=True)
    return target


class CategoryEditDialog(tk.Toplevel):
    """Modal dialog to edit or create a category classification rule."""

    def __init__(self, parent: tk.Widget, category_data: Optional[Dict[str, Any]] = None):
        super().__init__(parent)
        self.title("Edit Category Rule" if category_data else "New Category Rule")
        self.geometry("520x540")
        self.minsize(480, 480)
        self.configure(bg=BG_BASE)
        self.transient(parent.winfo_toplevel())
        self.grab_set()

        self.result: Optional[Dict[str, Any]] = None
        self.data = category_data or {}

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.center_window()

    def center_window(self):
        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        parent_x = self.master.winfo_rootx()
        parent_y = self.master.winfo_rooty()
        parent_w = self.master.winfo_width()
        parent_h = self.master.winfo_height()
        x = parent_x + max(0, (parent_w - w) // 2)
        y = parent_y + max(0, (parent_h - h) // 2)
        self.geometry(f"+{x}+{y}")

    def _build_ui(self):
        container = ttk.Frame(self, style="TFrame", padding=15)
        container.pack(fill=tk.BOTH, expand=True)

        # Form fields frame
        form = ttk.Labelframe(container, text=" Category Classification Rule ", style="TLabelframe", padding=12)
        form.pack(fill=tk.BOTH, expand=True, pady=(0, 15))

        # Name
        self.var_name = tk.StringVar(value=self.data.get("name", ""))
        r_name, _ = labeled_entry(form, "Category Name:", self.var_name, width=22, label_width=18,
                                  tooltip="Identifier for this category, e.g. dialogue, ui, lore, items")
        r_name.pack(fill=tk.X, pady=4)

        # Batch size
        self.var_batch_size = tk.StringVar(value=str(self.data.get("batch_size", 350)))
        r_bs, _ = labeled_entry(form, "Batch Size:", self.var_batch_size, width=10, label_width=18,
                                tooltip="Number of translatable entries bundled in one LLM call (default: 350 for UI, 200 for Dialogue)")
        r_bs.pack(fill=tk.X, pady=4)

        # Max expansion ratio
        exp_val = self.data.get("max_expansion_ratio")
        self.var_expansion = tk.StringVar(value=str(exp_val) if exp_val is not None else "")
        r_exp, _ = labeled_entry(form, "Max Expansion Ratio:", self.var_expansion, width=10, label_width=18,
                                 tooltip="Confidence cap for target string length vs source (e.g. 1.3 for UI buttons, 1.8 for dialogue). Leave blank for default.")
        r_exp.pack(fill=tk.X, pady=4)

        # Match Key Regex
        self.var_key_regex = tk.StringVar(value=self.data.get("match_key_regex") or "")
        r_key, _ = labeled_entry(form, "Match Key Regex:", self.var_key_regex, width=25, label_width=18,
                                 tooltip="Regex pattern matched against entry.key (e.g. ^UI_BTN_.*)")
        r_key.pack(fill=tk.X, pady=4)

        # Match Notes Regex
        self.var_notes_regex = tk.StringVar(value=self.data.get("match_notes_regex") or "")
        r_notes, _ = labeled_entry(form, "Match Notes Regex:", self.var_notes_regex, width=25, label_width=18,
                                   tooltip="Regex pattern matched against entry metadata notes (e.g. type:dialogue)")
        r_notes.pack(fill=tk.X, pady=4)

        # Match Source Regex
        self.var_source_regex = tk.StringVar(value=self.data.get("match_source_regex") or "")
        r_src, _ = labeled_entry(form, "Match Source Regex:", self.var_source_regex, width=25, label_width=18,
                                 tooltip="Regex matched against source text (e.g. argument-modifier syntax \\|(plural|gender)\\()")
        r_src.pack(fill=tk.X, pady=4)

        # Effort Override
        self.var_effort = tk.StringVar(value=self.data.get("effort") or "")
        r_eff, _ = labeled_entry(form, "Effort Override:", self.var_effort, width=10, label_width=18,
                                 tooltip="Optional per-category effort override for translation ('high' or 'low'). Leave blank for project default.")
        r_eff.pack(fill=tk.X, pady=4)

        # Checkboxes frame
        chk_frame = ttk.Frame(form, style="Card.TFrame")
        chk_frame.pack(fill=tk.X, pady=8)

        self.var_default = tk.BooleanVar(value=bool(self.data.get("default", False)))
        chk_def = labeled_checkbutton(chk_frame, "Default fallback category (catches unmatched entries)", self.var_default,
                                      tooltip="Mark as fallback category if no specific regex or speaker rule matches")
        chk_def.pack(anchor="w", pady=2)

        self.var_speaker = tk.BooleanVar(value=bool(self.data.get("match_speaker_present", False)))
        chk_spk = labeled_checkbutton(chk_frame, "Match when speaker field is present (dialogue lines)", self.var_speaker,
                                      tooltip="Route rows with a non-empty speaker/character name to this category")
        chk_spk.pack(anchor="w", pady=2)

        self.var_voice = tk.BooleanVar(value=bool(self.data.get("needs_character_voice", False)))
        chk_voc = labeled_checkbutton(chk_frame, "Include Character Voice Bible in prompt", self.var_voice,
                                      tooltip="Attach character register and personality guidelines from character-voices.md")
        chk_voc.pack(anchor="w", pady=2)

        # Action buttons
        btn_box = ttk.Frame(container, style="TFrame")
        btn_box.pack(fill=tk.X)

        btn_save = ttk.Button(btn_box, text="💾 Save Category", style="Primary.TButton", command=self._on_save)
        btn_save.pack(side=tk.LEFT, padx=(0, 10))

        btn_cancel = ttk.Button(btn_box, text="Cancel", command=self._on_cancel)
        btn_cancel.pack(side=tk.LEFT)

    def _on_save(self):
        name = self.var_name.get().strip()
        if not name:
            messagebox.showwarning("Validation", "Category name cannot be empty.", parent=self)
            return

        bs_str = self.var_batch_size.get().strip()
        try:
            bs = int(bs_str)
            if bs <= 0:
                raise ValueError
        except ValueError:
            messagebox.showwarning("Validation", "Batch size must be a positive integer.", parent=self)
            return

        res: Dict[str, Any] = {
            "name": name,
            "batch_size": bs,
            "default": self.var_default.get(),
            "match_speaker_present": self.var_speaker.get(),
            "needs_character_voice": self.var_voice.get(),
        }

        exp_str = self.var_expansion.get().strip()
        if exp_str:
            try:
                res["max_expansion_ratio"] = float(exp_str)
            except ValueError:
                messagebox.showwarning("Validation", "Max expansion ratio must be a valid number (e.g. 1.5).", parent=self)
                return
        else:
            res["max_expansion_ratio"] = None

        if self.var_key_regex.get().strip():
            res["match_key_regex"] = self.var_key_regex.get().strip()
        if self.var_notes_regex.get().strip():
            res["match_notes_regex"] = self.var_notes_regex.get().strip()
        if self.var_source_regex.get().strip():
            res["match_source_regex"] = self.var_source_regex.get().strip()
        if self.var_effort.get().strip():
            res["effort"] = self.var_effort.get().strip()

        self.result = res
        self.destroy()

    def _on_cancel(self):
        self.result = None
        self.destroy()


class NewProjectDialog(tk.Toplevel):
    """Modal dialog to create a new localization project (Game or Software)."""

    def __init__(self, parent: tk.Widget, projects_dir: Path):
        super().__init__(parent)
        self.title("New Localization Project")
        self.geometry("480x360")
        self.resizable(False, False)
        self.configure(bg=BG_BASE)
        self.transient(parent.winfo_toplevel())
        self.grab_set()

        self.projects_dir = projects_dir
        self.result: Optional[Dict[str, Any]] = None

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.center_window()

    def center_window(self):
        self.update_idletasks()
        w = self.winfo_width()
        h = self.winfo_height()
        parent_x = self.master.winfo_rootx()
        parent_y = self.master.winfo_rooty()
        parent_w = self.master.winfo_width()
        parent_h = self.master.winfo_height()
        x = parent_x + max(0, (parent_w - w) // 2)
        y = parent_y + max(0, (parent_h - h) // 2)
        self.geometry(f"+{x}+{y}")

    def _build_ui(self):
        container = ttk.Frame(self, style="TFrame", padding=15)
        container.pack(fill=tk.BOTH, expand=True)

        form = ttk.Labelframe(container, text=" Project Setup ", style="TLabelframe", padding=12)
        form.pack(fill=tk.BOTH, expand=True, pady=(0, 15))

        self.var_name = tk.StringVar(value="")
        r_n, self.entry_name = labeled_entry(form, "Project Name:", self.var_name, width=22, label_width=15,
                                             tooltip="Directory name for this project (e.g. MyGame or MyApp)")
        r_n.pack(fill=tk.X, pady=4)

        self.var_type = tk.StringVar(value="game")
        r_t, _ = labeled_combo(form, "Project Type:", self.var_type, values=["game", "software"],
                               width=20, label_width=15, state="readonly",
                               tooltip="Game (dialogue, narrative, UI, character voices) or Software (desktop/web UI, action verbs, menus, access keys)")
        r_t.pack(fill=tk.X, pady=4)

        self.var_format = tk.StringVar(value="generic_kv")
        r_f, _ = labeled_combo(form, "Format Adapter:", self.var_format,
                               values=["generic_kv", "naninovel", "uabea_json", "unity", "po_gettext", "ue4_5_po", "xliff", "weblate_xliff"],
                               width=20, label_width=15, state="readonly",
                               tooltip="File format adapter for batch extraction & merge")
        r_f.pack(fill=tk.X, pady=4)

        common_langs = ["en", "ja", "hu", "de", "fr", "es", "zh", "ko", "it", "pl", "pt", "ru"]
        r_l = ttk.Frame(form, style="Card.TFrame")
        r_l.pack(fill=tk.X, pady=4)

        self.var_src = tk.StringVar(value="en")
        r_s, _ = labeled_combo(r_l, "Source Lang:", self.var_src, values=common_langs, width=6, label_width=15,
                               tooltip="Source language code (e.g. en, ja, hu)")
        r_s.pack(side=tk.LEFT, padx=(0, 10))

        self.var_tgt = tk.StringVar(value="hu")
        r_tg, _ = labeled_combo(r_l, "Target Lang:", self.var_tgt, values=common_langs, width=6, label_width=11,
                                tooltip="Target language code (e.g. hu, en, ja)")
        r_tg.pack(side=tk.LEFT)

        btn_box = ttk.Frame(container, style="TFrame")
        btn_box.pack(fill=tk.X)

        btn_cancel = action_button(btn_box, "Cancel", self._on_cancel)
        btn_cancel.pack(side=tk.RIGHT, padx=(5, 0))

        btn_save = action_button(btn_box, "✓ Create Project", self._on_create, style="Action.TButton")
        btn_save.pack(side=tk.RIGHT)

        self.entry_name.focus_set()

    def _on_create(self):
        name = self.var_name.get().strip()
        if not name:
            messagebox.showwarning("Missing Name", "Project name cannot be empty.", parent=self)
            return
        proj_dir = self.projects_dir / name
        if proj_dir.exists():
            messagebox.showwarning("Already Exists", f"Project '{name}' already exists.", parent=self)
            return

        self.result = {
            "name": name,
            "type": self.var_type.get(),
            "format": self.var_format.get(),
            "source_lang": self.var_src.get().strip() or "en",
            "target_lang": self.var_tgt.get().strip() or "hu",
        }
        self.destroy()

    def _on_cancel(self):
        self.result = None
        self.destroy()


class ProjectsTab(ttk.Frame):
    def __init__(
        self,
        parent: tk.Widget,
        root: tk.Tk,
        shared_project_var: Optional[tk.StringVar] = None,
        on_project_changed_callback: Optional[Callable[[str], None]] = None
    ):
        super().__init__(parent, style="TFrame")
        self.root = root
        self.shared_project_var = shared_project_var
        self.on_project_changed_callback = on_project_changed_callback

        self.projects_dir = get_default_projects_dir()
        self.current_project_path: Optional[Path] = None
        self.raw_config: Dict[str, Any] = {}
        self.categories_list: List[Dict[str, Any]] = []
        self.is_dirty = False
        self._suppress_dirty = False

        self._build_ui()
        self.refresh_project_list()

    def _build_ui(self):
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # -------------------------------------------------------------
        # Left Frame: Project Explorer
        # -------------------------------------------------------------
        left_frame = section_frame(paned, "📁 Projects", padding=10)
        paned.add(left_frame, weight=1)

        self.project_listbox = tk.Listbox(
            left_frame,
            bg=BG_INSET,
            fg=FG_TEXT,
            selectbackground=ACCENT_INK,
            selectforeground="#ffffff",
            font=FONT_BODY,
            bd=1,
            highlightthickness=0,
        )
        self.project_listbox.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        self.project_listbox.bind("<<ListboxSelect>>", self._on_project_selected)
        create_tooltip(self.project_listbox, "List of projects located under locpipe/projects/")

        btn_box = ttk.Frame(left_frame, style="TFrame")
        btn_box.pack(fill=tk.X, pady=(0, 5))

        btn_new = action_button(btn_box, "+ New", self._create_new_project,
                                tooltip="Scaffold a new translation project with default project.yaml and folder structure")
        btn_new.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))

        btn_del = action_button(btn_box, "🗑 Delete", self._delete_project, style="Stop.TButton",
                                tooltip="Permanently delete the selected project directory from disk")
        btn_del.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(2, 2))

        btn_refresh = action_button(btn_box, "🔄", self.refresh_project_list,
                                    tooltip="Refresh projects directory from disk (F5)")
        btn_refresh.pack(side=tk.RIGHT, padx=(2, 0))

        # -------------------------------------------------------------
        # Right Frame: Config Editor Form
        # -------------------------------------------------------------
        right_frame = section_frame(paned, "⚙️ Project Configuration (project.yaml)", padding=15)
        paned.add(right_frame, weight=3)

        canvas = tk.Canvas(right_frame, bg=BG_SURFACE, highlightthickness=0)
        scrollbar = ttk.Scrollbar(right_frame, orient=tk.VERTICAL, command=canvas.yview)
        self.scrollable_form = ttk.Frame(canvas, style="Card.TFrame")

        self.scrollable_form.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=self.scrollable_form, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        form = self.scrollable_form

        # Row 1: Project Name, Type & Format
        r1 = ttk.Frame(form, style="Card.TFrame")
        r1.pack(fill=tk.X, pady=4)

        self.var_name = tk.StringVar()
        _, self.entry_name = labeled_entry(
            r1, "Project Name:", self.var_name, readonly=True, width=18, label_width=13,
            tooltip="Name of the project directory under locpipe/projects/"
        )
        r1.children["!frame"].pack(side=tk.LEFT, padx=(0, 10))

        self.var_project_type = tk.StringVar(value="game")
        r_pt, self.combo_type = labeled_combo(
            r1, "Type:", self.var_project_type,
            values=["game", "software"],
            width=9, label_width=6, state="readonly",
            tooltip="Project localization type:\n• game: Dialogue, narrative, UI, character voices, gaming style presets\n• software: Desktop/web UI, action verbs, menus, access keys (&), shortcuts"
        )
        r_pt.pack(side=tk.LEFT, padx=(0, 10))

        self.var_format = tk.StringVar(value="generic_kv")
        format_tooltips = (
            "Format Adapter:\n"
            "• naninovel: Naninovel Visual Novel engine (Scripts/*.txt & Managed Text *.txt)\n"
            "• uabea_json: Unity UABEA JSON dumps (CSV in m_Script or typetree walk)\n"
            "• unity: Official Unity Localization Package CSV export\n"
            "• ue4_5_po: Unreal Engine Localization Dashboard .po export (with plural/gender modifiers)\n"
            "• po_gettext: Standard GNU gettext .po files\n"
            "• generic_kv: Simple JSON key-value dictionaries\n"
            "• xliff / weblate_xliff: Standard XLIFF 1.2 CAT tool files"
        )
        r_fmt, self.combo_format = labeled_combo(
            r1, "Format Adapter:", self.var_format,
            values=["generic_kv", "naninovel", "uabea_json", "unity", "po_gettext", "ue4_5_po", "xliff", "weblate_xliff"],
            width=16, label_width=13, tooltip=format_tooltips
        )
        r_fmt.pack(side=tk.LEFT)

        # Row 2: Languages, Formality Register & Batch Glob
        r2 = ttk.Frame(form, style="Card.TFrame")
        r2.pack(fill=tk.X, pady=4)

        common_langs = ["en", "ja", "hu", "de", "fr", "es", "zh", "ko", "it", "pl", "pt", "ru"]

        self.var_source_lang = tk.StringVar(value="en")
        r_sl, _ = labeled_combo(r2, "Source Lang:", self.var_source_lang, values=common_langs, width=6, label_width=13,
                                tooltip="Source language code (e.g. en, ja, hu)")
        r_sl.pack(side=tk.LEFT, padx=(0, 10))

        self.var_target_lang = tk.StringVar(value="hu")
        r_tl, _ = labeled_combo(r2, "Target Lang:", self.var_target_lang, values=common_langs, width=6, label_width=11,
                                tooltip="Target language code (e.g. hu, en, ja)")
        r_tl.pack(side=tk.LEFT, padx=(0, 10))

        self.var_target_register = tk.StringVar(value="informal")
        r_tr, _ = labeled_combo(
            r2, "Register:", self.var_target_register,
            values=["informal", "formal"],
            width=10, label_width=9, state="readonly",
            tooltip="Project-wide address register for narration/UI/dialogue (informal = tegeződés, formal = magázódás). Individual character voices can override this."
        )
        r_tr.pack(side=tk.LEFT, padx=(0, 10))

        self.var_batch_glob = tk.StringVar(value="batches/*.json")
        r_bg, _ = labeled_entry(r2, "Batch Glob:", self.var_batch_glob, width=18, label_width=11,
                                tooltip="Glob pattern matching input batch files relative to project root (e.g. batches/*.json)")
        r_bg.pack(side=tk.LEFT)

        # Section: LLM Provider & Effort
        sec_prov = section_frame(form, "LLM Provider & Effort", padding=10)
        sec_prov.pack(fill=tk.X, pady=8)

        # Fixed provider notice
        lbl_prov_locked = tk.Label(
            sec_prov,
            text="🔒 Provider Engine: antigravity_cli (locked) — Uses local Google Antigravity CLI",
            font=FONT_MONO,
            bg=BG_INSET,
            fg=ACCENT_MOSS,
            padx=8,
            pady=4,
            relief="solid",
            bd=1,
            anchor="w"
        )
        lbl_prov_locked.pack(fill=tk.X, pady=(0, 6))
        create_tooltip(lbl_prov_locked, "The sole maintained LLM provider is Antigravity CLI.")

        # Row: Translate Phase
        r_t = ttk.Frame(sec_prov, style="Card.TFrame")
        r_t.pack(fill=tk.X, pady=3)
        self.var_prov_model = tk.StringVar(value="gemini-3.8-flash")
        r_tm, _ = labeled_combo(
            r_t, "Translate Model:", self.var_prov_model,
            values=["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.1-pro"],
            width=18, label_width=16, state="normal",
            tooltip="Model used for initial batch translation pass (Phase 8). Accepts standard or custom typed model names."
        )
        r_tm.pack(side=tk.LEFT, padx=(0, 15))

        self.var_prov_effort = tk.StringVar(value="low")
        r_te, _ = labeled_combo(
            r_t, "Translate Effort:", self.var_prov_effort,
            values=["low", "high"],
            width=8, label_width=15, state="readonly",
            tooltip="Thinking effort level for translate pass (low / high)."
        )
        r_te.pack(side=tk.LEFT)

        # Row: Review Phase
        r_r = ttk.Frame(sec_prov, style="Card.TFrame")
        r_r.pack(fill=tk.X, pady=3)
        self.var_prov_review_model = tk.StringVar(value="gemini-3.8-flash")
        r_rm, _ = labeled_combo(
            r_r, "Review Model:", self.var_prov_review_model,
            values=["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.1-pro"],
            width=18, label_width=16, state="normal",
            tooltip="Model used for Tier 1 review/repair of flagged items (Phase 13). Accepts standard or custom typed model names."
        )
        r_rm.pack(side=tk.LEFT, padx=(0, 15))

        self.var_prov_review_effort = tk.StringVar(value="high")
        r_re, _ = labeled_combo(
            r_r, "Review Effort:", self.var_prov_review_effort,
            values=["low", "high"],
            width=8, label_width=15, state="readonly",
            tooltip="Thinking effort level for review pass (low / high). Defaults to high for reasoning-heavy repairs."
        )
        r_re.pack(side=tk.LEFT)

        # Row: Escalation Phase
        r_e = ttk.Frame(sec_prov, style="Card.TFrame")
        r_e.pack(fill=tk.X, pady=3)
        self.var_prov_escalation_model = tk.StringVar(value="")
        r_em, _ = labeled_combo(
            r_e, "Escalation Model:", self.var_prov_escalation_model,
            values=["", "gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.1-pro"],
            width=18, label_width=16, state="normal",
            tooltip="Optional: Model for Tier 2 escalation when review fails. Leave blank to inherit Review Model."
        )
        r_em.pack(side=tk.LEFT, padx=(0, 15))

        self.var_prov_escalation_effort = tk.StringVar(value="")
        r_ee, _ = labeled_combo(
            r_e, "Escalation Effort:", self.var_prov_escalation_effort,
            values=["", "low", "high"],
            width=8, label_width=15, state="readonly",
            tooltip="Optional: Thinking effort level for Tier 2 escalation. Leave blank to inherit Review Effort."
        )
        r_ee.pack(side=tk.LEFT)

        # Row: Batch Output Token Cap (P2 Calibration Optimization)
        r_cap = ttk.Frame(sec_prov, style="Card.TFrame")
        r_cap.pack(fill=tk.X, pady=3)
        self.var_prov_batch_cap = tk.StringVar(value="")
        r_bc, _ = labeled_entry(
            r_cap, "Batch Output Cap:", self.var_prov_batch_cap,
            width=10, label_width=16,
            tooltip="Maximum tokens reserved for batch output (P2 optimization). Leave blank for conservative default (4,500). Set to ~14,384 for 2-3x throughput boost after calibrating with 0 wasted retries."
        )
        r_bc.pack(side=tk.LEFT, padx=(0, 15))

        # Section: Language Resources & Style Presets
        sec_res = section_frame(form, "Language Resources & Style Presets", padding=10)
        sec_res.pack(fill=tk.X, pady=8)

        r_lp = ttk.Frame(sec_res, style="Card.TFrame")
        r_lp.pack(fill=tk.X, pady=3)

        self.var_lang_style_preset = tk.StringVar(value="Custom (edit manually)")
        preset_options = ["Custom (edit manually)"] + list(LANG_STYLE_PRESETS.keys())
        r_lsp, self.combo_lang_style_preset = labeled_combo(
            r_lp, "Style Preset:", self.var_lang_style_preset,
            values=preset_options,
            width=32, label_width=16, state="readonly",
            tooltip="Select a Hungarian language style preset to auto-populate resources/lang-style.md. Guarded against accidental overwrites of custom guides."
        )
        r_lsp.pack(side=tk.LEFT, padx=(0, 10))
        self.combo_lang_style_preset.bind("<<ComboboxSelected>>", self._on_lang_style_preset_selected)

        action_button(
            r_lp, "📁 Open Resources", self._open_resources_folder,
            tooltip="Open project's resources/ folder in file manager"
        ).pack(side=tk.LEFT, padx=(0, 10))

        action_button(
            r_lp, "✨ Auto-Discover Resources (AI)", self._on_auto_discover_resources,
            style="Primary.TButton",
            tooltip="Use a lightweight LLM call to analyze extracted batch strings and auto-discover the best style preset, starter glossary, and character voices"
        ).pack(side=tk.LEFT, padx=(0, 10))

        action_button(
            r_lp, "⚡ Bootstrap (from TM)", self._on_bootstrap_resources,
            tooltip="AI-draft glossary, style guide, and character voice bible from existing Translation Memory (TM)"
        ).pack(side=tk.LEFT)

        # Section: Format Options
        sec_opt = section_frame(form, "Format Options", padding=10)
        sec_opt.pack(fill=tk.X, pady=8)

        # Noise Filter Toggle
        self.var_noise_filter = tk.BooleanVar(value=True)
        chk_noise = labeled_checkbutton(
            sec_opt, "Enable built-in engine noise filtering (uabea_json Case 2/3 typetree walk)",
            self.var_noise_filter,
            tooltip="Conservative zero-LLM heuristic that filters GUIDs, color codes, asset paths, and engine type names"
        )
        chk_noise.pack(anchor="w", pady=2)

        # Naturalness gating (P13 experimental)
        self.var_gate_naturalness = tk.BooleanVar(value=False)
        chk_nat = labeled_checkbutton(
            sec_opt, "Enable naturalness score gating (P13 experimental validation)",
            self.var_gate_naturalness,
            tooltip="Requires model to produce naturalness ratings and flags scores below threshold for Tier 1 review."
        )
        chk_nat.pack(anchor="w", pady=2)

        # Character replacements
        r_cr = ttk.Frame(sec_opt, style="Card.TFrame")
        r_cr.pack(fill=tk.X, pady=4)

        # Font fallback checkbox
        self.var_char_fallback_toggle = tk.BooleanVar(value=False)
        self.chk_char_fallback = ttk.Checkbutton(
            r_cr,
            text="Font doesn't support ő/ű → use ASCII-safe fallback (ő→ô, ű→û)",
            variable=self.var_char_fallback_toggle,
            command=self._on_char_fallback_toggled
        )
        self.chk_char_fallback.pack(anchor="w", pady=(0, 4))
        create_tooltip(
            self.chk_char_fallback,
            "One-click shortcut: replaces ő/ű with ô/û and Ő/Ű with Ô/Û for games with incomplete Hungarian font support"
        )

        lbl_cr = ttk.Label(r_cr, text="Character Replacements (JSON map, e.g. {\"ő\": \"ô\", \"ű\": \"û\"}):",
                           font=FONT_BODY, background=BG_SURFACE, foreground=FG_TEXT)
        lbl_cr.pack(anchor="w")
        create_tooltip(lbl_cr, "JSON mapping of characters to substitute in translations at merge time (for games with missing font glyphs)")

        self.var_char_replacements = tk.StringVar(value="{}")
        self.entry_char_replacements = ttk.Entry(r_cr, textvariable=self.var_char_replacements, font=FONT_MONO)
        self.entry_char_replacements.pack(fill=tk.X, pady=2)
        create_tooltip(self.entry_char_replacements, "Valid JSON dictionary mapping source characters to replacements, e.g. {\"ő\": \"ô\", \"ű\": \"û\"}")

        self.lbl_json_error = tk.Label(r_cr, text="", font=(FONT_FAMILY, 9, "bold"), bg=BG_SURFACE, fg=ACCENT_PAPRIKA)
        self.lbl_json_error.pack(anchor="w")

        # UABEA JSON Path Exclude
        r_ex = ttk.Frame(sec_opt, style="Card.TFrame")
        r_ex.pack(fill=tk.X, pady=4)
        lbl_ex = ttk.Label(r_ex, text="UABEA JSON Path Excludes (one regex pattern per line):",
                           font=FONT_BODY, background=BG_SURFACE, foreground=FG_TEXT)
        lbl_ex.pack(anchor="w")
        create_tooltip(lbl_ex, "Regex patterns matched against dotted json_path (e.g. ^internal_metadata\\..* or ^debug_.*)")

        self.txt_path_exclude = tk.Text(r_ex, height=4, bg=BG_INSET, fg=FG_TEXT, insertbackground=ACCENT_INK, font=FONT_MONO, bd=1)
        self.txt_path_exclude.pack(fill=tk.X, pady=2)
        create_tooltip(self.txt_path_exclude, "Regex patterns (one per line) to skip during typetree extraction without calling LLM")

        # Section: Categories
        sec_cat = section_frame(form, "Categories & Batch Rules", padding=10)
        sec_cat.pack(fill=tk.BOTH, expand=True, pady=8)

        cat_btn_box = ttk.Frame(sec_cat, style="Card.TFrame")
        cat_btn_box.pack(fill=tk.X, pady=(0, 6))

        action_button(cat_btn_box, "+ Add Category", self._add_category,
                      tooltip="Add a new category classification rule").pack(side=tk.LEFT, padx=(0, 4))
        action_button(cat_btn_box, "✏️ Edit Category", self._edit_selected_category,
                      tooltip="Edit all fields of the selected category rule (or double-click row)").pack(side=tk.LEFT, padx=4)
        action_button(cat_btn_box, "🗑 Remove", self._remove_category, style="Stop.TButton",
                      tooltip="Delete the selected category rule").pack(side=tk.LEFT, padx=4)

        cat_cols = ("Name", "Batch Size", "Default", "Speaker Check", "Voice Bible", "Max Expansion")
        self.tree_categories = ttk.Treeview(sec_cat, columns=cat_cols, show="headings", height=5)
        for col in cat_cols:
            self.tree_categories.heading(col, text=col)
            self.tree_categories.column(col, width=90, anchor="center")
        self.tree_categories.column("Name", width=120, anchor="w")
        self.tree_categories.pack(fill=tk.BOTH, expand=True)

        self.tree_categories.bind("<Double-1>", lambda e: self._edit_selected_category())
        create_tooltip(self.tree_categories, "Double-click any category to edit its full classification rules and parameters")

        # Action Buttons
        act_box = ttk.Frame(form, style="Card.TFrame")
        act_box.pack(fill=tk.X, pady=12)

        self.btn_save = action_button(act_box, "💾 Save project.yaml", self.save_project, style="Primary.TButton",
                                      tooltip="Save form changes back to project.yaml (Ctrl+S)")
        self.btn_save.pack(side=tk.LEFT, padx=(0, 10))

        self.btn_reload = action_button(act_box, "↩ Reload from Disk", self.reload_current_project,
                                        tooltip="Discard unsaved changes and reload project.yaml from disk")
        self.btn_reload.pack(side=tk.LEFT)

        self.lbl_status = tk.Label(act_box, text="", font=FONT_BODY, bg=BG_SURFACE, fg=ACCENT_MOSS)
        self.lbl_status.pack(side=tk.LEFT, padx=15)

        self._bind_dirty_events()

    def _detect_current_lang_style_preset(self):
        if not self.current_project_path:
            self.var_lang_style_preset.set("Custom (edit manually)")
            return
        ls_path = self.current_project_path / "resources" / "lang-style.md"
        if not ls_path.exists():
            self.var_lang_style_preset.set("Custom (edit manually)")
            return
        content = ls_path.read_text(encoding="utf-8").strip()
        matched = "Custom (edit manually)"
        for name, preset_text in LANG_STYLE_PRESETS.items():
            if content == preset_text.strip():
                matched = name
                break
        self.var_lang_style_preset.set(matched)

    def _on_lang_style_preset_selected(self, event=None):
        if not self.current_project_path:
            return
        selected = self.var_lang_style_preset.get()
        if selected == "Custom (edit manually)":
            return

        preset_text = LANG_STYLE_PRESETS.get(selected)
        if not preset_text:
            return

        ls_path = self.current_project_path / "resources" / "lang-style.md"
        ls_path.parent.mkdir(parents=True, exist_ok=True)

        if ls_path.exists():
            current_content = ls_path.read_text(encoding="utf-8").strip()
            if current_content == preset_text.strip():
                return
            if current_content and current_content not in ("# Language style guide", "# Language style guide\n"):
                confirm = messagebox.askyesno(
                    "Overwrite Language Style Guide?",
                    f"resources/lang-style.md in '{self.current_project_path.name}' already contains custom content.\n\n"
                    f"Do you want to overwrite it with the '{selected}' preset?",
                    parent=self.root,
                    icon="warning"
                )
                if not confirm:
                    self._detect_current_lang_style_preset()
                    return

        ls_path.write_text(preset_text, encoding="utf-8")
        self.lbl_status.config(text=f"Applied preset: {selected}", fg=ACCENT_MOSS)

    def _open_resources_folder(self):
        if not self.current_project_path:
            messagebox.showwarning("No Project", "Please select a project first.", parent=self.root)
            return
        res_dir = self.current_project_path / "resources"
        res_dir.mkdir(parents=True, exist_ok=True)
        open_folder(res_dir)

    def _on_auto_discover_resources(self):
        if not self.current_project_path:
            messagebox.showwarning("No Project", "Please select a project first.", parent=self.root)
            return

        proj_dir = self.current_project_path
        confirm = messagebox.askyesno(
            "Auto-Discover Resources (AI)",
            f"Launch AI Resource Auto-Discovery for '{proj_dir.name}'?\n\n"
            "This will use a single fast, low-cost LLM call to:\n"
            "• Analyze batch strings and match the best Hungarian Style Preset\n"
            "• Detect proper nouns and key terminology for resources/glossary.md\n"
            "• Detect characters and dialogue tone for resources/character-voices.md\n\n"
            "Proceed?",
            parent=self.root,
            icon="question"
        )
        if not confirm:
            return

        self._show_auto_discover_dialog(proj_dir)

    def _show_auto_discover_dialog(self, proj_dir: Path):
        dlg = tk.Toplevel(self.root)
        dlg.title(f"✨ Auto-Discover Resources — {proj_dir.name}")
        dlg.geometry("700x520")
        dlg.minsize(550, 400)
        dlg.configure(bg=BG_BASE)
        dlg.transient(self.root)
        dlg.grab_set()

        hdr = tk.Label(
            dlg,
            text=f"AI Resource & Style Auto-Discovery: {proj_dir.name}",
            font=FONT_TITLE,
            bg=BG_BASE,
            fg=ACCENT_INK,
            padx=12,
            pady=8,
            anchor="w"
        )
        hdr.pack(fill=tk.X)

        pbar = progress_bar(dlg, mode="indeterminate")
        pbar.pack(fill=tk.X, padx=12, pady=(0, 6))
        pbar.start(10)

        txt_log = tk.Text(dlg, bg=BG_INSET, fg=FG_TEXT, font=FONT_MONO, bd=1, relief="solid")
        txt_log.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)

        btn_box = ttk.Frame(dlg, style="Card.TFrame", padding=10)
        btn_box.pack(fill=tk.X)

        lbl_dlg_status = tk.Label(btn_box, text="Analyzing batch files with AI...", font=FONT_BODY, bg=BG_SURFACE, fg=ACCENT_AMBER)
        lbl_dlg_status.pack(side=tk.LEFT, padx=5)

        btn_open = action_button(
            btn_box, "📁 Open Resources Folder",
            lambda: open_folder(proj_dir / "resources"),
            state="disabled",
            tooltip="Open resources/ folder to review generated files"
        )
        btn_open.pack(side=tk.RIGHT, padx=5)

        btn_close = action_button(
            btn_box, "Close",
            dlg.destroy,
            state="disabled"
        )
        btn_close.pack(side=tk.RIGHT, padx=5)

        def run_thread():
            cmd = [
                sys.executable,
                "-m", "locpipe.cli",
                "audit",
                "--project", str(proj_dir),
                "--suggest",
                "--apply"
            ]
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                )
                for line in proc.stdout:
                    dlg.after(0, lambda l=line: (txt_log.insert(tk.END, l), txt_log.see(tk.END)))
                proc.wait()

                def on_done(code=proc.returncode):
                    pbar.stop()
                    pbar.pack_forget()
                    btn_close.config(state="normal")
                    btn_open.config(state="normal")
                    if code == 0:
                        lbl_dlg_status.config(text="✅ Auto-discovery completed & resources applied!", fg=ACCENT_MOSS)
                        self._detect_current_lang_style_preset()
                    else:
                        lbl_dlg_status.config(text=f"❌ Auto-discovery finished with exit code {code}", fg=ACCENT_PAPRIKA)

                dlg.after(0, on_done)
            except Exception as ex:
                def on_err(err=str(ex)):
                    pbar.stop()
                    pbar.pack_forget()
                    btn_close.config(state="normal")
                    txt_log.insert(tk.END, f"\nError running auto-discovery: {err}\n")
                    lbl_dlg_status.config(text=f"❌ Error: {err}", fg=ACCENT_PAPRIKA)
                dlg.after(0, on_err)

        threading.Thread(target=run_thread, daemon=True).start()

    def _on_bootstrap_resources(self):
        if not self.current_project_path:
            messagebox.showwarning("No Project", "Please select a project first.", parent=self.root)
            return

        proj_dir = self.current_project_path
        tm_path = proj_dir / "tm" / "translation_memory.sqlite3"
        if not tm_path.exists():
            messagebox.showwarning(
                "No Translation Memory",
                f"Translation Memory not found at:\n{tm_path}\n\n"
                "Please run a test translation (or full translation) first so there are translated records in TM to bootstrap from.",
                parent=self.root
            )
            return

        confirm = messagebox.askyesno(
            "Bootstrap Resources",
            f"Launch AI Resource Bootstrapping for '{proj_dir.name}'?\n\n"
            "This will analyze your Translation Memory (TM) and draft:\n"
            "• resources/glossary.draft.md (pre-filtered terminology)\n"
            "• resources/lang-style.draft.md (observed tone & grammar patterns)\n"
            "• resources/character-voices.draft.md (if speaker metadata is present)\n\n"
            "Outputs are saved strictly to *.draft.md sibling files and will not overwrite real resource files.\n\n"
            "Proceed with resource bootstrapping?",
            parent=self.root,
            icon="question"
        )
        if not confirm:
            return

        self._show_bootstrap_dialog(proj_dir)

    def _show_bootstrap_dialog(self, proj_dir: Path):
        dlg = tk.Toplevel(self.root)
        dlg.title(f"⚡ Bootstrap Resources — {proj_dir.name}")
        dlg.geometry("700x520")
        dlg.minsize(550, 400)
        dlg.configure(bg=BG_BASE)
        dlg.transient(self.root)
        dlg.grab_set()

        hdr = tk.Label(
            dlg,
            text=f"AI Resource Bootstrapping: {proj_dir.name}",
            font=FONT_TITLE,
            bg=BG_BASE,
            fg=ACCENT_INK,
            padx=12,
            pady=8,
            anchor="w"
        )
        hdr.pack(fill=tk.X)

        pbar = progress_bar(dlg, mode="indeterminate")
        pbar.pack(fill=tk.X, padx=12, pady=(0, 6))
        pbar.start(10)

        txt_log = tk.Text(dlg, bg=BG_INSET, fg=FG_TEXT, font=FONT_MONO, bd=1, relief="solid")
        txt_log.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)

        btn_box = ttk.Frame(dlg, style="Card.TFrame", padding=10)
        btn_box.pack(fill=tk.X)

        lbl_dlg_status = tk.Label(btn_box, text="Running bootstrap-resources...", font=FONT_BODY, bg=BG_SURFACE, fg=ACCENT_AMBER)
        lbl_dlg_status.pack(side=tk.LEFT, padx=5)

        btn_open = action_button(
            btn_box, "📁 Open Resources Folder",
            lambda: open_folder(proj_dir / "resources"),
            state="disabled",
            tooltip="Open resources/ folder to review draft files"
        )
        btn_open.pack(side=tk.RIGHT, padx=5)

        btn_close = action_button(
            btn_box, "Close",
            dlg.destroy,
            state="disabled"
        )
        btn_close.pack(side=tk.RIGHT, padx=5)

        def run_thread():
            cmd = [
                sys.executable,
                "-m", "locpipe.cli",
                "bootstrap-resources",
                "--project", str(proj_dir),
                "--yes"
            ]
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
                )
                for line in proc.stdout:
                    dlg.after(0, lambda l=line: (txt_log.insert(tk.END, l), txt_log.see(tk.END)))
                proc.wait()

                def on_done(code=proc.returncode):
                    pbar.stop()
                    pbar.pack_forget()
                    btn_close.config(state="normal")
                    btn_open.config(state="normal")
                    if code == 0:
                        lbl_dlg_status.config(text="✅ Bootstrap completed successfully!", fg=ACCENT_MOSS)
                    else:
                        lbl_dlg_status.config(text=f"❌ Bootstrap finished with exit code {code}", fg=ACCENT_PAPRIKA)

                dlg.after(0, on_done)
            except Exception as ex:
                def on_err(err=str(ex)):
                    pbar.stop()
                    pbar.pack_forget()
                    btn_close.config(state="normal")
                    txt_log.insert(tk.END, f"\nError running bootstrap: {err}\n")
                    lbl_dlg_status.config(text=f"❌ Error: {err}", fg=ACCENT_PAPRIKA)
                dlg.after(0, on_err)

        threading.Thread(target=run_thread, daemon=True).start()

    def _on_char_fallback_toggled(self):
        if self._suppress_dirty:
            return
        is_checked = self.var_char_fallback_toggle.get()
        raw = self.var_char_replacements.get().strip()
        if is_checked:
            if not raw or raw == "{}":
                self.var_char_replacements.set(json.dumps(_DEFAULT_CHAR_FALLBACK, ensure_ascii=False))
        else:
            try:
                parsed = json.loads(raw) if raw else {}
                if parsed == _DEFAULT_CHAR_FALLBACK:
                    self.var_char_replacements.set("{}")
            except Exception:
                pass
        self._on_field_changed()

    def _bind_dirty_events(self):
        for var in [
            self.var_project_type, self.var_source_lang, self.var_target_lang, self.var_target_register, self.var_format,
            self.var_batch_glob, self.var_noise_filter, self.var_gate_naturalness, self.var_char_replacements,
            self.var_prov_model, self.var_prov_effort,
            self.var_prov_review_model, self.var_prov_review_effort,
            self.var_prov_escalation_model, self.var_prov_escalation_effort,
            self.var_prov_batch_cap
        ]:
            var.trace_add("write", self._on_field_changed)
        self.txt_path_exclude.bind("<KeyRelease>", self._on_field_changed)

    def _on_field_changed(self, *args):
        if not self._suppress_dirty:
            self.is_dirty = True
            if self.current_project_path:
                self.lbl_status.config(text="● Unsaved changes", fg=ACCENT_AMBER)

    def check_unsaved_changes(self) -> bool:
        """Prompt user if there are unsaved changes. Returns True if OK to proceed, False if canceled."""
        if not self.is_dirty or not self.current_project_path:
            return True

        proj_name = self.current_project_path.name
        ans = messagebox.askyesnocancel(
            "Unsaved Changes",
            f"Project '{proj_name}' has unsaved modifications.\n\nSave changes before continuing?",
            parent=self.root
        )
        if ans is True:
            return self.save_project()
        elif ans is False:
            self.is_dirty = False
            return True
        else:
            return False  # Cancel

    def refresh_project_list(self):
        self.project_listbox.delete(0, tk.END)
        self.projects_dir = get_default_projects_dir()
        if not self.projects_dir.exists():
            return

        projects = []
        for p in sorted(self.projects_dir.iterdir()):
            if p.is_dir() and (p / "project.yaml").exists():
                projects.append(p.name)

        for proj in projects:
            self.project_listbox.insert(tk.END, proj)

        target = self.shared_project_var.get() if self.shared_project_var else ""
        if target and target in projects:
            idx = projects.index(target)
            self.project_listbox.selection_set(idx)
            self._load_project_by_name(target)
        elif projects:
            self.project_listbox.selection_set(0)
            self._load_project_by_name(projects[0])

    def select_project(self, name: str):
        if self.current_project_path and self.current_project_path.name == name:
            return
        if not self.check_unsaved_changes():
            # Revert listbox selection
            if self.current_project_path:
                items = self.project_listbox.get(0, tk.END)
                if self.current_project_path.name in items:
                    idx = items.index(self.current_project_path.name)
                    self.project_listbox.selection_clear(0, tk.END)
                    self.project_listbox.selection_set(idx)
            return

        items = self.project_listbox.get(0, tk.END)
        if name in items:
            idx = items.index(name)
            self.project_listbox.selection_clear(0, tk.END)
            self.project_listbox.selection_set(idx)
            self._load_project_by_name(name)
            if self.shared_project_var and self.shared_project_var.get() != name:
                self.shared_project_var.set(name)
            if self.on_project_changed_callback:
                self.on_project_changed_callback(name)

    def _on_project_selected(self, event=None):
        sel = self.project_listbox.curselection()
        if not sel:
            return
        name = self.project_listbox.get(sel[0])
        if self.current_project_path and self.current_project_path.name == name:
            return

        if not self.check_unsaved_changes():
            if self.current_project_path:
                items = self.project_listbox.get(0, tk.END)
                if self.current_project_path.name in items:
                    idx = items.index(self.current_project_path.name)
                    self.project_listbox.selection_clear(0, tk.END)
                    self.project_listbox.selection_set(idx)
            return

        self._load_project_by_name(name)
        if self.shared_project_var and self.shared_project_var.get() != name:
            self.shared_project_var.set(name)
        if self.on_project_changed_callback:
            self.on_project_changed_callback(name)

    def _load_project_by_name(self, name: str):
        proj_dir = self.projects_dir / name
        cfg_path = proj_dir / "project.yaml"
        if not cfg_path.exists():
            return

        self.current_project_path = proj_dir
        try:
            self.raw_config = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        except Exception as e:
            messagebox.showerror("Error", f"Failed to parse project.yaml: {e}")
            return

        self._suppress_dirty = True
        try:
            self.var_name.set(self.raw_config.get("project", name))
            self.var_project_type.set(self.raw_config.get("project_type", "game"))
            self.var_source_lang.set(self.raw_config.get("source_lang", "en"))
            self.var_target_lang.set(self.raw_config.get("target_lang", "hu"))
            self.var_target_register.set(self.raw_config.get("target_register", "informal"))
            self.var_format.set(self.raw_config.get("format", "uabea_json"))
            self.var_batch_glob.set((self.raw_config.get("batches") or {}).get("glob", "batches/*.json"))

            # Load provider configuration
            provider_cfg = self.raw_config.get("provider", {})
            self.var_prov_model.set(provider_cfg.get("model", "gemini-3.8-flash"))
            self.var_prov_effort.set(provider_cfg.get("effort", "low"))
            self.var_prov_review_model.set(provider_cfg.get("review_model", "gemini-3.8-flash"))
            self.var_prov_review_effort.set(provider_cfg.get("review_effort", "high"))
            self.var_prov_escalation_model.set(provider_cfg.get("escalation_model", "") or "")
            self.var_prov_escalation_effort.set(provider_cfg.get("escalation_effort", "") or "")
            cap_val = provider_cfg.get("batch_output_token_cap")
            self.var_prov_batch_cap.set(str(cap_val) if cap_val is not None else "")

            format_opts = self.raw_config.get("format_options", {})
            self.var_noise_filter.set(format_opts.get("noise_filter", True))

            confidence_cfg = self.raw_config.get("confidence", {})
            self.var_gate_naturalness.set(bool(confidence_cfg.get("gate_naturalness", False)))

            char_rep = format_opts.get("character_replacements", {})
            self.var_char_replacements.set(json.dumps(char_rep, ensure_ascii=False) if char_rep else "{}")
            self.var_char_fallback_toggle.set(char_rep == _DEFAULT_CHAR_FALLBACK)
            self.lbl_json_error.config(text="")
            self.entry_char_replacements.configure(style="TEntry")

            excludes = format_opts.get("uabea_json_path_exclude", [])
            self.txt_path_exclude.delete("1.0", tk.END)
            if isinstance(excludes, list):
                self.txt_path_exclude.insert(tk.END, "\n".join(str(x) for x in excludes))
            elif isinstance(excludes, str):
                self.txt_path_exclude.insert(tk.END, excludes)

            # Populate categories
            for row in self.tree_categories.get_children():
                self.tree_categories.delete(row)

            self.categories_list = self.raw_config.get("categories", [
                {"name": "dialogue", "match_speaker_present": True, "needs_character_voice": True, "batch_size": 200, "max_expansion_ratio": 1.8},
                {"name": "ui", "default": True, "needs_character_voice": False, "batch_size": 200, "max_expansion_ratio": 1.4}
            ])

            for c in self.categories_list:
                self._render_category_row(c)

            self._detect_current_lang_style_preset()
            self.is_dirty = False
            self.lbl_status.config(text=f"Loaded {name}", fg=ACCENT_MOSS)
        finally:
            self._suppress_dirty = False

    def _render_category_row(self, c: Dict[str, Any]):
        self.tree_categories.insert("", tk.END, values=(
            c.get("name", "category"),
            c.get("batch_size", 350),
            "Yes" if c.get("default") else "No",
            "Yes" if c.get("match_speaker_present") else "No",
            "Yes" if c.get("needs_character_voice") else "No",
            c.get("max_expansion_ratio") if c.get("max_expansion_ratio") is not None else "default"
        ))

    def reload_current_project(self):
        if self.current_project_path:
            self._load_project_by_name(self.current_project_path.name)

    def _create_new_project(self):
        if not self.check_unsaved_changes():
            return

        dialog = NewProjectDialog(self.root, self.projects_dir)
        self.wait_window(dialog)
        if not dialog.result:
            return

        name = dialog.result["name"]
        ptype = dialog.result.get("type", "game")
        fmt = dialog.result.get("format", "generic_kv")
        src = dialog.result.get("source_lang", "en")
        tgt = dialog.result.get("target_lang", "hu")

        proj_dir = self.projects_dir / name
        (proj_dir / "batches").mkdir(parents=True, exist_ok=True)
        (proj_dir / "resources").mkdir(parents=True, exist_ok=True)

        from locpipe.presets import LANG_STYLE_PRESETS
        from locpipe.bootstrap import ANTI_FABRICATION_DEFAULT

        if ptype == "software":
            categories = [
                {"name": "action", "batch_size": 200, "max_expansion_ratio": 1.4, "default_max_length": 35},
                {"name": "menu", "batch_size": 200, "max_expansion_ratio": 1.4},
                {"name": "dialog", "batch_size": 150, "max_expansion_ratio": 1.8},
                {"name": "ui", "default": True, "batch_size": 200, "max_expansion_ratio": 1.5}
            ]
            style_content = LANG_STYLE_PRESETS.get("Szoftver UI / Asztali alkalmazás", "# Language style guide\n")
            resource_files = [
                ("glossary.md", "# Glossary\n\n| Source term | Target translation | Category | Confidence | Source/justification |\n|---|---|---|---|---|\n| OK | OK | ui | high | standard UI |\n| Cancel | Mégse | ui | high | standard UI |\n| Save | Mentés | ui | high | standard UI |\n| Open | Megnyitás | ui | high | standard UI |\n"),
                ("lang-style.md", style_content),
                ("anti-fabrication-checklist.md", ANTI_FABRICATION_DEFAULT),
            ]
            resources_cfg = {
                "glossary": "resources/glossary.md",
                "lang_style": "resources/lang-style.md",
                "anti_fabrication_checklist": "resources/anti-fabrication-checklist.md",
            }
        else:
            categories = [
                {"name": "dialogue", "match_speaker_present": True, "needs_character_voice": True, "batch_size": 200, "max_expansion_ratio": 1.8},
                {"name": "ui", "default": True, "needs_character_voice": False, "batch_size": 200, "max_expansion_ratio": 1.4}
            ]
            style_content = LANG_STYLE_PRESETS.get("Modern, laza (kortárs akció/kaland)", "# Language style guide\n")
            resource_files = [
                ("glossary.md", "# Glossary\n\n| Source term | Target translation | Category | Confidence | Source/justification |\n|---|---|---|---|---|\n"),
                ("lang-style.md", style_content),
                ("character-voices.md", "# Character voice bible\n\n| Character | Register | Traits | Avoid |\n|---|---|---|---|\n"),
                ("anti-fabrication-checklist.md", ANTI_FABRICATION_DEFAULT),
            ]
            resources_cfg = {
                "glossary": "resources/glossary.md",
                "lang_style": "resources/lang-style.md",
                "character_voices": "resources/character-voices.md",
                "anti_fabrication_checklist": "resources/anti-fabrication-checklist.md",
            }

        if fmt == "naninovel":
            batch_glob = "batches/**/*.txt"
        elif fmt in ("po_gettext", "ue4_5_po"):
            batch_glob = "batches/**/*.po"
        elif fmt in ("xliff", "weblate_xliff"):
            batch_glob = "batches/**/*.xlf"
        elif fmt == "unity":
            batch_glob = "batches/**/*.csv"
        else:
            batch_glob = "batches/*.json"

        template_yaml = {
            "project": name,
            "project_type": ptype,
            "source_lang": src,
            "target_lang": tgt,
            "profile": "fast",
            "target_register": "informal",
            "format": fmt,
            "batches": {"glob": batch_glob},
            "resources": resources_cfg,
            "categories": categories,
            "provider": {
                "name": "antigravity_cli",
                "model": "gemini-3.8-flash",
                "effort": "low",
                "review_model": "gemini-3.8-flash",
                "review_effort": "low",
                "mode": "sync",
                "max_concurrency": 2,
            },
            "format_options": {
                "noise_filter": True,
                "character_replacements": {},
                "uabea_json_path_exclude": []
            },
            "tm": {"db_path": "tm/translation_memory.sqlite3"},
            "confidence": {
                "review_threshold": 0.65,
                "max_expansion_ratio": 1.6,
                "tier1_repair_attempts": 2,
                "fidelity_sample_rate": 0.0,
                "escalation_sample_rate": 0.0,
                "review_chunk_size": 50
            }
        }

        (proj_dir / "project.yaml").write_text(yaml.dump(template_yaml, sort_keys=False, allow_unicode=True), encoding="utf-8")

        for fname, content in resource_files:
            (proj_dir / "resources" / fname).write_text(content, encoding="utf-8")

        self.refresh_project_list()
        self.select_project(name)

    def _delete_project(self):
        if not self.current_project_path:
            messagebox.showwarning("No Project", "No active project selected to delete.")
            return

        name = self.current_project_path.name
        confirm = messagebox.askyesno(
            "Confirm Delete Project",
            f"Are you sure you want to PERMANENTLY DELETE project '{name}'?\n\nDirectory: {self.current_project_path}\n\nThis cannot be undone!",
            icon="warning",
            parent=self.root
        )
        if not confirm:
            return

        try:
            shutil.rmtree(self.current_project_path)
            self.is_dirty = False
            self.current_project_path = None
            self.refresh_project_list()
            messagebox.showinfo("Deleted", f"Project '{name}' has been deleted.", parent=self.root)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to delete project directory: {e}", parent=self.root)

    def _add_category(self):
        dialog = CategoryEditDialog(self)
        self.wait_window(dialog)
        if dialog.result:
            self.categories_list.append(dialog.result)
            self._render_category_row(dialog.result)
            self.is_dirty = True
            self.lbl_status.config(text="● Unsaved changes", fg=ACCENT_AMBER)

    def _edit_selected_category(self):
        sel = self.tree_categories.selection()
        if not sel:
            messagebox.showinfo("Select Category", "Please select a category row to edit.")
            return

        item_id = sel[0]
        idx = self.tree_categories.index(item_id)
        current_cat = self.categories_list[idx] if idx < len(self.categories_list) else {}

        dialog = CategoryEditDialog(self, category_data=current_cat)
        self.wait_window(dialog)
        if dialog.result:
            self.categories_list[idx] = dialog.result
            self.tree_categories.item(item_id, values=(
                dialog.result.get("name", "category"),
                dialog.result.get("batch_size", 350),
                "Yes" if dialog.result.get("default") else "No",
                "Yes" if dialog.result.get("match_speaker_present") else "No",
                "Yes" if dialog.result.get("needs_character_voice") else "No",
                dialog.result.get("max_expansion_ratio") if dialog.result.get("max_expansion_ratio") is not None else "default"
            ))
            self.is_dirty = True
            self.lbl_status.config(text="● Unsaved changes", fg=ACCENT_AMBER)

    def _remove_category(self):
        sel = self.tree_categories.selection()
        if not sel:
            return
        for s in sel:
            idx = self.tree_categories.index(s)
            if idx < len(self.categories_list):
                del self.categories_list[idx]
            self.tree_categories.delete(s)
        self.is_dirty = True
        self.lbl_status.config(text="● Unsaved changes", fg=ACCENT_AMBER)

    def save_project(self) -> bool:
        if not self.current_project_path:
            messagebox.showwarning("No Project", "No active project selected.")
            return False

        # Validate Character Replacements JSON
        raw_cr = self.var_char_replacements.get().strip()
        parsed_cr = {}
        if raw_cr:
            try:
                parsed_cr = json.loads(raw_cr)
                if not isinstance(parsed_cr, dict):
                    raise ValueError("Must be a JSON object mapping source chars to target chars.")
                self.lbl_json_error.config(text="")
            except Exception as e:
                self.lbl_json_error.config(text=f"❌ Invalid JSON in Character Replacements: {e}")
                messagebox.showerror(
                    "Validation Error",
                    f"Character Replacements contains invalid JSON:\n\n{e}\n\nSave aborted to prevent data loss. Please fix the JSON syntax.",
                    parent=self.root
                )
                return False

        cfg = dict(self.raw_config) if self.raw_config else {}
        cfg["project"] = self.var_name.get()
        cfg["project_type"] = self.var_project_type.get().strip() or "game"
        cfg["source_lang"] = self.var_source_lang.get()
        cfg["target_lang"] = self.var_target_lang.get()
        cfg["target_register"] = self.var_target_register.get().strip() or "informal"
        cfg["format"] = self.var_format.get()

        if "batches" not in cfg:
            cfg["batches"] = {}
        cfg["batches"]["glob"] = self.var_batch_glob.get()

        # Provider configuration
        if "provider" not in cfg:
            cfg["provider"] = {}
        cfg["provider"]["name"] = "antigravity_cli"
        cfg["provider"]["model"] = self.var_prov_model.get().strip() or "gemini-3.8-flash"
        cfg["provider"]["effort"] = self.var_prov_effort.get().strip() or "low"
        cfg["provider"]["review_model"] = self.var_prov_review_model.get().strip() or "gemini-3.8-flash"
        cfg["provider"]["review_effort"] = self.var_prov_review_effort.get().strip() or "high"

        esc_model = self.var_prov_escalation_model.get().strip()
        if esc_model:
            cfg["provider"]["escalation_model"] = esc_model
        else:
            cfg["provider"].pop("escalation_model", None)

        esc_effort = self.var_prov_escalation_effort.get().strip()
        if esc_effort:
            cfg["provider"]["escalation_effort"] = esc_effort
        else:
            cfg["provider"].pop("escalation_effort", None)

        batch_cap_str = self.var_prov_batch_cap.get().strip()
        if batch_cap_str.isdigit():
            cfg["provider"]["batch_output_token_cap"] = int(batch_cap_str)
        else:
            cfg["provider"].pop("batch_output_token_cap", None)

        # Confidence options
        if "confidence" not in cfg:
            cfg["confidence"] = {}
        if self.var_gate_naturalness.get():
            cfg["confidence"]["gate_naturalness"] = True
        else:
            cfg["confidence"].pop("gate_naturalness", None)

        # Format options
        if "format_options" not in cfg:
            cfg["format_options"] = {}
        cfg["format_options"]["noise_filter"] = self.var_noise_filter.get()
        cfg["format_options"]["character_replacements"] = parsed_cr

        raw_ex = self.txt_path_exclude.get("1.0", tk.END).strip()
        cfg["format_options"]["uabea_json_path_exclude"] = [line.strip() for line in raw_ex.splitlines() if line.strip()]

        if self.categories_list:
            cfg["categories"] = self.categories_list

        cfg_path = self.current_project_path / "project.yaml"
        try:
            cfg_path.write_text(yaml.dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
            self.raw_config = cfg
            self.is_dirty = False
            self.lbl_status.config(text="✅ Saved successfully!", fg=ACCENT_MOSS)
            self.lbl_json_error.config(text="")
            self.root.after(3000, lambda: self.lbl_status.config(text=""))
            return True
        except Exception as e:
            messagebox.showerror("Save Error", f"Failed to save project.yaml: {e}", parent=self.root)
            return False
