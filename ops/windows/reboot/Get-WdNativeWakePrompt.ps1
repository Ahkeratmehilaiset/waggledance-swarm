#requires -Version 5.1
[CmdletBinding(DefaultParameterSetName='Notification')]
param(
    [Parameter(Mandatory)]
    [ValidateSet('codex-lead-1', 'codex-tools-1')]
    [string] $Agent,
    [Parameter(Mandatory, ParameterSetName='Notification')]
    [string] $DeliveryId,
    [Parameter(Mandatory, ParameterSetName='Projection')]
    [switch] $TaskProjection,
    [Parameter(Mandatory, ParameterSetName='Projection')]
    [string] $TaskId,
    [Parameter(ParameterSetName='Projection')]
    [AllowEmptyString()]
    [ValidateLength(0, 256)]
    [string] $PreviousProjectionKey = '',
    [Parameter(Mandatory)]
    [string] $BundleRoot,
    [string] $ExpectedManifestHash = $env:WD_REBOOT_EXPECTED_MANIFEST_HASH
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($Agent -cnotin @('codex-lead-1', 'codex-tools-1')) {
    throw 'Native wake agent must match its canonical lowercase identity exactly'
}
if ($TaskProjection) {
    if ($Agent -cne 'codex-tools-1' -or $env:AGENT_BRIDGE_AGENT -cne $Agent -or
        $env:AGENT_BRIDGE_SESSION_ID -cnotmatch '\A[A-Za-z0-9._:-]{1,160}\z' -or
        $TaskId -cnotmatch '\A[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}\z' -or
        $ExpectedManifestHash -ine $env:WD_REBOOT_EXPECTED_MANIFEST_HASH) {
        throw 'Tools Mind task projection requires its launcher session, manifest anchor and task'
    }
} elseif ($DeliveryId -cnotmatch '^[0-9a-f]{32}$') {
    throw 'Native wake delivery_id must be 32 lowercase hexadecimal characters'
}
if ($ExpectedManifestHash -cnotmatch '^[0-9A-Fa-f]{64}$') {
    throw 'Native wake procedure requires a trusted manifest SHA-256 anchor'
}
if (-not [IO.Path]::IsPathRooted($BundleRoot)) {
    throw 'Native wake bundle root must be absolute'
}

$root = [IO.Path]::GetFullPath($BundleRoot).TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar)
if (-not [string]::Equals($root, $BundleRoot.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar), [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Native wake bundle root must be fully qualified without implicit drive resolution'
}
if (-not [IO.Directory]::Exists($root)) {
    throw 'Native wake bundle root is missing'
}
$component = $root
while ($component) {
    $componentItem = Get-Item -LiteralPath $component -Force -ErrorAction Stop
    if (($componentItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw 'Native wake bundle path contains a reparse point'
    }
    $parent = [IO.Path]::GetDirectoryName($component.TrimEnd([IO.Path]::DirectorySeparatorChar, [IO.Path]::AltDirectorySeparatorChar))
    if (-not $parent -or [string]::Equals($parent, $component, [StringComparison]::OrdinalIgnoreCase)) {
        break
    }
    $component = $parent
}

function Assert-DirectBundleFile {
    param([string] $Root, [string] $Name)
    $path = [IO.Path]::GetFullPath([IO.Path]::Combine($Root, $Name))
    if (-not [string]::Equals([IO.Path]::GetDirectoryName($path), $Root, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Native wake bundle file escapes its root'
    }
    if (-not [IO.File]::Exists($path)) {
        throw ('Native wake bundle file is missing: ' + $Name)
    }
    $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw ('Native wake bundle file is a reparse point: ' + $Name)
    }
    return $path
}

# Avoid a PowerShell 7 parent's PSModulePath breaking Get-FileHash discovery
# in Windows PowerShell 5.1. Match the existing continuity publisher contract.
function Get-NativeWakeBytesHash {
    param([byte[]] $Bytes)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $hash = $sha.ComputeHash($Bytes)
        return ([BitConverter]::ToString($hash)).Replace('-', '')
    } finally { $sha.Dispose() }
}

$manifestPath = Assert-DirectBundleFile -Root $root -Name 'deployment-manifest.json'
if ((Get-Item -LiteralPath $manifestPath -Force).Length -gt 1048576) {
    throw 'Native wake deployment manifest is oversized'
}
$manifestBytes = [IO.File]::ReadAllBytes($manifestPath)
if ($manifestBytes.Length -gt 1048576) { throw 'Native wake deployment manifest is oversized' }
$manifestHash = Get-NativeWakeBytesHash -Bytes $manifestBytes
if (-not [string]::Equals($manifestHash, $ExpectedManifestHash, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Native wake deployment manifest differs from its trusted anchor'
}
$manifest = [Text.Encoding]::UTF8.GetString($manifestBytes).TrimStart([char]0xFEFF) | ConvertFrom-Json -ErrorAction Stop
if ($null -eq $manifest -or $manifest.schema_version -ne 1 -or $null -eq $manifest.files) {
    throw 'Native wake deployment manifest has an invalid schema'
}

function Read-NativeWakePinnedBytes {
    param([string] $Name, [int] $MaxBytes, [string] $FileKind)
    $entries = @($manifest.files.PSObject.Properties | Where-Object { $_.Name -ceq $Name })
    if ($entries.Count -ne 1 -or [string]$entries[0].Value -cnotmatch '^[0-9A-Fa-f]{64}$') {
        throw ('Native wake ' + $FileKind + ' is not hash-pinned in the manifest: ' + $Name)
    }
    $path = Assert-DirectBundleFile -Root $root -Name $Name
    if ((Get-Item -LiteralPath $path -Force).Length -gt $MaxBytes) {
        throw ('Native wake ' + $FileKind + ' is oversized')
    }
    $bytes = [IO.File]::ReadAllBytes($path)
    if ($bytes.Length -gt $MaxBytes) { throw ('Native wake ' + $FileKind + ' is oversized') }
    $hash = Get-NativeWakeBytesHash -Bytes $bytes
    if ($hash -ine [string]$entries[0].Value) {
        throw ('Native wake ' + $FileKind + ' differs from its deployment manifest pin')
    }
    return [pscustomobject]@{Path=$path; Bytes=$bytes; Hash=$hash}
}

if ($TaskProjection) {
    # The key describes the last attempt. It never authorizes execution or carries prior view bytes.
    $key = 'wd.tools-mind-projection.v1=' + $manifestHash.ToLowerInvariant() + ':' + $TaskId
    $projection = [ordered]@{schema='wd.tools-mind-projection.v1'; authority='none';
        outcome='unchanged'; checkpoint_evidence=$key; view_sha256=$null; view_json=$null}
    if ($PreviousProjectionKey -cne $key) {
        $projection.outcome = 'unavailable'
        try {
            $reader = Read-NativeWakePinnedBytes -Name 'Invoke-WdMindStartupRead.ps1' -MaxBytes 65536 -FileKind 'Mind reader'
            $utf8 = New-Object Text.UTF8Encoding($false,$true)
            . ([scriptblock]::Create($utf8.GetString($reader.Bytes).TrimStart([char]0xFEFF)))
            # Omit Worktree to use the existing read-only path with no receipt write.
            $read = Invoke-WdMindStartupRead -BundleRoot $root -ExpectedManifestHash $manifestHash -Lane $Agent
            if ($read.outcome -ceq 'success' -and $read.view_json -is [string] -and $read.view_json.Length -gt 0) {
                $viewBytes = $utf8.GetBytes($read.view_json)
                $viewHash = (Get-NativeWakeBytesHash -Bytes $viewBytes).ToLowerInvariant()
                if ($viewBytes.Length -le 8192 -and $read.view_sha256 -ceq $viewHash) {
                    $projection.outcome = 'success'
                    $projection.view_json = $read.view_json
                    $projection.view_sha256 = $viewHash
                }
            }
        } catch [Management.Automation.PipelineStoppedException] { throw }
        catch {
            # Optional context remains unavailable on a read or local pin failure.
        }
    }
    ConvertTo-Json -InputObject $projection -Compress
    return
}

$procedureName = if ($Agent -ceq 'codex-lead-1') { 'WAKE_PROCEDURE_LEAD.md' } else { 'WAKE_PROCEDURE_TOOLS.md' }
$procedure = Read-NativeWakePinnedBytes -Name $procedureName -MaxBytes 65536 -FileKind 'procedure'
'Automatic bridge wake for ' + $Agent + '; delivery_id=' + $DeliveryId + '. Read and follow the verified procedure at ' + $procedure.Path + ' (SHA256 ' + $procedure.Hash + ').'
