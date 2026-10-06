"""Shells out to the `agy` (Antigravity CLI) binary directly, for
people who only have Antigravity CLI access and no separate
GEMINI_API_KEY for providers/gemini_provider.py.

Read this before using it for a real 50k+ row run:

`agy -p` / `--print` (non-interactive mode) has a real, currently open
bug: under a non-TTY context — exactly what a subprocess call from
Python is — it can complete a full model round trip and print NOTHING
to stdout, while still exiting 0. A pipeline that trusts exit code
alone will report success while silently translating nothing. This
isn't a one-off report; it shows up across multiple independent
write-ups from the last couple of months, with the same shape each
time ("worked in my terminal, empty in CI/subprocess").

Given that, this wrapper:
  1. never trusts exit code alone — every call requires the actual
     response text to parse as the expected JSON shape before it's
     accepted, matching the "two-stage gate" pattern the workaround
     writeups converge on;
  2. raises loudly (RuntimeError) on empty/unparseable output instead
     of returning a hollow success;
  3. uses ANTIGRAVITY_TOKEN for auth, not GEMINI_API_KEY -- agy
     ignores the latter entirely;
  4. does retry transient failures (rate limit, timeout, empty output --
     the silent-empty-output bug included, since a fixed short backoff is
     cheap insurance even though it isn't guaranteed to help with a
     harness-level bug) up to 5 attempts with backoff, but does NOT retry
     a non-zero exit with output on stderr -- that's treated as a real
     error (bad model name, auth failure, ...) worth surfacing immediately
     rather than burning attempts on something a retry can't fix;
   5. writes the prompt to a temporary UTF-8 file and passes its path to `agy --print`;
  6. asks pipeline.py for a per-batch pruned prompt instead of the
     category-level full-context one other providers get
     (`prefers_per_batch_context = True`) -- there's no persistent
     client here to cache the full glossary or character-voices file
     against, so sending them in full on every one-shot subprocess call
     would just be paying more tokens for nothing. Covers both: a
     dialogue batch only gets the voice-bible rows for the characters
     actually speaking in it, not the whole cast.

Flags below (`--print`, an auto-approve flag) are current as of the
sources checked while building this, but this CLI is genuinely
mid-flight — run `agy --help` and diff before trusting this against a
real 50k-row run, and validate with --limit 1 first (see the plan
command in cli.py) rather than finding out on batch 40.

If you can get a GEMINI_API_KEY at all (aistudio.google.com/apikey,
behavior to work around.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
from typing import Optional

from .base import TranslationProvider

logger = logging.getLogger(__name__)


_last_cleanup_ts: float = 0.0


def _cleanup_antigravity_session(temp_prompt_path: str) -> None:
    """Removes the temporary conversation directory in ~/.gemini/antigravity-cli/brain/
    AND the corresponding sqlite database in ~/.gemini/antigravity-cli/conversations/
    created by Antigravity CLI for this batch chunk prompt, keeping the session list clean.
    Debounced process-wide to at most once every 600 seconds.
    """
    global _last_cleanup_ts
    now = time.time()
    if now - _last_cleanup_ts < 600.0:
        return
    _last_cleanup_ts = now

    try:
        home_cli = Path.home() / ".gemini" / "antigravity-cli"
        brain_dir = home_cli / "brain"
        conv_dir = home_cli / "conversations"
        temp_name = Path(temp_prompt_path).name

        if brain_dir.is_dir():
            # Check most recent sessions first; this batch's session was created in the last 15 minutes
            recent_sessions = []
            now = time.time()
            for session_dir in brain_dir.iterdir():
                if not session_dir.is_dir():
                    continue
                try:
                    mtime = session_dir.stat().st_mtime
                    if now - mtime < 900:  # 15 minutes
                        recent_sessions.append((mtime, session_dir))
                except OSError:
                    pass
            recent_sessions.sort(key=lambda x: x[0], reverse=True)

            found_and_cleaned = False
            for _, session_dir in recent_sessions:
                transcript = session_dir / ".system_generated" / "logs" / "transcript.jsonl"
                if transcript.exists():
                    try:
                        with open(transcript, "r", encoding="utf-8", errors="ignore") as f:
                            first_line = f.readline()
                            if temp_name in first_line:
                                session_id = session_dir.name
                                shutil.rmtree(session_dir, ignore_errors=True)
                                if conv_dir.is_dir():
                                    db_file = conv_dir / f"{session_id}.db"
                                    if db_file.exists():
                                        try:
                                            db_file.unlink()
                                            for extra in [conv_dir / f"{session_id}.db-wal", conv_dir / f"{session_id}.db-shm"]:
                                                if extra.exists():
                                                    extra.unlink()
                                        except Exception:
                                            pass
                                summaries_db = home_cli / "conversation_summaries.db"
                                if summaries_db.exists():
                                    try:
                                        import sqlite3
                                        with sqlite3.connect(str(summaries_db), timeout=5) as conn:
                                            conn.execute("DELETE FROM conversation_summaries WHERE conversation_id = ?", (session_id,))
                                            conn.commit()
                                    except Exception:
                                        pass
                                found_and_cleaned = True
                                break
                    except Exception:
                        pass
                if found_and_cleaned:
                    break
    except Exception:
        pass

_BINARY = (
    shutil.which("agy")
    or shutil.which("agy.cmd")
    or shutil.which("agy.exe")
    or shutil.which("antigravity")
    or os.environ.get("ANTIGRAVITY_AGENTAPI_EXE")
    or os.environ.get("ANTIGRAVITY_CLI_EXE")
)


class AntigravityCLIProvider(TranslationProvider):
    #: No persistent client/cache between calls (each call is a fresh
    #: subprocess) -- get a per-batch pruned prompt (glossary and
    #: character voices both), not the category-level full-context one
    #: other providers benefit from caching.
    prefers_per_batch_context = True
    max_input_chars: int | None = None
    context_window_tokens: int | None = None

    def __init__(
        self,
        model: str = "gemini-3.8-flash",
        max_concurrency: int = 2,
        timeout_s: int = 300,
        effort: str = "low",
    ):
        if _BINARY is None:
            raise RuntimeError(
                "agy not found on PATH. Install: curl -fsSL https://antigravity.google/cli/install.sh | bash"
            )
        if not os.environ.get("ANTIGRAVITY_TOKEN") and not os.environ.get("ANTIGRAVITY_CLI_AUTHENTICATED"):
            # best-effort check only -- agy may also be authenticated via system
            # keyring from an interactive `agy auth login`, which this can't see.
            pass
        self.model = model
        self.max_concurrency = max_concurrency
        self.timeout_s = timeout_s
        self.effort = effort or "low"
        self._semaphores: dict[asyncio.AbstractEventLoop, asyncio.Semaphore] = {}

    @property
    def semaphore(self) -> asyncio.Semaphore:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.Semaphore(self.max_concurrency)
        if loop not in self._semaphores:
            self._semaphores[loop] = asyncio.Semaphore(self.max_concurrency)
        return self._semaphores[loop]

    def _run_agy(
        self,
        full_prompt: str,
        effort: Optional[str] = None,
        response_format: str = "json",
    ) -> str:
        # To avoid Windows command-line character length limit (32,767 chars),
        # write the full prompt to a temporary UTF-8 text file and pass its path to `agy --print`.
        with tempfile.NamedTemporaryFile(
            "w", prefix="locpipe_agy_prompt_", suffix=".txt", delete=False, encoding="utf-8"
        ) as temp_file:
            temp_file.write(full_prompt)
            temp_prompt_path = temp_file.name
        # Belt-and-suspenders: NamedTemporaryFile already creates with 0600 on
        # POSIX, but this content is unreleased translation/lore text, so make
        # the owner-only restriction explicit rather than relying on the
        # default. No-op in effect on Windows (no POSIX mode bits), harmless
        # to call there.
        try:
            os.chmod(temp_prompt_path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

        effective_effort = effort or self.effort
        if response_format == "json":
            prompt_instruction = (
                "Output ONLY the raw valid JSON array as requested in the instructions, "
                "with no conversational commentary, from prompt file: " + temp_prompt_path
            )
        else:
            prompt_instruction = (
                "Output ONLY the requested response without commentary from prompt file: " + temp_prompt_path
            )

        args = [
            _BINARY,
            "--print",
            prompt_instruction,
            "--model", self.model,
            # Required, not optional, in headless --print mode: agy's own
            # headless mode ignores permissions.allow from settings.json
            # entirely, so without this flag any tool-call prompt has nowhere
            # to render in a non-TTY subprocess and the process hangs until
            # timeout_s instead of failing or succeeding
            # (google-antigravity/antigravity-cli#548). Per agy's docs this
            # auto-approves ALL tool calls including file writes and command
            # execution, not just "answer with text" -- safe here only
            # because complete() always sends a closed translation prompt
            # that never asks the model to use a tool, but that safety
            # property lives in prompt_builder.py, not in this flag.
            "--dangerously-skip-permissions",
        ]

        if ("gemini-3" in self.model or "gemini-2" in self.model) and effective_effort:
            args.extend(["--effort", effective_effort])

        max_attempts = 5
        last_exit_code = None
        last_stderr = ""
        last_stdout = ""

        try:
            for attempt in range(max_attempts):
                attempt_num = attempt + 1
                try:
                    proc = subprocess.run(
                        args,
                        capture_output=True,
                        timeout=self.timeout_s,
                    )
                except subprocess.TimeoutExpired:
                    logger.warning(
                        "agy subprocess timed out after %ds on attempt %d/%d",
                        self.timeout_s,
                        attempt_num,
                        max_attempts,
                    )
                    if attempt == max_attempts - 1:
                        logger.error(
                            "agy timed out after %ds on all %d attempts",
                            self.timeout_s,
                            max_attempts,
                        )
                        raise RuntimeError(f"agy timed out after {self.timeout_s}s on every attempt ({max_attempts})")
                    backoff = [2, 4, 8, 12][min(attempt, 3)]
                    time.sleep(backoff)
                    continue

                stdout = proc.stdout.decode("utf-8", errors="replace").strip() if proc.stdout else ""
                stderr = proc.stderr.decode("utf-8", errors="replace").strip() if proc.stderr else ""
                last_exit_code = proc.returncode
                last_stderr = stderr
                last_stdout = stdout

                if proc.returncode == 0 and stdout:
                    if response_format == "json":
                        text_check = stdout.strip().strip("`")
                        if text_check.startswith("json"):
                            text_check = text_check[4:].strip()
                        s_idx = text_check.find("[")
                        e_idx = text_check.rfind("]")
                        if s_idx != -1 and e_idx != -1 and e_idx > s_idx:
                            text_check = text_check[s_idx : e_idx + 1]
                        try:
                            json.loads(text_check)
                            return stdout
                        except json.JSONDecodeError:
                            import re
                            text_clean = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", text_check)
                            try:
                                json.loads(text_clean, strict=False)
                                return stdout
                            except json.JSONDecodeError:
                                logger.warning(
                                    "agy returned unparseable/truncated JSON on attempt %d/%d (output length %d). Retrying with backoff...",
                                    attempt_num,
                                    max_attempts,
                                    len(stdout),
                                )
                                backoff = [1, 2, 4, 8][min(attempt, 3)]
                                time.sleep(backoff)
                                continue
                    return stdout

                combined_err = (stderr + " " + stdout).lower()
                is_transient = any(
                    err_str in combined_err
                    for err_str in (
                        "resource_exhausted",
                        "429",
                        "503",
                        "500",
                        "502",
                        "504",
                        "unavailable",
                        "deadline_exceeded",
                        "overloaded",
                        "service unavailable",
                        "bad gateway",
                        "gateway timeout",
                        "rate limit",
                        "ratelimit",
                        "too many requests",
                        "quota exceeded",
                        "connection reset",
                        "connection refused",
                        "network is unreachable",
                        "getaddrinfo",
                        "enotfound",
                        "eai_again",
                        "econnreset",
                        "econnrefused",
                        "etimedout",
                        "timed out",
                        "timeout",
                        "client.timeout",
                        "fetch failed",
                        "broken pipe",
                        "socket hang up",
                        "unexpected eof",
                    )
                )
                if is_transient:
                    logger.warning(
                        "agy transient error/rate limit on attempt %d/%d (exit code: %s, stderr: %r). Backing off...",
                        attempt_num,
                        max_attempts,
                        proc.returncode,
                        stderr[:300] or stdout[:300],
                    )
                    backoff = [2, 5, 10, 20][min(attempt, 3)]
                    time.sleep(backoff)
                    continue

                if proc.returncode != 0:
                    logger.error(
                        "agy subprocess failed on attempt %d/%d (exit code: %s, stderr: %r)",
                        attempt_num,
                        max_attempts,
                        proc.returncode,
                        stderr,
                    )
                    raise RuntimeError(f"agy exited {proc.returncode}: {stderr[:500]}")

                # If exit code was 0 but stdout is empty or whitespace-only (headless drop bug)
                if not stdout:
                    logger.warning(
                        "agy returned empty/silent stdout on attempt %d/%d (exit code: %s, stderr: %r). Retrying with backoff...",
                        attempt_num,
                        max_attempts,
                        proc.returncode,
                        stderr[:300],
                    )
                    backoff = [1, 2, 4, 8][min(attempt, 3)]
                    time.sleep(backoff)
                    continue

            logger.error(
                "agy failed after %d attempts. Last exit code: %s, stderr: %r, stdout length: %d",
                max_attempts,
                last_exit_code,
                last_stderr,
                len(last_stdout),
            )
            raise RuntimeError(
                f"agy failed after {max_attempts} attempts due to empty/silent output, timeout, or rate limit. "
                f"Last exit code: {last_exit_code}, stderr: {last_stderr[:500]!r}"
            )
        finally:
            if os.path.exists(temp_prompt_path):
                try:
                    os.unlink(temp_prompt_path)
                except Exception:
                    pass
            _cleanup_antigravity_session(temp_prompt_path)

    async def complete(
        self,
        system_prompt: str,
        user_payload: str,
        *,
        max_tokens: int = 8192,
        effort: Optional[str] = None,
        response_format: str = "json",
    ) -> str:
        if response_format == "json":
            full_prompt = (
                f"{system_prompt}\n\n--- INPUT ---\n{user_payload}\n\n"
                "Respond with ONLY the JSON array described above. No other text."
            )
        else:
            full_prompt = f"{system_prompt}\n\n--- INPUT ---\n{user_payload}\n"

        # Open design question for team discussion: Should semaphore acquisition wrap the entire
        # complete() call (including Gate 3 JSON parsing/validation) or only the raw subprocess
        # invocation in _run_agy? Keeping existing scope intact for now.
        async with self.semaphore:
            stdout = await asyncio.to_thread(self._run_agy, full_prompt, effort, response_format)

        if response_format != "json":
            return stdout.strip()

        # Gate 3: does it actually parse as the shape we asked for? An agent
        # harness is more likely than a raw completion API to wrap output in
        # commentary despite instructions -- strip a leading/trailing fence
        # and confirm before handing it back.
        text = stdout.strip().strip("`")
        if text.startswith("json"):
            text = text[4:].strip()

        start_idx = text.find("[")
        end_idx = text.rfind("]")
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            text = text[start_idx : end_idx + 1]

        try:
            json.loads(text)
        except json.JSONDecodeError:
            import re
            text_clean = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", text)
            try:
                json.loads(text_clean, strict=False)
                text = text_clean
            except json.JSONDecodeError as e:
                raise RuntimeError(f"agy output didn't parse as JSON: {e}\nRaw output: {stdout[:500]!r}")
        return text
