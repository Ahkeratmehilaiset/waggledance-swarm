#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2A-3 review-mode safety regression tests.
.DESCRIPTION
    Validates the review-mode safety invariants from three angles:

    1. Pure validator (Test-WaggleReviewSafeProfileViolations) over
       synthetic positive + negative fixtures.
    2. Runtime gate (Assert-WaggleReviewSafeProfile) throws on
       deliberately-corrupted effective profiles.
    3. Real review-mode metadata fixtures shaped like
       <iter>/reviews/<role>.metadata.json must satisfy the same
       invariants when fed back through the validator.

    The tests do NOT depend on any specific local iteration folder.
    All "metadata" fixtures are synthesized in this file.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib\review\ReviewAdapter.ps1')

$Script:Pass = 0
$Script:Fail = 0

function Assert-True {
    param([string] $Name, [bool] $Cond, [string] $Detail = '')
    if ($Cond) {
        Write-Host "PASS  $Name" -ForegroundColor Green
        $Script:Pass++
    } else {
        Write-Host "FAIL  $Name $Detail" -ForegroundColor Red
        $Script:Fail++
    }
}

# ----------------- positive fixture (the canonical safe profile) --------

$safe = Get-WaggleReviewSafeProfile
$safeArgList = @(
    '-p',
    '--model', 'opus',
    '--output-format', 'text',
    '--max-turns', '60',
    '--permission-mode', 'default',
    '--allowed-tools', 'Read,Glob,Grep',
    '--disallowed-tools', 'Bash,Write,Edit'
)

$r = Test-WaggleReviewSafeProfileViolations -EffectiveProfile $safe -ArgList $safeArgList
Assert-True 'positive: canonical safe profile passes' ($r.ok -and $r.violations.Count -eq 0) ($r.violations -join '; ')

# Same predicate without ArgList still passes.
$r = Test-WaggleReviewSafeProfileViolations -EffectiveProfile $safe
Assert-True 'positive: profile-only check passes' ($r.ok)

# Runtime gate must NOT throw on the safe profile.
$threw = $false
try { [void](Assert-WaggleReviewSafeProfile -EffectiveProfile $safe -ArgList $safeArgList) } catch { $threw = $true }
Assert-True 'positive: runtime gate does not throw on safe profile' (-not $threw)

# ----------------- negative fixtures: profile mutations -----------------

function New-MutatedProfile {
    param(
        [string] $FieldName,
        $NewValue
    )
    $base = Get-WaggleReviewSafeProfile
    $copy = [pscustomobject]@{
        safeMode                   = $base.safeMode
        allowBash                  = $base.allowBash
        dangerouslySkipPermissions = $base.dangerouslySkipPermissions
        requireUniqueArtifact      = $base.requireUniqueArtifact
        sanitizeEnvironment        = $base.sanitizeEnvironment
        allowedTools               = @($base.allowedTools)
        disallowedTools            = @($base.disallowedTools)
        exitMarker                 = $base.exitMarker
    }
    $copy.$FieldName = $NewValue
    return $copy
}

# Bash, Write, Edit each leaked into allowedTools
foreach ($tool in 'Bash', 'Write', 'Edit') {
    $bad = New-MutatedProfile -FieldName 'allowedTools' -NewValue (@('Read', 'Glob', 'Grep') + $tool)
    $r = Test-WaggleReviewSafeProfileViolations -EffectiveProfile $bad
    Assert-True ("negative: $tool in allowedTools rejected") (-not $r.ok -and (($r.violations -join ' ') -match "$tool must not appear in allowedTools"))
}

# Each of Bash, Write, Edit missing from disallowedTools
foreach ($tool in 'Bash', 'Write', 'Edit') {
    $partial = @()
    foreach ($t in @('Bash', 'Write', 'Edit')) { if ($t -ne $tool) { $partial += $t } }
    $bad = New-MutatedProfile -FieldName 'disallowedTools' -NewValue $partial
    $r = Test-WaggleReviewSafeProfileViolations -EffectiveProfile $bad
    Assert-True ("negative: $tool missing from disallowedTools rejected") (-not $r.ok -and (($r.violations -join ' ') -match "$tool must appear in disallowedTools"))
}

# allow_bash flipped true
$bad = New-MutatedProfile -FieldName 'allowBash' -NewValue $true
$r = Test-WaggleReviewSafeProfileViolations -EffectiveProfile $bad
Assert-True 'negative: allowBash=true rejected' (-not $r.ok -and (($r.violations -join ' ') -match 'allowBash must be False'))

# dangerously_skip_permissions flipped true
$bad = New-MutatedProfile -FieldName 'dangerouslySkipPermissions' -NewValue $true
$r = Test-WaggleReviewSafeProfileViolations -EffectiveProfile $bad
Assert-True 'negative: dangerouslySkipPermissions=true rejected' (-not $r.ok -and (($r.violations -join ' ') -match 'dangerouslySkipPermissions must be False'))

# require_unique_artifact flipped true
$bad = New-MutatedProfile -FieldName 'requireUniqueArtifact' -NewValue $true
$r = Test-WaggleReviewSafeProfileViolations -EffectiveProfile $bad
Assert-True 'negative: requireUniqueArtifact=true rejected' (-not $r.ok -and (($r.violations -join ' ') -match 'requireUniqueArtifact must be False'))

# sanitize_environment flipped false
$bad = New-MutatedProfile -FieldName 'sanitizeEnvironment' -NewValue $false
$r = Test-WaggleReviewSafeProfileViolations -EffectiveProfile $bad
Assert-True 'negative: sanitizeEnvironment=false rejected' (-not $r.ok -and (($r.violations -join ' ') -match 'sanitizeEnvironment must be True'))

# null profile
$r = Test-WaggleReviewSafeProfileViolations -EffectiveProfile $null
Assert-True 'negative: null profile rejected' (-not $r.ok -and (($r.violations -join ' ') -match 'null'))

# ----------------- negative fixtures: arg-list mutations ----------------

# --dangerously-skip-permissions in argList
$dangerArgs = $safeArgList + '--dangerously-skip-permissions'
$r = Test-WaggleReviewSafeProfileViolations -EffectiveProfile $safe -ArgList $dangerArgs
Assert-True 'negative: --dangerously-skip-permissions in ArgList rejected' (-not $r.ok -and (($r.violations -join ' ') -match 'dangerously-skip-permissions must not appear in ArgList'))

# Bash leaked into --allowed-tools cli value
$bashArgs = @(
    '-p', '--model', 'opus',
    '--allowed-tools', 'Read,Glob,Grep,Bash',
    '--disallowed-tools', 'Bash,Write,Edit'
)
$r = Test-WaggleReviewSafeProfileViolations -EffectiveProfile $safe -ArgList $bashArgs
Assert-True 'negative: Bash in --allowed-tools cli value rejected' (-not $r.ok -and (($r.violations -join ' ') -match 'ArgList must not contain Bash'))

# Write leaked
$writeArgs = @(
    '-p', '--model', 'opus',
    '--allowed-tools', 'Read,Write,Glob,Grep'
)
$r = Test-WaggleReviewSafeProfileViolations -EffectiveProfile $safe -ArgList $writeArgs
Assert-True 'negative: Write in --allowed-tools cli value rejected' (-not $r.ok -and (($r.violations -join ' ') -match 'ArgList must not contain Write'))

# Edit leaked
$editArgs = @(
    '-p', '--model', 'opus',
    '--allowed-tools', 'Read,Edit,Glob,Grep'
)
$r = Test-WaggleReviewSafeProfileViolations -EffectiveProfile $safe -ArgList $editArgs
Assert-True 'negative: Edit in --allowed-tools cli value rejected' (-not $r.ok -and (($r.violations -join ' ') -match 'ArgList must not contain Edit'))

# ----------------- runtime gate throws -----------------

$bad = New-MutatedProfile -FieldName 'allowBash' -NewValue $true
$threw = $false
$msg = ''
try { [void](Assert-WaggleReviewSafeProfile -EffectiveProfile $bad -ArgList $safeArgList) }
catch { $threw = $true; $msg = $_.Exception.Message }
Assert-True 'runtime gate: allowBash=true throws' $threw
Assert-True 'runtime gate: throw message names allowBash field' ($msg -match 'allowBash')

$bad = New-MutatedProfile -FieldName 'allowedTools' -NewValue (@('Read', 'Bash', 'Glob'))
$threw = $false
try { [void](Assert-WaggleReviewSafeProfile -EffectiveProfile $bad -ArgList $safeArgList) } catch { $threw = $true }
Assert-True 'runtime gate: Bash in allowedTools throws' $threw

$threw = $false
try { [void](Assert-WaggleReviewSafeProfile -EffectiveProfile $safe -ArgList ($safeArgList + '--dangerously-skip-permissions')) } catch { $threw = $true }
Assert-True 'runtime gate: dangerously-skip-permissions in ArgList throws' $threw

# ----------------- review metadata invariants -----------------

# A real review-mode metadata file (synthetic but shaped like the
# Phase 2A-2 runner emits). The validator must accept this AND must
# reject negative variants.

function New-FakeMetadata {
    param(
        [bool] $AllowBash = $false,
        [bool] $DangerouslySkip = $false,
        [bool] $UniqueArtifact = $false,
        [bool] $SanitizeEnv = $true,
        [string[]] $Allowed = @('Read','Glob','Grep'),
        [string[]] $Disallowed = @('Bash','Write','Edit'),
        [string] $CommandLineFragment = ''
    )
    if (-not $CommandLineFragment) {
        $CommandLineFragment = 'claude -p --model opus --output-format text --max-turns 60 --permission-mode default --allowed-tools ' + ($Allowed -join ',') + ' --disallowed-tools ' + ($Disallowed -join ',')
    }
    return [pscustomobject]@{
        source_iteration_id          = '2026-05-06_19-45-54'
        review_iteration_id          = '2026-05-06_20-26-14_review_architect'
        role                         = 'architect'
        status                       = 'COMPLETED'
        package_path                 = 'iterations/2026-05-06_19-45-54/llm_input_package.md'
        safe_mode                    = $true
        allow_bash                   = $AllowBash
        dangerously_skip_permissions = $DangerouslySkip
        require_unique_artifact      = $UniqueArtifact
        sanitize_environment         = $SanitizeEnv
        allowed_tools                = $Allowed
        disallowed_tools             = $Disallowed
        run_result                   = [pscustomobject]@{
            command_line = $CommandLineFragment
        }
    }
}

# Convert the metadata-shape into the profile-shape the validator
# expects. (The Phase 2A-2 runner serializes metadata with snake_case
# keys; the validator works on the camelCase profile shape -- this
# helper is the bridge tests use.)
function ConvertTo-ReviewProfileShape {
    param($Metadata)
    return [pscustomobject]@{
        safeMode                   = [bool]$Metadata.safe_mode
        allowBash                  = [bool]$Metadata.allow_bash
        dangerouslySkipPermissions = [bool]$Metadata.dangerously_skip_permissions
        requireUniqueArtifact      = [bool]$Metadata.require_unique_artifact
        sanitizeEnvironment        = [bool]$Metadata.sanitize_environment
        allowedTools               = @($Metadata.allowed_tools)
        disallowedTools            = @($Metadata.disallowed_tools)
        exitMarker                 = 'REVIEW-COMPLETE'
    }
}

$good = New-FakeMetadata
$gp = ConvertTo-ReviewProfileShape -Metadata $good
$gargs = ($good.run_result.command_line -split '\s+')
$r = Test-WaggleReviewSafeProfileViolations -EffectiveProfile $gp -ArgList $gargs
Assert-True 'meta-fixture positive: canonical metadata is safe' ($r.ok -and $r.violations.Count -eq 0) ($r.violations -join '; ')

# Bash leaked into allowed_tools in the metadata
$bad = New-FakeMetadata -Allowed @('Read','Glob','Grep','Bash')
$bp = ConvertTo-ReviewProfileShape -Metadata $bad
$r = Test-WaggleReviewSafeProfileViolations -EffectiveProfile $bp -ArgList ($bad.run_result.command_line -split '\s+')
Assert-True 'meta-fixture negative: Bash in allowed_tools rejected' (-not $r.ok)

# command_line carrying --dangerously-skip-permissions
$bad = New-FakeMetadata -CommandLineFragment 'claude -p --dangerously-skip-permissions --allowed-tools Read,Glob,Grep --disallowed-tools Bash,Write,Edit'
$bp  = ConvertTo-ReviewProfileShape -Metadata $bad
$r   = Test-WaggleReviewSafeProfileViolations -EffectiveProfile $bp -ArgList ($bad.run_result.command_line -split '\s+')
Assert-True 'meta-fixture negative: --dangerously-skip-permissions in command_line rejected' (-not $r.ok)

# ----------------- summary -----------------

Write-Host ''
Write-Host "Result: $Script:Pass/$($Script:Pass + $Script:Fail) tests passed" -ForegroundColor Cyan
if ($Script:Fail -gt 0) { exit 1 } else { exit 0 }
