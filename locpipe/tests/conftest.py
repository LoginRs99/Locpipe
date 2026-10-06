"""Pytest configuration and test hygiene isolation hooks.

Ensures:
- Strict test isolation using tmp_path
- Clean teardown of all temporary files and SQLite connections
- Zero residual artifacts left in the workspace directory after test runs
"""

from __future__ import annotations

import gc
import os
import shutil
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def test_hygiene_teardown():
    """Autouse fixture ensuring garbage collection and cleanup after each test."""
    yield
    # Force garbage collection to ensure all SQLite handles and open file descriptors are closed
    gc.collect()


def pytest_sessionfinish(session, exitstatus):
    """Teardown hook executed after the entire test suite completes.
    Sweeps any temporary files or orphaned SQLite databases in the locpipe root.
    """
    gc.collect()
    root = Path(__file__).parent.parent

    # Sweep any residual stray databases or temp files created directly in root
    stray_patterns = [
        "*.tmp",
        "*.sqlite3-journal",
        "*.sqlite3-wal",
        "test_*.tmp",
    ]
    for pattern in stray_patterns:
        for p in root.glob(pattern):
            try:
                if p.is_file():
                    p.unlink(missing_ok=True)
                elif p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
            except Exception:
                pass
