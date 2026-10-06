"""Deterministic fake provider so the rest of the pipeline (dedup, TM,
validation, confidence scoring, merge) can be exercised end-to-end in
tests and `locpipe run --dry-run` without hitting a real API or
needing a key configured. Not meant for real translation output.

Handles both payload shapes the pipeline actually sends — this was a
real bug (not a hypothetical one) until it was caught in testing:
schemas.build_user_payload() sends a flat list for bulk translation,
reviewer.build_review_payload() sends a {"glossary", "items"} dict for
the repair step. A provider that only understood one shape would crash
--dry-run the first time anything got routed to review.
"""

from __future__ import annotations

import json

from .base import TranslationProvider


class MockProvider(TranslationProvider):
    """Echoes each source string with a marker prefix, preserving
    placeholders exactly (so downstream placeholder validation has
    something meaningful to check against either way).
    """

    persists_to_tm = False

    async def complete(
        self,
        system_prompt: str,
        user_payload: str,
        *,
        max_tokens: int = 8192,
        effort: Optional[str] = None,
        response_format: str = "json",
    ) -> str:
        payload_json = user_payload.split("\n\n(Your previous response was invalid:")[0]
        parsed = json.loads(payload_json)

        # 1. Bulk-translation shape: [{"id": 0, "source": "..."}, ...]
        if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict) and "id" in parsed[0]:
            out = [{"id": item["id"], "translation": f"MOCK-HU: {item['source']}"} for item in parsed]
            return json.dumps(out, ensure_ascii=False)

        # 2. Review shape: {"items": [{"key": "...", "source": "..."}, ...]}
        if isinstance(parsed, dict) and "items" in parsed:
            out = [
                {
                    "key": item["key"],
                    "translation": f"MOCK-REVIEWED: {item['source']}",
                    "flag_for_human": False,
                    "reason": "",
                }
                for item in parsed["items"]
            ]
            return json.dumps(out, ensure_ascii=False)

        # 3. Bootstrap Glossary shape
        if "terminology" in system_prompt.lower() or "glossary-bootstrap" in system_prompt.lower() or ("# Glossary" in system_prompt and "Category" in system_prompt):
            lines = [
                "# Glossary\n\n| Source term | Target translation | Category | Confidence | Source/justification |\n|---|---|---|---|---|"
            ]
            sample = parsed if isinstance(parsed, list) else []
            for item in sample[:5]:
                src = item.get("source", "Term") if isinstance(item, dict) else "Term"
                tgt = item.get("translation", f"MOCK-HU: {src}") if isinstance(item, dict) else "MOCK-HU:"
                cat = item.get("category", "mechanic") if isinstance(item, dict) else "mechanic"
                lines.append(f"| {src} | {tgt} | {cat} | 1.0 | Mock term |")
            return "\n".join(lines)

        # 4. Bootstrap Language Style Guide shape
        if "style director" in system_prompt.lower() or "# Language style guide" in system_prompt:
            return "# Language style guide\n\n- Informal register (tegezés)\n- Standard Hungarian focus syntax\n"

        # 5. Bootstrap Character Voice shape
        if "voice bible" in system_prompt.lower() or "# Character voice bible" in system_prompt:
            return "# Character voice bible\n\n| Character | Register | Traits | Avoid |\n|---|---|---|---|\n| Hero | informal | courageous | slang |\n"

        if isinstance(parsed, list):
            out = [{"id": idx, "translation": f"MOCK-HU: {item.get('source', '')}"} for idx, item in enumerate(parsed)]
            return json.dumps(out, ensure_ascii=False)

        if isinstance(parsed, dict) and "items" in parsed:  # review shape
            out = [
                {
                    "key": item["key"],
                    "translation": f"MOCK-REVIEWED: {item['source']}",
                    "flag_for_human": False,
                    "reason": "",
                }
                for item in parsed["items"]
            ]
            return json.dumps(out, ensure_ascii=False)

        # 6. Auto-discovery suggestion shape
        if isinstance(parsed, dict) and "sample_strings" in parsed:
            return json.dumps(
                {
                    "recommended_preset": "Modern, laza (kortárs akció/kaland)",
                    "preset_rationale": "Contemporary fast-paced action fits modern gaming slang.",
                    "glossary": [
                        {"source": "Legion", "target": "Legion", "category": "brand", "note": "Do not translate"},
                        {"source": "Neuron", "target": "Neuron", "category": "brand", "note": "Task force"},
                    ],
                    "character_voices": [
                        {"character": "Akira", "register": "informal", "traits": "Determined and protective"}
                    ],
                    "suggested_path_excludes": []
                },
                ensure_ascii=False
            )

        raise ValueError(f"MockProvider got an unrecognized payload shape: {type(parsed).__name__}")
