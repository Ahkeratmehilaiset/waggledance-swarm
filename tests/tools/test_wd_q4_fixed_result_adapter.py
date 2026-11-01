"""Synthetic, pure result-format tests; no lane, runtime or real events."""

import hashlib
import json
import struct
from dataclasses import asdict

import pytest

from tools import wd_q4_lane_link, wd_q4_secure_transport
from tools import wd_q4_fixed_result_adapter as adapter


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def make_case():
    """Build the same synthetic snapshot graph without importing a FAISS backend."""
    from sandbox.mind.step2 import shared_index_checkpoint as checkpoint, shared_projection as projection
    from waggledance.core.bridge_log_reader import BridgeCursor

    roster = {"codex-lead-1": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
              "codex-tools-1": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
              "claude-rco-1": "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
              "claude-rco-2": "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
              "fable-5": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"}
    tasks = frozenset({"public-task", "private-task"})
    policy_bytes = canon({"schema": "wd.q4-non-review-policy.v1", "approved_tasks": sorted(tasks)})
    roster_bytes = canon({"lanes": [{"agent": k, "agent_uuid": v} for k, v in roster.items()
                                   if k != "codex-tools-1"],
                         "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": roster["codex-tools-1"]}})
    public_bytes = canon({"schema": "wd.mind-public-status-policy.v1", "public_tasks": ["public-task"],
                          "public_types": ["message"]})
    events = [{"ts_utc": f"2026-10-20T12:0{i}:00Z", "agent": "codex-lead-1",
               "agent_uuid": roster["codex-lead-1"], "type": "message", "status": "done",
               "task_id": "public-task"} for i in range(4)]
    projected = projection.project_events(events, approved_tasks=tasks, roster=roster, exclude_review_signal=True)
    projection_raw = json.dumps(projected, sort_keys=True, indent=1).encode()
    records = projected["records"]
    ids = [f"{record['record_id']}#{view}" for record in records for view in ("w", "d")]
    rows = [{"record_id": f"{record['record_id']}#{view}", "kind": "event", "time_utc": record["time_utc"],
             "cell_id": "learning", "provenance": dict(record["provenance"], record=record["record_id"], view=view)}
            for record in records for view in ("w", "d")]
    one = struct.pack("<f", 1.0)
    data = {"index": b"fake-index", "vectors": one * len(ids),
            "meta": canon({"dim": 1, "doc_ids": ids,
                           "texts": [record["stored_text"] for record in records for _ in ("w", "d")],
                           "metadata": [{} for _ in ids]}),
            "provenance": b"\n".join(map(canon, rows)) + b"\n"}
    encoder = {"model": "synthetic", "revision": "123", "weights_sha256": "b" * 64}
    pairs = hashlib.sha256()
    for item in ids:
        pairs.update(item.encode("utf-8") + b"\x00" + one)
    content = {"cell_id": "learning", "count": len(ids), "dim": 1, "encoder": encoder,
               "cutoff_utc": "2027-01-01T00:00:00Z", "files": {k: sha(v) for k, v in data.items()},
               "ids_sha256": sha("\n".join(ids).encode()), "pairs_sha256": pairs.hexdigest()}
    data["receipt"] = canon({"schema": "wd.bridge-mind-hex-cell.v1", "content": content,
                              "content_sha256": sha(canon(content))})
    cell_sha = sha(data["receipt"])
    cell_pointer = {"generation": 1, "seq": 1, "receipt_sha256": cell_sha}
    cell_log = {"schema": "wd.bridge-mind-hex-cell-log.v1", "prev_line_sha256": "0" * 64, **cell_pointer}
    cell_snapshots = {"learning": {"pointer": canon(cell_pointer), "log": canon(cell_log) + b"\n",
                                   "artifacts": data}}
    mode = {"status_mode": "public-v1", "public_policy_sha256": sha(public_bytes)}
    policy = {"authorship": projection.AUTHORSHIP_ROSTER, "review_signal_excluded": True,
              "policy_sha256": sha(policy_bytes), "roster_sha256": sha(roster_bytes)}
    memory = {"schema": "wd.bridge-mind-hex-memory-step2.v1", "scope": "wd.q4-shared-projected-events.v1",
              "dim": 1, "cells": {"learning": {"generation": 1, "receipt_sha256": cell_sha}},
              "projection_schema": projection.SCHEMA, "projection_sha256": sha(projection_raw),
              "policy": policy, "source_mode": mode}
    memory_raw = canon(memory)
    descriptor_raw = canon({"schema": "wd.bridge-mind-q4-memory-descriptor.v2", "scope": memory["scope"],
                            "memory_sha256": sha(memory_raw), "dim": 1, "encoder": encoder})
    cursor = BridgeCursor(offset=0, file_identity="posix-v1:1:2", generation=None)
    anchor_sha = "e" * 64
    binding = checkpoint._source_binding(cursor, cursor, anchor_sha, sha(projection_raw),
                                         sha(policy_bytes), sha(roster_bytes))
    build = {"schema": "wd.bridge-mind-shared-index-build-receipt.v1", "scope": memory["scope"],
             "descriptor_schema": "wd.bridge-mind-q4-memory-descriptor.v2", "projection_schema": projection.SCHEMA,
             "projection_file": "projection.json", "encoder": encoder, "dim": 1, "memory_name": "gen-a",
             "memory_sha256": sha(memory_raw), "descriptor_sha256": sha(descriptor_raw),
             "projection_sha256": sha(projection_raw), "policy": policy, "source_mode": mode,
             "source_binding": binding}
    active = {"descriptor.json": descriptor_raw, "memory.json": memory_raw,
              "projection.json": projection_raw, "build_receipt.json": canon(build)}
    pointer = {"schema": checkpoint.PREFIX_SCHEMA, "generation": "gen-a",
               "artifacts": {k: sha(active[name]) for k, name in checkpoint.ARTIFACTS.items()},
               "policy_sha256": sha(policy_bytes), "roster_sha256": sha(roster_bytes), "anchor_sha256": anchor_sha,
               "cursor_start": asdict(cursor), "cursor_end": asdict(cursor), "watermark": asdict(cursor),
               "prefix_start_sha256": "d" * 64, "prefix_watermark_sha256": "d" * 64,
               "empty_advance": None, "previous": None, "previous_pointer_sha256": None}
    pointer_raw = canon(pointer)
    return {"pointer_bytes": pointer_raw, "expected_pointer_sha256": sha(pointer_raw), "approved_tasks": tasks,
            "policy_bytes": policy_bytes, "policy_sha256": sha(policy_bytes), "roster_bytes": roster_bytes,
            "roster_sha256": sha(roster_bytes), "expected_public_policy_sha256": sha(public_bytes),
            "public_policy_bytes": public_bytes, "expected_source_sha256": anchor_sha,
            "expected_encoder_sha256": sha(checkpoint._canonical(encoder)), "active_artifact_bytes": active,
            "cell_snapshots": cell_snapshots, "history_bytes": {}, "enabled": True}


def invoke(case, monkeypatch, tmp_path):
    # Synthetic same-process substitution is a unit-test seam, not evidence of peer isolation.
    monkeypatch.setattr(wd_q4_secure_transport, "run_fixed_peer", lambda **kwargs: (case["result_bytes"], case["code"]))
    return adapter.invoke_fixed_public(
        executable="synthetic", expected_interpreter_sha256="1" * 64, interpreter_max_bytes=100,
        plan_bytes=case["plan_bytes"], expected_plan_sha256=case["expected_plan_sha256"],
        bundle_bytes=case["bundle_bytes"], runtime_manifest_bytes=b"synthetic", query_bytes=b"synthetic",
        cwd=str(tmp_path), timeout_seconds=1, max_output_bytes=4096, enabled=case["enabled"])


@pytest.fixture
def case(monkeypatch, tmp_path):
    synthetic = make_case()
    bundle = wd_q4_secure_transport.fixed_snapshot_bundle(**{
        key: synthetic[key] for key in ("pointer_bytes", "policy_bytes", "roster_bytes", "public_policy_bytes",
                                        "active_artifact_bytes", "history_bytes", "cell_snapshots")
    })
    plan = {
        "schema": "wd.q4-fixed-plan.v2", "backend": "public-snapshot-v1", "interpreter_sha256": "1" * 64,
        "peer_code_sha256": "2" * 64, "runtime_manifest_sha256": "3" * 64, "bundle_sha256": sha(bundle),
        "bundle_bytes": len(bundle), "verifier_sha256": "4" * 64, "runtime_root": str(tmp_path / "runtime"),
        "runtime_manifest_path": str(tmp_path / "runtime-manifest.json"), "runtime_generation": "5" * 40,
        "runtime_revision": "6" * 40, "approved_tasks": sorted(synthetic["approved_tasks"]),
        **{key: synthetic[key] for key in wd_q4_secure_transport._PUBLIC_PINS},
    }
    result = {"schema": "wd.q4-fixed-public-result.v1", "status": "ANSWER", "results": [{
        "stored_text": "synthetic", "ref_sha256": "a" * 64, "doc_id_sha256": "b" * 64,
        "ref_kind": "sha256_of_projected_metadata", "canonical_event": False,
    }], "source_authentication": "UNKNOWN", "freshness": "UNKNOWN",
        "runtime": "MANIFEST_VERIFIED_BEFORE_IMPORT_NOT_ISOLATED",
        "validation": "PARENT_RERESOLVED_AGAINST_PINNED_PROJECTION"}
    raw_plan = canon(plan)
    return {"result_bytes": canon(result), "code": 0, "plan_bytes": raw_plan,
            "expected_plan_sha256": sha(raw_plan), "bundle_bytes": bundle, "enabled": True}


def test_closed_synthetic_result_maps_exact_plan_and_pointer(case, monkeypatch, tmp_path):
    raw, code = invoke(case, monkeypatch, tmp_path)
    plan = json.loads(case["plan_bytes"])
    pointer = json.loads(wd_q4_secure_transport._fixed_bundle_decode(case["bundle_bytes"])["pointer_bytes"])
    pins = {"pointer_sha256": plan["expected_pointer_sha256"], "source_anchor_sha256": plan["expected_source_sha256"],
            "encoder_sha256": plan["expected_encoder_sha256"], "policy_sha256": plan["policy_sha256"],
            "roster_sha256": plan["roster_sha256"], "public_policy_sha256": plan["expected_public_policy_sha256"],
            "descriptor_sha256": pointer["artifacts"]["descriptor_sha256"]}
    result = wd_q4_lane_link._public_result(raw, code, pins)
    assert result["binding"] == {**pins, **{key: pointer["artifacts"][key] for key in
                                             ("memory_sha256", "projection_sha256", "receipt_sha256")}}
    assert all(result[key] == "UNKNOWN" for key in wd_q4_lane_link._PUBLIC_UNKNOWN)
    assert "lane" not in result and b"AUTHENTICATED" not in raw and b'"FRESH"' not in raw


def test_real_parent_validation_result_maps_to_lane_schema(case, monkeypatch, tmp_path):
    from sandbox.mind.step2 import shared_result_resolver as resolver

    plan = json.loads(case["plan_bytes"])
    parts = wd_q4_secure_transport._fixed_bundle_decode(case["bundle_bytes"])
    pointer = json.loads(parts["pointer_bytes"])
    projection = json.loads(parts["active_artifact_bytes"]["projection.json"])
    hits = [{"ref_sha256": sha(record["provenance"]["event_ref"].encode()),
             "doc_id_sha256": sha(record["record_id"].encode()),
             "ref_kind": record["provenance"]["ref_kind"], "canonical_event": False}
            for record in projection["records"]]
    query_time = "2026-10-20T13:00:00Z"
    resolved = resolver.resolve_results(
        memory_manifest=parts["active_artifact_bytes"]["memory.json"],
        expected_memory_sha256=pointer["artifacts"]["memory_sha256"],
        projection_bytes=parts["active_artifact_bytes"]["projection.json"], results=hits,
        query_utc=query_time, approved_tasks=frozenset(plan["approved_tasks"]),
        policy_bytes=parts["policy_bytes"], policy_sha256=plan["policy_sha256"],
        roster_bytes=parts["roster_bytes"], roster_sha256=plan["roster_sha256"])
    answer = canon({"schema": "wd.q4-fixed-public-answer.v1", "status": "ANSWER", "results": resolved,
                    "plan_sha256": sha(case["plan_bytes"]), "bundle_sha256": sha(case["bundle_bytes"]),
                    "backend": "public-snapshot-v1", "source_authentication": "UNKNOWN", "freshness": "UNKNOWN",
                    "runtime": "MANIFEST_VERIFIED_BEFORE_IMPORT_NOT_ISOLATED"})
    case["result_bytes"] = wd_q4_secure_transport._fixed_public_validate(
        case["plan_bytes"], case["bundle_bytes"], canon({"situation": "synthetic situation", "time": query_time}), answer)
    raw, code = invoke(case, monkeypatch, tmp_path)
    assert code == 0 and json.loads(raw)["results"] == resolved


@pytest.mark.parametrize("mutation", ["extra", "item_extra", "long", "utf8_long", "noncanonical", "duplicate",
                                       "wrong_claim", "bad_status", "bad_code", "wrong_plan_pin", "wrong_bundle",
                                       "bad_pointer", "too_many", "disabled", "enabled_nonbool"])
def test_bad_inputs_fail_closed(case, mutation, monkeypatch, tmp_path):
    result = json.loads(case["result_bytes"])
    if mutation == "extra": result["extra"] = 1
    if mutation == "item_extra": result["results"][0]["extra"] = 1
    if mutation == "long": result["results"][0]["stored_text"] = "x" * 321
    if mutation == "utf8_long": result["results"][0]["stored_text"] = "ö" * 161
    if mutation == "wrong_claim": result["freshness"] = "FRESH"
    if mutation == "bad_status": result["status"] = "ABSTAIN"
    if mutation == "duplicate": result["results"] *= 2
    if mutation == "too_many": result["results"] *= 101
    case["result_bytes"] = canon(result) if mutation != "noncanonical" else json.dumps(result, indent=1).encode()
    if mutation == "bad_code": case["code"] = 1
    if mutation == "wrong_plan_pin": case["expected_plan_sha256"] = "0" * 64
    if mutation == "wrong_bundle": case["bundle_bytes"] += b" "
    if mutation == "bad_pointer":
        plan = json.loads(case["plan_bytes"])
        plan["expected_pointer_sha256"] = "0" * 64
        case["plan_bytes"] = canon(plan)
        case["expected_plan_sha256"] = sha(case["plan_bytes"])
    if mutation == "disabled": case["enabled"] = False
    if mutation == "enabled_nonbool": case["enabled"] = 1
    with pytest.raises(adapter.FixedResultAdapterRefused):
        invoke(case, monkeypatch, tmp_path)


def test_upstream_refusal_maps_to_exact_fixed_public_refusal(case, monkeypatch, tmp_path):
    case.update(result_bytes=b"", code=2)
    raw, code = invoke(case, monkeypatch, tmp_path)
    assert wd_q4_lane_link._public_result(raw, code, {}) == {
        "schema": "wd.q4-public-query.v2", "status": "REFUSED", "reason": "runtime_refused"}


def test_upstream_refusal_cannot_hide_output(case, monkeypatch, tmp_path):
    case.update(result_bytes=b"private", code=2)
    with pytest.raises(adapter.FixedResultAdapterRefused):
        invoke(case, monkeypatch, tmp_path)


def test_detached_forged_result_has_no_public_entrypoint(case):
    import inspect
    assert not hasattr(adapter, "adapt_fixed_result")
    assert "result_bytes" not in inspect.signature(adapter.invoke_fixed_public).parameters


def test_public_entrypoint_calls_fixed_peer_itself(case, monkeypatch, tmp_path):
    observed = []
    def validated_parent(**kwargs):
        observed.append(kwargs)
        return case["result_bytes"], 0
    monkeypatch.setattr(wd_q4_secure_transport, "run_fixed_peer", validated_parent)
    raw, code = adapter.invoke_fixed_public(
        executable="synthetic", expected_interpreter_sha256="1" * 64, interpreter_max_bytes=100,
        plan_bytes=case["plan_bytes"], expected_plan_sha256=case["expected_plan_sha256"],
        bundle_bytes=case["bundle_bytes"], runtime_manifest_bytes=b"synthetic", query_bytes=b"synthetic",
        cwd=str(tmp_path), timeout_seconds=1, max_output_bytes=4096, enabled=True)
    assert len(observed) == 1 and observed[0]["plan_bytes"] == case["plan_bytes"]
    assert observed[0]["bundle_bytes"] == case["bundle_bytes"]
    assert observed[0]["public_snapshot_enabled"] is True
    assert code == 0 and json.loads(raw)["status"] == "ANSWER"


def test_real_synthetic_fixed_peer_is_called_before_mapping(monkeypatch, tmp_path):
    import importlib.util
    import os
    import sys
    from pathlib import Path
    from sandbox.mind.step2 import shared_result_resolver as resolver

    spec = importlib.util.spec_from_file_location("fixed_snapshot_test_helpers",
        Path(__file__).with_name("test_wd_q4_fixed_snapshot_transport.py"))
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    snapshot = make_case()
    projection = json.loads(snapshot["active_artifact_bytes"]["projection.json"])
    hits = [{"ref_sha256": sha(r["provenance"]["event_ref"].encode()),
             "doc_id_sha256": sha(r["record_id"].encode()), "ref_kind": r["provenance"]["ref_kind"],
             "canonical_event": False} for r in projection["records"]]
    resolved = resolver.resolve_results(
        memory_manifest=snapshot["active_artifact_bytes"]["memory.json"],
        expected_memory_sha256=json.loads(snapshot["pointer_bytes"])["artifacts"]["memory_sha256"],
        projection_bytes=snapshot["active_artifact_bytes"]["projection.json"], results=hits,
        query_utc=helpers.QUERY_TIME, approved_tasks=snapshot["approved_tasks"],
        policy_bytes=snapshot["policy_bytes"], policy_sha256=snapshot["policy_sha256"],
        roster_bytes=snapshot["roster_bytes"], roster_sha256=snapshot["roster_sha256"])
    assert resolved
    synthetic = {"parts": {name: snapshot[name] for name in helpers.PARTS},
                 "pins": {name: snapshot[name] for name in wd_q4_secure_transport._PUBLIC_PINS},
                 "tasks": sorted(snapshot["approved_tasks"]), "resolved": resolved}
    runtime = helpers.synthetic_runtime(tmp_path, helpers.make_stub(synthetic["resolved"]))
    bundle = wd_q4_secure_transport.fixed_snapshot_bundle(**synthetic["parts"])
    plan = helpers.public_plan(*runtime, bundle, pins=synthetic["pins"], tasks=synthetic["tasks"])
    raw, code = adapter.invoke_fixed_public(
        executable=sys.executable, expected_interpreter_sha256=helpers.interpreter_sha(),
        interpreter_max_bytes=64 * 1024 * 1024, plan_bytes=plan, expected_plan_sha256=sha(plan),
        bundle_bytes=bundle, runtime_manifest_bytes=runtime[1].read_bytes(), query_bytes=helpers.QUERY,
        cwd=str(tmp_path), timeout_seconds=60, max_output_bytes=65536, enabled=True)
    if os.name == "nt":
        assert code == 0 and json.loads(raw)["results"] == synthetic["resolved"]
    else:
        # The fixed peer relies on Windows named pipes and Windows file leases;
        # other platforms must execute and prove fail-closed, not skip the test.
        assert code == 2 and json.loads(raw) == {"schema": "wd.q4-public-query.v2", "status": "REFUSED",
                                                 "reason": "runtime_refused"}


def test_peer_refusal_and_unconfirmed_cleanup_are_not_confused(case, monkeypatch, tmp_path):
    from tools.wd_q4_fixed_parent_attestation import AttestationCleanupUnknown

    def refused(**_):
        raise wd_q4_secure_transport._Refused()
    monkeypatch.setattr(wd_q4_secure_transport, "run_fixed_peer", refused)
    kwargs = dict(executable="synthetic", expected_interpreter_sha256="1" * 64, interpreter_max_bytes=100,
        plan_bytes=case["plan_bytes"], expected_plan_sha256=case["expected_plan_sha256"],
        bundle_bytes=case["bundle_bytes"], runtime_manifest_bytes=b"synthetic", query_bytes=b"synthetic",
        cwd=str(tmp_path), timeout_seconds=1, max_output_bytes=4096, enabled=True)
    raw, code = adapter.invoke_fixed_public(**kwargs)
    assert json.loads(raw) == {"schema": "wd.q4-public-query.v2", "status": "REFUSED", "reason": "runtime_refused"}
    assert code == 2
    lease = object()
    def cleanup_unknown(**_):
        raise AttestationCleanupUnknown(lease)
    monkeypatch.setattr(wd_q4_secure_transport, "run_fixed_peer", cleanup_unknown)
    with pytest.raises(AttestationCleanupUnknown) as caught:
        adapter.invoke_fixed_public(**kwargs)
    assert caught.value.lease is lease


def test_transport_cleanup_unknown_is_not_converted_to_a_refusal(case, monkeypatch, tmp_path):
    def cleanup_unknown(**_):
        raise wd_q4_secure_transport.TransportCleanupUnknown("timeout")
    monkeypatch.setattr(wd_q4_secure_transport, "run_fixed_peer", cleanup_unknown)
    kwargs = dict(executable="synthetic", expected_interpreter_sha256="1" * 64, interpreter_max_bytes=100,
        plan_bytes=case["plan_bytes"], expected_plan_sha256=case["expected_plan_sha256"],
        bundle_bytes=case["bundle_bytes"], runtime_manifest_bytes=b"synthetic", query_bytes=b"synthetic",
        cwd=str(tmp_path), timeout_seconds=1, max_output_bytes=4096, enabled=True)
    with pytest.raises(wd_q4_secure_transport.TransportCleanupUnknown) as caught:
        adapter.invoke_fixed_public(**kwargs)
    assert caught.value.prior_outcome == "timeout"
