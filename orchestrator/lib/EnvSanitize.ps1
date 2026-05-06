# EnvSanitize.ps1
# Build a deliberately-controlled environment-variable map for the child
# Claude Code process. The default mode strips known credential carriers.
#
# Compatible with PowerShell 5.1.

Set-StrictMode -Version Latest

$Script:DefaultEnvDenylistPatterns = @(
    '^ANTHROPIC_API_KEY$',
    '^OPENAI_API_KEY$',
    '^GITHUB_TOKEN$',
    '^GH_TOKEN$',
    '^AWS_ACCESS_KEY_ID$',
    '^AWS_SECRET_ACCESS_KEY$',
    '^AWS_SESSION_TOKEN$',
    '^GOOGLE_APPLICATION_CREDENTIALS$',
    '^.*_API_KEY$',
    '^.*_TOKEN$',
    '^.*_SECRET$',
    '^.*_PASSWORD$',
    '^.*_PWD$',
    '^.*PRIVATE_KEY$',
    '^NPM_TOKEN$',
    '^HF_TOKEN$',
    '^DOCKER_PASSWORD$'
)

# Variables that ALWAYS pass through, even if they happen to match a denylist
# pattern, because the child cannot run without them.
$Script:DefaultEnvAlwaysAllow = @(
    'PATH', 'PATHEXT',
    'USER', 'USERNAME', 'USERPROFILE', 'HOME', 'HOMEPATH', 'HOMEDRIVE',
    'TEMP', 'TMP', 'TMPDIR',
    'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA', 'PROGRAMFILES', 'PROGRAMFILES(X86)',
    'PROGRAMW6432', 'COMMONPROGRAMFILES', 'COMMONPROGRAMFILES(X86)',
    'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'COMSPEC',
    'COMPUTERNAME', 'NUMBER_OF_PROCESSORS', 'PROCESSOR_ARCHITECTURE',
    'OS', 'LANG', 'LC_ALL', 'LC_CTYPE',
    'TERM', 'COLORTERM',
    'SHELL'
)

function Get-DefaultEnvDenylistPatterns { return ,$Script:DefaultEnvDenylistPatterns }
function Get-DefaultEnvAlwaysAllow      { return ,$Script:DefaultEnvAlwaysAllow }

function Test-EnvNameMatchesAny {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $Name, [string[]] $Patterns)
    if (-not $Patterns) { return $false }
    foreach ($p in $Patterns) {
        if ($Name -match $p) { return $true }
    }
    return $false
}

function Get-SanitizedEnvironment {
    <#
    .SYNOPSIS
    Returns a hashtable name->value of the variables to expose to the child.

    Default behavior: pass everything that does NOT match any denylist
    pattern; never strip anything that matches AlwaysAllow.

    Setting -AllowlistOnly switches to allowlist mode, where only AllowList
    + AlwaysAllow names are passed.

    Returns also the list of names that were stripped, for logging.
    #>
    [CmdletBinding()]
    param(
        [string[]] $DenylistPatterns = $null,
        [string[]] $AllowList = @(),
        [string[]] $AlwaysAllow = $null,
        [switch]   $AllowlistOnly
    )

    if ($null -eq $DenylistPatterns) { $DenylistPatterns = $Script:DefaultEnvDenylistPatterns }
    if ($null -eq $AlwaysAllow)      { $AlwaysAllow      = $Script:DefaultEnvAlwaysAllow }

    $upperAllow = @{}
    foreach ($n in @($AllowList) + @($AlwaysAllow)) {
        if ($n) { $upperAllow[$n.ToUpperInvariant()] = $true }
    }

    $result  = @{}
    $stripped = @()
    foreach ($entry in [System.Environment]::GetEnvironmentVariables('Process').GetEnumerator()) {
        $name = [string]$entry.Key
        $upper = $name.ToUpperInvariant()
        $isAlwaysAllow = $upperAllow.ContainsKey($upper)

        if ($AllowlistOnly) {
            if ($isAlwaysAllow) {
                $result[$name] = [string]$entry.Value
            } else {
                $stripped += $name
            }
            continue
        }

        if ($isAlwaysAllow) {
            $result[$name] = [string]$entry.Value
            continue
        }

        if (Test-EnvNameMatchesAny -Name $name -Patterns $DenylistPatterns) {
            $stripped += $name
        } else {
            $result[$name] = [string]$entry.Value
        }
    }

    return [pscustomobject]@{
        environment = $result
        stripped    = $stripped
    }
}

function Get-ParentSecretsPresent {
    <#
    .SYNOPSIS
    Returns the names (not values) of variables in the parent process that
    look like credentials and would be stripped by Get-SanitizedEnvironment.
    Used by preflight to warn the user.
    #>
    [CmdletBinding()]
    param([string[]] $DenylistPatterns = $null, [string[]] $AlwaysAllow = $null)

    if ($null -eq $DenylistPatterns) { $DenylistPatterns = $Script:DefaultEnvDenylistPatterns }
    if ($null -eq $AlwaysAllow)      { $AlwaysAllow      = $Script:DefaultEnvAlwaysAllow }

    $upperAllow = @{}
    foreach ($n in $AlwaysAllow) { if ($n) { $upperAllow[$n.ToUpperInvariant()] = $true } }

    $hits = @()
    foreach ($entry in [System.Environment]::GetEnvironmentVariables('Process').GetEnumerator()) {
        $name = [string]$entry.Key
        if ($upperAllow.ContainsKey($name.ToUpperInvariant())) { continue }
        if (Test-EnvNameMatchesAny -Name $name -Patterns $DenylistPatterns) { $hits += $name }
    }
    return ,$hits
}
