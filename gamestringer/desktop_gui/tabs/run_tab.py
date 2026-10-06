"""
Run Tab — Execute LocPipe Plan (dry token estimate) and Run (Antigravity CLI translation).
"""

from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Any, Callable, Optional

from locpipe.config import load_project
from locpipe.pipeline import plan
from gamestringer.desktop_gui.tabs.projects_tab import get_default_projects_dir
from gamestringer.desktop_gui.theme import (
    BG_BASE, BG_SURFACE, BG_INSET, FG_TEXT, FG_MUTED,
    ACCENT_INK, ACCENT_MOSS, ACCENT_PAPRIKA, ACCENT_AMBER,
    FONT_TITLE, FONT_HEADING, FONT_BODY, FONT_MONO
)
from gamestringer.desktop_gui.tooltip import create_tooltip
from gamestringer.desktop_gui.widgets import (
    section_frame, labeled_entry, labeled_combo, action_button, progress_bar
)


def _kill_proc_tree(proc: Optional[subprocess.Popen]) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=5,
            )
        else:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


class RunTab(ttk.Frame):
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
        self.current_project_dir: Optional[Path] = None
        self.active_process: Optional[subprocess.Popen] = None
        self.is_running = False
        self.has_run_plan_in_session = False
        self.start_time: Optional[float] = None
        self.timer_id: Optional[str] = None

        self._build_ui()
        self.refresh_projects()

    def _build_ui(self):
        main_frame = ttk.Frame(self, style="TFrame", padding=12)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Top Control Bar
        top_bar = ttk.Frame(main_frame, style="Card.TFrame", padding=10)
        top_bar.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(top_bar, text="Project:", font=FONT_HEADING, background=BG_SURFACE, foreground=ACCENT_INK).pack(side=tk.LEFT, padx=(5, 5))

        self.var_selected_project = tk.StringVar()
        self.combo_project = ttk.Combobox(
            top_bar,
            textvariable=self.var_selected_project,
            state="readonly",
            width=22,
        )
        self.combo_project.pack(side=tk.LEFT, padx=(0, 10))
        self.combo_project.bind("<<ComboboxSelected>>", self._on_project_changed)
        create_tooltip(self.combo_project, "Select target project to plan or translate")

        self.var_limit = tk.StringVar(value="")
        r_lim, _ = labeled_entry(
            top_bar, "Batch Limit:", self.var_limit, width=6, label_width=12,
            tooltip="For testing only: translates only the first N batches (e.g. 1 or 2). Leave blank to translate full project."
        )
        r_lim.pack(side=tk.LEFT, padx=(0, 10))

        self.var_max_api_calls = tk.StringVar(value="500")
        r_max_calls, _ = labeled_entry(
            top_bar, "Max API Calls:", self.var_max_api_calls, width=6, label_width=13,
            tooltip="Hard safety ceiling on total LLM API calls for this run. Stops the pipeline cleanly (no data loss — already-finished files stay committed) once reached. Leave blank only if you intend an unlimited run."
        )
        r_max_calls.pack(side=tk.LEFT, padx=(0, 10))

        self.btn_plan = action_button(
            top_bar, "📋 Run Plan (Dry Estimate)", self._run_plan,
            tooltip="Dry run: calculates exact batch counts, deduplication, and input/output token estimates with ZERO API calls"
        )
        self.btn_plan.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_run = action_button(
            top_bar, "🚀 Run Translation (Antigravity CLI)", self._start_translation,
            style="Primary.TButton",
            tooltip="Live execution: translates batch files using Antigravity CLI (gemini-3.8-flash) and writes output"
        )
        self.btn_run.pack(side=tk.LEFT, padx=(0, 6))

        self.btn_stop = action_button(
            top_bar, "⏹ Stop", self._stop_execution,
            style="Stop.TButton",
            tooltip="Safely abort the running translation subprocess"
        )
        self.btn_stop.config(state="disabled")
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 8))

        self.pbar = progress_bar(top_bar, mode="indeterminate", length=130)

        # Stats Card
        self.stats_frame = section_frame(main_frame, "Execution Stats & Progress", padding=10)
        self.stats_frame.pack(fill=tk.X, pady=(0, 10))

        stat_row = ttk.Frame(self.stats_frame, style="Card.TFrame")
        stat_row.pack(fill=tk.X)

        self.lbl_stats = tk.Label(
            stat_row,
            text="Ready. Select a project and run Plan to estimate tokens, or Run to start translation.",
            font=FONT_BODY,
            bg=BG_SURFACE,
            fg=FG_TEXT,
            justify=tk.LEFT
        )
        self.lbl_stats.pack(side=tk.LEFT, anchor="w", expand=True)

        self.lbl_timer = tk.Label(
            stat_row,
            text="",
            font=FONT_MONO,
            bg=BG_SURFACE,
            fg=ACCENT_INK
        )
        self.lbl_timer.pack(side=tk.RIGHT, padx=(10, 0))

        self.btn_open_folder = action_button(
            self.stats_frame, "📂 Open Project Folder", self._open_project_folder,
            tooltip="Open the project's root folder in the system file manager"
        )

        # Log Output Box
        log_frame = section_frame(main_frame, "Live Output & Logging Console", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.txt_log = tk.Text(log_frame, bg=BG_INSET, fg=FG_TEXT, font=FONT_MONO, bd=1, wrap=tk.CHAR)
        scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.txt_log.yview)
        self.txt_log.configure(yscrollcommand=scroll.set)

        self.txt_log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Color tags for console
        self.txt_log.tag_config("cyan", foreground=ACCENT_INK)
        self.txt_log.tag_config("green", foreground=ACCENT_MOSS)
        self.txt_log.tag_config("red", foreground=ACCENT_PAPRIKA)
        self.txt_log.tag_config("yellow", foreground=ACCENT_AMBER)
        self.txt_log.tag_config("muted", foreground=FG_MUTED)

    def refresh_projects(self):
        self.projects_dir = get_default_projects_dir()
        projs = []
        if self.projects_dir.exists():
            for p in sorted(self.projects_dir.iterdir()):
                if p.is_dir() and (p / "project.yaml").exists():
                    projs.append(p.name)

        self.combo_project["values"] = projs
        target = self.shared_project_var.get() if self.shared_project_var else ""
        if target and target in projs:
            self.var_selected_project.set(target)
            self.current_project_dir = self.projects_dir / target
        elif projs:
            self.var_selected_project.set(projs[0])
            self.current_project_dir = self.projects_dir / projs[0]
        else:
            self.var_selected_project.set("")
            self.current_project_dir = None

    def select_project(self, name: str):
        projs = self.combo_project["values"]
        if name in projs:
            self.var_selected_project.set(name)
            self.current_project_dir = self.projects_dir / name

    def _on_project_changed(self, event=None):
        name = self.var_selected_project.get()
        if name:
            self.current_project_dir = self.projects_dir / name
            if self.shared_project_var and self.shared_project_var.get() != name:
                self.shared_project_var.set(name)
            if self.on_project_changed_callback:
                self.on_project_changed_callback(name)

    def _log(self, text: str, tag: str = "normal"):
        self.txt_log.insert(tk.END, text, tag)
        self.txt_log.see(tk.END)

    def _open_project_folder(self):
        if not self.current_project_dir or not self.current_project_dir.exists():
            return
        target = str(self.current_project_dir.resolve())
        try:
            if sys.platform == "win32":
                os.startfile(target)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
        except Exception as e:
            messagebox.showerror("Error", f"Failed to open folder: {e}", parent=self.root)

    def _update_timer(self):
        if self.is_running and self.start_time:
            elapsed = int(time.time() - self.start_time)
            mins = elapsed // 60
            secs = elapsed % 60
            self.lbl_timer.config(text=f"⏱ Elapsed: {mins:02d}:{secs:02d}")
            self.timer_id = self.root.after(1000, self._update_timer)

    def _run_plan(self):
        if not self.current_project_dir:
            messagebox.showwarning("No Project", "Please select a project first.", parent=self.root)
            return

        try:
            config = load_project(self.current_project_dir)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load project: {e}", parent=self.root)
            return

        limit_val = int(self.var_limit.get().strip()) if self.var_limit.get().strip().isdigit() else None

        self.btn_plan.config(state="disabled")
        self.pbar.pack(side=tk.LEFT, padx=5)
        self.pbar.start(10)
        self.lbl_stats.config(text="Calculating plan and token estimates...", fg=ACCENT_INK)

        def worker():
            try:
                res = plan(config, limit_batches=limit_val)
                self.root.after(0, self._on_plan_complete, config, res, None)
            except Exception as e:
                self.root.after(0, self._on_plan_complete, config, None, str(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_plan_complete(self, config, res: Optional[dict], error: Optional[str]):
        self.btn_plan.config(state="normal")
        self.pbar.stop()
        self.pbar.pack_forget()

        if error:
            self._log(f"\n[ERROR] Plan failed: {error}\n", "red")
            self.lbl_stats.config(text=f"Plan failed: {error}", fg=ACCENT_PAPRIKA)
            return

        if not res:
            self._log("\n[ERROR] Plan returned empty result.\n", "red")
            self.lbl_stats.config(text="Plan returned empty result.", fg=ACCENT_PAPRIKA)
            return

        total = res.get("total_entries", 0)
        unique = res.get("unique_strings_needing_translation", 0)
        already_trans = res.get("already_translated", 0)
        tm_hits = res.get("tm_hits", 0)
        batches = res.get("llm_calls_needed", 0)
        output_tokens = res.get("estimated_output_tokens", 0)
        uncached_input = res.get("estimated_uncached_input_tokens", 0)
        cached_read = res.get("estimated_cache_read_tokens", 0)
        realistic_input = res.get("estimated_realistic_input_tokens", uncached_input + cached_read)
        caching_note = res.get("caching_note", "")

        dedup_pct = ((total - unique) / total * 100) if total > 0 else 0.0

        self.has_run_plan_in_session = True
        self._log(f"\n=== PRE-FLIGHT PLAN: {config.project} ===\n", "cyan")
        ptype = getattr(config, "project_type", "game").upper()
        self._log(f"Type: {ptype} | Pair: {config.source_lang} -> {config.target_lang}\n", "muted")
        self._log(f"Provider: {config.provider.name} ({config.provider.model})\n", "muted")
        self._log(f"Raw translatable entries:  {total:,}\n")
        self._log(f"Already translated:        {already_trans:,}\n")
        self._log(f"Filled from TM (0 cost):   {tm_hits:,}\n")
        self._log(f"Unique strings:            {unique:,} ({dedup_pct:.1f}% deduplication)\n")
        self._log(f"Total Batches:             {batches}\n")
        calls_by_cat = res.get("calls_by_category", {})
        if calls_by_cat:
            for cat, n in sorted(calls_by_cat.items()):
                self._log(f"    - {cat}: {n} call(s)\n", "muted")
        self._log(f"Estimated Total Input Tokens (no caching — Antigravity CLI): ~{realistic_input:,}\n")
        self._log(f"Estimated Target Output Tokens: ~{output_tokens:,}\n", "green")
        if caching_note:
            self._log(f"Note: {caching_note}\n", "muted")
        if res.get("context_window_warning"):
            self._log(f"  [ADVISORY] {res['context_window_warning']}\n", "yellow")

        stat_text = (
            f"Project: {config.project} | Batches: {batches} | Unique Strings: {unique:,} (dedup: {dedup_pct:.1f}%)\n"
            f"Estimated Tokens (no caching): ~{realistic_input:,} in / ~{output_tokens:,} out (0 API cost for plan)"
        )
        self.lbl_stats.config(text=stat_text, fg=ACCENT_INK)

    def _start_translation(self):
        if not self.current_project_dir:
            messagebox.showwarning("No Project", "Please select a project first.", parent=self.root)
            return

        if self.is_running:
            return

        try:
            config = load_project(self.current_project_dir)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to load project: {e}", parent=self.root)
            return

        proj_name = self.current_project_dir.name
        limit_val = self.var_limit.get().strip()
        limit_desc = f"{limit_val} batch(es) only" if limit_val.isdigit() else "NO LIMIT (Full Project)"

        max_calls_val = self.var_max_api_calls.get().strip()
        if max_calls_val.isdigit() and int(max_calls_val) > 0:
            max_calls_desc = f"{max_calls_val} calls max"
        else:
            max_calls_desc = "UNLIMITED"

        prov_info = f"{config.provider.name} ({config.provider.model}, effort: {config.provider.effort})"
        warning_msg = (
            f"Launch Live Translation Run?\n\n"
            f"• Project: {proj_name}\n"
            f"• Batch Scope: {limit_desc}\n"
            f"• Max API Calls: {max_calls_desc}\n"
            f"• Provider: {prov_info}\n\n"
        )
        if not (max_calls_val.isdigit() and int(max_calls_val) > 0):
            warning_msg += "⚠️ Warning: Max API Calls is UNLIMITED. If this is a large project, setting a safety ceiling (e.g. 500) is recommended to prevent unintended API usage.\n\n"

        if not self.has_run_plan_in_session:
            warning_msg += "⚠️ Note: You have not run 'Plan' yet in this session. Running Plan first is recommended to verify token estimates.\n\n"

        warning_msg += "Do you want to proceed with translation?"

        confirm = messagebox.askyesno(
            "Confirm Translation Run",
            warning_msg,
            icon="question",
            parent=self.root
        )
        if not confirm:
            return

        self.is_running = True
        self.btn_run.config(state="disabled")
        self.btn_plan.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.btn_open_folder.pack_forget()

        self.pbar.pack(side=tk.LEFT, padx=5)
        self.pbar.start(10)

        self.start_time = time.time()
        self._update_timer()

        # Build locpipe run command
        cmd = [
            sys.executable,
            "-m", "locpipe.cli",
            "run",
            "--project", str(self.current_project_dir)
        ]
        if limit_val.isdigit():
            cmd.extend(["--limit", limit_val])
        if max_calls_val.isdigit() and int(max_calls_val) > 0:
            cmd.extend(["--max-api-calls", max_calls_val])

        self._log(f"\n>>> Starting pipeline: {' '.join(cmd)}\n", "cyan")
        self.lbl_stats.config(text=f"Translation in progress for '{proj_name}' via Antigravity CLI...", fg=ACCENT_MOSS)

        def worker():
            env = os.environ.copy()
            repo_root = str(Path(__file__).resolve().parent.parent.parent.parent)
            locpipe_src = str(Path(__file__).resolve().parent.parent.parent.parent / "locpipe" / "src")
            paths = [repo_root, locpipe_src]
            if "PYTHONPATH" in env and env["PYTHONPATH"]:
                env["PYTHONPATH"] = f"{os.pathsep.join(paths)}{os.pathsep}{env['PYTHONPATH']}"
            else:
                env["PYTHONPATH"] = os.pathsep.join(paths)

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=env
                )
                self.active_process = proc

                for line in iter(proc.stdout.readline, ''):
                    if not line:
                        break
                    self.root.after(0, self._handle_log_line, line)

                proc.wait()
                exit_code = proc.returncode
                self.root.after(0, self._on_finished, exit_code)

            except Exception as e:
                self.root.after(0, self._log, f"[ERROR] Subprocess execution error: {e}\n", "red")
                self.root.after(0, self._on_finished, 1)

        threading.Thread(target=worker, daemon=True).start()

    def _handle_log_line(self, line: str):
        tag = "normal"
        if "[ERROR]" in line or "Error" in line or "FAILED" in line:
            tag = "red"
        elif "[WARNING]" in line or "Warning" in line:
            tag = "yellow"
        elif "[SUCCESS]" in line or "Phase " in line or "SUCCESS" in line:
            tag = "green"
        elif "===" in line or "---" in line:
            tag = "cyan"
        self._log(line, tag)

    def _stop_execution(self):
        proc = self.active_process
        if proc and proc.poll() is None:
            self._log("\n⏹ Terminating process tree...\n", "yellow")
            _kill_proc_tree(proc)

    def _on_finished(self, exit_code: int):
        self.is_running = False
        self.active_process = None
        self.btn_run.config(state="normal")
        self.btn_plan.config(state="normal")
        self.btn_stop.config(state="disabled")
        self.pbar.stop()
        self.pbar.pack_forget()

        if self.timer_id:
            self.root.after_cancel(self.timer_id)
            self.timer_id = None

        if exit_code == 0:
            self._log("\n✅ Pipeline completed successfully!\n", "green")
            self.lbl_stats.config(text="Pipeline execution completed successfully.", fg=ACCENT_MOSS)
            self.btn_open_folder.pack(side=tk.LEFT, pady=(8, 0))
        else:
            self._log(f"\n❌ Pipeline exited with code {exit_code}\n", "red")
            self.lbl_stats.config(text=f"Pipeline finished with exit code {exit_code}.", fg=ACCENT_PAPRIKA)
