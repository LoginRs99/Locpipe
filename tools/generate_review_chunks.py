#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_review_chunks.py
Splits the Astral Chain localization review package into bite-sized, self-contained micro-chunks
(both Markdown and PDF) sized perfectly for LLM prompt windows (Kimi k2/k3, GLM-4/5.3)
preventing any 'message cut off' limits by strictly requiring tabular error-only output.
"""

import os
import json
import html
import textwrap
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
CHUNKS_DIR = r"D:\github\Forditasok\AI_Translation_HU_dev\games\Astral chain\review\chunks"

pdfmetrics.registerFont(TTFont("Arial", "C:/Windows/Fonts/arial.ttf"))
pdfmetrics.registerFont(TTFont("Arial-Bold", "C:/Windows/Fonts/arialbd.ttf"))
pdfmetrics.registerFont(TTFont("Arial-Italic", "C:/Windows/Fonts/ariali.ttf"))

SYSTEM_PROMPT_HEADER = """# ASTRAL CHAIN (Nintendo Switch) — Lokalizációs Felülvizsgálat
### Feladat Kimi k3 / GLM-5.3 számára

**Szerep:** Vezető videojáték lokalizációs lektor (Angol -> Magyar).
**Kontextus:** 2078, The Ark megaváros. Idegen lények: Kimérák (Chimeras). Különítmény: Neuron. Fegyverek: befogott kimérák, a **Legion** (Kard, Nyíl, Kar, Fenevad, Fejsze). Hangnem: közvetlen, tegeződő játékos hangnem (informal), lendületes anime/cyberpunk akció.

**SZIGORÚ SZABÁLYOK:**
1. **Gombok és Vezérlőkódok:** A `[BTN:...]`, `[COLOR:...]`, `[HUDTEXT:...]` tag-eket tilos módosítani, törölni vagy lefordítani! A szóközt a zárójel előtt meg kell tartani (pl. `[BTN:L2 ]`).
2. **HUD Célkitűzések (MISSION_PURPOSE / PURPOSE_FILE):** A magyar szöveg **MAXIMUM 28 KARAKTER** lehet! Minden ennél hosszabb szöveg lecsúszik a képernyőről a játékban.
3. **Terminológia:**
   - A lények neve: **Legion** (Legionnel, Legionödet, Legionök). **SZIGORÚAN TILOS 'Légió'-nak fordítani!**
   - Neuron, IRIS, Legatus, X-Baton, Ark marad angolul.
   - Chimera -> Kiméra, Sync Attack -> Szinkrontámadás, Perfect Call -> Tökéletes hívás.
4. **FONTOS KIMENETI SZABÁLY (A cut-off / üzenet-megszakadás elkerüléséhez):**
   - **TILOS a helyes sorokat végigelemezni vagy listázni!**
   - A helyes vagy elfogadható sorokról **SEMMIT ne írj!**
   - **KIZÁRÓLAG egyetlen tömör Markdown táblázatot adj ki**, amely CSAK a javítandó sorokat tartalmazza:

| Key | Angol (EN) | Hibás HU | Javasolt HU | Hiba oka |
| :--- | :--- | :--- | :--- | :--- |

   - Ha az adott részletben egyetlen hiba sincs, csak ennyit válaszolj: **"Minden sor hibátlan."**

---
"""

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
        self.saveState()
        self.setFont("Arial", 8)
        self.setFillColor(colors.HexColor("#718096"))
        self.drawString(40, 810, "Astral Chain — Lokalizációs Felülvizsgálat (Micro-Chunk)")
        self.setStrokeColor(colors.HexColor("#E2E8F0"))
        self.setLineWidth(0.5)
        self.line(40, 804, 555, 804)
        page_str = f"Oldal {self._pageNumber} / {page_count}"
        self.drawRightString(555, 25, page_str)
        self.drawString(40, 25, "Kimi / GLM QA — Csak a hibás sorokat listázd táblázatban!")
        self.line(40, 35, 555, 35)
        self.restoreState()

def escape_text(text: str) -> str:
    if not text:
        return ""
    text = html.escape(text)
    return text.replace("\r\n", "<br/>").replace("\n", "<br/>")

def create_chunk_files(chunk_id: str, title: str, entries: list):
    if not entries:
        return

    # 1. Markdown file
    md_filename = f"{chunk_id}_{title}.md"
    md_path = os.path.join(CHUNKS_DIR, md_filename)
    
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(SYSTEM_PROMPT_HEADER)
        f.write(f"\n## {chunk_id}: {title.replace('_', ' ')} ({len(entries)} bejegyzés)\n\n")
        f.write("| Key | Angol (EN) | Magyar (HU) |\n")
        f.write("| :--- | :--- | :--- |\n")
        for e in entries:
            key = e.get("key", f"ID_{e.get('id')}")
            en = e.get("text", "").replace("\r\n", " ").replace("\n", " ").replace("|", "\\|")
            hu = e.get("HU", "").replace("\r\n", " ").replace("\n", " ").replace("|", "\\|")
            f.write(f"| {key} | {en} | {hu} |\n")

    # 2. PDF file
    pdf_filename = f"{chunk_id}_{title}.pdf"
    pdf_path = os.path.join(CHUNKS_DIR, pdf_filename)

    doc = SimpleDocTemplate(pdf_path, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=45, bottomMargin=45)
    
    styles = getSampleStyleSheet()
    h1_style = ParagraphStyle("H1", fontName="Arial-Bold", fontSize=13, leading=16, textColor=colors.HexColor("#1A365D"), spaceAfter=5)
    sub_style = ParagraphStyle("Sub", fontName="Arial", fontSize=8.5, leading=11, textColor=colors.HexColor("#4A5568"), spaceAfter=8)
    rule_style = ParagraphStyle("Rule", fontName="Arial", fontSize=7.5, leading=10, textColor=colors.HexColor("#2D3748"))
    
    cell_key = ParagraphStyle("CK", fontName="Arial", fontSize=7, leading=9, textColor=colors.HexColor("#718096"))
    cell_en = ParagraphStyle("CEN", fontName="Arial", fontSize=7.5, leading=9.5, textColor=colors.HexColor("#1A202C"))
    cell_hu = ParagraphStyle("CHU", fontName="Arial", fontSize=7.5, leading=9.5, textColor=colors.HexColor("#2B6CB0"))

    story = [
        Paragraph(f"<b>{chunk_id}: {title.replace('_', ' ')}</b>", h1_style),
        Paragraph(f"Astral Chain Nintendo Switch — Ellenőrző csomag ({len(entries)} bejegyzés)", sub_style),
        Paragraph("<b>Kritikus szabályok:</b> 1. <code>[BTN:...]</code> épen hagyása | 2. <code>MISSION_PURPOSE</code> max. 28 karakter | 3. Kizárólag <b>Legion</b> (soha nem 'Légió')<br/>"
                  "<b>Kimeneti utasítás:</b> Csak egy táblázatot adj ki a hibás/javítandó sorokról! A jó sorokról semmit se írj!", rule_style),
        Spacer(1, 6),
        HRFlowable(width="100%", thickness=1, color=colors.HexColor("#CBD5E0"), spaceAfter=8)
    ]

    table_rows = [
        [Paragraph("<b>Kulcs</b>", cell_key), Paragraph("<b>Angol Eredeti (EN)</b>", cell_key), Paragraph("<b>Magyar Fordítás (HU)</b>", cell_key)]
    ]

    for e in entries:
        key = e.get("key", f"ID_{e.get('id')}")
        raw_en = e.get("text", "")
        raw_hu = e.get("HU", "")

        if len(raw_en) > 350 or len(raw_hu) > 350:
            enc = textwrap.wrap(raw_en, 350) or [raw_en]
            huc = textwrap.wrap(raw_hu, 350) or [raw_hu]
            for i in range(max(len(enc), len(huc))):
                ec = enc[i] if i < len(enc) else ""
                hc = huc[i] if i < len(huc) else ""
                table_rows.append([
                    Paragraph(f"{key} [{i+1}]", cell_key),
                    Paragraph(escape_text(ec), cell_en),
                    Paragraph(escape_text(hc), cell_hu)
                ])
        else:
            table_rows.append([
                Paragraph(key, cell_key),
                Paragraph(escape_text(raw_en), cell_en),
                Paragraph(escape_text(raw_hu), cell_hu)
            ])

    t_data = Table(table_rows, colWidths=[115, 200, 205], repeatRows=1)
    t_data.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#EDF2F7")),
        ('GRID', (0,0), (-1,-1), 0.3, colors.HexColor("#CBD5E0")),
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [colors.white, colors.HexColor("#F7FAFC")]),
    ]))
    story.append(t_data)

    doc.build(story, canvasmaker=NumberedCanvas)
    print(f"  Created: {md_filename} & {pdf_filename} ({len(entries)} entries)")

def split_list(lst: list, chunk_size: int) -> list:
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]

def main():
    print("=== Generating Micro Review Chunks for Kimi / GLM ===")
    os.makedirs(CHUNKS_DIR, exist_ok=True)

    # Clean existing old chunks
    for f in os.listdir(CHUNKS_DIR):
        if f.endswith(".md") or f.endswith(".pdf"):
            os.remove(os.path.join(CHUNKS_DIR, f))

    # 1. Part 01: HUD, Options, Shop, GameWords
    hud = json.load(open(os.path.join(BATCHES_DIR, "Hud_text_USen.json"), encoding="utf-8"))
    options = json.load(open(os.path.join(BATCHES_DIR, "Option_text_USen.json"), encoding="utf-8"))
    shop = json.load(open(os.path.join(BATCHES_DIR, "Shop_text_USen.json"), encoding="utf-8"))
    gw = json.load(open(os.path.join(BATCHES_DIR, "GameWord_USen.json"), encoding="utf-8"))

    gw_bosses = [e for e in gw if e.get("key", "").startswith("EVENT_BOSS")]
    gw_missions = [e for e in gw if not e.get("key", "").startswith("EVENT_BOSS")]

    create_chunk_files("Part01A", "HUD_and_UI_Options", hud + options + shop)
    create_chunk_files("Part01B", "Boss_and_Chimera_Names", gw_bosses)
    create_chunk_files("Part01C", "Mission_Titles_and_Orders", gw_missions)

    # 2. Part 02: Character Names (split into 2 parts of ~420 short names)
    chara = json.load(open(os.path.join(BATCHES_DIR, "CharaName_USen.json"), encoding="utf-8"))
    chara_parts = split_list(chara, len(chara)//2 + 1)
    for idx, part in enumerate(chara_parts, 1):
        create_chunk_files(f"Part02_{idx}", f"Character_Names_Part{idx}", part)

    # 3. Part 03: Core Mission Purposes (Critical length <= 28)
    core = json.load(open(os.path.join(BATCHES_DIR, "Core_text_USen.json"), encoding="utf-8"))
    purposes = [e for e in core if "PURPOSE" in e.get("key", "")]
    create_chunk_files("Part03", "Core_Mission_Purposes_Max28Len", purposes)

    # 4. Part 04: Core Tutorials (split into 4 parts of ~250 entries)
    tutos = [e for e in core if "TUTO" in e.get("key", "")]
    tutos_parts = split_list(tutos, 250)
    for idx, part in enumerate(tutos_parts, 1):
        create_chunk_files(f"Part04_{idx}", f"Core_Tutorials_Part{idx}", part)

    # 5. Part 05: Core Items & Abilities (split into 4 parts of ~250 entries)
    items_and_abilities = [e for e in core if any(x in e.get("key", "") for x in ["ITEM", "ABILITY", "SKILL"])]
    item_parts = split_list(items_and_abilities, 250)
    for idx, part in enumerate(item_parts, 1):
        create_chunk_files(f"Part05_{idx}", f"Core_Items_and_Abilities_Part{idx}", part)

    # 6. Part 06: Other Core System Strings (split into 3 parts of ~250 entries)
    other_core = [e for e in core if e not in purposes and e not in tutos and e not in items_and_abilities]
    core_parts = split_list(other_core, 250)
    for idx, part in enumerate(core_parts, 1):
        create_chunk_files(f"Part06_{idx}", f"Core_System_and_Keywords_Part{idx}", part)

    # 7. Part 07: Menu Text (split into 4 parts of ~375 entries)
    menu = json.load(open(os.path.join(BATCHES_DIR, "menu_text_USen.json"), encoding="utf-8"))
    menu_parts = split_list(menu, 375)
    for idx, part in enumerate(menu_parts, 1):
        create_chunk_files(f"Part07_{idx}", f"Menu_Text_Part{idx}", part)

    # 8. Part 08: Story & Dialogues (split into 28 parts of ~600 entries)
    talk = json.load(open(os.path.join(BATCHES_DIR, "TalkSubtitleMessage_USen.json"), encoding="utf-8"))
    talk_parts = split_list(talk, 600)
    for idx, part in enumerate(talk_parts, 1):
        create_chunk_files(f"Part08_{idx:02d}", f"Story_and_Dialogues_Part{idx:02d}", part)

    print("=== All Micro-Chunks Successfully Generated! ===")

if __name__ == "__main__":
    main()
