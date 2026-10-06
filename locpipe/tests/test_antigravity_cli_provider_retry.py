"""Tests for AntigravityCLIProvider's retry/backoff behavior in
_run_agy() -- the subprocess wrapper around `agy --print`. Covers the
three transient failure modes it's supposed to retry (timeout, rate
limit, empty output) and the one it deliberately doesn't (a real,
non-zero exit with stderr content -- retrying that just burns quota on
something a retry can't fix, e.g. a bad model name or auth failure).
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from locpipe.providers.antigravity_cli_provider import AntigravityCLIProvider


def _make_provider(timeout_s: int = 5) -> AntigravityCLIProvider:
    provider = AntigravityCLIProvider.__new__(AntigravityCLIProvider)
    provider.model = "gemini-3.7-flash"
    provider.timeout_s = timeout_s
    provider.effort = "low"
    return provider


class _FakeProc:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_retries_after_timeout_then_succeeds():
    calls = {"n": 0}

    def fake_run(args, capture_output, timeout):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise subprocess.TimeoutExpired(cmd=args, timeout=timeout)
        return _FakeProc(stdout=b'[{"id": 0, "translation": "OK"}]')

    with patch("locpipe.providers.antigravity_cli_provider._BINARY", "/fake/agy"), \
         patch("subprocess.run", side_effect=fake_run), \
         patch("time.sleep", lambda s: None):
        result = _make_provider()._run_agy("prompt")

    assert calls["n"] == 3
    assert "OK" in result


def test_gives_up_after_max_attempts_of_pure_timeouts():
    def fake_run(args, capture_output, timeout):
        raise subprocess.TimeoutExpired(cmd=args, timeout=timeout)

    with patch("locpipe.providers.antigravity_cli_provider._BINARY", "/fake/agy"), \
         patch("subprocess.run", side_effect=fake_run), \
         patch("time.sleep", lambda s: None):
        try:
            _make_provider()._run_agy("prompt")
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "timed out" in str(e)


def test_retries_on_rate_limit_then_succeeds():
    calls = {"n": 0}

    def fake_run(args, capture_output, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeProc(returncode=1, stderr=b"429 RESOURCE_EXHAUSTED")
        return _FakeProc(stdout=b'[{"id": 0, "translation": "OK"}]')

    with patch("locpipe.providers.antigravity_cli_provider._BINARY", "/fake/agy"), \
         patch("subprocess.run", side_effect=fake_run), \
         patch("time.sleep", lambda s: None):
        result = _make_provider()._run_agy("prompt")

    assert calls["n"] == 2
    assert "OK" in result


def test_retries_on_empty_output_then_succeeds():
    calls = {"n": 0}

    def fake_run(args, capture_output, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeProc(returncode=0, stdout=b"")  # the documented silent-empty-output bug
        return _FakeProc(stdout=b'[{"id": 0, "translation": "OK"}]')

    with patch("locpipe.providers.antigravity_cli_provider._BINARY", "/fake/agy"), \
         patch("subprocess.run", side_effect=fake_run), \
         patch("time.sleep", lambda s: None):
        result = _make_provider()._run_agy("prompt")

    assert calls["n"] == 2
    assert "OK" in result


def test_retries_on_truncated_or_unparseable_json():
    calls = {"n": 0}

    def fake_run(args, capture_output, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            # Truncated JSON output (cut off mid-sentence)
            return _FakeProc(returncode=0, stdout=b'[{"id": 0, "translation": "Hahu')
        return _FakeProc(stdout=b'[{"id": 0, "translation": "Teljes mondat"}]')

    with patch("locpipe.providers.antigravity_cli_provider._BINARY", "/fake/agy"), \
         patch("subprocess.run", side_effect=fake_run), \
         patch("time.sleep", lambda s: None):
        result = _make_provider()._run_agy("prompt", response_format="json")

    assert calls["n"] == 2
    assert "Teljes mondat" in result


def test_does_not_retry_a_real_nonzero_exit():
    """A genuine failure (bad model name, auth error, ...) should fail
    immediately, not burn all 5 attempts retrying something a retry
    can never fix."""
    calls = {"n": 0}

    def fake_run(args, capture_output, timeout):
        calls["n"] += 1
        return _FakeProc(returncode=2, stderr=b"unknown model 'nonsense-model'")

    with patch("locpipe.providers.antigravity_cli_provider._BINARY", "/fake/agy"), \
         patch("subprocess.run", side_effect=fake_run), \
         patch("time.sleep", lambda s: None):
        try:
            _make_provider()._run_agy("prompt")
            assert False, "expected RuntimeError"
        except RuntimeError as e:
            assert "exited 2" in str(e)

    assert calls["n"] == 1
