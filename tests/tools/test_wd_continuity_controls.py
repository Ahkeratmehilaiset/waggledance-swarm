"""Adversarial, isolated checks of native continuity control precedence.

The fixture log and package are private to pytest's .codex-audit basetemp.
No test opens or writes the live bridge runtime.
"""

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q


TOOLS = REBOOT / "start-wd-tools-consumer.ps1"
AGENT = "codex-lead-1"
TASK = "control-probe"


def _event(*, agent="operator", task=TASK, to="", type="message", status="hold"):
    return dict(agent=agent, task_id=task, to=to, type=type, status=status,
                ts_utc="2026-09-28T22:59:00Z", payload={})


def _fixture(tmp_path, rows):
    bundle = tmp_path / "bundle"
    helper_dir = bundle / "tools-bootstrap/.agent-bridge/bin"
    helper_dir.mkdir(parents=True)
    files = {}
    for leaf in ("BridgeLogReader.ps1", "BridgeIncrementalReader.ps1"):
        target = helper_dir / leaf
        shutil.copyfile(REBOOT.parents[2] / ".agent-bridge/bin" / leaf, target)
        files[target.relative_to(bundle).as_posix()] = hashlib.sha256(target.read_bytes()).hexdigest().upper()
    manifest = bundle / "deployment-manifest.json"
    manifest.write_text(json.dumps({"files": files}), encoding="utf-8")
    anchor = hashlib.sha256(manifest.read_bytes()).hexdigest().upper()
    runtime = tmp_path / "runtime"
    (runtime / "shared").mkdir(parents=True)
    log = runtime / "shared/events.jsonl"
    log.write_text("".join(json.dumps(row, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    prelude = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    prelude += load(REBOOT / "Invoke-WdLaneTurnLoop.ps1", "Assert-WdTurnPath")
    prelude += load(TOOLS, "Test-WdContinuityControlEvents")
    prelude += f"""
$env:WD_BRIDGE_PYTHON_WRAPPER={q(bundle / 'Invoke-WdBridgePython.ps1')}
$env:WD_REBOOT_EXPECTED_MANIFEST_HASH='{anchor}'
function Check {{
 try {{@{{ok=$true;held=(Test-WdContinuityControlEvents -RuntimeRoot {q(runtime)} -TaskId {q(TASK)} -Agent {q(AGENT)} -CheckpointAt '2026-09-28T22:00:00Z')}}}}
 catch {{@{{ok=$false;error=$_.Exception.Message}}}}
}}
"""
    return log, prelude


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize(
    "event,held",
    [
        (_event(to=""), True),
        (_event(task="fleet", to="all"), True),
        (_event(task="fleet", to=AGENT), True),
        (_event(task="fleet", to="claude-rco-1"), False),
        (_event(agent="fable-5", task="other", to="all", type="finding", status="changes_requested"), False),
        (_event(agent="fable-5", status="on_hold"), True),
        (_event(agent="fable-5", status="changesRequested"), True),
        (_event(agent="fable-5", status="vetoed"), True),
        (_event(agent="fable-5", status="unblocked"), False),
        (_event(agent="fable-5", status="holdover"), False),
        (_event(agent="fable-5", status="failed"), False),
        (_event(agent="operator", task="other", to=AGENT,
                status="operator_signed_head_exact_retracted"), False),
        (_event(agent="fable-5", type="finding", status="approved"), True),
    ],
    ids=["operator-empty", "operator-all", "operator-lane", "operator-other-lane",
         "foreign-broadcast", "on-hold", "camel-changes-requested", "vetoed",
         "unblocked-is-not-blocked", "holdover-is-not-hold", "failure-is-not-hold",
         "retracted-is-not-release", "same-task-finding-fail-closed"],
)
def test_scoped_control_tokens_and_broadcasts(tmp_path, ps, event, held):
    _, script = _fixture(tmp_path, [event])
    report = json.loads(_run_powershell(script + "Check | ConvertTo-Json -Compress", executable=ps).stdout)
    assert report == {"ok": True, "held": held}, (event, report)


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("later", [_event(status="approved"), _event(status="in_progress"),
                                    _event(type="decision", status="rco_pass")],
                         ids=["approval", "message", "rco-pass"])
def test_later_noncontrol_never_releases_latched_hold(tmp_path, ps, later):
    log, script = _fixture(tmp_path, [_event()])
    script += f"""
$before=Check
[IO.File]::AppendAllText({q(log)}, {q(json.dumps(later) + chr(10))})
$after=Check
[IO.File]::WriteAllText({q(log)}, {q(json.dumps(_event(agent='peer', status='notice')) + chr(10))})
$truncated=Check
@{{before=$before;after=$after;truncated=$truncated}} | ConvertTo-Json -Depth 6 -Compress
"""
    report = json.loads(_run_powershell(script, executable=ps).stdout)
    assert all(report[name] == {"ok": True, "held": True}
               for name in ("before", "after", "truncated")), report


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_partial_prefix_is_unknown_until_all_pages_are_scanned(tmp_path, ps):
    row = _event(agent="peer", task="other", to=AGENT, status="notice")
    row["message"] = "x" * 4000
    _, script = _fixture(tmp_path, [row] * 800)
    script += "$checks=@(1..6 | ForEach-Object { Check }); $checks | ConvertTo-Json -Depth 5 -Compress"
    checks = json.loads(_run_powershell(script, executable=ps).stdout)
    assert checks[0]["ok"] is False and "catching up" in checks[0]["error"], checks
    assert checks[-1] == {"ok": True, "held": False}, checks
    first_complete = next(i for i, item in enumerate(checks) if item["ok"])
    assert all(not item["ok"] for item in checks[:first_complete]), checks
    assert all(item == {"ok": True, "held": False} for item in checks[first_complete:]), checks


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("mutation", ["truncate", "rotate", "partial"],
                         ids=["truncation", "rotation", "partial-row"])
def test_unreconciled_log_change_never_returns_clear(tmp_path, ps, mutation):
    row = _event(agent="peer", task="other", to=AGENT, status="notice")
    log, script = _fixture(tmp_path, [row] * 8)
    script += "$before=Check\n"
    if mutation == "truncate":
        script += f"[IO.File]::WriteAllText({q(log)}, '')\n"
    elif mutation == "rotate":
        script += f"[IO.File]::Delete({q(log)}); [IO.File]::WriteAllText({q(log)}, '')\n"
    else:
        script += f"[IO.File]::AppendAllText({q(log)}, '{{\"agent\":\"operator\"')\n"
    script += "$after=Check; @{before=$before;after=$after} | ConvertTo-Json -Depth 5 -Compress"
    report = json.loads(_run_powershell(script, executable=ps).stdout)
    assert report["before"] == {"ok": True, "held": False}, report
    assert report["after"]["ok"] is False, report


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_same_length_in_place_prefix_rewrite_cannot_hide_hold(tmp_path, ps):
    """A cursor must not treat an unchanged EOF as proof its prefix is unchanged."""
    log, script = _fixture(tmp_path, [_event(status="idle")])
    marker = b'"status":"idle"'
    offset = log.read_bytes().index(marker) + len(b'"status":"')
    script += f"""
$before=Check
$stream=[IO.File]::Open({q(log)},[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::ReadWrite)
try {{
 [void]$stream.Seek({offset},[IO.SeekOrigin]::Begin)
 $bytes=[Text.Encoding]::ASCII.GetBytes('hold')
 $stream.Write($bytes,0,$bytes.Length)
 $stream.Flush($true)
}} finally {{$stream.Dispose()}}
$after=Check
@{{before=$before;after=$after}} | ConvertTo-Json -Depth 5 -Compress
"""
    report = json.loads(_run_powershell(script, executable=ps).stdout)
    assert report["before"] == {"ok": True, "held": False}, report
    assert report["after"] != {"ok": True, "held": False}, report
