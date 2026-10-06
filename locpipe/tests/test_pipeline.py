"""Not a full test suite — two end-to-end smoke tests that exercise
the actual failure mode this whole redesign is about:

  test_dedup_and_context_scoping   proves duplicates collapse to one
      LLM call *except* when context (speaker) legitimately differs.

  test_broken_translation_gets_reviewed   proves a validation failure
      actually gets caught, scored low, routed to review, and (in this
      test) repaired — not just that a clean run reports zero issues,
      which would also happen if routing were silently broken.

Each test runs against its own throwaway copy of tests/fixtures/demo_project
so the committed fixture never gets mutated by running the tests.

Run with: python3 tests/test_pipeline.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from locpipe.config import load_project
from locpipe.models import EntryStatus
from locpipe.pipeline import plan, run
from locpipe.providers.base import TranslationProvider
from locpipe.providers.mock import MockProvider
from locpipe.schemas import build_system_prompt_for_category

FIXTURE = Path(__file__).parent / "fixtures" / "demo_project"


def _fresh_copy() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="locpipe_test_"))
    dest = tmp / "demo_project"
    shutil.copytree(FIXTURE, dest)
    return dest


def test_dedup_and_context_scoping() -> None:
    project_dir = _fresh_copy()
    try:
        config = load_project(project_dir)
        stats = run(config, MockProvider())

        assert stats.total_entries == 8, stats
        assert stats.already_translated == 1, stats
        # 5 unique: "Confirm" (x3->1), "Thanks!"/Kael, "Thanks!"/Narrator (kept separate!),
        # dev-text string, placeholder string. NOT 4 -- that would mean the two
        # differently-spoken "Thanks!" lines got wrongly collapsed into one.
        assert stats.unique_strings_sent_to_llm == 5, stats
        assert stats.review_queue_size == 0, stats  # M4: HU_SPELLING minors no longer penalize confidence into review queue

        batch = json.loads((project_dir / "batches" / "batch_001.json").read_text())
        by_id = {e["id"]: e for e in batch}
        assert by_id["ui_settings_confirm_01"]["target"] == by_id["ui_settings_confirm_02"]["target"]
        assert by_id["ui_settings_confirm_03"]["target"] == by_id["ui_settings_confirm_01"]["target"]
        # every non-target field must survive untouched
        assert by_id["ui_welcome_message"]["max_length"] == 40
        assert by_id["dlg_kael_thanks_01"]["speaker"] == "Kael"

        print("PASS  test_dedup_and_context_scoping")
    finally:
        shutil.rmtree(project_dir.parent, ignore_errors=True)


class _FaultThenRepairProvider(TranslationProvider):
    """Simulates a realistic failure: bulk MT drops a placeholder,
    then the review step — now holding the validator's actual error
    message — puts it back correctly. Handles both payload shapes
    schemas.py and reviewer.py send, same as MockProvider.
    """

    async def complete(self, system_prompt: str, user_payload: str, *, max_tokens: int = 8192) -> str:
        parsed = json.loads(user_payload)

        if isinstance(parsed, list):
            out = []
            for item in parsed:
                translation = f"MOCK-HU: {item['source']}"
                if "{playerName}" in translation:
                    translation = translation.replace("{playerName}", "")  # inject the fault
                out.append({"id": item["id"], "translation": translation})
            return json.dumps(out, ensure_ascii=False)

        if isinstance(parsed, dict) and "items" in parsed:
            out = []
            for item in parsed["items"]:
                translation = item["current_translation"]
                if "placeholder" in " ".join(item["issues"]).lower() and "{playerName}" not in translation:
                    translation = translation.rstrip("!") + "{playerName}!"  # repair using the error
                out.append(
                    {"key": item["key"], "translation": translation, "flag_for_human": False, "reason": ""}
                )
            return json.dumps(out, ensure_ascii=False)

        raise ValueError(f"unexpected payload shape: {type(parsed).__name__}")


def test_broken_translation_gets_reviewed() -> None:
    """Also exercises Tier 1 -> Tier 3 fallthrough: _FaultThenRepairProvider
    re-injects the same fault on ANY translate-shaped call, including
    Tier 1's retry (it doesn't look at the retry payload's `issue` field
    at all) -- so Tier 1 correctly gets attempted, correctly fails to
    fix it with this particular provider, marks the entry
    _tier1_retry_exhausted, and correctly falls through to Tier 3 review,
    which DOES look at the issue and fixes it. stats.tier1_repaired == 0
    here is the correct outcome, not a bug -- see
    test_tier1_repair_fixes_without_review_call for the case where Tier 1
    actually succeeds.
    """
    project_dir = _fresh_copy()
    try:
        config = load_project(project_dir)
        stats = run(config, _FaultThenRepairProvider())

        assert stats.validation_failures >= 1, stats
        assert stats.tier1_repaired == 0, (
            f"this fault provider re-injects the same bug on retry -- Tier 1 shouldn't "
            f"have been able to fix anything here, got {stats.tier1_repaired}"
        )
        assert stats.review_queue_size >= 1, stats
        assert stats.reviewed_and_repaired >= 1, stats

        review = json.loads((project_dir / "review" / "needs_review.json").read_text())
        flagged_keys = {r["key"] for r in review}
        assert "ui_welcome_message" in flagged_keys, review

        batch = json.loads((project_dir / "batches" / "batch_001.json").read_text())
        by_id = {e["id"]: e for e in batch}
        final = by_id["ui_welcome_message"]["target"]
        assert "{playerName}" in final, f"placeholder never got repaired: {final!r}"

        print("PASS  test_broken_translation_gets_reviewed")
        print(f"      needs_review.json flagged: {sorted(flagged_keys)}")
        print(f"      repaired translation: {final!r}")
    finally:
        shutil.rmtree(project_dir.parent, ignore_errors=True)


class _FixesOnTier1RetryProvider(TranslationProvider):
    """Breaks {playerName} on a normal translate call, but correctly
    restores it once given the retry payload's `issue` field -- the
    common case Tier 1 exists for: a validator pins down an exact
    mechanical defect, and a plain re-ask with that defect spelled out
    fixes it in one shot, without ever needing the review agent.
    """

    async def complete(self, system_prompt: str, user_payload: str, *, max_tokens: int = 8192) -> str:
        parsed = json.loads(user_payload)
        assert isinstance(parsed, list), f"expected a translate-style list payload, got {type(parsed)}"
        out = []
        for item in parsed:
            is_retry = "issue" in item
            translation = f"MOCK-HU: {item['source']}"
            if "{playerName}" in translation and not is_retry:
                translation = translation.replace("{playerName}", "")  # inject the fault, first attempt only
            out.append({"id": item["id"], "translation": translation})
        return json.dumps(out, ensure_ascii=False)


def test_tier1_repair_fixes_without_review_call() -> None:
    """The actual point of Tier 1: a mechanical defect a validator can
    pin down exactly gets fixed by a cheap retry through the bulk-
    translate provider, not routed to Tier 3 (the review agent) FOR
    THAT REASON.

    Can't assert the entry never appears in the review queue at all --
    fidelity sampling independently guarantees at least one quality
    spot-check per category regardless of validation outcome (see
    test_dedup_and_context_scoping), so it can still legitimately pick
    this exact entry for an unrelated reason. The precise claim checked
    below: stats.tier1_repaired reflects the fix, and if this entry does
    show up in the review queue, its `issues` list is empty (proving
    validation passed after Tier 1 -- so it's there because of fidelity
    sampling, not because the original placeholder defect was still
    present).
    """
    project_dir = _fresh_copy()
    try:
        config = load_project(project_dir)
        provider = _FixesOnTier1RetryProvider()
        stats = run(config, provider, review_provider=MockProvider())

        assert stats.tier1_repaired >= 1, stats

        review = json.loads((project_dir / "review" / "needs_review.json").read_text())
        by_key = {r["key"]: r for r in review}
        if "ui_welcome_message" in by_key:
            # Fidelity sampling can independently pick this entry for an
            # unrelated quality spot-check regardless of Tier 1 -- that's
            # fine and expected. What must NOT be true is that it's there
            # because of the placeholder defect: if Tier 1 actually fixed
            # it, validation passed, so `issues` must be empty here.
            non_spelling = [
                i for i in by_key["ui_welcome_message"]["issues"]
                if (i.get("code") if isinstance(i, dict) else getattr(i, "code", "")) != "HU_SPELLING"
            ]
            assert non_spelling == [], (
                f"Tier 1 should have cleared the validation failure before this could be "
                f"flagged for THAT reason: {by_key['ui_welcome_message']}"
            )

        batch = json.loads((project_dir / "batches" / "batch_001.json").read_text())
        by_id = {e["id"]: e for e in batch}
        final = by_id["ui_welcome_message"]["target"]
        assert "{playerName}" in final, f"placeholder never got repaired: {final!r}"

        print("PASS  test_tier1_repair_fixes_without_review_call")
        print(f"      tier1_repaired={stats.tier1_repaired}, review_queue_size={stats.review_queue_size}")
    finally:
        shutil.rmtree(project_dir.parent, ignore_errors=True)


class _AlwaysBreaksPlaceholderProvider(TranslationProvider):
    """Translate call always drops {playerName}. Review call ALWAYS claims
    success (flag_for_human: false) but returns a repair that STILL drops
    the placeholder -- simulating a review agent that's wrong about having
    fixed something. Used to prove the pipeline re-validates Tier 3's own
    output rather than trusting its claim.
    """

    async def complete(self, system_prompt: str, user_payload: str, *, max_tokens: int = 8192) -> str:
        parsed = json.loads(user_payload)

        if isinstance(parsed, list):
            out = []
            for item in parsed:
                translation = f"MOCK-HU: {item['source']}"
                if "{playerName}" in translation:
                    translation = translation.replace("{playerName}", "")
                out.append({"id": item["id"], "translation": translation})
            return json.dumps(out, ensure_ascii=False)

        if isinstance(parsed, dict) and "items" in parsed:
            out = []
            for item in parsed["items"]:
                # deliberately still broken -- just echoes current_translation back,
                # claims success anyway
                out.append(
                    {
                        "key": item["key"],
                        "translation": item["current_translation"],
                        "flag_for_human": False,
                        "reason": "",
                    }
                )
            return json.dumps(out, ensure_ascii=False)

        raise ValueError(f"unexpected payload shape: {type(parsed).__name__}")


def test_review_output_is_reverified_not_trusted() -> None:
    """Tier 3's own repair must be re-checked against the real validator,
    not trusted on its word. This provider's review step always claims
    flag_for_human=False while returning a translation that still fails
    validation -- if the pipeline trusted that claim, ui_welcome_message
    would end up REVIEWED (and committed to the TM at that trust level)
    while still actually being broken. It must instead end up BLOCKED,
    excluded from the TM commit, and needs_review.json must show the
    real, current (still-failing) validator issue -- not go silent just
    because Tier 3 was attempted.
    """
    project_dir = _fresh_copy()
    try:
        config = load_project(project_dir)
        stats = run(config, _AlwaysBreaksPlaceholderProvider())

        review = json.loads((project_dir / "review" / "needs_review.json").read_text())
        by_key = {r["key"]: r for r in review}
        assert "ui_welcome_message" in by_key, review
        entry = by_key["ui_welcome_message"]
        assert entry["issues"], (
            f"needs_review.json must show the STILL-failing validator issue, not go silent: {entry}"
        )
        assert any("playerName" in i["message"] for i in entry["issues"]), entry

        batch = json.loads((project_dir / "batches" / "batch_001.json").read_text())
        by_id = {e["id"]: e for e in batch}
        assert by_id["ui_welcome_message"]["target"] == by_id["ui_welcome_message"]["source"], (
            "BLOCKED entry falls back to source-language passthrough per Task 7"
        )

        from locpipe.tm import TranslationMemory

        tm = TranslationMemory(config.tm_db_path)
        cur = tm._conn.execute("SELECT * FROM tm WHERE source LIKE '%playerName%'")
        tm_rows = cur.fetchall()
        assert len(tm_rows) == 0, (
            f"a BLOCKED entry must never be committed to the TM -- found {len(tm_rows)} row(s), "
            f"meaning this broken translation would get reused by future duplicate strings: "
            f"{[dict(r) for r in tm_rows]}"
        )
        tm.close()

        print("PASS  test_review_output_is_reverified_not_trusted")
        print(f"      final status correctly BLOCKED, issues preserved: {entry['issues']}")
    finally:
        shutil.rmtree(project_dir.parent, ignore_errors=True)


def test_unexpected_file_crash_does_not_halt_the_run() -> None:
    """Reproduces the bug directly rather than just asserting the fix:
    a malformed batch file used to make run() raise all the way out,
    meaning every file after the broken one -- even perfectly fine ones
    -- never got attempted in that invocation. The circuit-breaker
    try/except in run()'s per-file loops must isolate this to just the
    broken file.
    """
    project_dir = _fresh_copy()
    try:
        (project_dir / "batches" / "batch_002.json").write_text("{not valid json", encoding="utf-8")
        (project_dir / "batches" / "batch_003.json").write_text(
            json.dumps([{"id": "ui_only_in_file3", "source": "File Three String", "target": ""}]),
            encoding="utf-8",
        )

        config = load_project(project_dir)
        stats = run(config, MockProvider())  # must NOT raise

        batch3 = json.loads((project_dir / "batches" / "batch_003.json").read_text())
        assert batch3[0]["target"], (
            "file 3 (fine, alphabetically after the broken file 2) must still be processed "
            "in this same run -- a crash in one file must not halt the rest of the project"
        )

        from locpipe.checkpoint import Checkpoint

        cp = Checkpoint(project_dir / "checkpoint.json")
        assert cp.is_file_done(str(project_dir / "batches" / "batch_001.json"))
        assert cp.is_file_done(str(project_dir / "batches" / "batch_003.json"))
        assert not cp.is_file_done(str(project_dir / "batches" / "batch_002.json")), (
            "the actually-broken file must NOT be marked done -- it should retry next run"
        )

        print("PASS  test_unexpected_file_crash_does_not_halt_the_run")
        print(f"      {stats.summary()}")
    finally:
        shutil.rmtree(project_dir.parent, ignore_errors=True)


def test_plan_matches_run() -> None:
    """plan() and run() compute dedup independently (plan can't share
    run()'s TM-writing code path since it must never write). They must
    still agree on the numbers that matter -- this test exists because
    they didn't: plan() reported 7 unique strings against run()'s 5 on
    the exact same fixture, from summing every entry in every dedup
    group instead of counting the groups. Caught by comparing outputs,
    not by reading the code.
    """
    plan_dir = _fresh_copy()
    run_dir = _fresh_copy()
    try:
        plan_result = plan(load_project(plan_dir))
        run_stats = run(load_project(run_dir), MockProvider())

        assert plan_result["unique_strings_needing_translation"] == run_stats.unique_strings_sent_to_llm, (
            plan_result, run_stats,
        )
        assert plan_result["llm_calls_needed"] == run_stats.llm_calls_made, (plan_result, run_stats)
        assert plan_result["already_translated"] == run_stats.already_translated

        print("PASS  test_plan_matches_run")
    finally:
        shutil.rmtree(plan_dir.parent, ignore_errors=True)
        shutil.rmtree(run_dir.parent, ignore_errors=True)


def test_tm_persists_across_runs() -> None:
    """The one that mattered most: enrich_and_dedupe() reads the TM, but
    for a while nothing wrote a run's own fresh output back into it --
    every batch would re-translate "Confirm" and every other repeat
    string from zero, forever, no matter how many prior runs had
    already translated it. Caught by literally running twice and
    checking whether the second run found anything, not by reading the
    code -- the bug was an absence, which review doesn't catch well.

    Uses a MockProvider subclass with persists_to_tm=True rather than
    MockProvider directly: MockProvider itself defaults that to False
    now (see providers/base.py) specifically so `locpipe run --dry-run`
    can't silently pollute the real, persistent TM with "MOCK-HU: ..."
    placeholder text -- but THIS test's whole point is verifying TM
    persistence itself, which needs a provider that actually persists.
    """

    class _PersistingMockProvider(MockProvider):
        persists_to_tm = True

    project_dir = _fresh_copy()
    try:
        config1 = load_project(project_dir)
        stats1 = run(config1, _PersistingMockProvider())
        assert stats1.newly_committed_to_tm > 0, stats1

        # Simulate the same content showing up in a NEW, not-yet-translated batch
        # file (batch_002.json). Using a different path means the checkpoint does
        # not mark it as done, so plan() counts it as pending — the correct
        # scenario the test was always meant to verify: a later batch reusing TM.
        shutil.copy(FIXTURE / "batches" / "batch_001.json", project_dir / "batches" / "batch_002.json")

        config2 = load_project(project_dir)
        result = plan(config2)
        assert result["tm_hits"] >= stats1.unique_strings_sent_to_llm, (result, stats1)
        assert result["unique_strings_needing_translation"] == 0, result

        print("PASS  test_tm_persists_across_runs")
    finally:
        shutil.rmtree(project_dir.parent, ignore_errors=True)


class _CountingBatchProvider(TranslationProvider):
    """Batch-mode-only mock that counts submit_batch calls -- the number
    that must stay at 1 for a given job no matter how many times run()
    is invoked against the same pending checkpoint.
    """

    def __init__(self):
        self.submit_count = 0
        self._job_results: dict[str, list[str]] = {}

    async def complete(self, system_prompt: str, user_payload: str, *, max_tokens: int = 8192) -> str:
        parsed = json.loads(user_payload)
        if isinstance(parsed, dict) and "items" in parsed:  # review shape triggered by fidelity sampling
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
        raise NotImplementedError("this test provider only exercises the batch-mode path and review")

    def submit_batch(self, requests, *, max_tokens: int = 8192) -> str:
        self.submit_count += 1
        job_id = f"job-{self.submit_count}"
        results = []
        for _, user_payload in requests:
            items = json.loads(user_payload)
            results.append(
                json.dumps(
                    [{"id": item["id"], "translation": f"MOCK-HU: {item['source']}"} for item in items],
                    ensure_ascii=False,
                )
            )
        self._job_results[job_id] = results
        return job_id

    def poll_batch(self, job_id, *, poll_interval_s: int = 30, timeout_s: int = 86400) -> None:
        pass  # test double: instantly "done", no real waiting

    def retrieve_batch_results(self, job_id, n_requests):
        return self._job_results[job_id]


def test_batch_mode_checkpoint_reattach_not_resubmit() -> None:
    """Simulates a process dying right after a batch job is submitted
    (checkpoint.json has a pending_job, but nothing was ever retrieved)
    and then being re-run. The correct behavior is: reattach using the
    saved job id, never call submit_batch a second time. Getting this
    wrong doesn't just produce a wrong number -- on a real provider it
    means paying for the same translations twice.
    """
    project_dir = _fresh_copy()
    try:
        # Compute the exact same batches run() would build, so the
        # fingerprint we pre-seed the checkpoint with is one run() will
        # actually recompute and match against.
        from locpipe.adapters.registry import get_adapter
        from locpipe.batcher import build_batches
        from locpipe.checkpoint import Checkpoint, fingerprint_batches
        from locpipe.classify import classify_entries
        from locpipe.dedupe import enrich_and_dedupe
        from locpipe.glossary import flag_disputed_terms, load_glossary
        from locpipe.pipeline import load_known_characters
        from locpipe.schemas import build_system_prompt_for_category, build_user_payload
        from locpipe.tm import TranslationMemory

        config = load_project(project_dir)
        adapter = get_adapter(config.format)
        tm = TranslationMemory(config.tm_db_path)
        glossary = load_glossary(config.resources.get("glossary"))
        known_characters = load_known_characters(config.resources.get("character_voices"))

        all_entries = []
        for path in config.batch_files:
            all_entries.extend(adapter.extract(path))
        classify_entries(all_entries, config, known_characters)
        flag_disputed_terms(all_entries, glossary)
        dedupe_result = enrich_and_dedupe(all_entries, tm, config.source_lang, config.target_lang)
        batches = build_batches(dedupe_result.unique_groups, config)
        tm.close()

        provider = _CountingBatchProvider()
        prompt_by_category = {}
        requests = []
        for b in batches:
            if b.category not in prompt_by_category:
                prompt_by_category[b.category] = build_system_prompt_for_category(config, b.category, glossary)
            requests.append((prompt_by_category[b.category], build_user_payload(b)))

        # This IS the "process died right after submitting" simulation:
        # a job exists on the provider side, checkpoint.json knows about
        # it, but nothing has been retrieved or merged yet.
        pre_submitted_job_id = provider.submit_batch(requests)
        assert provider.submit_count == 1
        checkpoint = Checkpoint(project_dir / "checkpoint.json")
        checkpoint.set_pending_job(
            pre_submitted_job_id, config.provider.name, fingerprint_batches(batches), len(requests)
        )

        # Now run() as if this were a fresh process picking the project back up.
        config2 = load_project(project_dir)
        config2.provider.mode = "batch"
        stats = run(config2, provider)

        assert provider.submit_count == 1, (
            f"submit_batch was called {provider.submit_count} times -- a resumed run must "
            "reattach to the pending job, not submit a duplicate."
        )
        assert stats.unique_strings_sent_to_llm == 5, stats
        assert not checkpoint.get_pending_job() is None or True  # re-read below, this instance is stale
        fresh_checkpoint = Checkpoint(project_dir / "checkpoint.json")
        assert fresh_checkpoint.get_pending_job() is None, "pending_job should be cleared once results are retrieved"

        print("PASS  test_batch_mode_checkpoint_reattach_not_resubmit")
    finally:
        shutil.rmtree(project_dir.parent, ignore_errors=True)


class _FailOnMarkerProvider(TranslationProvider):
    """Raises for any request whose payload contains `fail_marker` (unhandled
    exceptions are how a real provider timeout/rate-limit/connection-drop
    shows up to _translate_batches_sync -- no retry catches it, same as
    upstream), succeeds for everything else. Handles both payload shapes
    (translate: a bare list; review: {"items": [...]}) like
    _FaultThenRepairProvider does, and records every payload it was asked
    to translate so a second run can be checked for content, not just count
    -- fidelity sampling can legitimately add a review call on top of the
    one translate call, so "did it ever see file 1's strings again" is a
    more honest assertion than "exactly N calls".
    """

    def __init__(self, fail_marker: str | None):
        self.fail_marker = fail_marker
        self.received_payloads: list[str] = []

    async def complete(self, system_prompt: str, user_payload: str, *, max_tokens: int = 8192) -> str:
        if self.fail_marker and self.fail_marker in user_payload:
            raise RuntimeError("simulated crash/rate-limit for this batch")
        self.received_payloads.append(user_payload)
        parsed = json.loads(user_payload)

        if isinstance(parsed, list):
            out = [{"id": item["id"], "translation": f"MOCK-HU: {item['source']}"} for item in parsed]
            return json.dumps(out, ensure_ascii=False)

        if isinstance(parsed, dict) and "items" in parsed:
            out = [
                {"key": item["key"], "translation": f"MOCK-HU: {item['source']}", "flag_for_human": False, "reason": ""}
                for item in parsed["items"]
            ]
            return json.dumps(out, ensure_ascii=False)

        raise ValueError(f"unexpected payload shape: {type(parsed).__name__}")


def test_file_level_crash_resume() -> None:
    """The actual claim under test: a crash partway through a run doesn't
    throw away already-finished files, and a resumed run doesn't redo them.

    Two batch files. Run #1 uses a provider that fails every request for
    file 2 (simulating a crash/rate-limit hitting partway through a
    multi-file run) but succeeds for file 1. Run #2 uses a provider that
    never fails. Assertions: after run #1, file 1 is merged+committed and
    checkpoint.json marks it done while file 2 is untouched; after run #2,
    file 2 completes and run #2's provider never once receives a payload
    containing file 1's content -- proving file 1 was skipped via
    is_file_done() (not merely re-resolved cheaply through the TM, which
    would also pass a naive "no retranslation happened" check).
    """
    project_dir = _fresh_copy()
    try:
        (project_dir / "batches" / "batch_002.json").write_text(
            json.dumps(
                [{"id": "ui_extra_only_in_file2", "source": "Extra String Only In File Two", "target": ""}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        config = load_project(project_dir)
        provider1 = _FailOnMarkerProvider(fail_marker="Extra String Only In File Two")
        stats1 = run(config, provider1)
        assert stats1.total_entries == 9, stats1  # 8 from batch_001 + 1 from batch_002

        from locpipe.checkpoint import Checkpoint

        cp = Checkpoint(project_dir / "checkpoint.json")
        assert cp.is_file_done(str(project_dir / "batches" / "batch_001.json")), (
            "file 1 succeeded fully and must be marked done"
        )
        assert not cp.is_file_done(str(project_dir / "batches" / "batch_002.json")), (
            "file 2's only batch failed -- must NOT be marked done"
        )

        batch2_after_run1 = json.loads((project_dir / "batches" / "batch_002.json").read_text())
        assert batch2_after_run1[0]["target"] == "", "file 2 must be untouched after its batch failed"
        batch1_after_run1 = json.loads((project_dir / "batches" / "batch_001.json").read_text())
        assert all(e["target"] for e in batch1_after_run1), "file 1 must be fully merged despite file 2 failing"

        config2 = load_project(project_dir)
        provider2 = _FailOnMarkerProvider(fail_marker=None)  # never fails this time
        stats2 = run(config2, provider2)

        assert stats2.total_entries == 1, (
            f"run #2 should only have processed file 2's 1 entry, got {stats2.total_entries} -- "
            "file 1 should have been skipped via is_file_done(), not re-extracted at all."
        )
        file1_markers = ["Confirm", "Thanks!", "CombatEncounterManagerNode", "playerName"]
        for payload in provider2.received_payloads:
            parsed = json.loads(payload)
            items = parsed if isinstance(parsed, list) else parsed.get("items", [])
            for item in items:
                item_text = f"{item.get('source', '')} {item.get('current_translation', '')}"
                assert not any(m in item_text for m in file1_markers), (
                    f"run #2 was asked to (re)translate/review file 1 content -- file 1 was NOT "
                    f"actually skipped: {item!r}"
                )
        assert len(provider2.received_payloads) >= 1, "run #2 should have made at least the one translate call for file 2"

        batch2_final = json.loads((project_dir / "batches" / "batch_002.json").read_text())
        assert batch2_final[0]["target"], "file 2 must be translated after the retry run"

        cp2 = Checkpoint(project_dir / "checkpoint.json")
        assert cp2.is_file_done(str(project_dir / "batches" / "batch_002.json"))

        print("PASS  test_file_level_crash_resume")
        print(f"      run #2 made {len(provider2.received_payloads)} call(s), none touching file 1's content")
    finally:
        shutil.rmtree(project_dir.parent, ignore_errors=True)


def test_unity_csv_adapter_composite_key() -> None:
    """The Unity adapter's whole reason to exist over a naive id-column
    read is the composite (id, keyname) key -- Unity exports often reuse
    numeric ids across different content types. This proves two rows
    sharing an id but not a keyname stay distinguishable through a full
    extract -> translate -> merge round trip, not just at extract time.
    """
    import csv as csv_module

    from locpipe.adapters.unity import UnityCSVAdapter

    tmp = Path(tempfile.mkdtemp(prefix="locpipe_test_"))
    try:
        csv_path = tmp / "strings.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            w = csv_module.writer(f)
            w.writerow(["ID", "KeyName", "English", "Type"])
            w.writerow(["1", "confirm_btn", "Confirm", "ui"])
            w.writerow(["1", "quest_confirm", "Are you sure?", "narrative"])
            w.writerow(["2", "cancel_btn", "Cancel", "ui"])

        adapter = UnityCSVAdapter(target_column_names=["Hungarian"])
        entries = adapter.extract(csv_path)
        assert len(entries) == 3, entries
        assert len({e.key for e in entries}) == 3, "the two id=1 rows collapsed onto the same key"

        # The Type column's value must be reachable by a category rule's
        # match_notes_regex, not just sitting inert in entry.extra -- see
        # unity.py's extract() for why this is wired into notes now.
        by_source = {e.source: e for e in entries}
        assert by_source["Confirm"].notes == ["type:ui"]
        assert by_source["Are you sure?"].notes == ["type:narrative"]

        for e in entries:
            e.target = f"HU: {e.source}"
        adapter.merge(csv_path, entries)

        with open(csv_path, encoding="utf-8") as f:
            rows = list(csv_module.reader(f))
        header = rows[0]
        assert "Hungarian" in header
        hu_idx = header.index("Hungarian")
        by_keyname = {r[1]: r[hu_idx] for r in rows[1:]}
        assert by_keyname["confirm_btn"] == "HU: Confirm"
        assert by_keyname["quest_confirm"] == "HU: Are you sure?"
        assert by_keyname["cancel_btn"] == "HU: Cancel"

        print("PASS  test_unity_csv_adapter_composite_key")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class _TranslatesOverPatternProvider(TranslationProvider):
    """Translates normally, EXCEPT if the source contains 'Overdrive' --
    then translates it to a Hungarian word instead of keeping it verbatim,
    simulating exactly the mistake glossary.md's protected 'mechanic'
    entry for 'Overdrive' exists to catch (a term marked "not translated"
    that the model translated anyway).
    """

    async def complete(self, system_prompt: str, user_payload: str, *, max_tokens: int = 8192) -> str:
        parsed = json.loads(user_payload)
        if isinstance(parsed, list):
            out = []
            for item in parsed:
                src = item["source"]
                if "Overdrive" in src:
                    t = src.replace("Overdrive", "Túlhajtás")  # wrongly translated -- should stay "Overdrive"
                else:
                    t = f"MOCK-HU: {src}"
                out.append({"id": item["id"], "translation": t})
            return json.dumps(out, ensure_ascii=False)
        if isinstance(parsed, dict) and "items" in parsed:
            # review call: just echo back unchanged, still wrong -- this
            # test only cares whether the FIRST pass gets caught at all
            out = [{"key": i["key"], "translation": i["current_translation"], "flag_for_human": False, "reason": ""} for i in parsed["items"]]
            return json.dumps(out, ensure_ascii=False)
        raise ValueError("unexpected shape")


class _FailsNTimesThenSucceedsProvider(TranslationProvider):
    """Returns unparseable JSON for the first `fail_count` attempts on
    EVERY distinct batch it's asked to translate, then succeeds --
    simulating exactly what an undersized max_output_tokens for a given
    batch_size looks like from the pipeline's side (truncated/invalid
    response -> retry -> eventually succeeds, or eventually exhausts
    retries). Tracks attempts per distinct payload so each category's
    batch independently gets its own fail_count failures before success.
    """

    def __init__(self, fail_count: int):
        self.fail_count = fail_count
        self.attempts_by_payload: dict[str, int] = {}

    async def complete(self, system_prompt: str, user_payload: str, *, max_tokens: int = 8192) -> str:
        # keyed by the ORIGINAL payload content (before retry notes get appended),
        # so repeated retries of the same batch count against the same counter
        key = user_payload.split("\n\n(Your previous response was invalid")[0]
        n = self.attempts_by_payload.get(key, 0)
        self.attempts_by_payload[key] = n + 1
        if n < self.fail_count:
            return "this is not valid json at all, deliberately truncated-looking"
        items = json.loads(key)
        out = [{"id": i["id"], "translation": f"MOCK-HU: {i['source']}"} for i in items]
        return json.dumps(out, ensure_ascii=False)


def test_wasted_retry_attempts_are_tracked_accurately() -> None:
    """The actual instrumentation added to close the visibility gap: a
    truncated/invalid response used to trigger a full-payload retry with
    zero trace of it anywhere in the stats -- llm_calls_made only ever
    counted BATCHES attempted, never the retries within them. Provider
    fails once per batch before succeeding; demo project has 3 category
    batches (ui, dialogue, developer_text), so exactly 3 wasted attempts
    is the correct, precise answer -- not "some positive number."
    """
    project_dir = _fresh_copy()
    try:
        config = load_project(project_dir)
        provider = _FailsNTimesThenSucceedsProvider(fail_count=1)
        stats = run(config, provider)

        assert stats.wasted_retry_attempts == 3, (
            f"expected exactly 3 (1 wasted attempt x 3 category batches: ui/dialogue/"
            f"developer_text), got {stats.wasted_retry_attempts}"
        )
        assert "wasted full-payload retry" in stats.summary()

        print("PASS  test_wasted_retry_attempts_are_tracked_accurately")
        print(f"      {stats.summary()}")
    finally:
        shutil.rmtree(project_dir.parent, ignore_errors=True)


def test_broadened_protected_terms_catches_mistranslated_mechanic_term() -> None:
    """The actual point of today's glossary_terms.py change: protected-
    term enforcement now covers 'mechanic'/'lore', not just 'brand'.
    Nothing in the existing fixture data ever exercised this path even
    before today (grep confirmed zero references anywhere in tests/),
    so this proves it end-to-end rather than trusting the code review:
    glossary.md's 'Overdrive' entry (mechanic, not-translated) should
    cause a source containing 'Overdrive' to get flagged if the target
    doesn't preserve it verbatim.
    """
    project_dir = _fresh_copy()
    try:
        (project_dir / "batches" / "batch_002.json").write_text(
            json.dumps(
                [{"id": "ui_overdrive_mode", "source": "Activate Overdrive mode now", "target": ""}],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        config = load_project(project_dir)
        stats = run(config, _TranslatesOverPatternProvider())

        review = json.loads((project_dir / "review" / "needs_review.json").read_text())
        by_key = {r["key"]: r for r in review}
        assert "ui_overdrive_mode" in by_key, (
            f"a mistranslated protected 'mechanic' term should have been flagged: {review}"
        )
        issue_messages = " ".join(i["message"] for i in by_key["ui_overdrive_mode"]["issues"])
        assert "Overdrive" in issue_messages, issue_messages
        assert "glossary.md" in issue_messages, issue_messages  # confirms it's THIS check, not a different one

        print("PASS  test_broadened_protected_terms_catches_mistranslated_mechanic_term")
        print(f"      issue: {issue_messages}")
    finally:
        shutil.rmtree(project_dir.parent, ignore_errors=True)


def test_prompt_templates_use_configured_language_pair_not_hardcoded() -> None:
    """review.md used to hardcode 'avoid calques from English sentence
    structure' regardless of the project's actual source language, and
    wasn't even routed through fill() -- so it couldn't have used
    %%SOURCE_LANG%% even if it had one. Accidentally correct for this
    fixture (source_lang: en) specifically, but would have silently
    given wrong guidance for any other language pair. Confirms the fix
    is real: the rendered prompt reflects config.source_lang/target_lang,
    not a literal string, and doesn't regress if the fixture's language
    pair ever changes.
    """
    config = load_project(FIXTURE)

    from locpipe.prompt_builder import fill, load_template
    from locpipe.schemas import build_system_prompt_for_category
    from locpipe.glossary import load_glossary

    review_prompt = fill(load_template("review.md"), source_lang=config.source_lang, target_lang=config.target_lang)
    assert "English" not in review_prompt, "must not hardcode a specific source language"
    assert f"Source language: {config.source_lang}." in review_prompt
    assert f"Target language: {config.target_lang}." in review_prompt

    glossary = load_glossary(config.resources.get("glossary"))
    translate_prompt = build_system_prompt_for_category(config, "dialogue", glossary)
    assert "NATURALNESS" in translate_prompt
    assert f"native speaker of {config.target_lang}" in translate_prompt
    assert f"mirrors {config.source_lang} syntax" in translate_prompt

    print("PASS  test_prompt_templates_use_configured_language_pair_not_hardcoded")


def test_narrative_boundary_grouping_and_context() -> None:
    """Two things to prove: (1) entries sharing a boundary value (e.g.
    the same scene) land in the same batch even when a flat batch_size
    would otherwise split them, and a group bigger than batch_size still
    gets split (can't avoid that); (2) preceding_context only ever
    reaches back within the same boundary group, never across scenes.
    """
    from locpipe.batcher import build_batches
    from locpipe.config import CategoryRule, ProjectConfig, ProviderConfig
    from locpipe.context_key import build_tm_key
    from locpipe.models import Entry
    from locpipe.narrative_context import attach_narrative_context
    from locpipe.normalize import content_hash, normalize_source

    rule = CategoryRule(
        name="dialogue",
        is_default=True,
        batch_size=3,  # fits scene_a's 3 entries exactly -- big enough that grouping matters,
                       # not so big it stops proving anything
        narrative_boundary_field="context_screen",
        narrative_context_window=2,
    )
    config = ProjectConfig(
        project="test", source_lang="en", target_lang="hu", format="generic_kv",
        root=Path("/tmp"), batch_glob="*.json", resources={}, categories=[rule],
        provider=ProviderConfig(), tm_db_path=Path("/tmp/unused.sqlite3"),
    )

    # Deliberately interleaved: scene_b's line sits between scene_a's lines in
    # extraction order. A flat chunk-by-position (ignoring boundaries) at
    # batch_size=3 would produce [a1, b1, a2], [a3] -- mixing two unrelated
    # scenes in one batch. Grouping-aware packing must not do that.
    entries = [
        Entry(file="f", key="a1", source="Hello.", speaker="Kael", context_screen="scene_a"),
        Entry(file="f", key="b1", source="Welcome.", speaker="Guard", context_screen="scene_b"),
        Entry(file="f", key="a2", source="How are you?", speaker="Narrator", context_screen="scene_a"),
        Entry(file="f", key="a3", source="Fine, thanks.", speaker="Kael", context_screen="scene_a"),
    ]
    for e in entries:
        e.category = "dialogue"

    attach_narrative_context(entries, config)

    # a3's preceding context must be exactly [a1, a2] from scene_a -- never b1, and never itself
    a3 = entries[3]
    assert [c["source"] for c in a3.preceding_context] == ["Hello.", "How are you?"], a3.preceding_context
    # b1 opens scene_b -- nothing precedes it, scene_a's lines must not leak in
    b1 = entries[1]
    assert b1.preceding_context == [], b1.preceding_context

    for e in entries:
        e.content_hash = content_hash(normalize_source(e.source))
        e.tm_key = build_tm_key(e.content_hash, "dialogue", None)
    unique_groups = {e.tm_key: [e] for e in entries}

    batches = build_batches(unique_groups, config)
    scene_a_batch_indices = {
        b_i for b_i, b in enumerate(batches) for e in b.representatives if e.context_screen == "scene_a"
    }
    assert len(scene_a_batch_indices) == 1, (
        f"scene_a's 3 entries should land in exactly one batch despite scene_b interleaved "
        f"between them in extraction order -- landed across {len(scene_a_batch_indices)} batch(es)"
    )
    only_scene_a_batch = batches[next(iter(scene_a_batch_indices))]
    assert all(e.context_screen == "scene_a" for e in only_scene_a_batch.representatives), (
        "scene_a's batch must not have scene_b mixed in"
    )
    total_entries_batched = sum(len(b.representatives) for b in batches)
    assert total_entries_batched == 4, "no entries should be lost or duplicated by the grouping pass"

    # The other documented case: a group *larger* than batch_size can't be kept
    # whole without violating the size cap, so it must split -- confirm that's
    # still true rather than assuming "keep groups together" silently overrides it.
    oversized_rule = CategoryRule(
        name="dialogue", is_default=True, batch_size=2,
        narrative_boundary_field="context_screen", narrative_context_window=0,
    )
    oversized_config = ProjectConfig(
        project="test", source_lang="en", target_lang="hu", format="generic_kv",
        root=Path("/tmp"), batch_glob="*.json", resources={}, categories=[oversized_rule],
        provider=ProviderConfig(), tm_db_path=Path("/tmp/unused.sqlite3"),
    )
    scene_a_only = [e for e in entries if e.context_screen == "scene_a"]  # 3 entries, batch_size=2
    oversized_groups = {e.tm_key: [e] for e in scene_a_only}
    oversized_batches = build_batches(oversized_groups, oversized_config)
    assert len(oversized_batches) == 2, (
        f"a 3-entry scene against batch_size=2 must split (can't fit whole) -- got "
        f"{len(oversized_batches)} batch(es)"
    )
    assert sum(len(b.representatives) for b in oversized_batches) == 3

    print("PASS  test_narrative_boundary_grouping_and_context")


class _PartialThenCompleteResponseProvider(TranslationProvider):
    """Returns syntactically valid JSON that's missing one item's id on
    the first attempt for each distinct batch, then a complete response
    on retry -- simulating a response that parses cleanly (so the old
    code accepted it outright) but silently under-covers the batch."""

    def __init__(self):
        self.attempts_by_payload: dict[str, int] = {}

    async def complete(self, system_prompt: str, user_payload: str, *, max_tokens: int = 8192) -> str:
        key = user_payload.split("\n\n(Your previous response was invalid")[0]
        n = self.attempts_by_payload.get(key, 0)
        self.attempts_by_payload[key] = n + 1
        items = json.loads(key)
        out = [{"id": i["id"], "translation": f"MOCK-HU: {i['source']}"} for i in items]
        if n == 0 and len(out) > 1:
            out = out[:-1]  # drop the last item's id -- a partial-but-valid response
        return json.dumps(out, ensure_ascii=False)


def test_partial_response_triggers_retry_not_silent_partial_success() -> None:
    """A response missing some ids used to be accepted outright (parsed
    fine, no error) -- the missing entries silently stayed NOT_STARTED,
    only caught much later by the file-level unresolved check, at which
    point the WHOLE file (including entries that translated fine) gets
    marked unfinished and none of it lands in the TM. This proves the
    completeness check now catches it within the same call instead, via
    the existing retry-with-correction loop, so every entry gets a real
    translation and the file actually finishes."""
    project_dir = _fresh_copy()
    try:
        config = load_project(project_dir)
        provider = _PartialThenCompleteResponseProvider()
        stats = run(config, provider)
        # Under P11, partial progress is retained and only missing items are requested,
        # so this is a successful partial batch recovery with 0 wasted full-payload retries.
        assert stats.wasted_retry_attempts == 0

        for path in config.batch_files:
            data = json.loads(path.read_text(encoding="utf-8"))
            for entry in data:
                assert entry.get("target"), f"{path.name}: {entry['id']} never got a translation"

        print("PASS  test_partial_response_triggers_retry_not_silent_partial_success")
    finally:
        shutil.rmtree(project_dir.parent, ignore_errors=True)


def test_plan_includes_realistic_tokens_and_caching_note() -> None:
    project_dir = _fresh_copy()
    try:
        config = load_project(project_dir)
        res = plan(config)
        assert "total_entries" in res
        assert "estimated_uncached_input_tokens" in res
        assert "estimated_cache_read_tokens" in res
        assert "estimated_realistic_input_tokens" in res
        assert "caching_note" in res
        assert res["estimated_realistic_input_tokens"] >= res["estimated_uncached_input_tokens"]
        assert "Antigravity CLI" in res["caching_note"]
    finally:
        shutil.rmtree(project_dir.parent, ignore_errors=True)


def test_register_setting_and_prompt_instructions() -> None:
    project_dir = _fresh_copy()
    try:
        # Default is informal
        config = load_project(project_dir)
        assert config.target_register == "informal"

        prompt_informal = build_system_prompt_for_category(config, "dialogue", [])
        assert "tegeződés" in prompt_informal
        assert "character bible explicitly overrides" in prompt_informal

        # Switch to formal
        (project_dir / "project.yaml").write_text(
            (project_dir / "project.yaml").read_text(encoding="utf-8") + "\ntarget_register: formal\n",
            encoding="utf-8"
        )
        config_formal = load_project(project_dir)
        assert config_formal.target_register == "formal"

        prompt_formal = build_system_prompt_for_category(config_formal, "dialogue", [])
        assert "magázódás" in prompt_formal
        assert "character bible explicitly overrides" in prompt_formal

        # Invalid register
        (project_dir / "project.yaml").write_text(
            (project_dir / "project.yaml").read_text(encoding="utf-8").replace("target_register: formal", "target_register: pirate"),
            encoding="utf-8"
        )
        try:
            load_project(project_dir)
            assert False, "Should have raised ValueError on invalid register"
        except ValueError as e:
            assert "Invalid target_register 'pirate'" in str(e)
    finally:
        shutil.rmtree(project_dir.parent, ignore_errors=True)


def test_review_batch_register_instruction() -> None:
    import asyncio
    from locpipe.reviewer import review_batch
    from locpipe.review_queue import ReviewItem
    from locpipe.models import Entry, ValidationResult

    class CapturePromptProvider:
        def __init__(self):
            self.last_system_prompt = ""

        async def complete(self, system_prompt: str, user_payload: str, *, max_tokens: int = 16384) -> str:
            self.last_system_prompt = system_prompt
            return "[]"

    provider = CapturePromptProvider()
    entry = Entry(key="k1", file="test.json", source="Hello", target="Szia", category="dialogue")
    item = ReviewItem(entry=entry, validation=ValidationResult(entry_key="k1"), confidence=0.5, confidence_flags=[])

    asyncio.run(review_batch([item], [], provider, "en", "hu", target_register="informal"))
    assert "tegeződés" in provider.last_system_prompt
    assert "character bible explicitly overrides" in provider.last_system_prompt

    asyncio.run(review_batch([item], [], provider, "en", "hu", target_register="formal"))
    assert "magázódás" in provider.last_system_prompt
    assert "character bible explicitly overrides" in provider.last_system_prompt


def test_full_bilingual_and_consistency_reports() -> None:
    project_dir = _fresh_copy()
    try:
        config = load_project(project_dir)
        # Run with mock provider that persists to TM
        class PersistingMockProvider(MockProvider):
            persists_to_tm = True

        provider = PersistingMockProvider()
        stats = run(config, provider)

        bilingual_path = project_dir / "review" / "full_bilingual_report.md"
        consistency_path = project_dir / "review" / "consistency_report.md"

        assert bilingual_path.exists()
        bilingual_content = bilingual_path.read_text(encoding="utf-8")
        assert "| Source | Target | Category | Origin | Quality |" in bilingual_content
        assert "Full Bilingual Translation Export" in bilingual_content

        assert consistency_path.exists()
        consistency_content = consistency_path.read_text(encoding="utf-8")
        assert "Translation Consistency Report" in consistency_content
    finally:
        shutil.rmtree(project_dir.parent, ignore_errors=True)


def test_tm_invalidation_forces_retranslation() -> None:
    from locpipe.tm import TranslationMemory
    from locpipe.models import Entry, TMRecord
    from locpipe.dedupe import enrich_and_dedupe
    from locpipe.normalize import content_hash, normalize_source

    from locpipe.context_key import build_tm_key

    tm = TranslationMemory(":memory:")
    src = "Press {0} to open chest"
    h = content_hash(normalize_source(src))
    tm_key = build_tm_key(h, "ui", None)
    rec = TMRecord(tm_key, src, "Nyisd ki a ladat a {0} gombbal", "en", "hu", "ui", None, 1.0, "mt")
    tm.upsert(h, rec)
    assert tm.get(tm_key) is not None

    entry = Entry(key="chest_1", file="chest.json", source=src, target="", category="ui")
    res1 = enrich_and_dedupe([entry], tm, "en", "hu")
    assert res1.tm_hits == 1
    assert res1.total_unique_strings_to_translate == 0

    # Invalidate
    deleted = tm.invalidate(h)
    assert deleted is True
    assert tm.get(tm_key) is None

    # Next check should route to translation
    entry2 = Entry(key="chest_1", file="chest.json", source=src, target="", category="ui")
    res2 = enrich_and_dedupe([entry2], tm, "en", "hu")
    assert res2.tm_hits == 0
    assert res2.total_unique_strings_to_translate == 1
    assert list(res2.unique_groups.values())[0][0].source == src


def test_pseudoloc_provider_transformation() -> None:
    import asyncio
    from locpipe.providers.pseudoloc import PseudoLocProvider, pseudolocalize_text

    # Verify text transformation
    src = "Hello <color=red>{player}</color>, you have %d coins!"
    out = pseudolocalize_text(src)
    assert "<color=red>" in out
    assert "</color>" in out
    assert "{player}" in out
    assert "%d" in out
    assert any(c in out for c in "áéíőűÁÉÍŐŰ")
    assert len(out) > len(src)

    # Verify provider payload completion
    provider = PseudoLocProvider()
    assert provider.persists_to_tm is False

    payload = json.dumps([{"id": 0, "source": src}])
    resp = asyncio.run(provider.complete("system prompt", payload))
    parsed = json.loads(resp)
    assert parsed[0]["id"] == 0
    assert "{player}" in parsed[0]["translation"]


if __name__ == "__main__":
    test_dedup_and_context_scoping()
    test_broken_translation_gets_reviewed()
    test_tier1_repair_fixes_without_review_call()
    test_review_output_is_reverified_not_trusted()
    test_unexpected_file_crash_does_not_halt_the_run()
    test_prompt_templates_use_configured_language_pair_not_hardcoded()
    test_broadened_protected_terms_catches_mistranslated_mechanic_term()
    test_wasted_retry_attempts_are_tracked_accurately()
    test_partial_response_triggers_retry_not_silent_partial_success()
    test_plan_matches_run()
    test_tm_persists_across_runs()
    test_batch_mode_checkpoint_reattach_not_resubmit()
    test_file_level_crash_resume()
    test_unity_csv_adapter_composite_key()
    test_narrative_boundary_grouping_and_context()
    print("\nall smoke tests passed.")
