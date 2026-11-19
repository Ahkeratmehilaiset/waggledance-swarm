#requires -Version 5.1
<#
    Standalone, opt-in owner host.  This script is not wired into a launcher.
    Its caller supplies the independent bundle, policy and interpreter pins and
    verifies these host bytes before execution; self-verification alone is not
    a bootstrap trust boundary.
    It never signals, terminates or retries a process other than through the
    cooperative Start/Stop-WdMindManualOwner API.  The caller retains this host,
    its bounded output pipes and the named stop event. A signalled event or the
    exact controller process exiting requests a cooperative stop.
#>
[CmdletBinding()]
param(
    [switch] $Enabled,
    [string] $BundleRoot = '',
    [string] $ExpectedManifestHash = '',
    [string] $OperatorPolicyPath = '',
    [string] $OperatorPolicySha256 = '',
    [string] $OperatorApprovalReferenceSha256 = '',
    [string] $BridgePythonPath = '',
    [string] $BridgePythonSha256 = '',
    [string] $StopEventName = '',
    [int] $ControllerPid = 0,
    [string] $ControllerStartUtc = ''
    , [switch] $SourceBoundScriptBlock
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Read-WdHostPinnedBytes {
    param([string] $Path, [string] $Pin, [int] $Maximum)
    if ($Pin -cnotmatch '\A[0-9A-Fa-f]{64}\z' -or
        $Path -cnotmatch '\A[Cc]:[\\/]' -or $Path.Substring(3) -match '[:*?"<>|]' -or
        @($Path.Substring(3).Split([char[]]'\/') | Where-Object {
            $_ -eq '.' -or $_ -eq '..' -or $_ -match '[. ]$'
        }).Count -ne 0) { throw 'pin' }
    $full = [IO.Path]::GetFullPath($Path)
    $current = $full
    while ($current) {
        if (([IO.File]::GetAttributes($current) -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'path' }
        $parent = [IO.Path]::GetDirectoryName($current)
        if (-not $parent -or $parent -ceq $current) { break }
        $current = $parent
    }
    $stream = [IO.File]::Open($full, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        if ($stream.Length -lt 1 -or $stream.Length -gt $Maximum) { throw 'size' }
        $bytes = New-Object byte[] ([int]$stream.Length)
        $at = 0
        while ($at -lt $bytes.Length) {
            $count = $stream.Read($bytes, $at, $bytes.Length - $at)
            if ($count -le 0) { throw 'read' }
            $at += $count
        }
        if ($stream.ReadByte() -ne -1) { throw 'changed' }
    } finally { $stream.Dispose() }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $actual = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
    if ($actual -cne $Pin.ToLowerInvariant()) { throw 'pin' }
    return ,$bytes
}

function Get-WdHostParentPid {
    # ProcessId in the filter is this process's integer PID, not user text.
    # A missing, ambiguous or inaccessible OS parent observation must refuse.
    $rows = @(CimCmdlets\Get-CimInstance -ClassName Win32_Process `
        -Filter ('ProcessId = ' + [string]$PID) -Property ProcessId,ParentProcessId -ErrorAction Stop)
    if ($rows.Count -ne 1 -or $null -eq $rows[0] -or
        [long]$rows[0].ProcessId -ne [long]$PID -or
        [long]$rows[0].ParentProcessId -le 0 -or
        [long]$rows[0].ParentProcessId -gt [int]::MaxValue) { throw 'parent_query' }
    return [int]$rows[0].ParentProcessId
}

# This process is a separate host, so clearing inherited authority here cannot
# change the launcher's environment.  The pinned manual helper also constrains
# its worker environment independently.
$allow = @('SYSTEMROOT','SYSTEMDRIVE','WINDIR','PATH','PATHEXT','TEMP','TMP','USERPROFILE','LOCALAPPDATA','APPDATA','PSMODULEPATH')
foreach ($entry in @(Get-ChildItem Env:)) {
    if ($allow -cnotcontains $entry.Name.ToUpperInvariant()) {
        [Environment]::SetEnvironmentVariable($entry.Name, $null, 'Process')
    }
}
$env:PSModulePath = (Join-Path $PSHOME 'Modules') + ';' +
    (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\Modules')

$out = [Console]::OpenStandardOutput()
$pendingWrite = $null
$pendingBytes = $null
$lastSent = ''
$queuedState = ''
$outputBroken = $false
$totalOutputBytes = 0
function Queue-WdHostState([string] $State) {
    if ($State -cnotin @('DISABLED','STARTING','READY','HOLD','STOPPED','REFUSED','PRE_OWNER_REFUSED')) { throw 'state' }
    if ($State -cne $script:lastSent) { $script:queuedState = $State }
}
function Flush-WdHostState {
    if ($script:outputBroken) { return }
    try {
        if ($null -ne $script:pendingWrite) {
            if (-not $script:pendingWrite.IsCompleted) { return }
            [void]$script:pendingWrite.GetAwaiter().GetResult()
            $script:pendingWrite = $null
            $script:pendingBytes = $null
        }
        if ($script:queuedState) {
            $state = $script:queuedState
            $script:queuedState = ''
            $script:lastSent = $state
            $line = '{"schema":"wd.mind-owner-host-status.v1","state":"' + $state + '","authority":"none","caller_authentication":"UNKNOWN","index_refresh":"NOT_SCHEDULED"}' + "`n"
            $script:pendingBytes = [Text.Encoding]::ASCII.GetBytes($line)
            if ($script:totalOutputBytes + $script:pendingBytes.Length -gt 8192) { throw 'output_limit' }
            $script:totalOutputBytes += $script:pendingBytes.Length
            $script:pendingWrite = $script:out.WriteAsync($script:pendingBytes, 0, $script:pendingBytes.Length)
        }
    } catch { $script:outputBroken = $true }
}
function Finish-WdHostOutput {
    # A terminal state can be queued while a prior asynchronous write is still
    # pending. Give each already-bounded frame a short chance to drain without
    # making successful owner cleanup depend on an unresponsive consumer.
    for ($attempt = 0; $attempt -lt 3; $attempt++) {
        if ($null -ne $script:pendingWrite) {
            try { [void]$script:pendingWrite.Wait(250) } catch { $script:outputBroken = $true }
        }
        Flush-WdHostState
        if ($null -eq $script:pendingWrite -and -not $script:queuedState) { break }
    }
    if ($null -ne $script:pendingWrite -and $script:pendingWrite.IsCompleted) {
        try {
            [void]$script:pendingWrite.GetAwaiter().GetResult()
            $script:pendingWrite = $null
            $script:pendingBytes = $null
        } catch { $script:outputBroken = $true }
    }
    return (-not $script:outputBroken -and -not $script:queuedState -and
        $null -eq $script:pendingWrite)
}

if (-not $Enabled) {
    Queue-WdHostState 'DISABLED'
    Flush-WdHostState
    if (Finish-WdHostOutput) { exit 0 }
    exit 3
}

$owner = $null
$stopEvent = $null
$controller = $null
$stop = $false
try {
    if ($BundleRoot -cnotmatch '\A[Cc]:[\\/]' -or $BundleRoot -match '(^|[\\/])\.\.([\\/]|$)' -or
        $ExpectedManifestHash -cnotmatch '\A[0-9A-Fa-f]{64}\z' -or
        $StopEventName -cnotmatch '\ALocal\\wd-mind-owner-stop-[0-9a-f]{32}\z' -or
        $ControllerPid -le 0 -or [string]::IsNullOrWhiteSpace($ControllerStartUtc)) { throw 'input' }
    # A matching live PID is insufficient: it must be this host's *actual*
    # launcher. Compare creation times as well so an already-reused parent PID
    # cannot be mistaken for the process that created this host.
    if ((Get-WdHostParentPid) -ne $ControllerPid) { throw 'controller_parent' }
    $hostProcess = [Diagnostics.Process]::GetCurrentProcess()
    try { $hostStarted = $hostProcess.StartTime.ToUniversalTime() }
    finally { $hostProcess.Dispose() }
    $controller = [Diagnostics.Process]::GetProcessById($ControllerPid)
    [void]$controller.Handle # Retain an OS process handle, not just a PID.
    $controllerStarted = $controller.StartTime.ToUniversalTime()
    if ($controllerStarted.ToString('o') -cne $ControllerStartUtc -or
        $controllerStarted -gt $hostStarted -or $controller.HasExited) { throw 'controller' }
    $stopEvent = [Threading.EventWaitHandle]::OpenExisting($StopEventName)
    $root = [IO.Path]::GetFullPath($BundleRoot).TrimEnd('\')
    $manifestPath = Join-Path $root 'deployment-manifest.json'
    $utf8 = New-Object Text.UTF8Encoding($false, $true)
    $manifestBytes = Read-WdHostPinnedBytes $manifestPath $ExpectedManifestHash 4194304
    $manifest = $utf8.GetString($manifestBytes).TrimStart([char]0xFEFF) | ConvertFrom-Json -ErrorAction Stop
    if ($manifest.schema_version -ne 1 -or $null -eq $manifest.files) { throw 'manifest' }
    $self = $manifest.files.PSObject.Properties['Start-WdMindOwnerHost.ps1']
    $helper = $manifest.files.PSObject.Properties['Start-WdMindManualOwner.ps1']
    if ($null -eq $self -or $self.Value -isnot [string] -or $null -eq $helper -or $helper.Value -isnot [string]) { throw 'manifest' }
    $ownPath = Join-Path $root 'Start-WdMindOwnerHost.ps1'
    $ownBytes = Read-WdHostPinnedBytes $ownPath $self.Value 1048576
    if ($SourceBoundScriptBlock) {
        if ($null -eq $MyInvocation.MyCommand.ScriptBlock -or
            $MyInvocation.MyCommand.ScriptBlock.ToString() -cne $utf8.GetString($ownBytes).TrimStart([char]0xFEFF)) { throw 'host_source' }
    } elseif (-not [IO.Path]::GetFullPath($MyInvocation.MyCommand.Path).Equals($ownPath, [StringComparison]::OrdinalIgnoreCase)) { throw 'host_path' }
    $helperBytes = Read-WdHostPinnedBytes (Join-Path $root 'Start-WdMindManualOwner.ps1') $helper.Value 1048576
    [void](Read-WdHostPinnedBytes $manifestPath $ExpectedManifestHash 4194304)
    . ([scriptblock]::Create($utf8.GetString($helperBytes).TrimStart([char]0xFEFF)))
    if ((Get-WdHostParentPid) -ne $ControllerPid -or $controller.HasExited -or
        $stopEvent.WaitOne(0)) { throw 'cancelled' }
    Queue-WdHostState 'STARTING'
    Flush-WdHostState
    $owner = Start-WdMindManualOwner -Enabled -BundleRoot $root -ExpectedManifestHash $ExpectedManifestHash `
        -OperatorPolicyPath $OperatorPolicyPath -OperatorPolicySha256 $OperatorPolicySha256 `
        -OperatorApprovalReferenceSha256 $OperatorApprovalReferenceSha256 `
        -BridgePythonPath $BridgePythonPath -BridgePythonSha256 $BridgePythonSha256 -IncludePreOwnerProof
    if ($null -eq $owner) { throw 'owner' }
    while ($true) {
        if (-not $stop -and ($outputBroken -or $controller.HasExited -or $stopEvent.WaitOne(0))) { $stop = $true }
        if ($stop) { $status = Stop-WdMindManualOwner -Owner $owner }
        else { $status = Get-WdMindManualOwnerStatus -Owner $owner }
        if ($null -eq $status -or $status.state -cnotin @('STARTING','READY','HOLD','STOPPED','FAILED','PRE_OWNER_REFUSED')) { throw 'owner_status' }
        if ($status.state -ceq 'PRE_OWNER_REFUSED') {
            Queue-WdHostState 'PRE_OWNER_REFUSED'; Flush-WdHostState
            [void](Finish-WdHostOutput)
            $stopEvent.Dispose(); $controller.Dispose()
            exit 2
        }
        if ($status.state -ceq 'STOPPED') {
            Queue-WdHostState 'STOPPED'
            Flush-WdHostState
            $reported = Finish-WdHostOutput
            $stopEvent.Dispose()
            $controller.Dispose()
            if ($reported) { exit 0 }
            exit 3
        }
        # FAILED does not prove all descendants/handles cleaned up; retain the
        # host and ownership record in HOLD rather than retrying or killing.
        if ($status.state -ceq 'FAILED') { $stop = $true; Queue-WdHostState 'HOLD' }
        elseif ($stop -or $status.state -ceq 'HOLD') { Queue-WdHostState 'HOLD' }
        else { Queue-WdHostState $status.state }
        Flush-WdHostState
        [Threading.Thread]::Sleep(50)
    }
} catch {
    if ($null -eq $owner) {
        Queue-WdHostState 'REFUSED'
        Flush-WdHostState
        [void](Finish-WdHostOutput)
        if ($null -ne $stopEvent) { $stopEvent.Dispose() }
        if ($null -ne $controller) { $controller.Dispose() }
        exit 2
    }
    # Once ownership exists an exception is not proof of cleanup. Keep the
    # process and its registry alive; request cooperative stop when possible.
    Queue-WdHostState 'HOLD'
    while ($true) {
        Flush-WdHostState
        try {
            $status = Stop-WdMindManualOwner -Owner $owner
            if ($status.state -ceq 'STOPPED') {
                Queue-WdHostState 'STOPPED'; Flush-WdHostState
                $reported = Finish-WdHostOutput
                $stopEvent.Dispose()
                $controller.Dispose()
                if ($reported) { exit 0 }
                exit 3
            }
        } catch { }
        [Threading.Thread]::Sleep(100)
    }
}
