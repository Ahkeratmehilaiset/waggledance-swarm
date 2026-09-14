"""Read-only fleet observations using ordinary, isolated runtime records."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "ops/windows/reboot/Get-WdSwarmParallelStatus.ps1"
SHELLS = sorted({p for p in (shutil.which("powershell.exe"), shutil.which("pwsh")) if p})
pytestmark = pytest.mark.skipif(not SHELLS or not shutil.which("git"), reason="PowerShell/Git required")
GENERATION = "a" * 40


def quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@pytest.fixture(params=SHELLS or [None])
def fleet(request):
    audit = ROOT / ".codex-audit"
    audit.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="parallel-status-", dir=audit) as directory:
        root = Path(directory)
        now = datetime.now(timezone.utc)
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        lanes = []
        for agent in ["codex-lead-1", "claude-rco-1", "claude-rco-2", "fable-5", "codex-tools-1"]:
            worktree = root / agent
            (worktree / ".codex-audit").mkdir(parents=True)
            checkpoint = {
                "schema": "wd.lane-current.v1", "agent": agent,
                "worktree": str(worktree), "updated_at_utc": now.isoformat(),
                "task_id": "fixture-task", "status": "working", "head": head,
                "write_scope": [], "next_action": "Run affected tests",
                "blockers": [],
            }
            (worktree / ".codex-audit/wd-current-state.json").write_text(json.dumps(checkpoint), encoding="utf-8")
            lanes.append({"agent": agent, "worktree": str(worktree)})
        ready = root / "ready.json"
        started = (now - timedelta(minutes=2)).isoformat()
        ready.write_text(json.dumps({
            "schema": "wd.tools-consumer-ready.v1", "status": "ready",
            "generation": GENERATION, "pid": 12345, "process_start_utc": started,
            "ready_at_utc": (now - timedelta(minutes=1)).isoformat(),
            "worktree": lanes[-1]["worktree"],
        }), encoding="utf-8")
        manifest = root / "fleet.json"
        manifest.write_text(json.dumps({
            "schema_version": 2, "git_executable": shutil.which("git"),
            "runtime_root": str(root), "lanes": lanes[:-1],
            "tools_supervisor": dict(lanes[-1], task_name="WD-Supervisor", readiness_path=str(ready)),
        }), encoding="utf-8")
        pointer = root / "pointer.json"
        pointer.write_text(json.dumps({
            "schema_version": 1, "source_commit": GENERATION,
            "active_bundle": str(root), "fleet_manifest": str(manifest),
            "installed_at_utc": (now - timedelta(hours=1)).isoformat(),
        }), encoding="utf-8")
        yield {"root": root, "shell": request.param, "manifest": manifest,
               "pointer": pointer, "ready": ready, "lanes": lanes,
               "started": started, "head": head}


def update(path: Path, **changes):
    record = json.loads(path.read_text(encoding="utf-8"))
    record.update(changes)
    path.write_text(json.dumps(record), encoding="utf-8")


def checkpoint(fleet, index=-1):
    return Path(fleet["lanes"][index]["worktree"]) / ".codex-audit/wd-current-state.json"


def run_status(fleet, *, process="present", task="Ready", generation=GENERATION,
               started=None):
    before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in fleet["root"].rglob("*") if p.is_file()}
    process_body = {
        "absent": "return",
        "unknown": "throw 'process query unavailable'",
        "present": "[pscustomobject]@{ ProcessId = 12345; CreationDate = [datetime]" + quote(started or fleet["started"]) +
        "; CommandLine = " + quote("powershell.exe -File start-wd-tools-consumer.ps1 -Generation " + generation) + " }",
    }[process]
    task_body = "throw 'task query unavailable'" if task == "unknown" else "[pscustomobject]@{ State = " + quote(task) + " }"
    command = """
function Get-CimInstance { param($ClassName, $Filter, $ErrorAction) PROCESS_BODY }
function Get-ScheduledTask { param($TaskName, $ErrorAction) TASK_BODY }
function Start-Process { throw 'status attempted process start' }
function Set-Content { throw 'status attempted write' }
function Enable-ScheduledTask { throw 'status attempted task enable' }
& SCRIPT -ManifestPath MANIFEST POINTER -Json
""".replace("PROCESS_BODY", process_body).replace("TASK_BODY", task_body).replace("SCRIPT", quote(SCRIPT)).replace("MANIFEST", quote(fleet["manifest"])).replace("POINTER", "-CurrentStatePath " + quote(fleet["pointer"]))
    result = subprocess.run([fleet["shell"], "-NoProfile", "-NonInteractive", "-Command", command], cwd=ROOT,
                            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr
    after = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in fleet["root"].rglob("*") if p.is_file()}
    assert before == after, "status mutated its evidence or runtime directory"
    return json.loads(result.stdout)


def test_stale_checkpoint_keeps_legacy_plan_but_has_no_fresh_runnable_evidence(fleet):
    update(checkpoint(fleet), updated_at_utc="2020-01-01T00:00:00Z")
    report = run_status(fleet)
    lane = report["lanes"][-1]
    assert lane["state_health"] == "stale"
    assert lane["runnable"] is True  # Legacy means a recorded next action exists.
    assert lane["runnable_evidence"] == "unknown"
    assert report["summary"]["fresh_runnable_evidence_lanes"] == 0


def test_current_runtime_is_bound_to_pid_start_and_generation(fleet):
    report = run_status(fleet)
    lane = report["lanes"][-1]
    assert lane["runnable_evidence"] == "observed"
    assert lane["runtime"]["identity"] == "matched"
    assert lane["runtime"]["observed_pid"] == 12345
    assert lane["runtime"]["observed_generation"] == GENERATION
    assert lane["checkpoint"]["source_domain"] == "lane_checkpoint"
    assert report["installed_bundle"]["source_commit"] == GENERATION
    assert lane["head"] == fleet["head"] != GENERATION
    assert report["summary"]["fresh_runnable_evidence_lanes"] == 1
    assert all(item["runtime"]["identity"] == "unknown" for item in report["lanes"][:-1])
    assert all(item["progress"]["status"] == "unknown" for item in report["lanes"])


@pytest.mark.parametrize("case", ["absent", "query_unknown", "pid_reused", "generation_mismatch", "record_generation_mismatch", "missing_ready", "invalid_ready", "old_ready", "degraded", "missing_pointer", "invalid_pointer", "unknown_task", "future_checkpoint", "head_mismatch"])
def test_incomplete_or_mismatched_runtime_never_becomes_fresh_runnable(fleet, case):
    kwargs = {}
    if case == "absent": kwargs["process"] = "absent"
    if case == "query_unknown": kwargs["process"] = "unknown"
    if case == "pid_reused": kwargs["started"] = datetime.now(timezone.utc).isoformat()
    if case == "generation_mismatch": kwargs["generation"] = "b" * 40
    if case == "record_generation_mismatch": update(fleet["ready"], generation="b" * 40)
    if case == "missing_ready": fleet["ready"].unlink()
    if case == "invalid_ready": fleet["ready"].write_text("{", encoding="utf-8")
    if case == "old_ready": update(fleet["ready"], ready_at_utc="2020-01-01T00:00:00Z")
    if case == "degraded": update(fleet["ready"], status="degraded")
    if case == "missing_pointer": fleet["pointer"].unlink()
    if case == "invalid_pointer": update(fleet["pointer"], source_commit="not-a-commit")
    if case == "unknown_task": kwargs["task"] = "unknown"
    if case == "future_checkpoint": update(checkpoint(fleet), updated_at_utc="2099-01-01T00:00:00Z")
    if case == "head_mismatch": update(checkpoint(fleet), head="b" * 40)
    report = run_status(fleet, **kwargs)
    assert report["lanes"][-1]["runnable_evidence"] != "observed"
    assert report["summary"]["fresh_runnable_evidence_lanes"] == 0


def test_disabled_supervisor_is_an_independent_intentional_state(fleet):
    report = run_status(fleet, task="Disabled")
    assert report["supervisor"]["status"] == "disabled"
    assert report["lanes"][-1]["runtime"]["identity"] == "matched"
    assert report["lanes"][-1]["runnable_evidence"] == "not_observed"
    assert report["summary"]["fresh_runnable_evidence_lanes"] == 0


@pytest.mark.parametrize("status,blockers", [("blocked", []), ("working", ["Await owner decision"])])
def test_checkpoint_blockers_prevent_fresh_runnable_claim(fleet, status, blockers):
    update(checkpoint(fleet), status=status, blockers=blockers)
    lane = run_status(fleet)["lanes"][-1]
    assert lane["runnable_evidence"] == "not_observed"


def test_heartbeat_and_checkpoint_time_are_not_substantive_progress(fleet):
    (fleet["root"] / "heartbeat_codex-tools-1.json").write_text(json.dumps({"ts_utc": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
    report = run_status(fleet)
    for lane in report["lanes"]:
        assert lane["progress"]["status"] == "unknown"
        assert lane["progress"]["last_substantive_progress_at_utc"] is None
        assert lane["progress"]["wait_age_seconds"] is None


@pytest.mark.parametrize("case", ["missing", "invalid_json", "missing_head", "missing_scope", "oversized"])
def test_missing_or_invalid_checkpoint_remains_unknown(fleet, case):
    path = checkpoint(fleet)
    if case == "missing":
        path.unlink()
    elif case == "invalid_json":
        path.write_text("{", encoding="utf-8")
    elif case == "oversized":
        update(path, next_action="a" * 33000)
    else:
        record = json.loads(path.read_text(encoding="utf-8"))
        del record["head" if case == "missing_head" else "write_scope"]
        path.write_text(json.dumps(record), encoding="utf-8")
    lane = run_status(fleet)["lanes"][-1]
    assert lane["runnable_evidence"] == "unknown"
    assert lane["state_health"] == ("missing" if case == "missing" else "invalid")


def test_other_selected_manifest_does_not_claim_installed_runtime(fleet):
    update(fleet["pointer"], fleet_manifest=str(fleet["root"] / "other-fleet.json"))
    report = run_status(fleet)
    assert report["installed_bundle"]["matches_selected_manifest"] is False
    assert report["lanes"][-1]["runtime"]["identity"] == "unknown"


def test_old_readiness_can_match_a_durable_process_but_is_not_progress(fleet):
    start = "2020-01-01T00:00:00+00:00"
    update(fleet["ready"], process_start_utc=start, ready_at_utc="2020-01-01T00:01:00+00:00")
    lane = run_status(fleet, started=start)["lanes"][-1]
    assert lane["runtime"]["identity"] == "matched"
    assert lane["runtime"]["readiness_age_seconds"] > 1800
    assert lane["progress"]["status"] == "unknown"


@pytest.mark.parametrize("status,expected", [("unknown", "unknown"), ("awaiting_ci", "unknown"), ("waiting", "not_observed"), ("completed", "not_observed")])
def test_free_form_checkpoint_status_does_not_imply_runnable(fleet, status, expected):
    update(checkpoint(fleet), status=status)
    lane = run_status(fleet)["lanes"][-1]
    assert lane["runnable"] is True
    assert lane["runnable_evidence"] == expected


def test_record_read_allows_concurrent_atomic_replacement(fleet):
    """A writer may replace the canonical record while its old snapshot is read."""
    path = fleet["ready"]
    replacement = path.with_suffix(".next.json")
    path.write_text('{"value":"before"}', encoding="utf-8")
    replacement.write_text('{"value":"after"}', encoding="utf-8")
    command = rf"""
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile({quote(SCRIPT)}, [ref]$tokens, [ref]$errors)
$reader = $ast.Find({{ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Read-WdStatusRecord'
}}, $true)
if (-not $reader -or $errors.Count) {{ throw 'Status reader could not be parsed' }}
Invoke-Expression $reader.Extent.Text
# Parsing occurs inside the reader's try/finally while its read handle is
# still held. This deterministic interleaving requires no timing or retries.
function ConvertFrom-Json {{
    [CmdletBinding()]
    param([Parameter(ValueFromPipeline)] [string] $InputObject)
    process {{
        [IO.File]::Replace({quote(replacement)}, {quote(path)}, {quote(path.with_suffix('.previous.json'))})
        Microsoft.PowerShell.Utility\ConvertFrom-Json -InputObject $InputObject
    }}
}}
$snapshot = Read-WdStatusRecord -Path {quote(path)}
$current = Microsoft.PowerShell.Utility\ConvertFrom-Json -InputObject ([IO.File]::ReadAllText({quote(path)}))
# The old snapshot is now the backup. Exclusive access proves its read handle
# was disposed after parsing, rather than leaked across subsequent observations.
$exclusive = [IO.File]::Open({quote(path.with_suffix('.previous.json'))}, [IO.FileMode]::Open,
    [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
$exclusive.Dispose()
[pscustomobject]@{{ snapshot = $snapshot.value; current = $current.value }} | ConvertTo-Json -Compress
"""
    result = subprocess.run([fleet["shell"], "-NoProfile", "-NonInteractive", "-Command", command],
                            cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"snapshot": "before", "current": "after"}
    assert not replacement.exists()
