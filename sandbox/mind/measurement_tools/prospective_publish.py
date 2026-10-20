"""Dormant one-shot PRIVATE publisher for the frozen P3 pipeline.

No worker/model/scorer is imported or invoked. Only explicitly listed blind
bytes are published to root/blind; private inputs/plans/cases/nonces never are.
The original native worker receipts are integrity-checked, not authenticated:
actual role/GO origin, loaded native/runtime identity and Medium supervision
remain the separately approved controller/worker's duties. No OS/cryptographic
confidentiality, independent execution evidence or study readiness is claimed.

The original final LOCKED protocol must contain publisher_authorizer equal to
native_authorizer, preregistration_sha256, and the exact publisher object below.
Typed phase GO is separate, original raw bytes and disabled without execute.
Partial phases consume permanently; no overwrite, adoption, retry or resume.
The exact forest study_root + direct-audit control_root + ledger_root has
one64MiB budget. All existing regular files/ancestors are pinned deny-write and
deny-delete during each publication. Files appearing concurrently are refused.
This is bounded local integrity, not protection against a privileged actor.
Budget scans use metadata/byte sizes, not generic collection payload reads.
Inbox/baseline/blind/score and original capture are inside study_root; control
and ledger remain DIRECT children of the global audit root, as frozen recorder
requires. The original capture is verified read-only; a NEW
root/baseline byte copy is published for native roles and also counts toward
the shared limit. Capturing directly into root/baseline therefore refuses.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import functools
import hashlib
import os
from pathlib import Path
import stat

from sandbox.mind.measurement_tools import prospective_pipeline as pipe


capture = pipe.capture
BUDGET_BYTES = 64 * 1024 * 1024
TERMINAL_RESERVE = 8192
PIPELINE_SHA256 = "9d4f79fa3fa1fa280a7ffa2981d06824a69941a8505a1074703eff1fcb4fe06a"
CAPTURE_SHA256 = "4203b3215c446020b57940b69deccbf3694af4b936af468685a439867a300b12"
CASES_SHA256 = "8fbc095c80227d67bc1d8ef59274add608fe197407a34193a688a33b4f2c729a"
NATIVE_SHA256 = "6511543f33c25f430eabd85b66b40ecc335b683827da7a7856ba7722ad80187d"
_PHASES = ("prepass_inputs", "complete_cases")
_ERRORS = frozenset({"schema", "binding", "go", "clock", "source", "path", "budget", "space",
    "dormant", "consumed", "prior", "native", "write", "pipeline", "terminal_receipt_unavailable",
    "score_release_contract_unknown"})
_ARGS = frozenset({"bindings", "baseline", "entries", "protocol_sha256", "capsule_sha256",
    "collector_counts", "collector_counts_sha256", "runner_binding", "runner_binding_sha256",
    "runner_design_sha256", "adapter_source_sha256"})
_COLLECTION_FIELDS = ("audit_root", "control_root", "ledger_root", "inbox", "baseline_root", "blind_root",
    "score_root", "stop_signal", "collection_start_utc", "collection_end_utc", "baseline_resolution_us",
    "max_record_delay_seconds", "max_records", "allowed_reporters", "sources", "poll_ms", "max_wall_seconds")


class NotReady(Exception):
    def __init__(self, reason):
        self.reason = reason if reason in _ERRORS else "schema"
        super().__init__("STOP_NOT_READY:" + self.reason)


def _need(ok, reason="schema"):
    if not ok:
        raise NotReady(reason)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _canon(value):
    return pipe.cases._canon(value)


def _json(data, expected=None):
    return pipe.cases._json(data, expected)


def _keys(value, names):
    _need(type(value) is dict and set(value) == set(names))


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _time(value):
    return capture.parse_utc(value)


def _closed(fn):
    @functools.wraps(fn)
    def call(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except NotReady:
            raise
        except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError,
                RecursionError, capture.CaptureError, pipe.NotReady):
            raise NotReady("schema") from None
    return call


def _originals(args, preregistration):
    _keys(args, _ARGS)
    _need(type(args["bindings"]) is pipe.cases.Bindings and type(args["baseline"]) is pipe.cases.Baseline)
    _need(type(args["entries"]) in (list, tuple) and len(args["entries"]) <= 10000)
    baseline = args["baseline"]
    data = {"protocol.json": args["bindings"].protocol_json, "capsule.json": args["bindings"].capsule_json,
            "preregistration.json": preregistration, "baseline_manifest.json": baseline.manifest_json,
            "baseline_seal.json": baseline.seal_json, "collector_counts.json": args["collector_counts"],
            "runner_binding.json": args["runner_binding"]}
    declarations = {key: args[key] for key in ("protocol_sha256", "capsule_sha256", "collector_counts_sha256",
        "runner_binding_sha256", "runner_design_sha256", "adapter_source_sha256")}
    _need(all(pipe.cases._hex(value) for value in declarations.values()), "binding")
    declarations["entries"] = []
    for index, entry in enumerate(args["entries"]):
        _need(type(entry) is pipe.cases.CaseInput)
        _need(pipe.cases._hex(entry.incident_sha256) and pipe.cases._hex(entry.evidence_sha256), "binding")
        key = f"entries/{index:06d}"
        data[key + ".incident.json"], data[key + ".evidence.json"] = entry.incident_json, entry.evidence_json
        declarations["entries"].append({"incident_sha256": entry.incident_sha256, "evidence_sha256": entry.evidence_sha256})
    for digest, body in baseline.blobs:
        _need(pipe.cases._hex(digest) and _sha(body) == digest, "binding")
        key = "blobs/" + digest + ".bin"
        _need(key not in data)
        data[key] = body
    data["declarations.json"] = _canon(declarations)
    _need(all(type(body) is bytes for body in data.values()))
    _need(sum(map(len, data.values())) <= BUDGET_BYTES, "budget")
    return data


@_closed
def input_pins(prepare_args, *, preregistration):
    """Pure exact ORIGINAL input inventory, not authorization or execution."""
    return {"original/" + key: _sha(body) for key, body in sorted(_originals(prepare_args, preregistration).items())}


@_closed
def completion_pins(prepare_args, *, preregistration, prior_result, native_manifest,
                    prepared_receipts, native_prepass, native_prepare_receipt, native_prepass_receipt):
    pins = input_pins(prepare_args, preregistration=preregistration)
    for key, body in (("prior_result", prior_result), ("native_manifest", native_manifest),
            ("prepared_receipts", prepared_receipts), ("native_prepass", native_prepass),
            ("native_prepare_receipt", native_prepare_receipt), ("native_prepass_receipt", native_prepass_receipt)):
        _need(type(body) is bytes and 0 < len(body) <= BUDGET_BYTES)
        pins[key] = _sha(body)
    return pins


def _authority(args, preregistration, go_bytes, go_sha256, phase, pins):
    _need(pipe.cases._hex(go_sha256), "go")
    go = _json(go_bytes, go_sha256)
    _keys(go, ("schema", "status", "study_id", "protocol_sha256", "phase", "authorizer", "source_sha256",
               "pins", "issued_at_utc", "expires_at_utc", "event_ref"))
    protocol = _json(args["bindings"].protocol_json, args["protocol_sha256"])
    prereg = _json(preregistration)
    _need(protocol.get("schema") == "wd.m1-p3-protocol-lock.v1" and protocol.get("status") == "LOCKED"
          and prereg.get("schema") == "wd.m1-prospective-preregistration.v1" and prereg.get("status") == "LOCKED"
          and prereg.get("study_id") == protocol.get("study_id")
          and protocol.get("preregistration_sha256") == _sha(preregistration), "go")
    config = protocol.get("publisher")
    _keys(config, ("root", "paths", "source_sha256", "pipeline_sha256", "capture_sha256", "cases_sha256"))
    _keys(config["paths"], ("private", "blind", "score"))
    root = capture._path(config["root"], audit=True)
    _need(capture._path(protocol.get("study_root"), audit=True) == root, "path")
    roles = [capture._path(config["paths"][role], audit=True) for role in ("private", "blind", "score")]
    capture._disjoint(roles)
    _need(all(path == root / role for path, role in zip(roles, ("private", "blind", "score"))), "path")
    collection = protocol.get("collection_controller")
    _keys(collection, _COLLECTION_FIELDS)
    _need(capture._path(collection["audit_root"]) == capture.AUDIT_ROOT, "path")
    collection_paths = {key: capture._path(collection[key], audit=True) for key in
        ("control_root", "ledger_root", "inbox", "baseline_root", "blind_root", "score_root")}
    _need(all(collection_paths[key].parent == capture.AUDIT_ROOT for key in ("control_root", "ledger_root")), "path")
    _need(all(root in collection_paths[key].parents for key in ("inbox", "baseline_root", "blind_root", "score_root")), "path")
    capture._disjoint((root, collection_paths["control_root"], collection_paths["ledger_root"]))
    capture._disjoint(list(collection_paths.values()))
    capture._disjoint((collection_paths["baseline_root"], root / "baseline"))
    _need(collection_paths["blind_root"] == root / "blind" and collection_paths["score_root"] == root / "score"
          and capture._path(collection["stop_signal"], audit=True) == collection_paths["inbox"] / "STOP.json", "path")
    _need(go["schema"] == "wd.m1-p3-publish-go.v1" and go["status"] == "LOCKED_PUBLISH_GO"
          and go["study_id"] == protocol.get("study_id") and pipe.cases._id(go["study_id"])
          and go["protocol_sha256"] == args["protocol_sha256"] and go["phase"] == phase
          and type(go["authorizer"]) is dict and go["authorizer"] == protocol.get("publisher_authorizer")
          and go["authorizer"] == protocol.get("native_authorizer")
          and go["pins"] == pins and pipe.cases._hex(go["event_ref"]), "go")
    pipe.cases._identity(go["authorizer"])
    expected = {"source_sha256": _sha(Path(__file__).read_bytes()), "pipeline_sha256": PIPELINE_SHA256,
                "capture_sha256": CAPTURE_SHA256, "cases_sha256": CASES_SHA256}
    _need(all(config[key] == digest for key, digest in expected.items())
          and go["source_sha256"] == config["source_sha256"], "source")
    _need(args["adapter_source_sha256"] == PIPELINE_SHA256, "source")
    # Frozen Root decision: runner design is original protocol, not a manifest
    # containing yet-to-be-published SAFE input hashes. Native GO separately
    # binds the later original native manifest, without a placeholder design.
    _need(args["runner_design_sha256"] == args["protocol_sha256"], "binding")
    issued, expires, now = _time(go["issued_at_utc"]), _time(go["expires_at_utc"]), _now()
    _need(type(now) is dt.datetime and now.tzinfo == dt.timezone.utc and issued <= now < expires
          and dt.timedelta(0) < expires - issued <= dt.timedelta(seconds=1800), "clock")
    capsule = _json(args["bindings"].capsule_json, args["capsule_sha256"])
    _need(_time(capsule["collection_end_utc"]) <= issued, "clock")
    _need(all(collection[key] == capsule[key] for key in ("collection_start_utc", "collection_end_utc")), "binding")
    baseline_identity = _json(args["baseline"].manifest_json)["identity"]
    _need(capture._path(baseline_identity["destination"], audit=True) == collection_paths["baseline_root"]
          and baseline_identity["preregistration_sha256"] == _sha(preregistration), "binding")
    return root, protocol, go, expires, now


class _Disk:
    def __init__(self, root, stack, forest):
        self.root, self.stack, self.pinned = root, stack, set()
        self.forest = tuple(forest)
        _need(len(self.forest) == 3 and self.forest[0] == root, "path")
        capture._disjoint(self.forest)
        self.known = {}
        self.metadata = {}
        self.scan(initial=True)
        self.last, self.expires = None, None

    def scan(self, *, initial=False):
        total, seen = 0, set()
        def walk(folder):
            nonlocal total
            for path in sorted(folder.iterdir()):
                info = path.lstat()
                _need(not path.is_symlink() and not (getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT), "path")
                _need(stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode), "path")
                if path not in self.pinned:
                    self.stack.enter_context(capture._pin(path))
                    self.pinned.add(path)
                if stat.S_ISDIR(info.st_mode):
                    walk(path)
                else:
                    _need(info.st_nlink == 1, "path")
                    info = path.lstat()  # Recheck after the deny-write pin.
                    _need(info.st_nlink == 1, "path")
                    total += info.st_size
                    _need(total <= BUDGET_BYTES, "budget")
                    seen.add(path)
                    metadata = (info.st_dev, info.st_ino, info.st_size)
                    if path in self.known:
                        _need(self.metadata[path] == metadata, "binding")
                        if self.known[path] is not None:
                            _need(self.known[path] == _sha(capture._read_bounded(path)), "binding")
                    else:
                        _need(initial, "binding")
                        self.known[path] = None
                        self.metadata[path] = metadata
        for tree in self.forest:
            walk(tree)
        _need(set(self.known) == seen, "binding")
        return total

    def clock(self):
        now = _now()
        _need(type(now) is dt.datetime and now.tzinfo == dt.timezone.utc
              and self.last <= now < self.expires, "clock")
        self.last = now

    def mkdir(self, path):
        path.mkdir(exist_ok=False)
        self.stack.enter_context(capture._pin(path))
        self.pinned.add(path)

    def parents(self, path):
        if path == self.root:
            return
        if not path.exists():
            self.parents(path.parent)
            self.mkdir(path)
        _need(path.is_dir() and path in self.pinned, "path")

    def put(self, path, body, *, terminal=False):
        _need(type(body) is bytes, "write")
        _need(self.root in path.parents, "path")
        if not terminal:
            self.clock()
        limit = BUDGET_BYTES if terminal else BUDGET_BYTES - TERMINAL_RESERVE
        _need(self.scan() + len(body) <= limit, "budget")
        capture._space(self.root)
        self.parents(path.parent)
        try:
            capture._exclusive_write(path, body)
        except BaseException:
            # A failed exclusive write may leave bytes. Preserve/account that
            # exact regular file so terminal STOP receipts can still be added.
            if os.path.lexists(path):
                info = path.lstat()
                _need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and not path.is_symlink()
                      and not (getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT), "path")
                self.stack.enter_context(capture._pin(path))
                self.pinned.add(path)
                self.known[path] = _sha(capture._read_bounded(path))
                info = path.stat()
                self.metadata[path] = (info.st_dev, info.st_ino, info.st_size)
            raise
        self.stack.enter_context(capture._pin(path))
        self.pinned.add(path)
        actual = capture._read_bounded(path)
        self.known[path] = _sha(actual)
        info = path.stat()
        self.metadata[path] = (info.st_dev, info.st_ino, info.st_size)
        _need(actual == body, "write")
        if not terminal:
            self.clock()
        return {"sha256": _sha(body), "bytes": len(body)}

    def read_exact(self, path, body):
        _need(path in self.known and capture._read_bounded(path) == body, "prior")
        self.known[path] = _sha(body)

    def digest(self, path):
        _need(path in self.known, "prior")
        digest = _sha(capture._read_bounded(path))
        _need(self.known[path] in (None, digest), "prior")
        self.known[path] = digest
        return digest


def _write_set(disk, outputs):
    return {path.relative_to(disk.root).as_posix(): disk.put(path, body) for path, body in outputs.items()}


def _original_baseline(disk, plan, protocol):
    """Verify, never repair/adopt, the supplied original capture inside budget."""
    source = capture._path(protocol["collection_controller"]["baseline_root"], audit=True)
    capture.verify_baseline(source, expected_manifest_sha256=_sha(plan.baseline.manifest_json))
    disk.read_exact(source / "manifest.json", plan.baseline.manifest_json)
    disk.read_exact(source / "SEALED.json", plan.baseline.seal_json)
    for digest, body in plan.baseline.blobs:
        disk.read_exact(source / "blobs" / digest, body)


def _prior(disk, prior_result, plan, pins):
    root = disk.root
    disk.read_exact(root / "private/prepass_inputs/RESULT.json", prior_result)
    prior = _json(prior_result)
    _keys(prior, ("schema", "status", "study_id", "phase", "protocol_sha256", "go_sha256", "source_sha256",
                  "input_pins", "plan_sha256", "outputs", "counts", "scorer_unlocked", "invocation_consumed"))
    _need(prior["schema"] == "wd.m1-p3-publish-result.v1" and prior["status"] == "COMPLETE"
          and prior["phase"] == "prepass_inputs" and prior["invocation_consumed"] is True
          and prior["scorer_unlocked"] is False and prior["protocol_sha256"] == plan.protocol_sha256
          and prior["study_id"] == _json(plan.bindings.capsule_json)["study_id"]
          and prior["source_sha256"] == _sha(Path(__file__).read_bytes())
          and prior["input_pins"] == {k: v for k, v in pins.items() if k.startswith("original/")}
          and prior["plan_sha256"] == _sha(plan.manifest_json), "prior")
    expected_outputs = _prepare_outputs(root, plan, None)
    # Exact known output cover is rederived; no relative path from a receipt is followed.
    _need(set(prior["outputs"]) == {p.relative_to(root).as_posix() for p in expected_outputs}, "prior")
    for path, body in expected_outputs.items():
        relative = path.relative_to(root).as_posix()
        _need(prior["outputs"][relative] == {"sha256": _sha(body), "bytes": len(body)}, "prior")
        disk.read_exact(path, body)
    for role in ("blind", "baseline"):
        tree = root / role
        _need({p for p in disk.known if tree in p.parents}
              == {p for p in expected_outputs if tree in p.parents}, "prior")
    return prior


def _prepare_outputs(root, plan, originals):
    private = root / "private/prepass_inputs"
    # Reconstruct exact originals from checked plan; prereg supplied separately.
    if originals is None:
        originals = {"protocol.json": plan.bindings.protocol_json, "capsule.json": plan.bindings.capsule_json,
            "baseline_manifest.json": plan.baseline.manifest_json, "baseline_seal.json": plan.baseline.seal_json,
            "collector_counts.json": plan.collector_counts_json, "runner_binding.json": plan.executor_inputs.runner_binding_json}
        for index, entry in enumerate(plan.entries):
            originals[f"entries/{index:06d}.incident.json"] = entry.incident_json
            originals[f"entries/{index:06d}.evidence.json"] = entry.evidence_json
        for digest, body in plan.baseline.blobs:
            originals["blobs/" + digest + ".bin"] = body
        # Both are separately checked against supplied phase originals by caller.
        for name in ("preregistration.json", "declarations.json"):
            originals[name] = capture._read_bounded(private / "original" / name)
    outputs = {private / "original" / key: body for key, body in originals.items()}
    outputs[private / "plan.json"] = plan.manifest_json
    inputs = plan.executor_inputs
    outputs.update({root / "blind/blind.jsonl": inputs.blind_jsonl,
        root / "blind/time_sidecar.json": inputs.time_sidecar_json,
        root / "blind/runner_cases.json": inputs.runner_cases_json,
        root / "blind/binding.json": inputs.runner_binding_json,
        root / "blind/request.json": inputs.request_json,
        root / "blind/snapshot.json": plan.snapshot_json,
        root / "baseline/manifest.json": plan.baseline.manifest_json,
        root / "baseline/SEALED.json": plan.baseline.seal_json})
    outputs.update({root / "baseline/blobs" / (digest + ".bin"): body for digest, body in plan.baseline.blobs})
    return outputs


def _native(disk, plan, supplied):
    root = disk.root
    _need(plan.runner_design_sha256 == plan.protocol_sha256, "native")
    native_manifest_sha256 = _sha(supplied["native_manifest"])
    manifest = _json(supplied["native_manifest"])
    _keys(manifest, ("schema", "study_id", "protocol", "worker", "root", "owner", "python", "source_root",
        "source_files", "package_roots", "cache_root", "cache_files", "environment", "binding", "helpers", "inputs",
        "stdio_paths", "corpus_as_of_utc", "collection_start_utc", "collection_end_utc"))
    _keys(manifest["worker"], ("path", "sha256"))
    _need(manifest["worker"]["sha256"] == NATIVE_SHA256, "native")
    _need(manifest.get("schema") == "wd.m1-p3-native-manifest.v1" and manifest.get("root") == str(root)
          and manifest.get("study_id") == _json(plan.bindings.capsule_json)["study_id"]
          and manifest.get("protocol", {}).get("sha256") == plan.protocol_sha256
          and manifest.get("binding") == _json(plan.executor_inputs.runner_binding_json), "native")
    capsule = _json(plan.bindings.capsule_json)
    _need(manifest["collection_start_utc"] == capsule["collection_start_utc"]
          and manifest["collection_end_utc"] == capsule["collection_end_utc"], "native")
    inputs = manifest.get("inputs")
    _keys(inputs, ("runner_cases", "snapshot", "baseline_manifest", "baseline_seal", "baseline_blobs", "commitments_path"))
    for name, path, body in (("runner_cases", "blind/runner_cases.json", plan.executor_inputs.runner_cases_json),
            ("snapshot", "blind/snapshot.json", plan.snapshot_json), ("baseline_manifest", "baseline/manifest.json", plan.baseline.manifest_json),
            ("baseline_seal", "baseline/SEALED.json", plan.baseline.seal_json)):
        _need(inputs[name] == {"path": str(root / path), "sha256": _sha(body)}, "native")
    _need(inputs["baseline_blobs"] == {digest: str(root / "baseline/blobs" / (digest + ".bin")) for digest, _ in plan.baseline.blobs}
          and inputs["commitments_path"] == str(root / "blind/kept_commitments.json"), "native")
    targets = {"prepared_receipts": root / "native/PREPARED.json", "native_prepass": root / "native/journal/prepass/RESULT.json",
        "native_prepare_receipt": root / "native/prepare/RESULT.json", "native_prepass_receipt": root / "native/prepass/RESULT.json"}
    for key, path in targets.items():
        disk.read_exact(path, supplied[key])
    prepared = _json(supplied["prepared_receipts"])
    _need(type(prepared) is dict and all(type(row) is dict and row.get("memory_dir") == str(root / "prepared/shared/memory") for row in prepared.values()), "native")
    for phase in ("prepare", "prepass"):
        receipt = _json(supplied["native_" + phase + "_receipt"])
        _keys(receipt, ("schema", "status", "study_id", "phase", "manifest_sha256", "go_sha256", "invocation_consumed",
            "actual_data_opened", "SAFE_cases", "outputs", "retained_outputs", "observations", "stdout", "stderr", "DLL_closure"))
        _need(receipt["schema"] == "wd.m1-p3-native-phase.v1" and receipt["status"] == "COMPLETE"
            and receipt["phase"] == phase and receipt["study_id"] == manifest["study_id"]
            and receipt["manifest_sha256"] == native_manifest_sha256 and receipt["invocation_consumed"] is True
            and receipt["actual_data_opened"] is True and pipe.cases._hex(receipt["go_sha256"])
            and type(receipt["SAFE_cases"]) is int and receipt["SAFE_cases"] == len(_json(plan.executor_inputs.runner_cases_json))
            and receipt["DLL_closure"] == "UNKNOWN", "native")
        retained = receipt["retained_outputs"]
        _need(type(retained) is dict and "native/PREPARED.json" in retained, "native")
        for relative, info in retained.items():
            _need(type(relative) is str and "\\" not in relative and relative.startswith("native/")
                and all(part not in ("", ".", "..") and ":" not in part for part in relative.split("/")), "native")
            _keys(info, ("sha256",))
            _need(pipe.cases._hex(info["sha256"]) and disk.digest(root / relative) == info["sha256"], "native")
        _need(retained["native/PREPARED.json"] == {"sha256": _sha(supplied["prepared_receipts"])}, "native")
        key, body = ("PREPARED", supplied["prepared_receipts"]) if phase == "prepare" else ("RESULT.json", supplied["native_prepass"])
        _need(receipt["outputs"].get(key) == {"sha256": _sha(body), "bytes": len(body)}, "native")
        if phase == "prepass":
            _need(retained.get("native/journal/prepass/RESULT.json") == {"sha256": _sha(body)}, "native")


def _run(args, *, preregistration, go, go_sha256, phase, execute, supplied=None):
    _need(execute is True, "dormant")
    consumed, disk, stage = False, None, None
    pins = input_pins(args, preregistration=preregistration) if supplied is None else completion_pins(args, preregistration=preregistration, **supplied)
    root, protocol, authority, expires, last = _authority(args, preregistration, go, go_sha256, phase, pins)
    try:
        with contextlib.ExitStack() as stack:
            capture._volume_check(root)
            cfg = protocol["collection_controller"]
            forest = (root, capture._path(cfg["control_root"], audit=True), capture._path(cfg["ledger_root"], audit=True))
            for tree in forest:
                capture._pin_chain(stack, tree)
            disk = _Disk(root, stack, forest)
            disk.last, disk.expires = last, expires
            owner = _json(capture._read_bounded(root / "OWNER.json"))
            _need(owner == {"schema": "wd.m1-p3-study-root.v1", "study_id": protocol["study_id"],
                "protocol_sha256": args["protocol_sha256"], "root": str(root)}, "binding")
            for module, digest in ((pipe, PIPELINE_SHA256), (capture, CAPTURE_SHA256), (pipe.cases, CASES_SHA256),
                                   (__import__(__name__, fromlist=["*"]), authority["source_sha256"])):
                path = capture._path(module.__file__)
                capture._pin_chain(stack, path)
                _need(_sha(capture._read_bounded(path)) == digest, "source")
            private = root / "private"
            _need(not (private / "HALTED.json").exists() and not (root / "native/HALTED.json").exists()
                  and not (root / "score").exists(), "consumed")
            if not private.exists():
                disk.mkdir(private)
            stage = private / phase
            _need(not os.path.lexists(stage), "consumed")
            disk.mkdir(stage)
            consumed = True
            try:
                disk.put(stage / "INTENT.json", _canon({"schema": "wd.m1-p3-publish-intent.v1", "study_id": protocol["study_id"],
                    "protocol_sha256": args["protocol_sha256"], "go_sha256": go_sha256, "phase": phase,
                    "input_pins": pins, "invocation_consumed": True, "scorer_unlocked": False}))
                plan = pipe.prepare_prepass(**args)
                _original_baseline(disk, plan, protocol)
                originals = _originals(args, preregistration)
                if phase == "prepass_inputs":
                    _need(not os.path.lexists(root / "blind") and not os.path.lexists(root / "baseline")
                          and not os.path.lexists(root / "native"), "consumed")
                    outputs = _prepare_outputs(root, plan, originals)
                    counts = {"SAFE": len(_json(plan.executor_inputs.runner_cases_json))}
                else:
                    _prior(disk, supplied["prior_result"], plan, pins)
                    for name, body in originals.items():
                        disk.read_exact(root / "private/prepass_inputs/original" / name, body)
                    _need(not os.path.lexists(root / "native/arms")
                          and not os.path.lexists(root / "native/RUN_MANIFEST.json"), "native")
                    _native(disk, plan, supplied)
                    disk.clock()
                    result = pipe.complete_cases(plan, expected_plan_sha256=_sha(plan.manifest_json),
                        native_prepass=supplied["native_prepass"], native_prepass_sha256=_sha(supplied["native_prepass"]),
                        prepared_receipts=supplied["prepared_receipts"], prepared_receipts_sha256=_sha(supplied["prepared_receipts"]))
                    artifact = result.case_artifacts
                    outputs = {stage / "cases.pre.json": artifact.cases_pre_json, stage / "cases.json": artifact.cases_json,
                        stage / "denominators.json": artifact.denominators_json, stage / "privacy_applicability.json": result.privacy_applicability_json,
                        stage / "prepass_denominators.json": result.prepass_denominators_json, stage / "provenance.json": result.provenance_json,
                        root / "blind/kept_commitments.json": result.commitments_json,
                        root / "blind/kept_blind.jsonl": artifact.blind_jsonl,
                        root / "blind/kept_time_sidecar.json": result.kept_time_sidecar_json}
                    outputs.update({stage / (key + ".original.json"): body for key, body in supplied.items()})
                    counts = _json(result.prepass_denominators_json)
                receipts = _write_set(disk, outputs)
                disk.scan()
                disk.clock()
                body = _canon({"schema": "wd.m1-p3-publish-result.v1", "status": "COMPLETE", "study_id": protocol["study_id"],
                    "phase": phase, "protocol_sha256": args["protocol_sha256"], "go_sha256": go_sha256,
                    "source_sha256": authority["source_sha256"], "input_pins": pins, "plan_sha256": _sha(plan.manifest_json),
                    "outputs": receipts, "counts": counts, "scorer_unlocked": False, "invocation_consumed": True})
                receipt = disk.put(stage / "RESULT.json", body, terminal=True)
                disk.clock()  # A late/uncertain terminal publication is HALTED,
                # even if COMPLETE bytes were already durably written. HALTED
                # always dominates retained RESULT; nothing is erased/adopted.
                return {"status": "COMPLETE", "phase": phase, "result_sha256": receipt["sha256"], "counts": counts,
                    "invocation_consumed": True, "scorer_unlocked": False}
            except BaseException as error:
                reason = error.reason if type(error) is NotReady else "consumed" if type(error) is FileExistsError else "pipeline" if isinstance(error, pipe.NotReady) else "write"
                alarm = _canon({"status": "STOP_NOT_READY", "phase": phase, "reason": reason,
                    "invocation_consumed": True, "scorer_unlocked": False})
                terminal_ok = True
                for path in (stage / "STOP_NOT_READY.json", private / "HALTED.json"):
                    try:
                        disk.put(path, alarm, terminal=True)
                    except BaseException:
                        terminal_ok = False
                raise NotReady(reason if terminal_ok else "terminal_receipt_unavailable") from None
    except (FileExistsError, FileNotFoundError):
        raise NotReady("consumed" if consumed else "path") from None


@_closed
def publish_prepass_inputs(prepare_args, *, preregistration, go, go_sha256, execute=False):
    """Consume prepass_inputs once and publish exact originals + SAFE inputs."""
    return _run(prepare_args, preregistration=preregistration, go=go, go_sha256=go_sha256,
                phase="prepass_inputs", execute=execute)


@_closed
def publish_complete_cases(prepare_args, *, preregistration, prior_result, native_manifest,
        prepared_receipts, native_prepass, native_prepare_receipt, native_prepass_receipt,
        go, go_sha256, execute=False):
    """Recheck original native/publisher receipts, compose once, publish privately.

    Caller supplies the same original builder inputs; no plan is deserialized
    from a blind artifact. Full SAFE harness inputs are never rewritten.
    """
    supplied = {"prior_result": prior_result, "native_manifest": native_manifest, "prepared_receipts": prepared_receipts,
        "native_prepass": native_prepass, "native_prepare_receipt": native_prepare_receipt,
        "native_prepass_receipt": native_prepass_receipt}
    return _run(prepare_args, preregistration=preregistration, go=go, go_sha256=go_sha256,
                phase="complete_cases", execute=execute, supplied=supplied)


def release_score_package(**_):
    """No future arm/scoring contract is guessed, even with two caller hashes."""
    raise NotReady("score_release_contract_unknown")
