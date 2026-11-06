#requires -Version 5.1

function Get-WdMindReadConfig {
    <#
    .SYNOPSIS
    Derive the optional read-only Mind session settings from verified fleet bytes.

    .DESCRIPTION
    The caller must independently verify FleetSha256 against the anchored deployment
    manifest before invoking this function. Comparing the caller's digest to the
    file proves consistency only, not authorization or manifest authenticity.
    This function changes no environment variable, creates no path and starts no
    process. A path check is not a handle pin: later replacement remains possible.
    DriveInfo cannot establish the ultimate target of a substituted drive letter.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $FleetPath,
        [Parameter(Mandatory)] [string] $FleetSha256
    )

    function Assert-MindLocalPath {
        param([Parameter(Mandatory)] [string] $Path)
        if ($Path -cnotmatch '^[A-Za-z]:\\' -or $Path.Substring(2).Contains(':') -or
            $Path.Contains('/') -or $Path.IndexOfAny([IO.Path]::GetInvalidPathChars()) -ge 0) {
            throw 'mind_config_path_invalid'
        }
        foreach ($segment in $Path.Substring(3).Split('\')) {
            if ($segment -ceq '.' -or $segment -ceq '..' -or
                $segment.EndsWith(' ') -or $segment.EndsWith('.') -or
                $segment.IndexOfAny([char[]]'*?') -ge 0) {
                throw 'mind_config_path_invalid'
            }
        }
        try {
            $full = [IO.Path]::GetFullPath($Path)
            $drive = New-Object IO.DriveInfo([IO.Path]::GetPathRoot($full))
            if (-not $drive.IsReady -or $drive.DriveType -ne [IO.DriveType]::Fixed) {
                throw 'mind_config_drive_invalid'
            }
            $current = $full
            while ($current) {
                try {
                    $attributes = [IO.File]::GetAttributes($current)
                    if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                        throw 'mind_config_reparse_refused'
                    }
                    if ($current -cne $full -and ($attributes -band [IO.FileAttributes]::Directory) -eq 0) {
                        throw 'mind_config_ancestor_not_directory'
                    }
                } catch [IO.FileNotFoundException] {
                    # A producer may create its own fresh live directory later.
                } catch [IO.DirectoryNotFoundException] {
                    # Continue to check all existing ancestors.
                }
                $parent = [IO.Path]::GetDirectoryName($current)
                if (-not $parent -or $parent -ceq $current) { break }
                $current = $parent
            }
            return $full
        } catch {
            throw 'mind_config_path_refused'
        }
    }

    if ($FleetSha256 -cnotmatch '^[0-9A-Fa-f]{64}$') {
        throw 'mind_config_fleet_digest_invalid'
    }
    $fleetFull = Assert-MindLocalPath -Path $FleetPath
    try {
        $stream = [IO.File]::Open($fleetFull, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        try {
            if ($stream.Length -gt 1048576) { throw 'mind_config_fleet_too_large' }
            $buffer = New-Object byte[] ([int]$stream.Length)
            $position = 0
            while ($position -lt $buffer.Length) {
                $count = $stream.Read($buffer, $position, $buffer.Length - $position)
                if ($count -le 0) { throw 'mind_config_fleet_short_read' }
                $position += $count
            }
            if ($stream.ReadByte() -ne -1) { throw 'mind_config_fleet_changed' }
        } finally {
            $stream.Dispose()
        }
        $sha = [Security.Cryptography.SHA256]::Create()
        try {
            $digest = [BitConverter]::ToString($sha.ComputeHash($buffer)).Replace('-', '').ToLowerInvariant()
        } finally {
            $sha.Dispose()
        }
        if ($digest -cne $FleetSha256.ToLowerInvariant()) { throw 'mind_config_fleet_digest_mismatch' }
        $encoding = New-Object Text.UTF8Encoding($false, $true)
        $text = $encoding.GetString($buffer).TrimStart([char]0xFEFF)
        $fleet = $text | ConvertFrom-Json -ErrorAction Stop
    } catch {
        throw 'mind_config_fleet_refused'
    }
    if ($null -eq $fleet -or $fleet -isnot [pscustomobject]) { throw 'mind_config_fleet_invalid' }
    $property = $fleet.PSObject.Properties['mind_read']
    if ($null -eq $property) { return $null }
    $config = $property.Value
    if ($null -eq $config -or $config -isnot [pscustomobject]) { throw 'mind_config_invalid' }
    $enabled = $config.PSObject.Properties['enabled']
    if ($null -eq $enabled -or $enabled.Value -isnot [bool]) { throw 'mind_config_invalid' }
    if (-not $enabled.Value) { return $null }
    $liveProperty = $config.PSObject.Properties['live_dir']
    $liveMode = $config.PSObject.Properties['live_dir_mode']
    if ($null -ne $liveProperty -and $null -eq $liveMode) {
        if ($liveProperty.Value -isnot [string] -or [string]::IsNullOrWhiteSpace($liveProperty.Value)) {
            throw 'mind_config_invalid'
        }
        $live = Assert-MindLocalPath -Path $liveProperty.Value
    } elseif ($null -eq $liveProperty -and $null -ne $liveMode -and
              $liveMode.Value -ceq 'shared_runtime_audit') {
        $runtimeProperty = $fleet.PSObject.Properties['runtime_root']
        if ($null -eq $runtimeProperty -or $runtimeProperty.Value -isnot [string] -or
            [string]::IsNullOrWhiteSpace($runtimeProperty.Value)) { throw 'mind_config_invalid' }
        $runtime = Assert-MindLocalPath -Path $runtimeProperty.Value
        $live = Assert-MindLocalPath -Path (Join-Path $runtime 'shared\.codex-audit\mind-live')
    } else { throw 'mind_config_invalid' }
    if ($live -ieq [IO.Path]::GetPathRoot($live)) { throw 'mind_config_live_dir_refused' }
    $live = $live.TrimEnd('\')
    $parent = [IO.Path]::GetDirectoryName($live)
    $auditRoot = $null
    while ($parent) {
        if ([IO.Path]::GetFileName($parent) -ceq '.codex-audit') {
            $auditRoot = $parent
            break
        }
        $next = [IO.Path]::GetDirectoryName($parent)
        if (-not $next -or $next -ceq $parent) { break }
        $parent = $next
    }
    if (-not $auditRoot -or -not [IO.Directory]::Exists($auditRoot)) { throw 'mind_config_audit_root_refused' }
    if ([IO.File]::Exists($live)) { throw 'mind_config_live_dir_refused' }
    $latest = Assert-MindLocalPath -Path (Join-Path $live 'latest.json')
    if ([IO.Directory]::Exists($latest)) { throw 'mind_config_latest_refused' }
    return [pscustomobject]@{
        WD_MIND_READ_ENABLED = '1'
        WD_MIND_LATEST = $latest
        WD_MIND_AUDIT_ROOT = $auditRoot
        WD_MIND_FLEET = $fleetFull
        WD_MIND_FLEET_SHA256 = $digest
    }
}
