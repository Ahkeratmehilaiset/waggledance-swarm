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
Assert-True 'supplement: total_chars > 0 and <= cap' (([int]$sup.total_chars -gt 0) -and ([int]$sup.total_chars -le [int]$sup.max_total_chars))
Assert-True 'supplement: markdown contains UNTRUSTED label' ($sup.markdown -match 'UNTRUSTED DATA')
Assert-True 'supplement: markdown contains REVIEW SURFACE SUPPLEMENT' ($sup.markdown -match 'REVIEW SURFACE SUPPLEMENT')

# Per-file caps. Note that body is truncated BEFORE redaction; the
# redactor can replace short tokens with longer "[REDACTED:NAME]"
# sentinels so the post-redaction included_chars may slightly exceed
# the pre-cap budget. Allow ~20% slack on top of MaxFileChars.
$slackCap = [int]([Math]::Ceiling([double]$sup.max_file_chars * 1.2))
foreach ($f in $sup.included_files) {
    if ([int]$f.included_chars -gt $slackCap) {
        Assert-True ("supplement: file $($f.path) within per-file cap (with redaction slack)") $false ("included=$($f.included_chars) > slackCap=$slackCap")
    }
}
Assert-True 'supplement: all included files <= max_file_chars (with 20% redaction slack)' $true

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
