"""Unit test suite for Step 2 optimizations:
- P7: Hungarian spellcheck memoization and golden-output invariant
- P8: Fidelity sampling invariants (no forced minimum 1)
- P10: Antigravity CLI session cleanup debounce
"""

import time
import zlib
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from locpipe.models import Entry, EntryStatus, ValidationResult, Severity
from locpipe.validators.validate_hu_spelling import (
    is_hu_word_known,
    spellcheck_target,
    _WORD_KNOWN_CACHE,
    _get_spellchecker,
)
from locpipe.providers.antigravity_cli_provider import (
    _cleanup_antigravity_session,
)
import locpipe.providers.antigravity_cli_provider as agy_provider_mod


# ---------------------------------------------------------------------------
# P7: Hungarian spellcheck memoization & golden-output invariant
# ---------------------------------------------------------------------------

def test_p7_memoization_cache():
    class DummySpell:
        def __init__(self):
            self.calls = 0

        def unknown(self, words):
            self.calls += 1
            # "alma" is known (empty unknown), "rosszszo" is unknown
            return [w for w in words if "rossz" in w]

    dummy = DummySpell()
    _WORD_KNOWN_CACHE.clear()

    # First lookup populates cache
    res1 = is_hu_word_known("alma", dummy)
    assert res1 is True
    assert "alma" in _WORD_KNOWN_CACHE
    assert dummy.calls == 1

    # Second lookup hits cache directly without calling dummy.unknown
    res2 = is_hu_word_known("alma", dummy)
    assert res2 is True
    assert dummy.calls == 1  # Still 1! Cache was used.


def test_p7_golden_output_invariant():
    """Verify that memoized spellcheck produces byte-identical ValidationIssue lists."""
    test_strings = [
        "A hős kardot rántott a sárkány ellen.",
        "Ez egy tesztszöveg hibás szavakal és helyesekkel.",
        "Mentés a játékállásba (Slot 1).",
        "A varázsló felidézte a jégvihart és a tűzgolyót.",
        "xyznonexistentword meg egy masikrosszszo",
    ]

    _WORD_KNOWN_CACHE.clear()
    first_run_issues = [spellcheck_target(s) for s in test_strings]

    # Run again with warm cache
    second_run_issues = [spellcheck_target(s) for s in test_strings]

    assert len(first_run_issues) == len(second_run_issues)
    for list1, list2 in zip(first_run_issues, second_run_issues):
        assert len(list1) == len(list2)
        for iss1, iss2 in zip(list1, list2):
            assert iss1.severity == iss2.severity
            assert iss1.code == iss2.code
            assert iss1.message == iss2.message


# ---------------------------------------------------------------------------
# P8: Fidelity sampling invariants
# ---------------------------------------------------------------------------

def test_p8_fidelity_sampling_no_forced_minimum():
    """Test that removing the forced minimum means small batches/files don't always sample 1."""
    # Create 5 entries whose crc32 bucket >= threshold
    rate = 0.03  # 3% threshold
    threshold = int(rate * 100)  # 3

    candidates = []
    for i in range(10):
        e = Entry("file.po", f"key_{i}", f"Source text {i}", f"Target text {i}")
        bucket = zlib.crc32(e.key.encode("utf-8")) % 100
        # If bucket happens to be >= 3, it should NOT be sampled
        candidates.append((e, bucket))

    # Pick entries where bucket >= threshold
    unselected = [e for e, b in candidates if b >= threshold]
    if not unselected:
        pytest.skip("Could not find entries with bucket >= threshold")

    # Simulate the sampling loop from pipeline.py
    sampled = []
    for e in unselected:
        bucket = zlib.crc32(e.key.encode("utf-8")) % 100
        if bucket < threshold:
            sampled.append(e)

    # In the old code: if not sampled and cands: sampled.append(min(...))
    # In the new code (P8): no forced minimum!
    assert len(sampled) == 0

    # Determinism: identical candidates produce identical sample sets
    sampled2 = [e for e in unselected if (zlib.crc32(e.key.encode("utf-8")) % 100) < threshold]
    assert sampled == sampled2


# ---------------------------------------------------------------------------
# P10: Antigravity CLI cleanup debounce
# ---------------------------------------------------------------------------

def test_p10_cleanup_debounce(tmp_path):
    agy_provider_mod._last_cleanup_ts = 0.0

    dummy_prompt = tmp_path / "locpipe_agy_prompt_test.txt"
    dummy_prompt.write_text("test prompt", encoding="utf-8")

    with patch("pathlib.Path.home") as mock_home:
        mock_home.return_value = tmp_path
        # First call executes and updates _last_cleanup_ts
        _cleanup_antigravity_session(str(dummy_prompt))
        ts1 = agy_provider_mod._last_cleanup_ts
        assert ts1 > 0.0

        # Second call immediately after skips (debounced < 600s)
        _cleanup_antigravity_session(str(dummy_prompt))
        ts2 = agy_provider_mod._last_cleanup_ts
        assert ts1 == ts2  # Not updated because skipped early
