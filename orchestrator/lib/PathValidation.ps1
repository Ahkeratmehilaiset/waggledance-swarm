# PathValidation.ps1
# Strict validation for IterationId and any path the orchestrator joins
# against project-controlled directories. Prevents path traversal, absolute
# overrides, and reserved characters.
#
# Compatible with PowerShell 5.1.

Set-StrictMode -Version Latest

# Iteration IDs are filesystem-safe: alnum, dot, underscore, hyphen, 1..80 chars.
$Script:IterationIdRegex = '^[A-Za-z0-9._\-]{1,80}$'

# Reserved Windows device names (case-insensitive, with or without extension)
$Script:ReservedNames = @('CON','PRN','AUX','NUL',
    'COM1','COM2','COM3','COM4','COM5','COM6','COM7','COM8','COM9',
    'LPT1','LPT2','LPT3','LPT4','LPT5','LPT6','LPT7','LPT8','LPT9')

function Test-IterationIdValid {
    <#
    .SYNOPSIS
    Returns $true only if the id passes all safety checks.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)] [AllowEmptyString()] [string] $Id)

    if ([string]::IsNullOrWhiteSpace($Id))                  { return $false }
    if ($Id -notmatch $Script:IterationIdRegex)             { return $false }
    if ($Id.Contains('..'))                                 { return $false }
    if ($Id.Contains('/') -or $Id.Contains('\'))            { return $false }
    if ($Id.Contains(':'))                                  { return $false }
    if ($Id.StartsWith('.'))                                { return $false }
    if ($Id.StartsWith('-'))                                { return $false }  # avoid CLI-flag confusion
    if ($Id.EndsWith('.'))                                  { return $false }
    if ([System.IO.Path]::IsPathRooted($Id))                { return $false }
    $core = ($Id -split '\.', 2)[0]
    if ($Script:ReservedNames -contains $core.ToUpperInvariant()) { return $false }
    return $true
}

function Assert-IterationIdValid {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [AllowEmptyString()] [string] $Id)
    if (-not (Test-IterationIdValid -Id $Id)) {
        throw "Invalid IterationId '$Id'. Must match $Script:IterationIdRegex and not contain path separators or reserved names."
    }
}

function Get-SafeIterationFolder {
    <#
    .SYNOPSIS
    Joins iterationsRoot + IterationId, then verifies the resolved path is
    physically under iterationsRoot. Throws if validation fails.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $IterationsRoot,
        [Parameter(Mandatory)] [string] $IterationId
    )
    Assert-IterationIdValid -Id $IterationId

    if (-not (Test-Path $IterationsRoot)) {
        New-Item -ItemType Directory -Path $IterationsRoot -Force | Out-Null
    }
    $rootFull = (Resolve-Path -Path $IterationsRoot).ProviderPath
    $candidate = Join-Path $rootFull $IterationId

    # Resolve the candidate even if it does not yet exist, by normalising:
    $candidateFull = [System.IO.Path]::GetFullPath($candidate)
    $rootSep = $rootFull
    if (-not $rootSep.EndsWith([IO.Path]::DirectorySeparatorChar)) {
        $rootSep = $rootSep + [IO.Path]::DirectorySeparatorChar
    }

    if (-not ($candidateFull.StartsWith($rootSep, [System.StringComparison]::OrdinalIgnoreCase) -or
              $candidateFull.Equals($rootFull, [System.StringComparison]::OrdinalIgnoreCase))) {
        throw "Path traversal detected: '$candidateFull' is not under '$rootFull'"
    }

    return $candidateFull
}
