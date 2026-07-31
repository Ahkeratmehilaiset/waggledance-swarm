#requires -Version 5.1
<#
.SYNOPSIS
    Fail-closed watchdog for bridge watchers, the Tools consumer, and drivers.

.DESCRIPTION
    In report-only mode, verifies the exact configured processes and deliberate
    merge-driver HOLD. With -Apply it starts only absent helpers and contains
    driver tasks that are running or enabled without proven non-Apply legacy
    arguments. It never enables a scheduled task and emits no synthetic bridge
    events.
#>
[CmdletBinding()]
param(
    [switch] $Apply,
    [string] $ConfigPath = '',
    [string] $LogPath = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (-not $ConfigPath) {
    $ConfigPath = Join-Path $PSScriptRoot 'wd_supervisor_loop.json'
}
$selfPid = $PID
$actions = New-Object 'System.Collections.Generic.List[string]'

function Get-RequiredText {
    param(
        [Parameter(Mandatory)] [psobject] $Object,
        [Parameter(Mandatory)] [string] $Name
    )

    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property -or [string]::IsNullOrWhiteSpace([string]$property.Value)) {
        throw "supervisor configuration is missing '$Name'"
    }
    return [string]$property.Value
}

function Read-Utf8SupervisorSnapshot {
    param([Parameter(Mandatory)] [string] $Path)

    $bytes = [IO.File]::ReadAllBytes($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $hash = [BitConverter]::ToString(
            $sha.ComputeHash($bytes)
        ).Replace('-', '')
    }
    finally {
        $sha.Dispose()
    }
    $text = [Text.Encoding]::UTF8.GetString($bytes)
    if ($text.Length -gt 0 -and $text[0] -eq [char]0xFEFF) {
        $text = $text.Substring(1)
    }
    return [pscustomobject]@{
        Hash = $hash
        Text = $text
    }
}

function Resolve-OwnBundleGeneration {
    param([Parameter(Mandatory)] [string] $ScriptRoot)

    $deploymentPath = Join-Path $ScriptRoot 'deployment-manifest.json'
    if (Test-Path -LiteralPath $deploymentPath -PathType Leaf) {
        $expectedManifestHash = [string]$env:WD_REBOOT_EXPECTED_MANIFEST_HASH
        $deploymentSnapshot = Read-Utf8SupervisorSnapshot -Path $deploymentPath
        $actualManifestHash = ([string]$deploymentSnapshot.Hash).ToUpperInvariant()
        if (
            $expectedManifestHash -cnotmatch '^[0-9A-Fa-f]{64}$' -or
            $actualManifestHash -cne $expectedManifestHash.ToUpperInvariant()
        ) {
            throw 'supervisor deployment manifest is not externally anchored'
        }
        $deployment = [string]$deploymentSnapshot.Text |
            ConvertFrom-Json -ErrorAction Stop
        $generation = ([string]$deployment.source_commit).ToLowerInvariant()
        $expectedHashProperty = $deployment.files.PSObject.Properties[
            'wd_supervisor.ps1'
        ]
        $scriptPath = Join-Path $ScriptRoot 'wd_supervisor.ps1'
        if (
            [int]$deployment.schema_version -ne 1 -or
            $generation -cnotmatch '^[0-9a-f]{40}$' -or
            [IO.Path]::GetFileName(
                [IO.Path]::GetFullPath($ScriptRoot).TrimEnd('\')
            ) -cne $generation -or
            $null -eq $expectedHashProperty -or
            (Get-FileHash -LiteralPath $scriptPath -Algorithm SHA256).Hash -cne
                [string]$expectedHashProperty.Value
        ) {
            throw 'supervisor deployment generation is not exact'
        }
        return $generation
    }

    $previousPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& git -C $ScriptRoot rev-parse HEAD 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    $generation = (@($output | ForEach-Object { [string]$_ }) -join "`n").Trim().ToLowerInvariant()
    if ($exitCode -ne 0 -or $generation -cnotmatch '^[0-9a-f]{40}$') {
        throw 'supervisor source generation is not a full Git commit'
    }
    return $generation
}

function Assert-SupervisorBundleFileIntegrity {
    param([Parameter(Mandatory)] [string] $RelativePath)

    $deploymentPath = Join-Path $PSScriptRoot 'deployment-manifest.json'
    if (-not (Test-Path -LiteralPath $deploymentPath -PathType Leaf)) {
        return
    }
    $expectedManifestHash = [string]$env:WD_REBOOT_EXPECTED_MANIFEST_HASH
    $deploymentSnapshot = Read-Utf8SupervisorSnapshot -Path $deploymentPath
    if (
        $expectedManifestHash -cnotmatch '^[0-9A-Fa-f]{64}$' -or
        [string]$deploymentSnapshot.Hash -cne
            $expectedManifestHash.ToUpperInvariant()
    ) {
        throw 'supervisor deployment manifest changed after external attestation'
    }
    $manifestRelative = $RelativePath.Replace('\', '/').TrimStart('/')
    if (
        [IO.Path]::IsPathRooted($RelativePath) -or
        $manifestRelative -match '(^|/)\.\.(/|$)'
    ) {
        throw "unsafe supervisor bundle dependency path: $RelativePath"
    }
    $deployment = [string]$deploymentSnapshot.Text |
        ConvertFrom-Json -ErrorAction Stop
    $hashProperty = $deployment.files.PSObject.Properties[$manifestRelative]
    $candidate = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $RelativePath))
    $bundlePrefix = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\'
    if (
        -not $candidate.StartsWith(
            $bundlePrefix,
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        $null -eq $hashProperty -or
        -not (Test-Path -LiteralPath $candidate -PathType Leaf) -or
        (Get-FileHash -LiteralPath $candidate -Algorithm SHA256).Hash -cne
            [string]$hashProperty.Value
    ) {
        throw "supervisor bundle dependency hash mismatch: $manifestRelative"
    }
}

function Assert-MachineToolsConfigExact {
    param([Parameter(Mandatory)] [string] $MachineConfigPath)

    Assert-SupervisorBundleFileIntegrity `
        -RelativePath 'wd_supervisor_loop.json'
    $bundledConfigPath = Join-Path $PSScriptRoot 'wd_supervisor_loop.json'
    if (
        -not (Test-Path -LiteralPath $MachineConfigPath -PathType Leaf) -or
        (Get-FileHash -LiteralPath $MachineConfigPath -Algorithm SHA256).Hash -cne
            (Get-FileHash -LiteralPath $bundledConfigPath -Algorithm SHA256).Hash
    ) {
        throw 'machine Tools config differs from the externally anchored bundle'
    }
}

function Resolve-PowerShellChildHost {
    $candidates = New-Object 'System.Collections.Generic.List[string]'
    $currentWindowsPowerShell = ''

    try {
        $current = Get-Process -Id $PID -ErrorAction Stop
        $currentHostName = [IO.Path]::GetFileName([string]$current.Path)
        if ($current.Path -and $currentHostName -match '^(?i:pwsh)\.exe$') {
            $candidates.Add($current.Path)
        }
        elseif ($current.Path -and $currentHostName -match '^(?i:powershell)\.exe$') {
            $currentWindowsPowerShell = $current.Path
        }
    }
    catch {
        $actions.Add("WARN could not inspect current PowerShell host: $($_.Exception.Message)")
    }

    foreach ($commandName in @('pwsh.exe', 'pwsh')) {
        try {
            $command = Get-Command $commandName -ErrorAction Stop |
                Where-Object {
                    $_.CommandType -eq [Management.Automation.CommandTypes]::Application
                } |
                Select-Object -First 1
            if ($null -ne $command) {
                $path = if ($command.Path) { $command.Path } else { $command.Source }
                if ($path) { $candidates.Add([string]$path) }
            }
        }
        catch {
            # Other dynamic probes below may still resolve a supported host.
        }
    }

    if ($env:ProgramFiles) {
        $powerShellRoot = Join-Path $env:ProgramFiles 'PowerShell'
        if (Test-Path -LiteralPath $powerShellRoot -PathType Container) {
            $versionDirectories = @(
                Get-ChildItem -LiteralPath $powerShellRoot -Directory -ErrorAction SilentlyContinue |
                    Sort-Object {
                        $version = [Version]'0.0'
                        [void][Version]::TryParse($_.Name, [ref]$version)
                        $version
                    } -Descending
            )
            foreach ($directory in $versionDirectories) {
                $candidates.Add((Join-Path $directory.FullName 'pwsh.exe'))
            }
        }
    }

    if ($env:SystemRoot) {
        if ($currentWindowsPowerShell) {
            $candidates.Add($currentWindowsPowerShell)
        }
        $candidates.Add(
            (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe')
        )
    }
    foreach ($commandName in @('powershell.exe', 'powershell')) {
        try {
            $command = Get-Command $commandName -ErrorAction Stop |
                Where-Object {
                    $_.CommandType -eq [Management.Automation.CommandTypes]::Application
                } |
                Select-Object -First 1
            if ($null -ne $command) {
                $path = if ($command.Path) { $command.Path } else { $command.Source }
                if ($path) { $candidates.Add([string]$path) }
            }
        }
        catch {
            # The final candidate validation below emits one fail-closed error.
        }
    }

    $seen = @{}
    foreach ($candidate in $candidates) {
        if ([string]::IsNullOrWhiteSpace($candidate)) { continue }
        try {
            $full = [IO.Path]::GetFullPath($candidate)
        }
        catch {
            continue
        }
        $key = $full.ToLowerInvariant()
        if ($seen.ContainsKey($key)) { continue }
        $seen[$key] = $true
        if (Test-Path -LiteralPath $full -PathType Leaf) {
            return $full
        }
    }

    throw 'could not dynamically resolve a supported pwsh/powershell child executable'
}

function Test-TextContains {
    param(
        [AllowEmptyString()] [string] $Text,
        [Parameter(Mandatory)] [string] $Expected
    )
    return $Text.IndexOf($Expected, [StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Test-NamedCommandLineArgument {
    param(
        [AllowEmptyString()] [string] $CommandLine,
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [string] $Value
    )

    if ([string]::IsNullOrWhiteSpace($CommandLine)) {
        return $false
    }
    $pattern = '(?i)(?:^|\s)-{0}\s+(?:"{1}"|{1})(?:\s|$)' -f
        [Regex]::Escape($Name),
        [Regex]::Escape($Value)
    return $CommandLine -match $pattern
}

function ConvertTo-SupervisorUtc {
    param([Parameter(Mandatory)] $Value)

    if ($Value -is [DateTimeOffset]) {
        return ([DateTimeOffset]$Value).ToUniversalTime()
    }
    if ($Value -is [DateTime]) {
        $dateTime = [DateTime]$Value
        if ($dateTime.Kind -eq [DateTimeKind]::Unspecified) {
            $dateTime = [DateTime]::SpecifyKind($dateTime, [DateTimeKind]::Utc)
        }
        return ([DateTimeOffset]$dateTime).ToUniversalTime()
    }
    $parsed = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse(
            [string]$Value,
            [Globalization.CultureInfo]::InvariantCulture,
            (
                [Globalization.DateTimeStyles]::AssumeUniversal -bor
                [Globalization.DateTimeStyles]::AdjustToUniversal
            ),
            [ref]$parsed
        )) {
        throw 'invalid Tools timestamp'
    }
    return $parsed.ToUniversalTime()
}

function Test-ToolsWrapperReadiness {
    param(
        [Parameter(Mandatory)] $Process,
        [Parameter(Mandatory)] $Tools,
        [Parameter(Mandatory)] [string] $Generation,
        [Parameter(Mandatory)] [string] $ConfigPath,
        [Parameter(Mandatory)] [string] $ReadinessPath
    )

    try {
        if (-not (Test-Path -LiteralPath $ReadinessPath -PathType Leaf)) {
            return $false
        }
        $record = Get-Content -LiteralPath $ReadinessPath -Raw -Encoding UTF8 |
            ConvertFrom-Json -ErrorAction Stop
        if (
            [string]$record.schema -cne 'wd.tools-consumer-ready.v1' -or
            [string]$record.generation -cne $Generation -or
            [int]$record.pid -ne [int]$Process.ProcessId -or
            [string]$record.branch -cne [string]$Tools.expected_branch -or
            [string]$record.head -cne [string]$Tools.expected_head -or
            -not ([string]$record.config_path).Equals(
                $ConfigPath,
                [StringComparison]::OrdinalIgnoreCase
            ) -or
            -not ([string]$record.worktree).Equals(
                [string]$Tools.worktree,
                [StringComparison]::OrdinalIgnoreCase
            )
        ) {
            return $false
        }
        $processCreated = ConvertTo-SupervisorUtc $Process.CreationDate
        $recordCreated = ConvertTo-SupervisorUtc $record.process_start_utc
        $readyAt = ConvertTo-SupervisorUtc $record.ready_at_utc
        return (
            [Math]::Abs(($recordCreated - $processCreated).TotalSeconds) -le 1 -and
            $readyAt -ge $recordCreated
        )
    }
    catch {
        return $false
    }
}

function Test-ToolsWrapperWithinStartupGrace {
    param(
        [Parameter(Mandatory)] $Process,
        [Parameter(Mandatory)] [int] $GraceSeconds
    )

    try {
        $age = [DateTimeOffset]::UtcNow - (
            ConvertTo-SupervisorUtc $Process.CreationDate
        )
        return $age.TotalSeconds -ge -5 -and $age.TotalSeconds -le $GraceSeconds
    }
    catch {
        return $false
    }
}

function Get-AgentCommandProcesses {
    param(
        [Parameter(Mandatory)] [object[]] $Processes,
        [Parameter(Mandatory)] [string] $ScriptName,
        [Parameter(Mandatory)] [string] $Agent
    )

    $agentPattern = '(?i)(?:^|\s)-Agent\s+(?:"{0}"|{0})(?:\s|$)' -f
        [Regex]::Escape($Agent)
    return @(
        $Processes |
            Where-Object {
                (Test-TextContains ([string]$_.CommandLine) $ScriptName) -and
                [string]$_.CommandLine -match $agentPattern
            }
    )
}

function Invoke-WithChildIdentity {
    param(
        [Parameter(Mandatory)] [string] $Agent,
        [Parameter(Mandatory)] [string] $RuntimeRoot,
        [Parameter(Mandatory)] [scriptblock] $Action
    )

    $values = @{}
    $names = @(
        'AGENT_BRIDGE_RUNTIME_ROOT',
        'AGENT_BRIDGE_AGENT',
        'AGENT_BRIDGE_RUN_ID',
        'AGENT_BRIDGE_SESSION_ID',
        'AGENT_BRIDGE_AGENT_UUID',
        'AGENT_BRIDGE_ROLE',
        'AGENT_BRIDGE_CAPABILITIES',
        'AGENT_BRIDGE_OWNER_SESSION_ID',
        'AGENT_BRIDGE_OWNER_TOKEN',
        'AGENT_BRIDGE_OWNER_PID',
        'AGENT_BRIDGE_OWNER_PROCESS_START_UTC'
    )
    foreach ($name in $names) {
        $values[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }

    try {
        foreach ($name in $names) {
            [Environment]::SetEnvironmentVariable($name, $null, 'Process')
        }
        [Environment]::SetEnvironmentVariable(
            'AGENT_BRIDGE_RUNTIME_ROOT',
            $RuntimeRoot,
            'Process'
        )
        [Environment]::SetEnvironmentVariable('AGENT_BRIDGE_AGENT', $Agent, 'Process')
        & $Action
    }
    finally {
        foreach ($name in $names) {
            [Environment]::SetEnvironmentVariable($name, $values[$name], 'Process')
        }
    }
}

function Start-DetachedPowerShell {
    param(
        [Parameter(Mandatory)] [string] $HostPath,
        [Parameter(Mandatory)] [string[]] $ArgumentList,
        [Parameter(Mandatory)] [string] $Name
    )

    $process = Start-Process `
        -FilePath $HostPath `
        -ArgumentList $ArgumentList `
        -WindowStyle Hidden `
        -PassThru `
        -ErrorAction Stop
    $actions.Add("RELAUNCHED $Name pid=$($process.Id)")
}

function Write-ToolsReplacementConflict {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [int] $RootPid,
        [Parameter(Mandatory)] [string] $Reason,
        [int[]] $ProcessIds = @()
    )

    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        [void](New-Item -ItemType Directory -Path $parent -Force -ErrorAction Stop)
    }
    $temporary = "$Path.$PID.tmp"
    $record = [ordered]@{
        schema = 'wd.tools-replacement-conflict.v1'
        created_at_utc = [DateTime]::UtcNow.ToString('o')
        supervisor_pid = $PID
        stale_root_pid = $RootPid
        process_ids = @($ProcessIds | Sort-Object -Unique)
        reason = $Reason
    }
    try {
        $record |
            ConvertTo-Json -Depth 4 |
            Set-Content -LiteralPath $temporary -Encoding UTF8
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporary -PathType Leaf) {
            Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        }
    }
}

function Throw-ToolsReplacementConflict {
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] [int] $RootPid,
        [Parameter(Mandatory)] [string] $Reason,
        [int[]] $ProcessIds = @()
    )

    try {
        Write-ToolsReplacementConflict `
            -Path $Path `
            -RootPid $RootPid `
            -Reason $Reason `
            -ProcessIds $ProcessIds
    }
    catch {
        throw (
            "$Reason; additionally failed to persist Tools replacement conflict " +
            "marker '$Path': $($_.Exception.Message)"
        )
    }
    throw "$Reason; persistent replacement conflict recorded at '$Path'"
}

function Test-ToolsLineageEdge {
    param(
        [Parameter(Mandatory)] $ParentProcess,
        [Parameter(Mandatory)] $ChildProcess
    )

    if (
        [int]$ChildProcess.ProcessId -eq [int]$ParentProcess.ProcessId -or
        [int]$ChildProcess.ParentProcessId -ne [int]$ParentProcess.ProcessId
    ) {
        return $false
    }
    $parentCreated = (
        [DateTime]$ParentProcess.CreationDate
    ).ToUniversalTime()
    $childCreated = (
        [DateTime]$ChildProcess.CreationDate
    ).ToUniversalTime()
    return $childCreated.Ticks -ge $parentCreated.Ticks
}

function Stop-VerifiedProcessTree {
    param(
        [Parameter(Mandatory)] $RootProcess,
        [Parameter(Mandatory)] [object[]] $InitialProcesses,
        [Parameter(Mandatory)] [string] $ConflictPath
    )

    $rootPid = [int]$RootProcess.ProcessId
    $initialTree = @($RootProcess)
    $initialLineage = @{ $rootPid = $RootProcess }
    do {
        $added = $false
        foreach ($candidate in $InitialProcesses) {
            $candidatePid = [int]$candidate.ProcessId
            $parentPid = [int]$candidate.ParentProcessId
            if (
                -not $initialLineage.ContainsKey($candidatePid) -and
                $initialLineage.ContainsKey($parentPid) -and
                (Test-ToolsLineageEdge `
                    -ParentProcess $initialLineage[$parentPid] `
                    -ChildProcess $candidate)
            ) {
                $initialLineage[$candidatePid] = $candidate
                $initialTree += $candidate
                $added = $true
            }
        }
    } while ($added)
    $lineageByPid = @{}
    foreach ($process in $initialTree) {
        $lineageByPid[[int]$process.ProcessId] = $process
    }

    $taskkill = Join-Path $env:SystemRoot 'System32\taskkill.exe'
    if (-not (Test-Path -LiteralPath $taskkill -PathType Leaf)) {
        Throw-ToolsReplacementConflict `
            -Path $ConflictPath `
            -RootPid $rootPid `
            -Reason "taskkill is missing: $taskkill" `
            -ProcessIds @($lineageByPid.Keys)
    }

    $deadline = (Get-Date).AddSeconds(5)
    $clearSamples = 0
    $rootKillSucceeded = $false
    $lastActiveIds = @()
    do {
        $currentProcesses = @(Get-CimInstance Win32_Process -ErrorAction Stop)
        $currentRoot = @(
            $currentProcesses |
                Where-Object { [int]$_.ProcessId -eq $rootPid }
        )
        if ($currentRoot.Count -gt 1) {
            Throw-ToolsReplacementConflict `
                -Path $ConflictPath `
                -RootPid $rootPid `
                -Reason "multiple current processes reported stale Tools PID $rootPid" `
                -ProcessIds @($rootPid)
        }
        if (-not $rootKillSucceeded -and $currentRoot.Count -eq 0) {
            Throw-ToolsReplacementConflict `
                -Path $ConflictPath `
                -RootPid $rootPid `
                -Reason (
                    'stale Tools wrapper exited before its process tree could be ' +
                    "re-attested and stopped: $rootPid"
                ) `
                -ProcessIds @($lineageByPid.Keys)
        }

        # Persist every observed lineage identity across samples. This still
        # finds a grandchild after its parent has exited and closes the common
        # wrapper-exit/orphan race before a replacement can launch.
        $currentTree = @()
        foreach ($candidate in $currentProcesses) {
            $candidatePid = [int]$candidate.ProcessId
            if (-not $lineageByPid.ContainsKey($candidatePid)) {
                continue
            }
            $expected = $lineageByPid[$candidatePid]
            $expectedCreated = (
                [DateTime]$expected.CreationDate
            ).ToUniversalTime()
            $candidateCreated = (
                [DateTime]$candidate.CreationDate
            ).ToUniversalTime()
            if ($candidateCreated.Ticks -ne $expectedCreated.Ticks) {
                Throw-ToolsReplacementConflict `
                    -Path $ConflictPath `
                    -RootPid $rootPid `
                    -Reason (
                        "stale Tools lineage PID identity changed: $candidatePid"
                    ) `
                    -ProcessIds @($candidatePid)
            }
            if (
                $candidatePid -eq $rootPid -and
                (
                    [string]$candidate.Name -cne [string]$RootProcess.Name -or
                    [string]$candidate.CommandLine -cne
                        [string]$RootProcess.CommandLine
                )
            ) {
                Throw-ToolsReplacementConflict `
                    -Path $ConflictPath `
                    -RootPid $rootPid `
                    -Reason (
                        "stale Tools PID identity changed before tree replacement: " +
                        $rootPid
                    ) `
                    -ProcessIds @($rootPid)
            }
            $currentTree += $candidate
        }
        do {
            $added = $false
            foreach ($candidate in $currentProcesses) {
                $candidatePid = [int]$candidate.ProcessId
                $parentPid = [int]$candidate.ParentProcessId
                if (
                    -not $lineageByPid.ContainsKey($candidatePid) -and
                    $lineageByPid.ContainsKey($parentPid) -and
                    (Test-ToolsLineageEdge `
                        -ParentProcess $lineageByPid[$parentPid] `
                        -ChildProcess $candidate)
                ) {
                    $lineageByPid[$candidatePid] = $candidate
                    $currentTree += $candidate
                    $added = $true
                }
            }
        } while ($added)

        $lastActiveIds = @(
            $currentTree | ForEach-Object { [int]$_.ProcessId }
        )
        if ($lastActiveIds.Count -eq 0) {
            if (-not $rootKillSucceeded) {
                Throw-ToolsReplacementConflict `
                    -Path $ConflictPath `
                    -RootPid $rootPid `
                    -Reason 'stale Tools tree cleared without a verified root-tree stop' `
                    -ProcessIds @($lineageByPid.Keys)
            }
            $clearSamples++
            if ($clearSamples -ge 2) {
                return $initialTree.Count
            }
            Start-Sleep -Milliseconds 150
            continue
        }
        $clearSamples = 0

        # Never use taskkill /T here: Windows builds that tree from untrusted
        # live ParentProcessId values and could include a PID-reuse bystander.
        # Stop only lineage members whose creation identity is re-attested
        # immediately before the individual kill.
        $killTargets = @(
            $currentTree |
                Sort-Object {
                    ([DateTime]$_.CreationDate).ToUniversalTime()
                } -Descending
        )
        foreach ($killTarget in $killTargets) {
            $killPid = [int]$killTarget.ProcessId
            $reattested = Get-CimInstance `
                -ClassName Win32_Process `
                -Filter "ProcessId=$killPid" `
                -ErrorAction SilentlyContinue
            if ($null -eq $reattested) {
                continue
            }
            $expectedCreated = (
                [DateTime]$killTarget.CreationDate
            ).ToUniversalTime()
            $reattestedCreated = (
                [DateTime]$reattested.CreationDate
            ).ToUniversalTime()
            if ($reattestedCreated.Ticks -ne $expectedCreated.Ticks) {
                Throw-ToolsReplacementConflict `
                    -Path $ConflictPath `
                    -RootPid $rootPid `
                    -Reason (
                        "stale Tools kill target PID identity changed: $killPid"
                    ) `
                    -ProcessIds @($killPid)
            }
            $previousPreference = $ErrorActionPreference
            try {
                $ErrorActionPreference = 'Continue'
                $killOutput = @(& $taskkill /PID $killPid /F 2>&1)
                $killExit = $LASTEXITCODE
            }
            finally {
                $ErrorActionPreference = $previousPreference
            }
            if ($killExit -ne 0) {
                $afterFailure = Get-CimInstance `
                    -ClassName Win32_Process `
                    -Filter "ProcessId=$killPid" `
                    -ErrorAction SilentlyContinue
                if (
                    $killPid -ne $rootPid -and
                    $null -eq $afterFailure
                ) {
                    continue
                }
                Throw-ToolsReplacementConflict `
                    -Path $ConflictPath `
                    -RootPid $rootPid `
                    -Reason (
                        "taskkill failed for stale Tools lineage PID $killPid " +
                        "with exit $killExit`: $($killOutput -join ' ')"
                    ) `
                    -ProcessIds $lastActiveIds
            }
            if ($killPid -eq $rootPid) {
                $rootKillSucceeded = $true
            }
        }
        Start-Sleep -Milliseconds 200
    } while ((Get-Date) -lt $deadline)

    Throw-ToolsReplacementConflict `
        -Path $ConflictPath `
        -RootPid $rootPid `
        -Reason (
            'stale Tools process tree did not stop before replacement deadline; ' +
            "survivors=$($lastActiveIds -join ',')"
        ) `
        -ProcessIds $lastActiveIds
}

function Test-LegacyDriverProvenNonApply {
    param(
        [Parameter(Mandatory)] $Task,
        [Parameter(Mandatory)] [string] $ExpectedScript
    )

    $taskActions = @($Task.Actions)
    if ($taskActions.Count -eq 0) { return $false }
    $escapedScript = [Regex]::Escape($ExpectedScript)
    $safeArguments = (
        '^(?i:-NoProfile\s+-ExecutionPolicy\s+Bypass\s+-File\s+' +
        '(?:"{0}"|{0})\s+-Loop\s+-PollSeconds\s+120)\s*$'
    ) -f $escapedScript
    $stablePowerShell = Join-Path $env:SystemRoot (
        'System32\WindowsPowerShell\v1.0\powershell.exe'
    )
    foreach ($taskAction in $taskActions) {
        $execute = [string]$taskAction.Execute
        $arguments = [string]$taskAction.Arguments
        if ([string]::IsNullOrWhiteSpace($execute) -or
            [string]::IsNullOrWhiteSpace($arguments)) {
            return $false
        }
        if (
            -not $execute.Equals(
                'powershell.exe',
                [StringComparison]::OrdinalIgnoreCase
            ) -and
            -not $execute.Equals(
                $stablePowerShell,
                [StringComparison]::OrdinalIgnoreCase
            )
        ) {
            return $false
        }
        if ($arguments -notmatch $safeArguments) {
            return $false
        }
    }
    return $true
}

function Get-OptionalScheduledTask {
    param([Parameter(Mandatory)] [string] $TaskName)

    try {
        return Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop
    }
    catch {
        if (
            [string]$_.FullyQualifiedErrorId -like 'CmdletizationQuery_NotFound*' -or
            $_.CategoryInfo.Category -eq [Management.Automation.ErrorCategory]::ObjectNotFound
        ) {
            return $null
        }
        throw
    }
}

function Invoke-TaskContainment {
    param(
        [Parameter(Mandatory)] [string] $TaskName,
        [Parameter(Mandatory)] [string] $Reason
    )

    $task = Get-OptionalScheduledTask -TaskName $TaskName
    if ($null -eq $task) {
        $actions.Add("WARN scheduled task '$TaskName' not found for $Reason")
        return
    }

    $isRunning = [string]$task.State -eq 'Running'
    $isEnabled = [bool]$task.Settings.Enabled
    if (-not $isRunning -and -not $isEnabled) {
        $actions.Add("HOLD verified $TaskName disabled/not-running ($Reason)")
        return
    }

    if (-not $Apply) {
        if ($isRunning) {
            $actions.Add("WOULD-STOP $TaskName ($Reason)")
        }
        if ($isEnabled) {
            $actions.Add("WOULD-DISABLE $TaskName ($Reason)")
        }
        return
    }

    if ($isRunning) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        $actions.Add("STOPPED $TaskName ($Reason)")
    }
    if ($isEnabled) {
        Disable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
        $actions.Add("DISABLED $TaskName ($Reason)")
    }
}

$configFull = [IO.Path]::GetFullPath($ConfigPath)
if (-not (Test-Path -LiteralPath $configFull -PathType Leaf)) {
    throw "supervisor configuration not found: $configFull"
}
$supervisorConfigSnapshot = Read-Utf8SupervisorSnapshot -Path $configFull
$loadedSupervisorConfigHash = [string]$supervisorConfigSnapshot.Hash
$configuration = [string]$supervisorConfigSnapshot.Text |
    ConvertFrom-Json -ErrorAction Stop
if ([string]$configuration.schema -cne 'wd.supervisor-loop.v2') {
    throw "unsupported supervisor configuration schema: $($configuration.schema)"
}
Assert-SupervisorBundleFileIntegrity -RelativePath 'wd_supervisor_loop.json'
$bundledSupervisorConfig = Join-Path $PSScriptRoot 'wd_supervisor_loop.json'
if (
    $loadedSupervisorConfigHash -cne
        (Get-FileHash -LiteralPath $bundledSupervisorConfig -Algorithm SHA256).Hash -or
    (Get-FileHash -LiteralPath $configFull -Algorithm SHA256).Hash -cne
        $loadedSupervisorConfigHash
) {
    throw 'supervisor config differs from the externally anchored bundle'
}

$runtimeRoot = [IO.Path]::GetFullPath((Get-RequiredText $configuration 'runtime_root'))
if (-not $LogPath) {
    $LogPath = Get-RequiredText $configuration 'log_path'
}
$logFull = [IO.Path]::GetFullPath($LogPath)
$logParent = Split-Path -Parent $logFull
if (-not (Test-Path -LiteralPath $logParent -PathType Container)) {
    [void](New-Item -ItemType Directory -Path $logParent -Force -ErrorAction Stop)
}

$powerShellHost = Resolve-PowerShellChildHost
$processes = @(
    Get-CimInstance Win32_Process -ErrorAction Stop |
        Where-Object {
            $_.ProcessId -ne $selfPid -and
            -not [string]::IsNullOrWhiteSpace([string]$_.CommandLine)
        }
)

if ($null -eq $configuration.tools_consumer) {
    throw 'supervisor configuration has no tools_consumer object'
}
$tools = $configuration.tools_consumer
$toolsEnabled = [bool]$tools.enabled
$toolsAgent = ''
$toolsGeneration = ''
$toolsExpectedHead = ''
$toolsLauncher = ''
$toolsConfig = ''
$toolsConflictPath = ''
if ($toolsEnabled) {
    $toolsAgent = Get-RequiredText $tools 'agent'
    $toolsExpectedHead = (Get-RequiredText $tools 'expected_head').ToLowerInvariant()
    if ($toolsExpectedHead -cnotmatch '^[0-9a-f]{40}$') {
        throw 'tools expected_head must be a full lowercase Git commit'
    }
    $toolsGeneration = Resolve-OwnBundleGeneration -ScriptRoot $PSScriptRoot
    $toolsLauncher = [IO.Path]::GetFullPath(
        (Get-RequiredText $tools 'launcher_script')
    )
    $toolsConfig = [IO.Path]::GetFullPath(
        (Get-RequiredText $tools 'config_path')
    )
    $toolsConflictPath = [IO.Path]::GetFullPath(
        (Get-RequiredText $tools 'replacement_conflict_path')
    )
    $readinessPath = [IO.Path]::GetFullPath(
        (Get-RequiredText $tools 'readiness_path')
    )
    if (
        -not (Split-Path -Parent $toolsConflictPath).Equals(
            (Split-Path -Parent $readinessPath),
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        [IO.Path]::GetExtension($toolsConflictPath) -cne '.json'
    ) {
        throw 'Tools replacement conflict marker is outside the readiness directory'
    }
    if (-not (Test-Path -LiteralPath $toolsLauncher -PathType Leaf)) {
        throw "required tools consumer launcher is missing: $toolsLauncher"
    }
    if (-not (Test-Path -LiteralPath $toolsConfig -PathType Leaf)) {
        throw "required tools consumer config is missing: $toolsConfig"
    }
    Assert-MachineToolsConfigExact -MachineConfigPath $toolsConfig
    $toolsValidationOutput = @(
        & $toolsLauncher `
            -ConfigPath $toolsConfig `
            -Generation $toolsGeneration `
            -ValidateOnly
    )
    $toolsValidation = @(
        $toolsValidationOutput |
            Where-Object {
                $_ -is [psobject] -and
                [string]$_.schema -ceq 'wd.tools-consumer-validation.v1'
            }
    )
    if ($toolsValidation.Count -ne 1) {
        throw 'Tools consumer preflight returned no exact validation record'
    }
    $toolsValidation = $toolsValidation[0]
    if (
        -not [bool]$toolsValidation.validated -or
        [string]$toolsValidation.generation -cne $toolsGeneration -or
        [string]$toolsValidation.head -cne $toolsExpectedHead -or
        -not ([string]$toolsValidation.readiness_path).Equals(
            $readinessPath,
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        -not ([string]$toolsValidation.worktree).Equals(
            [string]$tools.worktree,
            [StringComparison]::OrdinalIgnoreCase
        ) -or
        -not [bool]$toolsValidation.require_dedicated_worktree
    ) {
        throw 'Tools consumer preflight does not match supervisor generation'
    }
    if (Test-Path -LiteralPath $toolsConflictPath -PathType Leaf) {
        throw (
            'Tools replacement is blocked by persistent orphan-conflict marker: ' +
            $toolsConflictPath
        )
    }
}

if ($null -eq $configuration.watchers) {
    throw 'supervisor configuration has no watchers object'
}
$watcherRelative = Get-RequiredText $configuration.watchers 'script_relative'
if ([IO.Path]::IsPathRooted($watcherRelative)) {
    throw 'supervisor watcher script must be relative to the reboot bundle'
}
$watcherScript = [IO.Path]::GetFullPath(
    (Join-Path $PSScriptRoot $watcherRelative)
)
$bundlePrefix = [IO.Path]::GetFullPath($PSScriptRoot).TrimEnd('\') + '\'
if (-not $watcherScript.StartsWith(
        $bundlePrefix,
        [StringComparison]::OrdinalIgnoreCase
    )) {
    throw "supervisor watcher script escapes the reboot bundle: $watcherRelative"
}
$watcherAgents = @(
    @($configuration.watchers.agents) |
        ForEach-Object { [string]$_ } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
)
if ($watcherAgents.Count -eq 0) {
    throw 'supervisor watcher agent list must not be empty'
}
if (-not (Test-Path -LiteralPath $watcherScript -PathType Leaf)) {
    throw "required watcher source is missing: $watcherScript"
}
else {
    Assert-SupervisorBundleFileIntegrity -RelativePath $watcherRelative
    foreach ($agent in $watcherAgents) {
        if ($agent -cnotmatch '^[a-z][a-z0-9_-]{1,32}$') {
            throw "invalid configured watcher identity: $agent"
        }
        $allAgentWatchers = @(
            Get-AgentCommandProcesses `
                $processes `
                'Watch-Bridge.ps1' `
                $agent
        )
        $exactAgentWatchers = @(
            $allAgentWatchers |
                Where-Object {
                    (Test-TextContains ([string]$_.CommandLine) $watcherScript) -and
                    (Test-TextContains ([string]$_.CommandLine) $runtimeRoot)
                }
        )

        if ($exactAgentWatchers.Count -eq 1 -and $allAgentWatchers.Count -eq 1) {
            continue
        }
        if ($allAgentWatchers.Count -gt 0) {
            $ids = @($allAgentWatchers | ForEach-Object { [string]$_.ProcessId }) -join ','
            $actions.Add(
                "CONFLICT watcher:$agent count=$($allAgentWatchers.Count) pids=$ids; no duplicate launched"
            )
            continue
        }

        if (-not $Apply) {
            $actions.Add("WOULD-RELAUNCH watcher:$agent")
            continue
        }

        $watcherArguments = @(
            '-NoProfile',
            '-ExecutionPolicy', 'Bypass',
            '-File', $watcherScript,
            '-Agent', $agent,
            '-RuntimeRoot', $runtimeRoot
        )
        Assert-SupervisorBundleFileIntegrity -RelativePath $watcherRelative
        Invoke-WithChildIdentity $agent $runtimeRoot {
            Start-DetachedPowerShell $powerShellHost $watcherArguments "watcher:$agent"
        }
    }
}

if ($toolsEnabled) {
    $wrapperProcesses = @(
        $processes |
            Where-Object {
                [string]$_.Name -match '^(?i:powershell|pwsh)\.exe$' -and
                (Test-NamedCommandLineArgument `
                    -CommandLine ([string]$_.CommandLine) `
                    -Name 'File' `
                    -Value $toolsLauncher)
            }
    )
    $configuredWrapperProcesses = @(
        $wrapperProcesses |
            Where-Object {
                Test-NamedCommandLineArgument `
                    -CommandLine ([string]$_.CommandLine) `
                    -Name 'ConfigPath' `
                    -Value $toolsConfig
            }
    )
    $exactWrapperProcesses = @(
        $configuredWrapperProcesses |
            Where-Object {
                Test-NamedCommandLineArgument `
                    -CommandLine ([string]$_.CommandLine) `
                    -Name 'Generation' `
                    -Value $toolsGeneration
            }
    )
    $readyWrapperProcesses = @(
        $exactWrapperProcesses |
            Where-Object {
                Test-ToolsWrapperReadiness `
                    -Process $_ `
                    -Tools $tools `
                    -Generation $toolsGeneration `
                    -ConfigPath $toolsConfig `
                    -ReadinessPath $readinessPath
            }
    )
    $readyWrapperIds = @(
        $readyWrapperProcesses |
            ForEach-Object { [int]$_.ProcessId }
    )
    $startingWrapperProcesses = @(
        $exactWrapperProcesses |
            Where-Object { [int]$_.ProcessId -notin $readyWrapperIds }
    )
    $toolsStartupGraceSeconds = [int]$tools.codex_timeout_seconds + 60
    $graceWrapperProcesses = @(
        $startingWrapperProcesses |
            Where-Object {
                Test-ToolsWrapperWithinStartupGrace `
                    -Process $_ `
                    -GraceSeconds $toolsStartupGraceSeconds
            }
    )
    $graceWrapperIds = @(
        $graceWrapperProcesses |
            ForEach-Object { [int]$_.ProcessId }
    )
    $expiredWrapperProcesses = @(
        $startingWrapperProcesses |
            Where-Object { [int]$_.ProcessId -notin $graceWrapperIds }
    )
    $legacyConsumers = @(
        Get-AgentCommandProcesses `
            $processes `
            'Start-AgentBridgeConsumerLoop.ps1' `
            $toolsAgent
    )
    $toolsArguments = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', $toolsLauncher,
        '-ConfigPath', $toolsConfig,
        '-Generation', $toolsGeneration
    )

    if ($readyWrapperProcesses.Count -eq 1 -and
        $exactWrapperProcesses.Count -eq 1 -and
        $wrapperProcesses.Count -eq 1 -and
        $legacyConsumers.Count -eq 0) {
        # Healthy: readiness is bound to this PID, start time, and generation.
    }
    elseif ($graceWrapperProcesses.Count -eq 1 -and
        $exactWrapperProcesses.Count -eq 1 -and
        $wrapperProcesses.Count -eq 1 -and
        $legacyConsumers.Count -eq 0) {
        $actions.Add(
            "STARTING consumer-loop:codex-tools-1 pid=$($graceWrapperProcesses[0].ProcessId)"
        )
    }
    elseif ($wrapperProcesses.Count -eq 1 -and
        $configuredWrapperProcesses.Count -eq 1 -and
        (
            $exactWrapperProcesses.Count -eq 0 -or
            $expiredWrapperProcesses.Count -eq 1
        ) -and
        $legacyConsumers.Count -eq 0) {
        $staleProcess = $wrapperProcesses[0]
        $replacementReason = if ($expiredWrapperProcesses.Count -eq 1) {
            'expired-startup'
        } else {
            'stale-generation'
        }
        if (-not $Apply) {
            $actions.Add(
                "WOULD-REPLACE $replacementReason consumer-loop:codex-tools-1 pid=$($staleProcess.ProcessId)"
            )
        }
        else {
            $stalePid = [int]$staleProcess.ProcessId
            $stoppedCount = Stop-VerifiedProcessTree `
                -RootProcess $staleProcess `
                -InitialProcesses $processes `
                -ConflictPath $toolsConflictPath
            if ($stoppedCount -gt 0) {
                $actions.Add(
                    "STOPPED $replacementReason consumer-loop:codex-tools-1 tree=$stoppedCount pid=$stalePid"
                )
            }
            else {
                $actions.Add(
                    "STALE-EXITED consumer-loop:codex-tools-1 pid=$stalePid"
                )
            }
            Assert-MachineToolsConfigExact -MachineConfigPath $toolsConfig
            Start-DetachedPowerShell `
                $powerShellHost `
                $toolsArguments `
                'consumer-loop:codex-tools-1'
        }
    }
    elseif ($wrapperProcesses.Count -gt 0 -or $legacyConsumers.Count -gt 0) {
        $wrapperIds = @($wrapperProcesses | ForEach-Object { [string]$_.ProcessId }) -join ','
        $legacyIds = @($legacyConsumers | ForEach-Object { [string]$_.ProcessId }) -join ','
        $actions.Add(
            "CONFLICT tools consumer wrappers=$wrapperIds legacy=$legacyIds; no duplicate launched"
        )
    }
    elseif (-not $Apply) {
        $actions.Add('WOULD-RELAUNCH consumer-loop:codex-tools-1')
    }
    else {
        Assert-MachineToolsConfigExact -MachineConfigPath $toolsConfig
        Start-DetachedPowerShell `
            $powerShellHost `
            $toolsArguments `
            'consumer-loop:codex-tools-1'
    }
}

if ($null -eq $configuration.driver_containment) {
    throw 'supervisor configuration has no driver_containment object'
}
$driver = $configuration.driver_containment
$standingTaskName = Get-RequiredText $driver 'standing_task'
$legacyTaskName = Get-RequiredText $driver 'legacy_task'
$legacyScript = [IO.Path]::GetFullPath(
    (Get-RequiredText $driver 'legacy_script_path')
)

Invoke-TaskContainment $standingTaskName 'deliberate standing-driver HOLD'

$legacyTask = Get-OptionalScheduledTask -TaskName $legacyTaskName
if ($null -eq $legacyTask) {
    $actions.Add("WARN scheduled task '$legacyTaskName' not found for legacy verification")
}
else {
    if (Test-LegacyDriverProvenNonApply $legacyTask $legacyScript) {
        $actions.Add("LEGACY-NONAPPLY verified $legacyTaskName; left unchanged")
    }
    else {
        Invoke-TaskContainment $legacyTaskName 'legacy action is not proven non-Apply'
    }
}

$bridgeNote = 'bridge-last-event-age=unknown'
try {
    $eventsPath = Join-Path $runtimeRoot 'shared\events.jsonl'
    if (Test-Path -LiteralPath $eventsPath -PathType Leaf) {
        $newest = $null
        foreach ($line in (Get-Content -LiteralPath $eventsPath -Tail 80 -ErrorAction Stop)) {
            try {
                $timestamp = [string](($line | ConvertFrom-Json -ErrorAction Stop).ts_utc)
                if ($timestamp) {
                    $candidate = [DateTimeOffset]::Parse(
                        $timestamp,
                        [Globalization.CultureInfo]::InvariantCulture,
                        [Globalization.DateTimeStyles]::AssumeUniversal
                    ).UtcDateTime
                    if ($null -eq $newest -or $candidate -gt $newest) {
                        $newest = $candidate
                    }
                }
            }
            catch {
                # One concurrently appended/truncated row cannot hide valid rows.
            }
        }
        if ($null -ne $newest) {
            $ageMinutes = ([DateTime]::UtcNow - $newest).TotalMinutes
            $bridgeNote = 'bridge-last-event-age={0:N1}min' -f $ageMinutes
            if ($ageMinutes -gt 30) {
                $actions.Add("ALERT bridge quiet for $([int]$ageMinutes)min")
            }
        }
    }
}
catch {
    $actions.Add("WARN bridge freshness check failed: $($_.Exception.Message)")
}

$mode = if ($Apply) { 'APPLY' } else { 'dry-run' }
$timestampUtc = [DateTime]::UtcNow.ToString(
    'yyyy-MM-ddTHH:mm:ssZ',
    [Globalization.CultureInfo]::InvariantCulture
)
$summary = if ($actions.Count -eq 0) {
    'all configured helpers healthy; driver HOLD verified'
}
else {
    $actions -join '; '
}
$line = "[$timestampUtc] [$mode] host=$powerShellHost; $bridgeNote :: $summary"
Add-Content -LiteralPath $logFull -Value $line -Encoding UTF8
$line
