#requires -Version 5.1
<# Fixed on-demand public status in the inherited lane session. Default OFF.
Candidate code and the active bridge have independent caller-pinned manifests.
This emits no query or arbitrary text and cannot authenticate the same OS user.
#>
[CmdletBinding(PositionalBinding=$false)]
param([switch]$Run, [string]$CandidateBundle='', [string]$ExpectedCandidateManifestSha256='')
$ErrorActionPreference='Stop'
if (-not $Run) { [Console]::Out.WriteLine('{"status":"OFF"}'); return }
$entered=$false
try {
    # The packaged bridge wrapper uses Get-FileHash. A lane may inherit a
    # restricted PSModulePath, so load the inbox module by its fixed path.
    if ($PSVersionTable.PSEdition -ceq 'Desktop') {
        Import-Module -Name (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -ErrorAction Stop
    }
    function CheckPath([string]$Path,[bool]$Directory=$false) {
        if ($Path -notmatch '^[Cc]:\\' -or $Path.Substring(2).Contains(':') -or
            ($Path -split '[\\/]' | Where-Object { $_ -in @('.','..') })) { throw 'path' }
        $full=[IO.Path]::GetFullPath($Path)
        $item=Get-Item -LiteralPath $full -Force -ErrorAction Stop
        if ([bool]($item.Attributes -band [IO.FileAttributes]::Directory) -ne $Directory) { throw 'type' }
        while ($null -ne $item) {
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'reparse' }
            if ($item -is [IO.DirectoryInfo]) { $item=$item.Parent } else { $item=$item.Directory }
        }
        return $full
    }
    function HashMatches([string]$Path,[string]$Pin) {
        if ($Pin -cnotmatch '^[0-9a-fA-F]{64}$') { throw 'pin' }
        $stream=[IO.File]::OpenRead($Path); $sha=[Security.Cryptography.SHA256]::Create()
        try { $actual=[BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','') }
        finally { $sha.Dispose(); $stream.Dispose() }
        return $actual -ceq $Pin.ToUpperInvariant()
    }
    $activePin=[string]$env:WD_REBOOT_EXPECTED_MANIFEST_HASH
    if ($activePin -cnotmatch '^[0-9a-fA-F]{64}$' -or
        $ExpectedCandidateManifestSha256 -cnotmatch '^[0-9a-fA-F]{64}$') { throw 'pin' }
    $activeBin=CheckPath ([string]$env:WD_BRIDGE_BIN) $true
    $activeBundle=Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $activeBin))
    if (-not [IO.Path]::GetFullPath($activeBin).Equals(
            (Join-Path $activeBundle 'tools-bootstrap\.agent-bridge\bin'),[StringComparison]::OrdinalIgnoreCase)) { throw 'active' }
    $activeManifest=CheckPath (Join-Path $activeBundle 'deployment-manifest.json')
    if (-not (HashMatches $activeManifest $activePin)) { throw 'active' }
    $active=Get-Content -LiteralPath $activeManifest -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    foreach ($name in @('Get-BridgeExecutionEvidence.ps1','Write-AgentEvent.ps1')) {
        $path=CheckPath (Join-Path $activeBin $name)
        $pin=[string]$active.files.PSObject.Properties[('tools-bootstrap/.agent-bridge/bin/'+$name)].Value
        if (-not (HashMatches $path $pin)) { throw 'active' }
    }
    $candidate=CheckPath $CandidateBundle $true
    $manifest=CheckPath (Join-Path $candidate 'deployment-manifest.json')
    if (-not (HashMatches $manifest $ExpectedCandidateManifestSha256)) { throw 'candidate' }
    $doc=Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
    $required=@('Invoke-WdBridgePython.ps1','BridgeCodeContext.ps1','bridge-code-files.json','wd-fleet.json',
        'tools-bootstrap/tools/wd_mind_public_event_writer.py',
        'tools-bootstrap/tools/wd_mind_public_event_writer_cli.py',
        'tools-bootstrap/configs/mind/pilot_non_review_policy.json',
        'tools-bootstrap/configs/mind/pilot_public_status_policy.json')
    foreach ($name in $required) {
        $path=CheckPath (Join-Path $candidate $name.Replace('/','\'))
        $pin=[string]$doc.files.PSObject.Properties[$name].Value
        if (-not (HashMatches $path $pin)) { throw 'candidate' }
    }
    $wrapper=Join-Path $candidate 'Invoke-WdBridgePython.ps1'
    $oldActive=[Environment]::GetEnvironmentVariable('WD_MIND_PUBLIC_ACTIVE_MANIFEST_SHA256','Process')
    $oldCandidate=[Environment]::GetEnvironmentVariable('WD_REBOOT_EXPECTED_MANIFEST_HASH','Process')
    try {
        [Environment]::SetEnvironmentVariable('WD_MIND_PUBLIC_ACTIVE_MANIFEST_SHA256',$activePin,'Process')
        [Environment]::SetEnvironmentVariable('WD_REBOOT_EXPECTED_MANIFEST_HASH',$ExpectedCandidateManifestSha256,'Process')
        $entered=$true
        $lines=@(& $wrapper 'tools/wd_mind_public_event_writer_cli.py' '--enable')
        $code=$LASTEXITCODE
        if ($lines.Count -ne 1 -or ([string]$lines[0]).Length -gt 512) { throw 'result' }
        $value=[string]$lines[0] | ConvertFrom-Json -ErrorAction Stop
        $keys=@($value.PSObject.Properties.Name)
        if ($code -eq 0 -and [string]$value.status -ceq 'WRITTEN' -and $keys.Count -eq 2 -and
            [string]$value.delivery -ceq 'canonical') { [Console]::Out.WriteLine('{"status":"WRITTEN","delivery":"canonical"}'); return }
        if ($code -eq 0 -and [string]$value.status -ceq 'QUEUED' -and $keys.Count -eq 2 -and
            [string]$value.retained_wal_sha256 -cmatch '^[0-9a-f]{64}$') {
            [Console]::Out.WriteLine(('{"status":"QUEUED","retained_wal_sha256":"'+$value.retained_wal_sha256+'"}')); return
        }
        if ($code -eq 2 -and $keys.Count -eq 1 -and [string]$value.status -ceq 'REFUSED') {
            [Console]::Out.WriteLine('{"status":"REFUSED"}'); exit 2
        }
        throw 'unknown'
    } finally {
        [Environment]::SetEnvironmentVariable('WD_MIND_PUBLIC_ACTIVE_MANIFEST_SHA256',$oldActive,'Process')
        [Environment]::SetEnvironmentVariable('WD_REBOOT_EXPECTED_MANIFEST_HASH',$oldCandidate,'Process')
    }
} catch {
    if ($entered) { [Console]::Out.WriteLine('{"status":"UNKNOWN"}'); exit 3 }
    [Console]::Out.WriteLine('{"status":"REFUSED"}'); exit 2
}
