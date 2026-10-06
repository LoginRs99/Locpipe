"""Checkpoint/resume state for long runs.

Two genuinely different problems, both under one file because they
share the same on-disk state:

1. Sync mode: real resumability, not just a progress log. pipeline.run()
   now processes one input file at a time -- extract, translate,
   validate, review/repair, merge, and commit that file's results to
   the TM -- before moving to the next file, and calls mark_file_done()
   only once all of that has landed. A crash (or a rate-limit that
   exhausts retries) partway through a run leaves every already-
   finished file committed and skippable on restart via is_file_done();
   only the file that was in flight, and anything after it, gets
   reprocessed -- and even that reprocessing is cheap, since anything
   within it that already made it into the TM (e.g. other strings in
   the same file that translated fine before a sibling batch failed)
   is picked straight back up by enrich_and_dedupe() rather than
   re-translated. completed_batches below remains a human-readable
   progress trail on top of this, not the resumability mechanism itself.

2. Batch mode: this is where a checkpoint is load-bearing, not
   decoration. A Message Batch / Gemini Batch job is submitted once
   and can take up to 24-48h to resolve. If the Python process dies
   while *waiting* -- laptop sleeps, terminal closes, SSH drops -- a
   naive restart would submit a brand new job for the same content.
   Both providers' docs are explicit that batch creation is not
   idempotent: that's not a retry, it's a duplicate charge for the
   same translations. This module persists the pending job id the
   moment it's returned, before any blocking poll, so a restart
   reattaches to the same job instead of resubmitting.

   Recomputing the batch structure fresh on resume (rather than
   serializing the full request payload) is deliberate: extract ->
   classify -> dedupe -> build_batches is a pure function of the batch
   files and TM state, both of which are untouched between submission
   and a resume (nothing writes to either until results come back).
   So resuming recomputes it and checks a fingerprint against what was
   actually submitted, rather than trusting that nothing changed --
   if a batch file was hand-edited while a job was in flight, this
   fails loudly instead of silently mis-attributing results.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Optional


def fingerprint_batches(batches) -> str:
    ordered = [[e.tm_key for e in b.representatives] for b in batches]
    return hashlib.sha256(json.dumps(ordered, sort_keys=False).encode("utf-8")).hexdigest()


class Checkpoint:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception as e:
                # Do NOT silently fall back to a fresh/empty checkpoint here.
                # In batch mode this file is the only record of pending_job --
                # losing it on a parse error means a resume can't reattach to
                # an in-flight batch and will submit a brand new one, which
                # per the module docstring above is a non-idempotent, billable
                # duplicate. Fail loudly and make the user decide.
                raise RuntimeError(
                    f"Checkpoint file {self.path} exists but is not valid JSON ({e}). "
                    "Refusing to auto-reset it, since it may reference a pending "
                    "batch job -- resubmitting that job would be a duplicate, "
                    "billable API call. Inspect/repair the file by hand, check "
                    "the provider dashboard for an in-flight job first, or "
                    "delete the file yourself once you've confirmed it's safe "
                    "to start fresh."
                ) from e
            if isinstance(data, dict):
                data.setdefault("completed_files", [])
                return data
        return {"completed_batches": [], "pending_job": None, "last_updated": None, "completed_files": []}

    def _save(self) -> None:
        with self._lock:
            self._save_locked()

    def mark_batch_done(self, category: str, entry_count: int) -> None:
        with self._lock:
            self.data["completed_batches"].append(
                {"category": category, "entry_count": entry_count, "at": time.time()}
            )
            self._save_locked()

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["last_updated"] = time.time()
        tmp_path = self.path.with_suffix(f".tmp.{threading.get_ident()}")
        try:
            tmp_path.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
            for attempt in range(3):
                try:
                    tmp_path.replace(self.path)
                    break
                except PermissionError:
                    if attempt == 2:
                        raise
                    time.sleep(0.05)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

    def save_batch_drafts(self, drafts: dict[str, str]) -> None:
        with self._lock:
            self.data.setdefault("batch_drafts", {}).update(drafts)
            self._save_locked()

    def get_batch_drafts(self) -> dict[str, str]:
        with self._lock:
            return dict(self.data.get("batch_drafts", {}))

    def mark_file_done(self, file_path: str) -> None:
        """Called only after a file's translations are validated, reviewed
        (if needed), merged into its native file, AND committed to the TM --
        i.e. only after there is nothing left that a crash could lose for
        this file. See run()'s per-file loop in pipeline.py: everything
        between extract() and this call either all lands or, on an
        exception partway through, none of it does (the file isn't marked
        done, and TM commits for OTHER already-finished files are
        untouched -- they aren't rolled back just because a later file
        in the same run failed).
        """
        with self._lock:
            if file_path not in self.data["completed_files"]:
                self.data["completed_files"].append(file_path)
            self._save_locked()

    def is_file_done(self, file_path: str) -> bool:
        with self._lock:
            return file_path in self.data["completed_files"]

    def set_pending_job(self, job_id: str, provider_name: str, fingerprint: str, n_requests: int) -> None:
        with self._lock:
            self.data["pending_job"] = {
                "job_id": job_id,
                "provider_name": provider_name,
                "fingerprint": fingerprint,
                "n_requests": n_requests,
                "submitted_at": time.time(),
            }
            self._save_locked()

    def get_pending_job(self) -> Optional[dict]:
        with self._lock:
            val = self.data.get("pending_job")
            return dict(val) if isinstance(val, dict) else val

    def clear_pending_job(self) -> None:
        with self._lock:
            self.data["pending_job"] = None
            self._save_locked()

    def progress_summary(self) -> str:
        n = len(self.data["completed_batches"])
        total = sum(b["entry_count"] for b in self.data["completed_batches"])
        return f"{n} batch(es) completed so far, {total} entries committed"
