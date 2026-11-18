import json
import os
import shutil
import tempfile
import time
from pathlib import Path
import pytest

from locpipe.preflight.run_safety import sweep_orphaned_agy_artifacts
from locpipe.glossary import load_glossary
from locpipe.validators.glossary_terms import parse_glossary, load_glossary_for_check


def test_sweep_orphaned_agy_artifacts_with_conversations(monkeypatch):
    with tempfile.TemporaryDirectory() as tmp_home, tempfile.TemporaryDirectory() as tmp_temp:
        fake_home = Path(tmp_home)
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        monkeypatch.setattr(tempfile, "gettempdir", lambda: tmp_temp)

        brain_dir = fake_home / ".gemini" / "antigravity-cli" / "brain"
        conv_dir = fake_home / ".gemini" / "antigravity-cli" / "conversations"
        brain_dir.mkdir(parents=True, exist_ok=True)
        conv_dir.mkdir(parents=True, exist_ok=True)

        now = time.time()
        stale_time = now - 7200  # 2 hours old
        fresh_time = now - 100   # 100 seconds old

        # 1. Stale locpipe session 1: has both brain dir and conversation DB + wal + shm
        sess1_brain = brain_dir / "session_locpipe_stale_1"
        sess1_brain.mkdir()
        logs_dir = sess1_brain / ".system_generated" / "logs"
        logs_dir.mkdir(parents=True)
        (logs_dir / "transcript.jsonl").write_text('{"content":"locpipe_agy_prompt_stale.txt"}')
        os.utime(sess1_brain, (stale_time, stale_time))

        sess1_db = conv_dir / "session_locpipe_stale_1.db"
        sess1_wal = conv_dir / "session_locpipe_stale_1.db-wal"
        sess1_shm = conv_dir / "session_locpipe_stale_1.db-shm"
        sess1_db.write_text("locpipe_agy_prompt_dummy db")
        sess1_wal.write_text("dummy wal")
        sess1_shm.write_text("dummy shm")
        os.utime(sess1_db, (stale_time, stale_time))

        # 2. Stale locpipe session 2: orphaned conversation DB without a brain folder
        sess2_db = conv_dir / "session_locpipe_stale_2_nobrain.db"
        sess2_db.write_text("locpipe_agy_prompt_dummy db 2")
        os.utime(sess2_db, (stale_time, stale_time))

        # 3. Fresh locpipe session 3: active, should NOT be removed
        sess3_brain = brain_dir / "session_locpipe_fresh_3"
        sess3_brain.mkdir()
        logs3_dir = sess3_brain / ".system_generated" / "logs"
        logs3_dir.mkdir(parents=True)
        (logs3_dir / "transcript.jsonl").write_text('{"content":"locpipe_agy_prompt_fresh.txt"}')
        os.utime(sess3_brain, (fresh_time, fresh_time))

        sess3_db = conv_dir / "session_locpipe_fresh_3.db"
        sess3_db.write_text("locpipe fresh db")
        os.utime(sess3_db, (fresh_time, fresh_time))

        # 4. Stale USER session: MUST NOT be removed even though > 2 hours old!
        user_stale_brain = brain_dir / "user_stale_coding_chat"
        user_stale_brain.mkdir()
        user_logs = user_stale_brain / ".system_generated" / "logs"
        user_logs.mkdir(parents=True)
        (user_logs / "transcript.jsonl").write_text('{"content":"Please help me write a Python script"}')
        os.utime(user_stale_brain, (stale_time, stale_time))

        user_stale_db = conv_dir / "user_stale_coding_chat.db"
        user_stale_db.write_text("user sqlite db content without any proprietary keywords")
        os.utime(user_stale_db, (stale_time, stale_time))

        # 5. Temp prompt files
        stale_temp = Path(tmp_temp) / "locpipe_agy_prompt_stale.txt"
        stale_temp.write_text("prompt")
        os.utime(stale_temp, (stale_time, stale_time))

        fresh_temp = Path(tmp_temp) / "locpipe_agy_prompt_fresh.txt"
        fresh_temp.write_text("fresh prompt")
        os.utime(fresh_temp, (fresh_time, fresh_time))

        # 6. User multi-turn coding chat that DISCUSSES locpipe_agy_prompt_: MUST NEVER be deleted!
        user_locpipe_chat_brain = brain_dir / "user_chat_discussing_locpipe"
        user_locpipe_chat_brain.mkdir()
        user_locpipe_logs = user_locpipe_chat_brain / ".system_generated" / "logs"
        user_locpipe_logs.mkdir(parents=True)
        user_locpipe_logs_content = "\n".join([
            json.dumps({"step_index": 0, "content": "Can you inspect locpipe_agy_prompt_stale.txt for me?"}),
            json.dumps({"step_index": 1, "content": "Sure, here are the contents of that prompt file."}),
            json.dumps({"step_index": 2, "content": "Thanks, now let's write a unit test."}),
        ])
        (user_locpipe_logs / "transcript.jsonl").write_text(user_locpipe_logs_content)
        os.utime(user_locpipe_chat_brain, (stale_time, stale_time))

        user_locpipe_chat_db = conv_dir / "user_chat_discussing_locpipe.db"
        user_locpipe_chat_db.write_text("dummy db mentioning locpipe_agy_prompt_stale.txt in conversation")
        os.utime(user_locpipe_chat_db, (stale_time, stale_time))

        res = sweep_orphaned_agy_artifacts(max_age_s=3600)

        assert res["removed_temp_files"] == 1
        assert res["removed_sessions"] == 1
        assert res["removed_conversation_dbs"] == 2

        # Check that stale locpipe artifacts were unlinked
        assert not sess1_brain.exists()
        assert not sess1_db.exists()
        assert not sess1_wal.exists()
        assert not sess1_shm.exists()
        assert not sess2_db.exists()
        assert not stale_temp.exists()

        # Check that fresh artifacts remain untouched
        assert sess3_brain.exists()
        assert sess3_db.exists()
        assert fresh_temp.exists()

        # CRITICAL SAFETY CHECK: Verify user sessions were NOT deleted!
        assert user_stale_brain.exists()
        assert user_stale_db.exists()
        assert user_locpipe_chat_brain.exists(), "User multi-turn chat discussing locpipe prompt was wrongly deleted!"
        assert user_locpipe_chat_db.exists(), "User multi-turn chat DB was wrongly deleted!"


def test_load_glossary_json_bare_list(tmp_path):
    json_path = tmp_path / "glossary.json"
    content = [
        {
            "source": "Health Potion",
            "target": "Életerő ital",
            "category": "mechanic",
            "confidence": "high",
            "justification": "Standard RPG item"
        }
    ]
    json_path.write_text(json.dumps(content), encoding="utf-8")

    terms = load_glossary(json_path)
    assert len(terms) == 1
    assert terms[0].source_term == "Health Potion"
    assert terms[0].target_term == "Életerő ital"


def test_load_glossary_json_dict_wrapped(tmp_path):
    # Tests {"terms": [...]}
    json_path = tmp_path / "glossary.json"
    content = {
        "terms": [
            {
                "source": "Mana Potion",
                "target": "Mana ital",
                "category": "mechanic",
                "confidence": "high"
            }
        ]
    }
    json_path.write_text(json.dumps(content), encoding="utf-8")

    terms = load_glossary(json_path)
    assert len(terms) == 1
    assert terms[0].source_term == "Mana Potion"
    assert terms[0].target_term == "Mana ital"


def test_load_glossary_json_malformed_raises(tmp_path):
    json_path = tmp_path / "glossary.json"
    json_path.write_text('{"terms": [{"source": "Broken", }', encoding="utf-8")  # syntax error

    with pytest.raises(ValueError, match="Failed to parse JSON glossary"):
        load_glossary(json_path)


def test_load_glossary_json_invalid_structure_raises(tmp_path):
    json_path = tmp_path / "glossary.json"
    json_path.write_text(json.dumps({"invalid_key": "not a list"}), encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid JSON glossary format"):
        load_glossary(json_path)


def test_parse_glossary_json_error_reporting(tmp_path):
    json_path = tmp_path / "glossary.json"

    # 1. Malformed JSON
    json_path.write_text('{"terms": [{"source": "Broken", }', encoding="utf-8")
    entries, issues = parse_glossary(json_path)
    assert len(entries) == 0
    assert len(issues) == 1
    assert "Érvénytelen JSON szintaxis" in issues[0][1]

    # load_glossary_for_check should raise on malformed JSON
    with pytest.raises(ValueError, match="Failed to load JSON glossary"):
        load_glossary_for_check(str(json_path))

    # 2. Dict-wrapped JSON
    json_path.write_text(json.dumps({"terms": [{"source": "Fireball", "target": "Tűzgolyó"}]}), encoding="utf-8")
    entries, issues = parse_glossary(json_path)
    assert len(entries) == 1
    assert len(issues) == 0
    assert entries[0]["source"] == "Fireball"
    assert entries[0]["target"] == "Tűzgolyó"

    checked = load_glossary_for_check(str(json_path))
    assert len(checked) == 1
    assert checked[0]["source"] == "Fireball"


def test_parse_glossary_markdown_still_works(tmp_path):
    md_path = tmp_path / "glossary.md"
    md_path.write_text(
        "| Source term | Target translation | Category | Confidence | Source/justification |\n"
        "|---|---|---|---|---|\n"
        "| Gold | Arany | mechanic | high | Core currency |\n",
        encoding="utf-8"
    )

    terms = load_glossary(md_path)
    assert len(terms) == 1
    assert terms[0].source_term == "Gold"
    assert terms[0].target_term == "Arany"

    entries, issues = parse_glossary(md_path)
    assert len(entries) == 1
    assert len(issues) == 0
    assert entries[0]["source"] == "Gold"
