"""Pluggable translation backend. The pipeline only depends on this
interface, never on a specific vendor's SDK — swap providers by
changing project.yaml's provider.name, not pipeline code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class TranslationProvider(ABC):
    #: Set True for providers with no caching continuity between calls
    #: (e.g. a fresh subprocess per call) -- pipeline.py builds them a
    #: per-batch pruned prompt (glossary AND character-voices, both
    #: trimmed to just what this batch actually needs) instead of the
    #: category-level full-context one, since there's no cache to
    #: amortize the extra tokens against. See
    #: providers/antigravity_cli_provider.py.
    prefers_per_batch_context: bool = False

    #: False for providers whose output must never land in the persistent
    #: TM -- currently just MockProvider. Without this, `locpipe run
    #: --dry-run` would silently write its "MOCK-HU: ..." placeholder
    #: text into the real, persistent TM database (same origin tags as
    #: genuine output, since commit_to_tm() has no way to tell them apart
    #: on its own), where a LATER real run's TM lookup could then reuse
    #: it as if it were a real translation -- a dry run is supposed to be
    #: safe to run repeatedly with no lasting effect, and silently
    #: corrupting future real output is the opposite of that.
    persists_to_tm: bool = True

    #: Transport input length cap in characters. Defaults to 24000 for standard
    #: transport backends. If None, transport imposes no cap (e.g. prompt travels via temp file).
    max_input_chars: int | None = 24000

    #: Declared model context window in tokens, or None if undeclared/unknown.
    #: When set, used solely to drive advisory warnings in plan(); never treated as a number
    #: to drive batch sizing.
    context_window_tokens: int | None = None

    @abstractmethod
    async def complete(
        self,
        system_prompt: str,
        user_payload: str,
        *,
        max_tokens: int,
        effort: Optional[str] = None,
        response_format: str = "json",
    ) -> str:
        """Send one request, return the raw text response. Retries,
        rate limiting, and schema validation happen one layer up in
        pipeline.py — this method's only job is "prompt in, text out."
        """
        raise NotImplementedError
