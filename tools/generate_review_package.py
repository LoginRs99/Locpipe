#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_review_package.py
Generates a comprehensive translation review package for Astral Chain (Nintendo Switch)
specifically formatted for high-tier LLM evaluation (Kimi k2/k3, GLM-4/5.3) and human review.
Produces:
1. Astral_Chain_Review_Guide_and_Core_UI.pdf (Guide, Terminology, Rules + All Core UI/Tutorial/Mission text)
2. REVIEW_PROMPT_KIMI_GLM.md (Ready-to-use prompt for Kimi / GLM)
3. Astral_Chain_Bilingual_Core_UI.json / .md (High-density structured formats for LLMs)
"""

import os
import json
import html
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, HRFlowable
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

BATCHES_DIR = r"D:\github\GameStringer-main\locpipe\projects\Astral Chain\batches"
OUTPUT_DIR = r"D:\github\Forditasok\AI_Translation_HU_dev\games\Astral chain\review"

# Register TrueType fonts with full Unicode support
pdfmetrics.registerFont(TTFont("Arial", "C:/Windows/Fonts/arial.ttf"))
pdfmetrics.registerFont(TTFont("Arial-Bold", "C:/Windows/Fonts/arialbd.ttf"))
pdfmetrics.registerFont(TTFont("Arial-Italic", "C:/Windows/Fonts/ariali.ttf"))

class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count):
        if self._pageNumber == 1:
            return  # Suppress on cover page
        self.saveState()
        self.setFont("Arial", 8)
        self.setFillColor(colors.HexColor("#718096"))
        
        # Header
        self.drawString(40, 810, "Astral Chain — Lokalizációs és Minőségbiztosítási Felülvizsgálat")
        self.setStrokeColor(colors.HexColor("#E2E8F0"))
        self.setLineWidth(0.5)
        self.line(40, 804, 555, 804)
        
        # Footer
        page_str = f"Oldal {self._pageNumber} / {page_count}"
        self.drawRightString(555, 25, page_str)
        self.drawString(40, 25, "Szigorúan belső használatra | Kimi k3 / GLM-5.3 felülvizsgálati csomag")
        self.line(40, 35, 555, 35)
        self.restoreState()

def escape_text(text: str) -> str:
    if not text:
        return ""
    text = html.escape(text)
    return text.replace("\r\n", "<br/>").replace("\n", "<br/>")

def build_pdf_guide_and_core():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    pdf_path = os.path.join(OUTPUT_DIR, "Astral_Chain_Review_Guide_and_Core_UI.pdf")
    
    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=A4,
        leftMargin=36,
        rightMargin=36,
        topMargin=45,
        bottomMargin=45
    )

    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle("CoverTitle", fontName="Arial-Bold", fontSize=24, leading=28, textColor=colors.HexColor("#1A365D"), alignment=1)
    subtitle_style = ParagraphStyle("CoverSub", fontName="Arial", fontSize=12, leading=16, textColor=colors.HexColor("#4A5568"), alignment=1)
    h1_style = ParagraphStyle("Heading1", fontName="Arial-Bold", fontSize=15, leading=18, textColor=colors.HexColor("#2B6CB0"), spaceAfter=8, keepWithNext=True)
    h2_style = ParagraphStyle("Heading2", fontName="Arial-Bold", fontSize=12, leading=15, textColor=colors.HexColor("#2D3748"), spaceAfter=6, keepWithNext=True)
    body_style = ParagraphStyle("Body", fontName="Arial", fontSize=9, leading=12, textColor=colors.HexColor("#2D3748"), spaceAfter=5)
    bullet_style = ParagraphStyle("Bullet", fontName="Arial", fontSize=8.5, leading=11, textColor=colors.HexColor("#2D3748"), leftIndent=12, spaceAfter=3)
    code_style = ParagraphStyle("Code", fontName="Arial", fontSize=8, leading=10, textColor=colors.HexColor("#742A2A"), backColor=colors.HexColor("#FFF5F5"))
    
    cell_key = ParagraphStyle("CellKey", fontName="Arial", fontSize=7, leading=9, textColor=colors.HexColor("#718096"))
    cell_en = ParagraphStyle("CellEN", fontName="Arial", fontSize=8, leading=10.5, textColor=colors.HexColor("#1A202C"))
    cell_hu = ParagraphStyle("CellHU", fontName="Arial", fontSize=8, leading=10.5, textColor=colors.HexColor("#2B6CB0"))

    story = []

    # --- COVER PAGE ---
    story.append(Spacer(1, 40))
    story.append(Paragraph("ASTRAL CHAIN", title_style))
    story.append(Spacer(1, 8))
    story.append(Paragraph("Magyar Lokalizáció — Felülvizsgálati és Minőségbiztosítási Kézikönyv", ParagraphStyle("CoverH2", fontName="Arial-Bold", fontSize=15, leading=18, textColor=colors.HexColor("#2C5282"), alignment=1)))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Teljes kontextus, technikai korlátok, stílus- és terminológiai útmutató LLM felülvizsgálathoz<br/>(Ajánlott modellek: Kimi k2 / k3, GLM-4 / GLM-5.3)", subtitle_style))
    story.append(Spacer(1, 15))
    story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor("#2B6CB0"), spaceAfter=20))

    # Executive Overview
    story.append(Paragraph("1. A Projekt Áttekintése és Kontextus (Game Lore)", h1_style))
    story.append(Paragraph(
        "Az <b>Astral Chain</b> a PlatinumGames 2019-es akció-szerepjátéka Nintendo Switchre. A játék a 2078-as disztópikus jövőben játszódik az <b>Ark</b> nevű mesterséges szigeten, amelyet egy másik dimenzióból, az <b>Asztrálsíkról (Astral Plane)</b> érkező idegen lények, a <b>Kimérák (Chimeras)</b> fenyegetnek. A kimérák vörös korrupciót (<b>Red Matter / Vörös Anyag</b> és <b>Redshift / Vöröseltolódás</b>) terjesztenek.",
        body_style
    ))
    story.append(Paragraph(
        "A rendőrség elit alakulata a <b>Neuron</b>, amelynek tisztjei a <b>Legatus</b> eszközzel befogott és megláncolt kimérákat, azaz <b>Legionöket</b> (Kard, Nyíl, Kar, Fenevad, Fejsze) használnak élő fegyverként. A játék nyelvezete dinamikus cyberpunk/anime akció, közvetlen, tegeződő stílusban.",
        body_style
    ))
    story.append(Spacer(1, 10))

    story.append(Paragraph("2. Szigorú Technikai Invariánsok és Szabályok (Non-Negotiables)", h1_style))
    story.append(Paragraph("A felülvizsgálat során az alábbi szabályok betartása kritikus:", body_style))
    story.append(Paragraph("• <b>Vezérlőkódok és Gombok Érintetlensége:</b> Minden <code>[BTN:...]</code>, <code>[COLOR:...]</code> és <code>[HUDTEXT:...]</code> címke szigorúan megőrzendő! A gombkódok belsejét (pl. <code>[BTN:L2 ]</code>, <code>[BTN:RS ]</code>) tilos módosítani vagy lefordítani.", bullet_style))
    story.append(Paragraph("• <b>HUD Célkitűzés Hosszkorlát (Max Length):</b> A <code>CORE_MISSION_PURPOSE_*</code> és <code>CORE_PURPOSE_FILE_*</code> célkitűzések a játék képernyőjének jobb felső HUD dobozában jelennek meg, melynek fizikai korlátja legfeljebb <b>28-30 karakter</b>! Minden 28 karakternél hosszabb célkitűzés levágásra kerül a játékban. A mondatok legyenek feszesek és rövidek (pl. <i>'Infó az eltűntekről.'</i> a hosszú <i>'Gyűjts infót az eltűnt polgárokról.'</i> helyett).", bullet_style))
    story.append(Paragraph("• <b>Tutorial Gomb-kombinációk Tömörsége:</b> A <code>CORE_TUTO_W_NAME_*</code> és <code>CORE_TUTORIAL_BTN_*</code> elemekben a kontroller gombjai mellé nem szabad felesleges töltelékszavakat írni (pl. nem <i>'Tartsd nyomva a [BTN:L2 ] gombot'</i>, hanem a direkt videojátékos forma: <b><code>[BTN:L2 ] (tartva): Legion mozgatása</code></b>), különben a gombikon lecsúszik a képernyőről.", bullet_style))
    story.append(Paragraph("• <b>Betűkészlet Ékezetleképezés:</b> A játék betűtípusa a kalapos ékezeteket (<code>ô</code>, <code>û</code>, <code>Ô</code>, <code>Û</code>) használja a kettős éles (<code>ő</code>, <code>ű</code>) helyett a font textúrában. A felülvizsgált szövegekben a nyers <code>ő/ű</code> karakterek automatikusan <code>ô/û</code>-re lesznek leképezve.", bullet_style))
    story.append(Spacer(1, 10))

    story.append(Paragraph("3. Hivatalos Terminológiai Szótár (Glossary)", h1_style))
    
    glossary_data = [
        [Paragraph("<b>Angol Kifejezés (EN)</b>", h2_style), Paragraph("<b>Hivatalos Magyar Megfelelő</b>", h2_style), Paragraph("<b>Megjegyzés / Szabály</b>", h2_style)],
        [Paragraph("Legion", cell_en), Paragraph("<b>Legion</b> (sosem 'Légió'!)", cell_hu), Paragraph("Kard Legion, Nyíl Legion, Legionnel, Legionödet", cell_key)],
        [Paragraph("Neuron", cell_en), Paragraph("<b>Neuron</b>", cell_hu), Paragraph("Rendőrségi különítmény, nem fordítandó", cell_key)],
        [Paragraph("IRIS", cell_en), Paragraph("<b>IRIS</b>", cell_hu), Paragraph("Kiterjesztett valóság vizor, nagybetűs", cell_key)],
        [Paragraph("Chimera / Chimeras", cell_en), Paragraph("<b>Kiméra / Kimérák</b>", cell_hu), Paragraph("Asztrálsíki szörnyek", cell_key)],
        [Paragraph("Astral Plane", cell_en), Paragraph("<b>Asztrálsík</b>", cell_hu), Paragraph("A túlvilági dimenzió", cell_key)],
        [Paragraph("Red Matter", cell_en), Paragraph("<b>Vörös Anyag</b>", cell_hu), Paragraph("Kimérák által hagyott korrupt anyag", cell_key)],
        [Paragraph("Redshift / Blueshift", cell_en), Paragraph("<b>Vöröseltolódás / Kékeltolás</b>", cell_hu), Paragraph("Megfertőződés / Megtisztítás", cell_key)],
        [Paragraph("Sync Attack", cell_en), Paragraph("<b>Szinkrontámadás</b>", cell_hu), Paragraph("Kombinált támadás a Legionnel", cell_key)],
        [Paragraph("Perfect Call", cell_en), Paragraph("<b>Tökéletes hívás</b>", cell_hu), Paragraph("Időzített ellentámadásos Legion-idézés", cell_key)],
        [Paragraph("Chain Bind", cell_en), Paragraph("<b>Lánckötés / Megkötözés</b>", cell_hu), Paragraph("Ellenség körbetekerése a lánccal", cell_key)],
        [Paragraph("Chain Jump", cell_en), Paragraph("<b>Láncugrás</b>", cell_hu), Paragraph("Ugrás a Legion pozíciójához", cell_key)],
        [Paragraph("X-Baton", cell_en), Paragraph("<b>X-Baton</b> (Bot, Gladius, Blaster)", cell_hu), Paragraph("A rendőrségi multifunkciós fegyver", cell_key)],
        [Paragraph("Legatus", cell_en), Paragraph("<b>Legatus</b> (Legatusod)", cell_hu), Paragraph("Az alkaron viselt Legion-vezérlő egység", cell_key)],
        [Paragraph("Ark", cell_en), Paragraph("<b>Ark</b>", cell_hu), Paragraph("A megaváros helyszíne", cell_key)],
    ]
    
    t_glossary = Table(glossary_data, colWidths=[120, 180, 220])
    t_glossary.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#EBF8FF")),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor("#CBD5E0")),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t_glossary)
    
    story.append(PageBreak())

    # --- SECTION 4: BILINGUAL TABLES FOR CORE FILES ---
    story.append(Paragraph("4. Rendszer, HUD, Feladatok és Oktatóanyagok Felülvizsgálata", h1_style))
    story.append(Paragraph("Az alábbi táblázatok a játék legfontosabb logikai, kezelőfelületi és oktató szövegeit tartalmazzák fájlonként csoportosítva.", body_style))
    story.append(Spacer(1, 10))

    files_to_include = [
        ("Hud_text_USen.json", "HUD Szövegek és Rendszerüzenetek"),
        ("GameWord_USen.json", "Játékkifejezések és Rendszerkulcsszavak"),
        ("Option_text_USen.json", "Beállítások és Opciók"),
        ("Shop_text_USen.json", "Bolt és Áruszövegek"),
        ("Core_text_USen.json", "Fő Játékrendszer, Célkitűzések és Tutorialok (Core)"),
        ("menu_text_USen.json", "Menük, Képességek és Leírások"),
    ]

    for fname, desc in files_to_include:
        fpath = os.path.join(BATCHES_DIR, fname)
        if not os.path.exists(fpath):
            continue

        with open(fpath, "r", encoding="utf-8") as fp:
            entries = json.load(fp)

        story.append(Paragraph(f"<b>{desc}</b> ({fname} — {len(entries)} bejegyzés)", h2_style))
        
        table_rows = [
            [Paragraph("<b>Azonosító / Kulcs</b>", cell_key), Paragraph("<b>Eredeti Angol (EN)</b>", cell_key), Paragraph("<b>Magyar Fordítás (HU)</b>", cell_key)]
        ]

        max_rows = len(entries)
        for e in entries[:max_rows]:
            key = e.get("key", f"ID_{e.get('id')}")
            raw_en = e.get("text", "")
            raw_hu = e.get("HU", "")

            # Chunk very long text to prevent single table row from exceeding page height
            chunks = []
            if len(raw_en) > 400 or len(raw_hu) > 400:
                en_paras = [p for p in raw_en.split("\n") if p.strip()]
                hu_paras = [p for p in raw_hu.split("\n") if p.strip()]
                if len(en_paras) == len(hu_paras) and len(en_paras) > 1:
                    for i, (ep, hp) in enumerate(zip(en_paras, hu_paras)):
                        chunks.append((f"{key} [p{i+1}]", ep, hp))
                else:
                    import textwrap
                    en_c = textwrap.wrap(raw_en, 400, replace_whitespace=False) or [raw_en]
                    hu_c = textwrap.wrap(raw_hu, 400, replace_whitespace=False) or [raw_hu]
                    for i in range(max(len(en_c), len(hu_c))):
                        ec = en_c[i] if i < len(en_c) else ""
                        hc = hu_c[i] if i < len(hu_c) else ""
                        chunks.append((f"{key} [{i+1}]", ec, hc))
            else:
                chunks = [(key, raw_en, raw_hu)]

            for c_key, c_en, c_hu in chunks:
                table_rows.append([
                    Paragraph(c_key, cell_key),
                    Paragraph(escape_text(c_en), cell_en),
                    Paragraph(escape_text(c_hu), cell_hu)
                ])

        t_data = Table(table_rows, colWidths=[120, 200, 200], repeatRows=1)
        t_data.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#EDF2F7")),
            ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor("#E2E8F0")),
            ('TOPPADDING', (0,0), (-1,-1), 3),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),
            ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor("#F7FAFC")]),
        ]))
        story.append(t_data)
        story.append(Spacer(1, 15))

    print(f"Building PDF: {pdf_path}...")
    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"SUCCESS: {pdf_path} created ({os.path.getsize(pdf_path):,} bytes).")

def generate_ai_prompt_instructions():
    md_path = os.path.join(OUTPUT_DIR, "REVIEW_PROMPT_KIMI_GLM.md")
    content = """# Astral Chain (Nintendo Switch) — AI Localization Review Prompt
### Optimized for: Kimi k2 / k3, GLM-4 / GLM-5.3

Oszd meg ezt a felhívást és a mellékelt szövegfájlt az AI modellel (Kimi vagy GLM):

---

```markdown
# TASK: Video Game Localization Proofreading & QA (English -> Hungarian)
# GAME: Astral Chain (Nintendo Switch, PlatinumGames)
# ROLE: Senior Game Localization Editor / Lead Proofreader

## 1. Game Lore & Context
- Setting: Year 2078, "The Ark" (cyberpunk megacity).
- Conflict: Alien invasion by "Chimeras" (Kimérák) from the "Astral Plane" (Asztrálsík) spreading corruption ("Red Matter" / Vörös Anyag, "Redshift" / Vöröseltolódás).
- Faction: "Neuron" (elite police unit). Officers tame Chimeras into living tethered weapons called "Legions" using their "Legatus" arm unit.
- Legion Types: Sword (Kard), Arrow (Nyíl), Arm (Kar), Beast (Fenevad), Axe (Fejsze).
- Tone: Informal, dynamic anime/cyberpunk action (közvetlen, tegeződő játékos hangnem).

## 2. Mandatory Rules & Invariants (HARD CHECKS)
1. **Button & Control Tags:**
   - NEVER alter, translate, or drop tags like `[BTN:L2 ]`, `[BTN:RS ]`, `[BTN:R2 ]`, `[COLOR:2 ]`, `[HUDTEXT:...]`.
   - Preserve spacing inside tags: `[BTN:L2 ]` (not `[BTN:L2]`).
2. **HUD Objective Length Constraint (MAX 28 CHARACTERS):**
   - Keys containing `MISSION_PURPOSE` or `PURPOSE_FILE` MUST be 28 characters or fewer in Hungarian! The in-game HUD box truncates anything longer than 28 chars.
   - Example:
     - BAD (37 chars, truncated): "Gyűjts infót az eltűnt polgárokról."
     - GOOD (20 chars): "Infó az eltűntekről."
3. **Tutorial Button Prompts:**
   - Keep tutorial command prompts concise so button icons are not pushed off-screen.
   - Example: `[BTN:L2 ] + [BTN:RS ] (tartva): Legion mozgatása` (DO NOT write verbose sentences like "Tartsd nyomva a ... gombot...").
4. **Terminology:**
   - The entities are called **Legion** (Legionnel, Legionödet, Legionök). NEVER translate as "Légió"!
   - Neuron, IRIS, Legatus, X-Baton, Ark stay in canonical English form.
   - Chimera -> Kiméra.
   - Sync Attack -> Szinkrontámadás.
   - Perfect Call -> Tökéletes hívás.

## 3. Output Format
For every line where you find a mistranslation, text overflow (>28 chars for purpose strings), or unnatural phrasing, output:
- **Key / ID:** <key>
- **EN:** <English original>
- **Current HU:** <Current translation>
- **Suggested HU:** <Your corrected Hungarian translation>
- **Reason:** <Brief explanation>
```

---
"""
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"Created AI Review Prompt: {md_path}")

def generate_json_and_md_for_llms():
    """Generates compact JSON and Markdown bilingual exports ideal for feeding directly into LLMs."""
    md_export_path = os.path.join(OUTPUT_DIR, "Astral_Chain_Core_and_UI_Bilingual.md")
    json_export_path = os.path.join(OUTPUT_DIR, "Astral_Chain_Core_and_UI_Bilingual.json")

    files = [
        "Hud_text_USen.json",
        "GameWord_USen.json",
        "Option_text_USen.json",
        "Shop_text_USen.json",
        "Core_text_USen.json",
        "menu_text_USen.json",
        "CharaName_USen.json"
    ]

    all_data = {}
    md_lines = ["# Astral Chain — Core UI & System Bilingual Dataset\n"]

    for fname in files:
        fpath = os.path.join(BATCHES_DIR, fname)
        if not os.path.exists(fpath):
            continue
        with open(fpath, "r", encoding="utf-8") as fp:
            entries = json.load(fp)

        all_data[fname] = entries
        md_lines.append(f"\n## {fname} ({len(entries)} strings)\n")
        md_lines.append("| Key | English | Hungarian |")
        md_lines.append("| :--- | :--- | :--- |")
        for e in entries:
            key = e.get("key", f"ID_{e.get('id')}")
            en_txt = e.get("text", "").replace("\n", " ").replace("|", "\\|")
            hu_txt = e.get("HU", "").replace("\n", " ").replace("|", "\\|")
            md_lines.append(f"| {key} | {en_txt} | {hu_txt} |")

    with open(json_export_path, "w", encoding="utf-8") as fp:
        json.dump(all_data, fp, ensure_ascii=False, indent=2)
    print(f"Created JSON dataset: {json_export_path} ({os.path.getsize(json_export_path):,} bytes)")

    with open(md_export_path, "w", encoding="utf-8") as fp:
        fp.write("\n".join(md_lines))
    print(f"Created Markdown dataset: {md_export_path} ({os.path.getsize(md_export_path):,} bytes)")

def main():
    print("=== Generating Astral Chain Review Package ===")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    generate_ai_prompt_instructions()
    generate_json_and_md_for_llms()
    build_pdf_guide_and_core()
    print("=== All Review Documents Generated Successfully! ===")

if __name__ == "__main__":
    main()
