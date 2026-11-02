"""Synthetic descriptor-to-host tuple tests; no agent, query or live runtime."""
import copy
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tools import wd_q4_session_outer_entry as entry, wd_q4_roster_from_fleet as roster


def test_descriptor_host_resolver_is_packaged_as_a_library():
    definition = json.loads((Path(__file__).resolve().parents[2] / "ops/windows/reboot/bridge-code-files.json").read_bytes())
    resolver = "tools/wd_q4_descriptor_host_tuple.py"
    assert definition["python_files"].count(resolver) == 1
    assert resolver not in definition["python_entrypoints"].values()


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


@pytest.fixture
def candidate(tmp_path):
    lanes = ["codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"]
    fleet = _bytes({"schema_version": 2, "fleet_name": "synthetic-fleet",
        "lanes": [{"agent": name, "agent_uuid": f"{i:08x}-abcd-4ef0-8abc-000000000001"}
                  for i, name in enumerate(lanes) if name != "codex-tools-1"],
        "tools_supervisor": {"agent": "codex-tools-1", "agent_uuid": "00000009-abcd-4ef0-8abc-000000000002"}})
    _, roster_sha = roster.build_roster_snapshot(fleet, _sha(fleet), enabled=True)
    pins = {key: _sha(key.encode("ascii")) for key in entry._BINDING_KEYS}
    pins["roster_sha256"] = roster_sha
    binding = _bytes({"schema": "wd.q4-lane-binding.v1", "bundle_commit": "a" * 40,
        "manifest_sha256": "b" * 64, "wrapper_sha256": "c" * 64,
        "public_plan_sha256": "d" * 64, "binding": pins, "lanes": lanes})
    worker = {key: str(tmp_path / key) for key in entry._WORKER_KEYS}
    for key in worker:
        if key.endswith("sha256"):
            worker[key] = _sha(key.encode("ascii"))
    worker.update({"lane": lanes[0], "fleet-sha256": _sha(fleet), "binding-sha256": _sha(binding),
                   "interpreter-max-bytes": "1024", "timeout-seconds": "30", "max-output-bytes": "4096"})
    outer = {key: _sha(key.encode("ascii")) for key in entry._OUTER_KEYS}
    outer.update({"runtime-root": str(tmp_path / "runtime"), "runtime-generation": "e" * 40,
        "runtime-revision": "f" * 40,
        "outer-path": str(tmp_path / "runtime/assets/code/tools/wd_q4_session_outer_synthetic.py")})
    mapping = {"lane": "lane", "fleet_path": "fleet", "fleet_sha256": "fleet-sha256",
        "bundle_root": "bundle-root", "public_plan": "public-plan", "interpreter": "interpreter",
        "interpreter_sha256": "interpreter-sha256", "interpreter_max_bytes": "interpreter-max-bytes",
        "cwd": "cwd", "timeout_seconds": "timeout-seconds", "max_output_bytes": "max-output-bytes",
        "binding_path": "binding", "binding_sha256": "binding-sha256", "fixed_plan_path": "fixed-plan",
        "fixed_plan_sha256": "fixed-plan-sha256", "snapshot_path": "snapshot", "snapshot_sha256": "snapshot-sha256",
        "runtime_manifest_path": "runtime-manifest", "runtime_manifest_sha256": "runtime-manifest-sha256"}
    descriptor = {key: (int(worker[value]) if key in {"interpreter_max_bytes", "timeout_seconds", "max_output_bytes"}
                         else worker[value]) for key, value in mapping.items()}
    descriptor.update(worker=worker, outer=outer, expected_binding=pins)
    identity = {"bundle_commit": "a" * 40, "manifest_sha256": "b" * 64,
                "wrapper_sha256": "c" * 64, "public_plan_sha256": "d" * 64}
    host = {"HostScript": str(tmp_path / "Invoke-WdQ4SessionHost.ps1"), "HostSha256": "1" * 64,
        "JobHelper": str(tmp_path / "WdQ4ProcessJob.ps1"), "JobHelperSha256": "2" * 64,
        "Interpreter": worker["interpreter"], "InterpreterSha256": worker["interpreter-sha256"],
        "Bootstrap": str(tmp_path / "wd_q4_session_outer_entry.py"), "BootstrapSha256": "3" * 64,
        "WorkingDirectory": worker["cwd"], "ScratchRoot": str(tmp_path / "scratch"),
        "TimeoutSeconds": 30, "MaxOutputBytes": 4096}
    return {"fleet_bytes": fleet, "expected_fleet_sha256": _sha(fleet), "binding_bytes": binding,
        "expected_binding_sha256": _sha(binding), "lane": lanes[0], "descriptor": descriptor,
        "runtime_identity": identity, "host": host}


def _resolve(**kwargs):
    from tools.wd_q4_descriptor_host_tuple import resolve_descriptor_host_tuple
    return resolve_descriptor_host_tuple(**kwargs)


def test_off_does_not_inspect_or_call_inputs(monkeypatch):
    import tools.wd_q4_descriptor_host_tuple as module
    monkeypatch.setattr(module, "resolve_attributed_session_tuple", lambda **_: pytest.fail("called"))
    monkeypatch.setattr("builtins.open", lambda *_, **__: pytest.fail("file I/O"))
    class Trap:
        def __getattribute__(self, name):
            raise AssertionError("input inspected")
    assert _resolve(fleet_bytes=Trap(), expected_fleet_sha256=Trap(), binding_bytes=Trap(),
                    expected_binding_sha256=Trap(), lane=Trap(), descriptor=Trap(),
                    runtime_identity=Trap(), host=Trap()) == {"status": "OFF"}


@pytest.mark.parametrize("lane", ["codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"])
def test_each_lane_builds_exact_argv_without_a_query(candidate, lane):
    from tools.wd_q4_session_tuple import build_session_argv
    candidate["lane"] = candidate["descriptor"]["lane"] = candidate["descriptor"]["worker"]["lane"] = lane
    result = _resolve(**candidate, enabled=True)
    descriptor = candidate["descriptor"]
    assert result["status"] == "TUPLE_CONSISTENT"
    assert result["attribution"] == "DECLARED_NOT_AUTHENTICATED" and result["freshness"] == "FRESHNESS_UNKNOWN"
    assert result["host_arguments"]["BootstrapArguments"] == build_session_argv(
        descriptor["worker"], descriptor["outer"], descriptor["expected_binding"], enabled=True)
    assert "synthetic-query-marker" not in repr(result)


@pytest.mark.skipif(os.name != "nt" or not shutil.which("powershell.exe"), reason="Windows PowerShell helper")
def test_real_v2_helper_output_composes_into_host_arguments(candidate, tmp_path):
    descriptor = candidate["descriptor"]
    raw = {key: value for key, value in descriptor.items()
           if key not in {"lane", "fleet_path", "worker"}}
    raw.update(schema="wd.q4-shared-session-config.v2", lanes=["codex-lead-1", "codex-tools-1",
               "claude-rco-1", "claude-rco-2", "fable-5"])
    fleet_path = Path(descriptor["fleet_path"])
    fleet_path.write_bytes(candidate["fleet_bytes"])
    descriptor_path = tmp_path / "descriptor.json"
    descriptor_path.write_bytes(_bytes(raw))
    helper = Path(__file__).resolve().parents[2] / "ops/windows/reboot/Get-WdQ4SharedSessionConfig.ps1"
    quote = lambda item: "'" + str(item).replace("'", "''") + "'"
    script = ("$ErrorActionPreference='Stop'; . " + quote(helper)
        + "; Get-WdQ4SharedSessionConfig -Enabled -FleetPath " + quote(fleet_path)
        + " -FleetSha256 " + quote(candidate["expected_fleet_sha256"])
        + " -DescriptorPath " + quote(descriptor_path) + " -DescriptorSha256 " + quote(_sha(descriptor_path.read_bytes()))
        + " -Lane 'codex-lead-1' | ConvertTo-Json -Depth 8 -Compress")
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    proc = subprocess.run([shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                          capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    candidate["descriptor"] = json.loads(proc.stdout)
    result = _resolve(**candidate, enabled=True)
    assert result["status"] == "TUPLE_CONSISTENT"


@pytest.mark.parametrize("fault", ["missing_host", "extra_host", "missing_descriptor", "extra_descriptor",
    "extra_identity", "missing_identity", "mixed_lane", "mixed_binding", "mixed_roster", "mixed_interpreter",
    "mixed_interpreter_pin", "mixed_cwd", "mixed_timeout", "mixed_output", "path_collision", "query_field",
    "argument_injection", "mismatched_descriptor", "nonbool"])
def test_refuses_mixed_or_unexpected_input_without_dispatch(candidate, fault):
    value = copy.deepcopy(candidate)
    if fault == "missing_host": del value["host"]["JobHelperSha256"]
    if fault == "extra_host": value["host"]["query"] = "synthetic-query-marker"
    if fault == "missing_descriptor": del value["descriptor"]["outer"]
    if fault == "extra_descriptor": value["descriptor"]["query"] = "synthetic-query-marker"
    if fault == "extra_identity": value["runtime_identity"]["query"] = "synthetic-query-marker"
    if fault == "missing_identity": del value["runtime_identity"]["wrapper_sha256"]
    if fault == "mixed_lane": value["descriptor"]["worker"]["lane"] = "fable-5"
    if fault == "mixed_binding": value["descriptor"]["worker"]["binding-sha256"] = "0" * 64
    if fault == "mixed_roster": value["descriptor"]["expected_binding"]["roster_sha256"] = "0" * 64
    if fault == "mixed_interpreter": value["host"]["Interpreter"] += "-other"
    if fault == "mixed_interpreter_pin": value["host"]["InterpreterSha256"] = "0" * 64
    if fault == "mixed_cwd": value["host"]["WorkingDirectory"] += "-other"
    if fault == "mixed_timeout": value["host"]["TimeoutSeconds"] = 31
    if fault == "mixed_output": value["host"]["MaxOutputBytes"] = 4097
    if fault == "path_collision": value["host"]["JobHelper"] = value["host"]["HostScript"]
    if fault == "query_field": value["descriptor"]["worker"]["query"] = "synthetic-query-marker"
    if fault == "argument_injection": value["host"]["Bootstrap"] += "\x00--enabled"
    if fault == "mismatched_descriptor": value["descriptor"]["snapshot_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="^descriptor_host_tuple_refused$") as caught:
        _resolve(**value, enabled=1 if fault == "nonbool" else True)
    assert "synthetic-query-marker" not in repr(caught.value)
