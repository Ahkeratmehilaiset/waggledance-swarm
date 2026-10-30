#requires -Version 5.1
<#
.SYNOPSIS
    Thin PowerShell 5.1/7 front end for the F29 read-only bridge doctor.

.DESCRIPTION
    Runs tools/wd_bridge_doctor.py ONLY through the inherited pinned wrapper
    $env:WD_BRIDGE_PYTHON_WRAPPER and relays its single machine-JSON report and
    exit status unchanged:
      0 ready, 1 degraded, 2 refuse, 3 invalid input or doctor unavailable.
    There is no second evaluator here: this script validates its own
    arguments, verifies the pinned wrapper against the bundle deployment
    manifest and the external anchor $env:WD_REBOOT_EXPECTED_MANIFEST_HASH,
    and checks that the doctor's JSON verdict matches its exit code.
    Anything else (missing or unverified wrapper, unset anchor, doctor not
    packaged, a wrapper error, non-JSON or inconsistent output) refuses with
    exit 3 and a JSON report whose verdict is "doctor_unavailable" or
    "invalid_input". There is never a bare-Python, PATH or repository-relative
    fallback. Arguments are passed as an argv array; no command line is built.

    PACKAGE HISTORY: the doctor was not packaged in bundle 8a7576af, so that pin
    refuses with doctor_unavailable. Bundle 7779e9a2145c82389dd97c333bbe3112e3c29ff0
    includes tools/wd_bridge_doctor.py and the bridge_doctor entrypoint in
    bridge-code-files.json. Packaging is not evidence of provider readiness
    or launcher wiring: this front end still verifies the inherited pin and
    report on every invocation. No direct doctor invocation was found in
    start-wd-agent.ps1 or start-wd-tools-consumer.ps1 at that exact source head.

    It performs no credential access, provider or model call, installation
    or file write. The pinned wrapper keeps its own documented behaviour.

.PARAMETER ManifestPath
    Absolute path of the component manifest (wd.bridge-components.v1).

.PARAMETER PathsConfig
    Absolute path of the local paths config (wd.bridge-local-paths.v1).

.PARAMETER EvidencePath
    Optional absolute path of provider evidence (wd.bridge-provider-evidence.v1).

.PARAMETER Lane
    Lane name declared in the manifest.

.PARAMETER Now
    Optional ISO-8601 evaluation time with a timezone (for reproducible runs).

.EXAMPLE
    powershell -NoProfile -File Test-WdBridgeComponents.ps1 -ManifestPath C:\cfg\bridge_components.json -PathsConfig C:\cfg\paths.json -Lane claude-rco-2

.NOTES
    Run it as its own process (-File) so its exit status is the process exit
    code. Isolated relay fixtures: tests/tools/test_wd_bridge_components_ps.py.
    Fixture success is not a live provider, launch or continuity observation.
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [Parameter(Mandatory)] [string] $ManifestPath,
    [Parameter(Mandatory)] [string] $PathsConfig,
    [string] $EvidencePath = '',
    [Parameter(Mandatory)] [string] $Lane,
    [string] $Now = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$DoctorTool = 'tools/wd_bridge_doctor.py'
$ReportSchema = 'wd.bridge-doctor-report.v1'

function Write-FrontEndRefusal {
    param([Parameter(Mandatory)] [string] $Verdict, [Parameter(Mandatory)] [string] $Reason)
    $text = [string]$Reason
    if ($text.Length -gt 300) { $text = $text.Substring(0, 300) }
    $report = [ordered]@{
        authority_effect   = 'none'
        error              = $text
        front_end          = 'Test-WdBridgeComponents.ps1'
        installs_performed = $false
        schema             = $ReportSchema
        verdict            = $Verdict
    }
    Write-Output ($report | ConvertTo-Json -Compress)
    exit 3
}

function Test-FullyQualifiedPath {
    param([string] $Path)
    if ([string]::IsNullOrWhiteSpace($Path) -or $Path.IndexOf([char]0) -ge 0) { return $false }
    try { $root = [IO.Path]::GetPathRoot($Path) } catch { return $false }
    if ([string]::IsNullOrEmpty($root)) { return $false }
    return ($root -match '^[A-Za-z]:[\\/]$' -or $root -match '^\\\\[^\\/]+\\[^\\/]+\\?$')
}

function Get-Sha256Hex {
    param([Parameter(Mandatory)] [byte[]] $Bytes)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace('-', '') }
    finally { $sha.Dispose() }
}

# 1. Own arguments: absolute paths (so no value can start with '-' or be
#    resolved against a caller directory), a lane name, a strict timestamp.
foreach ($pair in @(@('ManifestPath', $ManifestPath), @('PathsConfig', $PathsConfig))) {
    if (-not (Test-FullyQualifiedPath $pair[1])) {
        Write-FrontEndRefusal -Verdict 'invalid_input' -Reason ($pair[0] + ' must be an absolute path')
    }
}
if ($EvidencePath -and -not (Test-FullyQualifiedPath $EvidencePath)) {
    Write-FrontEndRefusal -Verdict 'invalid_input' -Reason 'EvidencePath must be an absolute path'
}
if ($Lane -cnotmatch '^[a-z][a-z0-9-]{0,63}$') {
    Write-FrontEndRefusal -Verdict 'invalid_input' -Reason 'Lane must match ^[a-z][a-z0-9-]{0,63}$'
}
if ($Now -and $Now -cnotmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,7})?(Z|[+-]\d{2}:\d{2})$') {
    Write-FrontEndRefusal -Verdict 'invalid_input' -Reason 'Now must be ISO-8601 with a timezone'
}

# 2. The inherited pinned wrapper and its external anchor. No fallback.
$wrapper = [string]$env:WD_BRIDGE_PYTHON_WRAPPER
$anchor = [string]$env:WD_REBOOT_EXPECTED_MANIFEST_HASH
if (-not (Test-FullyQualifiedPath $wrapper)) {
    Write-FrontEndRefusal -Verdict 'doctor_unavailable' -Reason 'WD_BRIDGE_PYTHON_WRAPPER is not an absolute path'
}
if ([IO.Path]::GetFileName($wrapper) -cne 'Invoke-WdBridgePython.ps1') {
    Write-FrontEndRefusal -Verdict 'doctor_unavailable' -Reason 'WD_BRIDGE_PYTHON_WRAPPER does not name Invoke-WdBridgePython.ps1'
}
if ($anchor -cnotmatch '^[A-Fa-f0-9]{64}$') {
    Write-FrontEndRefusal -Verdict 'doctor_unavailable' -Reason 'WD_REBOOT_EXPECTED_MANIFEST_HASH is not set to a SHA-256 anchor'
}
try {
    $wrapperItem = Get-Item -LiteralPath $wrapper -Force -ErrorAction Stop
    if ($wrapperItem.PSIsContainer -or ($wrapperItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'wrapper is not a regular file'
    }
    $bundleRoot = [IO.Path]::GetDirectoryName($wrapperItem.FullName)
    $manifestFile = Join-Path $bundleRoot 'deployment-manifest.json'
    $manifestBytes = [IO.File]::ReadAllBytes($manifestFile)
    if ((Get-Sha256Hex -Bytes $manifestBytes) -cne $anchor.ToUpperInvariant()) {
        throw 'bundle deployment manifest differs from its external anchor'
    }
    $manifestText = [Text.Encoding]::UTF8.GetString($manifestBytes)
    if ($manifestText.Length -gt 0 -and $manifestText[0] -eq [char]0xFEFF) { $manifestText = $manifestText.Substring(1) }
    $manifest = $manifestText | ConvertFrom-Json -ErrorAction Stop
    $filesProperty = $manifest.PSObject.Properties['files']
    if ($null -eq $filesProperty -or $null -eq $filesProperty.Value) { throw 'deployment manifest has no files map' }
    $wrapperEntry = $filesProperty.Value.PSObject.Properties['Invoke-WdBridgePython.ps1']
    if ($null -eq $wrapperEntry) { throw 'deployment manifest does not pin Invoke-WdBridgePython.ps1' }
    # .NET SHA-256, not Get-FileHash: Windows PowerShell started with a PowerShell 7 module path cannot load it.
    $wrapperHash = Get-Sha256Hex -Bytes ([IO.File]::ReadAllBytes($wrapperItem.FullName))
    if ($wrapperHash -cne ([string]$wrapperEntry.Value).ToUpperInvariant()) {
        throw 'pinned wrapper differs from its deployment manifest entry'
    }
}
catch {
    Write-FrontEndRefusal -Verdict 'doctor_unavailable' -Reason ('unverified pinned wrapper: ' + $_.Exception.Message)
}

# 3. One call through the pinned wrapper with an argv array.
$toolArguments = New-Object System.Collections.Generic.List[string]
foreach ($item in @('--manifest', $ManifestPath, '--paths-config', $PathsConfig, '--lane', $Lane)) {
    $toolArguments.Add([string]$item)
}
if ($EvidencePath) { $toolArguments.Add('--evidence'); $toolArguments.Add($EvidencePath) }
if ($Now) { $toolArguments.Add('--now'); $toolArguments.Add($Now) }
$argv = $toolArguments.ToArray()

$global:LASTEXITCODE = -1
try {
    $lines = @(& $wrapperItem.FullName -Tool $DoctorTool -VerifyPackage @argv)
    $code = $global:LASTEXITCODE
}
catch {
    Write-FrontEndRefusal -Verdict 'doctor_unavailable' -Reason ('pinned doctor unavailable: ' + $_.Exception.Message)
}

# 4. Relay only a well-formed report whose verdict matches the exit status.
$texts = @($lines | ForEach-Object { [string]$_ } | Where-Object { $_.Trim() -ne '' })
if ($texts.Count -eq 0) {
    Write-FrontEndRefusal -Verdict 'doctor_unavailable' -Reason ('doctor produced no report; exit ' + [string]$code)
}
foreach ($extra in @($texts | Select-Object -SkipLast 1)) { [Console]::Error.WriteLine($extra) }
$last = $texts[-1].Trim()
try {
    $report = $last | ConvertFrom-Json -ErrorAction Stop
}
catch {
    Write-FrontEndRefusal -Verdict 'doctor_unavailable' -Reason ('doctor output is not JSON; exit ' + [string]$code)
}
$schema = $report.PSObject.Properties['schema']
$verdictProperty = $report.PSObject.Properties['verdict']
if ($null -eq $schema -or [string]$schema.Value -cne $ReportSchema -or $null -eq $verdictProperty) {
    Write-FrontEndRefusal -Verdict 'doctor_unavailable' -Reason 'doctor output is not a wd.bridge-doctor-report.v1 report'
}
$expected = $null
switch -CaseSensitive ([string]$verdictProperty.Value) {
    'ready' { $expected = 0 }
    'degraded' { $expected = 1 }
    'refuse' { $expected = 2 }
    'invalid_input' { $expected = 3 }
}
if ($null -eq $expected -or -not ($code -is [int]) -or $code -ne $expected) {
    Write-FrontEndRefusal -Verdict 'doctor_unavailable' -Reason ('doctor verdict and exit status disagree; exit ' + [string]$code)
}
Write-Output $last
exit $code
