"""Invented bytes only, below the allowed audit root; no native execution."""
import copy
import contextlib
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

import pytest

from sandbox.mind.measurement_tools import prospective_publish as pub
from sandbox.mind.measurement_tools import test_prospective_pipeline as fixtures


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def sha(value):
    return hashlib.sha256(value).hexdigest()


@pytest.fixture
def study(monkeypatch):
    parent = pub.capture.AUDIT_ROOT / "m1-p3-prospective-20261020/publish-author/synthetic"
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="invented-forest-", dir=parent) as name:
        forest = Path(name)
        # Narrow, never widen, the real allowed audit root for direct-child
        # synthetic control/ledger siblings. No real global sibling is touched.
        monkeypatch.setattr(pub.capture, "AUDIT_ROOT", forest)
        root = forest / "study"
        root.mkdir()
        control, ledger = forest / "control", forest / "ledger"
        control.mkdir()
        ledger.mkdir()
        args = fixtures.fixture()
        prereg = raw({"schema": "wd.m1-prospective-preregistration.v1", "status": "LOCKED", "study_id": "a" * 32})
        lock = json.loads(args["bindings"].protocol_json)
        identity = {"agent": "synthetic-authorizer", "agent_uuid": "b" * 32, "session_id": "synthetic-session"}
        lock.update({"native_authorizer": identity, "publisher_authorizer": identity,
                     "study_root": str(root),
                     "preregistration_sha256": sha(prereg), "publisher": {
                         "root": str(root), "paths": {k: str(root / k) for k in ("private", "blind", "score")},
                         "source_sha256": sha(Path(pub.__file__).read_bytes()),
                         "pipeline_sha256": pub.PIPELINE_SHA256, "capture_sha256": pub.CAPTURE_SHA256,
                         "cases_sha256": pub.CASES_SHA256}})
        lock["collection_controller"] = {
            "audit_root": str(pub.capture.AUDIT_ROOT), "control_root": str(control),
            "ledger_root": str(ledger), "inbox": str(root / "collection/inbox"),
            "baseline_root": str(root / "captured_baseline"), "blind_root": str(root / "blind"),
            "score_root": str(root / "score"), "stop_signal": str(root / "collection/inbox/STOP.json"),
            "collection_start_utc": "2035-04-05T06:00:00Z", "collection_end_utc": "2035-04-06T06:00:00Z",
            "baseline_resolution_us": 1_000_000, "max_record_delay_seconds": 120, "max_records": 100,
            "allowed_reporters": [], "sources": [], "poll_ms": 100, "max_wall_seconds": 86400}
        protocol = raw(lock)
        capsule = json.loads(args["bindings"].capsule_json)
        manifest = json.loads(args["baseline"].manifest_json)
        manifest["identity"]["protocol_sha256"] = sha(protocol)
        manifest["identity"]["preregistration_sha256"] = sha(prereg)
        manifest["identity"]["destination"] = str(root / "captured_baseline")
        manifest_bytes = raw(manifest)
        seal = json.loads(args["baseline"].seal_json)
        seal["manifest_sha256"] = sha(manifest_bytes)
        capsule.update({"protocol_sha256": sha(protocol), "baseline_manifest_sha256": sha(manifest_bytes),
                        "baseline_seal_sha256": sha(raw(seal)), "capture_identity_sha256": sha(raw(manifest["identity"])),
                        "privacy_source_sha256": pub.PIPELINE_SHA256})
        cases = pub.pipe.cases
        bindings = cases.Bindings.from_bytes(raw(capsule), expected_sha256=sha(raw(capsule)), protocol=protocol, protocol_sha256=sha(protocol))
        baseline = cases.Baseline.from_bytes(manifest_bytes, raw(seal), dict(args["baseline"].blobs), bindings=bindings)
        entries = []
        for entry in args["entries"]:
            incident = json.loads(entry.incident_json)
            incident.update({"protocol_sha256": sha(protocol), "baseline_sha256": sha(manifest_bytes)})
            entries.append(cases.CaseInput(raw(incident), sha(raw(incident)), entry.evidence_json, entry.evidence_sha256))
        args.update({"bindings": bindings, "baseline": baseline, "entries": entries, "protocol_sha256": sha(protocol),
                     "capsule_sha256": sha(raw(capsule)), "adapter_source_sha256": pub.PIPELINE_SHA256,
                     "runner_design_sha256": sha(protocol)})
        provisional = pub.pipe.prepare_prepass(**args)
        native_manifest = raw({"schema": "wd.m1-p3-native-manifest.v1", "study_id": "a" * 32, "root": str(root),
            "protocol": {"path": str(root / "private/prepass_inputs/original/protocol.json"), "sha256": sha(protocol)},
            "worker": {"path": str(Path(pub.__file__).with_name("prospective_native.py")), "sha256": pub.NATIVE_SHA256},
            # Opaque runtime declarations are the separate worker's duty; these
            # invented placeholders are NEVER passed to/imported by a worker.
            "owner": {}, "python": {}, "source_root": "C:/synthetic/source", "source_files": {},
            "package_roots": [], "cache_root": "C:/synthetic/cache", "cache_files": {}, "environment": {},
            "helpers": {}, "stdio_paths": {}, "corpus_as_of_utc": "2035-04-05T05:59:00Z",
            "collection_start_utc": capsule["collection_start_utc"], "collection_end_utc": capsule["collection_end_utc"],
            "binding": json.loads(args["runner_binding"]), "inputs": {
                "runner_cases": {"path": str(root / "blind/runner_cases.json"), "sha256": sha(provisional.executor_inputs.runner_cases_json)},
                "snapshot": {"path": str(root / "blind/snapshot.json"), "sha256": sha(provisional.snapshot_json)},
                "baseline_manifest": {"path": str(root / "baseline/manifest.json"), "sha256": sha(manifest_bytes)},
                "baseline_seal": {"path": str(root / "baseline/SEALED.json"), "sha256": sha(raw(seal))},
                "baseline_blobs": {digest: str(root / "baseline/blobs" / (digest + ".bin")) for digest, _ in baseline.blobs},
                "commitments_path": str(root / "blind/kept_commitments.json")}})
        owner = raw({"schema": "wd.m1-p3-study-root.v1", "study_id": "a" * 32, "protocol_sha256": sha(protocol), "root": str(root)})
        (root / "OWNER.json").write_bytes(owner)
        captured = root / "captured_baseline"
        (captured / "blobs").mkdir(parents=True)
        (captured / "manifest.json").write_bytes(manifest_bytes)
        (captured / "SEALED.json").write_bytes(raw(seal))
        (captured / "intent.json").write_bytes(raw({"schema": "wd.m1-p3-capture-intent.v1", "identity": manifest["identity"],
            "started_utc": manifest["started_utc"], "attempt_consumed": True}))
        for digest, body in baseline.blobs:
            (captured / "blobs" / digest).write_bytes(body)
        monkeypatch.setattr(pub, "_now", lambda: pub.capture.parse_utc("2035-04-06T06:00:05Z"))
        yield {"root": root, "args": args, "protocol": protocol, "prereg": prereg, "identity": identity,
               "manifest": native_manifest, "control": control, "ledger": ledger}


def authority(study, phase, pins):
    return raw({"schema": "wd.m1-p3-publish-go.v1", "status": "LOCKED_PUBLISH_GO", "study_id": "a" * 32,
                "protocol_sha256": sha(study["protocol"]), "phase": phase, "authorizer": study["identity"],
                "source_sha256": sha(Path(pub.__file__).read_bytes()), "pins": pins,
                "issued_at_utc": "2035-04-06T06:00:00Z", "expires_at_utc": "2035-04-06T06:15:00Z", "event_ref": "c" * 64})


def start(study, *, execute=True, go=None):
    pins = pub.input_pins(study["args"], preregistration=study["prereg"])
    go = authority(study, "prepass_inputs", pins) if go is None else go
    return pub.publish_prepass_inputs(study["args"], preregistration=study["prereg"], go=go, go_sha256=sha(go), execute=execute)


def native(study):
    """Fabricate pinned native receipts without importing/invoking a worker."""
    root, args = study["root"], study["args"]
    plan = pub.pipe.prepare_prepass(**args)
    result = fixtures.prepass(plan)
    prepared = json.loads(fixtures.prepared(plan))
    for row in prepared.values():
        row["memory_dir"] = str(root / "prepared/shared/memory")
    prepared = raw(prepared)
    native_manifest = study["manifest"]
    out = root / "native"
    for path in (out / "prepare", out / "prepass", out / "journal/prepass"):
        path.mkdir(parents=True, exist_ok=True)
    (out / "PREPARED.json").write_bytes(prepared)
    (out / "journal/prepass/RESULT.json").write_bytes(result)
    info = lambda body: {"sha256": sha(body), "bytes": len(body)}
    receipts = {}
    for phase, retained, outputs in (
        ("prepare", {"native/PREPARED.json": {"sha256": sha(prepared)}}, {"PREPARED": info(prepared)}),
        ("prepass", {"native/PREPARED.json": {"sha256": sha(prepared)}, "native/journal/prepass/RESULT.json": {"sha256": sha(result)}}, {"RESULT.json": info(result)}),
    ):
        body = raw({"schema": "wd.m1-p3-native-phase.v1", "status": "COMPLETE", "study_id": "a" * 32,
                    "phase": phase, "manifest_sha256": sha(native_manifest), "go_sha256": "d" * 64,
                    "invocation_consumed": True, "actual_data_opened": True, "SAFE_cases": 2,
                    "outputs": outputs, "retained_outputs": retained, "observations": [],
                    "stdout": {"sha256": "e" * 64, "bytes": 0}, "stderr": {"sha256": "e" * 64, "bytes": 0}, "DLL_closure": "UNKNOWN"})
        (out / phase / "RESULT.json").write_bytes(body)
        receipts[phase] = body
    return {"native_manifest": native_manifest, "prepared_receipts": prepared, "native_prepass": result,
            "native_prepare_receipt": receipts["prepare"], "native_prepass_receipt": receipts["prepass"]}


def finish(study, supplied=None, *, go=None):
    supplied = native(study) if supplied is None else supplied
    prior = (study["root"] / "private/prepass_inputs/RESULT.json").read_bytes()
    pins = pub.completion_pins(study["args"], preregistration=study["prereg"], prior_result=prior, **supplied)
    go = authority(study, "complete_cases", pins) if go is None else go
    return pub.publish_complete_cases(study["args"], preregistration=study["prereg"], prior_result=prior,
                                     go=go, go_sha256=sha(go), execute=True, **supplied)


def test_disabled_has_no_effect(study):
    with pytest.raises(pub.NotReady, match="dormant"):
        start(study, execute=False)
    assert sorted(p.name for p in study["root"].iterdir()) == ["OWNER.json", "captured_baseline"]


def test_prepass_publishes_exact_originals_and_only_safe_blind_bytes(study):
    result = start(study)
    root = study["root"]
    plan = pub.pipe.prepare_prepass(**study["args"])
    assert result["status"] == "COMPLETE" and result["invocation_consumed"] is True
    assert (root / "blind/runner_cases.json").read_bytes() == plan.executor_inputs.runner_cases_json
    assert (root / "blind/time_sidecar.json").read_bytes() == plan.executor_inputs.time_sidecar_json
    assert all(set(json.loads(line)) == {"id", "situation", "snapshot_sha256"}
               for line in (root / "blind/blind.jsonl").read_bytes().splitlines())
    assert (root / "private/prepass_inputs/original/protocol.json").read_bytes() == study["protocol"]
    blind = b"".join(p.read_bytes() for p in (root / "blind").rglob("*") if p.is_file())
    assert all(word not in blind for word in (b"gold_basename", b'"nonce"', b"incident_kind"))
    # The immutable whole-corpus snapshot contains ALL baseline basenames; it
    # is not a nomination/gold projection and is required by the native runner.
    assert b"synthetic_000.md" not in (root / "blind/runner_cases.json").read_bytes()
    assert not (root / "native").exists() and not (root / "score").exists()


def test_complete_preserves_full_safe_inputs_and_keeps_private_artifacts_private(study):
    start(study)
    original = (study["root"] / "blind/runner_cases.json").read_bytes()
    result = finish(study)
    root = study["root"]
    assert result["status"] == "COMPLETE" and result["scorer_unlocked"] is False
    assert (root / "blind/runner_cases.json").read_bytes() == original
    assert len(json.loads(original)) == 2
    assert (root / "private/complete_cases/cases.pre.json").is_file()
    assert b'"nonce"' in (root / "private/complete_cases/cases.json").read_bytes()
    assert b'"nonce"' not in (root / "blind/kept_commitments.json").read_bytes()
    assert not (root / "score").exists()


@pytest.mark.parametrize("phase", ["prepass_inputs", "complete_cases"])
def test_consumed_phase_is_never_retried_or_adopted(study, phase):
    start(study)
    supplied = native(study) if phase == "complete_cases" else None
    if supplied:
        finish(study, supplied)
    before = {str(p.relative_to(study["root"])): p.read_bytes() for p in study["root"].rglob("*") if p.is_file()}
    with pytest.raises(pub.NotReady):
        finish(study, supplied) if supplied else start(study)
    assert before == {str(p.relative_to(study["root"])): p.read_bytes() for p in study["root"].rglob("*") if p.is_file()}


@pytest.mark.parametrize("mutation", ["status", "schema", "authorizer", "source", "missing_pin", "unknown_pin", "bad_pin", "extra", "expired", "future", "hash_unknown"])
def test_invalid_go_never_starts(study, mutation):
    pins = pub.input_pins(study["args"], preregistration=study["prereg"])
    value = json.loads(authority(study, "prepass_inputs", pins))
    if mutation in ("status", "schema"):
        value[mutation] = "PROPOSED"
    elif mutation == "authorizer":
        value["authorizer"]["session_id"] = "other"
    elif mutation == "source":
        value["source_sha256"] = "0" * 64
    elif mutation == "missing_pin":
        value["pins"].pop(next(iter(pins)))
    elif mutation == "unknown_pin":
        value["pins"]["unknown"] = "0" * 64
    elif mutation == "bad_pin":
        value["pins"][next(iter(pins))] = "0" * 64
    elif mutation == "extra":
        value["fallback"] = True
    elif mutation == "expired":
        value["expires_at_utc"] = "2035-04-06T06:00:05Z"
    elif mutation == "future":
        value["issued_at_utc"] = "2035-04-06T06:00:06Z"
    else:
        value["source_sha256"] = None
    with pytest.raises(pub.NotReady):
        start(study, go=raw(value))
    assert not (study["root"] / "private").exists()


def test_partial_io_consumes_and_halts_without_private_error_text(study, monkeypatch):
    original = pub.capture._exclusive_write
    def broken(path, data):
        if path.name == "capsule.json":
            path.write_bytes(b"partial")
            raise OSError("SYNTHETIC_PRIVATE_SENTINEL")
        return original(path, data)
    monkeypatch.setattr(pub.capture, "_exclusive_write", broken)
    with pytest.raises(pub.NotReady) as error:
        start(study)
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in str(error.value)
    assert (study["root"] / "private/prepass_inputs/INTENT.json").exists()
    assert (study["root"] / "private/HALTED.json").exists()
    assert (study["root"] / "private/prepass_inputs/original/capsule.json").read_bytes() == b"partial"
    with pytest.raises(pub.NotReady):
        start(study)


def test_existing_collection_bytes_count_toward_shared_budget(study, monkeypatch):
    (study["root"] / "collection").mkdir()
    (study["root"] / "collection/existing.bin").write_bytes(b"x" * 40000)
    monkeypatch.setattr(pub, "BUDGET_BYTES", 45000)
    with pytest.raises(pub.NotReady):
        start(study)
    assert (study["root"] / "collection/existing.bin").stat().st_size == 40000


def test_corrupt_prior_publication_consumes_completion_without_constructing(study, monkeypatch):
    start(study)
    supplied = native(study)
    (study["root"] / "blind/runner_cases.json").write_bytes(b"corrupt")
    calls = []
    monkeypatch.setattr(pub.pipe, "complete_cases", lambda *a, **k: calls.append(1))
    with pytest.raises(pub.NotReady):
        finish(study, supplied)
    assert calls == []
    assert (study["root"] / "private/complete_cases/INTENT.json").exists()


@pytest.mark.parametrize("field", ["native_manifest", "prepared_receipts", "native_prepass", "native_prepare_receipt", "native_prepass_receipt"])
def test_corrupt_native_original_refuses_before_case_construction(study, monkeypatch, field):
    start(study)
    supplied = native(study)
    supplied[field] += b" "
    calls = []
    monkeypatch.setattr(pub.pipe, "complete_cases", lambda *a, **k: calls.append(1))
    with pytest.raises(pub.NotReady):
        finish(study, supplied)
    assert calls == []


def test_score_release_is_explicitly_not_implemented_even_with_invented_hashes(study):
    with pytest.raises(pub.NotReady, match="score_release_contract_unknown"):
        pub.release_score_package(arm_a_sha256="a" * 64, arm_b_sha256="b" * 64)
    assert not (study["root"] / "score").exists()


def test_all_required_original_hashes_are_explicit_not_optional(study):
    for key in ("protocol_sha256", "capsule_sha256", "collector_counts_sha256", "runner_binding_sha256",
                "runner_design_sha256", "adapter_source_sha256"):
        args = dict(study["args"], **{key: None})
        with pytest.raises(pub.NotReady):
            pub.input_pins(args, preregistration=study["prereg"])
    with pytest.raises(pub.NotReady):
        go = authority(study, "prepass_inputs", pub.input_pins(study["args"], preregistration=study["prereg"]))
        pub.publish_prepass_inputs(study["args"], preregistration=study["prereg"], go=go, go_sha256=None, execute=True)


@pytest.mark.parametrize("which", ["protocol", "prereg"])
def test_proposed_documents_are_not_authority(study, which):
    if which == "protocol":
        value = json.loads(study["protocol"])
        value["status"] = "PRELOCK"
        study["protocol"] = raw(value)
        b = study["args"]["bindings"]
        study["args"]["bindings"] = pub.pipe.cases.Bindings(b.capsule_json, study["protocol"])
        study["args"]["protocol_sha256"] = sha(study["protocol"])
    else:
        value = json.loads(study["prereg"])
        value["status"] = "PROPOSED"
        study["prereg"] = raw(value)
    with pytest.raises(pub.NotReady):
        start(study)
    assert not (study["root"] / "private").exists()


@pytest.mark.parametrize("target", ["blind", "baseline", "native"])
def test_preexisting_effect_tree_is_never_adopted(study, target):
    tree = study["root"] / target
    tree.mkdir()
    (tree / "synthetic-existing.bin").write_bytes(b"preserve")
    with pytest.raises(pub.NotReady):
        start(study)
    assert (tree / "synthetic-existing.bin").read_bytes() == b"preserve"
    assert (study["root"] / "private/prepass_inputs/INTENT.json").exists()
    assert not (study["root"] / "private/prepass_inputs/RESULT.json").exists()


def test_hardlinked_existing_collection_file_refuses(study):
    original = study["root"] / "synthetic-one.bin"
    original.write_bytes(b"invented")
    os.link(original, study["root"] / "synthetic-two.bin")
    with pytest.raises(pub.NotReady):
        start(study)
    assert not (study["root"] / "private").exists()


def test_reparse_flagged_existing_path_refuses(study, monkeypatch):
    path = study["root"] / "synthetic-reparse.bin"
    path.write_bytes(b"invented")
    real = Path.lstat
    class Reparse:
        st_file_attributes = stat.FILE_ATTRIBUTE_REPARSE_POINT
        st_mode = stat.S_IFREG
        st_nlink = 1
    monkeypatch.setattr(Path, "lstat", lambda p: Reparse() if p == path else real(p))
    with pytest.raises(pub.NotReady):
        start(study)
    assert not (study["root"] / "private").exists()


def test_insufficient_free_space_consumes_without_claiming_complete(study, monkeypatch):
    monkeypatch.setattr(pub.capture, "_space", lambda _: (_ for _ in ()).throw(pub.capture.CaptureError("free_space_insufficient")))
    with pytest.raises(pub.NotReady, match="terminal_receipt_unavailable"):
        start(study)
    assert (study["root"] / "private/prepass_inputs").is_dir()
    assert not (study["root"] / "private/prepass_inputs/RESULT.json").exists()


def test_backward_clock_consumes_even_when_every_byte_hash_is_valid(study, monkeypatch):
    count = iter(["2035-04-06T06:00:05Z", "2035-04-06T06:00:04Z"])
    previous = ["2035-04-06T06:00:04Z"]
    def now():
        previous[0] = next(count, previous[0])
        return pub.capture.parse_utc(previous[0])
    monkeypatch.setattr(pub, "_now", now)
    with pytest.raises(pub.NotReady, match="clock"):
        start(study)
    assert (study["root"] / "private/prepass_inputs").exists()
    assert not (study["root"] / "private/prepass_inputs/RESULT.json").exists()


def test_terminal_receipt_failure_is_explicit_and_partial_intent_still_consumes(study, monkeypatch):
    original = pub.capture._exclusive_write
    def broken(path, body):
        if path.name != "INTENT.json":
            raise OSError("SYNTHETIC_PRIVATE_SENTINEL")
        return original(path, body)
    monkeypatch.setattr(pub.capture, "_exclusive_write", broken)
    with pytest.raises(pub.NotReady, match="terminal_receipt_unavailable"):
        start(study)
    assert (study["root"] / "private/prepass_inputs/INTENT.json").exists()


def test_unknown_native_result_shape_never_calls_complete_cases(study, monkeypatch):
    start(study)
    supplied = native(study)
    value = json.loads(supplied["native_prepass_receipt"])
    value["future_automatic_score_release"] = True
    supplied["native_prepass_receipt"] = raw(value)
    (study["root"] / "native/prepass/RESULT.json").write_bytes(supplied["native_prepass_receipt"])
    calls = []
    monkeypatch.setattr(pub.pipe, "complete_cases", lambda *a, **k: calls.append(1))
    with pytest.raises(pub.NotReady):
        finish(study, supplied)
    assert calls == []


def test_native_manifest_and_runner_design_are_separate_original_domains(study):
    assert study["args"]["runner_design_sha256"] == sha(study["protocol"])
    assert sha(study["manifest"]) != sha(study["protocol"])
    start(study)
    assert finish(study)["status"] == "COMPLETE"


@pytest.mark.parametrize("mutation", ["outside_audit", "overlap", "alias", "unknown_source_pin"])
def test_fixed_three_tree_paths_and_source_pin_schema_refuse(study, mutation):
    protocol = json.loads(study["protocol"])
    config = protocol["publisher"]
    if mutation == "outside_audit":
        config["root"] = "C:/synthetic-not-audit"
        config["paths"] = {role: config["root"] + "/" + role for role in ("private", "blind", "score")}
    elif mutation == "overlap":
        config["paths"]["blind"] = config["paths"]["private"]
    elif mutation == "alias":
        config["paths"]["blind"] = str(study["root"] / "not-blind")
    else:
        config["fallback_source_pin"] = "f" * 64
    study["protocol"] = raw(protocol)
    b = study["args"]["bindings"]
    study["args"]["bindings"] = pub.pipe.cases.Bindings(b.capsule_json, study["protocol"])
    study["args"]["protocol_sha256"] = sha(study["protocol"])
    study["args"]["runner_design_sha256"] = sha(study["protocol"])
    with pytest.raises(pub.NotReady):
        start(study)
    assert not (study["root"] / "private").exists()


@pytest.mark.parametrize("tree", ["blind", "baseline"])
def test_unrecognized_extra_effect_file_refuses_before_nonce_generation(study, monkeypatch, tree):
    start(study)
    supplied = native(study)
    (study["root"] / tree / "synthetic-unknown.bin").write_bytes(b"unrecognized")
    calls = []
    monkeypatch.setattr(pub.pipe, "complete_cases", lambda *a, **k: calls.append(1))
    with pytest.raises(pub.NotReady):
        finish(study, supplied)
    assert calls == []


def test_complete_cases_called_once_only_after_all_native_receipts_validate(study, monkeypatch):
    start(study)
    calls = []
    original = pub.pipe.complete_cases
    def counted(*a, **k):
        calls.append(1)
        return original(*a, **k)
    monkeypatch.setattr(pub.pipe, "complete_cases", counted)
    assert finish(study)["status"] == "COMPLETE"
    assert calls == [1]


def test_new_concurrent_file_is_not_silently_adopted(study, monkeypatch):
    original = pub.capture._exclusive_write
    added = []
    def write(path, body):
        original(path, body)
        if path.name == "INTENT.json" and not added:
            added.append(1)
            (study["root"] / "synthetic-concurrent.bin").write_bytes(b"unrecognized")
    monkeypatch.setattr(pub.capture, "_exclusive_write", write)
    with pytest.raises(pub.NotReady):
        start(study)
    assert (study["root"] / "private/prepass_inputs/INTENT.json").is_file()
    assert not (study["root"] / "private/prepass_inputs/RESULT.json").exists()


def test_corrupt_write_readback_is_never_complete(study, monkeypatch):
    original = pub.capture._exclusive_write
    def write(path, body):
        original(path, body)
        if path.name == "capsule.json":
            path.write_bytes(b"corrupt")
    monkeypatch.setattr(pub.capture, "_exclusive_write", write)
    with pytest.raises(pub.NotReady):
        start(study)
    assert not (study["root"] / "private/prepass_inputs/RESULT.json").exists()


def test_native_arms_must_not_precede_case_completion(study, monkeypatch):
    start(study)
    supplied = native(study)
    (study["root"] / "native/arms").mkdir()
    calls = []
    monkeypatch.setattr(pub.pipe, "complete_cases", lambda *a, **k: calls.append(1))
    with pytest.raises(pub.NotReady):
        finish(study, supplied)
    assert calls == []


def test_unknown_native_manifest_contract_refuses(study, monkeypatch):
    start(study)
    supplied = native(study)
    manifest = json.loads(supplied["native_manifest"])
    manifest["future_release_contract"] = {"auto": True}
    supplied["native_manifest"] = raw(manifest)
    calls = []
    monkeypatch.setattr(pub.pipe, "complete_cases", lambda *a, **k: calls.append(1))
    with pytest.raises(pub.NotReady):
        finish(study, supplied)
    assert calls == []


def test_expiry_during_terminal_write_is_halted_not_returned_complete(study, monkeypatch):
    original = pub.capture._exclusive_write
    clock = ["2035-04-06T06:00:05Z"]
    monkeypatch.setattr(pub, "_now", lambda: pub.capture.parse_utc(clock[0]))
    def write(path, body):
        original(path, body)
        if path.name == "RESULT.json":
            clock[0] = "2035-04-06T06:15:00Z"
    monkeypatch.setattr(pub.capture, "_exclusive_write", write)
    with pytest.raises(pub.NotReady, match="clock"):
        start(study)
    assert (study["root"] / "private/HALTED.json").is_file()
    assert (study["root"] / "private/prepass_inputs/STOP_NOT_READY.json").is_file()
    # Nothing is erased. HALTED dominates this retained late receipt.
    assert (study["root"] / "private/prepass_inputs/RESULT.json").is_file()


def test_malformed_private_evidence_does_not_escape_in_error_or_blind_files(study):
    entry = study["args"]["entries"][0]
    body = b'{"SYNTHETIC_PRIVATE_SENTINEL":'
    study["args"]["entries"][0] = pub.pipe.cases.CaseInput(entry.incident_json, entry.incident_sha256, body, sha(body))
    with pytest.raises(pub.NotReady) as error:
        start(study)
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in str(error.value)
    assert not (study["root"] / "blind").exists()


def test_existing_files_are_os_pinned_during_publication(study, monkeypatch):
    original = pub.capture._exclusive_write
    denied = []
    def write(path, body):
        if path.name == "INTENT.json":
            with pytest.raises(PermissionError):
                (study["root"] / "OWNER.json").write_bytes(b"overwrite")
            denied.append(1)
        original(path, body)
    monkeypatch.setattr(pub.capture, "_exclusive_write", write)
    assert start(study)["status"] == "COMPLETE" and denied == [1]


def test_direct_child_global_control_is_supported_not_forced_inside_study(study):
    protocol = json.loads(study["protocol"])
    study["protocol"] = raw(protocol)
    args = study["args"]
    b = args["bindings"]
    args["bindings"] = pub.pipe.cases.Bindings(b.capsule_json, study["protocol"])
    args["protocol_sha256"] = args["runner_design_sha256"] = sha(study["protocol"])
    pins = pub.input_pins(args, preregistration=study["prereg"])
    go = authority(study, "prepass_inputs", pins)
    assert pub._authority(args, study["prereg"], go, sha(go), "prepass_inputs", pins)[0] == study["root"]


@pytest.mark.parametrize("part", ["intent", "seal", "blob"])
def test_original_captured_baseline_is_verified_read_only_not_adopted(study, part):
    root = study["root"] / "captured_baseline"
    path = root / {"intent": "intent.json", "seal": "SEALED.json"}.get(part, "blobs/" + study["args"]["baseline"].blobs[0][0])
    path.write_bytes(b"corrupt-synthetic")
    with pytest.raises(pub.NotReady):
        start(study)
    assert path.read_bytes() == b"corrupt-synthetic"
    assert (study["root"] / "private/prepass_inputs/INTENT.json").is_file()
    assert not (study["root"] / "blind").exists()


def test_budget_scan_counts_disjoint_control_and_ledger_siblings(study):
    (study["control"] / "synthetic-control.bin").write_bytes(b"c" * 12000)
    (study["ledger"] / "synthetic-ledger.bin").write_bytes(b"l" * 23000)
    with contextlib.ExitStack() as stack:
        disk = pub._Disk(study["root"], stack, (study["root"], study["control"], study["ledger"]))
        actual = disk.scan()
    expected = sum(p.stat().st_size for tree in (study["root"], study["control"], study["ledger"]) for p in tree.rglob("*") if p.is_file())
    assert actual == expected


def test_generic_control_ledger_payloads_are_not_read_for_budgeting(study, monkeypatch):
    for tree in (study["control"], study["ledger"]):
        (tree / "synthetic-private.bin").write_bytes(b"INVENTED_UNNEEDED_PAYLOAD")
    original = pub.capture._read_bounded
    def read(path, *args, **kwargs):
        assert not any(tree in path.parents for tree in (study["control"], study["ledger"]))
        return original(path, *args, **kwargs)
    monkeypatch.setattr(pub.capture, "_read_bounded", read)
    assert start(study)["status"] == "COMPLETE"


def test_shared_budget_refuses_bytes_in_global_control_and_ledger(study, monkeypatch):
    (study["control"] / "synthetic-control.bin").write_bytes(b"c" * 23000)
    (study["ledger"] / "synthetic-ledger.bin").write_bytes(b"l" * 23000)
    monkeypatch.setattr(pub, "BUDGET_BYTES", 45000)
    with pytest.raises(pub.NotReady, match="budget"):
        start(study)
    assert not (study["root"] / "private/prepass_inputs/RESULT.json").exists()
