"""Format-agnostic quote balance/style checker.

Migrated from gamestringer/core/quote_checker.py, which only ever ran
against XLIFF files parsed with xml.etree, standalone, outside the
locpipe pipeline. The actual check (_analyze_quote_pair) never needed
XML at all -- it only needs a (source, target) string pair -- so this
version drops the XLIFF/ElementTree wrapper entirely and exposes the
same shape validators/protected_tokens.py already uses, so pipeline.py
can call it once per Entry regardless of which format adapter produced
that entry (see pipeline.py's _run_all_validators).
"""

from __future__ import annotations

from typing import List, Tuple

from ..models import Severity, ValidationIssue

_HU_QUOTE_CHARS = ("„", "»", "«")
_CURLY_QUOTE_CHARS = ("“", "‘", "’")
_JA_QUOTE_CHARS = ("「", "」", "『", "』")


def _analyze_quote_pair(src: str, tgt: str) -> List[Tuple[str, str]]:
    """Returns (issue_type, recommendation) tuples. Supports Hungarian,
    curly, straight, and Japanese corner brackets."""
    issues: List[Tuple[str, str]] = []

    src_straight_doubles = src.count('"')
    tgt_straight_doubles = tgt.count('"')

    has_hungarian_style = any(q in tgt for q in _HU_QUOTE_CHARS)
    has_curly_style = any(q in tgt for q in _CURLY_QUOTE_CHARS) or ("”" in tgt and not has_hungarian_style)
    has_ja_style_tgt = any(q in tgt for q in _JA_QUOTE_CHARS)
    has_ja_style_src = any(q in src for q in _JA_QUOTE_CHARS)

    if tgt_straight_doubles % 2 != 0:
        if src_straight_doubles % 2 == 0:
            issues.append(("unbalanced", "Unbalanced straight double quotes in target"))
    elif ("„" in tgt and "”" not in tgt and '"' not in tgt) or ("“" in tgt and "”" not in tgt and '"' not in tgt):
        if src_straight_doubles % 2 == 0 and not any(q in src for q in _HU_QUOTE_CHARS + _CURLY_QUOTE_CHARS + ("”",)):
            issues.append(("unbalanced", "Missing closing quote for opening quote in target"))

    if tgt.count("「") != tgt.count("」"):
        issues.append(("unbalanced", "Unbalanced Japanese corner brackets 「...」 in target"))
    if tgt.count("『") != tgt.count("』"):
        issues.append(("unbalanced", "Unbalanced Japanese white corner brackets 『...』 in target"))

    src_has_quotes = src_straight_doubles >= 2 or '"' in src or "“" in src or "„" in src or has_ja_style_src
    tgt_has_quotes = tgt_straight_doubles >= 1 or has_hungarian_style or has_curly_style or has_ja_style_tgt or "”" in tgt

    if src_has_quotes and not tgt_has_quotes and not any(iss[0] == "unbalanced" for iss in issues):
        issues.append(("missing_quotes", "Source contains quotes but target has no quotes"))

    # Style mismatch check only applies when source is straight ASCII quotes and target is not Japanese
    if src_straight_doubles >= 2 and not has_ja_style_src and not has_ja_style_tgt:
        if has_hungarian_style and not any(iss[0] == "unbalanced" for iss in issues):
            issues.append((
                "mismatched_style",
                "Hungarian quotes „...” used in target while source has straight quotes. "
                "Verify game font supports Hungarian quotes or convert to straight quotes \"...\".",
            ))
        elif has_curly_style and not any(iss[0] in ("unbalanced", "mismatched_style") for iss in issues):
            issues.append(("mismatched_style", "Convert curly quotes to straight quotes \"...\" for game font compatibility"))

    return issues


_SEVERITY_BY_ISSUE_TYPE = {
    "unbalanced": Severity.MAJOR,        # breaks rendering / unterminated string
    "missing_quotes": Severity.MINOR,    # cosmetic, doesn't break the string
    "mismatched_style": Severity.MINOR,  # font-support-dependent, not a hard failure alone
}


def audit_quote_pair(source: str, target: str) -> List[ValidationIssue]:
    """Format-agnostic replacement for GameStringer's check_xliff_quotes.
    Called once per Entry from pipeline.py's _run_all_validators, same
    tier as validators/protected_tokens.py's audit_entry_tokens.
    """
    if not source or not target or not target.strip():
        return []

    issues: List[ValidationIssue] = []
    for issue_type, recommendation in _analyze_quote_pair(source, target):
        issues.append(
            ValidationIssue(
                severity=_SEVERITY_BY_ISSUE_TYPE.get(issue_type, Severity.MINOR),
                code=f"QUOTE_{issue_type.upper()}",
                message=recommendation,
            )
        )
    return issues
