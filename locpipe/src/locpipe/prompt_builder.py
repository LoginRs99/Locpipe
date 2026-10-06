"""PromptBuilder: loads locpipe/agents/*.md and substitutes %%TOKEN%%
placeholders. The templates are static, project-agnostic English text
at rest — same principle as glossary.md/lang-style.md living outside
Python for a *project*, just applied to the instructions themselves.
Changing how the translator or reviewer is instructed is now a
markdown edit, not a Python edit.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_AGENTS_DIR = Path(__file__).parent / "agents"


def load_template(name: str) -> str:
    return _load_template_cached(name)


@lru_cache(maxsize=None)
def _load_template_cached(name: str) -> str:
    """Templates under agents/ are static for the lifetime of a process --
    they're read-only prompt text shipped with locpipe, never edited by a
    running pipeline. Caching means a project with thousands of batches
    reads translate.md/review.md off disk once instead of once per batch.
    """
    path = _AGENTS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Prompt template not found: {path}")
    return path.read_text(encoding="utf-8")


def fill(template: str, **values: str) -> str:
    for key, value in values.items():
        template = template.replace(f"%%{key.upper()}%%", value)
    return template


def get_register_instruction(target_register: str = "informal", target_lang: str = "hu") -> str:
    """Return explicit instruction for target language formality register,
    noting that character voice bibles override the project default for specific characters.
    """
    lang = str(target_lang).lower()
    is_formal = str(target_register).lower() == "formal"

    if lang in ("hu", "hungarian"):
        if is_formal:
            return (
                "Address the player formally throughout (magázódás — use 'Ön'/'Maga' forms, not 'te'), "
                "consistently across all UI, narration, and dialogue, UNLESS a specific character's voice "
                "entry in the character bible explicitly overrides this for their own lines."
            )
        return (
            "Address the player informally throughout (tegeződés — use 'te' forms, not 'Ön'), "
            "consistently across all UI, narration, and dialogue, UNLESS a specific character's voice "
            "entry in the character bible explicitly overrides this for their own lines."
        )

    if lang in ("ja", "japanese", "jpn"):
        if is_formal:
            return (
                "Use polite/formal Japanese register (丁寧語・敬語 — です/ます体) throughout all UI, narration, "
                "and dialogue, UNLESS a specific character's voice entry in the character bible explicitly "
                "overrides this with informal speech (タメ口) or a specific persona dialect."
            )
        return (
            "Use natural casual/informal Japanese register (普通体 — だ/である体 or conversational タメ口) "
            "throughout UI and dialogue, UNLESS a specific character's voice entry in the character bible "
            "explicitly requires formal honorific speech (敬語/丁寧語)."
        )

    if lang in ("en", "english"):
        if is_formal:
            return (
                "Maintain a formal, professional English register throughout, avoiding contractions and colloquial slang, "
                "consistently across all UI, narration, and dialogue, UNLESS a specific character's voice "
                "entry in the character bible explicitly overrides this."
            )
        return (
            "Maintain a natural, idiomatic and conversational English register throughout, using contractions where natural, "
            "consistently across all UI, narration, and dialogue, UNLESS a specific character's voice "
            "entry in the character bible explicitly overrides this."
        )

    if is_formal:
        return f"Maintain a formal and respectful register in {target_lang} throughout, unless overridden by a character voice entry."
    return f"Maintain a natural, informal and conversational register in {target_lang} throughout, unless overridden by a character voice entry."


def toggle_section(template: str, start_marker: str, end_marker: str, *, keep: bool) -> str:
    """A %%SECTION_START%% ... %%SECTION_END%% block: keep=True unwraps
    it (removes just the marker lines, leaves the content), keep=False
    removes the whole block. Used for translate.md's character-voice
    section, which only applies to categories that need it.
    """
    start_idx = template.find(start_marker)
    end_idx = template.find(end_marker)
    if start_idx == -1 or end_idx == -1:
        return template
    before = template[:start_idx]
    after = template[end_idx + len(end_marker):]
    if keep:
        middle = template[start_idx + len(start_marker):end_idx]
        return before + middle + after
    return before + after


_PLACEHOLDER_RULES = {
    "hu": (
        "- If target language is Hungarian (hu):\n"
        "  1. Always prefix dynamic placeholders, button glyphs, and variables with 'a(z) ' when a definite article "
        "is required (e.g. \"a(z) {0}\", \"a(z) [BTN:R2 ]-t\").\n"
        "  2. Hungarian marks grammatical case with suffixes whose exact form depends on the word they attach to. "
        "Use a HYPHENATED suffix directly after the placeholder (e.g. \"{0}-hoz\", \"{item}-t\", \"{0}-val/-vel\"). "
        "Never glue a suffix directly onto a placeholder/tag with no hyphen (e.g. \"{item}t\", \"{item}ban\")."
    ),
    "ja_target": (
        "- If target language is Japanese (ja):\n"
        "  Japanese attaches particles (は, が, を, に, で, へ, と, から, まで, の) naturally after placeholders "
        "(e.g. \"{0}を\", \"{player}の\").\n"
        "  Preserve all code tags, placeholders ({0}, %s, etc.), and control codes in exact half-width ASCII.\n"
        "  Convert dialogue quotes to standard Japanese corner brackets 「...」 and book/title brackets 『...』."
    ),
    "en": (
        "- If target language is English (en):\n"
        "  Preserve standard English word order, prepositions, and plural markers (e.g. \"{0} items\", \"{item}s\")."
    ),
    "ja_source": (
        "- If source language is Japanese (ja):\n"
        "  Japanese corner brackets 「...」 must be localized to appropriate quotation marks in the target language "
        "(\"...\" or „...\").\n"
        "  Preserve game engine escape codes (\\n, \\C[...], \\V[...], etc.) and ruby annotations accurately."
    ),
}


def get_placeholder_rules(source_lang: str = "en", target_lang: str = "hu") -> str:
    """Return the configured language pair's placeholder and particle rules."""
    src = str(source_lang).lower()
    tgt = str(target_lang).lower()
    rules = []
    if tgt in ("hu", "hungarian"):
        rules.append(_PLACEHOLDER_RULES["hu"])
    elif tgt in ("ja", "japanese", "jpn"):
        rules.append(_PLACEHOLDER_RULES["ja_target"])
    elif tgt in ("en", "english"):
        rules.append(_PLACEHOLDER_RULES["en"])
    if src in ("ja", "japanese", "jpn"):
        rules.append(_PLACEHOLDER_RULES["ja_source"])
    return "\n".join(rules)

