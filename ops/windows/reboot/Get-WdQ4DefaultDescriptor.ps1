#requires -Version 5.1
# Read-only: the wrapper must hash-verify these helper bytes against its
# externally anchored bundle manifest before dot-sourcing this function.
function Get-WdQ4DefaultDescriptor {
    param([string] $PointerPath, [string] $ExpectedPointerSha256, [string] $ActiveBundle,
        [string] $ExpectedManifestHash, [string] $ExpectedCommit,
        [string] $ExplicitPath = '', [string] $ExplicitSha256 = '')
    function Result([string]$State, [string]$Path = '', [string]$Hash = '', [string]$Reason = '') {
        return [pscustomobject]@{ State=$State; DescriptorPath=$Path; DescriptorSha256=$Hash; Reason=$Reason }
    }
    function CheckPath([string]$Path) {
        $full=[IO.Path]::GetFullPath($Path)
        if ([IO.Path]::GetPathRoot($full).TrimEnd('\') -cne 'C:') { throw 'path' }
        $current=$full
        while ($current) {
            $item=Get-Item -LiteralPath $current -Force -ErrorAction Stop
            if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'reparse' }
            $parent=[IO.Path]::GetDirectoryName($current)
            if (-not $parent -or $parent -ceq $current) { break }; $current=$parent
        }
        return $full
    }
    function ReadPinned([string]$Path,[string]$Pin,[int]$Limit) {
        $full=CheckPath $Path
        $stream=[IO.File]::Open($full,[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
        try {
            if ($stream.Length -le 0 -or $stream.Length -gt $Limit) { throw 'size' }
            $bytes=New-Object byte[] ([int]$stream.Length);$offset=0
            while ($offset -lt $bytes.Length) {
                $count=$stream.Read($bytes,$offset,$bytes.Length-$offset)
                if ($count -le 0) { throw 'read' };$offset+=$count
            }
            if ($stream.ReadByte() -ne -1) { throw 'size' }
        } finally { $stream.Dispose() }
        $sha=[Security.Cryptography.SHA256]::Create()
        try { $actual=[BitConverter]::ToString($sha.ComputeHash($bytes)).Replace('-','') } finally { $sha.Dispose() }
        if ($actual -cne $Pin.ToUpperInvariant()) { throw 'hash' }
        return [Text.UTF8Encoding]::new($false,$true).GetString($bytes)
    }
    try {
        $hasExplicit = -not [string]::IsNullOrWhiteSpace($ExplicitPath)
        if ($hasExplicit -ne (-not [string]::IsNullOrWhiteSpace($ExplicitSha256))) { throw 'pair' }
        if ($hasExplicit -and $ExplicitSha256 -cnotmatch '^[0-9a-f]{64}$') { throw 'pair' }
        if (-not $ExpectedPointerSha256) {
            if (Test-Path -LiteralPath $PointerPath) { throw 'unanchored' }
            if ($hasExplicit) { return Result 'MANUAL' $ExplicitPath $ExplicitSha256 }
            return Result 'OFF'
        }
        if ($ExpectedPointerSha256 -cnotmatch '^[0-9A-Fa-f]{64}$' -or
            $ExpectedManifestHash -cnotmatch '^[0-9A-Fa-f]{64}$' -or
            $ExpectedCommit -cnotmatch '^[0-9a-f]{40}$') { throw 'anchor' }
        $root=CheckPath $ActiveBundle
        $manifestPath=Join-Path $root 'deployment-manifest.json'
        $manifestText=ReadPinned $manifestPath $ExpectedManifestHash 1048576
        $manifest=$manifestText | ConvertFrom-Json -ErrorAction Stop
        $helperPath=Join-Path $root 'Get-WdQ4SharedSessionConfig.ps1'
        $helperPin=[string]$manifest.files.PSObject.Properties['Get-WdQ4SharedSessionConfig.ps1'].Value
        $fleetPin=[string]$manifest.files.PSObject.Properties['wd-fleet.json'].Value
        if ($helperPin -cnotmatch '^[0-9A-Fa-f]{64}$' -or $fleetPin -cnotmatch '^[0-9A-Fa-f]{64}$') { throw 'anchor' }
        $helperText=ReadPinned $helperPath $helperPin 1048576
        . ([scriptblock]::Create($helperText))
        $pointer=ConvertFrom-WdSharedSessionJson (ReadPinned $PointerPath $ExpectedPointerSha256 4096)
        $keys=@('schema','active_bundle','manifest_sha256','bundle_commit','descriptor_path','descriptor_sha256','generation')
        if (@($pointer.PSObject.Properties).Count -ne $keys.Count) { throw 'shape' }
        foreach ($prop in $pointer.PSObject.Properties) {
            if ($keys -cnotcontains $prop.Name -or $prop.Value -isnot [string]) { throw 'shape' }
        }
        if ($pointer.schema -cne 'wd.q4-descriptor-pointer.v1' -or
            $pointer.manifest_sha256 -cne $ExpectedManifestHash.ToUpperInvariant() -or
            $pointer.bundle_commit -cne $ExpectedCommit -or
            $pointer.generation -cnotmatch '^(?:[0-9a-f]{40}|[0-9a-f]{64})$' -or
            $pointer.descriptor_sha256 -cnotmatch '^[0-9a-f]{64}$' -or
            -not [IO.Path]::GetFullPath($pointer.active_bundle).TrimEnd('\').Equals(
                $root.TrimEnd('\'),[StringComparison]::OrdinalIgnoreCase)) { throw 'stale' }
        $descriptorPath=CheckPath $pointer.descriptor_path
        if ($descriptorPath.StartsWith($root.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'cycle' }
        if ([IO.Path]::GetFileName($descriptorPath) -cne 'shared-session-config.v2.json' -or
            [IO.Path]::GetFileName([IO.Path]::GetDirectoryName($descriptorPath)) -cne $pointer.generation) {
            throw 'generation'
        }
        if ($hasExplicit -and (-not [IO.Path]::GetFullPath($ExplicitPath).Equals(
                $descriptorPath,[StringComparison]::OrdinalIgnoreCase) -or
                $ExplicitSha256 -cne $pointer.descriptor_sha256)) { throw 'explicit_conflict' }
        $descriptor=ConvertFrom-WdSharedSessionJson (ReadPinned $descriptorPath $pointer.descriptor_sha256 65536)
        if ([string]$descriptor.schema -cne 'wd.q4-shared-session-config.v2' -or
            -not [IO.Path]::GetFullPath([string]$descriptor.bundle_root).TrimEnd('\').Equals(
                $root.TrimEnd('\'),[StringComparison]::OrdinalIgnoreCase)) { throw 'descriptor' }
        $fleetPath=Join-Path $root 'wd-fleet.json'
        foreach ($lane in @('codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5')) {
            $value=Get-WdQ4SharedSessionConfig -FleetPath $fleetPath -FleetSha256 $fleetPin.ToLowerInvariant() `
                -DescriptorPath $descriptorPath -DescriptorSha256 $pointer.descriptor_sha256 -Lane $lane -Enabled
            if ($null -eq $value -or [string]$value.lane -cne $lane) { throw 'lane' }
        }
        $expectedBindingPath = Join-Path ([IO.Path]::GetDirectoryName($descriptorPath)) 'lane-binding.v1.json'
        if (-not [IO.Path]::GetFullPath([string]$descriptor.binding_path).Equals(
                [IO.Path]::GetFullPath($expectedBindingPath),[StringComparison]::OrdinalIgnoreCase)) { throw 'binding' }
        $binding=ConvertFrom-WdSharedSessionJson (ReadPinned ([string]$descriptor.binding_path) ([string]$descriptor.binding_sha256) 65536)
        if ([string]$binding.schema -cne 'wd.q4-lane-binding.v1' -or [string]$binding.bundle_commit -cne $ExpectedCommit -or
            ([string]$binding.manifest_sha256).ToUpperInvariant() -cne $ExpectedManifestHash.ToUpperInvariant()) { throw 'binding' }
        if ($binding.binding -isnot [pscustomobject] -or $descriptor.expected_binding -isnot [pscustomobject]) { throw 'binding' }
        $bindingNames=[string[]]@($binding.binding.PSObject.Properties.Name)
        $descriptorNames=[string[]]@($descriptor.expected_binding.PSObject.Properties.Name)
        [Array]::Sort($bindingNames,[StringComparer]::Ordinal)
        [Array]::Sort($descriptorNames,[StringComparer]::Ordinal)
        if (($bindingNames -join ',') -cne ($descriptorNames -join ',')) { throw 'binding' }
        foreach ($name in $descriptorNames) {
            if ([string]$binding.binding.$name -cne [string]$descriptor.expected_binding.$name) { throw 'binding' }
        }
        [void](ReadPinned $manifestPath $ExpectedManifestHash 1048576)
        [void](ReadPinned $PointerPath $ExpectedPointerSha256 4096)
        [void](ReadPinned $descriptorPath $pointer.descriptor_sha256 65536)
        return Result 'READY' $descriptorPath $pointer.descriptor_sha256
    } catch { return Result 'UNAVAILABLE' '' '' 'q4_default_descriptor_refused' }
}
