"""Source-only invented bytes; no real collector/native/model access."""
import json
import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

from sandbox.mind.measurement_tools import prospective_case_cli as cli
from sandbox.mind.measurement_tools import prospective_publish as pub
from sandbox.mind.measurement_tools import prospective_collection as coll
from sandbox.mind.measurement_tools import prospective_incidents as rec
from sandbox.mind.measurement_tools.test_prospective_publish import study as _publisher_study
from sandbox.mind.measurement_tools.test_prospective_capture import records


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def sha(value):
    return hashlib.sha256(value).hexdigest()


MAPPING = {"schema": "schema", "unsupported_native_schema": "schema", "unsupported_native_record": "schema",
           "unsupported_resolution": "time", "outside_window": "time", "empty_context": "schema", "duplicate": "duplicate"}


def fake_identity():
    return {"integrity_rid": 8192, "elevated": False, "ABI_bits": 64, "python": {"sha256": cli.native.PYTHON_SHA256},
            "pid": 12345, "creation_filetime_100ns": "123456789", "executable": "C:/synthetic/python.exe"}


@pytest.fixture
def study(monkeypatch):
    # Reuse invented bodies, but keep ALL new temporary fixture writes below
    # this author's current allowlisted audit tree, not the older publisher lane.
    author_audit = Path("C:/Python/project2/.codex-audit/m1-p3-prospective-20261020/case-cli-author/synthetic")
    author_audit.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(pub.capture, "AUDIT_ROOT", author_audit)
    yield from _publisher_study.__wrapped__(monkeypatch)


@pytest.fixture
def synthetic(study):
    root = study["root"]
    p = json.loads(study["protocol"])
    oldcap = json.loads(study["args"]["bindings"].capsule_json)
    p["case_parameters"] = {key: oldcap[key] for key in ("timestamp_resolution_us", "baseline_resolution_us", "max_record_delay_seconds",
        "minimum_n", "cap_n", "seed", "source_commit", "arm_a_version", "arm_b_sha256", "grader_sha256", "evidence_source_sha256", "privacy_source_sha256")}
    p["case_cli"] = {"path": str(Path(cli.__file__)), "sha256": sha(Path(cli.__file__).read_bytes())}
    p["collector_refusal_mapping"] = dict(MAPPING)
    source_dir = root.parent / "native-synthetic"
    source_dir.mkdir()
    source_path = source_dir / "invented.jsonl"
    session = p["reporter_sources"][0]["native_session_id"]
    rows = records()
    for row, stamp in zip(rows, ("2035-04-05T06:00:03.000Z", "2035-04-05T06:00:04.000Z", "2035-04-05T06:01:00.123Z")):
        row.update(sessionId=session, timestamp=stamp)
    prefix = b"".join(raw(row) + b"\n" for row in rows)
    source_path.write_bytes(prefix)
    info = source_path.stat()
    source = {"id": "c" * 32, "schema": "wd.m1-p3-claude-jsonl.v1", "reporter": p["reporter_sources"][0]["reporter"],
        "native_session_id": session, "evidence_path": str(source_path), "file_identity": {"device": info.st_dev, "inode": info.st_ino},
        "initial_cursor": 0, "max_range_bytes": 2 * 1024 * 1024, "timestamp_resolution_us": 1000}
    p["collection_controller"]["sources"] = [source]
    p["collection_controller"]["allowed_reporters"] = [source["reporter"]]
    protocol = raw(p)
    manifest = json.loads(study["args"]["baseline"].manifest_json)
    manifest["identity"]["protocol_sha256"] = sha(protocol)
    manifest = raw(manifest)
    seal = json.loads(study["args"]["baseline"].seal_json)
    seal["manifest_sha256"] = sha(manifest)
    seal = raw(seal)
    capture = root / "captured_baseline"
    (capture / "manifest.json").write_bytes(manifest)
    (capture / "SEALED.json").write_bytes(seal)
    (capture / "intent.json").write_bytes(raw({"schema": "wd.m1-p3-capture-intent.v1", "identity": json.loads(manifest)["identity"],
        "started_utc": json.loads(manifest)["started_utc"], "attempt_consumed": True}))
    (root / "OWNER.json").write_bytes(raw({"schema": "wd.m1-p3-study-root.v1", "study_id": p["study_id"], "protocol_sha256": sha(protocol), "root": str(root)}))
    cfg = p["collection_controller"]
    control, ledger = Path(cfg["control_root"]), Path(cfg["ledger_root"])
    pc, bc, mapping, _ = coll.capsules(p, {"protocol_sha256": sha(protocol), "baseline_manifest_sha256": sha(manifest)}, cfg,
                                     json.loads(manifest), json.loads(seal), rec, pub.capture)
    for name, body in (("protocol-capsule.json", pc), ("baseline-capsule.json", bc), ("lesson-mapping.json", raw(mapping)), ("LOCK", b"0")):
        (control / name).write_bytes(body)
    (control / "intent.json").write_bytes(raw({"schema": "wd.m1-p3-collection-intent.v1", "attempt_consumed": True,
        "study_id": p["study_id"], "protocol_sha256": sha(protocol), "go_sha256": "e" * 64,
        "baseline_manifest_sha256": sha(manifest), "started_utc": "2035-04-05T06:00:01.000000Z"}))
    reference = {"session_id": session, "uuid": rows[-1]["uuid"], "tool_use_id": "toolu_synthetic1",
        "record_sha256": sha(raw(rows[-1])), "action_utc": rows[-1]["timestamp"]}
    evidence = pub.capture.extract_action_evidence(prefix, reference=reference, baseline_utc=json.loads(seal)["sealed_utc"],
        collection_start_utc=cfg["collection_start_utc"], collection_end_utc=cfg["collection_end_utc"], timestamp_resolution_us=1000)
    incident = json.loads(study["args"]["entries"][0].incident_json)
    incident.update(protocol_sha256=sha(protocol), baseline_sha256=sha(manifest), action_sha256=evidence["record_sha256"],
        action_ref=sha(raw({key: evidence[key] for key in ("session_id", "uuid", "tool_use_id")}))[:32])
    ar = raw({"schema": "wd.prospective-action.v1", "action_ref": incident["action_ref"], "reporter": incident["reporter"],
        "action_time_utc": incident["action_time_utc"], "action_time_resolution_seconds": 1, "source_record_sha256": evidence["record_sha256"]})
    incident["action_receipt_sha256"] = sha(ar)
    incident = raw(incident)
    (ledger / "LOCK").write_bytes(b"0")
    (ledger / "ledger.json").write_bytes(raw({"schema": "wd.prospective-ledger.v1", "run_id": "f" * 32, "study_id": p["study_id"],
        "protocol_sha256": sha(protocol), "baseline_sha256": sha(manifest), "protocol_capsule_sha256": sha(pc), "baseline_capsule_sha256": sha(bc)}))
    cid = json.loads(incident)["incident_id"]
    (ledger / ("incident-" + cid + ".json")).write_bytes(incident)
    receipt = {"schema": "wd.m1-p3-source-range.v1", "source_id": source["id"], "cursor_before": 0, "overlap_bytes": 0,
        "lookback_validation": None, "previous_range_receipt_sha256": None, "file_identity": source["file_identity"], "start": 0,
        "end": len(prefix), "source_size": len(prefix), "read_bytes": len(prefix), "range_sha256": sha(prefix)}
    (control / "source-range-00000001.json").write_bytes(raw(receipt))
    proof = {key: evidence[key] for key in ("schema", "session_id", "uuid", "tool_use_id", "record_sha256", "action_utc", "action_latest_utc",
        "timestamp_resolution_us", "supplied_jsonl_sha256")}
    proof.update(evidence_file=source_path.name, source_id=source["id"], source_range={**receipt, "receipt_sha256": sha(raw(receipt)), "replay": False,
        "cursor_after": len(prefix)}, native_record_dispositions=pub.capture.native_record_dispositions(prefix), action_receipt_sha256=sha(ar), record_sha256_receipt=sha(incident))
    counts = {"reported": 1, "accepted": 1, "primary_candidates": 1, "near_miss": 0, "refused": 0, "duplicates": 0}
    event = raw({"schema": "wd.m1-p3-collection-event.v1", "sequence": 1, "status": {"disposition": "RECORDED", "reason": None},
        "nomination_sha256": "d" * 64, "incident_id": cid, "proof": proof, "counts": counts})
    (control / "event-00000001.json").write_bytes(event)
    terminal = raw({"schema": "wd.m1-p3-collection-result.v1", "status": "CLOSED", "reason": "collection_closed", "counts": counts,
        "journal_sha256": sha(raw([sha(event)]))})
    (control / "RESULT.json").write_bytes(terminal)
    study.update(protocol=protocol, manifest=manifest, seal=seal, terminal=terminal, native_path=source_path, prefix=prefix,
        evidence=raw(evidence), incident=incident, control=control, ledger=ledger)
    return study


def seal_go(s, **changes):
    p = json.loads(s["protocol"])
    value = {"schema": "wd.m1-p3-case-cli-go.v1", "status": "CASE_INPUT_SEAL_GO", "phase": "seal_inputs", "study_id": p["study_id"],
        "protocol_sha256": sha(s["protocol"]), "preregistration_sha256": sha(s["prereg"]), "source_sha256": sha(Path(cli.__file__).read_bytes()),
        "authorizer": p["publisher_authorizer"], "event_ref": "1" * 64, "issued_at_utc": "2035-04-06T06:00:00Z", "expires_at_utc": "2035-04-06T06:15:00Z",
        "pins": {"collection_result_sha256": sha(s["terminal"]), "journal_sha256": json.loads(s["terminal"])["journal_sha256"],
            "baseline_manifest_sha256": sha(s["manifest"]), "baseline_seal_sha256": sha(s["seal"]), "runner_binding_sha256": sha(s["args"]["runner_binding"])}}
    value.update(changes)
    return raw(value)


def seal(s, **kwargs):
    go = kwargs.pop("go", seal_go(s))
    kwargs.setdefault("_test_identity", fake_identity)
    return cli.seal_inputs(s["protocol"], protocol_sha256=sha(s["protocol"]), preregistration=s["prereg"], preregistration_sha256=sha(s["prereg"]),
        runner_binding=s["args"]["runner_binding"], runner_binding_sha256=sha(s["args"]["runner_binding"]), go=go, go_sha256=sha(go), execute=True, **kwargs)


def test_functionality_seal_and_reload_same_evidence_once(synthetic, monkeypatch):
    calls = []
    original = pub.capture.extract_action_evidence
    monkeypatch.setattr(pub.capture, "extract_action_evidence", lambda *a, **k: calls.append(1) or original(*a, **k))
    receipt = seal(synthetic)
    assert receipt["status"] == "COMPLETE" and calls == [1]
    monkeypatch.setattr(pub.capture, "extract_action_evidence", lambda *a, **k: pytest.fail("must not re-extract"))
    loaded = cli.load_sealed_inputs(synthetic["protocol"], protocol_sha256=sha(synthetic["protocol"]),
        preregistration=synthetic["prereg"], preregistration_sha256=sha(synthetic["prereg"]), sealed_result_sha256=receipt["result_sha256"], _test_identity=fake_identity)
    assert loaded["entries"][0].evidence_json == synthetic["evidence"]
    assert loaded["entries"][0].incident_json == synthetic["incident"]
    request = json.loads((synthetic["root"] / "private/case_cli/seal_inputs/PUBLISH_REQUEST.json").read_bytes())
    assert request["publisher_pins"] == pub.input_pins(loaded, preregistration=synthetic["prereg"])
    assert b'"situation"' not in raw(request) and b'"nonce"' not in raw(request)


def test_dormant_cli_never_reads_even_explicit_paths(monkeypatch, capsys):
    reads = []
    monkeypatch.setattr(cli, "_read", lambda *a, **k: reads.append(a), raising=False)
    assert cli.main(["--phase", "prepass_inputs"]) == 2
    assert reads == []
    assert json.loads(capsys.readouterr().out)["status"] == "STOP_NOT_READY"


def test_unknown_phase_and_unknown_option_are_sanitized(capsys):
    assert cli.main(["--execute", "--phase", "SYNTHETIC_PRIVATE_SENTINEL"]) == 2
    text = capsys.readouterr().out
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in text


def test_cannot_release_score_or_invoke_worker_from_this_cli(capsys):
    assert cli.main(["--execute", "--phase", "score"]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "STOP_NOT_READY"


def test_no_private_read_before_missing_consumed_input_seal_transition(monkeypatch, capsys):
    reads = []
    def read(*args, **kwargs):
        reads.append(args)
        raise OSError("SYNTHETIC_PRIVATE_SENTINEL")
    monkeypatch.setattr(cli, "_read", read, raising=False)
    args = ["--execute", "--phase", "prepass_inputs"]
    for field in ("protocol", "protocol-sha256", "preregistration", "preregistration-sha256", "go", "go-sha256",
                  "runner-binding", "runner-binding-sha256", "collection-result-sha256"):
        args.extend(["--" + field, "SYNTHETIC_DECLARED_VALUE"])
    assert cli.main(args) == 2
    assert reads == []
    assert json.loads(capsys.readouterr().out)["reason"] in ("schema", "go")


def load(s, receipt):
    return cli.load_sealed_inputs(s["protocol"], protocol_sha256=sha(s["protocol"]), preregistration=s["prereg"],
        preregistration_sha256=sha(s["prereg"]), sealed_result_sha256=receipt["result_sha256"], _test_identity=fake_identity)


def add_refusals(s, reasons):
    control = s["control"]
    counts = dict(json.loads(s["terminal"])["counts"])
    hashes = [sha((control / "event-00000001.json").read_bytes())]
    for sequence, reason in enumerate(reasons, 2):
        counts["reported"] += 1
        counts["refused"] += 1
        counts["duplicates"] += int(reason == "duplicate")
        body = raw({"schema": "wd.m1-p3-collection-event.v1", "sequence": sequence,
            "status": {"disposition": "REFUSE_CASE", "reason": reason}, "nomination_sha256": "c" * 64,
            "incident_id": None, "proof": None, "counts": dict(counts)})
        (control / f"event-{sequence:08d}.json").write_bytes(body)
        hashes.append(sha(body))
    terminal = json.loads(s["terminal"])
    terminal.update(counts=counts, journal_sha256=sha(raw(hashes)))
    s["terminal"] = raw(terminal)
    (control / "RESULT.json").write_bytes(s["terminal"])


def test_full_seven_reason_mapping_and_original_provenance(synthetic):
    add_refusals(synthetic, MAPPING)
    receipt = seal(synthetic)
    args = load(synthetic, receipt)
    mapping = json.loads((synthetic["root"] / "private/case_cli/seal_inputs/REFUSAL_MAPPING.json").read_bytes())
    assert mapping["original_refusals_by_reason"] == {k: 1 for k in MAPPING}
    assert mapping["original_counts"]["refused"] == 7
    assert json.loads(args["collector_counts"])["refusals_by_reason"] == {"schema": 4, "time": 2, "duplicate": 1, "size": 0}
    assert len(mapping["original_event_sha256"]) == 8


def test_private_payload_reads_only_after_consumed_intent(synthetic, monkeypatch):
    original = cli._read
    private = (synthetic["control"], synthetic["ledger"], synthetic["root"] / "captured_baseline")
    observed = []
    def read(path, *args, **kw):
        if any(tree in Path(path).parents for tree in private):
            assert (synthetic["root"] / "private/case_cli/seal_inputs/INTENT.json").is_file()
            observed.append(path)
        return original(path, *args, **kw)
    monkeypatch.setattr(cli, "_read", read)
    assert seal(synthetic)["status"] == "COMPLETE" and observed


@pytest.mark.parametrize("kind", ["status", "unknown", "missing_hash", "authorizer", "source", "expired", "future"])
def test_invalid_seal_go_has_no_private_reads_or_effects(synthetic, monkeypatch, kind):
    value = json.loads(seal_go(synthetic))
    if kind == "status": value["status"] = "PROPOSED"
    elif kind == "unknown": value["fallback"] = True
    elif kind == "missing_hash": value["pins"].pop("journal_sha256")
    elif kind == "authorizer": value["authorizer"]["session_id"] = "other"
    elif kind == "source": value["source_sha256"] = "0" * 64
    elif kind == "expired": value["expires_at_utc"] = "2035-04-06T06:00:05Z"
    else: value["issued_at_utc"] = "2035-04-06T06:00:06Z"
    with pytest.raises(pub.NotReady): seal(synthetic, go=raw(value))
    assert not (synthetic["root"] / "private/case_cli").exists()


def test_once_consumed_no_retry_adopt_or_new_extraction(synthetic, monkeypatch):
    receipt = seal(synthetic)
    before = {str(x): x.read_bytes() for x in (synthetic["root"] / "private/case_cli").rglob("*") if x.is_file()}
    monkeypatch.setattr(pub.capture, "extract_action_evidence", lambda *a, **k: pytest.fail("retry extraction"))
    with pytest.raises(pub.NotReady, match="consumed"): seal(synthetic)
    assert before == {str(x): x.read_bytes() for x in (synthetic["root"] / "private/case_cli").rglob("*") if x.is_file()}


@pytest.mark.parametrize("part", ["journal", "range", "ledger", "native", "identity", "extra_ledger", "extra_control"])
def test_corrupt_source_is_consumed_and_never_sealed(synthetic, part):
    if part == "journal": path = synthetic["control"] / "event-00000001.json"
    elif part == "range": path = synthetic["control"] / "source-range-00000001.json"
    elif part == "ledger": path = next(synthetic["ledger"].glob("incident-*.json"))
    elif part == "native": path = synthetic["native_path"]
    elif part == "identity":
        path = synthetic["native_path"]
        original = path.read_bytes()
        path.unlink()
        path.write_bytes(original)
    elif part == "extra_ledger": path = synthetic["ledger"] / "incident-ffffffffffffffffffffffffffffffff.json"
    else: path = synthetic["control"] / "event-00000002.json"
    if part != "identity": path.write_bytes(b"SYNTHETIC_PRIVATE_CORRUPTION")
    with pytest.raises(pub.NotReady) as error: seal(synthetic)
    assert "SYNTHETIC_PRIVATE_CORRUPTION" not in str(error.value)
    stage = synthetic["root"] / "private/case_cli/seal_inputs"
    assert (stage / "INTENT.json").is_file() and not (stage / "RESULT.json").exists()
    assert (synthetic["root"] / "private/case_cli/HALTED.json").is_file()


def test_journal_chain_verified_before_any_native_extraction(synthetic, monkeypatch):
    path = synthetic["control"] / "event-00000001.json"
    value = json.loads(path.read_bytes())
    value["proof"]["uuid"] = "00000000-0000-4000-8000-000000000099"
    path.write_bytes(raw(value))
    monkeypatch.setattr(pub.capture, "extract_action_evidence", lambda *a, **k: pytest.fail("unauthenticated event extraction"))
    with pytest.raises(pub.NotReady): seal(synthetic)


def test_unknown_nonzero_reason_refuses_without_fallback(synthetic):
    add_refusals(synthetic, ["future_unknown"])
    with pytest.raises(pub.NotReady): seal(synthetic)
    assert not (synthetic["root"] / "private/case_cli/seal_inputs/RESULT.json").exists()


def test_partial_io_is_consumed_and_terminal_failure_explicit(synthetic, monkeypatch):
    original = pub.capture._exclusive_write
    def write(path, body):
        if path.name == "capsule.json":
            path.write_bytes(b"partial")
            raise OSError("SYNTHETIC_PRIVATE_SENTINEL")
        return original(path, body)
    monkeypatch.setattr(pub.capture, "_exclusive_write", write)
    with pytest.raises(pub.NotReady) as error: seal(synthetic)
    assert error.value.invocation_consumed is True and error.value.actual_data_opened is True
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in str(error.value)
    assert (synthetic["root"] / "private/case_cli/seal_inputs/original/capsule.json").read_bytes() == b"partial"


@pytest.mark.parametrize("part", ["evidence", "request", "mapping", "source_ref", "original_journal", "extra"])
def test_reload_refuses_corrupt_immutable_bytes_without_reextraction(synthetic, monkeypatch, part):
    receipt = seal(synthetic)
    stage = synthetic["root"] / "private/case_cli/seal_inputs"
    path = {"evidence": stage / "original/entries/000000.evidence.json", "request": stage / "PUBLISH_REQUEST.json",
            "mapping": stage / "REFUSAL_MAPPING.json", "source_ref": stage / "SOURCE_REFS.json",
            "original_journal": synthetic["control"] / "event-00000001.json", "extra": stage / "extra.json"}[part]
    path.write_bytes(b"corrupt synthetic")
    monkeypatch.setattr(pub.capture, "extract_action_evidence", lambda *a, **k: pytest.fail("no reload extraction"))
    with pytest.raises(pub.NotReady): load(synthetic, receipt)


def test_native_file_can_grow_after_seal_but_reload_never_reads_it(synthetic, monkeypatch):
    receipt = seal(synthetic)
    with synthetic["native_path"].open("ab") as stream: stream.write(b"not part of sealed prefix\n")
    monkeypatch.setattr(cli, "_source_bytes", lambda *a, **k: pytest.fail("native reread"))
    assert load(synthetic, receipt)["entries"][0].evidence_json == synthetic["evidence"]


def test_isolated_script_entrypoint_is_dormant_without_project_import_path():
    result = subprocess.run([sys.executable, "-I", "-B", "-S", cli.__file__, "--phase", "seal_inputs"], capture_output=True)
    assert result.returncode == 2
    assert json.loads(result.stdout)["actual_data_opened"] is False
    assert result.stderr == b""


def files_for_cli(s, go, phase, receipt=None, native=None):
    config = s["root"].parent / "config"
    config.mkdir(exist_ok=True)
    args = ["--execute", "--phase", phase]
    for name, body in (("protocol", s["protocol"]), ("preregistration", s["prereg"]), ("go", go)):
        path = config / (name + ".json")
        path.write_bytes(body)
        args.extend(["--" + name, str(path), "--" + name + "-sha256", sha(body)])
    if phase == "seal_inputs":
        path = config / "binding.json"
        body = s["args"]["runner_binding"]
        path.write_bytes(body)
        args.extend(["--runner-binding", str(path), "--runner-binding-sha256", sha(body)])
    if receipt:
        args.extend(["--sealed-result-sha256", receipt["result_sha256"]])
    if native:
        path = config / "native-manifest.json"
        path.write_bytes(native)
        args.extend(["--native-manifest", str(path), "--native-manifest-sha256", sha(native)])
    return args


def publisher_go(s, phase, pins):
    p = json.loads(s["protocol"])
    return raw({"schema": "wd.m1-p3-publish-go.v1", "status": "LOCKED_PUBLISH_GO", "study_id": p["study_id"],
        "protocol_sha256": sha(s["protocol"]), "phase": phase, "authorizer": p["publisher_authorizer"], "source_sha256": cli.PUBLISHER_SHA256,
        "pins": pins, "event_ref": "b" * 64, "issued_at_utc": "2035-04-06T06:00:00Z", "expires_at_utc": "2035-04-06T06:15:00Z"})


def fake_native(s, args):
    """Pure fixtures; NEVER imported by/invoked as a native worker."""
    from sandbox.mind.measurement_tools.test_prospective_pipeline import prepass, prepared
    root = s["root"]
    plan = pub.pipe.prepare_prepass(**args)
    manifest = {"schema": "wd.m1-p3-native-manifest.v1", "study_id": json.loads(s["protocol"])["study_id"], "root": str(root),
        "protocol": {"path": str(root / "private/prepass_inputs/original/protocol.json"), "sha256": sha(s["protocol"])},
        "worker": {"path": str(Path(cli.__file__).with_name("prospective_native.py")), "sha256": pub.NATIVE_SHA256},
        "owner": {}, "python": {}, "source_root": "C:/synthetic/source", "source_files": {}, "package_roots": [],
        "cache_root": "C:/synthetic/cache", "cache_files": {}, "environment": {}, "helpers": {}, "stdio_paths": {},
        "corpus_as_of_utc": "2035-04-05T05:59:56Z", "collection_start_utc": "2035-04-05T06:00:00Z", "collection_end_utc": "2035-04-06T06:00:00Z",
        "binding": json.loads(args["runner_binding"]), "inputs": {
            "runner_cases": {"path": str(root / "blind/runner_cases.json"), "sha256": sha(plan.executor_inputs.runner_cases_json)},
            "snapshot": {"path": str(root / "blind/snapshot.json"), "sha256": sha(plan.snapshot_json)},
            "baseline_manifest": {"path": str(root / "baseline/manifest.json"), "sha256": sha(s["manifest"])},
            "baseline_seal": {"path": str(root / "baseline/SEALED.json"), "sha256": sha(s["seal"])},
            "baseline_blobs": {digest: str(root / "baseline/blobs" / (digest + ".bin")) for digest, _ in args["baseline"].blobs},
            "commitments_path": str(root / "blind/kept_commitments.json")}}
    manifest = raw(manifest)
    result = prepass(plan)
    prep = json.loads(prepared(plan))
    for row in prep.values(): row["memory_dir"] = str(root / "prepared/shared/memory")
    prep = raw(prep)
    native = root / "native"
    for folder in (native / "prepare", native / "prepass", native / "journal/prepass"): folder.mkdir(parents=True, exist_ok=True)
    (native / "PREPARED.json").write_bytes(prep)
    (native / "journal/prepass/RESULT.json").write_bytes(result)
    outputs = {}
    for phase, payload, field in (("prepare", prep, "PREPARED"), ("prepass", result, "RESULT.json")):
        retained = {"native/PREPARED.json": {"sha256": sha(prep)}}
        if phase == "prepass": retained["native/journal/prepass/RESULT.json"] = {"sha256": sha(result)}
        outer = raw({"schema": "wd.m1-p3-native-phase.v1", "status": "COMPLETE", "study_id": json.loads(s["protocol"])["study_id"],
            "phase": phase, "manifest_sha256": sha(manifest), "go_sha256": "d" * 64, "invocation_consumed": True, "actual_data_opened": True,
            "SAFE_cases": len(json.loads(plan.executor_inputs.runner_cases_json)), "outputs": {field: {"sha256": sha(payload), "bytes": len(payload)}},
            "retained_outputs": retained, "observations": [], "stdout": {"sha256": "e" * 64, "bytes": 0}, "stderr": {"sha256": "e" * 64, "bytes": 0}, "DLL_closure": "UNKNOWN"})
        (native / phase / "RESULT.json").write_bytes(outer)
        outputs[phase] = outer
    return {"native_manifest": manifest, "prepared_receipts": prep, "native_prepass": result,
            "native_prepare_receipt": outputs["prepare"], "native_prepass_receipt": outputs["prepass"]}


def test_actual_cli_three_phases_delegates_frozen_publisher_without_reextract(synthetic, monkeypatch, capsys):
    # Only injected invented native receipt files; no native code/model invoked.
    assert cli.main(files_for_cli(synthetic, seal_go(synthetic), "seal_inputs"), _test_identity=fake_identity) == 0
    sealed = json.loads(capsys.readouterr().out)
    args = load(synthetic, sealed)
    monkeypatch.setattr(pub.capture, "extract_action_evidence", lambda *a, **k: pytest.fail("no repeated extraction"))
    go = publisher_go(synthetic, "prepass_inputs", pub.input_pins(args, preregistration=synthetic["prereg"]))
    assert cli.main(files_for_cli(synthetic, go, "prepass_inputs", sealed), _test_identity=fake_identity) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "COMPLETE"
    safe = (synthetic["root"] / "blind/runner_cases.json").read_bytes()
    supplied = fake_native(synthetic, args)
    prior = (synthetic["root"] / "private/prepass_inputs/RESULT.json").read_bytes()
    pins = pub.completion_pins(args, preregistration=synthetic["prereg"], prior_result=prior, **supplied)
    go = publisher_go(synthetic, "complete_cases", pins)
    assert cli.main(files_for_cli(synthetic, go, "complete_cases", sealed, supplied["native_manifest"]), _test_identity=fake_identity) == 0
    assert json.loads(capsys.readouterr().out)["scorer_unlocked"] is False
    assert (synthetic["root"] / "blind/runner_cases.json").read_bytes() == safe
    assert not (synthetic["root"] / "score").exists()
    assert (synthetic["root"] / "private/complete_cases/cases.json").exists()


def test_no_plan_cases_or_nonce_construction_during_seal(synthetic, monkeypatch):
    monkeypatch.setattr(pub.pipe, "prepare_prepass", lambda *a, **k: pytest.fail("no duplicate plan"))
    monkeypatch.setattr(pub.pipe, "complete_cases", lambda *a, **k: pytest.fail("no early cases"))
    assert seal(synthetic)["status"] == "COMPLETE"


def test_wrong_publisher_pins_refuse_before_private_reload(synthetic, monkeypatch, capsys):
    sealed = seal(synthetic)
    args = load(synthetic, sealed)
    pins = pub.input_pins(args, preregistration=synthetic["prereg"])
    pins["original/entries/000000.evidence.json"] = "0" * 64
    go = publisher_go(synthetic, "prepass_inputs", pins)
    monkeypatch.setattr(cli, "_load", lambda *a, **k: pytest.fail("bad GO private reload"))
    assert cli.main(files_for_cli(synthetic, go, "prepass_inputs", sealed), _test_identity=fake_identity) == 2
    assert json.loads(capsys.readouterr().out)["actual_data_opened"] is False


def test_actual_medium_gate_required_before_private_inputs(synthetic, monkeypatch):
    monkeypatch.setattr(cli, "_medium_identity", lambda: {"integrity_rid": 12288, "elevated": True}, raising=False)
    with pytest.raises(pub.NotReady): seal(synthetic, _test_identity=None)
    assert not (synthetic["root"] / "private/case_cli").exists()


@pytest.mark.parametrize("known_before_open", [True, False])
def test_first_private_read_failure_does_not_falsely_claim_opened(synthetic, monkeypatch, known_before_open):
    original = cli._read
    def read(path, *args, **kwargs):
        if path == synthetic["control"] / "RESULT.json":
            error = PermissionError("synthetic before-open denial")
            if known_before_open: error.actual_data_opened = False
            raise error
        return original(path, *args, **kwargs)
    monkeypatch.setattr(cli, "_read", read)
    with pytest.raises(pub.NotReady) as failure: seal(synthetic)
    assert failure.value.invocation_consumed is True
    assert failure.value.actual_data_opened is (False if known_before_open else None)


def test_synthetic_token_observation_never_qualifies_real_reload(synthetic, monkeypatch):
    receipt = seal(synthetic)
    result = json.loads((synthetic["root"] / "private/case_cli/seal_inputs/RESULT.json").read_bytes())
    assert result["synthetic_identity"] is True
    identity = json.loads((synthetic["root"] / "private/case_cli/seal_inputs/IDENTITY.json").read_bytes())
    assert identity["logical_actor_qualification"] == "NOT_INFERRED"
    monkeypatch.setattr(cli, "_medium_identity", fake_identity)  # No OS observation.
    with pytest.raises(pub.NotReady, match="source"):
        cli.load_sealed_inputs(synthetic["protocol"], protocol_sha256=sha(synthetic["protocol"]), preregistration=synthetic["prereg"],
            preregistration_sha256=sha(synthetic["prereg"]), sealed_result_sha256=receipt["result_sha256"])


def test_original_go_may_expire_after_valid_seal_and_immutable_reload_still_valid(synthetic, monkeypatch):
    receipt = seal(synthetic)
    monkeypatch.setattr(pub, "_now", lambda: pub.capture.parse_utc("2035-04-06T08:00:00Z"))
    assert load(synthetic, receipt)["entries"][0].evidence_json == synthetic["evidence"]


def test_replay_refusal_preserves_cursor_chain_without_second_extraction(synthetic, monkeypatch):
    add_refusals(synthetic, ["duplicate"])
    control = synthetic["control"]
    first = json.loads((control / "event-00000001.json").read_bytes())
    second = json.loads((control / "event-00000002.json").read_bytes())
    proof = dict(first["proof"]["source_range"], replay=True)
    second["proof"] = {"source_range": proof, "native_record_dispositions": first["proof"]["native_record_dispositions"]}
    (control / "event-00000002.json").write_bytes(raw(second))
    terminal = json.loads(synthetic["terminal"])
    terminal["journal_sha256"] = sha(raw([sha((control / f"event-{n:08d}.json").read_bytes()) for n in (1, 2)]))
    synthetic["terminal"] = raw(terminal)
    (control / "RESULT.json").write_bytes(synthetic["terminal"])
    calls = []
    original = pub.capture.extract_action_evidence
    monkeypatch.setattr(pub.capture, "extract_action_evidence", lambda *a, **k: calls.append(1) or original(*a, **k))
    assert seal(synthetic)["status"] == "COMPLETE" and calls == [1]


def test_generic_control_ledger_bytes_count_without_unreferenced_payload_reads(synthetic, monkeypatch):
    (synthetic["control"] / "synthetic-unreferenced.bin").write_bytes(b"c" * 18000)
    (synthetic["ledger"] / "synthetic-unreferenced.bin").write_bytes(b"l" * 18000)
    monkeypatch.setattr(pub, "BUDGET_BYTES", 35000)
    original = cli._read
    def read(path, *a, **kw):
        assert Path(path).name != "synthetic-unreferenced.bin"
        return original(path, *a, **kw)
    monkeypatch.setattr(cli, "_read", read)
    with pytest.raises(pub.NotReady, match="budget"): seal(synthetic)
    assert not (synthetic["root"] / "private/case_cli/seal_inputs/RESULT.json").exists()


def test_corrupt_intent_refuses_immutable_reload(synthetic):
    receipt = seal(synthetic)
    (synthetic["root"] / "private/case_cli/seal_inputs/INTENT.json").write_bytes(b"corrupt")
    with pytest.raises(pub.NotReady): load(synthetic, receipt)
