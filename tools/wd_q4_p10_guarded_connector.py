"""Explicit P10 candidate: captured synthetic genesis -> composer -> guarded CLI.

No work on import; no activation, installation, live bridge reads or fake answer
callback. capture_parts only maps bounded pinned bytes, never validates FAISS.
The opt-in worker requires independently approved, freshly frozen inputs, an
independent CURRENT digest and explicit live_mode="frozen". It
verifies their runtime before imports, uses the existing fixed bundle serializer,
binding producer, staged composer and actual caller CLI. The caller's real native
owners must supply same-call cleanup; no witness is manufactured here.

Only three-row P10 genesis with one runtime-known cell is supported. Unknown shapes,
extra files and history refuse. Failed/partial output is retained without retry.
The invoking custodian must independently bound the WHOLE worker with a watchdog;
the inner transport's Job/cleanup does not prove an outer hard deadline. Python
environment flags do not establish child D15 coverage (reported UNKNOWN).
Pins and observed cooperative cleanup are not authentication or OS isolation.
"""
import base64
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import stat
import sys

MAX_CAPTURE = 16 * 1024 * 1024
MAX_RAW = 8 * 1024 * 1024
_HEX = re.compile(r"[0-9a-f]{64}\Z")
# Closed vocabulary from the pinned runtime topology. Keep this pure byte mapper
# independent of runtime imports; the regression checks topology drift explicitly.
_P10_CELLS = frozenset({"general", "thermal", "energy", "safety", "seasonal", "math", "system", "learning"})
_ACTIVE = {"descriptor_sha256": "descriptor.json", "memory_sha256": "memory.json",
           "projection_sha256": "projection.json", "receipt_sha256": "build_receipt.json"}
_CAPTURE = {"schema", "status", "generation", "pointer_base64", "pointer_sha256", "files",
            "cell_native_validation", "guarded_query"}
_CONFIG = {"schema", "approved_to_execute", "runtime_root", "runtime_manifest", "runtime_generation",
           "runtime_revision", "verifier_sha256", "capture", "policy", "public_policy", "fleet",
           "checkpoint_root", "bundle_root", "bundle_manifest", "bundle_commit",
           "public_plan_template", "request_template", "output_root", "bundle_store_root", "active_bundle_root",
           "expected_state_sha256", "live_mode"}


class ConnectorRefused(ValueError):
    pass


class ConnectorOutcomeUnknown(RuntimeError):
    pass


def _check(ok):
    if not ok:
        raise ConnectorRefused("p10_connector_refused")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _raw(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def _pairs(items):
    result = {}
    for key, value in items:
        _check(key not in result)
        result[key] = value
    return result


def _json(raw):
    return json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=_pairs,
                      parse_constant=lambda _: _check(False))


def _b64(value):
    _check(type(value) is str and len(value) <= MAX_CAPTURE)
    raw = base64.b64decode(value.encode("ascii"), validate=True)
    _check(base64.b64encode(raw).decode("ascii") == value)
    return raw


def _capture_parts(capture_bytes, expected_capture_sha256, policy_bytes, public_policy_bytes, roster_bytes):
    _check(type(capture_bytes) is bytes and 0 < len(capture_bytes) <= MAX_CAPTURE
           and type(expected_capture_sha256) is str and _HEX.fullmatch(expected_capture_sha256)
           and _sha(capture_bytes) == expected_capture_sha256)
    _check(all(type(value) is bytes and 0 < len(value) <= 1024 * 1024
               for value in (policy_bytes, public_policy_bytes, roster_bytes)))
    capture = _json(capture_bytes)
    _check(type(capture) is dict and set(capture) == _CAPTURE
           and capture["schema"] == "wd.q4-p10-frozen-files.v1"
           and capture["status"] == "CANDIDATE_UNVERIFIED"
           and capture["cell_native_validation"] == "NOT_RUN"
           and capture["guarded_query"] == "NOT_RUN_FINAL_TUPLE_REQUIRED"
           and type(capture["files"]) is dict and 0 < len(capture["files"]) <= 512)
    pointer_raw = _b64(capture["pointer_base64"])
    _check(len(pointer_raw) <= 65536 and _sha(pointer_raw) == capture["pointer_sha256"])
    pointer = _json(pointer_raw)
    _check(pointer["generation"] == capture["generation"]
           and type(pointer["generation"]) is str
           and re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", pointer["generation"])
           and pointer["previous"] is None and pointer["previous_pointer_sha256"] is None
           and pointer["policy_sha256"] == _sha(policy_bytes)
           and pointer["roster_sha256"] == _sha(roster_bytes)
           and type(pointer["artifacts"]) is dict and set(pointer["artifacts"]) == set(_ACTIVE))
    files, total = {}, 0
    for name, item in capture["files"].items():
        _check(type(name) is str and 0 < len(name) <= 256 and "\\" not in name
               and all(re.fullmatch(r"[a-zA-Z0-9_-]+(?:\.[a-zA-Z0-9_-]+)*", part)
                       for part in name.split("/"))
               and type(item) is dict and set(item) == {"bytes", "sha256", "base64"}
               and type(item["bytes"]) is int and 0 < item["bytes"] <= MAX_RAW
               and type(item["sha256"]) is str and _HEX.fullmatch(item["sha256"]))
        data = _b64(item["base64"])
        total += len(data)
        _check(total <= MAX_RAW and len(data) == item["bytes"] and _sha(data) == item["sha256"])
        files[name] = data
    active = {name: files[name] for name in _ACTIVE.values()}
    _check(all(_sha(active[name]) == pointer["artifacts"][pin] for pin, name in _ACTIVE.items()))
    memory = _json(active["memory.json"])
    _check(type(memory["cells"]) is dict and len(memory["cells"]) == 1
           and set(memory["cells"]).issubset(_P10_CELLS))
    used, cells = set(active), {}
    for cell, bound in memory["cells"].items():
        _check(type(bound) is dict and set(bound) in ({"generation", "receipt_sha256"},
                                                     {"generation", "receipt_sha256", "count", "seq"})
               and type(bound["generation"]) is int and 0 <= bound["generation"] <= 999999
               and type(bound["receipt_sha256"]) is str and _HEX.fullmatch(bound["receipt_sha256"]))
        if "count" in bound:
            _check(all(type(bound[key]) is int and bound[key] >= 0 for key in ("count", "seq")))
        prefix = "cells/" + cell + "/"
        gen = prefix + "gen-" + format(bound["generation"], "06d") + "/"
        paths = {"index": gen + "faiss/cell_" + cell + "/index.faiss",
                 "meta": gen + "faiss/cell_" + cell + "/meta.json",
                 "vectors": gen + "vectors.f32", "provenance": gen + "provenance.jsonl",
                 "receipt": gen + "receipt.json"}
        artifacts = {key: files[name] for key, name in paths.items()}
        control = _json(files[prefix + "current.json"])
        _check(type(control) is dict and set(control) in ({"generation", "receipt_sha256"},
                                                         {"generation", "receipt_sha256", "seq"})
               and type(control["generation"]) is int and control["generation"] == bound["generation"]
               and control["receipt_sha256"] == bound["receipt_sha256"] == _sha(artifacts["receipt"]))
        if "seq" in control:
            _check(type(control["seq"]) is int and control["seq"] >= 0)
        if "seq" in bound:
            _check(control.get("seq") == bound["seq"])
        receipt = _json(artifacts["receipt"])
        if "count" in bound:
            _check(type(receipt["content"]["count"]) is int and receipt["content"]["count"] == bound["count"])
        _check(receipt["content"]["files"] == {key: _sha(value) for key, value in artifacts.items()
                                               if key != "receipt"})
        cells[cell] = {"pointer": files[prefix + "current.json"], "log": files[prefix + "LOG.jsonl"],
                       "artifacts": artifacts}
        used.update(paths.values())
        used.update((prefix + "current.json", prefix + "LOG.jsonl"))
    _check(set(files) == used)
    return {"pointer_bytes": pointer_raw, "policy_bytes": policy_bytes, "roster_bytes": roster_bytes,
            "public_policy_bytes": public_policy_bytes, "active_artifact_bytes": active,
            "history_bytes": {}, "cell_snapshots": cells}


def capture_parts(capture_bytes, *, expected_capture_sha256, policy_bytes, public_policy_bytes, roster_bytes):
    """Map exact P10 genesis bytes only; real consumers retain semantic/native checks."""
    try:
        return _capture_parts(capture_bytes, expected_capture_sha256, policy_bytes, public_policy_bytes, roster_bytes)
    except Exception:
        pass
    raise ConnectorRefused("p10_connector_refused") from None


def _path(value):
    _check(type(value) is str and value and len(value) <= 512)
    path = Path(value)
    _check(path.is_absolute() and path.drive.lower() == "c:" and not value.startswith(("\\\\", "//")))
    for part in value.replace("\\", "/").split("/")[1:]:
        _check(part and part not in (".", "..") and not part.endswith((" ", ".")) and ":" not in part
               and re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?", part, re.I) is None)
    for parent in (path, *path.parents):
        if parent.exists():
            info = parent.lstat()
            _check(not stat.S_ISLNK(info.st_mode)
                   and not (getattr(info, "st_file_attributes", 0) & 0x400))
    return path


def _read(descriptor, maximum):
    _check(type(descriptor) is dict and set(descriptor) == {"path", "bytes", "sha256"}
           and type(descriptor["bytes"]) is int and 0 < descriptor["bytes"] <= maximum
           and type(descriptor["sha256"]) is str and _HEX.fullmatch(descriptor["sha256"]))
    path = _path(descriptor["path"])
    info = path.stat()
    _check(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size == descriptor["bytes"])
    with path.open("rb") as stream:
        raw = stream.read(descriptor["bytes"] + 1)
    _check(len(raw) == descriptor["bytes"] and _sha(raw) == descriptor["sha256"])
    return raw


def _exclusive(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _state_pin(config):
    """An independent caller pin, never derived from the mutable CURRENT file."""
    pin = config.get("expected_state_sha256")
    _check(type(pin) is str and _HEX.fullmatch(pin))
    return pin


def synthetic_probe_spec():
    """Fresh fixed public inputs, identical to the original P10 fixture."""
    return {"query": {"situation": "what did codex-tools-1 finish done",
                      "time": "2026-10-31T13:00:00Z"},
            "target_text": "status done codex-tools-1 lead-mind-production-20261031"}


def _compose_staged(composer, *, config, stage, bundle, manifest, public_plan):
    """c86 contract: preserve the composer's identity/root/CURRENT checks."""
    state_pin = _state_pin(config)
    return composer.compose(enabled=True, request_path=str(Path(config["output_root"]) / "request.json"),
        stage_dir=str(stage), expected_bundle=str(bundle), expected_commit=config["bundle_commit"],
        expected_manifest_sha256=_sha(manifest).upper(), expected_public_plan_sha256=_sha(public_plan),
        generation_root=str(stage.parent), bundle_store_root=config["bundle_store_root"],
        active_bundle_root=config["active_bundle_root"], expected_state_sha256=state_pin)


@contextmanager
def _frozen_caller_environment(staged, manifest_sha256, mode):
    """The P10 captured snapshot never opts into an ambient mutable selector."""
    _check(type(mode) is str and mode == "frozen")
    _check(type(staged) is dict and type(staged.get("descriptor_path")) is str
           and staged["descriptor_path"] and type(staged.get("descriptor_sha256")) is str
           and _HEX.fullmatch(staged["descriptor_sha256"])
           and type(manifest_sha256) is str and _HEX.fullmatch(manifest_sha256))
    values = {"WD_Q4_LIVE_MODE": "", "WD_Q4_SHARED_DESCRIPTOR_PATH": staged["descriptor_path"],
              "WD_Q4_SHARED_DESCRIPTOR_SHA256": staged["descriptor_sha256"],
              "WD_REBOOT_EXPECTED_MANIFEST_HASH": manifest_sha256}
    missing = object()
    previous = {key: os.environ.get(key, missing) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key, value in previous.items():
            if value is missing:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _verified_runtime(config):
    _check(os.name == "nt" and sys.flags.isolated and sys.flags.no_site and sys.dont_write_bytecode)
    _check(not any(name in sys.modules for name in ("tools", "sandbox", "core", "waggledance", "faiss", "torch")))
    root = _path(config["runtime_root"])
    manifest = _read(config["runtime_manifest"], 8 * 1024 * 1024)
    verifier = root / "assets/code/sandbox/mind/step2/runtime_manifest.py"
    info = verifier.stat()
    raw = _read({"path": str(verifier), "bytes": info.st_size,
                 "sha256": config["verifier_sha256"]}, 131072)
    scope = {"__name__": "_p10_connector_verified_runtime", "__file__": str(verifier)}
    exec(compile(raw, "<p10-verified-runtime>", "exec"), scope)
    scope["verify_runtime"](root, Path(config["runtime_manifest"]["path"]), config["runtime_manifest"]["sha256"],
                            expected_generation=config["runtime_generation"], expected_revision=config["runtime_revision"])
    return root, manifest


def _run(config):
    _check(type(config) is dict and set(config) == _CONFIG
           and config["schema"] == "wd.q4-p10-guarded-config.v1" and config["approved_to_execute"] is True)
    _state_pin(config)
    _check(type(config["live_mode"]) is str and config["live_mode"] == "frozen")
    output = _path(config["output_root"])
    audit = Path(__file__).resolve().parents[1] / ".codex-audit"
    _check(audit in output.parents and not output.exists())
    root, runtime_manifest = _verified_runtime(config)
    _check(root != output and root not in output.parents and output not in root.parents)
    policy, public, fleet = (_read(config[key], 1024 * 1024) for key in ("policy", "public_policy", "fleet"))
    capture = _read(config["capture"], MAX_CAPTURE)
    request = _json(_read(config["request_template"], 65536))
    public_plan = _json(_read(config["public_plan_template"], 65536))
    manifest = _read(config["bundle_manifest"], 2 * 1024 * 1024)
    bundle = _path(config["bundle_root"])
    store = _path(config["bundle_store_root"])
    active_bundle = _path(config["active_bundle_root"])
    _check(_path(config["bundle_manifest"]["path"]) == bundle / "deployment-manifest.json")
    _check(type(config["bundle_commit"]) is str and re.fullmatch(r"[0-9a-f]{40}", config["bundle_commit"]))
    _check(config["runtime_revision"] == config["bundle_commit"])
    checkpoint = _path(config["checkpoint_root"])
    _check(audit in checkpoint.parents)
    guard_root = _path(_json(fleet)["runtime_root"])
    _check(output in guard_root.parents)
    sys.path.insert(0, str(root / "assets/code"))
    sys.path.append(str(root / "site"))
    from tools import wd_q4_roster_from_fleet as roster_producer
    from tools import wd_q4_secure_transport as transport
    from tools import wd_q4_lane_binding_producer as binding_producer
    from tools import wd_q4_staged_composer as composer
    from tools import wd_q4_lane_caller_cli as caller
    from tools import wd_q4_guard_state_provision as provision
    roster, _ = roster_producer.build_roster_snapshot(fleet, _sha(fleet), enabled=True)
    parts = capture_parts(capture, expected_capture_sha256=config["capture"]["sha256"],
                           policy_bytes=policy, public_policy_bytes=public, roster_bytes=roster)
    snapshot = transport.fixed_snapshot_bundle(**parts)
    pointer = _json(parts["pointer_bytes"])
    active = parts["active_artifact_bytes"]
    descriptor = _json(active["descriptor.json"])
    current = checkpoint / "q4-latest.json"
    current_desc = {"path": str(current), "bytes": len(parts["pointer_bytes"]), "sha256": _sha(parts["pointer_bytes"])}
    _check(_read(current_desc, 65536) == parts["pointer_bytes"])
    # All runtime-related values in the reviewed template must already match.
    fixed = request["fixed_plan"]
    _check(fixed["runtime_root"] == str(root) and fixed["runtime_generation"] == config["runtime_generation"]
           and fixed["runtime_revision"] == config["runtime_revision"]
           and fixed["runtime_manifest_path"] == config["runtime_manifest"]["path"]
           and fixed["runtime_manifest_sha256"] == _sha(runtime_manifest)
           and fixed["verifier_sha256"] == config["verifier_sha256"])
    output.mkdir()
    stage = output / "generations" / "stage"
    stage.mkdir(parents=True)
    for name, data in (("policy.json", policy), ("public-policy.json", public), ("roster.json", roster),
                       ("snapshot.json", snapshot)):
        _exclusive(output / name, data)
    public_plan.update(root=str(checkpoint), policy_path=str(output / "policy.json"),
        roster_path=str(output / "roster.json"), public_policy_path=str(output / "public-policy.json"),
        pointer_sha256=_sha(parts["pointer_bytes"]), source_anchor_sha256=pointer["anchor_sha256"],
        encoder_sha256=_sha(_raw(descriptor["encoder"])), policy_sha256=_sha(policy),
        roster_sha256=_sha(roster), public_policy_sha256=_sha(public))
    public_plan["config"]["descriptor_sha256"] = _sha(active["descriptor.json"])
    public_raw = _raw(public_plan)
    _exclusive(output / "public-plan.json", public_raw)
    wrapper = bundle / "Invoke-WdQ4Query.ps1"
    wrapper_raw = _read({"path": str(wrapper), "bytes": wrapper.stat().st_size,
                         "sha256": _json(manifest)["files"]["Invoke-WdQ4Query.ps1"].lower()}, 1024 * 1024)
    binding = binding_producer.build_lane_binding(enabled=True, expected_commit=config["bundle_commit"],
        manifest_bytes=manifest, expected_manifest_sha256=_sha(manifest), fleet_bytes=fleet,
        expected_fleet_sha256=_sha(fleet), wrapper_bytes=wrapper_raw, expected_wrapper_sha256=_sha(wrapper_raw),
        public_plan_bytes=public_raw, expected_public_plan_sha256=_sha(public_raw),
        snapshot_bytes=snapshot, expected_snapshot_sha256=_sha(snapshot))
    pins = _json(binding["binding_bytes"])["binding"]
    request.update(target_dir=str(stage), fleet_path=str(bundle / "wd-fleet.json"), fleet_sha256=_sha(fleet),
        binding_path=str(stage / "binding-source.json"), binding_sha256="0" * 64,
        snapshot_path=str(output / "snapshot.json"), snapshot_sha256=_sha(snapshot))
    request["descriptor"].update(bundle_root=str(bundle), public_plan=str(output / "public-plan.json"), expected_binding=pins)
    fixed.update(bundle_sha256=_sha(snapshot), bundle_bytes=len(snapshot),
        expected_pointer_sha256=pins["pointer_sha256"], policy_sha256=pins["policy_sha256"],
        roster_sha256=pins["roster_sha256"], expected_public_policy_sha256=pins["public_policy_sha256"],
        expected_source_sha256=pins["source_anchor_sha256"], expected_encoder_sha256=pins["encoder_sha256"])
    _exclusive(output / "request.json", _raw(request))
    staged = _compose_staged(composer, config=config, stage=stage, bundle=bundle,
                              manifest=manifest, public_plan=public_raw)
    # Synthetic guard ancestors only; never point this at the live fleet root.
    (guard_root / "shared/.codex-audit").mkdir(parents=True)
    provision.provision_guard_state(enabled=True, fleet_bytes=fleet, expected_fleet_sha256=_sha(fleet))
    state = guard_root / "shared/.codex-audit/codex-tools-1/q4-caller-state"
    _check(not list(state.iterdir()))
    probe = synthetic_probe_spec()
    query = _raw(probe["query"])
    old_stdin, old_stdout, old_stderr = sys.stdin, sys.stdout, sys.stderr
    stdout, stderr = io.StringIO(), io.StringIO()
    sys.stdin = io.TextIOWrapper(io.BytesIO(query), encoding="ascii")
    sys.stdout, sys.stderr = stdout, stderr
    try:
        with _frozen_caller_environment(staged, _sha(manifest), config["live_mode"]):
            code = caller.main(["--enabled"])
    finally:
        sys.stdin, sys.stdout, sys.stderr = old_stdin, old_stdout, old_stderr
    _check(type(code) is int and code == 0 and not stderr.getvalue() and len(stdout.getvalue()) <= 65536)
    result = _json(stdout.getvalue().encode("ascii"))
    _check(result["status"] == "VALIDATED" and result["cleanup"] == "chain_confirmed"
           and result["lane"] == "codex-tools-1" and result["answer"]["binding"] == pins
           and result["inner_status"] == "ANSWER" and not list(state.iterdir())
           and _read(current_desc, 65536) == parts["pointer_bytes"])
    target = probe["target_text"]
    _check(any(item["stored_text"] == target for item in result["answer"]["results"]))
    return {"status": "GUARDED_SYNTHETIC_ANSWER", "cleanup": result["cleanup"],
            "answer": result, "staged": staged, "child_hook_coverage": "UNKNOWN",
            "outer_watchdog": "CUSTODIAN_RECEIPT_REQUIRED", "authority": "none"}


def run_guarded_candidate(config):
    """Fresh isolated worker only. Failure after any write is UNKNOWN, never retried."""
    _check(type(config) is dict and config.get("approved_to_execute") is True)
    old_path, old_env = list(sys.path), dict(os.environ)
    try:
        return _run(config)
    except BaseException:
        pass
    finally:
        sys.path[:] = old_path
        os.environ.clear()
        os.environ.update(old_env)
    raise ConnectorOutcomeUnknown("p10_connector_state_unknown") from None


if __name__ == "__main__":
    try:
        _check(len(sys.argv) == 4 and sys.argv[1] == "--config")
        path = _path(sys.argv[2])
        config = _json(_read({"path": str(path), "bytes": path.stat().st_size, "sha256": sys.argv[3]}, 65536))
        result = run_guarded_candidate(config)
        output = _path(config["output_root"]) / "RESULT.json"
        _exclusive(output, _raw(result))
        sys.stdout.write('{"status":"GUARDED_SYNTHETIC_ANSWER","authority":"none"}\n')
    except BaseException:
        sys.stdout.write('{"status":"CONNECTOR_NOT_COMPLETED","authority":"none"}\n')
        raise SystemExit(2)
