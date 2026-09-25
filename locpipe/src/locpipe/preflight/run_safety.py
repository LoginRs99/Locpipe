"""Pre-run hardening: TM snapshot and orphaned agy-artifact sweep.

Both are called from cli.py's cmd_run before pipeline.run() starts --
neither belongs in pipeline.py itself, which stays free of any
assumption about the antigravity_cli provider's on-disk session layout
or the local filesystem's temp directory.
"""

from __future__ import annotations

import glob
import logging
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)


def snapshot_tm(config) -> Path:
    """Copy the project's TM sqlite file to a timestamped backup before a
    real (non-dry-run) run starts. checkpoint.py already refuses to
    silently reset a corrupted checkpoint.json -- nothing previously
    protected the TM database itself the same way against a mid-write
    crash (disk full, kill -9). Cheap: a single file, not a project
    directory copy.

    Returns the TM db path unchanged (no-op) if it doesn't exist yet
    (first run for this project -- nothing to snapshot).
    """
    tm_path = Path(config.tm_db_path)
    if not tm_path.exists():
        return tm_path

    backups_dir = tm_path.parent / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    backup_path = backups_dir / f"{tm_path.stem}.{stamp}.bak{tm_path.suffix}"
    try:
        import sqlite3
        with sqlite3.connect(str(tm_path)) as src_conn, sqlite3.connect(str(backup_path)) as dst_conn:
            src_conn.backup(dst_conn)
    except Exception as err:
        logger.warning("sqlite3.backup failed (%s), falling back to copy2", err)
        shutil.copy2(tm_path, backup_path)
        wal = tm_path.with_name(tm_path.name + "-wal")
        if wal.exists():
            try:
                shutil.copy2(wal, backup_path.with_name(backup_path.name + "-wal"))
            except OSError:
                pass
    logger.info("TM snapshot written to %s", backup_path)
    return backup_path


def sweep_orphaned_agy_artifacts(max_age_s: int = 3600) -> Dict[str, int]:
    """Removes stray temp-prompt files and antigravity-cli brain session
    directories left behind by a crashed (kill -9, OOM, power loss) prior
    run -- a clean run already removes both itself (see
    antigravity_cli_provider.py's _run_agy `finally` block and
    _cleanup_antigravity_session), so anything found here by definition
    survived an unclean exit.

    Only removes locpipe's own temp files -- matched by the
    'locpipe_agy_prompt_' prefix antigravity_cli_provider.py now writes
    (Task 5 of this spec) -- never any other .txt file in the system temp
    directory.

    max_age_s=3600 (1h) is comfortably larger than
    ProviderConfig.sync_call_timeout_s (300s) times max_retries (5) = the
    longest a single legitimate in-flight call could plausibly take, so
    this will never delete a file an actually-running call still owns.
    """
    removed_temp_files = 0
    pattern = os.path.join(tempfile.gettempdir(), "locpipe_agy_prompt_*.txt")
    for f in glob.glob(pattern):
        try:
            if time.time() - os.path.getmtime(f) > max_age_s:
                os.unlink(f)
                removed_temp_files += 1
        except OSError:
            pass

    removed_sessions = 0
    removed_dbs = 0
    home_cli = Path.home() / ".gemini" / "antigravity-cli"
    brain_dir = home_cli / "brain"
    conv_dir = home_cli / "conversations"
    summaries_db = home_cli / "conversation_summaries.db"

    def _is_locpipe_session_dir(s_dir: Path) -> bool:
        transcript = s_dir / ".system_generated" / "logs" / "transcript.jsonl"
        if transcript.exists():
            try:
                with open(transcript, "r", encoding="utf-8", errors="ignore") as f:
                    for _ in range(10):
                        line = f.readline()
                        if not line:
                            break
                        if "locpipe_agy_prompt_" in line:
                            return True
            except OSError:
                pass
        return False

    def _is_locpipe_db(db_p: Path) -> bool:
        try:
            with open(db_p, "rb") as f:
                chunk = f.read(65536)
                if b"locpipe_agy_prompt_" in chunk:
                    return True
        except OSError:
            pass
        return False

    def _delete_summary(session_id: str) -> None:
        if summaries_db.exists():
            try:
                import sqlite3
                with sqlite3.connect(str(summaries_db), timeout=5) as conn:
                    conn.execute("DELETE FROM conversation_summaries WHERE conversation_id = ?", (session_id,))
                    conn.commit()
            except Exception:
                pass

    if brain_dir.is_dir():
        for session_dir in brain_dir.iterdir():
            if not session_dir.is_dir():
                continue
            try:
                if time.time() - session_dir.stat().st_mtime > max_age_s:
                    if not _is_locpipe_session_dir(session_dir):
                        continue
                    session_id = session_dir.name
                    shutil.rmtree(session_dir, ignore_errors=True)
                    removed_sessions += 1

                    if conv_dir.is_dir():
                        db_file = conv_dir / f"{session_id}.db"
                        if db_file.exists():
                            try:
                                db_file.unlink()
                                removed_dbs += 1
                                for extra in [conv_dir / f"{session_id}.db-wal", conv_dir / f"{session_id}.db-shm"]:
                                    if extra.exists():
                                        extra.unlink()
                            except OSError:
                                pass
                    _delete_summary(session_id)
            except OSError:
                pass

    if conv_dir.is_dir():
        for db_file in conv_dir.glob("*.db"):
            try:
                if time.time() - db_file.stat().st_mtime > max_age_s:
                    if not _is_locpipe_db(db_file):
                        continue
                    session_id = db_file.stem
                    db_file.unlink()
                    removed_dbs += 1
                    for extra in [conv_dir / f"{session_id}.db-wal", conv_dir / f"{session_id}.db-shm"]:
                        if extra.exists():
                            try:
                                extra.unlink()
                            except OSError:
                                pass
                    _delete_summary(session_id)
            except OSError:
                pass

    return {
        "removed_temp_files": removed_temp_files,
        "removed_sessions": removed_sessions,
        "removed_conversation_dbs": removed_dbs,
    }
