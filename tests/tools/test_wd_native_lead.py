"""Native Codex handoff preserves thread identity and unresolved-work holds."""
import json
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q

THREAD = "01a0a654-12af-7d81-85fc-d75d515c5b65"


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("case", ["clean", "fresh", "pending", "blocked", "wrong_thread", "foreign", "interrupt", "recovery"])
def test_native_resume_never_guesses_or_bypasses_pending_work(tmp_path, ps, case):
    runtime = tmp_path / "bridge"
    runtime.mkdir()
    journal = tmp_path / ".codex-audit" / "wd-turn-loop"
    journal.mkdir(parents=True)
    identity = dict(schema="wd.codex-conversation.v1", agent="codex-lead-1",
                    worktree=str(tmp_path), thread_id=THREAD,
                    initial_context_delivered=True, interrupting=False, recovery_required=False)
    if case == "wrong_thread":
        identity["thread_id"] = "--last"
    if case == "foreign":
        identity["agent"] = "codex-tools-1"
    if case == "interrupt":
        identity["interrupting"] = True
    if case == "recovery":
        identity["recovery_required"] = True
    if case != "fresh":
        (journal / "conversation.json").write_text(json.dumps(identity))
    if case == "pending":
        (journal / ("turn-" + "a" * 32 + ".pending")).write_text("preserve me")
    pointer = dict(schema="wd.lane-turn-owner.v1", agent="codex-lead-1",
                   status="blocked" if case == "blocked" else "stopped", pending_path=None,
                   worktree=str(tmp_path), journal_root=str(journal))
    (runtime / ".wd-turn-codex-lead-1.owner.json").write_text(json.dumps(pointer))
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ["Assert-LanePathWithoutReparse", "Read-Utf8LaneSnapshot", "Get-WdNativeLeadResumeState"]:
        script += load(REBOOT / "start-wd-agent.ps1", name)
    script += f"""
try {{ $s=Get-WdNativeLeadResumeState -Worktree {q(tmp_path)} -RuntimeRoot {q(runtime)}; @{{accepted=$true;state=$s}} | ConvertTo-Json }}
catch {{ @{{accepted=$false;error=$_.Exception.Message}} | ConvertTo-Json }}
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result["accepted"] is (case in ("clean", "fresh")), result
    if case == "clean":
        assert result["state"] == dict(thread_id=THREAD, initial_context_delivered=True)
    if case == "fresh":
        assert result["state"] == dict(thread_id="", initial_context_delivered=False)
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("delivered", [True, False])
def test_native_resume_arguments_keep_exact_thread_and_image_once(ps, delivered):
    source = (REBOOT / "start-wd-agent.ps1").read_text(encoding="utf-8")
    start = source.rindex("$launchArguments = @()")
    end = source.index("$previousPreference = $ErrorActionPreference", start)
    script = f"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$nativeLead=$true; $nativeResume=[pscustomobject]@{{thread_id='{THREAD}';initial_context_delivered=${str(delivered).lower()}}}
$lane=@{{}}; $manifest=@{{lanes=@()}}; $externalSessions=@(); $script:checks=0
$sourceTreeMode=$false; $DryRun=$false
function Assert-WdLaneLaunchAvailable {{
  param($Lane,$KnownLanes,$ExternalSessions,[switch]$AllowUnpinnedParser)
  if($AllowUnpinnedParser){{throw 'Live native launch must require the pinned parser'}}
  $script:checks++
}}
$cliName='codex.cmd'; $model='gpt-6-astra'; $effort='xhigh'; $worktree='C:\\Python\\project2'; $Agent='codex-lead-1'
$startupPrompt='FIRST visual'; $continuationPrompt='Existing context'; $targetImagePath='exact.png'
{source[start:end]}
@{{arguments=$launchArguments;checks=$script:checks}} | ConvertTo-Json
"""
    record = json.loads(_run_powershell(script, executable=ps).stdout)
    args = record["arguments"]
    assert args[:2] == ["resume", THREAD]
    assert args[args.index("--model") + 1] == "gpt-6-astra"
    assert 'model_reasoning_effort="xhigh"' in args
    assert ("--image" in args) is not delivered
    assert args[-1] == ("Existing context" if delivered else "FIRST visual")
    assert "--last" not in args and "app-server" not in args
    assert record["checks"] == 1


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
def test_native_lead_never_starts_permission_clicker(ps):
    script = load(REBOOT / "start-wd-all.ps1", "Get-WdLeadPromptWatcherPolicy") + """
$ErrorActionPreference='Stop'
$lane=[pscustomobject]@{agent='codex-lead-1';cli='codex.cmd';turn_mode='interactive';native_resume_policy='recorded_conversation'}
$required=(Get-WdLeadPromptWatcherPolicy -Lane $lane -WatcherState ([pscustomobject]@{action='launch'})).required
$rejected=$false
try { Get-WdLeadPromptWatcherPolicy -Lane $lane -WatcherState ([pscustomobject]@{action='current'}) | Out-Null } catch { $rejected=$true }
@{required=$required;existing_rejected=$rejected}|ConvertTo-Json
"""
    assert json.loads(_run_powershell(script, executable=ps).stdout) == {"required": False, "existing_rejected": True}
