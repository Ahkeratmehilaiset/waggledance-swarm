"""Default-off, no-I/O composition of a caller-extracted v2 session tuple.

The descriptor argument is the already parsed v2 shared-session helper output.
This function does not read or verify the original
descriptor, runtime, host scripts, or actual session. When opted in, it checks
fixed path/pins against externally anchored deployment-manifest bytes only.
The opt-in result adds ``manifest_binding`` and ``manifest_additional_pins``;
the latter contains only manifest-derived command and runtime-bootstrap pins,
not evidence that those files have been read or verified.
The caller must independently authenticate every byte and pin, the extraction,
lane identity and clock; hash equality is not authentication or freshness.

No query argument exists. The caller must ensure that configuration fields do
not themselves contain query text or secrets, and must never shell-interpolate
the returned argv. The candidate is not a connection or authorization to run.

The caller supplies the host Job timeout independently and strictly above the
inner fixed-peer timeout. The difference is a caller-chosen margin, not a
proven sufficient budget: preflight and I/O can block outside that Job clock.
An independently bounded whole-call watchdog is still required.
"""

import os
from pathlib import Path
import re

from tools.wd_q4_attributed_session_tuple import resolve_attributed_session_tuple
from tools.wd_q4_manifest_host_binding import resolve_manifest_host_binding


_HEX = re.compile(r"[0-9a-f]{64}\Z")
_PARTS = frozenset({"worker", "outer", "expected_binding"})
_DUPLICATES = {"lane": "lane", "fleet_path": "fleet", "fleet_sha256": "fleet-sha256",
    "bundle_root": "bundle-root", "public_plan": "public-plan", "interpreter": "interpreter",
    "interpreter_sha256": "interpreter-sha256", "interpreter_max_bytes": "interpreter-max-bytes",
    "cwd": "cwd", "timeout_seconds": "timeout-seconds", "max_output_bytes": "max-output-bytes",
    "binding_path": "binding", "binding_sha256": "binding-sha256", "fixed_plan_path": "fixed-plan",
    "fixed_plan_sha256": "fixed-plan-sha256", "snapshot_path": "snapshot", "snapshot_sha256": "snapshot-sha256",
    "runtime_manifest_path": "runtime-manifest", "runtime_manifest_sha256": "runtime-manifest-sha256"}
_DESCRIPTOR = _PARTS | frozenset(_DUPLICATES)
_IDENTITY = frozenset({"bundle_commit", "manifest_sha256", "wrapper_sha256", "public_plan_sha256"})
_HOST = frozenset({"HostScript", "HostSha256", "JobHelper", "JobHelperSha256", "Interpreter",
                   "InterpreterSha256", "Bootstrap", "BootstrapSha256", "WorkingDirectory",
                   "ScratchRoot", "TimeoutSeconds", "MaxOutputBytes"})
_PATHS = ("HostScript", "JobHelper", "Interpreter", "Bootstrap", "WorkingDirectory", "ScratchRoot")
_UNSET = object()


def _path(value):
    if (type(value) is not str or not value or len(value) > 32767
            or any(ord(char) < 32 for char in value)):
        raise ValueError()
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or value.startswith(("\\\\", "//")):
        raise ValueError()
    return os.path.normcase(os.path.abspath(value))


def resolve_descriptor_host_tuple(*, fleet_bytes=None, expected_fleet_sha256=None, binding_bytes=None,
                                  expected_binding_sha256=None, lane=None, descriptor=None,
                                  runtime_identity=None, host=None, enabled=False,
                                  manifest_bytes=_UNSET, expected_manifest_sha256=_UNSET,
                                  expected_manifest_commit=_UNSET):
    """Return a host-argument candidate, without file reads or execution."""
    if enabled is False:
        return {"status": "OFF"}
    result = runtime = attributed = paths = arguments = None
    try:
        if (enabled is not True or type(descriptor) is not dict or set(descriptor) != _DESCRIPTOR
                or type(runtime_identity) is not dict or set(runtime_identity) != _IDENTITY
                or type(host) is not dict or set(host) != _HOST):
            raise ValueError()
        if any(type(descriptor[key]) is not dict for key in _PARTS):
            raise ValueError()
        paths = {key: _path(host[key]) for key in _PATHS}
        for key in ("HostSha256", "JobHelperSha256", "InterpreterSha256", "BootstrapSha256"):
            if type(host[key]) is not str or _HEX.fullmatch(host[key]) is None:
                raise ValueError()
        for key, maximum in (("TimeoutSeconds", 1800), ("MaxOutputBytes", 65536)):
            if type(host[key]) is not int or not 1 <= host[key] <= maximum:
                raise ValueError()
        if (Path(host["HostScript"]).name.lower() != "invoke-wdq4sessionhost.ps1"
                or Path(host["JobHelper"]).name.lower() != "wdq4processjob.ps1"
                or Path(host["Bootstrap"]).name.lower() != "wd_q4_session_outer_entry.py"):
            raise ValueError()
        checked = {}
        for name, pin in (("HostScript", "HostSha256"), ("JobHelper", "JobHelperSha256"),
                          ("Interpreter", "InterpreterSha256"), ("Bootstrap", "BootstrapSha256")):
            if paths[name] in checked and checked[paths[name]] != host[pin]:
                raise ValueError()
            checked[paths[name]] = host[pin]
        worker = descriptor["worker"]
        for source_key, worker_key in _DUPLICATES.items():
            value = descriptor[source_key]
            if source_key in ("interpreter_max_bytes", "timeout_seconds", "max_output_bytes"):
                if type(value) is not int:
                    raise ValueError()
                value = str(value)
            if type(value) is not str or value != worker[worker_key]:
                raise ValueError()
        if (paths["Interpreter"] != _path(worker["interpreter"])
                or host["InterpreterSha256"] != worker["interpreter-sha256"]
                or paths["WorkingDirectory"] != _path(worker["cwd"])
                or host["TimeoutSeconds"] <= int(worker["timeout-seconds"])
                or str(host["MaxOutputBytes"]) != worker["max-output-bytes"]):
            raise ValueError()
        manifest_opt_in = any(value is not _UNSET for value in
                              (manifest_bytes, expected_manifest_sha256, expected_manifest_commit))
        if manifest_opt_in:
            if (any(value is _UNSET for value in (manifest_bytes, expected_manifest_sha256,
                                                 expected_manifest_commit))
                    or expected_manifest_sha256 != runtime_identity["manifest_sha256"]
                    or expected_manifest_commit != runtime_identity["bundle_commit"]):
                raise ValueError()
            pins = resolve_manifest_host_binding(manifest_bytes,
                expected_manifest_sha256=expected_manifest_sha256,
                expected_commit=expected_manifest_commit, enabled=True)
            for path_key, hash_key, relative in (
                ("HostScript", "HostSha256", "Invoke-WdQ4SessionHost.ps1"),
                ("JobHelper", "JobHelperSha256", "WdQ4ProcessJob.ps1"),
                ("Bootstrap", "BootstrapSha256", "tools-bootstrap/tools/wd_q4_session_outer_entry.py")):
                expected_path = os.path.join(descriptor["bundle_root"], *relative.split('/'))
                if paths[path_key] != _path(expected_path) or host[hash_key] != pins[relative]:
                    raise ValueError()
        runtime = {key: dict(descriptor[key]) for key in _PARTS}
        runtime.update(runtime_identity)
        attributed = resolve_attributed_session_tuple(fleet_bytes=fleet_bytes,
            expected_fleet_sha256=expected_fleet_sha256, binding_bytes=binding_bytes,
            expected_binding_sha256=expected_binding_sha256, lane=lane, runtime_tuple=runtime, enabled=True)
        arguments = {key: host[key] for key in _HOST if key not in ("HostScript", "HostSha256")}
        arguments["BootstrapArguments"] = list(attributed["argv"])
        result = {"status": "TUPLE_CONSISTENT", "attribution": "DECLARED_NOT_AUTHENTICATED",
                  "freshness": "FRESHNESS_UNKNOWN", "host_script": host["HostScript"],
                  "host_sha256": host["HostSha256"], "host_arguments": arguments}
        if manifest_opt_in:
            result["manifest_binding"] = "MANIFEST_PINS_ONLY"
            result["manifest_additional_pins"] = {
                name: pins[name] for name in ("Invoke-WdQ4SessionCommand.ps1",
                    "tools-bootstrap/sandbox/mind/step2/runtime_bootstrap.py")}
    except Exception:
        pass
    if result is not None:
        return result
    fleet_bytes = expected_fleet_sha256 = binding_bytes = expected_binding_sha256 = lane = None
    descriptor = runtime_identity = host = enabled = runtime = attributed = paths = arguments = None
    worker = checked = key = name = pin = source_key = worker_key = value = None
    manifest_bytes = expected_manifest_sha256 = expected_manifest_commit = pins = None
    manifest_opt_in = expected_path = path_key = hash_key = relative = None
    raise ValueError("descriptor_host_tuple_refused") from None
