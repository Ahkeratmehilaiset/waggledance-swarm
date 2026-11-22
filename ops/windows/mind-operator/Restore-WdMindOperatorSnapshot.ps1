#requires -Version 5.1
<# Candidate for independent review; no production invocation has been tested.
   No automatic deployment, restart or deletion. User-operated only. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('Snapshot','Restore')][string]$Phase,
    [Parameter(Mandatory)][ValidateSet('Production','Synthetic')][string]$Mode,
    [Parameter(Mandatory)][string]$SnapshotRoot,
    [string]$ExpectedSnapshotSha256 = '',
    [switch]$RecoverOldPointer,
    [string]$ExpectedOldPointerSha256 = '',
    [string]$ExpectedCurrentCommit = '',
    [string]$ExpectedCurrentManifestSha256 = '',
    [string]$SyntheticRoot = '',
    [string]$SyntheticCommit = '',
    [string]$SyntheticManifestSha256 = '',
    [string]$SyntheticDisposeFailureName = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if ($Mode -ieq 'Production') {
    if ($SyntheticRoot -or $SyntheticCommit -or $SyntheticManifestSha256 -or $SyntheticDisposeFailureName) { throw 'HOLD synthetic arguments in production' }
    $machine = 'C:\Python'
    $base = 'C:\Python\wd-reboot-backups'
    $expectedCommit = '29a356a99b79ca00a10b69edd080f2de4532693d'
    $expectedManifest = '5A54839D79FDFBF0125281C35431C1A45F7EC1F3474054ABA4AE67F013DA942C'
    $expectedBundle = 'C:\Python\wd-reboot-bundles\29a356a99b79ca00a10b69edd080f2de4532693d'
} else {
    if ($SyntheticCommit -cnotmatch '^[0-9a-f]{40}$' -or $SyntheticManifestSha256 -cnotmatch '^[0-9a-fA-F]{64}$') { throw 'HOLD synthetic pair' }
    $audit = 'C:\Python\project2\.codex-audit'
    if (-not $SyntheticRoot) { throw 'HOLD synthetic root' }
    $SyntheticRoot = [IO.Path]::GetFullPath($SyntheticRoot).TrimEnd('\')
    if ([IO.Path]::GetDirectoryName($SyntheticRoot) -ine $audit -or
        [IO.Path]::GetFileName($SyntheticRoot) -cnotmatch '^pr2179-operator-synthetic-[0-9a-f]{32}$') { throw 'HOLD synthetic boundary' }
    $machine = Join-Path $SyntheticRoot 'machine'
    $base = Join-Path $SyntheticRoot 'backups'
    $expectedCommit = $SyntheticCommit
    $expectedManifest = $SyntheticManifestSha256.ToUpperInvariant()
    $expectedBundle = Join-Path (Join-Path $SyntheticRoot 'bundles') $expectedCommit
}
$pointer = 'WD_REBOOT_STATE_CURRENT.json'
$names = @(
 'CheckedPowerShellResult.ps1','Invoke-WdGrok.ps1','start-wd-all.ps1',
 'start-wd-agent.ps1','start-wd-codex-lead.ps1','start-wd-codex-tools.ps1',
 'start-wd-claude-rco.ps1','start-wd-claude-rco2.ps1','start-wd-fable-5.ps1',
 'wd_supervisor.ps1','start-wd-tools-consumer.ps1','wd_supervisor_loop.json',
 'wd_supervisor_loop_snapshot.json','BOOT_AFTER_REBOOT.md',
 'Set-WdTaskConsoleContainment.ps1','WD_LOCAL_GPU_GUIDE.md',
 'WD_SWARM_PARALLEL_POLICY_V1.md','Write-WdLaneCurrentState.ps1',
 'Get-WdSwarmParallelStatus.ps1','WD_REBOOT_STATE_CURRENT.json',
 'WD_REBOOT_STATE_CURRENT.md','WD_REBOOT_INTEGRITY_CURRENT.sha256',
 'WD_REBOOT_INTEGRITY_CURRENT.json','WD_GROK_MODEL_CURRENT.json','WD_GROK_MODEL_CURRENT.md'
)
if ($names.Count -ne 25 -or @($names | Select-Object -Unique).Count -ne 25) { throw 'HOLD names' }
if ($SyntheticDisposeFailureName -and ($Mode -ine 'Synthetic' -or $names -cnotcontains $SyntheticDisposeFailureName)) { throw 'HOLD synthetic failure input' }
$SnapshotRoot = [IO.Path]::GetFullPath($SnapshotRoot).TrimEnd('\')
if ([IO.Path]::GetDirectoryName($SnapshotRoot) -ine $base -or
    [IO.Path]::GetFileName($SnapshotRoot) -cnotmatch '^mind-pr2179-[0-9a-f]{32}$') { throw 'HOLD backup root' }
function Assert-Directory([string]$path) {
    $part = [IO.DirectoryInfo]::new([IO.Path]::GetFullPath($path))
    while ($null -ne $part) {
        $part.Refresh()
        if (-not $part.Exists -or ($part.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'HOLD directory' }
        $part = $part.Parent
    }
}
function Assert-Leaf([string]$path) {
    $item = Get-Item -LiteralPath $path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'HOLD leaf' }
}
function Read-Leaf([string]$path) {
    Assert-Leaf $path
    return ,([IO.File]::ReadAllBytes($path))
}
function Hash-Bytes([byte[]]$data) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($data)).Replace('-','') }
    finally { $sha.Dispose() }
}
function Target([string]$dir,[string]$name) {
    if (($dir -ine $machine -and $dir -ine $SnapshotRoot) -or $names -cnotcontains $name -or
        [IO.Path]::GetFileName($name) -cne $name) { throw 'HOLD target' }
    $result = [IO.Path]::GetFullPath((Join-Path $dir $name))
    if ([IO.Path]::GetDirectoryName($result) -ine $dir) { throw 'HOLD escaped target' }
    return $result
}
function Write-Leaf([string]$path,[byte[]]$bytes,[bool]$new) {
    if (-not $new) { Assert-Leaf $path }
    $mode = if ($new) { [IO.FileMode]::CreateNew } else { [IO.FileMode]::Truncate }
    $stream = [IO.File]::Open($path,$mode,[IO.FileAccess]::Write,[IO.FileShare]::None)
    try { $stream.Write($bytes,0,$bytes.Length); $stream.Flush($true) } finally { $stream.Dispose() }
}
function Assert-Pointer([byte[]]$bytes) {
    $p = [Text.Encoding]::UTF8.GetString($bytes).TrimStart([char]0xFEFF) | ConvertFrom-Json
    if ($p.source_commit -cne $expectedCommit -or $p.final_commit -cne $expectedCommit -or
        $p.manifest_sha256 -ine $expectedManifest -or $p.final_manifest_sha256 -ine $expectedManifest -or
        [IO.Path]::GetFullPath([string]$p.active_bundle).TrimEnd('\') -ine $expectedBundle) { throw 'HOLD pointer pair' }
}
Assert-Directory $machine
Assert-Directory $base
Assert-Directory $expectedBundle
$oldManifestPath = Join-Path $expectedBundle 'deployment-manifest.json'
$oldManifestBytes = Read-Leaf $oldManifestPath
if ((Hash-Bytes $oldManifestBytes) -ine $expectedManifest) { throw 'HOLD previous bundle manifest hash' }
$oldManifest = [Text.Encoding]::UTF8.GetString($oldManifestBytes).TrimStart([char]0xFEFF) | ConvertFrom-Json
if ($oldManifest.source_commit -cne $expectedCommit) { throw 'HOLD previous bundle manifest commit' }
if ($oldManifest.schema_version -ne 1 -or $null -eq $oldManifest.files -or $oldManifest.files -isnot [pscustomobject]) { throw 'HOLD previous manifest shape' }
$bundlePrefix = [IO.Path]::GetFullPath($expectedBundle).TrimEnd('\') + '\'
foreach ($item in @($oldManifest.files.PSObject.Properties)) {
    $relative = [string]$item.Name
    if (-not $relative -or $relative -match '[\\:]' -or $relative.StartsWith('/') -or
        @($relative.Split('/') | Where-Object { -not $_ -or $_ -in @('.','..') -or $_.EndsWith('.') -or $_.EndsWith(' ') }).Count -gt 0 -or
        $item.Value -isnot [string] -or $item.Value -cnotmatch '^[0-9a-fA-F]{64}$') { throw 'HOLD previous manifest file entry' }
    $bundleFile = [IO.Path]::GetFullPath((Join-Path $expectedBundle ($relative.Replace('/','\'))))
    if (-not $bundleFile.StartsWith($bundlePrefix,[StringComparison]::OrdinalIgnoreCase)) { throw 'HOLD previous bundle file boundary' }
    Assert-Directory ([IO.Path]::GetDirectoryName($bundleFile))
    if ((Hash-Bytes (Read-Leaf $bundleFile)) -ine $item.Value) { throw 'HOLD previous bundle file hash' }
}
if ($Phase -ieq 'Snapshot') {
    if ($RecoverOldPointer -or $ExpectedOldPointerSha256) { throw 'HOLD recovery args only valid for restore' }
    if (Test-Path -LiteralPath $SnapshotRoot) { throw 'HOLD backup already exists' }
    $pointerBefore = Read-Leaf (Join-Path $machine $pointer)
    Assert-Pointer $pointerBefore
    [void][IO.Directory]::CreateDirectory($SnapshotRoot)
    Assert-Directory $SnapshotRoot
    $entries = @()
    foreach ($name in $names) {
        $source = Target $machine $name
        if (Test-Path -LiteralPath $source) {
            $data = Read-Leaf $source
            $sha = Hash-Bytes $data
            $destination = Target $SnapshotRoot $name
            Write-Leaf $destination $data $true
            $copy = Read-Leaf $destination
            if ($copy.Length -ne $data.Length -or (Hash-Bytes $copy) -cne $sha) { throw 'HOLD backup mismatch' }
            $entries += [ordered]@{name=$name; present=$true; length=$data.Length; sha256=$sha}
        } else { $entries += [ordered]@{name=$name; present=$false; length=$null; sha256=$null} }
    }
    $pointerCopy = Read-Leaf (Join-Path $SnapshotRoot $pointer)
    $pointerAfter = Read-Leaf (Join-Path $machine $pointer)
    if ((Hash-Bytes $pointerBefore) -cne (Hash-Bytes $pointerCopy) -or
        (Hash-Bytes $pointerBefore) -cne (Hash-Bytes $pointerAfter)) { throw 'HOLD pointer changed' }
    Assert-Pointer $pointerCopy
    $snap = [ordered]@{schema='wd.operator-rollback-candidate.v1';source_commit=$expectedCommit;
        manifest_sha256=$expectedManifest; machine_root=$machine; backup_root=$SnapshotRoot; files=$entries}
    $bytes = [Text.Encoding]::UTF8.GetBytes(($snap | ConvertTo-Json -Depth 8))
    $path = Join-Path $SnapshotRoot 'snapshot.json'
    Write-Leaf $path $bytes $true
    if ((Hash-Bytes (Read-Leaf $path)) -cne (Hash-Bytes $bytes)) { throw 'HOLD snapshot mismatch' }
    Write-Output ('SNAPSHOT_SHA256=' + (Hash-Bytes $bytes))
    return
}
Assert-Directory $SnapshotRoot
$currentPointer = Read-Leaf (Join-Path $machine $pointer)
if ($RecoverOldPointer) {
    if ($ExpectedOldPointerSha256 -cnotmatch '^[0-9a-fA-F]{64}$') { throw 'HOLD old pointer anchor missing' }
    if ((Hash-Bytes $currentPointer) -ine $ExpectedOldPointerSha256) { throw 'HOLD old pointer anchor mismatch' }
    Assert-Pointer $currentPointer
} else {
    if ($ExpectedOldPointerSha256) { throw 'HOLD old pointer anchor without explicit recovery mode' }
    if ($ExpectedCurrentCommit -cnotmatch '^[0-9a-f]{40}$' -or
        $ExpectedCurrentManifestSha256 -cnotmatch '^[0-9a-fA-F]{64}$') { throw 'HOLD current installation anchor missing' }
    if ($ExpectedCurrentCommit -ceq $expectedCommit -or $ExpectedCurrentManifestSha256 -ieq $expectedManifest) {
        throw 'HOLD ordinary restore requires a distinct current pair'
    }
    $expectedCurrentBundle = if ($Mode -ieq 'Production') {
        Join-Path 'C:\Python\wd-reboot-bundles' $ExpectedCurrentCommit
    } else {
        Join-Path (Join-Path $SyntheticRoot 'bundles') $ExpectedCurrentCommit
    }
    $currentPointer = Read-Leaf (Join-Path $machine $pointer)
    $current = [Text.Encoding]::UTF8.GetString($currentPointer).TrimStart([char]0xFEFF) | ConvertFrom-Json
    if ($current.source_commit -cne $ExpectedCurrentCommit -or $current.final_commit -cne $ExpectedCurrentCommit -or
        $current.manifest_sha256 -ine $ExpectedCurrentManifestSha256 -or $current.final_manifest_sha256 -ine $ExpectedCurrentManifestSha256 -or
        [IO.Path]::GetFullPath([string]$current.active_bundle).TrimEnd('\') -ine $expectedCurrentBundle) { throw 'HOLD current installation binding' }
}
if ($ExpectedSnapshotSha256 -cnotmatch '^[0-9A-Fa-f]{64}$') { throw 'HOLD missing snapshot anchor' }
$raw = Read-Leaf (Join-Path $SnapshotRoot 'snapshot.json')
if ((Hash-Bytes $raw) -ine $ExpectedSnapshotSha256) { throw 'HOLD snapshot anchor mismatch' }
$snapshot = [Text.Encoding]::UTF8.GetString($raw) | ConvertFrom-Json
if ($snapshot.schema -cne 'wd.operator-rollback-candidate.v1' -or
    $snapshot.source_commit -cne $expectedCommit -or $snapshot.manifest_sha256 -ine $expectedManifest -or
    $snapshot.machine_root -ine $machine -or $snapshot.backup_root -ine $SnapshotRoot -or
    @($snapshot.files).Count -ne 25) { throw 'HOLD snapshot binding' }
$inventory = @{}
foreach ($entry in $snapshot.files) {
    if ($entry.name -isnot [string] -or $names -cnotcontains $entry.name -or
        $inventory.ContainsKey($entry.name) -or $entry.present -isnot [bool]) { throw 'HOLD snapshot entries' }
    $inventory[$entry.name] = $entry
}
if ($inventory.Count -ne 25 -or -not $inventory[$pointer].present) { throw 'HOLD pointer missing' }
# Cache every verified backup before touching any target. No live deletions: if a
# previously absent leaf now exists, HOLD until safe identity-bound recovery exists.
$verified = @{}
foreach ($name in $names) {
    $entry = $inventory[$name]
    $target = Target $machine $name
    $copy = Target $SnapshotRoot $name
    if ($entry.present) {
        if ($entry.sha256 -isnot [string] -or $entry.sha256 -cnotmatch '^[0-9A-F]{64}$' -or
            (($entry.length -isnot [int]) -and ($entry.length -isnot [long])) -or $entry.length -lt 0) { throw 'HOLD backup metadata' }
        $data = Read-Leaf $copy
        if ($data.Length -ne $entry.length -or (Hash-Bytes $data) -cne $entry.sha256) { throw 'HOLD backup verification' }
        if (Test-Path -LiteralPath $target) { Assert-Leaf $target }
        $verified[$name] = $data
    } else {
        if ($null -ne $entry.sha256 -or $null -ne $entry.length -or (Test-Path -LiteralPath $copy)) { throw 'HOLD absence metadata' }
        if (Test-Path -LiteralPath $target) { throw 'HOLD previously absent target now exists' }
    }
}
Assert-Pointer $verified[$pointer]
if ($RecoverOldPointer -and (Hash-Bytes $verified[$pointer]) -ine $ExpectedOldPointerSha256) { throw 'HOLD snapshot old pointer mismatch' }
if ((Hash-Bytes (Read-Leaf (Join-Path $machine $pointer))) -cne (Hash-Bytes $currentPointer)) { throw 'HOLD current pointer changed' }
# Hold exclusive handles for every originally present target before any mutation.
# A missing target refuses instead of creating it during partial restoration.
# This only prevents failures caused by an already-held incompatible handle; it
# does not make the following writes atomic or prove ACL/path/identity safety.
$handles = @{}
try {
    foreach ($name in $names) {
        $target = Target $machine $name
        if ($inventory[$name].present) {
            Assert-Leaf $target
            $handles[$name] = [IO.File]::Open($target,[IO.FileMode]::Open,[IO.FileAccess]::ReadWrite,[IO.FileShare]::None)
        } elseif (Test-Path -LiteralPath $target) { throw 'HOLD previously absent target now exists' }
    }
    # Compare pointer through the held handle: the path cannot be freshly opened
    # with this process's own FileShare.None handle still open.
    $pstream = $handles[$pointer]
    if ($pstream.Length -gt 65536) { throw 'HOLD current pointer size' }
    $pstream.Position = 0
    $pointerBytes = New-Object byte[] ([int]$pstream.Length)
    $read = 0
    while ($read -lt $pointerBytes.Length) {
        $n = $pstream.Read($pointerBytes,$read,$pointerBytes.Length-$read)
        if ($n -le 0) { throw 'HOLD current pointer read' }
        $read += $n
    }
    if ((Hash-Bytes $pointerBytes) -cne (Hash-Bytes $currentPointer)) { throw 'HOLD current pointer changed after preflight' }
    foreach ($name in $names) {
        if (-not $inventory[$name].present -and (Test-Path -LiteralPath (Target $machine $name))) { throw 'HOLD previously absent target now exists' }
    }
    $order = @($names | Where-Object { $_ -cne $pointer }) + @($pointer)
    foreach ($name in $order) {
        if ($inventory[$name].present) {
            $stream = $handles[$name]
            $stream.Position = 0
            $stream.SetLength(0)
            $data = $verified[$name]
            $stream.Write($data,0,$data.Length)
            $stream.Flush($true)
            $stream.Position = 0
            $after = New-Object byte[] ([int]$stream.Length)
            $read = 0
            while ($read -lt $after.Length) {
                $n = $stream.Read($after,$read,$after.Length-$read)
                if ($n -le 0) { throw 'HOLD target short read' }
                $read += $n
            }
            if ($after.Length -ne $inventory[$name].length -or (Hash-Bytes $after) -cne $inventory[$name].sha256) { throw 'HOLD target verification' }
        }
        Write-Output ('PROCESSED ' + $name)
    }
    foreach ($name in $names) {
        if ($inventory[$name].present) {
            $stream = $handles[$name]
            $stream.Position = 0
            $after = New-Object byte[] ([int]$stream.Length)
            $read = 0
            while ($read -lt $after.Length) {
                $n = $stream.Read($after,$read,$after.Length-$read)
                if ($n -le 0) { throw 'HOLD final short read' }
                $read += $n
            }
            if ($after.Length -ne $inventory[$name].length -or (Hash-Bytes $after) -cne $inventory[$name].sha256) { throw 'HOLD final verification' }
        } elseif (Test-Path -LiteralPath (Target $machine $name)) { throw 'HOLD final absence' }
    }
    $pstream.Position = 0
    $pointerAfter = New-Object byte[] ([int]$pstream.Length)
    $read = 0
    while ($read -lt $pointerAfter.Length) {
        $n = $pstream.Read($pointerAfter,$read,$pointerAfter.Length-$read)
        if ($n -le 0) { throw 'HOLD final pointer read' }
        $read += $n
    }
    Assert-Pointer $pointerAfter
} finally {
    $cleanupFailed = $false
    foreach ($name in $names) {
        if (-not $handles.ContainsKey($name)) { continue }
        try {
            $handles[$name].Dispose()
            # Injection occurs only in synthetic mode after actual handle close.
            if ($SyntheticDisposeFailureName -and $SyntheticDisposeFailureName -ceq $name) { throw 'synthetic dispose error' }
        } catch { $cleanupFailed = $true }
        if ($SyntheticDisposeFailureName) { Write-Output ('SYNTHETIC_DISPOSE_ATTEMPT ' + $name) }
    }
    if ($cleanupFailed) { throw 'HOLD handle cleanup failed' }
}
Write-Output 'RESTORE_CANDIDATE_COMPLETE'
