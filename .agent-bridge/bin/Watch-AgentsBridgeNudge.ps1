#requires -Version 5.1
<#
.SYNOPSIS
    Autonomous bridge-follow nudge watcher for the WaggleDance agent trio.

.DESCRIPTION
    Complements C:\Python\Watch-AgentsUnified.ps1 (do NOT modify that file).
    Watch-AgentsUnified watches WINDOW TEXT and nudges on-screen idle/stuck.
    THIS script watches BRIDGE STATE and catches the failure mode that the
    window watcher cannot see: an agent that is ALIVE (heartbeat job still
    emitting) but whose session has STOPPED READING the bridge -- so messages
    addressed to it sit unread (the 2026-05-29 codex-tools-1 case).

    Detection per agent (from shared\events.jsonl):
      heartbeatAge   = age of latest heartbeat/liveness event
      substantiveAge = age of latest non-heartbeat, non-(message/received) event
      readAge        = age of latest message/received ack (Read-AgentBridge.ps1
                       auto-emits these ONLY when the agent actually reads the
                       bridge -> fresh heartbeat + stale readAge = not following)
      openIncoming   = overdue surviving tasks after exact task-keyed
                       answer/requester-closeout reduction

    Classify (nudge trigger) when the session is alive but ignoring bridge work.
    Remediation (NEVER kills/relaunches a window -- that would lose the session's
    accumulated context): optional cheap bridge wake_request, then ACTIVATE the
    agent's window and send a short "jatka" keystroke so it resumes polling.

    Fully autonomous: runs in a background loop, no operator interaction.

.NOTES
    Additive file. Reuses proven mechanisms copied from Watch-AgentsUnified.ps1
    (window discovery + focus/SendKeys P/Invoke) and the events.jsonl tail/parse
    pattern from .agent-bridge\bin\Read-AgentBridge.ps1.
#>
[CmdletBinding()]
param(
    [string]   $TargetAgents          = 'codex-lead-1,codex-tools-1,claude-rco-1,claude-rco-2,fable-5',
    [string]   $RuntimeRoot           = '',
    [int]      $PollIntervalSeconds   = 45,
    [int]      $StartupDelaySeconds   = 0,
    [int]      $HeartbeatStaleSeconds = 300,
    [int]      $UnansweredSeconds     = 900,
    [int]      $NudgeCooldownSeconds  = 180,
    # Separate bridge-event cooldown. Window nudges may need a shorter cadence,
    # but bridge wake_request events are durable audit records and must not
    # flood the log when a tab stays stale or the watcher is started with a
    # very small window-nudge cooldown for testing.
    [int]      $BridgeWakeCooldownSeconds = 900,
    # B1 OPERATOR-ACTIVITY GUARD (focus-safety): never select a tab / SendKeys /
    # /clear while the operator is actively using the machine. Require the OS input
    # to have been idle at least this long before any physical send. Bridge
    # wake_request events are still emitted (audit) when skipped. Keep the
    # default effectively non-interactive; explicit one-shot ops may lower it
    # when foregrounding a terminal is acceptable. 0 disables the guard.
    [int]      $OperatorIdleGuardSeconds = 86400,
    # Smart guard: if an agent has been dark (no heartbeat) >= this many seconds, override the
    # operator-activity guard and nudge anyway -- a genuinely stuck agent wakes even while the
    # operator is at the machine, while brief active periods stay protected. 0 disables.
    [int]      $ForceWakeAfterDarkSeconds = 0,
    # B2 RCO-TRIGGER: agents whose wake_request / clear_redirect_requested events are
    # honored as an explicit fast trigger (a fresh wake_request from these nudges the
    # target promptly instead of waiting UnansweredSeconds).
    [string]   $RcoTriggerAgents      = 'claude-rco-1,claude-rco-2,operator',
    [string]   $NudgeText             = 'jatka - lue bridge ja vastaa avoimiin pyyntoihin',
    [string]   $WindowTitleMap        = '',
    [switch]   $NoBridgeWake,
    [switch]   $DryRun,
    [switch]   $OnceOnly,
    [string]   $InstanceName          = 'bridgenudge',
    # Test/ops overrides. Defaults preserve the existing machine-local paths.
    [string]   $LogPath               = '',
    [string]   $CsvPath               = '',
    [string]   $MarkerPrefix          = '',
    # --- /clear escalation (opt-in, destructive): when an agent stays stuck this
    #     long (no substantive output + open incoming) AND plain "jatka" nudges
    #     haven't helped, reset its context with /clear then a bootstrap line.
    #     OFF by default; turn on with -EnableClearEscalation once you've verified
    #     tab-targeting (-DryRun) selects the RIGHT tab.
    [switch]   $EnableClearEscalation,
    [int]      $ClearEscalationSeconds = 900,
    [int]      $ClearCooldownSeconds   = 1800,
    [string]   $BootstrapText         = 'jatka - lue bridge (Read-AgentBridge.ps1 -Agent <AGENT>) ja jatka avoimista tehtavista bridgen mukaan',
    # Agents that must NEVER be /clear-ed (context loss would be catastrophic).
    # Both RCOs are protected: they are the gate's independent reviewers and must
    # not have their review context wiped autonomously.
    [string]   $ClearNeverAgents      = 'claude-rco-1,claude-rco-2',
    # B3 RCO-TRIGGERED /clear: honor a `clear_redirect_requested` marker from an
    # RcoTriggerAgent to reset+redirect a wedged agent (operator chose RCO-triggered
    # over autonomous-timer /clear). ON by default; the autonomous deepStuck /clear
    # remains separately opt-in via -EnableClearEscalation.
    [switch]   $DisableRcoClearTrigger
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$StateHelperPath = Join-Path $PSScriptRoot 'BridgeNudgeState.ps1'
if (-not (Test-Path -LiteralPath $StateHelperPath -PathType Leaf)) {
    throw "canonical nudger state helper not found: $StateHelperPath"
}
. $StateHelperPath

# ------------------------------------------------------------------ paths / defaults
if (-not $RuntimeRoot) {
    $RuntimeRoot = if ($env:AGENT_BRIDGE_RUNTIME_ROOT) { [string]$env:AGENT_BRIDGE_RUNTIME_ROOT }
                   else { 'C:\Python\project2-master\.agent-bridge' }
}
$EventsPath       = Join-Path (Join-Path $RuntimeRoot 'shared') 'events.jsonl'
$WriteEventScript = Join-Path $PSScriptRoot 'Write-AgentEvent.ps1'
$dateStr          = Get-Date -Format 'yyyyMMdd'
if (-not $LogPath) {
    $LogPath = "C:\Python\watch-agents-bridgenudge-$InstanceName-$dateStr.log"
}
if (-not $CsvPath) {
    $CsvPath = "C:\Python\watch-agents-bridgenudge-$dateStr.csv"
}
if (-not $MarkerPrefix) {
    $MarkerPrefix = 'C:\Python\watch-agents-lastnudge-'
}

$agents = @($TargetAgents -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$clearNever = @($ClearNeverAgents -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$rcoTrigger = @($RcoTriggerAgents -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })

# Optional bridge-agent-id -> window-title-substring map, e.g.
#   "codex-tools-1=codex-tools-1-fresh;claude-rco-1=bridge-follow-nudge-watcher"
# Use when a window's title does NOT contain the bridge agent id (Claude/codex CLIs
# often set the tab title to the current activity). If unmapped, the agent id itself
# is used as the title substring.
$TitleMap = @{}
if ($WindowTitleMap) {
    foreach ($pair in ($WindowTitleMap -split ';')) {
        $kv = $pair -split '=', 2
        if ($kv.Count -eq 2 -and $kv[0].Trim()) { $TitleMap[$kv[0].Trim()] = $kv[1].Trim() }
    }
}

# ------------------------------------------------------------------ types (copied from Watch-AgentsUnified.ps1)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms

if (-not ('Win32FocusBridgeNudgeV1' -as [type])) {
    Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32FocusBridgeNudgeV1 {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);
    [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint idAttach, uint idAttachTo, bool fAttach);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
    [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
    public const int SW_RESTORE = 9;
    public const byte VK_MENU = 0x12;
    public const uint KEYEVENTF_KEYDOWN = 0x0;
    public const uint KEYEVENTF_KEYUP = 0x2;

    // B1 operator-activity guard: seconds since the last OS-wide keyboard/mouse input.
    [StructLayout(LayoutKind.Sequential)] public struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }
    [DllImport("user32.dll")] public static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);
    [DllImport("kernel32.dll")] public static extern uint GetTickCount();
    public static double IdleSeconds() {
        LASTINPUTINFO lii = new LASTINPUTINFO();
        lii.cbSize = (uint)Marshal.SizeOf(lii);
        if (!GetLastInputInfo(ref lii)) { return 0.0; }   // fail-closed: 0 => "operator active" => skip send
        uint now = GetTickCount();
        uint last = lii.dwTime;
        double ms = (now >= last) ? (now - last) : (uint.MaxValue - last + now);
        return ms / 1000.0;
    }
}
"@
}

# ------------------------------------------------------------------ logging
function Write-NudgeLog {
    param([string] $Message, [string] $Level = 'INFO')
    $ts = [DateTime]::Now.ToString('yyyy-MM-dd HH:mm:ss', [System.Globalization.CultureInfo]::InvariantCulture)
    $line = "[$ts] [$Level] $Message"
    Write-Host $line
    try { Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8 } catch { }
}

function Write-NudgeCsv {
    param([string]$Agent,[double]$HeartbeatAge,[double]$ReadAge,[double]$SubstantiveAge,[int]$OpenIncoming,[string]$Classification,[string]$Action)
    $ts = [DateTime]::UtcNow.ToString('yyyy-MM-ddTHH:mm:ssZ', [System.Globalization.CultureInfo]::InvariantCulture)
    function fmt($v) { if ([double]::IsInfinity($v)) { 'inf' } else { [int]$v } }
    $row = "{0},{1},{2},{3},{4},{5},{6},{7}" -f $ts,$Agent,(fmt $HeartbeatAge),(fmt $ReadAge),(fmt $SubstantiveAge),$OpenIncoming,$Classification,$Action
    try {
        if (-not (Test-Path -LiteralPath $CsvPath)) {
            Add-Content -LiteralPath $CsvPath -Value 'ts,agent,heartbeat_age,read_age,substantive_age,open_incoming,classification,action' -Encoding UTF8
        }
        Add-Content -LiteralPath $CsvPath -Value $row -Encoding UTF8
    } catch { }
}

# ------------------------------------------------------------------ bridge read (pattern from Read-AgentBridge.ps1 47-68)
function Read-BridgeEvents {
    param([string]$Path, [int]$MaxLines = 50000)
    $items = New-Object System.Collections.Generic.List[object]
    if (-not (Test-Path -LiteralPath $Path)) { return $items }
    $lines = @(Get-Content -Path $Path -Tail $MaxLines -Encoding UTF8)
    foreach ($line in $lines) {
        if (-not $line) { continue }
        try { [void]$items.Add(($line | ConvertFrom-Json)) } catch { }
    }
    return $items
}

function Get-TsUtc {
    param([object]$Value)
    if ($null -eq $Value) { return $null }
    if ($Value -is [DateTime]) { return $Value.ToUniversalTime() }
    $t = [string]$Value
    if (-not $t) { return $null }
    try {
        return [DateTime]::Parse($t, [System.Globalization.CultureInfo]::InvariantCulture,
            [System.Globalization.DateTimeStyles]::AssumeUniversal -bor [System.Globalization.DateTimeStyles]::AdjustToUniversal).ToUniversalTime()
    } catch { return $null }
}

# ------------------------------------------------------------------ window discovery + send (copied from Watch-AgentsUnified.ps1)
function Get-AllTopLevelWindows {
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $cond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::Window)
    try { return $root.FindAll([System.Windows.Automation.TreeScope]::Children, $cond) } catch { return @() }
}

function Get-AgentWindowHandle {
    param([string]$Agent)
    $pattern = if ($TitleMap.ContainsKey($Agent)) { $TitleMap[$Agent] } else { $Agent }
    if (-not $pattern) { return [System.IntPtr]::Zero }
    foreach ($win in (Get-AllTopLevelWindows)) {
        $name = ''
        try { $name = $win.Current.Name } catch { }
        if (-not $name) { continue }
        if ($name -like ('*' + $pattern + '*')) {
            try { return [System.IntPtr]$win.Current.NativeWindowHandle } catch { }
        }
    }
    return [System.IntPtr]::Zero
}

# Agents run as TABS inside one shared Windows Terminal window, so foregrounding
# the top-level window is NOT enough: SendKeys lands in whatever tab is ACTIVE.
# Select the agent's tab (UIAutomation TabItem.SelectionItemPattern.Select) FIRST
# so keystrokes -- especially a destructive /clear -- hit the intended agent.
# Returns the owning top-level Window handle on success, or Zero (then the
# caller MUST NOT send keys: fail-safe).
function Select-AgentTab {
    param([string]$Agent)
    $pattern = if ($TitleMap.ContainsKey($Agent)) { $TitleMap[$Agent] } else { $Agent }
    if (-not $pattern) { return [System.IntPtr]::Zero }
    $root = [System.Windows.Automation.AutomationElement]::RootElement
    $cond = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
        [System.Windows.Automation.ControlType]::TabItem)
    $tabs = @()
    try { $tabs = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond) } catch { return [System.IntPtr]::Zero }
    foreach ($tab in $tabs) {
        $name = ''
        try { $name = $tab.Current.Name } catch { }
        if (-not $name) { continue }
        if ($name -like ('*' + $pattern + '*')) {
            try {
                $sip = $tab.GetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern)
                $sip.Select()
                Start-Sleep -Milliseconds 400
            } catch { return [System.IntPtr]::Zero }
            # walk up to the owning top-level Window for the foreground handle
            try {
                $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
                $cur = $tab
                for ($i = 0; $i -lt 12 -and $cur -ne $null; $i++) {
                    if ($cur.Current.ControlType -eq [System.Windows.Automation.ControlType]::Window) {
                        return [System.IntPtr]$cur.Current.NativeWindowHandle
                    }
                    $cur = $walker.GetParent($cur)
                }
            } catch { }
            # fallback: title-based top-level lookup (title follows active tab)
            return (Get-AgentWindowHandle -Agent $Agent)
        }
    }
    return [System.IntPtr]::Zero
}

function Send-Keystrokes {
    param([System.IntPtr]$Handle, [string]$Keys, [bool]$AppendEnter = $true, [bool]$ClearLineBeforeSend = $false)
    if ([Win32FocusBridgeNudgeV1]::IsIconic($Handle)) {
        $null = [Win32FocusBridgeNudgeV1]::ShowWindow($Handle, [Win32FocusBridgeNudgeV1]::SW_RESTORE)
    }
    function Send-AltKeyTrick {
        try {
            [Win32FocusBridgeNudgeV1]::keybd_event([Win32FocusBridgeNudgeV1]::VK_MENU, 0, [Win32FocusBridgeNudgeV1]::KEYEVENTF_KEYDOWN, [UIntPtr]::Zero)
            Start-Sleep -Milliseconds 30
            [Win32FocusBridgeNudgeV1]::keybd_event([Win32FocusBridgeNudgeV1]::VK_MENU, 0, [Win32FocusBridgeNudgeV1]::KEYEVENTF_KEYUP, [UIntPtr]::Zero)
            Start-Sleep -Milliseconds 30
        } catch { }
    }
    function Try-Foreground {
        $fgWin = [Win32FocusBridgeNudgeV1]::GetForegroundWindow()
        if ($fgWin -eq $Handle) { return $true }
        $myTid = [Win32FocusBridgeNudgeV1]::GetCurrentThreadId()
        $fgTid = 0
        $null = [Win32FocusBridgeNudgeV1]::GetWindowThreadProcessId($fgWin, [ref] $fgTid)
        if ($fgTid -ne 0 -and $fgTid -ne $myTid) { $null = [Win32FocusBridgeNudgeV1]::AttachThreadInput($myTid, $fgTid, $true) }
        $null = [Win32FocusBridgeNudgeV1]::BringWindowToTop($Handle)
        $null = [Win32FocusBridgeNudgeV1]::SetForegroundWindow($Handle)
        if ($fgTid -ne 0 -and $fgTid -ne $myTid) { $null = [Win32FocusBridgeNudgeV1]::AttachThreadInput($myTid, $fgTid, $false) }
        Start-Sleep -Milliseconds 150
        return ([Win32FocusBridgeNudgeV1]::GetForegroundWindow() -eq $Handle)
    }

    if (-not (Try-Foreground)) {
        Send-AltKeyTrick
        if (-not (Try-Foreground)) {
            throw "could not bring target HWND $Handle to foreground"
        }
    }
    try {
        $root = [System.Windows.Automation.AutomationElement]::RootElement
        $winCond = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Window)
        $wins = $root.FindAll([System.Windows.Automation.TreeScope]::Children, $winCond)
        foreach ($win in $wins) {
            $hwnd = [System.IntPtr]::Zero
            try { $hwnd = [System.IntPtr]$win.Current.NativeWindowHandle } catch { }
            if ($hwnd -ne $Handle) { continue }
            try { $win.SetFocus() } catch { }
            $textCond = New-Object System.Windows.Automation.PropertyCondition(
                [System.Windows.Automation.AutomationElement]::IsTextPatternAvailableProperty, $true)
            $textEls = $win.FindAll([System.Windows.Automation.TreeScope]::Descendants, $textCond)
            $best = $null
            $bestLen = -1
            foreach ($el in $textEls) {
                try {
                    $tp = $el.GetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern)
                    $txt = $tp.DocumentRange.GetText(-1)
                    if ($txt.Length -gt $bestLen) { $best = $el; $bestLen = $txt.Length }
                } catch { }
            }
            if ($best -ne $null) { try { $best.SetFocus() } catch { } }
            break
        }
    } catch { }
    $fgWin = [Win32FocusBridgeNudgeV1]::GetForegroundWindow()
    $myTid = [Win32FocusBridgeNudgeV1]::GetCurrentThreadId()
    $fgTid = 0
    $null = [Win32FocusBridgeNudgeV1]::GetWindowThreadProcessId($fgWin, [ref] $fgTid)
    if ($fgTid -ne 0 -and $fgTid -ne $myTid) { $null = [Win32FocusBridgeNudgeV1]::AttachThreadInput($myTid, $fgTid, $true) }
    $null = [Win32FocusBridgeNudgeV1]::BringWindowToTop($Handle)
    $null = [Win32FocusBridgeNudgeV1]::SetForegroundWindow($Handle)
    if ($fgTid -ne 0 -and $fgTid -ne $myTid) { $null = [Win32FocusBridgeNudgeV1]::AttachThreadInput($myTid, $fgTid, $false) }
    Start-Sleep -Milliseconds 150
    if ($ClearLineBeforeSend) {
        [System.Windows.Forms.SendKeys]::SendWait('^u')
        Start-Sleep -Milliseconds 150
    }
    [System.Windows.Forms.SendKeys]::SendWait($Keys)
    if ($AppendEnter) {
        Start-Sleep -Milliseconds 120
        [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')
    }
}

# ------------------------------------------------------------------ cooldown marker
function Test-CooldownElapsed {
    param([string]$Agent)
    $marker = "$MarkerPrefix$Agent.txt"
    if (-not (Test-Path -LiteralPath $marker)) { return $true }
    try {
        $last = Get-TsUtc (Get-Content -LiteralPath $marker -Raw).Trim()
        if ($null -eq $last) { return $true }
        return (([DateTime]::UtcNow - $last).TotalSeconds -ge $NudgeCooldownSeconds)
    } catch { return $true }
}

function Set-CooldownMarker {
    param([string]$Agent)
    $marker = "$MarkerPrefix$Agent.txt"
    try { Set-Content -LiteralPath $marker -Value ([DateTime]::UtcNow.ToString('o')) -Encoding UTF8 } catch { }
}

# ------------------------------------------------------------------ B1 operator-activity guard
# True when it is SAFE to send physical keystrokes (operator input idle >= guard, or
# guard disabled). Fail-closed: any error -> treat operator as active -> do not send.
function Test-OperatorIdle {
    if ($OperatorIdleGuardSeconds -le 0) { return $true }
    try {
        $idle = [Win32FocusBridgeNudgeV1]::IdleSeconds()
        return ($idle -ge $OperatorIdleGuardSeconds)
    } catch { return $false }
}

# ------------------------------------------------------------------ remediation
function Test-BridgeWakeCooldownElapsed {
    param([string]$Agent)
    $cooldown = [Math]::Max(60, $BridgeWakeCooldownSeconds)
    $marker = "${MarkerPrefix}bridgewake-$Agent.txt"
    if (-not (Test-Path -LiteralPath $marker)) { return $true }
    try {
        $last = Get-TsUtc (Get-Content -LiteralPath $marker -Raw).Trim()
        if ($null -eq $last) { return $true }
        return (([DateTime]::UtcNow - $last).TotalSeconds -ge $cooldown)
    } catch { return $true }
}

function Set-BridgeWakeMarker {
    param([string]$Agent)
    $marker = "${MarkerPrefix}bridgewake-$Agent.txt"
    try { Set-Content -LiteralPath $marker -Value ([DateTime]::UtcNow.ToString('o')) -Encoding UTF8 } catch { }
}

function Invoke-BridgeWake {
    param([string]$Agent, [string]$Reason)
    if ($NoBridgeWake -or $DryRun) { return }
    if (-not (Test-Path -LiteralPath $WriteEventScript)) { return }
    # Idempotency fix 2026-07-02 (bridge audit: 2891 wake_request/day flood):
    # the WINDOW-nudge cooldown marker is only set when action='nudged', so
    # while the operator is active (window nudge returns skip_operator_active)
    # this bridge emit used to fire on EVERY poll. Gate the bridge emit on its
    # own per-agent cooldown so it fires at most once per NudgeCooldownSeconds
    # regardless of the window-nudge outcome.
    if (-not (Test-BridgeWakeCooldownElapsed -Agent $Agent)) { return }
    $previousRuntimeRoot = [Environment]::GetEnvironmentVariable(
        'AGENT_BRIDGE_RUNTIME_ROOT',
        'Process'
    )
    try {
        $env:AGENT_BRIDGE_RUNTIME_ROOT = $RuntimeRoot
        & $WriteEventScript -Agent operator -Type wake_request -To $Agent -Status open `
            -Severity medium -TaskId ("bridge-follow-nudge-{0:yyyyMMdd}" -f (Get-Date)) `
            -Message "jatka: read the bridge (Read-AgentBridge.ps1 -Agent $Agent) and answer open requests. $Reason" | Out-Null
        Set-BridgeWakeMarker -Agent $Agent
    } catch {
        Write-NudgeLog "bridge wake emit failed for ${Agent}: $_" 'WARN'
    } finally {
        if ($null -eq $previousRuntimeRoot) {
            [Environment]::SetEnvironmentVariable(
                'AGENT_BRIDGE_RUNTIME_ROOT',
                $null,
                'Process'
            )
        } else {
            $env:AGENT_BRIDGE_RUNTIME_ROOT = $previousRuntimeRoot
        }
    }
}

function Invoke-WindowNudge {
    param([string]$Agent, [double]$DarkSeconds = 0)
    # B1: never steal focus while the operator is actively using the machine --
    # UNLESS the agent has been dark long enough to be genuinely stuck (smart guard).
    if (-not (Test-OperatorIdle)) {
        if ($ForceWakeAfterDarkSeconds -gt 0 -and $DarkSeconds -ge $ForceWakeAfterDarkSeconds) {
            Write-NudgeLog "operator active BUT $Agent dark $([int]$DarkSeconds)s >= ${ForceWakeAfterDarkSeconds}s -> FORCE nudge (smart guard)" 'INFO'
        } else {
            Write-NudgeLog "skip window nudge for $Agent (operator active; bridge wake still emitted)" 'INFO'
            return 'skip_operator_active'
        }
    }
    # Select the agent's TAB first (fail-safe: if not found, do NOT send keys).
    $handle = Select-AgentTab -Agent $Agent
    if ($handle -eq [System.IntPtr]::Zero) {
        Write-NudgeLog "no tab/window found for $Agent (cannot nudge)" 'WARN'
        return 'no_window'
    }
    if ($DryRun) { return 'dryrun_would_nudge' }
    try {
        Send-Keystrokes -Handle $handle -Keys $NudgeText -AppendEnter $true -ClearLineBeforeSend $true
        return 'nudged'
    } catch {
        Write-NudgeLog "window nudge failed for ${Agent}: $_" 'WARN'
        return 'nudge_failed'
    }
}

# clear-escalation cooldown (separate, longer marker than the nudge cooldown)
function Test-ClearCooldownElapsed {
    param([string]$Agent)
    $marker = "$MarkerPrefix$Agent-clear.txt"
    if (-not (Test-Path -LiteralPath $marker)) { return $true }
    try {
        $last = Get-TsUtc (Get-Content -LiteralPath $marker -Raw).Trim()
        if ($null -eq $last) { return $true }
        return (([DateTime]::UtcNow - $last).TotalSeconds -ge $ClearCooldownSeconds)
    } catch { return $true }
}
function Set-ClearCooldownMarker {
    param([string]$Agent)
    $marker = "$MarkerPrefix$Agent-clear.txt"
    try { Set-Content -LiteralPath $marker -Value ([DateTime]::UtcNow.ToString('o')) -Encoding UTF8 } catch { }
}

# DESTRUCTIVE: reset a wedged agent's context. Selects the tab (fail-safe),
# sends /clear, waits, then a bootstrap line so the cleared agent re-reads the
# bridge. NEVER touches a ClearNever agent. Gated by -EnableClearEscalation.
function Invoke-ClearEscalation {
    param([string]$Agent, [string]$RedirectText = '')
    if ($clearNever -contains $Agent) { return 'clear_skipped_protected' }
    # B1: /clear is destructive AND focus-stealing; never fire while operator active.
    if (-not (Test-OperatorIdle)) {
        Write-NudgeLog "skip /clear for $Agent (operator active)" 'INFO'
        return 'clear_skip_operator_active'
    }
    $handle = Select-AgentTab -Agent $Agent
    if ($handle -eq [System.IntPtr]::Zero) {
        Write-NudgeLog "CLEAR: no tab found for $Agent -> NOT clearing (fail-safe)" 'WARN'
        return 'clear_no_window'
    }
    if ($DryRun) { return 'dryrun_would_clear' }
    $bootSrc = if ($RedirectText) { $RedirectText } else { $BootstrapText }
    $boot = $bootSrc -replace '<AGENT>', $Agent
    try {
        Send-Keystrokes -Handle $handle -Keys '/clear' -AppendEnter $true
        Start-Sleep -Seconds 2
        # re-select in case /clear changed focus, then bootstrap
        $h2 = Select-AgentTab -Agent $Agent
        if ($h2 -ne [System.IntPtr]::Zero) { $handle = $h2 }
        Send-Keystrokes -Handle $handle -Keys $boot -AppendEnter $true
        Set-ClearCooldownMarker -Agent $Agent
        Write-NudgeLog "CLEAR escalation applied to $Agent (context reset + bootstrap)" 'WARN'
        return 'cleared'
    } catch {
        Write-NudgeLog "clear escalation failed for ${Agent}: $_" 'WARN'
        return 'clear_failed'
    }
}

# ------------------------------------------------------------------ one evaluation pass
function Invoke-Pass {
    $events = Read-BridgeEvents -Path $EventsPath
    if ($events.Count -eq 0) { Write-NudgeLog 'no events read (empty/missing events.jsonl)' 'WARN'; return }
    $now = [DateTime]::UtcNow

    foreach ($agent in $agents) {
        # latest timestamps
        $hbTs = $null; $subTs = $null; $readTs = $null
        # B3 explicit clear trigger addressed to this agent. Clear semantics
        # remain outside the task-state reducer and require separate security
        # review; this slice changes only normal open-work/RCO-wake accounting.
        $clearReqTs = $null; $clearReqMsg = ''   # B3 RCO-triggered /clear+redirect
        foreach ($e in $events) {
            $ea = [string]$e.agent
            $ty = [string]$e.type
            $st = [string]$e.status
            $ts = Get-TsUtc $e.ts_utc
            if ($null -eq $ts) { continue }
            if ($ea -eq $agent) {
                if ($ty -eq 'heartbeat' -or $ty -eq 'liveness') { if ($null -eq $hbTs -or $ts -gt $hbTs) { $hbTs = $ts } }
                elseif ($ty -eq 'message' -and $st -eq 'received') { if ($null -eq $readTs -or $ts -gt $readTs) { $readTs = $ts } }
                else { if ($null -eq $subTs -or $ts -gt $subTs) { $subTs = $ts } }
            }
            else {
                $tid = [string]$e.task_id
                # exclude this watcher's own bridge-follow-nudge wakes so it cannot feed itself
                $isBridgeFollowNudge = ($tid -like 'bridge-follow-nudge*')
                # These watcher-generated nudges are audit/remediation noise,
                # not first-party RCO/operator work. If they reach the explicit
                # trigger path they can keep re-triggering the watcher and make
                # an agent look active only because it acknowledged wake spam.
                if ($isBridgeFollowNudge) { continue }
                # B3: explicit clear trigger uses exact parsed recipients.
                if (
                    (Test-BridgeAddressedTo -Event $e -TargetAgent $agent) -and
                    ($rcoTrigger -contains $ea)
                ) {
                    if ($st -eq 'clear_redirect_requested') {
                        if ($null -eq $clearReqTs -or $ts -gt $clearReqTs) { $clearReqTs = $ts; $clearReqMsg = [string]$e.message }
                    }
                }
            }
        }

        $heartbeatAge   = if ($hbTs)   { ($now - $hbTs).TotalSeconds }   else { [double]::PositiveInfinity }
        $readAge        = if ($readTs) { ($now - $readTs).TotalSeconds } else { [double]::PositiveInfinity }
        $substantiveAge = if ($subTs)  { ($now - $subTs).TotalSeconds }  else { [double]::PositiveInfinity }

        # MODEL-AGNOSTIC trigger: reduce exact incoming requests by
        # target/requester/task/type, resolve only same-task answers/closures,
        # then count overdue tasks once. Agent-wide substantive output remains
        # diagnostic only and cannot clear unrelated work.
        $nudgeState = Get-BridgeNudgeAgentState `
            -Events $events `
            -Agent $agent `
            -NowUtc $now `
            -UnansweredSeconds $UnansweredSeconds `
            -RcoTriggerAgents $rcoTrigger `
            -SummaryOnly
        $openIncoming = [int]$nudgeState.open_incoming_count
        $freshRcoWake = [bool]$nudgeState.fresh_rco_wake

        # B3 clear-trigger freshness remains unchanged pending its separate
        # security-focused task.
        $subRefT = if ($subTs) { $subTs } else { [DateTime]::MinValue }
        $freshClear   = ($null -ne $clearReqTs)   -and ($clearReqTs -gt $subRefT)

        # classify
        $classification = 'healthy'
        if ($openIncoming -gt 0) { $classification = 'not_following_bridge' }
        if ($freshRcoWake) { $classification = 'rco_wake_requested' }
        if ($freshClear)   { $classification = 'rco_clear_requested' }

        $action = 'none'
        # --- B3: RCO-TRIGGERED /clear+redirect takes priority (operator-chosen path) ---
        if ($freshClear -and (-not $DisableRcoClearTrigger) -and ($clearNever -notcontains $agent)) {
            if (Test-ClearCooldownElapsed -Agent $agent) {
                Invoke-BridgeWake -Agent $agent -Reason 'rco clear_redirect_requested -> context reset + redirect'
                $action = Invoke-ClearEscalation -Agent $agent -RedirectText $clearReqMsg
            } else {
                $action = 'skip_clear_cooldown'
            }
            Write-NudgeLog ("{0}: {1} (sub={2}s) -> {3}" -f $agent, $classification, `
                $(if([double]::IsInfinity($substantiveAge)){'inf'}else{[int]$substantiveAge}), $action) `
                $(if ($action -like '*fail*' -or $action -like '*no_window*') {'WARN'} else {'INFO'})
        }
        else {
            # --- plain nudge: open unanswered work OR a fresh RCO/operator wake_request (B2 fast path) ---
            $needsNudge = ($openIncoming -gt 0) -or $freshRcoWake
            if ($needsNudge) {
                # Deep-stuck -> autonomous /clear escalation (opt-in, destructive; separate from
                # the RCO-triggered path above). Still OFF unless -EnableClearEscalation.
                $deepStuck = ($EnableClearEscalation -and ($substantiveAge -gt $ClearEscalationSeconds) -and ($clearNever -notcontains $agent))
                if ($deepStuck -and (Test-ClearCooldownElapsed -Agent $agent)) {
                    $action = Invoke-ClearEscalation -Agent $agent
                }
                elseif (-not (Test-CooldownElapsed -Agent $agent)) {
                    $action = 'skip_cooldown'
                }
                else {
                    Invoke-BridgeWake -Agent $agent -Reason "classification=$classification openIncoming=$openIncoming rcoWake=$freshRcoWake"
                    $action = Invoke-WindowNudge -Agent $agent -DarkSeconds $substantiveAge
                    if ($action -eq 'nudged') { Set-CooldownMarker -Agent $agent }
                }
                Write-NudgeLog ("{0}: {1} (hb={2}s read={3}s sub={4}s open={5} rcoWake={6}) -> {7}" -f `
                    $agent, $classification, ([int]$heartbeatAge), $(if([double]::IsInfinity($readAge)){'inf'}else{[int]$readAge}), `
                    $(if([double]::IsInfinity($substantiveAge)){'inf'}else{[int]$substantiveAge}), $openIncoming, $freshRcoWake, $action) `
                    $(if ($action -like '*fail*' -or $action -eq 'no_window') {'WARN'} else {'INFO'})
            }
        }
        Write-NudgeCsv -Agent $agent -HeartbeatAge $heartbeatAge -ReadAge $readAge -SubstantiveAge $substantiveAge -OpenIncoming $openIncoming -Classification $classification -Action $action
    }
}

# ------------------------------------------------------------------ main
Write-NudgeLog ("Watch-AgentsBridgeNudge starting | agents=[{0}] events={1} dryRun={2} poll={3}s cooldown={4}s" -f `
    ($agents -join ','), $EventsPath, $DryRun.IsPresent, $PollIntervalSeconds, $NudgeCooldownSeconds)

if ($StartupDelaySeconds -gt 0) { Start-Sleep -Seconds $StartupDelaySeconds }

if ($OnceOnly) {
    Invoke-Pass
    Write-NudgeLog 'OnceOnly pass complete; exiting.'
    return
}

while ($true) {
    try { Invoke-Pass } catch { Write-NudgeLog "pass error: $_" 'ERROR' }
    Start-Sleep -Seconds $PollIntervalSeconds
}
