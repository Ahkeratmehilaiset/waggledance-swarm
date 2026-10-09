"""A blocked Tools wake relay is operator-visible (RCO2 78aaf7b0 C1); the original error and no-retry stay intact.

The exact try/catch around Invoke-WdNativeToolsWakeRelay is cut out of Invoke-WdNativeToolsTerminal (PowerShell AST)
and run with test doubles: no native process, queue, notice publisher or bridge write.
"""
import json
from pathlib import Path

import pytest

from test_wd_native_tools_wake import TOOLS
from test_wd_reboot_bundle import LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import q

HARNESS = r"""
$ErrorActionPreference='Stop'
$WarningPreference='__WARN__'
Set-StrictMode -Version Latest
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile('__TOOLS__',[ref]$tokens,[ref]$errors)
$terminal=$ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'Invoke-WdNativeToolsTerminal'},$true)
$try=@($terminal.FindAll({param($n) $n -is [Management.Automation.Language.TryStatementAst] -and
    $n.Body.Extent.Text.Contains('Invoke-WdNativeToolsWakeRelay -Native')},$true))
if ($try.Count -lt 1) { throw 'relay try missing' }
$try=@($try | Sort-Object { $_.Extent.Text.Length } | Select-Object -First 1)   # the innermost: try { relay } catch
$script:writes=@(); $script:notices=@()
function Write-WdTurnJson { param($Path,$Value) $script:writes+=@(($Value | ConvertTo-Json -Compress)) }
function Invoke-WdNativeToolsWakeRelay { if ('__RELAY__' -ceq 'fails') { throw [InvalidOperationException]::new('Codex queue timed out; delivery outcome is uncertain, automatic retry is blocked') } }
function Invoke-WdContinuityOperatorNotice {
    param($Agent,$ThreadId,$Worktree,$RuntimeRoot,$SessionId,$ErrorText)
    $script:notices+=@([ordered]@{agent=$Agent;thread=$ThreadId;session=$SessionId;error=$ErrorText})
    if ('__NOTICE__' -ceq 'fails') { throw 'fixture notice publisher down' }
    return [pscustomobject]@{schema='wd.continuity-alert-result.v1';status='published'}
}
$native=[pscustomobject]@{Id=1}
$Saved=[pscustomobject]@{thread_id='00000000-0000-4000-8000-000000000001'}
$BaseRecord=@{generation='g';codex_command_sha256=('A'*64);session_id='wd-fixture-session'}
$record=[ordered]@{status='terminal_ready';bridge_wake_transport='codex_queue'}
$ReadinessPath='__READY__'; $RuntimeRoot='__ROOT__'; $Worktree='__WT__'; $CliPath='cli'
$caught=$null
try { . ([scriptblock]::Create($try[0].Extent.Text)) } catch { $caught=$_ }
[ordered]@{
    error=$(if ($caught) { $caught.Exception.Message } else { '' })
    error_type=$(if ($caught) { $caught.Exception.GetType().FullName } else { '' })
    writes=@($script:writes); notices=@($script:notices)
} | ConvertTo-Json -Depth 6 -Compress
"""

TIMEOUT = "Codex queue timed out; delivery outcome is uncertain, automatic retry is blocked"


def _run(tmp_path, ps, relay, notice, warn="Continue"):
    script = (HARNESS.replace("__TOOLS__", str(TOOLS)).replace("__RELAY__", relay).replace("__NOTICE__", notice)
              .replace("__WARN__", warn).replace("__READY__", str(tmp_path / "ready.json"))
              .replace("__ROOT__", str(tmp_path)).replace("__WT__", str(tmp_path)))
    done = _run_powershell(script, executable=ps)
    return json.loads(done.stdout.strip().splitlines()[-1]), done


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_a_blocked_relay_records_readiness_and_one_operator_notice_then_rethrows_the_original(tmp_path, ps):
    report, _ = _run(tmp_path, ps, "fails", "ok")
    assert (report["error"], report["error_type"]) == (TIMEOUT, "System.InvalidOperationException"), report
    assert report["notices"] == [{"agent": "codex-tools-1", "thread": "00000000-0000-4000-8000-000000000001",
                                  "session": "wd-fixture-session", "error": "native_wake_relay_blocked: " + TIMEOUT}]
    first, last = (json.loads(w) for w in (report["writes"][0], report["writes"][-1]))
    assert first["status"] == "bridge_wake_blocked" and first["bridge_wake_error"] == TIMEOUT
    assert last["bridge_wake_notice"] == "published"


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("warn", ["Continue", "Stop"])
def test_a_failed_notice_never_replaces_the_original_error(tmp_path, ps, warn):
    report, done = _run(tmp_path, ps, "fails", "fails", warn)
    assert (report["error"], report["error_type"]) == (TIMEOUT, "System.InvalidOperationException"), report
    assert len(report["notices"]) == 1 and len(report["writes"]) == 1
    assert json.loads(report["writes"][0])["status"] == "bridge_wake_blocked"


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_a_relay_that_ends_normally_sends_no_notice(tmp_path, ps):
    report, _ = _run(tmp_path, ps, "ok", "ok")
    assert report == {"error": "", "error_type": "", "writes": [], "notices": []}, report


def test_the_notice_reason_map_names_the_blocked_relay():
    text = Path(TOOLS).read_text(encoding="utf-8")
    assert "'^native_wake_relay_blocked: ' { 'native_wake_relay_blocked'; break }" in text
    assert text.index("} catch [Management.Automation.PipelineStoppedException] { throw }",
                      text.index("-ErrorText ('native_wake_relay_blocked: '")) > 0
