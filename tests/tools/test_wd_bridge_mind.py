# SPDX-License-Identifier: BUSL-1.1
"""Dormant Mind: synthetic envelopes only, never a live bridge or gate action."""
import ast
from copy import deepcopy
import json
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
    assert str(caught.value) == "private_marker_refused"


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
    rows = [envelope(status="active", payload=dict(pr=1900, head=HEAD, required_checks_green=True,
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
    first = envelope(payload=dict(pr=1900, head=HEAD, **{"class": "a"}, write_scope=["tools/a.py"]))
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
