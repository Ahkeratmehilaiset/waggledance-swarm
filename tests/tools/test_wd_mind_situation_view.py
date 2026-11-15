"""Synthetic-only situation projection tests; no live bridge reads."""
import hashlib
import json

import pytest

from tools import bridge_compact_view
from tools import wd_mind_situation_view as view


def raw(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def pin(data):
    return hashlib.sha256(data).hexdigest()


UUIDS = {lane: f"{i:08x}-1111-4111-8111-111111111111" for i, lane in enumerate(view.LANES, 1)}


def event(lane, kind, time="2026-11-14T05:29:00Z", task="task-123", **extra):
    row = {"agent": lane, "agent_uuid": UUIDS[lane], "type": kind, "ts_utc": time,
           "task_id": task, "message": "sensitive synthetic text must not appear"}
    row.update(extra)
    return {"ref": bridge_compact_view.event_id(row), "event": row}


def inputs(events):
    fleet = raw({"lanes": [{"agent": lane, "agent_uuid": UUIDS[lane]}
                           for lane in view.LANES if lane != "codex-tools-1"],
                 "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": UUIDS["codex-tools-1"]}})
    manifest = raw({"schema_version": 1, "source_commit": "a" * 40,
                    "files": {"wd-fleet.json": pin(fleet).upper()}})
    manifest_sha = pin(manifest)
    commit = "a" * 40
    pointer = raw({"schema_version": 1, "source_commit": commit, "final_commit": commit,
                   "active_bundle": "C:\\Python\\wd-reboot-bundles\\" + commit,
                   "manifest_sha256": manifest_sha.upper(), "final_manifest_sha256": manifest_sha.upper()})
    snapshot = raw(events)
    return {"snapshot_bytes": snapshot, "snapshot_sha256": pin(snapshot),
            "fleet_bytes": fleet, "fleet_sha256": pin(fleet),
            "pointer_bytes": pointer, "pointer_sha256": pin(pointer),
            "manifest_bytes": manifest, "manifest_sha256": manifest_sha,
            "pointer_observed_utc": "2026-11-14T05:29:30Z", "as_of_utc": "2026-11-14T05:30:00Z"}


def test_five_lanes_bundle_and_metadata_only():
    events = [event(lane, "claim", task="task-" + lane) for lane in view.LANES]
    events += [event("codex-lead-1", "handoff", time="2026-11-14T05:29:20Z"),
               event("codex-lead-1", "done", time="2026-11-14T05:29:30Z")]
    result = view.project_situation(**inputs(events))
    assert result["authority"] == "none" and result["scope"] == "bounded_tail_not_current_state_or_gate"
    assert result["bundle"]["status"] == "OBSERVED" and result["bundle"]["source_commit"] == "a" * 40
    assert result["bundle"]["source_kind"] == "local_pointer_and_manifest_bytes"
    assert "event_ref" not in result["bundle"] and result["bundle"]["pointer_authentication"] == "UNKNOWN"
    for lane in view.LANES:
        assert result["lanes"][lane]["latest_claim_event"]["event_ref"] == events[view.LANES.index(lane)]["ref"]
        assert result["lanes"][lane]["current_task"] == "UNKNOWN"
    assert result["lanes"]["codex-lead-1"]["latest_work_event"]["event_type"] == "done"
    assert result["lanes"]["codex-lead-1"]["latest_handoff_event"]["event_type"] == "handoff"
    assert "sensitive synthetic" not in json.dumps(result)


def test_stale_event_and_stale_pointer_are_unknown_with_evidence():
    row = event("codex-lead-1", "claim", time="2026-11-14T05:15:00Z", task="old-task")
    args = inputs([row])
    args["pointer_observed_utc"] = "2026-11-14T05:15:00Z"
    result = view.project_situation(**args)
    obs = result["lanes"]["codex-lead-1"]["latest_claim_event"]
    assert obs["status"] == obs["task_id"] == obs["event_type"] == "UNKNOWN"
    assert obs["event_ref"] == row["ref"] and obs["age_seconds_upper_bound"] == 900
    assert result["bundle"]["status"] == result["bundle"]["source_commit"] == "UNKNOWN"


def test_newer_release_is_observation_not_active_claim_inference():
    rows = [event("codex-lead-1", "claim", time="2026-11-14T05:29:00Z"),
            event("codex-lead-1", "release", time="2026-11-14T05:29:30Z")]
    lane = view.project_situation(**inputs(rows))["lanes"]["codex-lead-1"]
    assert lane["latest_claim_event"]["event_type"] == "release"
    assert lane["latest_claim_event"]["event_ref"] == rows[1]["ref"]
    assert lane["current_task"] == "UNKNOWN"


def test_request_observed_but_open_and_approval_never_inferred_from_tail():
    request = event("codex-lead-1", "message", request_id="request-1", to="claude-rco-1",
                    status="review_requested", payload={"pr": 2134, "head": "b" * 40})
    result = view.project_situation(**inputs([request]))
    obs = result["request_observations"][0]
    assert obs["event_ref"] == request["ref"] and obs["recipients"] == ["claude-rco-1"]
    assert obs["resolution"] == obs["approval"] == obs["binding_valid"] == "UNKNOWN"
    assert obs["requested_review"] is True and obs["pr"] == 2134 and obs["head"] == "b" * 40


def test_generic_request_does_not_prove_review_absent():
    request = event("codex-lead-1", "request", request_id="request-generic", to="claude-rco-1",
                    status="request", payload={"pr": 2134, "head": "b" * 40})
    obs = view.project_situation(**inputs([request]))["request_observations"][0]
    assert obs["requested_review"] == "UNKNOWN"


def test_well_formed_task_id_can_still_contain_personal_data():
    task = "codex-lead-1/customer-jane-doe-0401234567-20261114"
    result = view.project_situation(**inputs([event("codex-lead-1", "claim", task=task)]))
    assert task not in json.dumps(result)
    assert result["lanes"]["codex-lead-1"]["latest_claim_event"]["task_id"] == "sha256:" + pin(task.encode())


def test_conflicting_heads_remain_unknown_and_multi_recipient_request_is_preserved():
    request = event("codex-lead-1", "request", request_id="request-2",
                    to="claude-rco-1, claude-rco-2", payload={"head": "b" * 40, "exact_head": "c" * 40})
    obs = view.project_situation(**inputs([request]))["request_observations"][0]
    assert obs["head"] == "UNKNOWN" and obs["recipients"] == ["claude-rco-1", "claude-rco-2"]


def test_wrong_uuid_and_unrelated_events_do_not_invent_lane_state():
    row = event("codex-lead-1", "claim", agent_uuid=UUIDS["fable-5"])
    assert view.project_situation(**inputs([row]))["lanes"]["codex-lead-1"]["latest_claim_event"] == "UNKNOWN"


@pytest.mark.parametrize("key", ["snapshot_sha256", "fleet_sha256", "pointer_sha256", "manifest_sha256"])
def test_bad_pin_refused(key):
    args = inputs([])
    args[key] = "0" * 64
    with pytest.raises(view.SituationRefused, match="^mind_situation_refused$"):
        view.project_situation(**args)


def test_wrong_event_reference_refused_even_if_snapshot_pin_matches():
    row = event("codex-lead-1", "claim")
    row["event"]["task_id"] = "changed"
    with pytest.raises(view.SituationRefused): view.project_situation(**inputs([row]))


def test_wrong_pointer_manifest_pair_refused():
    args = inputs([])
    pointer = json.loads(args["pointer_bytes"])
    pointer["final_manifest_sha256"] = "0" * 64
    args["pointer_bytes"] = raw(pointer)
    args["pointer_sha256"] = pin(args["pointer_bytes"])
    with pytest.raises(view.SituationRefused): view.project_situation(**args)


@pytest.mark.parametrize("manifest_commit", [None, "b" * 40, "not-a-commit", True])
def test_manifest_commit_must_match_pointer_even_with_valid_hashes(manifest_commit):
    args = inputs([])
    manifest = json.loads(args["manifest_bytes"])
    if manifest_commit is None:
        manifest.pop("source_commit")
    else:
        manifest["source_commit"] = manifest_commit
    args["manifest_bytes"] = raw(manifest)
    args["manifest_sha256"] = pin(args["manifest_bytes"])
    pointer = json.loads(args["pointer_bytes"])
    pointer["manifest_sha256"] = pointer["final_manifest_sha256"] = args["manifest_sha256"]
    args["pointer_bytes"] = raw(pointer)
    args["pointer_sha256"] = pin(args["pointer_bytes"])
    with pytest.raises(view.SituationRefused, match="^mind_situation_refused$"):
        view.project_situation(**args)


def test_pointer_cannot_name_another_bundle():
    args = inputs([])
    pointer = json.loads(args["pointer_bytes"])
    pointer["active_bundle"] = "C:\\Python\\wd-reboot-bundles\\" + "b" * 40
    args["pointer_bytes"] = raw(pointer)
    args["pointer_sha256"] = pin(args["pointer_bytes"])
    with pytest.raises(view.SituationRefused): view.project_situation(**args)


def test_future_event_and_conflicting_request_not_accepted():
    future = event("codex-lead-1", "claim", time="2026-11-14T05:30:00.0000001Z")
    with pytest.raises(view.SituationRefused): view.project_situation(**inputs([future]))
    request = event("codex-lead-1", "message", request_id="one", payload={"request_id": "other"}, to="fable-5")
    assert view.project_situation(**inputs([request]))["request_observations"] == []


def test_explicit_duplicate_json_key_is_refused():
    args = inputs([])
    args["pointer_bytes"] = b'{"schema_version":1,"schema_version":1}'
    args["pointer_sha256"] = pin(args["pointer_bytes"])
    with pytest.raises(view.SituationRefused): view.project_situation(**args)


def test_fleet_must_be_pinned_by_active_bundle_manifest():
    args = inputs([])
    for fleet_entry in (None, "0" * 64, True):
        altered = dict(args)
        data = {"schema_version": 1, "source_commit": "a" * 40, "files": {}}
        if fleet_entry is not None:
            data["files"]["wd-fleet.json"] = fleet_entry
        altered["manifest_bytes"] = raw(data)
        altered["manifest_sha256"] = pin(altered["manifest_bytes"])
        pointer = json.loads(altered["pointer_bytes"])
        pointer["manifest_sha256"] = pointer["final_manifest_sha256"] = altered["manifest_sha256"]
        altered["pointer_bytes"] = raw(pointer)
        altered["pointer_sha256"] = pin(altered["pointer_bytes"])
        with pytest.raises(view.SituationRefused): view.project_situation(**altered)


@pytest.mark.parametrize("value", ["C:/private/customer-secret.txt", "sk-synthetic-secret-value-1234",
    "codex-lead-1/abcdef0123456789abcdef0123456789-20261114",
    "codex-lead-1/work-abcdef0123456789-20261114"])
def test_path_or_synthetic_secret_never_returned_in_identifiers(value):
    rows = [event("codex-lead-1", "claim", task=value),
            event("codex-lead-1", "request", request_id=value, to="fable-5")]
    result = view.project_situation(**inputs(rows))
    assert value not in json.dumps(result)
    assert result["lanes"]["codex-lead-1"]["latest_claim_event"]["task_id"] == "sha256:" + pin(value.encode())
    assert result["request_observations"][0]["request_id"] == "sha256:" + pin(value.encode())


def test_recognized_lane_task_identifier_remains_correlatable():
    task = "codex-lead-1/mind-direct-integration-20261112"
    result = view.project_situation(**inputs([event("codex-lead-1", "claim", task=task)]))
    assert result["lanes"]["codex-lead-1"]["latest_claim_event"]["task_id"] == "sha256:" + pin(task.encode())


def test_historical_missing_bundle_explicit_opt_in_keeps_pinned_bridge_observation():
    row = event("codex-lead-1", "claim", task="codex-lead-1/mind-direct-integration-20261112")
    args = inputs([row])
    for name in ("pointer_bytes", "pointer_sha256", "manifest_bytes", "manifest_sha256", "pointer_observed_utc"):
        args[name] = None
    args["allow_missing_bundle"] = True
    result = view.project_situation(**args)
    assert result["bundle"] == "UNKNOWN" and result["source"]["fleet_manifest_binding"] == "UNKNOWN"
    assert result["lanes"]["codex-lead-1"]["latest_claim_event"]["event_ref"] == row["ref"]


@pytest.mark.parametrize("partial", [True, False])
def test_missing_bundle_is_refused_without_opt_in_or_on_partial_data(partial):
    args = inputs([])
    for name in ("pointer_bytes", "pointer_sha256", "manifest_bytes", "manifest_sha256", "pointer_observed_utc"):
        args[name] = None
    if partial:
        args["allow_missing_bundle"] = True
        args["pointer_sha256"] = "0" * 64
    else:
        args["allow_missing_bundle"] = False
    with pytest.raises(view.SituationRefused): view.project_situation(**args)


def test_historical_opt_in_does_not_hide_invalid_supplied_pointer_or_manifest_pin():
    args = inputs([])
    args["allow_missing_bundle"] = True
    args["manifest_sha256"] = "0" * 64
    with pytest.raises(view.SituationRefused): view.project_situation(**args)
