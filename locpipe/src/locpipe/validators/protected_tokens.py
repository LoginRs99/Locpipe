"""Protected Token Detection & Validation Module for LocPipe.

Identifies and validates game-specific protected tokens, placeholders,
markup tags, and formatting sequences that must survive translation
unchanged (e.g. @primary attack@, {comma}, {0}, <color=#FF0000>, %s, etc.).

Extraction is strictly READ-ONLY: source text is never destructively modified.
"""

from __future__ import annotations

import re
from typing import List, Tuple
from ..models import Severity, ValidationIssue, ValidationResult

# Protected token patterns
PROTECTED_PATTERNS = [
    # 1. Game marker tags (@primary attack@, @damage@, @maximum health@)
    re.compile(r"@[^@\n]+@"),
    # 2. Placeholders, Ren'Py tags, and variables ({comma}, {0}, {player}, {0:N0}, {fs|ella}, {color=#f00}, {w=1.5}, {fast})
    re.compile(r"\{[^{}\n]+\}"),
    # 3. HTML, Unity, TextMeshPro & Unreal Engine rich-text tags (<color=#FF0000>, </color>, <b>, </i>, <size=12>, <quad ... />, <RichText.Bold>)
    re.compile(r"</?[a-zA-Z0-9_\-=\#\.\s\":/'\*\?]+(?:/?>|>)"),
    # 4. Printf format specifiers (%s, %d, %f, %.2f, %1$s, %2$d, %02d, %lld, %zu, %X, %%)
    re.compile(r"%(?:%|(?:\d+\$)?[-+0 #]*(?:\d+|\*)?(?:\.(?:\d+|\*))?(?:hh|h|ll|l|L|z|j|t)?[sdfuxXgGcping])"),
    # 5. Game tag bracket identifiers, Ren'Py expressions, and commands ([ITEM_ID], [KEY_NAME], [style=accent], [BTN:L2 ], [flag!q], [player.name])
    re.compile(r"\[[A-Za-z0-9_\-\:\.=#\!]+(?:\s+[A-Za-z0-9_\-\:\.=#\!]+)*\s*\]"),
    # 6. BBCode formatting tags ([b], [/b], [color=red], [/color], [url=...], [/url])
    re.compile(r"\[/?[A-Za-z0-9_\-]+(?:=[^\]\n]+)?\]"),
    # 7. RPG Maker / Visual Novel / game engine escape sequences (\C[1], \V[2], \I[3], \G, \!, \., \|, \>, \<, \{, \})
    re.compile(r"\\[A-Za-z]+\[\d+\]|\\[Gg]|\\[\.\!\^\|\>\<\{\}\$]"),
    # 8. Escaped string characters (\n, \r, \t)
    re.compile(r"\\n|\\r|\\t"),
    # 9. Ruby / Furigana markup (<ruby>, </ruby>, <rt>, </rt>, [ruby], [/ruby])
    re.compile(r"</?ruby(?:=[^>]+)?>|</?rt>|</?rp>|\[/?ruby(?:=[^\]]+)?\]|\[/?rt\]"),
    # 10. Keyboard shortcuts (Ctrl+S, Alt+F4, Ctrl+Shift+P)
    re.compile(r"\b(?:Ctrl|Alt|Shift|Cmd|Meta)\+[A-Za-z0-9\+]+"),
    # 11. Naninovel inline expression slot ($@)
    re.compile(r"\$@"),
]


def extract_protected_tokens(text: str) -> List[str]:
    """Extract all protected game tokens from a string. Read-only, no side effects."""
    if not text:
        return []

    tokens: List[str] = []
    seen = set()

    for pattern in PROTECTED_PATTERNS:
        for match in pattern.finditer(text):
            tok = match.group(0)
            if tok not in seen:
                seen.add(tok)
                tokens.append(tok)

    return tokens


def validate_protected_tokens(source: str, target: str) -> Tuple[List[str], List[str]]:
    """Validate that all protected tokens present in source are preserved in target.

    Returns:
        (missing_tokens, modified_tokens)
    """
    if not source:
        return [], []

    source_tokens = extract_protected_tokens(source)
    if not source_tokens:
        return [], []

    # If source uses escaped newlines (\\n) and target uses real newlines (\n), or vice versa,
    # normalize target for token extraction so newline tokens aren't falsely reported missing.
    target_for_tokens = target
    if "\\n" in source and "\n" in target:
        target_for_tokens = target_for_tokens.replace("\r\n", "\\n").replace("\n", "\\n")
    elif "\n" in source and "\\n" in target:
        target_for_tokens = target_for_tokens.replace("\\n", "\n")

    target_tokens = extract_protected_tokens(target_for_tokens)
    target_token_set = set(target_tokens)

    missing: List[str] = []
    modified: List[str] = []

    for src_tok in source_tokens:
        # Gender slot markers ({ms|...}, {fs|...}, {mp|...}, {fp|...}, {n|...})
        m_gender = re.match(r"^\{(ms|fs|mp|fp|n)\|", src_tok)
        if m_gender:
            slot_prefix = f"{{{m_gender.group(1)}|"
            if slot_prefix not in target_for_tokens:
                missing.append(src_tok)
            continue

        if src_tok not in target_token_set:
            # Check if token was partially modified/translated (e.g. @primary attack@ -> @támadás@)
            if src_tok.startswith("@") and src_tok.endswith("@"):
                if re.search(r"@[^@\n]+@", target_for_tokens):
                    modified.append(src_tok)
                else:
                    missing.append(src_tok)
            elif src_tok.startswith("{") and src_tok.endswith("}"):
                inner = src_tok[1:-1]
                if re.search(r"\{[^{}\n]+\}", target_for_tokens) and not re.search(r"\{" + re.escape(inner) + r"\}", target_for_tokens):
                    modified.append(src_tok)
                else:
                    missing.append(src_tok)
            elif src_tok.startswith("\\"):
                if re.search(re.escape(src_tok[0:2]), target_for_tokens):
                    modified.append(src_tok)
                else:
                    missing.append(src_tok)
            elif src_tok.startswith("[") and src_tok.endswith("]"):
                if re.search(r"\[[A-Za-z0-9_\-\:\.=#\s]+\]", target_for_tokens):
                    modified.append(src_tok)
                else:
                    missing.append(src_tok)
            else:
                missing.append(src_tok)

    return missing, modified


def audit_entry_tokens(source: str, target: str) -> List[ValidationIssue]:
    """Perform a deterministic token audit on a source/target pair.

    Returns a list of ValidationIssue objects.
    """
    issues: List[ValidationIssue] = []
    if not source or not target:
        return issues

    missing, modified = validate_protected_tokens(source, target)

    for tok in missing:
        issues.append(
            ValidationIssue(
                severity=Severity.CRITICAL,
                code="PROTECTED_TOKEN_MISSING",
                message=f"Protected game token '{tok}' missing from translation.",
            )
        )

    for tok in modified:
        issues.append(
            ValidationIssue(
                severity=Severity.CRITICAL,
                code="PROTECTED_TOKEN_MODIFIED",
                message=f"Protected game token '{tok}' was improperly modified/translated.",
            )
        )

    # Count check for exact matches
    src_tokens = extract_protected_tokens(source)
    for tok in src_tokens:
        if tok in missing or tok in modified:
            continue
        # Skip count check for gender markers where internal content varies
        if re.match(r"^\{(ms|fs|mp|fp|n)\|", tok):
            continue
        # Skip ruby markup tags if translating between different scripts/languages where ruby isn't duplicated
        if tok.startswith(("<ruby", "</ruby", "<rt", "</rt", "[ruby", "[/ruby")):
            continue
        # Skip count check for line breaks and whitespace escapes: line wrapping in target language naturally varies
        if tok in ("\\n", "\\r", "\\t", "\n", "\r", "\t"):
            continue
        c_src = source.count(tok)
        c_tgt = target.count(tok)
        if c_src != c_tgt:
            issues.append(
                ValidationIssue(
                    severity=Severity.MAJOR,
                    code="PROTECTED_TOKEN_COUNT_MISMATCH",
                    message=f"Protected token '{tok}' count mismatch: source has {c_src}, target has {c_tgt}.",
                )
            )

    # Accelerator / access key check: &File, &Open, Save &As
    has_src_acc = bool(re.search(r"(?<!&)&[A-Za-z0-9](?!&)", source))
    has_tgt_acc = bool(re.search(r"(?<!&)&[A-Za-z0-9áéíóöőúüűÁÉÍÓÖŐÚÜŰ](?!&)", target))
    if has_src_acc and not has_tgt_acc:
        issues.append(
            ValidationIssue(
                severity=Severity.MAJOR,
                code="ACCESS_KEY_MISSING",
                message="Source contains an access key accelerator ('&'), but none was found in translation.",
            )
        )

    return issues


class TokenMasker:
    """Deterministic token masking and unmasking engine.
    Provides mathematical assurance that game code tokens, tags, and formatting sequences
    cannot be altered, corrupted, or mistranslated by an LLM.
    """
    SENTINEL_PREFIX = "⟦T"
    SENTINEL_SUFFIX = "⟧"
    SENTINEL_RE = re.compile(r"⟦T(\d+)⟧")

    @classmethod
    def mask(cls, text: str) -> tuple[str, dict[str, str]]:
        """Replaces all protected game tokens with immutable sentinels (⟦T0⟧, ⟦T1⟧, ...).
        Returns:
            (masked_text, token_map)
        """
        if not text:
            return text, {}

        tokens = extract_protected_tokens(text)
        if not tokens:
            return text, {}

        token_map: dict[str, str] = {}
        masked = text

        # Sort tokens by length descending to prevent shorter token substring replacement
        for idx, tok in enumerate(sorted(tokens, key=len, reverse=True)):
            sentinel = f"{cls.SENTINEL_PREFIX}{idx}{cls.SENTINEL_SUFFIX}"
            token_map[sentinel] = tok
            masked = masked.replace(tok, sentinel)

        return masked, token_map

    @classmethod
    def unmask(cls, text: str, token_map: dict[str, str]) -> tuple[str, list[str]]:
        """Restores all sentinels strictly to their original byte values.
        Returns:
            (unmasked_text, missing_sentinels)
        """
        if not text or not token_map:
            return text, []

        unmasked = text
        missing: list[str] = []

        for sentinel, original_tok in token_map.items():
            if sentinel in unmasked:
                unmasked = unmasked.replace(sentinel, original_tok)
            else:
                missing.append(original_tok)

        return unmasked, missing
