#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
refine_astral_translations.py
Optimizes Astral Chain Hungarian localization:
1. Replaces 'Légió' with 'Legion' (with correct Hungarian declensions).
2. Compacts tutorial button prompts so button icons are never pushed off-screen.
3. Shortens HUD Mission Objectives to <= 28 characters to eliminate text truncation.
4. Applies strict character mapping (ő->ô, ű->û, Ő->Ô, Ű->Û).
"""

import os
import json
import re

BATCHES_DIR = r"D:\github\GameStringer-main\locpipe\projects\Astral Chain\batches"

# Specific high-quality concise rewrites for HUD Mission Purpose & File Objectives
PURPOSE_REWRITES = {
    "CORE_MISSION_PURPOSE_3560_00": "Keresd a sérülteket.",            # EN: Find the hurt civilians. (24) -> 21
    "CORE_MISSION_PURPOSE_3830_01": "Keresd a graffitist.",            # EN: Find the grafitti artist. (24) -> 20
    "CORE_PURPOSE_FILE_1004": "Nézd meg a detektort.",                 # EN: Check the next detector. (24) -> 21
    "CORE_MISSION_PURPOSE_2a30_05": "Menekülj az Asztrálsíkról.",      # EN: Escape the Astral Plane. (24) -> 26
    "CORE_MISSION_PURPOSE_600a_02": "Vidd vissza az elemeket.",        # EN: Return with the parts. (22) -> 24
    "CORE_MISSION_PURPOSE_c800_00": "Próbáld ki a kiképzést.",         # EN: Try Legionis training. (22) -> 23
    "CORE_MISSION_PURPOSE_3a60_00": "Keresd meg a barátokat.",         # EN: Find the lost friends. (22) -> 23
    "CORE_MISSION_PURPOSE_26f0_00": "Segíts a Neuronnak.",             # EN: Help Neuron fight. (18) -> 19
    "CORE_MISSION_PURPOSE_3830_02": "Állítsd meg a graffitist.",       # EN: Stop the grafitti artist. (25) -> 25
    "CORE_MISSION_PURPOSE_2c00_02": "Biztosítsd a zónát.",             # EN: Secure the next area. (21) -> 19
    "CORE_MISSION_PURPOSE_2630_01": "Szerezd meg a kulcskártyát.",     # EN: Get the wall keycard. (21) -> 27
    "CORE_PURPOSE_FILE_0303": "Járd be a HQ részlegeit.",              # EN: Visit each facility in HQ. (26) -> 24
    "CORE_MISSION_PURPOSE_3a10_00": "Hódítsd meg Olive-ot.",           # EN: Find a way to woo Olive. (24) -> 21
    "CORE_MISSION_PURPOSE_3a70_00": "Kékeltolás a civilen.",           # EN: Blueshift the civilian. (23) -> 21
    "CORE_MISSION_PURPOSE_3910_01": "Add át az alkatrészeket.",        # EN: Give Tabuchi the parts. (23) -> 24
    "CORE_MISSION_PURPOSE_3201_03": "Próbálj ki egy edzést.",          # EN: Try a training program. (23) -> 22
    "CORE_MISSION_PURPOSE_2a90_00": "Fékezd meg Akirát.",              # EN: Stop Akira's rampage. (21) -> 18
    "CORE_PURPOSE_FILE_0304": "Találkozz Marie-val.",                  # EN: Meet Marie in the garage. (25) -> 20
    "CORE_MISSION_PURPOSE_3605_00": "Állítsd meg a graffitist.",       # EN: Stop the graffiti artist. (25) -> 25
    "CORE_MISSION_PURPOSE_2a20_00": "Keresd meg a macskát.",           # EN: Look for the lost cat. (22) -> 21
    "CORE_MISSION_PURPOSE_3910_00": "Szerezz alkatrészt.",             # EN: Get parts from Tabitha. (24) -> 19
    "CORE_MISSION_PURPOSE_2a40_00": "Infó az eltűntekről.",            # EN: Get info on lost citizens. (26) -> 20
    "CORE_MISSION_PURPOSE_3303_00": "Használd a terminált.",           # EN: Use the Legatus Terminal. (25) -> 21
    "CORE_MISSION_PURPOSE_3b10_02": "Emelj tárgyat Legionnel.",        # EN: Lift an object with a Legion. (29) -> 24
    "CORE_MISSION_PURPOSE_6181_00": "Keresés a Fenevaddal.",           # EN: Use the Beast to search. (24) -> 21
    "CORE_MISSION_PURPOSE_3312_00": "Keresd a graffitist.",            # EN: Find the grafitti artist. (24) -> 20
    "CORE_PURPOSE_FILE_0411": "Szabadítsd ki Jint.",                   # EN: Free Jin from the Legion. (25) -> 19
    "CORE_MISSION_PURPOSE_3b10_01": "Törd szét a dobozt.",             # EN: Break the box with a Legion. (28) -> 19
    "CORE_MISSION_PURPOSE_64e2_03": "Láncugrás a célba.",              # EN: Chain Jump to safety. (21) -> 18
    "CORE_PURPOSE_FILE_0606": "Kérdezősködj a zónában.",               # EN: Ask around Zone 09. (20) -> 23
    "CORE_MISSION_PURPOSE_6181_03": "Vidd vissza az elemeket.",        # EN: Return with the parts. (22) -> 24
    "CORE_MISSION_PURPOSE_2825_00": "Gyűjts fémhulladékot.",           # EN: Collect the scrap. (18) -> 21
    "CORE_MISSION_PURPOSE_3646_00": "Helyezd áram alá a gépet.",       # EN: Power the database. (20) -> 25
    "CORE_PURPOSE_FILE_0902": "Kutasd át a terepet.",                  # EN: Search Harmony Square. (22) -> 20
    "CORE_PURPOSE_FILE_0802": "Menj fel a tetőre.",                    # EN: Go to the Rayleigh Plaza roof. (30) -> 18
    "CORE_MISSION_PURPOSE_3201_01": "Használd az IRIS-t.",             # EN: Use the IRIS on the terminal. (29) -> 19
    "CORE_MISSION_PURPOSE_3b20_00": "Keresd a szerelmest.",            # EN: Find the lovestruck man. (24) -> 20
    "CORE_MISSION_PURPOSE_2738_00": "Kékeltolás a rendőrön.",          # EN: Blueshift the officer. (22) -> 22
    "CORE_MISSION_PURPOSE_2650_00": "Kérdezősködj itt.",               # EN: Ask around Sector V. (20) -> 17
    "CORE_MISSION_PURPOSE_3620_00": "Keresd a pénzchipet.",            # EN: Find the lost cashchip. (23) -> 20
    "CORE_MISSION_PURPOSE_3830_00": "Kapd el a műkedvelőt.",           # EN: Arrest the art lover. (21) -> 21
    "CORE_MISSION_PURPOSE_3520_00": "Állítsd le a zárlatot.",          # EN: Stop the short. (15) -> 22
    "CORE_MISSION_PURPOSE_2a50_00": "Keress biztonságos utat.",        # EN: Find a safe route. (18) -> 24
    "CORE_MISSION_PURPOSE_3401_00": "Vezesd el a férfit.",             # EN: Guide the lost man. (19) -> 19
}

# Concrete QA / External LLM findings
EXACT_REWRITES = {
    # Part 01 review findings:
    "EVENT_BOSS_NAME_0170": "R-TÍPUSÚ KARD LEGION",
    "EVENT_BOSS_NAME_0173": "R-TÍPUSÚ FARKAS LEGION",
    "EVENT_BOSS_NAME_0174": "R-TÍPUSÚ BARDICHE LEGION",
    "EVENT_BOSS_NAME_0150": "PROTO-LEGION",
    "FILE_MISSION_2790": "Proto-Legion harc",
    "EVENT_BOSS_NAME_0163": "FENEVAD NEMEZIS",
    "EVENT_BOSS_NAME_0162": "KAR NEMEZIS",
    "EVENT_BOSS_NAME_2063": "NOAH, AZ AMBÍCIÓ LELKE",
    "SHOP_DIALOGUE_TXT_05": "Képesség létrehozása.\nBiztos vagy benne?",
    "OPTION_BTN_0412": "Legion kitérés",
    "OPTION_BTN_0080": "Reset / Célzás",
    "OPTION_BTN_0100": "Tárgy használata/váltása",
    "OPTION_BTN_0210": "Rendőrségi jegyzetek",
    "HUD_TUTO_01": "[BTN:L2 ]: Szinkrontámadás, ha a Legatus felvillan.",
    "HUD_TUTO_03": "[BTN:R1 ] (tartva) + [BTN:RS ]: célzás az ellenségre.\nA Legion célra zár.",
    "FILE_MISSION_3510": "Lappy panasza",
    "FILE_MISSION_3830": "Graffitimester",
    "FILE_MISSION_2a70": "Kimératámadás",
    "FILE_MISSION_2a11": "Veszélyes szemétdomb",
    "FILE_MISSION_2850": "Kapuellenőrzés a tetőn",
    "FILE_MISSION_2851": "Kapuellenőrzés a tetőn",
    "HUD_LOG_TXT_09": "Ügy célkitűzése frissült.",
}

LEGIO_REPLACEMENTS = [
    # Uppercase and compounds
    (r'\bLÉGIÓSTÍLUS\b', 'LEGION-STÍLUS'),
    (r'\bLÉGIÓ\b', 'LEGION'),
    (r'\bLégiófejlesztésben\b', 'Legion-fejlesztésben'),
    (r'\bLégiófejlesztés\b', 'Legion-fejlesztés'),
    (r'\blégiófejlesztésben\b', 'legion-fejlesztésben'),
    (r'\blégiófejlesztés\b', 'legion-fejlesztés'),
    (r'\bLégiótanulásban\b', 'Legion-tanulásban'),
    (r'\bLégiótanulás\b', 'Legion-tanulás'),
    (r'\blégiótanulásban\b', 'legion-tanulásban'),
    (r'\blégiótanulás\b', 'legion-tanulás'),
    (r'\bLégióroham\b', 'Legion-roham'),
    (r'\blégióroham\b', 'legion-roham'),
    (r'\bLégióváltás\b', 'Legion-váltás'),
    (r'\blégióváltás\b', 'legion-váltás'),
    (r'\bLégiótípusokhoz\b', 'Legion-típusokhoz'),
    (r'\bLégiótípusok\b', 'Legion-típusok'),
    (r'\bLégiótípus\b', 'Legion-típus'),
    (r'\blégiótípusokhoz\b', 'legion-típusokhoz'),
    (r'\blégiótípusok\b', 'legion-típusok'),
    (r'\blégiótípus\b', 'legion-típus'),
    (r'\bProtolegionök\b', 'Proto-Legionök'),
    (r'\bProtolégió\b', 'Proto-Legion'),
    (r'\bprotolégió\b', 'proto-legion'),
    (r'\bprotolegion\b', 'proto-legion'),
    # General declensions of Legion (order matters: longest first)
    (r'\bL[ée]gi[oó]kkal\b', 'Legionökkel'),
    (r'\bl[ée]gi[oó]kkal\b', 'legionökkel'),
    (r'\bL[ée]gi[oó]kat\b', 'Legionöket'),
    (r'\bl[ée]gi[oó]kat\b', 'legionöket'),
    (r'\bL[ée]gi[oó]k\b', 'Legionök'),
    (r'\bl[ée]gi[oó]k\b', 'legionök'),
    (r'\bL[ée]gi[oó]val\b', 'Legionnel'),
    (r'\bl[ée]gi[oó]val\b', 'legionnel'),
    (r'\bL[ée]gi[oó]dat\b', 'Legionödet'),
    (r'\bl[ée]gi[oó]dat\b', 'legionödet'),
    (r'\bL[ée]gi[oó]ddal\b', 'Legionöddel'),
    (r'\bl[ée]gi[oó]ddal\b', 'legionöddel'),
    (r'\bL[ée]gi[oó]dnak\b', 'Legionödnek'),
    (r'\bl[ée]gi[oó]dnak\b', 'legionödnek'),
    (r'\bL[ée]gi[oó]dra\b', 'Legionödre'),
    (r'\bl[ée]gi[oó]dra\b', 'legionödre'),
    (r'\bL[ée]gi[oó]d\b', 'Legionöd'),
    (r'\bl[ée]gi[oó]d\b', 'legionöd'),
    (r'\bL[ée]gi[oó]hoz\b', 'Legionhöz'),
    (r'\bl[ée]gi[oó]hoz\b', 'legionhöz'),
    (r'\bL[ée]gi[oó]b[oó]l\b', 'Legionből'),
    (r'\bl[ée]gi[oó]b[oó]l\b', 'legionből'),
    (r'\bL[ée]gi[oó]ban\b', 'Legionben'),
    (r'\bl[ée]gi[oó]ban\b', 'legionben'),
    (r'\bL[ée]gi[oó]ben\b', 'Legionben'),
    (r'\bl[ée]gi[oó]ben\b', 'legionben'),
    (r'\bL[ée]gi[oó]ra\b', 'Legionre'),
    (r'\bl[ée]gi[oó]ra\b', 'legionre'),
    (r'\bL[ée]gi[oó]r[oó]l\b', 'Legionről'),
    (r'\b[Ll]égión lovagolva\b', 'Legionön lovagolva'),
    (r'\bL[ée]gi[oó]don\b', 'Legionödön'),
    (r'\bl[ée]gi[oó]don\b', 'legionödön'),
    (r'\bl[ée]gi[oó]t\b', 'legiont'),
    (r'\bL[ée]gi[oó]nak\b', 'Legionnek'),
    (r'\bl[ée]gi[oó]nak\b', 'legionnek'),
    (r'\bL[ée]gi[oó]knak\b', 'Legionöknek'),
    (r'\bl[ée]gi[oó]knak\b', 'legionöknek'),
    (r'\bL[ée]gi[oó]kt[oó]l\b', 'Legionöktől'),
    (r'\bl[ée]gi[oó]kt[oó]l\b', 'legionöktől'),
    (r'\bL[ée]gi[oó]t[oó]l\b', 'Legiontől'),
    (r'\bl[ée]gi[oó]t[oó]l\b', 'legiontől'),
    (r'\bL[ée]gi[oó]é\b', 'Legioné'),
    (r'\bl[ée]gi[oó]é\b', 'legioné'),
    (r'\bL[ée]gi[oó]mban\b', 'Legionömben'),
    (r'\bl[ée]gi[oó]mban\b', 'legionömben'),
    (r'\bL[ée]gi[oó]m\b', 'Legionöm'),
    (r'\bl[ée]gi[oó]m\b', 'legionöm'),
    (r'\bL[ée]gi[oó]nk\b', 'Legionünk'),
    (r'\bl[ée]gi[oó]nk\b', 'legionünk'),
    (r'\bL[ée]gi[oó]tok\b', 'Legionötök'),
    (r'\bl[ée]gi[oó]tok\b', 'legionötök'),
    (r'\bL[ée]gi[oó]iddal\b', 'Legionjeiddel'),
    (r'\bl[ée]gi[oó]iddal\b', 'legionjeiddel'),
    (r'\bL[ée]gi[oó]ja\b', 'Legionje'),
    (r'\bl[ée]gi[oó]ja\b', 'legionje'),
    (r'\bL[ée]gi[oó]\b', 'Legion'),
    (r'\bl[ée]gi[oó]\b', 'legion'),
]

def map_accents(text: str) -> str:
    return text.replace("ő", "ô").replace("ű", "û").replace("Ő", "Ô").replace("Ű", "Û")

def compact_tutorial_names(text: str, key: str) -> str:
    # Only compact in tutorial name headers and short button prompts
    if not ("TUTO_W_NAME" in key or "TUTORIAL_BTN" in key or "ACT_BTN" in key):
        return text

    # "Tartsd nyomva a [BTN:X ] gombot + mozgasd a [BTN:Y ] kart:" -> "[BTN:X ] (tartva) + [BTN:Y ]:"
    text = re.sub(r'Tartsd nyomva a (\[BTN:[^\]]+\]) gombot \+ mozgasd a (\[BTN:[^\]]+\]) kart:?', r'\1 (tartva) + \2:', text)
    # "Tartsd nyomva: [BTN:X ] + Nyomd meg: [BTN:Y ]:" -> "[BTN:X ] (tartva) + [BTN:Y ]:"
    text = re.sub(r'Tartsd nyomva: (\[BTN:[^\]]+\]) \+ Nyomd meg: (\[BTN:[^\]]+\]):?', r'\1 (tartva) + \2:', text)
    # "Tartsd nyomva a [BTN:X ]-t + [BTN:Y ]:" -> "[BTN:X ] (tartva) + [BTN:Y ]:"
    text = re.sub(r'Tartsd nyomva a (\[BTN:[^\]]+\])-t \+ (\[BTN:[^\]]+\]):?', r'\1 (tartva) + \2:', text)
    # "Tartsd nyomva a [BTN:X ] + [BTN:Y ] gombot:" -> "[BTN:X ] + [BTN:Y ] (tartva):"
    text = re.sub(r'Tartsd nyomva a (\[BTN:[^\]]+\] \+ \[[^\]]+\]) gombot:?', r'\1 (tartva):', text)
    # "Tartsd nyomva a [BTN:X ]-t + [BTN:Y ]:" -> "[BTN:X ] (tartva) + [BTN:Y ]:"
    text = re.sub(r'Tartsd nyomva a (\[BTN:[^\]]+\])-t \+ (\[BTN:[^\]]+\]):?', r'\1 (tartva) + \2:', text)
    # "Tartsd nyomva a [BTN:X ] gombot:" -> "[BTN:X ] (tartva):"
    text = re.sub(r'Tartsd nyomva a (\[BTN:[^\]]+\]) gombot:?', r'\1 (tartva):', text)
    # "Tartsd nyomva a [BTN:X ] gombot" -> "[BTN:X ] (tartva)"
    text = re.sub(r'Tartsd nyomva a (\[BTN:[^\]]+\]) gombot', r'\1 (tartva)', text)

    return text

def process_file(file_path: str):
    with open(file_path, "r", encoding="utf-8") as f:
        entries = json.load(f)

    modified_count = 0
    for e in entries:
        hu = e.get("HU", "")
        if not hu:
            continue

        orig_hu = hu
        key = e.get("key", "")

        # 1. Mission Purpose specific rewrites
        if key in PURPOSE_REWRITES:
            hu = PURPOSE_REWRITES[key]

        # 2. Tutorial button prompt compacting
        hu = compact_tutorial_names(hu, key)

        # 3. Terminology: Légió -> Legion
        for pattern, repl in LEGIO_REPLACEMENTS:
            hu = re.sub(pattern, repl, hu)

        # 3b. Clean up any erroneous "Legionön" that isn't "Legionön lovagolva"
        hu = re.sub(r'\bLegionön(?!\s+lovagolva)\b', 'Legion', hu)
        hu = re.sub(r'\blegionön(?!\s+lovagolva)\b', 'legion', hu)

        # 3c. Exact QA / External LLM rewrites
        if key in EXACT_REWRITES:
            hu = EXACT_REWRITES[key]

        # 4. Strict font accent mapping
        hu = map_accents(hu)

        if hu != orig_hu:
            e["HU"] = hu
            modified_count += 1

    if modified_count > 0:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False, indent=2)
        print(f"  Updated {os.path.basename(file_path)}: {modified_count} strings refined.")

def main():
    print("=== Refining Astral Chain Hungarian Localization ===")
    for fname in sorted(os.listdir(BATCHES_DIR)):
        if fname.endswith(".json"):
            fpath = os.path.join(BATCHES_DIR, fname)
            process_file(fpath)
    print("=== Refinement complete! ===")

if __name__ == "__main__":
    main()
