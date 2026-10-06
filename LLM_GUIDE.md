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

## 2. Production LLM Engine & Future Architecture

### Current Production Engine: Google Gemini 3.8 Flash
In production, GameStringer and LocPipe use **Google Gemini 3.8 Flash** via the hardened Antigravity CLI provider (`antigravity_cli`). Local AI models (such as Ollama, DeepSeek, or Qwen) are **not currently used in active production**.

| Task Role | Model | Effort Level | Batch Size | Rationale |
|---|---|---|---|---|
| **Bulk Batch Translation** | `gemini-3.8-flash` | `effort: low` | `batch_size: 200` | Ultra-fast throughput, wide context window, zero token truncation, cost-effective. |
| **Review & Automated Repair** | `gemini-3.8-flash` | `effort: high` | `review_batch_size: 25` | Increased reasoning depth for fixing flagged token errors, length limits, or tone mismatches. |

Standard `project.yaml` provider configuration:
```yaml
provider:
  name: antigravity_cli
  model: gemini-3.8-flash
  effort: low
  review_model: gemini-3.8-flash
  review_effort: high
  mode: sync
  max_concurrency: 2
```

---

### Future Extensibility: Is Local AI Support Built-In?
**Yes, the architecture is designed from the ground up to support local models in the future.**

The core pipeline (`pipeline.py`, `batcher.py`, `dedupe.py`, `checkpoint.py`, `validators/`) is completely decoupled from any specific LLM vendor. It interacts strictly with an abstract interface defined in `locpipe/src/locpipe/providers/base.py`:

```python
class TranslationProvider(ABC):
    @abstractmethod
    async def complete(
        self,
        system_prompt: str,
        user_payload: str,
        *,
        max_tokens: int,
        effort: Optional[str] = None,
        response_format: str = "json",
    ) -> str:
        """Prompt in, raw JSON string out."""
```

#### How to Add Local AI Models in the Future:
To enable a local model (such as DeepSeek-R1 or Qwen-2.5 running locally in Ollama, LM Studio, or vLLM), only two small steps are required:
1. Implement a new subclass (e.g. `providers/openai_compatible.py`) implementing `complete()` using standard HTTP POST requests to `http://localhost:11434/v1/chat/completions`.
2. Register the provider name in `cli.py` and `config.py`.

All prompt building, engine token masking, retry loops, translation memory caching, format adapters, and post-merge validation will work automatically with zero changes to pipeline code.

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
