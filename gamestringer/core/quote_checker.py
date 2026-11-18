"""
Quote Consistency Checker for GameStringer CLI.

Analyzes XLIFF files to detect quote mismatches, unbalanced opening/closing quote pairs,
and font-incompatible quote styles (e.g., Hungarian „...” or curly “...” vs. straight "...").
"""

import os
import json
import re
import xml.etree.ElementTree as ET
from typing import List, Dict, Any, Optional
from gamestringer.core.logger import logger


def _parse_xliff_units(xliff_path: str) -> List[tuple]:
    if not os.path.exists(xliff_path):
        raise FileNotFoundError(f"XLIFF file not found: {xliff_path}")
    tree = ET.parse(xliff_path)
    root = tree.getroot()
    units = []
    for tu in root.iter():
        if tu.tag.endswith("trans-unit") or tu.tag == "trans-unit":
            tu_id = tu.attrib.get("id", "")
            src = ""
            tgt = ""
            for child in tu:
                tag = child.tag.split("}")[-1]
                if tag == "source":
                    src = child.text or ""
                elif tag == "target":
                    tgt = child.text or ""
            units.append((tu_id, src, tgt))
    return units


def check_xliff_quotes(xliff_path: str, output_json: Optional[str] = None) -> Dict[str, Any]:
    """
    Parse an XLIFF file and analyze source and target text pairs for quote inconsistencies.

    Returns dict with keys: 'issues', 'total_checked', 'issues_found'.
    """
    units = _parse_xliff_units(xliff_path)
    issues = []
    total_checked = 0

    for tu_id, src, tgt in units:
        if not src or not tgt or not tgt.strip():
            continue

        total_checked += 1
        unit_issues = _analyze_quote_pair(src, tgt)
        for issue_type, rec in unit_issues:
            issues.append({
                "id": tu_id,
                "source": src[:100],
                "target": tgt[:100],
                "issue": issue_type,
                "recommendation": rec
            })

    result = {
        "issues": issues,
        "total_checked": total_checked,
        "issues_found": len(issues)
    }

    if output_json:
        with open(output_json, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        logger.info(f"Quote check report saved to: {output_json}")

    return result


def _analyze_quote_pair(src: str, tgt: str) -> List[tuple]:
    """Analyze a single (source, target) string pair for quote issues."""
    issues = []

    src_straight_doubles = src.count('"')
    tgt_straight_doubles = tgt.count('"')

    has_hungarian_style = any(q in tgt for q in ("„", "»", "«"))
    has_curly_style = any(q in tgt for q in ("“", "‘", "’")) or ("”" in tgt and not has_hungarian_style)

    # 1. Unbalanced Quotes Detection
    if tgt_straight_doubles % 2 != 0:
        issues.append(("unbalanced", "Unbalanced straight double quotes in target"))
    elif ("„" in tgt and "”" not in tgt and '"' not in tgt) or ("“" in tgt and "”" not in tgt and '"' not in tgt):
        issues.append(("unbalanced", "Missing closing quote for opening quote in target"))

    # 2. Missing Quotes Detection
    src_has_quotes = src_straight_doubles >= 2 or '"' in src or "“" in src or "„" in src
    tgt_has_quotes = tgt_straight_doubles >= 1 or has_hungarian_style or has_curly_style or "”" in tgt

    if src_has_quotes and not tgt_has_quotes and not any(iss[0] == "unbalanced" for iss in issues):
        issues.append(("missing_quotes", "Source contains quotes but target has no quotes"))

    # 3. Mismatched Quote Style Detection
    if src_straight_doubles >= 2 and has_hungarian_style and not any(iss[0] == "unbalanced" for iss in issues):
        issues.append(("mismatched_style", "Hungarian quotes „...” used in target while source has straight quotes. Verify game font supports Hungarian quotes or convert to straight quotes \"...\"."))
    elif src_straight_doubles >= 2 and has_curly_style and not any(iss[0] in ("unbalanced", "mismatched_style") for iss in issues):
        issues.append(("mismatched_style", "Convert curly quotes to straight quotes \"...\" for game font compatibility"))

    return issues
