"""F29 read-only doctor tests (authored per operator directive; NOT executed yet).

Every refusal test has a success twin built from the same fixture, so a doctor that
refuses everything cannot pass. Inputs are synthetic files under tmp_path only.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from tools import wd_bridge_doctor as doctor

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "configs" / "bridge_components.json"
SOURCE = ROOT / "tools" / "wd_bridge_doctor.py"
NOW = datetime(2026, 9, 29, 21, 0, tzinfo=timezone.utc)
ALL_KEYS = ("git_executable", "bridge_python_executable", "windows_powershell_executable",
            "pwsh_executable", "codex_executable", "claude_executable", "grok_executable")


def _paths(tmp_path: Path, omit: tuple[str, ...] = ()) -> dict:
    mapping = {}
    for key in ALL_KEYS:
        if key in omit:
            continue
        target = tmp_path / "bin" / (key + ".exe")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"MZ")
        mapping[key] = str(target)
    if "bridge_runtime_root" not in omit:
        runtime = tmp_path / "runtime"
        runtime.mkdir(exist_ok=True)
        mapping["bridge_runtime_root"] = str(runtime)
    return {"schema": doctor.PATHS_SCHEMA, "paths": mapping}


def _fresh(provider_states: dict | None = None, age: timedelta = timedelta(minutes=1)) -> dict:
    stamp = (NOW - age).isoformat().replace("+00:00", "Z")
    good = {"auth": "valid", "quota": "available", "observed_turn": "succeeded"}
    providers = {}
    for provider in ("claude", "codex", "grok"):
        states = dict(good, **(provider_states or {}).get(provider, {}))
        providers[provider] = {d: {"state": s, "observed_at_utc": stamp, "source": "fixture"}
                               for d, s in states.items()}
    return {"schema": doctor.EVIDENCE_SCHEMA, "providers": providers}


def _run(tmp_path: Path, lane: str, paths: dict, evidence: dict | None, manifest: dict | None = None):
    manifest_path = MANIFEST
    if manifest is not None:
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    paths_path = tmp_path / "paths.json"
    paths_path.write_text(json.dumps(paths), encoding="utf-8")
    evidence_path = None
    if evidence is not None:
        evidence_path = tmp_path / "evidence.json"
        evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    return doctor.run(manifest_path, paths_path, evidence_path, lane, NOW)


def _feature(report: dict, feature_id: str) -> dict:
    return next(f for f in report["features"] if f["id"] == feature_id)


def test_shipped_manifest_validates():
    manifest = doctor.validate_manifest(doctor.load_json(MANIFEST, "manifest"))
    assert set(manifest["features"]) >= {"bridge_core", "claude_lane", "codex_lane"}


def test_everything_present_and_fresh_is_ready(tmp_path):
    code, report = _run(tmp_path, "claude-rco-2", _paths(tmp_path), _fresh())
    assert (code, report["verdict"]) == (0, "ready")
    assert report["missing_required"] == []
    assert report["installs_performed"] is False and report["authority_effect"] == "none"


@pytest.mark.parametrize("key,component", [
    ("git_executable", "git"), ("bridge_python_executable", "bridge_python"),
    ("windows_powershell_executable", "windows_powershell"), ("bridge_runtime_root", "bridge_runtime_root")])
def test_missing_required_component_refuses_with_instructions(tmp_path, key, component):
    code, report = _run(tmp_path, "claude-rco-2", _paths(tmp_path, omit=(key,)), _fresh())
    assert (code, report["verdict"]) == (2, "refuse")
    entry = next(g for g in report["missing_required"] if g.get("component") == component)
    assert entry["state"] == "unknown" and entry["instructions"]
    assert _feature(report, "bridge_core")["status"] == "unsatisfied"


def test_configured_but_absent_path_is_missing_not_unknown(tmp_path):
    paths = _paths(tmp_path)
    paths["paths"]["git_executable"] = str(tmp_path / "nowhere" / "git.exe")
    _, report = _run(tmp_path, "claude-rco-2", paths, _fresh())
    git = next(c for c in report["components"] if c["id"] == "git")
    assert (git["state"], git["reason"]) == ("missing", "path_absent")


@pytest.mark.parametrize("value", ["git.exe", "relative\\git.exe", "C:git.exe", ""])
def test_non_absolute_path_is_unknown_and_refuses(tmp_path, value):
    paths = _paths(tmp_path)
    paths["paths"]["git_executable"] = value
    code, report = _run(tmp_path, "claude-rco-2", paths, _fresh())
    assert code == 2
    assert next(c for c in report["components"] if c["id"] == "git")["state"] == "unknown"


def test_directory_where_executable_expected_is_missing(tmp_path):
    paths = _paths(tmp_path)
    paths["paths"]["git_executable"] = str(tmp_path)
    _, report = _run(tmp_path, "claude-rco-2", paths, _fresh())
    assert next(c for c in report["components"] if c["id"] == "git")["reason"] == "not_a_file"


def test_missing_optional_component_only_disables_its_feature(tmp_path):
    code, report = _run(tmp_path, "claude-rco-2", _paths(tmp_path, omit=("pwsh_executable",)), _fresh())
    assert (code, report["verdict"]) == (1, "degraded")
    assert _feature(report, "ps7_parity")["status"] == "disabled"
    assert _feature(report, "bridge_core")["status"] == "enabled"
    assert report["missing_required"] == []


def test_minimal_fork_passes_without_unused_providers(tmp_path):
    # A Claude lane needs neither Codex nor Grok: removing both is not a refusal.
    paths = _paths(tmp_path, omit=("codex_executable", "grok_executable"))
    code, report = _run(tmp_path, "claude-rco-2", paths, _fresh())
    assert (code, report["verdict"]) == (0, "ready")
    assert _feature(report, "codex_lane")["status"] == "not_applicable"


def test_codex_lane_refuses_without_codex_cli(tmp_path):
    code, report = _run(tmp_path, "codex-tools-1", _paths(tmp_path, omit=("codex_executable",)), _fresh())
    assert code == 2 and _feature(report, "codex_lane")["status"] == "unsatisfied"


@pytest.mark.parametrize("dimension,bad", [("auth", "invalid"), ("quota", "exhausted"),
                                           ("observed_turn", "failed")])
def test_each_provider_state_is_reported_separately(tmp_path, dimension, bad):
    code, report = _run(tmp_path, "claude-rco-2", _paths(tmp_path), _fresh({"claude": {dimension: bad}}))
    assert code == 2
    claude = next(p for p in report["providers"] if p["id"] == "claude")
    assert claude["ready"] is False and claude["states"][dimension]["state"] == bad
    others = [d for d in ("auth", "quota", "observed_turn") if d != dimension]
    assert all(claude["states"][d]["state"] != bad for d in others)
    assert claude["states"]["cli"]["state"] == "installed"


def test_live_process_or_callback_is_not_readiness(tmp_path):
    evidence = _fresh()
    evidence["providers"]["claude"]["observed_turn"] = {"state": "process_alive",
                                                        "observed_at_utc": NOW.isoformat()}
    code, report = _run(tmp_path, "claude-rco-2", _paths(tmp_path), evidence)
    assert code == 2
    claude = next(p for p in report["providers"] if p["id"] == "claude")
    assert claude["states"]["observed_turn"] == {"state": "unknown", "reason": "unrecognised_state"}


def test_stale_future_and_missing_evidence_are_unknown(tmp_path):
    code, report = _run(tmp_path, "claude-rco-2", _paths(tmp_path), _fresh(age=timedelta(hours=5)))
    assert code == 2
    assert next(p for p in report["providers"] if p["id"] == "claude")["states"]["quota"]["reason"] == "stale"
    code, report = _run(tmp_path, "claude-rco-2", _paths(tmp_path), _fresh(age=timedelta(hours=-1)))
    assert code == 2
    code, report = _run(tmp_path, "claude-rco-2", _paths(tmp_path), None)
    assert code == 2
    assert next(p for p in report["providers"] if p["id"] == "claude")["states"]["auth"]["reason"] == "no_evidence"


def test_naive_timestamp_is_unknown(tmp_path):
    evidence = _fresh()
    evidence["providers"]["claude"]["auth"]["observed_at_utc"] = "2026-09-29T20:59:00"
    code, _ = _run(tmp_path, "claude-rco-2", _paths(tmp_path), evidence)
    assert code == 2


def test_evidence_free_text_and_credentials_are_never_echoed(tmp_path):
    evidence = _fresh()
    secret = "sk-SECRET-TOKEN-SHOULD-NOT-LEAK"
    evidence["providers"]["claude"]["auth"].update(detail=secret, source="bad source " + secret)
    _, report = _run(tmp_path, "claude-rco-2", _paths(tmp_path), evidence)
    assert secret not in json.dumps(report)


def test_report_is_deterministic(tmp_path):
    first = _run(tmp_path, "codex-lead-1", _paths(tmp_path, omit=("grok_executable",)), _fresh())
    second = _run(tmp_path, "codex-lead-1", _paths(tmp_path, omit=("grok_executable",)), _fresh())
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first[1]["verdict"] == "degraded"  # grok_advisory is optional for the Lead


@pytest.mark.parametrize("mutate", [
    lambda m: m.update(schema="wd.bridge-components.v0"),
    lambda m: m.update(extra=1),
    lambda m: m["components"].append(dict(m["components"][0])),
    lambda m: m["components"][0].update(kind="installer"),
    lambda m: m["components"][0]["install"].update(command="winget install git"),
    lambda m: m["features"][0]["components"].append("no_such_component"),
    lambda m: m["features"][0]["required_for_lanes"].append("unknown-lane"),
    lambda m: m["features"][1]["required_for_lanes"].append("*"),
    lambda m: m["provider_state_max_age_seconds"].update(quota=True),
])
def test_invalid_manifest_is_invalid_input_not_evaluated(tmp_path, mutate):
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    mutate(manifest)
    code, report = _run(tmp_path, "claude-rco-2", _paths(tmp_path), _fresh(), manifest=manifest)
    assert (code, report["verdict"]) == (3, "invalid_input")
    assert "features" not in report


def test_duplicate_keys_and_non_finite_constants_are_rejected(tmp_path):
    bad = tmp_path / "paths.json"
    bad.write_text('{"schema":"wd.bridge-local-paths.v1","paths":{},"paths":{}}', encoding="utf-8")
    with pytest.raises(doctor.DoctorInputError):
        doctor.load_json(bad, "paths config")
    bad.write_text('{"schema":"wd.bridge-local-paths.v1","paths":{"git_executable":NaN}}', encoding="utf-8")
    with pytest.raises(doctor.DoctorInputError):
        doctor.load_json(bad, "paths config")
    bad.write_text("[" * 5000 + "]" * 5000, encoding="utf-8")
    with pytest.raises(doctor.DoctorInputError):
        doctor.load_json(bad, "paths config")


def test_unknown_lane_is_invalid_input(tmp_path):
    code, report = _run(tmp_path, "not-a-lane", _paths(tmp_path), _fresh())
    assert (code, report["verdict"]) == (3, "invalid_input")


def test_doctor_source_never_executes_or_installs_and_has_no_machine_paths():
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {alias.name.split(".")[0] for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")])}
    assert not imported & {"subprocess", "socket", "urllib", "http", "requests", "shutil", "ctypes"}
    calls = {node.func.attr for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    assert not calls & {"system", "popen", "startfile", "spawnv", "execv", "remove", "unlink", "rmdir", "write_text"}
    for fragment in ("C:\\\\Python", "C:\\\\Users", "project2", "janik"):
        assert fragment not in source
