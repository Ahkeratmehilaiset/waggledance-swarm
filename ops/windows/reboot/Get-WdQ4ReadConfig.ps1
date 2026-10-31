#requires -Version 5.1
# Configuration only. No Q4 entrypoint, data read, model load or connection proof.
function ConvertFrom-WdQ4StrictJson {
    param([string] $Text)
    # Two independently manifest-pinned entrypoints need this bootstrap guard:
    # wrapper before it may load any helper, and standalone config helper.
    # Tests require their bodies to remain identical. Framework JSON parsing
    # supplies grammar; this raw-token pass forbids per-object key collisions.
    if (-not $Text -or $Text.Length -gt 1048576 -or
        -not $Text.TrimStart().StartsWith('{') -or -not $Text.TrimEnd().EndsWith('}')) {
        throw 'q4_json_refused'
    }
    $parsed=ConvertFrom-Json -InputObject $Text -ErrorAction Stop
    if ($parsed -isnot [pscustomobject]) { throw 'q4_json_refused' }
    $pattern='"(?:\\["\\/bfnrt]|\\u[0-9a-fA-F]{4}|[^"\\\x00-\x1f])*"|[{}\[\],:]|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?|true|false|null|[ \t\r\n]+|(?<bad>.)'
    $tokens=[regex]::Matches($Text,$pattern,[Text.RegularExpressions.RegexOptions]::Singleline)
    $stack=[Collections.Generic.List[object]]::new()
    $count=0
    foreach ($token in $tokens) {
        $count++
        if ($count -gt 131072 -or $token.Groups['bad'].Success) { throw 'q4_json_refused' }
        $value=$token.Value
        if ($value -ceq '{' -or $value -ceq '[') {
            if ($stack.Count -ge 64) { throw 'q4_json_refused' }
            $stack.Add([pscustomobject]@{Object=($value -ceq '{');ExpectKey=$true;
                Keys=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)})
        } elseif ($value -ceq '}' -or $value -ceq ']') {
            if ($stack.Count -eq 0 -or
                $stack[$stack.Count-1].Object -ne ($value -ceq '}')) { throw 'q4_json_refused' }
            $stack.RemoveAt($stack.Count-1)
        } elseif ($value -ceq ',') {
            if ($stack.Count -eq 0) { throw 'q4_json_refused' }
            if ($stack[$stack.Count-1].Object) { $stack[$stack.Count-1].ExpectKey=$true }
        } elseif ($value.StartsWith('"') -and $stack.Count -gt 0) {
            $frame=$stack[$stack.Count-1]
            if ($frame.Object -and $frame.ExpectKey) {
                # Decode JSON escapes as a VALUE under one fixed key, never as
                # attacker-chosen property names before collision detection.
                $decoded=ConvertFrom-Json -InputObject ('{"key":'+$value+'}') -ErrorAction Stop
                if ($decoded.key -isnot [string] -or -not $frame.Keys.Add($decoded.key)) {
                    throw 'q4_json_duplicate_key'
                }
                $frame.ExpectKey=$false
            }
        }
    }
    if ($stack.Count -ne 0) { throw 'q4_json_refused' }
    return $parsed
}

function Get-WdQ4ReadConfig {
    <#
    The caller must verify FleetSha256 against its externally anchored deployment
    manifest and pin this helper before invoking it. A self-computed fleet digest
    establishes consistency, not authorization. Tuple pins below are configuration,
    not evidence that runtime/model/data bytes have been opened or verified.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $FleetPath,
        [Parameter(Mandatory)] [string] $FleetSha256
    )

    function Assert-Q4Path {
        param([string] $Value, [ValidateSet('File', 'Directory')] [string] $ExpectedType = 'File')
        if ([string]::IsNullOrWhiteSpace($Value) -or $Value.Length -gt 4096 -or $Value -cnotmatch '^[A-Za-z]:\\' -or
            $Value.Substring(2).Contains(':') -or $Value.Contains('/') -or
            $Value.IndexOfAny([char[]]'<>|"*?') -ge 0 -or $Value -match '[\x00-\x1f]') {
            throw 'q4_config_path_invalid'
        }
        foreach ($segment in $Value.Substring(3).Split('\')) {
            if (-not $segment -or $segment -ceq '.' -or $segment -ceq '..' -or
                $segment.Length -gt 255 -or $segment.StartsWith(' ') -or
                $segment.EndsWith(' ') -or $segment.EndsWith('.') -or
                $segment -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$') {
                throw 'q4_config_path_invalid'
            }
        }
        try {
            $full = [IO.Path]::GetFullPath($Value)
            $drive = New-Object IO.DriveInfo([IO.Path]::GetPathRoot($full))
            if (-not $drive.IsReady -or $drive.DriveType -ne [IO.DriveType]::Fixed) {
                throw 'q4_config_drive_invalid'
            }
            $current = $full
            while ($current) {
                try {
                    $attrs = [IO.File]::GetAttributes($current)
                    if (($attrs -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                        throw 'q4_config_reparse_refused'
                    }
                    if ($current -ceq $full -and
                        (($ExpectedType -ceq 'Directory' -and ($attrs -band [IO.FileAttributes]::Directory) -eq 0) -or
                         ($ExpectedType -ceq 'File' -and ($attrs -band [IO.FileAttributes]::Directory) -ne 0))) {
                        throw 'q4_config_path_kind_invalid'
                    }
                    if ($current -cne $full -and ($attrs -band [IO.FileAttributes]::Directory) -eq 0) {
                        throw 'q4_config_ancestor_not_directory'
                    }
                } catch [IO.FileNotFoundException] {
                } catch [IO.DirectoryNotFoundException] {
                }
                $parent = [IO.Path]::GetDirectoryName($current)
                if (-not $parent -or $parent -ceq $current) { break }
                $current = $parent
            }
            return $full
        } catch { throw 'q4_config_path_refused' }
    }

    if ($FleetSha256 -cnotmatch '^[0-9A-Fa-f]{64}$') { throw 'q4_config_fleet_digest_invalid' }
    $fleetFull = Assert-Q4Path $FleetPath
    try {
        $stream = [IO.File]::Open($fleetFull, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        try {
            if ($stream.Length -gt 1048576) { throw 'q4_config_fleet_too_large' }
            $buffer = New-Object byte[] ([int]$stream.Length)
            $offset = 0
            while ($offset -lt $buffer.Length) {
                $count = $stream.Read($buffer, $offset, $buffer.Length - $offset)
                if ($count -le 0) { throw 'q4_config_fleet_short_read' }
                $offset += $count
            }
            if ($stream.ReadByte() -ne -1) { throw 'q4_config_fleet_changed' }
        } finally { $stream.Dispose() }
        $sha = [Security.Cryptography.SHA256]::Create()
        try { $actual = [BitConverter]::ToString($sha.ComputeHash($buffer)).Replace('-', '').ToLowerInvariant() }
        finally { $sha.Dispose() }
        if ($actual -cne $FleetSha256.ToLowerInvariant()) { throw 'q4_config_fleet_digest_mismatch' }
        $encoding = New-Object Text.UTF8Encoding($false, $true)
        $fleet = ConvertFrom-WdQ4StrictJson -Text ($encoding.GetString($buffer).TrimStart([char]0xFEFF))
    } catch { throw 'q4_config_fleet_refused' }
    if ($null -eq $fleet -or $fleet -isnot [pscustomobject]) { throw 'q4_config_fleet_invalid' }
    $property = $fleet.PSObject.Properties['q4_read']
    if ($null -eq $property) { return $null }
    if ($property.Name -cne 'q4_read' -or $null -eq $property.Value -or
        $property.Value -isnot [pscustomobject]) { throw 'q4_config_invalid' }
    $config = $property.Value
    $enabled = $config.PSObject.Properties['enabled']
    if ($null -eq $enabled -or $enabled.Name -cne 'enabled' -or $enabled.Value -isnot [bool]) {
        throw 'q4_config_invalid'
    }
    if (-not $enabled.Value) { return $null }

    $paths = @('memory_root', 'runtime_root', 'scratch_root', 'descriptor_path', 'python_path', 'runtime_manifest_path',
               'code_manifest_path', 'lock_file')
    $digests = @('descriptor_sha256', 'python_sha256', 'runtime_manifest_sha256', 'code_manifest_sha256')
    $required = @('schema', 'enabled', 'code_commit', 'runtime_generation', 'runtime_revision', 'lock_timeout_seconds') + $paths + $digests
    foreach ($name in @($config.PSObject.Properties.Name)) {
        if ($required -cnotcontains $name -and $name -cne 'resolved_inputs') { throw 'q4_config_unknown_field' }
    }
    foreach ($name in $required) {
        if ($null -eq $config.PSObject.Properties[$name]) { throw 'q4_config_field_missing' }
    }
    if ($config.schema -isnot [string] -or $config.schema -cne 'wd.q4-read-config.v1' -or
        $config.code_commit -isnot [string] -or $config.code_commit -cnotmatch '^[0-9a-f]{40}$') {
        throw 'q4_config_identity_invalid'
    }
    foreach ($name in @('runtime_generation', 'runtime_revision')) {
        if ($config.$name -isnot [string] -or $config.$name -cnotmatch '^(?:[0-9a-f]{40}|[0-9a-f]{64})$') {
            throw 'q4_config_identity_invalid'
        }
    }
    $values = @{}
    foreach ($name in $paths) {
        if ($config.$name -isnot [string]) { throw 'q4_config_path_invalid' }
        $kind = if ($name -in @('memory_root', 'runtime_root', 'scratch_root')) { 'Directory' } else { 'File' }
        $values[$name] = Assert-Q4Path -Value $config.$name -ExpectedType $kind
    }
    if (-not [IO.Directory]::Exists($values.scratch_root)) { throw 'q4_config_scratch_missing' }
    foreach ($other in @($values.memory_root,$values.runtime_root)) {
        $left=$values.scratch_root.TrimEnd('\');$right=$other.TrimEnd('\')
        if ($left.Equals($right,[StringComparison]::OrdinalIgnoreCase) -or
            $left.StartsWith($right+'\',[StringComparison]::OrdinalIgnoreCase) -or
            $right.StartsWith($left+'\',[StringComparison]::OrdinalIgnoreCase)) {
            throw 'q4_config_scratch_overlap'
        }
    }
    $runtimePrefix = $values.runtime_root.TrimEnd('\') + '\'
    if ($values.runtime_manifest_path.Equals($values.runtime_root, [StringComparison]::OrdinalIgnoreCase) -or
        $values.runtime_manifest_path.StartsWith($runtimePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'q4_config_manifest_inside_runtime'
    }
    foreach ($name in $digests) {
        if ($config.$name -isnot [string] -or $config.$name -cnotmatch '^[0-9a-f]{64}$') {
            throw 'q4_config_digest_invalid'
        }
    }
    $rawTimeout = $config.lock_timeout_seconds
    if ($rawTimeout -isnot [int] -and $rawTimeout -isnot [long] -and
        $rawTimeout -isnot [double] -and $rawTimeout -isnot [decimal]) {
        throw 'q4_config_timeout_invalid'
    }
    $timeout = [double]$rawTimeout
    if ([double]::IsNaN($timeout) -or [double]::IsInfinity($timeout) -or $timeout -lt 0 -or $timeout -gt 30) {
        throw 'q4_config_timeout_invalid'
    }

    # Optional resolved snapshots are configuration only. Deployment/caller
    # authorization is external; no Source data is discovered or opened here.
    $resolvedValues = @{}
    $resolvedProperty = $config.PSObject.Properties['resolved_inputs']
    if ($null -ne $resolvedProperty) {
        $r = $resolvedProperty.Value
        $keys = @('schema','memory_path','memory_sha256','projection_path','projection_sha256','policy_path','policy_sha256','roster_path','roster_sha256')
        if ($resolvedProperty.Name -cne 'resolved_inputs' -or $r -isnot [pscustomobject] -or
            @($r.PSObject.Properties).Count -ne $keys.Count -or
            $r.schema -isnot [string] -or $r.schema -cne 'wd.q4-resolved-inputs.v1') {
            throw 'q4_config_resolved_invalid'
        }
        foreach ($key in @($r.PSObject.Properties.Name)) {
            if ($keys -cnotcontains $key) { throw 'q4_config_resolved_invalid' }
        }
        foreach ($name in @('memory','projection','policy','roster')) {
            $pathKey=$name+'_path';$hashKey=$name+'_sha256'
            if ($r.$pathKey -isnot [string] -or $r.$hashKey -isnot [string] -or
                $r.$hashKey -cnotmatch '^[0-9a-f]{64}$') { throw 'q4_config_resolved_invalid' }
            $resolvedValues['WD_Q4_RESOLVED_'+$name.ToUpperInvariant()+'_PATH']=Assert-Q4Path $r.$pathKey
            $resolvedValues['WD_Q4_RESOLVED_'+$name.ToUpperInvariant()+'_SHA256']=$r.$hashKey
        }
    }
    $out = [ordered]@{
        WD_Q4_READ_ENABLED = '1'
        WD_Q4_MEMORY_ROOT = $values.memory_root
        WD_Q4_DESCRIPTOR = $values.descriptor_path
        WD_Q4_DESCRIPTOR_SHA256 = $config.descriptor_sha256
        WD_Q4_PYTHON = $values.python_path
        WD_Q4_PYTHON_SHA256 = $config.python_sha256
        WD_Q4_RUNTIME_ROOT = $values.runtime_root
        WD_Q4_SCRATCH_ROOT = $values.scratch_root
        WD_Q4_RUNTIME_GENERATION = $config.runtime_generation
        WD_Q4_RUNTIME_REVISION = $config.runtime_revision
        WD_Q4_RUNTIME_MANIFEST = $values.runtime_manifest_path
        WD_Q4_RUNTIME_MANIFEST_SHA256 = $config.runtime_manifest_sha256
        WD_Q4_CODE_MANIFEST = $values.code_manifest_path
        WD_Q4_CODE_MANIFEST_SHA256 = $config.code_manifest_sha256
        WD_Q4_CODE_COMMIT = $config.code_commit
        WD_Q4_LOCK_FILE = $values.lock_file
        WD_Q4_LOCK_TIMEOUT = $timeout.ToString('R', [Globalization.CultureInfo]::InvariantCulture)
        WD_Q4_FLEET = $fleetFull
        WD_Q4_FLEET_SHA256 = $actual
    }
    foreach ($key in $resolvedValues.Keys) { $out[$key]=$resolvedValues[$key] }
    return [pscustomobject]$out
}
