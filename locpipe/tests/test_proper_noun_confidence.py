import pytest
from locpipe.models import Entry, ValidationResult
from locpipe.confidence import score, confidence_flags, is_plausible_proper_noun
from locpipe.config import ProjectConfig, ProviderConfig, CategoryRule
from pathlib import Path


def test_is_plausible_proper_noun():
    # Names, characters, weapons, gaming acronyms
    assert is_plausible_proper_noun("Rosa") is True
    assert is_plausible_proper_noun("Balder") is True
    assert is_plausible_proper_noun("Jeanne") is True
    assert is_plausible_proper_noun("Chain Chomp") is True
    assert is_plausible_proper_noun("Inferno Slayer") is True
    assert is_plausible_proper_noun("All 4 One") is True
    assert is_plausible_proper_noun("Shuraba") is True
    assert is_plausible_proper_noun("Chernobog") is True
    assert is_plausible_proper_noun("OK") is True
    assert is_plausible_proper_noun("HP") is True
    assert is_plausible_proper_noun("MP") is True
    assert is_plausible_proper_noun("BGM") is True

    # Untranslated English sentences / UI prompts (MUST NOT be treated as proper nouns)
    assert is_plausible_proper_noun("You must defeat the evil dragon.") is False
    assert is_plausible_proper_noun("Press button to jump") is False
    assert is_plausible_proper_noun("Select an option") is False
    assert is_plausible_proper_noun("The sword is broken") is False
    assert is_plausible_proper_noun("Warning: Core temperature critical") is False
    assert is_plausible_proper_noun("Do you want to continue?") is False


def test_proper_nouns_do_not_flood_review_queue():
    proper_nouns = [
        "Rosa", "Balder", "Undine", "Chain Chomp", "Arwing",
        "Shuraba", "All 4 One", "Rasetsu", "Inferno Slayer", "OK"
    ]
    for noun in proper_nouns:
        entry = Entry(
            file="test.json",
            key="k_test",
            source=noun,
            target=noun,
        )
        val = ValidationResult(entry_key="k_test")
        s = score(entry, val)
        assert s == 1.0, f"Expected 1.0 for proper noun {noun}, got {s}"
        flags = confidence_flags(entry)
        assert "translation is identical to source and nothing marks that as expected" not in flags


def test_untranslated_sentences_are_properly_penalized_and_flagged():
    sentences = [
        "You must defeat the evil dragon before nightfall.",
        "Press button to jump",
        "Select an option to proceed with your journey",
    ]
    for sent in sentences:
        entry = Entry(
            file="test.json",
            key="k_sent",
            source=sent,
            target=sent,
        )
        val = ValidationResult(entry_key="k_sent")
        s = score(entry, val)
        # Should be penalized by 0.4 -> 0.60
        assert s <= 0.65, f"Expected penalty for untranslated sentence '{sent}', got {s}"
        flags = confidence_flags(entry)
        assert any("translation is identical to source" in f for f in flags)


def test_allow_identical_proper_nouns_disabled():
    cfg = ProjectConfig(
        project="test",
        source_lang="en",
        target_lang="hu",
        format="generic_kv",
        root=Path("."),
        batch_glob="*.json",
        resources={},
        categories=[CategoryRule(name="default", is_default=True)],
        provider=ProviderConfig(),
        tm_db_path=Path("tm.sqlite3"),
        allow_identical_proper_nouns=False,
    )
    entry = Entry(file="test.json", key="k", source="Rosa", target="Rosa")
    val = ValidationResult(entry_key="k")
    s = score(entry, val, config=cfg)
    assert s <= 0.65  # Penalized when flag is explicitly disabled
    flags = confidence_flags(entry, config=cfg)
    assert any("translation is identical to source" in f for f in flags)
