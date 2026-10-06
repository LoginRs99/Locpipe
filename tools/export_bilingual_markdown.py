#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
export_bilingual_markdown.py
Exports the entire, up-to-date bilingual dataset (English + Hungarian)
for Astral Chain into clean, comprehensive Markdown files:
1. Astral_Chain_Complete_Bilingual_All.md (All 27,213 entries across all 29 batches)
2. Astral_Chain_Core_and_UI_Bilingual.md (All UI, Menu, Core, Option, Shop, Chara entries)
3. Astral_Chain_Story_and_Dialogues_Bilingual.md (All TalkSubtitleMessage entries)
"""

import os
import json
import re

BATCHES_DIR = r"D:\github\GameStringer-main\locpipe\projects\Astral Chain\batches"
REVIEW_DIR = r"D:\github\Forditasok\AI_Translation_HU_dev\games\Astral chain\review"

def clean_cell(text: str) -> str:
    if not text:
        return ""
    # Normalize newlines to <br/> so markdown tables stay intact
    text = text.replace("\r\n", "<br/>").replace("\n", "<br/>")
    # Escape pipe characters
    text = text.replace("|", "\\|")
    return text

def write_bilingual_md(target_path: str, title: str, file_list: list[str]):
    print(f"Generating {os.path.basename(target_path)}...")
    total_entries = 0
    file_stats = []

    for fname in file_list:
        fpath = os.path.join(BATCHES_DIR, fname)
        if os.path.exists(fpath):
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
            file_stats.append((fname, len(data)))
            total_entries += len(data)

    with open(target_path, "w", encoding="utf-8") as out:
        out.write(f"# {title}\n\n")
        out.write(f"**Összes bejegyzés:** {total_entries:,} sor | **Fájlok száma:** {len(file_list)} db\n\n")
        
        # Table of Contents
        out.write("## Tartalomjegyzék\n\n")
        for fname, count in file_stats:
            anchor = fname.lower().replace(".", "").replace("_", "-")
            out.write(f"- [{fname}](#{anchor}) ({count:,} sor)\n")
        out.write("\n---\n\n")

        # Sections
        for fname, count in file_stats:
            anchor = fname.lower().replace(".", "").replace("_", "-")
            out.write(f"<a name=\"{anchor}\"></a>\n")
            out.write(f"## {fname} ({count:,} sor)\n\n")
            out.write("| Key / ID | Angol eredeti (EN) | Magyar fordítás (HU) |\n")
            out.write("| :--- | :--- | :--- |\n")

            fpath = os.path.join(BATCHES_DIR, fname)
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)

            for e in data:
                key = e.get("key", f"ID_{e.get('id')}")
                en = clean_cell(e.get("text", ""))
                hu = clean_cell(e.get("HU", ""))
                out.write(f"| {key} | {en} | {hu} |\n")

            out.write("\n---\n\n")

    size_mb = os.path.getsize(target_path) / (1024 * 1024)
    print(f"  Saved {os.path.basename(target_path)}: {total_entries:,} rows ({size_mb:.2f} MB)")

def main():
    os.makedirs(REVIEW_DIR, exist_ok=True)
    all_files = sorted([f for f in os.listdir(BATCHES_DIR) if f.endswith(".json")])

    # 1. Complete All-in-One Markdown
    all_md_path = os.path.join(REVIEW_DIR, "Astral_Chain_Complete_Bilingual_All.md")
    write_bilingual_md(
        all_md_path,
        "Astral Chain (Nintendo Switch) — Teljes Kétnyelvű Adatbázis (HU / EN)",
        all_files
    )

    # 2. Core & UI Split Markdown
    core_ui_files = [f for f in all_files if f != "TalkSubtitleMessage_USen.json"]
    core_ui_md_path = os.path.join(REVIEW_DIR, "Astral_Chain_Core_and_UI_Bilingual.md")
    write_bilingual_md(
        core_ui_md_path,
        "Astral Chain — Rendszer, Menü és UI Kétnyelvű Adatbázis",
        core_ui_files
    )

    # 3. Story & Dialogue Split Markdown
    story_files = ["TalkSubtitleMessage_USen.json"]
    story_md_path = os.path.join(REVIEW_DIR, "Astral_Chain_Story_and_Dialogues_Bilingual.md")
    write_bilingual_md(
        story_md_path,
        "Astral Chain — Történet, Párbeszédek és Átvezetők Kétnyelvű Adatbázis",
        story_files
    )

    print("\n=== Minden kétnyelvű Markdown fájl sikeresen legenerálva! ===")

if __name__ == "__main__":
    main()
