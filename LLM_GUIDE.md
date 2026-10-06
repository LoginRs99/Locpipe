# LLM_GUIDE.md — Operational Manual for AI Agents & Developers

Welcome, Agent. This document is the authoritative engineering manual for operating, extending, and translating with **GameStringer** and **LocPipe**.

LocPipe is a deterministic, LLM-powered video game localization pipeline built on strict separation of concerns:
* **The LLM's only role:** High-fidelity translation of natural language within given context constraints.
* **The Pipeline's role:** File I/O, format parsing, tag masking, deduplication, translation memory (TM), validation, checkpointing, and integrity verification.

---

## 1. Optimal Operational Path for AI Agents

When tasked with localizing a game project under `locpipe/projects/<Project Name>/`, execute the workflow in these sequential stages:

```mermaid
flowchart TD
    A["1. Preflight Audit<br/>(locpipe audit)"] --> B["2. Staged Canary Test<br/>(locpipe run --limit 1)"]
    B --> C{"Canary Passed?<br/>(Check tags & 1:1 IDs)"}
    C -->|Yes| D["3. Full Unattended Run<br/>(External Terminal / locpipe auto)"]
    C -->|No| E["Fix project.yaml / prompts"]
    E --> A
    D --> F["4. Post-Merge Verification<br/>(locpipe verify)"]
    F --> G["5. Inspect Bilingual & Review Reports"]
```

### Stage 1: Preflight Audit (Zero Cost)
Always run audit first to verify extraction and noise filtering without calling any LLMs:
```bash
locpipe audit --project "locpipe/projects/<Project Name>"
```
* Verify kept vs. engine-noise breakdown.
* Ensure dialogue and UI strings are not mistakenly caught in noise filters.

### Stage 2: Staged Canary Test (Mandatory Safety Checkpoint)
Never execute a full unattended run on a new or modified project without a bounded canary test:
```bash
locpipe run --project "locpipe/projects/<Project Name>" --limit 1 --max-api-calls 20
```
* Inspect sample lines (Source → Target).
* Verify 1:1 array index alignment.
* Verify tag and placeholder survival.
* **STOP and report results to the human operator** before proceeding to the full run.

### Stage 3: Full Run Execution (Zero-Touch or External Terminal)
Once approved, execute the full localization run:
* **Autonomous Single Command:**
  ```bash
  locpipe auto --project "locpipe/projects/<Project Name>"
  ```
* **External PowerShell Terminal (Recommended for large runs):**
  Advise the user to run `locpipe run` in an external terminal window to prevent subagent session flooding in IDE conversation transcripts.

### Stage 4: Post-Run Integrity Verification
Prove that non-translatable engine noise and excluded asset paths were completely untouched:
```bash
locpipe verify --project "locpipe/projects/<Project Name>"
```

---

## 2. Recommended LLM Architectures by Task

Choose the optimal LLM architecture based on task complexity, privacy requirements, and operational constraints:

| Task Type | Recommended Models | Key Strengths | Configuration Notes |
|---|---|---|---|
| **Bulk Batch Translation (Fast & Economical)** | **Gemini 2.5 / 3.0 Flash**<br/>`gemini-3.8-flash` | Ultra-low latency, large context window, zero truncation retries. | `effort: low`<br/>`batch_size: 200` |
| **Complex Lore, Cutscenes & Creative Dialogue** | **DeepSeek-V3**<br/>**DeepSeek-R1**<br/>**Claude 3.5 Sonnet** | Deep semantic reasoning, natural dialogue rhythm, zero calques/idiom translations into Hungarian/Japanese. | Local via vLLM/Ollama or API.<br/>`effort: high` |
| **High-Density UI, Menus & Code Placeholders** | **Qwen-2.5-72B-Instruct**<br/>**Qwen-2.5-Coder-32B** | Outstanding ASCII code preservation, strict JSON schema compliance, zero tag hallucination. | Local via vLLM/Ollama.<br/>`batch_size: 100-150` |
| **Intelligent Review & Escalation Repair** | **Gemini Pro**<br/>**DeepSeek-R1**<br/>**Claude 3.5 Sonnet** | Surgical precision for repairing flagged items without modifying unaffected surrounding text. | `review_effort: high`<br/>`review_batch_size: 25` |

### Using Local Open-Weight Models (DeepSeek / Qwen)
For air-gapped or cost-free game localization, deploy DeepSeek or Qwen via an OpenAI-compatible local server (vLLM, Ollama, LM Studio):
```yaml
provider:
  name: openai_compatible   # or custom provider adapter
  base_url: "http://localhost:11434/v1"
  model: "deepseek-r1:32b"  # or "qwen2.5:72b"
  max_output_tokens: 8192
```

---

## 3. Absolute, Non-Negotiable Guardrails

Every AI agent and prompt modification must strictly respect these immutable localization rules:

### Rule 1: Mathematical 1:1 Index Alignment
* The input JSON is an array of objects `[{"id": 0, "source": "..."}, ...]`.
* The output JSON must return an identical array `[{"id": 0, "translation": "..."}, ...]` matching every single ID.
* Never reorder, drop, or concatenate items.

### Rule 2: Absolute Code, Tag & Placeholder Preservation
The following tokens must survive translation **100% byte-identical**:
* **Rich Text & Engine Tags:** `<color=#ff0000>`, `</color>`, `<b>`, `</b>`, `<i>`, `</i>`, `<size=14>`, `<quad ... />`, `<RichText.Bold>`, `<style="Header">`.
* **Printf Specifiers:** `%s`, `%d`, `%f`, `%.2f`, `%02d`, `%lld`, `%zu`, `%X`, `%%`.
* **Bracket Tokens & Controller Glyphs:** `[BTN:L2 ]`, `[BTN:RS ]`, `[player_name]`, `[ITEM_ID]`, `[flag!q]`.
* **Game Markers:** `@primary attack@`, `@aberrations@`.
* **Escape Characters:** `\n` (literal backslash-n), `\r`, `\t`, `\"`.
* **Visual Novel Escape Codes:** `\C[1]`, `\V[2]`, `\I[3]`, `\G`, `\!`.

> [!TIP]
> **Deterministic Token Masking (`TokenMasker`):**
> When working with fragile custom game engines, use `TokenMasker.mask(source)` to replace all engine tokens with immutable sentinels (`⟦T0⟧`, `⟦T1⟧`). After LLM translation, call `TokenMasker.unmask(target, token_map)` to restore original tags with mathematical certainty.

### Rule 3: Target Grammar Attachment on Placeholders
* **Hungarian (`hu`):**
  * When a definite article is required before a placeholder or button glyph, always use `a(z) `:
    * ✅ `a(z) {0}`, `a(z) [BTN:R2 ]-t`
    * ❌ `a {0}`, `az [BTN:R2 ]`
  * Attach case endings with a **hyphen**:
    * ✅ `{item}-t`, `{0}-hoz`, `[BTN:L2 ]-vel`
    * ❌ `{item}t`, `{0}hoz` (corrupts variable delimiter)
* **Japanese (`ja`):**
  * Attach particles naturally after ASCII placeholders: `{0}を`, `{player}の`.
  * Preserve exact half-width ASCII for all tag tokens.

### Rule 4: Anti-Fabrication & Anti-Omission
* Never invent characters, stats, numbers, or details not present in the source string.
* Never omit meaningful source sentences or dialogue beats.
* Do not pad strings with explanatory clauses or disclaimers.

### Rule 5: De-Anglicization of Figurative Idioms
* Never translate English figurative idioms literally word-for-word into the target language.
* Example: `"pull the plug"` → translate as tactical meaning (*"szakítsd meg a kapcsolatot"*, *"állítsd le rögtön"*), NEVER literal *"húzd ki a dugót"*.
* Example: `"carry the world on your shoulders"` → translate as *"nem kell egyedül cipelned a világ terhét"*.

### Rule 6: Avoid Passive Voice in Game UI
* Do not translate English passive notifications (*"detected"*, *"unlocked"*, *"equipped"*) into stiff passive participles (*"-va/-ve észlelve"*).
* Use punchy active verbs or concise nominal statements:
  * ✅ *"Új fegyver elérhető!"* or *"Feloldva!"*
  * ❌ *"Új fegyver fel lett oldva."*

---

## 4. Test Isolation & Environment Cleanliness

* Always execute test runs via `uv run pytest` inside `locpipe/`.
* All test fixtures MUST utilize `tmp_path` for temporary directories and SQLite databases.
* Never commit `.sqlite3` databases, `.draft.md` files, or raw game dumps to Git.
