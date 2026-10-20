"""Synthetic-only pipeline wiring; no native runner/model or study input reads."""
import copy
from dataclasses import replace
import hashlib
import itertools
import json
import uuid

import pytest

from sandbox.mind.measurement_tools import prospective_pipeline as pipe


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def fixture(n=2, *, situations=None, names=None, kinds=None):
    """Invented future byte records; independent of any private source."""
    cases = pipe.cases
    names = names or [f"synthetic_{i:03}.md" for i in range(max(n, 1))]
    bodies = {name: f"---\nname: Invented label {i}\n---\nOriginal fabricated lesson {i}.".encode() for i, name in enumerate(sorted(set(names)))}
    blobs = {sha(body): body for body in bodies.values()}
    reporter = {"agent": "synthetic-agent", "agent_uuid": "1" * 32, "session_id": "synthetic-run"}
    session = str(uuid.UUID(int=2))
    protocol = raw({"schema": "wd.m1-p3-protocol-lock.v1", "status": "LOCKED", "study_id": "a" * 32,
                    "reporter_sources": [{"reporter": reporter, "native_session_id": session}]})
    identity = {"schema": "wd.m1-p3-capture-authorization.v1", "status": "LOCKED_CAPTURE_GO", "study_id": "a" * 32,
                "capture_id": "synthetic", "source_dir": "C:/synthetic/source", "destination": "C:/synthetic/capture",
                "preregistration_sha256": "3" * 64, "protocol_sha256": sha(protocol)}
    manifest = raw({"schema": "wd.m1-p3-baseline.v1", "identity": identity, "started_utc": "2035-04-05T05:59:50Z",
                    "sealed_utc": "2035-04-05T05:59:52Z", "files": [{"name": name, "sha256": sha(body), "bytes": len(body)} for name, body in sorted(bodies.items())]})
    seal = raw({"schema": "wd.m1-p3-baseline-seal.v1", "status": "SEALED", "manifest_sha256": sha(manifest), "sealed_utc": "2035-04-05T05:59:54Z"})
    capsule = {"schema": "wd.m1-p3-case-bindings.v1", "study_id": "a" * 32, "protocol_sha256": sha(protocol),
               "baseline_manifest_sha256": sha(manifest), "baseline_seal_sha256": sha(seal), "capture_identity_sha256": sha(raw(identity)),
               "collection_start_utc": "2035-04-05T06:00:00Z", "collection_end_utc": "2035-04-06T06:00:00Z",
               "timestamp_resolution_us": 1000, "baseline_resolution_us": 1_000_000, "max_record_delay_seconds": 120,
               "allowed_reporters": [reporter], "privacy_source_sha256": "4" * 64, "evidence_source_sha256": cases.EVIDENCE_SOURCE_SHA256,
               "source_commit": cases.MEASURED_SOURCE, "arm_a_version": "v21a", "arm_b_sha256": cases.BM25_SHA256,
               "grader_sha256": cases.GRADER_SHA256, "minimum_n": 40, "cap_n": 60, "seed": 20261019}
    bindings = cases.Bindings.from_bytes(raw(capsule), expected_sha256=sha(raw(capsule)), protocol=protocol, protocol_sha256=sha(protocol))
    baseline = cases.Baseline.from_bytes(manifest, seal, blobs, bindings=bindings)
    entries = []
    for i in range(n):
        text = (situations or ["Inspect the invented service settings carefully."] * n)[i]
        record_hash = sha(f"synthetic_action_{i}".encode())
        evidence = {"schema": "wd.m1-p3-claude-evidence.v1", "session_id": session, "uuid": str(uuid.UUID(int=i + 1000)),
                    "tool_use_id": f"synthetic_tool_{i}", "record_sha256": record_hash, "action_utc": "2035-04-05T06:01:00.123Z",
                    "action_latest_utc": "2035-04-05T06:01:00.124000Z", "timestamp_resolution_us": 1000,
                    "supplied_jsonl_sha256": sha(f"synthetic_prefix_{i}".encode()), "preaction_record_sha256": ["d" * 64],
                    "situation": text, "situation_sha256": sha(text.encode())}
        name = names[i]
        incident = {"schema": "wd.prospective-incident.v1", "study_id": "a" * 32, "protocol_sha256": sha(protocol), "baseline_sha256": sha(manifest),
                    "incident_id": f"{i+1:032x}", "incident_kind": (kinds or ["recurred"] * n)[i], "reporter": copy.deepcopy(reporter),
                    "action_time_utc": "2035-04-05T06:01:00Z", "recorded_at_utc": "2035-04-05T06:01:01Z",
                    "action_ref": f"{i+5000:032x}", "action_time_resolution_seconds": 1, "action_sha256": record_hash,
                    "action_receipt_sha256": "5" * 64, "lesson_ref": cases.lesson_ref(name), "baseline_blob_sha256": sha(bodies[name])}
        entries.append(cases.CaseInput(raw(incident), sha(raw(incident)), raw(evidence), sha(raw(evidence))))
    near = sum(kind == "near_miss" for kind in kinds or [])
    counts = raw({"reported": n, "accepted": n, "refused_cases": 0, "duplicates": 0, "primary_candidates": n-near, "near_miss": near,
                  "refusals_by_reason": {key: 0 for key in cases.COLLECTOR_REASONS}})
    # Deliberately noncanonical original bytes: original and harness digests differ.
    runner_binding = json.dumps({"source_commit": cases.MEASURED_SOURCE, "rule": {"guard_band_s": 1.0, "bm25_k1": 1.2, "bm25_b": 0.75}}, indent=2).encode()
    args = {"bindings": bindings, "baseline": baseline, "entries": entries, "protocol_sha256": sha(protocol), "capsule_sha256": sha(raw(capsule)),
            "collector_counts": counts, "collector_counts_sha256": sha(counts), "runner_binding": runner_binding,
            "runner_binding_sha256": sha(runner_binding), "runner_design_sha256": sha(protocol), "adapter_source_sha256": "4" * 64}
    return args


def prepass(plan, *, guarded=(), empty=()):
    request = json.loads(plan.executor_inputs.request_json)
    rows = json.loads(plan.executor_inputs.runner_cases_json)
    snapshot = json.loads(plan.snapshot_json)
    names = sorted(snapshot)
    applicable = {row["id"]: ([] if row["id"] in empty else names) for row in rows if row["id"] not in guarded}
    return raw({"schema": "wd.m1-prepass.v1", "design_sha256": request["design_sha256"], "binding_sha256": request["binding_sha256"],
                "input_sha256": request["input_sha256"], "guarded": list(guarded), "applicable": applicable,
                "case_receipts": {cid: {"snapshot_sha256": sha(plan.snapshot_json), "memory_sha256": "6" * 64, "documents_sha256": "7" * 64} for cid in applicable}})


def prepared(plan):
    request = json.loads(plan.executor_inputs.request_json)
    return raw({row["id"]: {"snapshot_sha256": sha(plan.snapshot_json), "binding_sha256": request["binding_sha256"],
                           "memory_dir": "C:/Python/project2/.codex-audit/synthetic-native/prepared/shared/memory",
                           "memory_sha256": "6" * 64} for row in json.loads(plan.executor_inputs.runner_cases_json)})


def finish(plan, result=None, **overrides):
    result = prepass(plan) if result is None else result
    counter = itertools.count(1)
    kwargs = {"expected_plan_sha256": sha(plan.manifest_json), "native_prepass": result, "native_prepass_sha256": sha(result),
              "prepared_receipts": prepared(plan), "prepared_receipts_sha256": sha(prepared(plan)),
              "_test_nonce_factory": lambda: f"{next(counter):064x}"}
    kwargs.update(overrides)
    return pipe.complete_cases(plan, **kwargs)


def test_prepare_is_gold_blind_and_original_and_adapter_hashes_are_distinct():
    args = fixture()
    plan = pipe.prepare_prepass(**args)
    rows = [json.loads(line) for line in plan.executor_inputs.blind_jsonl.splitlines()]
    times = json.loads(plan.executor_inputs.time_sidecar_json)
    adapted = json.loads(plan.executor_inputs.runner_cases_json)
    assert all(set(row) == {"id", "situation", "snapshot_sha256"} for row in rows)
    assert all(set(row) == {"id", "situation", "time", "snapshot_sha256"} for row in adapted)
    assert set(times) == {row["id"] for row in rows}
    assert set(times.values()) == {"2035-04-05T06:01:00Z"}
    assert all(row["time"] == times[row["id"]] for row in adapted)
    request = json.loads(plan.executor_inputs.request_json)
    assert request["input_sha256"] == sha(plan.executor_inputs.runner_cases_json)
    manifest = json.loads(plan.manifest_json)
    assert manifest["runner_binding_original_sha256"] == sha(args["runner_binding"])
    assert manifest["runner_binding_canonical_sha256"] != manifest["runner_binding_original_sha256"]
    assert manifest["protocol_original_sha256"] == args["protocol_sha256"]
    assert manifest["capsule_original_sha256"] == args["capsule_sha256"]
    executor = b"".join(getattr(plan.executor_inputs, name) for name in plan.executor_inputs.__dataclass_fields__)
    assert all(word not in executor for word in (b"gold_basename", b"nonce", b"lesson_ref", b"synthetic_000.md"))
    assert b"synthetic_000.md" not in repr(plan).encode()


def test_mechanical_precheck_is_not_a_native_guard_and_requires_no_native_row():
    args = fixture(situations=["Inspect C:/invented/settings carefully.", "Inspect the invented service settings carefully."])
    plan = pipe.prepare_prepass(**args)
    adapted = pipe.adapt_prepass(plan, expected_plan_sha256=sha(plan.manifest_json), native_prepass=prepass(plan), native_prepass_sha256=sha(prepass(plan)),
                                prepared_receipts=prepared(plan), prepared_receipts_sha256=sha(prepared(plan)))
    privacy = json.loads(adapted.privacy_applicability_json)
    blocked = f"{1:032x}"
    assert privacy["cases"][blocked]["guarded"] is True and privacy["cases"][blocked]["applicable"] == []
    counts = json.loads(adapted.denominators_json)
    assert counts == {"accepted": 2, "source_precheck_guarded": 1, "source_precheck_by_reason": {"blind_obvious_leak_or_path": 1},
                      "native_not_run": 1, "native_submitted": 1, "native_guarded": 0, "native_applicable_nonempty": 1, "native_applicable_empty": 0}
    assert json.loads(adapted.provenance_json)["source_precheck"][blocked] == "blind_obvious_leak_or_path"


def test_final_cases_come_after_prepass_and_full_safe_list_is_retained_for_arms():
    args = fixture(names=["synthetic_000.md", "synthetic_000.md"])
    plan = pipe.prepare_prepass(**args)
    result = finish(plan)
    rows = json.loads(result.case_artifacts.cases_json)
    assert sum(row["kept"] for row in rows) == 1
    assert result.executor_inputs == plan.executor_inputs
    assert len(json.loads(result.executor_inputs.runner_cases_json)) == 2
    assert len(result.case_artifacts.blind_jsonl.splitlines()) == 1
    assert set(json.loads(result.kept_time_sidecar_json)) == set(json.loads(result.commitments_json)) == {f"{1:032x}"}
    assert result.commitments_json == result.case_artifacts.commitments_json
    assert result.original_native_prepass_json == prepass(plan)
    assert b'"nonce"' not in result.commitments_json
    assert b"synthetic_000.md" not in repr(result).encode()


@pytest.mark.parametrize("mutation", ["missing", "unknown", "overlap", "duplicate_guard", "missing_receipt", "guard_receipt", "snapshot", "memory", "documents", "unknown_ref", "duplicate_ref", "design", "binding", "input", "unknown_key"])
def test_native_result_requires_exact_cover_and_all_bindings(mutation):
    plan = pipe.prepare_prepass(**fixture())
    result = json.loads(prepass(plan))
    cid = f"{1:032x}"
    if mutation == "missing":
        del result["applicable"][cid]
    elif mutation == "unknown":
        result["applicable"]["f" * 32] = []
    elif mutation == "overlap":
        result["guarded"] = [cid]
    elif mutation == "duplicate_guard":
        result["guarded"] = [cid, cid]
    elif mutation == "missing_receipt":
        del result["case_receipts"][cid]
    elif mutation == "guard_receipt":
        result["guarded"] = [cid]
        del result["applicable"][cid]
    elif mutation in ("snapshot", "memory", "documents"):
        key = {"snapshot": "snapshot_sha256", "memory": "memory_sha256", "documents": "documents_sha256"}[mutation]
        result["case_receipts"][cid][key] = "0" * 64 if mutation == "snapshot" else "UNKNOWN"
    elif mutation == "unknown_ref":
        result["applicable"][cid].append("unknown.md")
    elif mutation == "duplicate_ref":
        result["applicable"][cid] *= 2
    elif mutation in ("design", "binding", "input"):
        result[mutation + "_sha256"] = "0" * 64
    else:
        result["gold"] = "SYNTHETIC_PRIVATE_SENTINEL"
    with pytest.raises(pipe.NotReady) as error:
        finish(plan, raw(result))
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in str(error.value)


def test_no_construct_or_nonce_effect_before_complete_valid_prepass(monkeypatch):
    calls = []
    original = pipe.cases.construct_cases
    monkeypatch.setattr(pipe.cases, "construct_cases", lambda *a, **kw: calls.append(1) or original(*a, **kw))
    plan = pipe.prepare_prepass(**fixture())
    assert calls == []
    with pytest.raises(pipe.NotReady):
        finish(plan, b"{}")
    assert calls == []
    assert finish(plan).case_artifacts.blind_jsonl
    assert calls == [1]


@pytest.mark.parametrize("field", ["protocol_sha256", "capsule_sha256", "collector_counts_sha256", "runner_binding_sha256"])
def test_original_hash_mismatch_refuses_before_prepass(field):
    args = fixture()
    args[field] = "0" * 64
    with pytest.raises(pipe.NotReady):
        pipe.prepare_prepass(**args)


def test_plan_or_native_original_hash_drift_refuses():
    plan = pipe.prepare_prepass(**fixture())
    with pytest.raises(pipe.NotReady):
        finish(plan, expected_plan_sha256="0" * 64)
    with pytest.raises(pipe.NotReady):
        finish(plan, native_prepass_sha256="0" * 64)
    changed_inputs = replace(plan.executor_inputs, time_sidecar_json=b"{}")
    with pytest.raises(pipe.NotReady):
        finish(replace(plan, executor_inputs=changed_inputs))


def test_empty_and_all_blocked_have_explicit_empty_native_cover():
    for args in (fixture(0), fixture(situations=["", "Inspect C:/invented/settings."])):
        plan = pipe.prepare_prepass(**args)
        assert json.loads(plan.executor_inputs.runner_cases_json) == []
        result = finish(plan)
        assert result.case_artifacts.blind_jsonl == b"" and json.loads(result.commitments_json) == {}


def test_native_guards_empty_applicability_and_collector_counts_stay_separate():
    plan = pipe.prepare_prepass(**fixture(3))
    result = finish(plan, prepass(plan, guarded=[f"{1:032x}"], empty=[f"{2:032x}"]))
    counts = json.loads(result.prepass_denominators_json)
    assert counts["native_guarded"] == counts["native_applicable_empty"] == counts["native_applicable_nonempty"] == 1
    case_counts = json.loads(result.case_artifacts.denominators_json)
    assert case_counts["collector"]["accepted"] == 3
    assert case_counts["drops_by_grader_reason"]["situation_guarded"] == 1
    assert case_counts["drops_by_grader_reason"]["empty_applicable_corpus"] == 1


def test_bad_case_input_never_silently_skips():
    args = fixture()
    old = args["entries"][0]
    args["entries"][0] = replace(old, evidence_sha256="0" * 64)
    with pytest.raises(pipe.NotReady):
        pipe.prepare_prepass(**args)


def test_prepass_original_hash_and_harness_canonical_hash_stay_separate():
    plan = pipe.prepare_prepass(**fixture())
    original = json.dumps(json.loads(prepass(plan)), indent=2).encode()
    result = finish(plan, original)
    provenance = json.loads(result.provenance_json)
    assert result.original_native_prepass_json == original
    assert provenance["original_native_prepass_sha256"] == sha(original)
    assert provenance["harness_prepass_canonical_sha256"] == sha(raw(json.loads(original)) + b"\n") != sha(original)
    assert provenance["privacy_applicability_sha256"] == sha(result.privacy_applicability_json)
    assert provenance["privacy_adapter_sha256"] == json.loads(result.privacy_applicability_json)["source_sha256"] == "4" * 64
    assert provenance["native_runner_sha256"] == pipe.BLIND_RUNNER_SHA256 != "4" * 64


def test_runner_input_hash_cannot_be_replaced_with_strict_projection_hash():
    plan = pipe.prepare_prepass(**fixture())
    result = json.loads(prepass(plan))
    assert sha(plan.executor_inputs.blind_jsonl) != result["input_sha256"]
    result["input_sha256"] = sha(plan.executor_inputs.blind_jsonl)
    with pytest.raises(pipe.NotReady):
        finish(plan, raw(result))


def test_source_precheck_id_cannot_be_mislabeled_as_native_guard():
    plan = pipe.prepare_prepass(**fixture(situations=["Inspect C:/invented/settings", "Inspect the invented settings carefully."]))
    result = json.loads(prepass(plan))
    result["guarded"] = [f"{1:032x}"]
    with pytest.raises(pipe.NotReady, match="privacy_pass"):
        finish(plan, raw(result))


@pytest.mark.parametrize("change", ["source", "guard_band_s", "bm25_k1", "bm25_b", "boolean_rule"])
def test_runtime_science_declarations_cannot_change(change):
    args = fixture()
    runtime = json.loads(args["runner_binding"])
    if change == "source":
        runtime["source_commit"] = "0" * 40
    elif change == "boolean_rule":
        runtime["rule"]["guard_band_s"] = True
    else:
        runtime["rule"][change] = 123
    args.update(runner_binding=raw(runtime), runner_binding_sha256=sha(raw(runtime)))
    with pytest.raises(pipe.NotReady):
        pipe.prepare_prepass(**args)


@pytest.mark.parametrize("field", ["collector_counts", "runner_binding"])
def test_duplicate_original_json_keys_are_fatal_even_with_matching_hash(field):
    args = fixture()
    data = b'{"schema":"synthetic","schema":"duplicate"}'
    args[field] = data
    args[field + "_sha256"] = sha(data)
    with pytest.raises(pipe.NotReady):
        pipe.prepare_prepass(**args)


def test_composite_privacy_source_must_match_original_capsule():
    args = fixture()
    args["adapter_source_sha256"] = pipe.BLIND_RUNNER_SHA256
    with pytest.raises(pipe.NotReady):
        pipe.prepare_prepass(**args)


@pytest.mark.parametrize("field", ["incident_id", "action_ref", "native_action"])
def test_duplicate_identity_refuses_before_native_prepass(field):
    args = fixture()
    first, second = args["entries"]
    a, b = json.loads(first.incident_json), json.loads(second.incident_json)
    evidence = json.loads(second.evidence_json)
    if field == "native_action":
        evidence = json.loads(first.evidence_json)
        b["action_sha256"] = evidence["record_sha256"]
    else:
        b[field] = a[field]
    args["entries"][1] = pipe.cases.CaseInput(raw(b), sha(raw(b)), raw(evidence), sha(raw(evidence)))
    with pytest.raises(pipe.NotReady, match="duplicate"):
        pipe.prepare_prepass(**args)


def test_malformed_native_json_and_unknown_receipt_keys_refuse():
    plan = pipe.prepare_prepass(**fixture())
    for data in (b'{"schema":"wd.m1-prepass.v1","schema":"duplicate"}', b'{"schema":NaN}', b'not-json'):
        with pytest.raises(pipe.NotReady):
            finish(plan, data)
    result = json.loads(prepass(plan))
    result["case_receipts"][f"{1:032x}"]["gold_basename"] = "SYNTHETIC_PRIVATE_SENTINEL"
    with pytest.raises(pipe.NotReady) as error:
        finish(plan, raw(result))
    assert "SYNTHETIC_PRIVATE_SENTINEL" not in str(error.value)


def test_pure_byte_budget_is_bounded(monkeypatch):
    args = fixture()
    monkeypatch.setattr(pipe, "BUDGET_BYTES", 500)
    with pytest.raises(pipe.NotReady, match="budget"):
        pipe.prepare_prepass(**args)


def test_pipeline_has_no_filesystem_clock_or_native_invocation(monkeypatch, capsys):
    from pathlib import Path
    args = fixture()
    before = copy.deepcopy(args)
    monkeypatch.setattr(Path, "read_bytes", lambda *a: pytest.fail("unexpected source/data read"))
    monkeypatch.setattr(Path, "write_bytes", lambda *a: pytest.fail("unexpected output write"))
    monkeypatch.setattr(pipe.cases.secrets, "token_hex", lambda *a: pytest.fail("synthetic nonce factory was bypassed"))
    plan = pipe.prepare_prepass(**args)
    result = finish(plan)
    assert result.case_artifacts.blind_jsonl
    assert args == before
    assert capsys.readouterr() == ("", "")


def test_collector_accepted_count_cannot_silently_differ():
    args = fixture()
    counts = json.loads(args["collector_counts"])
    counts["accepted"] = 99
    args.update(collector_counts=raw(counts), collector_counts_sha256=sha(raw(counts)))
    with pytest.raises(pipe.NotReady):
        pipe.prepare_prepass(**args)


@pytest.mark.parametrize("field", ["capsule_sha256", "collector_counts_sha256", "runner_binding_sha256"])
def test_missing_original_expected_hash_cannot_disable_verification(field):
    args = fixture()
    args[field] = None
    with pytest.raises(pipe.NotReady):
        pipe.prepare_prepass(**args)


@pytest.mark.parametrize("field", ["expected_plan_sha256", "native_prepass_sha256", "prepared_receipts_sha256"])
def test_missing_completion_expected_hash_cannot_disable_verification(field):
    plan = pipe.prepare_prepass(**fixture())
    with pytest.raises(pipe.NotReady):
        finish(plan, **{field: None})


@pytest.mark.parametrize("field", ["incident_sha256", "evidence_sha256"])
def test_missing_case_input_expected_hash_is_fatal(field):
    args = fixture()
    args["entries"][0] = replace(args["entries"][0], **{field: None})
    with pytest.raises(pipe.NotReady):
        pipe.prepare_prepass(**args)


@pytest.mark.parametrize("private", [{"gold_basename": "SYNTHETIC_PRIVATE"}, {"nested": [{"nonce": "SYNTHETIC_PRIVATE"}]}, {"case_inputs": "SYNTHETIC_PRIVATE"}])
def test_private_binding_payload_cannot_reach_executor(private):
    args = fixture()
    runtime = json.loads(args["runner_binding"])
    runtime.update(private)
    args.update(runner_binding=raw(runtime), runner_binding_sha256=sha(raw(runtime)))
    with pytest.raises(pipe.NotReady) as error:
        pipe.prepare_prepass(**args)
    assert "SYNTHETIC_PRIVATE" not in str(error.value)


@pytest.mark.parametrize("mutation", ["missing", "unknown", "snapshot", "binding", "memory", "split_memory", "split_directory", "path", "unknown_key"])
def test_original_prepared_receipts_cover_safe_ids_and_match_one_shared_memory(mutation):
    plan = pipe.prepare_prepass(**fixture())
    mapping = json.loads(prepared(plan))
    cid = f"{1:032x}"
    if mutation == "missing":
        del mapping[cid]
    elif mutation == "unknown":
        mapping["f" * 32] = copy.deepcopy(mapping[cid])
    elif mutation in ("snapshot", "binding", "memory"):
        key = {"snapshot": "snapshot_sha256", "binding": "binding_sha256", "memory": "memory_sha256"}[mutation]
        for info in mapping.values():
            info[key] = "0" * 64
    elif mutation == "split_memory":
        mapping[cid]["memory_sha256"] = "8" * 64
    elif mutation == "split_directory":
        mapping[cid]["memory_dir"] += "-different"
    elif mutation == "path":
        for info in mapping.values():
            info["memory_dir"] = "C:/outside-audit/synthetic"
    else:
        mapping[cid]["nonce"] = "SYNTHETIC_PRIVATE"
    with pytest.raises(pipe.NotReady):
        finish(plan, prepared_receipts=raw(mapping), prepared_receipts_sha256=sha(raw(mapping)))


def test_original_prepared_hash_is_required_and_preserved_separately():
    plan = pipe.prepare_prepass(**fixture())
    data = json.dumps(json.loads(prepared(plan)), indent=2).encode()
    with pytest.raises(pipe.NotReady):
        finish(plan, prepared_receipts=data, prepared_receipts_sha256="0" * 64)
    result = finish(plan, prepared_receipts=data, prepared_receipts_sha256=sha(data))
    assert json.loads(result.provenance_json)["prepared_receipts_original_sha256"] == sha(data)


def test_frozen_sampling_keeps_full_prepass_input_and_exact_kept_time_subset():
    plan = pipe.prepare_prepass(**fixture(64))
    result = finish(plan)
    assert len(json.loads(result.executor_inputs.runner_cases_json)) == 64
    assert len(result.executor_inputs.blind_jsonl.splitlines()) == 64
    assert len(result.case_artifacts.blind_jsonl.splitlines()) == 60
    assert len(json.loads(result.kept_time_sidecar_json)) == len(json.loads(result.commitments_json)) == 60
    assert json.loads(result.provenance_json)["full_safe_harness_input_sha256"] == sha(plan.executor_inputs.runner_cases_json)


def test_collector_refusals_source_prechecks_and_native_guards_are_distinct():
    args = fixture(situations=["Inspect C:/invented/settings", "Inspect the invented settings carefully."])
    counts = json.loads(args["collector_counts"])
    counts.update(reported=5, refused_cases=3, duplicates=1)
    counts["refusals_by_reason"].update(duplicate=1, size=2)
    args.update(collector_counts=raw(counts), collector_counts_sha256=sha(raw(counts)))
    plan = pipe.prepare_prepass(**args)
    result = finish(plan, prepass(plan, guarded=[f"{2:032x}"]))
    pre = json.loads(result.prepass_denominators_json)
    case = json.loads(result.case_artifacts.denominators_json)
    assert pre["accepted"] == 2 and pre["native_not_run"] == pre["native_submitted"] == pre["native_guarded"] == 1
    assert case["collector"] == counts
    assert case["drops_by_grader_reason"]["duplicate_incident"] == 0
