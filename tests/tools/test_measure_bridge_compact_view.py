"""Read-only measurements use one bounded corpus and make no gate claims."""
import json
from datetime import datetime, timezone

import pytest

from tools.bridge_compact_view import _json, _read_view_rows, compact_view, event_id
from tools.measure_bridge_compact_view import main, measure


NOW = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)


def event(kind="message", **fields):
    return dict(ts_utc="2026-09-13T11:59:00Z", agent="codex-tools-1",
                session_id="session-1", task_id="task-1", type=kind,
                status="progress", message="ä" * 1000) | fields


def corpus(tmp_path, rows):
    path = tmp_path / "events.jsonl"
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return path


def test_exact_rendered_size_and_coverage_with_disjoint_exclusions(tmp_path):
    heartbeat = event("heartbeat")
    work = event("finding")
    rows = [heartbeat, heartbeat, work, dict(work), event("unknown_future_type")]
    path = corpus(tmp_path, rows)
    result = measure(path, now=NOW)
    selected, cursor, reader = _read_view_rows(path, 5000, "")
    compact = compact_view(selected) | {"cursor": cursor, "reader": reader}
    assert result["bytes"]["source_json_utf8"] == len(_json(rows).encode("utf-8"))
    assert result["bytes"]["compact_json_utf8"] == len(_json(compact).encode("utf-8"))
    assert result["coverage"] == {
        "unique_meaningful_source_events": 2, "visible_events": 2,
        "all_meaningful_refs_preserved": True,
        "meaningful_chronology_preserved": True,
        "full_payload_preservation": "not_claimed_use_event_detail",
    }
    assert result["exclusions"] == {"exact_canonical_duplicates": 2,
                                    "unique_heartbeat_or_liveness_events": 1}
    assert result["traffic"]["all_rows"]["heartbeat_or_liveness"] == 2
    assert result["traffic"]["unique_rows"]["useful_work_proxy"] == 2
    assert result["authority"] == "none"
    assert result["provider_metrics"] == {"tokens": None, "cost": None,
                                           "reason": "no_provider_measurement"}
    assert set(result["timings_seconds"]) == {"read_parse", "compact_render"}


def test_tail_comparison_uses_same_selected_corpus_and_does_not_mutate(tmp_path):
    path = corpus(tmp_path, [event(message=str(index) + "ä" * 1000) for index in range(20)])
    cursor_path = tmp_path / "cursor.json"
    cursor_path.write_bytes(b"cursor must remain unchanged")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    result = measure(path, tail=2, now=NOW)
    assert result["selected_events"] == 2
    assert result["scope"] == "bounded_snapshot_not_complete_history"
    assert result["reader"]["snapshot_length"] == len(before[path.name])
    assert result["reader"]["bytes_read"] < result["reader"]["snapshot_length"]
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_explicit_received_ack_correlation_and_latency_are_advisory(tmp_path):
    request = event(status="requested", to="codex-lead-1")
    ack = event(status="received", agent="codex-lead-1", to=request["agent"],
                ts_utc="2026-09-13T11:59:20Z", payload={
                    "request_ts_utc": request["ts_utc"], "request_agent": request["agent"],
                    "request_type": request["type"], "request_status": request["status"],
                })
    result = measure(corpus(tmp_path, [request, ack]), now=NOW)
    assert result["traffic"]["unique_rows"]["ack"] == 1
    observed = result["lifecycle"]["received_ack_observations"][0]
    assert observed["request_ref"] == event_id(request)
    assert observed["correlation"] == "unique_explicit_fields_in_snapshot"
    assert observed["ack_wait_seconds"] == 20.0
    assert observed["request_age_seconds"] == 60.0
    assert result["lifecycle"]["completion_wait_seconds"] is None


@pytest.mark.parametrize("variant", ["missing", "negative", "ambiguous", "wrong_recipient", "future", "invalid"])
def test_ack_unknown_or_negative_time_never_becomes_success(tmp_path, variant):
    request = event(status="requested", to="codex-lead-1")
    ack = event(status="received", agent="codex-lead-1", to=request["agent"],
                ts_utc="2026-09-13T11:59:20Z", payload={
                    "request_ts_utc": request["ts_utc"], "request_agent": request["agent"],
                    "request_type": request["type"], "request_status": request["status"],
                })
    rows = [request, ack]
    if variant == "missing":
        ack["payload"] = {}
    elif variant == "negative":
        ack["ts_utc"] = "2026-09-13T11:58:00Z"
    elif variant == "ambiguous":
        rows.insert(1, request | {"session_id": "other-session"})
    elif variant == "future":
        ack["ts_utc"] = "2026-09-13T12:00:20Z"
    elif variant == "invalid":
        ack["ts_utc"] = "not-a-time"
    else:
        request["to"] = "codex-review-1"
    observed = measure(corpus(tmp_path, rows), now=NOW)["lifecycle"]["received_ack_observations"][0]
    assert observed["ack_wait_seconds"] is None
    assert observed["reason"] != "observed"


@pytest.mark.parametrize("content", ["not json\n", "[]\n", '{"type": []}\n'])
def test_bad_source_fails_explicitly_without_echoing_input(tmp_path, capsys, content):
    path = tmp_path / "events.jsonl"
    path.write_text(content, encoding="utf-8")
    assert main(["--events", str(path)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["action"] == "inspect raw snapshot; do not infer no work"


def test_missing_source_and_empty_corpus_are_distinct(tmp_path):
    missing = measure(tmp_path / "absent", now=NOW)
    assert missing["source_availability"] == "missing"
    assert missing["bytes"]["compact_to_source_ratio"] is None
    assert missing["coverage"]["all_meaningful_refs_preserved"] is None
    empty = measure(corpus(tmp_path, []), now=NOW)
    assert empty["source_availability"] == "observed"
    assert empty["coverage"]["all_meaningful_refs_preserved"] is True


def test_cli_emits_json_without_writes_or_token_estimates(tmp_path, capsys):
    path = corpus(tmp_path, [event()])
    before = path.read_bytes()
    assert main(["--events", str(path), "--tail", "1",
                 "--now", "2026-09-13T12:00:00Z"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["selected_events"] == 1
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("tail", [-1, 100001])
def test_tail_limits_rejected(tmp_path, tail):
    with pytest.raises(ValueError, match="tail"):
        measure(tmp_path / "absent", tail=tail, now=NOW)


def test_oversized_tail_row_fails_with_existing_reader_byte_budget(tmp_path, monkeypatch, capsys):
    from waggledance.core import bridge_log_reader

    monkeypatch.setattr(bridge_log_reader, "MAX_MAX_BYTES", 128)
    path = corpus(tmp_path, [event()])
    before = path.read_bytes()
    assert main(["--events", str(path), "--tail", "1"]) == 2
    assert capsys.readouterr().out == ""
    assert path.read_bytes() == before
