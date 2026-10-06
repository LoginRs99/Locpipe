"""One call — run_validator(format_name, path, glossary_path) — over
every format that currently has a working adapter (see
adapters/registry.py). Formats without an extract/merge adapter yet
(renpy, ue3) have no validator here either — a validator with no
adapter behind it can never actually run in `locpipe run`, so carrying
it would just be dead weight. Add both together when one of those
formats is actually needed.

Two integration modes, and this is honest about which formats use
which, rather than pretending uniformity that doesn't exist yet:

  DIRECT IMPORT (validate_file(path, glossary_entries) -> 4 lists):
    generic_kv, po_gettext, ue4_5_po, weblate_xliff
    These already expose a clean function; call it in-process.
    ue4_5_po layers one Unreal-specific check (argument-modifier
    skeleton survival -- see validate_ue4_5_po.py) on top of the same
    validate_po_gettext.validate_file() plain po_gettext uses, rather
    than duplicating the base PO checks.

  SUBPROCESS (only main(argv) exists, needs format-specific flags the
  shared function signature doesn't have room for, e.g. Unity's
  --source/--target column names):
    unity
    Shells out to the original script and parses its
    "-- LABEL (N) --" stdout convention back into ValidationIssue
    objects.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from ..models import Severity, ValidationIssue, ValidationResult
from . import validate_generic_kv, validate_po_gettext, validate_ue4_5_po, validate_weblate_xliff, validate_uabea_json, validate_naninovel
from .glossary_terms import load_glossary_for_check

_VALIDATORS_DIR = Path(__file__).parent

_DIRECT_IMPORT = {
    "generic_kv": validate_generic_kv,
    "po_gettext": validate_po_gettext,
    "ue4_5_po": validate_ue4_5_po,
    "weblate_xliff": validate_weblate_xliff,
    "xliff": validate_weblate_xliff,
    "uabea_json": validate_uabea_json,
    "naninovel": validate_naninovel,
}

_SUBPROCESS_SCRIPT = {
    "unity": "validate_unity_csv.py",
}

_SECTION_RE = re.compile(r"^-- (CRITICAL|MAJOR|MINOR|INFO) \((\d+)\) --$")
_ITEM_RE = re.compile(r"^\s*-\s+(.*)$")


def _parse_stdout_sections(stdout: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"CRITICAL": [], "MAJOR": [], "MINOR": [], "INFO": []}
    current: Optional[str] = None
    for line in stdout.splitlines():
        m = _SECTION_RE.match(line.strip())
        if m:
            current = m.group(1)
            continue
        if current:
            m2 = _ITEM_RE.match(line)
            if m2:
                out[current].append(m2.group(1))
    return out


def run_validator(
    format_name: str,
    path: Path,
    glossary_path: Optional[Path] = None,
    entry_key: str = "",
    format_kwargs: Optional[dict] = None,
) -> ValidationResult:
    format_kwargs = format_kwargs or {}
    result = ValidationResult(entry_key=entry_key or str(path))

    if format_name in _DIRECT_IMPORT:
        module = _DIRECT_IMPORT[format_name]
        glossary_entries = load_glossary_for_check(str(glossary_path)) if glossary_path else []
        critical, major, minor, info = module.validate_file(str(path), glossary_entries)
        for sev, items in (
            (Severity.CRITICAL, critical),
            (Severity.MAJOR, major),
            (Severity.MINOR, minor),
            (Severity.INFO, info),
        ):
            for msg in items:
                getattr(result, sev.value.lower()).append(
                    ValidationIssue(severity=sev, code=format_name, message=msg)
                )
        return result

    if format_name in _SUBPROCESS_SCRIPT:
        if format_name == "unity":
            missing = [k for k in ("source_col", "target_col") if k not in format_kwargs]
            if missing:
                raise ValueError(
                    f"format 'unity' requires {missing} in project.yaml's "
                    f"format_kwargs, but {'it is' if len(missing) == 1 else 'they are'} missing"
                )
            try:
                from . import validate_unity_csv
                glossary_entries = load_glossary_for_check(str(glossary_path)) if glossary_path else []
                critical, major, minor, info = validate_unity_csv.validate_file(
                    str(path),
                    glossary_entries,
                    source_col=format_kwargs["source_col"],
                    target_col=format_kwargs["target_col"],
                )
                for sev, items in (
                    (Severity.CRITICAL, critical),
                    (Severity.MAJOR, major),
                    (Severity.MINOR, minor),
                    (Severity.INFO, info),
                ):
                    for msg in items:
                        getattr(result, sev.value.lower()).append(
                            ValidationIssue(severity=sev, code=format_name, message=msg)
                        )
                return result
            except Exception:
                pass  # Fall back to subprocess script below

        script = _VALIDATORS_DIR / _SUBPROCESS_SCRIPT[format_name]
        argv = [str(path)]
        if format_name == "unity":
            argv += ["--source", format_kwargs["source_col"], "--target", format_kwargs["target_col"]]
        if glossary_path:
            argv += ["--glossary", str(glossary_path)]

        proc = subprocess.run(
            [sys.executable, str(script), *argv],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        sections = _parse_stdout_sections(proc.stdout)
        for sev_name, msgs in sections.items():
            sev = Severity(sev_name)
            for msg in msgs:
                getattr(result, sev.value.lower()).append(
                    ValidationIssue(severity=sev, code=format_name, message=msg)
                )
        if proc.returncode != 0 and not result.critical and not result.major:
            err_msg = proc.stderr.strip() or proc.stdout.strip() or f"validator returned exit code {proc.returncode}"
            result.critical.append(
                ValidationIssue(
                    severity=Severity.CRITICAL,
                    code=format_name,
                    message=f"validator failed (exit {proc.returncode}): {err_msg[:500]}",
                )
            )
        elif proc.returncode not in (0, 1):
            result.critical.append(
                ValidationIssue(
                    severity=Severity.CRITICAL,
                    code=format_name,
                    message=f"validator crashed (exit {proc.returncode}): {proc.stderr.strip()[:500]}",
                )
            )
        return result

    raise ValueError(f"No validator registered for format '{format_name}'")
