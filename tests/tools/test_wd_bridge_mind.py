# SPDX-License-Identifier: BUSL-1.1
"""Dormant Mind: synthetic envelopes only, never a live bridge or gate action."""
import ast
from copy import deepcopy
import json
import os
from pathlib import Path

import pytest

from tools import wd_bridge_mind as mind
from tools.bridge_compact_view import event_id

AS_OF = "2026-10-13T04:00:00Z"
UUID = "7a8af68d-20bc-4598-9953-23c5dd98b102"
HEAD = "a" * 40
OTHER_HEAD = "b" * 40


def envelope(**changes):
    event = dict(agent="codex-tools-1", agent_uuid=UUID, session_id="session-1", run_id="run-1",
                 ts_utc="2026-10-13T03:59:00Z", type="message", status="head_updated", task_id="task/1",
                 payload=dict(pr=1900, head=HEAD))
    event.update(changes)
    return dict(ref=event_id(event), event=event)


def source(start, end, **changes):
    value = dict(log_identity="fixture-file-1", generation="fixture-generation", cursor_start=start,
                 cursor_end=end, event_count=end-start, coverage="complete")
    value.update(changes)
    return value


def build(rows, **policy):
    return mind.full_rebuild(rows, source=source(0, len(rows)), as_of_utc=AS_OF, **policy)


def nested_publication_fixture():
    """Synthetic complete core plus the established reflected field shapes."""
    rows = [envelope(type="claim", status="active", write_scope=["tools/a.py"]),
            envelope(type="decision", status="build_consensus_pass"),
            envelope(agent="claude-rco-1", type="finding", status="changes_requested")]
    value = mind.snapshot(build(rows), as_of_utc=AS_OF)
    ref = rows[0]["ref"]
    value["source"]["blocked"] = dict(active=True, offset=0, reason="partial_record")
    value["tensions"] = [dict(tension_id="d"*64, rule="claim_overlap", severity="high",
        subject="fixture", claim="fixture claim", observation="fixture observation",
        actors=["codex-tools-1"], refs=[ref], lifecycle="new")]
    value["attention"] = [dict(kind="tension_high", subject="fixture", actors=["codex-tools-1"],
        refs=[ref], detail="fixture detail", rank=1, suggestion_only=True)]
    question = dict(question="fixture question", attention_rank=1, refs=[ref])
    value["next_question"] = {"global": deepcopy(question), "lanes": {"codex-tools-1": deepcopy(question)}}
    return value


def _dict_paths(value, path=()):
    if isinstance(value, dict):
        yield path
        for key, child in value.items():
            yield from _dict_paths(child, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _dict_paths(child, (*path, index))


def _at_path(value, path):
    for key in path:
        value = value[key]
    return value


@pytest.mark.parametrize("api", ["write", "read"])
def test_every_nested_dict_rejects_extra_key_after_complete_public_positive(tmp_path, api):
    # MUT_NESTED_PUBLICATION_ALLOWLIST: real public boundary, every dict path.
    value = nested_publication_fixture()
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    target = audit / "good.json"
    mind.write_snapshot(value, target, audit_root=audit)
    assert mind.read_snapshot(target, audit_root=audit) == value
    original = target.read_bytes()
    accepted = []
    paths = list(_dict_paths(value))
    assert ("source",) in paths and ("source", "blocked") in paths
    assert ("next_question", "lanes", "codex-tools-1") in paths
    for path in paths:
        bad = deepcopy(value)
        _at_path(bad, path)["unexpected_fixture_key"] = "inert fixture value"
        if api == "read":
            altered = dict(schema="wd.bridge-mind-publication.v1", snapshot=bad,
                snapshot_sha256=mind.hashlib.sha256(mind.canonical_bytes(bad)).hexdigest())
            poisoned = audit / "poisoned.json"
            poisoned.write_bytes(mind.canonical_bytes(altered))
        try:
            if api == "write":
                mind.write_snapshot(bad, target, audit_root=audit)
            else:
                mind.read_snapshot(poisoned, audit_root=audit)
        except mind.ProjectionError:
            assert target.read_bytes() == original
        else:
            accepted.append(path)
            if api == "write":
                target.write_bytes(original)
    assert accepted == [], f"nested paths accepted: {accepted}"
    assert mind.read_snapshot(target, audit_root=audit) == value


@pytest.mark.parametrize("field", ["agent", "status"])
@pytest.mark.parametrize("collection", ["votes", "stale_votes", "veto_observations"])
@pytest.mark.parametrize("api", ["write", "read"])
def test_public_signal_tokens_cannot_collide_with_delimiter(tmp_path, field, collection, api):
    # MUT_SIGNAL_TOKEN_FORMAT: positive every signal family before corruption.
    value = nested_publication_fixture()
    pr = value["prs"]["1900"]
    vote = deepcopy(pr["votes"][0])
    if collection == "veto_observations":
        vote["activity"] = "UNKNOWN"
    pr[collection] = [vote]
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    target = audit / "good.json"
    mind.write_snapshot(value, target, audit_root=audit)
    assert mind.read_snapshot(target, audit_root=audit) == value
    original = target.read_bytes()
    bad = deepcopy(value)
    bad["prs"]["1900"][collection][0][field] = "agent:a" if field == "agent" else "a:hold"
    if api == "read":
        poisoned = audit / "poisoned.json"
        poisoned.write_bytes(mind.canonical_bytes(dict(schema="wd.bridge-mind-publication.v1", snapshot=bad,
            snapshot_sha256=mind.hashlib.sha256(mind.canonical_bytes(bad)).hexdigest())))
    with pytest.raises(mind.ProjectionError):
        if api == "write":
            mind.write_snapshot(bad, target, audit_root=audit)
        else:
            mind.read_snapshot(poisoned, audit_root=audit)
    assert target.read_bytes() == original


@pytest.mark.parametrize("status", ["", "hold:x", "changes requested", "HOLD", "UNKNOWN", ":hold", "a"*65])
def test_every_producer_veto_status_roundtrips_without_losing_block_or_source_ref(tmp_path, status):
    # MUT_RAW_PRODUCER_STATUS: real fold -> public write -> public read, no schema bypass.
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    control = envelope(agent="claude-rco-1", type="finding", status="info")
    control_value = mind.snapshot(build([control]), as_of_utc=AS_OF)
    control_path = audit / "control.json"
    mind.write_snapshot(control_value, control_path, audit_root=audit)
    assert mind.read_snapshot(control_path, audit_root=audit) == control_value
    original = control_path.read_bytes()
    passed = envelope(agent="claude-rco-1", type="decision", status="rco_pass")
    row = envelope(agent="claude-rco-1", type="finding", status=status, ts_utc="2026-10-13T03:59:01Z")
    value = mind.snapshot(build([passed, row]), as_of_utc=AS_OF)
    pr = value["prs"]["1900"]
    assert not pr["votes"] and pr["stale_votes"]  # Blocking observations are never cleared by normalization.
    assert pr["veto_observations"][0]["source_refs"] == [row["ref"]]
    assert pr["veto_observations"][0]["activity"] == "UNKNOWN"
    expected = status.lower() if status.lower() in {"hold", "unknown"} else "unknown_status"
    assert pr["veto_observations"][0]["status"] == expected
    path = audit / "produced.json"
    mind.write_snapshot(value, path, audit_root=audit)
    assert mind.read_snapshot(path, audit_root=audit) == value
    assert control_path.read_bytes() == original
    bad = deepcopy(value)
    bad["prs"]["1900"]["veto_observations"][0]["status"] = "hold:x"
    with pytest.raises(mind.ProjectionError):
        mind.write_snapshot(bad, control_path, audit_root=audit)
    assert control_path.read_bytes() == original  # Validator remains strict for caller-fed malformed status.


def test_exact_provenance_positive_then_missing_or_mixed_ref_refused():
    # MUT_FACT_WITHOUT_REF / MUT_MIND_SKIPS_REF_VALIDATION
    row = envelope()
    snap = mind.snapshot(build([row]), as_of_utc=AS_OF)
    assert snap["authority"] == "none" and snap["schema"] == "wd.bridge-mind.v1"
    assert snap["prs"]["1900"]["head"] == HEAD
    assert snap["tensions"] == snap["attention"] == []
    assert snap["next_question"] == {"lanes": {}, "global": {"question": "UNKNOWN", "attention_rank": None, "refs": ["UNKNOWN"]}}
    assert snap["facts"]
    for fact in snap["facts"]:
        assert fact["source_refs"] == [row["ref"]] and fact["canonical_sha256"] == row["ref"]
        assert fact["verification"] == "declared"
    for bad in ({"event": row["event"]}, dict(row, ref="f"*64)):
        with pytest.raises(mind.ProjectionError, match="provenance"):
            build([bad])


@pytest.mark.parametrize("field", ["message", "payload", "run_id"])
def test_decoded_marker_is_refused_without_echo_and_positive_is_usable(field):
    # MUT_SNAPSHOT_SKIPS_MARKER_GUARD: decoded input, including depth >12.
    assert mind.snapshot(build([envelope()]), as_of_utc=AS_OF)["facts"]
    marker = "PRIVATE" + "_MARKER"
    nested = marker
    for _ in range(15):
        nested = {"next": nested}
    value = json.loads(json.dumps(marker).replace("PRIVATE", "\\u0050RIVATE"))
    bad = envelope(**{field: {"nested": nested} if field == "payload" else value})
    with pytest.raises(mind.ProjectionError) as caught:
        build([bad])
    assert marker not in str(caught.value)
    assert str(caught.value) == "guarded_word_refused"


def test_allowlist_does_not_copy_messages_credentials_or_arbitrary_payloads():
    row = envelope(message="do not project this free text", payload=dict(pr=1900, head=HEAD,
                   auth_token="not-a-real-secret-fixture", arbitrary={"transcript": "omit-this"}))
    encoded = mind.canonical_bytes(mind.snapshot(build([row]), as_of_utc=AS_OF))
    for omitted in (b"do not project", b"auth_token", b"not-a-real-secret", b"omit-this", b"transcript"):
        assert omitted not in encoded


def test_old_head_vote_never_approves_new_head_and_current_positive_survives():
    # MUT_VOTE_NOT_HEAD_BOUND
    rows = [envelope(), envelope(type="decision", status="build_consensus_pass"),
            envelope(status="head_updated", payload=dict(pr=1900, head=OTHER_HEAD)),
            envelope(type="decision", status="rco_pass", payload=dict(pr=1900, head=HEAD))]
    old = mind.snapshot(build(rows), as_of_utc=AS_OF)["prs"]["1900"]
    assert old["head"] == OTHER_HEAD and old["votes"] == [] and old["stale_votes"]
    rows.append(envelope(type="decision", status="build_consensus_pass", payload=dict(pr=1900, head=OTHER_HEAD)))
    new = mind.snapshot(build(rows), as_of_utc=AS_OF)["prs"]["1900"]
    assert len(new["votes"]) == 1 and new["votes"][0]["head"] == OTHER_HEAD
    assert new["votes"][0]["verification"] == "declared"
    assert new["reported_ci"]["status"] == "UNKNOWN" and new["class"] == "UNKNOWN"


def test_reported_ci_veto_claim_and_identity_are_not_authenticated_or_cleared():
    rows = [envelope(type="claim", status="active", payload=dict(pr=1900, head=HEAD, required_checks_green=True,
             write_scope=["tools/a.py"], waiting_on=["peer/task"])),
            envelope(agent="claude-rco-1", type="decision", status="changes_requested"),
            envelope(agent="claude-rco-1", type="decision", status="rco_pass")]
    snap = mind.snapshot(build(rows), as_of_utc=AS_OF)
    pr = snap["prs"]["1900"]
    assert pr["reported_ci"]["status"] == "reported_green" and pr["reported_ci"]["verification"] == "declared"
    assert pr["veto_observations"] and pr["veto_observations"][0]["activity"] == "UNKNOWN"
    lane = snap["lanes"]["codex-tools-1|" + UUID]
    assert lane["claims"]["write_scope"] == ["tools/a.py"] and lane["waits"]["waiting_on"] == ["peer/task"]
    assert lane["verification"] == "declared" and lane["idle_status"] == "UNKNOWN"
    assert lane["observation_age_seconds"] == 60
    unverified = mind.snapshot(build([envelope(agent_uuid="unknown")]), as_of_utc=AS_OF)
    assert "codex-tools-1|" + UUID not in unverified["lanes"]
    assert next(iter(unverified["lanes"].values()))["verification"] == "unverified"


@pytest.mark.parametrize("cut", range(6))
def test_incremental_rebuild_byte_parity_with_duplicate_and_late_corrections(cut):
    # MUT_INCREMENTAL_SKIPS_DUPLICATE_DEDUP / MUT_LATE_EVENT_TIMESTAMP_WATERMARK
    first = envelope()
    rows = [first, envelope(type="decision", status="rco_pass"),
            envelope(status="head_updated", payload=dict(pr=1900, head=OTHER_HEAD)), deepcopy(first),
            envelope(ts_utc="2026-10-13T03:58:00Z", status="head_updated", payload=dict(pr=1900, head=HEAD))]
    full = build(rows, max_facts=1)
    prefix = mind.full_rebuild(rows[:cut], source=source(0, cut), as_of_utc=AS_OF, max_facts=1)
    incremental = mind.apply_incremental(prefix, rows[cut:], source=source(cut, len(rows)), as_of_utc=AS_OF)
    assert mind.canonical_bytes(mind.snapshot(full, as_of_utc=AS_OF)) == mind.canonical_bytes(mind.snapshot(incremental, as_of_utc=AS_OF))
    assert mind.snapshot(full, as_of_utc=AS_OF)["source"]["duplicate_count"] == 1
    assert mind.snapshot(full, as_of_utc=AS_OF)["prs"]["1900"]["head"] == HEAD
    assert len(mind.snapshot(full, as_of_utc=AS_OF)["facts"]) == 1  # eviction did not evict reducer state


@pytest.mark.parametrize("change", [dict(cursor_start=2, cursor_end=3), dict(log_identity="rotated"),
                         dict(generation="new-generation"), dict(coverage="partial"),
                         dict(cursor_end=None), dict(event_count=2)])
def test_cursor_loss_rotation_gap_and_partial_coverage_refused_without_reset(change):
    # MUT_MIND_ACCEPTS_CURSOR_GAP; exact continuation is the positive twin.
    prefix = build([envelope()])
    next_row = envelope(status="head_updated", payload=dict(pr=1900, head=OTHER_HEAD))
    good = mind.apply_incremental(prefix, [next_row], source=source(1, 2), as_of_utc=AS_OF)
    assert mind.snapshot(good, as_of_utc=AS_OF)["prs"]["1900"]["head"] == OTHER_HEAD
    with pytest.raises(mind.ProjectionError):
        mind.apply_incremental(prefix, [next_row], source=source(1, 2, **change), as_of_utc=AS_OF)
    assert mind.snapshot(prefix, as_of_utc=AS_OF)["prs"]["1900"]["head"] == HEAD


def test_ttl_boundary_and_serialization_have_no_hidden_wall_clock(monkeypatch):
    # MUT_TTL_USES_WALL_CLOCK / MUT_TTL_BOUNDARY_INCLUSIVE
    import time
    row = envelope(ts_utc="2026-10-13T03:55:00Z")
    state = mind.full_rebuild([row], source=source(0, 1), as_of_utc=AS_OF, ttl_seconds=300)
    monkeypatch.setattr(time, "time", lambda: (_ for _ in ()).throw(AssertionError("wall clock used")))
    assert mind.snapshot(state, as_of_utc="2026-10-13T03:59:59Z")["facts"]
    assert mind.snapshot(state, as_of_utc=AS_OF)["facts"] == []
    assert mind.snapshot(state, as_of_utc=AS_OF)["prs"]["1900"]["head"] == HEAD
    assert mind.canonical_bytes(mind.snapshot(state, as_of_utc=AS_OF)) == mind.canonical_bytes(mind.snapshot(state, as_of_utc=AS_OF))


def test_atomic_owned_audit_output_roundtrip_torn_input_and_failed_replace(tmp_path, monkeypatch):
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    path = audit / "mind.json"
    snap = mind.snapshot(build([envelope()]), as_of_utc=AS_OF)
    mind.write_snapshot(snap, path, audit_root=audit)
    assert mind.read_snapshot(path, audit_root=audit) == snap
    original = path.read_bytes()
    monkeypatch.setattr(mind.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("synthetic fail")))
    with pytest.raises(mind.ProjectionError):
        mind.write_snapshot(snap, path, audit_root=audit)
    assert path.read_bytes() == original
    with pytest.raises(mind.ProjectionError):
        mind.write_snapshot(snap, tmp_path / "outside.json", audit_root=audit)
    path.write_text("{", encoding="utf-8")
    with pytest.raises(mind.ProjectionError):
        mind.read_snapshot(path, audit_root=audit)


def test_snapshot_writer_refuses_fact_without_refs_and_no_gate_imports_mind(tmp_path):
    # MUT_FACT_WITHOUT_REF / MUT_GATE_READS_MIND
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    snap = mind.snapshot(build([envelope()]), as_of_utc=AS_OF)
    bad = deepcopy(snap)
    bad["facts"][0]["source_refs"] = []
    with pytest.raises(mind.ProjectionError):
        mind.write_snapshot(bad, audit / "bad.json", audit_root=audit)
    root = Path(__file__).resolve().parents[2]
    gates = ["check_bridge_changes_requested", "check_rco_pass_present", "idle_consensus_auto_merge",
             "bridge_rule12_review_eligibility", "rule12_grok_ledger_adapter",
             "write_bridge_consensus_merge_receipt", "check_proven_safe_autosign_class",
             "check_standing_consensus_sign_class"]
    for gate in gates:
        tree = ast.parse((root / "tools" / (gate + ".py")).read_text(encoding="utf-8-sig"))
        names = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        names += [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        assert not any("wd_bridge_mind" in name for name in names)


def test_snapshot_publication_rejects_unknown_projected_fields_and_tampered_cursor(tmp_path):
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    snap = mind.snapshot(build([envelope()]), as_of_utc=AS_OF)
    good = audit / "good.json"
    mind.write_snapshot(snap, good, audit_root=audit)
    for change in (lambda value: value["prs"]["1900"].update(auth_token="fixture-not-secret"),
                   lambda value: value["lanes"]["codex-tools-1|"+UUID].update(transcript="fixture-free-text"),
                   lambda value: value["prs"]["1900"].update(source_refs=[]),
                   lambda value: value["source"].update(unique_event_count=999)):
        bad = deepcopy(snap)
        change(bad)
        with pytest.raises(mind.ProjectionError):
            mind.write_snapshot(bad, audit / "bad.json", audit_root=audit)
    assert mind.read_snapshot(good, audit_root=audit) == snap


def test_as_of_cannot_precede_hidden_newer_event_after_late_update():
    rows = [envelope(), envelope(ts_utc="2026-10-13T03:58:00Z", payload=dict(pr=1900, head=OTHER_HEAD))]
    state = build(rows)
    assert mind.snapshot(state, as_of_utc=AS_OF)["prs"]["1900"]["head"] == OTHER_HEAD
    with pytest.raises(mind.ProjectionError, match="as_of_precedes_event"):
        mind.snapshot(state, as_of_utc="2026-10-13T03:58:30Z")


def test_current_head_class_and_preserved_claims_keep_their_actual_source_refs():
    first = envelope(type="claim", status="active", payload=dict(pr=1900, head=HEAD, **{"class": "a"}, write_scope=["tools/a.py"]))
    later = envelope(type="decision", status="build_consensus_pass", payload=dict(pr=1900, head=HEAD))
    snap = mind.snapshot(build([first, later]), as_of_utc=AS_OF)
    assert first["ref"] in snap["prs"]["1900"]["source_refs"]
    assert first["ref"] in snap["lanes"]["codex-tools-1|"+UUID]["source_refs"]


def test_gate_verdict_bytes_do_not_depend_on_present_absent_or_corrupt_snapshot(tmp_path, monkeypatch):
    from tools.check_bridge_changes_requested import check_bridge_clear_to_merge
    path = tmp_path / "mind.json"
    monkeypatch.setenv("WD_BRIDGE_MIND_SNAPSHOT", str(path))
    verdicts = []
    for contents in (None, "{}", "{corrupt"):
        if contents is not None:
            path.write_text(contents, encoding="utf-8")
        verdict = check_bridge_clear_to_merge(events=[], task_id="fixture/task", merging_agent="codex-lead-1")
        verdicts.append(json.dumps(verdict, sort_keys=True))
    assert verdicts[0] == verdicts[1] == verdicts[2]


def test_double_escaped_marker_refused_without_embedded_json_decode():
    # MUT_MIND_SKIPS_DOUBLE_ESCAPE_GUARD, positive ordinary embedded JSON is omitted.
    safe = envelope(message=json.dumps({"question": "ordinary-fixture"}))
    assert mind.snapshot(build([safe]), as_of_utc=AS_OF)["facts"]
    marker = "\\u0050RIVATE_MARKER"
    bad = envelope(message=json.dumps({"question": marker}))
    with pytest.raises(mind.ProjectionError) as caught:
        build([bad])
    assert "MARKER" not in str(caught.value) and "u0050" not in str(caught.value)


def test_blocked_view_keeps_good_cursor_and_state_and_never_echoes_bad_content():
    state = build([envelope()])
    original = mind.canonical_bytes(mind.snapshot(state, as_of_utc=AS_OF))
    blocked = mind.blocked_snapshot(state, as_of_utc=AS_OF, offset=1, reason="canonical_event_invalid")
    assert blocked["source"]["blocked"] == dict(active=True, offset=1, reason="canonical_event_invalid")
    assert blocked["source"]["cursor_end"] == 1 and blocked["prs"]["1900"]["head"] == HEAD
    assert mind.canonical_bytes(mind.snapshot(state, as_of_utc=AS_OF)) == original
    with pytest.raises(mind.ProjectionError):
        mind.blocked_snapshot(state, as_of_utc=AS_OF, offset=1, reason="arbitrary rejected row text")


def test_atomic_resumable_checkpoint_preserves_evicted_history_and_cursor(tmp_path, monkeypatch):
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    first = envelope()
    rows = [first, envelope(status="head_updated", payload=dict(pr=1900, head=OTHER_HEAD)), deepcopy(first)]
    prefix = build(rows[:2], max_facts=1)
    path = audit / "checkpoint.json"
    mind.write_checkpoint(prefix, path, as_of_utc=AS_OF, audit_root=audit)
    restored = mind.read_checkpoint(path, audit_root=audit)
    incremental = mind.apply_incremental(restored, rows[2:], source=source(2, 3), as_of_utc=AS_OF)
    full = build(rows, max_facts=1)
    assert mind.state_bytes(incremental) == mind.state_bytes(full)
    assert mind.canonical_bytes(mind.snapshot(incremental, as_of_utc=AS_OF)) == mind.canonical_bytes(mind.snapshot(full, as_of_utc=AS_OF))
    original = path.read_bytes()
    monkeypatch.setattr(mind.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("fixture fail")))
    with pytest.raises(mind.ProjectionError):
        mind.write_checkpoint(incremental, path, as_of_utc=AS_OF, audit_root=audit)
    assert path.read_bytes() == original and mind.read_checkpoint(path, audit_root=audit).source["cursor_end"] == 2


def test_checkpoint_with_reported_votes_restores_exact_state_bytes(tmp_path):
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    state = build([envelope(), envelope(type="decision", status="rco_pass")])
    path = audit / "votes.json"
    mind.write_checkpoint(state, path, as_of_utc=AS_OF, audit_root=audit)
    assert mind.state_bytes(mind.read_checkpoint(path, audit_root=audit)) == mind.state_bytes(state)


def test_audit_dotdot_and_hardlink_refused_without_touching_the_public_control(tmp_path):
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    safe = audit / "safe.json"
    snap = mind.snapshot(build([envelope()]), as_of_utc=AS_OF)
    mind.write_snapshot(snap, safe, audit_root=audit)
    original = safe.read_bytes()
    with pytest.raises(mind.ProjectionError):
        mind.write_snapshot(snap, audit / ".." / "outside.json", audit_root=audit)
    assert not (tmp_path / "outside.json").exists()
    linked = audit / "linked.json"
    mind.os.link(safe, linked)
    with pytest.raises(mind.ProjectionError):
        mind.read_snapshot(linked, audit_root=audit)
    assert safe.read_bytes() == original


@pytest.mark.parametrize("kind,status", [("decision", "superseded"), ("decision", "cancelled"),
    ("decision", "rco_pass_withdrawn"), ("rco_review", "needs_work"), ("finding", "info"),
    ("finding", "high"), ("finding", "changes_requested"), ("decision", "hold")])
def test_main_gate_block_implies_observation_and_old_pass_not_current(kind, status):
    # MUT_MIND_CLOSED_VETO_SET / MUT_MIND_KEEPS_SUPERSEDED_PASS
    from tools.check_bridge_changes_requested import check_bridge_clear_to_merge
    rco_uuid = "2b2f6ff9-06c2-4ec8-b526-f10071ce7103"
    passed = envelope(agent="claude-rco-1", agent_uuid=rco_uuid, type="decision", status="rco_pass")
    positive = mind.snapshot(build([passed]), as_of_utc=AS_OF)["prs"]["1900"]
    assert len(positive["votes"]) == 1 and positive["veto_observations"] == []
    signal = envelope(agent="claude-rco-1", agent_uuid=rco_uuid, type=kind, status=status,
                      ts_utc="2026-10-13T03:59:01Z")
    rows = [passed, signal]
    gate = check_bridge_clear_to_merge(events=[row["event"] for row in rows], task_id="task/1",
                  merging_agent="codex-lead-1", identity_registry={"claude-rco-1": rco_uuid}, pr_number=1900)
    assert gate["clear_to_merge"] is False
    observed = mind.snapshot(build(rows), as_of_utc=AS_OF)["prs"]["1900"]
    assert observed["veto_observations"] and observed["votes"] == []
    assert observed["stale_votes"]  # retain the earlier pass as evidence, not as current


def test_plain_empty_scope_message_does_not_retire_claim_and_release_is_explicit():
    # MUT_MIND_MESSAGE_WIPES_CLAIM
    claimed = envelope(type="claim", status="active", write_scope=["tools/a.py"])
    ordinary = envelope(type="message", status="info", write_scope=[], ts_utc="2026-10-13T03:59:01Z")
    rows = [claimed, ordinary]
    lane = mind.snapshot(build(rows), as_of_utc=AS_OF)["lanes"]["codex-tools-1|"+UUID]
    assert lane["claims"]["write_scope"] == ["tools/a.py"]
    assert lane["claims"]["lease_expires_utc"] == lane["claims"]["expired"] == "UNKNOWN"
    released = envelope(type="release", status="abandoned", write_scope=[], ts_utc="2026-10-13T03:59:02Z")
    lane = mind.snapshot(build([*rows, released]), as_of_utc=AS_OF)["lanes"]["codex-tools-1|"+UUID]
    assert lane["claims"]["write_scope"] == []


def test_sensitive_refusal_can_be_published_as_loud_block(tmp_path):
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    state = build([envelope()])
    original = mind.canonical_bytes(mind.snapshot(state, as_of_utc=AS_OF))
    bad = envelope(message="PRIVATE"+"_MARKER")
    with pytest.raises(mind.ProjectionError) as caught:
        build([bad])
    blocked = mind.blocked_snapshot(state, as_of_utc=AS_OF, offset=1, reason=str(caught.value))
    path = audit / "blocked.json"
    mind.write_snapshot(blocked, path, audit_root=audit)
    assert mind.read_snapshot(path, audit_root=audit)["source"]["blocked"]["active"] is True
    assert mind.canonical_bytes(mind.snapshot(state, as_of_utc=AS_OF)) == original


def test_multiple_claim_tasks_survive_messages_and_only_named_release_is_retired():
    # MUT_MIND_LAST_CLAIM_ONLY / MUT_MIND_RELEASE_ALL_CLAIMS
    a = envelope(type="claim", status="active", task_id="task/a", write_scope=["tools/a.py"])
    b = envelope(type="claim", status="active", task_id="task/b", write_scope=["tools/b.py"])
    message = envelope(type="message", status="info", task_id="other/context", write_scope=[])
    ready = envelope(type="handoff", status="ready", task_id="task/a", write_scope=[])
    rows = [a, b, message, ready]
    lane = mind.snapshot(build(rows), as_of_utc=AS_OF)["lanes"]["codex-tools-1|"+UUID]
    assert lane["claims"]["write_scope"] == ["tools/a.py", "tools/b.py"]
    assert {claim["task_id"] for claim in lane["claims"]["by_task"]} == {"task/a", "task/b"}
    released = envelope(type="release", status="abandoned", task_id="task/a", write_scope=[])
    lane = mind.snapshot(build([*rows, released]), as_of_utc=AS_OF)["lanes"]["codex-tools-1|"+UUID]
    assert lane["claims"]["write_scope"] == ["tools/b.py"]
    assert [claim["task_id"] for claim in lane["claims"]["by_task"]] == ["task/b"]
    assert lane["claims"]["lease_expires_utc"] == lane["claims"]["expired"] == "UNKNOWN"


def test_old_review_request_cannot_rewind_head_and_unknown_actor_is_unverified():
    rows = [envelope(), envelope(payload=dict(pr=1900, head=OTHER_HEAD)),
            envelope(status="review_requested", payload=dict(pr=1900, head=HEAD))]
    assert mind.snapshot(build(rows), as_of_utc=AS_OF)["prs"]["1900"]["head"] == OTHER_HEAD
    for status in ("rco_pass", "hold"):
        row = envelope(agent="claude-rco-1", agent_uuid="invalid", type="decision", status=status)
        pr = mind.snapshot(build([row]), as_of_utc=AS_OF)["prs"]["1900"]
        values = pr["votes"] + pr["veto_observations"]
        assert values and all(value["verification"] == "unverified" for value in values)


def test_all_supported_blocked_reasons_publish_and_pass_positive_neutral_and_closeout_twins(tmp_path):
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    state = build([envelope()])
    for reason in mind.BLOCKED_REASONS:
        blocked = mind.blocked_snapshot(state, as_of_utc=AS_OF, offset=1, reason=reason)
        path = audit / (reason+".json")
        mind.write_snapshot(blocked, path, audit_root=audit)
        assert mind.read_snapshot(path, audit_root=audit)["source"]["blocked"]["reason"] == reason
    passed = envelope(agent="claude-rco-1", type="decision", status="rco_pass")
    neutral = envelope(agent="claude-rco-1", type="decision", status="rco_closed_postmerge")
    pr = mind.snapshot(build([passed, neutral]), as_of_utc=AS_OF)["prs"]["1900"]
    assert pr["votes"] and not pr["veto_observations"]
    held = envelope(agent="claude-rco-1", type="decision", status="hold")
    closeout = envelope(type="decision", status="cancelled")
    pr = mind.snapshot(build([passed, held, closeout]), as_of_utc=AS_OF)["prs"]["1900"]
    assert pr["veto_observations"] and not pr["votes"]
    new_pass = envelope(agent="claude-rco-1", type="decision", status="rco_pass", ts_utc="2026-10-13T03:59:02Z")
    pr = mind.snapshot(build([passed, held, new_pass]), as_of_utc=AS_OF)["prs"]["1900"]
    assert len(pr["votes"]) == 1 and pr["votes"][0]["source_refs"] == [new_pass["ref"]]
    assert pr["veto_observations"][0]["activity"] == "UNKNOWN"  # no clearing verdict


def test_canonical_block_type_parity_matrix_keeps_non_rco_peer_blocks_visible():
    # MUT_MIND_NON_RCO_BLOCKED_IGNORED: actual canonical checker, no alternate gate.
    from tools.check_bridge_changes_requested import check_bridge_clear_to_merge
    passed = envelope(agent="claude-rco-1", type="decision", status="rco_pass")
    positive = mind.snapshot(build([passed]), as_of_utc=AS_OF)["prs"]["1900"]
    assert positive["votes"] and not positive["veto_observations"]
    under_reports = []
    cases = 0
    for actor in ("claude-rco-1", "codex-tools-1", "codex-lead-1"):
        for kind in ("decision", "finding", "rco_review", "message", "done", "blocked"):
            for status in ("superseded", "cancelled", "info", "needs_work", "changes_requested", "hold",
                           "blocked", "rejected", "veto", "withdrawn", "rco_pass_withdrawn", "approved"):
                signal = envelope(agent=actor, type=kind, status=status, ts_utc="2026-10-13T03:59:01Z")
                rows = [passed, signal]
                gate = check_bridge_clear_to_merge(events=[row["event"] for row in rows], task_id="task/1",
                                                  merging_agent="codex-lead-1", pr_number=1900,
                                                  # Same actor bindings as the original owned test config;
                                                  # do not depend on gitignored host registry provisioning.
                                                  identity_registry={"claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
                                                      "codex-tools-1": UUID,
                                                      "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101"})
                pr = mind.snapshot(build(rows), as_of_utc=AS_OF)["prs"]["1900"]
                if not gate["clear_to_merge"] and not pr["veto_observations"]:
                    under_reports.append((actor, kind, status))
                if actor == "codex-tools-1" and kind == "blocked":
                    observations = pr["veto_observations"]
                    if not observations or observations[0]["source_refs"] != [signal["ref"]]:
                        under_reports.append((actor, kind, status, "lost_ref"))
                    assert all(item["activity"] == "UNKNOWN" for item in observations)
                    assert pr["votes"]  # Another actor's block is observed, not a forged pass retraction.
                cases += 1
    assert cases == 216
    assert under_reports == []


@pytest.mark.parametrize('api', ['write_snapshot', 'write_checkpoint'])
def test_public_writers_refuse_double_cleanup_failure_after_positive(api, tmp_path, monkeypatch):
    # MUT_RAW_CLEANUP_ERROR: replace and cleanup both fail, without ACL changes.
    audit = tmp_path / '.codex-audit'
    audit.mkdir()
    path = audit / 'owned-cleanup-publication.json'
    state = build([envelope()])
    snap = mind.snapshot(state, as_of_utc=AS_OF)

    def publish():
        if api == 'write_snapshot':
            return mind.write_snapshot(snap, path, audit_root=audit)
        return mind.write_checkpoint(state, path, as_of_utc=AS_OF, audit_root=audit)

    publish()
    if api == 'write_snapshot':
        assert mind.read_snapshot(path, audit_root=audit) == snap
    else:
        assert mind.state_bytes(mind.read_checkpoint(path, audit_root=audit)) == mind.state_bytes(state)
    original = path.read_bytes()
    attempted = []

    def denied_replace(temporary, target):
        assert Path(target) == path and Path(temporary).parent == audit
        attempted.append('replace')
        raise PermissionError(13, 'synthetic atomic replacement denial', str(path))

    def denied_cleanup(temporary):
        assert Path(temporary).parent == audit
        attempted.append('cleanup')
        raise PermissionError(13, 'synthetic temporary cleanup denial', str(temporary))

    with monkeypatch.context() as faults:
        faults.setattr(mind.os, 'replace', denied_replace)
        faults.setattr(mind.os, 'unlink', denied_cleanup)
        faults.setattr(mind, '_sleep', lambda seconds: None)
        with pytest.raises(mind.ProjectionError) as caught:
            publish()
    # A denied replace is retried within the sharing-window bound, then the cleanup failure stays a failure.
    assert attempted == ['replace'] * mind._SHARING_TRIES + ['cleanup']
    assert str(caught.value) == 'snapshot_cleanup_failed'
    assert caught.value.__cause__ is None and caught.value.__suppress_context__ is True
    assert getattr(caught.value, 'filename', None) is None
    assert path.name not in str(caught.value) and 'synthetic' not in str(caught.value)
    assert path.read_bytes() == original
    # Failed cleanup stays a failure; retained temporary evidence is not success.
    assert len(list(audit.glob('.mind-*.tmp'))) == 1


@pytest.mark.parametrize('api', ['write_snapshot','read_snapshot','write_checkpoint','read_checkpoint'])
def test_all_public_audit_apis_refuse_stat_oserror_without_echo_after_positive(api, tmp_path, monkeypatch):
    # MUT_RAW_AUDIT_OSERROR: actual public API, isolated synthetic denial, no ACL mutation.
    audit = tmp_path / '.codex-audit'
    audit.mkdir()
    path = audit / 'public-caller-fixture.json'
    state = build([envelope()])
    snap = mind.snapshot(state, as_of_utc=AS_OF)
    checkpoint = 'checkpoint' in api
    if checkpoint:
        mind.write_checkpoint(state,path,as_of_utc=AS_OF,audit_root=audit)
        assert mind.state_bytes(mind.read_checkpoint(path,audit_root=audit))==mind.state_bytes(state)
    else:
        mind.write_snapshot(snap,path,audit_root=audit)
        assert mind.read_snapshot(path,audit_root=audit)==snap
    original=path.read_bytes()
    real=Path.lstat
    def denied(value,*args,**kwargs):
        if value==path:
            raise PermissionError(13,'synthetic caller text',str(path))
        return real(value,*args,**kwargs)
    monkeypatch.setattr(Path,'lstat',denied)
    with pytest.raises(mind.ProjectionError) as caught:
        if api=='write_snapshot':mind.write_snapshot(snap,path,audit_root=audit)
        elif api=='read_snapshot':mind.read_snapshot(path,audit_root=audit)
        elif api=='write_checkpoint':mind.write_checkpoint(state,path,as_of_utc=AS_OF,audit_root=audit)
        else:mind.read_checkpoint(path,audit_root=audit)
    assert str(caught.value)=='audit_path_refused'
    assert path.name not in str(caught.value) and 'synthetic caller text' not in str(caught.value)
    assert path.read_bytes()==original


def test_case_variants_keep_canonical_blocks_pass_identity_and_claim_lifecycle():
    # MUT_RAW_CASE_KIND: lowercase control FIRST, original complete event refs retained.
    from tools.check_bridge_changes_requested import check_bridge_clear_to_merge
    rco_uuid='2b2f6ff9-06c2-4ec8-b526-f10071ce7103'
    passed=envelope(agent='claude-rco-1',agent_uuid=rco_uuid,type='decision',status='rco_pass')
    misses=[]
    cases=0
    for kind in ('Decision','DECISION','Finding','FINDING','Blocked','BLOCKED','RCO_REVIEW'):
        for status in ('superseded','cancelled','rco_pass_withdrawn','info','needs_work','changes_requested','hold','approved'):
            lower=envelope(agent='claude-rco-1',agent_uuid=rco_uuid,type=kind.lower(),status=status,ts_utc='2026-10-13T03:59:01Z')
            mixed=envelope(agent='claude-rco-1',agent_uuid=rco_uuid,type=kind,status=status,ts_utc='2026-10-13T03:59:01Z')
            gate=check_bridge_clear_to_merge(events=[passed['event'],lower['event']],task_id='task/1',merging_agent='codex-lead-1',pr_number=1900,identity_registry={'claude-rco-1':rco_uuid})
            positive=mind.snapshot(build([passed,lower]),as_of_utc=AS_OF)['prs']['1900']
            observed=mind.snapshot(build([passed,mixed]),as_of_utc=AS_OF)['prs']['1900']
            if not gate['clear_to_merge']:
                assert positive['veto_observations'] and not positive['votes']
                if not observed['veto_observations'] or observed['votes']:
                    misses.append((kind,status))
                else:
                    assert observed['veto_observations'][0]['source_refs']==[mixed['ref']]
                    assert observed['veto_observations'][0]['activity']=='UNKNOWN'
                    assert observed['stale_votes'][0]['source_refs']==[passed['ref']]
            cases+=1
    assert cases==56 and misses==[]
    for kind in ('Decision','DECISION'):
        fresh=envelope(agent='claude-rco-1',agent_uuid=rco_uuid,type=kind,status='RCO_PASS')
        pr=mind.snapshot(build([fresh]),as_of_utc=AS_OF)['prs']['1900']
        assert len(pr['votes'])==1 and not pr['veto_observations']
        assert pr['votes'][0]['agent_uuid']==rco_uuid and pr['votes'][0]['source_refs']==[fresh['ref']]
    a=envelope(type='Claim',task_id='task/a',write_scope=['tools/a.py'])
    b=envelope(type='CLAIM',task_id='task/b',write_scope=['tools/b.py'])
    msg=envelope(type='Message',task_id='task/a',write_scope=[])
    release=envelope(type='Release',task_id='task/a',write_scope=[])
    snap=mind.snapshot(build([a,b,msg,release]),as_of_utc=AS_OF)
    claims=snap['lanes']['codex-tools-1|'+UUID]['claims']
    assert [r['task_id'] for r in claims['by_task']]==['task/b']
    assert claims['lease_expires_utc']==claims['expired']=='UNKNOWN'


@pytest.mark.parametrize("agent, kind, status, recused, kept", [
    ("claude-rco-2", "message", "rco_recused", True, True),
    ("claude-rco-2", "decision", "rco_recused", None, True),      # no recused field: the exact status suffices
    ("claude-rco-2", "message", "rco_recused", False, False),     # recused false is no recusal
    ("codex-tools-1", "message", "rco_recused", True, False),     # only a recognized RCO recuses
    ("claude-rco-2", "message", "recused", True, False),          # only the exact status token
    ("claude-rco-2", "rco_review", "rco_recused", True, False),   # not a message or decision
])
def test_rco_recusal_is_recorded_head_bound_never_a_vote_and_survives_checkpoints(agent, kind, status, recused,
                                                                                  kept, tmp_path):
    """Sandbox round 2 (fable-5): PR1878 still asked claude-rco-2 after its rco_recused at that exact head."""
    payload = dict(pr=1900, head=HEAD)
    if recused is not None:
        payload["recused"] = recused
    rows = [envelope(), envelope(agent=agent, type=kind, status=status, payload=payload)]
    state = build(rows)
    view = mind.snapshot(state, as_of_utc=AS_OF)
    pr = view["prs"]["1900"]
    assert pr["votes"] == [] and pr["stale_votes"] == []
    if kept:
        assert [(row["agent"], row["head"], row["status"], row["verification"], row["source_refs"])
                for row in pr["recusals"]] == [(agent, HEAD, "rco_recused", "declared", [rows[1]["ref"]])]
    else:
        assert "recusals" not in pr
    incremental = mind.apply_incremental(mind.full_rebuild(rows[:1], source=source(0, 1), as_of_utc=AS_OF),
                                         rows[1:], source=source(1, 2), as_of_utc=AS_OF)
    assert mind.state_bytes(incremental) == mind.state_bytes(state)
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    path = audit / "recusal.json"
    mind.write_checkpoint(state, path, as_of_utc=AS_OF, audit_root=audit)
    assert mind.state_bytes(mind.read_checkpoint(path, audit_root=audit)) == mind.state_bytes(state)
    if kept:
        for change in ({"agent": "codex-tools-1"}, {"status": "rco_pass"}, {"head": "UNKNOWN"}, None):
            bad = deepcopy(view)
            if change is None:
                bad["prs"]["1900"]["recusals"] = []
            else:
                bad["prs"]["1900"]["recusals"][0].update(change)
            with pytest.raises(mind.ProjectionError):
                mind._validate_snapshot(bad)


@pytest.mark.parametrize("failures, error, published", [
    (0, PermissionError, True),
    (2, PermissionError, True),                 # a short Windows sharing window: retried, then published
    (11, PermissionError, True),                # the last allowed try still publishes
    (12, PermissionError, False),               # bounded: never more than _SHARING_TRIES attempts
    (1, FileNotFoundError, False),              # any other error is never retried
])
def test_publication_replace_retries_only_the_sharing_window_and_is_bounded(failures, error, published, tmp_path,
                                                                            monkeypatch):
    """Sandbox 10:30Z: 1 writer + 5 readers on Windows failed 15 of 313 publishes without a retry (RCO1/RCO2)."""
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    path = audit / "shared.json"
    snap = mind.snapshot(build([envelope()]), as_of_utc=AS_OF)
    real_replace, calls, delays = os.replace, [], []

    def flaky_replace(temporary, target):
        calls.append(target)
        if len(calls) <= failures:
            raise error(32, "synthetic sharing violation", str(target))
        return real_replace(temporary, target)

    with monkeypatch.context() as faults:
        faults.setattr(mind.os, "replace", flaky_replace)
        faults.setattr(mind, "_sleep", delays.append)
        if published:
            mind.write_snapshot(snap, path, audit_root=audit)
        else:
            with pytest.raises(mind.ProjectionError) as caught:
                mind.write_snapshot(snap, path, audit_root=audit)
            assert str(caught.value) == "snapshot_publication_failed" and "synthetic" not in str(caught.value)
    expected = min(failures + 1, mind._SHARING_TRIES) if error is PermissionError else 1
    assert len(calls) == expected and len(delays) == expected - 1
    assert all(delay <= mind._SHARING_MAX_DELAY for delay in delays) and sum(delays) < 1.0
    assert list(audit.glob(".mind-*.tmp")) == []
    if published:
        assert mind.read_snapshot(path, audit_root=audit) == snap
    else:
        assert not path.exists()


@pytest.mark.parametrize("failures, readable", [(0, True), (3, True), (12, False)])
def test_publication_read_retries_only_the_sharing_window(failures, readable, tmp_path, monkeypatch):
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    path = audit / "shared.json"
    snap = mind.snapshot(build([envelope()]), as_of_utc=AS_OF)
    mind.write_snapshot(snap, path, audit_root=audit)
    real_read, calls = Path.read_bytes, []

    def flaky_read(self):
        calls.append(self)
        if len(calls) <= failures:
            raise PermissionError(32, "synthetic sharing violation", str(self))
        return real_read(self)

    with monkeypatch.context() as faults:
        faults.setattr(mind.Path, "read_bytes", flaky_read)
        faults.setattr(mind, "_sleep", lambda seconds: None)
        if readable:
            assert mind.read_snapshot(path, audit_root=audit) == snap
        else:
            with pytest.raises(mind.ProjectionError) as caught:
                mind.read_snapshot(path, audit_root=audit)
            assert str(caught.value) == "snapshot_read_refused"
    assert len(calls) == min(failures + 1, mind._SHARING_TRIES)


@pytest.mark.parametrize("verification", ["trusted", "", None, "UNKNOWN"])
def test_a_recusal_row_with_any_other_verification_is_refused(verification):
    """Release reconciliation: a recusal row keeps the vote rows' declared/unverified vocabulary, nothing else."""
    rows = [envelope(), envelope(agent="claude-rco-2", type="message", status="rco_recused",
                                 payload=dict(pr=1900, head=HEAD))]
    view = mind.snapshot(build(rows), as_of_utc=AS_OF)
    mind._validate_snapshot(view)
    bad = deepcopy(view)
    bad["prs"]["1900"]["recusals"][0]["verification"] = verification
    with pytest.raises(mind.ProjectionError):
        mind._validate_snapshot(bad)
