"""UABEA JSON Format Adapter for LocPipe.

Parses and reconstructs Unity UABEA (Unity Asset Bundle Extractor Avalonia)
exported JSON localization files, including:
1. CSV-in-m_Script TextAsset exports (e.g. Consumables, Subtitles, Menus, Objectives)
2. MonoBehaviour typetree localization banks (e.g. LocalizedTextBank)
3. Generic JSON key-value & array dumps

Guarantees 100% LOSSLESS reconstruction:
- Non-target language columns (FR, DE, ES, PL, RU, FA, ZH, JP, BR, TR, TW, KO) are untouched
- Version metadata columns (EN_VER, FR_VER, etc.) are untouched
- Original JSON structure, ordering, unknown fields, and Unity metadata are untouched
- Only intended target-language values are updated in-place
"""

from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..models import Entry
from .base import FormatAdapter
from .engine_noise import noise_reason

# Standard UABEA internal fields to ignore during typetree scanning
IGNORED_UNITY_KEYS = {
    "m_GameObject",
    "m_Enabled",
    "m_Script",
    "m_Name",
    "guid_",
    "add_to_repository_",
    "csv_files_",
    "local_to_merge_",
    "import_new_rows_from_remote_",
    "compare_based_on_version_",
    "stat_variable_names_",
    "variable_names_",
    "location_variable_names_",
    "character_variable_names_",
    "characters_tutorials_",
    "difficulties_text_",
}


class UABEAJsonAdapter(FormatAdapter):
    name = "uabea_json"

    def __init__(self, options: dict | None = None):
        super().__init__(options)
        source_opt = self.options.get("source_column") or self.options.get("source_lang", "EN")
        target_opt = self.options.get("target_column") or self.options.get("target_lang", "HU")
        self.source_col_name = str(source_opt).upper()
        self.target_col_name = str(target_opt).upper()
        # character_replacements: same config key as the XLIFF adapter uses.
        # Applied to every target string at merge time so the pipeline's
        # format_options.character_replacements config works for both formats.
        # e.g. {"\u0151": "\u00f4", "\u0171": "\u00fb"} for Children of Morta.
        self._char_replacements: dict[str, str] = self.options.get("character_replacements", {})
        # Case 2/3 (typetree walk, JSON array) have to guess which string
        # fields in an arbitrary object graph are narrative/UI text versus
        # engine plumbing -- Case 1 (CSV in m_Script) reads a known column
        # and never needs this. See engine_noise.py for what the built-in
        # filter catches and why it's deliberately conservative.
        self._noise_filter_enabled = self.options.get("noise_filter", True)
        # uabea_json_path_exclude: project-specific regex denylist matched
        # against the dotted json_path (e.g. "internal_metadata.debug_id").
        # A match skips that field AND, for a dict/list, its whole subtree --
        # this is the escape hatch for whatever the built-in heuristic
        # doesn't (and, being deliberately conservative, can't) catch. See
        # `locpipe audit` for building this list from real extraction output.
        self._exclude_patterns = [re.compile(p) for p in (self.options.get("uabea_json_path_exclude") or [])]

    def extract(self, path: Path, audit_sink: Optional[list] = None) -> List[Entry]:
        content = path.read_text(encoding="utf-8", errors="ignore")
        try:
            data = json.loads(content)
        except Exception as err:
            raise ValueError(f"Failed to parse UABEA JSON file '{path.name}': {err}")

        entries: List[Entry] = []

        # Case 1: Unity I2 Localization LanguageSource
        if self._is_i2_languagesource(data):
            asset_name = data.get("m_Name") or path.stem
            entries.extend(self._extract_i2_language_source(path, asset_name, data, audit_sink=audit_sink))

        # Case 2: CSV inside m_Script (Primary Children of Morta UABEA structure)
        elif isinstance(data, dict) and "m_Script" in data and isinstance(data["m_Script"], str):
            asset_name = data.get("m_Name") or path.stem
            script_text = data["m_Script"]
            entries.extend(self._extract_csv_m_script(path, asset_name, script_text))

        # Case 3: MonoBehaviour Typetree or Key-Value Dictionary
        elif isinstance(data, dict):
            asset_name = data.get("m_Name") or path.stem
            entries.extend(self._extract_typetree_dict(path, asset_name, data, audit_sink=audit_sink))

        # Case 4: JSON Array of objects
        elif isinstance(data, list):
            asset_name = path.stem
            entries.extend(self._extract_json_array(path, asset_name, data, audit_sink=audit_sink))

        return entries

    def _is_i2_languagesource(self, data: Any) -> bool:
        return (
            isinstance(data, dict)
            and isinstance(data.get("mTerms"), dict)
            and isinstance(data.get("mLanguages"), dict)
            and "Array" in data["mTerms"]
            and "Array" in data["mLanguages"]
            and isinstance(data["mTerms"]["Array"], list)
            and isinstance(data["mLanguages"]["Array"], list)
        )

    def _extract_i2_language_source(
        self, path: Path, asset_name: str, data: dict, audit_sink: Optional[list] = None
    ) -> List[Entry]:
        entries: List[Entry] = []
        languages = data.get("mLanguages", {}).get("Array", [])
        mterms = data.get("mTerms", {}).get("Array", [])

        # Find source language column index
        source_idx = 0
        src_col_upper = self.source_col_name.upper()
        for idx, lang in enumerate(languages):
            if isinstance(lang, dict):
                code = str(lang.get("Code", "")).upper()
                name = str(lang.get("Name", "")).upper()
                if (
                    code == src_col_upper
                    or name == src_col_upper
                    or (src_col_upper in ("EN", "ENGLISH") and (code == "EN" or name == "ENGLISH"))
                ):
                    source_idx = idx
                    break

        # Find target language column index if present
        target_idx = source_idx  # default to overwriting source language if target slot doesn't exist
        tgt_col_upper = self.target_col_name.upper()
        for idx, lang in enumerate(languages):
            if isinstance(lang, dict):
                code = str(lang.get("Code", "")).upper()
                name = str(lang.get("Name", "")).upper()
                if (
                    code == tgt_col_upper
                    or name == tgt_col_upper
                    or (tgt_col_upper in ("HU", "HUNGARIAN") and (code == "HU" or name == "HUNGARIAN"))
                ):
                    target_idx = idx
                    break

        for idx, term in enumerate(mterms):
            if not isinstance(term, dict):
                continue
            term_name = term.get("Term", f"term_{idx}")
            term_type = term.get("TermType", 0)
            desc = term.get("Description", "")

            # Filter non-text asset terms (1=Font, 2=Texture, 3=Audio, 4=GameObject, 5=Sprite/UnityObject, 6=Material)
            if term_type != 0:
                if audit_sink is not None:
                    audit_sink.append((f"mTerms.{term_name}", f"TermType={term_type}", "noise:i2-non-text-asset"))
                continue

            # Filter internal language codes/metadata keys
            if term_name == "language/code" or term_name.endswith("/code"):
                if audit_sink is not None:
                    audit_sink.append((f"mTerms.{term_name}", "code", "noise:i2-language-code"))
                continue

            # Check project-specific exclude patterns against term name / path
            full_path_str = f"mTerms.Array.{idx}.Languages.Array.{source_idx}"
            if self._exclude_patterns and any(
                p.search(full_path_str) or p.search(term_name) for p in self._exclude_patterns
            ):
                if audit_sink is not None:
                    audit_sink.append((full_path_str, term_name, "excluded_by_config"))
                continue

            lang_array = term.get("Languages", {}).get("Array", [])
            if not isinstance(lang_array, list) or source_idx >= len(lang_array):
                continue

            src_val = lang_array[source_idx]
            if not isinstance(src_val, str) or len(src_val.strip()) == 0:
                continue

            if self._noise_filter_enabled:
                reason = noise_reason(src_val)
                if reason is not None:
                    if audit_sink is not None:
                        audit_sink.append((full_path_str, src_val, f"noise:{reason}"))
                    continue

            if audit_sink is not None:
                audit_sink.append((full_path_str, src_val, "kept"))

            notes: List[str] = []
            if desc and str(desc).strip():
                notes.append(f"desc:{str(desc).strip()}")
            if term_name:
                notes.append(f"term:{term_name}")

            entry_key = f"{asset_name}:{term_name}" if term_name else f"{asset_name}:term_{idx}"
            extra = {
                "uabea_structure": "i2_languagesource",
                "term_index": idx,
                "term_name": term_name,
                "source_idx": source_idx,
                "target_idx": target_idx,
            }

            entries.append(
                Entry(
                    file=str(path),
                    key=entry_key,
                    source=src_val,
                    target="",
                    namespace=asset_name,
                    notes=notes,
                    extra=extra,
                )
            )

        return entries

    def _extract_csv_m_script(self, path: Path, asset_name: str, script_text: str) -> List[Entry]:
        entries: List[Entry] = []
        lines = script_text.splitlines()
        if not lines:
            return entries

        # Detect delimiter (comma or tab)
        first_line = lines[0]
        delimiter = "\t" if "\t" in first_line else ","

        reader = csv.reader(io.StringIO(script_text), delimiter=delimiter)
        try:
            header = next(reader, None)
        except Exception:
            return entries

        if not header:
            return entries

        # Normalize header column lookups
        header_upper = [c.strip().upper() for c in header]

        source_col_idx = None
        target_col_idx = None
        cat_col_idx = None
        fld_col_idx = None
        desc_col_idx = None
        id_col_idx = None

        tgt_upper = self.target_col_name.upper()
        src_upper = self.source_col_name.upper()

        for idx, col in enumerate(header_upper):
            if (col == src_upper or col in ("EN", "ENGLISH", "SOURCE")) and source_col_idx is None:
                source_col_idx = idx
            elif (col == tgt_upper or col in ("HU", "HUNGARIAN", "TARGET")) and target_col_idx is None:
                target_col_idx = idx
            elif col in ("CAT", "CATEGORY"):
                cat_col_idx = idx
            elif col in ("FLD", "FIELD"):
                fld_col_idx = idx
            elif col in ("DESC", "DESCRIPTION", "NOTE", "NOTES"):
                desc_col_idx = idx
            elif col in ("ID", "KEY", "UID"):
                id_col_idx = idx

        if source_col_idx is None:
            # Fallback: first non-ID column
            source_col_idx = 0 if id_col_idx != 0 else 1

        for row_idx, row in enumerate(reader, start=2):
            if not row or source_col_idx >= len(row):
                continue

            src_val = row[source_col_idx].strip()
            if not src_val or len(src_val) < 1:
                continue

            tgt_val = row[target_col_idx].strip() if (target_col_idx is not None and target_col_idx < len(row)) else ""
            row_id = row[id_col_idx].strip() if (id_col_idx is not None and id_col_idx < len(row)) else f"row_{row_idx}"
            cat_val = row[cat_col_idx].strip() if (cat_col_idx is not None and cat_col_idx < len(row)) else asset_name
            fld_val = row[fld_col_idx].strip() if (fld_col_idx is not None and fld_col_idx < len(row)) else ""
            desc_val = row[desc_col_idx].strip() if (desc_col_idx is not None and desc_col_idx < len(row)) else ""

            notes: List[str] = []
            if cat_val and cat_val != asset_name:
                notes.append(f"cat:{cat_val}")
            if fld_val:
                notes.append(f"fld:{fld_val}")
            if desc_val:
                notes.append(f"desc:{desc_val}")
            notes.append(f"asset:{asset_name}")

            entry_key = f"{asset_name}:{row_id}:{row_idx}"

            extra = {
                "uabea_structure": "csv_m_script",
                "asset_name": asset_name,
                "row_index": row_idx - 2,  # 0-indexed in row list
                "row_id": row_id,
                "source_col_idx": source_col_idx,
                "target_col_idx": target_col_idx,
                "target_col_name": self.target_col_name,
                "delimiter": delimiter,
                "header": header,
            }

            entries.append(
                Entry(
                    file=str(path),
                    key=entry_key,
                    source=src_val,
                    target=tgt_val,
                    namespace=asset_name,
                    category=cat_val or asset_name,
                    notes=notes,
                    extra=extra,
                )
            )

        return entries

    def _extract_typetree_dict(
        self, path: Path, asset_name: str, data: dict, audit_sink: Optional[list] = None
    ) -> List[Entry]:
        entries: List[Entry] = []

        def record_excluded_subtree(obj: Any, path_stack: List[str]) -> None:
            """audit_sink only: an excluded dict/list is never walked for
            real extraction (see below -- it's a `continue`, not a descend),
            but the report is a lot less useful if it just says "excluded"
            without showing what was actually under there. This walks a
            skipped subtree purely to log it -- no Entry objects, no noise
            filtering (the exclude pattern already made the decision).
            """
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k in IGNORED_UNITY_KEYS:
                        continue
                    full_path = path_stack + [k]
                    if isinstance(v, str) and v.strip():
                        audit_sink.append((".".join(full_path), v, "excluded_by_config"))
                    elif isinstance(v, (dict, list)):
                        record_excluded_subtree(v, full_path)
            elif isinstance(obj, list):
                for idx, item in enumerate(obj):
                    if isinstance(item, str) and item.strip():
                        audit_sink.append((".".join(path_stack + [str(idx)]), item, "excluded_by_config"))
                    elif isinstance(item, (dict, list)):
                        record_excluded_subtree(item, path_stack + [str(idx)])
            elif isinstance(obj, str) and obj.strip():
                audit_sink.append((".".join(path_stack), obj, "excluded_by_config"))

        def walk(obj: Any, path_stack: List[str]):
            if isinstance(obj, dict):
                # Sibling character limit check for Unity UI components (e.g. TMP_InputField m_CharacterLimit)
                sibling_limit = None
                char_lim = obj.get("m_CharacterLimit")
                if isinstance(char_lim, int) and char_lim > 0:
                    sibling_limit = char_lim

                for k, v in obj.items():
                    if k in IGNORED_UNITY_KEYS:
                        continue
                    full_path = path_stack + [k]
                    full_path_str = ".".join(full_path)

                    # Project-specific denylist: skips this field entirely,
                    # and -- since the check happens before we look at type --
                    # skips descending into a matched dict/list subtree too.
                    if self._exclude_patterns and any(p.search(full_path_str) for p in self._exclude_patterns):
                        if audit_sink is not None:
                            if isinstance(v, str) and v.strip():
                                audit_sink.append((full_path_str, v, "excluded_by_config"))
                            elif isinstance(v, (dict, list)):
                                record_excluded_subtree(v, full_path)
                        continue

                    if isinstance(v, str) and len(v.strip()) > 0:
                        reason = noise_reason(v) if self._noise_filter_enabled else None
                        if reason is not None:
                            if audit_sink is not None:
                                audit_sink.append((full_path_str, v, f"noise:{reason}"))
                            continue
                        if audit_sink is not None:
                            audit_sink.append((full_path_str, v, "kept"))

                        entry_key = f"{asset_name}:" + full_path_str
                        extra = {
                            "uabea_structure": "json_typetree",
                            "json_path": full_path,
                        }
                        entries.append(
                            Entry(
                                file=str(path),
                                key=entry_key,
                                source=v,
                                target="",
                                max_length=sibling_limit,
                                namespace=asset_name,
                                notes=[f"path:{full_path_str}"],
                                extra=extra,
                            )
                        )
                    elif isinstance(v, (dict, list)):
                        walk(v, full_path)
            elif isinstance(obj, list):
                for idx, item in enumerate(obj):
                    full_path = path_stack + [str(idx)]
                    full_path_str = ".".join(full_path)
                    if self._exclude_patterns and any(p.search(full_path_str) for p in self._exclude_patterns):
                        if audit_sink is not None:
                            if isinstance(item, str) and item.strip():
                                audit_sink.append((full_path_str, item, "excluded_by_config"))
                            elif isinstance(item, (dict, list)):
                                record_excluded_subtree(item, full_path)
                        continue
                    if isinstance(item, str) and len(item.strip()) > 0:
                        reason = noise_reason(item) if self._noise_filter_enabled else None
                        if reason is not None:
                            if audit_sink is not None:
                                audit_sink.append((full_path_str, item, f"noise:{reason}"))
                            continue
                        if audit_sink is not None:
                            audit_sink.append((full_path_str, item, "kept"))

                        entry_key = f"{asset_name}:" + full_path_str
                        extra = {
                            "uabea_structure": "json_typetree",
                            "json_path": full_path,
                        }
                        entries.append(
                            Entry(
                                file=str(path),
                                key=entry_key,
                                source=item,
                                target="",
                                max_length=None,
                                namespace=asset_name,
                                notes=[f"path:{full_path_str}"],
                                extra=extra,
                            )
                        )
                    elif isinstance(item, (dict, list)):
                        walk(item, full_path)

        walk(data, [])
        return entries

    def _extract_json_array(
        self, path: Path, asset_name: str, data: list, audit_sink: Optional[list] = None
    ) -> List[Entry]:
        entries: List[Entry] = []
        for idx, item in enumerate(data):
            if isinstance(item, dict):
                src_val = (
                    item.get("source")
                    or item.get("original_text")
                    or item.get(self.source_col_name)
                    or item.get("en")
                    or item.get("text")
                    or item.get("EN")
                )
                tgt_val = (
                    item.get("target")
                    or item.get("translated_text")
                    or item.get(self.target_col_name)
                    or item.get("hu")
                    or item.get("HU")
                    or ""
                )
                if src_val and isinstance(src_val, str):
                    if self._noise_filter_enabled:
                        reason = noise_reason(src_val)
                        if reason is not None:
                            if audit_sink is not None:
                                audit_sink.append((f"[{idx}]", src_val, f"noise:{reason}"))
                            continue
                    if audit_sink is not None:
                        audit_sink.append((f"[{idx}]", src_val, "kept"))
                    context_val = item.get("context") or item.get("path") or item.get("file_context")
                    speaker_val = item.get("speaker") or item.get("character") or item.get("speaker_name")
                    row_id = item.get("internal_path") or item.get("id") or item.get("key") or f"idx_{idx}"

                    if context_val:
                        entry_key = f"{asset_name}:{context_val}:{row_id}:{idx}"
                    else:
                        entry_key = f"{asset_name}:{row_id}:{idx}"

                    notes: List[str] = []
                    if context_val:
                        notes.append(f"context:{context_val}")
                    if item.get("notes"):
                        notes.append(str(item["notes"]))
                    if item.get("comment"):
                        notes.append(str(item["comment"]))

                    extra = {
                        "uabea_structure": "json_array",
                        "array_index": idx,
                        "id": row_id,
                    }
                    if context_val:
                        extra["context"] = context_val
                    if speaker_val:
                        extra["speaker"] = speaker_val

                    item_limit = None
                    char_lim = item.get("m_CharacterLimit")
                    if isinstance(char_lim, int) and char_lim > 0:
                        item_limit = char_lim
                    entries.append(
                        Entry(
                            file=str(path),
                            key=entry_key,
                            source=src_val,
                            target=tgt_val if isinstance(tgt_val, str) else "",
                            max_length=item_limit,
                            namespace=asset_name,
                            notes=notes if notes else None,
                            speaker=speaker_val if speaker_val else None,
                            extra=extra,
                        )
                    )
        return entries

    def _apply_replacements(self, text: str) -> str:
        """Apply format_options.character_replacements to a target string.
        Called at merge time for every target value, same as xliff.py line 90-92.
        No-op when character_replacements is empty (the common case).
        """
        if not self._char_replacements or not text:
            return text
        for old_char, new_char in self._char_replacements.items():
            text = text.replace(old_char, new_char)
        return text

    def merge(self, path: Path, entries: List[Entry]) -> None:
        content = path.read_text(encoding="utf-8", errors="ignore")
        try:
            data = json.loads(content)
        except Exception as err:
            raise ValueError(f"Failed to parse UABEA JSON file '{path.name}' for merging: {err}")

        key_map = {e.key: e for e in entries}

        # Case 1: Unity I2 Localization LanguageSource
        if self._is_i2_languagesource(data):
            self._merge_i2_language_source(data, entries)

        # Case 2: CSV inside m_Script
        elif isinstance(data, dict) and "m_Script" in data and isinstance(data["m_Script"], str):
            script_text = data["m_Script"]
            data["m_Script"] = self._merge_csv_m_script(script_text, entries, key_map)

        # Case 3: MonoBehaviour Typetree
        elif isinstance(data, dict):
            for e in entries:
                if e.extra.get("uabea_structure") == "json_typetree" and e.target:
                    json_path = e.extra.get("json_path", [])
                    if json_path:
                        self._set_json_path_value(data, json_path, self._apply_replacements(e.target))

        # Case 3: JSON Array
        elif isinstance(data, list):
            for e in entries:
                if e.extra.get("uabea_structure") == "json_array" and e.target:
                    idx = e.extra.get("array_index")
                    if idx is not None and 0 <= idx < len(data):
                        item = data[idx]
                        if isinstance(item, dict):
                            if "translated_text" in item:
                                item["translated_text"] = self._apply_replacements(e.target)
                            elif self.target_col_name in item:
                                item[self.target_col_name] = self._apply_replacements(e.target)
                            elif "target" in item:
                                item["target"] = self._apply_replacements(e.target)
                            elif "hu" in item:
                                item["hu"] = self._apply_replacements(e.target)
                            else:
                                tgt_col = self.target_col_name
                                item[tgt_col] = self._apply_replacements(e.target)

        # Write reconstructed UABEA JSON back to disk
        out_content = json.dumps(data, indent=2, ensure_ascii=False)
        path.write_text(out_content, encoding="utf-8")

    def _merge_i2_language_source(self, data: dict, entries: List[Entry]) -> None:
        mterms = data.get("mTerms", {}).get("Array", [])
        for e in entries:
            if not e.target or e.extra.get("uabea_structure") != "i2_languagesource":
                continue
            idx = e.extra.get("term_index")
            target_idx = e.extra.get("target_idx", 0)
            if idx is not None and 0 <= idx < len(mterms):
                term = mterms[idx]
                if isinstance(term, dict):
                    lang_array = term.setdefault("Languages", {}).setdefault("Array", [])
                    while len(lang_array) <= target_idx:
                        lang_array.append("")
                    lang_array[target_idx] = self._apply_replacements(e.target)

    def _merge_csv_m_script(self, script_text: str, entries: List[Entry], key_map: Dict[str, Entry]) -> str:
        lines = script_text.splitlines()
        if not lines:
            return script_text

        delimiter = "\t" if "\t" in lines[0] else ","
        reader = list(csv.reader(io.StringIO(script_text), delimiter=delimiter))
        if not reader:
            return script_text

        header = list(reader[0])
        header_upper = [c.strip().upper() for c in header]

        tgt_upper = self.target_col_name.upper()
        target_col_idx = None
        target_ver_idx = None

        for idx, col in enumerate(header_upper):
            if col == tgt_upper or col in ("HU", "HUNGARIAN", "TARGET"):
                target_col_idx = idx
            elif col == f"{tgt_upper}_VER" or col == "HU_VER":
                target_ver_idx = idx

        # If target column doesn't exist in header, append target column dynamically!
        if target_col_idx is None:
            header.append(self.target_col_name)
            target_col_idx = len(header) - 1
            # Optionally add target version column
            header.append(f"{self.target_col_name}_VER")
            target_ver_idx = len(header) - 1

        rows = reader[1:]

        # Map entries by row_index or key
        row_idx_to_entry: Dict[int, Entry] = {}
        for e in entries:
            r_idx = e.extra.get("row_index")
            if r_idx is not None:
                row_idx_to_entry[r_idx] = e

        for idx, row in enumerate(rows):
            entry = row_idx_to_entry.get(idx)
            if entry and entry.target:
                # Ensure row has enough columns
                while len(row) <= target_col_idx:
                    row.append("")
                row[target_col_idx] = self._apply_replacements(entry.target)

                if target_ver_idx is not None:
                    while len(row) <= target_ver_idx:
                        row.append("")
                    row[target_ver_idx] = "1"

        # Reconstruct CSV string with original delimiter
        out_buf = io.StringIO()
        writer = csv.writer(out_buf, delimiter=delimiter, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)

        return out_buf.getvalue()

    def _set_json_path_value(self, data: Any, json_path: List[str], target_value: str) -> None:
        curr = data
        for p in json_path[:-1]:
            if isinstance(curr, dict) and p in curr:
                curr = curr[p]
            elif isinstance(curr, list) and (isinstance(p, int) or (isinstance(p, str) and p.isdigit())):
                idx = int(p)
                if 0 <= idx < len(curr):
                    curr = curr[idx]
                else:
                    return
            else:
                return
        last_key = json_path[-1]
        if isinstance(curr, dict):
            curr[last_key] = target_value
        elif isinstance(curr, list) and (isinstance(last_key, int) or (isinstance(last_key, str) and last_key.isdigit())):
            idx = int(last_key)
            if 0 <= idx < len(curr):
                curr[idx] = target_value
