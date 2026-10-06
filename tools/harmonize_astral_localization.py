#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
harmonize_astral_localization.py
Phase 1 & Phase 2: Deterministic Global Termbase Harmonization Engine.
Applies the Canonical Termbase across all 29 batch files in Astral Chain:
- Standardizes modes (Gumibot mód, Blaster mód, Gladius mód)
- Standardizes combat skills (Szinkrontámadás, Tökéletes hívás, Lánckötés, Vágóállás, Vágás)
- Standardizes factions and names (Remete / Remeték, Hollók, Asztrálsík, Vörös Anyag)
- Applies critical mistranslation fixes (Douglas táskája, Lengéscsillapító, Az ötödik kerék, etc.)
- Fixes typos (PÉNCÉL -> PÁNCÉL, Bevezt -> Bevet, Rôrsapka -> Rendôrsapka, adagragu -> adatragu)
- Maintains strict font glyph encoding (ő/ű -> ô/û)
"""

import os
import json
import re

BATCHES_DIR = r"D:\github\GameStringer-main\locpipe\projects\Astral Chain\batches"

EXACT_KEY_REWRITES = {
    # Mistranslations & Special fixes
    "CORE_ITEM_NAME_A000": "Douglas táskája",
    "CORE_ITEM_NAME_5780": "Koszos lengéscsillapító",
    "CORE_ITEM_NAME_6014": "Lengéscsillapító",
    "CORE_KEYWORD_0904": "lengéscsillapító",
    "CORE_KEYWORD_0904_1": "lengéscsillapító",
    "FILE_MISSION_2b05": "Az ötödik kerék",
    "FILE_MISSION_3305": "Pihenj meg egyet",
    "FILE_MISSION_26f0": "Neuron bajban",
    "FILE_MISSION_2960": "Célszemély",
    "CORE_ABILITY_NAME_0077": "Tűzvédelem",
    "EVENT_BOSS_NAME_0015": "ASTRAEUS-CAPANEUS",
    "CORE_ITEM_NAME_8260": "Rendőrsapka",
    "CORE_ITEM_HELP_TXT_0751": "Cseppfolyósított adatragu...",
    "FILE_MISSION_2202": "Válság a Fősugárúton",

    # HUD Objectives <= 28 chars
    "CORE_MISSION_PURPOSE_2650_00": "Kérdezősködj a V-szektorban",    # 27 chars
    "CORE_MISSION_PURPOSE_2826_00": "Vidd az ellátmányt.",             # 19 chars
    "CORE_MISSION_PURPOSE_2905_00": "Vidd az ellátmányt.",             # 19 chars
    "CORE_MISSION_PURPOSE_6181_00": "Keress a Fenevaddal.",            # 20 chars
    "CORE_MISSION_PURPOSE_64e2_03": "Láncugrás a biztonságba",         # 23 chars
    "CORE_MISSION_PURPOSE_3b10_01": "Törd szét a dobozt Legionnel",    # 28 chars
    "CORE_PURPOSE_FILE_0608": "Juss be a Remeték bázisára!",          # 27 chars
    "CORE_PURPOSE_FILE_0902": "Kutasd át a Harmony teret.",            # 26 chars

    # Untranslated Menu / Order / Tutorial items
    "MENU_TUTO_NAME_30": "Szinkrontámadások",
    "MENU_ORDER_NAME_2011": "Tárgymániás",
    "MENU_ORDER_NAME_2012": "Mintatiszt",
    "MENU_ORDER_NAME_34a1": "Mintatiszt",
    "MENU_ORDER_NAME_2220": "Felfegyverzett és veszélyes",
    "MENU_ORDER_NAME_2090": "Lánchajtás-mester",
    "MENU_ORDER_NAME_3400": "Ismerd az ellenséged 1. szint",
    "MENU_ORDER_NAME_3401": "Ismerd az ellenséged 2. szint",
    "MENU_ORDER_NAME_3402": "Ismerd az ellenséged 3. szint",
    "MENU_ORDER_NAME_3403": "Ismerd az ellenséged Max",
    "MENU_ORDER_NAME_4410": "Szolgálat hív",
    "MENU_DATA_ARCHIVE_NAME_6071": "Lefolyógödör",
    "MENU_DATA_ARCHIVE_NAME_6081": "Yoseph Calvert laboratóriuma",
    "MENU_SKILL_NAME_0102": "Képességhely hozzáadása",
    "MENU_SKILL_NAME_0202": "Képességhely hozzáadása",
    "MENU_SKILL_NAME_0302": "Képességhely hozzáadása",
    "MENU_SKILL_NAME_0402": "Képességhely hozzáadása",
    "MENU_SKILL_NAME_0502": "Képességhely hozzáadása",
    "MENU_SKILL_NAME_01b5": "Sebességcsillag",
    "MENU_SKILL_NAME_02b5": "Sebességcsillag",
    "MENU_SKILL_NAME_03b5": "Sebességcsillag",
    "MENU_SKILL_NAME_04b5": "Sebességcsillag",
    "MENU_SKILL_NAME_05b5": "Sebességcsillag",
    "MENU_TUTO_NAME_06": "Tárgyak használata",
    "MENU_ALBUM_DIALOG_01": "Kiválasztottak törlése.\nBiztos vagy benne?",
    "MENU_SHARE_DIALOG_02": "Kooperatív játék befejezése...",
    "MENU_SAVE_DIALOG_32": "Megszakítod ezt az ügyet?...\n(A mentetlen előrehaladás elvész.)",
    "MENU_SELECT_CASE_DIALOG_07": "Visszatérsz a 12. aktához?",
    "MENU_SELECT_CASE_NAME_0900": "Nyilatkozat",
    "MENU_ORDER_INFO_4ff0": "Teljesíts minden megbízást.",
    "MENU_ORDER_INFO_3420": "Fejezd be az archívumot.",
    "MENU_DATA_AREA_NAME_5160": "Különleges Műveleti Iroda Főhadiszállás",
    "MENU_FILE_NAME_0012": "Az újjászületés folyamata...",
    "MENU_ORDER_NAME_1150": "Egyezség: S+",
    "MENU_TUTO_NAME_31": "Vágás",
    "MENU_ORDER_NAME_3600": "Hív a természet: Főhadiszállás",
}

# Regex replacements applied across all batch entries
GLOBAL_TERM_REPLACEMENTS = [
    # 1. Typos
    (r'\bPÉNCÉL\b', 'PÁNCÉL'),
    (r'\bBevezt\b', 'Bevet'),
    (r'\bRôrsapka\b', 'Rendôrsapka'),
    (r'\badagragu\b', 'adatragu'),
    (r'\bélvesznek\b', 'elvesznek'),
    (r'\bekábítsa\b', 'elkábítsa'),

    # 2. X-Baton Modes -> Gumibot mód, Blaster mód, Gladius mód
    (r'(?i)\bBaton\s+m[oóőô]d', 'Gumibot mód'),
    (r'(?i)\bBot\s+m[oóőô]d', 'Gumibot mód'),
    (r'(?i)\bBaton\s+edz[eé]s', 'Gumibot-edzés'),
    (r'(?i)\bBlaszter\s+m[oóőô]d', 'Blaster mód'),
    (r'(?i)\bSug[aá]rvet[oöőô]\s+m[oóőô]d', 'Blaster mód'),

    # 3. Combat Skills & Stances
    (r'(?i)\bT[oö]k[eé]letes\s+el[oöőô]h[ií]v[aá]s', 'Tökéletes hívás'),
    (r'(?i)\bL[aá]ncb[eé]kly[oó]z[aá]s\b', 'Lánckötés'),
    (r'(?i)\bL[aá]ncb[eé]kly[oó]ba\b', 'Lánckötésbe'),
    (r'(?i)\bL[aá]ncb[eé]kly[oó]b[oóőô]l\b', 'Lánckötésből'),
    (r'(?i)\bL[aá]ncb[eé]kly[oó]\b', 'Lánckötés'),
    (r'(?i)\bL[aá]ncos\s+megb[eé]kly[oó]z[aá]s\b', 'Lánckötés'),
    (r'(?i)\bSuhint[oóőô]\s+[aá]ll[aá]s', 'Vágóállás'),
    (r'(?i)\bV[aá]g[oóőô]\s+[aá]ll[aá]s', 'Vágóállás'),
    (r'(?i)\bRohamcsap[aá]s-mester\b', 'Csapásroham-mester'),
    (r'(?i)\bRohamcsap[aá]s\b', 'Csapásroham'),
    (r'(?i)\bL[aá]ncos\s+ellent[aá]mad[aá]s-mester\b', 'Lánckontra-mester'),
    (r'(?i)\bL[aá]ncos\s+ellent[aá]mad[aá]s\b', 'Lánckontra'),

    # 4. Factions & Lore: The Hermits -> Remeték, Remete
    (r'(?i)\bHermiteknek\b', 'Remetéknek'),
    (r'(?i)\bHermitekt[oöőô]l\b', 'Remetéktől'),
    (r'(?i)\bHermiteket\b', 'Remetéket'),
    (r'(?i)\bHermitekkel\b', 'Remetékkel'),
    (r'(?i)\bHermitekr[oöőô]l\b', 'Remetékről'),
    (r'(?i)\bHermitek\b', 'Remeték'),
    (r'(?i)\bHermit(?=[\[\)\s\.,\?!:-])(?!onic)', 'Remete'),
    (r'(?i)\bHermit\b(?!onic)', 'Remete'),
    (r'(?i)\bVereked[oöőô]\s+Hermit\b', 'Verekedő remete'),
    (r'(?i)\bId[oöőô]s\s+Hermit\b', 'Idős remete'),
    (r'(?i)\bMegfigyel[oöőô]\s+Hermit\b', 'Megfigyelő remete'),
    (r'(?i)\bNeh[eé]zkardos\s+Hermit\b', 'Nehézkardos remete'),
    (r'(?i)\bMolotovos\s+Hermit\b', 'Molotovos remete'),
    (r'(?i)\bDr[oó]npil[oó]ta\s+Hermit\b', 'Drónpilóta remete'),
    (r'(?i)\bCsatl[oó]s\s+Hermit\b', 'Csatlós remete'),
    (r'(?i)\bKutat[oó]\s+Hermit\b', 'Kutató remete'),

    # 5. Locations & System Terms
    (r'\bAsztr[aá]lis s[ií]k', 'Asztrálsík'),
    (r'\basztr[aá]lis s[ií]k', 'asztrálsík'),
    (r'\bHarmony Square-en\b', 'Harmony téren'),
    (r'\bHarmony Square-re\b', 'Harmony térre'),
    (r'\bHarmony Square-r[oöőô]l\b', 'Harmony térről'),
    (r'\bHarmony Square-t[oöőô]l\b', 'Harmony tértől'),
    (r'\bHarmony Square\b', 'Harmony tér'),
    (r'\bHarm[oó]nia t[eé]r\b', 'Harmony tér'),
    (r'\bharm[oó]nia t[eé]r\b', 'harmony tér'),
    (r'\bSector V\b', 'V. szektor'),
    (r'\bV szektor\b', 'V. szektor'),
    (r'\bArk Mall\b', 'Ark Pláza'),
    (r'\bArk Plaza\b', 'Ark Pláza'),
    (r'\bL[aá]ncolatlan m[oó]d\b', 'Unchained mód'),
    (r'\bl[aá]ncolatlan m[oó]d\b', 'unchained mód'),
    (r'\bK[Öö]TETLEN\b', 'UNCHAINED'),
    (r'\bLegion-fejleszt[eé]s f[uü]l\b', 'Legion-tanulás fül'),
    (r'\bLegion-fejleszt[eé]sben\b', 'Legion-tanulásban'),
    (r'\bDosszi[eé]k\b', 'Akták'),
    (r'\bdosszi[eé]k\b', 'akták'),
    (r'\bDosszi[eé]\b', 'Akta'),
    (r'\bdosszi[eé]\b', 'akta'),
    (r'\bHomunculus β\b', 'Homunkulusz β'),
    (r'\bHomunculus γ\b', 'Homunkulusz γ'),
    (r'\bHomunculus α\b', 'Homunkulusz α'),
    (r'\bHomunculus Δ\b', 'Homunkulusz Δ'),
    (r'\bHomunculus\b', 'Homunkulusz'),
    (r'\bhomunculus\b', 'homunkulusz'),
    (r'\bFilthwing\b', 'Mocskosszárny'),
    (r'\bPiszkossz[aá]rny\b', 'Mocskosszárny'),
]

def map_accents(text: str) -> str:
    """Strict Nintendo Switch font encoding mapping."""
    return text.replace("ő", "ô").replace("ű", "û").replace("Ő", "Ô").replace("Ű", "Û")

def harmonize_entry(entry: dict) -> bool:
    hu = entry.get("HU", "")
    if not hu:
        return False
    
    orig_hu = hu
    key = entry.get("key", "")

    # 1. Exact Key Rewrites
    if key in EXACT_KEY_REWRITES:
        hu = EXACT_KEY_REWRITES[key]

    # 2. Global Term Replacements
    for pattern, repl in GLOBAL_TERM_REPLACEMENTS:
        hu = re.sub(pattern, repl, hu)

    # 3. Accents mapping
    hu = map_accents(hu)

    if hu != orig_hu:
        entry["HU"] = hu
        return True
    return False

def main():
    print("=== Astral Chain: Canonical Termbase Harmonization Engine ===")
    total_files_changed = 0
    total_strings_changed = 0

    for fname in sorted(os.listdir(BATCHES_DIR)):
        if fname.endswith(".json"):
            fpath = os.path.join(BATCHES_DIR, fname)
            with open(fpath, "r", encoding="utf-8") as f:
                entries = json.load(f)

            file_modified = 0
            for e in entries:
                if harmonize_entry(e):
                    file_modified += 1

            if file_modified > 0:
                with open(fpath, "w", encoding="utf-8") as f:
                    json.dump(entries, f, ensure_ascii=False, indent=2)
                print(f"  [HARMONIZED] {fname}: {file_modified} strings updated.")
                total_files_changed += 1
                total_strings_changed += file_modified
            else:
                print(f"  [CLEAN]      {fname}: No harmonization required.")

    print(f"\nHarmonization complete! Total {total_strings_changed} strings updated across {total_files_changed} files.")

if __name__ == "__main__":
    main()
