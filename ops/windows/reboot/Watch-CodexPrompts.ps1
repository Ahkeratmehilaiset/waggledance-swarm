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
    [string]$LogPath = (Join-Path $env:USERPROFILE 'codex-autoapprove.log'),
    [int]$MaxSendRetries = 3,
    # Never send UI keystrokes while the operator is actively using the desktop.
    # Set to 0 only for an isolated throwaway desktop/session.
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
    param([int64]$TargetHwndInt64)
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

try {
    if (-not $NoAllNighter) {
        $savedAllNighterState = Save-AllNighterState
        Write-Log ('AllNighter: saving original state -> ' + (Format-AllNighterDelta -Saved $savedAllNighterState)) 'INFO'
        Enable-AllNighter
        Write-Log 'AllNighter: ENABLED.' 'INFO'
    } else {
        Write-Log 'AllNighter: SKIPPED.' 'INFO'
    }

    if ($StrictTabTitleMode) {
        Write-Log ('STRICT TAB TITLE MODE: ONLY pressing y in windows whose name contains "' + $TabTitle + '". No fallback.') 'INFO'
    }

    Write-Log 'FOCUS FIX v3: Win32FocusV3 type with IsIconic + ALT-key trick.' 'INFO'

    if ($AllowAll) { Write-Log 'ALLOWALL mode: DANGEROUS.' 'DANGEROUS-ALLOW' }

    Write-Log ('Watch-CodexPrompts started. TabTitle="' + $TabTitle + '" Strict=' + $StrictTabTitleMode + ' DryRun=' + $DryRun + ' YesToAll=' + $YesToAll + ' AllowAll=' + $AllowAll + ' AllNighter=' + (-not $NoAllNighter) + ' HwndLock=' + (-not $NoHwndLock)) 'INFO'

    $lastSeenCommand = $null
    $consecutiveFailures = 0
    $consecutiveTabMisses = 0

    while ($true) {
        try {
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
                        if ($wname -notlike ('*' + $TabTitle + '*')) { continue }
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
                            Write-Log ('STRICT MODE: no tab whose name contains "' + $TabTitle + '". Idle.') 'WARN'
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
            } else {
                $consecutiveFailures = 0
                if ($targetCmd -ne $lastSeenCommand) {
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
                            if ($MinUserIdleSeconds -gt 0 -and $null -ne $idleSeconds -and $idleSeconds -lt $MinUserIdleSeconds) {
                                Write-Log ("Operator active: desktop idle ${idleSeconds}s < ${MinUserIdleSeconds}s; not sending y+Enter.") 'SAFE-SKIP'
                            } elseif ($DryRun) {
                                Write-Log "DryRun=on, would press 'y' + Enter." 'DRYRUN'
                            } else {
                                if ($AllowAll) { Write-Log 'DANGEROUSLY auto-approving (AllowAll).' 'DANGEROUS-ACTION' }
                                else { Write-Log 'Auto-approving: sending y + Enter.' 'ACTION' }
                                $ok = Send-YesEnter -Window $targetWin -TextElement $targetTextEl -TargetHwndInt64 $thisHwndInt64
                                if ($ok) {
                                    if (-not $NoHwndLock -and -not $StrictTabTitleMode -and $LockedHwndInt64 -eq 0 -and $thisHwndInt64 -ne 0) {
                                        $LockedHwndInt64 = $thisHwndInt64
                                        $LockedWindowName = $targetWname
                                        Write-Log ('HWND-LOCK ACTIVATED: locked to HWND ' + $LockedHwndInt64 + '.') 'INFO'
                                    }
                                } else {
                                    Write-Log 'CONFIRMED FAILURE: prompt was not dismissed.' 'ERROR'
                                }
                            }
                        }
                    }
                    $lastSeenCommand = $targetCmd
                }
            }
        } catch { Write-Log ('Loop error: ' + $_) 'ERROR' }
        Start-Sleep -Seconds $PollIntervalSeconds
    }
}
finally {
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
