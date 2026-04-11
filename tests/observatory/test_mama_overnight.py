# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the overnight real-data collector + analysis tools.

The tests build a tiny temporary SQLite database that mirrors the
schema of ``data/chat_history.db`` and drive the overnight collector
against it. None of these tests touch the real chat history file.

Coverage targets (from x.txt PHASE 5):

* checkpoint resume works
* empty night does not crash and produces an honest report
* new real rows are observed exactly once (no duplicates)
* synthetic data cannot leak into the overnight verdict
* UTF-8 / cp1252 risks do not break reporting
* the strongest candidate preview uses ``redacted_utterance``
* ``assert_no_hype`` is called before the final report is written
* the morning report never makes a stronger claim than the data
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from waggledance.observatory.mama_events.gate import GateVerdict
from waggledance.observatory.mama_events.reports import assert_no_hype


_ROOT = Path(__file__).resolve().parents[2]
_COLLECTOR = _ROOT / "tools" / "mama_event_overnight.py"
_ANALYSIS = _ROOT / "tools" / "mama_event_overnight_analysis.py"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def collector():
    return _load("mama_event_overnight", _COLLECTOR)


@pytest.fixture(scope="module")
def analysis():
    return _load("mama_event_overnight_analysis", _ANALYSIS)


# ── helpers ──────────────────────────────────────────────


def _make_db(path: Path, rows):
    """Create a chat_history.db with the given rows.

    rows: iterable of (id, conversation_id, role, content, agent_name, language, created_at)
    """
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            "CREATE TABLE messages ("
            "id INTEGER PRIMARY KEY, conversation_id INTEGER, role TEXT, "
            "content TEXT, agent_name TEXT, language TEXT, "
            "response_time_ms INTEGER, created_at TIMESTAMP)"
        )
        conn.executemany(
            "INSERT INTO messages "
            "(id, conversation_id, role, content, agent_name, language, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _append_rows(path: Path, rows):
    conn = sqlite3.connect(str(path))
    try:
        conn.executemany(
            "INSERT INTO messages "
            "(id, conversation_id, role, content, agent_name, language, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _baseline_rows():
    """A few-row corpus with one user turn and three assistant turns."""
    return [
        (1, 1, "user", "Hei robotti", None, "fi", "2026-04-10T00:00:00"),
        (2, 1, "assistant", "Tervehdys ihmiselle, mita kysyt?", "consciousness", "fi", "2026-04-10T00:00:01"),
        (3, 1, "user", "Kerro itsestasi", None, "fi", "2026-04-10T00:00:02"),
        (4, 1, "assistant", "Olen WaggleDance-runtime ja prosessoin tekstia.", "consciousness", "fi", "2026-04-10T00:00:03"),
    ]


# ── checkpoint round-trip ────────────────────────────────


def test_checkpoint_round_trip(tmp_path, collector):
    cp_path = tmp_path / "checkpoint.json"
    cp = collector.Checkpoint(
        last_row_id=42,
        cumulative_real_events=7,
        first_started_at="2026-04-10T01:00:00Z",
        last_run_started_at="2026-04-10T02:00:00Z",
        last_run_ended_at="2026-04-10T03:00:00Z",
    )
    collector.save_checkpoint(cp_path, cp)
    loaded = collector.load_checkpoint(cp_path)
    assert loaded is not None
    assert loaded.last_row_id == 42
    assert loaded.cumulative_real_events == 7
    assert loaded.first_started_at == "2026-04-10T01:00:00Z"
    # No leftover .tmp file from atomic write
    assert not (tmp_path / "checkpoint.json.tmp").exists()


def test_checkpoint_missing_returns_none(tmp_path, collector):
    assert collector.load_checkpoint(tmp_path / "nope.json") is None


def test_checkpoint_corrupt_returns_none(tmp_path, collector):
    cp_path = tmp_path / "checkpoint.json"
    cp_path.write_text("{not json", encoding="utf-8")
    assert collector.load_checkpoint(cp_path) is None


# ── tail-only first run ──────────────────────────────────


def test_first_run_tail_mode_skips_existing_rows(tmp_path, collector):
    db = tmp_path / "chat.db"
    _make_db(db, _baseline_rows())
    out = tmp_path / "out"
    res = collector.run_once(
        db_path=db, out_dir=out, include_existing=False,
        base_ts_ms=1_700_000_000_000, snapshot_interval_seconds=0,
        duration_seconds=0, poll_interval_seconds=0, watch=False,
        batch_limit=100,
    )
    assert res["fresh_start"] is True
    assert res["new_real_events_this_run"] == 0
    cp = json.loads((out / "checkpoint.json").read_text(encoding="utf-8"))
    assert cp["last_row_id"] == 4  # advanced past existing
    assert cp["cumulative_real_events"] == 0


def test_first_run_include_existing_processes_rows(tmp_path, collector):
    db = tmp_path / "chat.db"
    _make_db(db, _baseline_rows())
    out = tmp_path / "out"
    res = collector.run_once(
        db_path=db, out_dir=out, include_existing=True,
        base_ts_ms=1_700_000_000_000, snapshot_interval_seconds=0,
        duration_seconds=0, poll_interval_seconds=0, watch=False,
        batch_limit=100,
    )
    # only assistant rows become events: 2 of 4
    assert res["new_real_events_this_run"] == 2
    assert res["cumulative_real_events"] == 2
    assert res["checkpoint_start_row_id"] == 0
    assert res["checkpoint_end_row_id"] == 4


# ── resume + no duplicates ───────────────────────────────


def test_resume_picks_up_new_rows_only(tmp_path, collector):
    db = tmp_path / "chat.db"
    _make_db(db, _baseline_rows())
    out = tmp_path / "out"
    # First run: tail-only, sets checkpoint to MAX(id)
    collector.run_once(
        db_path=db, out_dir=out, include_existing=False,
        base_ts_ms=1_700_000_000_000, snapshot_interval_seconds=0,
        duration_seconds=0, poll_interval_seconds=0, watch=False,
        batch_limit=100,
    )
    # New rows arrive
    _append_rows(db, [
        (5, 1, "user", "Lisaa kysymyksia", None, "fi", "2026-04-10T00:01:00"),
        (6, 1, "assistant", "Vastaan parhaani mukaan ja yritan auttaa.", "consciousness", "fi", "2026-04-10T00:01:01"),
        (7, 1, "assistant", "Lisaksi voin tarkentaa edellista vastaustani jos se auttaa.", "consciousness", "fi", "2026-04-10T00:01:02"),
    ])
    res2 = collector.run_once(
        db_path=db, out_dir=out, include_existing=False,
        base_ts_ms=1_700_000_000_000, snapshot_interval_seconds=0,
        duration_seconds=0, poll_interval_seconds=0, watch=False,
        batch_limit=100,
    )
    assert res2["fresh_start"] is False
    assert res2["new_real_events_this_run"] == 2
    assert res2["cumulative_real_events"] == 2
    assert res2["checkpoint_start_row_id"] == 4
    assert res2["checkpoint_end_row_id"] == 7

    # Third run with no new data: still no duplicates, no new events
    res3 = collector.run_once(
        db_path=db, out_dir=out, include_existing=False,
        base_ts_ms=1_700_000_000_000, snapshot_interval_seconds=0,
        duration_seconds=0, poll_interval_seconds=0, watch=False,
        batch_limit=100,
    )
    assert res3["new_real_events_this_run"] == 0
    assert res3["cumulative_real_events"] == 2
    assert res3["checkpoint_start_row_id"] == 7
    assert res3["checkpoint_end_row_id"] == 7

    # Snapshot indices must remain globally monotonic across all
    # three cycles so the morning stability check does not flag the
    # restart as a regression.
    snaps = [
        json.loads(line)
        for line in (out / "overnight_snapshots.ndjson").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    indices = [s["snapshot_index"] for s in snaps]
    assert indices == sorted(indices)
    assert indices[0] == 0
    assert len(set(indices)) == len(indices)  # no duplicates


# ── empty night ──────────────────────────────────────────


def test_empty_night_does_not_crash(tmp_path, collector):
    db = tmp_path / "chat.db"
    _make_db(db, [])
    out = tmp_path / "out"
    res = collector.run_once(
        db_path=db, out_dir=out, include_existing=False,
        base_ts_ms=1_700_000_000_000, snapshot_interval_seconds=0,
        duration_seconds=0, poll_interval_seconds=0, watch=False,
        batch_limit=100,
    )
    assert res["new_real_events_this_run"] == 0
    assert res["cumulative_real_events"] == 0
    # Snapshots are still recorded (start + end)
    snaps_path = out / "overnight_snapshots.ndjson"
    assert snaps_path.is_file() and snaps_path.stat().st_size > 0


# ── window seeding from prior rows ───────────────────────


def test_window_seeding_uses_prior_db_rows(tmp_path, collector):
    db = tmp_path / "chat.db"
    _make_db(db, _baseline_rows())
    # Pretend the checkpoint already advanced to id=2
    cp_path = tmp_path / "out" / "checkpoint.json"
    cp_path.parent.mkdir(parents=True)
    cp_path.write_text(
        json.dumps({"last_row_id": 2, "cumulative_real_events": 0}),
        encoding="utf-8",
    )
    seeded = collector.fetch_window_rows_before(db, before_id=2, n=3)
    assert seeded[-1] == "Tervehdys ihmiselle, mita kysyt?"
    assert "Hei robotti" in seeded


# ── UTF-8 / cp1252 hardness ──────────────────────────────


def test_utf8_replacement_does_not_crash_reader(tmp_path, collector):
    db = tmp_path / "chat.db"
    # Write a row with raw bytes the SQLite text_factory will need to
    # decode forgivingly. This mimics legacy cp1252-encoded entries.
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE messages ("
        "id INTEGER PRIMARY KEY, conversation_id INTEGER, role TEXT, "
        "content TEXT, agent_name TEXT, language TEXT, "
        "response_time_ms INTEGER, created_at TIMESTAMP)"
    )
    bad_blob = b"\xe4iti on aiti? \xff\xfe joo joo joo joo joo joo"
    conn.execute(
        "INSERT INTO messages (id, conversation_id, role, content, agent_name, language, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (1, 1, "assistant", bad_blob, "consciousness", "fi", "2026-04-10T00:00:00"),
    )
    conn.commit()
    conn.close()
    rows = collector.fetch_rows_after(db, after_id=0)
    assert len(rows) == 1
    assert isinstance(rows[0].content, str)  # forgiving decode succeeded


# ── analysis: empty overnight ────────────────────────────


def test_analysis_empty_overnight_renders_honest_report(tmp_path, analysis, collector):
    db = tmp_path / "chat.db"
    _make_db(db, [])
    out = tmp_path / "out"
    collector.run_once(
        db_path=db, out_dir=out, include_existing=False,
        base_ts_ms=1_700_000_000_000, snapshot_interval_seconds=0,
        duration_seconds=0, poll_interval_seconds=0, watch=False,
        batch_limit=100,
    )
    result = analysis.analyse(out)
    assert result.data_status == analysis.DATA_STATUS_NO_NEW
    assert result.observatory_verdict == GateVerdict.NO_CANDIDATES.value
    # The two are reported as separate fields, never collapsed
    assert result.new_real_events_this_run == 0
    text = analysis.render_overnight_report(result)
    assert "data_status" in text
    assert "observatory_verdict" in text
    assert "NO_NEW_REAL_DATA" in text
    assert_no_hype(text)


# ── analysis: with new data ──────────────────────────────


def test_analysis_with_new_data_separates_status_and_verdict(tmp_path, analysis, collector):
    db = tmp_path / "chat.db"
    _make_db(db, _baseline_rows())
    out = tmp_path / "out"
    collector.run_once(
        db_path=db, out_dir=out, include_existing=True,
        base_ts_ms=1_700_000_000_000, snapshot_interval_seconds=0,
        duration_seconds=0, poll_interval_seconds=0, watch=False,
        batch_limit=100,
    )
    result = analysis.analyse(out)
    assert result.data_status == analysis.DATA_STATUS_NEW
    assert result.new_real_events_this_run == 2
    # Observatory verdict on benign data: NO_CANDIDATES (no target tokens)
    assert result.observatory_verdict == GateVerdict.NO_CANDIDATES.value
    text = analysis.render_overnight_report(result)
    assert "NEW_REAL_DATA_OBSERVED" in text
    assert GateVerdict.NO_CANDIDATES.value in text  # NO_CANDIDATE_EVENTS


# ── strongest verdict ────────────────────────────────────


def test_strongest_verdict_uses_closed_gate_ordering(analysis):
    assert analysis.strongest_verdict([]) == GateVerdict.NO_CANDIDATES.value
    assert analysis.strongest_verdict([
        GateVerdict.NO_CANDIDATES.value,
        GateVerdict.WEAK_SPONTANEOUS_ONLY.value,
        GateVerdict.NO_CANDIDATES.value,
    ]) == GateVerdict.WEAK_SPONTANEOUS_ONLY.value
    assert analysis.strongest_verdict([
        GateVerdict.WEAK_SPONTANEOUS_ONLY.value,
        GateVerdict.STRONG_PROTO_SOCIAL.value,
        GateVerdict.GROUNDED_CANDIDATE.value,
    ]) == GateVerdict.STRONG_PROTO_SOCIAL.value
    # Unknown verdicts are ignored, never inflate the result
    assert analysis.strongest_verdict([
        "FAKE_HIGHER_TIER",
        GateVerdict.WEAK_SPONTANEOUS_ONLY.value,
    ]) == GateVerdict.WEAK_SPONTANEOUS_ONLY.value


# ── synthetic leak prevention ────────────────────────────


def test_analyse_only_reads_overnight_directory(tmp_path, analysis):
    """The analyser must read ONLY from its given directory.

    If the longrun synthetic NDJSON happens to live elsewhere on
    disk, the overnight analysis must not pick it up. We assert
    this by pointing the analyser at an empty directory and
    verifying every count is zero / NO_CANDIDATES even though the
    repo also contains synthetic longrun artifacts.
    """
    empty = tmp_path / "empty_overnight"
    empty.mkdir()
    result = analysis.analyse(empty)
    assert result.new_real_events_this_run == 0
    assert result.cumulative_real_events == 0
    assert result.candidate_events == 0
    assert result.max_score == 0
    assert result.observatory_verdict == GateVerdict.NO_CANDIDATES.value
    assert result.data_status == analysis.DATA_STATUS_NO_NEW
    # And the report still renders cleanly
    text = analysis.render_overnight_report(result)
    assert_no_hype(text)


# ── render asserts no-hype ───────────────────────────────


def test_render_overnight_report_blocks_hype(analysis):
    """If a forbidden term reaches the rendered text via any
    passthrough field, the in-renderer ``assert_no_hype`` call must
    abort ``render_overnight_report`` itself — not some downstream
    re-check. We exercise this by smuggling a forbidden term through
    one of the string fields the renderer interpolates verbatim and
    expecting AssertionError straight from ``render_overnight_report``.
    """
    tainted = analysis.OvernightAnalysis(
        data_status=analysis.DATA_STATUS_NO_NEW,
        observatory_verdict=GateVerdict.NO_CANDIDATES.value,
        # ``first_started_at`` is rendered as plain text inside Section 1
        first_started_at="2026-04-10T00:00:00Z conscious",
    )
    with pytest.raises(AssertionError):
        analysis.render_overnight_report(tainted)


# ── write_overnight_report happy path ────────────────────


def test_write_overnight_report_writes_to_disk(tmp_path, analysis):
    out = tmp_path / "out"
    out.mkdir()
    report_path = tmp_path / "MAMA_EVENT_OVERNIGHT.md"
    an = analysis.OvernightAnalysis(
        data_status=analysis.DATA_STATUS_NO_NEW,
        observatory_verdict=GateVerdict.NO_CANDIDATES.value,
    )
    written = analysis.write_overnight_report(an, report_path=report_path)
    assert written == report_path
    text = report_path.read_text(encoding="utf-8")
    assert "Overnight Run" in text
    assert "data_status" in text
    assert_no_hype(text)


# ── top candidates use redacted_utterance ───────────────


def test_collect_top_candidates_prefers_redacted_utterance(analysis):
    events = [
        {
            "score": {"total": 60, "band": "STRONG_PROTO_SOCIAL"},
            "event": {
                "session_id": "s1",
                "caregiver_candidate_id": "ann",
                "redacted_utterance": "redacted-preview",
                "utterance_text": "raw-secret-text",
            },
            "contamination": {"flags": []},
        },
    ]
    rows = analysis.collect_top_candidates(events)
    assert rows
    assert rows[0]["preview"] == "redacted-preview"
    assert "raw-secret-text" not in rows[0]["preview"]


# ── stability check warns on missing snapshots ──────────


def test_stability_warns_on_missing_snapshots(tmp_path, analysis):
    out = tmp_path / "out"
    out.mkdir()
    # Empty overnight dir → analyse returns NO_NEW with stability warnings
    result = analysis.analyse(out)
    assert any("snapshot" in note.lower() for note in result.framework_stability_notes)
