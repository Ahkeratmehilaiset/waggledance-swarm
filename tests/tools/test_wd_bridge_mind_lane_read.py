# SPDX-License-Identifier: BUSL-1.1
"""Synthetic tests for the read-only Mind lane projection (tools/wd_bridge_mind_lane_read.py).

Every input is synthetic and lives under one tmp ``.codex-audit`` root: a producer-shaped ``latest.json`` pointer,
its cycle ``state.json`` with the pinned reader's position cursor, and a publication written by the core's own
``write_snapshot``. No bridge, network, process, clock or gate is touched.
"""
from __future__ import annotations

import ast
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re

import pytest

from tools import wd_bridge_mind as core
from tools import wd_bridge_mind_lane_read as lane_read
from tools.bridge_compact_view import event_id

LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")
AS_OF = "2026-10-31T07:59:00Z"
NOW = datetime(2026, 10, 31, 7, 59, tzinfo=timezone.utc)
HEAD = "84b6fe0f109f6753e8e78ec9562217a1a9f860a0"
BASE = "23564ad4c0494b659bb726f1ed4c57cbbfbac1ba"
UUIDS = {"codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101", "codex-tools-1": "11111111-2222-4333-8444-555555555555",
         "claude-rco-1": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee", "claude-rco-2": "99999999-8888-4777-8666-555555555555",
         "fable-5": "f5f5f5f5-0a0a-4b0b-8c0c-0d0d0d0d0d0d"}  # synthetic roster: lane -> uuid the caller trusts
FOREIGN_UUID = "0f0f0f0f-1111-4222-8333-444444444444"
LOG_ID = "windows-v1:0000abcd:0000000000000001"  # bridge log reader identity shape, synthetic values
OTHER_LOG_ID = "windows-v1:0000abcd:0000000000000002"
SECRET_PATH = "tools/claimed_secret_scope_path.py"
CYCLE = "20261031T075900Z"
PRODUCER = Path(__file__).resolve().parents[2] / "sandbox" / "mind" / "mind_capture_producer.py"
READER = "Read-AgentBridge.ps1 -Compact -EventId"  # the producer's READER; pinned against its source below


def cursor(file_identity=LOG_ID, generation="g1", offset=10):
    body = json.dumps({"file_identity": file_identity, "generation": generation, "offset": offset},
                      sort_keys=True, separators=(",", ":"))
    return "bc1." + base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii").rstrip("=")


def view(token=None):
    return {"schema": "wd.bridge.compact-view.v1", "authority": "none", "cursor": token or cursor()}


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def event(agent, kind, status, task, ts, **payload):
    row = {"agent": agent, "agent_uuid": UUIDS[agent], "session_id": "s1", "run_id": "r1", "type": kind,
           "status": status, "task_id": task, "ts_utc": ts, "payload": payload}
    return {"event": row, "ref": event_id(row)}


def rows():
    claim = event("codex-lead-1", "claim", "active", "lead-task", "2026-10-31T07:50:00Z",
                  write_scope=[SECRET_PATH], claim_lease_expires_utc="2026-10-31T08:20:00Z")
    note = event("codex-tools-1", "message", "milestone", "tools-task", "2026-10-31T07:51:00Z")
    opened = event("codex-lead-1", "message", "opened", "codex-lead-1/mind-live-read-20261031", "2026-10-31T07:52:00Z",
                   pr=1963, exact_head=HEAD, base=BASE, required_checks_green=True)
    vote = event("claude-rco-1", "decision", "rco_pass", "codex-lead-1/mind-live-read-20261031", "2026-10-31T07:53:00Z",
                 pr=1963, exact_head=HEAD)
    veto = event("claude-rco-2", "finding", "changes_requested", "codex-lead-1/mind-live-read-20261031",
                 "2026-10-31T07:58:00Z", pr=1963, exact_head=HEAD)
    return [claim, note, opened, vote, veto]


def producer_last_ok(name, digests, *, snapshot_sha256, board_text_sha256, context=False, observed_utc=AS_OF):
    """The ``last_ok`` exactly as the producer's ``candidate_cycle`` returns it (sandbox/mind/mind_capture_producer.py
    at c07334cd): 18 keys, exact file names under ``cycles/<name>/``, ``context_file``/``context_sha256`` None without
    a context. ``test_g6_the_fixture_and_the_contract_are_derived_from_the_producer_source`` pins this mirror to the
    producer's own return literal so the two cannot drift apart silently again (RCO2 G6)."""
    return {
        "cycle_dir": name, "generation": 1, "observed_utc": observed_utc,
        "capture_file": f"cycles/{name}/capture.json", "capture_sha256": digests["capture.json"],
        "context_file": f"cycles/{name}/context.json" if context else None,
        "context_sha256": digests["context.json"] if context else None,
        "state_file": f"cycles/{name}/state.json", "state_sha256": digests["state.json"],
        "publication_file": f"cycles/{name}/run/reflected.publication.json",
        "board_file": f"cycles/{name}/run/board.txt", "receipt_file": f"cycles/{name}/run/receipt.json",
        "reader": READER, "snapshot_sha256": snapshot_sha256,
        "board_text_sha256": board_text_sha256, "counts": {"events": 5, "lanes": 3},
        "global_question": "synthetic question text",
        "measures": {"capture_ms": 3, "context_ms": 0, "run_ms": 7, "events": 5, "new_rows": 5, "capture_bytes": 38,
                     "board_bytes": 6, "publication_bytes": 1024},
    }


def producer_contract():
    """``(READER, last_ok keys)`` read from the producer source itself: the module constant and the keys of the dict
    literal ``candidate_cycle`` returns. Static ``ast`` only; the producer is never imported or run here."""
    tree = ast.parse(PRODUCER.read_text(encoding="utf-8"))
    reader = keys = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "READER" for t in node.targets):
            reader = ast.literal_eval(node.value)
        if isinstance(node, ast.FunctionDef) and node.name == "candidate_cycle":
            returned = [n.value for n in ast.walk(node) if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)]
            assert len(returned) == 1
            keys = {ast.literal_eval(k) for k in returned[0].keys}
    assert reader is not None and keys is not None
    return reader, keys


def make_live(tmp_path: Path, *, log_identity="bridge-log:" + LOG_ID, generation="g1", token=None, events=None,
              context=False):
    audit = tmp_path / ".codex-audit"
    audit.mkdir(parents=True)
    cycle = audit / "cycles" / CYCLE
    (cycle / "run").mkdir(parents=True)
    events = rows() if events is None else events
    source = {"log_identity": log_identity, "generation": generation, "cursor_start": 0, "cursor_end": len(events),
              "event_count": len(events), "coverage": "complete"}
    state = core.full_rebuild(events, source=source, as_of_utc=AS_OF)
    snapshot = core.snapshot(state, as_of_utc=AS_OF)
    publication = cycle / "run" / "reflected.publication.json"
    digest = core.write_snapshot(snapshot, publication, audit_root=audit)
    capture = cycle / "capture.json"
    capture.write_bytes(b'{"schema":"wd.bridge-mind-capture.v1"}')
    state_obj = {"schema": "wd.bridge-mind-producer-state.v1", "generation": 1, "cursor": token or cursor(),
                 "source": source, "window_sha256": "0" * 64, "window": []}
    state_bytes = json.dumps(state_obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    (cycle / "state.json").write_bytes(state_bytes)
    (cycle / "run" / "board.txt").write_bytes(b"board\n")
    (cycle / "run" / "receipt.json").write_bytes(b'{"schema":"wd.bridge-mind-shadow-receipt.v1"}')
    digests = {"capture.json": sha(capture.read_bytes()), "state.json": sha(state_bytes)}
    if context:
        (cycle / "context.json").write_bytes(b'{"schema":"wd.bridge-mind-context.v1"}')
        digests["context.json"] = sha((cycle / "context.json").read_bytes())
    last_ok = producer_last_ok(CYCLE, digests, snapshot_sha256=digest, board_text_sha256=sha(b"board\n"),
                               context=context)
    latest = {"schema": "wd.bridge-mind-shadow-latest.v2", "authority": "none", "scope": "shadow_observation_not_gate",
              "committed_utc": AS_OF, "last_ok": last_ok}
    latest_path = audit / "latest.json"
    latest_path.write_text(json.dumps(latest, sort_keys=True), encoding="utf-8")
    return audit, latest_path, cycle


def read(audit, latest_path, **overrides):
    args = dict(latest_path=latest_path, audit_root=audit, lane="codex-lead-1", as_of=NOW, binding=view(), roster=dict(UUIDS))
    args.update(overrides)
    return lane_read.lane_read(**args)


@pytest.mark.parametrize("lane", LANES)
def test_every_lane_gets_the_same_safe_projection_without_paths_or_peer_identities(tmp_path, lane):
    audit, latest, _ = make_live(tmp_path)
    result = read(audit, latest, lane=lane)
    assert result["schema"] == lane_read.SCHEMA and result["authority"] == "none" and result["lane"] == lane
    assert result["freshness"]["status"] == "fresh" and result["refusal"] is None
    assert set(result["lanes"]) == set(LANES)
    assert result["lanes"]["fable-5"] == {"observed_in_window": False, "status": "not_seen_in_window"}
    assert result["source"]["window"] == {"cursor_start": 0, "cursor_end": 5, "coverage": "complete"}
    lead = result["lanes"]["codex-lead-1"]
    assert lead["observed_in_window"] is True and lead["identity_verification"] == "roster_bound" and lead["claims"] == [{"task_id": "lead-task", "lease_expires_utc": "2026-10-31T08:20:00Z", "expired": False}]
    pr = result["prs"]["1963"]
    assert pr["head"] == HEAD and pr["base"] == BASE and pr["reported_ci_status"] == "reported_green"
    assert pr["missing"] == ["gate_eligibility_UNKNOWN"]
    assert set(pr) == {"pr", "head", "base", "class", "reported_ci_status", "missing"}  # no decision-derived aggregates
    text = core.canonical_bytes(result).decode("utf-8")
    assert SECRET_PATH not in text and "claimed_secret" not in text
    for peer in ("claude-rco-1", "claude-rco-2"):  # peer votes and vetoes are neither attributed nor counted
        assert text.count(peer) == (3 if peer == lane else 2)  # lanes key + identity.bound_lanes, plus the lane field when it is this lane
    for value in UUIDS.values():
        assert value not in text
    assert result["binding"] == {"file_identity": LOG_ID, "generation": "g1", "origin": "lane_reader_view_not_authenticated"}
    assert result["pointer"]["cycle_generation"] == 1 and result["source"]["generation"] == "g1"
    assert all("value" not in fact for fact in result["facts_meta"]) and result["facts_meta"]


def test_current_producer_identity_fails_closed_and_names_the_missing_change(tmp_path):
    audit, latest, _ = make_live(tmp_path, log_identity="capture-window:" + "a" * 64, generation=None)
    with pytest.raises(lane_read.LaneReadRefused) as info:
        read(audit, latest)
    assert str(info.value) == "producer_binding_unavailable"
    assert "shadow_materializer_run_source_declaration" in info.value.missing_producer_change and "generation None" in info.value.missing_producer_change


def test_pointer_race_state_replaced_after_commit_is_refused(tmp_path):
    audit, latest, cycle = make_live(tmp_path)
    (cycle / "state.json").write_bytes((cycle / "state.json").read_bytes() + b" ")
    with pytest.raises(lane_read.LaneReadRefused, match="pointer_race_or_mismatch"):
        read(audit, latest)


def test_pointer_snapshot_digest_mismatch_is_refused(tmp_path):
    audit, latest, _ = make_live(tmp_path)
    obj = json.loads(latest.read_text(encoding="utf-8"))
    obj["last_ok"]["snapshot_sha256"] = "0" * 64
    latest.write_text(json.dumps(obj), encoding="utf-8")
    with pytest.raises(lane_read.LaneReadRefused, match="pointer_race_or_mismatch"):
        read(audit, latest)


@pytest.mark.parametrize("binding, reason", [
    (view(cursor(file_identity=OTHER_LOG_ID)), "log_identity_mismatch"),
    (view(cursor(generation="g2")), "log_identity_mismatch"),
    (view(cursor(generation=None)), "generation_unavailable"),
    (view("bc1.not-base64!"), "binding_invalid"),
    ({"schema": "other", "authority": "none", "cursor": cursor()}, "binding_invalid"),
    ("g1", "binding_invalid"),
])
def test_untrusted_or_absent_generation_binding_is_refused(tmp_path, binding, reason):
    audit, latest, _ = make_live(tmp_path)
    with pytest.raises(lane_read.LaneReadRefused, match=reason):
        read(audit, latest, binding=binding)


def test_state_cursor_differing_from_snapshot_identity_is_refused(tmp_path):
    audit, latest, _ = make_live(tmp_path, token=cursor(file_identity=OTHER_LOG_ID))
    with pytest.raises(lane_read.LaneReadRefused, match="log_identity_mismatch"):
        read(audit, latest, binding=view(cursor(file_identity=OTHER_LOG_ID)))


def test_fresh_stale_boundary_future_and_naive_clock(tmp_path):
    audit, latest, _ = make_live(tmp_path)
    assert read(audit, latest, as_of=NOW + timedelta(seconds=900))["freshness"]["status"] == "fresh"
    with pytest.raises(lane_read.LaneReadRefused, match="snapshot_stale"):  # G1: stale never returns a body
        read(audit, latest, as_of=NOW + timedelta(seconds=901))
    with pytest.raises(lane_read.LaneReadRefused, match="snapshot_stale"):
        read(audit, latest, as_of=NOW + timedelta(seconds=301), max_age=timedelta(seconds=300))
    for bad in (timedelta(seconds=901), timedelta(0), timedelta(seconds=-1), 900, None):  # caller cannot widen the window
        with pytest.raises(lane_read.LaneReadRefused, match="max_age_invalid"):
            read(audit, latest, max_age=bad)
    with pytest.raises(lane_read.LaneReadRefused, match="future_snapshot"):
        read(audit, latest, as_of=NOW - timedelta(seconds=1))
    with pytest.raises(lane_read.LaneReadRefused, match="invalid_context"):
        read(audit, latest, as_of=datetime(2026, 10, 31, 8, 0))


@pytest.mark.parametrize("lane", ["unknown-agent", "codex-lead-1/../x", "", None, ["codex-lead-1"]])
def test_unknown_or_path_shaped_lane_is_refused(tmp_path, lane):
    audit, latest, _ = make_live(tmp_path)
    with pytest.raises(lane_read.LaneReadRefused, match="lane_unknown"):
        read(audit, latest, lane=lane)


def test_pointer_outside_the_audit_root_is_refused(tmp_path):
    audit, latest, _ = make_live(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    copy = other / "latest.json"
    copy.write_bytes(latest.read_bytes())
    with pytest.raises(lane_read.LaneReadRefused, match="audit_path_refused"):
        read(audit, copy)


def test_absent_pointer_and_absent_publication_are_refused(tmp_path):
    audit, latest, cycle = make_live(tmp_path)
    with pytest.raises(lane_read.LaneReadRefused, match="pointer_invalid"):
        read(audit, audit / "missing.json")
    (cycle / "run" / "reflected.publication.json").unlink()
    with pytest.raises(lane_read.LaneReadRefused, match="snapshot_read_refused"):
        read(audit, latest)


def test_a_value_outside_the_allowlist_refuses_the_whole_output_without_echo(tmp_path, monkeypatch):
    audit, latest, _ = make_live(tmp_path)
    original = core.read_snapshot
    marker = "user" + "_at_" + "example.invalid"

    def tampered(path, *, audit_root):
        value = original(path, audit_root=audit_root)
        value["source"]["last_event_ref"] = marker
        return value

    monkeypatch.setattr(lane_read.core, "read_snapshot", tampered)
    monkeypatch.setattr(lane_read, "_pointer_digest_check", lambda *_: None)
    with pytest.raises(lane_read.LaneReadRefused) as info:
        read(audit, latest)
    assert str(info.value) == "field_not_allowlisted" and marker not in repr(info.value.__dict__)


def test_output_budget_is_enforced_before_any_result(tmp_path, monkeypatch):
    audit, latest, _ = make_live(tmp_path)
    monkeypatch.setattr(lane_read, "BUDGET", 64)
    with pytest.raises(lane_read.LaneReadRefused, match="output_budget_exceeded"):
        read(audit, latest)


def test_module_reads_no_clock_network_process_or_gate():
    text = Path(lane_read.__file__).read_text(encoding="utf-8")
    for token in ("datetime.now", "time.time(", "subprocess", "socket", "urllib", "requests", "verify_bridge_consensus",
                  "check_bridge_changes_requested", "check_rco_pass_present", "idle_consensus_auto_merge"):
        assert token not in text, token
    imports = re.findall(r"^(?:from|import)\s+([A-Za-z0-9_.]+)", text, re.MULTILINE)
    assert all(name.split(".")[0] in {"base64", "datetime", "hashlib", "json", "pathlib", "re", "stat", "typing", "tools", "__future__"} for name in imports), imports
    tools_imports = re.findall(r"^from tools import ([A-Za-z0-9_]+)", text, re.MULTILINE)
    assert tools_imports == ["wd_bridge_mind"], tools_imports  # the core only: no shadow pipeline, no sandbox, no gate


_DECISION_WORDS = ("vote", "veto", "recusal", "decision", "rco_pass", "changes_requested", "consensus")


@pytest.mark.parametrize("lane", LANES)
def test_two_rco_noninterference_a_peer_decision_changes_nothing_in_the_projection(tmp_path, lane):
    """Lead finding candidate 08:32:43Z: a reviewer who knows its own decision is absent must not be able to infer a peer
    decision from any aggregate. The projection with one RCO decision must equal the projection with the same event
    replaced by a plain message, for every lane, and no decision-derived key may exist at all."""
    claim, note, opened, vote, _veto = rows()
    silent = event("claude-rco-1", "message", "note", "codex-lead-1/mind-live-read-20261031", "2026-10-31T07:53:00Z")
    audit_a, latest_a, _ = make_live(tmp_path / "a", events=[claim, note, opened, vote])
    audit_b, latest_b, _ = make_live(tmp_path / "b", events=[claim, note, opened, silent])
    one = read(audit_a, latest_a, lane=lane)
    other = read(audit_b, latest_b, lane=lane)
    assert core.canonical_bytes(one["prs"]) == core.canonical_bytes(other["prs"])
    assert core.canonical_bytes(one["facts_meta"]) == core.canonical_bytes(other["facts_meta"])
    text = core.canonical_bytes(one).decode("utf-8").lower()
    assert not [word for word in _DECISION_WORDS if word in text]


def test_g2_r2_a_foreign_uuid_row_never_overrides_a_roster_bound_lane_and_never_blanks_the_view(tmp_path):
    """RCO2 G2 + RCO1 R2: a second declared uuid under a lane name is ignored for that lane when the caller roster binds
    the lane; the other lanes stay readable (no whole-view refusal)."""
    events = rows()
    foreign = event("claude-rco-1", "message", "milestone", "other-task", "2026-10-31T07:58:30Z")
    foreign["event"]["agent_uuid"] = FOREIGN_UUID
    foreign["ref"] = event_id(foreign["event"])
    audit, latest, _ = make_live(tmp_path, events=events + [foreign])
    result = read(audit, latest)
    rco1 = result["lanes"]["claude-rco-1"]
    assert rco1["observed_in_window"] is True and rco1["identity_verification"] == "roster_bound"
    assert rco1["last_event_utc"] == "2026-10-31T07:53:00Z"  # the real row, not the later foreign one
    assert result["lanes"]["codex-lead-1"]["observed_in_window"] is True
    text = core.canonical_bytes(result).decode("utf-8")
    assert "other-task" not in text and FOREIGN_UUID not in text and "0f0f0f0f" not in text


def test_r1_a_lone_foreign_uuid_never_impersonates_a_silent_lane(tmp_path):
    """RCO1 R1 P2: the real fable-5 lane is silent in the window; one fabricated well-formed uuid under its name must not
    read as the lane. With a roster the row is unverified; without a roster nothing is authenticated."""
    events = rows()
    fake = event("fable-5", "claim", "active", "fake-task", "2026-10-31T07:58:30Z",
                 write_scope=["tools/z.py"], claim_lease_expires_utc="2026-10-31T08:30:00Z")
    fake["event"]["agent_uuid"] = FOREIGN_UUID
    fake["ref"] = event_id(fake["event"])
    audit, latest, _ = make_live(tmp_path, events=events + [fake])
    result = read(audit, latest)
    assert result["lanes"]["fable-5"] == {"observed_in_window": False, "status": "unverified_identity_rows_only"}
    text = core.canonical_bytes(result).decode("utf-8")
    assert "fake-task" not in text and FOREIGN_UUID not in text and "roster_bound" in text
    assert result["identity"] == {"roster": "caller_declared", "bound_lanes": sorted(UUIDS)}


def test_r1_without_a_roster_every_observed_lane_is_unknown_and_bodyless(tmp_path):
    audit, latest, _ = make_live(tmp_path)
    for roster in (None, {}):
        result = read(audit, latest, roster=roster)
        assert result["identity"] == {"roster": "none", "bound_lanes": []}
        for lane in ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2"):
            assert result["lanes"][lane] == {"observed_in_window": core.UNKNOWN, "status": "roster_unbound",
                                             "identity_verification": "declared_not_authenticated"}
        assert result["lanes"]["fable-5"] == {"observed_in_window": False, "status": "not_seen_in_window"}
        text = core.canonical_bytes(result).decode("utf-8")
        assert "lead-task" not in text and SECRET_PATH not in text and "roster_bound" not in text
    partial = read(audit, latest, roster={"codex-lead-1": UUIDS["codex-lead-1"]})
    assert partial["lanes"]["codex-lead-1"]["identity_verification"] == "roster_bound"
    assert partial["lanes"]["codex-tools-1"]["status"] == "roster_unbound"
    assert partial["identity"] == {"roster": "caller_declared", "bound_lanes": ["codex-lead-1"]}


@pytest.mark.parametrize("roster", [{"codex-lead-1": "not-a-uuid"}, {"someone-else": UUIDS["codex-lead-1"]}, "codex-lead-1",
                                    [("codex-lead-1", UUIDS["codex-lead-1"])], {"codex-lead-1": None}])
def test_r1_a_malformed_roster_is_refused(tmp_path, roster):
    audit, latest, _ = make_live(tmp_path)
    with pytest.raises(lane_read.LaneReadRefused, match="roster_invalid"):
        read(audit, latest, roster=roster)


SHAPED = "C:/Users/example/private"


@pytest.mark.parametrize("field", ["missing", "coverage", "log_identity", "fact_verification"])
def test_r3_path_shaped_values_in_fixed_token_fields_are_refused_without_echo(tmp_path, monkeypatch, field):
    """RCO1 R3: every output token field is pinned to its fixed vocabulary or exact shape, so a path can never ride a
    generic token regex into the projection."""
    audit, latest, _ = make_live(tmp_path)
    original = core.read_snapshot

    def tampered(path, *, audit_root):
        value = original(path, audit_root=audit_root)
        if field == "missing":
            value["prs"]["1963"]["missing"] = [SHAPED]
        elif field == "coverage":
            value["source"]["coverage"] = SHAPED
        elif field == "log_identity":
            value["source"]["log_identity"] = "bridge-log:" + SHAPED
        else:
            value["facts"][0]["verification"] = SHAPED
        return value

    monkeypatch.setattr(lane_read.core, "read_snapshot", tampered)
    monkeypatch.setattr(lane_read, "_pointer_digest_check", lambda *_: None)
    with pytest.raises(lane_read.LaneReadRefused) as info:
        read(audit, latest)
    assert str(info.value) in {"field_not_allowlisted", "log_identity_mismatch"} and "example" not in repr(info.value.__dict__)


@pytest.mark.parametrize("identity", [SHAPED, "/srv/log", "windows-v1:0000abcd:../x", "posix-v1:C:/Users/x", "synthetic-log"])
def test_r3_binding_file_identity_must_have_the_reader_identity_shape(tmp_path, identity):
    audit, latest, _ = make_live(tmp_path)
    with pytest.raises(lane_read.LaneReadRefused) as info:
        read(audit, latest, binding=view(cursor(file_identity=identity)))
    assert str(info.value) == "binding_invalid" and "Users" not in repr(info.value.__dict__)
    assert lane_read.decode_cursor(cursor(file_identity="posix-v1:fd01:1a2b3c")) == ("posix-v1:fd01:1a2b3c", "g1")


def test_g2_rows_without_a_verified_identity_never_mark_a_lane_observed(tmp_path):
    events = rows()
    row = {"agent": "fable-5", "session_id": "s1", "run_id": "r1", "type": "message", "status": "milestone",
           "task_id": "fable-task", "ts_utc": "2026-10-31T07:58:30Z", "payload": {}}  # no agent_uuid at all
    unverified = {"event": row, "ref": event_id(row)}
    audit, latest, _ = make_live(tmp_path, events=events + [unverified])
    result = read(audit, latest)
    assert result["lanes"]["fable-5"] == {"observed_in_window": False, "status": "unverified_identity_rows_only"}
    text = core.canonical_bytes(result).decode("utf-8")
    assert "fable-task" not in text and "absent" not in text.lower()


@pytest.mark.parametrize("shaped", ["C:/Users/example/private", "/srv/data/x", "home/user/private/x", "x:y", "a/../b"])
def test_na_path_shaped_task_ids_are_redacted_to_unknown_and_never_echoed(tmp_path, shaped):
    """RCO2 N-a via Lead 08:47:06Z: a claim task_id shaped like a path must not reach the output; legitimate lane/branch
    task ids keep working. Synthetic tokens only, no real private path."""
    events = rows()
    shaped_claim = event("codex-tools-1", "claim", "active", shaped, "2026-10-31T07:57:00Z",
                         write_scope=["tools/x.py"], claim_lease_expires_utc="2026-10-31T08:30:00Z")
    branch_claim = event("claude-rco-2", "claim", "active", "codex-lead-1/mind-live-read-20261031", "2026-10-31T07:57:30Z",
                         write_scope=["tools/y.py"], claim_lease_expires_utc="2026-10-31T08:30:00Z")
    audit, latest, _ = make_live(tmp_path, events=events + [shaped_claim, branch_claim])
    result = read(audit, latest)
    tools_claims = result["lanes"]["codex-tools-1"]["claims"]
    assert [row["task_id"] for row in tools_claims] == [core.UNKNOWN]
    assert [row["task_id"] for row in result["lanes"]["claude-rco-2"]["claims"]] == ["codex-lead-1/mind-live-read-20261031"]
    assert [row["task_id"] for row in result["lanes"]["codex-lead-1"]["claims"]] == ["lead-task"]
    text = core.canonical_bytes(result).decode("utf-8")
    for fragment in ("Users", "example", "private", "srv", "data/x", "home", "x:y", "a/.."):
        assert fragment not in text


# -- G6 (RCO2): the pointer schema is the producer's REQUIRED plus KNOWN_OPTIONAL last_ok, never a reduced one ----------

def rewrite_pointer(latest: Path, change):
    obj = json.loads(latest.read_text(encoding="utf-8"))
    change(obj["last_ok"])
    latest.write_text(json.dumps(obj), encoding="utf-8")


def test_g6_the_fixture_and_the_contract_are_derived_from_the_producer_source():
    reader, keys = producer_contract()
    assert reader == READER == lane_read.READER
    assert keys == lane_read.LAST_OK_REQUIRED | lane_read.LAST_OK_OPTIONAL
    assert set(producer_last_ok(CYCLE, {"capture.json": "0" * 64, "state.json": "1" * 64}, snapshot_sha256="2" * 64,
                                board_text_sha256="3" * 64)) == keys
    assert lane_read.LAST_OK_REQUIRED.isdisjoint(lane_read.LAST_OK_OPTIONAL)
    assert "receipt_file" in lane_read.LAST_OK_REQUIRED and "global_question" in lane_read.LAST_OK_OPTIONAL


def test_g6_p1_a_pointer_exactly_as_the_producer_commits_it_is_accepted_and_only_four_fields_reach_the_output(tmp_path):
    audit, latest, _ = make_live(tmp_path)  # 18 keys, context None
    pointer = lane_read.read_pointer(latest, audit)
    assert set(pointer) == lane_read.LAST_OK_REQUIRED  # the known optional fields never leave read_pointer
    result = read(audit, latest)
    assert result["refusal"] is None
    assert set(result["pointer"]) == {"cycle_dir", "cycle_generation", "snapshot_sha256", "state_sha256"}
    text = json.dumps(result)
    for value in ("synthetic question text", "capture_ms", "board_text", READER, "observed_utc", "receipt.json"):
        assert value not in text


def test_g6_p2_a_pointer_with_a_context_file_and_digest_is_accepted(tmp_path):
    audit, latest, _ = make_live(tmp_path, context=True)
    assert read(audit, latest)["refusal"] is None


def _p3_unknown_key(p):
    p["extra_key"] = 1


def _p3_producer_drift_key(p):
    p["window_sha256"] = "0" * 64


def _p4_receipt_missing(p):
    del p["receipt_file"]


def _p4_receipt_renamed(p):
    p["receipt_file"] = f"cycles/{CYCLE}/run/receipt2.json"


def _p5_board_traversal(p):
    p["board_file"] = f"cycles/{CYCLE}/run/../board.txt"


def _p5_board_other_name(p):
    p["board_file"] = f"cycles/{CYCLE}/run/board.md"


def _p5_publication_other_cycle(p):
    p["publication_file"] = "cycles/20261031T000000Z/run/reflected.publication.json"


def _p6_context_file_without_digest(p):
    p["context_file"] = f"cycles/{CYCLE}/context.json"


def _p6_digest_without_context_file(p):
    p["context_sha256"] = "a" * 64


def _p6_context_file_other_name(p):
    p["context_file"], p["context_sha256"] = f"cycles/{CYCLE}/context2.json", "a" * 64


def _p6_context_pair_half_present(p):
    del p["context_sha256"]


def _p8_generation_true(p):
    p["generation"] = True


def _p8_generation_zero(p):
    p["generation"] = 0


def _p9_reader_other_value(p):
    p["reader"] = "Read-AgentBridge.ps1 -Compact"


def _observed_utc_not_a_time(p):
    p["observed_utc"] = "yesterday"


def _board_text_sha_not_hex(p):
    p["board_text_sha256"] = "zz"


def _counts_not_an_object(p):
    p["counts"] = [1, 2]


def _measures_negative(p):
    p["measures"] = {"capture_ms": -1}


def _measures_bool(p):
    p["measures"] = {"capture_ms": True}


def _measures_string(p):
    p["measures"] = {"capture_ms": "3"}


def _legacy_nine_key_pointer(p):
    for key in list(p):
        if key not in {"cycle_dir", "generation", "capture_file", "state_file", "publication_file", "board_file",
                       "capture_sha256", "state_sha256", "snapshot_sha256"}:
            del p[key]


@pytest.mark.parametrize("change", [
    _p3_unknown_key, _p3_producer_drift_key, _p4_receipt_missing, _p4_receipt_renamed, _p5_board_traversal,
    _p5_board_other_name, _p5_publication_other_cycle, _p6_context_file_without_digest, _p6_digest_without_context_file,
    _p6_context_file_other_name, _p6_context_pair_half_present, _p8_generation_true, _p8_generation_zero,
    _p9_reader_other_value, _observed_utc_not_a_time, _board_text_sha_not_hex, _counts_not_an_object,
    _measures_negative, _measures_bool, _measures_string, _legacy_nine_key_pointer,
], ids=lambda f: f.__name__.strip("_"))
def test_g6_p3_to_p9_a_pointer_outside_the_producer_contract_is_refused_without_echo(tmp_path, change):
    audit, latest, _ = make_live(tmp_path)
    rewrite_pointer(latest, change)
    with pytest.raises(lane_read.LaneReadRefused, match="pointer_invalid") as info:
        read(audit, latest)
    assert str(info.value) == "pointer_invalid"


def test_g6_p7_free_text_in_the_pointer_never_reaches_the_output(tmp_path):
    audit, latest, _ = make_live(tmp_path)
    secrets = ("C:/Users/example/private/notes.txt", "token=sk-synthetic-0000", "user" + "_at_" + "example.invalid")

    def change(p):
        p["global_question"] = " ".join(secrets)
        p["counts"] = {secrets[0]: 1}

    rewrite_pointer(latest, change)
    result = read(audit, latest)
    text = json.dumps(result)
    assert result["refusal"] is None and all(value not in text for value in secrets)
