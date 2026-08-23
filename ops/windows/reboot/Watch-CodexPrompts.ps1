<#
.SYNOPSIS
    Watches a named Windows Terminal tab/window for Codex prompts and
    presses y. Versioned Win32 type (Win32FocusV3) avoids stale-type collisions
    in long-lived PowerShell sessions.

.DESCRIPTION
    FOCUS FIX v3 (2026-05-11): The Win32 P/Invoke type is now named Win32FocusV3
    (was Win32Focus). When you reload the script in the same PowerShell session,
    the old type stays loaded but new method calls go to the V3 type. No
    "method not found" errors when adding new APIs.

    If you ever need to add another P/Invoke method, bump V3 -> V4.

    Modes: DEFAULT (allowlist) | -YesToAll | -AllowAll

.EXAMPLE
    PS> .\Watch-CodexPrompts.ps1 -AllowAll -TabTitle CodexMaster
#>

[CmdletBinding()]
param(
    [string]$TabTitle = '',
    [int]$PollIntervalSeconds = 3,
    [switch]$DryRun,
    [switch]$ListWindows,
    [switch]$YesToAll,
    [switch]$AllowAll,
    [switch]$NoAllNighter,
    [switch]$NoHwndLock,
    # Opt-in Lead continuation path. The launcher pins the runtime root and
    # exact tab identity; no caller-provided prompt text is accepted.
    [switch]$ContinueOnWake,
    [string]$WakeRuntimeRoot = '',
    [ValidateRange(60, 86400)]
    [int]$WakeCooldownSeconds = 300,
    [int]$LeadProcessId = 0,
    [string]$LeadProcessStartUtc = '',
    [string]$ReadyPath = '',
    [string]$LogPath = (Join-Path $env:USERPROFILE 'codex-autoapprove.log'),
    [int]$MaxSendRetries = 3,
    # Never send UI keystrokes while the operator is actively using the desktop.
    # Set to 0 only for an isolated throwaway desktop/session.
    [ValidateRange(0, 86400)]
    [int]$MinUserIdleSeconds = 60
)

$AllowList = @(
    '^Start-Sleep\s+-Seconds\s+\d+;\s*gh\s+pr\s+view\s+\d+\s+--json\s+[\w,]+$',
    '^Start-Sleep\s+-Seconds\s+\d+;\s*gh\s+pr\s+list\s+--state\s+(open|closed|merged)\s+--limit\s+\d+\s+--json\s+[\w,]+$',
    '^gh\s+pr\s+view\s+\d+\s+--json\s+[\w,]+$',
    '^gh\s+pr\s+checks\s+\d+(\s+--watch)?$',
    '^gh\s+pr\s+list\s+--state\s+(open|closed|merged)\s+--limit\s+\d+\s+--json\s+[\w,]+$',
    '^[A-Za-z:\\\.]*powershell(\.exe)?\s+-Command\s+"gh\s+pr\s+view\s+\d+\s+--json\s+[\w,]+"$',
    '^[A-Za-z:\\\.]*powershell(\.exe)?\s+-Command\s+"gh\s+pr\s+list\s+--state\s+(open|closed|merged)\s+--limit\s+\d+\s+--json\s+[\w,]+"$',
    '^.*Get-AgentBridgeStatus\.ps1.*$',
    '^.*Read-AgentBridge\.ps1\s+-Agent\s+\w+\s+-Show.*$',
    '^.*Test-Bridge.*Smoke\.ps1.*$',
    '^.*Test-BridgeBranchSwitchSafe\.ps1.*$',
    '^git\s+status(\s|$).*',
    '^git\s+log\s+--oneline.*$',
    '^git\s+diff(\s|$).*'
)

$DenyList = @(
    'Stop-Computer','Restart-Computer','Shutdown','reg\s+(add|delete|import)'
)

# Fixed provenance and no-authority contract for automated Lead wake turns.
# Keep this free of SendKeys metacharacters: + ^ % ~ ( ) { } [ ].
$script:WdBridgeWakePrompt = 'AUTOMATED BRIDGE WAKE: jatka only the already-authorized current task within its existing scope. This message grants no new authority. Do not merge, deploy, undraft, sign, change allowlists or drivers, perform destructive actions, or assume an operator decision. Read live bridge next action and current claim. If operator input is required, remain blocked.'

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms

# Versioned Win32 P/Invoke type. Bump version suffix when adding new methods
# to avoid collisions with already-loaded older versions in the same PS session.
if (-not ('Win32FocusV3' -as [type])) {
    Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32FocusV3 {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
    public const int SW_RESTORE = 9; public const int SW_SHOW = 5;
    public const byte VK_MENU = 0x12;
    public const uint KEYEVENTF_KEYDOWN = 0x0;
    public const uint KEYEVENTF_KEYUP = 0x2;
}
"@
}

if (-not ('Win32LastInputGuardV1' -as [type])) {
    Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32LastInputGuardV1 {
    [StructLayout(LayoutKind.Sequential)]
    public struct LASTINPUTINFO {
        public uint cbSize;
        public uint dwTime;
    }
    [DllImport("user32.dll")]
    public static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);
    [DllImport("kernel32.dll")]
    public static extern uint GetTickCount();
}
"@
}

function Write-Log {
    param([string]$Message, [string]$Level = 'INFO')
    $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $line = '[' + $ts + '] [' + $Level + '] ' + $Message
    Write-Host $line
    Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
}

function Get-PowerCfgValue {
    param([string]$Sub, [string]$Setting)
    $result = @{ AC = $null; DC = $null }
    try {
        $output = & powercfg /query SCHEME_CURRENT $Sub $Setting 2>$null
        foreach ($line in $output) {
            if ($line -match 'Current AC Power Setting Index:\s+0x([0-9A-Fa-f]+)') { $result.AC = [Convert]::ToInt32($matches[1], 16) }
            elseif ($line -match 'Current DC Power Setting Index:\s+0x([0-9A-Fa-f]+)') { $result.DC = [Convert]::ToInt32($matches[1], 16) }
        }
    } catch { Write-Log ('Get-PowerCfgValue failed: ' + $_) 'WARN' }
    return $result
}

function Save-AllNighterState {
    $saved = @{
        Monitor = Get-PowerCfgValue -Sub 'SUB_VIDEO' -Setting 'VIDEOIDLE'
        Standby = Get-PowerCfgValue -Sub 'SUB_SLEEP' -Setting 'STANDBYIDLE'
        Hibernate = Get-PowerCfgValue -Sub 'SUB_SLEEP' -Setting 'HIBERNATEIDLE'
        ScreenSaveActive = $null; ScreenSaveTimeOut = $null; ScreenSaverIsSecure = $null
    }
    try { $saved.ScreenSaveActive = (Get-ItemProperty 'HKCU:\Control Panel\Desktop' -Name 'ScreenSaveActive' -ErrorAction Stop).ScreenSaveActive } catch { }
    try { $saved.ScreenSaveTimeOut = (Get-ItemProperty 'HKCU:\Control Panel\Desktop' -Name 'ScreenSaveTimeOut' -ErrorAction Stop).ScreenSaveTimeOut } catch { }
    try { $saved.ScreenSaverIsSecure = (Get-ItemProperty 'HKCU:\Control Panel\Desktop' -Name 'ScreenSaverIsSecure' -ErrorAction Stop).ScreenSaverIsSecure } catch { }
    return $saved
}

function Enable-AllNighter {
    try {
        & powercfg /setacvalueindex SCHEME_CURRENT SUB_VIDEO VIDEOIDLE 0 2>$null | Out-Null
        & powercfg /setdcvalueindex SCHEME_CURRENT SUB_VIDEO VIDEOIDLE 0 2>$null | Out-Null
        & powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP STANDBYIDLE 0 2>$null | Out-Null
        & powercfg /setdcvalueindex SCHEME_CURRENT SUB_SLEEP STANDBYIDLE 0 2>$null | Out-Null
        & powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP HIBERNATEIDLE 0 2>$null | Out-Null
        & powercfg /setdcvalueindex SCHEME_CURRENT SUB_SLEEP HIBERNATEIDLE 0 2>$null | Out-Null
        & powercfg /setactive SCHEME_CURRENT 2>$null | Out-Null
    } catch { Write-Log ('Enable-AllNighter powercfg failed: ' + $_) 'WARN' }
    try {
        Set-ItemProperty 'HKCU:\Control Panel\Desktop' -Name 'ScreenSaveActive' -Value '0' -ErrorAction SilentlyContinue
        Set-ItemProperty 'HKCU:\Control Panel\Desktop' -Name 'ScreenSaveTimeOut' -Value '0' -ErrorAction SilentlyContinue
        Set-ItemProperty 'HKCU:\Control Panel\Desktop' -Name 'ScreenSaverIsSecure' -Value '0' -ErrorAction SilentlyContinue
    } catch { Write-Log ('Enable-AllNighter screensaver failed: ' + $_) 'WARN' }
}

function Restore-AllNighter {
    param($Saved)
    if ($null -eq $Saved) { return }
    try {
        if ($null -ne $Saved.Monitor.AC)   { & powercfg /setacvalueindex SCHEME_CURRENT SUB_VIDEO VIDEOIDLE $Saved.Monitor.AC 2>$null | Out-Null }
        if ($null -ne $Saved.Monitor.DC)   { & powercfg /setdcvalueindex SCHEME_CURRENT SUB_VIDEO VIDEOIDLE $Saved.Monitor.DC 2>$null | Out-Null }
        if ($null -ne $Saved.Standby.AC)   { & powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP STANDBYIDLE $Saved.Standby.AC 2>$null | Out-Null }
        if ($null -ne $Saved.Standby.DC)   { & powercfg /setdcvalueindex SCHEME_CURRENT SUB_SLEEP STANDBYIDLE $Saved.Standby.DC 2>$null | Out-Null }
        if ($null -ne $Saved.Hibernate.AC) { & powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP HIBERNATEIDLE $Saved.Hibernate.AC 2>$null | Out-Null }
        if ($null -ne $Saved.Hibernate.DC) { & powercfg /setdcvalueindex SCHEME_CURRENT SUB_SLEEP HIBERNATEIDLE $Saved.Hibernate.DC 2>$null | Out-Null }
        & powercfg /setactive SCHEME_CURRENT 2>$null | Out-Null
    } catch { Write-Log ('Restore-AllNighter powercfg failed: ' + $_) 'WARN' }
    try {
        if ($null -ne $Saved.ScreenSaveActive)    { Set-ItemProperty 'HKCU:\Control Panel\Desktop' -Name 'ScreenSaveActive' -Value $Saved.ScreenSaveActive -ErrorAction SilentlyContinue }
        if ($null -ne $Saved.ScreenSaveTimeOut)   { Set-ItemProperty 'HKCU:\Control Panel\Desktop' -Name 'ScreenSaveTimeOut' -Value $Saved.ScreenSaveTimeOut -ErrorAction SilentlyContinue }
        if ($null -ne $Saved.ScreenSaverIsSecure) { Set-ItemProperty 'HKCU:\Control Panel\Desktop' -Name 'ScreenSaverIsSecure' -Value $Saved.ScreenSaverIsSecure -ErrorAction SilentlyContinue }
    } catch { Write-Log ('Restore-AllNighter screensaver failed: ' + $_) 'WARN' }
}

function Format-AllNighterDelta {
    param($Saved)
    if ($null -eq $Saved) { return '(no saved state)' }
    $f = { param($v); if ($null -eq $v) { return '?' }; if ($v -eq 0) { return 'never' }; $min = [math]::Round($v / 60.0, 1); return "$min min" }
    return ("Monitor AC=" + (& $f $Saved.Monitor.AC) + " / DC=" + (& $f $Saved.Monitor.DC) +
            " | Standby AC=" + (& $f $Saved.Standby.AC) + " / DC=" + (& $f $Saved.Standby.DC) +
            " | Hibernate AC=" + (& $f $Saved.Hibernate.AC) + " / DC=" + (& $f $Saved.Hibernate.DC) +
            " | Screensaver=" + ($Saved.ScreenSaveActive))
}

function Get-UserIdleSeconds {
    try {
        $lii = New-Object Win32LastInputGuardV1+LASTINPUTINFO
        $lii.cbSize = [System.Runtime.InteropServices.Marshal]::SizeOf($lii)
        if (-not [Win32LastInputGuardV1]::GetLastInputInfo([ref]$lii)) { return $null }
        $ticks = [Win32LastInputGuardV1]::GetTickCount()
        $elapsedMs = [uint32]($ticks - $lii.dwTime)
        return [math]::Floor($elapsedMs / 1000)
    } catch {
        Write-Log ('Get-UserIdleSeconds failed: ' + $_) 'WARN'
        return $null
    }
}

function ConvertTo-WdWakeUtc {
    param([AllowEmptyString()] [string]$Value)

    $text = [string]$Value
    if ($text -cnotmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?Z$') {
        return $null
    }
    $parsed = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse(
            $text,
            [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::AssumeUniversal -bor
                [Globalization.DateTimeStyles]::AdjustToUniversal,
            [ref]$parsed
        )) {
        return $null
    }
    return $parsed.ToUniversalTime()
}

function Get-WdProcessStartUtc {
    param([Parameter(Mandatory)] [int]$ProcessId)

    if ($ProcessId -le 0) { return $null }
    try {
        $process = Get-Process -Id $ProcessId -ErrorAction Stop
        if ($process.HasExited) { return $null }
        return [DateTimeOffset]$process.StartTime.ToUniversalTime()
    } catch {
        return $null
    }
}

function Test-WdProcessGeneration {
    param(
        [Parameter(Mandatory)] [int]$ProcessId,
        [Parameter(Mandatory)] [string]$ExpectedStartUtc
    )

    $expected = ConvertTo-WdWakeUtc -Value $ExpectedStartUtc
    if ($null -eq $expected) { return $false }
    $actual = Get-WdProcessStartUtc -ProcessId $ProcessId
    if ($null -eq $actual) { return $false }
    return $actual.UtcDateTime.Ticks -eq $expected.UtcDateTime.Ticks
}

function Get-WdWakeFileUtc {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [switch]$AllowMissing
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        if ($AllowMissing) { return $null }
        throw "wake timestamp file is missing: $Path"
    }
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        ($item.PSObject.Properties['LinkType'] -and $null -ne $item.LinkType) -or
        $item.Length -gt 128
    ) {
        throw "wake timestamp file is not a small plain file: $Path"
    }
    $value = (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 -ErrorAction Stop).Trim()
    $parsed = ConvertTo-WdWakeUtc -Value $value
    if ($null -eq $parsed) {
        throw "wake timestamp file is malformed: $Path"
    }
    return $parsed
}

function Assert-WdWakePathIsPlainOrMissing {
    param([Parameter(Mandatory)] [string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) { return }
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (
        $item.PSIsContainer -or
        ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        ($item.PSObject.Properties['LinkType'] -and $null -ne $item.LinkType)
    ) {
        throw "wake state path is not a plain file: $Path"
    }
}

function Test-WdWakeCooldownElapsed {
    param(
        [AllowNull()] $LastAttemptUtc,
        [Parameter(Mandatory)] [DateTimeOffset]$NowUtc,
        [Parameter(Mandatory)] [int]$CooldownSeconds
    )

    if ($null -eq $LastAttemptUtc) { return $true }
    $last = if ($LastAttemptUtc -is [DateTimeOffset]) {
        [DateTimeOffset]$LastAttemptUtc
    } else {
        ConvertTo-WdWakeUtc -Value ([string]$LastAttemptUtc)
    }
    if ($null -eq $last) { return $false }
    $elapsed = ($NowUtc.ToUniversalTime() - $last.ToUniversalTime()).TotalSeconds
    return ($elapsed -ge 0 -and $elapsed -ge $CooldownSeconds)
}

function Write-WdUtf8TextAtomic {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [string]$Text
    )

    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw "atomic write directory is missing: $parent"
    }
    Assert-WdWakePathIsPlainOrMissing -Path $Path
    $temp = "$Path.tmp.$PID.$([guid]::NewGuid().ToString('N'))"
    $backup = "$Path.bak.$PID.$([guid]::NewGuid().ToString('N'))"
    $encoding = New-Object Text.UTF8Encoding($false)
    $stream = $null
    try {
        $stream = New-Object IO.FileStream(
            $temp,
            [IO.FileMode]::CreateNew,
            [IO.FileAccess]::Write,
            [IO.FileShare]::None,
            4096,
            [IO.FileOptions]::WriteThrough
        )
        $bytes = $encoding.GetBytes($Text)
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Flush($true)
        $stream.Dispose()
        $stream = $null
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            [IO.File]::Replace($temp, $Path, $backup)
        } else {
            [IO.File]::Move($temp, $Path)
        }
    } finally {
        if ($null -ne $stream) { $stream.Dispose() }
        if (Test-Path -LiteralPath $temp -PathType Leaf) {
            Remove-Item -LiteralPath $temp -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $backup -PathType Leaf) {
            Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
        }
    }
}

function Write-WdWakeAttemptUtc {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [DateTimeOffset]$AttemptUtc
    )

    Write-WdUtf8TextAtomic `
        -Path $Path `
        -Text $AttemptUtc.UtcDateTime.ToString(
            'o',
            [Globalization.CultureInfo]::InvariantCulture
        )
}

function Write-WdPromptWatcherReadyRecord {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [string]$WatcherProcessStartUtc,
        [Parameter(Mandatory)] [int]$ExpectedLeadProcessId,
        [Parameter(Mandatory)] [string]$ExpectedLeadProcessStartUtc,
        [Parameter(Mandatory)] [string]$ExactTabTitle,
        [Parameter(Mandatory)] [string]$TabRuntimeId,
        [Parameter(Mandatory)] [int64]$WindowHandle,
        [Parameter(Mandatory)] [string]$WakeRoot
    )

    $record = [ordered]@{
        schema_version = 1
        status = 'ready'
        watcher_pid = $PID
        watcher_process_start_utc = $WatcherProcessStartUtc
        lead_process_id = $ExpectedLeadProcessId
        lead_process_start_utc = $ExpectedLeadProcessStartUtc
        tab_title = $ExactTabTitle
        tab_runtime_id = $TabRuntimeId
        window_handle = $WindowHandle
        wake_runtime_root = $WakeRoot
        created_at_utc = [DateTime]::UtcNow.ToString('o')
    }
    Write-WdUtf8TextAtomic `
        -Path $Path `
        -Text ($record | ConvertTo-Json -Depth 4 -Compress)
}

function Remove-WdOwnPromptWatcherReadyRecord {
    param(
        [Parameter(Mandatory)] [string]$Path,
        [Parameter(Mandatory)] [string]$WatcherProcessStartUtc
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
    try {
        Assert-WdWakePathIsPlainOrMissing -Path $Path
        $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
        if ($item.Length -gt 4096) { return }
        $record = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 |
            ConvertFrom-Json -ErrorAction Stop
        if (
            [int]$record.watcher_pid -eq $PID -and
            [string]$record.watcher_process_start_utc -ceq
                $WatcherProcessStartUtc
        ) {
            [IO.File]::Delete($Path)
        }
    } catch {
        Write-Log ('Ready marker cleanup failed: ' + $_) 'WARN'
    }
}

function Get-WdWakeSafetyDisposition {
    param(
        [Parameter(Mandatory)] [int]$ExactTabCount,
        [Parameter(Mandatory)] [bool]$TabGenerationMatches,
        [Parameter(Mandatory)] [int64]$WindowHandle,
        [Parameter(Mandatory)] [bool]$InputSurfaceReady,
        [Parameter(Mandatory)] [bool]$CodexBusy,
        [Parameter(Mandatory)] [bool]$ConfirmationPromptActive,
        [AllowNull()] $UserIdleSeconds,
        [Parameter(Mandatory)] [int]$MinimumUserIdleSeconds,
        [int64]$ExpectedWindowHandle = 0
    )

    if ($ConfirmationPromptActive) { return 'confirmation_prompt_active' }
    if ($ExactTabCount -ne 1) { return 'exact_tab_required' }
    if (-not $TabGenerationMatches) { return 'tab_generation_changed' }
    if ($WindowHandle -eq 0) { return 'window_handle_missing' }
    if ($ExpectedWindowHandle -ne 0 -and $WindowHandle -ne $ExpectedWindowHandle) {
        return 'window_generation_changed'
    }
    if (-not $InputSurfaceReady) { return 'codex_input_surface_missing' }
    if ($CodexBusy) { return 'codex_turn_active' }
    if (
        $MinimumUserIdleSeconds -gt 0 -and
        ($null -eq $UserIdleSeconds -or
            [double]$UserIdleSeconds -lt $MinimumUserIdleSeconds)
    ) {
        return 'operator_active_or_unknown'
    }
    return 'ready'
}

function Get-WdWakeIdentityDisposition {
    param(
        [Parameter(Mandatory)] [int]$ExactTabCount,
        [Parameter(Mandatory)] [bool]$TabGenerationMatches,
        [Parameter(Mandatory)] [int64]$WindowHandle,
        [AllowNull()] $UserIdleSeconds,
        [Parameter(Mandatory)] [int]$MinimumUserIdleSeconds,
        [int64]$ExpectedWindowHandle = 0
    )

    if ($ExactTabCount -ne 1) { return 'exact_tab_required' }
    if (-not $TabGenerationMatches) { return 'tab_generation_changed' }
    if ($WindowHandle -eq 0) { return 'window_handle_missing' }
    if ($ExpectedWindowHandle -ne 0 -and $WindowHandle -ne $ExpectedWindowHandle) {
        return 'window_generation_changed'
    }
    if (
        $MinimumUserIdleSeconds -gt 0 -and
        ($null -eq $UserIdleSeconds -or
            [double]$UserIdleSeconds -lt $MinimumUserIdleSeconds)
    ) {
        return 'operator_active_or_unknown'
    }
    return 'ready'
}

function Get-WdWakeSubmitReceiptDisposition {
    param(
        [Parameter(Mandatory)] [int]$ExactTabCount,
        [Parameter(Mandatory)] [bool]$TabGenerationMatches,
        [Parameter(Mandatory)] [int64]$WindowHandle,
        [Parameter(Mandatory)] [int64]$ExpectedWindowHandle,
        [Parameter(Mandatory)] [bool]$SnapshotComplete,
        [Parameter(Mandatory)] [bool]$InputSurfaceReady,
        [Parameter(Mandatory)] [bool]$CodexBusy,
        [Parameter(Mandatory)] [bool]$ConfirmationPromptActive,
        [Parameter(Mandatory)] [bool]$ContinueEchoAdvanced
    )

    if ($ExactTabCount -ne 1) { return 'exact_tab_required' }
    if (-not $TabGenerationMatches) { return 'tab_generation_changed' }
    if (
        $WindowHandle -eq 0 -or
        $WindowHandle -ne $ExpectedWindowHandle
    ) {
        return 'window_generation_changed'
    }
    if (-not $SnapshotComplete) { return 'snapshot_incomplete' }
    if (
        $ConfirmationPromptActive -or
        $CodexBusy -or
        ($ContinueEchoAdvanced -and $InputSurfaceReady)
    ) {
        return 'submitted_confirmed'
    }
    return 'submitted_transition_pending'
}

function Get-WdLeadPermissionReceiptDisposition {
    param(
        [Parameter(Mandatory)] [int]$ExactTabCount,
        [Parameter(Mandatory)] [bool]$TabGenerationMatches,
        [Parameter(Mandatory)] [int64]$WindowHandle,
        [Parameter(Mandatory)] [int64]$ExpectedWindowHandle,
        [Parameter(Mandatory)] [bool]$SnapshotComplete,
        [Parameter(Mandatory)] [bool]$ConfirmationPromptActive,
        [AllowEmptyString()] [string]$PromptCommand,
        [Parameter(Mandatory)] [string]$ExpectedCommand,
        [Parameter(Mandatory)] [bool]$VisibleTextChanged
    )

    if ($ExactTabCount -ne 1) { return 'exact_tab_required' }
    if (-not $TabGenerationMatches) { return 'tab_generation_changed' }
    if (
        $WindowHandle -eq 0 -or
        $WindowHandle -ne $ExpectedWindowHandle
    ) {
        return 'window_generation_changed'
    }
    if (-not $SnapshotComplete) { return 'snapshot_incomplete' }
    if ($ConfirmationPromptActive) {
        if ([string]::IsNullOrWhiteSpace($PromptCommand)) {
            return 'original_prompt_unresolved'
        }
        if ($PromptCommand -ceq $ExpectedCommand) {
            return 'original_prompt_present'
        }
        return 'next_prompt_candidate'
    }
    if (-not $VisibleTextChanged) {
        return 'post_state_unconfirmed'
    }
    return 'dismissal_candidate'
}

function Repair-WdWakeInflight {
    param(
        [Parameter(Mandatory)] [string]$SentinelPath,
        [Parameter(Mandatory)] [string]$InflightPath,
        [Parameter(Mandatory)] [string]$AttemptPath,
        [Parameter(Mandatory)] [DateTimeOffset]$NowUtc,
        [Parameter(Mandatory)] [int]$CooldownSeconds
    )

    Assert-WdWakePathIsPlainOrMissing -Path $SentinelPath
    Assert-WdWakePathIsPlainOrMissing -Path $InflightPath
    Assert-WdWakePathIsPlainOrMissing -Path $AttemptPath
    if (-not (Test-Path -LiteralPath $InflightPath -PathType Leaf)) {
        return 'no_inflight'
    }

    [void](Get-WdWakeFileUtc -Path $InflightPath)
    $lastAttemptUtc = Get-WdWakeFileUtc -Path $AttemptPath -AllowMissing
    if ($null -eq $lastAttemptUtc) {
        if (Test-Path -LiteralPath $SentinelPath -PathType Leaf) {
            [void](Get-WdWakeFileUtc -Path $SentinelPath)
            [IO.File]::Delete($InflightPath)
            return 'inflight_superseded_without_attempt'
        }
        [IO.File]::Move($InflightPath, $SentinelPath)
        return 'inflight_restored_without_attempt'
    }
    if (-not (Test-WdWakeCooldownElapsed `
            -LastAttemptUtc $lastAttemptUtc `
            -NowUtc $NowUtc `
            -CooldownSeconds $CooldownSeconds)) {
        return 'inflight_cooldown'
    }

    if (Test-Path -LiteralPath $SentinelPath -PathType Leaf) {
        [void](Get-WdWakeFileUtc -Path $SentinelPath)
        [IO.File]::Delete($InflightPath)
        return 'inflight_superseded'
    }

    [IO.File]::Move($InflightPath, $SentinelPath)
    return 'inflight_restored'
}

function Invoke-WdWakeSentinelTransaction {
    param(
        [Parameter(Mandatory)] [string]$SentinelPath,
        [Parameter(Mandatory)] [string]$InflightPath,
        [Parameter(Mandatory)] [string]$AttemptPath,
        [Parameter(Mandatory)] [int]$CooldownSeconds,
        [Parameter(Mandatory)] [DateTimeOffset]$NowUtc,
        [Parameter(Mandatory)] [scriptblock]$SendAction,
        [switch]$DryRun
    )

    Assert-WdWakePathIsPlainOrMissing -Path $SentinelPath
    Assert-WdWakePathIsPlainOrMissing -Path $InflightPath
    Assert-WdWakePathIsPlainOrMissing -Path $AttemptPath

    if ($DryRun) {
        if (Test-Path -LiteralPath $InflightPath -PathType Leaf) {
            [void](Get-WdWakeFileUtc -Path $InflightPath)
            $dryRunInflightAttemptUtc = Get-WdWakeFileUtc `
                -Path $AttemptPath `
                -AllowMissing
            if ($null -eq $dryRunInflightAttemptUtc) {
                return 'dry_run_inflight_recoverable_without_attempt'
            }
            if (-not (Test-WdWakeCooldownElapsed `
                    -LastAttemptUtc $dryRunInflightAttemptUtc `
                    -NowUtc $NowUtc `
                    -CooldownSeconds $CooldownSeconds)) {
                return 'inflight_cooldown'
            }
            return 'dry_run_inflight_recoverable'
        }
        if (-not (Test-Path -LiteralPath $SentinelPath -PathType Leaf)) {
            return 'no_wake'
        }
        [void](Get-WdWakeFileUtc -Path $SentinelPath)
        $dryRunLastAttemptUtc = Get-WdWakeFileUtc -Path $AttemptPath -AllowMissing
        if (-not (Test-WdWakeCooldownElapsed `
                -LastAttemptUtc $dryRunLastAttemptUtc `
                -NowUtc $NowUtc `
                -CooldownSeconds $CooldownSeconds)) {
            return 'cooldown'
        }
        return 'dry_run_ready'
    }

    $repair = Repair-WdWakeInflight `
        -SentinelPath $SentinelPath `
        -InflightPath $InflightPath `
        -AttemptPath $AttemptPath `
        -NowUtc $NowUtc `
        -CooldownSeconds $CooldownSeconds
    if ($repair -in @('inflight_attempt_unknown', 'inflight_cooldown')) {
        return $repair
    }
    if (-not (Test-Path -LiteralPath $SentinelPath -PathType Leaf)) {
        return $repair
    }

    [void](Get-WdWakeFileUtc -Path $SentinelPath)
    $lastAttemptUtc = Get-WdWakeFileUtc -Path $AttemptPath -AllowMissing
    if (-not (Test-WdWakeCooldownElapsed `
            -LastAttemptUtc $lastAttemptUtc `
            -NowUtc $NowUtc `
            -CooldownSeconds $CooldownSeconds)) {
        return 'cooldown'
    }

    # Persist the attempt before the atomic claim. A crash can delay a retry by
    # one cooldown, but cannot strand a claimed wake without its recovery time.
    Write-WdWakeAttemptUtc -Path $AttemptPath -AttemptUtc $NowUtc
    try {
        [IO.File]::Move($SentinelPath, $InflightPath)
    } catch [IO.IOException] {
        return 'claim_race'
    }

    $sent = $false
    try {
        $sent = [bool](& $SendAction)
    } catch {
        Write-Log ('Wake continue send failed: ' + $_) 'ERROR'
    }
    if (-not $sent) {
        return 'send_failed_inflight_retained'
    }

    # Delete only the claimed generation. A writer that created a newer
    # canonical sentinel during SendAction remains untouched for the next pass.
    [IO.File]::Delete($InflightPath)
    return 'sent'
}

function Get-AllTerminalWindows {
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $classNames = @('CASCADIA_HOSTING_WINDOW_CLASS','WindowsTerminal','Windows.UI.Core.CoreWindow')
    $results = @()
    foreach ($cls in $classNames) {
        try {
            $cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::ClassNameProperty, $cls)
            $found = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $cond)
            foreach ($w in $found) { $results += $w }
        } catch { }
    }
    return $results
}

function Get-WdAutomationRuntimeIdText {
    param([Parameter(Mandatory)] $Element)

    try {
        return (@($Element.GetRuntimeId()) -join '.')
    } catch {
        return ''
    }
}

function Get-WdTextSha256 {
    param([AllowEmptyString()] [string]$Text)

    $sha256 = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes([string]$Text)
        return (($sha256.ComputeHash($bytes) | ForEach-Object {
                    $_.ToString('x2')
                }) -join '')
    } finally {
        $sha256.Dispose()
    }
}

function Get-WdExactWakeTabSnapshot {
    param(
        [Parameter(Mandatory)] [string]$ExactTabTitle,
        [AllowEmptyString()] [string]$ExpectedRuntimeId = '',
        [switch]$SelectTab
    )

    $result = [ordered]@{
        ExactTabCount = 0
        TabRuntimeId = ''
        TabGenerationMatches = $false
        WindowHandle = [int64]0
        InputSurfaceReady = $false
        CodexBusy = $true
        ConfirmationPromptActive = $false
        ContinueEchoCount = 0
        PromptCommand = ''
        VisibleTextSha256 = ''
        SnapshotComplete = $false
        Window = $null
        TextElement = $null
    }
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $tabCondition = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::TabItem
    )
    $matches = @()
    try {
        $tabs = $root.FindAll(
            [System.Windows.Automation.TreeScope]::Descendants,
            $tabCondition
        )
        $matches = @(
            $tabs | Where-Object {
                [string]$_.Current.Name -ceq $ExactTabTitle
            }
        )
    } catch {
        return [pscustomobject]$result
    }
    $result.ExactTabCount = $matches.Count
    if ($matches.Count -ne 1) { return [pscustomobject]$result }

    $tab = $matches[0]
    $runtimeId = Get-WdAutomationRuntimeIdText -Element $tab
    $result.TabRuntimeId = $runtimeId
    $result.TabGenerationMatches = (
        -not [string]::IsNullOrWhiteSpace($runtimeId) -and
        ([string]::IsNullOrWhiteSpace($ExpectedRuntimeId) -or
            $runtimeId -ceq $ExpectedRuntimeId)
    )
    if (-not $result.TabGenerationMatches) {
        return [pscustomobject]$result
    }

    if ($SelectTab) {
        try {
            $selection = $tab.GetCurrentPattern(
                [System.Windows.Automation.SelectionItemPattern]::Pattern
            )
            $selection.Select()
            Start-Sleep -Milliseconds 300
            if (-not $selection.Current.IsSelected) {
                return [pscustomobject]$result
            }
        } catch {
            return [pscustomobject]$result
        }
    }

    $window = $null
    try {
        $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
        $current = $tab
        for ($depth = 0; $depth -lt 12 -and $null -ne $current; $depth++) {
            if ($current.Current.ControlType -eq [System.Windows.Automation.ControlType]::Window) {
                $window = $current
                break
            }
            $current = $walker.GetParent($current)
        }
    } catch {
        return [pscustomobject]$result
    }
    if ($null -eq $window) {
        return [pscustomobject]$result
    }
    $windowName = [string]$window.Current.Name
    if ($SelectTab -and $windowName -cne $ExactTabTitle) {
        return [pscustomobject]$result
    }
    $handle = [int64]$window.Current.NativeWindowHandle
    if ($handle -eq 0) { return [pscustomobject]$result }
    $result.Window = $window
    $result.WindowHandle = $handle

    # An inactive tab shares its top-level HWND with the selected tab, whose
    # TermControl text must not be mistaken for the Lead input surface. Identity
    # callers can still bind the exact tab and HWND before an idle-gated select.
    if (-not $SelectTab -and $windowName -cne $ExactTabTitle) {
        return [pscustomobject]$result
    }

    try {
        $textCondition = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::IsTextPatternAvailableProperty,
            $true
        )
        $termControls = @(
            $window.FindAll(
                [System.Windows.Automation.TreeScope]::Descendants,
                $textCondition
            ) | Where-Object {
                [string]$_.Current.ClassName -ceq 'TermControl'
            }
        )
        if ($termControls.Count -ne 1) { return [pscustomobject]$result }
        $termControl = $termControls[0]
        $textPattern = $termControl.GetCurrentPattern(
            [System.Windows.Automation.TextPattern]::Pattern
        )
        $visibleText = @(
            $textPattern.GetVisibleRanges() |
                ForEach-Object { $_.GetText(-1) }
        ) -join "`n"
        $result.InputSurfaceReady = (
            $visibleText -cmatch 'Ask Codex to do anything'
        )
        $result.CodexBusy = (
            $visibleText -match '(?i)esc to interrupt'
        )
        $result.ConfirmationPromptActive = (
            $visibleText -match 'Press enter to confirm or esc to cancel' -or
            ($visibleText -match '1\.\s+Yes,\s+proceed' -and
                $visibleText -match 'Would you like to run the following command')
        )
        $normalizedVisibleText = ($visibleText -replace '\s+', ' ').Trim()
        $result.VisibleTextSha256 = Get-WdTextSha256 -Text $visibleText
        $result.ContinueEchoCount = [regex]::Matches(
            $normalizedVisibleText,
            [regex]::Escape($script:WdBridgeWakePrompt)
        ).Count
        $promptCommand = Find-PromptCommand -Text $visibleText
        if ($null -ne $promptCommand) {
            $result.PromptCommand = [string]$promptCommand
        }
        $result.SnapshotComplete = $true
        $result.TextElement = $termControl
    } catch {
        return [pscustomobject]$result
    }
    return [pscustomobject]$result
}

function Get-TextElementsFromWindow {
    param($Win)
    $cond = New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::IsTextPatternAvailableProperty, $true)
    $items = @()
    try {
        $found = $Win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
        foreach ($el in $found) {
            $txt = ''
            try {
                $tp = $el.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
                if ($null -ne $tp) { $txt = $tp.DocumentRange.GetText(-1) }
            } catch { }
            $aid = ''; $cls = ''; $nm = ''
            try { $aid = $el.Current.AutomationId } catch { }
            try { $cls = $el.Current.ClassName } catch { }
            try { $nm  = $el.Current.Name } catch { }
            $items += @{ Element=$el; Text=$txt; AutomationId=$aid; ClassName=$cls; Name=$nm }
        }
    } catch { }
    return $items
}

function Find-PromptCommand {
    param([string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return $null }
    $hasPress  = $Text -match 'Press enter to confirm or esc to cancel'
    $hasYes1   = $Text -match '1\.\s+Yes,\s+proceed'
    $hasWould  = $Text -match 'Would you like to run the following command'
    if (-not ($hasPress -or ($hasYes1 -and $hasWould))) { return $null }
    $tail = ($Text -split "`n") | Select-Object -Last 80
    $tailText = $tail -join "`n"
    if ($tailText -match '(?ms)^\s*\$\s+([\s\S]+?)\r?\n\s*[^\s\w\$]?\s*1\.\s+Yes,\s+proceed') {
        $raw = $matches[1]; $clean = ($raw -replace '\s+', ' ').Trim(); return $clean
    }
    if ($tailText -match '(?ms)^\s*\$\s+([\s\S]+?)(?:\r?\n\s*\r?\n|$)') {
        $raw = $matches[1]; $clean = ($raw -replace '\s+', ' ').Trim(); return $clean
    }
    if ($tailText -match '(?ms)^\s*\$\s+(.+)$') { return $matches[1].Trim() }
    return $null
}

function Test-CommandSafe {
    param([string]$Command, [bool]$YesToAllMode, [bool]$AllowAllMode)
    if ($AllowAllMode) {
        Write-Log "ALLOWALL mode: bypassing both denylist and allowlist -> approving." 'DANGEROUS-ALLOW'
        return $true
    }
    foreach ($denyPattern in $DenyList) {
        if ($Command -match $denyPattern) {
            Write-Log ("DENY-match '" + $denyPattern + "' in command, refusing.") 'WARN'
            return $false
        }
    }
    if ($YesToAllMode) { Write-Log "YesToAll mode: not on denylist -> approving." 'INFO'; return $true }
    foreach ($allowPattern in $AllowList) {
        if ($Command -match $allowPattern) { Write-Log ("ALLOW-match '" + $allowPattern + "' for command.") 'INFO'; return $true }
    }
    Write-Log 'No allowlist match -- leaving for human.' 'INFO'
    return $false
}

function Get-ElementHwndInt64 {
    param($Element)
    try {
        $hwndInt = $Element.Current.NativeWindowHandle
        if ($null -eq $hwndInt) { return [int64]0 }
        return [int64]$hwndInt
    } catch { return [int64]0 }
}

function Send-AltKeyTrick {
    try {
        [Win32FocusV3]::keybd_event([Win32FocusV3]::VK_MENU, 0, [Win32FocusV3]::KEYEVENTF_KEYDOWN, [UIntPtr]::Zero)
        Start-Sleep -Milliseconds 30
        [Win32FocusV3]::keybd_event([Win32FocusV3]::VK_MENU, 0, [Win32FocusV3]::KEYEVENTF_KEYUP, [UIntPtr]::Zero)
        Start-Sleep -Milliseconds 30
    } catch { Write-Log ('Send-AltKeyTrick failed: ' + $_) 'WARN' }
}

function Force-Foreground {
    param(
        [int64]$TargetHwndInt64,
        [switch]$NoAltKeyTrick
    )
    if ($TargetHwndInt64 -eq 0) { return $false }
    $TargetHwnd = [IntPtr]$TargetHwndInt64
    try {
        if ([Win32FocusV3]::IsIconic($TargetHwnd)) {
            [void][Win32FocusV3]::ShowWindow($TargetHwnd, [Win32FocusV3]::SW_RESTORE)
            Start-Sleep -Milliseconds 100
        }

        $foreHwndInt = [Win32FocusV3]::GetForegroundWindow().ToInt64()
        if ($foreHwndInt -eq $TargetHwndInt64) { return $true }

        $myThread = [Win32FocusV3]::GetCurrentThreadId()
        $foreProcId = 0
        $foreThread = [Win32FocusV3]::GetWindowThreadProcessId([IntPtr]$foreHwndInt, [ref]$foreProcId)
        $attached = $false
        if ($foreThread -ne 0 -and $foreThread -ne $myThread) {
            $attached = [Win32FocusV3]::AttachThreadInput($foreThread, $myThread, $true)
        }
        [void][Win32FocusV3]::SetForegroundWindow($TargetHwnd)
        if ($attached) { [void][Win32FocusV3]::AttachThreadInput($foreThread, $myThread, $false) }

        Start-Sleep -Milliseconds 150
        $newFore = [Win32FocusV3]::GetForegroundWindow().ToInt64()
        if ($newFore -eq $TargetHwndInt64) { return $true }

        if ($NoAltKeyTrick) {
            Write-Log 'Force-Foreground: SetForegroundWindow blocked; ALT-key trick disabled for wake safety.' 'WARN'
            return $false
        }

        Write-Log 'Force-Foreground: SetForegroundWindow blocked, trying ALT-key trick.' 'INFO'
        Send-AltKeyTrick

        $foreHwndInt = [Win32FocusV3]::GetForegroundWindow().ToInt64()
        if ($foreHwndInt -eq $TargetHwndInt64) { return $true }

        $foreThread2 = [Win32FocusV3]::GetWindowThreadProcessId([IntPtr]$foreHwndInt, [ref]$foreProcId)
        $attached2 = $false
        if ($foreThread2 -ne 0 -and $foreThread2 -ne $myThread) {
            $attached2 = [Win32FocusV3]::AttachThreadInput($foreThread2, $myThread, $true)
        }
        [void][Win32FocusV3]::SetForegroundWindow($TargetHwnd)
        if ($attached2) { [void][Win32FocusV3]::AttachThreadInput($foreThread2, $myThread, $false) }

        Start-Sleep -Milliseconds 150
        $newFore2 = [Win32FocusV3]::GetForegroundWindow().ToInt64()
        if ($newFore2 -eq $TargetHwndInt64) {
            Write-Log 'Force-Foreground: ALT-key trick succeeded.' 'INFO'
            return $true
        }

        Write-Log ('Force-Foreground: even ALT-key trick failed. Current fg=' + $newFore2 + ' target=' + $TargetHwndInt64) 'WARN'
        return $false
    } catch { Write-Log ('Force-Foreground exception: ' + $_) 'WARN'; return $false }
}

function Send-WdWakeContinue {
    param(
        [Parameter(Mandatory)] [string]$ExactTabTitle,
        [Parameter(Mandatory)] [string]$ExpectedRuntimeId,
        [Parameter(Mandatory)] [int64]$ExpectedWindowHandle,
        [Parameter(Mandatory)] [int]$MinimumUserIdleSeconds,
        [Parameter(Mandatory)] [int]$ExpectedLeadProcessId,
        [Parameter(Mandatory)] [string]$ExpectedLeadProcessStartUtc
    )

    if (-not (Test-WdProcessGeneration `
            -ProcessId $ExpectedLeadProcessId `
            -ExpectedStartUtc $ExpectedLeadProcessStartUtc)) {
        Write-Log 'Wake continue blocked: Lead process generation changed.' 'SAFE-SKIP'
        return $false
    }

    # Selecting the tab is allowed only after the first fail-closed safety
    # check. The same tab generation, HWND, input marker, busy state, prompt
    # state, idle gate, and foreground are rechecked immediately before send.
    $idleSeconds = Get-UserIdleSeconds
    $preSelectSnapshot = Get-WdExactWakeTabSnapshot `
        -ExactTabTitle $ExactTabTitle `
        -ExpectedRuntimeId $ExpectedRuntimeId
    $preSelectDisposition = Get-WdWakeIdentityDisposition `
        -ExactTabCount $preSelectSnapshot.ExactTabCount `
        -TabGenerationMatches $preSelectSnapshot.TabGenerationMatches `
        -WindowHandle $preSelectSnapshot.WindowHandle `
        -UserIdleSeconds $idleSeconds `
        -MinimumUserIdleSeconds $MinimumUserIdleSeconds `
        -ExpectedWindowHandle $ExpectedWindowHandle
    if ($preSelectDisposition -cne 'ready') {
        Write-Log ("Wake continue pre-select check blocked: $preSelectDisposition") 'SAFE-SKIP'
        return $false
    }

    $snapshot = Get-WdExactWakeTabSnapshot `
        -ExactTabTitle $ExactTabTitle `
        -ExpectedRuntimeId $ExpectedRuntimeId `
        -SelectTab
    $postSelectIdleSeconds = Get-UserIdleSeconds
    $postSelectDisposition = Get-WdWakeSafetyDisposition `
        -ExactTabCount $snapshot.ExactTabCount `
        -TabGenerationMatches $snapshot.TabGenerationMatches `
        -WindowHandle $snapshot.WindowHandle `
        -InputSurfaceReady $snapshot.InputSurfaceReady `
        -CodexBusy $snapshot.CodexBusy `
        -ConfirmationPromptActive $snapshot.ConfirmationPromptActive `
        -UserIdleSeconds $postSelectIdleSeconds `
        -MinimumUserIdleSeconds $MinimumUserIdleSeconds `
        -ExpectedWindowHandle $ExpectedWindowHandle
    if ($postSelectDisposition -cne 'ready') {
        Write-Log ("Wake continue post-select check blocked: $postSelectDisposition") 'SAFE-SKIP'
        return $false
    }

    if (-not (Force-Foreground `
            -TargetHwndInt64 $snapshot.WindowHandle `
            -NoAltKeyTrick)) {
        Write-Log 'Wake continue blocked: exact target could not be made foreground.' 'SAFE-SKIP'
        return $false
    }
    try {
        $snapshot.TextElement.SetFocus()
    } catch {
        Write-Log ('Wake continue blocked: input focus failed: ' + $_) 'SAFE-SKIP'
        return $false
    }
    Start-Sleep -Milliseconds 150

    $freshIdleSeconds = Get-UserIdleSeconds
    $fresh = Get-WdExactWakeTabSnapshot `
        -ExactTabTitle $ExactTabTitle `
        -ExpectedRuntimeId $ExpectedRuntimeId
    $freshDisposition = Get-WdWakeSafetyDisposition `
        -ExactTabCount $fresh.ExactTabCount `
        -TabGenerationMatches $fresh.TabGenerationMatches `
        -WindowHandle $fresh.WindowHandle `
        -InputSurfaceReady $fresh.InputSurfaceReady `
        -CodexBusy $fresh.CodexBusy `
        -ConfirmationPromptActive $fresh.ConfirmationPromptActive `
        -UserIdleSeconds $freshIdleSeconds `
        -MinimumUserIdleSeconds $MinimumUserIdleSeconds `
        -ExpectedWindowHandle $ExpectedWindowHandle
    if ($freshDisposition -cne 'ready') {
        Write-Log ("Wake continue final check blocked: $freshDisposition") 'SAFE-SKIP'
        return $false
    }
    try {
        $fresh.TextElement.SetFocus()
    } catch {
        Write-Log ('Wake continue final focus failed: ' + $_) 'SAFE-SKIP'
        return $false
    }

    # This is the final operator/process/focus fence. No UIA traversal or
    # synthetic focus workaround occurs between this sample and the literal
    # text injection.
    $finalIdleSeconds = Get-UserIdleSeconds
    if (
        $MinimumUserIdleSeconds -gt 0 -and
        ($null -eq $finalIdleSeconds -or
            $finalIdleSeconds -lt $MinimumUserIdleSeconds)
    ) {
        Write-Log 'Wake continue blocked: operator became active or idle became unknown before send.' 'SAFE-SKIP'
        return $false
    }
    if (-not (Test-WdProcessGeneration `
            -ProcessId $ExpectedLeadProcessId `
            -ExpectedStartUtc $ExpectedLeadProcessStartUtc)) {
        Write-Log 'Wake continue blocked: Lead process generation changed before send.' 'SAFE-SKIP'
        return $false
    }
    if (
        [Win32FocusV3]::GetForegroundWindow().ToInt64() -ne
            $ExpectedWindowHandle
    ) {
        Write-Log 'Wake continue blocked: foreground changed before send.' 'SAFE-SKIP'
        return $false
    }

    try {
        # One fixed sequence closes the text/Enter interleave window. Sampling
        # desktop idle after SendKeys would be self-defeating because synthetic
        # input may update GetLastInputInfo.
        [System.Windows.Forms.SendKeys]::SendWait(
            $script:WdBridgeWakePrompt + '{ENTER}'
        )
    } catch {
        Write-Log ('Wake continue dispatch failed: ' + $_) 'ERROR'
        return $false
    }

    $receiptDeadline = (Get-Date).AddSeconds(4)
    do {
        Start-Sleep -Milliseconds 120
        if (-not (Test-WdProcessGeneration `
                -ProcessId $ExpectedLeadProcessId `
                -ExpectedStartUtc $ExpectedLeadProcessStartUtc)) {
            Write-Log 'Wake continue receipt failed: Lead process generation changed.' 'ERROR'
            return $false
        }
        $submittedSnapshot = Get-WdExactWakeTabSnapshot `
            -ExactTabTitle $ExactTabTitle `
            -ExpectedRuntimeId $ExpectedRuntimeId
        $submittedReceipt = Get-WdWakeSubmitReceiptDisposition `
            -ExactTabCount $submittedSnapshot.ExactTabCount `
            -TabGenerationMatches $submittedSnapshot.TabGenerationMatches `
            -WindowHandle $submittedSnapshot.WindowHandle `
            -ExpectedWindowHandle $ExpectedWindowHandle `
            -SnapshotComplete $submittedSnapshot.SnapshotComplete `
            -InputSurfaceReady $submittedSnapshot.InputSurfaceReady `
            -CodexBusy $submittedSnapshot.CodexBusy `
            -ConfirmationPromptActive $submittedSnapshot.ConfirmationPromptActive `
            -ContinueEchoAdvanced (
                [int]$submittedSnapshot.ContinueEchoCount -gt
                    [int]$fresh.ContinueEchoCount
            )
        if ($submittedReceipt -ceq 'submitted_confirmed') {
            Write-Log 'Wake continue receipt confirmed on the pinned Lead generation.' 'ACTION'
            return $true
        }
    } while ((Get-Date) -lt $receiptDeadline)

    Write-Log ("Wake continue submit receipt timed out: $submittedReceipt") 'ERROR'
    return $false
}

function Test-PromptStillPresent {
    param($Element)
    try {
        $tp = $Element.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
        if ($null -eq $tp) { return $false }
        $text = $tp.DocumentRange.GetText(-1)
        if ([string]::IsNullOrWhiteSpace($text)) { return $false }
        $tail = ($text -split "`n") | Select-Object -Last 40
        $tailText = $tail -join "`n"
        $hasPress = $tailText -match 'Press enter to confirm or esc to cancel'
        $hasYes1  = $tailText -match '1\.\s+Yes,\s+proceed'
        return ($hasPress -or $hasYes1)
    } catch { return $false }
}

function Send-WdLeadPermissionYesEnter {
    param(
        [Parameter(Mandatory)] [string]$ExpectedCommand,
        [Parameter(Mandatory)] [string]$ExpectedRuntimeId,
        [Parameter(Mandatory)] [int64]$ExpectedWindowHandle,
        [Parameter(Mandatory)] [int]$MinimumUserIdleSeconds,
        [Parameter(Mandatory)] [int]$ExpectedLeadProcessId,
        [Parameter(Mandatory)] [string]$ExpectedLeadProcessStartUtc
    )

    for ($attempt = 1; $attempt -le $MaxSendRetries; $attempt++) {
        if (-not (Test-WdProcessGeneration `
                -ProcessId $ExpectedLeadProcessId `
                -ExpectedStartUtc $ExpectedLeadProcessStartUtc)) {
            Write-Log 'Lead permission send blocked: Lead process generation changed.' 'SAFE-SKIP'
            return $false
        }
        $idleSeconds = Get-UserIdleSeconds
        $snapshot = Get-WdExactWakeTabSnapshot `
            -ExactTabTitle 'codex-lead-1' `
            -ExpectedRuntimeId $ExpectedRuntimeId
        $identity = Get-WdWakeIdentityDisposition `
            -ExactTabCount $snapshot.ExactTabCount `
            -TabGenerationMatches $snapshot.TabGenerationMatches `
            -WindowHandle $snapshot.WindowHandle `
            -UserIdleSeconds $idleSeconds `
            -MinimumUserIdleSeconds $MinimumUserIdleSeconds `
            -ExpectedWindowHandle $ExpectedWindowHandle
        if (
            $identity -cne 'ready' -or
            -not $snapshot.SnapshotComplete -or
            -not $snapshot.ConfirmationPromptActive -or
            [string]$snapshot.PromptCommand -cne $ExpectedCommand
        ) {
            Write-Log ("Lead permission send blocked before focus: $identity") 'SAFE-SKIP'
            return $false
        }
        if (
            [Win32FocusV3]::GetForegroundWindow().ToInt64() -ne
                $ExpectedWindowHandle
        ) {
            Write-Log 'Lead permission send blocked: pinned Lead window is not foreground.' 'SAFE-SKIP'
            return $false
        }
        try {
            $snapshot.TextElement.SetFocus()
        } catch {
            Write-Log ('Lead permission send blocked: input focus failed: ' + $_) 'SAFE-SKIP'
            return $false
        }
        Start-Sleep -Milliseconds 120

        $freshIdleSeconds = Get-UserIdleSeconds
        $fresh = Get-WdExactWakeTabSnapshot `
            -ExactTabTitle 'codex-lead-1' `
            -ExpectedRuntimeId $ExpectedRuntimeId
        $freshIdentity = Get-WdWakeIdentityDisposition `
            -ExactTabCount $fresh.ExactTabCount `
            -TabGenerationMatches $fresh.TabGenerationMatches `
            -WindowHandle $fresh.WindowHandle `
            -UserIdleSeconds $freshIdleSeconds `
            -MinimumUserIdleSeconds $MinimumUserIdleSeconds `
            -ExpectedWindowHandle $ExpectedWindowHandle
        if (
            $freshIdentity -cne 'ready' -or
            -not $fresh.SnapshotComplete -or
            -not $fresh.ConfirmationPromptActive -or
            [string]$fresh.PromptCommand -cne $ExpectedCommand -or
            -not (Test-WdProcessGeneration `
                -ProcessId $ExpectedLeadProcessId `
                -ExpectedStartUtc $ExpectedLeadProcessStartUtc) -or
            [Win32FocusV3]::GetForegroundWindow().ToInt64() -ne
                $ExpectedWindowHandle
        ) {
            Write-Log ("Lead permission send blocked by final fence: $freshIdentity") 'SAFE-SKIP'
            return $false
        }
        try {
            [System.Windows.Forms.SendKeys]::SendWait('y{ENTER}')
        } catch {
            Write-Log ('Lead permission SendKeys failed: ' + $_) 'ERROR'
            continue
        }

        $receiptDeadline = (Get-Date).AddSeconds(2)
        $consecutiveDismissalReceipts = 0
        do {
            Start-Sleep -Milliseconds 120
            if (-not (Test-WdProcessGeneration `
                    -ProcessId $ExpectedLeadProcessId `
                    -ExpectedStartUtc $ExpectedLeadProcessStartUtc)) {
                Write-Log 'Lead permission receipt failed: Lead process generation changed.' 'ERROR'
                return $false
            }
            $receipt = Get-WdExactWakeTabSnapshot `
                -ExactTabTitle 'codex-lead-1' `
                -ExpectedRuntimeId $ExpectedRuntimeId
            $receiptDisposition = Get-WdLeadPermissionReceiptDisposition `
                -ExactTabCount $receipt.ExactTabCount `
                -TabGenerationMatches $receipt.TabGenerationMatches `
                -WindowHandle $receipt.WindowHandle `
                -ExpectedWindowHandle $ExpectedWindowHandle `
                -SnapshotComplete $receipt.SnapshotComplete `
                -ConfirmationPromptActive $receipt.ConfirmationPromptActive `
                -PromptCommand ([string]$receipt.PromptCommand) `
                -ExpectedCommand $ExpectedCommand `
                -VisibleTextChanged (
                    [string]$receipt.VisibleTextSha256 -cne
                        [string]$fresh.VisibleTextSha256
                )
            if ($receiptDisposition -ceq 'dismissal_candidate') {
                $consecutiveDismissalReceipts++
            } else {
                $consecutiveDismissalReceipts = 0
            }
            if ($consecutiveDismissalReceipts -ge 2) {
                Write-Log 'Lead permission receipt confirmed on the pinned generation.' 'INFO'
                return $true
            }
        } while ((Get-Date) -lt $receiptDeadline)
        Write-Log ("Lead permission attempt $attempt was not confirmed; retrying through all fences.") 'WARN'
    }
    Write-Log ('Lead permission send failed after ' + $MaxSendRetries + ' fenced attempts.') 'ERROR'
    return $false
}

function Send-YesEnter {
    param($Window, $TextElement, [int64]$TargetHwndInt64)
    if ($TargetHwndInt64 -eq 0) { Write-Log 'Target window has no native HWND, cannot verify focus.' 'WARN' }
    for ($attempt = 1; $attempt -le $MaxSendRetries; $attempt++) {
        $isForeground = $false
        if ($TargetHwndInt64 -ne 0) {
            $foreHwndInt = [Win32FocusV3]::GetForegroundWindow().ToInt64()
            if ($foreHwndInt -eq $TargetHwndInt64) { $isForeground = $true }
            else {
                $isForeground = Force-Foreground -TargetHwndInt64 $TargetHwndInt64
                if (-not $isForeground) {
                    $foreHwndInt2 = [Win32FocusV3]::GetForegroundWindow().ToInt64()
                    Write-Log ('Attempt ' + $attempt + ': could not bring target to foreground (currently HWND ' + $foreHwndInt2 + ' is foreground, target is ' + $TargetHwndInt64 + '). Retrying after 1.5s.') 'WARN'
                    Start-Sleep -Milliseconds 1500
                    continue
                }
            }
        } else { try { $Window.SetFocus() } catch { } }
        try {
            Start-Sleep -Milliseconds 350
            [System.Windows.Forms.SendKeys]::SendWait('y')
            Start-Sleep -Milliseconds 120
            [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
        } catch { Write-Log ('Attempt ' + $attempt + ': SendKeys threw: ' + $_) 'ERROR'; Start-Sleep -Milliseconds 1000; continue }
        Start-Sleep -Milliseconds 1200
        $stillPresent = $true
        if ($null -ne $TextElement) { $stillPresent = Test-PromptStillPresent -Element $TextElement.Element }
        else { $stillPresent = $false }
        if (-not $stillPresent) {
            if ($attempt -eq 1) { Write-Log 'Send confirmed: prompt dismissed on first attempt.' 'INFO' }
            else { Write-Log ('Send confirmed on attempt ' + $attempt + '.') 'INFO' }
            return $true
        }
        Write-Log ('Attempt ' + $attempt + ': prompt still present after send. Retrying.') 'WARN'
        Start-Sleep -Milliseconds 1500
    }
    Write-Log ('Send failed after ' + $MaxSendRetries + ' attempts. Leaving prompt for human.') 'ERROR'
    return $false
}

if ($ListWindows) {
    Write-Host ''
    Write-Host '=== Windows Terminal-like windows ===' -ForegroundColor Cyan
    $wins = Get-AllTerminalWindows
    if ($wins.Count -eq 0) { Write-Host 'No matching windows found.' -ForegroundColor Yellow; return }
    $i = 0
    foreach ($w in $wins) {
        $i = $i + 1
        $wname = ''; try { $wname = $w.Current.Name } catch { }
        Write-Host ''
        Write-Host ('--- Window ' + $i + ' (Name: ' + $wname + ') ---') -ForegroundColor Cyan
        $hwnd = Get-ElementHwndInt64 -Element $w
        Write-Host ('  HWND (Int64): ' + $hwnd)
        $textEls = Get-TextElementsFromWindow -Win $w
        if ($textEls.Count -eq 0) { Write-Host '  (no text-bearing descendants found)'; continue }
        $j = 0
        foreach ($t in $textEls) {
            $j = $j + 1
            $len = 0; if ($null -ne $t.Text) { $len = $t.Text.Length }
            Write-Host ('  Text element ' + $j + ': class=' + $t.ClassName + ' aid=' + $t.AutomationId + ' name=' + $t.Name + ' len=' + $len)
            if ($len -gt 0) {
                $sample = $t.Text
                if ($sample.Length -gt 300) { $sample = $sample.Substring($sample.Length - 300) }
                Write-Host '    Last 300 chars:'
                Write-Host ('      ' + ($sample -replace "`r`n", "`n      " -replace "`n", "`n      "))
                $cmd = Find-PromptCommand -Text $t.Text
                if ($null -ne $cmd) { Write-Host ('    >>> ACTIVE CODEX PROMPT: ' + $cmd) -ForegroundColor Green }
            }
        }
    }
    Write-Host ''
    return
}

if ($AllowAll) {
    Write-Host ''
    Write-Host '#################################################################' -ForegroundColor Red
    Write-Host '#  >>> ALLOWALL MODE -- DANGEROUS <<<                           #' -ForegroundColor Red
    Write-Host '#  Press Ctrl+C in 5 seconds to abort.                          #' -ForegroundColor Red
    Write-Host '#################################################################' -ForegroundColor Red
    Write-Host ''
    Start-Sleep -Seconds 5
} elseif ($YesToAll) {
    Write-Host ''
    Write-Host '*****************************************************************' -ForegroundColor Yellow
    Write-Host '*  YESTOALL MODE ACTIVE                                         *' -ForegroundColor Yellow
    Write-Host '*****************************************************************' -ForegroundColor Yellow
    Write-Host ''
    Start-Sleep -Seconds 3
}

$savedAllNighterState = $null
[int64]$LockedHwndInt64 = 0
$LockedWindowName = $null
$StrictTabTitleMode = -not [string]::IsNullOrWhiteSpace($TabTitle)
$wakeLockStream = $null
$wakeSentinelPath = ''
$wakeInflightPath = ''
$wakeAttemptPath = ''
$wakeTabRuntimeId = ''
[int64]$wakeWindowHandle = 0
$lastWakeDisposition = ''
$watcherProcessStart = Get-WdProcessStartUtc -ProcessId $PID
$watcherProcessStartUtc = if ($null -eq $watcherProcessStart) {
    ''
} else {
    $watcherProcessStart.UtcDateTime.ToString(
        'o',
        [Globalization.CultureInfo]::InvariantCulture
    )
}
$readyRecordWritten = $false

try {
    if ($ContinueOnWake) {
        if ($TabTitle -cne 'codex-lead-1') {
            throw 'ContinueOnWake is restricted to exact TabTitle codex-lead-1.'
        }
        if ($MinUserIdleSeconds -lt 60) {
            throw 'ContinueOnWake requires MinUserIdleSeconds >= 60.'
        }
        if ([string]::IsNullOrWhiteSpace($WakeRuntimeRoot)) {
            throw 'ContinueOnWake requires WakeRuntimeRoot.'
        }
        if (
            $LeadProcessId -le 0 -or
            $null -eq (ConvertTo-WdWakeUtc -Value $LeadProcessStartUtc) -or
            -not (Test-WdProcessGeneration `
                -ProcessId $LeadProcessId `
                -ExpectedStartUtc $LeadProcessStartUtc)
        ) {
            throw 'ContinueOnWake requires the live pinned Lead process generation.'
        }
        if (
            [string]::IsNullOrWhiteSpace($ReadyPath) -or
            [string]::IsNullOrWhiteSpace($watcherProcessStartUtc)
        ) {
            throw 'ContinueOnWake requires ReadyPath and the watcher process generation.'
        }
        $wakeRootItem = Get-Item -LiteralPath $WakeRuntimeRoot -Force -ErrorAction Stop
        if (
            -not $wakeRootItem.PSIsContainer -or
            ($wakeRootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            ($wakeRootItem.PSObject.Properties['LinkType'] -and
                $null -ne $wakeRootItem.LinkType)
        ) {
            throw "WakeRuntimeRoot must be a plain directory: $WakeRuntimeRoot"
        }
        $WakeRuntimeRoot = [IO.Path]::GetFullPath($wakeRootItem.FullName).TrimEnd('\')
        $wakeSentinelPath = Join-Path $WakeRuntimeRoot 'wake_codex-lead-1'
        $wakeInflightPath = Join-Path $WakeRuntimeRoot 'wake_codex-lead-1.continue-inflight'
        $wakeAttemptPath = Join-Path $WakeRuntimeRoot 'wake_codex-lead-1.continue-last-attempt'
        $wakeLockPath = Join-Path $WakeRuntimeRoot 'wake_codex-lead-1.continue.lock'
        $ReadyPath = [IO.Path]::GetFullPath($ReadyPath)
        $readyParent = Split-Path -Parent $ReadyPath
        $readyParentItem = Get-Item -LiteralPath $readyParent -Force -ErrorAction Stop
        if (
            -not $readyParentItem.PSIsContainer -or
            ($readyParentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            ($readyParentItem.PSObject.Properties['LinkType'] -and
                $null -ne $readyParentItem.LinkType)
        ) {
            throw "ReadyPath parent must be a plain directory: $readyParent"
        }
        foreach ($wakePath in @(
                $wakeSentinelPath,
                $wakeInflightPath,
                $wakeAttemptPath,
                $wakeLockPath,
                $ReadyPath
            )) {
            Assert-WdWakePathIsPlainOrMissing -Path $wakePath
        }
        if (-not $DryRun) {
            try {
                $wakeLockStream = [IO.File]::Open(
                    $wakeLockPath,
                    [IO.FileMode]::OpenOrCreate,
                    [IO.FileAccess]::ReadWrite,
                    [IO.FileShare]::None
                )
            } catch {
                throw "ContinueOnWake singleton lock is already held or unusable: $wakeLockPath"
            }
        }

        $initialWakeTab = Get-WdExactWakeTabSnapshot -ExactTabTitle $TabTitle
        if (
            $initialWakeTab.ExactTabCount -ne 1 -or
            [string]::IsNullOrWhiteSpace($initialWakeTab.TabRuntimeId) -or
            $initialWakeTab.WindowHandle -eq 0
        ) {
            throw 'ContinueOnWake requires one exact Lead tab with stable runtime ID and HWND at startup.'
        }
        $wakeTabRuntimeId = $initialWakeTab.TabRuntimeId
        $wakeWindowHandle = $initialWakeTab.WindowHandle
        Write-Log (
            'ContinueOnWake pinned exact Lead tab generation runtimeId=' +
            $wakeTabRuntimeId + ' hwnd=' + $wakeWindowHandle +
            ' sentinel="' + $wakeSentinelPath + '" cooldown=' +
            $WakeCooldownSeconds + 's idle=' + $MinUserIdleSeconds + 's.'
        ) 'INFO'
        if (-not $DryRun) {
            Write-WdPromptWatcherReadyRecord `
                -Path $ReadyPath `
                -WatcherProcessStartUtc $watcherProcessStartUtc `
                -ExpectedLeadProcessId $LeadProcessId `
                -ExpectedLeadProcessStartUtc $LeadProcessStartUtc `
                -ExactTabTitle $TabTitle `
                -TabRuntimeId $wakeTabRuntimeId `
                -WindowHandle $wakeWindowHandle `
                -WakeRoot $WakeRuntimeRoot
            $readyRecordWritten = $true
        }
    }

    if (-not $NoAllNighter) {
        $savedAllNighterState = Save-AllNighterState
        Write-Log ('AllNighter: saving original state -> ' + (Format-AllNighterDelta -Saved $savedAllNighterState)) 'INFO'
        Enable-AllNighter
        Write-Log 'AllNighter: ENABLED.' 'INFO'
    } else {
        Write-Log 'AllNighter: SKIPPED.' 'INFO'
    }

    if ($StrictTabTitleMode) {
        Write-Log ('STRICT TAB TITLE MODE: ONLY pressing y in windows whose name exactly equals "' + $TabTitle + '". No fallback.') 'INFO'
    }

    Write-Log 'FOCUS FIX v3: Win32FocusV3 type with IsIconic + ALT-key trick.' 'INFO'

    if ($AllowAll) { Write-Log 'ALLOWALL mode: DANGEROUS.' 'DANGEROUS-ALLOW' }

    Write-Log ('Watch-CodexPrompts started. TabTitle="' + $TabTitle + '" Strict=' + $StrictTabTitleMode + ' DryRun=' + $DryRun + ' YesToAll=' + $YesToAll + ' AllowAll=' + $AllowAll + ' AllNighter=' + (-not $NoAllNighter) + ' HwndLock=' + (-not $NoHwndLock) + ' ContinueOnWake=' + $ContinueOnWake) 'INFO'

    $lastSeenCommand = $null
    $deferredPromptCommand = $null
    $promptRetryAfterUtc = [DateTimeOffset]::MinValue
    $consecutiveFailures = 0
    $consecutiveTabMisses = 0

    while ($true) {
        try {
            if ($ContinueOnWake) {
                if (-not (Test-WdProcessGeneration `
                        -ProcessId $LeadProcessId `
                        -ExpectedStartUtc $LeadProcessStartUtc)) {
                    Write-Log 'ContinueOnWake stopping: pinned Lead process generation ended.' 'ERROR'
                    break
                }
                $generationSnapshot = Get-WdExactWakeTabSnapshot `
                    -ExactTabTitle $TabTitle `
                    -ExpectedRuntimeId $wakeTabRuntimeId
                $generationDisposition = Get-WdWakeIdentityDisposition `
                    -ExactTabCount $generationSnapshot.ExactTabCount `
                    -TabGenerationMatches $generationSnapshot.TabGenerationMatches `
                    -WindowHandle $generationSnapshot.WindowHandle `
                    -UserIdleSeconds 0 `
                    -MinimumUserIdleSeconds 0 `
                    -ExpectedWindowHandle $wakeWindowHandle
                if ($generationDisposition -cne 'ready') {
                    Write-Log ("ContinueOnWake stopping: terminal generation lost ($generationDisposition).") 'ERROR'
                    break
                }
            }

            $wins = Get-AllTerminalWindows
            $targetWin = $null
            $targetCmd = $null
            $targetTextEl = $null
            $targetWname = $null

            if ($wins.Count -gt 0) {
                if ($StrictTabTitleMode) {
                    $tabMatched = $false
                    foreach ($w in $wins) {
                        $wname = ''; try { $wname = $w.Current.Name } catch { }
                        if ([string]$wname -cne $TabTitle) { continue }
                        $tabMatched = $true
                        $textEls = Get-TextElementsFromWindow -Win $w
                        foreach ($t in $textEls) {
                            $cmd = Find-PromptCommand -Text $t.Text
                            if ($null -ne $cmd) { $targetWin = $w; $targetCmd = $cmd; $targetTextEl = $t; $targetWname = $wname; break }
                        }
                        if ($null -ne $targetCmd) { break }
                    }
                    if (-not $tabMatched) {
                        $consecutiveTabMisses = $consecutiveTabMisses + 1
                        if ($consecutiveTabMisses -eq 1 -or ($consecutiveTabMisses % 20) -eq 0) {
                            Write-Log ('STRICT MODE: no tab whose name exactly equals "' + $TabTitle + '". Idle.') 'WARN'
                        }
                    } else {
                        $consecutiveTabMisses = 0
                    }
                } else {
                    foreach ($w in $wins) {
                        $textEls = Get-TextElementsFromWindow -Win $w
                        foreach ($t in $textEls) {
                            $cmd = Find-PromptCommand -Text $t.Text
                            if ($null -ne $cmd) {
                                $wname = ''; try { $wname = $w.Current.Name } catch { }
                                $targetWin = $w; $targetCmd = $cmd; $targetTextEl = $t; $targetWname = $wname; break
                            }
                        }
                        if ($null -ne $targetCmd) { break }
                    }
                }
            }

            if ($wins.Count -eq 0) {
                $consecutiveFailures = $consecutiveFailures + 1
                if ($consecutiveFailures -eq 1 -or ($consecutiveFailures % 20) -eq 0) {
                    Write-Log ('No Windows Terminal windows visible (attempt ' + $consecutiveFailures + ').') 'WARN'
                }
            } elseif ($null -eq $targetCmd) {
                $consecutiveFailures = 0
                $lastSeenCommand = $null
                $deferredPromptCommand = $null
                $promptRetryAfterUtc = [DateTimeOffset]::MinValue
            } else {
                $consecutiveFailures = 0
                $retryDeferredPrompt = (
                    $null -ne $deferredPromptCommand -and
                    $targetCmd -ceq $deferredPromptCommand -and
                    [DateTimeOffset]::UtcNow -ge $promptRetryAfterUtc
                )
                if ($targetCmd -cne $lastSeenCommand -or $retryDeferredPrompt) {
                    $promptHandled = $true
                    [int64]$thisHwndInt64 = Get-ElementHwndInt64 -Element $targetWin
                    Write-Log ('Detected prompt (window="' + $targetWname + '" hwnd=' + $thisHwndInt64 + ' lockedHwnd=' + $LockedHwndInt64 + ' lockedName="' + $LockedWindowName + '" strictTab=' + $StrictTabTitleMode + ') for: ' + $targetCmd) 'PROMPT'

                    $hwndLockBlocks = $false
                    if (-not $NoHwndLock -and -not $StrictTabTitleMode) {
                        if ($LockedHwndInt64 -ne 0) {
                            if ($thisHwndInt64 -eq 0) {
                                Write-Log ('HWND-LOCK: HWND unknown but lock active. SAFE FAIL.') 'WARN'
                                $hwndLockBlocks = $true
                            } elseif ($thisHwndInt64 -ne $LockedHwndInt64) {
                                Write-Log ('HWND-LOCK: prompt in HWND ' + $thisHwndInt64 + ' but locked to HWND ' + $LockedHwndInt64 + '. NOT pressing y here.') 'WARN'
                                $hwndLockBlocks = $true
                            }
                        }
                    }

                    if (-not $hwndLockBlocks) {
                        if (Test-CommandSafe -Command $targetCmd -YesToAllMode $YesToAll -AllowAllMode $AllowAll) {
                            $idleSeconds = Get-UserIdleSeconds
                            if (
                                $MinUserIdleSeconds -gt 0 -and
                                ($null -eq $idleSeconds -or
                                    $idleSeconds -lt $MinUserIdleSeconds)
                            ) {
                                $idleText = if ($null -eq $idleSeconds) { 'unknown' } else { "${idleSeconds}s" }
                                Write-Log ("Operator active or idle unknown: desktop idle $idleText; minimum ${MinUserIdleSeconds}s; not sending y+Enter.") 'SAFE-SKIP'
                                $promptHandled = $false
                            } elseif ($DryRun) {
                                Write-Log "DryRun=on, would press 'y' + Enter." 'DRYRUN'
                            } else {
                                if ($AllowAll) { Write-Log 'DANGEROUSLY auto-approving (AllowAll).' 'DANGEROUS-ACTION' }
                                else { Write-Log 'Auto-approving: sending y + Enter.' 'ACTION' }
                                $ok = if ($ContinueOnWake) {
                                    Send-WdLeadPermissionYesEnter `
                                        -ExpectedCommand $targetCmd `
                                        -ExpectedRuntimeId $wakeTabRuntimeId `
                                        -ExpectedWindowHandle $wakeWindowHandle `
                                        -MinimumUserIdleSeconds $MinUserIdleSeconds `
                                        -ExpectedLeadProcessId $LeadProcessId `
                                        -ExpectedLeadProcessStartUtc $LeadProcessStartUtc
                                } else {
                                    Send-YesEnter `
                                        -Window $targetWin `
                                        -TextElement $targetTextEl `
                                        -TargetHwndInt64 $thisHwndInt64
                                }
                                if ($ok) {
                                    if (-not $NoHwndLock -and -not $StrictTabTitleMode -and $LockedHwndInt64 -eq 0 -and $thisHwndInt64 -ne 0) {
                                        $LockedHwndInt64 = $thisHwndInt64
                                        $LockedWindowName = $targetWname
                                        Write-Log ('HWND-LOCK ACTIVATED: locked to HWND ' + $LockedHwndInt64 + '.') 'INFO'
                                    }
                                } else {
                                    Write-Log 'CONFIRMED FAILURE: prompt was not dismissed.' 'ERROR'
                                    $promptHandled = $false
                                }
                            }
                        }
                    }
                    $lastSeenCommand = $targetCmd
                    if ($promptHandled) {
                        $deferredPromptCommand = $null
                        $promptRetryAfterUtc = [DateTimeOffset]::MinValue
                    } else {
                        $deferredPromptCommand = $targetCmd
                        $promptRetryAfterUtc = [DateTimeOffset]::UtcNow.AddSeconds(5)
                    }
                }
            }

            # Permission prompts always win. A wake sentinel is considered only
            # when no confirmation command is present anywhere in the target.
            if ($ContinueOnWake -and $null -eq $targetCmd) {
                $hasWakeState = (
                    (Test-Path -LiteralPath $wakeSentinelPath) -or
                    (Test-Path -LiteralPath $wakeInflightPath)
                )
                if ($hasWakeState) {
                    $wakeNowUtc = [DateTimeOffset]::UtcNow
                    $wakeDisposition = Invoke-WdWakeSentinelTransaction `
                        -SentinelPath $wakeSentinelPath `
                        -InflightPath $wakeInflightPath `
                        -AttemptPath $wakeAttemptPath `
                        -CooldownSeconds $WakeCooldownSeconds `
                        -NowUtc $wakeNowUtc `
                        -SendAction { $false } `
                        -DryRun
                    if ($wakeDisposition -in @(
                            'dry_run_ready',
                            'dry_run_inflight_recoverable',
                            'dry_run_inflight_recoverable_without_attempt'
                        )) {
                        $wakeIdleSeconds = Get-UserIdleSeconds
                        $wakeSnapshot = Get-WdExactWakeTabSnapshot `
                            -ExactTabTitle $TabTitle `
                            -ExpectedRuntimeId $wakeTabRuntimeId
                        $wakeDisposition = Get-WdWakeIdentityDisposition `
                            -ExactTabCount $wakeSnapshot.ExactTabCount `
                            -TabGenerationMatches $wakeSnapshot.TabGenerationMatches `
                            -WindowHandle $wakeSnapshot.WindowHandle `
                            -UserIdleSeconds $wakeIdleSeconds `
                            -MinimumUserIdleSeconds $MinUserIdleSeconds `
                            -ExpectedWindowHandle $wakeWindowHandle
                    }
                    if ($DryRun -and $wakeDisposition -ceq 'ready') {
                        $wakeDisposition = 'dry_run_identity_ready_no_ui_mutation'
                    } elseif ($wakeDisposition -ceq 'ready') {
                        $selectedWakeSnapshot = Get-WdExactWakeTabSnapshot `
                            -ExactTabTitle $TabTitle `
                            -ExpectedRuntimeId $wakeTabRuntimeId `
                            -SelectTab
                        $selectedWakeIdleSeconds = Get-UserIdleSeconds
                        $wakeDisposition = Get-WdWakeSafetyDisposition `
                            -ExactTabCount $selectedWakeSnapshot.ExactTabCount `
                            -TabGenerationMatches $selectedWakeSnapshot.TabGenerationMatches `
                            -WindowHandle $selectedWakeSnapshot.WindowHandle `
                            -InputSurfaceReady $selectedWakeSnapshot.InputSurfaceReady `
                            -CodexBusy $selectedWakeSnapshot.CodexBusy `
                            -ConfirmationPromptActive $selectedWakeSnapshot.ConfirmationPromptActive `
                            -UserIdleSeconds $selectedWakeIdleSeconds `
                            -MinimumUserIdleSeconds $MinUserIdleSeconds `
                            -ExpectedWindowHandle $wakeWindowHandle
                    }
                    if (-not $DryRun -and $wakeDisposition -ceq 'ready') {
                        $wakeDisposition = Invoke-WdWakeSentinelTransaction `
                            -SentinelPath $wakeSentinelPath `
                            -InflightPath $wakeInflightPath `
                            -AttemptPath $wakeAttemptPath `
                            -CooldownSeconds $WakeCooldownSeconds `
                            -NowUtc $wakeNowUtc `
                            -SendAction {
                                Send-WdWakeContinue `
                                    -ExactTabTitle $TabTitle `
                                    -ExpectedRuntimeId $wakeTabRuntimeId `
                                    -ExpectedWindowHandle $wakeWindowHandle `
                                    -MinimumUserIdleSeconds $MinUserIdleSeconds `
                                    -ExpectedLeadProcessId $LeadProcessId `
                                    -ExpectedLeadProcessStartUtc $LeadProcessStartUtc
                            }
                    }
                    if (
                        $wakeDisposition -cne $lastWakeDisposition -or
                        $wakeDisposition -in @('sent', 'send_failed_inflight_retained')
                    ) {
                        $level = if ($wakeDisposition -ceq 'sent') { 'INFO' } else { 'SAFE-SKIP' }
                        $wakeMessage = if ($wakeDisposition -ceq 'sent') {
                            'ContinueOnWake disposition: sent; confirmed generation consumed.'
                        } else {
                            "ContinueOnWake disposition: $wakeDisposition"
                        }
                        Write-Log $wakeMessage $level
                    }
                    $lastWakeDisposition = $wakeDisposition
                } else {
                    $lastWakeDisposition = ''
                }
            }
        } catch { Write-Log ('Loop error: ' + $_) 'ERROR' }
        Start-Sleep -Seconds $PollIntervalSeconds
    }
}
finally {
    if ($readyRecordWritten -and -not [string]::IsNullOrWhiteSpace($ReadyPath)) {
        Remove-WdOwnPromptWatcherReadyRecord `
            -Path $ReadyPath `
            -WatcherProcessStartUtc $watcherProcessStartUtc
        $readyRecordWritten = $false
    }
    if ($null -ne $wakeLockStream) {
        $wakeLockStream.Dispose()
        $wakeLockStream = $null
    }
    if ($null -ne $savedAllNighterState) {
        Restore-AllNighter -Saved $savedAllNighterState
        Write-Log ('AllNighter: RESTORED original state -> ' + (Format-AllNighterDelta -Saved $savedAllNighterState)) 'INFO'
    } else {
        Write-Log 'AllNighter: nothing to restore.' 'INFO'
    }
    if ($LockedHwndInt64 -ne 0) {
        Write-Log ('HWND-LOCK: was locked to HWND ' + $LockedHwndInt64 + ' (window="' + $LockedWindowName + '").') 'INFO'
    }
    Write-Log 'Watch-CodexPrompts stopped.' 'INFO'
}
