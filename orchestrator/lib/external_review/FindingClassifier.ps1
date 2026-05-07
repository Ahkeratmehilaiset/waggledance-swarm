# FindingClassifier.ps1
#
# Phase 2B-Revision (REL-014): classify each finding into one of
# four categories so the local loop can polish trivial / clear
# issues automatically while routing strategic / ambiguous /
# unsafe issues to external review.
#
# Classes:
#   TRIVIAL_AUTO_FIX           — fixability=trivial, <=2 files, <=medium
#   LOCAL_REPAIR               — fixability in {trivial, clear},
#                                <=3 files, severity in {low, medium, high},
#                                not (security AND ambiguous semantics)
#   EXTERNAL_REVIEW_REQUIRED   — fixability in {ambiguous, strategic} OR
#                                affected_files > 3 OR
#                                (severity = critical AND fixability != trivial) OR
#                                category in {architecture, security_semantics,
#                                lock_state_signal, concurrency, public_api} OR
#                                finding_signature reappeared after a verified fix
#   NEEDS_MANUAL_ACTION        — fixability=unsafe OR category in {login_required,
#                                captcha, real_secret_in_diff,
#                                destructive_filesystem_action, unknown_files_modified}

$ErrorActionPreference = 'Stop'

# Default keyword lists (overridable via config.finding_classifier).
$Script:FCDefaultUnsafeKeywords = @(
    'credential', 'secret', 'private_key', 'destructive',
    'lock corruption', 'state corruption', 'signal corruption',
    'rm -rf', 'git push --force', 'force-with-lease'
)
$Script:FCDefaultStrategicKeywords = @(
    'should we', 'consider redesigning', 'two valid approaches',
    'architectural direction', 'policy decision', 'tos',
    'compliance', 'ambiguous semantics'
)
$Script:FCDefaultForceExternalCategories = @(
    'architecture', 'security_semantics', 'lock_state_signal',
    'concurrency', 'public_api'
)
$Script:FCManualCategories = @(
    'login_required', 'captcha', 'real_secret_in_diff',
    'destructive_filesystem_action', 'unknown_files_modified'
)

function _Fc-FieldOr {
    param($Obj, [string] $Name, $Default)
    if ($null -eq $Obj) { return $Default }
    if ($Obj -is [pscustomobject]) {
        $p = $Obj.PSObject.Properties[$Name]
        if ($p -and ($null -ne $p.Value)) { return $p.Value }
        return $Default
    }
    if ($Obj -is [System.Collections.IDictionary]) {
        if ($Obj.Contains($Name)) {
            $v = $Obj[$Name]
            if ($null -eq $v) { return $Default }
            return $v
        }
        return $Default
    }
    return $Default
}

function _Fc-Lower {
    param($v)
    if ($null -eq $v) { return '' }
    return ([string]$v).ToLowerInvariant()
}

function Get-WaggleFindingClassifierConfig {
    [CmdletBinding()]
    param($Config)
    $cfg = _Fc-FieldOr -Obj $Config -Name 'finding_classifier' -Default $null
    return [pscustomobject]@{
        enabled                              = [bool] (_Fc-FieldOr -Obj $cfg -Name 'enabled' -Default $true)
        max_auto_repair_iterations_per_epoch = [int] (_Fc-FieldOr -Obj $cfg -Name 'max_auto_repair_iterations_per_epoch' -Default 3)
        max_files_for_trivial_auto_fix       = [int] (_Fc-FieldOr -Obj $cfg -Name 'max_files_for_trivial_auto_fix' -Default 2)
        max_files_for_local_repair           = [int] (_Fc-FieldOr -Obj $cfg -Name 'max_files_for_local_repair' -Default 3)
        unsafe_keywords                      = @(_Fc-FieldOr -Obj $cfg -Name 'unsafe_keywords' -Default $Script:FCDefaultUnsafeKeywords)
        strategic_keywords                   = @(_Fc-FieldOr -Obj $cfg -Name 'strategic_keywords' -Default $Script:FCDefaultStrategicKeywords)
        force_external_categories            = @(_Fc-FieldOr -Obj $cfg -Name 'force_external_categories' -Default $Script:FCDefaultForceExternalCategories)
    }
}

function Get-WaggleFindingFingerprint {
    <#
    .SYNOPSIS
    Produce a fingerprint that can match a finding across iterations
    for resurrection detection. Uses (finding_id, where, primary
    affected file).
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)] $Finding)
    $id = [string](_Fc-FieldOr -Obj $Finding -Name 'id' -Default '')
    $where = [string](_Fc-FieldOr -Obj $Finding -Name 'where' -Default '')
    $files = @(_Fc-FieldOr -Obj $Finding -Name 'affected_files' -Default @())
    $primary = if ($files.Count -gt 0) { [string]$files[0] } else { '' }
    $payload = ($id + '|' + $where + '|' + $primary)
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($payload)
        $hash = $sha.ComputeHash($bytes)
        return ([System.BitConverter]::ToString($hash) -replace '-','').ToLowerInvariant()
    } finally { $sha.Dispose() }
}

function Test-WaggleFindingResurrection {
    <#
    .SYNOPSIS
    Check whether the same finding fingerprint has been verified
    before in the regression ledger. If yes, the finding has
    "resurrected" — force EXTERNAL_REVIEW_REQUIRED regardless of
    fixability.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $FindingFingerprint,
        $RegressionLedger
    )
    if ($null -eq $RegressionLedger) { return $false }
    if (-not $RegressionLedger.PSObject.Properties['regressions']) { return $false }
    foreach ($reg in @($RegressionLedger.regressions)) {
        $sig = [string](_Fc-FieldOr -Obj $reg -Name 'issue_signature' -Default '')
        $status = [string](_Fc-FieldOr -Obj $reg -Name 'status' -Default '')
        if ($sig -eq $FindingFingerprint -and $status -eq 'verified') {
            return $true
        }
    }
    return $false
}

function Get-WaggleFixabilityHeuristic {
    <#
    .SYNOPSIS
    Decide a fixability value (trivial / clear / ambiguous /
    strategic / unsafe) for a finding. Heuristic uses:
      - number of affected_files
      - presence of `where` field with file:line
      - evidence text patterns (unsafe / strategic keyword match)
      - finding category against force_external_categories
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Finding,
        $ClassifierConfig = $null
    )
    $cfg = if ($ClassifierConfig) { $ClassifierConfig } else { Get-WaggleFindingClassifierConfig -Config $null }

    $hayParts = @(
        [string](_Fc-FieldOr -Obj $Finding -Name 'title' -Default ''),
        [string](_Fc-FieldOr -Obj $Finding -Name 'evidence' -Default ''),
        [string](_Fc-FieldOr -Obj $Finding -Name 'why_it_matters' -Default ''),
        [string](_Fc-FieldOr -Obj $Finding -Name 'recommended_action' -Default '')
    )
    $hay = ($hayParts -join ' ')
    $hayLower = $hay.ToLowerInvariant()

    foreach ($u in $cfg.unsafe_keywords) {
        if ([string]::IsNullOrWhiteSpace($u)) { continue }
        if ($hayLower.Contains((_Fc-Lower $u))) { return 'unsafe' }
    }
    $cat = _Fc-Lower (_Fc-FieldOr -Obj $Finding -Name 'category' -Default '')
    if ($cfg.force_external_categories -contains $cat) {
        # category alone forces EXTERNAL via classifier; the
        # fixability we return here is "strategic" so the matrix
        # records why.
        return 'strategic'
    }
    foreach ($s in $cfg.strategic_keywords) {
        if ([string]::IsNullOrWhiteSpace($s)) { continue }
        if ($hayLower.Contains((_Fc-Lower $s))) { return 'strategic' }
    }

    $files = @(_Fc-FieldOr -Obj $Finding -Name 'affected_files' -Default @())
    $where = [string](_Fc-FieldOr -Obj $Finding -Name 'where' -Default '')
    $hasFileLine = ($where -match ':\d+\b')

    $clearSignals = $false
    # Phase 2B-R1 P6 (CLF-BUG-001): loosen the "actual" boundary so
    # real-world evidence text like "expected key: foo_count actual: fooCount"
    # is recognized as a clear signal. Prior regex required \s+actual\s+
    # which missed punctuation-prefixed forms (actual:, actual;).
    if ($hayLower -match 'expected\s+\S+\s+but\s+got|expected\s+.{1,80}[\s:;,]actual[\s:;,]|missing field|missing property|typo|off[-\s]?by[-\s]?one|missing brace|parse error|fence-length|reg(ex|ular expression) tightening') {
        $clearSignals = $true
    }
    # Phase 2B-R2 P5b (REL-019): the operator confirmed REL-019 was a
    # 1-line shape-unification fix that the classifier had over-routed
    # to EXTERNAL_REVIEW_REQUIRED. The bug's evidence carried explicit
    # strict-mode missing-property signatures
    # ("the property 'X' cannot be found on this object",
    # "pscustomobject without 'role'", "PropertyNotFoundStrict"),
    # plus the recommended_action was a single shape-unification
    # ("add 'role' to the DryRun return object"). Recognize these as
    # clear signals so future analogous null-guard / shape-fix
    # findings route LOCAL_REPAIR.
    if ($hayLower -match "the property\s+['""]?[a-z0-9_\.\$]+['""]?\s+cannot be found|propertynotfoundstrict|null[-\s]?guard|null guard|shape[-\s]?unification|pscustomobject\s+(?:without|lacks|missing)\s|return\s+(?:object|shape)\s+(?:lacks|missing|without)") {
        $clearSignals = $true
    }
    if ($files.Count -eq 1 -and $hasFileLine -and $clearSignals) { return 'trivial' }
    if (($files.Count -le 1 -and $hasFileLine) -or $clearSignals) { return 'clear' }
    if ($files.Count -gt 3) { return 'strategic' }

    # Default: ambiguous. The classifier will route to EXTERNAL.
    return 'ambiguous'
}

function Get-WaggleFindingClass {
    <#
    .SYNOPSIS
    Decide one of TRIVIAL_AUTO_FIX / LOCAL_REPAIR /
    EXTERNAL_REVIEW_REQUIRED / NEEDS_MANUAL_ACTION for a finding.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Finding,
        $ClassifierConfig = $null,
        $RegressionLedger = $null,
        [string] $Severity = '',
        [string] $Fixability = ''
    )
    $cfg = if ($ClassifierConfig) { $ClassifierConfig } else { Get-WaggleFindingClassifierConfig -Config $null }
    if (-not $Fixability) {
        $Fixability = Get-WaggleFixabilityHeuristic -Finding $Finding -ClassifierConfig $cfg
    }
    if (-not $Severity) {
        $Severity = [string](_Fc-FieldOr -Obj $Finding -Name 'severity' -Default 'medium')
    }
    $catLower = _Fc-Lower (_Fc-FieldOr -Obj $Finding -Name 'category' -Default '')
    $files = @(_Fc-FieldOr -Obj $Finding -Name 'affected_files' -Default @())
    $fileCount = $files.Count

    # NEEDS_MANUAL_ACTION (highest priority)
    if ($Fixability -eq 'unsafe' -or ($Script:FCManualCategories -contains $catLower)) {
        return [pscustomobject]@{ class = 'NEEDS_MANUAL_ACTION'; fixability = $Fixability; severity = $Severity; reason = 'unsafe_or_manual_category' }
    }

    # Resurrection check: if the finding fingerprint has been verified
    # before, it is suspect and goes EXTERNAL regardless of fixability.
    $fingerprint = Get-WaggleFindingFingerprint -Finding $Finding
    if (Test-WaggleFindingResurrection -FindingFingerprint $fingerprint -RegressionLedger $RegressionLedger) {
        return [pscustomobject]@{ class = 'EXTERNAL_REVIEW_REQUIRED'; fixability = $Fixability; severity = $Severity; reason = 'resurrection_after_verified' }
    }

    # EXTERNAL_REVIEW_REQUIRED conditions
    if ($Fixability -in @('ambiguous', 'strategic')) {
        return [pscustomobject]@{ class = 'EXTERNAL_REVIEW_REQUIRED'; fixability = $Fixability; severity = $Severity; reason = 'fixability_' + $Fixability }
    }
    if ($fileCount -gt $cfg.max_files_for_local_repair) {
        return [pscustomobject]@{ class = 'EXTERNAL_REVIEW_REQUIRED'; fixability = $Fixability; severity = $Severity; reason = 'affected_files_gt_' + $cfg.max_files_for_local_repair }
    }
    if ($Severity -eq 'critical' -and $Fixability -ne 'trivial') {
        return [pscustomobject]@{ class = 'EXTERNAL_REVIEW_REQUIRED'; fixability = $Fixability; severity = $Severity; reason = 'critical_non_trivial' }
    }
    if ($cfg.force_external_categories -contains $catLower) {
        return [pscustomobject]@{ class = 'EXTERNAL_REVIEW_REQUIRED'; fixability = $Fixability; severity = $Severity; reason = 'category_forces_external:' + $catLower }
    }

    # TRIVIAL_AUTO_FIX
    if ($Fixability -eq 'trivial' -and $fileCount -le $cfg.max_files_for_trivial_auto_fix -and (@('info','low','medium','critical') -contains $Severity)) {
        # Note: critical+trivial is allowed (e.g. typo in a critical
        # config). The TRIVIAL path is fine — the verification gate
        # catches misclassification.
        return [pscustomobject]@{ class = 'TRIVIAL_AUTO_FIX'; fixability = $Fixability; severity = $Severity; reason = 'trivial_with_small_scope' }
    }

    # LOCAL_REPAIR
    if (($Fixability -in @('trivial', 'clear')) -and $fileCount -le $cfg.max_files_for_local_repair -and (@('info','low','medium','high') -contains $Severity)) {
        # security + ambiguous semantics are excluded by the earlier
        # branches; here we just need to make sure security findings
        # with ambiguous fixability didn't slip through.
        return [pscustomobject]@{ class = 'LOCAL_REPAIR'; fixability = $Fixability; severity = $Severity; reason = 'clear_or_trivial_local_scope' }
    }

    # Fallback: route to EXTERNAL. The classifier is conservative.
    return [pscustomobject]@{ class = 'EXTERNAL_REVIEW_REQUIRED'; fixability = $Fixability; severity = $Severity; reason = 'fallback_conservative' }
}
