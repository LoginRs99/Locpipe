"""Deterministic pseudo-localization provider.

Simulates Hungarian-scale text expansion (~30%) and accented glyph rendering
(á, é, í, ő, ű) to catch UI overflow, clipping, and font-encoding bugs before
spending real API tokens, while strictly preserving protected tokens and placeholders.
"""

from __future__ import annotations

import json
from typing import Optional

from ..validators.protected_tokens import PROTECTED_PATTERNS
from .base import TranslationProvider

_ACCENT_MAP = {
    'a': 'á', 'A': 'Á',
    'e': 'é', 'E': 'É',
    'i': 'í', 'I': 'Í',
    'o': 'ő', 'O': 'Ő',
    'u': 'ű', 'U': 'Ű',
}


def _accentuate(text: str) -> str:
    return ''.join(_ACCENT_MAP.get(c, c) for c in text)


def pseudolocalize_text(text: str) -> str:
    """Transform a source string with accented vowels and ~30% length expansion,
    leaving all protected tokens/placeholders strictly unchanged.
    """
    if not text:
        return text

    # Find protected token spans
    spans = []
    for pattern in PROTECTED_PATTERNS:
        for m in pattern.finditer(text):
            spans.append((m.start(), m.end()))

    if not spans:
        merged_spans = []
    else:
        spans.sort()
        merged_spans = [spans[0]]
        for start, end in spans[1:]:
            last_start, last_end = merged_spans[-1]
            if start < last_end:
                merged_spans[-1] = (last_start, max(last_end, end))
            else:
                merged_spans.append((start, end))

    pieces = []
    last_idx = 0
    for s_start, s_end in merged_spans:
        if s_start > last_idx:
            pieces.append(_accentuate(text[last_idx:s_start]))
        pieces.append(text[s_start:s_end])
        last_idx = s_end
    if last_idx < len(text):
        pieces.append(_accentuate(text[last_idx:]))

    transformed = ''.join(pieces)

    # 30% expansion padding
    pad_len = max(1, int(len(text) * 0.30))
    pad_chars = 'áéőű'
    pad = ''.join(pad_chars[i % len(pad_chars)] for i in range(pad_len))

    return f'[{transformed} ~{pad}~]'


class PseudoLocProvider(TranslationProvider):
    """Deterministic pseudo-localization provider that does not persist to TM."""

    persists_to_tm = False

    async def complete(
        self,
        system_prompt: str,
        user_payload: str,
        *,
        max_tokens: int = 8192,
        effort: Optional[str] = None,
    ) -> str:
        payload_json = user_payload.split("\n\n(Your previous response was invalid:")[0]
        parsed = json.loads(payload_json)

        if isinstance(parsed, list):  # bulk-translation shape: [{"id", "source", ...}, ...]
            out = [
                {'id': item['id'], 'translation': pseudolocalize_text(item['source'])}
                for item in parsed
            ]
            return json.dumps(out, ensure_ascii=False)

        if isinstance(parsed, dict) and 'items' in parsed:  # review shape
            out = [
                {
                    'key': item['key'],
                    'translation': pseudolocalize_text(item['source']),
                    'flag_for_human': False,
                    'reason': '',
                }
                for item in parsed['items']
            ]
            return json.dumps(out, ensure_ascii=False)

        raise ValueError(f'PseudoLocProvider got an unrecognized payload shape: {type(parsed).__name__}')
