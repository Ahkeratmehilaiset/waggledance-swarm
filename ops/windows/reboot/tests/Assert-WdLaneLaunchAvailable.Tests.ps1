#requires -Version 5.1
<#
.SYNOPSIS
  Pester (3.4+) tests for the lane availability check of start-wd-agent.ps1.

.DESCRIPTION
  Covers the 2026-11-05 restore failure: a transient native claude.exe without
  launcher ancestry refused all four lanes at once with no diagnostics.
  The function under test is extracted from the real launcher source by AST,
  the process snapshot and Start-Sleep are mocked.

  Run:  powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-Pester -Path ops/windows/reboot/tests"
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$reboot = Split-Path -Parent $PSScriptRoot
$launcherPath = Join-Path $reboot 'start-wd-agent.ps1'
$fleetPath = Join-Path $reboot 'start-wd-all.ps1'
$parserPath = Join-Path $reboot 'wd_supervisor.ps1'

function Import-WdLauncherFunction {
  param([Parameter(Mandatory)] [string] $Name)
  $tokens = $null; $errors = $null
  $ast = [Management.Automation.Language.Parser]::ParseFile($launcherPath, [ref]$tokens, [ref]$errors)
  if ($errors.Count) { throw "launcher source does not parse: $($errors[0].Message)" }
  $definition = $ast.Find({ param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -ceq $Name
  }, $true)
  if (-not $definition) { throw "function $Name not found in the launcher" }
  return $definition.Extent.Text
}

. ([scriptblock]::Create((Import-WdLauncherFunction -Name 'Assert-WdLaneLaunchAvailable')))
$PSDefaultParameterValues['Assert-WdLaneLaunchAvailable:ParserSourcePath'] = $parserPath
$PSDefaultParameterValues['Assert-WdLaneLaunchAvailable:AllowUnpinnedParser'] = $true

$launcherCommand = 'powershell.exe -STA -NoProfile -ExecutionPolicy Bypass -File C:\Python\start-wd-agent.ps1 -ManifestPath C:\Python\wd-reboot-bundles\x\wd-fleet.json -Agent {0} -RunId wd-reboot-test'
$epoch = [datetime]'2026-11-05T22:40:00Z'

function New-Row {
  param(
    [int] $Id, [int] $Parent, [string] $Name, [string] $Command,
    [int] $Seconds = 0, [string] $Executable = ''
  )
  if (-not $Executable) { $Executable = 'C:\cli\' + $Name }
  return [pscustomobject]@{
    ProcessId = $Id; ParentProcessId = $Parent; Name = $Name; CommandLine = $Command
    ExecutablePath = $Executable; CreationDate = $epoch.AddSeconds($Seconds)
  }
}

$lead = [pscustomobject]@{ agent = 'codex-lead-1'; legacy_process_markers = @('start-wd-codex-lead.ps1') }
$rco1 = [pscustomobject]@{ agent = 'claude-rco-1'; legacy_process_markers = @('start-wd-claude-rco.ps1') }
$rco2 = [pscustomobject]@{ agent = 'claude-rco-2'; legacy_process_markers = @('start-wd-claude-rco2.ps1') }
$fable = [pscustomobject]@{ agent = 'fable-5'; legacy_process_markers = @('start-wd-fable-5.ps1') }
$allLanes = @($lead, $rco1, $rco2, $fable)
$currentPid = 100

function New-Baseline {
  # The fleet as it looks during a restore: four launchers, each with its own
  # native child, plus the Terminal window and this launcher (PID 100).
  return @(
    (New-Row -Id 10 -Parent 1 -Name 'WindowsTerminal.exe' -Command 'wt.exe -w new new-tab' -Seconds 0),
    (New-Row -Id $currentPid -Parent 10 -Name 'powershell.exe' -Command ($launcherCommand -f 'claude-rco-2') -Seconds 1),
    (New-Row -Id 200 -Parent 10 -Name 'powershell.exe' -Command ($launcherCommand -f 'codex-lead-1') -Seconds 1),
    (New-Row -Id 201 -Parent 200 -Name 'codex.exe' -Command 'codex.exe resume --last' -Seconds 20),
    (New-Row -Id 300 -Parent 10 -Name 'powershell.exe' -Command ($launcherCommand -f 'claude-rco-1') -Seconds 1),
    (New-Row -Id 301 -Parent 300 -Name 'claude.exe' -Command 'claude.exe --resume a' -Seconds 20),
    (New-Row -Id 400 -Parent 10 -Name 'powershell.exe' -Command ($launcherCommand -f 'fable-5') -Seconds 1),
    (New-Row -Id 401 -Parent 400 -Name 'claude.exe' -Command 'claude.exe --resume b' -Seconds 20)
  )
}

function Read-DiagnosticRecords {
  param([Parameter(Mandatory)] [string] $Path)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return @() }
  return @(Get-Content -LiteralPath $Path | Where-Object { $_ } | ForEach-Object { $_ | ConvertFrom-Json })
}

Describe 'Assert-WdLaneLaunchAvailable sibling attribution' {
  Mock Start-Sleep { }
  Mock Write-Warning { }

  It 'attributes a sibling lane claude.exe under its own launcher and does not throw' {
    $script:snapshots = [Collections.Generic.Queue[object]]::new()
    $script:snapshots.Enqueue((New-Baseline))
    Mock Get-CimInstance { $script:snapshots.Dequeue() }
    { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 3 -RecheckDelaySeconds 5 } | Should Not Throw
    Assert-MockCalled -Scope It Get-CimInstance -Exactly 1
    Assert-MockCalled -Scope It Start-Sleep -Exactly 0
  }

  It 'needs no recheck when every native process is attributed' {
    $script:snapshots = [Collections.Generic.Queue[object]]::new()
    $script:snapshots.Enqueue((New-Baseline))
    Mock Get-CimInstance { $script:snapshots.Dequeue() }
    $log = Join-Path $TestDrive 'attributed.jsonl'
    Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 3 -RecheckDelaySeconds 5 -DiagnosticsPath $log
    $records = @(Read-DiagnosticRecords -Path $log)
    $records.Count | Should Be 1
    $records[0].outcome | Should Be 'available'
    $records[0].pass | Should Be 1
    @($records[0].unattributed).Count | Should Be 0
  }
}

Describe 'Assert-WdLaneLaunchAvailable bounded recheck' {
  Mock Start-Sleep { }
  Mock Write-Warning { }

  It 'does not throw for a transient unattributed claude.exe that is gone on the second pass and logs the retry' {
    $stray = New-Row -Id 16300 -Parent 999 -Name 'claude.exe' -Command 'claude.exe --resume old' -Seconds 5
    $script:snapshots = [Collections.Generic.Queue[object]]::new()
    $script:snapshots.Enqueue(@((New-Baseline) + @($stray)))
    $script:snapshots.Enqueue((New-Baseline))
    Mock Get-CimInstance { $script:snapshots.Dequeue() }
    $log = Join-Path $TestDrive 'transient.jsonl'
    { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 3 -RecheckDelaySeconds 5 -DiagnosticsPath $log } | Should Not Throw
    Assert-MockCalled -Scope It Get-CimInstance -Exactly 2
    Assert-MockCalled -Scope It Start-Sleep -Exactly 1 -ParameterFilter { $Seconds -eq 5 }
    $records = @(Read-DiagnosticRecords -Path $log)
    $records.Count | Should Be 2
    $records[0].outcome | Should Be 'recheck_scheduled'
    $records[0].max_passes | Should Be 4
    @($records[0].unattributed).Count | Should Be 1
    $records[0].unattributed[0].pid | Should Be 16300
    $records[0].unattributed[0].identity | Should Be ('16300|claude.exe|' + ([DateTimeOffset]$stray.CreationDate).UtcTicks)
    $records[1].outcome | Should Be 'available'
    $records[1].pass | Should Be 2
  }

  It 'throws after all attempts for a persistent unattributed claude.exe with parent_missing and the ancestry chain' {
    $stray = New-Row -Id 16300 -Parent 999 -Name 'claude.exe' -Command 'claude.exe --resume old' -Seconds 5 -Executable 'C:\npm\claude.exe'
    $script:snapshots = [Collections.Generic.Queue[object]]::new()
    foreach ($i in 1..4) { $script:snapshots.Enqueue(@((New-Baseline) + @($stray))) }
    Mock Get-CimInstance { $script:snapshots.Dequeue() }
    $log = Join-Path $TestDrive 'persistent.jsonl'
    $message = ''
    try {
      Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 3 -RecheckDelaySeconds 5 -DiagnosticsPath $log
    } catch { $message = $_.Exception.Message }
    $message | Should Match '^cannot prove lane availability: unmarked native claude\.exe PID 16300 has no verified launcher ancestry; leave it running'
    $message | Should Match 'reason=parent_missing'
    $message | Should Match 'parent PID 999 missing'
    $message | Should Match 'ancestry 16300:claude\.exe'
    $message | Should Match 'executable C:\\npm\\claude\.exe'
    $message | Should Match '1 unattributed after 4 pass\(es\) with recheck 3 x 5 s'
    $message | Should Match ('diagnostics .*' + [regex]::Escape((Split-Path -Leaf $log)))
    Assert-MockCalled -Scope It Get-CimInstance -Exactly 4
    Assert-MockCalled -Scope It Start-Sleep -Exactly 3 -ParameterFilter { $Seconds -eq 5 }
    $records = @(Read-DiagnosticRecords -Path $log)
    $records.Count | Should Be 4
    ($records | ForEach-Object { $_.outcome }) -join ',' | Should Be 'recheck_scheduled,recheck_scheduled,recheck_scheduled,refused'
    $last = $records[3].unattributed[0]
    $last.break_reason | Should Be 'parent_missing'
    $last.parent_pid | Should Be 999
    $last.parent_alive | Should Be $false
    $last.ancestry | Should Be '16300:claude.exe'
    $last.executable_path | Should Be 'C:\npm\claude.exe'
    # pwsh 7 ConvertFrom-Json turns the ISO text into a local DateTime; compare the instant.
    ([DateTimeOffset]$last.creation_utc).ToUniversalTime().ToString('o') | Should Be ([DateTimeOffset]$stray.CreationDate).ToUniversalTime().ToString('o')
    $last.age_seconds | Should BeGreaterThan 0
  }

  It 'walks a chain through a live non-launcher parent and reports parent details' {
    $worker = New-Row -Id 5000 -Parent 999 -Name 'python.exe' -Command 'python.exe worker.py' -Seconds 2
    $stray = New-Row -Id 5001 -Parent 5000 -Name 'codex.exe' -Command 'codex.exe exec' -Seconds 5
    $script:snapshots = [Collections.Generic.Queue[object]]::new()
    $script:snapshots.Enqueue(@((New-Baseline) + @($worker, $stray)))
    Mock Get-CimInstance { $script:snapshots.Dequeue() }
    $message = ''
    try { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 0 } catch { $message = $_.Exception.Message }
    $message | Should Match 'unmarked native codex\.exe PID 5001'
    $message | Should Match 'reason=parent_missing'
    $message | Should Match 'parent PID 5000 python\.exe alive'
    $message | Should Match 'ancestry 5001:codex\.exe -> 5000:python\.exe'
    Assert-MockCalled -Scope It Start-Sleep -Exactly 0
  }

  It 'treats a new unattributed process that appears during the budget the same way inside the same budget' {
    $first = New-Row -Id 16300 -Parent 999 -Name 'claude.exe' -Command 'claude.exe one' -Seconds 5
    $second = New-Row -Id 16400 -Parent 999 -Name 'claude.exe' -Command 'claude.exe two' -Seconds 6
    $script:snapshots = [Collections.Generic.Queue[object]]::new()
    $script:snapshots.Enqueue(@((New-Baseline) + @($first)))
    $script:snapshots.Enqueue(@((New-Baseline) + @($second)))
    $script:snapshots.Enqueue(@((New-Baseline) + @($second)))
    Mock Get-CimInstance { $script:snapshots.Dequeue() }
    $message = ''
    try { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 2 -RecheckDelaySeconds 3 } catch { $message = $_.Exception.Message }
    $message | Should Match 'unmarked native claude\.exe PID 16400'
    Assert-MockCalled -Scope It Get-CimInstance -Exactly 3
    Assert-MockCalled -Scope It Start-Sleep -Exactly 2 -ParameterFilter { $Seconds -eq 3 }
  }

  It 'continues when the new process disappears before the budget ends' {
    $first = New-Row -Id 16300 -Parent 999 -Name 'claude.exe' -Command 'claude.exe one' -Seconds 5
    $second = New-Row -Id 16400 -Parent 999 -Name 'claude.exe' -Command 'claude.exe two' -Seconds 6
    $script:snapshots = [Collections.Generic.Queue[object]]::new()
    $script:snapshots.Enqueue(@((New-Baseline) + @($first)))
    $script:snapshots.Enqueue(@((New-Baseline) + @($second)))
    $script:snapshots.Enqueue((New-Baseline))
    Mock Get-CimInstance { $script:snapshots.Dequeue() }
    { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 3 -RecheckDelaySeconds 5 } | Should Not Throw
    Assert-MockCalled -Scope It Get-CimInstance -Exactly 3
    Assert-MockCalled -Scope It Start-Sleep -Exactly 2
  }
}

Describe 'Assert-WdLaneLaunchAvailable break reasons' {
  Mock Start-Sleep { }
  Mock Write-Warning { }

  It 'reports parent_newer_than_child for a recycled parent PID' {
    $recycledParent = New-Row -Id 999 -Parent 1 -Name 'explorer.exe' -Command 'explorer.exe' -Seconds 50
    $stray = New-Row -Id 16300 -Parent 999 -Name 'claude.exe' -Command 'claude.exe --resume old' -Seconds 5
    $script:snapshots = [Collections.Generic.Queue[object]]::new()
    $script:snapshots.Enqueue(@((New-Baseline) + @($recycledParent, $stray)))
    Mock Get-CimInstance { $script:snapshots.Dequeue() }
    $message = ''
    try { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 0 } catch { $message = $_.Exception.Message }
    $message | Should Match 'reason=parent_newer_than_child'
    $message | Should Match 'parent PID 999 explorer\.exe alive'
  }

  It 'reports missing_creation_date when a row in the chain has no CreationDate' {
    $parent = New-Row -Id 999 -Parent 1 -Name 'cmd.exe' -Command 'cmd.exe' -Seconds 0
    $parent.CreationDate = $null
    $stray = New-Row -Id 16300 -Parent 999 -Name 'claude.exe' -Command 'claude.exe --resume old' -Seconds 5
    $script:snapshots = [Collections.Generic.Queue[object]]::new()
    $script:snapshots.Enqueue(@((New-Baseline) + @($parent, $stray)))
    Mock Get-CimInstance { $script:snapshots.Dequeue() }
    $message = ''
    try { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 0 } catch { $message = $_.Exception.Message }
    $message | Should Match 'reason=missing_creation_date'
  }

  It 'reports no_launcher_in_chain when the chain reaches the process root' {
    $root = New-Row -Id 4 -Parent 0 -Name 'System' -Command '' -Seconds 0
    $stray = New-Row -Id 16300 -Parent 4 -Name 'claude.exe' -Command 'claude.exe --resume old' -Seconds 5
    $script:snapshots = [Collections.Generic.Queue[object]]::new()
    $script:snapshots.Enqueue(@((New-Baseline) + @($root, $stray)))
    Mock Get-CimInstance { $script:snapshots.Dequeue() }
    $message = ''
    try { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 0 } catch { $message = $_.Exception.Message }
    $message | Should Match 'reason=no_launcher_in_chain'
    $message | Should Match 'ancestry 16300:claude\.exe -> 4:System'
  }

  It 'reports cycle when the parent chain loops' {
    $a = New-Row -Id 7000 -Parent 7001 -Name 'cmd.exe' -Command 'cmd.exe' -Seconds 0
    $b = New-Row -Id 7001 -Parent 7000 -Name 'cmd.exe' -Command 'cmd.exe' -Seconds 0
    $stray = New-Row -Id 16300 -Parent 7000 -Name 'claude.exe' -Command 'claude.exe --resume old' -Seconds 5
    $script:snapshots = [Collections.Generic.Queue[object]]::new()
    $script:snapshots.Enqueue(@((New-Baseline) + @($a, $b, $stray)))
    Mock Get-CimInstance { $script:snapshots.Dequeue() }
    $message = ''
    try { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 0 } catch { $message = $_.Exception.Message }
    $message | Should Match 'reason=cycle'
  }
}

Describe 'Assert-WdLaneLaunchAvailable immediate refusals never recheck' {
  Mock Start-Sleep { }
  Mock Write-Warning { }

  It 'refuses a live launcher of the same lane at once' {
    $script:snapshots = [Collections.Generic.Queue[object]]::new()
    $script:snapshots.Enqueue((New-Baseline))
    Mock Get-CimInstance { $script:snapshots.Dequeue() }
    { Assert-WdLaneLaunchAvailable -Lane $rco1 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 3 -RecheckDelaySeconds 5 } | Should Throw "lane 'claude-rco-1' already has a live launcher (PID 300)"
    Assert-MockCalled -Scope It Get-CimInstance -Exactly 1
    Assert-MockCalled -Scope It Start-Sleep -Exactly 0
  }

  It 'refuses a same-lane native child under a legacy launcher marker at once' {
    $rows = @(
      (New-Row -Id 600 -Parent 10 -Name 'powershell.exe' -Command 'powershell.exe -File "C:\Python\start-wd-fable-5.ps1"' -Seconds 1),
      (New-Row -Id 601 -Parent 600 -Name 'claude.exe' -Command 'claude.exe --resume z' -Seconds 20)
    )
    $script:snapshots = [Collections.Generic.Queue[object]]::new()
    $script:snapshots.Enqueue($rows)
    Mock Get-CimInstance { $script:snapshots.Dequeue() }
    { Assert-WdLaneLaunchAvailable -Lane $fable -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 3 -RecheckDelaySeconds 5 } | Should Throw "already has a live launcher (PID 600)"
    Assert-MockCalled -Scope It Start-Sleep -Exactly 0
  }

  It 'refuses a native CLI owned by the same lane (owns-native path) at once' {
    # The owns-native refusal is reachable through a launcher that is an owner
    # but not a same-lane launcher by argv: the Tools consumer loop. Its native
    # codex child refuses a codex-tools-1 launch immediately, without recheck.
    $tools = [pscustomobject]@{ agent = 'codex-tools-1'; legacy_process_markers = @() }
    $rows = @((New-Baseline) + @(
      (New-Row -Id 500 -Parent 10 -Name 'powershell.exe' -Command 'powershell.exe -NoProfile -File C:\Python\start-wd-tools-consumer.ps1' -Seconds 1),
      (New-Row -Id 501 -Parent 500 -Name 'codex.exe' -Command 'codex.exe exec --json' -Seconds 20)
    ))
    $script:snapshots = [Collections.Generic.Queue[object]]::new()
    $script:snapshots.Enqueue($rows)
    Mock Get-CimInstance { $script:snapshots.Dequeue() }
    { Assert-WdLaneLaunchAvailable -Lane $tools -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 3 -RecheckDelaySeconds 5 } | Should Throw "lane 'codex-tools-1' already owns native CLI PID 501"
    Assert-MockCalled -Scope It Get-CimInstance -Exactly 1
    Assert-MockCalled -Scope It Start-Sleep -Exactly 0
  }
}

Describe 'Assert-WdLaneLaunchAvailable explicit external attribution is unchanged' {
  Mock Start-Sleep { }
  Mock Write-Warning { }

  It 'accepts an ExternalSessions entry that matches the exact process lifetime and nothing looser' {
    $stray = New-Row -Id 16300 -Parent 999 -Name 'claude.exe' -Command 'claude.exe --resume old' -Seconds 5
    $entry = [pscustomobject]@{
      pid = 16300; name = 'claude.exe'; command_line = $stray.CommandLine
      executable_path = $stray.ExecutablePath; process_start_utc = ([DateTimeOffset]$stray.CreationDate).ToString('o')
    }
    Mock Get-CimInstance { @((New-Baseline) + @($stray)) }
    { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -ExternalSessions @($entry) -RecheckAttempts 0 } | Should Not Throw
    $reused = $entry.PSObject.Copy()
    $reused.process_start_utc = ([DateTimeOffset]$stray.CreationDate).AddSeconds(1).ToString('o')
    { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -ExternalSessions @($reused) -RecheckAttempts 0 } | Should Throw 'has no verified launcher ancestry'
    Assert-MockCalled -Scope It Start-Sleep -Exactly 0
  }

  It 'accepts a native child of an approved native_parent and refuses a different child command' {
    $worker = New-Row -Id 5000 -Parent 999 -Name 'python.exe' -Command 'C:\external\python.exe worker.py' -Seconds 2 -Executable 'C:\external\python.exe'
    $child = New-Row -Id 5001 -Parent 5000 -Name 'codex.exe' -Command 'C:\cli\codex.exe -s read-only exec task' -Seconds 5
    $approval = [pscustomobject]@{
      kind = 'native_parent'; pid = 5000; name = 'python.exe'; command_line = $worker.CommandLine
      executable_path = $worker.ExecutablePath; process_start_utc = ([DateTimeOffset]$worker.CreationDate).ToString('o')
      native_child_name = 'codex.exe'; native_child_executable_path = $child.ExecutablePath
      native_child_command_prefix = 'C:\cli\codex.exe -s read-only exec '
    }
    Mock Get-CimInstance { @((New-Baseline) + @($worker, $child)) }
    { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -ExternalSessions @($approval) -RecheckAttempts 0 } | Should Not Throw
    $child.CommandLine = 'C:\cli\codex.exe -s workspace-write exec task'
    { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -ExternalSessions @($approval) -RecheckAttempts 0 } | Should Throw 'unmarked native codex.exe PID 5001'
  }
}

Describe 'Assert-WdLaneLaunchAvailable recheck budget and configuration' {
  Mock Start-Sleep { }
  Mock Write-Warning { }

  It 'refuses a configuration whose total wait exceeds 30 seconds before reading any process' {
    Mock Get-CimInstance { throw 'must not be called' }
    { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 7 -RecheckDelaySeconds 5 } | Should Throw 'recheck configuration is invalid'
    { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 1 -RecheckDelaySeconds 31 } | Should Throw 'recheck configuration is invalid'
    { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts -1 -RecheckDelaySeconds 5 } | Should Throw 'recheck configuration is invalid'
    Assert-MockCalled -Scope It Get-CimInstance -Exactly 0
  }

  It 'never waits longer than attempts x delay (30 s at the limit)' {
    $stray = New-Row -Id 16300 -Parent 999 -Name 'claude.exe' -Command 'claude.exe --resume old' -Seconds 5
    Mock Get-CimInstance { @((New-Baseline) + @($stray)) }
    $script:waited = 0
    Mock Start-Sleep { $script:waited += $Seconds }
    { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 6 -RecheckDelaySeconds 5 } | Should Throw 'has no verified launcher ancestry'
    $script:waited | Should Be 30
    Assert-MockCalled -Scope It Get-CimInstance -Exactly 7
  }

  It 'takes the launcher configuration from the script scope when no parameter is given, and rechecks never without it' {
    $stray = New-Row -Id 16300 -Parent 999 -Name 'claude.exe' -Command 'claude.exe --resume old' -Seconds 5
    Mock Get-CimInstance { @((New-Baseline) + @($stray)) }
    { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid } | Should Throw 'with recheck 0 x 5 s'
    Assert-MockCalled -Scope It Get-CimInstance -Exactly 1
    $script:WdLaneAvailabilityRecheckAttempts = 2
    $script:WdLaneAvailabilityRecheckDelaySeconds = 4
    try {
      { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid } | Should Throw 'with recheck 2 x 4 s'
      Assert-MockCalled -Scope It Start-Sleep -Exactly 2 -ParameterFilter { $Seconds -eq 4 }
    } finally {
      Remove-Variable -Name WdLaneAvailabilityRecheckAttempts -Scope Script
      Remove-Variable -Name WdLaneAvailabilityRecheckDelaySeconds -Scope Script
    }
  }

  It 'keeps every byte of the command line out of the diagnostics, the warning and the refusal (secret at the start)' {
    # Lead finding 2026-11-06: a 120-character prefix still leaks a token that
    # sits near the beginning of the command line. Only the length is recorded.
    $secret = 'claude.exe --token SECRETVALUE --resume old ' + ('x' * 150)
    $stray = New-Row -Id 16300 -Parent 999 -Name 'claude.exe' -Command $secret -Seconds 5
    Mock Get-CimInstance { @((New-Baseline) + @($stray)) }
    $script:warnings = @()
    Mock Write-Warning { $script:warnings += [string]$Message }
    $log = Join-Path $TestDrive 'prefix.jsonl'
    $message = ''
    try { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 1 -DiagnosticsPath $log } catch { $message = $_.Exception.Message }
    $message | Should Match 'has no verified launcher ancestry'
    $message | Should Not Match 'SECRETVALUE'
    $message | Should Not Match '--resume'
    ($script:warnings -join "`n") | Should Not Match 'SECRETVALUE'
    $text = Get-Content -LiteralPath $log -Raw
    $text | Should Not Match 'SECRETVALUE'
    $text | Should Not Match '--resume'
    $text | Should Not Match 'claude\.exe --'
    $passes = @(Read-DiagnosticRecords -Path $log)
    $passes.Count | Should Be 2
    foreach ($pass in $passes) {
      @($pass.unattributed).Count | Should Be 1
      foreach ($record in @($pass.unattributed)) {
        @($record.PSObject.Properties | Where-Object { $_.Name -imatch 'command' }).Name | Should Be @('command_line_length')
        $record.command_line_length | Should Be $secret.Length
      }
    }
  }

  It 'reports the per-pass record on the verbose stream when no diagnostics path is given and -Verbose is set' {
    $stray = New-Row -Id 16300 -Parent 999 -Name 'claude.exe' -Command 'claude.exe --token SECRETVALUE' -Seconds 5
    Mock Get-CimInstance { @((New-Baseline) + @($stray)) }
    # The refusal throws after the record is written, so collect the stream as it
    # flows instead of assigning the whole pipeline result.
    $script:verboseLines = @()
    try {
      Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 0 -Verbose 4>&1 |
        ForEach-Object { if ($_ -is [Management.Automation.VerboseRecord]) { $script:verboseLines += [string]$_.Message } }
    } catch { }
    $lines = @($script:verboseLines)
    @($lines | Where-Object { $_ -match '"schema":"wd\.lane-availability-check\.v1"' }).Count | Should Be 1
    ($lines -join "`n") | Should Not Match 'SECRETVALUE'
    ($lines -join "`n") | Should Match '"command_line_length":30'
  }

  It 'redacts the user name of a profile-rooted executable path in the record and the refusal, and keeps other paths whole' {
    # Lead pre-review of 5524038b: a native CLI usually lives under the user
    # profile, so the full executable path names the Windows user.
    $stray = New-Row -Id 16300 -Parent 999 -Name 'claude.exe' -Command 'claude.exe --resume old' -Seconds 5 -Executable 'C:\Users\someone\AppData\Roaming\npm\claude.exe'
    $other = New-Row -Id 16301 -Parent 999 -Name 'codex.exe' -Command 'codex.exe' -Seconds 6 -Executable 'D:\tools\codex\codex.exe'
    Mock Get-CimInstance { @((New-Baseline) + @($stray, $other)) }
    $log = Join-Path $TestDrive 'redact.jsonl'
    $message = ''
    try { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 0 -DiagnosticsPath $log } catch { $message = $_.Exception.Message }
    $message | Should Match 'has no verified launcher ancestry'
    $message | Should Not Match 'someone'
    $message | Should Match ([regex]::Escape('C:\Users\<redacted>\AppData\Roaming\npm\claude.exe'))
    if ($log.StartsWith($env:USERPROFILE, [StringComparison]::OrdinalIgnoreCase)) {
      # The diagnostics path named in the refusal is redacted too (own profile root).
      $message | Should Not Match ([regex]::Escape($env:USERPROFILE))
      $message | Should Match 'diagnostics <userprofile>'
    }
    $text = Get-Content -LiteralPath $log -Raw
    $text | Should Not Match 'someone'
    $records = @((Read-DiagnosticRecords -Path $log)[0].unattributed)
    @($records | Where-Object { $_.pid -eq 16300 })[0].executable_path | Should Be 'C:\Users\<redacted>\AppData\Roaming\npm\claude.exe'
    @($records | Where-Object { $_.pid -eq 16301 })[0].executable_path | Should Be 'D:\tools\codex\codex.exe'
  }

  It 'redacts the actual user profile root even when it is not under <drive>:\Users' {
    # A relocated profile (ProfilesDirectory elsewhere) is covered through the
    # launcher's own USERPROFILE, which is the same account that runs the lanes.
    $profileExe = Join-Path $env:USERPROFILE 'tools\claude.exe'
    $stray = New-Row -Id 16300 -Parent 999 -Name 'claude.exe' -Command 'claude.exe --resume old' -Seconds 5 -Executable $profileExe
    Mock Get-CimInstance { @((New-Baseline) + @($stray)) }
    $log = Join-Path $TestDrive 'profile.jsonl'
    $message = ''
    try { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 0 -DiagnosticsPath $log } catch { $message = $_.Exception.Message }
    $message | Should Match 'has no verified launcher ancestry'
    $message | Should Not Match ([regex]::Escape($env:USERPROFILE))
    $record = (Read-DiagnosticRecords -Path $log)[0].unattributed[0]
    $record.executable_path | Should Be ('<userprofile>' + [IO.Path]::DirectorySeparatorChar + 'tools' + [IO.Path]::DirectorySeparatorChar + 'claude.exe')
    (Get-Content -LiteralPath $log -Raw) | Should Not Match ([regex]::Escape($env:USERPROFILE))
  }

  It 'reports a failed diagnostics write with safe content only: no path, no exception text, verdict unchanged' {
    # Lead finding 2026-11-06 06:19Z: the write-failure warning interpolated the
    # raw diagnostics path and the exception message.
    $stray = New-Row -Id 16300 -Parent 999 -Name 'claude.exe' -Command 'claude.exe --resume old' -Seconds 5
    Mock Get-CimInstance { @((New-Baseline) + @($stray)) }
    $script:warnings = @()
    Mock Write-Warning { $script:warnings += [string]$Message }
    $blocker = Join-Path $TestDrive 'blocker.txt'
    Set-Content -LiteralPath $blocker -Value 'a file where a directory is needed'
    $log = Join-Path (Join-Path $blocker 'SECRETMARKER') 'diag.jsonl'
    $message = ''
    try { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 0 -DiagnosticsPath $log } catch { $message = $_.Exception.Message }
    $message | Should Match 'has no verified launcher ancestry'
    Test-Path -LiteralPath $log | Should Be $false
    $failed = @($script:warnings | Where-Object { $_ -match 'diagnostics could not be written' })
    $failed.Count | Should Be 1
    $failed[0] | Should Not Match 'SECRETMARKER'
    $failed[0] | Should Not Match 'blocker'
    $failed[0] | Should Not Match ([regex]::Escape($env:USERPROFILE))
    $failed[0] | Should Match 'Exception'
  }

  It 'writes no file when no diagnostics path is given (the DryRun contract) and still refuses with the full reason' {
    $stray = New-Row -Id 16300 -Parent 999 -Name 'claude.exe' -Command 'claude.exe --token SECRETVALUE' -Seconds 5
    Mock Get-CimInstance { @((New-Baseline) + @($stray)) }
    $before = @(Get-ChildItem -LiteralPath $TestDrive -Recurse -Force).Count
    $message = ''
    try { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 0 } catch { $message = $_.Exception.Message }
    $message | Should Match 'has no verified launcher ancestry'
    $message | Should Not Match 'diagnostics '
    $message | Should Not Match 'SECRETVALUE'
    @(Get-ChildItem -LiteralPath $TestDrive -Recurse -Force).Count | Should Be $before
  }
}

Describe 'fleet preflight and launcher wiring (static)' {
  $agentText = Get-Content -LiteralPath $launcherPath -Raw
  $fleetText = Get-Content -LiteralPath $fleetPath -Raw

  It 'exposes -CheckLaunchAvailability on the lane launcher, DryRun only' {
    $agentText | Should Match '\[switch\] \$CheckLaunchAvailability,'
    $agentText | Should Match "if \(\`$CheckLaunchAvailability -and -not \`$DryRun\) \{\s*throw 'CheckLaunchAvailability requires -DryRun'"
  }

  It 'runs the availability check in the DryRun block when the switch is set' {
    $dryRun = $agentText.IndexOf("if (`$DryRun) {`r`n  # The fleet preflight asks for the native-process availability check on every`r`n  # lane (-CheckLaunchAvailability); managed admission implies it.`r`n  `$checkLaunchAvailabilityNow = [bool]`$CheckLaunchAvailability`r`n  if (`$CheckManagedAdmission -and `$turnMode -ceq 'managed') { `$checkLaunchAvailabilityNow = `$true }`r`n  if (`$checkLaunchAvailabilityNow) {")
    if ($dryRun -lt 0) {
      $dryRun = $agentText.IndexOf("if (`$DryRun) {`n  # The fleet preflight asks for the native-process availability check on every`n  # lane (-CheckLaunchAvailability); managed admission implies it.`n  `$checkLaunchAvailabilityNow = [bool]`$CheckLaunchAvailability`n  if (`$CheckManagedAdmission -and `$turnMode -ceq 'managed') { `$checkLaunchAvailabilityNow = `$true }`n  if (`$checkLaunchAvailabilityNow) {")
    }
    $dryRun | Should BeGreaterThan 0
    $check = $agentText.IndexOf('Assert-WdLaneLaunchAvailable -Lane $lane', $dryRun)
    $return = $agentText.IndexOf('return [pscustomobject]@{', $dryRun)
    ($check -gt $dryRun -and $check -lt $return) | Should Be $true
  }

  It 'asks for the availability check from the whole-fleet preflight before any lane is started' {
    $fleetText | Should Match '\$resumePlan = & \$agentLauncherTarget -Agent \(\[string\]\$lane\.agent\) -DryRun -CheckLaunchAvailability'
    $fleetText.IndexOf('-DryRun -CheckLaunchAvailability') | Should BeLessThan $fleetText.IndexOf("Write-Host '=== whole-fleet preflight passed ==='")
  }

  It 'keeps DryRun byte-inert: the diagnostics file path is set only for a live launch' {
    $agentText | Should Match "\`$script:WdLaneAvailabilityDiagnosticsPath = if \(\`$DryRun\) \{ '' \} else \{ Join-Path \(Join-Path \`$runtimeRoot 'lane_availability'\) \('\{0\}\.jsonl' -f \`$Agent\) \}"
    $agentText | Should Not Match "\`$script:WdLaneAvailabilityDiagnosticsPath = Join-Path"
  }

  It 'defaults the recheck budget to 3 x 5 s and bounds it to 30 s' {
    $agentText | Should Match '\[ValidateRange\(0, 30\)\] \[int\] \$LaneAvailabilityRecheckAttempts = 3,'
    $agentText | Should Match '\[ValidateRange\(1, 30\)\] \[int\] \$LaneAvailabilityRecheckDelaySeconds = 5,'
    $agentText | Should Match 'LaneAvailabilityRecheckAttempts x LaneAvailabilityRecheckDelaySeconds must not exceed 30 seconds'
  }
}

Describe 'Assert-WdLaneLaunchAvailable host identity change, depth limit and renamed executables' {
  Mock Start-Sleep { }
  Mock Write-Warning { }

  It 'refuses a PowerShell host whose identity changed between the snapshot and the fresh read, without recheck' {
    # A host row without a readable command line is re-read once by PID; a
    # different CreationDate on the fresh row is an identity change and must
    # throw immediately (never rechecked, never waived).
    $blank = New-Row -Id 700 -Parent 10 -Name 'powershell.exe' -Command '' -Seconds 3
    $recycled = New-Row -Id 700 -Parent 10 -Name 'powershell.exe' -Command 'powershell.exe -NoProfile' -Seconds 4
    $script:snapshots = [Collections.Generic.Queue[object]]::new()
    $script:snapshots.Enqueue(@((New-Baseline) + @($blank)))
    $script:snapshots.Enqueue(@($recycled))
    Mock Get-CimInstance { $script:snapshots.Dequeue() }
    { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 3 -RecheckDelaySeconds 5 } | Should Throw 'PowerShell command line is unavailable or its identity changed'
    Assert-MockCalled -Scope It Get-CimInstance -Exactly 2
    Assert-MockCalled -Scope It Start-Sleep -Exactly 0
  }

  It 'reports depth_limit when a launcher sits beyond 64 ancestors' {
    $rows = @(New-Baseline)
    # native 9000 -> 9001 -> ... -> 9070 -> launcher 300 (claude-rco-1); each
    # ancestor is older than its child, so only the depth limit stops the walk.
    for ($i = 0; $i -le 70; $i++) {
      $parent = if ($i -eq 70) { 300 } else { 9001 + $i }
      $name = if ($i -eq 0) { 'claude.exe' } else { 'conhost.exe' }
      $rows += New-Row -Id (9000 + $i) -Parent $parent -Name $name -Command ($name + ' x') -Seconds (200 - $i)
    }
    $script:snapshots = [Collections.Generic.Queue[object]]::new()
    $script:snapshots.Enqueue($rows)
    Mock Get-CimInstance { $script:snapshots.Dequeue() }
    $message = ''
    try { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 0 } catch { $message = $_.Exception.Message }
    $message | Should Match 'unmarked native claude\.exe PID 9000 '
    $message | Should Match 'reason=depth_limit'
    $message | Should Match 'ancestry 9000:claude\.exe -> 9001:conhost\.exe -> '
  }

  It 'reports a renamed native executable (old-file suffix) like the installed name' {
    $stray = New-Row -Id 16500 -Parent 999 -Name 'codex.exe.old.3' -Command 'codex.exe.old.3 exec' -Seconds 5
    $script:snapshots = [Collections.Generic.Queue[object]]::new()
    $script:snapshots.Enqueue(@((New-Baseline) + @($stray)))
    Mock Get-CimInstance { $script:snapshots.Dequeue() }
    { Assert-WdLaneLaunchAvailable -Lane $rco2 -KnownLanes $allLanes -CurrentPid $currentPid -RecheckAttempts 0 } | Should Throw 'unmarked native codex.exe.old.3 PID 16500 has no verified launcher ancestry; leave it running (reason=parent_missing'
  }
}
