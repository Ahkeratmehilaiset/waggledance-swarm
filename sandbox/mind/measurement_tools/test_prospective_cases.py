"""Synthetic-only supplied-byte case glue tests; never open study inputs."""
import copy
import datetime as dt
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import sys
import uuid

import pytest

from sandbox.mind.measurement_tools import prospective_capture as capture


SPEC = importlib.util.spec_from_file_location(
    "prospective_cases", Path(__file__).with_name("prospective_cases.py")
)
cases = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = cases
SPEC.loader.exec_module(cases)


def test_frozen_sample_and_reason_enum():
    assert (cases.MIN_N, cases.CAP_KEPT, cases.SEED) == (40, 60, 20261019)
    assert len(cases.GRADER_REASONS) == 21


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def sha(value):
    return hashlib.sha256(value).hexdigest()


def nonces():
    counter = itertools.count(1)
    return lambda: f"{next(counter):064x}"


def synthetic(n=1, *, names=None, situations=None, kinds=None, times=None, capsule_patch=None,
              bridge_session_id=None):
    """Fabricated future records only; real history, memory or lessons are forbidden."""
    names = names or [f"synthetic_lesson_{i:03}.md" for i in range(n)]
    unique = sorted(set(names), key=lambda name: (name.casefold(), name))
    bodies = {name: f"---\nname: Synthetic label {i:03}\n---\nOriginal invented lesson body {i}.".encode()
              for i, name in enumerate(unique)}
    blobs = {sha(body): body for body in bodies.values()}
    study = "a" * 32
    native_session = str(uuid.UUID("2" * 32))
    reporter = {"agent": "synthetic-agent", "agent_uuid": "1" * 32,
                "session_id": bridge_session_id or native_session}
    protocol = raw({"schema": "wd.m1-p3-protocol-lock.v1", "status": "LOCKED", "study_id": study,
                    "reporter_sources": [{"reporter": reporter, "native_session_id": native_session}]})
    identity = {"schema": "wd.m1-p3-capture-authorization.v1", "status": "LOCKED_CAPTURE_GO",
                "study_id": study, "capture_id": "synthetic-capture", "source_dir": "C:/synthetic/source",
                "destination": "C:/synthetic/capture", "preregistration_sha256": "3" * 64,
                "protocol_sha256": sha(protocol)}
    manifest = raw({"schema": "wd.m1-p3-baseline.v1", "identity": identity,
                    "started_utc": "2035-04-05T05:59:50Z", "sealed_utc": "2035-04-05T05:59:52Z",
                    "files": [{"name": name, "sha256": sha(bodies[name]), "bytes": len(bodies[name])}
                              for name in unique]})
    seal = raw({"schema": "wd.m1-p3-baseline-seal.v1", "status": "SEALED",
                "manifest_sha256": sha(manifest), "sealed_utc": "2035-04-05T05:59:54Z"})
    capsule = {"schema": "wd.m1-p3-case-bindings.v1", "study_id": study, "protocol_sha256": sha(protocol),
               "baseline_manifest_sha256": sha(manifest), "baseline_seal_sha256": sha(seal),
               "capture_identity_sha256": sha(raw(identity)), "collection_start_utc": "2035-04-05T06:00:00Z",
               "collection_end_utc": "2035-04-06T06:00:00Z", "timestamp_resolution_us": 1_000_000,
               "baseline_resolution_us": 1_000_000, "max_record_delay_seconds": 120,
               "allowed_reporters": [reporter], "privacy_source_sha256": "4" * 64,
               "evidence_source_sha256": cases.EVIDENCE_SOURCE_SHA256, "source_commit": cases.MEASURED_SOURCE,
               "arm_a_version": "v21a", "arm_b_sha256": cases.BM25_SHA256, "grader_sha256": cases.GRADER_SHA256,
               "minimum_n": 40, "cap_n": 60, "seed": 20261019}
    capsule.update(capsule_patch or {})
    bindings = cases.Bindings.from_bytes(raw(capsule), expected_sha256=sha(raw(capsule)),
                                         protocol=protocol, protocol_sha256=sha(protocol))
    baseline = cases.Baseline.from_bytes(manifest, seal, blobs, bindings=bindings)
    snapshot = cases.fixed_snapshot(baseline, bindings=bindings)
    entries, guards = [], {}
    for i, name in enumerate(names):
        cid = f"{i + 1:032x}"
        action_uuid = str(uuid.UUID(int=i + 1000))
        stamp = (times or ["2035-04-05T06:01:00Z"] * n)[i]
        when = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        context = (situations or ["Inspect the synthetic service settings before applying changes."] * n)[i]
        user = {"type": "user", "sessionId": native_session, "uuid": str(uuid.UUID(int=i + 10000)),
                "timestamp": (when - dt.timedelta(seconds=3)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "message": {"role": "user", "content": context or "A temporary fabricated preaction context"}}
        action = {"type": "assistant", "sessionId": native_session, "uuid": action_uuid,
                  "timestamp": stamp, "message": {"role": "assistant", "content": [
                      {"type": "tool_use", "id": f"synthetic_tool_{i}", "name": "Edit",
                       "input": {"description": "ACTION INPUT MUST NOT ENTER SITUATION"}}]}}
        source = raw(user) + b"\n" + raw(action) + b"\n"
        evidence = capture.extract_action_evidence(source,
            reference={"session_id": native_session, "uuid": action_uuid,
                       "tool_use_id": f"synthetic_tool_{i}", "record_sha256": sha(raw(action)), "action_utc": stamp},
            baseline_utc=json.loads(seal)["sealed_utc"], collection_start_utc=capsule["collection_start_utc"],
            collection_end_utc=capsule["collection_end_utc"], timestamp_resolution_us=1_000_000)
        if not context:
            # Explicit malformed/empty upstream seam used only for exclusion tests.
            evidence["situation"] = ""
            evidence["situation_sha256"] = sha(b"")
        incident = {"schema": "wd.prospective-incident.v1", "study_id": study, "protocol_sha256": sha(protocol),
                    "baseline_sha256": sha(manifest), "incident_id": cid,
                    "incident_kind": (kinds or ["recurred"] * n)[i], "reporter": copy.deepcopy(reporter),
                    "action_time_utc": stamp, "recorded_at_utc": (when + dt.timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "action_ref": f"{i + 5000:032x}", "action_time_resolution_seconds": 1,
                    "action_sha256": evidence["record_sha256"], "action_receipt_sha256": "5" * 64,
                    "lesson_ref": cases.lesson_ref(name), "baseline_blob_sha256": sha(bodies[name])}
        entry = cases.CaseInput(raw(incident), sha(raw(incident)), raw(evidence), sha(raw(evidence)))
        entries.append(entry)
        guards[cid] = {"incident_sha256": entry.incident_sha256, "evidence_sha256": entry.evidence_sha256,
                       "situation_sha256": evidence["situation_sha256"], "memory_snapshot_sha256": sha(snapshot),
                       "guarded": False, "applicable": unique.copy()}
    near = sum(kind == "near_miss" for kind in kinds or [])
    privacy = {"schema": "wd.m1-p3-privacy-applicability.v1", "study_id": study,
               "protocol_sha256": sha(protocol), "baseline_manifest_sha256": sha(manifest),
               "source_sha256": capsule["privacy_source_sha256"], "cases": guards}
    counts = {"reported": n, "accepted": n, "refused_cases": 0, "duplicates": 0,
              "primary_candidates": n - near, "near_miss": near,
              "refusals_by_reason": {reason: 0 for reason in cases.COLLECTOR_REASONS}}
    return {"bindings": bindings, "baseline": baseline, "entries": entries, "privacy": privacy,
            "counts": counts, "capsule": capsule, "protocol": protocol}


def construct(bundle, **kwargs):
    privacy = raw(bundle["privacy"])
    return cases.construct_cases(bundle["bindings"], bundle["baseline"], bundle["entries"],
                                 privacy_applicability=privacy, privacy_applicability_sha256=sha(privacy),
                                 collector_counts=bundle["counts"], _test_nonce_factory=nonces(), **kwargs)


def replace_entry(bundle, index, *, incident=None, evidence=None):
    old = bundle["entries"][index]
    inc = raw(incident) if incident is not None else old.incident_json
    ev = raw(evidence) if evidence is not None else old.evidence_json
    new = cases.CaseInput(inc, sha(inc), ev, sha(ev))
    bundle["entries"][index] = new
    cid = json.loads(new.incident_json)["incident_id"]
    if cid in bundle["privacy"]["cases"]:
        guard = bundle["privacy"]["cases"][cid]
        guard.update(incident_sha256=new.incident_sha256, evidence_sha256=new.evidence_sha256,
                     situation_sha256=json.loads(new.evidence_json)["situation_sha256"])


def test_roundtrip_fixed_snapshot_commitment_and_strict_projection():
    bundle = synthetic(2)
    result = construct(bundle)
    rows, commits = json.loads(result.cases_json), json.loads(result.commitments_json)
    assert all(row["kept"] and row["drop_reason"] is None for row in rows)
    assert {row["memory_snapshot_sha256"] for row in rows} == {sha(result.snapshot_json)}
    assert {row["mtime"] for row in json.loads(result.snapshot_json).values()} == {"2035-04-05T05:59:55.000000Z"}
    for row in rows:
        exact = "|".join(row[key] for key in ("id", "time", "gold_basename", "situation_sha256", "memory_snapshot_sha256", "nonce"))
        assert commits[row["id"]] == sha(exact.encode())
    for row in [json.loads(line) for line in result.blind_jsonl.splitlines()]:
        assert set(row) == {"id", "situation", "snapshot_sha256"}
        assert "ACTION INPUT" not in row["situation"]
        assert not any(word in row["situation"] for word in ("Synthetic label", "synthetic_lesson", "nonce"))
    counts = json.loads(result.denominators_json)
    assert counts["kept"] == 2 and counts["dropped"] == 0
    assert set(counts["drops_by_grader_reason"]) == cases.GRADER_REASONS
    assert all(not row["kept"] and row["drop_reason"] is None for row in json.loads(result.cases_pre_json))


def test_exact_case_sensitive_lesson_reference():
    assert cases.lesson_ref("Synthetic.md") == sha(b"Synthetic.md")[:32]
    assert cases.lesson_ref("Synthetic.md") != cases.lesson_ref("synthetic.md")


def test_bridge_run_label_and_native_session_are_distinct_explicit_identities():
    bundle = synthetic(bridge_session_id="wd-reboot-synthetic-run-20350405")
    result = construct(bundle)
    row = json.loads(result.cases_json)[0]
    assert row["kept"] is True
    assert row["reporter"]["session_id"] == "wd-reboot-synthetic-run-20350405"
    evidence = json.loads(bundle["entries"][0].evidence_json)
    assert evidence["session_id"] != row["reporter"]["session_id"]


@pytest.mark.parametrize("mutation", ["missing", "unknown_reporter", "duplicate", "unknown_key", "non_uuid", "hex_alias"])
def test_original_locked_reporter_source_map_is_mandatory_and_exact(mutation):
    bundle = synthetic(bridge_session_id="wd-reboot-synthetic")
    lock = json.loads(bundle["protocol"])
    source = lock["reporter_sources"][0]
    if mutation == "missing":
        del lock["reporter_sources"]
    elif mutation == "unknown_reporter":
        source["reporter"]["session_id"] = "unmapped-run"
    elif mutation == "duplicate":
        lock["reporter_sources"].append(copy.deepcopy(source))
    elif mutation == "unknown_key":
        source["fallback"] = True
    elif mutation == "non_uuid":
        source["native_session_id"] = "wd-reboot-synthetic"
    else:
        source["native_session_id"] = "2" * 32
    protocol = raw(lock)
    capsule = bundle["capsule"].copy()
    capsule["protocol_sha256"] = sha(protocol)
    with pytest.raises(cases.NotReady):
        cases.Bindings.from_bytes(raw(capsule), expected_sha256=sha(raw(capsule)),
                                  protocol=protocol, protocol_sha256=sha(protocol))


def test_unmapped_bridge_reporter_never_falls_back_to_matching_native_session():
    bundle = synthetic(bridge_session_id="wd-reboot-synthetic")
    incident = json.loads(bundle["entries"][0].incident_json)
    incident["reporter"]["session_id"] = "a-different-run"
    replace_entry(bundle, 0, incident=incident)
    with pytest.raises(cases.NotReady, match="identity"):
        construct(bundle)


@pytest.mark.parametrize("name", ["../lesson.md", "C:/lesson.md", "MEMORY.md", "\u0131nvented.md", "unknown.txt"])
def test_lesson_reference_refuses_non_ascii_paths_and_non_lessons(name):
    with pytest.raises(cases.NotReady) as error:
        cases.lesson_ref(name)
    assert str(error.value) == "STOP_NOT_READY:baseline"


@pytest.mark.parametrize("patch", [
    {"minimum_n": 39}, {"cap_n": 61}, {"seed": 1}, {"arm_a_version": "v22"},
    {"source_commit": "0" * 40}, {"arm_b_sha256": "0" * 64}, {"grader_sha256": "0" * 64},
    {"evidence_source_sha256": "0" * 64}, {"baseline_resolution_us": 0},
    {"timestamp_resolution_us": True}, {"collection_end_utc": "UNKNOWN"},
    {"allowed_reporters": []}, {"unknown": "not allowed"},
])
def test_unknown_or_changed_pins_are_fatal(patch):
    with pytest.raises(cases.NotReady):
        synthetic(capsule_patch=patch)


@pytest.mark.parametrize("field", ["protocol_sha256", "baseline_sha256", "action_sha256", "lesson_ref", "baseline_blob_sha256"])
def test_incident_binding_change_is_fatal(field):
    bundle = synthetic()
    incident = json.loads(bundle["entries"][0].incident_json)
    incident[field] = "f" * (32 if field == "lesson_ref" else 64)
    replace_entry(bundle, 0, incident=incident)
    with pytest.raises(cases.NotReady, match="binding"):
        construct(bundle)


@pytest.mark.parametrize("field,value", [("session_id", str(uuid.UUID(int=4))), ("uuid", "UNKNOWN"),
                                        ("tool_use_id", None), ("record_sha256", "0" * 64),
                                        ("action_latest_utc", "2035-04-05T06:01:00Z"),
                                        ("timestamp_resolution_us", 0), ("situation_sha256", "0" * 64)])
def test_exact_evidence_binding_required(field, value):
    bundle = synthetic()
    evidence = json.loads(bundle["entries"][0].evidence_json)
    evidence[field] = value
    replace_entry(bundle, 0, evidence=evidence)
    with pytest.raises(cases.NotReady):
        construct(bundle)


@pytest.mark.parametrize("stamp,recorded", [
    ("2035-04-05T05:59:56Z", "2035-04-05T05:59:57Z"),  # baseline bound + one second tie
    ("2035-04-05T06:00:00Z", "2035-04-05T06:00:01Z"),  # collection start tie
    ("2035-04-06T05:59:59Z", "2035-04-06T06:00:00Z"),  # collection close tie
    ("2035-04-05T06:01:00Z", "2035-04-05T06:01:00Z"),  # not after action interval
    ("2035-04-05T06:01:00Z", "2035-04-05T06:03:01Z"),  # record delay exceeded
    ("2035-04-05T06:01:00.000000Z", "2035-04-05T06:01:01Z"),  # not exact recorder UTCsecond
])
def test_unknown_or_tied_action_clock_is_fatal_before_exclusion(stamp, recorded):
    bundle = synthetic(kinds=["near_miss"])
    incident = json.loads(bundle["entries"][0].incident_json)
    evidence = json.loads(bundle["entries"][0].evidence_json)
    when = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    incident.update(action_time_utc=stamp, recorded_at_utc=recorded)
    evidence.update(action_utc=stamp, action_latest_utc=(when + dt.timedelta(seconds=1)).isoformat(timespec="microseconds").replace("+00:00", "Z"))
    replace_entry(bundle, 0, incident=incident, evidence=evidence)
    with pytest.raises(cases.NotReady, match="clock"):
        construct(bundle)


@pytest.mark.parametrize("factory", [lambda: "not a nonce", lambda: "1" * 64,
                                    lambda: (_ for _ in ()).throw(ValueError("PRIVATE_SYNTHETIC_SENTINEL"))])
def test_invalid_duplicate_or_failed_nonce_generation_is_fatal_and_sanitized(factory):
    bundle = synthetic(2)
    privacy = raw(bundle["privacy"])
    with pytest.raises(cases.NotReady) as error:
        cases.construct_cases(bundle["bindings"], bundle["baseline"], bundle["entries"],
                              privacy_applicability=privacy, privacy_applicability_sha256=sha(privacy),
                              collector_counts=bundle["counts"], _test_nonce_factory=factory)
    assert str(error.value) == "STOP_NOT_READY:nonce"


@pytest.mark.parametrize("text,reason", [
    ("Use synthetic_lesson_000.md", "gold_name"), ("use SYNTHETIC_LESSON_000", "gold_name"),
    ("Use Synthetic label 000", "gold_name"), ("Check [[an unrelated lesson]]", "link"),
    ("This error RECURRED today", "recurred_word"),
])
def test_leak_filters_count_expected_exclusions(text, reason):
    bundle = synthetic(situations=[text])
    gold = next(iter(dict(bundle["baseline"].blobs).values()))
    assert reason in cases.leak_reasons(text, "synthetic_lesson_000.md", gold)
    result = construct(bundle)
    assert json.loads(result.cases_json)[0]["drop_reason"] == "leak"
    assert json.loads(result.denominators_json)["drops_by_grader_reason"]["leak"] == 1
    assert result.blind_jsonl == b"" and json.loads(result.commitments_json) == {}


def test_exact_shared_eight_token_filter_and_seven_token_boundary():
    gold = b"zero one two three four five six seven eight nine"
    assert cases.leak_reasons("one TWO three four five six seven eight", "invented.md", gold) == ("token_window",)
    assert cases.leak_reasons("one two three four five six seven", "invented.md", gold) == ()


@pytest.mark.parametrize("kind,text,guard,applicable,expected", [
    ("near_miss", "Inspect synthetic settings", False, None, "near_miss"),
    ("recurred", "", False, None, "situation_empty"),
    ("recurred", "Inspect synthetic settings", True, None, "situation_guarded"),
    ("recurred", "Inspect synthetic settings", False, [], "empty_applicable_corpus"),
    ("recurred", "Inspect C:/synthetic settings", False, None, "situation_guarded"),
])
def test_expected_exclusions_are_retained(kind, text, guard, applicable, expected):
    bundle = synthetic(kinds=[kind], situations=[text])
    result_guard = next(iter(bundle["privacy"]["cases"].values()))
    result_guard["guarded"] = guard
    if applicable is not None:
        result_guard["applicable"] = applicable
    result = construct(bundle)
    assert json.loads(result.cases_json)[0]["drop_reason"] == expected
    counts = json.loads(result.denominators_json)
    assert counts["case_inputs"] == counts["dropped"] == 1
    assert counts["drops_by_grader_reason"][expected] == 1


def test_gold_engine_invisible_is_a_case_exclusion():
    bundle = synthetic(2)
    bundle["privacy"]["cases"]["0" * 31 + "1"]["applicable"] = ["synthetic_lesson_001.md"]
    result = construct(bundle)
    assert json.loads(result.cases_json)[0]["drop_reason"] == "gold_engine_invisible"


@pytest.mark.parametrize("mutation", ["missing", "extra", "missing_guard", "non_bool", "hash", "source", "unknown_name", "duplicate_applicable"])
def test_privacy_pass_never_defaults_missing_or_invalid_results(mutation):
    bundle = synthetic()
    results = bundle["privacy"]["cases"]
    cid = next(iter(results))
    guard = results[cid]
    if mutation == "missing":
        del results[cid]
    elif mutation == "extra":
        results["f" * 32] = copy.deepcopy(guard)
    elif mutation == "missing_guard":
        del guard["guarded"]
    elif mutation == "non_bool":
        guard["guarded"] = 0
    elif mutation == "hash":
        guard["memory_snapshot_sha256"] = "0" * 64
    elif mutation == "source":
        bundle["privacy"]["source_sha256"] = "0" * 64
    elif mutation == "unknown_name":
        guard["applicable"].append("unknown.md")
    else:
        guard["applicable"] *= 2
    with pytest.raises(cases.NotReady):
        construct(bundle)


def test_dedupe_earliest_action_then_id_before_fixed_sampling():
    bundle = synthetic(3, names=["lesson.md"] * 3,
                       times=["2035-04-05T06:02:00Z", "2035-04-05T06:01:00Z", "2035-04-05T06:01:00Z"])
    result = construct(bundle)
    kept = [row for row in json.loads(result.cases_json) if row["kept"]]
    assert [row["id"] for row in kept] == [f"{2:032x}"]
    counts = json.loads(result.denominators_json)
    assert counts["eligible_before_gold_dedupe"] == 3
    assert counts["eligible_distinct_gold"] == counts["kept"] == 1
    assert counts["drops_by_grader_reason"]["duplicate_gold"] == 2


def test_sample_is_exact_frozen_rng_over_sorted_ids_and_order_independent():
    bundle = synthetic(64)
    result = construct(bundle)
    import random
    ids = sorted(json.loads(result.commitments_json))
    expected = sorted(random.Random(20261019).sample(sorted(f"{i + 1:032x}" for i in range(64)), 60))
    assert ids == expected
    bundle["entries"].reverse()
    reverse_result = construct(bundle)
    assert reverse_result == result
    assert json.loads(result.denominators_json)["drops_by_grader_reason"]["not_sampled"] == 4


def test_all_collector_denominators_and_refusals_remain_separate():
    bundle = synthetic()
    bundle["counts"].update(reported=4, refused_cases=3, duplicates=1)
    bundle["counts"]["refusals_by_reason"].update(schema=2, duplicate=1)
    counts = json.loads(construct(bundle).denominators_json)
    assert counts["collector"] == bundle["counts"]
    assert counts["drops_by_grader_reason"]["duplicate_incident"] == 0
    bundle["counts"]["accepted"] = 2
    with pytest.raises(cases.NotReady, match="prerequisite"):
        construct(bundle)


def test_duplicate_native_action_is_fatal_even_if_opaque_refs_differ():
    bundle = synthetic(2)
    incident = json.loads(bundle["entries"][1].incident_json)
    evidence = json.loads(bundle["entries"][0].evidence_json)
    incident["action_sha256"] = evidence["record_sha256"]
    replace_entry(bundle, 1, incident=incident, evidence=evidence)
    with pytest.raises(cases.NotReady, match="duplicate"):
        construct(bundle)


def test_duplicate_opaque_action_receipt_is_also_fatal():
    bundle = synthetic(2)
    incident = json.loads(bundle["entries"][1].incident_json)
    incident["action_ref"] = json.loads(bundle["entries"][0].incident_json)["action_ref"]
    replace_entry(bundle, 1, incident=incident)
    with pytest.raises(cases.NotReady, match="duplicate"):
        construct(bundle)


def test_truncated_lesson_reference_collision_is_fatal(monkeypatch):
    monkeypatch.setattr(cases, "lesson_ref", lambda name: "a" * 32)
    with pytest.raises(cases.NotReady, match="binding"):
        synthetic(2)


@pytest.mark.parametrize("field,value", [("privacy_applicability", None), ("entries", None),
                                        ("entries", ["PRIVATE_SYNTHETIC_SENTINEL"]),
                                        ("collector_counts", [])])
def test_malformed_public_inputs_fail_closed_without_echo(field, value):
    bundle = synthetic()
    privacy = raw(bundle["privacy"])
    args = {"bindings": bundle["bindings"], "baseline": bundle["baseline"], "entries": bundle["entries"],
            "privacy_applicability": privacy, "privacy_applicability_sha256": sha(privacy),
            "collector_counts": bundle["counts"], "_test_nonce_factory": nonces()}
    args[field] = value
    with pytest.raises(cases.NotReady) as error:
        cases.construct_cases(**args)
    assert "PRIVATE_SYNTHETIC_SENTINEL" not in str(error.value)
    assert str(error.value).startswith("STOP_NOT_READY:")


def test_empty_collection_is_explicit_and_generates_no_nonces():
    bundle = synthetic()
    bundle["entries"] = []
    bundle["privacy"]["cases"] = {}
    bundle["counts"].update(reported=0, accepted=0, primary_candidates=0)
    result = construct(bundle)
    assert result.blind_jsonl == b"" and json.loads(result.cases_json) == []
    counts = json.loads(result.denominators_json)
    assert counts["kept"] == counts["dropped"] == counts["case_inputs"] == 0


def test_default_nonces_are_invocation_only_and_never_executor_fields(monkeypatch):
    bundle = synthetic(2)
    calls = []
    factory = nonces()
    monkeypatch.setattr(cases.secrets, "token_hex", lambda size: calls.append(size) or factory())
    check = importlib.util.module_from_spec(SPEC)
    sys.modules[SPEC.name] = check
    try:
        SPEC.loader.exec_module(check)
    finally:
        sys.modules[SPEC.name] = cases
    assert calls == []
    privacy = raw(bundle["privacy"])
    result = cases.construct_cases(bundle["bindings"], bundle["baseline"], bundle["entries"],
                                  privacy_applicability=privacy, privacy_applicability_sha256=sha(privacy),
                                  collector_counts=bundle["counts"])
    assert calls == [32, 32]
    assert b'"nonce"' not in result.blind_jsonl and b'"gold_basename"' not in result.blind_jsonl
    assert "synthetic_lesson" not in repr(result) and "synthetic_lesson" not in repr(bundle["baseline"])


def test_wrong_hash_duplicate_keys_and_nonfinite_input_refuse():
    bundle = synthetic()
    old = bundle["entries"][0]
    for data, expected in [(old.incident_json, "0" * 64), (b'{"x":1,"x":2}', None), (b'{"x":NaN}', None)]:
        bad = cases.CaseInput(data, sha(data) if expected is None else expected, old.evidence_json, old.evidence_sha256)
        bundle["entries"] = [bad]
        with pytest.raises(cases.NotReady):
            construct(bundle)


def test_baseline_blobs_hash_order_and_case_collision_are_fatal():
    bundle = synthetic(2)
    baseline, bindings = bundle["baseline"], bundle["bindings"]
    blobs = dict(baseline.blobs)
    blobs[next(iter(blobs))] = b"tampered synthetic body"
    with pytest.raises(cases.NotReady, match="binding"):
        cases.Baseline.from_bytes(baseline.manifest_json, baseline.seal_json, blobs, bindings=bindings)
    with pytest.raises(cases.NotReady, match="baseline"):
        synthetic(2, names=["Lesson.md", "lesson.md"])


def test_no_filesystem_or_clock_needed_and_inputs_are_not_mutated(monkeypatch, capsys):
    bundle = synthetic()
    before = copy.deepcopy(bundle)
    monkeypatch.setattr(Path, "read_bytes", lambda *a: pytest.fail("unexpected file read"))
    monkeypatch.setattr(Path, "write_bytes", lambda *a: pytest.fail("unexpected file write"))
    result = construct(bundle)
    assert bundle == before
    assert capsys.readouterr() == ("", "")
    assert json.loads(result.denominators_json)["kept"] == 1
