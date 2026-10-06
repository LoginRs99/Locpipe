# GameStringer — LocPipe Desktop Preflight & Translation Manager

> **Unified Localization Pipeline & Preflight GUI** wrapping the `locpipe` deterministic translation engine with **Google Antigravity CLI** (`gemini-3.8-flash`) as the sole LLM provider.

---

## 🎯 Architecture & Scope

GameStringer consolidates video game and software localization into a clean, deterministic pipeline:
- **Dual Localization Modes**: Specialized modes for Video Games (`project_type: game`) with character voices, dialogue, and RPG/VN tags, and Desktop/Web Software (`project_type: software`) with UI action verbs, menu hierarchies, accelerator keys (`&File`), and shortcuts (`Ctrl+S`).
- **Multilingual & Cross-Translation**: Fully supports any source and target language pair (`en`, `ja`, `hu`, `de`, `fr`, `es`, `zh`, etc.) with language-specific formality registers, script-aware expansion caps, and Japanese quote pairing (`「...」`).
- **Engine Rules & Physical Limits**: Strict deterministic preservation for control codes, escape characters (`\n`, `\t`), RPG Maker/VN tags, Ruby/Furigana markup, gender slots, and hard `max_length` limits with automatic Tier 1 mechanical shortening.
- **Sole LLM Provider**: Hardened Antigravity CLI (`gemini-3.8-flash`) integration with automatic retry, exponential backoff, orphan conversation cleanup, and zero manual token waste.
- **Tkinter Desktop GUI (`gamestringer-gui`)**: 4 focused tabs for managing projects, inspecting engine noise, checking font glyphs, and streaming live translations.
- **Zero Binary Extraction Inside Tool**: Text dumping and reimporting is done externally via standard community tools (e.g. **UABEA** for Unity dumps, **Unreal Localization Dashboard** PO export, standard JSON/PO/XLIFF).

---

## 🔄 End-to-End Workflow

```
1. Manual Extraction (External)
   ├── Naninovel: Visual novel localization scripts & managed text (*.txt)
   ├── Unity: UABEA JSON export or Unity Localization CSV
   ├── Unreal: Localization Dashboard .po export (ue4_5_po)
   └── Software / Generic: PO gettext, XLIFF 1.2, or JSON key-value
           │
           ▼
2. Project Setup (`gamestringer-gui` -> Projects Tab or `locpipe init`)
   ├── Choose Project Type: Game or Software
   ├── Configure source_lang, target_lang, format, batch_glob
   └── Drop extracted batch files into projects/<name>/batches/
           │
           ▼
3. Preflight & Noise Audit (Preflight & Audit Tabs)
   ├── Check Hungarian font glyph compatibility (ő/ű/Ő/Ű via check-fonts)
   └── Run non-LLM extraction audit to inspect noise & 1-click exclude junk paths
           │
           ▼
4. Plan (Run Tab)
   └── Run `locpipe plan` for dry-run deduplication, batch counts & token estimates (0 API cost)
           │
           ▼
5. Translation (Run Tab or External PowerShell)
   └── Run `locpipe run` via Antigravity CLI (Gemini 3.8 Flash) with live log streaming
           │
           ▼
6. Reimport & Post-Patch Fix
   ├── Manually reimport translated files into the game/software
   └── Run Catalog CRC Fixer (`gamestringer fix-catalog`) for Unity Addressables
```

---

## 📦 Installation

```bash
# Clone the repository
git clone https://github.com/LoginRs99/GameStringer.git
cd GameStringer

# Install in editable mode (Python 3.10+)
pip install -e .

# Launch Desktop GUI
gamestringer-gui

# Run automated tests (221 tests, 100% pass)
pytest
```

### Dependencies
- Python 3.10+
- `click>=8.0.0`
- `pyyaml>=6.0`
- `jsonschema>=4.20`
- `polib>=1.2`
- **Antigravity CLI** (`agy` on PATH, authenticated via `agy auth login`)

---

## 🖥️ Desktop GUI Tabs (`gamestringer-gui` or `gamestringer gui`)

1. **📁 Projects Tab**: List, scaffold, and configure `project.yaml` files (Type, languages, format adapters, batch globs, character replacements, category rules, P2 batch output token caps, P13 naturalness gating).
2. **🔍 Preflight & Fixes Tab**: Run Hungarian font compatibility checks on Unity/IL2CPP assets and recalculate Addressables `catalog.json` CRC32 checksums.
3. **🔇 Audit Noise Tab**: Run `locpipe audit` to view translatable text vs. engine noise, and one-click append exclusion patterns to `project.yaml`.
4. **🚀 Plan & Run Tab**: Run dry pre-flight token estimates (`locpipe plan`) and execute live Antigravity CLI translation (`locpipe run`) with real-time log streaming.

---

## ⚙️ CLI Reference

### Unified GameStringer CLI (`gamestringer`)
`gamestringer` provides a single unified entry point for all localization pipeline tools and preflight utilities:

```bash
# Launch Desktop GUI
gamestringer gui

# Pre-flight plan and token estimate (dry run, 0 API tokens)
gamestringer plan --project "locpipe/projects/<project_name>"

# Audit extraction noise and format excludes (no LLM calls)
gamestringer audit --project "locpipe/projects/<project_name>"

# Run translation pipeline with Antigravity CLI
gamestringer run --project "locpipe/projects/<project_name>" [--limit N] [--max-api-calls N]

# Verify post-run merge integrity
gamestringer verify --project "locpipe/projects/<project_name>"

# Check Unity/IL2CPP font assets for Hungarian ő/ű glyph support
gamestringer check-fonts --input "path/to/game_dir" --engine unity

# Recalculate CRC32 checksums for modified AssetBundles and update catalog.json
gamestringer fix-catalog --input "path/to/game_dir"
```

### Standalone LocPipe CLI (`locpipe`)
All pipeline commands can also be run directly via `locpipe`:
```bash
locpipe init <project_name> [--type game|software] [--source en] [--target hu] [--format generic_kv]
locpipe plan --project "locpipe/projects/<project_name>"
locpipe audit --project "locpipe/projects/<project_name>"
locpipe run --project "locpipe/projects/<project_name>"
locpipe verify --project "locpipe/projects/<project_name>"
locpipe bootstrap-resources --project "locpipe/projects/<project_name>"
```

---

## 📂 Project Structure

```
GameStringer-main/
├── gamestringer/                  # GUI and standalone preflight/post-patch utilities
│   ├── cli.py                     # check-fonts & fix-catalog Click commands
│   ├── __main__.py                # CLI / GUI entry point
│   ├── core/                      # font_checker, addressables_crc, backup, quote_checker, logger
│   └── desktop_gui/               # 4-tab Tkinter GUI (app.py, theme.py, tabs/)
├── locpipe/                       # LocPipe deterministic translation engine
│   ├── pyproject.toml             # Standalone locpipe package spec
│   ├── src/locpipe/               # Pipeline, adapters (naninovel, uabea_json, po, unity, xliff), providers (antigravity_cli)
│   └── tests/                     # LocPipe test suite
├── archive/                       # Archived legacy extraction scripts & test packs
├── pyproject.toml                 # Root unified package configuration
├── test_cli.py                    # GameStringer utility test suite
├── test_gui.py                    # Desktop GUI component test suite
└── test_gamestringer_core_extra.py # Core & CLI comprehensive test suite
```
