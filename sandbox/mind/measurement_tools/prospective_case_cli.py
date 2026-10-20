"""Dormant P3 input-seal and immutable-reload caller; no model/worker/scorer.

Separate original CASE_INPUT_SEAL_GO consumes a fresh seal_inputs intent BEFORE
private payload reads. Exactly one admitted bounded native evidence extraction;
immutable private bytes then serve BOTH later publisher phases without another
extraction. Original hashes and full refusal provenance are retained separately
from the frozen4-category grouping. GO origin/actual qualified actor and loaded
runtime remain external duties; Source token metadata does not qualify anyone.
No actual data authority is minted. Only explicit original controlled paths.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import functools
import json
import os
from pathlib import Path
import stat
import hashlib
import sys
import types


def _frozen_helpers():
    """Literal Source-only bootstrap for direct -I -B -S script execution.

    No sys.path mutation, package discovery, dependency installation or study
    input read. Compile the ORIGINAL verified bytes, never a later reread.
    Existing in-process modules are only for ordinary tests/API callers; actual
    qualified CLI launch must use a fresh interpreter and external Source pin.
    """
    folder = Path("C:/Python/project2/sandbox/mind/measurement_tools")
    pins = (("prospective_capture", "4203b3215c446020b57940b69deccbf3694af4b936af468685a439867a300b12"),
        ("prospective_incidents", "f015a09c56c192de474fe5a9590f6c26e603386adc0c62c1e4ba27e5cff5cdd0"),
        ("prospective_cases", "8fbc095c80227d67bc1d8ef59274add608fe197407a34193a688a33b4f2c729a"),
        ("prospective_pipeline", "9d4f79fa3fa1fa280a7ffa2981d06824a69941a8505a1074703eff1fcb4fe06a"),
        ("prospective_publish", "a948f6da3d045de20d9b74aa97bccf313bc423a502d8264b839a59146a3d3c0f"),
        ("prospective_collection", "375ef01c95334a7c8ff20d196f3eb853998fdd5083e49b8554aa33de9abf7737"),
        ("prospective_native", "6511543f33c25f430eabd85b66b40ecc335b683827da7a7856ba7722ad80187d"))
    for package in ("sandbox", "sandbox.mind", "sandbox.mind.measurement_tools"):
        if package not in sys.modules:
            module = types.ModuleType(package)
            module.__path__ = []
            sys.modules[package] = module
            if "." in package:
                parent, child = package.rsplit(".", 1)
                setattr(sys.modules[parent], child, module)
    loaded = {}
    for name, expected in pins:
        path = folder / (name + ".py")
        for part in (path, *path.parents):
            info = part.lstat()
            if part.is_symlink() or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("source")
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 100000:
            raise ValueError("source")
        body = path.read_bytes()
        after = path.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or hashlib.sha256(body).hexdigest() != expected:
            raise ValueError("source")
        full = "sandbox.mind.measurement_tools." + name
        if full not in sys.modules:
            module = types.ModuleType(full)
            module.__file__ = str(path)
            sys.modules[full] = module
            setattr(sys.modules["sandbox.mind.measurement_tools"], name, module)
            exec(compile(body, str(path), "exec"), module.__dict__)
        else:
            module = sys.modules[full]
            if Path(module.__file__) != path:
                raise ValueError("source")
        loaded[name] = module
    return loaded


try:
    _helpers = _frozen_helpers()
    pub, collector, recorder = (_helpers[name] for name in ("prospective_publish", "prospective_collection", "prospective_incidents"))
    native = _helpers["prospective_native"]
except BaseException:
    print('{"actual_data_opened":false,"invocation_consumed":false,"reason":"source","status":"STOP_NOT_READY"}')
    raise SystemExit(2) from None


cap, cases = pub.capture, pub.pipe.cases
COLLECTOR_SHA256 = "375ef01c95334a7c8ff20d196f3eb853998fdd5083e49b8554aa33de9abf7737"
PUBLISHER_SHA256 = "a948f6da3d045de20d9b74aa97bccf313bc423a502d8264b839a59146a3d3c0f"
RECORDER_SHA256 = "f015a09c56c192de474fe5a9590f6c26e603386adc0c62c1e4ba27e5cff5cdd0"
NotReady = pub.NotReady


def need(ok, reason="binding"):
    if not ok:
        raise NotReady(reason)


def sha(body):
    return pub._sha(body)


def parse(body, expected=None):
    need(expected is None or cases._hex(expected))
    return cases._json(body, expected)


def keys(value, fields):
    need(type(value) is dict and set(value) == set(fields), "schema")


def _read(path, stack, expected=None, limit=pub.BUDGET_BYTES):
    path = cap._path(path)
    cap._pin_chain(stack, path)
    info = path.stat()
    need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= limit, "path")
    body = cap._read_bounded(path, limit)
    if expected is not None:
        need(cases._hex(expected) and sha(body) == expected)
    return body


def derive_capsule(protocol, preregistration, manifest, seal):
    """Deterministic byte capsule from ORIGINAL lock fields, never observed gold."""
    p, pr, m, s = map(parse, (protocol, preregistration, manifest, seal))
    need(p.get("schema") == "wd.m1-p3-protocol-lock.v1" and p.get("status") == "LOCKED"
         and pr.get("schema") == "wd.m1-prospective-preregistration.v1" and pr.get("status") == "LOCKED"
         and p.get("study_id") == pr.get("study_id") and p.get("preregistration_sha256") == sha(preregistration), "go")
    params = p.get("case_parameters")
    keys(params, ("timestamp_resolution_us", "baseline_resolution_us", "max_record_delay_seconds", "minimum_n",
        "cap_n", "seed", "source_commit", "arm_a_version", "arm_b_sha256", "grader_sha256",
        "evidence_source_sha256", "privacy_source_sha256"))
    need(params["privacy_source_sha256"] == pub.PIPELINE_SHA256 and params["evidence_source_sha256"] == pub.CAPTURE_SHA256)
    cfg = p["collection_controller"]
    need(params["max_record_delay_seconds"] == cfg["max_record_delay_seconds"]
         and params["baseline_resolution_us"] == cfg["baseline_resolution_us"])
    value = {"schema": "wd.m1-p3-case-bindings.v1", "study_id": p["study_id"], "protocol_sha256": sha(protocol),
        "baseline_manifest_sha256": sha(manifest), "baseline_seal_sha256": sha(seal),
        "capture_identity_sha256": sha(pub._canon(m["identity"])),
        "collection_start_utc": cfg["collection_start_utc"], "collection_end_utc": cfg["collection_end_utc"],
        "allowed_reporters": cfg["allowed_reporters"], **params}
    body = pub._canon(value)
    cases.Bindings.from_bytes(body, expected_sha256=sha(body), protocol=protocol, protocol_sha256=sha(protocol))
    return body


def _source_bytes(source, proof, stack):
    keys(proof["file_identity"], ("device", "inode"))
    need(proof["file_identity"] == source["file_identity"])
    path = cap._path(source["evidence_path"])
    cap._pin_chain(stack, path)
    with path.open("rb", buffering=0) as stream:
        data, receipt = collector.bounded_range(stream, start=proof["start"], end=proof["end"],
            expected_identity=source["file_identity"], limit=source["max_range_bytes"])
    need(receipt["range_sha256"] == proof["range_sha256"] and receipt["read_bytes"] == proof["read_bytes"]
         and receipt["source_size"] >= proof["source_size"] and receipt["end"] == proof["end"])
    return data


def closed_entries(protocol, manifest, seal, *, terminal, terminal_sha256, read, read_range):
    """Verify exact CLOSED journal/ledger, reconstruct only accepted evidence.

    read/read_range are private mechanical boundaries; the CLI supplies ONLY
    pinned protocol-controlled paths. Pure synthetic tests may supply invented
    byte maps. Original refused events are retained unchanged in controller.
    """
    p, m = parse(protocol), parse(manifest)
    cfg, control, ledger = p["collection_controller"], Path(p["collection_controller"]["control_root"]), Path(p["collection_controller"]["ledger_root"])
    end = parse(terminal, terminal_sha256)
    keys(end, ("schema", "status", "reason", "counts", "journal_sha256"))
    need(end["schema"] == "wd.m1-p3-collection-result.v1" and end["status"] == "CLOSED"
         and end["reason"] in ("collection_closed", "manual_stop"), "prior")
    count_keys = ("reported", "accepted", "primary_candidates", "near_miss", "refused", "duplicates")
    keys(end["counts"], count_keys)
    need(all(type(n) is int and n >= 0 for n in end["counts"].values()) and end["counts"]["reported"] <= 4096 and end["counts"]["accepted"] <= cfg["max_records"], "prior")
    intent = parse(read(control / "intent.json"))
    need(intent.get("schema") == "wd.m1-p3-collection-intent.v1" and intent.get("attempt_consumed") is True
         and intent.get("study_id") == p["study_id"] and intent.get("protocol_sha256") == sha(protocol)
         and intent.get("baseline_manifest_sha256") == sha(manifest) and cases._hex(intent.get("go_sha256")), "prior")
    pc, bc = read(control / "protocol-capsule.json"), read(control / "baseline-capsule.json")
    header = parse(read(ledger / "ledger.json"))
    keys(header, ("schema", "run_id", "study_id", "protocol_sha256", "baseline_sha256", "protocol_capsule_sha256", "baseline_capsule_sha256"))
    need(header["schema"] == "wd.prospective-ledger.v1" and cases._id(header["run_id"])
         and header["study_id"] == p["study_id"] and header["protocol_sha256"] == sha(protocol)
         and header["baseline_sha256"] == sha(manifest) and header["protocol_capsule_sha256"] == sha(pc)
         and header["baseline_capsule_sha256"] == sha(bc), "prior")
    go = {"protocol_sha256": sha(protocol), "baseline_manifest_sha256": sha(manifest)}
    expected_pc, expected_bc, mapping, _ = collector.capsules(p, go, cfg, m, parse(seal), recorder, cap)
    need(pc == expected_pc and bc == expected_bc)
    need(parse(read(control / "lesson-mapping.json")) == mapping
         and read(control / "LOCK") == b"0" and read(ledger / "LOCK") == b"0", "prior")
    sources = {row["id"]: row for row in cfg["sources"]}
    need(len(sources) == len(cfg["sources"]))
    counts = {key: 0 for key in count_keys}
    need(p.get("collector_refusal_mapping") == REFUSAL_MAPPING, "binding")
    original_reasons = {key: 0 for key in REFUSAL_MAPPING}
    reasons = {key: 0 for key in cases.COLLECTOR_REASONS}
    event_bodies = [read(control / f"event-{n:08d}.json") for n in range(1, end["counts"]["reported"] + 1)]
    need(sum(map(len, event_bodies)) <= pub.BUDGET_BYTES, "budget")
    journal = [sha(body) for body in event_bodies]
    # Authenticate the exact complete original byte chain BEFORE extracting
    # anything from a native range selected by an event.
    need(sha(pub._canon(journal)) == end["journal_sha256"], "prior")
    entries, accepted, ranges, cursors, last, native_refs = [], set(), {}, {}, {}, []
    expected_control = {"intent.json", "LOCK", "protocol-capsule.json", "baseline-capsule.json", "lesson-mapping.json", "RESULT.json"}
    for sequence, body in enumerate(event_bodies, 1):
        expected_control.add(f"event-{sequence:08d}.json")
        event = parse(body)
        keys(event, ("schema", "sequence", "status", "nomination_sha256", "incident_id", "proof", "counts"))
        need(event["schema"] == "wd.m1-p3-collection-event.v1" and event["sequence"] == sequence
             and cases._hex(event["nomination_sha256"]))
        counts["reported"] += 1
        keys(event["status"], ("disposition", "reason"))
        proof = event["proof"]
        if proof is not None and "source_range" in proof:
            rp = proof["source_range"]
            keys(rp, ("schema", "source_id", "cursor_before", "overlap_bytes", "lookback_validation", "previous_range_receipt_sha256",
                "file_identity", "start", "end", "source_size", "read_bytes", "range_sha256", "receipt_sha256", "replay", "cursor_after"))
            need(rp["schema"] == "wd.m1-p3-source-range.v1" and rp["source_id"] in sources and type(rp["replay"]) is bool)
            source = sources[rp["source_id"]]
            need(rp["file_identity"] == source["file_identity"]
                 and all(type(rp[k]) is int for k in ("cursor_before", "start", "end", "overlap_bytes", "source_size", "read_bytes", "cursor_after"))
                 and 0 <= rp["start"] < rp["end"] <= rp["source_size"] < 2**63
                 and rp["end"] - rp["start"] <= source["max_range_bytes"]
                 and rp["read_bytes"] == rp["end"] - rp["start"] + int(rp["start"] > 0)
                 and cases._hex(rp["range_sha256"]) and cases._hex(rp["receipt_sha256"]), "prior")
            cursor = cursors.get(source["id"], source["initial_cursor"])
            key = (source["id"], rp["start"], rp["end"])
            if rp["replay"]:
                need(key in ranges and ranges[key] == {k: v for k, v in rp.items() if k not in ("replay", "cursor_after")})
            else:
                need(key not in ranges and rp["cursor_before"] == cursor and rp["start"] <= cursor < rp["end"]
                     and rp["overlap_bytes"] == cursor - rp["start"] and rp["previous_range_receipt_sha256"] == last.get(source["id"]))
                receipt = read(control / f"source-range-{sequence:08d}.json")
                expected_control.add(f"source-range-{sequence:08d}.json")
                stored = {k: v for k, v in rp.items() if k not in ("replay", "cursor_after", "receipt_sha256")}
                need(sha(receipt) == rp["receipt_sha256"] and parse(receipt) == stored)
                if rp["start"] < cursor:
                    look = rp["lookback_validation"]
                    keys(look, ("receipt_sha256", "range_sha256", "read_bytes"))
                    old = [x for x in ranges.values() if x["source_id"] == source["id"] and x["end"] == cursor and x["receipt_sha256"] == last.get(source["id"])]
                    need(len(old) == 1 and old[0]["start"] <= rp["start"] and look["receipt_sha256"] == old[0]["receipt_sha256"] and look["range_sha256"] == old[0]["range_sha256"])
                else:
                    need(rp["lookback_validation"] is None)
                ranges[key] = {k: v for k, v in rp.items() if k not in ("replay", "cursor_after")}
                cursors[source["id"]], last[source["id"]] = rp["end"], rp["receipt_sha256"]
            need(rp["cursor_after"] == cursors[source["id"]])
        if event["status"]["disposition"] == "REFUSE_CASE":
            reason = event["status"]["reason"]
            need(reason in REFUSAL_MAPPING and event["incident_id"] is None, "prior")
            original_reasons[reason] += 1
            reasons[REFUSAL_MAPPING[reason]] += 1
            counts["refused"] += 1
            counts["duplicates"] += int(reason == "duplicate")
        else:
            need(event["status"] == {"disposition": "RECORDED", "reason": None} and type(proof) is dict)
            keys(proof, ("schema", "session_id", "uuid", "tool_use_id", "record_sha256", "action_utc", "action_latest_utc",
                "timestamp_resolution_us", "supplied_jsonl_sha256", "evidence_file", "source_id", "source_range",
                "native_record_dispositions", "action_receipt_sha256", "record_sha256_receipt"))
            cid = event["incident_id"]
            need(cases._id(cid) and cid not in accepted and proof["source_id"] in sources)
            accepted.add(cid)
            source = sources[proof["source_id"]]
            need(source["schema"] == "wd.m1-p3-claude-jsonl.v1" and proof["session_id"] == source["native_session_id"]
                 and proof["evidence_file"] == Path(source["evidence_path"]).name)
            native = read_range(source, proof["source_range"])
            native_refs.append({"source_id": source["id"], "evidence_path": source["evidence_path"], **proof["source_range"]})
            reference = {key: proof[key] for key in ("session_id", "uuid", "tool_use_id", "record_sha256", "action_utc")}
            evidence = cap.extract_action_evidence(native, reference=reference, baseline_utc=parse(seal)["sealed_utc"],
                collection_start_utc=cfg["collection_start_utc"], collection_end_utc=cfg["collection_end_utc"],
                timestamp_resolution_us=source["timestamp_resolution_us"])
            need(all(evidence[key] == proof[key] for key in ("schema", "session_id", "uuid", "tool_use_id", "record_sha256",
                "action_utc", "action_latest_utc", "timestamp_resolution_us", "supplied_jsonl_sha256"))
                 and cap.native_record_dispositions(native) == proof["native_record_dispositions"])
            incident = read(ledger / ("incident-" + cid + ".json"))
            value = parse(incident)
            need(sha(incident) == proof["record_sha256_receipt"] and value["incident_id"] == cid
                 and value["action_receipt_sha256"] == proof["action_receipt_sha256"] and value["reporter"] == source["reporter"])
            action_receipt = pub._canon({"schema": "wd.prospective-action.v1", "action_ref": value["action_ref"], "reporter": value["reporter"],
                "action_time_utc": value["action_time_utc"], "action_time_resolution_seconds": 1, "source_record_sha256": evidence["record_sha256"]})
            need(sha(action_receipt) == proof["action_receipt_sha256"])
            entries.append(cases.CaseInput(incident, sha(incident), pub._canon(evidence), sha(pub._canon(evidence))))
            counts["accepted"] += 1
            need(value["incident_kind"] in ("recurred", "near_miss"))
            counts["primary_candidates" if value["incident_kind"] == "recurred" else "near_miss"] += 1
        need(event["counts"] == counts, "prior")
    need(counts == end["counts"] and sha(pub._canon(journal)) == end["journal_sha256"], "prior")
    need({x.name for x in control.iterdir()} == expected_control, "prior")
    adapted = {key: counts[key] for key in ("reported", "accepted", "duplicates", "primary_candidates", "near_miss")}
    adapted.update({"refused_cases": counts["refused"], "refusals_by_reason": reasons})
    provenance = {"schema": "wd.m1-p3-collector-refusal-mapping.v1", "mapping": REFUSAL_MAPPING,
        "original_counts": end["counts"], "original_refusals_by_reason": original_reasons,
        "derived_counts": adapted, "original_terminal_sha256": terminal_sha256,
        "original_journal_sha256": end["journal_sha256"], "original_event_sha256": journal}
    return entries, pub._canon(adapted), accepted, pub._canon(provenance), native_refs



REFUSAL_MAPPING = {"schema": "schema", "unsupported_native_schema": "schema",
    "unsupported_native_record": "schema", "unsupported_resolution": "time",
    "outside_window": "time", "empty_context": "schema", "duplicate": "duplicate"}
SEAL_PINS = ("collection_result_sha256", "journal_sha256", "baseline_manifest_sha256",
    "baseline_seal_sha256", "runner_binding_sha256")
STAGE = "private/case_cli/seal_inputs"


def _closed(fn):
    @functools.wraps(fn)
    def call(*a, **kw):
        try:
            return fn(*a, **kw)
        except NotReady:
            raise
        except (OSError, TypeError, ValueError, KeyError, AttributeError, OverflowError,
                RecursionError, cap.CaptureError, cases.NotReady, collector.Stop):
            raise NotReady("schema") from None
    return call


def _identity(protocol, protocol_sha256, preregistration, preregistration_sha256):
    need(cases._hex(protocol_sha256) and cases._hex(preregistration_sha256), "go")
    p, pr = parse(protocol, protocol_sha256), parse(preregistration, preregistration_sha256)
    need(p.get("schema") == "wd.m1-p3-protocol-lock.v1" and p.get("status") == "LOCKED"
         and pr.get("schema") == "wd.m1-prospective-preregistration.v1" and pr.get("status") == "LOCKED"
         and cases._id(p.get("study_id")) and p["study_id"] == pr.get("study_id")
         and p.get("preregistration_sha256") == preregistration_sha256, "go")
    keys(p.get("case_cli"), ("path", "sha256"))
    need(cap._path(p["case_cli"]["path"]) == cap._path(__file__)
         and p["case_cli"]["sha256"] == sha(Path(__file__).read_bytes()), "source")
    need(p.get("collector_refusal_mapping") == REFUSAL_MAPPING, "binding")
    cfg = p["collection_controller"]
    keys(cfg, pub._COLLECTION_FIELDS)
    root = cap._path(p["study_root"], audit=True)
    need(cap._path(p["publisher"]["root"], audit=True) == root, "path")
    need(cap._path(cfg["audit_root"]) == cap.AUDIT_ROOT, "path")
    control, ledger = (cap._path(cfg[k], audit=True) for k in ("control_root", "ledger_root"))
    need(control.parent == cap.AUDIT_ROOT and ledger.parent == cap.AUDIT_ROOT, "path")
    forest = (root, control, ledger)
    cap._disjoint(forest)
    paths = [cap._path(cfg[k], audit=True) for k in ("inbox", "baseline_root", "blind_root", "score_root")]
    need(all(root in path.parents for path in paths), "path")
    cap._disjoint(paths + [control, ledger])
    need(paths[2] == root / "blind" and paths[3] == root / "score"
         and cap._path(cfg["stop_signal"], audit=True) == paths[0] / "STOP.json", "path")
    need(type(p.get("publisher_authorizer")) is dict and p["publisher_authorizer"] == p.get("native_authorizer"), "go")
    cases._identity(p["publisher_authorizer"])
    return p, forest


def _pin_source(stack, p):
    for module, digest in ((collector, COLLECTOR_SHA256), (recorder, RECORDER_SHA256),
        (pub, PUBLISHER_SHA256), (cap, pub.CAPTURE_SHA256), (cases, pub.CASES_SHA256),
        (pub.pipe, pub.PIPELINE_SHA256), (native, pub.NATIVE_SHA256)):
        _read(module.__file__, stack, digest)
    _read(__file__, stack, p["case_cli"]["sha256"])


def _medium_identity():
    # Only the frozen stdlib metadata function; NEVER native main/execute/SUT.
    return native.token_identity()


def _require_medium(test_identity=None):
    try:
        identity = _medium_identity() if test_identity is None else test_identity()
        need(type(identity) is dict and type(identity.get("integrity_rid")) is int
             and identity["integrity_rid"] == 8192 and identity.get("elevated") is False
             and identity.get("ABI_bits") == 64 and identity.get("python", {}).get("sha256") == native.PYTHON_SHA256, "source")
        return identity, test_identity is not None
    except BaseException:
        raise NotReady("source") from None


def _owner(p, protocol_sha256, root, stack):
    expected = {"schema": "wd.m1-p3-study-root.v1", "study_id": p["study_id"],
        "protocol_sha256": protocol_sha256, "root": str(root)}
    need(parse(_read(root / "OWNER.json", stack)) == expected, "binding")


def _halting(root):
    for path in (root / "private/HALTED.json", root / "private/case_cli/HALTED.json",
                 root / "native/HALTED.json"):
        need(not os.path.lexists(path), "consumed")


def _seal_authority(p, protocol_sha256, prereg_sha256, binding_sha256, go, go_sha256):
    need(cases._hex(go_sha256) and cases._hex(binding_sha256), "go")
    value = parse(go, go_sha256)
    keys(value, ("schema", "status", "phase", "study_id", "protocol_sha256", "source_sha256",
        "preregistration_sha256", "authorizer", "event_ref", "issued_at_utc", "expires_at_utc", "pins"))
    keys(value["pins"], SEAL_PINS)
    need(all(cases._hex(x) for x in value["pins"].values()), "go")
    need(value["schema"] == "wd.m1-p3-case-cli-go.v1" and value["status"] == "CASE_INPUT_SEAL_GO"
         and value["phase"] == "seal_inputs" and value["study_id"] == p["study_id"]
         and value["protocol_sha256"] == protocol_sha256 and value["source_sha256"] == p["case_cli"]["sha256"]
         and value["preregistration_sha256"] == prereg_sha256 and value["authorizer"] == p["publisher_authorizer"]
         and cases._hex(value["event_ref"]) and value["pins"]["runner_binding_sha256"] == binding_sha256, "go")
    issued, expires, now = cap.parse_utc(value["issued_at_utc"]), cap.parse_utc(value["expires_at_utc"]), pub._now()
    need(type(now) is dt.datetime and now.tzinfo == dt.timezone.utc and issued <= now < expires
         and dt.timedelta(0) < expires-issued <= dt.timedelta(seconds=1800)
         and cap.parse_utc(p["collection_controller"]["collection_end_utc"]) <= issued, "clock")
    return value, now, expires


def _prepare_from_originals(originals):
    declaration = parse(originals["declarations.json"])
    bindings = cases.Bindings.from_bytes(originals["capsule.json"], expected_sha256=declaration["capsule_sha256"],
        protocol=originals["protocol.json"], protocol_sha256=declaration["protocol_sha256"])
    blobs = {name[6:-4]: body for name, body in originals.items() if name.startswith("blobs/")}
    baseline = cases.Baseline.from_bytes(originals["baseline_manifest.json"], originals["baseline_seal.json"], blobs, bindings=bindings)
    entries = []
    for i, hashes in enumerate(declaration["entries"]):
        entries.append(cases.CaseInput(originals[f"entries/{i:06d}.incident.json"], hashes["incident_sha256"],
            originals[f"entries/{i:06d}.evidence.json"], hashes["evidence_sha256"]))
    return {"bindings": bindings, "baseline": baseline, "entries": entries, **{key: declaration[key] for key in
        ("protocol_sha256", "capsule_sha256", "collector_counts_sha256", "runner_binding_sha256", "runner_design_sha256", "adapter_source_sha256")},
        "collector_counts": originals["collector_counts.json"], "runner_binding": originals["runner_binding.json"]}


def _input_check(args, preregistration):
    # Validate against frozen helpers without constructing a plan/cases/nonces.
    originals = pub._originals(args, preregistration)
    need(args["adapter_source_sha256"] == pub.PIPELINE_SHA256 and args["runner_design_sha256"] == args["protocol_sha256"])
    b = cases._bound(args["bindings"])
    snapshot = parse(cases.fixed_snapshot(args["baseline"], bindings=args["bindings"]))
    verified = [cases._entry(entry, b, snapshot) for entry in args["entries"]]
    need(len({x[0]["incident_id"] for x in verified}) == len(verified)
         and len({x[0]["action_ref"] for x in verified}) == len(verified)
         and len({(x[1]["session_id"], x[1]["uuid"], x[1]["tool_use_id"]) for x in verified}) == len(verified))
    pub.pipe._collector_counts(parse(args["collector_counts"], args["collector_counts_sha256"]), verified)
    binding = parse(args["runner_binding"], args["runner_binding_sha256"])
    pub.pipe._blind_binding(binding)
    need(binding.get("source_commit") == cases.MEASURED_SOURCE)
    need(all(type(binding.get("rule", {}).get(k)) in (int, float) and binding["rule"][k] == v
        for k, v in (("guard_band_s", 1.0), ("bm25_k1", 1.2), ("bm25_b", .75))))
    return originals


@_closed
def seal_inputs(protocol, *, protocol_sha256, preregistration, preregistration_sha256,
        runner_binding, runner_binding_sha256, go, go_sha256, execute=False, _test_identity=None):
    need(execute is True, "dormant")
    p, forest = _identity(protocol, protocol_sha256, preregistration, preregistration_sha256)
    need(type(runner_binding) is bytes and cases._hex(runner_binding_sha256) and sha(runner_binding) == runner_binding_sha256, "binding")
    authority, now, expires = _seal_authority(p, protocol_sha256, preregistration_sha256, runner_binding_sha256, go, go_sha256)
    identity, synthetic_identity = _require_medium(_test_identity)
    root, cfg = forest[0], p["collection_controller"]
    with contextlib.ExitStack() as stack:
        cap._volume_check(root)
        for tree in forest:
            cap._pin_chain(stack, tree)
        _pin_source(stack, p)
        _owner(p, protocol_sha256, root, stack)
        _halting(root)
        disk = pub._Disk(root, stack, forest)
        disk.last, disk.expires = now, expires
        parent, stage = root / "private/case_cli", root / STAGE
        disk.parents(parent)
        need(not os.path.lexists(stage), "consumed")
        disk.mkdir(stage)
        opened = False
        try:
            intent_receipt = disk.put(stage / "INTENT.json", pub._canon({"schema": "wd.m1-p3-case-seal-intent.v1",
                "study_id": p["study_id"], "protocol_sha256": protocol_sha256, "source_sha256": p["case_cli"]["sha256"],
                "go_sha256": go_sha256, "pins": authority["pins"], "invocation_consumed": True, "actual_data_opened": False}))
            source_refs = {}
            def private_read(path, expected=None):
                nonlocal opened
                disk.clock()
                need(path in disk.known, "path")
                try:
                    body = _read(path, stack, expected)
                except BaseException as error:
                    if opened is not True:
                        # A reader may establish an explicit before-open denial;
                        # otherwise its partial/open state is UNKNOWN, not True.
                        observed = getattr(error, "actual_data_opened", None)
                        opened = observed if type(observed) is bool else None
                    raise
                opened = True
                disk.read_exact(path, body)
                source_refs[str(path)] = {"sha256": sha(body), "bytes": len(body)}
                return body
            control, ledger = forest[1:]
            need(not (control / "STOP_NOT_READY.json").exists() and not (ledger / "HALTED").exists(), "prior")
            pins = authority["pins"]
            terminal = private_read(control / "RESULT.json", pins["collection_result_sha256"])
            need(parse(terminal)["journal_sha256"] == pins["journal_sha256"], "prior")
            baseline_root = cap._path(cfg["baseline_root"], audit=True)
            manifest = private_read(baseline_root / "manifest.json", pins["baseline_manifest_sha256"])
            seal = private_read(baseline_root / "SEALED.json", pins["baseline_seal_sha256"])
            cap.verify_baseline(baseline_root, expected_manifest_sha256=pins["baseline_manifest_sha256"])
            capsule = derive_capsule(protocol, preregistration, manifest, seal)
            bindings = cases.Bindings.from_bytes(capsule, expected_sha256=sha(capsule), protocol=protocol, protocol_sha256=protocol_sha256)
            blobs = {row["sha256"]: private_read(baseline_root / "blobs" / row["sha256"], row["sha256"]) for row in parse(manifest)["files"]}
            baseline = cases.Baseline.from_bytes(manifest, seal, blobs, bindings=bindings)
            entries, counters, ids, provenance, native_refs = closed_entries(protocol, manifest, seal, terminal=terminal,
                terminal_sha256=pins["collection_result_sha256"], read=private_read,
                read_range=lambda source, proof: _source_bytes(source, proof, stack))
            need({x.name for x in ledger.iterdir()} == {"LOCK", "ledger.json"} | {"incident-" + cid + ".json" for cid in ids}, "prior")
            args = {"bindings": bindings, "baseline": baseline, "entries": entries, "protocol_sha256": protocol_sha256,
                "capsule_sha256": sha(capsule), "collector_counts": counters, "collector_counts_sha256": sha(counters),
                "runner_binding": runner_binding, "runner_binding_sha256": runner_binding_sha256,
                "runner_design_sha256": protocol_sha256, "adapter_source_sha256": pub.PIPELINE_SHA256}
            _input_check(args, preregistration)
            originals = pub._originals(args, preregistration)
            outputs = {stage / "original" / name: body for name, body in originals.items()}
            outputs[stage / "REFUSAL_MAPPING.json"] = provenance
            outputs[stage / "GO.original.json"] = go
            outputs[stage / "IDENTITY.json"] = pub._canon({"schema": "wd.m1-p3-case-token.v1", "observation": identity,
                "synthetic_identity": synthetic_identity, "logical_actor_qualification": "NOT_INFERRED"})
            request = pub._canon({"schema": "wd.m1-p3-publisher-input-hashes.v1", "study_id": p["study_id"],
                "protocol_sha256": protocol_sha256, "source_sha256": p["case_cli"]["sha256"],
                "publisher_source_sha256": PUBLISHER_SHA256, "publisher_pins": pub.input_pins(args, preregistration=preregistration),
                "collection_result_sha256": pins["collection_result_sha256"], "journal_sha256": pins["journal_sha256"],
                "accepted": len(entries), "refusal_mapping_sha256": sha(provenance)})
            outputs[stage / "PUBLISH_REQUEST.json"] = request
            outputs[stage / "SOURCE_REFS.json"] = pub._canon({"schema": "wd.m1-p3-case-source-refs.v1", "files": source_refs,
                "native_ranges": native_refs})
            receipts = {path.relative_to(stage).as_posix(): disk.put(path, body) for path, body in outputs.items()}
            disk.clock()
            result = pub._canon({"schema": "wd.m1-p3-case-sealed.v1", "status": "COMPLETE", "study_id": p["study_id"],
                "protocol_sha256": protocol_sha256, "preregistration_sha256": preregistration_sha256,
                "source_sha256": p["case_cli"]["sha256"], "go_sha256": go_sha256, "seal_pins": pins,
                "intent_sha256": intent_receipt["sha256"],
                "entry_count": len(entries), "outputs": receipts, "request_sha256": sha(request),
                "synthetic_identity": synthetic_identity,
                "started_utc": now.isoformat(timespec="microseconds").replace("+00:00", "Z"),
                "sealed_utc": disk.last.isoformat(timespec="microseconds").replace("+00:00", "Z"),
                "invocation_consumed": True, "actual_data_opened": opened})
            out = disk.put(stage / "RESULT.json", result, terminal=True)
            disk.clock()
            return {"status": "COMPLETE", "phase": "seal_inputs", "result_sha256": out["sha256"],
                "request_sha256": sha(request), "accepted": len(entries), "invocation_consumed": True, "actual_data_opened": opened}
        except BaseException as error:
            reason = error.reason if type(error) is NotReady else "write"
            alarm = pub._canon({"status": "STOP_NOT_READY", "reason": reason, "phase": "seal_inputs",
                "invocation_consumed": True, "actual_data_opened": opened})
            retained = True
            for path in (stage / "STOP_NOT_READY.json", parent / "HALTED.json"):
                try:
                    disk.put(path, alarm, terminal=True)
                except BaseException:
                    retained = False
            failure = NotReady(reason if retained else "terminal_receipt_unavailable")
            failure.invocation_consumed, failure.actual_data_opened = True, opened
            raise failure from None


def _load(protocol, protocol_sha256, preregistration, preregistration_sha256, sealed_result_sha256, stack, allow_synthetic=False):
    p, forest = _identity(protocol, protocol_sha256, preregistration, preregistration_sha256)
    need(cases._hex(sealed_result_sha256), "go")
    root, stage = forest[0], forest[0] / STAGE
    cap._volume_check(root)
    for tree in forest:
        cap._pin_chain(stack, tree)
    _pin_source(stack, p)
    _halting(root)
    _owner(p, protocol_sha256, root, stack)
    disk = pub._Disk(root, stack, forest)  # Metadata-only shared budget/reparse checks.
    result = parse(_read(stage / "RESULT.json", stack, sealed_result_sha256))
    keys(result, ("schema", "status", "study_id", "protocol_sha256", "preregistration_sha256", "source_sha256",
        "go_sha256", "seal_pins", "intent_sha256", "entry_count", "outputs", "request_sha256", "synthetic_identity", "started_utc", "sealed_utc", "invocation_consumed", "actual_data_opened"))
    need(result["schema"] == "wd.m1-p3-case-sealed.v1" and result["status"] == "COMPLETE"
         and result["study_id"] == p["study_id"] and result["protocol_sha256"] == protocol_sha256
         and result["preregistration_sha256"] == preregistration_sha256 and result["source_sha256"] == p["case_cli"]["sha256"]
         and result["invocation_consumed"] is True and result["actual_data_opened"] is True, "prior")
    need(cases._hex(result["go_sha256"]) and type(result["entry_count"]) is int and 0 <= result["entry_count"] <= 4096, "prior")
    need(type(result["synthetic_identity"]) is bool and (allow_synthetic or result["synthetic_identity"] is False), "source")
    keys(result["seal_pins"], SEAL_PINS)
    need(all(cases._hex(x) for x in result["seal_pins"].values()), "prior")
    need(cases._hex(result["intent_sha256"]), "prior")
    intent = parse(_read(stage / "INTENT.json", stack, result["intent_sha256"]))
    need(intent == {"schema": "wd.m1-p3-case-seal-intent.v1", "study_id": p["study_id"], "protocol_sha256": protocol_sha256,
        "source_sha256": p["case_cli"]["sha256"], "go_sha256": result["go_sha256"], "pins": result["seal_pins"],
        "invocation_consumed": True, "actual_data_opened": False}, "prior")
    need(type(result["outputs"]) is dict, "prior")
    originals, other = {}, {}
    for name, receipt in result["outputs"].items():
        need(type(name) is str and "\\" not in name and all(x not in ("", ".", "..") and ":" not in x for x in name.split("/")), "path")
        keys(receipt, ("sha256", "bytes"))
        need(cases._hex(receipt["sha256"]) and type(receipt["bytes"]) is int and receipt["bytes"] >= 0)
        body = _read(stage / name, stack, receipt["sha256"])
        need(len(body) == receipt["bytes"])
        if name.startswith("original/"):
            originals[name[9:]] = body
        else:
            other[name] = body
    need(set(other) == {"REFUSAL_MAPPING.json", "SOURCE_REFS.json", "PUBLISH_REQUEST.json", "GO.original.json", "IDENTITY.json"}, "prior")
    identity = parse(other["IDENTITY.json"])
    keys(identity, ("schema", "observation", "synthetic_identity", "logical_actor_qualification"))
    need(identity["schema"] == "wd.m1-p3-case-token.v1" and identity["synthetic_identity"] is result["synthetic_identity"]
         and identity["logical_actor_qualification"] == "NOT_INFERRED", "source")
    _require_medium(lambda: identity["observation"])  # Validate shape, never qualify an actor.
    old_go = parse(other["GO.original.json"], result["go_sha256"])
    keys(old_go, ("schema", "status", "phase", "study_id", "protocol_sha256", "source_sha256", "preregistration_sha256",
        "authorizer", "event_ref", "issued_at_utc", "expires_at_utc", "pins"))
    need(old_go["schema"] == "wd.m1-p3-case-cli-go.v1" and old_go["status"] == "CASE_INPUT_SEAL_GO" and old_go["phase"] == "seal_inputs"
         and old_go["study_id"] == p["study_id"] and old_go["protocol_sha256"] == protocol_sha256
         and old_go["preregistration_sha256"] == preregistration_sha256 and old_go["source_sha256"] == p["case_cli"]["sha256"]
         and old_go["authorizer"] == p["publisher_authorizer"] and cases._hex(old_go["event_ref"])
         and old_go["pins"] == result["seal_pins"], "prior")
    issued, expires = cap.parse_utc(old_go["issued_at_utc"]), cap.parse_utc(old_go["expires_at_utc"])
    need(issued <= cap.parse_utc(result["started_utc"]) <= cap.parse_utc(result["sealed_utc"]) < expires
         and dt.timedelta(0) < expires-issued <= dt.timedelta(seconds=1800)
         and issued >= cap.parse_utc(p["collection_controller"]["collection_end_utc"]), "clock")
    need(originals["protocol.json"] == protocol and originals["preregistration.json"] == preregistration, "binding")
    args = _prepare_from_originals(originals)
    _input_check(args, preregistration)
    need(pub._originals(args, preregistration) == originals and len(args["entries"]) == result["entry_count"], "prior")
    request = parse(other["PUBLISH_REQUEST.json"], result["request_sha256"])
    keys(request, ("schema", "study_id", "protocol_sha256", "source_sha256", "publisher_source_sha256", "publisher_pins",
        "collection_result_sha256", "journal_sha256", "accepted", "refusal_mapping_sha256"))
    need(request["schema"] == "wd.m1-p3-publisher-input-hashes.v1" and request["study_id"] == p["study_id"]
         and request["protocol_sha256"] == protocol_sha256 and request["source_sha256"] == p["case_cli"]["sha256"]
         and request["publisher_source_sha256"] == PUBLISHER_SHA256
         and request["accepted"] == result["entry_count"] and request["collection_result_sha256"] == result["seal_pins"]["collection_result_sha256"]
         and request["journal_sha256"] == result["seal_pins"]["journal_sha256"], "prior")
    need(request["publisher_pins"] == pub.input_pins(args, preregistration=preregistration)
         and request["refusal_mapping_sha256"] == sha(other["REFUSAL_MAPPING.json"]), "prior")
    mapping = parse(other["REFUSAL_MAPPING.json"])
    need(mapping["mapping"] == REFUSAL_MAPPING and mapping["derived_counts"] == parse(args["collector_counts"]), "prior")
    refs = parse(other["SOURCE_REFS.json"])
    keys(refs, ("schema", "files", "native_ranges"))
    need(refs["schema"] == "wd.m1-p3-case-source-refs.v1", "prior")
    allowed = [cap._path(p["collection_controller"][key], audit=True) for key in ("control_root", "ledger_root", "baseline_root")]
    for path, receipt in refs["files"].items():
        keys(receipt, ("sha256", "bytes"))
        need(cases._hex(receipt["sha256"]) and type(receipt["bytes"]) is int and receipt["bytes"] >= 0, "prior")
        actual = cap._path(path, audit=True)
        need(any(tree in actual.parents for tree in allowed), "path")
        body = _read(actual, stack, receipt["sha256"])
        need(len(body) == receipt["bytes"], "prior")
    expected_files = set(result["outputs"]) | {"INTENT.json", "RESULT.json"}
    actual_files = {x.relative_to(stage).as_posix() for x in stage.rglob("*") if x.is_file()}
    need(actual_files == expected_files and not (stage / "STOP_NOT_READY.json").exists(), "prior")
    return args


@_closed
def load_sealed_inputs(protocol, *, protocol_sha256, preregistration, preregistration_sha256, sealed_result_sha256, _test_identity=None):
    """Private caller API; no native reads/extraction/construction/nonces."""
    _, synthetic = _require_medium(_test_identity)
    with contextlib.ExitStack() as stack:
        return _load(protocol, protocol_sha256, preregistration, preregistration_sha256, sealed_result_sha256, stack, synthetic)


class Parser(argparse.ArgumentParser):
    def error(self, _):
        raise NotReady("schema")


def main(argv=None, *, _test_identity=None):
    opened, publisher_called = False, False
    try:
        parser = Parser(add_help=False)
        parser.add_argument("--execute", action="store_true")
        parser.add_argument("--phase", choices=("seal_inputs", "prepass_inputs", "complete_cases"), required=True)
        for flag in ("protocol", "protocol-sha256", "preregistration", "preregistration-sha256", "go", "go-sha256",
                     "runner-binding", "runner-binding-sha256", "sealed-result-sha256", "native-manifest", "native-manifest-sha256"):
            parser.add_argument("--" + flag)
        a = parser.parse_args(argv)
        need(a.execute is True, "dormant")
        need(all((a.protocol, a.preregistration, a.go)) and all(cases._hex(x) for x in (a.protocol_sha256, a.preregistration_sha256, a.go_sha256)), "go")
        with contextlib.ExitStack() as stack:
            protocol, prereg, go = _read(a.protocol, stack, a.protocol_sha256), _read(a.preregistration, stack, a.preregistration_sha256), _read(a.go, stack, a.go_sha256)
            if a.phase == "seal_inputs":
                need(a.runner_binding and cases._hex(a.runner_binding_sha256), "go")
                binding = _read(a.runner_binding, stack, a.runner_binding_sha256)
                out = seal_inputs(protocol, protocol_sha256=a.protocol_sha256, preregistration=prereg, preregistration_sha256=a.preregistration_sha256,
                    runner_binding=binding, runner_binding_sha256=a.runner_binding_sha256, go=go, go_sha256=a.go_sha256, execute=True, _test_identity=_test_identity)
            else:
                need(cases._hex(a.sealed_result_sha256), "go")
                # Validate full PublisherGO scalar schema/authority/time BEFORE private reload.
                p, _ = _identity(protocol, a.protocol_sha256, prereg, a.preregistration_sha256)
                g = parse(go, a.go_sha256)
                keys(g, ("schema", "status", "study_id", "protocol_sha256", "phase", "authorizer", "source_sha256",
                    "pins", "issued_at_utc", "expires_at_utc", "event_ref"))
                need(g["schema"] == "wd.m1-p3-publish-go.v1" and g["status"] == "LOCKED_PUBLISH_GO" and g["phase"] == a.phase
                    and g["study_id"] == p["study_id"] and g["protocol_sha256"] == a.protocol_sha256 and g["authorizer"] == p["publisher_authorizer"]
                    and g["source_sha256"] == PUBLISHER_SHA256 and cases._hex(g["event_ref"]), "go")
                now, issued, expires = pub._now(), cap.parse_utc(g["issued_at_utc"]), cap.parse_utc(g["expires_at_utc"])
                need(issued <= now < expires and dt.timedelta(0) < expires-issued <= dt.timedelta(seconds=1800)
                    and issued >= cap.parse_utc(p["collection_controller"]["collection_end_utc"]), "clock")
                _, synthetic = _require_medium(_test_identity)
                need(type(g["pins"]) is dict and all(cases._hex(x) for x in g["pins"].values()), "go")
                root = cap._path(p["study_root"], audit=True)
                # Check the hash-only sealed request against the separately
                # issued PublisherGO BEFORE loading private CaseInput bodies.
                meta = parse(_read(root / STAGE / "RESULT.json", stack, a.sealed_result_sha256))
                request = parse(_read(root / STAGE / "PUBLISH_REQUEST.json", stack, meta["request_sha256"]))
                extras = set() if a.phase == "prepass_inputs" else {"prior_result", "native_manifest", "prepared_receipts", "native_prepass", "native_prepare_receipt", "native_prepass_receipt"}
                need(set(g["pins"]) == set(request["publisher_pins"]) | extras
                    and all(g["pins"][key] == value for key, value in request["publisher_pins"].items()), "go")
                opened = None  # Unknown until exact immutable private reload returns.
                prepared = _load(protocol, a.protocol_sha256, prereg, a.preregistration_sha256, a.sealed_result_sha256, stack, synthetic)
                opened = True
                if a.phase == "prepass_inputs":
                    publisher_called = True
                    out = pub.publish_prepass_inputs(prepared, preregistration=prereg, go=go, go_sha256=a.go_sha256, execute=True)
                else:
                    need(a.native_manifest and cases._hex(a.native_manifest_sha256), "go")
                    root = cap._path(p["study_root"], audit=True)
                    supplied = {"native_manifest": _read(a.native_manifest, stack, a.native_manifest_sha256),
                        "prior_result": _read(root / "private/prepass_inputs/RESULT.json", stack),
                        "prepared_receipts": _read(root / "native/PREPARED.json", stack),
                        "native_prepass": _read(root / "native/journal/prepass/RESULT.json", stack),
                        "native_prepare_receipt": _read(root / "native/prepare/RESULT.json", stack),
                        "native_prepass_receipt": _read(root / "native/prepass/RESULT.json", stack)}
                    publisher_called = True
                    out = pub.publish_complete_cases(prepared, preregistration=prereg, go=go, go_sha256=a.go_sha256, execute=True, **supplied)
        print(json.dumps(out, sort_keys=True, separators=(",", ":")))
        return 0
    except BaseException as error:
        reason = error.reason if type(error) is NotReady else "schema"
        print(json.dumps({"status": "STOP_NOT_READY", "reason": reason,
            "actual_data_opened": getattr(error, "actual_data_opened", opened),
            "invocation_consumed": getattr(error, "invocation_consumed", None if publisher_called else False),
            "publisher_invoked": publisher_called}, sort_keys=True, separators=(",", ":")))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

