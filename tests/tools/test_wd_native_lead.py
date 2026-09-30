"""Native Codex handoff preserves thread identity and unresolved-work holds."""
import json
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q

THREAD = "01a0a654-12af-7d81-85fc-d75d515c5b65"


SNAPSHOT_ID = "0123456789abcdef" * 2
OTHER_ID = "fedcba9876543210" * 2
RELAY_ACCEPTED = ("relay_queued", "relay_watching", "relay_queued_legacy_snapshot", "relay_no_agent",
                  "relay_generation", "relay_rejected", "relay_rejected_named", "relay_rejected_nostamp",
                  "relay_queued_named", "relay_claiming_moved", "relay_claiming_unmoved", "relay_legacy_kept",
                  "relay_refusal_receipt")
RELAY_REFUSED = ("relay_submitting", "relay_snapshot", "relay_orphan_snapshot", "relay_orphan_named", "relay_thread",
                 "relay_agent", "relay_bad_stamp", "relay_fresh", "relay_rejected_count", "relay_rejected_badstamp",
                 "relay_rejected_legacy_snapshot", "relay_claiming_no_id", "relay_bad_snapshot_id", "relay_unowned",
                 "relay_miscased_snapshot", "relay_miscased_field", "relay_watching_named", "relay_claiming_legacy",
                 "relay_snapshot_directory", "relay_submitting_receipt", "relay_live_lead", "relay_state_directory")


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("case", ["clean", "fresh", "pending", "blocked", "wrong_thread", "foreign", "interrupt",
                                  "recovery", *RELAY_ACCEPTED, *RELAY_REFUSED])
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
    if case not in ("fresh", "relay_fresh"):
        (journal / "conversation.json").write_text(json.dumps(identity))
    if case == "pending":
        (journal / ("turn-" + "a" * 32 + ".pending")).write_text("preserve me")
    pointer = dict(schema="wd.lane-turn-owner.v1", agent="codex-lead-1",
                   status="blocked" if case == "blocked" else "stopped", pending_path=None,
                   worktree=str(tmp_path), journal_root=str(journal))
    (runtime / ".wd-turn-codex-lead-1.owner.json").write_text(json.dumps(pointer))
    if case.startswith("relay_"):
        # The Lead wake relay record and its snapshots, shaped as the composed relay writes them (e4 plus the RCO1
        # 0900 named-snapshot contract). The preflight must accept exactly what the relay's first poll accepts.
        relay = dict(schema="wd.native-tools-wake.v1", status="queued", thread_id=THREAD, agent="codex-lead-1",
                     generation="gen-a", queue_id="", updated_at_utc="2026-09-30T07:00:00.0000000+00:00")
        relay.update({
            "relay_watching": dict(status="watching"), "relay_submitting": dict(status="submitting"),
            "relay_snapshot": dict(status="watching"), "relay_agent": dict(agent="codex-tools-1"),
            "relay_thread": dict(thread_id="02b1b765-12af-7d81-85fc-d75d515c5b66"),
            "relay_generation": dict(generation="another-generation"),
            "relay_bad_stamp": dict(updated_at_utc="not a time"),
            "relay_rejected": dict(status="rejected", rejections=1),
            "relay_rejected_named": dict(status="rejected", rejections=2, snapshot_id=SNAPSHOT_ID),
            "relay_rejected_nostamp": dict(status="rejected", updated_at_utc="not a time"),
            "relay_rejected_count": dict(status="rejected", rejections="many", snapshot_id=SNAPSHOT_ID),
            "relay_rejected_badstamp": dict(status="rejected", updated_at_utc="not a time", snapshot_id=SNAPSHOT_ID),
            "relay_rejected_legacy_snapshot": dict(status="rejected", rejections=1),
            "relay_queued_named": dict(snapshot_id=SNAPSHOT_ID), "relay_unowned": dict(snapshot_id=SNAPSHOT_ID),
            "relay_miscased_snapshot": dict(snapshot_id=SNAPSHOT_ID),
            "relay_snapshot_directory": dict(snapshot_id=SNAPSHOT_ID),
            "relay_claiming_moved": dict(status="claiming", snapshot_id=SNAPSHOT_ID, delivery_id=""),
            "relay_claiming_unmoved": dict(status="claiming", snapshot_id=SNAPSHOT_ID, delivery_id=""),
            "relay_claiming_legacy": dict(status="claiming", snapshot_id=SNAPSHOT_ID, delivery_id=""),
            "relay_claiming_no_id": dict(status="claiming", delivery_id=""),
            "relay_bad_snapshot_id": dict(snapshot_id=SNAPSHOT_ID.upper()),
            "relay_watching_named": dict(status="watching", snapshot_id=SNAPSHOT_ID),
            "relay_submitting_receipt": dict(status="submitting", snapshot_id=SNAPSHOT_ID, delivery_id=OTHER_ID),
            # The live Lead record since 2026-09-29T23:08:06Z: submitting, a fixed-name snapshot, no snapshot_id.
            "relay_live_lead": dict(status="submitting", generation="8" * 40, native_pid=23104, relay_pid=19704,
                                    delivery_id="7d8ae8b5561442f1a537b7119897b050",
                                    updated_at_utc="2026-09-29T23:08:06.0577847+00:00",
                                    task_completion_verified=False, prompt_mode="pinned_procedure"),
        }.get(case, {}))
        if case == "relay_no_agent":
            relay.pop("agent")
        if case == "relay_miscased_field":
            relay["Status"] = relay.pop("status")
        if case == "relay_state_directory":
            (journal / "native-bridge-wake.json").mkdir()
        elif case not in ("relay_orphan_snapshot", "relay_orphan_named"):
            (journal / "native-bridge-wake.json").write_text(json.dumps(relay))
        suffix = {"relay_snapshot": ".wake", "relay_queued_legacy_snapshot": ".wake",
                  "relay_orphan_snapshot": ".wake", "relay_rejected_legacy_snapshot": ".wake",
                  "relay_claiming_legacy": ".wake", "relay_live_lead": ".wake",
                  "relay_legacy_kept": ".wake.legacy-638000000000000000", "relay_unowned": ".wake." + OTHER_ID,
                  "relay_miscased_snapshot": ".WAKE." + SNAPSHOT_ID}.get(case)
        if case in ("relay_orphan_named", "relay_rejected_named", "relay_rejected_count", "relay_rejected_badstamp",
                    "relay_queued_named", "relay_claiming_moved", "relay_watching_named", "relay_submitting_receipt"):
            suffix = ".wake." + SNAPSHOT_ID
        if suffix is not None:
            (journal / ("native-bridge-wake.json" + suffix)).write_text("{}")
        if case == "relay_snapshot_directory":
            (journal / ("native-bridge-wake.json.wake." + SNAPSHOT_ID)).mkdir()
        if case in ("relay_refusal_receipt", "relay_submitting_receipt"):
            (journal / ("native-bridge-wake.json.refusal-" + OTHER_ID)).write_text("{}")
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ["Assert-LanePathWithoutReparse", "Read-Utf8LaneSnapshot", "Get-WdNativeLeadResumeState"]:
        script += load(REBOOT / "start-wd-agent.ps1", name)
    script += f"""
try {{ $s=Get-WdNativeLeadResumeState -Worktree {q(tmp_path)} -RuntimeRoot {q(runtime)}; @{{accepted=$true;state=$s}} | ConvertTo-Json }}
catch {{ @{{accepted=$false;error=$_.Exception.Message}} | ConvertTo-Json }}
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result["accepted"] is (case in ("clean", "fresh") + RELAY_ACCEPTED), result
    if case == "clean" or case in RELAY_ACCEPTED:
        assert result["state"] == dict(thread_id=THREAD, initial_context_delivered=True)
    if case in RELAY_REFUSED:
        assert "reconciled bridge wake relay state" in result["error"], result
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
$cliName='codex.cmd'; $model='gpt-6-astra'; $effort='xhigh'; $autoCompactTokens=$null; $worktree='C:\\Python\\project2'; $Agent='codex-lead-1'
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
