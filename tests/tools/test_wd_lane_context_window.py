"""Per-lane auto-compact window (lane profile switching PR-17): validated early, passed to the lane CLI."""
import hashlib
import json
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q

LAUNCHER = REBOOT / "start-wd-agent.ps1"
THREAD = "9f375967-f824-4e2e-8104-7f0011117cf5"


def tokens_for(lane_json: str, ps: str) -> dict:
    """Get-WdLaneAutoCompactTokens on a lane parsed by ConvertFrom-Json, as the launcher parses the fleet."""
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n" + load(LAUNCHER, "Get-WdLaneAutoCompactTokens")
    script += f"""
$lane = {q(lane_json)} | ConvertFrom-Json
try {{ $v = Get-WdLaneAutoCompactTokens -Lane $lane -Agent 'fable-5'; @{{ok=$true; value=$v; type=$(if ($null -eq $v) {{ 'null' }} else {{ $v.GetType().Name }})}} | ConvertTo-Json -Compress }}
catch {{ @{{ok=$false; error=$_.Exception.Message}} | ConvertTo-Json -Compress }}
"""
    return json.loads(_run_powershell(script, executable=ps).stdout)


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("value", [100000, 400000, 1000000])
def test_a_whole_number_in_range_is_the_window(ps, value):
    result = tokens_for(json.dumps({"agent": "fable-5", "auto_compact_tokens": value}), ps)
    assert result == {"ok": True, "value": value, "type": "Int64"}


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_an_absent_window_leaves_the_cli_default(ps):
    assert tokens_for(json.dumps({"agent": "fable-5"}), ps) == {"ok": True, "value": None, "type": "null"}


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("raw", ["99999", "1000001", "0", "-400000", '"400000"', "400000.0", "4e5", "true", "null",
                                 "[400000]", '{"tokens": 400000}', "9223372036854775807"])
def test_a_present_but_malformed_window_refuses_the_launch(ps, raw):
    result = tokens_for('{"agent": "fable-5", "auto_compact_tokens": ' + raw + "}", ps)
    assert result["ok"] is False, result
    assert "auto_compact_tokens must be a whole number of tokens from 100000 to 1000000" in result["error"]


def run_block(tmp_path, ps, cli, tokens, native_lead=False):
    """The launcher's real argument block, with the lane's validated window."""
    source = LAUNCHER.read_text(encoding="utf-8")
    start = source.rindex("$launchArguments = @()")
    end = source.index("$previousPreference = $ErrorActionPreference", start)
    settings = tmp_path / "wd-claude-event-driven-settings.json"
    settings.write_bytes((REBOOT / settings.name).read_bytes())
    pin = hashlib.sha256(settings.read_bytes()).hexdigest().upper()
    window = "$null" if tokens is None else f"[long]{tokens}"
    script = f"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
{load(LAUNCHER, 'Assert-LanePathWithoutReparse')}
function Assert-WdLaneLaunchAvailable {{ param($Lane,$KnownLanes,$ExternalSessions,[switch]$AllowUnpinnedParser) }}
$PSScriptRoot={q(tmp_path)}
$laneTrustedDrive={q(tmp_path)}
$deploymentAnchor=[pscustomobject]@{{files=[pscustomobject]@{{'wd-claude-event-driven-settings.json'='{pin}'}}}}
$claudeResume=[pscustomobject]@{{thread_id='{THREAD}';initial_context_delivered=$true}}
$nativeResume=[pscustomobject]@{{thread_id='{THREAD}';initial_context_delivered=$true}}
$nativeLead=${str(native_lead).lower()}; $lane=@{{}}; $manifest=@{{lanes=@()}}; $externalSessions=@()
$sourceTreeMode=$false; $DryRun=$false; $worktree={q(tmp_path)}; $targetImagePath='exact.png'
$cliName='{cli}'; $Agent='fable-5'; $model='native'; $effort='native'; $autoCompactTokens={window}
$startupPrompt='Read image first'; $continuationPrompt='Resume authorized work'
{source[start:end]}
ConvertTo-Json -InputObject $launchArguments
"""
    return json.loads(_run_powershell(script, executable=ps).stdout), settings


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_claude_gets_the_window_after_its_pinned_settings(tmp_path, ps):
    args, settings = run_block(tmp_path, ps, "claude.cmd", 400000)
    assert args[:6] == ["--resume", THREAD, "--settings", str(settings), "--autocompact", "400000"]
    assert args.count("--autocompact") == 1
    assert args[-1] == "Resume authorized work"


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("native_lead", [False, True])
def test_codex_gets_the_window_as_its_compact_token_limit(tmp_path, ps, native_lead):
    args, _ = run_block(tmp_path, ps, "codex.cmd", 250000, native_lead=native_lead)
    assert args.count("model_auto_compact_token_limit=250000") == 1
    assert args[args.index("model_auto_compact_token_limit=250000") - 1] == "-c"
    assert "--autocompact" not in args
    if native_lead:
        assert args[:2] == ["resume", THREAD]


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("cli", ["claude.cmd", "codex.cmd"])
def test_no_window_adds_no_argument(tmp_path, ps, cli):
    args, _ = run_block(tmp_path, ps, cli, None)
    assert "--autocompact" not in args
    assert not any("model_auto_compact_token_limit" in arg for arg in args)


def test_the_window_is_validated_before_any_launch_work():
    source = LAUNCHER.read_text(encoding="utf-8")
    call = source.index("$autoCompactTokens = Get-WdLaneAutoCompactTokens -Lane $lane -Agent $Agent")
    assert source.index("$effort = [string]$lane.effort") < call < source.index("$supportedEfforts = ")
    assert call < source.index("$launchArguments = @()\n") and source.count("Get-WdLaneAutoCompactTokens -Lane") == 1


def test_the_fleet_runs_the_window_experiment_on_fable_5_only():
    fleet = json.loads((REBOOT / "wd-fleet.json").read_text(encoding="utf-8"))
    windows = {lane["agent"]: lane.get("auto_compact_tokens") for lane in fleet["lanes"]}
    assert windows == {"codex-lead-1": None, "claude-rco-1": None, "claude-rco-2": None, "fable-5": 400000}
