# SPDX-License-Identifier: BUSL-1.1
"""Lane profile switching PR-4: the launcher shadow read logs and never switches."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

import tools.lane_profile_launch_probe as probe_module
from tools.lane_profile_catalog import load_catalog
from tools.lane_profile_launch_probe import append_entry, main, probe
from tools.lane_profile_record import record_path, write_record

ROOT = Path(__file__).resolve().parents[2]
REBOOT = ROOT / "ops" / "windows" / "reboot"
CATALOG_PATH = ROOT / "configs" / "lane_profile_catalog.json"
CATALOG, DIGEST = load_catalog(CATALOG_PATH)
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
LEAD_UUID = "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101"
HOSTS = list(dict.fromkeys(filter(None, [shutil.which("pwsh"), shutil.which("powershell.exe")])))


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def record(**overrides) -> dict:
    base = {
        "schema": "wd.lane-profile-record.v1", "lane": "claude-rco-1",
        "desired_profile": "claude-opus-5-5-xhigh", "previous_profile": "claude-sonnet-5-xhigh",
        "reason": "review load", "requested_by": {"agent": "codex-lead-1", "agent_uuid": LEAD_UUID,
                                                  "session_id": "wd-lane-codex-lead-1-x"},
        "request_id": "req-1", "transition_id": "tid-1",
        "created_at": iso(NOW - timedelta(minutes=15)), "expires_at": iso(NOW + timedelta(hours=1)),
        "catalog_sha256": DIGEST, "launched": None,
    }
    base.update(overrides)
    return base


def run_probe(root, lane="claude-rco-1", **kwargs):
    return probe(root, lane, "start-wd-agent", "native", "native", now=NOW, **kwargs)


# ---------------------------------------------------------------- the probe

def test_shadow_record_is_logged_as_would_apply_and_the_launch_stays_native(tmp_path):
    write_record(record_path(tmp_path, "claude-rco-1"), record())
    entry = run_probe(tmp_path)
    assert entry["launch"] == "native_argv_unchanged"
    assert (entry["mode"], entry["decision_action"], entry["apply_suppressed"]) == ("shadow", "native", False)
    assert entry["would_apply"]["profile_id"] == "claude-opus-5-5-xhigh"
    assert entry["catalog_sha256"] == DIGEST and entry["fallback_event"] is None
    assert (entry["argv_model"], entry["argv_effort"]) == ("native", "native")


def test_no_record_is_a_quiet_native_launch(tmp_path):
    entry = run_probe(tmp_path)
    assert (entry["decision_action"], entry["would_apply"], entry["fallback_event"]) == ("native", None, None)


def test_expired_record_is_a_fallback_event_and_a_native_launch(tmp_path):
    write_record(record_path(tmp_path, "claude-rco-1"),
                 record(expires_at=iso(NOW - timedelta(minutes=1))))
    entry = run_probe(tmp_path)
    assert entry["launch"] == "native_argv_unchanged" and entry["would_apply"] is None
    assert entry["fallback_event"]["reason"] == "record_unusable"


def test_record_for_another_catalog_is_refused(tmp_path):
    write_record(record_path(tmp_path, "claude-rco-1"), record(catalog_sha256="0" * 64))
    entry = run_probe(tmp_path)
    assert entry["would_apply"] is None and entry["fallback_event"]["reason"] == "record_unusable"


def test_unknown_lane_is_a_fallback_event(tmp_path):
    entry = run_probe(tmp_path, lane="..\\evil")
    assert entry["fallback_event"]["reason"] == "invalid_lane"
    assert entry["launch"] == "native_argv_unchanged"


@pytest.mark.parametrize("content", [None, b"{", b'{"schema": "x", "schema": "y"}'])
def test_an_unusable_catalog_is_a_native_launch(tmp_path, content):
    path = tmp_path / "catalog.json"
    if content is not None:
        path.write_bytes(content)
    entry = run_probe(tmp_path, catalog_path=path)
    assert entry["launch"] == "native_argv_unchanged" and entry["catalog_sha256"] is None
    assert entry["fallback_event"]["reason"] == "catalog_unusable"


def test_an_apply_decision_is_logged_as_suppressed_never_applied(tmp_path, monkeypatch):
    # A future signed auto catalog: PR-4 still only reads.
    import tools.lane_profile_record as record_module
    target = {"profile_id": "claude-opus-5-5-xhigh", "model": "claude-opus-5-5", "effort": "xhigh"}
    monkeypatch.setattr(record_module, "launch_decision", lambda *a, **k: {
        "mode": "auto", "action": "apply", "profile": target, "would_apply": target, "fallback_event": None})
    entry = run_probe(tmp_path)
    assert (entry["decision_action"], entry["apply_suppressed"]) == ("apply", True)
    assert entry["launch"] == "native_argv_unchanged"


def test_a_raising_decision_is_still_a_native_launch(tmp_path, monkeypatch):
    import tools.lane_profile_record as record_module

    def boom(*args, **kwargs):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(record_module, "launch_decision", boom)
    entry = run_probe(tmp_path)
    assert entry["fallback_event"] == {"reason": "decision_failed", "detail": "RuntimeError"}
    assert entry["launch"] == "native_argv_unchanged"


# ---------------------------------------------------------------- the log

def test_the_log_appends_one_line_per_launch(tmp_path):
    for _ in range(3):
        assert append_entry(tmp_path, run_probe(tmp_path)) == "logged"
    lines = (tmp_path / "lane_profiles" / "launch-shadow.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3 and all(json.loads(line)["schema"] == "wd.lane-profile-launch-shadow.v1"
                                   for line in lines)


def test_a_full_log_stops_growing_but_the_launch_goes_on(tmp_path, monkeypatch):
    monkeypatch.setattr(probe_module, "MAX_LOG_BYTES", 10)
    assert append_entry(tmp_path, run_probe(tmp_path)) == "logged"
    assert append_entry(tmp_path, run_probe(tmp_path)) == "log_full"
    assert len((tmp_path / "lane_profiles" / "launch-shadow.jsonl").read_text().splitlines()) == 1


def test_a_log_just_under_the_bound_still_appends(tmp_path, monkeypatch):
    path = tmp_path / "lane_profiles" / "launch-shadow.jsonl"
    path.parent.mkdir()
    path.write_bytes(b"x" * 9)
    monkeypatch.setattr(probe_module, "MAX_LOG_BYTES", 10)
    assert append_entry(tmp_path, run_probe(tmp_path)) == "logged"


def _junction(link: Path, target: Path) -> bool:
    if sys.platform != "win32":
        return False
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
    return result.returncode == 0


def test_a_junctioned_log_directory_is_refused(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    if not _junction(runtime / "lane_profiles", outside):
        pytest.skip("junctions unavailable")
    assert append_entry(runtime, run_probe(runtime)) == "log_failed:RecordError"
    assert list(outside.iterdir()) == []


def test_cli_always_exits_zero_and_prints_one_json_line(tmp_path, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    code = main(["--runtime-root", str(blocker), "--lane", "claude-rco-1", "--launcher", "start-wd-agent",
                 "--argv-model", "native", "--argv-effort", "native"])
    out = capsys.readouterr().out.strip().splitlines()
    assert code == 0 and len(out) == 1
    entry = json.loads(out[0])
    assert entry["launch"] == "native_argv_unchanged" and entry["log"].startswith("log_failed:")


def test_the_packaged_closure_runs_isolated_like_the_bridge_wrapper(tmp_path):
    # Copy exactly the packaged files and run with -S -B and PYTHONSAFEPATH from a foreign cwd,
    # the way Invoke-WdBridgePythonTool does. A missing packaged module fails here.
    definition = json.loads((REBOOT / "bridge-code-files.json").read_text(encoding="utf-8"))
    code_root = tmp_path / "tools-bootstrap"
    for relative in definition["python_files"]:
        target = code_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    assert definition["python_entrypoints"]["lane_profile_launch_probe"] == "tools/lane_profile_launch_probe.py"
    runtime = tmp_path / "runtime"
    now = datetime.now(timezone.utc)
    write_record(record_path(runtime, "claude-rco-1"),
                 record(created_at=iso(now - timedelta(minutes=5)), expires_at=iso(now + timedelta(hours=1))))
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env.update(PYTHONSAFEPATH="1", PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-S", "-B", str(code_root / "tools" / "lane_profile_launch_probe.py"),
         "--runtime-root", str(runtime), "--lane", "claude-rco-1", "--launcher", "start-wd-agent",
         "--argv-model", "native", "--argv-effort", "native"],
        capture_output=True, text=True, timeout=60, cwd=str(tmp_path), env=env)
    assert result.returncode == 0, result.stderr
    entry = json.loads(result.stdout.strip().splitlines()[-1])
    assert entry["catalog_sha256"] == DIGEST and entry["fallback_event"] is None
    assert entry["would_apply"]["profile_id"] == "claude-opus-5-5-xhigh" and entry["log"] == "logged"


# ---------------------------------------------------------------- the launchers

LAUNCHERS = [("start-wd-agent.ps1", "start-wd-agent"), ("start-wd-tools-consumer.ps1", "start-wd-tools-consumer")]


def _function_text(script: str) -> str:
    source = (REBOOT / script).read_text(encoding="utf-8")
    start = source.index("function Invoke-WdLaneProfileShadowRead {")
    return source[start:source.index("\n}\n", start) + 3]


@pytest.mark.parametrize("script,launcher", LAUNCHERS)
def test_the_launcher_calls_the_read_before_every_launch_path(script, launcher):
    source = (REBOOT / script).read_text(encoding="utf-8")
    call = source.index(f"-Launcher '{launcher}'")
    if script == "start-wd-agent.ps1":
        assert call < source.index("if ($launchTurnMode -ceq 'managed') {")
        assert call < source.index("$launchArguments = @()\n")
        assert call > source.index("CLI application changed after its handshake")
    else:
        assert call < source.index("if ($conversationSurface -cin @('local_window','native_terminal')) {\n"
                                   "    if ([Threading.Thread]::CurrentThread.GetApartmentState()")
        assert call > source.index("Assert-ToolsBootstrapIntegrity `\n    -ScriptRoot $PSScriptRoot")
    assert source.count("Invoke-WdLaneProfileShadowRead") == 2  # one definition, one call


@pytest.mark.parametrize("script,launcher", LAUNCHERS)
def test_the_read_cannot_touch_launcher_state(script, launcher):
    body = _function_text(script)
    for forbidden in ("$script:", "$global:", "Set-Variable", "$model", "$effort", "$launchArguments",
                      "return ", "Write-Output", "exit"):
        assert forbidden not in body, forbidden
    assert "catch {" in body


STUB = {
    "lines": "function Invoke-WdBridgePythonTool { param($BundleRoot,$Tool,$ToolArguments) "
             "$global:seen = @($Tool) + @($ToolArguments); 'noise'; '{\"launch\":\"native_argv_unchanged\"}' }",
    "throws": "function Invoke-WdBridgePythonTool { param($BundleRoot,$Tool,$ToolArguments) "
              "throw [IO.IOException]::new('manifest changed') }",
}


@pytest.mark.parametrize("host", HOSTS or [None])
@pytest.mark.parametrize("script,launcher", LAUNCHERS)
@pytest.mark.parametrize("stub", sorted(STUB))
def test_the_read_emits_nothing_and_never_throws(tmp_path, host, script, launcher, stub):
    if host is None or os.name != "nt":
        pytest.skip("Windows PowerShell launcher")
    probe_script = tmp_path / "probe.ps1"
    probe_script.write_text(f"""
$ErrorActionPreference='Stop'; Set-StrictMode -Version Latest
{STUB[stub]}
{_function_text(script)}
$model='claude-sonnet-5'; $effort='xhigh'
$result = @(Invoke-WdLaneProfileShadowRead -BundleRoot 'C:\\b' -RuntimeRoot 'C:\\r' -Lane 'claude-rco-1' `
  -Launcher '{launcher}' -Model '' -Effort $effort)
$seenValue = @()
if (Get-Variable -Name seen -Scope Global -ErrorAction SilentlyContinue) {{ $seenValue = $global:seen }}
[pscustomobject]@{{ count = $result.Count; model = $model; effort = $effort; seen = @($seenValue) }} |
  ConvertTo-Json -Compress
""", encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    result = subprocess.run([host, "-NoProfile", "-NonInteractive", "-File", str(probe_script)],
                            capture_output=True, text=True, timeout=60, env=env)
    assert result.returncode == 0, result.stderr
    state = json.loads(result.stdout.strip().splitlines()[-1])
    assert state["count"] == 0  # nothing reaches the caller's success stream
    assert (state["model"], state["effort"]) == ("claude-sonnet-5", "xhigh")
    if stub == "lines":
        seen = state["seen"]
        assert seen[0] == "tools/lane_profile_launch_probe.py"
        assert seen[seen.index("--argv-model") + 1] == "unset"  # never an empty native argument
        assert seen[seen.index("--launcher") + 1] == launcher


def test_a_log_exactly_at_the_bound_is_full(tmp_path, monkeypatch):
    path = tmp_path / "lane_profiles" / "launch-shadow.jsonl"
    path.parent.mkdir()
    path.write_bytes(b"x" * 10)
    monkeypatch.setattr(probe_module, "MAX_LOG_BYTES", 10)
    assert append_entry(tmp_path, run_probe(tmp_path)) == "log_full"
    assert path.read_bytes() == b"x" * 10


@pytest.mark.parametrize("script,guard", [
    ("start-wd-agent.ps1", "if (-not $sourceTreeMode -and -not $DryRun) {\n  Invoke-WdLaneProfileShadowRead"),
    ("start-wd-tools-consumer.ps1",
     "if ($null -ne $bridgeCodeContext -and -not $ValidateOnly) {\n    Invoke-WdLaneProfileShadowRead"),
])
def test_rehearsal_and_validation_runs_skip_the_read(script, guard):
    source = (REBOOT / script).read_text(encoding="utf-8")
    assert source.count(guard) == 1
