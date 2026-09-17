"""Tools conversation launcher/readiness contracts; no CLI or model calls."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
REBOOT = ROOT / "ops/windows/reboot"
TOOLS = REBOOT / "start-wd-tools-consumer.ps1"
SUPERVISOR = REBOOT / "wd_supervisor.ps1"
WINDOWS_POWERSHELL = shutil.which("powershell.exe")


def _quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _run_powershell(script: str) -> subprocess.CompletedProcess[str]:
    assert WINDOWS_POWERSHELL is not None
    return subprocess.run(
        [
            WINDOWS_POWERSHELL,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_tools_local_window_uses_verified_shared_conversation_without_legacy_tick() -> None:
    text = TOOLS.read_text(encoding="utf-8")

    assert "wd.tools-consumer-ready.v2" in text
    assert "ui_transport_only" in text
    assert "OnTransportReady" in text
    assert "OnTurnFinalized" in text
    assert "Invoke-WdCodexConversationLoop.ps1" in text
    assert "Show-WdOperatorConversation.ps1" in text
    assert "Invoke-WdLaneTurnLoop.ps1" in text
    assert "Invoke-WdCodexConversationLoop @" in text
    assert "native_process_start_utc" in text
    assert "last_checkpoint_verified_at_utc" in text
    assert "Read-WdToolsConversationCodeSnapshot" in text
    assert "conversation_code_sha256 = [pscustomobject]$conversationCodeHashes" in text
    assert "target_state_image_initial_tick_only = $true" in text
    assert "AdditionalWritableRoots = @($conversationWritableRoots)" in text
    assert "NetworkAccess = [bool]$conversationPermissions.NetworkAccess" in text

    local_start = text.rindex("if ($conversationSurface -cin @('local_window','native_terminal'))")
    legacy_start = text.index("$commonConsumerArguments = @{", local_start)
    local = text[local_start:legacy_start]
    legacy = text[legacy_start:]
    assert "& $consumerScript" not in local
    assert "Invoke-WdCodexConversationLoop" in local
    assert "& $consumerScript" in legacy


def test_supervisor_launches_single_hidden_sta_wrapper_and_selects_v2_by_mode() -> None:
    text = SUPERVISOR.read_text(encoding="utf-8")

    assert "'-STA'" in text
    assert "CREATE_NO_WINDOW" in text
    assert "ShowWindow = if ($VisibleTerminal) { [uint16]1 } else { [uint16]0 }" in text
    assert "wd.tools-consumer-ready.v1" in text
    assert "wd.tools-consumer-ready.v2" in text
    assert "conversation_surface" in text
    assert "native_parent_pid" in text
    assert "native_process_start_utc" in text
    assert "task_completion_verified" in text
    assert "Start-AgentBridgeConsumerLoop.ps1" in text
    assert text.count("'-File', $toolsLauncher") == 1


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None or os.name != "nt",
    reason="Windows PowerShell parser is unavailable",
)
def test_owned_powershell_files_parse() -> None:
    result = _run_powershell(
        f"""
$ErrorActionPreference = 'Stop'
$records = foreach ($path in @({_quote(TOOLS)}, {_quote(SUPERVISOR)})) {{
  $tokens = $null
  $errors = $null
  [void][Management.Automation.Language.Parser]::ParseFile(
    $path, [ref]$tokens, [ref]$errors
  )
  [pscustomobject]@{{ path = $path; errors = @($errors).Count }}
}}
$records | ConvertTo-Json -Compress
"""
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == [
        {"path": str(TOOLS), "errors": 0},
        {"path": str(SUPERVISOR), "errors": 0},
    ]


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None or os.name != "nt",
    reason="Windows PowerShell is unavailable",
)
def test_tools_permission_parser_is_strict_and_defaults_legacy_safely() -> None:
    result = _run_powershell(
        f"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  {_quote(TOOLS)}, [ref]$tokens, [ref]$errors
)
$functionAst = $ast.Find({{
  param($node)
  $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq 'Get-WdToolsConversationPermissions'
}}, $true)
. ([scriptblock]::Create($functionAst.Extent.Text))
$default = Get-WdToolsConversationPermissions -Tools ([pscustomobject]@{{}})
$valid = Get-WdToolsConversationPermissions -Tools ([pscustomobject]@{{
  conversation_permissions = [pscustomobject]@{{
    network_access = $true
    additional_writable_roots = @()
  }}
}})
$blocked = 0
foreach ($policy in @(
  [pscustomobject]@{{network_access='true';additional_writable_roots=@()}},
  [pscustomobject]@{{network_access=$true;additional_writable_roots='C:\\Python'}},
  [pscustomobject]@{{network_access=$true;additional_writable_roots=@(123)}},
  [pscustomobject]@{{network_access=$true;additional_writable_roots=@();extra=$true}}
)) {{
  try {{
    [void](Get-WdToolsConversationPermissions -Tools ([pscustomobject]@{{
      conversation_permissions = $policy
    }}))
  }} catch {{ $blocked++ }}
}}
[pscustomobject]@{{
  default_network = $default.NetworkAccess
  default_roots = @($default.AdditionalWritableRoots).Count
  valid_network = $valid.NetworkAccess
  valid_roots = @($valid.AdditionalWritableRoots)
  blocked = $blocked
}} | ConvertTo-Json -Compress
"""
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "default_network": False,
        "default_roots": 0,
        "valid_network": True,
        "valid_roots": [],
        "blocked": 4,
    }


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None or os.name != "nt",
    reason="Windows PowerShell is unavailable",
)
def test_v2_writer_separates_latest_terminal_from_verified_checkpoint(
    tmp_path: Path,
) -> None:
    ready = tmp_path / "tools-ready.json"
    worktree = tmp_path / "tools-worktree"
    worktree.mkdir()
    result = _run_powershell(
        f"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  {_quote(TOOLS)}, [ref]$tokens, [ref]$errors
)
foreach ($name in @(
    'ConvertTo-WdToolsUtc',
    'Get-WdToolsConversationFact',
    'Write-WdToolsConversationReadiness'
)) {{
  $functionAst = $ast.Find({{
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq $name
  }}, $true)
  . ([scriptblock]::Create($functionAst.Extent.Text))
}}
$started = (Get-Process -Id $PID).StartTime.ToUniversalTime()
$base = [ordered]@{{
  generation = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
  pid = $PID
  process_start_utc = $started.ToString('o')
  config_path = 'C:\\Python\\wd_supervisor_loop.json'
  worktree = {_quote(worktree)}
  branch = 'tools/current'
  head = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
  baseline_branch = 'tools/baseline'
  baseline_head = 'cccccccccccccccccccccccccccccccccccccccc'
  resume_policy = 'current_worktree'
  agent = 'codex-tools-1'
  agent_uuid = '7a8af68d-20bc-4598-9953-23c5dd98b102'
  role = 'tools-tests'
  model = 'gpt-5.6-terra'
  reasoning_effort = 'high'
  codex_command = 'C:\\codex.exe'
  codex_command_sha256 = ('A' * 64)
  python_executable = 'C:\\python.exe'
  python_executable_sha256 = ('B' * 64)
  conversation_code_sha256 = @{{}}
  conversation_network_access = $true
  conversation_additional_writable_roots = @()
  target_state_id = 'wd-swarm-target-state-v1'
  target_state_sha256 = ('C' * 64)
  target_state_image_path = 'C:\\target.png'
  target_state_image_sha256 = ('D' * 64)
  target_state_image_delivery = 'codex_cli_initial_image'
  target_state_image_initial_turn_only = $true
  target_state_manifested = $true
  run_id = 'wd-tools-test'
  session_id = 'wd-tools-test'
  append_canary = $true
  append_canary_task_id = 'wd-append-canary-wd-tools-test'
  append_canary_event_utc = $started.ToString('o')
  append_canary_latency_ms = 1
}}
$state = @{{
  transport_ready=$false;transport_ready_at_utc=$null;thread_id=$null
  native_pid=$null;native_parent_pid=$null;native_process_start_utc=$null
  last_turn_id=$null;last_native_turn_id=$null;last_native_status=$null
  last_turn_disposition=$null;last_turn_finalized_at_utc=$null
  native_checkpoint_verified=$false;last_checkpoint_turn_id=$null
  last_checkpoint_native_turn_id=$null;last_checkpoint_disposition=$null
  last_checkpoint_verified_at_utc=$null
}}
$facts = [pscustomobject]@{{
  agent='codex-tools-1';session_id='wd-tools-test'
  generation='aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
  worktree={_quote(worktree)}
  compact_state_path=(Join-Path {_quote(worktree)} '.codex-audit\\wd-current-state.json')
  thread_id='thread-tools';turn_id=$null;native_turn_id=$null
  native_pid=($PID + 10000);native_parent_pid=$PID
  native_process_start_utc=[DateTimeOffset]::UtcNow.ToString('o')
  owner_pid=$PID;owner_process_start_utc=$started.ToString('o')
  native_status='';disposition=$null;checkpoint_verified=$false
  model='gpt-5.6-terra';effort='high';completion_scope='native_conversation'
  task_completion_verified=$false
}}
[void](Write-WdToolsConversationReadiness -Path {_quote(ready)} `
  -BaseRecord $base -State $state -Facts $facts -Phase transport)
$transport = Get-Content -LiteralPath {_quote(ready)} -Raw | ConvertFrom-Json
$facts.turn_id = 'turn-' + ('a' * 32)
$facts.native_turn_id = 'native-1'
$facts.native_status = 'completed'
$facts.disposition = 'idle'
$facts.checkpoint_verified = $true
[void](Write-WdToolsConversationReadiness -Path {_quote(ready)} `
  -BaseRecord $base -State $state -Facts $facts -Phase terminal)
$checkpoint = Get-Content -LiteralPath {_quote(ready)} -Raw | ConvertFrom-Json
$checkpointTime = [string]$checkpoint.last_checkpoint_verified_at_utc
$facts.turn_id = 'turn-' + ('b' * 32)
$facts.native_turn_id = 'native-2'
$facts.native_status = 'completed'
$facts.disposition = 'native_chat_completed'
$facts.checkpoint_verified = $false
[void](Write-WdToolsConversationReadiness -Path {_quote(ready)} `
  -BaseRecord $base -State $state -Facts $facts -Phase terminal)
$chat = Get-Content -LiteralPath {_quote(ready)} -Raw | ConvertFrom-Json
[pscustomobject]@{{
  schema = $transport.schema
  status = $transport.status
  scope = $transport.readiness_scope
  transport = $transport.transport_ready
  transport_checkpoint = $transport.native_checkpoint_verified
  no_exit_code = $null -eq $transport.PSObject.Properties['initial_tick_exit_code']
  checkpoint_latest = $checkpoint.native_checkpoint_verified
  checkpoint_time = -not [string]::IsNullOrWhiteSpace($checkpointTime)
  chat_latest = $chat.native_checkpoint_verified
  chat_disposition = $chat.last_turn_disposition
  preserved_checkpoint = [string]$chat.last_checkpoint_verified_at_utc -ceq $checkpointTime
  task_completion = $chat.task_completion_verified
}} | ConvertTo-Json -Compress
"""
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "schema": "wd.tools-consumer-ready.v2",
        "status": "transport_ready",
        "scope": "ui_transport_only",
        "transport": True,
        "transport_checkpoint": False,
        "no_exit_code": True,
        "checkpoint_latest": True,
        "checkpoint_time": True,
        "chat_latest": False,
        "chat_disposition": "native_chat_completed",
        "preserved_checkpoint": True,
        "task_completion": False,
    }


@pytest.mark.skipif(
    WINDOWS_POWERSHELL is None or os.name != "nt",
    reason="Windows PowerShell is unavailable",
)
@pytest.mark.parametrize('native_terminal', [False, True])
def test_supervisor_v2_requires_live_native_ancestry_but_not_checkpoint(
    tmp_path: Path, native_terminal: bool,
) -> None:
    codex = tmp_path / "codex.exe"
    python = tmp_path / "python.exe"
    codex.write_bytes(b"codex-test")
    python.write_bytes(b"python-test")
    ready = tmp_path / "ready.json"
    result = _run_powershell(
        f"""
$ErrorActionPreference = 'Stop'
$tokens = $null
$errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
  {_quote(SUPERVISOR)}, [ref]$tokens, [ref]$errors
)
function Get-FileHash {{
  param([string]$LiteralPath,[string]$Algorithm='SHA256')
  $stream=[IO.File]::OpenRead($LiteralPath)
  $sha=[Security.Cryptography.SHA256]::Create()
  try {{
    [pscustomobject]@{{Hash=[BitConverter]::ToString(
      $sha.ComputeHash($stream)
    ).Replace('-','')}}
  }} finally {{ $sha.Dispose(); $stream.Dispose() }}
}}
foreach ($name in @(
    'Test-WdSupervisorJsonBooleanTrue',
    'Test-WdSupervisorJsonIntegerRange',
    'ConvertTo-SupervisorUtc',
    'Test-ToolsConversationNativeProcess',
    'Test-ToolsWrapperReadiness'
)) {{
  $functionAst = $ast.Find({{
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
      $node.Name -eq $name
  }}, $true)
  . ([scriptblock]::Create($functionAst.Extent.Text))
}}
$started = [DateTimeOffset]::UtcNow.AddSeconds(-3)
$nativeStarted = $started.AddSeconds(1)
$process = [pscustomobject]@{{ProcessId=404;CreationDate=$started}}
$script:native = [pscustomobject]@{{
  ProcessId=505;ParentProcessId=404;Name='codex.exe'
  ExecutablePath={_quote(codex)};CreationDate=$nativeStarted
}}
function Get-CimInstance {{
  param([string]$ClassName,[string]$Filter,[string]$ErrorAction)
  if ($ClassName -ne 'Win32_Process') {{
    throw 'unexpected process query'
  }}
  return $script:native
}}
$hashes = [pscustomobject]@{{
  'Invoke-WdLaneTurnLoop.ps1'=('A' * 64)
  'Show-WdOperatorConversation.ps1'=('B' * 64)
  'Invoke-WdCodexConversationLoop.ps1'=('C' * 64)
}}
$tools = [pscustomobject]@{{
  agent='codex-tools-1';conversation_surface='local_window'
  resume_policy='current_worktree';expected_branch='tools/base'
  expected_head='dddddddddddddddddddddddddddddddddddddddd'
  model='gpt-5.6-terra';reasoning_effort='high'
  worktree='C:\\Python\\tools-worktree'
}}
$validation = [pscustomobject]@{{
  codex_command={_quote(codex)}
  codex_command_sha256=(Get-FileHash -LiteralPath {_quote(codex)} -Algorithm SHA256).Hash
  python_executable={_quote(python)}
  python_executable_sha256=(Get-FileHash -LiteralPath {_quote(python)} -Algorithm SHA256).Hash
  conversation_network_access=$true
  conversation_additional_writable_roots=@()
  conversation_code_sha256=$hashes
}}
$generation = 'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'
$record = [ordered]@{{
  schema='wd.tools-consumer-ready.v2';status='transport_ready'
  readiness_scope='ui_transport_only';conversation_surface='local_window'
  transport_ready=$true;generation=$generation;pid=404
  process_start_utc=$started.ToString('o')
  config_path='C:\\Python\\wd_supervisor_loop.json'
  worktree=$tools.worktree;branch='tools/current'
  head='ffffffffffffffffffffffffffffffffffffffff'
  baseline_branch=$tools.expected_branch;baseline_head=$tools.expected_head
  resume_policy=$tools.resume_policy;agent=$tools.agent
  model=$tools.model;reasoning_effort=$tools.reasoning_effort
  codex_command=$validation.codex_command
  codex_command_sha256=$validation.codex_command_sha256
  python_executable=$validation.python_executable
  python_executable_sha256=$validation.python_executable_sha256
  conversation_network_access=$true
  conversation_additional_writable_roots=@()
  conversation_code_sha256=$hashes
  target_state_manifested=$true;target_state_id='wd-swarm-target-state-v1'
  run_id='wd-tools-ready-404';session_id='wd-tools-ready-404'
  append_canary=$true;append_canary_task_id='wd-append-canary-wd-tools-ready-404'
  append_canary_event_utc=$started.AddMilliseconds(100).ToString('o')
  append_canary_latency_ms=2
  ready_at_utc=$started.AddSeconds(2).ToString('o')
  transport_ready_at_utc=$started.AddSeconds(2).ToString('o')
  thread_id='thread-tools';native_pid=505;native_parent_pid=404
  native_process_start_utc=$nativeStarted.ToString('o')
  native_checkpoint_verified=$false;task_completion_verified=$false
  last_turn_id=$null;last_native_turn_id=$null;last_native_status=$null
  last_turn_disposition=$null;last_turn_finalized_at_utc=$null
  last_checkpoint_turn_id=$null;last_checkpoint_native_turn_id=$null
  last_checkpoint_disposition=$null;last_checkpoint_verified_at_utc=$null
}}
function Write-Ready {{
  $record | ConvertTo-Json -Depth 8 |
    Set-Content -LiteralPath {_quote(ready)} -Encoding UTF8
}}
if (${str(native_terminal).lower()}) {{
  $tools.conversation_surface='native_terminal'
  $record.schema='wd.tools-consumer-ready.v3'
  $record.status='terminal_ready'
  $record.readiness_scope='native_cli_only'
  $record.conversation_surface='native_terminal'
  $record.thread_id='01a0a07b-ca98-71e1-90cb-d588435a2d8d'
}}
Write-Ready
$exact = Test-ToolsWrapperReadiness -Process $process -Tools $tools `
  -Validation $validation -Generation $generation `
  -ConfigPath 'C:\\Python\\wd_supervisor_loop.json' `
  -ReadinessPath {_quote(ready)}
$record.native_parent_pid = 999
Write-Ready
$wrongParent = Test-ToolsWrapperReadiness -Process $process -Tools $tools `
  -Validation $validation -Generation $generation `
  -ConfigPath 'C:\\Python\\wd_supervisor_loop.json' `
  -ReadinessPath {_quote(ready)}
$record.native_parent_pid = 404
$script:native.CreationDate = $nativeStarted.AddMinutes(10)
Write-Ready
$reusedPid = Test-ToolsWrapperReadiness -Process $process -Tools $tools `
  -Validation $validation -Generation $generation `
  -ConfigPath 'C:\\Python\\wd_supervisor_loop.json' `
  -ReadinessPath {_quote(ready)}
$script:native.CreationDate = $nativeStarted
$tools.conversation_surface = 'none'
Write-Ready
$wrongMode = Test-ToolsWrapperReadiness -Process $process -Tools $tools `
  -Validation $validation -Generation $generation `
  -ConfigPath 'C:\\Python\\wd_supervisor_loop.json' `
  -ReadinessPath {_quote(ready)}
$record.schema = 'wd.tools-consumer-ready.v1'
$record.status = 'ready'
Write-Ready
$legacy = Test-ToolsWrapperReadiness -Process $process -Tools $tools `
  -Validation $validation -Generation $generation `
  -ConfigPath 'C:\\Python\\wd_supervisor_loop.json' `
  -ReadinessPath {_quote(ready)}
[pscustomobject]@{{
  exact=$exact
  wrong_parent=$wrongParent
  reused_pid=$reusedPid
  wrong_mode=$wrongMode
  legacy=$legacy
}} |
  ConvertTo-Json -Compress
"""
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {
        "exact": True,
        "wrong_parent": False,
        "reused_pid": False,
        "wrong_mode": False,
        "legacy": True,
    }
