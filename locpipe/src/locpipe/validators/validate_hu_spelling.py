"""
Hungarian Spellcheck Validator for LocPipe.

Performs cheap, deterministic spellchecking on Hungarian target translations
using pyspellchecker with a bundled Hungarian frequency dictionary and cautious
agglutinative suffix handling.
Only ever produces MINOR severity issues (hints for human review, never blocking gates).
Glossary entries and protected tokens are excluded from spellchecking.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
from pathlib import Path
import re
from typing import Any, List, Optional, Set, Tuple

from ..models import Severity, ValidationIssue, ValidationResult
from .protected_tokens import extract_protected_tokens

_SPELLCHECKER_INSTANCE = None
_HU_WORDLIST_PATH = Path(__file__).parent / "hu_words.json.gz"
_WORD_RE = re.compile(r"[a-záéíóöőúüűA-ZÁÉÍÓÖŐÚÜŰ]+", re.UNICODE)

# Common Hungarian inflectional and derivational suffixes for cautious stem recovery
HU_SUFFIXES = [
    "ban", "ben", "ból", "ből", "ba", "be",
    "nak", "nek", "val", "vel", "tal", "tel",
    "ról", "ről", "ra", "re",
    "tól", "től", "hoz", "hez", "höz",
    "nál", "nél", "ért", "ként", "kor",
    "on", "en", "ön", "ul", "ül",
    "ot", "et", "öt", "at", "t",
    "ok", "ek", "ök", "ak", "k",
    "om", "am", "em", "öm", "od", "ad", "ed", "öd",
    "ja", "je", "unk", "ünk", "tok", "tek", "tök",
    "juk", "jük", "uk", "ük",
    "i", "s", "ig"
]


def _get_spellchecker():
    global _SPELLCHECKER_INSTANCE
    if _SPELLCHECKER_INSTANCE is not None:
        return _SPELLCHECKER_INSTANCE

    try:
        from spellchecker import SpellChecker
    except ImportError:
        return None

    spell = SpellChecker(language=None)
    if _HU_WORDLIST_PATH.exists():
        try:
            with gzip.open(_HU_WORDLIST_PATH, "rt", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    spell.word_frequency.load_words(data.keys())
                elif isinstance(data, list):
                    spell.word_frequency.load_words(data)
        except Exception:
            pass

    _SPELLCHECKER_INSTANCE = spell
    return _SPELLCHECKER_INSTANCE


_WORD_KNOWN_CACHE: dict[str, bool] = {}


def is_hu_word_known(word: str, spell) -> bool:
    """Check if a word (or its plausible Hungarian stem) is in the dictionary."""
    w = word.lower()
    if w in _WORD_KNOWN_CACHE:
        return _WORD_KNOWN_CACHE[w]

    if not spell.unknown([w]):
        _WORD_KNOWN_CACHE[w] = True
        return True

    # Try suffix stripping with vowel alternations (e.g. almá-t -> alma, szótár-ban -> szótár)
    for suf in sorted(HU_SUFFIXES, key=lambda x: -len(x)):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            stem = w[:-len(suf)]
            if not spell.unknown([stem]):
                _WORD_KNOWN_CACHE[w] = True
                return True
            if stem.endswith("á"):
                if not spell.unknown([stem[:-1] + "a"]):
                    _WORD_KNOWN_CACHE[w] = True
                    return True
            elif stem.endswith("é"):
                if not spell.unknown([stem[:-1] + "e"]):
                    _WORD_KNOWN_CACHE[w] = True
                    return True

    _WORD_KNOWN_CACHE[w] = False
    return False


def _extract_target_strings(path: Path, target_lang: str = "hu") -> List[str]:
    """Extract translated target strings from a file based on its extension/content."""
    if not path.exists():
        return []

    targets: List[str] = []
    suffix = path.suffix.lower()

    if suffix == ".json":
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
            # Case 1: CSV in m_Script
            if isinstance(data, dict) and "m_Script" in data and isinstance(data["m_Script"], str):
                reader = csv.reader(io.StringIO(data["m_Script"]))
                header = next(reader, None)
                if header:
                    target_idx = None
                    target_col = target_lang.upper()
                    for idx, col in enumerate(header):
                        col_u = col.strip().upper()
                        if col_u in (target_col, "TARGET", "HU", "HUNGARIAN"):
                            target_idx = idx
                            break
                    if target_idx is not None:
                        for row in reader:
                            if target_idx < len(row):
                                val = row[target_idx].strip()
                                if val:
                                    targets.append(val)
            # Case 2: Typetree or generic dict / array
            elif isinstance(data, dict):
                def walk(obj):
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if isinstance(v, str) and len(v.strip()) > 0:
                                targets.append(v)
                            elif isinstance(v, (dict, list)):
                                walk(v)
                    elif isinstance(obj, list):
                        for item in obj:
                            walk(item)
                walk(data)
            elif isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        tgt = item.get("target") or item.get("hu") or item.get("HU") or item.get("translation") or item.get("text")
                        if tgt and isinstance(tgt, str):
                            targets.append(tgt)
                    elif isinstance(item, str):
                        targets.append(item)
        except Exception:
            pass

    elif suffix == ".po":
        try:
            import polib
            po = polib.pofile(str(path))
            for entry in po:
                if entry.msgstr.strip():
                    targets.append(entry.msgstr.strip())
        except Exception:
            pass

    return targets


def _clean_text_for_spellcheck(text: str) -> str:
    """Remove protected tokens, markup, and URLs prior to tokenizing words."""
    cleaned = text
    for tok in extract_protected_tokens(text):
        cleaned = cleaned.replace(tok, " ")

    # Strip HTML-like tags if any remain
    cleaned = re.sub(r"</?[^>]+>", " ", cleaned)
    # Strip URL-like patterns
    cleaned = re.sub(r"https?://\S+", " ", cleaned)
    return cleaned


def build_glossary_word_whitelist(glossary_entries: Optional[List[Any]]) -> Set[str]:
    """Collect every source/target word from a project's glossary into a
    lowercase set, so glossary terms -- which may legitimately not be in a
    general-purpose Hungarian dictionary (lore names, coined terms) -- are
    never flagged as misspellings. Shared by validate_file (whole-file,
    legacy entry point, kept for standalone callers) and spellcheck_target
    (per-Entry, called from pipeline.py's _run_all_validators).
    """
    glossary_words: Set[str] = set()
    if not glossary_entries:
        return glossary_words
    for item in glossary_entries:
        if isinstance(item, dict):
            src = item.get("source", "")
            tgt = item.get("target", "")
        elif isinstance(item, (list, tuple)):
            src = item[0] if len(item) > 0 else ""
            tgt = item[1] if len(item) > 1 else ""
        else:
            src = getattr(item, "source", "")
            tgt = getattr(item, "target", "")
        for w in _WORD_RE.findall(src):
            glossary_words.add(w.lower())
        for w in _WORD_RE.findall(tgt):
            glossary_words.add(w.lower())
    return glossary_words


def spellcheck_target(target_text: str, glossary_words: Optional[Set[str]] = None) -> List[ValidationIssue]:
    """Per-string Hungarian spellcheck, MINOR-only, never blocking -- same
    contract as audit_entry_tokens/audit_quote_pair. Called once per Entry
    from pipeline.py's _run_all_validators. Replaces hu_spelling's former
    life as an unreachable pseudo-format: it had no matching adapter in
    adapters/registry.py, so `format: hu_spelling` in project.yaml could
    never actually run through `locpipe run`.
    """
    issues: List[ValidationIssue] = []
    if not target_text or not target_text.strip():
        return issues

    spell = _get_spellchecker()
    if spell is None:
        return issues

    glossary_words = glossary_words or set()
    cleaned = _clean_text_for_spellcheck(target_text)
    words = _WORD_RE.findall(cleaned)

    candidates = [
        w for w in words
        if len(w) > 1 and w.lower() not in glossary_words and not w.isupper()
    ]

    for word in candidates:
        if not is_hu_word_known(word, spell):
            preview = target_text.replace("\n", " ")
            if len(preview) > 50:
                preview = preview[:47] + "..."
            issues.append(
                ValidationIssue(
                    severity=Severity.MINOR,
                    code="HU_SPELLING",
                    message=f"Possible Hungarian misspelling: '{word}' in '{preview}'",
                )
            )
    return issues


def validate_file(
    path_str: str,
    glossary_entries: Optional[List[Any]] = None,
    target_lang: str = "hu",
) -> Tuple[List[str], List[str], List[str], List[str]]:
    """Whole-file entry point, kept for any standalone/script caller. No
    longer wired into validators/registry.py's format dispatch -- see
    spellcheck_target for the per-Entry path pipeline.py actually uses.
    Returns (critical, major, minor, info).
    """
    critical: List[str] = []
    major: List[str] = []
    minor: List[str] = []
    info: List[str] = []

    if target_lang.lower() != "hu":
        return critical, major, minor, info

    glossary_words = build_glossary_word_whitelist(glossary_entries)
    path = Path(path_str)
    targets = _extract_target_strings(path, target_lang=target_lang)

    for target_text in targets:
        for issue in spellcheck_target(target_text, glossary_words):
            minor.append(issue.message)

    return critical, major, minor, info
