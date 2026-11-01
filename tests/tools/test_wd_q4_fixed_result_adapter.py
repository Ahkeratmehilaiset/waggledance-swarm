"""Synthetic, pure result-format tests; no lane, runtime or real events."""

import hashlib
import json

import pytest

from sandbox.mind.step2.test_verified_snapshot_checkpoint import make_case
from tools import wd_q4_lane_link, wd_q4_secure_transport
from tools import wd_q4_fixed_result_adapter as adapter


def canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha(value):
    return hashlib.sha256(value).hexdigest()


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
    synthetic = make_case(monkeypatch)
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
    import sys
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("fixed_snapshot_test_helpers",
        Path(__file__).with_name("test_wd_q4_fixed_snapshot_transport.py"))
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    synthetic = helpers.snapshot_case(monkeypatch)
    runtime = helpers.synthetic_runtime(tmp_path, helpers.make_stub(synthetic["resolved"]))
    bundle = wd_q4_secure_transport.fixed_snapshot_bundle(**synthetic["parts"])
    plan = helpers.public_plan(*runtime, bundle, pins=synthetic["pins"], tasks=synthetic["tasks"])
    raw, code = adapter.invoke_fixed_public(
        executable=sys.executable, expected_interpreter_sha256=helpers.interpreter_sha(),
        interpreter_max_bytes=64 * 1024 * 1024, plan_bytes=plan, expected_plan_sha256=sha(plan),
        bundle_bytes=bundle, runtime_manifest_bytes=runtime[1].read_bytes(), query_bytes=helpers.QUERY,
        cwd=str(tmp_path), timeout_seconds=60, max_output_bytes=65536, enabled=True)
    assert code == 0 and json.loads(raw)["results"] == synthetic["resolved"]


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
