#!/usr/bin/env python3
"""
_glossary_terms.py -- megosztott segédmodul a glossary.md táblázat
beolvasásához és a "védett kifejezés" ellenőrzéshez.

NEM önálló szkript -- a többi validate_*.py importálja, ha --glossary
kapcsolót kap. Ugyanabban a mappában kell maradnia, mint a validate_*.py
fájlok (Python automatikusan a futtatott szkript mappáját is felveszi
a keresési útvonalba, külön telepítés nélkül).

Táblázat oszlopai (glossary-schema.md szerint, pontosan 5 oszlop):
    Forrás kifejezés | Célnyelvi fordítás | Kategória | Bizalom | Forrás / indoklás

Kettős/kontextusfüggő bejegyzés (⚠️ jel az 5. oszlopban, vagy " / " a
2. oszlopban) -- egy soron belül több, kontextustól függő fordítást is
tartalmazhat (ld. glossary-schema.md "Vitatott/kettős bejegyzés" szakasza).
Ezeket a védett-kifejezés-ellenőrzés kihagyja, mert nem egyértelmű, melyik
forma várható egy adott előfordulásnál -- csak validate_glossary.py nézi
át őket, külön szabály szerint.

Ismert egyszerűsítés: a táblázat-sor-parser nem kezeli az escape-elt
'\\|' karaktert egy cellán belül (a kit egyik jelenlegi bejegyzésében
sem fordul elő, de ha a jövőben szükség lenne rá, itt kell bővíteni).
"""
import json
import os
import re

VALID_CATEGORIES = {"brand", "lore", "mechanic", "ui", "person"}
VALID_CONFIDENCE = {"high", "medium", "low"}
DUAL_MARKER = "⚠️"

_ROW_RE = re.compile(r"^\|(.+)\|\s*$")
_SEP_CELL_RE = re.compile(r"^:?-+:?$")


def split_row(line):
    """Egy markdown táblázat-sor cellákra bontása. None, ha a sor nem
    '| ... | ... |' alakú."""
    m = _ROW_RE.match(line.strip())
    if not m:
        return None
    return [c.strip() for c in m.group(1).split("|")]


def parse_glossary(path):
    """Beolvassa a glossary.md-t vagy glossary.json-t. Visszaadás: (entries, issues)."""
    if str(path).lower().endswith(".json"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as err:
            return [], [(1, f"Érvénytelen JSON szintaxis a szószedetben ({path}): {err}")]

        raw_list = None
        if isinstance(data, list):
            raw_list = data
        elif isinstance(data, dict):
            for key in ("terms", "glossary", "entries", "items"):
                if isinstance(data.get(key), list):
                    raw_list = data[key]
                    break

        if raw_list is None:
            return [], [(1, f"Érvénytelen JSON szószedet formátum ({path}): várt egy JSON lista vagy egy objektum 'terms'/'glossary'/'entries' listával.")]

        entries = []
        issues = []
        for lineno, item in enumerate(raw_list, start=1):
            if not isinstance(item, dict):
                issues.append((lineno, f"A szószedet #{lineno}. eleme nem objektum (dict): {item!r}"))
                continue
            src = item.get("source") or item.get("source_term", "")
            tgt = item.get("target") or item.get("target_term", "")
            justification = item.get("justification", "")
            is_dual = (
                bool(item.get("is_dual", False))
                or bool(item.get("is_disputed", False))
                or (DUAL_MARKER in justification)
                or (" / " in tgt)
            )
            if not src or not tgt:
                issues.append((lineno, f"Hiányzó 'source' vagy 'target' a(z) #{lineno}. szószedet-bejegyzésben: {item!r}"))
                continue
            entries.append({
                "lineno": lineno,
                "source": src,
                "target": tgt,
                "category": item.get("category", "mechanic"),
                "confidence": item.get("confidence", "high"),
                "justification": justification,
                "is_dual": is_dual,
            })
        return entries, issues

    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    entries = []
    issues = []
    state = "before_table"  # before_table -> header -> data

    for lineno, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")
        cells = split_row(line)
        if cells is None:
            continue

        if state == "before_table":
            # Elfogadja mind a magyar, mind az angol fejléc-konvenciót --
            # korábban csak a pontos "Forrás kifejezés" kifejezést kereste,
            # ami CSENDBEN, ÉSZREVÉTLENÜL nem talált semmit egyetlen olyan
            # projektnél sem, ahol a glossary.md fejléce angolul van írva
            # ("Source term | Target translation | ..."), pedig ez a
            # gyakoribb eset (ld. glossary.py load_glossary()-je, ami ezt
            # már eddig is helyesen, mindkét nyelven kezelte -- ez a
            # parser itt egy KÜLÖN, független beolvasó, nem osztozik azzal
            # a kóddal). Egy sor akkor számít fejlécnek, ha az első cellája
            # "forrás"/"forras"/"source"-szal kezdődik, kis-nagybetűtől
            # függetlenül.
            first_cell = cells[0].strip().lower() if cells else ""
            if first_cell.startswith(("forrás", "forras", "source")):
                state = "header"
            continue

        if state == "header":
            # ez a |---|---|...| elválasztó sor -- feltétlenül kihagyjuk
            state = "data"
            continue

        # state == "data"
        if len(cells) != 5:
            issues.append((lineno, f"{len(cells)} oszlopot találtam 5 helyett -- sor: {line!r}"))
            continue

        source, target, category, confidence, justification = cells
        entries.append({
            "lineno": lineno,
            "source": source,
            "target": target,
            "category": category,
            "confidence": confidence,
            "justification": justification,
            "is_dual": (DUAL_MARKER in justification) or (" / " in target),
        })

    return entries, issues


# Categories where an inconsistent term is a real player-facing problem,
# not just a style nitpick: a brand name translated differently in two
# places looks like a bug; the same is true for a specific ability/resource
# name (mechanic) or an invented proper noun central to the story (lore).
# ui/person are deliberately excluded -- UI strings are usually generic
# words with no single "protected" form, and person entries are handled
# by the character-voice system instead, not exact-term enforcement.
ENFORCED_CATEGORIES = {"brand", "mechanic", "lore"}


def protected_brand_terms(entries):
    """Azok a forrás-kifejezések, amik high-bizalmú, VÉDETT kategóriájú
    (ld. ENFORCED_CATEGORIES fent -- eredetileg csak 'brand' volt, bővítve
    'mechanic' és 'lore'-ra is, mert egy játékmechanika-név vagy egy
    központi lore-kifejezés következetlen fordítása ugyanúgy zavaró egy
    játékosnak, mint egy márkanévé), NEM kettős/kontextusfüggő bejegyzések,
    ÉS a célnyelvi mező kifejezetten jelzi, hogy a kifejezés nem fordítandó
    (pl. '(nem fordítandó ...)'). Ezeknek szó szerint meg kell jelenniük a
    célszövegben is, ha a forrásban szerepelnek."""
    protected = []
    for e in entries:
        if e["is_dual"]:
            continue
        if e["category"].strip() not in ENFORCED_CATEGORIES:
            continue
        if e["confidence"].split("(")[0].strip() != "high":
            continue
        target_lower = e["target"].lower()
        if "nem ford" in target_lower or "not translat" in target_lower:
            if e["source"].strip():
                protected.append(e["source"].strip())
    return protected


def check_protected_terms(pairs, entries):
    """pairs: (forrás_szöveg, cél_szöveg, címke) hármasok listája egy adott
    batch-ból. Visszaadja a talált sértések listáját (str formában, kész
    QA-üzenetként).

    A forrás-oldali illesztés szigorú szóhatáros (\\bterm\\b), mert a
    forrásban a védett kifejezés (márkanév, mechanika-név vagy lore-
    kifejezés -- ld. protected_brand_terms fent) önálló szóként/kifejezésként
    áll. A cél-oldali (hiányzik-e) ellenőrzés viszont csak a KEZDŐ szóhatárt
    követeli meg, a záró szóhatárt nem -- mert a magyar nyelv toldalékokat
    közvetlenül a szóhoz ragaszt (pl. "ExampleBrand" -> "ExampleBrandet",
    "Connect-tel"), és egy szigorú záró \\b ezt hamis pozitívként jelezné,
    holott a kifejezés helyesen, változatlanul szerepel, csak ragozva."""
    violations = []
    protected = protected_brand_terms(entries)
    if not protected:
        return violations

    compiled = [
        (term, re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE),
         re.compile(r"(?<!\w)" + re.escape(term), re.IGNORECASE))
        for term in protected
    ]

    for source_text, target_text, label in pairs:
        if not isinstance(source_text, str) or not isinstance(target_text, str):
            continue
        for term, strict_re, prefix_re in compiled:
            if strict_re.search(source_text) and not prefix_re.search(target_text):
                violations.append(
                    f"{label}: védett kifejezés ('{term}', ld. glossary.md) szerepel a "
                    f"forrásban, de szó szerint nem található a célszövegben -- ellenőrizd, "
                    f"nem lett-e véletlenül lefordítva."
                )
    return violations


def extract_glossary_arg(argv):
    """Kivesz egy opcionális '--glossary <path>' kapcsolót egy argv listából,
    hogy minden validate_*.py szkript ugyanúgy kezelje. Visszaadás:
    (maradék_argv, glossary_path_vagy_None). Nem dobja el a többi argot."""
    argv = list(argv)
    if "--glossary" in argv:
        idx = argv.index("--glossary")
        path = argv[idx + 1] if idx + 1 < len(argv) else None
        del argv[idx:idx + 2]
        return argv, path
    return argv, None


_GLOSSARY_CHECK_CACHE: dict[tuple[str, float], list] = {}


def load_glossary_for_check(path):
    """Kényelmi függvény: beolvassa a glossary.md-t vagy glossary.json-t és visszaadja az
    entries listát a check_protected_terms()-hez. Gyorsítótárazva (str(path), mtime) alapján."""
    if not path:
        return []
    try:
        norm_path = str(path)
        mtime = os.path.getmtime(norm_path)
        cache_key = (norm_path, mtime)
        if cache_key in _GLOSSARY_CHECK_CACHE:
            return _GLOSSARY_CHECK_CACHE[cache_key]

        entries, issues = parse_glossary(norm_path)
        if norm_path.lower().endswith(".json") and issues and not entries:
            raise ValueError(f"Failed to load JSON glossary '{norm_path}': {issues[0][1]}")
        _GLOSSARY_CHECK_CACHE[cache_key] = entries
        return entries
    except OSError:
        return []
