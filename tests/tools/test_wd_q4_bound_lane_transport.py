"""Synthetic only: fixed peer and lane wrapper plan are pinned to the same snapshot."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import traceback

import pytest

from tools import wd_q4_lane_link, wd_q4_lane_query, wd_q4_secure_transport
from tools import wd_q4_bound_lane_transport as bound


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def sha(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def case(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("fixed_snapshot_test_helpers_bound",
        Path(__file__).with_name("test_wd_q4_fixed_snapshot_transport.py"))
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    synthetic = helpers.snapshot_case(monkeypatch)
    runtime = helpers.synthetic_runtime(tmp_path, helpers.make_stub(synthetic["resolved"]))
    fixed_bundle = wd_q4_secure_transport.fixed_snapshot_bundle(**synthetic["parts"])
    fixed_plan = helpers.public_plan(*runtime, fixed_bundle, pins=synthetic["pins"], tasks=synthetic["tasks"])
    pointer = json.loads(synthetic["parts"]["pointer_bytes"])
    binding = {"pointer_sha256": synthetic["pins"]["expected_pointer_sha256"],
               "source_anchor_sha256": synthetic["pins"]["expected_source_sha256"],
               "encoder_sha256": synthetic["pins"]["expected_encoder_sha256"],
               "policy_sha256": synthetic["pins"]["policy_sha256"],
               "roster_sha256": synthetic["pins"]["roster_sha256"],
               "public_policy_sha256": synthetic["pins"]["expected_public_policy_sha256"],
               "descriptor_sha256": pointer["artifacts"]["descriptor_sha256"]}
    root = tmp_path / "wrapper-bundle"
    root.mkdir()
    wrapper = root / "Invoke-WdQ4Query.ps1"
    wrapper.write_bytes(b"synthetic wrapper is never executed")
    commit = "a" * 40
    manifest = canonical({"schema_version": 1, "source_commit": commit,
                          "files": {wrapper.name: sha(wrapper.read_bytes())}})
    (root / "deployment-manifest.json").write_bytes(manifest)
    plan_doc = {"schema": "wd.q4-public-wrapper-plan.v1", "config": {"descriptor_sha256": binding["descriptor_sha256"]},
                "root": "synthetic", "policy_path": "synthetic", "roster_path": "synthetic",
                "public_policy_path": "synthetic", **{key: value for key, value in binding.items()
                                                       if key != "descriptor_sha256"}}
    public_path = tmp_path / "public-plan.json"
    public_path.write_bytes(canonical(plan_doc))
    return {"bundle_root": str(root), "expected_commit": commit, "expected_manifest_sha256": sha(manifest),
            "expected_wrapper_sha256": sha(wrapper.read_bytes()), "public_plan_path": str(public_path),
            "expected_public_plan_sha256": sha(public_path.read_bytes()), "executable": sys.executable,
            "expected_interpreter_sha256": helpers.interpreter_sha(), "interpreter_max_bytes": 64 * 1024 * 1024,
            "fixed_plan_bytes": fixed_plan, "expected_fixed_plan_sha256": sha(fixed_plan),
            "fixed_bundle_bytes": fixed_bundle, "runtime_manifest_bytes": runtime[1].read_bytes(),
            "cwd": str(tmp_path), "timeout_seconds": 60, "max_output_bytes": 65536, "enabled": True,
            "_expected": synthetic["resolved"], "_query": helpers.QUERY, "_parts": synthetic["parts"],
            "_binding": {**binding, "projection_sha256": pointer["artifacts"]["projection_sha256"]}}


def create(case):
    return bound.make_bound_transport(**{key: value for key, value in case.items() if not key.startswith("_")})


def invoke(case, transport):
    return wd_q4_lane_link.invoke_public(
        lane="codex-lead-1", bundle_root=case["bundle_root"], expected_commit=case["expected_commit"],
        expected_manifest_sha256=case["expected_manifest_sha256"], expected_wrapper_sha256=case["expected_wrapper_sha256"],
        public_plan_path=case["public_plan_path"], expected_public_plan_sha256=case["expected_public_plan_sha256"],
        query_bytes=case["_query"], transport=transport, enabled=True)


def transport_args(case):
    return {"wrapper": str(Path(case["bundle_root"]) / "Invoke-WdQ4Query.ps1"),
            "arguments": ("-BundleRoot", case["bundle_root"], "-ExpectedBundleCommit", case["expected_commit"],
                "-ExpectedManifestSha256", case["expected_manifest_sha256"], "-Public", "-PublicPlanPath",
                case["public_plan_path"], "-ExpectedPublicPlanSha256", case["expected_public_plan_sha256"]),
            "stdin_bytes": case["_query"]}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows fixed-peer transport is required")
def test_real_synthetic_fixed_peer_to_lane_link(case):
    result = invoke(case, create(case))
    assert result["status"] == "ANSWER" and result["results"] == case["_expected"]
    assert all(result[key] == "UNKNOWN" for key in wd_q4_lane_link._PUBLIC_UNKNOWN)
    assert "lane" not in result


def invoke_query(case, declared_lane):
    from datetime import datetime
    query = json.loads(case["_query"])
    parts = case["_parts"]
    return wd_q4_lane_query.query_public(
        lane=declared_lane, situation=query["situation"], time=datetime.fromisoformat(query["time"].replace("Z", "+00:00")),
        bundle_root=case["bundle_root"],
        expected_commit=case["expected_commit"], expected_manifest_sha256=case["expected_manifest_sha256"],
        expected_wrapper_sha256=case["expected_wrapper_sha256"], public_plan_path=case["public_plan_path"],
        expected_public_plan_sha256=case["expected_public_plan_sha256"], expected_binding=case["_binding"],
        projection_bytes=parts["active_artifact_bytes"]["projection.json"], policy_bytes=parts["policy_bytes"],
        roster_bytes=parts["roster_bytes"], transport=create(case), enabled=True)


@pytest.mark.parametrize("declared_lane", sorted(wd_q4_lane_link._LANES))
@pytest.mark.skipif(sys.platform != "win32", reason="Windows fixed-peer transport is required")
def test_real_synthetic_fixed_peer_to_full_lane_query(case, declared_lane):
    result = invoke_query(case, declared_lane)
    assert result["status"] == "ANSWER" and result["result_count"] == len(case["_expected"])
    assert result["lane"] == declared_lane and result["trust"]["lane_authentication"] == "UNKNOWN"


@pytest.mark.parametrize("failure", ["timeout", "transport_cleanup_unknown", "attestation_cleanup_unknown"])
def test_full_query_chain_preserves_sanitized_indeterminate_state(case, failure, monkeypatch):
    from tools.wd_q4_fixed_parent_attestation import AttestationCleanupUnknown
    lease = object()
    original = {"timeout": TimeoutError("synthetic-private-timeout"),
                "transport_cleanup_unknown": wd_q4_secure_transport.TransportCleanupUnknown("synthetic-private"),
                "attestation_cleanup_unknown": AttestationCleanupUnknown(lease)}[failure]
    def fail(**_):
        raise original
    monkeypatch.setattr(bound.adapter, "invoke_fixed_public", fail)
    with pytest.raises(type(original)) as caught:
        invoke_query(case, "codex-lead-1")
    assert caught.value is not original
    assert caught.value.__context__ is None and caught.value.__cause__ is None
    assert "synthetic-private" not in str(caught.value)
    if failure == "transport_cleanup_unknown": assert caught.value.prior_outcome == "other"
    if failure == "attestation_cleanup_unknown": assert caught.value.lease is lease


@pytest.mark.parametrize("key", sorted(wd_q4_lane_link._PUBLIC_BIND_PINS))
def test_every_independently_pinned_wrapper_plan_field_must_match_before_query(case, key):
    path = Path(case["public_plan_path"])
    document = json.loads(path.read_bytes())
    if key == "descriptor_sha256": document["config"][key] = "0" * 64
    else: document[key] = "0" * 64
    path.write_bytes(canonical(document))
    case["expected_public_plan_sha256"] = sha(path.read_bytes())
    with pytest.raises(bound.BoundLaneTransportRefused):
        create(case)


@pytest.mark.parametrize("change", ["wrapper", "arguments", "extra", "disabled", "fixed_plan_pin", "fixed_bundle"])
def test_call_and_factory_reject_bad_binding(case, change):
    if change == "disabled": case["enabled"] = False
    if change == "fixed_plan_pin": case["expected_fixed_plan_sha256"] = "0" * 64
    if change == "fixed_bundle": case["fixed_bundle_bytes"] += b" "
    if change in ("disabled", "fixed_plan_pin", "fixed_bundle"):
        with pytest.raises(bound.BoundLaneTransportRefused): create(case)
        return
    transport = create(case)
    root = case["bundle_root"]
    wrapper = str(Path(root) / "Invoke-WdQ4Query.ps1")
    args = ("-BundleRoot", root, "-ExpectedBundleCommit", case["expected_commit"],
            "-ExpectedManifestSha256", case["expected_manifest_sha256"], "-Public", "-PublicPlanPath",
            case["public_plan_path"], "-ExpectedPublicPlanSha256", case["expected_public_plan_sha256"])
    if change == "wrapper": wrapper += "other"
    if change == "arguments": args += ("-Extra",)
    with pytest.raises((bound.BoundLaneTransportRefused, TypeError)):
        if change == "extra": transport(wrapper=wrapper, arguments=args, stdin_bytes=case["_query"], result_bytes=b"forged")
        else: transport(wrapper=wrapper, arguments=args, stdin_bytes=case["_query"])


@pytest.mark.parametrize("target", ["plan", "wrapper"])
def test_mutated_pinned_file_is_rejected_before_fixed_peer(case, target, monkeypatch):
    transport = create(case)
    path = Path(case["public_plan_path"]) if target == "plan" else Path(transport_args(case)["wrapper"])
    path.write_bytes(path.read_bytes() + b" ")
    monkeypatch.setattr(bound.adapter, "invoke_fixed_public", lambda **_: pytest.fail("dispatched after pin changed"))
    with pytest.raises(bound.BoundLaneTransportRefused):
        transport(**transport_args(case))


@pytest.mark.parametrize("failure", ["timeout", "transport_cleanup_unknown", "attestation_cleanup_unknown"])
def test_direct_transport_preserves_indeterminate_state(case, failure, monkeypatch):
    from tools.wd_q4_fixed_parent_attestation import AttestationCleanupUnknown
    transport = create(case)
    original = {"timeout": TimeoutError("synthetic_timeout"),
                "transport_cleanup_unknown": wd_q4_secure_transport.TransportCleanupUnknown("timeout"),
                "attestation_cleanup_unknown": AttestationCleanupUnknown(object())}[failure]
    def fail(**_):
        raise original
    monkeypatch.setattr(bound.adapter, "invoke_fixed_public", fail)
    with pytest.raises(type(original)) as caught:
        transport(**transport_args(case))
    assert caught.value is original


def test_factory_binding_refusal_drops_context_and_sensitive_frame_locals(case):
    secret = b'SYNTHETIC_PRIVATE_PLAN'
    case['fixed_plan_bytes'] = secret
    case['expected_fixed_plan_sha256'] = sha(secret)
    with pytest.raises(bound.BoundLaneTransportRefused, match='^binding_refused$') as caught:
        create(case)
    error = caught.value
    assert error.__cause__ is None and error.__context__ is None
    frames = [frame for frame, _ in traceback.walk_tb(error.__traceback__)
              if frame.f_code.co_name == 'make_bound_transport']
    assert len(frames) == 1
    assert not {'fixed_plan_bytes', 'fixed_bundle_bytes', 'runtime_manifest_bytes',
                'bundle_root', 'public_plan_path', 'expected_fixed_plan_sha256'} & set(frames[0].f_locals)


def test_transport_binding_refusal_drops_context_and_sensitive_frame_locals(case):
    transport = create(case)
    args = transport_args(case)
    args['wrapper'] += '-invalid'
    args['stdin_bytes'] = b'SYNTHETIC_PRIVATE_QUERY'
    with pytest.raises(bound.BoundLaneTransportRefused, match='^binding_refused$') as caught:
        transport(**args)
    error = caught.value
    assert error.__cause__ is None and error.__context__ is None
    frames = [frame for frame, _ in traceback.walk_tb(error.__traceback__)
              if frame.f_code.co_name == 'transport']
    assert len(frames) == 1
    assert not {'stdin_bytes', 'arguments', 'wrapper', 'fixed_plan_bytes', 'fixed_bundle_bytes',
                'runtime_manifest_bytes', 'bundle_root', 'public_plan_path'} & set(frames[0].f_locals)


@pytest.fixture
def unsupported_fixed_peer(monkeypatch):
    # Model the unsupported OS branch without changing the process-wide os module.
    from tools import wd_q4_secure_transport as fixed
    real_os = fixed.os
    class UnsupportedOS:
        name = "posix"
        def __getattr__(self, name):
            return getattr(real_os, name)
    monkeypatch.setattr(fixed, "os", UnsupportedOS())


def test_unsupported_peer_refuses_lane_link(case, unsupported_fixed_peer):
    result = invoke(case, create(case))
    assert result == {"schema": "wd.q4-public-query.v2", "status": "REFUSED", "reason": "runtime_refused"}


@pytest.mark.parametrize("declared_lane", sorted(wd_q4_lane_link._LANES))
def test_unsupported_peer_refuses_full_lane_query(case, unsupported_fixed_peer, declared_lane):
    result = invoke_query(case, declared_lane)
    assert result["status"] == "REFUSED" and result["result_count"] == 0
