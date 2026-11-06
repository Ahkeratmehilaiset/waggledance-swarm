#requires -Version 5.1
<# Explicit one-shot five-lane guard-directory provisioning. OFF by default.
   This is not lane authentication, an ACL check, a Mind query or installation.
   Entered failures are UNKNOWN: the provisioner may have created directories.
   No production action is performed by importing or dot-sourcing this script.
#>
[CmdletBinding(PositionalBinding = $false)]
param(
    [string] $BundleRoot = '',
    [string] $ExpectedCommit = '',
    [string] $ExpectedManifestSha256 = '',
    [switch] $Apply
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Assert-WdGuardPlainPath {
    param([string] $Path, [bool] $Directory)
    if ([string]::IsNullOrEmpty($Path) -or $Path -notmatch '^[Cc]:\\' -or
        $Path.Substring(2).Contains(':') -or ($Path -split '[\\/]' | Where-Object { $_ -in @('.', '..') })) { throw 'refused' }
    $full = [IO.Path]::GetFullPath($Path)
    $item = Get-Item -LiteralPath $full -Force -ErrorAction Stop
    if ([bool]($item.Attributes -band [IO.FileAttributes]::Directory) -ne $Directory) { throw 'refused' }
    while ($null -ne $item) {
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'refused' }
        if ($item -is [IO.DirectoryInfo]) { $item = $item.Parent } else { $item = $item.Directory }
    }
    return $full
}

function Get-WdGuardSha256 {
    param([string] $Path)
    $stream = [IO.File]::OpenRead($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose(); $stream.Dispose() }
}

function Invoke-WdQ4GuardProvision {
    param([switch] $Apply, [string] $BundleRoot = '', [string] $ExpectedCommit = '',
          [string] $ExpectedManifestSha256 = '', [string] $StatePath = '', [string] $OwnScriptPath = '')
    if (-not $Apply) { return '{"status":"OFF"}' }
    try {
        if ($ExpectedCommit -cnotmatch '^[0-9a-f]{40}$' -or $ExpectedManifestSha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'refused' }
        $root = Assert-WdGuardPlainPath $BundleRoot $true
        $statePathChecked = Assert-WdGuardPlainPath $StatePath $false
        $own = Assert-WdGuardPlainPath $OwnScriptPath $false
        $manifestPath = Assert-WdGuardPlainPath (Join-Path $root 'deployment-manifest.json') $false
        $fleet = Assert-WdGuardPlainPath (Join-Path $root 'wd-fleet.json') $false
        $wrapper = Assert-WdGuardPlainPath (Join-Path $root 'Invoke-WdBridgePython.ps1') $false
        $cli = Assert-WdGuardPlainPath (Join-Path $root 'tools-bootstrap\tools\wd_q4_guard_state_provision_cli.py') $false
        if (-not [String]::Equals($own, (Join-Path $root 'Invoke-WdQ4ProvisionGuard.ps1'), [StringComparison]::OrdinalIgnoreCase)) { throw 'refused' }
        if ((Get-Item -LiteralPath $statePathChecked).Length -gt 1048576 -or (Get-Item -LiteralPath $manifestPath).Length -gt 4194304) { throw 'refused' }
        $manifestHash = Get-WdGuardSha256 $manifestPath
        if ($manifestHash -cne $ExpectedManifestSha256) { throw 'refused' }
        $state = Get-Content -LiteralPath $statePathChecked -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
        if ($manifest.schema_version -ne 1 -or [string]$manifest.source_commit -cne $ExpectedCommit -or
            [string]$state.source_commit -cne $ExpectedCommit -or [string]$state.final_commit -cne $ExpectedCommit -or
            ([string]$state.manifest_sha256).ToLowerInvariant() -cne $ExpectedManifestSha256 -or
            ([string]$state.final_manifest_sha256).ToLowerInvariant() -cne $ExpectedManifestSha256 -or
            -not [String]::Equals([IO.Path]::GetFullPath([string]$state.active_bundle), $root, [StringComparison]::OrdinalIgnoreCase)) { throw 'refused' }
        foreach ($pair in @(@('Invoke-WdQ4ProvisionGuard.ps1',$own), @('Invoke-WdBridgePython.ps1',$wrapper),
                            @('tools-bootstrap/tools/wd_q4_guard_state_provision_cli.py',$cli), @('wd-fleet.json',$fleet))) {
            $pin = $manifest.files.PSObject.Properties[[string]$pair[0]]
            if ($null -eq $pin -or [string]$pin.Value -notmatch '^[0-9a-fA-F]{64}$' -or
                (Get-WdGuardSha256 $pair[1]) -cne ([string]$pin.Value).ToLowerInvariant()) { throw 'refused' }
        }
        if ((Get-WdGuardSha256 $manifestPath) -cne $ExpectedManifestSha256) { throw 'refused' }
    } catch { return '{"status":"REFUSED"}' }
    $old = [Environment]::GetEnvironmentVariable('WD_REBOOT_EXPECTED_MANIFEST_HASH', 'Process')
    try {
        [Environment]::SetEnvironmentVariable('WD_REBOOT_EXPECTED_MANIFEST_HASH', $ExpectedManifestSha256, 'Process')
        $lines = @(& $wrapper 'tools/wd_q4_guard_state_provision_cli.py' '--enable' $root $ExpectedManifestSha256)
        if ($LASTEXITCODE -ne 0 -or $lines.Count -ne 1) { throw 'unknown' }
        $result = [string]$lines[0] | ConvertFrom-Json -ErrorAction Stop
        if (@($result.PSObject.Properties).Count -ne 1 -or [string]$result.status -cne 'READY') { throw 'unknown' }
        return '{"status":"READY"}'
    } catch { return '{"status":"UNKNOWN"}' }
    finally { [Environment]::SetEnvironmentVariable('WD_REBOOT_EXPECTED_MANIFEST_HASH', $old, 'Process') }
}

if ($MyInvocation.InvocationName -ne '.') {
    $result = Invoke-WdQ4GuardProvision -Apply:$Apply -BundleRoot $BundleRoot -ExpectedCommit $ExpectedCommit `
        -ExpectedManifestSha256 $ExpectedManifestSha256 -StatePath 'C:\Python\WD_REBOOT_STATE_CURRENT.json' -OwnScriptPath $PSCommandPath
    Write-Output $result
    $status = ($result | ConvertFrom-Json).status
    if ($status -ceq 'REFUSED') { exit 2 }
    if ($status -ceq 'UNKNOWN') { exit 3 }
    exit 0
}
