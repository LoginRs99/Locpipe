#!/usr/bin/env python3
"""
validate_unity_csv.py -- mechanikus integritas-ellenorzo Unity
Localization csomag CSV exporthoz (format-unity.md).

FONTOS: Unity-projekteknel ketfele export is elofordulhat -- ez a
szkript csak a hivatalos Localization csomag CSV formatumat ellenorzi
(Key, Id, <locale-oszlopok>). Ha a fajl fejlece nem ilyen, a szkript
figyelmeztet, es javasolja a generikus adapter/kulon adapter hasznalatat.

Ellenorzesek:
  1. Fejlec-formatum felismerese (Key/Id oszlop + a megadott forras- es
     celnyelvi oszlopok megletenek ellenorzese).
  2. Duplikalt Key.
  3. Smart Format placeholder-szeru tokenek ({0}, {Name}, {} onhivatkozas,
     beleertve az egymasba agyazott plural/list formattereket is, pl.
     '{0:plural:1 elem|{0} elem}') darabszam-erzekeny egyezese forras- es
     celoszlop kozott -- ez NEM valodi SmartFormat-parser, csak balanszolt
     zarojel-part es azonossag-tokenek (index/valtozonev) multihalmazat
     hasonlit ossze, ld. format-unity.md a pontos szemantikahoz. Korabban
     ismert korlatozas volt, hogy egy beagyazott onhivatkozas elfedte a
     kulso formatter-azonositot -- ez javitva.

Opcionalis --glossary <glossary.md>: minden sort ellenoriz a szoszedet
high-bizalmu 'brand' bejegyzesei ellen (ld. _glossary_terms.py).

Hasznalat:
    python3 validate_unity_csv.py <fajl.csv> --source en --target hu [--glossary <glossary.md>]

Kilepesi kod: 1, ha talalt CRITICAL vagy MAJOR problemat, kulonben 0.
"""
import csv
import sys
from collections import Counter

try:
    from .glossary_terms import check_protected_terms, extract_glossary_arg, load_glossary_for_check
    from .html_tags import check_html_tags
except (ImportError, ValueError):
    from pathlib import Path
    _here = Path(__file__).resolve().parent
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    from glossary_terms import check_protected_terms, extract_glossary_arg, load_glossary_for_check
    from html_tags import check_html_tags


def extract_balanced_spans(text):
    """Kinyeri a legkülső, balanszolt {...} tartományokat -- helyesen kezeli
    az egymásba ágyazott SmartFormat kifejezéseket (pl. a plural/list
    formatterek gyakran tartalmaznak beágyazott {} önhivatkozást, pl.
    '{0:plural:1 elem|{0} elem}'). Egy naiv, nem-ágyazott regex ezt csak a
    belső '{}'-ként ismerné fel és elveszítené a külső {0:...} azonosítót --
    ez korábban dokumentált, ismert korlátozás volt a docstringben, most már
    javítva, mélységszámlálással."""
    spans = []
    depth = 0
    start = None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    spans.append(text[start:i + 1])
                    start = None
    return spans


def token_identity(span):
    """Egy {...} tartományból kinyeri az összehasonlításra használt
    'azonosságot': a nyitó kapocs utáni, első ':' vagy '|' előtti részt
    (jellemzően az index vagy változónév; {} önhivatkozásnál ez üres
    string). A tényleges ágszöveget (pl. angol 'item'/'items' vs. magyar
    'elem'/'elem') SZÁNDÉKOSAN nem hasonlítjuk -- az fordítandó tartalom,
    nyelvenként jogosan eltérhet (ld. lang-style.md többes szám-szabálya:
    magyarban ugyanaz a szöveg minden plural-ágon, ez NEM hiba)."""
    inner = span[1:-1]
    for sep in (":", "|"):
        idx = inner.find(sep)
        if idx != -1:
            inner = inner[:idx]
    return inner.strip()


def extract_tokens(text):
    if not text:
        return Counter()
    return Counter(token_identity(span) for span in extract_balanced_spans(text))


def validate_file(
    path: str,
    glossary_entries=None,
    *,
    source_col: str = "en",
    target_col: str = "hu",
) -> tuple[list[str], list[str], list[str], list[str]]:
    critical, major, minor, info = [], [], [], []

    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []

        key_col = next((k for k in ["Key", "ID", "id", "key"] if k in fieldnames), None)
        if not key_col:
            critical.append(
                f"Nincs 'Key' vagy 'ID' oszlop a fejlecben (talalt oszlopok: {fieldnames}). "
                f"Ez nem a varhato Unity Localization csomag CSV formatum -- "
                f"ellenorizd, hogy nem legacy/I2 exportrol van-e szo (ld. format-unity.md)."
            )
            return critical, major, minor, info

        if source_col not in fieldnames:
            critical.append(f"A megadott forras-oszlop ('{source_col}') nincs a fejlecben: {fieldnames}")
        if target_col not in fieldnames:
            critical.append(f"A megadott celnyelvi oszlop ('{target_col}') nincs a fejlecben: {fieldnames}")

        if critical:
            return critical, major, minor, info

        seen_keys = {}
        empty_target = 0
        row_count = 0
        pairs = []

        for i, row in enumerate(reader, start=2):  # 2: fejlec az 1. sor
            row_count += 1
            key = row.get(key_col, "")
            if key in seen_keys:
                critical.append(f"Duplikalt Key: '{key}' (sor {seen_keys[key]} es {i})")
            else:
                seen_keys[key] = i

            source_val = row.get(source_col, "")
            target_val = row.get(target_col, "")

            if not target_val.strip():
                empty_target += 1
                continue

            pairs.append((source_val, target_val, f"Key='{key}' (sor {i})"))

            src_tokens = extract_tokens(source_val)
            tgt_tokens = extract_tokens(target_val)
            if src_tokens != tgt_tokens:
                missing = src_tokens - tgt_tokens
                extra = tgt_tokens - src_tokens
                detail = []
                if missing:
                    detail.append(f"hianyzik: {sorted(missing.elements())}")
                if extra:
                    detail.append(f"tobblet: {sorted(extra.elements())}")
                major.append(f"Key='{key}' (sor {i}): token-keszlet eltere -- {'; '.join(detail)}")

            html_issues = check_html_tags(source_val, target_val, f"Key='{key}'")
            if html_issues:
                major.extend(html_issues)

    if empty_target:
        info.append(f"{empty_target} sornak ures a celnyelvi ('{target_col}') mezoje (fordítandó).")
    info.append(f"Osszesen {row_count} sor feldolgozva.")

    if glossary_entries:
        major.extend(check_protected_terms(pairs, glossary_entries))

    return critical, major, minor, info


def main(argv):
    argv, glossary_path = extract_glossary_arg(argv)
    if not argv or "--source" not in argv or "--target" not in argv:
        print(__doc__)
        return 1

    path = argv[0]
    source_col = argv[argv.index("--source") + 1]
    target_col = argv[argv.index("--target") + 1]

    entries = load_glossary_for_check(glossary_path) if glossary_path else []
    critical, major, minor, info = validate_file(path, entries, source_col=source_col, target_col=target_col)

    print(f"=== {path} ===")
    for label, items in (("CRITICAL", critical), ("MAJOR", major), ("INFO", info)):
        if items:
            print(f"-- {label} ({len(items)}) --")
            for item in items:
                print(f"  - {item}")

    return 1 if (critical or major) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
