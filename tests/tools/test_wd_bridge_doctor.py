"""F29 read-only doctor tests (authored per operator directive; NOT executed yet).

Every refusal test has a success twin built from the same fixture, so a doctor that
refuses everything cannot pass. Inputs are synthetic files under tmp_path only.
"""

from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import json
import os
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


@pytest.mark.parametrize("value", ["git.exe", "relative\\git.exe", "C:git.exe", "",
                                   # UNC, device namespaces and POSIX '//': refused on every OS,
                                   # before any stat (they could reach a share, a pipe or a device).
                                   "\\\\server\\share\\git.exe", "//server/share/git.exe",
                                   "\\\\.\\pipe\\git", "\\\\?\\C:\\git.exe", "\\git.exe"])
def test_non_absolute_path_is_unknown_and_refuses(tmp_path, value, monkeypatch):
    paths = _paths(tmp_path)
    paths["paths"]["git_executable"] = value
    real_stat = doctor.os.stat

    def guarded_stat(path, *args, **kwargs):
        assert path != value, "a refused path must never be stat'ed"
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(doctor.os, "stat", guarded_stat)
    code, report = _run(tmp_path, "claude-rco-2", paths, _fresh())
    assert code == 2
    git = next(c for c in report["components"] if c["id"] == "git")
    assert git["state"] == "unknown" and "path" not in git


def test_component_that_exists_is_present_unverified_never_installed(tmp_path):
    # S3: existence is all a stat proves; the report never claims installed or compatible.
    code, report = _run(tmp_path, "claude-rco-2", _paths(tmp_path), _fresh())
    assert code == 0
    git = next(c for c in report["components"] if c["id"] == "git")
    assert (git["state"], git["reason"]) == ("present_unverified", "path_present_version_unverified")
    claude = next(p for p in report["providers"] if p["id"] == "claude")
    assert claude["states"]["cli"]["state"] == "present_unverified"
    states = {c["state"] for c in report["components"]}
    states |= {p["states"]["cli"]["state"] for p in report["providers"]}
    assert states == {"present_unverified"}  # never "installed", "ok" or "compatible"


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
    assert claude["states"]["cli"]["state"] == "present_unverified"


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


@pytest.mark.parametrize("token", [
    # Token shapes WITHOUT spaces: the old [A-Za-z0-9._:/-] pattern echoed all of these.
    "sk-ant-api03-AbCdEf0123456789-xyz", "ghp_0123456789abcdefABCDEF", "xoxb-123-456-abcdef",
    "fixture", "claude-rco-2-extra", "Operator"])
def test_only_registered_evidence_sources_are_echoed(tmp_path, token):
    evidence = _fresh()
    evidence["providers"]["claude"]["auth"]["source"] = token
    evidence["providers"]["claude"]["quota"]["source"] = "claude-rco-2"  # success twin: a declared lane
    evidence["providers"]["claude"]["observed_turn"]["source"] = "operator"
    code, report = _run(tmp_path, "claude-rco-2", _paths(tmp_path), evidence)
    assert code == 0
    claude = next(p for p in report["providers"] if p["id"] == "claude")["states"]
    assert "source" not in claude["auth"] and token not in json.dumps(report)
    assert claude["quota"]["source"] == "claude-rco-2"
    assert claude["observed_turn"]["source"] == "operator"


def test_duplicate_and_unknown_key_names_are_never_echoed(tmp_path):
    secret = "sk-ant-api03-KEYNAME0123456789"
    bad = tmp_path / "evidence.json"
    bad.write_text('{"schema":"x","' + secret + '":1,"' + secret + '":2}', encoding="utf-8")
    with pytest.raises(doctor.DoctorInputError) as duplicate:
        doctor.load_json(bad, "evidence")
    assert secret not in str(duplicate.value)
    with pytest.raises(doctor.DoctorInputError) as unknown:
        doctor.validate_evidence({"schema": doctor.EVIDENCE_SCHEMA, "providers": {}, secret: 1})
    assert secret not in str(unknown.value) and "1 unknown key" in str(unknown.value)
    # Success twin: the same shape without the extra key validates.
    assert doctor.validate_evidence({"schema": doctor.EVIDENCE_SCHEMA, "providers": {}}) == {}


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
    # features[2] is codex_lane (explicit lanes): features[0] is ["*"], where the
    # "'*' must stand alone" check fired first and hid the unknown-lane check (vacuous).
    lambda m: m["features"][2]["required_for_lanes"].append("unknown-lane"),
    lambda m: m["features"][1]["required_for_lanes"].append("*"),
    lambda m: m["provider_state_max_age_seconds"].update(quota=True),
    lambda m: m["providers"][0].update(cli_component=[]),
    lambda m: m["providers"][0].update(cli_component="bridge_runtime_root"),
    lambda m: m["features"][0].update(required_for_lanes=["claude-rco-2"], optional_for_lanes=["*"]),
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
    # B1: the explicit bound fires, on every platform, before the scanner recurses.
    with pytest.raises(doctor.DoctorInputError, match="nests deeper"):
        doctor.load_json(bad, "paths config")


def test_nesting_bound_is_exact_and_ignores_brackets_inside_strings(tmp_path, monkeypatch):
    path = tmp_path / "nested.json"
    depth = doctor.MAX_JSON_DEPTH
    path.write_text("[" * depth + "]" * depth, encoding="utf-8")
    assert doctor.load_json(path, "fixture") is not None  # success twin: exactly at the bound
    path.write_text("[" * (depth + 1) + "]" * (depth + 1), encoding="utf-8")
    with pytest.raises(doctor.DoctorInputError, match="nests deeper"):
        doctor.load_json(path, "fixture")
    # Brackets and escaped quotes inside strings are data, not nesting.
    path.write_text(json.dumps({"k": "[{" * 500 + '\\"[[' + '"' * 3}), encoding="utf-8")
    assert doctor.load_json(path, "fixture")["k"].startswith("[{")
    # The bound is the module's own constant, not the interpreter's recursion limit.
    monkeypatch.setattr(doctor, "MAX_JSON_DEPTH", 3)
    path.write_text("[[[[]]]]", encoding="utf-8")
    with pytest.raises(doctor.DoctorInputError, match="nests deeper than 3"):
        doctor.load_json(path, "fixture")


@pytest.mark.parametrize("raw", ["relative.json", "\\\\server\\share\\m.json", "//server/share/m.json",
                                 "\\\\.\\pipe\\doctor"])
def test_input_file_must_be_a_local_absolute_path(tmp_path, raw):
    with pytest.raises(doctor.DoctorInputError, match="local absolute"):
        doctor.load_json(Path(raw), "manifest")


def test_directory_or_fifo_input_is_refused_without_blocking(tmp_path):
    with pytest.raises(doctor.DoctorInputError):
        doctor.load_json(tmp_path, "manifest")  # a directory is not a regular file
    if hasattr(doctor.os, "mkfifo"):
        fifo = tmp_path / "manifest.fifo"
        doctor.os.mkfifo(fifo)
        # O_NONBLOCK: the open returns at once with no writer; fstat refuses the FIFO.
        with pytest.raises(doctor.DoctorInputError, match="not a regular file"):
            doctor.load_json(fifo, "manifest")


def test_a_non_regular_descriptor_is_refused_and_closed_before_any_fdopen(tmp_path, monkeypatch):
    # The POSIX directory case on any host (Linux CI, 2026-10-01): the open succeeds, and fdopen
    # would raise IsADirectoryError without closing the descriptor. fstat refuses it first, and the
    # descriptor is closed exactly once.
    path = tmp_path / "manifest.json"
    path.write_text("{}", encoding="utf-8")
    real_fstat, real_close = doctor.os.fstat, doctor.os.close
    closed = []

    def fstat(fd):
        return doctor.os.stat_result((doctor.stat_module.S_IFDIR | 0o755,) + tuple(real_fstat(fd))[1:])

    def fdopen(*args, **kwargs):
        raise IsADirectoryError(21, "Is a directory")

    def close(fd):
        closed.append(fd)
        real_close(fd)

    monkeypatch.setattr(doctor.os, "fstat", fstat)
    monkeypatch.setattr(doctor.os, "fdopen", fdopen)
    monkeypatch.setattr(doctor.os, "close", close)
    with pytest.raises(doctor.DoctorInputError, match="not a regular file"):
        doctor.load_json(path, "manifest")
    assert len(closed) == 1


@pytest.mark.parametrize("regular", [False, True], ids=["error_in_flight", "no_error_in_flight"])
def test_a_failed_close_never_replaces_the_error_in_flight(tmp_path, monkeypatch, regular):
    # RCO2 D1 (2026-10-01): the one close may itself fail. With a refusal in flight that refusal
    # still surfaces; with none in flight it is unreadable input, never a raw OSError. One close.
    path = tmp_path / "manifest.json"
    path.write_text("{}", encoding="utf-8")
    real_fstat, real_close = doctor.os.fstat, doctor.os.close
    closed = []

    def fstat(fd):
        return doctor.os.stat_result((doctor.stat_module.S_IFDIR | 0o755,) + tuple(real_fstat(fd))[1:])

    def close(fd):
        closed.append(fd)
        real_close(fd)
        raise OSError(5, "Input/output error")

    if not regular:
        monkeypatch.setattr(doctor.os, "fstat", fstat)
    monkeypatch.setattr(doctor.os, "close", close)
    with pytest.raises(doctor.DoctorInputError, match="unreadable: OSError" if regular else "not a regular file"):
        doctor.load_json(path, "manifest")
    assert len(closed) == 1


def test_unknown_lane_is_invalid_input(tmp_path):
    code, report = _run(tmp_path, "not-a-lane", _paths(tmp_path), _fresh())
    assert (code, report["verdict"]) == (3, "invalid_input")


@pytest.mark.parametrize("tail", [
    ["--unknown", "secret-do-not-echo"], ["--now"],
    ["--now", "not-a-date"], ["--now", "2026-09-29T21:00:00"],
    ["--now", "0001-01-01T00:00:00+01:00"],
    ["--now", "9999-12-31T23:59:59-01:00"],
    ["--paths-conf", "unexpected-abbreviation"],
])
def test_cli_invalid_arguments_return_json_without_evaluation(tmp_path, capsys, monkeypatch, tail):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid CLI input must not read files or evaluate components")

    monkeypatch.setattr(doctor, "run", forbidden)
    argv = ["--manifest", str(MANIFEST), "--paths-config", str(tmp_path / "paths.json"),
            "--lane", "claude-rco-2"] + tail
    assert doctor.main(argv) == 3
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["schema"] == doctor.REPORT_SCHEMA and report["verdict"] == "invalid_input"
    assert report["installs_performed"] is False and report["authority_effect"] == "none"
    assert "features" not in report and captured.err == ""
    assert "secret-do-not-echo" not in captured.out


def test_cli_missing_required_arguments_return_invalid_json(capsys):
    assert doctor.main([]) == 3
    captured = capsys.readouterr()
    assert json.loads(captured.out)["verdict"] == "invalid_input"
    assert captured.err == ""


def test_cli_help_preserves_normal_success(capsys):
    with pytest.raises(SystemExit) as stopped:
        doctor.main(["--help"])
    assert stopped.value.code == 0
    assert "--paths-config" in capsys.readouterr().out


def test_cli_help_works_without_a_docstring_as_under_python_OO(capsys, monkeypatch):
    # N3: python -OO sets __doc__ to None; the parser must not read it.
    monkeypatch.setattr(doctor, "__doc__", None)
    with pytest.raises(SystemExit) as stopped:
        doctor.main(["--help"])
    assert stopped.value.code == 0
    assert "F29 read-only bridge doctor" in capsys.readouterr().out  # a short fragment: help text wraps


@pytest.mark.parametrize("error", [TypeError, KeyError, OverflowError, AttributeError, RuntimeError])
def test_cli_unexpected_error_is_invalid_input_json_not_degraded(tmp_path, capsys, monkeypatch, error):
    # S1 catch-all: exit 1 means "degraded", so no unexpected error may surface as 1.
    def broken(*args, **kwargs):
        raise error("sk-ant-api03-DO-NOT-ECHO")

    monkeypatch.setattr(doctor, "run", broken)
    argv = ["--manifest", str(MANIFEST), "--paths-config", str(tmp_path / "paths.json"),
            "--lane", "claude-rco-2"]
    assert doctor.main(argv) == 3
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["verdict"] == "invalid_input" and error.__name__ in report["error"]
    assert "DO-NOT-ECHO" not in captured.out and captured.err == ""


@pytest.mark.parametrize("omit,verdict,code", [
    ((), "ready", 0), (("pwsh_executable",), "degraded", 1),
    (("git_executable",), "refuse", 2),
])
def test_cli_valid_inputs_preserve_verdict_codes(tmp_path, capsys, omit, verdict, code):
    paths_path, evidence_path = tmp_path / "paths.json", tmp_path / "evidence.json"
    paths_path.write_text(json.dumps(_paths(tmp_path, omit=omit)), encoding="utf-8")
    evidence_path.write_text(json.dumps(_fresh()), encoding="utf-8")
    assert doctor.main(["--manifest", str(MANIFEST), "--paths-config", str(paths_path),
                        "--evidence", str(evidence_path), "--lane", "claude-rco-2",
                        "--now", NOW.isoformat()]) == code
    assert json.loads(capsys.readouterr().out)["verdict"] == verdict


def test_json_read_is_bounded_and_size_limit_is_inclusive(tmp_path, monkeypatch):
    path = tmp_path / "input.json"
    monkeypatch.setattr(doctor, "MAX_INPUT_BYTES", 16)
    path.write_bytes(b"{}" + b" " * 14)
    assert doctor.load_json(path, "fixture") == {}
    path.write_bytes(b"{}" + b" " * 15)
    with pytest.raises(doctor.DoctorInputError, match="exceeds"):
        doctor.load_json(path, "fixture")


def test_invalid_input_path_is_reported_not_raised(tmp_path):
    code, report = doctor.run(Path("invalid\0path"), tmp_path / "paths.json", None,
                              "claude-rco-2", NOW)
    assert (code, report["verdict"]) == (3, "invalid_input")


def test_doctor_source_never_executes_or_installs_and_has_no_machine_paths():
    source = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {alias.name.split(".")[0] for node in ast.walk(tree)
                if isinstance(node, (ast.Import, ast.ImportFrom))
                for alias in (node.names if isinstance(node, ast.Import) else [ast.alias(node.module or "")])}
    assert not imported & {"subprocess", "socket", "urllib", "http", "requests", "shutil", "ctypes",
                           "multiprocessing", "asyncio", "pty", "webbrowser", "ftplib", "smtplib"}
    calls = {node.func.attr for node in ast.walk(tree)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    # N2: every os.exec*/spawn*/posix_spawn* variant and every write/move/delete primitive.
    exec_family = {name for name in dir(os) if name.startswith(("exec", "spawn", "posix_spawn"))}
    exec_family |= {"execv", "execve", "execl", "execlp", "execvp", "execvpe", "spawnv", "spawnve",
                    "posix_spawn", "posix_spawnp"}
    writers = {"system", "popen", "startfile", "fork", "kill", "remove", "unlink", "rmdir", "removedirs",
               "rename", "renames", "replace", "write_text", "write_bytes", "mkdir", "makedirs", "mkfifo",
               "symlink", "link", "hardlink_to", "symlink_to", "touch", "truncate", "chmod", "chown",
               "utime", "ftruncate"}
    assert not calls & (exec_family | writers), calls & (exec_family | writers)
    # The only low-level open is read-only: no write, create, truncate or append flag.
    flags = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
             and node.attr.startswith("O_")}
    assert flags <= {"O_RDONLY", "O_NONBLOCK", "O_BINARY"}, flags
    for fragment in ("C:\\\\Python", "C:\\\\Users", "project2", "janik"):
        assert fragment not in source
