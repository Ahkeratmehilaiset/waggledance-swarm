"""Synthetic session descriptors only: no launcher, query, or live data."""
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "ops/windows/reboot/Get-WdQ4SharedSessionConfig.ps1"
SHELL = shutil.which("powershell.exe") if os.name == "nt" else None
pytestmark = pytest.mark.skipif(not SHELL, reason="Windows PowerShell required")
LANES = ["codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def quote(s):
    return "'" + str(s).replace("'", "''") + "'"


def run(tmp_path, mutate=None, lane="codex-lead-1", enabled=True, fleet_pin=None, descriptor_pin=None, raw=None):
    fleet = tmp_path / "fleet.json"
    fleet_raw = json.dumps({"lanes": [{"agent": x} for x in LANES if x != "codex-tools-1"],
                            "tools_supervisor": {"agent": "codex-tools-1"}}).encode()
    fleet.write_bytes(fleet_raw)
    cfg = {"schema": "wd.q4-shared-session-config.v1", "fleet_sha256": sha(fleet_raw),
           "lanes": LANES, "bundle_root": str(tmp_path / "bundle"),
           "public_plan": str(tmp_path / "public.json"), "interpreter": str(tmp_path / "python.exe"),
           "interpreter_sha256": "a" * 64, "interpreter_max_bytes": 10000, "cwd": str(tmp_path / "code"),
           "timeout_seconds": 30, "max_output_bytes": 32000}
    for name in ("binding", "fixed_plan", "snapshot", "runtime_manifest"):
        cfg[name + "_path"] = str(tmp_path / (name + ".json"))
        cfg[name + "_sha256"] = "b" * 64
    if mutate: mutate(cfg)
    data = raw(cfg) if raw else json.dumps(cfg).encode()
    desc = tmp_path / "descriptor.json"
    desc.write_bytes(data)
    script = ("$ErrorActionPreference='Stop'\ntry {\n"
              f". {quote(HELPER)}\n"
              f"$v=Get-WdQ4SharedSessionConfig -FleetPath {quote(fleet)} -FleetSha256 {quote(fleet_pin or sha(fleet_raw))} "
              f"-DescriptorPath {quote(desc)} -DescriptorSha256 {quote(descriptor_pin or sha(data))} -Lane {quote(lane)}"
              + (" -Enabled" if enabled else "") + "\n"
              "[ordered]@{ok=$true;value=$v}|ConvertTo-Json -Depth 8 -Compress\n"
              "} catch { [ordered]@{ok=$false;reason=$_.Exception.Message}|ConvertTo-Json -Compress }\n")
    code = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    process = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", code],
                             capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, (process.stdout, process.stderr)
    return json.loads(process.stdout)


def test_disabled_does_not_read_or_dispatch(tmp_path):
    assert run(tmp_path, enabled=False, fleet_pin="bad", descriptor_pin="bad") == {"ok": True, "value": None}


def test_disabled_accepts_nonexistent_files_without_reading_them(tmp_path):
    script = ("$ErrorActionPreference='Stop'\n" + f". {quote(HELPER)}\n"
              + f"$v=Get-WdQ4SharedSessionConfig -FleetPath {quote(tmp_path / 'missing-fleet')} "
              + f"-DescriptorPath {quote(tmp_path / 'missing-descriptor')} -Lane 'bad'\n"
              + "[ordered]@{value=$v}|ConvertTo-Json -Compress\n")
    code = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    process = subprocess.run([SHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", code],
                             capture_output=True, text=True, timeout=30)
    assert process.returncode == 0 and json.loads(process.stdout) == {"value": None}


@pytest.mark.parametrize("lane", LANES)
def test_pinned_complete_descriptor_yields_exact_lane_and_cli_inputs(tmp_path, lane):
    result = run(tmp_path, lane=lane)
    assert result["ok"] and result["value"]["lane"] == lane
    assert result["value"]["fleet_sha256"] and result["value"]["fixed_plan_sha256"] == "b" * 64
    assert "query" not in result["value"]


@pytest.mark.parametrize("lane", LANES)
def test_one_lane_pilot_allows_only_the_selected_lane(tmp_path, lane):
    change = lambda d: d.update(lanes=[lane])
    other = "fable-5" if lane != "fable-5" else "codex-lead-1"
    assert run(tmp_path, mutate=change, lane=lane)["ok"]
    assert not run(tmp_path, mutate=change, lane=other)["ok"]


@pytest.mark.parametrize("change", [lambda d: d.pop("binding_sha256"),
                                    lambda d: d.update(query="no"),
                                    lambda d: d.update(lanes=["fable-5"]),
                                    lambda d: d.update(lanes=[]),
                                    lambda d: d.update(lanes=["codex-lead-1", "codex-lead-1"]),
                                    lambda d: d.update(lanes=["codex-lead-1", "unknown"]),
                                    lambda d: d.update(timeout_seconds=1801),
                                    lambda d: d.update(interpreter_sha256="x" * 64),
                                    lambda d: d.update(snapshot_path="relative"),
                                    lambda d: d.update(fleet_sha256="0" * 64)])
def test_invalid_descriptor_fails_closed(tmp_path, change):
    assert not run(tmp_path, mutate=change)["ok"]


@pytest.mark.parametrize("kwargs", [{"lane": "other"}, {"fleet_pin": "0" * 64}, {"descriptor_pin": "0" * 64}])
def test_wrong_lane_or_external_hash_refuses(tmp_path, kwargs):
    assert not run(tmp_path, **kwargs)["ok"]


def test_case_colliding_json_key_refuses(tmp_path):
    def duplicate(d):
        text = json.dumps(d)
        return (text[:-1] + ',"SCHEMA":"wd.q4-shared-session-config.v1"}').encode()
    assert not run(tmp_path, raw=duplicate)["ok"]


def test_escaped_duplicate_json_key_refuses(tmp_path):
    def duplicate(d):
        return (json.dumps(d)[:-1] + r',"\u0073chema":"wd.q4-shared-session-config.v1"}').encode()
    assert not run(tmp_path, raw=duplicate)["ok"]


def test_helper_is_required_in_deployment_and_fleet():
    reboot = ROOT / "ops/windows/reboot"
    name = "Get-WdQ4SharedSessionConfig.ps1"
    assert "'" + name + "'" in (reboot / "Deploy-WdRebootBundle.ps1").read_text(encoding="utf-8")
    fleet = json.loads((reboot / "wd-fleet.json").read_text(encoding="utf-8"))
    assert name in fleet["deployment"]["required_bundle_files"]

# v2 RED candidate: synthetic declared pins only, never a runtime authorization.
_V2_OUTER_KEYS = (
    "outer-path", "outer-sha256", "runtime-root", "runtime-generation", "runtime-revision",
    "verifier-sha256", "public-outer-sha256", "public-child-sha256",
    "public-query-sha256", "worker-sha256",
)
_V2_BINDING_KEYS = (
    "pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256",
    "roster_sha256", "public_policy_sha256", "descriptor_sha256", "projection_sha256",
)


def run_v2(tmp_path, mutate=None, **kwargs):
    captured = {}

    def descriptor(d):
        d["schema"] = "wd.q4-shared-session-config.v2"
        root = tmp_path / "runtime"
        d["outer"] = {
            "outer-path": str(root / "assets/code/tools/wd_q4_session_outer_synthetic.py"),
            "outer-sha256": "c" * 64, "runtime-root": str(root),
            "runtime-generation": "d" * 40, "runtime-revision": "e" * 40,
            "verifier-sha256": "1" * 64, "public-outer-sha256": "2" * 64,
            "public-child-sha256": "3" * 64, "public-query-sha256": "4" * 64,
            "worker-sha256": "5" * 64,
        }
        d["expected_binding"] = {key: format(i + 1, "064x")
                                 for i, key in enumerate(_V2_BINDING_KEYS)}
        if mutate:
            mutate(d)
        captured.update(d)

    return run(tmp_path, mutate=descriptor, **kwargs), captured


def expected_v2_worker(tmp_path, d, lane):
    out = {"lane": lane, "fleet": str(tmp_path / "fleet.json"),
           "fleet-sha256": d["fleet_sha256"]}
    for flag, key in (
        ("bundle-root", "bundle_root"), ("public-plan", "public_plan"),
        ("interpreter", "interpreter"), ("interpreter-sha256", "interpreter_sha256"),
        ("interpreter-max-bytes", "interpreter_max_bytes"), ("cwd", "cwd"),
        ("timeout-seconds", "timeout_seconds"), ("max-output-bytes", "max_output_bytes"),
        ("binding", "binding_path"), ("binding-sha256", "binding_sha256"),
        ("fixed-plan", "fixed_plan_path"), ("fixed-plan-sha256", "fixed_plan_sha256"),
        ("snapshot", "snapshot_path"), ("snapshot-sha256", "snapshot_sha256"),
        ("runtime-manifest", "runtime_manifest_path"),
        ("runtime-manifest-sha256", "runtime_manifest_sha256"),
    ):
        out[flag] = str(d[key])
    assert len(out) == 19
    return out


@pytest.mark.parametrize("lane", LANES)
def test_v2_complete_tuple_exposes_exact_worker_outer_and_binding(tmp_path, lane):
    result, doc = run_v2(tmp_path, lane=lane)
    assert result["ok"], result
    value = result["value"]
    assert value["worker"] == expected_v2_worker(tmp_path, doc, lane)
    assert value["outer"] == doc["outer"] and len(value["outer"]) == 10
    assert value["expected_binding"] == doc["expected_binding"]
    assert len(value["expected_binding"]) == 8
    assert "query" not in value and "authenticated" not in value
    assert all(isinstance(v, str) for v in value["worker"].values())


@pytest.mark.parametrize("section, key",
                         [("outer", key) for key in _V2_OUTER_KEYS]
                         + [("expected_binding", key) for key in _V2_BINDING_KEYS])
def test_v2_missing_tuple_field_refuses(tmp_path, section, key):
    result, _ = run_v2(tmp_path, mutate=lambda d: d[section].pop(key))
    assert result == {"ok": False, "reason": "q4_shared_session_config_refused"}


@pytest.mark.parametrize("change", [
    lambda d: d["outer"].update(extra="x"),
    lambda d: d["expected_binding"].update(extra="a" * 64),
    lambda d: d.update(outer=[]),
    lambda d: d.update(expected_binding=None),
    lambda d: d["outer"].update({"outer-sha256": "A" * 64}),
    lambda d: d["outer"].update({"runtime-generation": "g" * 40}),
    lambda d: d["outer"].update({"runtime-revision": "e" * 39}),
    lambda d: d["outer"].update({"outer-path": d["interpreter"]}),
    lambda d: d["outer"].update({"runtime-root": "relative"}),
    lambda d: d["expected_binding"].update(policy_sha256=1),
    lambda d: d.update(timeout_seconds=True),
    lambda d: d.update(schema="wd.q4-shared-session-config.v999"),
    lambda d: d.update(fleet_sha256="0" * 64),
    lambda d: d.update(runtime_manifest_path=str(Path(d["outer"]["runtime-root"]) / "manifest.json")),
    lambda d: d.update(binding_path=d["snapshot_path"], binding_sha256="f" * 64),
])
def test_v2_bad_shape_pin_path_or_crosswire_refuses(tmp_path, change):
    result, _ = run_v2(tmp_path, mutate=change)
    assert result == {"ok": False, "reason": "q4_shared_session_config_refused"}


@pytest.mark.parametrize("spelling", ['"outer-sha256"', '"OUTER-SHA256"',
                                    r'"outer-\u0073ha256"'])
def test_v2_nested_duplicate_and_escaped_key_refuse(tmp_path, spelling):
    def duplicate(doc):
        text = json.dumps(doc)
        needle = '"outer-sha256": "' + "c" * 64 + '"'
        assert text.count(needle) == 1
        return text.replace(needle, needle + ", " + spelling + ': "' + "f" * 64 + '"').encode()
    result, _ = run_v2(tmp_path, raw=duplicate)
    assert result == {"ok": False, "reason": "q4_shared_session_config_refused"}


def test_v2_disabled_ignores_invalid_pins_and_new_fields(tmp_path):
    result, _ = run_v2(tmp_path, mutate=lambda d: d.update(outer=None),
                       enabled=False, fleet_pin="bad", descriptor_pin="bad")
    assert result == {"ok": True, "value": None}


@pytest.mark.parametrize("kwargs", [{"fleet_pin": "0" * 64}, {"descriptor_pin": "0" * 64}])
def test_v2_external_fleet_or_descriptor_pin_mismatch_refuses(tmp_path, kwargs):
    result, _ = run_v2(tmp_path, **kwargs)
    assert result == {"ok": False, "reason": "q4_shared_session_config_refused"}


@pytest.mark.parametrize("lane", LANES)
def test_v2_content_binding_preserves_exact_seven_pins_for_each_lane(tmp_path, lane):
    from tools.wd_q4_lane_query import CONTENT_BINDING_KEYS
    content = {key: "a" * 64 for key in CONTENT_BINDING_KEYS}
    result, _ = run_v2(tmp_path, lane=lane, mutate=lambda d: d.update(expected_binding=content))
    assert result["ok"] and result["value"]["expected_binding"] == content


@pytest.mark.parametrize("key", ["encoder_sha256", "policy_sha256", "roster_sha256",
                                  "content_decision_sha256", "descriptor_sha256",
                                  "memory_sha256", "projection_sha256"])
def test_v2_content_binding_refuses_each_missing_or_invalid_pin(tmp_path, key):
    from tools.wd_q4_lane_query import CONTENT_BINDING_KEYS
    def change(d):
        d["expected_binding"] = {k: "a" * 64 for k in CONTENT_BINDING_KEYS}
        d["expected_binding"][key] = "z" * 64
    result, _ = run_v2(tmp_path, mutate=change)
    assert not result["ok"]


@pytest.mark.parametrize("key", ["encoder_sha256", "policy_sha256", "roster_sha256",
                                  "content_decision_sha256", "descriptor_sha256",
                                  "memory_sha256", "projection_sha256"])
def test_v2_content_binding_refuses_each_missing_pin(tmp_path, key):
    from tools.wd_q4_lane_query import CONTENT_BINDING_KEYS
    def change(d):
        d["expected_binding"] = {k: "a" * 64 for k in CONTENT_BINDING_KEYS}
        del d["expected_binding"][key]
    result, _ = run_v2(tmp_path, mutate=change)
    assert not result["ok"]


def test_v2_content_binding_rejects_mixed_status_or_card_pins(tmp_path):
    from tools.wd_q4_lane_query import CONTENT_BINDING_KEYS
    def change(d):
        d["expected_binding"] = {k: "a" * 64 for k in CONTENT_BINDING_KEYS}
        d["expected_binding"]["card_decision_sha256"] = "b" * 64
    result, _ = run_v2(tmp_path, mutate=change)
    assert not result["ok"]
