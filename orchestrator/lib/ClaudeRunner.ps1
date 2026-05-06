# ClaudeRunner.ps1
# Phase 1.6: process-owned Claude Code execution.
#
# Key changes:
#  - Real-time monitoring loop while the child runs. Polls stdout/stderr
#    tail every PollIntervalSeconds. If an interactive prompt regex hits,
#    the runner returns NEEDS_MANUAL_ACTION early instead of waiting for
#    the global timeout.
#  - Environment is sanitized via System.Diagnostics.Process and a
#    deterministic env-map (no implicit inheritance of secrets).
#  - Atomic args building. No prompt or arg ever contains a secret.
#
# Compatible with PowerShell 5.1.

Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'EnvSanitize.ps1')

function Build-ClaudeArgs {
    [CmdletBinding()]
    param(
        [string]   $Model,
        [ValidateSet('text','json','stream-json')] [string] $OutputFormat = 'text',
        [int]      $MaxTurns,
        [string]   $PermissionMode,
        [string[]] $AllowedTools,
        [string[]] $DisallowedTools,
        [string]   $DebugFile,
        [bool]     $DangerouslySkipPermissions = $false,
        [string]   $McpConfigFile = ''
    )
    $argList = @('-p')
    if ($Model)              { $argList += @('--model', $Model) }
    if ($OutputFormat)       { $argList += @('--output-format', $OutputFormat) }
    if ($MaxTurns -gt 0)     { $argList += @('--max-turns', "$MaxTurns") }
    if ($PermissionMode)     { $argList += @('--permission-mode', $PermissionMode) }
    if ($AllowedTools)       { $argList += @('--allowed-tools', ($AllowedTools -join ',')) }
    if ($DisallowedTools)    { $argList += @('--disallowed-tools', ($DisallowedTools -join ',')) }
    if ($DebugFile)          { $argList += @('--debug-file', $DebugFile) }
    if ($McpConfigFile)      { $argList += @('--mcp-config', $McpConfigFile) }
    if ($DangerouslySkipPermissions) {
        Write-Warning '--dangerously-skip-permissions is ENABLED.'
        $argList += '--dangerously-skip-permissions'
    }
    return ,$argList
}

function Format-CommandLine {
    [CmdletBinding()]
    param([string] $Executable, [string[]] $ArgList)
    $quoted = $ArgList | ForEach-Object {
        if ($_ -match '\s') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
    }
    return "$Executable " + ($quoted -join ' ')
}

function _IsWindowsHost {
    if ($null -ne (Get-Variable -Name IsWindows -ErrorAction SilentlyContinue)) { return [bool]$IsWindows }
    if ($env:OS -eq 'Windows_NT') { return $true }
    return $false
}

function Stop-ProcessTree {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [int] $ProcessId)
    if (_IsWindowsHost) {
        try { & taskkill /F /T /PID $ProcessId 2>$null | Out-Null } catch {}
    } else {
        try { & kill -TERM $ProcessId 2>/dev/null } catch {}
        Start-Sleep -Milliseconds 500
        try { & kill -KILL $ProcessId 2>/dev/null } catch {}
    }
}

function _ReadTailSafe {
    [CmdletBinding()]
    param([string] $Path, [int] $Lines = 80)
    if (-not (Test-Path $Path)) { return @() }
    try { return @(Get-Content -Path $Path -Tail $Lines -ErrorAction Stop) } catch { return @() }
}

function _MatchesAnyPattern {
    [CmdletBinding()]
    param([string[]] $Lines, [string[]] $Patterns)
    if (-not $Lines -or $Lines.Count -eq 0) { return $null }
    if (-not $Patterns -or $Patterns.Count -eq 0) { return $null }
    $startIdx = [Math]::Max(0, $Lines.Count - 25)
    $tail = ($Lines[$startIdx..($Lines.Count - 1)] -join "`n")
    foreach ($p in $Patterns) {
        if ($tail -match $p) { return $p }
    }
    return $null
}

function Invoke-ClaudeCodePrint {
    <#
    .SYNOPSIS
    Spawns `claude -p` as an owned subprocess with sanitised environment,
    monitors stdout/stderr in near-realtime for interactive prompts, and
    returns a result object describing what happened.

    .PARAMETER InteractivePromptPatterns
    Regex list. If matched against the tail of stdout or stderr while the
    process is alive, the process is killed and result.early_status is
    set to NEEDS_MANUAL_ACTION.

    .PARAMETER PollIntervalSeconds
    How often to read tails and check the process state.

    .PARAMETER SanitizeEnvironment
    If true (recommended), the child is launched with a sanitised env map.
    Variables matching EnvDenylistPatterns are stripped.

    .OUTPUTS
    pscustomobject with fields:
      early_status, early_status_reason, early_status_match,
      process_exited, exit_code, timed_out, killed_for_interactive,
      elapsed_seconds, command_line, stdout_path, stderr_path, pid,
      started_at, ended_at, env_stripped (names only).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $ClaudeCommand,
        [Parameter(Mandatory)] [string] $PromptFile,
        [Parameter(Mandatory)] [string] $StdoutFile,
        [Parameter(Mandatory)] [string] $StderrFile,
        [Parameter(Mandatory)] [string] $WorkingDirectory,
        [Parameter(Mandatory)] [int]    $TimeoutSeconds,
        [string[]] $ArgList = @(),
        [string[]] $InteractivePromptPatterns = @(),
        [int]      $PollIntervalSeconds = 3,
        [bool]     $SanitizeEnvironment = $true,
        [string[]] $EnvDenylistPatterns = $null,
        [string[]] $EnvAllowList        = @(),
        [bool]     $KillOnInteractivePrompt = $true
    )

    if (-not (Test-Path $PromptFile))      { throw "Prompt file not found: $PromptFile" }
    if (-not (Test-Path $WorkingDirectory)){ throw "Working directory not found: $WorkingDirectory" }
    foreach ($p in @($StdoutFile, $StderrFile)) {
        $d = Split-Path -Parent $p
        if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
    }

    $cmdLine = Format-CommandLine -Executable $ClaudeCommand -ArgList $ArgList

    # Resolve the executable -- ProcessStartInfo wants the path or a name resolvable on PATH.
    $resolved = Get-Command $ClaudeCommand -ErrorAction SilentlyContinue
    if (-not $resolved) { throw "Cannot resolve Claude executable: $ClaudeCommand" }

    # Process.Start cannot directly run .ps1 files. If the resolved command is
    # a .ps1, prefer a sibling .cmd (npm-on-Windows installs both); otherwise
    # wrap the launch via powershell.exe -File.
    $resolvedSource = [string]$resolved.Source
    $launchExe = $resolvedSource
    $launchArgs = @()
    $ext = [System.IO.Path]::GetExtension($resolvedSource).ToLowerInvariant()
    if ($ext -eq ".ps1") {
        $folder = [System.IO.Path]::GetDirectoryName($resolvedSource)
        $base   = [System.IO.Path]::GetFileNameWithoutExtension($resolvedSource)
        $sibCmd = Join-Path $folder ($base + ".cmd")
        $sibBat = Join-Path $folder ($base + ".bat")
        if (Test-Path $sibCmd) {
            $launchExe = $sibCmd
            Write-Verbose "Using sibling .cmd wrapper: $sibCmd"
        } elseif (Test-Path $sibBat) {
            $launchExe = $sibBat
        } else {
            $psExe = (Get-Command powershell.exe -ErrorAction SilentlyContinue).Source
            if (-not $psExe) { $psExe = "powershell.exe" }
            $launchExe = $psExe
            $launchArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $resolvedSource)
            Write-Verbose "Wrapping .ps1 via powershell.exe -File"
        }
    }


    # Build sanitized env
    $envInfo = [pscustomobject]@{ environment = $null; stripped = @() }
    if ($SanitizeEnvironment) {
        $envInfo = Get-SanitizedEnvironment -DenylistPatterns $EnvDenylistPatterns -AllowList $EnvAllowList
    }

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $launchExe
    # PS 5.1 / .NET Framework ProcessStartInfo has no ArgumentList collection;
    # always assemble Arguments as a single quoted string.
    $allArgs = @() + $launchArgs + $ArgList
    $psi.Arguments = ($allArgs | ForEach-Object {
        if ($_ -match '\s') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
    }) -join ' '
    $psi.UseShellExecute        = $false
    $psi.CreateNoWindow         = $true
    $psi.WorkingDirectory       = $WorkingDirectory
    $psi.RedirectStandardInput  = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true

    if ($SanitizeEnvironment) {
        # Wipe the inherited env vars on the PSI and set our own deterministic map.
        $psi.EnvironmentVariables.Clear()
        foreach ($k in $envInfo.environment.Keys) {
            $psi.EnvironmentVariables[$k] = [string]$envInfo.environment[$k]
        }
    }

    $proc = New-Object System.Diagnostics.Process
    $proc.StartInfo = $psi

    # Async stdout/stderr -> files (buffered writers we own)
    $outFs = [System.IO.File]::Open($StdoutFile, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read)
    $errFs = [System.IO.File]::Open($StderrFile, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::Read)
    $outSw = New-Object System.IO.StreamWriter($outFs, [System.Text.Encoding]::UTF8)
    $errSw = New-Object System.IO.StreamWriter($errFs, [System.Text.Encoding]::UTF8)
    $outSw.AutoFlush = $true
    $errSw.AutoFlush = $true

    $stateBag = [pscustomobject]@{ outSw = $outSw; errSw = $errSw }

    $outHandler = {
        param($sender, $e)
        if ($null -ne $e.Data) { $stateBag.outSw.WriteLine($e.Data) }
    }.GetNewClosure()
    $errHandler = {
        param($sender, $e)
        if ($null -ne $e.Data) { $stateBag.errSw.WriteLine($e.Data) }
    }.GetNewClosure()
    $null = Register-ObjectEvent -InputObject $proc -EventName 'OutputDataReceived' -Action $outHandler
    $null = Register-ObjectEvent -InputObject $proc -EventName 'ErrorDataReceived'  -Action $errHandler

    $startedAt = Get-Date
    [void]$proc.Start()
    $proc.BeginOutputReadLine()
    $proc.BeginErrorReadLine()

    # Pipe the prompt to stdin and close
    try {
        $promptBytes = [System.IO.File]::ReadAllBytes($PromptFile)
        $proc.StandardInput.BaseStream.Write($promptBytes, 0, $promptBytes.Length)
        $proc.StandardInput.BaseStream.Flush()
        $proc.StandardInput.Close()
    } catch {
        # If stdin write fails, just continue; the child will see EOF.
    }

    $earlyStatus = $null
    $earlyReason = $null
    $earlyMatch  = $null
    $killedForInteractive = $false
    $timedOut    = $false

    $deadline = $startedAt.AddSeconds($TimeoutSeconds)

    while (-not $proc.HasExited) {
        Start-Sleep -Seconds $PollIntervalSeconds

        # Timeout
        if ((Get-Date) -ge $deadline) {
            $timedOut = $true
            Write-Warning "Process pid=$($proc.Id) timed out after ${TimeoutSeconds}s. Killing."
            Stop-ProcessTree -ProcessId $proc.Id
            try { $null = $proc.WaitForExit(5000) } catch {}
            break
        }

        if ($InteractivePromptPatterns.Count -gt 0) {
            $tailOut = _ReadTailSafe -Path $StdoutFile -Lines 80
            $tailErr = _ReadTailSafe -Path $StderrFile -Lines 80
            $hit = _MatchesAnyPattern -Lines $tailOut -Patterns $InteractivePromptPatterns
            if (-not $hit) { $hit = _MatchesAnyPattern -Lines $tailErr -Patterns $InteractivePromptPatterns }
            if ($hit) {
                $earlyStatus = 'NEEDS_MANUAL_ACTION'
                $earlyReason = "Interactive prompt detected mid-run: $hit"
                $earlyMatch  = $hit
                if ($KillOnInteractivePrompt) {
                    Write-Warning "Interactive prompt detected. Killing pid=$($proc.Id)."
                    Stop-ProcessTree -ProcessId $proc.Id
                    $killedForInteractive = $true
                    try { $null = $proc.WaitForExit(5000) } catch {}
                }
                break
            }
        }
    }

    # Wait briefly for any tail data still streaming
    try { $null = $proc.WaitForExit(2000) } catch {}
    # Phase 2A-2 fix: per MSDN, a timed WaitForExit(int) does NOT
    # guarantee the async stdout/stderr pump has flushed all data.
    # Calling WaitForExit() with no args after the process has already
    # exited synchronously drains the redirected streams.
    if ($proc.HasExited) {
        try { $proc.WaitForExit() } catch {}
    }

    # Tear down event subscriptions
    Get-EventSubscriber | Where-Object { $_.SourceObject -eq $proc } | Unregister-Event -Force -ErrorAction SilentlyContinue

    try { $outSw.Flush(); $outSw.Dispose() } catch {}
    try { $errSw.Flush(); $errSw.Dispose() } catch {}

    $elapsed = ((Get-Date) - $startedAt).TotalSeconds

    return [pscustomobject]@{
        early_status            = $earlyStatus
        early_status_reason     = $earlyReason
        early_status_match      = $earlyMatch
        process_exited          = $proc.HasExited
        exit_code               = if ($proc.HasExited) { $proc.ExitCode } else { -1 }
        timed_out               = $timedOut
        killed_for_interactive  = $killedForInteractive
        elapsed_seconds         = [Math]::Round($elapsed, 2)
        command_line            = $cmdLine
        stdout_path             = $StdoutFile
        stderr_path             = $StderrFile
        pid                     = $proc.Id
        started_at              = $startedAt.ToUniversalTime().ToString('o')
        ended_at                = (Get-Date).ToUniversalTime().ToString('o')
        env_stripped            = @($envInfo.stripped)
        sanitize_environment    = [bool]$SanitizeEnvironment
    }
}
