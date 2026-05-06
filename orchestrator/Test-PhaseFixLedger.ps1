#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2A-5 ledger validator. Reads
    docs/design/phase_fix_ledger.json (source of truth) and:
      1. Asserts required tag-number ranges are present.
      2. Asserts every `Phase 2A-N (ARCH|REL|SEC)-N` reference in
         committed source / tests has a matching ledger row.
      3. Asserts ledger rows with status fixed / already_fixed /
         false_positive_due_to_truncation have at least one anchor.
      4. Asserts every canonical anchor file exists and (if `path ::
         text`) the text actually appears in the file.
      5. Asserts backlog rows have a non-empty future-phase note.
      6. Asserts fixed rows have at least one test (or notes
         explicitly explain why it's documentation-only).
      7. Asserts ARCH-005 (Phase 2A-5) and ARCH-006 (Phase 2A-5)
         point to the right anchors.
      8. Asserts the markdown view exists and has a row for every
         JSON ledger entry (loose count only).

    The unique key is (phase_introduced, tag), NOT bare tag, because
    each review run numbers findings from 0 so the same tag-id can
    appear in multiple phases with different meanings.
#>
[CmdletBinding()] param()

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$ledgerJson = Join-Path $repoRoot 'docs/design/phase_fix_ledger.json'
$ledgerMd   = Join-Path $repoRoot 'docs/design/phase_fix_ledger.md'

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

# ----------------- 1. Files exist -----------------

Assert-True 'ledger JSON exists' (Test-Path -LiteralPath $ledgerJson)
Assert-True 'ledger MD exists'   (Test-Path -LiteralPath $ledgerMd)

if (-not (Test-Path -LiteralPath $ledgerJson)) {
    Write-Host ''
    Write-Host "Result: $Script:Pass/$($Script:Pass + $Script:Fail) tests passed (early-exit, no JSON)" -ForegroundColor Cyan
    exit 1
}

$ledger = Get-Content -Raw -Path $ledgerJson -Encoding UTF8 | ConvertFrom-Json
Assert-True 'ledger JSON parses' ($null -ne $ledger -and $null -ne $ledger.rows)
$rows = @($ledger.rows)

# Index by (phase_introduced, tag) for collision-aware lookup.
$rowKey = {
    param($r)
    return ($r.phase_introduced + ' :: ' + $r.tag)
}
$keyedRows = @{}
foreach ($r in $rows) {
    $k = & $rowKey $r
    if (-not $keyedRows.ContainsKey($k)) { $keyedRows[$k] = @() }
    $keyedRows[$k] += $r
}

# ----------------- 2. Required tag-number ranges -----------------

$expectedTags = @()
0..6 | ForEach-Object { $expectedTags += ('ARCH-{0:D3}' -f $_) }
0..9 | ForEach-Object { $expectedTags += ('REL-{0:D3}' -f $_) }
0..7 | ForEach-Object { $expectedTags += ('SEC-{0:D3}' -f $_) }
# Master prompt uses 1-2 digit form (ARCH-001 not ARCH-001-padded).
$expectedTags = @()
0..6 | ForEach-Object { $expectedTags += ('ARCH-' + [string]$_.ToString().PadLeft(3, '0').TrimStart('0').PadLeft(3, '0')) }
# Simpler: master prompt explicitly lists ARCH-000..ARCH-006 etc.
$expectedTags = @(
    'ARCH-000','ARCH-001','ARCH-002','ARCH-003','ARCH-004','ARCH-005','ARCH-006',
    'REL-000','REL-001','REL-002','REL-003','REL-004','REL-005','REL-006','REL-007','REL-008','REL-009',
    'SEC-000','SEC-001','SEC-002','SEC-003','SEC-004','SEC-005','SEC-006','SEC-007'
)
$presentTags = @($rows | ForEach-Object { $_.tag } | Sort-Object -Unique)
$missingTags = @($expectedTags | Where-Object { $presentTags -notcontains $_ })
Assert-True 'ledger covers required ARCH/REL/SEC tag-number ranges' ($missingTags.Count -eq 0) ("missing: " + ($missingTags -join ', '))

# ----------------- 3. Every (phase, tag) reference in source has a row -----------------

# Walk committed source/test/doc files (exclude ledger itself, runtime
# artifacts, review narrative under iterations/, .git, third-party).
$searchRoots = @(
    'orchestrator',
    'schemas',
    'prompts',
    'docs/design',
    'docs/runs/orchestrator_phase2a4_review_integrity_2026_05_06',
    'docs/runs/orchestrator_phase2a3_review_surface_2026_05_06',
    'docs/runs/orchestrator_phase2a5_fix_ledger_2026_05_06'
)
$excludePathFragments = @(
    'phase_fix_ledger.md',     # the ledger view itself (narrative table)
    'phase_fix_ledger.json',   # the source of truth
    'iterations/',
    'state/',
    'transcripts/',
    '.git/',
    'node_modules/',
    '__pycache__/',
    '/reviews/'                # review-output narrative
)

$pat = 'Phase\s+2A-(\d+)\s+(ARCH|REL|SEC)-(\d+)'
$found = @{}  # key=phase tag string -> count
foreach ($root in $searchRoots) {
    $rootAbs = Join-Path $repoRoot $root
    if (-not (Test-Path -LiteralPath $rootAbs)) { continue }
    $files = @(Get-ChildItem -LiteralPath $rootAbs -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object {
            ($_.Extension -eq '.ps1' -or $_.Extension -eq '.psm1' -or $_.Extension -eq '.md' -or $_.Extension -eq '.json' -or $_.Extension -eq '.txt') -and
            ($_.Length -le 5MB)
        })
    foreach ($f in $files) {
        $rel = ($f.FullName -replace '\\', '/').Substring(($repoRoot -replace '\\','/').Length + 1)
        $skip = $false
        foreach ($ex in $excludePathFragments) {
            if ($rel.IndexOf($ex, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) { $skip = $true; break }
        }
        if ($skip) { continue }
        $text = ''
        try { $text = Get-Content -Raw -Path $f.FullName -Encoding UTF8 } catch { continue }
        $matches = [regex]::Matches($text, $pat)
        foreach ($m in $matches) {
            $phase = 'Phase 2A-' + $m.Groups[1].Value
            $tagFamily = $m.Groups[2].Value
            $tagNum = $m.Groups[3].Value.PadLeft(3, '0')
            $tag = $tagFamily + '-' + $tagNum
            $key = $phase + ' :: ' + $tag
            if (-not $found.ContainsKey($key)) { $found[$key] = 0 }
            $found[$key]++
        }
    }
}

$missingFromLedger = @()
foreach ($k in $found.Keys) {
    # Match against ledger by (phase_fixed_or_documented OR phase_introduced) + tag.
    # Code anchors typically reference the phase in which the fix
    # landed; some references in older docs may use phase_introduced.
    $phase, $tag = $k -split ' :: '
    $hit = $false
    foreach ($r in $rows) {
        if ($r.tag -eq $tag -and (
            $r.phase_fixed_or_documented -eq $phase -or
            $r.phase_introduced -eq $phase
        )) {
            $hit = $true; break
        }
    }
    if (-not $hit) {
        $missingFromLedger += $k
    }
}
Assert-True 'every (Phase 2A-N, tag) source reference has a ledger row' ($missingFromLedger.Count -eq 0) ('missing: ' + ($missingFromLedger -join '; '))

# ----------------- 4. Fixed rows have anchors + (5) anchor files exist + (6) anchor text appears -----------------

$missingAnchors = @()
$missingFiles = @()
$missingText = @()
foreach ($r in $rows) {
    $needAnchor = ($r.status -in @('fixed','already_fixed','false_positive_due_to_truncation'))
    $anchors = @()
    if ($r.canonical_source_anchors) { $anchors = @($r.canonical_source_anchors) }
    if ($needAnchor -and $anchors.Count -eq 0) {
        $missingAnchors += (& $rowKey $r)
        continue
    }
    foreach ($a in $anchors) {
        $path = $a
        $text = ''
        if ($a -match '^(.+?)\s*::\s*(.+)$') {
            $path = $Matches[1].Trim()
            $text = $Matches[2].Trim()
        }
        $abs = Join-Path $repoRoot $path
        if (-not (Test-Path -LiteralPath $abs)) {
            $missingFiles += ((& $rowKey $r) + ' -> ' + $path)
            continue
        }
        if ($text) {
            $body = ''
            try { $body = Get-Content -Raw -Path $abs -Encoding UTF8 } catch { }
            if ($body -notmatch [regex]::Escape($text)) {
                # Tolerate the case where the anchor text is itself
                # the ledger row's title and the path is the ledger
                # file (self-reference for reserved rows).
                if ($a -match 'phase_fix_ledger\.(md|json)\s*::\s*(.+)$') {
                    # the anchor text must appear in the ledger -- it does, this row's tag.
                    if ($body -notmatch [regex]::Escape($text)) {
                        $missingText += ((& $rowKey $r) + ' -> ' + $a)
                    }
                } else {
                    $missingText += ((& $rowKey $r) + ' -> ' + $a)
                }
            }
        }
    }
}
Assert-True 'fixed/already_fixed/false-positive rows have at least one anchor' ($missingAnchors.Count -eq 0) ('missing: ' + ($missingAnchors -join '; '))
Assert-True 'every canonical anchor file exists' ($missingFiles.Count -eq 0) ('missing: ' + ($missingFiles -join '; '))
Assert-True 'every "path :: text" anchor text appears in the file' ($missingText.Count -eq 0) ('missing: ' + ($missingText -join '; '))

# ----------------- 7. Backlog rows have a future-phase note -----------------

$emptyBacklog = @()
foreach ($r in $rows) {
    if ($r.status -ne 'backlog') { continue }
    $notes = [string]$r.notes
    # Required: notes mentions "Acceptance" or a future phase like Phase 2A-N
    if (-not ($notes -match 'Acceptance' -and $notes -match 'Phase')) {
        $emptyBacklog += (& $rowKey $r)
    }
}
Assert-True 'every backlog row has a future-phase + acceptance note' ($emptyBacklog.Count -eq 0) ('missing: ' + ($emptyBacklog -join '; '))

# ----------------- 8. Fixed rows have tests (or notes explain doc-only) -----------------

$noTestFixed = @()
foreach ($r in $rows) {
    if ($r.status -ne 'fixed' -and $r.status -ne 'already_fixed') { continue }
    $tests = @()
    if ($r.tests) { $tests = @($r.tests) }
    if ($tests.Count -eq 0) {
        $notes = [string]$r.notes
        if ($notes -notmatch '(?i)documentation.only|doc.only') {
            $noTestFixed += (& $rowKey $r)
        }
    }
}
Assert-True 'every fixed/already_fixed row has at least one test (or notes flag doc-only)' ($noTestFixed.Count -eq 0) ('missing: ' + ($noTestFixed -join '; '))

# ----------------- 9. ARCH-005 (Phase 2A-5) wiring -----------------

$arch005Phase2A5 = @($rows | Where-Object { $_.tag -eq 'ARCH-005' -and $_.phase_introduced -eq 'Phase 2A-5' })
Assert-True 'ARCH-005 (Phase 2A-5) row exists' ($arch005Phase2A5.Count -eq 1)
if ($arch005Phase2A5.Count -eq 1) {
    $r = $arch005Phase2A5[0]
    $anchorPaths = @($r.canonical_source_anchors | ForEach-Object { ($_ -split '::')[0].Trim() })
    Assert-True 'ARCH-005 (Phase 2A-5) anchors include phase_fix_ledger.md' ($anchorPaths -contains 'docs/design/phase_fix_ledger.md')
    Assert-True 'ARCH-005 (Phase 2A-5) anchors include Test-PhaseFixLedger.ps1' ($anchorPaths -contains 'orchestrator/Test-PhaseFixLedger.ps1')
}

# ----------------- 10. ARCH-006 wiring -----------------

$arch006 = @($rows | Where-Object { $_.tag -eq 'ARCH-006' })
Assert-True 'ARCH-006 row exists' ($arch006.Count -ge 1)
if ($arch006.Count -ge 1) {
    $r = $arch006[0]
    $anchorPaths = @($r.canonical_source_anchors | ForEach-Object { ($_ -split '::')[0].Trim() })
    Assert-True 'ARCH-006 anchors include Run-WaggleHardeningGates.ps1' ($anchorPaths -contains 'orchestrator/Run-WaggleHardeningGates.ps1')
    Assert-True 'ARCH-006 anchors include Test-HardeningGatesReportPath.ps1' ($anchorPaths -contains 'orchestrator/Test-HardeningGatesReportPath.ps1')
}

# ----------------- 11. Markdown view has at least as many rows as JSON (loose count) -----------------

if (Test-Path -LiteralPath $ledgerMd) {
    $mdText = Get-Content -Raw -Path $ledgerMd -Encoding UTF8
    # Count table rows that begin with `| \`TAG-NNN\``
    $mdRowMatches = [regex]::Matches($mdText, '(?m)^\|\s*`(ARCH|REL|SEC)-\d{3}`')
    Assert-True 'ledger markdown has at least one table row per JSON entry' ($mdRowMatches.Count -ge $rows.Count) ("md rows = $($mdRowMatches.Count); json rows = $($rows.Count)")
}

# ----------------- summary -----------------

Write-Host ''
Write-Host "Result: $Script:Pass/$($Script:Pass + $Script:Fail) tests passed" -ForegroundColor Cyan
if ($Script:Fail -gt 0) { exit 1 } else { exit 0 }
