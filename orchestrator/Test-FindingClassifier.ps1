#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B-Revision (REL-014) tests for
    orchestrator/lib/external_review/FindingClassifier.ps1 +
    orchestrator/Build-WaggleAutoRepairPrompt.ps1.

    Goal: at least 20 cases covering all four classes
    (TRIVIAL_AUTO_FIX, LOCAL_REPAIR, EXTERNAL_REVIEW_REQUIRED,
    NEEDS_MANUAL_ACTION), the fixability heuristic, and
    resurrection-detection.
#>
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/external_review/FindingClassifier.ps1')
. (Join-Path $PSScriptRoot 'Build-WaggleAutoRepairPrompt.ps1')

$Script:Pass = 0; $Script:Fail = 0
function Assert-True {
    param([string] $Name, [bool] $Cond, [string] $Detail = '')
    if ($Cond) { Write-Host "PASS  $Name" -ForegroundColor Green; $Script:Pass++ }
    else        { Write-Host "FAIL  $Name $Detail" -ForegroundColor Red; $Script:Fail++ }
}

$cfg = Get-WaggleFindingClassifierConfig -Config $null

function New-Finding {
    param(
        [string] $Id = 'F-001',
        [string] $Severity = 'medium',
        [string] $Title = 't',
        [string] $Where = 'foo.ps1:42',
        [string] $Evidence = 'evidence',
        [string] $WhyItMatters = 'why',
        [string] $RecommendedAction = 'do x',
        [string] $Category = '',
        [string[]] $AffectedFiles = @('foo.ps1')
    )
    $r = [ordered]@{
        id = $Id; severity = $Severity; title = $Title; where = $Where
        evidence = $Evidence; why_it_matters = $WhyItMatters
        recommended_action = $RecommendedAction; affected_files = $AffectedFiles
    }
    if ($Category) { $r['category'] = $Category }
    return [pscustomobject]$r
}

# 1. trivial: missing JSON property ---------------------------------------

$f1 = New-Finding -Severity 'low' -Title 'JSON property typo' -Evidence 'expected field name X but got Y' -Where 'orchestrator/Build-Foo.ps1:120' -AffectedFiles @('orchestrator/Build-Foo.ps1')
$r = Get-WaggleFindingClass -Finding $f1 -ClassifierConfig $cfg
Assert-True 'C1: JSON property typo -> TRIVIAL_AUTO_FIX' ($r.class -eq 'TRIVIAL_AUTO_FIX')
Assert-True 'C1: fixability=trivial' ($r.fixability -eq 'trivial')

# 2. trivial: PowerShell parse error from missing brace -------------------

$f2 = New-Finding -Severity 'low' -Title 'missing brace parse error' -Evidence 'parse error: missing closing brace at line 87' -Where 'orchestrator/Foo.ps1:87' -AffectedFiles @('orchestrator/Foo.ps1')
$r = Get-WaggleFindingClass -Finding $f2 -ClassifierConfig $cfg
Assert-True 'C2: missing-brace parse error -> TRIVIAL_AUTO_FIX' ($r.class -eq 'TRIVIAL_AUTO_FIX')

# 3. clear single-test failure -> LOCAL_REPAIR ----------------------------

$f3 = New-Finding -Severity 'medium' -Title 'single test fails' -Evidence 'expected 5 actual 6' -Where 'tests/test_foo.py:30' -AffectedFiles @('tests/test_foo.py','src/foo.py','src/bar.py')
$r = Get-WaggleFindingClass -Finding $f3 -ClassifierConfig $cfg
Assert-True 'C3: clear single-test failure -> LOCAL_REPAIR' ($r.class -eq 'LOCAL_REPAIR')

# 4. single redaction false positive -> LOCAL_REPAIR ----------------------

$f4 = New-Finding -Severity 'medium' -Title 'redaction false positive' -Evidence 'regex tightening needed; expected SHA preserved actual REDACTED' -Where 'orchestrator/lib/Redactor.ps1:50' -AffectedFiles @('orchestrator/lib/Redactor.ps1')
$r = Get-WaggleFindingClass -Finding $f4 -ClassifierConfig $cfg
Assert-True 'C4: redaction false positive -> LOCAL_REPAIR' (@('LOCAL_REPAIR','TRIVIAL_AUTO_FIX') -contains $r.class)

# 5. 5 affected_files -> EXTERNAL ----------------------------------------

$f5 = New-Finding -Severity 'medium' -AffectedFiles @('a','b','c','d','e','f')
$r = Get-WaggleFindingClass -Finding $f5 -ClassifierConfig $cfg
Assert-True 'C5: 6 affected_files -> EXTERNAL' ($r.class -eq 'EXTERNAL_REVIEW_REQUIRED')

# 6. architecture category -> EXTERNAL -----------------------------------

$f6 = New-Finding -Severity 'medium' -Category 'architecture' -Title 'module boundary violation' -Where 'src/' -AffectedFiles @('src/Foo.ps1')
$r = Get-WaggleFindingClass -Finding $f6 -ClassifierConfig $cfg
Assert-True 'C6: architecture category -> EXTERNAL' ($r.class -eq 'EXTERNAL_REVIEW_REQUIRED')

# 7. ambiguous semantics -> EXTERNAL -------------------------------------

$f7 = New-Finding -Severity 'medium' -Title 'ambiguous semantics in lock release' -Evidence 'Should we hold the lock during cleanup, or release first? two valid approaches.' -Where 'lib/Lockfile.ps1:120'
$r = Get-WaggleFindingClass -Finding $f7 -ClassifierConfig $cfg
Assert-True 'C7: strategic keyword ("two valid approaches") -> EXTERNAL' ($r.class -eq 'EXTERNAL_REVIEW_REQUIRED')

# 8. login required -> NEEDS_MANUAL_ACTION -------------------------------

$f8 = New-Finding -Severity 'high' -Category 'login_required' -Title 'login expired'
$r = Get-WaggleFindingClass -Finding $f8 -ClassifierConfig $cfg
Assert-True 'C8: login_required category -> NEEDS_MANUAL_ACTION' ($r.class -eq 'NEEDS_MANUAL_ACTION')

# 9. real_secret_in_diff -> NEEDS_MANUAL_ACTION --------------------------

$f9 = New-Finding -Category 'real_secret_in_diff' -Title 'leaked credential in diff'
$r = Get-WaggleFindingClass -Finding $f9 -ClassifierConfig $cfg
Assert-True 'C9: real_secret_in_diff -> NEEDS_MANUAL_ACTION' ($r.class -eq 'NEEDS_MANUAL_ACTION')

# 10. unsafe keyword (credential) -> NEEDS_MANUAL_ACTION -----------------

$f10 = New-Finding -Severity 'high' -Title 'destructive operation in CI' -Evidence 'rm -rf used unconditionally; destructive filesystem action'
$r = Get-WaggleFindingClass -Finding $f10 -ClassifierConfig $cfg
Assert-True 'C10: unsafe keyword (destructive) -> NEEDS_MANUAL_ACTION' ($r.class -eq 'NEEDS_MANUAL_ACTION')

# 11. resurrection after verified -> EXTERNAL ----------------------------

$f11 = New-Finding -Id 'ARC-021' -Severity 'low' -Title 'JSON property typo' -Evidence 'expected name X actual Y' -Where 'orchestrator/Foo.ps1:120' -AffectedFiles @('orchestrator/Foo.ps1')
$fingerprint = Get-WaggleFindingFingerprint -Finding $f11
$ledger = [pscustomobject]@{
    regressions = @( [pscustomobject]@{ id='REG-X'; status='verified'; issue_signature=$fingerprint } )
}
$r = Get-WaggleFindingClass -Finding $f11 -ClassifierConfig $cfg -RegressionLedger $ledger
Assert-True 'C11: resurrection -> EXTERNAL even if fixability=trivial' ($r.class -eq 'EXTERNAL_REVIEW_REQUIRED')
Assert-True 'C11: reason cites resurrection_after_verified' ($r.reason -eq 'resurrection_after_verified')

# 12. critical severity, fixability=trivial (typo in critical config) -> TRIVIAL_AUTO_FIX

$f12 = New-Finding -Severity 'critical' -Title 'JSON property typo in critical config' -Evidence 'expected key allowed_tools but got allowedTools' -Where 'orchestrator.config.example.json:30' -AffectedFiles @('orchestrator.config.example.json')
$r = Get-WaggleFindingClass -Finding $f12 -ClassifierConfig $cfg
Assert-True 'C12: critical+trivial -> TRIVIAL_AUTO_FIX' ($r.class -eq 'TRIVIAL_AUTO_FIX')

# 13. critical severity, fixability=ambiguous -> EXTERNAL ----------------

$f13 = New-Finding -Severity 'critical' -Title 'ambiguous semantics in critical path' -Evidence 'should we change the API? compliance angle.'
$r = Get-WaggleFindingClass -Finding $f13 -ClassifierConfig $cfg
Assert-True 'C13: critical+ambiguous -> EXTERNAL' ($r.class -eq 'EXTERNAL_REVIEW_REQUIRED')

# 14. fixability heuristic returns trivial for "expected/actual" + 1 file
$ff = Get-WaggleFixabilityHeuristic -Finding (New-Finding -Severity 'low' -Where 'src/x.py:10' -Evidence 'expected 5 actual 6' -AffectedFiles @('src/x.py')) -ClassifierConfig $cfg
Assert-True 'C14: fixability heuristic = trivial' ($ff -eq 'trivial')

# 15. heuristic returns strategic when category forces external
$ff = Get-WaggleFixabilityHeuristic -Finding (New-Finding -Category 'concurrency' -Severity 'high') -ClassifierConfig $cfg
Assert-True 'C15: heuristic = strategic for concurrency cat' ($ff -eq 'strategic')

# 16. heuristic returns unsafe on "credential" keyword
$ff = Get-WaggleFixabilityHeuristic -Finding (New-Finding -Severity 'high' -Evidence 'leaked credential found') -ClassifierConfig $cfg
Assert-True 'C16: heuristic = unsafe for credential keyword' ($ff -eq 'unsafe')

# 17. heuristic returns ambiguous as fallback
$ff = Get-WaggleFixabilityHeuristic -Finding (New-Finding -Severity 'medium' -AffectedFiles @() -Where '' -Evidence 'general improvement') -ClassifierConfig $cfg
Assert-True 'C17: heuristic = ambiguous fallback' ($ff -eq 'ambiguous')

# Phase 2B-R1 (CLF-BUG-001): heuristic must accept punctuation-prefixed
# 'actual' (e.g. "expected X: a actual: b").
$ffPunct = Get-WaggleFixabilityHeuristic -Finding (New-Finding -Severity 'medium' -Where 'schemas/foo.schema.json:30' -Evidence 'expected key: foo_count actual: fooCount' -AffectedFiles @('schemas/foo.schema.json')) -ClassifierConfig $cfg
Assert-True 'C17b: heuristic = clear when actual: with colon' ($ffPunct -eq 'trivial' -or $ffPunct -eq 'clear')
$clsPunct = Get-WaggleFindingClass -Finding (New-Finding -Severity 'medium' -Where 'schemas/foo.schema.json:30' -Evidence 'expected key: foo_count actual: fooCount' -AffectedFiles @('schemas/foo.schema.json')) -ClassifierConfig $cfg
Assert-True 'C17c: schema-mismatch with actual: -> LOCAL_REPAIR or TRIVIAL_AUTO_FIX' (@('LOCAL_REPAIR','TRIVIAL_AUTO_FIX') -contains $clsPunct.class)

# Phase 2B-R2 (REL-019 / P5b): single shape-unification fix patterns
# must route LOCAL_REPAIR or better. Recognises strict-mode missing
# property errors and pscustomobject shape gaps as clear signals.
$relStrict = Get-WaggleFixabilityHeuristic -Finding (New-Finding -Severity 'medium' -Where 'orchestrator/Invoke-WaggleReview.ps1:577' -Evidence "the property 'role' cannot be found on this object. The DryRun pscustomobject lacks role; the CLI Write-Host accesses `$r.role under StrictMode and exits 1." -RecommendedAction 'Add role to the DryRun return object so it carries the same identity fields as the non-DryRun branch.' -AffectedFiles @('orchestrator/Invoke-WaggleReview.ps1')) -ClassifierConfig $cfg
Assert-True 'C17d: heuristic = clear when strict-mode missing property error present' ($relStrict -eq 'trivial' -or $relStrict -eq 'clear')
$relCls = Get-WaggleFindingClass -Finding (New-Finding -Severity 'medium' -Where 'orchestrator/Invoke-WaggleReview.ps1:577' -Evidence "the property 'role' cannot be found on this object. The DryRun pscustomobject without role; PropertyNotFoundStrict thrown" -RecommendedAction 'Add role to the DryRun return object.' -AffectedFiles @('orchestrator/Invoke-WaggleReview.ps1')) -ClassifierConfig $cfg
Assert-True 'C17e: REL-019 strict-mode shape fix -> LOCAL_REPAIR or TRIVIAL_AUTO_FIX' (@('LOCAL_REPAIR','TRIVIAL_AUTO_FIX') -contains $relCls.class)
$nullGuard = Get-WaggleFixabilityHeuristic -Finding (New-Finding -Severity 'low' -Where 'orchestrator/Foo.ps1:42' -Evidence 'add null-guard before dereference' -RecommendedAction 'wrap in null guard') -ClassifierConfig $cfg
Assert-True 'C17f: heuristic = clear when null-guard signal present' ($nullGuard -eq 'trivial' -or $nullGuard -eq 'clear')

# 18-19. repair prompt builder fills placeholders -------------------------

$tmp = Join-Path $env:TEMP ("waggle-test-fc-{0}" -f ([guid]::NewGuid().ToString('N')))
[void](New-Item -ItemType Directory -Path $tmp -Force)
$cfgFile = Join-Path $tmp 'orchestrator.config.json'
@{ projectRoot = $tmp; finding_classifier = @{ enabled = $true } } | ConvertTo-Json -Depth 5 | Set-Content -Path $cfgFile -Encoding UTF8
$promptOut = Join-Path $tmp 'repair_prompt.md'
$rb = Build-WaggleAutoRepairPrompt -ConfigPath $cfgFile -FindingId 'ARC-099' -EpochId 'e' -IterationId 'iter-x' `
        -RepairClass 'LOCAL_REPAIR' -Severity 'medium' -Fixability 'clear' `
        -Title 'fix off-by-one' -Where 'orchestrator/Foo.ps1:50' -Evidence 'expected 5 actual 6' `
        -RepairAttemptIndex 1 -TargetTestFile 'Foo' -OutputPromptPath $promptOut
Assert-True 'C18: repair prompt file written' (Test-Path -LiteralPath $promptOut)
$body = Get-Content -Raw -Path $promptOut -Encoding UTF8

# 19. all 11 hard rules present
$rulesPresent = $true
for ($i = 1; $i -le 11; $i++) {
    if ($body -notmatch ('(?m)^' + $i + '\.')) { $rulesPresent = $false; break }
}
Assert-True 'C19: repair prompt contains all 11 hard rules' $rulesPresent

# 20. scope limit clause present
Assert-True 'C20: repair prompt has SCOPE LIMIT clause' ($body -match 'SCOPE LIMIT')
Assert-True 'C20b: repair prompt names the finding ID' ($body -match 'ARC-099')
Assert-True 'C20c: repair prompt names the title' ($body -match 'fix off-by-one')

# 21. repair iteration that emits repair_escalated.txt -> classifier
# would route to EXTERNAL on next decision. (We test only that the
# prompt explicitly instructs writing repair_escalated.txt.)
Assert-True 'C21: prompt instructs repair_escalated.txt on overflow' ($body -match 'repair_escalated\.txt')

# 22. verification iteration prefix from P7 (cross-link via P7B test)
. (Join-Path $PSScriptRoot 'lib/external_review/EpochCycleTrigger.ps1')
$prefix = Get-WaggleVerificationIterationPrefix -IssueIds @('ARC-099')
Assert-True 'C22: verification prefix references issue id' ($prefix -match 'ARC-099')

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmp

Write-Host ''
Write-Host "Result: $Script:Pass/$($Script:Pass + $Script:Fail) tests passed" -ForegroundColor Cyan
if ($Script:Fail -gt 0) { exit 1 } else { exit 0 }
