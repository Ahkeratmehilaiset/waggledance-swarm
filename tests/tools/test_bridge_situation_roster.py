"""Synthetic roster matching; no live bridge, secrets, or provider calls."""

from copy import deepcopy
from hashlib import sha256
import json

import pytest


LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")


def source_ref(event):
    raw = json.dumps(event, sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return sha256(raw).hexdigest()


def inputs():
    lanes, handshakes, sources = [], [], []
    for i, lane in enumerate(LANES):
        identity = {"agent": lane, "agent_uuid": f"synthetic-uuid-{i}",
                    "session_id": f"synthetic-session-{i}", "run_id": f"synthetic-run-{i}"}
        lanes.append({"agent": lane, "agent_uuid": identity["agent_uuid"]})
        event = dict(identity, type="status", status="append_canary", message="SYNTHETIC BODY")
        ref = source_ref(event)
        handshakes.append(dict(identity, status="bridge_bootstrapped", current=True,
                               source_event_ref=ref))
        sources.append({"source_event_ref": ref, "event": event})
    fleet = {"lanes": [row for row in lanes if row["agent"] != "codex-tools-1"],
             "tools_supervisor": next(row for row in lanes if row["agent"] == "codex-tools-1")}
    return fleet, handshakes, sources


def assess(fleet, handshakes, sources):
    from tools.bridge_situation_roster import assess_roster
    return assess_roster(fleet=fleet, handshakes=handshakes, source_events=sources)


def test_matched_content_is_not_authenticated_or_usable(capsys):
    fleet, handshakes, sources = inputs()
    original = deepcopy((fleet, handshakes, sources))
    result = assess(fleet, handshakes, sources)
    assert result["coverage"] == "UNKNOWN"
    assert result["authority"] == "none"
    assert result["source_authentication"] == "UNKNOWN"
    assert result["role_verification"] == "UNKNOWN"
    assert result["reason"] == "identity_source_unverified"
    assert result["roster"] is None
    assert result["matched_count"] == len(LANES)
    assert "SYNTHETIC BODY" not in json.dumps(result)
    assert (fleet, handshakes, sources) == original
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("change", ["missing_handshake", "missing_source", "uuid_conflict",
                                    "event_identity_conflict", "reference_mismatch",
                                    "duplicate_fleet", "duplicate_current"])
def test_missing_or_conflicting_evidence_never_yields_a_roster(change):
    fleet, handshakes, sources = inputs()
    if change == "missing_handshake":
        handshakes.pop()
    elif change == "missing_source":
        sources.pop()
    elif change == "uuid_conflict":
        handshakes[0]["agent_uuid"] = "wrong-uuid"
    elif change == "event_identity_conflict":
        sources[0]["event"]["session_id"] = "wrong-session"
        ref = source_ref(sources[0]["event"])
        sources[0]["source_event_ref"] = ref
        handshakes[0]["source_event_ref"] = ref
    elif change == "reference_mismatch":
        sources[0]["event"]["message"] = "different content"
    elif change == "duplicate_fleet":
        fleet["lanes"].append(deepcopy(fleet["lanes"][0]))
    elif change == "duplicate_current":
        handshakes.append(deepcopy(handshakes[0]))
    result = assess(fleet, handshakes, sources)
    assert result["coverage"] == "UNKNOWN"
    assert result["roster"] is None
    assert result["reason"] != "identity_source_unverified"


def test_history_must_also_match_sources_and_only_one_session_is_current():
    fleet, handshakes, sources = inputs()
    old = deepcopy(handshakes[0])
    old.update(session_id="previous-session", run_id="previous-run", current=False)
    event = {key: old[key] for key in ("agent", "agent_uuid", "session_id", "run_id")}
    event.update(type="status", status="append_canary")
    old["source_event_ref"] = source_ref(event)
    handshakes.append(old)
    sources.append({"source_event_ref": old["source_event_ref"], "event": event})
    result = assess(fleet, handshakes, sources)
    assert result["reason"] == "identity_source_unverified"
    assert result["matched_count"] == len(LANES) + 1
    assert result["roster"] is None


@pytest.mark.parametrize("bad", [None, [], "bad", {"lanes": []}])
def test_malformed_inputs_are_unknown_without_exception_or_echo(bad):
    _, handshakes, sources = inputs()
    result = assess(bad, handshakes, sources)
    assert result["coverage"] == "UNKNOWN" and result["roster"] is None
    assert result["reason"] != "identity_source_unverified"


def test_assessment_never_opens_files_or_starts_processes(monkeypatch):
    import builtins
    import subprocess
    from tools.bridge_situation_roster import assess_roster
    fleet, handshakes, sources = inputs()

    def forbidden(*args, **kwargs):
        raise AssertionError("unexpected I/O")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    result = assess_roster(fleet=fleet, handshakes=handshakes, source_events=sources)
    assert result["coverage"] == "UNKNOWN" and result["roster"] is None


@pytest.mark.parametrize("where", ["fleet_agent", "handshake_agent", "non_json_event", "bad_ref"])
def test_untrusted_types_and_noncanonical_refs_refuse_without_exception(where):
    fleet, handshakes, sources = inputs()
    if where == "fleet_agent":
        fleet["lanes"][0]["agent"] = []
    elif where == "handshake_agent":
        handshakes[0]["agent"] = {}
    elif where == "non_json_event":
        sources[0]["event"]["not_json"] = float("nan")
    else:
        sources[0]["source_event_ref"] = "é" * 64
    result = assess(fleet, handshakes, sources)
    assert result["coverage"] == "UNKNOWN" and result["roster"] is None
    assert result["reason"] != "identity_source_unverified"
