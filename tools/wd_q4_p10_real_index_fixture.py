"""Bounded synthetic P10 prefix fixture; default-off, no live bridge or install.

The pure case factory uses only three public status envelopes. The opt-in worker
uses an independently pinned runtime, the existing fixed E5/prefix builder and
FAISS. It freezes newly built synthetic generation files as CANDIDATE bytes.
It does not call a query seam or manufacture a guarded cleanup witness.

The final guarded caller connector is deliberately unavailable until Lead
freezes its code/config/owner tuple. A captured prefix is not an ANSWER, recall
measurement, D15 pass, installed service, authentication or job-cleanup proof.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

TASK = "lead-mind-production-20261031"
LANES = frozenset(("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"))
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
WEIGHTS = "1a55775f53449dac10a2bcbc312469fac40b96d53198c407081a831f81c98477"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_CONFIG_KEYS = {"schema", "approved_to_execute", "runtime_root", "runtime_manifest",
                "runtime_generation", "runtime_revision", "verifier_sha256", "inputs",
                "model_identity", "output_root"}


class SyntheticFixtureRefused(ValueError):
    pass


class FixtureOutcomeUnknown(RuntimeError):
    pass


class GuardedRouteUnavailable(RuntimeError):
    pass


def _refuse():
    raise SyntheticFixtureRefused("fixture_inputs_refused") from None


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _refuse()
        result[key] = value
    return result


def _json(raw):
    return json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_unique,
                      parse_constant=lambda _: _refuse())


def public_status_case(fleet_bytes):
    """Create fixture DATA, never a bridge event or authenticated lane identity."""
    try:
        if type(fleet_bytes) is not bytes or not 0 < len(fleet_bytes) <= 1024 * 1024:
            _refuse()
        fleet = _json(fleet_bytes)
        entries = [*fleet["lanes"], fleet["tools_supervisor"]]
        if len(entries) != 5 or any(type(x) is not dict for x in entries):
            _refuse()
        roster = {}
        for entry in entries:
            agent, identity = entry["agent"], entry["agent_uuid"]
            if (type(agent) is not str or agent not in LANES or agent in roster
                    or type(identity) is not str or _UUID.fullmatch(identity) is None):
                _refuse()
            roster[agent] = identity
        if set(roster) != LANES or len(set(roster.values())) != 5:
            _refuse()
        events = []
        for minute, agent, status_value in ((0, "codex-lead-1", "info"),
                                            (5, "codex-tools-1", "done"),
                                            (10, "fable-5", "info")):
            events.append({"ts_utc": f"2026-10-31T12:{minute:02d}:00Z",
                "agent": agent, "agent_uuid": roster[agent], "type": "status",
                "status": status_value, "task_id": TASK, "to": "", "message": "",
                "paths": [], "write_scope": [], "payload": {"public_status": True}})
        return {"events": events, "query": {"situation": "what did codex-tools-1 finish done",
                 "time": "2026-10-31T13:00:00Z"},
                "target_text": "status done codex-tools-1 " + TASK,
                "model_or_query_executed": False}
    except SyntheticFixtureRefused:
        raise
    except Exception:
        _refuse()


def run_guarded_probe(_inputs=None):
    """No fake result/callback is an implementation of the final guarded route."""
    raise GuardedRouteUnavailable("final_guarded_tuple_required")


def _run_prefix_once(runner, *, fleet, route, plan, policy, public, anchor):
    """Fleet and canonical roster are distinct inputs on the current runner API."""
    return runner(enabled=True, route_bytes=route, expected_route_sha256=_sha(route),
                  plan_bytes=plan, policy_bytes=policy, public_policy_bytes=public,
                  fleet_bytes=fleet, expected_fleet_sha256=_sha(fleet), anchor_bytes=anchor)


def _plain(path):
    path = Path(path)
    if not path.is_absolute() or any(part in (".", "..") for part in str(path).replace("\\", "/").split("/")):
        _refuse()
    for parent in path.parents:
        info = parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
            _refuse()
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE
            or info.st_nlink != 1):
        _refuse()
    return path


def _read_bounded(path, maximum):
    with _plain(path).open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        _refuse()
    return raw


def _read_descriptor(value, maximum):
    if (type(value) is not dict or set(value) != {"path", "bytes", "sha256"}
            or type(value["bytes"]) is not int or not 0 < value["bytes"] <= maximum
            or type(value["sha256"]) is not str or _HEX.fullmatch(value["sha256"]) is None):
        _refuse()
    path = _plain(value["path"])
    with path.open("rb") as stream:
        raw = stream.read(value["bytes"] + 1)
    if len(raw) != value["bytes"] or _sha(raw) != value["sha256"]:
        _refuse()
    return raw


def _validate(config, output):
    if (type(config) is not dict or set(config) != _CONFIG_KEYS
            or config["schema"] != "wd.q4-p10-real-fixture-config.v1"
            or config["approved_to_execute"] is not True):
        _refuse()
    if (os.name != "nt" or not (sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode)
            or not os.environ.get("PYTEST_ADDOPTS", "").strip()):
        _refuse()
    root, output = Path(config["runtime_root"]), Path(output)
    audit = Path(__file__).resolve().parents[1] / ".codex-audit"
    if (str(root) != config["runtime_root"] or not root.is_absolute() or root.drive.lower() != "c:"
            or str(output) != config["output_root"] or not output.is_absolute()
            or output == audit or audit not in output.parents or output.exists()
            or root == output or root in output.parents or output in root.parents):
        _refuse()
    if any(name in sys.modules for name in ("tools", "sandbox", "core", "faiss", "torch", "sentence_transformers")):
        _refuse()  # Fresh process only; never reuse a monkeypatched/imported backend.
    for value in (config["runtime_generation"], config["runtime_revision"]):
        if type(value) is not str or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) is None:
            _refuse()
    manifest_raw = _read_descriptor(config["runtime_manifest"], 8 * 1024 * 1024)
    manifest = _json(manifest_raw)
    if (manifest["generation"] != config["runtime_generation"] or manifest["revision"] != config["runtime_revision"]
            or type(manifest["files"]) is not list):
        _refuse()
    inputs = config["inputs"]
    if type(inputs) is not dict or set(inputs) != {"policy", "public_policy", "fleet"}:
        _refuse()
    policy, public, fleet = (_read_descriptor(inputs[key], 1024 * 1024)
                             for key in ("policy", "public_policy", "fleet"))
    case = public_status_case(fleet)
    identity = config["model_identity"]
    if (type(identity) is not dict or set(identity) != {"model", "revision", "weights_sha256", "dim", "assets"}
            or identity["model"] != "intfloat/multilingual-e5-small" or identity["revision"] != REVISION
            or identity["weights_sha256"] != WEIGHTS or type(identity["dim"]) is not int or identity["dim"] != 384):
        _refuse()
    model_pins = {row["path"][6:]: row["sha256"] for row in manifest["files"] if row["path"].startswith("model/")}
    if len(model_pins) != 10 or identity["assets"] != model_pins:
        _refuse()
    verifier = root / "assets/code/sandbox/mind/step2/runtime_manifest.py"
    raw = _read_bounded(verifier, 131072)
    if _sha(raw) != config["verifier_sha256"]:
        _refuse()
    scope = {"__name__": "_p10_pinned_runtime_verifier", "__file__": str(verifier)}
    exec(compile(raw, "<p10-pinned-runtime-verifier>", "exec"), scope)
    scope["verify_runtime"](root, Path(config["runtime_manifest"]["path"]),
        config["runtime_manifest"]["sha256"], expected_generation=config["runtime_generation"],
        expected_revision=config["runtime_revision"])
    return root, output, policy, public, fleet, case, identity


def _frozen_generation(root, generation, pointer_bytes):
    """Bounded synthetic file capture, not a verified fixed-peer snapshot bundle."""
    path = root / generation
    files = {}
    total = 0
    for parent, dirs, names in os.walk(path, followlinks=False):
        for name in dirs:
            info = (Path(parent) / name).lstat()
            if getattr(info, "st_file_attributes", 0) & _REPARSE:
                _refuse()
        for name in names:
            source = _plain(Path(parent) / name)
            if len(files) >= 512:
                _refuse()
            raw = _read_bounded(source, 16 * 1024 * 1024 - total)
            total += len(raw)
            if total > 16 * 1024 * 1024 or len(files) >= 512:
                _refuse()
            files[source.relative_to(path).as_posix()] = {"bytes": len(raw),
                "sha256": _sha(raw), "base64": base64.b64encode(raw).decode("ascii")}
    if not files or not any(name.endswith("/index.faiss") for name in files):
        _refuse()
    return {"schema": "wd.q4-p10-frozen-files.v1", "status": "CANDIDATE_UNVERIFIED",
        "generation": generation, "pointer_base64": base64.b64encode(pointer_bytes).decode("ascii"),
        "pointer_sha256": _sha(pointer_bytes), "files": files,
        "cell_native_validation": "NOT_RUN", "guarded_query": "NOT_RUN_FINAL_TUPLE_REQUIRED"}


def build_and_capture(config, output_root):
    """Opt-in worker: real prefix build and candidate capture;never a query bypass."""
    try:
        root, output, policy, public, fleet, case, identity = _validate(config, output_root)
    except SyntheticFixtureRefused:
        raise
    except Exception:
        _refuse()
    output.mkdir()  # Exclusive one-attempt boundary;partial effects are never deleted/retried.
    old_path, old_env = list(sys.path), dict(os.environ)
    try:
        for name in ("input", "checkpoint", "private", "tmp"):
            (output / name).mkdir()
        for name in ("TMP", "TEMP", "TMPDIR"):
            os.environ[name] = str(output / "tmp")
        for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
            os.environ[name] = "1"
        sys.path.insert(0, str(root / "assets/code"))
        sys.path.append(str(root / "site"))
        from dataclasses import asdict
        from tools import wd_q4_status_prefix_runner as runner
        from tools import wd_q4_roster_from_fleet as roster_producer
        from waggledance.core.bridge_log_reader import BridgeCursor, read_bridge_log
        source, sidecar = output / "input/synthetic-status.jsonl", output / "input/generation.json"
        source_raw = b"".join(_canonical(row) + b"\n" for row in case["events"])
        source.write_bytes(source_raw)
        sidecar.write_bytes(_canonical({"generation": "p10-synthetic-v1"}))
        probe = read_bridge_log(source, generation_path=sidecar, max_bytes=65536, max_rows=10)
        if probe.candidate_cursor is None:
            _refuse()
        cursor = BridgeCursor(offset=0, file_identity=probe.candidate_cursor.file_identity, generation="p10-synthetic-v1")
        roster, roster_pin = roster_producer.build_roster_snapshot(fleet, _sha(fleet), enabled=True)
        if _sha(roster) != roster_pin:
            _refuse()
        pins = {"policy_sha256": _sha(policy), "roster_sha256": roster_pin,
                "public_policy_sha256": _sha(public)}
        anchor = _canonical({"schema": "wd.q4-source-anchor.v1", "source_path": str(source),
            "generation_path": str(sidecar), "cursor_start": asdict(cursor),
            **pins, "status_mode": "public-v1"})
        pins["anchor_sha256"] = _sha(anchor)
        generation = _sha(source_raw + config["runtime_revision"].encode("ascii"))
        plan = _canonical({"schema": "wd.q4-prefix-build-plan.v3", "generation": generation,
            "expected_pointer_sha256": None, "max_prefix_bytes": 65536, "max_windows": 1, "timeout": 30,
            "encoder": {**identity, "model_dir": str(root / "model")}, "pins": pins, "fenced": True})
        route = _canonical({"schema": "wd.q4-status-prefix-route.v1", "source": str(source),
            "generation_path": str(sidecar), "checkpoint_root": str(output / "checkpoint"),
            "private_state_root": str(output / "private"), "trusted_model_root": str(root / "model"),
            "cursor": asdict(cursor), "prefix_sha256": _sha(b""), "max_bytes": 65536, "max_rows": 10,
            "plan_sha256": _sha(plan), **pins})
        built = _run_prefix_once(runner.run_once, fleet=fleet, route=route, plan=plan,
                                 policy=policy, public=public, anchor=anchor)
        if built["status"] != "PUBLISHED_GENESIS" or built["generation"] != generation:
            _refuse()
        from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
        pointer_path = output / "checkpoint" / checkpoint.POINTER
        pointer = _read_bounded(pointer_path, 65536)
        if _sha(pointer) != built["pointer_sha256"]:
            _refuse()
        active = output / "checkpoint" / generation
        projection = _json(_read_bounded(active / "projection.json", 1024 * 1024))
        if (len(projection["records"]) != 3
                or case["target_text"] not in {row["stored_text"] for row in projection["records"]}):
            _refuse()
        descriptor = _json(_read_bounded(active / "descriptor.json", 65536))
        if any(descriptor["encoder"][key] != identity[key] for key in ("model", "revision", "weights_sha256")):
            _refuse()
        frozen = _frozen_generation(output / "checkpoint", generation, pointer)
        if _read_bounded(pointer_path, 65536) != pointer:
            _refuse()
        capture = _canonical(frozen)
        (output / "frozen-capture.json").write_bytes(capture)
        result = {"status": "REAL_PREFIX_CAPTURED_GUARDED_QUERY_NOT_RUN", "records": 3,
            "fake_encoder": False, "model_identity": descriptor["encoder"],
            "model_load_count": "FIXED_BUILDER_PATH_NOT_INSTRUMENTED",
            "capture_sha256": _sha(capture), "capture_bytes": len(capture),
            "runtime_manifest_sha256": config["runtime_manifest"]["sha256"],
            "runtime_revision": config["runtime_revision"], "prefix_result": built,
            "guarded_query": "NOT_RUN_FINAL_TUPLE_REQUIRED",
            "cleanup": "DIRECT_PROCESS_EXIT_ONLY_NOT_GUARDED_JOB_PROOF",
            "d15": "CUSTODIAN_RECEIPT_REQUIRED", "pending_markers_retained": True}
        (output / "RESULT.json").write_bytes(_canonical(result))
        return result
    except BaseException:
        raise FixtureOutcomeUnknown("fixture_state_unknown") from None
    finally:
        sys.path[:] = old_path
        os.environ.clear()
        os.environ.update(old_env)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] != "--build-capture":
        _refuse()
    raw = _read_bounded(args[1], 65536)
    config = _json(raw)
    result = build_and_capture(config, config["output_root"])
    sys.stdout.buffer.write(_canonical(result) + b"\n")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SyntheticFixtureRefused, FixtureOutcomeUnknown, GuardedRouteUnavailable, KeyError, OSError):
        sys.stdout.write('{"status":"FIXTURE_NOT_COMPLETED","authority":"none"}\n')
        raise SystemExit(2)

