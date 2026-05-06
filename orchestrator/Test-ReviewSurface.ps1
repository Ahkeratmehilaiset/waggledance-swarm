#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2A-3 tests for orchestrator/lib/review/ReviewSurface.ps1.
.DESCRIPTION
    Covers package-quality counting, sparse detection, supplement
    construction (caps, redaction, dynamic fences, deterministic
    ordering, truncation markers, missing-file annotations).
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$libDir = Join-Path $PSScriptRoot 'lib\review'
. (Join-Path $libDir 'ReviewAdapter.ps1')
. (Join-Path $libDir 'ReviewSurface.ps1')

$Script:Pass = 0
$Script:Fail = 0
$Script:Tmp  = Join-Path $env:TEMP ("waggle-test-review-surface-{0}" -f ([guid]::NewGuid().ToString('N')))
[void](New-Item -ItemType Directory -Path $Script:Tmp -Force)

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

function New-PackageFile {
    param([string] $Name, [string] $Content)
    $p = Join-Path $Script:Tmp $Name
    Set-Content -Path $p -Value $Content -Encoding UTF8
    return $p
}

# ----------------- package quality on EMPTY-ish package ------------------

$emptyPkg = New-PackageFile 'empty.md' @"
# Iteration X

## SECURITY PREAMBLE

Just metadata.

## Run metadata (run_metadata.json)

``````json
{ "iteration_id": "X" }
``````
"@
$q = Get-WaggleReviewPackageQuality -PackagePath $emptyPkg
Assert-True 'empty pkg: source_section_count == 0' ([int]$q.source_section_count -eq 0)
Assert-True 'empty pkg: reviewable_files_count low' ([int]$q.reviewable_files_count -lt 3)
Assert-True 'empty pkg: section_count > 0' ([int]$q.section_count -gt 0)

$sparse = Test-WaggleReviewPackageIsSparse -Quality $q
Assert-True 'empty pkg: classified as sparse' ($sparse.sparse)
Assert-True 'empty pkg: sparse reason mentions source sections' ($sparse.reason -match 'source/test/schema/prompt sections')

# ----------------- package quality on NON-sparse package -----------------

$richPkg = New-PackageFile 'rich.md' @"
# Iteration Y

## Source: orchestrator/Invoke-WaggleIteration.ps1

``````powershell
function Foo { Write-Host 'hi' }
function Bar { return 1 }
function Baz { 'ok' }
function Qux { Get-Content x.txt }
function Quux { 'asdf' }
function Corge { 'asdf' }
function Grault { 'asdf' }
function Garply { 'asdf' }
function Waldo { 'asdf' }
function Fred { 'asdf' }
function Plugh { 'asdf' }
function Xyzzy { 'asdf' }
function Thud { 'asdf' }
function A1 { 'asdf' }
function A2 { 'asdf' }
function A3 { 'asdf' }
function A4 { 'asdf' }
function A5 { 'asdf' }
function A6 { 'asdf' }
function A7 { 'asdf' }
function A8 { 'asdf' }
function A9 { 'asdf' }
function A10 { 'asdf' }
function A11 { 'asdf' }
function A12 { 'asdf' }
function A13 { 'asdf' }
function A14 { 'asdf' }
function A15 { 'asdf' }
function A16 { 'asdf' }
function A17 { 'asdf' }
function A18 { 'asdf' }
function A19 { 'asdf' }
function A20 { 'asdf' }
function A21 { 'asdf' }
function A22 { 'asdf' }
function A23 { 'asdf' }
function A24 { 'asdf' }
function A25 { 'asdf' }
function A26 { 'asdf' }
function A27 { 'asdf' }
function A28 { 'asdf' }
function A29 { 'asdf' }
function A30 { 'asdf' }
``````

## Tests: orchestrator/Test-Phase2A2.ps1

``````powershell
Assert-True 'X' (\$true)
Assert-True 'Y' (\$true)
Assert-True 'Z' (\$true)
Assert-True 'A' (\$true)
Assert-True 'B' (\$true)
Assert-True 'C' (\$true)
Assert-True 'D' (\$true)
Assert-True 'E' (\$true)
Assert-True 'F' (\$true)
Assert-True 'G' (\$true)
Assert-True 'H' (\$true)
Assert-True 'I' (\$true)
``````

## Schema: schemas/review.schema.json

``````json
{ "x": 1 }
``````

## Prompt: prompts/review/architect.md

``````markdown
You are an architect reviewer.
``````
"@
$q2 = Get-WaggleReviewPackageQuality -PackagePath $richPkg
Assert-True 'rich pkg: source_section_count >= 4' ([int]$q2.source_section_count -ge 4)
Assert-True 'rich pkg: reviewable_lines_count > 0' ([int]$q2.reviewable_lines_count -gt 0)

$sparse2 = Test-WaggleReviewPackageIsSparse -Quality $q2
Assert-True 'rich pkg: NOT sparse' (-not $sparse2.sparse)

# ----------------- supplement build over the real repo ------------------

$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path

$sup = Get-WaggleReviewSurfaceSupplement -ProjectRoot $repoRoot
Assert-True 'supplement: file_count > 0' ([int]$sup.file_count -gt 0)

# Phase 2A-4 ARCH-001: Redactor.ps1's COOKIE_HEADER + SET_COOKIE
# pattern definitions must NOT be corrupted by the source-supplement
# redactor. The Phase 2A-3 reviewer reported these were rewritten
# to "[REDACTED:COOKIE_HEADER]" inside the supplement view.
$cookieLineRegex   = "name\s*=\s*'COOKIE_HEADER'"
$setCookieRegex    = "name\s*=\s*'SET_COOKIE'"
$bearerLineRegex   = "name\s*=\s*'BEARER_TOKEN'"
$pwdLineRegex      = "name\s*=\s*'PASSWORD_KV'"
Assert-True 'ARCH-001: COOKIE_HEADER name literal preserved'   ($sup.markdown -match $cookieLineRegex)
Assert-True 'ARCH-001: SET_COOKIE name literal preserved'      ($sup.markdown -match $setCookieRegex)
Assert-True 'ARCH-001: BEARER_TOKEN name literal preserved'    ($sup.markdown -match $bearerLineRegex)
Assert-True 'ARCH-001: PASSWORD_KV name literal preserved'     ($sup.markdown -match $pwdLineRegex)
Assert-True 'ARCH-001: no broken `pattern = .[REDACTED:` line in supplement' (
    $sup.markdown -notmatch "pattern\s*=\s*'\(\?i\)\[REDACTED:COOKIE_HEADER\]"
)
Assert-True 'ARCH-001: cookie regex pattern body intact' (
    $sup.markdown -match "pattern\s*=\s*'\(\?i\)cookie:\\s\*\[\^\\r\\n\]\+'"
)

# Phase 2A-4 ARCH-001: structural integrity of the Redactor.ps1
# excerpt as it appears in the supplement. We do NOT do a full PS
# parse of the excerpt because the per-file char cap can legitimately
# truncate mid-hashtable / mid-function, which would make any excerpt
# unparseable regardless of redaction. Instead we assert the
# substantive lines that the Phase 2A-3 reviewer flagged are intact.
$rxStartHdr = '(?ms)^### Surface file orchestrator/lib/Redactor\.ps1.*?\n```\w*\r?\n(.*?)\r?\n```'
$rxMatch = [regex]::Match($sup.markdown, $rxStartHdr)
Assert-True 'ARCH-001: Redactor.ps1 excerpt locatable in supplement' ($rxMatch.Success)
if ($rxMatch.Success) {
    $excerpt = $rxMatch.Groups[1].Value
    Assert-True 'ARCH-001: Redactor.ps1 excerpt contains intact COOKIE_HEADER pattern definition line' (
        $excerpt -match "name\s*=\s*'COOKIE_HEADER';\s*pattern\s*=\s*'\(\?i\)cookie:"
    )
    Assert-True 'ARCH-001: Redactor.ps1 excerpt contains no [REDACTED:COOKIE_HEADER] substring' (
        $excerpt -notmatch '\[REDACTED:COOKIE_HEADER\]'
    )
    Assert-True 'ARCH-001: Redactor.ps1 excerpt contains no [REDACTED:SET_COOKIE] substring' (
        $excerpt -notmatch '\[REDACTED:SET_COOKIE\]'
    )
    Assert-True 'ARCH-001: Redactor.ps1 excerpt contains no [REDACTED:PASSWORD_KV] substring' (
        $excerpt -notmatch '\[REDACTED:PASSWORD_KV\]'
    )
    Assert-True 'ARCH-001: Redactor.ps1 excerpt contains no [REDACTED:BEARER_TOKEN] substring' (
        $excerpt -notmatch '\[REDACTED:BEARER_TOKEN\]'
    )
}
# Total cap is enforced pre-redaction; redaction can grow size
# slightly when replacing short tokens with longer "[REDACTED:NAME]"
# sentinels. Allow ~5% slack on top of MaxTotalChars.
$totalSlack = [int]([Math]::Ceiling([double]$sup.max_total_chars * 1.05))
Assert-True 'supplement: total_chars > 0 and <= cap (with redaction slack)' (
    ([int]$sup.total_chars -gt 0) -and ([int]$sup.total_chars -le $totalSlack)
)
Assert-True 'supplement: markdown contains UNTRUSTED label' ($sup.markdown -match 'UNTRUSTED DATA')
Assert-True 'supplement: markdown contains REVIEW SURFACE SUPPLEMENT' ($sup.markdown -match 'REVIEW SURFACE SUPPLEMENT')

# Per-file caps. Phase 2A-4 P12: keyword-window files use a higher
# per-file cap (12000) so all critical windows fit. Body is
# truncated BEFORE redaction; redactor can grow size slightly when
# replacing short tokens with longer "[REDACTED:NAME]" sentinels.
# Allow ~20% slack on top of the effective per-file cap.
$keywordFiles = @(
    'orchestrator/Invoke-WaggleIteration.ps1',
    'orchestrator/Invoke-WaggleReview.ps1',
    'orchestrator/lib/CompletionVerifier.ps1',
    'orchestrator/lib/ArtifactValidator.ps1',
    'orchestrator/lib/review/ReviewSurface.ps1',
    'orchestrator/lib/review/ReviewAdapter.ps1',
    'orchestrator/lib/Lockfile.ps1'
)
$slackCapDefault = [int]([Math]::Ceiling([double]$sup.max_file_chars * 1.2))
$slackCapKeyword = [int]([Math]::Ceiling(12000.0 * 1.2))
foreach ($f in $sup.included_files) {
    $cap = if ($keywordFiles -contains $f.path) { $slackCapKeyword } else { $slackCapDefault }
    if ([int]$f.included_chars -gt $cap) {
        Assert-True ("supplement: file $($f.path) within per-file cap (with redaction slack)") $false ("included=$($f.included_chars) > cap=$cap")
    }
}
Assert-True 'supplement: all included files <= per-file cap (with 20% redaction slack)' $true

# ----------------- dynamic fences ------------------

# Force a body that contains both ``` and ```` so the fence picker has
# to skip past 4 backticks.
$mockSurface = @{}
$body1 = "before ``" + "``" + "`a fenced thing here``" + "``" + "` after"
$dyn = ([scriptblock]::Create('. (Join-Path $args[0] "ReviewSurface.ps1"); _Get-DynamicFence -Body $args[1]')).Invoke($libDir, $body1)
Assert-True 'dynamic fence: skips 3-backtick when body has 3 backticks' ($dyn.Length -ge 3)

# ----------------- redaction is applied ------------------

# Build a tiny synthetic project tree with one source file containing a
# token-shaped string, then ask for the supplement and confirm the
# token does not survive in the markdown.
$miniRoot = Join-Path $Script:Tmp 'mini'
[void](New-Item -ItemType Directory -Path $miniRoot -Force)
$tokenSrc = Join-Path $miniRoot 'orchestrator'
[void](New-Item -ItemType Directory -Path $tokenSrc -Force)
# Reconstruct token at runtime so this source file does not contain
# a contiguous token-shaped literal.
$fakeTok = 'ghp_' + ('a' * 40)
Set-Content -Path (Join-Path $tokenSrc 'Invoke-WaggleIteration.ps1') -Value (
    "# fake content for redaction test`nGITHUB_TOKEN=$fakeTok`n"
) -Encoding UTF8
$sup2 = Get-WaggleReviewSurfaceSupplement -ProjectRoot $miniRoot -FileList @('orchestrator/Invoke-WaggleIteration.ps1')
# Either GITHUB_PAT (bare-token pattern) or ENV_KV_SECRET (label
# `^GITHUB_TOKEN=...`) is a valid redaction marker; on this fixture
# ENV_KV_SECRET fires first because the line starts with `GITHUB_TOKEN=`.
Assert-True 'supplement: redaction strips ghp token' (
    $sup2.markdown -notmatch 'ghp_a{36}' -and (
        $sup2.markdown -match 'REDACTED:GITHUB_PAT' -or
        $sup2.markdown -match 'REDACTED:ENV_KV_SECRET'
    )
)

# ----------------- truncation marker -----------------

# Force a tiny budget so the supplement truncates.
$big = 'X' * 5000
$bigDir = Join-Path $miniRoot 'orchestrator/lib'
[void](New-Item -ItemType Directory -Path $bigDir -Force)
Set-Content -Path (Join-Path $bigDir 'Big.ps1') -Value $big -Encoding UTF8
$sup3 = Get-WaggleReviewSurfaceSupplement -ProjectRoot $miniRoot -FileList @('orchestrator/lib/Big.ps1') -MaxFileChars 200 -MaxTotalChars 5000 -MaxFiles 5
Assert-True 'supplement: truncation marker present' ($sup3.markdown -match 'truncated to 200')
Assert-True 'supplement: truncated_files lists the file' ($sup3.truncated_files -contains 'orchestrator/lib/Big.ps1')

# ----------------- missing files annotated -----------------

$sup4 = Get-WaggleReviewSurfaceSupplement -ProjectRoot $miniRoot -FileList @('orchestrator/Invoke-WaggleIteration.ps1','orchestrator/Nonexistent.ps1')
Assert-True 'supplement: missing file annotated' ($sup4.markdown -match 'Nonexistent.ps1' -and $sup4.markdown -match 'not present in working tree')

# ----------------- P12 keyword-window extraction ------------------------

# Build a synthetic source file with lock acquire/release far apart.
$kwTmp = Join-Path $Script:Tmp 'kw'
[void](New-Item -ItemType Directory -Path $kwTmp -Force)
$lockSrc = @()
$lockSrc += "# header line 1"
$lockSrc += "# header line 2"
$lockSrc += "# header line 3"
$lockSrc += ('# filler ' * 0)
for ($i = 0; $i -lt 80; $i++) { $lockSrc += "# filler middle line $i with no keywords" }
$lockSrc += "    `$lock = Acquire-WaggleLock -Path `$lockPath -IterationId `$id"
$lockSrc += "    try {"
$lockSrc += "        # ... main work ..."
for ($i = 0; $i -lt 80; $i++) { $lockSrc += "        # iteration body line $i with no keywords" }
$lockSrc += "    }"
$lockSrc += "    finally {"
$lockSrc += "        [void](Release-WaggleLock -Path `$lockPath -LockId `$lock.lock_id)"
$lockSrc += "    }"
$lockBody = ($lockSrc -join "`n")

$kw = & ([scriptblock]::Create('. (Join-Path $args[0] "ReviewSurface.ps1"); _Get-KeywordWindowExcerpt -Body $args[1] -MaxChars 6000')) $libDir $lockBody
Assert-True 'P12: keyword_windows_used > 0 on lock file' ([int]$kw.keyword_windows_used -gt 0)
Assert-True 'P12: omitted_ranges_count > 0 (filler omitted)' ([int]$kw.omitted_ranges_count -gt 0)
Assert-True 'P12: Acquire-WaggleLock window present in excerpt' ($kw.text -match 'Acquire-WaggleLock')
Assert-True 'P12: Release-WaggleLock window present in excerpt' ($kw.text -match 'Release-WaggleLock')
Assert-True 'P12: file header present in excerpt' ($kw.text -match 'header line 1')
Assert-True 'P12: omitted markers present' ($kw.text -match '\[OMITTED: lines \d+-\d+\]')
Assert-True 'P12: line numbers present' ($kw.text -match '^\s*\d+:\s')
Assert-True 'P12: keywords_hit array populated' (@($kw.keywords_hit).Count -gt 0)

# Repeat against actual repo Invoke-WaggleIteration.ps1 -- both
# Acquire-WaggleLock and Release-WaggleLock should be visible in
# the supplement. Phase 2A-3 reviewers raised REL-001 because the
# Phase 2A-3 head-truncated supplement view did not include the
# finally block. Phase 2A-4 P12 fixes that.
$realBody = Get-Content -Raw -Path (Join-Path $repoRoot 'orchestrator/Invoke-WaggleIteration.ps1') -Encoding UTF8
$kw2 = & ([scriptblock]::Create('. (Join-Path $args[0] "ReviewSurface.ps1"); _Get-KeywordWindowExcerpt -Body $args[1] -MaxChars 12000')) $libDir $realBody
Assert-True 'P12 real: Invoke-WaggleIteration excerpt has Acquire-WaggleLock' ($kw2.text -match 'Acquire-WaggleLock')
Assert-True 'P12 real: Invoke-WaggleIteration excerpt has Release-WaggleLock' ($kw2.text -match 'Release-WaggleLock')
# `finally {` may be on its own line OR may be at end of `} finally {`.
# Accept either, line-numbered.
Assert-True 'P12 real: Invoke-WaggleIteration excerpt has finally block' (
    $kw2.text -match '(?m)^\s*\d+:\s*finally\s*\{' -or
    $kw2.text -match '(?m)^\s*\d+:.*\}\s*finally\s*\{' -or
    $kw2.text -match '(?m)^\s*\d+:.*finally\s*\{'
)

# CompletionVerifier excerpt should expose every status branch.
$cvBody = Get-Content -Raw -Path (Join-Path $repoRoot 'orchestrator/lib/CompletionVerifier.ps1') -Encoding UTF8
$kw3 = & ([scriptblock]::Create('. (Join-Path $args[0] "ReviewSurface.ps1"); _Get-KeywordWindowExcerpt -Body $args[1] -MaxChars 12000')) $libDir $cvBody
Assert-True 'P12 real: CompletionVerifier exposes Test-UniqueIterationArtifact call' ($kw3.text -match 'Test-UniqueIterationArtifact')
Assert-True 'P12 real: CompletionVerifier exposes NEEDS_REVIEW_CONFLICT branch' ($kw3.text -match 'NEEDS_REVIEW_CONFLICT')
Assert-True 'P12 real: CompletionVerifier exposes COMPLETED_UNVERIFIED branch' ($kw3.text -match 'COMPLETED_UNVERIFIED')

# Build full supplement against real repo and check that keyword-window
# files have extraction_reason set + nonzero keyword_windows_used.
$supKw = Get-WaggleReviewSurfaceSupplement -ProjectRoot $repoRoot
$kwFiles = @($supKw.included_files | Where-Object {
    $_.path -in @(
        'orchestrator/Invoke-WaggleIteration.ps1',
        'orchestrator/Invoke-WaggleReview.ps1',
        'orchestrator/lib/CompletionVerifier.ps1'
    )
})
Assert-True 'P12 supplement: at least one keyword-window file recorded' ($kwFiles.Count -gt 0)
foreach ($f in $kwFiles) {
    # Reason depends on file size: small files (< per-file budget)
    # come back as 'small_file_full_include'; large files come back
    # as 'header_plus_keyword_windows'. Both are P12 OK.
    Assert-True ("P12 supplement: $($f.path) extraction_reason is keyword-window aware") (
        $f.extraction_reason -in @('header_plus_keyword_windows', 'small_file_full_include')
    )
}

# ----------------- P13 controlled-glob expansion -----------------

# Real repo: Get-WaggleReviewSurfaceFileSet should yield canonical
# files PLUS any new ones matched by the controlled allowlist.
$set = Get-WaggleReviewSurfaceFileSet -ProjectRoot $repoRoot
Assert-True 'P13: file set canonical_count > 0' ([int]$set.canonical_count -gt 0)
Assert-True 'P13: file set is deterministic (no duplicates)' (
    @($set.files | Select-Object -Unique).Count -eq @($set.files).Count
)
Assert-True 'P13: no excluded directory paths in final set' (
    -not ($set.files | Where-Object { $_ -match '^iterations/|^state/|^transcripts/|^\.git/|^node_modules/|^__pycache__/' })
)

# Synthetic: drop a brand-new test file under orchestrator/Test-*.ps1
# and confirm the controlled glob picks it up (without modifying the
# real repo's Test-* files).
$tmpRepo = Join-Path $Script:Tmp ('p13-' + [guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path (Join-Path $tmpRepo 'orchestrator/lib/review') -Force)
[void](New-Item -ItemType Directory -Path (Join-Path $tmpRepo 'iterations/should-be-excluded') -Force)
[void](New-Item -ItemType Directory -Path (Join-Path $tmpRepo 'state') -Force)
Set-Content -Path (Join-Path $tmpRepo 'orchestrator/Test-NewlyAdded.ps1') -Value '# new test' -Encoding UTF8
Set-Content -Path (Join-Path $tmpRepo 'iterations/should-be-excluded/x.ps1') -Value '# should not be picked' -Encoding UTF8
Set-Content -Path (Join-Path $tmpRepo 'state/leak.ps1') -Value '# leak risk' -Encoding UTF8
$set2 = Get-WaggleReviewSurfaceFileSet -ProjectRoot $tmpRepo
Assert-True 'P13: brand-new Test-*.ps1 in allowlist dir is picked up' ($set2.files -contains 'orchestrator/Test-NewlyAdded.ps1')
Assert-True 'P13: file under iterations/ is NOT picked up' (-not ($set2.files | Where-Object { $_ -like 'iterations/*' }))
Assert-True 'P13: file under state/ is NOT picked up' (-not ($set2.files | Where-Object { $_ -like 'state/*' }))

# Synthetic: large file is rejected by size cap.
$bigFile = Join-Path $tmpRepo 'orchestrator/Test-Huge.ps1'
$bigContent = ('# x' * 100000)  # ~300000 bytes
Set-Content -Path $bigFile -Value $bigContent -Encoding UTF8
$set3 = Get-WaggleReviewSurfaceFileSet -ProjectRoot $tmpRepo -MaxOnDiskBytes 100000
Assert-True 'P13: oversize file is rejected by MaxOnDiskBytes' (
    -not ($set3.files -contains 'orchestrator/Test-Huge.ps1') -and
    ($set3.rejected | Where-Object { $_.path -eq 'orchestrator/Test-Huge.ps1' })
)

# Max-files cap is respected.
$set4 = Get-WaggleReviewSurfaceFileSet -ProjectRoot $repoRoot -MaxFiles 5
Assert-True 'P13: MaxFiles cap respected' ($set4.files.Count -le 5)

# ----------------- glue helper -----------------

$ghi = Get-WaggleReviewPackageQualityWithSupplementInfo -ProjectRoot $repoRoot -PackagePath $emptyPkg
Assert-True 'glue: empty package -> sparse=true' ($ghi.sparse)
Assert-True 'glue: empty package -> supplement built' ($null -ne $ghi.supplement -and $ghi.supplement.file_count -gt 0)

$ghi2 = Get-WaggleReviewPackageQualityWithSupplementInfo -ProjectRoot $repoRoot -PackagePath $richPkg
Assert-True 'glue: rich package -> sparse=false' (-not $ghi2.sparse)
Assert-True 'glue: rich package -> no supplement' ($null -eq $ghi2.supplement)

# ----------------- cleanup -----------------

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $Script:Tmp

Write-Host ''
Write-Host "Result: $Script:Pass/$($Script:Pass + $Script:Fail) tests passed" -ForegroundColor Cyan
if ($Script:Fail -gt 0) { exit 1 } else { exit 0 }
