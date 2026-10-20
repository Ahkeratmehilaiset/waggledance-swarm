"""Pure supplied-byte M1-P3 prepass -> case glue; no execution/publication.

Only the explicit BlindInputs bytes may go to the blind worker. PrepassPlan and
PipelineArtifacts contain PRIVATE builder inputs/artifacts and must never be
passed whole to an executor. Gold/nonces/case JSON never enter BlindInputs.
The full safe native input list is retained for BOTH harness phases; kept-only
blind/time bytes are a separate projection, never a replacement harness input.

This is source-only wiring, not a phase controller: no CLI, filesystem access,
native/model/encoder/harness/grader invocation or clock discovery. Root's worker
must verify loaded source/runtime/prepared-tree bytes, persist one consumed
phase intent before effects, enforce shared disk/budget/NTFS/tree separation,
publish original hashes, and gate gold release after both arm outputs. A caller
hash is integrity binding, not proof of phase execution or authorization.

The frozen case module's validation helpers are intentionally reused under its
exact source pin; they do not construct cases or generate nonces. Final current
CaseArtifacts are composed only after a complete validated supplied prepass.
"""
from __future__ import annotations

from dataclasses import dataclass
import functools
import hashlib
import json
from pathlib import PureWindowsPath

from sandbox.mind.measurement_tools import prospective_capture as capture
from sandbox.mind.measurement_tools import prospective_cases as cases


BUDGET_BYTES = 64 * 1024 * 1024
CASE_SOURCE_SHA256 = "8fbc095c80227d67bc1d8ef59274add608fe197407a34193a688a33b4f2c729a"
CAPTURE_SOURCE_SHA256 = "4203b3215c446020b57940b69deccbf3694af4b936af468685a439867a300b12"
BLIND_RUNNER_SHA256 = "2011c5dca6b5e8f8880a7117911136d446faf5fa7d87faf24dbb6c032408b862"
BM25_SHA256 = "a1667a27da015dcaed0f97960bbede3281dab3540018106d2458879db213579a"
GRADER_SHA256 = "074b443657daf72797b66994fd6afa5f2f4dd9384c26d3b955e429f451740117"
MEASURED_SOURCE = "6cf81dbbefe2b0e19a4c8306100beb30784ac4e4"
NotReady = cases.NotReady
_SOURCE_PRECHECK_REASONS = frozenset({"blind_situation_invalid", "blind_obvious_leak_or_path"})
_PRIVATE_BINDING_KEYS = frozenset({"gold", "gold_basename", "nonce", "nomination", "nominations",
    "cases_json", "cases_pre_json", "case_inputs", "case_artifacts", "incident_json", "evidence_json",
    "score_package", "score_cases", "lesson_ref"})


def _need(value, reason="binding"):
    if not value:
        raise NotReady(reason)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _canon(value):
    return cases._canon(value)


def _harness_bytes(value):
    # Exact frozen blindrunner.canonical/digest convention, including final LF.
    return _canon(value) + b"\n"


def _json(data, expected=None):
    return cases._json(data, expected)


def _blind_binding(value):
    """Reject structural private payloads before exporting the pinned binding."""
    if type(value) is dict:
        for key, item in value.items():
            low = key.casefold()
            _need(low not in _PRIVATE_BINDING_KEYS and not low.startswith(("gold_", "nonce_", "nomination_")), "privacy_pass")
            _blind_binding(item)
    elif type(value) is list:
        for item in value:
            _blind_binding(item)


def _closed(function):
    @functools.wraps(function)
    def call(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (TypeError, KeyError, AttributeError, ValueError, OverflowError, RecursionError):
            raise NotReady("schema") from None
    return call


@dataclass(frozen=True, repr=False)
class BlindInputs:
    """Gold-blind bytes only; unchanged full SAFE list for prepass AND arms."""
    blind_jsonl: bytes
    time_sidecar_json: bytes
    runner_cases_json: bytes
    runner_binding_json: bytes
    request_json: bytes


@dataclass(frozen=True, repr=False)
class PrepassPlan:
    """PRIVATE builder custody. Never hand this whole object to an executor."""
    bindings: cases.Bindings
    baseline: cases.Baseline
    entries: tuple[cases.CaseInput, ...]
    collector_counts_json: bytes
    protocol_sha256: str
    capsule_sha256: str
    collector_counts_sha256: str
    runner_binding_sha256: str
    runner_design_sha256: str
    adapter_source_sha256: str
    executor_inputs: BlindInputs
    snapshot_json: bytes
    manifest_json: bytes


@dataclass(frozen=True, repr=False)
class PrepassAdaptation:
    privacy_applicability_json: bytes
    denominators_json: bytes
    provenance_json: bytes
    original_native_prepass_json: bytes


@dataclass(frozen=True, repr=False)
class PipelineArtifacts:
    """PRIVATE aggregate; executor gets only the explicitly blind fields."""
    case_artifacts: cases.CaseArtifacts
    executor_inputs: BlindInputs
    original_native_prepass_json: bytes
    commitments_json: bytes
    kept_time_sidecar_json: bytes
    privacy_applicability_json: bytes
    prepass_denominators_json: bytes
    provenance_json: bytes


def _collector_counts(value, verified):
    cases._keys(value, ("reported", "accepted", "refused_cases", "duplicates", "primary_candidates", "near_miss", "refusals_by_reason"))
    cases._keys(value["refusals_by_reason"], cases.COLLECTOR_REASONS)
    _need(all(type(value[key]) is int and value[key] >= 0 for key in ("reported", "accepted", "refused_cases", "duplicates", "primary_candidates", "near_miss")), "prerequisite")
    _need(all(type(v) is int and v >= 0 for v in value["refusals_by_reason"].values()), "prerequisite")
    near = sum(item[0]["incident_kind"] == "near_miss" for item in verified)
    _need(value["accepted"] == len(verified)
          and value["reported"] == len(verified) + value["refused_cases"]
          and value["refused_cases"] == sum(value["refusals_by_reason"].values())
          and value["duplicates"] == value["refusals_by_reason"]["duplicate"]
          and value["near_miss"] == near and value["primary_candidates"] == len(verified) - near, "prerequisite")


@_closed
def prepare_prepass(bindings, baseline, entries, *, protocol_sha256, capsule_sha256,
                    collector_counts, collector_counts_sha256, runner_binding,
                    runner_binding_sha256, runner_design_sha256, adapter_source_sha256):
    """Validate originals, emit strict3-key blind rows and a separate UTC sidecar.

    adapter_source_sha256 is the final lock's composite privacy-adapter source
    pin, matched to the ORIGINAL case capsule, not substituted for native runner
    2011C5DC. Actual loaded-file hash verification is the runtime worker's duty.
    All input hashes below cover supplied original bytes, never reserialization.
    """
    _need(type(bindings) is cases.Bindings and type(baseline) is cases.Baseline)
    _need(all(cases._hex(pin) for pin in (protocol_sha256, capsule_sha256, collector_counts_sha256,
                                         runner_binding_sha256, runner_design_sha256, adapter_source_sha256)))
    checked = cases.Bindings.from_bytes(bindings.capsule_json, expected_sha256=capsule_sha256,
                                       protocol=bindings.protocol_json, protocol_sha256=protocol_sha256)
    b = cases._bound(checked)
    _need(cases._hex(adapter_source_sha256) and b["privacy_source_sha256"] == adapter_source_sha256)
    _need((b["source_commit"], b["evidence_source_sha256"], b["arm_b_sha256"], b["grader_sha256"], b["arm_a_version"])
          == (MEASURED_SOURCE, CAPTURE_SOURCE_SHA256, BM25_SHA256, GRADER_SHA256, "v21a"))
    _need(cases._hex(runner_design_sha256))
    checked_baseline = cases.Baseline.from_bytes(baseline.manifest_json, baseline.seal_json, dict(baseline.blobs), bindings=checked)
    snapshot_json = cases.fixed_snapshot(checked_baseline, bindings=checked)
    snapshot, snapsha = _json(snapshot_json), _sha(snapshot_json)
    _need(type(entries) in (list, tuple) and len(entries) <= 10000, "schema")
    _need(all(type(entry) is cases.CaseInput for entry in entries), "schema")
    _need(all(cases._hex(entry.incident_sha256) and cases._hex(entry.evidence_sha256) for entry in entries))
    original_bytes = (len(checked.capsule_json) + len(checked.protocol_json) + len(baseline.manifest_json)
                      + len(baseline.seal_json) + sum(len(body) for _, body in baseline.blobs)
                      + sum(len(e.incident_json) + len(e.evidence_json) for e in entries))
    _need(type(collector_counts) is bytes and type(runner_binding) is bytes
          and original_bytes + len(collector_counts) + len(runner_binding) <= BUDGET_BYTES, "budget")
    verified = [cases._entry(entry, b, snapshot) for entry in entries]
    ids = [item[0]["incident_id"] for item in verified]
    _need(len(set(ids)) == len(ids) and len({item[0]["action_ref"] for item in verified}) == len(ids)
          and len({(item[1]["session_id"], item[1]["uuid"], item[1]["tool_use_id"]) for item in verified}) == len(ids), "duplicate")
    _collector_counts(_json(collector_counts, collector_counts_sha256), verified)
    runtime = _json(runner_binding, runner_binding_sha256)
    _need(type(runtime) is dict and runtime.get("source_commit") == MEASURED_SOURCE)
    _blind_binding(runtime)
    rule = runtime.get("rule")
    _need(type(rule) is dict)
    for key, expected in (("guard_band_s", 1.0), ("bm25_k1", 1.2), ("bm25_b", 0.75)):
        _need(type(rule.get(key)) in (int, float) and rule[key] == expected)
    binding_digest = _sha(_harness_bytes(runtime))
    blind, times, blocked, entry_hashes = [], {}, {}, []
    for entry, (incident, evidence, _, _) in zip(entries, verified):
        cid = incident["incident_id"]
        entry_hashes.append({"id": cid, "incident_sha256": entry.incident_sha256,
                             "evidence_sha256": entry.evidence_sha256, "situation_sha256": evidence["situation_sha256"]})
        try:
            row = capture.project_blind_case({"id": cid, "situation": evidence["situation"], "snapshot_sha256": snapsha})
        except capture.CaptureError as exc:
            _need(str(exc) in _SOURCE_PRECHECK_REASONS, "privacy_pass")
            blocked[cid] = str(exc)  # Fixed code, never query/path payload.
            continue
        blind.append(row)
        times[cid] = incident["action_time_utc"]  # Exact validated recorder UTCsecond.
    blind.sort(key=lambda row: row["id"])
    times = dict(sorted(times.items()))
    blind_jsonl = b"".join(_canon(row) + b"\n" for row in blind)
    time_json = _canon(times)
    runner_cases = [{"id": row["id"], "situation": row["situation"], "time": times[row["id"]], "snapshot_sha256": row["snapshot_sha256"]} for row in blind]
    runner_json = _harness_bytes(runner_cases)
    request = {"schema": "wd.m1-p3-blind-prepass-request.v1", "design_sha256": runner_design_sha256,
               "binding_sha256": binding_digest, "input_sha256": _sha(runner_json),
               "blind_jsonl_sha256": _sha(blind_jsonl), "time_sidecar_sha256": _sha(time_json),
               "snapshot_sha256": snapsha, "harness_source_sha256": BLIND_RUNNER_SHA256,
               "bm25_source_sha256": BM25_SHA256, "source_commit": MEASURED_SOURCE, "arm_a_version": "v21a"}
    inputs = BlindInputs(blind_jsonl, time_json, runner_json, bytes(runner_binding), _canon(request))
    manifest = {"schema": "wd.m1-p3-pipeline-plan.v1", "study_id": b["study_id"],
                "protocol_original_sha256": protocol_sha256, "capsule_original_sha256": capsule_sha256,
                "baseline_manifest_sha256": b["baseline_manifest_sha256"], "baseline_seal_sha256": b["baseline_seal_sha256"],
                "capture_identity_sha256": b["capture_identity_sha256"], "snapshot_sha256": snapsha,
                "collector_counts_original_sha256": collector_counts_sha256,
                "runner_binding_original_sha256": runner_binding_sha256, "runner_binding_canonical_sha256": binding_digest,
                "runner_design_sha256": runner_design_sha256, "runner_input_sha256": _sha(runner_json),
                "blind_jsonl_sha256": _sha(blind_jsonl), "time_sidecar_sha256": _sha(time_json),
                "request_sha256": _sha(inputs.request_json), "input_entries": entry_hashes,
                "source_precheck": dict(sorted(blocked.items())), "adapter_source_sha256": adapter_source_sha256,
                "source_pins": {"cases": CASE_SOURCE_SHA256, "capture": CAPTURE_SOURCE_SHA256, "blindrunner": BLIND_RUNNER_SHA256,
                                "bm25": BM25_SHA256, "grader": GRADER_SHA256, "measured_commit": MEASURED_SOURCE}}
    manifest_json = _canon(manifest)
    _need(sum(len(getattr(inputs, key)) for key in inputs.__dataclass_fields__) + len(snapshot_json) + len(manifest_json) <= BUDGET_BYTES, "budget")
    return PrepassPlan(checked, checked_baseline, tuple(entries), bytes(collector_counts), protocol_sha256, capsule_sha256,
                       collector_counts_sha256, runner_binding_sha256, runner_design_sha256, adapter_source_sha256,
                       inputs, snapshot_json, manifest_json)


def _checked_plan(plan, expected_sha256):
    _need(type(plan) is PrepassPlan)
    _need(cases._hex(expected_sha256))
    _json(plan.manifest_json, expected_sha256)
    rebuilt = prepare_prepass(plan.bindings, plan.baseline, plan.entries, protocol_sha256=plan.protocol_sha256,
        capsule_sha256=plan.capsule_sha256, collector_counts=plan.collector_counts_json,
        collector_counts_sha256=plan.collector_counts_sha256, runner_binding=plan.executor_inputs.runner_binding_json,
        runner_binding_sha256=plan.runner_binding_sha256, runner_design_sha256=plan.runner_design_sha256,
        adapter_source_sha256=plan.adapter_source_sha256)
    _need(rebuilt == plan)
    return _json(plan.manifest_json)


@_closed
def adapt_prepass(plan, *, expected_plan_sha256, native_prepass, native_prepass_sha256,
                  prepared_receipts, prepared_receipts_sha256):
    """Exact ALL-accepted map; native guards and mechanical prechecks stay distinct.

    Memory hashes must match the separately hash-bound original PREPARED map.
    Underlying tree rehashing and document payload digests belong to the pinned
    native worker/harness; these metadata bytes contain no such payloads.
    """
    manifest = _checked_plan(plan, expected_plan_sha256)
    _need(cases._hex(native_prepass_sha256) and cases._hex(prepared_receipts_sha256))
    original = _json(native_prepass, native_prepass_sha256)
    cases._keys(original, ("schema", "design_sha256", "binding_sha256", "input_sha256", "guarded", "applicable", "case_receipts"))
    request = _json(plan.executor_inputs.request_json)
    _need(original["schema"] == "wd.m1-prepass.v1"
          and all(original[key] == request[key] for key in ("design_sha256", "binding_sha256", "input_sha256")))
    safe = set(_json(plan.executor_inputs.time_sidecar_json))
    blocked = manifest["source_precheck"]
    prepared = _json(prepared_receipts, prepared_receipts_sha256)
    _need(type(prepared) is dict and set(prepared) == safe, "privacy_pass")
    memory_dirs, memory_hashes = set(), set()
    audit = PureWindowsPath("C:/Python/project2/.codex-audit")
    for info in prepared.values():
        cases._keys(info, ("snapshot_sha256", "binding_sha256", "memory_dir", "memory_sha256"))
        _need(info["snapshot_sha256"] == request["snapshot_sha256"] and info["binding_sha256"] == request["binding_sha256"]
              and all(cases._hex(info[key]) for key in ("snapshot_sha256", "binding_sha256", "memory_sha256")))
        directory = info["memory_dir"]
        _need(type(directory) is str and 0 < len(directory) <= 32768 and not any(ord(c) < 32 for c in directory), "privacy_pass")
        path = PureWindowsPath(directory)
        _need(path.is_absolute() and path.drive.casefold() == "c:" and audit in path.parents
              and all(part not in (".", "..") and ":" not in part and not part.endswith((".", " ")) for part in path.parts[1:]), "privacy_pass")
        memory_dirs.add(directory)
        memory_hashes.add(info["memory_sha256"])
    _need(len(memory_dirs) == len(memory_hashes) == (1 if safe else 0), "privacy_pass")
    guarded, applicable, receipts = (original[key] for key in ("guarded", "applicable", "case_receipts"))
    _need(type(guarded) is list and all(type(cid) is str and cid in safe for cid in guarded), "privacy_pass")
    _need(len(set(guarded)) == len(guarded) and type(applicable) is dict and type(receipts) is dict, "privacy_pass")
    guardset, appset = set(guarded), set(applicable)
    _need(not guardset & appset and guardset | appset == safe and set(receipts) == appset, "privacy_pass")
    snapshot = _json(plan.snapshot_json)
    for cid, refs in applicable.items():
        _need(type(refs) is list and all(type(name) is str and name in snapshot for name in refs), "privacy_pass")
        _need(len(set(refs)) == len(refs) and refs == sorted(refs), "privacy_pass")
        receipt = receipts[cid]
        cases._keys(receipt, ("snapshot_sha256", "memory_sha256", "documents_sha256"))
        _need(receipt["snapshot_sha256"] == manifest["snapshot_sha256"]
              and receipt["memory_sha256"] == prepared[cid]["memory_sha256"]
              and all(cases._hex(receipt[key]) for key in ("snapshot_sha256", "memory_sha256", "documents_sha256")))
    output = {}
    for row in manifest["input_entries"]:
        cid = row["id"]
        output[cid] = {"incident_sha256": row["incident_sha256"], "evidence_sha256": row["evidence_sha256"],
                       "situation_sha256": row["situation_sha256"], "memory_snapshot_sha256": manifest["snapshot_sha256"],
                       "guarded": cid in blocked or cid in guardset,
                       "applicable": [] if cid in blocked or cid in guardset else applicable[cid]}
    b = cases._bound(plan.bindings)
    privacy = _canon({"schema": "wd.m1-p3-privacy-applicability.v1", "study_id": b["study_id"],
                      "protocol_sha256": manifest["protocol_original_sha256"], "baseline_manifest_sha256": b["baseline_manifest_sha256"],
                      "source_sha256": plan.adapter_source_sha256, "cases": output})
    reasons = {reason: sum(code == reason for code in blocked.values()) for reason in sorted(set(blocked.values()))}
    counts = {"accepted": len(output), "source_precheck_guarded": len(blocked), "source_precheck_by_reason": reasons,
              "native_not_run": len(blocked), "native_submitted": len(safe), "native_guarded": len(guardset),
              "native_applicable_nonempty": sum(bool(refs) for refs in applicable.values()),
              "native_applicable_empty": sum(not refs for refs in applicable.values())}
    provenance = _canon({"schema": "wd.m1-p3-pipeline-prepass-provenance.v1", "plan_sha256": expected_plan_sha256,
                         "original_native_prepass_sha256": native_prepass_sha256,
                         "harness_prepass_canonical_sha256": _sha(_harness_bytes(original)),
                         "privacy_adapter_sha256": plan.adapter_source_sha256, "native_runner_sha256": BLIND_RUNNER_SHA256,
                         "privacy_applicability_sha256": _sha(privacy), "source_precheck": blocked,
                         "prepared_receipts_original_sha256": prepared_receipts_sha256,
                         "prepared_memory_sha256_by_id": {cid: prepared[cid]["memory_sha256"] for cid in sorted(prepared)},
                         "native_case_receipts": receipts,
                         "opaque_receipt_hash_scope": "memory_matches_hash_bound_PREPARED; document_digest_bound_by_original_result; underlying payload rehash is native-worker-owned"})
    denominators = _canon(counts)
    _need(len(native_prepass) + len(prepared_receipts) + len(privacy) + len(provenance) + len(denominators) <= BUDGET_BYTES, "budget")
    return PrepassAdaptation(privacy, denominators, provenance, bytes(native_prepass))


@_closed
def complete_cases(plan, *, expected_plan_sha256, native_prepass, native_prepass_sha256,
                   prepared_receipts, prepared_receipts_sha256, _test_nonce_factory=None):
    """One pure composition AFTER validated prepass; no native/grade operation.

    Author fixtures inject nonce generation. Production phase intent and single
    invocation custody are external prerequisites, not provided by this API.
    """
    adapted = adapt_prepass(plan, expected_plan_sha256=expected_plan_sha256,
                            native_prepass=native_prepass, native_prepass_sha256=native_prepass_sha256,
                            prepared_receipts=prepared_receipts, prepared_receipts_sha256=prepared_receipts_sha256)
    result = cases.construct_cases(plan.bindings, plan.baseline, plan.entries,
        privacy_applicability=adapted.privacy_applicability_json,
        privacy_applicability_sha256=_sha(adapted.privacy_applicability_json),
        collector_counts=_json(plan.collector_counts_json, plan.collector_counts_sha256),
        _test_nonce_factory=_test_nonce_factory)
    _need(type(result) is cases.CaseArtifacts and result.snapshot_json == plan.snapshot_json)
    commitments = _json(result.commitments_json)
    safe_rows = {row["id"]: row for row in (_json(line) for line in plan.executor_inputs.blind_jsonl.splitlines())}
    kept_rows = [_json(line) for line in result.blind_jsonl.splitlines()]
    _need(type(commitments) is dict and len(kept_rows) == len(commitments)
          and {row["id"] for row in kept_rows} == set(commitments) <= set(safe_rows))
    _need(all(row == safe_rows[row["id"]] for row in kept_rows))
    times = _json(plan.executor_inputs.time_sidecar_json)
    kept_times = _canon({cid: times[cid] for cid in sorted(commitments)})
    provenance = _json(adapted.provenance_json)
    provenance.update({"schema": "wd.m1-p3-pipeline-completion.v1",
        "case_artifacts_sha256": {key: _sha(getattr(result, key)) for key in result.__dataclass_fields__},
        "kept_time_sidecar_sha256": _sha(kept_times), "full_safe_harness_input_sha256": _sha(plan.executor_inputs.runner_cases_json)})
    provenance_json = _canon(provenance)
    _need(sum(len(getattr(result, key)) for key in result.__dataclass_fields__)
          + len(adapted.privacy_applicability_json) + len(adapted.denominators_json)
          + len(provenance_json) + len(kept_times) <= BUDGET_BYTES, "budget")
    return PipelineArtifacts(result, plan.executor_inputs, adapted.original_native_prepass_json,
                             result.commitments_json, kept_times, adapted.privacy_applicability_json,
                             adapted.denominators_json, provenance_json)
