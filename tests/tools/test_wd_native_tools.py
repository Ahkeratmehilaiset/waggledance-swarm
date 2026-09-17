"""Native Tools keeps its saved thread, exclusive owner and visible terminal."""
import base64
import json
import os
import sys
from pathlib import Path

import pytest

from test_wd_reboot_bundle import REBOOT, LANE_TEST_SHELLS, _run_powershell
from test_wd_startup_recovery import load, q

THREAD = "01a0a07b-ca98-71e1-90cb-d588435a2d8d"
TOOLS = REBOOT / "start-wd-tools-consumer.ps1"


@pytest.mark.skipif(os.name != "nt", reason="Windows process command-line parsing")
@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("case", ["current", "old_bundle", "external", "payload"])
def test_supervisor_does_not_miss_existing_tools_after_bundle_update(ps, case):
    supervisor = REBOOT / 'wd_supervisor.ps1'
    script = f"""
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile({q(supervisor)},[ref]$tokens,[ref]$errors)
foreach($fn in $ast.FindAll({{param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]}},$false)) {{
 . ([scriptblock]::Create($fn.Extent.Text))
}}
"""
    commands = {
        'current': r'powershell.exe -NoProfile -STA -File C:\bundles\new\start-wd-tools-consumer.ps1 -Generation new',
        'old_bundle': r'powershell.exe -NoProfile -STA -File C:\bundles\old\start-wd-tools-consumer.ps1 -Generation old',
        'external': r'powershell.exe -NoProfile -File C:\unrelated\worker.ps1',
        'payload': r'powershell.exe -Command "Write-Output start-wd-tools-consumer.ps1"',
    }
    allowed = r'C:\bundles\new\start-wd-tools-consumer.ps1'
    script += f"""
$p=[pscustomobject]@{{Name='powershell.exe';ProcessId=24840;CommandLine={q(commands[case])}}}
try {{
 Assert-WdToolsLauncherGeneration -Processes @($p) -AllowedPaths @({q(allowed)})
 @{{ok=$true}}|ConvertTo-Json
}} catch {{ @{{ok=$false;error=$_.Exception.Message}}|ConvertTo-Json }}
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result['ok'] is (case != 'old_bundle'), result
    if case == 'old_bundle':
        assert '24840' in result['error'] and 'controlled handoff' in result['error']


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("case", ["paused", "foreign", "wrong_worktree", "bad_id", "interrupt", "recovery"])
def test_native_tools_preserves_recorded_context_and_unresolved_holds(tmp_path, ps, case):
    journal = tmp_path / ".codex-audit" / "wd-turn-loop"
    journal.mkdir(parents=True)
    record = dict(schema="wd.codex-conversation.v1", agent="codex-tools-1", worktree=str(tmp_path),
                  thread_id=THREAD, automatic_enabled=False, initial_context_delivered=True,
                  interrupting=False, recovery_required=False, codex_permission_posture="workspace_write")
    if case == "foreign": record["agent"] = "codex-lead-1"
    if case == "wrong_worktree": record["worktree"] = "elsewhere"
    if case == "bad_id": record["thread_id"] = "--last"
    if case == "interrupt": record["interrupting"] = True
    if case == "recovery": record["recovery_required"] = True
    identity = journal / "conversation.json"
    identity.write_text(json.dumps(record))
    script = "$ErrorActionPreference='Stop'\nSet-StrictMode -Version Latest\n"
    for name in ["Test-PathAtOrBelow", "Assert-DirectoryPathWithoutReparse", "Assert-FilePathWithoutReparse", "Get-WdNativeToolsResumeState"]:
        script += load(TOOLS, name)
    script += f"""
try {{ $s=Get-WdNativeToolsResumeState -Worktree {q(tmp_path)}; @{{ok=$true;thread=$s.thread_id}}|ConvertTo-Json }}
catch {{ @{{ok=$false;error=$_.Exception.Message}}|ConvertTo-Json }}
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result["ok"] is (case == "paused"), result
    if result["ok"]: assert result["thread"] == THREAD
    assert json.loads(identity.read_text()) == record


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_native_tools_resume_arguments_keep_scoped_permissions_and_history(ps):
    script = load(TOOLS, "Get-WdNativeToolsArguments") + f"""
$s=[pscustomobject]@{{thread_id='{THREAD}';initial_context_delivered=$true}}
$a=Get-WdNativeToolsArguments -Saved $s -Worktree 'C:\\Tools Space' -Model gpt-5.6-terra -Effort high `
 -Prompt 'Restore claims' -ImagePath 'image.png' -WritableRoots @('C:\\bridge\\outbox') -NetworkAccess $true
ConvertTo-Json -InputObject $a
"""
    args = json.loads(_run_powershell(script, executable=ps).stdout)
    assert args[:2] == ["resume", THREAD]
    assert args[args.index("--sandbox") + 1] == "workspace-write"
    assert args[args.index("--ask-for-approval") + 1] == "never"
    assert args[args.index("--add-dir") + 1] == r"C:\bridge\outbox"
    assert "sandbox_workspace_write.network_access=true" in args
    assert "--image" not in args and "--last" not in args and "app-server" not in args
    assert "standard interactive Codex terminal" in args[-1]


@pytest.mark.skipif(os.name != "nt", reason="Windows native argv semantics")
@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_start_process_roundtrips_literal_prompt_without_shell_evaluation(tmp_path, ps):
    out = tmp_path / "argv.json"
    capture = tmp_path / "capture.py"
    capture.write_text("import sys,json,pathlib\npathlib.Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n")
    values = ['quotes "inside" a prompt', '$(not_a_command); & literal', 'C:\\space dir\\', '']
    argv = [str(capture), str(out), *values]
    script = load(TOOLS, "ConvertTo-WdToolsNativeArgument") + f"""
$a=@({','.join(q(v) for v in argv)})
$line=@($a|ForEach-Object {{ ConvertTo-WdToolsNativeArgument $_ }}) -join ' '
$p=Start-Process -FilePath {q(sys.executable)} -ArgumentList $line -NoNewWindow -Wait -PassThru
if($p.ExitCode -ne 0) {{throw 'fake native process failed'}}
"""
    _run_powershell(script, executable=ps)
    assert json.loads(out.read_text()) == values


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_supervisor_requests_a_visible_terminal_without_custom_ui(ps):
    script = "$ErrorActionPreference='Stop'\n"
    for name in ["ConvertTo-WindowsCommandLineArgument", "Start-OutOfTaskJobPowerShell"]:
        script += load(REBOOT / "wd_supervisor.ps1", name)
    script += """
function Get-Command { [CmdletBinding()]param($Name,$CommandType) [pscustomobject]@{Source='C:\\Terminal\\wt.exe'} }
function New-CimInstance { [CmdletBinding()]param($ClassName,$Property,[switch]$ClientOnly) return [pscustomobject]$Property }
function Invoke-CimMethod { [CmdletBinding()]param($ClassName,$MethodName,$Arguments) $script:launch=$Arguments; return @{ReturnValue=0;ProcessId=444} }
$actions=New-Object 'System.Collections.Generic.List[string]'
Start-OutOfTaskJobPowerShell -HostPath 'C:\\Windows\\powershell.exe' -ArgumentList @('-File','C:\\Python\\start-wd-tools-consumer.ps1','-Generation','abc') -Name tools -VisibleTerminal
@{command=$script:launch.CommandLine;show=$script:launch.ProcessStartupInformation.ShowWindow;flags=$script:launch.ProcessStartupInformation.CreateFlags}|ConvertTo-Json
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result["command"].startswith(r"C:\Windows\powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand ")
    bootstrap = base64.b64decode(result["command"].split()[-1]).decode('utf-16le')
    assert "Start-Process -WindowStyle Normal -FilePath 'C:\\Terminal\\wt.exe'" in bootstrap
    assert "-w new new-tab --title codex-tools-1" in bootstrap
    assert "start-wd-tools-consumer.ps1" in bootstrap
    assert result["show"] == 0  # only the activation helper is hidden


def test_native_mode_returns_before_loading_custom_window():
    source = TOOLS.read_text(encoding="utf-8")
    native = source.index("Invoke-WdNativeToolsTerminal -Saved")
    gui = source.index("$conversationResult = &", native)
    assert "return" in source[native:gui]
    assert "[IO.FileShare]::None" in source
    assert "$nativeToolsLease.Dispose()" in source
    assert "startup_continuation_requested" in source
    assert "task_completion_verified=$false" in source


@pytest.mark.parametrize("ps", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
def test_native_runtime_import_preserves_launcher_identity(ps):
    script = load(TOOLS, "Get-WdNativeToolsRuntimeFunctions") + f"""
$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$worktree='C:\\Tools'
$runtimeRoot='C:\\bridge'
$generation='pinned-generation'
$agent='codex-tools-1'
$model='gpt-5.6-terra'
$code=Get-Content -LiteralPath {q(REBOOT / 'Invoke-WdLaneTurnLoop.ps1')} -Raw
. (Get-WdNativeToolsRuntimeFunctions -VerifiedCode $code)
@{{worktree=$worktree;runtime=$runtimeRoot;generation=$generation;agent=$agent;model=$model;
   writer=[bool](Get-Command Write-WdTurnOwner -ErrorAction Stop)}}|ConvertTo-Json
"""
    result = json.loads(_run_powershell(script, executable=ps).stdout)
    assert result == dict(worktree=r"C:\Tools", runtime=r"C:\bridge", generation="pinned-generation",
                          agent="codex-tools-1", model="gpt-5.6-terra", writer=True)
