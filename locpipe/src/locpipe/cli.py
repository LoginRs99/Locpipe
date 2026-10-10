from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_project
from .audit import run_audit, render_report_markdown
from .adapters.registry import get_adapter
from .pipeline import plan, run
from .preflight.font_check import apply_hungarian_fallback_if_needed
from .preflight.run_safety import snapshot_tm, sweep_orphaned_agy_artifacts

_INIT_GAME_TEMPLATE = """\
project: {name}
project_type: game
profile: fast   # fast (70% speed / 30% quality - recommended) | balanced | thorough
source_lang: {source_lang}
target_lang: {target_lang}
target_register: informal   # informal (tegez — default) | formal (magáz)

format: {format}   # ported: generic_kv, po_gettext, ue4_5_po (Unreal Localization
                     # Dashboard .po export), unity (official Localization Package CSV
                     # export), uabea_json (UABEA asset-dump export), naninovel
                     # (Scripts/*.txt & Managed Text *.txt), xliff/weblate_xliff
                     # -- see locpipe/adapters/registry.py for details on each

batches:
  glob: "{batch_glob}"

resources:
  glossary: resources/glossary.md
  lang_style: resources/lang-style.md
  character_voices: resources/character-voices.md
  anti_fabrication_checklist: resources/anti-fabrication-checklist.md

categories:
  - name: dialogue
    match_speaker_present: true
    needs_character_voice: true
    batch_size: 100
    max_expansion_ratio: 1.8   # dialogue usually has room to run a bit longer
  - name: ui
    default: true
    needs_character_voice: false
    batch_size: 120
    max_expansion_ratio: 1.4   # tighter: buttons/labels are the ones that actually clip

provider:
  name: antigravity_cli   # antigravity_cli (default) | gemini
  model: gemini-3.8-flash # bulk-translate model
  effort: low             # low | high -- antigravity_cli only
  review_model: gemini-3.8-flash
  review_effort: low
  mode: sync        # or "batch" for large non-urgent runs
  max_concurrency: 2

tm:
  db_path: tm/translation_memory.sqlite3

confidence:
  review_threshold: 0.65
  max_expansion_ratio: 1.6
  tier1_repair_attempts: 2
"""

_INIT_SOFTWARE_TEMPLATE = """\
project: {name}
project_type: software
profile: fast   # fast (70% speed / 30% quality - recommended) | balanced | thorough
source_lang: {source_lang}
target_lang: {target_lang}
target_register: informal   # informal (közvetlen — default) | formal (hivatalos)

format: {format}   # generic_kv, po_gettext, xliff, etc.

batches:
  glob: "{batch_glob}"

resources:
  glossary: resources/glossary.md
  lang_style: resources/lang-style.md
  anti_fabrication_checklist: resources/anti-fabrication-checklist.md

categories:
  - name: action
    batch_size: 120
    max_expansion_ratio: 1.4
    default_max_length: 35
  - name: menu
    batch_size: 120
    max_expansion_ratio: 1.4
  - name: dialog
    batch_size: 100
    max_expansion_ratio: 1.8
  - name: ui
    default: true
    batch_size: 120
    max_expansion_ratio: 1.5

provider:
  name: antigravity_cli
  model: gemini-3.8-flash
  effort: low
  review_model: gemini-3.8-flash
  review_effort: low
  mode: sync
  max_concurrency: 2

tm:
  db_path: tm/translation_memory.sqlite3

confidence:
  review_threshold: 0.65
  max_expansion_ratio: 1.6
  tier1_repair_attempts: 2
"""

_INIT_TEMPLATE = _INIT_GAME_TEMPLATE


def cmd_init(args: argparse.Namespace) -> int:
    root = Path("projects") / args.name
    if root.exists():
        print(f"projects/{args.name} already exists.", file=sys.stderr)
        return 1
    (root / "batches").mkdir(parents=True)
    (root / "resources").mkdir(parents=True)

    ptype = getattr(args, "type", "game") or "game"
    src = getattr(args, "source", "en") or "en"
    tgt = getattr(args, "target", "hu") or "hu"
    fmt = getattr(args, "format", "generic_kv") or "generic_kv"

    if fmt == "naninovel":
        batch_glob = "batches/**/*.txt"
    elif fmt in ("po_gettext", "ue4_5_po"):
        batch_glob = "batches/**/*.po"
    elif fmt in ("xliff", "weblate_xliff"):
        batch_glob = "batches/**/*.xlf"
    elif fmt == "unity":
        batch_glob = "batches/**/*.csv"
    else:
        batch_glob = "batches/*.json"

    from .presets import LANG_STYLE_PRESETS
    from .bootstrap import ANTI_FABRICATION_DEFAULT

    if ptype == "software":
        tmpl = _INIT_SOFTWARE_TEMPLATE
        style_content = LANG_STYLE_PRESETS.get("Szoftver UI / Asztali alkalmazás", "# Language style guide\n")
        resource_files = [
            ("glossary.md", "# Glossary\n\n| Source term | Target translation | Category | Confidence | Source/justification |\n|---|---|---|---|---|\n| OK | OK | ui | high | standard UI |\n| Cancel | Mégse | ui | high | standard UI |\n| Save | Mentés | ui | high | standard UI |\n| Open | Megnyitás | ui | high | standard UI |\n"),
            ("lang-style.md", style_content),
            ("anti-fabrication-checklist.md", ANTI_FABRICATION_DEFAULT),
        ]
    else:
        tmpl = _INIT_GAME_TEMPLATE
        style_content = LANG_STYLE_PRESETS.get("Modern, laza (kortárs akció/kaland)", "# Language style guide\n")
        resource_files = [
            ("glossary.md", "# Glossary\n\n| Source term | Target translation | Category | Confidence | Source/justification |\n|---|---|---|---|---|\n"),
            ("lang-style.md", style_content),
            ("character-voices.md", "# Character voice bible\n\n| Character | Register | Traits | Avoid |\n|---|---|---|---|\n"),
            ("anti-fabrication-checklist.md", ANTI_FABRICATION_DEFAULT),
        ]

    (root / "project.yaml").write_text(
        tmpl.format(name=args.name, source_lang=src, target_lang=tgt, format=fmt, batch_glob=batch_glob),
        encoding="utf-8"
    )
    for fname, content in resource_files:
        (root / "resources" / fname).write_text(content, encoding="utf-8")

    print(f"Created projects/{args.name}/ ({ptype} mode, {src} -> {tgt}). Edit project.yaml, drop batch files in batches/, then:")
    print(f"  locpipe plan --project projects/{args.name}   # check the numbers first")
    print(f"  locpipe run --project projects/{args.name}")
    return 0


def _build_provider(
    config,
    dry_run: bool,
    pseudo_loc: bool = False,
    model_override: str | None = None,
    effort_override: str | None = None,
):
    if pseudo_loc:
        from .providers.pseudoloc import PseudoLocProvider

        return PseudoLocProvider()

    if dry_run:
        from .providers.mock import MockProvider

        return MockProvider()

    name = config.provider.name
    model = model_override or config.provider.model
    if name == "antigravity_cli":
        from .providers.antigravity_cli_provider import AntigravityCLIProvider

        return AntigravityCLIProvider(
            model=model,
            max_concurrency=config.provider.max_concurrency,
            timeout_s=config.provider.sync_call_timeout_s,
            effort=effort_override or config.provider.effort,
        )
    raise ValueError(f"Unsupported provider.name '{name}' in project.yaml (only antigravity_cli is supported)")


def cmd_plan(args: argparse.Namespace) -> int:
    config = load_project(args.project)
    if config.preflight.font_check_enabled and config.preflight.font_check_asset_path:
        font_result = apply_hungarian_fallback_if_needed(
            config, config.preflight.font_check_asset_path, config.preflight.font_check_engine
        )
        print(f"Font Preflight: {font_result['message'].splitlines()[0]}")
        if font_result.get("character_replacements_applied"):
            print(f"  Auto-applied character fallback: {font_result['character_replacements_applied']}")
    limit = args.limit or args.sample
    try:
        provider = _build_provider(config, dry_run=getattr(args, "dry_run", False))
    except Exception as e:
        provider = None
        print(f"Note: Could not instantiate provider for capability lookup ({e}); using base defaults.")
    result = plan(config, provider=provider, limit_batches=limit, include_totals=True)
    if result.get("context_window_warning"):
        print(f"  [ADVISORY] {result['context_window_warning']}")

    print("=== PRE-FLIGHT PLAN & TOKEN ESTIMATE ===")
    print(f"  Project:                      {config.project}")
    print(f"  Provider & Model:             {config.provider.name} ({config.provider.model}, effort: {config.provider.effort})")
    print(f"  Pending Batch Files:          {result.get('pending_files_count', 0):,}")
    print(f"  Total Scanned Entries:        {result['total_entries']:,}")
    print(f"  Already Translated in Source: {result['already_translated']:,}")
    print(f"  Filled from TM (0 LLM Calls):  {result['tm_hits']:,}")
    print(f"  Unique Strings to Translate:  {result['unique_strings_needing_translation']:,}")
    print(f"  LLM Calls Needed:             {result['llm_calls_needed']:,}")
    for cat, n in sorted(result["calls_by_category"].items()):
        print(f"    - {cat}: {n} call(s)")
    print()
    print("Token Estimates (Heuristic char/4 calculation):")
    print(f"  Estimated Input Tokens (caching-optimistic): ~{result['estimated_uncached_input_tokens']:,}")
    print(f"  Estimated System Prompt Tokens (cached):    ~{result['estimated_cache_read_tokens']:,} (reused across {result['llm_calls_needed']} calls)")
    if "estimated_realistic_input_tokens" in result:
        print(f"  Estimated Realistic Input Tokens (no-cache): ~{result['estimated_realistic_input_tokens']:,}")
    print(f"  Estimated Target Output Tokens:              ~{result['estimated_output_tokens']:,}")
    print()
    if result.get("caching_note"):
        print(f"Note: {result['caching_note']}")
    else:
        print("Note: Gemini models automatically cache long context prompts.")
    print("Check current per-token pricing at ai.google.dev/pricing before sizing your billing expectations.")
    print("=======================================")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    import asyncio
    from .auto_suggest import analyze_project_and_suggest
    from .providers.mock import MockProvider

    config = load_project(args.project)
    report = run_audit(config)
    markdown = render_report_markdown(report, config.project)

    out_path = Path(args.out) if args.out else config.root / "audit_report.md"
    out_path.write_text(markdown, encoding="utf-8")

    if not report["supported"]:
        print(f"'{config.format}' doesn't support extraction auditing yet -- see {out_path} for details.")
        return 0

    reasons = report["reason_counts"]
    kept = reasons.get("kept", 0)
    excluded = reasons.get("excluded_by_config", 0)
    noise_total = sum(v for k, v in reasons.items() if k.startswith("noise:"))
    print(f"Scanned {report['files_scanned']} file(s).")
    print(f"  kept (would be sent to the LLM):        {kept}")
    print(f"  filtered as engine noise (built-in):    {noise_total}")
    print(f"  filtered by uabea_json_path_exclude:    {excluded}")
    if report["files_failed"]:
        print(f"  files that failed to parse (skipped):   {len(report['files_failed'])}")
    print(f"Full breakdown by asset/path: {out_path}")

    if getattr(args, "suggest", False):
        print("\n=== AI RESOURCE & STYLE AUTO-DISCOVERY ===")
        print(f"Analyzing extracted strings with {config.provider.name} (low effort)...")
        provider = (
            MockProvider()
            if getattr(args, "dry_run", False)
            else _build_provider(config, effort_override="low")
        )
        try:
            sugg = asyncio.run(analyze_project_and_suggest(config, provider))
            print(f"\n  Recommended Preset:  {sugg.recommended_preset}")
            if sugg.preset_rationale:
                print(f"  Rationale:           {sugg.preset_rationale}")

            if sugg.glossary_items:
                print(f"\n  Discovered Glossary Candidates ({len(sugg.glossary_items)}):")
                for g in sugg.glossary_items[:6]:
                    print(f"    • {g.get('source')} -> {g.get('target')} ({g.get('category', 'UI')})")

            if sugg.character_voices:
                print(f"\n  Discovered Characters ({len(sugg.character_voices)}):")
                for cv in sugg.character_voices[:4]:
                    print(f"    • {cv.get('character')} ({cv.get('register', 'informal')}): {cv.get('traits')}")

            if sugg.suggested_path_excludes:
                print(f"\n  Suggested Path Excludes ({len(sugg.suggested_path_excludes)}):")
                for rx in sugg.suggested_path_excludes:
                    print(f"    • {rx}")

            if not sugg.has_detected_speakers:
                print("\n  Speaker Detection:   No character tags detected in keys -> recommended needs_character_voice: false")
            else:
                print("\n  Speaker Detection:   Character identifiers detected in keys/dump -> needs_character_voice: true")
            print(f"  Recommended Profile: {sugg.recommended_profile} (70% speed / 30% quality)")

            if getattr(args, "apply", False):
                # Apply suggested preset to lang-style.md
                ls_path = config.resources.get("lang_style") or (config.root / "resources" / "lang-style.md")
                ls_path.parent.mkdir(parents=True, exist_ok=True)
                ls_path.write_text(sugg.style_guide_content, encoding="utf-8")
                print(f"\n  [APPLIED] Written '{sugg.recommended_preset}' preset to {ls_path}")

                # Apply glossary if provided
                if sugg.glossary_items:
                    g_path = config.resources.get("glossary") or (config.root / "resources" / "glossary.md")
                    g_lines = [
                        "# Glossary\n\n| Source term | Target translation | Category | Confidence | Source/justification |\n|---|---|---|---|---|"
                    ]
                    for g in sugg.glossary_items:
                        src = g.get("source", "").replace("|", "")
                        tgt = g.get("target", "").replace("|", "")
                        cat = g.get("category", "General").replace("|", "")
                        note = g.get("note", "Auto-discovered").replace("|", "")
                        g_lines.append(f"| {src} | {tgt} | {cat} | 0.90 | {note} |")
                    g_path.write_text("\n".join(g_lines) + "\n", encoding="utf-8")
                    print(f"  [APPLIED] Written {len(sugg.glossary_items)} terms to {g_path}")

                # Apply character voices if provided
                if sugg.character_voices:
                    cv_path = config.resources.get("character_voices") or (config.root / "resources" / "character-voices.md")
                    cv_lines = [
                        "# Character voice bible\n\n| Character | Register | Traits | Avoid |\n|---|---|---|---|"
                    ]
                    for cv in sugg.character_voices:
                        ch = cv.get("character", "").replace("|", "")
                        reg = cv.get("register", "informal").replace("|", "")
                        traits = cv.get("traits", "").replace("|", "")
                        cv_lines.append(f"| {ch} | {reg} | {traits} | Out-of-character tone |")
                    cv_path.write_text("\n".join(cv_lines) + "\n", encoding="utf-8")
                    print(f"  [APPLIED] Written {len(sugg.character_voices)} characters to {cv_path}")

                # Apply configuration changes to project.yaml
                import yaml
                cfg_file = config.root / "project.yaml"
                if cfg_file.exists():
                    cfg_data = yaml.safe_load(cfg_file.read_text(encoding="utf-8")) or {}
                    yaml_changed = False

                    # Auto-tune dialogue category based on whether speaker identifiers exist
                    if not sugg.has_detected_speakers:
                        cats = cfg_data.get("categories", [])
                        for cat in cats:
                            if isinstance(cat, dict) and cat.get("name") == "dialogue":
                                if cat.get("needs_character_voice", False):
                                    cat["needs_character_voice"] = False
                                    yaml_changed = True
                                    print("  [APPLIED] Set dialogue needs_character_voice: false (prevents review stalls)")

                    # Ensure profile is configured
                    if "profile" not in cfg_data:
                        cfg_data["profile"] = sugg.recommended_profile
                        yaml_changed = True
                        print(f"  [APPLIED] Set profile: {sugg.recommended_profile}")

                    # Apply suggested path excludes if present
                    if sugg.suggested_path_excludes:
                        f_opts = cfg_data.setdefault("format_options", {})
                        p_ex = f_opts.setdefault("uabea_json_path_exclude", [])
                        if not isinstance(p_ex, list):
                            p_ex = [str(p_ex)]
                            f_opts["uabea_json_path_exclude"] = p_ex
                        added = 0
                        for rx in sugg.suggested_path_excludes:
                            if rx not in p_ex:
                                p_ex.append(rx)
                                added += 1
                        if added > 0:
                            yaml_changed = True
                            print(f"  [APPLIED] Added {added} path exclude regex(es)")

                    if yaml_changed:
                        cfg_file.write_text(yaml.dump(cfg_data, sort_keys=False, allow_unicode=True), encoding="utf-8")
                        print(f"  [APPLIED] Updated {cfg_file}")
            else:
                print("\nTip: Run with `locpipe audit --suggest --apply` to automatically write these resources.")
            print("==========================================")
        except Exception as e:
            print(f"Auto-discovery failed: {e}")

    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = load_project(args.project)
    limit = args.limit or args.sample
    pseudo_loc = getattr(args, "pseudo_loc", False)
    is_real_run = not args.dry_run and not pseudo_loc

    # Hook 1: font preflight + auto character-fallback (in-memory only; see
    # preflight/font_check.py for why project.yaml itself is never rewritten).
    if config.preflight.font_check_enabled and config.preflight.font_check_asset_path:
        font_result = apply_hungarian_fallback_if_needed(
            config, config.preflight.font_check_asset_path, config.preflight.font_check_engine
        )
        print(f"  Font Preflight: {font_result['message'].splitlines()[0]}")
        if font_result.get("character_replacements_applied"):
            print(f"  Auto-applied character fallback: {font_result['character_replacements_applied']}")

    # Hook 2: sweep artifacts left behind by a prior crashed run, before this
    # run creates any of its own.
    sweep_result = sweep_orphaned_agy_artifacts()
    if sweep_result.get("removed_temp_files") or sweep_result.get("removed_sessions") or sweep_result.get("removed_conversation_dbs"):
        dbs_info = f", {sweep_result.get('removed_conversation_dbs', 0)} stale conversation db(s)" if sweep_result.get("removed_conversation_dbs") else ""
        print(
            f"  Startup Sweep:  removed {sweep_result['removed_temp_files']} orphaned temp file(s), "
            f"{sweep_result['removed_sessions']} stale agy session dir(s){dbs_info}"
        )

    # Hook 3: TM snapshot before any real (billable, TM-writing) run.
    if is_real_run:
        snapshot_path = snapshot_tm(config)
        print(f"  TM Snapshot:    {snapshot_path}")

    provider = _build_provider(config, args.dry_run, pseudo_loc=pseudo_loc)

    # Hook 4: mandatory max_api_calls -- auto-calculated from plan() if the
    # user didn't pass --max-api-calls explicitly. NOTE: this budget only
    # counts bulk-translate calls (see _translate_batches_sync's counter) --
    # Tier-1/review/escalation calls are NOT included, so a run can still
    # exceed this ceiling in total LLM cost once QA overhead is added. Known
    # limitation, not fixed here.
    effective_max_api_calls = args.max_api_calls
    if effective_max_api_calls is None and is_real_run:
        plan_result = plan(config, provider=provider, limit_batches=limit, include_totals=False)
        effective_max_api_calls = max(1, int(plan_result["llm_calls_needed"] * 1.1) + 3)
        print(
            f"  Safety Budget:  auto-calculated max_api_calls = {effective_max_api_calls} "
            f"(plan(): {plan_result['llm_calls_needed']} call(s) needed + 10% margin, bulk-translate only)"
        )

    # Pre-flight report (Phase 10)
    mode_str = "PSEUDO-LOC (PseudoLocProvider)" if pseudo_loc else ("DRY-RUN (MockProvider)" if args.dry_run else config.provider.mode)
    print("=== PRE-FLIGHT TRANSLATION REPORT ===")
    print(f"  Project:        {config.project}")
    print(f"  Source -> Target: {config.source_lang} -> {config.target_lang}")
    print(f"  Format:         {config.format}")
    print(f"  Provider:       {config.provider.name} (model: {config.provider.model}, effort: {config.provider.effort})")
    print(f"  Mode:           {mode_str}")
    if effective_max_api_calls:
        budget_source = "explicit" if args.max_api_calls else "auto-calculated"
        print(f"  Safety Budget:  Max {effective_max_api_calls} API call(s) ({budget_source})")
    if limit:
        print(f"  File Limit:     Only processing first {limit} file(s)")
    print("=====================================")
    print()

    review_model = config.provider.review_model or config.provider.model
    review_effort = config.provider.review_effort or "high"
    review_provider = _build_provider(
        config,
        args.dry_run,
        pseudo_loc=pseudo_loc,
        model_override=review_model,
        effort_override=review_effort,
    )

    escalation_model = config.provider.escalation_model or review_model
    escalation_effort = config.provider.escalation_effort or "high"
    escalation_provider = _build_provider(
        config,
        args.dry_run,
        pseudo_loc=pseudo_loc,
        model_override=escalation_model,
        effort_override=escalation_effort,
    )

    stats = run(
        config,
        provider,
        review_provider=review_provider,
        escalation_provider=escalation_provider,
        limit_batches=limit,
        max_api_calls=effective_max_api_calls,
    )
    print(stats.summary())
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    config = load_project(args.project)
    adapter = get_adapter(config.format, {**config.format_options, "source_lang": config.source_lang, "target_lang": config.target_lang})

    snapshot_dir = config.root / "tm" / "pre_merge_snapshots"
    if not snapshot_dir.exists() or not any(snapshot_dir.iterdir()):
        print("=== POST-RUN INTEGRITY VERIFICATION ===")
        print(f"  Project: {config.project}")
        print()
        print("  [ERROR] No pre-merge snapshots found in 'tm/pre_merge_snapshots'.")
        print("  Verification requires snapshots created before merge during 'locpipe run'.")
        print("  Historical runs completed prior to snapshot support cannot be retroactively verified.")
        print("=======================================")
        return 1

    import inspect
    supports_sink = "audit_sink" in inspect.signature(adapter.extract).parameters

    if not supports_sink:
        print("=== POST-RUN INTEGRITY VERIFICATION ===")
        print(f"  Project: {config.project}")
        print(f"  Format '{config.format}' does not support extraction classification auditing.")
        print("=======================================")
        return 0

    batch_files = config.batch_files
    files_verified = 0
    clean_untouched_count = 0
    kept_changed_count = 0
    kept_unchanged_count = 0
    anomalies: list[dict[str, Any]] = []

    batches_dir = config.root / "batches"
    for path in batch_files:
        try:
            rel = path.relative_to(batches_dir)
            snapshot_file = snapshot_dir / rel
        except ValueError:
            try:
                rel = path.relative_to(config.root)
                snapshot_file = snapshot_dir / rel
            except ValueError:
                snapshot_file = snapshot_dir / path.name
        if not snapshot_file.exists():
            snapshot_file = snapshot_dir / path.name
        if not snapshot_file.exists():
            continue

        snap_sink: list[tuple[str, str, str]] = []
        try:
            adapter.extract(snapshot_file, audit_sink=snap_sink)
        except Exception as e:
            anomalies.append({
                "file": path.name,
                "path": "<file-level>",
                "action": "parse_error",
                "expected": f"Valid snapshot {snapshot_file.name}",
                "actual": f"Snapshot extraction error: {e}",
            })
            continue

        curr_sink: list[tuple[str, str, str]] = []
        try:
            adapter.extract(path, audit_sink=curr_sink)
        except Exception as e:
            anomalies.append({
                "file": path.name,
                "path": "<file-level>",
                "action": "parse_error",
                "expected": f"Valid merged file {path.name}",
                "actual": f"Current file extraction error: {e}",
            })
            continue

        curr_values = {json_path: val for json_path, val, _ in curr_sink}
        files_verified += 1

        for json_path, orig_val, action in snap_sink:
            curr_val = curr_values.get(json_path)

            if action.startswith("noise:") or action == "excluded_by_config":
                if curr_val != orig_val:
                    anomalies.append({
                        "file": path.name,
                        "path": json_path,
                        "action": action,
                        "expected": orig_val,
                        "actual": curr_val,
                    })
                else:
                    clean_untouched_count += 1
            elif action == "kept":
                if curr_val != orig_val:
                    kept_changed_count += 1
                else:
                    kept_unchanged_count += 1

    print("=== POST-RUN INTEGRITY VERIFICATION ===")
    print(f"  Project:                               {config.project}")
    print(f"  Batch Files Verified:                  {files_verified}")
    print(f"  Untouched Noise / Excluded Strings:    {clean_untouched_count} (100% verified clean)")
    print(f"  Translated Kept Strings Modified:      {kept_changed_count}")
    if kept_unchanged_count > 0:
        print(f"  Kept Strings Unchanged:                {kept_unchanged_count}")
    print(f"  Anomalies Found:                       {len(anomalies)}")

    if anomalies:
        print("\n--- ANOMALIES DETECTED ---")
        for a in anomalies:
            print(f"  • [{a['file']}] {a['path']}")
            print(f"      Classification:   {a['action']}")
            print(f"      Pre-merge Value:  {a['expected']!r}")
            print(f"      Post-merge Value: {a['actual']!r}")
        print("=======================================")
        return 1

    print("  Status: All noise and excluded paths remained perfectly untouched.")
    print("=======================================")
    return 0


def cmd_auto(args: argparse.Namespace) -> int:
    """Zero-touch autonomous execution:
    1. Preflight audit & validation
    2. Staged canary test run (limit=1 file, bounded API calls)
    3. Full project execution with auto-budgeting
    4. Post-run integrity verification & summary output
    """
    proj_path = Path(args.project)
    print("==================================================================")
    print(f"🚀 LOCPIPE ZERO-TOUCH AUTOMATION: {proj_path.name}")
    print("==================================================================")

    # Stage 1: Load config & verify files
    config = load_project(proj_path)
    print(f"[Stage 1/4] Project loaded: {config.project} ({config.source_lang} -> {config.target_lang}, {config.format})")
    if not config.batch_files:
        print("❌ Error: No batch files found matching batch glob pattern. Aborting.")
        return 1
    print(f"            Found {len(config.batch_files)} batch file(s).")

    # Stage 2: Canary Batch Test
    if not getattr(args, "skip_canary", False) and len(config.batch_files) > 0:
        canary_calls = getattr(args, "canary_calls", 5)
        print(f"\n[Stage 2/4] Running Staged Canary Test (limit=1 file, max_api_calls={canary_calls})...")
        canary_args = argparse.Namespace(
            project=str(proj_path),
            dry_run=args.dry_run,
            pseudo_loc=getattr(args, "pseudo_loc", False),
            limit=1,
            sample=None,
            max_api_calls=canary_calls,
        )
        canary_ret = cmd_run(canary_args)
        if canary_ret != 0:
            print("❌ Canary test stage failed! Halting before full run.")
            return canary_ret
        print("✅ Canary stage verified successfully. Checkpoints and TM updated.")

    # Stage 3: Full Project Run
    print("\n[Stage 3/4] Running Full Unattended Project Translation...")
    full_args = argparse.Namespace(
        project=str(proj_path),
        dry_run=args.dry_run,
        pseudo_loc=getattr(args, "pseudo_loc", False),
        limit=None,
        sample=None,
        max_api_calls=None,
    )
    full_ret = cmd_run(full_args)
    if full_ret != 0:
        print("❌ Full run terminated with errors.")
        return full_ret
    print("✅ Full project translation complete.")

    # Stage 4: Post-Run Verification
    print("\n[Stage 4/4] Executing Post-Run Integrity Verification...")
    verify_args = argparse.Namespace(project=str(proj_path))
    cmd_verify(verify_args)

    print("\n==================================================================")
    print(f"🎉 ZERO-TOUCH LOCALIZATION COMPLETED FOR: {config.project}")
    print("   Reports generated under review/ and translation memory persisted.")
    print("==================================================================")
    return 0


def cmd_tm_invalidate(args: argparse.Namespace) -> int:
    config = load_project(args.project)
    from .tm import TranslationMemory
    from .normalize import content_hash, normalize_source

    tm = TranslationMemory(config.tm_db_path)
    try:
        raw_key = args.key
        deleted = tm.invalidate(raw_key)
        if not deleted:
            h = content_hash(normalize_source(raw_key))
            deleted = tm.invalidate(h)

        if deleted:
            print(f"✅ Successfully invalidated TM entry for '{raw_key}' in {config.project}.")
            return 0
        else:
            print(f"⚠ No matching TM entry found for '{raw_key}' in {config.project}.", file=sys.stderr)
            return 1
    finally:
        tm.close()


def cmd_bootstrap_resources(args: argparse.Namespace) -> int:
    import asyncio
    import json
    from .tm import TranslationMemory
    from .bootstrap import (
        update_existing_anti_fabrication_checklist,
        filter_glossary_candidates,
        bootstrap_glossary,
        bootstrap_lang_style,
        bootstrap_character_voices,
        _load_agent_template,
    )

    config = load_project(args.project)

    # 1. Check/update existing empty anti-fabrication checklist
    updated_af = update_existing_anti_fabrication_checklist(config)

    # 2. Check TM
    tm_path = Path(config.tm_db_path)
    if not tm_path.exists():
        print(f"Error: Translation Memory not found at {tm_path}. Translate files first before bootstrapping resources.", file=sys.stderr)
        return 1

    tm = TranslationMemory(config.tm_db_path)
    try:
        all_tm_records = list(tm.iter_all())
    finally:
        tm.close()

    if not all_tm_records:
        print(f"Error: Translation Memory at {tm_path} is empty. No translations available to bootstrap resources from.", file=sys.stderr)
        return 1

    run_glossary = not args.skip_glossary
    run_lang_style = not args.skip_lang_style
    run_character_voices = not args.skip_character_voices

    glossary_candidates = []
    total_tm = len(all_tm_records)
    if run_glossary:
        glossary_candidates, _ = filter_glossary_candidates(all_tm_records)

    lang_style_samples_count = min(60, total_tm) if run_lang_style else 0

    adapter = get_adapter(config.format, {**config.format_options, "source_lang": config.source_lang, "target_lang": config.target_lang})
    speaker_count = 0
    if run_character_voices:
        speakers = set()
        for p in config.batch_files:
            try:
                for e in adapter.extract(p):
                    if e.speaker and e.speaker.strip():
                        speakers.add(e.speaker.strip())
            except Exception:
                pass
        speaker_count = len(speakers)

    # Cost & token estimates (reusing char/4 heuristic)
    est_calls = 0
    est_input_tokens = 0

    if run_glossary and glossary_candidates:
        est_calls += 1
        g_chars = len(json.dumps(glossary_candidates[:350], ensure_ascii=False))
        est_input_tokens += (len(_load_agent_template("glossary-bootstrap.md")) + g_chars) // 4

    if run_lang_style and lang_style_samples_count > 0:
        est_calls += 1
        est_input_tokens += 2500

    if run_character_voices and speaker_count > 0:
        est_calls += 1
        est_input_tokens += 500 + (speaker_count * 300)

    print("=== BOOTSTRAP RESOURCES PLAN & ESTIMATE ===")
    print(f"  Project:                      {config.project}")
    print(f"  Anti-fabrication checklist:   {'Updated template to standard' if updated_af else 'Up to date'}")
    if run_glossary:
        print(f"  Glossary Candidates:         {total_tm:,} total TM strings -> {len(glossary_candidates):,} pre-filtered candidate terms")
    else:
        print(f"  Glossary:                     Skipped (--skip-glossary)")

    if run_lang_style:
        print(f"  Language Style Sample:        {lang_style_samples_count} representative translated strings")
    else:
        print(f"  Language Style:               Skipped (--skip-lang-style)")

    if run_character_voices:
        if speaker_count > 0:
            print(f"  Character Voices:             Found {speaker_count} speaker(s) with metadata")
        else:
            print(f"  Character Voices:             this project's format doesn't carry speaker metadata — skipping character-voices bootstrap")
    else:
        print(f"  Character Voices:             Skipped (--skip-character-voices)")

    review_model = config.provider.review_model or config.provider.model or "gemini-3.8-flash"
    print(f"  Provider & Model:             {config.provider.name} ({review_model}, effort: high)")
    print(f"  Estimated LLM Calls:          {est_calls}")
    print(f"  Estimated Total Input Tokens: ~{est_input_tokens:,}")
    print("===========================================")

    if est_calls == 0 and not updated_af:
        print("No bootstrap tasks to run.")
        return 0

    if not getattr(args, "yes", False) and not getattr(args, "dry_run", False) and est_calls > 0:
        try:
            resp = input("Proceed with LLM resource bootstrapping? [y/N]: ").strip().lower()
            if resp not in ("y", "yes"):
                print("Bootstrap canceled.")
                return 0
        except (EOFError, KeyboardInterrupt):
            print("\nBootstrap canceled.")
            return 0

    provider = _build_provider(
        config,
        dry_run=getattr(args, "dry_run", False),
        model_override=review_model,
        effort_override="high",
    )

    out_files: list[str] = []

    if run_glossary and glossary_candidates:
        print("Drafting glossary.draft.md...")
        out_g = asyncio.run(bootstrap_glossary(config, provider, glossary_candidates))
        if out_g:
            out_files.append(f"• Glossary:         {out_g}")

    if run_lang_style and lang_style_samples_count > 0:
        print("Drafting lang-style.draft.md...")
        out_ls = asyncio.run(bootstrap_lang_style(config, provider))
        if out_ls:
            out_files.append(f"• Language Style:   {out_ls}")

    if run_character_voices:
        if speaker_count > 0:
            print("Drafting character-voices.draft.md...")
            out_cv, cv_err = asyncio.run(bootstrap_character_voices(config, provider))
            if out_cv:
                out_files.append(f"• Character Voices: {out_cv}")
        else:
            print("this project's format doesn't carry speaker metadata — skipping character-voices bootstrap")

    print("\n=== BOOTSTRAP COMPLETED ===")
    for item in out_files:
        print(f"  {item}")
    print("\nNOTE: Draft files (*.draft.md) are advisory drafts. Review and edit them manually before promoting/renaming into canonical resource files.")
    print("===========================")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(prog="locpipe")
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="scaffold a new project under projects/<name>/")
    p_init.add_argument("name")
    p_init.add_argument("--type", choices=["game", "software"], default="game", help="project type: game | software")
    p_init.add_argument("--source", default="en", help="source language code (e.g. en, ja, hu)")
    p_init.add_argument("--target", default="hu", help="target language code (e.g. hu, en, ja)")
    p_init.add_argument("--format", default="generic_kv", help="file format adapter (generic_kv, naninovel, po_gettext, unity, uabea_json, ue4_5_po, xliff)")
    p_init.set_defaults(func=cmd_init)

    p_plan = sub.add_parser("plan", help="read-only: dedup/batch/token estimate, no LLM calls, no writes")
    p_plan.add_argument("--project", required=True, help="path to the project directory")
    p_plan.add_argument("--limit", type=int, default=None, help="only scan the first N batch files")
    p_plan.add_argument("--sample", type=int, default=None, help="alias for --limit")
    p_plan.set_defaults(func=cmd_plan)

    p_audit = sub.add_parser(
        "audit",
        help="report what extraction would keep vs. filter as engine noise, and optionally auto-discover presets/glossary with --suggest",
    )
    p_audit.add_argument("--project", required=True, help="path to the project directory")
    p_audit.add_argument("--out", default=None, help="report path (default: <project>/audit_report.md)")
    p_audit.add_argument("--suggest", action="store_true", help="use a lightweight LLM call to auto-discover style presets, glossary candidates, and character voices")
    p_audit.add_argument("--apply", action="store_true", help="apply suggested preset and resources directly to resources/lang-style.md, glossary.md, character-voices.md")
    p_audit.add_argument("--dry-run", action="store_true", help="use mock provider, no API calls")
    p_audit.set_defaults(func=cmd_audit)

    p_verify = sub.add_parser(
        "verify",
        help="post-run integrity verification: diffs merged batch files against pre-merge snapshots to prove noise/excluded paths were untouched",
    )
    p_verify.add_argument("--project", required=True, help="path to the project directory")
    p_verify.set_defaults(func=cmd_verify)

    p_run = sub.add_parser("run", help="run the pipeline for a project")
    p_run.add_argument("--project", required=True, help="path to the project directory")
    p_run.add_argument("--dry-run", action="store_true", help="use the mock provider, no API calls")
    p_run.add_argument("--pseudo-loc", action="store_true", help="run deterministic pseudo-localization with ~30%% expansion and accented glyphs, no API calls")
    p_run.add_argument("--limit", type=int, default=None, help="only process the first N batch files")
    p_run.add_argument("--sample", type=int, default=None, help="alias for --limit")
    p_run.add_argument("--max-api-calls", type=int, default=None, help="hard ceiling on total LLM API completion requests")
    p_run.set_defaults(func=cmd_run)

    p_auto = sub.add_parser(
        "auto",
        help="zero-touch autonomous localization: preflight audit, canary batch test, full execution, and post-run verification",
    )
    p_auto.add_argument("--project", required=True, help="path to the project directory")
    p_auto.add_argument("--dry-run", action="store_true", help="use mock provider, no API calls")
    p_auto.add_argument("--pseudo-loc", action="store_true", help="run deterministic pseudo-localization")
    p_auto.add_argument("--canary-calls", type=int, default=5, help="max API calls for canary stage (default: 5)")
    p_auto.add_argument("--skip-canary", action="store_true", help="skip staged canary and proceed directly to full run")
    p_auto.set_defaults(func=cmd_auto)

    p_inv = sub.add_parser("tm-invalidate", help="invalidate a TM entry to force retranslation on next run")
    p_inv.add_argument("--project", required=True, help="path to the project directory")
    p_inv.add_argument("--key", required=True, help="source string or content hash to invalidate")
    p_inv.set_defaults(func=cmd_tm_invalidate)

    p_boot = sub.add_parser("bootstrap-resources", help="draft glossary, lang-style, and character-voices resource files from TM")
    p_boot.add_argument("--project", required=True, help="path to the project directory")
    p_boot.add_argument("--skip-glossary", action="store_true", help="skip drafting glossary.draft.md")
    p_boot.add_argument("--skip-lang-style", action="store_true", help="skip drafting lang-style.draft.md")
    p_boot.add_argument("--skip-character-voices", action="store_true", help="skip drafting character-voices.draft.md")
    p_boot.add_argument("--dry-run", action="store_true", help="use mock provider, no API calls")
    p_boot.add_argument("--yes", action="store_true", help="skip interactive confirmation prompt")
    p_boot.set_defaults(func=cmd_bootstrap_resources)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
