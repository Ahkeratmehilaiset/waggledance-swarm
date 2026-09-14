"""Managed turn-loop regressions; fake native children never call model APIs."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "ops/windows/reboot/Invoke-WdLaneTurnLoop.ps1"
PS = shutil.which("powershell.exe") or shutil.which("pwsh")
NATIVE_PYTHON = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Python/Python313/python.exe"
PYTHON = str(NATIVE_PYTHON) if NATIVE_PYTHON.is_file() else sys.executable
SHELLS = list(dict.fromkeys(filter(None, [PS, shutil.which("pwsh")])))


def test_managed_runner_exists() -> None:
    assert RUNNER.is_file(), "a wake sentinel cannot invoke an interactive model turn"


def quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def open_json_snapshot(path: Path):
    """Keep atomic publication compatible with a concurrent Windows observer."""
    if os.name != "nt":
        return path.open(encoding="utf-8-sig")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    # READ | WRITE | DELETE sharing: replacements remain atomic; our held
    # handle reads the old complete snapshot, never a partially rewritten one.
    handle = kernel.CreateFileW(str(path), 0x80000000, 7, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    return os.fdopen(descriptor, encoding="utf-8-sig")


def read_json_snapshot(path: Path):
    with open_json_snapshot(path) as handle:
        return json.load(handle)


def run_ps(script: str, timeout: int = 30, executable: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [executable or PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=timeout, cwd=ROOT,
    )


@pytest.mark.skipif(os.name != "nt" or PS is None, reason="Windows native process runner")
@pytest.mark.parametrize("ps", list(dict.fromkeys(filter(None, [PS, shutil.which("pwsh")]))), ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("scenario,agent,backend,model,effort", [
    (scenario, "codex-lead-1", "codex", "gpt-5.6-sol", "ultra")
    for scenario in ["complete", "ack", "stale", "wake_during", "timeout", "wrong_identity",
                     "child_survives", "child_drains", "wrong_task", "future", "huge_receipt", "output_budget", "two_turns", "idle", "blocked", "blocked_then_wake", "invalid_blocked"]
] + [
    ("complete", "codex-tools-1", "codex", "gpt-5.6-terra", "high"),
    ("complete", "claude-rco-1", "claude", "sonnet", "max"),
    ("complete", "claude-rco-2", "claude", "sonnet", "max"),
    ("complete", "fable-5", "claude", "fable", "max"),
])
def test_native_turn_receipt_contract(tmp_path: Path, scenario: str, agent: str,
                                      backend: str, model: str, effort: str, ps: str) -> None:
    runtime = tmp_path / "bridge"
    runtime.mkdir()
    audit = tmp_path / ".codex-audit"
    audit.mkdir()
    (runtime / f"wake_{agent}").write_text("coalesced burst")
    state = audit / "wd-current-state.json"
    state.write_text('{}')
    fake = tmp_path / "fake_model.py"
    fake.write_text('''import json, os, pathlib, subprocess, sys, time
spec = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
scenario = sys.argv[2]
calls = pathlib.Path(spec["worktree"]) / "calls.jsonl"
with calls.open("a") as handle: handle.write(json.dumps({"pid":os.getpid(), "image":sys.argv[3] if len(sys.argv)>3 else "", "turn_id":spec["turn_id"]}) + "\\n")
if scenario == "timeout": time.sleep(20)
if scenario == "output_budget":
    print("x" * 8192, flush=True)
    time.sleep(20)
if scenario in ["child_survives", "child_drains"]:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(" + ("20" if scenario == "child_survives" else "0.25") + ")"])
    (pathlib.Path(spec["worktree"]) / "child.pid").write_text(str(child.pid))
first_turn = len(calls.read_text().splitlines()) == 1
turn_disposition = "blocked" if scenario in ["blocked", "invalid_blocked"] or (scenario == "blocked_then_wake" and first_turn) else "idle" if scenario == "idle" else "completed"
if scenario == "wake_during" or (scenario in ["two_turns", "blocked_then_wake"] and first_turn):
    pathlib.Path(spec["wake_path"]).write_text("new wake during running turn")
checkpoint = pathlib.Path(spec["compact_state_path"])
if scenario != "stale":
    checkpoint.write_text(json.dumps({"schema":"wd.lane-current.v1", "agent":spec["agent"],
        "worktree":spec["worktree"], "task_id":"test", "status":"completed" if scenario == "invalid_blocked" else turn_disposition,
        "next_action":"wait", "updated_at_utc":"2999-01-01T00:00:00Z" if scenario == "future" else __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()}))
if scenario != "ack":
    receipt = {k:spec[k] for k in ["turn_id","agent","session_id","generation","compact_state_path"]}
    receipt["disposition"] = turn_disposition
    receipt["task_id"] = "different-task" if scenario == "wrong_task" else "test"
    if scenario == "huge_receipt": receipt["padding"] = "x" * 32768
    if scenario == "wrong_identity": receipt["session_id"] = "other"
    pathlib.Path(spec["receipt_path"]).write_text(json.dumps(receipt))
print("ACK received" if scenario == "ack" else "completed bounded slice")
''')
    script = f"""
. {quote(RUNNER)}
function Get-WdTurnArguments {{ param($Backend, $Model, $Effort, $ImagePath, $RuntimeRoot, $ClaudePermissionPosture)
    # Observe on the owner thread before native construction, not through a
    # competing non-delete-sharing reader after process creation.
    $spec = ConvertFrom-WdTurnJson ([IO.File]::ReadAllText($script:WdTurnSpecPath))
    $owner = ConvertFrom-WdTurnJson ([IO.File]::ReadAllText((Join-Path $RuntimeRoot ('.wd-turn-' + $spec.agent + '.owner.json'))))
    if ($owner.turn_id -cne $spec.turn_id -or -not (Test-Path -LiteralPath $owner.pending_path)) {{
        throw 'root pending evidence must precede native construction'
    }}
    return @({quote(fake)}, $script:WdTurnSpecPath, {quote(scenario)}, $ImagePath)
}}
Invoke-WdLaneTurnLoop -Agent {agent} -Backend {backend} -CliPath {quote(PYTHON)} `
  -Model {model} -Effort {effort} -Worktree {quote(tmp_path)} -RuntimeRoot {quote(runtime)} `
  -SessionId test-session -Generation abc123 -CompactStatePath {quote(state)} `
  -StartupPrompt 'INITIAL_VISUAL_DIRECTIVE' -ContinuationPrompt 'CONTINUE_COMPACT_STATE' `
  -ImagePath 'initial-image.png' -MaxTurns {2 if scenario in ['two_turns', 'blocked_then_wake'] else 1} `
  -TurnTimeoutSeconds 2 -MaxOutputBytes 4096 -ClaudePermissionPosture existing_interactive
"""
    result = run_ps(script, executable=ps)
    assert result.returncode == 0, result.stdout + result.stderr
    journal = json.loads((audit / "wd-turn-loop" / "owner.json").read_text(encoding="utf-8-sig"))
    assert json.loads((runtime / f".wd-turn-{agent}.owner.json").read_text(encoding="utf-8-sig")) == journal
    expected = {"complete":"completed", "wake_during":"completed", "ack":"blocked_missing_receipt",
                "stale":"blocked_invalid_checkpoint", "wrong_identity":"blocked_invalid_receipt",
                "timeout":"blocked_timeout", "child_survives":"blocked_child_survived", "child_drains":"completed",
                "wrong_task":"blocked_invalid_checkpoint", "future":"blocked_invalid_checkpoint",
                "huge_receipt":"blocked_invalid_receipt", "output_budget":"blocked_output_budget",
                "two_turns":"completed", "idle":"idle", "blocked":"blocked",
                "blocked_then_wake":"completed", "invalid_blocked":"blocked_invalid_checkpoint"}[scenario]
    assert journal["last_disposition"] == expected
    assert journal["pid"] > 0 and journal["process_start_utc"]
    assert journal["session_id"] == "test-session" and journal["generation"] == "abc123"
    assert journal["continuation"] == "compact_state"
    assert journal["completion_scope"] == "model_turn_checkpointed"
    assert journal["task_completion_verified"] is False
    checkpointed = expected in {"completed", "idle", "blocked"}
    assert journal["status"] == ("stopped" if checkpointed else "blocked")
    assert (runtime / f"wake_{agent}").exists() == (scenario == "wake_during")
    assert bool(list((audit / "wd-turn-loop").glob("*.pending"))) != checkpointed
    calls = [json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert len(calls) == (2 if scenario in ["two_turns", "blocked_then_wake"] else 1)
    if scenario == "blocked_then_wake":
        records = [read_json_snapshot(audit / "wd-turn-loop" / f'turn-{call["turn_id"]}.result.json') for call in calls]
        assert [record["disposition"] for record in records] == ["blocked", "completed"]
        assert all(record["task_completion_verified"] is False for record in records)
        first_checkpoint = read_json_snapshot(audit / "wd-turn-loop" / f'turn-{calls[0]["turn_id"]}.state.json')
        assert first_checkpoint["status"] == "blocked"
        assert len(list((audit / "wd-turn-loop").glob("*.checkpointed.json"))) == 2
    if scenario == "two_turns":
        assert [call["image"] for call in calls] == ["initial-image.png", ""]
        prompts = [(audit / "wd-turn-loop" / f'turn-{call["turn_id"]}.prompt.txt').read_text() for call in calls]
        assert "INITIAL_VISUAL_DIRECTIVE" in prompts[0]
        assert "INITIAL_VISUAL_DIRECTIVE" not in prompts[1]
        assert "CONTINUE_COMPACT_STATE" in prompts[1]
    child_ids = [call["pid"] for call in calls]
    if (tmp_path / "child.pid").exists(): child_ids.append(int((tmp_path / "child.pid").read_text()))
    if scenario == "child_drains":
        result_record = json.loads(next((audit / "wd-turn-loop").glob("*.result.json")).read_text(encoding="utf-8-sig"))
        assert any(item["Pid"] == child_ids[-1] and item["ImagePath"].lower().endswith("python.exe")
                   for item in result_record["descendants_before_drain"])
        assert not result_record["surviving_descendants"]
    dead = run_ps(f"@(Get-Process -Id {','.join(map(str, child_ids))} -ErrorAction SilentlyContinue).Count")
    assert dead.stdout.strip() == "0", "contained children must exit before lease release"


@pytest.mark.skipif(os.name != "nt" or PS is None, reason="Windows native process runner")
def test_existing_interactive_lane_never_launches_child(tmp_path: Path) -> None:
    result = run_ps(f"""
. {quote(RUNNER)}
function Get-WdTurnArguments {{ throw 'must not launch' }}
Invoke-WdLaneTurnLoop -Agent codex-lead-1 -Backend codex -CliPath {quote(PYTHON)} `
  -Model gpt-5.6-sol -Effort ultra -Worktree {quote(tmp_path)} -RuntimeRoot {quote(tmp_path)} `
  -SessionId test -Generation abc -CompactStatePath {quote(tmp_path / '.codex-audit/wd-current-state.json')} `
  -StartupPrompt test -ExistingInteractivePid $PID | ConvertTo-Json -Compress
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "unsupported_live_interactive"
    assert not (tmp_path / ".codex-audit/wd-turn-loop").exists()


@pytest.mark.skipif(os.name != "nt" or PS is None, reason="Windows native process runner")
def test_os_lease_blocks_second_owner_in_another_worktree(tmp_path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    first = tmp_path / "first-worktree"
    second = tmp_path / "second-worktree"
    runtime = tmp_path / "bridge"
    for path in (first, second, runtime): path.mkdir()
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(runtime / ".wd-turn-codex-lead-1.lock"), 0xC0000000, 0, None, 4, 0x80, None)
    assert handle != wintypes.HANDLE(-1).value
    try:
        result = run_ps(f"""
. {quote(RUNNER)}
function Get-WdTurnArguments {{ throw 'must not launch' }}
Invoke-WdLaneTurnLoop -Agent codex-lead-1 -Backend codex -CliPath {quote(PYTHON)} `
  -Model gpt-5.6-sol -Effort ultra -Worktree {quote(second)} -RuntimeRoot {quote(runtime)} `
  -SessionId second-owner -Generation next-generation `
  -CompactStatePath {quote(second / '.codex-audit/wd-current-state.json')} -StartupPrompt test | ConvertTo-Json -Compress
""")
    finally:
        kernel.CloseHandle(handle)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "blocked_duplicate_owner"
    assert not (second / ".codex-audit").exists()


@pytest.mark.skipif(os.name != "nt" or PS is None, reason="Windows native process runner")
def test_crashed_turn_blocks_new_generation_without_replay(tmp_path: Path) -> None:
    runtime = tmp_path / "bridge"
    runtime.mkdir()
    wake = runtime / "wake_codex-lead-1"
    wake.write_text("wake after crash")
    journal = tmp_path / ".codex-audit/wd-turn-loop"
    journal.mkdir(parents=True)
    pending = journal / ("turn-" + "a" * 32 + ".pending")
    pending.write_text('{"session_id":"crashed-owner"}')
    result = run_ps(f"""
. {quote(RUNNER)}
function Get-WdTurnArguments {{ throw 'must not launch' }}
Invoke-WdLaneTurnLoop -Agent codex-lead-1 -Backend codex -CliPath {quote(PYTHON)} `
  -Model gpt-5.6-sol -Effort ultra -Worktree {quote(tmp_path)} -RuntimeRoot {quote(runtime)} `
  -SessionId replacement -Generation newer `
  -CompactStatePath {quote(tmp_path / '.codex-audit/wd-current-state.json')} -StartupPrompt test | ConvertTo-Json -Compress
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["last_disposition"] == "blocked_unresolved_turn"
    assert wake.exists() and pending.exists()
    assert not list(journal.glob("*.stdout.log"))


@pytest.mark.skipif(PS is None, reason="PowerShell is unavailable")
def test_completed_retention_preserves_pending_and_unowned_files(tmp_path: Path) -> None:
    old = "turn-" + "a" * 32
    new = "turn-" + "b" * 32
    unresolved = "turn-" + "c" * 32
    for stem in (old, new, unresolved):
        (tmp_path / f"{stem}.checkpointed.json").write_text('{}')
        (tmp_path / f"{stem}.stdout.log").write_text('log')
    (tmp_path / f"{unresolved}.pending").write_text('unresolved')
    (tmp_path / "turn-not-owned.stdout.log").write_text('leave')
    os.utime(tmp_path / f"{old}.checkpointed.json", (1, 1))
    result = run_ps(f". {quote(RUNNER)}; Remove-WdCompletedTurnArtifacts {quote(tmp_path)} 1")
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / f"{old}.stdout.log").exists()
    assert (tmp_path / f"{unresolved}.pending").exists()
    assert (tmp_path / f"{unresolved}.stdout.log").exists()
    assert (tmp_path / "turn-not-owned.stdout.log").exists()


@pytest.mark.skipif(PS is None, reason="PowerShell is unavailable")
def test_native_cli_arguments_pin_authority_and_initial_image() -> None:
    result = run_ps(f"""
. {quote(RUNNER)}
[pscustomobject]@{{
 codex=@(Get-WdTurnArguments codex gpt-5.6-sol ultra 'C:\\image with spaces.png' 'C:\\runtime bridge');
 claude=@(Get-WdTurnArguments claude sonnet max '' 'C:\\runtime bridge' -ClaudePermissionPosture existing_interactive)
}} | ConvertTo-Json -Compress
""")
    assert result.returncode == 0, result.stdout + result.stderr
    arguments = json.loads(result.stdout)
    assert arguments["codex"][:3] == ["--ask-for-approval", "never", "exec"]
    assert arguments["codex"][-2:] == ["--", "-"]
    assert arguments["codex"][arguments["codex"].index("--add-dir") + 1] == "C:\\runtime bridge"
    assert arguments["claude"][0] == "--print"
    assert arguments["claude"][arguments["claude"].index("--permission-prompts") + 1] == "none"


@pytest.mark.skipif(os.name != "nt" or PS is None, reason="Windows native process runner")
@pytest.mark.parametrize("ps", SHELLS, ids=lambda value: Path(value).stem)
def test_standalone_entry_refused_before_files_or_native_process(tmp_path: Path, ps: str) -> None:
    runtime = tmp_path / "bridge"
    runtime.mkdir()
    result = run_ps(f"""
& {quote(RUNNER)} -Agent codex-lead-1 -Backend codex -CliPath {quote(PYTHON)} `
  -Model gpt-5.6-sol -Effort ultra -Worktree {quote(tmp_path)} -RuntimeRoot {quote(runtime)} `
  -SessionId standalone -Generation test `
  -CompactStatePath {quote(tmp_path / '.codex-audit/wd-current-state.json')} -StartupPrompt test
""", executable=ps)
    assert result.returncode != 0
    assert "requires the integrity-checked lane launcher" in result.stderr
    assert not (tmp_path / ".codex-audit").exists()
    assert not list(runtime.iterdir())


@pytest.mark.skipif(os.name != "nt" or PS is None, reason="Windows native process runner")
@pytest.mark.parametrize("ps", SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("pointer_status", ["running", "waiting"])
def test_previous_worktree_pending_blocks_new_worktree(tmp_path: Path, ps: str, pointer_status: str) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    runtime = tmp_path / "bridge"
    for path in (first, second, runtime): path.mkdir()
    old_journal = first / ".codex-audit/wd-turn-loop"
    old_journal.mkdir(parents=True)
    pending = old_journal / ("turn-" + "a" * 32 + ".pending")
    pending.write_text('{"session_id":"crashed-owner"}')
    pointer = runtime / ".wd-turn-codex-lead-1.owner.json"
    pointer_record = {"schema":"wd.lane-turn-owner.v1", "agent":"codex-lead-1",
                      "status":pointer_status, "worktree":str(first), "journal_root":str(old_journal),
                      "pending_path":str(pending) if pointer_status == "running" else None,
                      "session_id":"old-owner", "generation":"old"}
    pointer.write_text(json.dumps(pointer_record))
    result = run_ps(f"""
. {quote(RUNNER)}
function Get-WdTurnArguments {{ throw 'must not launch into a new worktree' }}
Invoke-WdLaneTurnLoop -Agent codex-lead-1 -Backend codex -CliPath {quote(PYTHON)} `
  -Model gpt-5.6-sol -Effort ultra -Worktree {quote(second)} -RuntimeRoot {quote(runtime)} `
  -SessionId replacement -Generation newer `
  -CompactStatePath {quote(second / '.codex-audit/wd-current-state.json')} -StartupPrompt test | ConvertTo-Json -Compress
""", executable=ps)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["last_disposition"] == "blocked_previous_unresolved_turn"
    assert not (second / ".codex-audit").exists()
    assert json.loads(pointer.read_text()) == pointer_record
    assert pending.exists()


@pytest.mark.skipif(os.name != "nt" or PS is None, reason="Windows native process runner")
@pytest.mark.parametrize("ps", SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("hold_to_deadline,forever", [(False, False), (True, False), (True, True)])
def test_wake_sharing_conflict_defers_only_unstarted_turn(tmp_path: Path, ps: str, hold_to_deadline: bool, forever: bool) -> None:
    import ctypes
    from ctypes import wintypes

    runtime = tmp_path / "bridge"
    runtime.mkdir()
    wake = runtime / "wake_codex-lead-1"
    wake.write_text("ordinary watcher write")
    state = tmp_path / ".codex-audit/wd-current-state.json"
    fake = tmp_path / "fake_model.py"
    fake.write_text('''import datetime, json, pathlib, sys
s = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8-sig"))
pathlib.Path(s["compact_state_path"]).write_text(json.dumps({"schema":"wd.lane-current.v1", "agent":s["agent"], "worktree":s["worktree"], "task_id":"test", "status":sys.argv[2], "next_action":"wait", "updated_at_utc":datetime.datetime.now(datetime.timezone.utc).isoformat()}))
r = {k:s[k] for k in ["turn_id","agent","session_id","generation","compact_state_path"]}
r.update(task_id="test", disposition=sys.argv[2])
pathlib.Path(s["receipt_path"]).write_text(json.dumps(r))
''')
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    # Ordinary read/write sharing, deliberately without FILE_SHARE_DELETE:
    # this reproduces a watcher holding the sentinel while writing it.
    handle = kernel.CreateFileW(str(wake), 0xC0000000, 3, None, 3, 0x80, None)
    assert handle != wintypes.HANDLE(-1).value
    script = f"""
. {quote(RUNNER)}
function Get-WdTurnArguments {{ return @({quote(fake)}, $script:WdTurnSpecPath, {quote('invalid-disposition' if forever else 'completed')}) }}
Invoke-WdLaneTurnLoop -Agent codex-lead-1 -Backend codex -CliPath {quote(PYTHON)} `
  -Model gpt-5.6-sol -Effort ultra -Worktree {quote(tmp_path)} -RuntimeRoot {quote(runtime)} `
  -SessionId sharing -Generation test -PollSeconds 1 -WakeSnapshotTimeoutSeconds 2 `
  -BackstopSeconds 1 {'-Forever' if forever else ''} `
  -CompactStatePath {quote(state)} -StartupPrompt test | ConvertTo-Json -Compress
"""
    process = subprocess.Popen([ps, "-NoProfile", "-NonInteractive", "-Command", script],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=ROOT)
    journal = tmp_path / ".codex-audit/wd-turn-loop"
    try:
        deadline = time.monotonic() + 10
        status = ""
        while time.monotonic() < deadline:
            owner_path = journal / "owner.json"
            try:
                status = read_json_snapshot(owner_path)["status"]
            except FileNotFoundError:
                # The observer may sample before the first atomic publication.
                status = ""
            if status == "waiting_wake" or process.poll() is not None: break
            time.sleep(0.05)
        assert status == "waiting_wake", f"sharing conflict was not deferred: {status}"
        assert not list(journal.glob("*.pending"))
        assert not list(journal.glob("*.stdout.log"))
        assert wake.exists()
        if hold_to_deadline and not forever:
            deferred_stdout, deferred_stderr = process.communicate(timeout=10)
            assert process.returncode == 0, deferred_stdout + deferred_stderr
            assert json.loads(deferred_stdout)["last_disposition"] == "deferred_wake_busy"
            assert not list(journal.glob("*.pending"))
            assert not list(journal.glob("*.stdout.log"))
            assert wake.exists()
        if forever:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    deferred = read_json_snapshot(journal / "owner.json")
                except FileNotFoundError:
                    continue
                if deferred["status"] == "waiting" and deferred["last_disposition"] == "deferred_wake_busy": break
                time.sleep(0.05)
            else: pytest.fail("Forever owner did not schedule a bounded snapshot backstop")
            assert deferred["pid"] == process.pid
            assert process.poll() is None
            assert not list(journal.glob("*.pending"))
            assert not list(journal.glob("*.stdout.log"))
            assert wake.exists()
    finally:
        kernel.CloseHandle(handle)
        stdout, stderr = process.communicate(timeout=20)
    assert process.returncode == 0, stdout + stderr
    if hold_to_deadline and not forever:
        retried = run_ps(script, executable=ps)
        assert retried.returncode == 0, retried.stdout + retried.stderr
        stdout = retried.stdout
    assert json.loads(stdout)["last_disposition"] == ("blocked_invalid_receipt" if forever else "completed")
    assert len(list(journal.glob("*.stdout.log"))) == 1
    if forever:
        assert json.loads(stdout)["pid"] == process.pid
        assert len(list(journal.glob("*.pending"))) == 1
    else:
        assert len(list(journal.glob("*.checkpointed.json"))) == 1
        assert not list(journal.glob("*.pending"))


@pytest.mark.skipif(os.name != "nt" or PS is None, reason="Windows native process runner")
@pytest.mark.parametrize("ps", SHELLS, ids=lambda value: Path(value).stem)
def test_atomic_owner_publication_waits_for_benign_reader(tmp_path: Path, ps: str) -> None:
    import ctypes
    from ctypes import wintypes

    destination = tmp_path / "owner.json"
    destination.write_text('{"value":"old"}')
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateFileW(str(destination), 0x80000000, 1, None, 3, 0x80, None)
    assert handle != wintypes.HANDLE(-1).value
    script = f". {quote(RUNNER)}; Write-WdTurnJson {quote(destination)} @{{ value='new' }}"
    process = subprocess.Popen([ps, "-NoProfile", "-NonInteractive", "-Command", script],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=ROOT)
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not list(tmp_path.glob("owner.json.*.tmp")):
            time.sleep(0.05)
        assert list(tmp_path.glob("owner.json.*.tmp")), "writer did not stage the atomic snapshot"
        time.sleep(0.2)
        assert process.poll() is None, "ordinary held reader caused an immediate publication failure"
        assert json.loads(destination.read_text())["value"] == "old"
    finally:
        kernel.CloseHandle(handle)
        stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, stdout + stderr
    assert json.loads(destination.read_text())["value"] == "new"
    assert not list(tmp_path.glob("owner.json.*.tmp"))


@pytest.mark.skipif(os.name != "nt" or PS is None, reason="Windows atomic publication")
@pytest.mark.parametrize("ps", SHELLS, ids=lambda value: Path(value).stem)
def test_live_snapshot_observer_allows_atomic_replace(tmp_path: Path, ps: str) -> None:
    destination = tmp_path / "owner.json"
    destination.write_text('{"value":"old"}')
    # Deliberately keep the observation handle open across the entire replace.
    # This detects missing FILE_SHARE_DELETE without timing/retry assumptions.
    with open_json_snapshot(destination) as old_snapshot:
        result = run_ps(
            f". {quote(RUNNER)}; Write-WdTurnJson {quote(destination)} @{{value='new'}}",
            executable=ps,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.load(old_snapshot) == {"value": "old"}
        assert read_json_snapshot(destination) == {"value": "new"}


@pytest.mark.skipif(PS is None, reason="PowerShell is unavailable")
@pytest.mark.parametrize("model", ["sonnet", "fable"])
@pytest.mark.parametrize("ps", SHELLS, ids=lambda value: Path(value).stem)
def test_managed_claude_requires_explicit_existing_permission_posture(model: str, ps: str) -> None:
    result = run_ps(f"""
$ErrorActionPreference='Stop'
. {quote(RUNNER)}
$unapprovedRejected=$false
try {{ [void](Get-WdTurnArguments claude {model} max '' 'C:\\bridge') }}
catch {{ $unapprovedRejected=$true }}
$approved=@(Get-WdTurnArguments claude {model} max '' 'C:\\bridge' -ClaudePermissionPosture existing_interactive)
$codex=@(Get-WdTurnArguments codex gpt-5.6-sol ultra '' 'C:\\bridge')
[pscustomobject]@{{ rejected=$unapprovedRejected; approved=$approved; codex=$codex }} | ConvertTo-Json -Compress
""", executable=ps)
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads(result.stdout)
    assert record["rejected"] is True
    assert "--dangerously-skip-permissions" in record["approved"]
    assert "--permission-prompts" in record["approved"]
    assert "--dangerously-skip-permissions" not in record["codex"]


@pytest.mark.skipif(os.name != "nt" or PS is None, reason="Windows native process runner")
@pytest.mark.parametrize("ps", SHELLS, ids=lambda value: Path(value).stem)
def test_missing_claude_permission_posture_refuses_before_writes(tmp_path: Path, ps: str) -> None:
    result = run_ps(f"""
. {quote(RUNNER)}
function Get-WdTurnArguments {{ throw 'native construction must not be reached' }}
Invoke-WdLaneTurnLoop -Agent claude-rco-1 -Backend claude -CliPath {quote(PYTHON)} `
  -Model sonnet -Effort max -Worktree {quote(tmp_path)} -RuntimeRoot {quote(tmp_path)} `
  -SessionId no-posture -Generation test `
  -CompactStatePath {quote(tmp_path / '.codex-audit/wd-current-state.json')} -StartupPrompt test
""", executable=ps)
    assert result.returncode != 0
    assert "explicit existing_interactive permission posture" in result.stderr
    assert not list(tmp_path.iterdir())
