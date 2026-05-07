#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B-Revision (ARCH-013): build a proposal matrix that
    aggregates all improvement proposals from internal Claude
    reviews (Phase 2A-2 architect/security/reliability),
    optional Codex Scout findings, and optional external
    (Gemini/Grok/GPT) reviewer imports.

    GPT synthesis (P9) consumes this matrix as its primary decision
    surface so it does not re-derive proposals from raw review text.

    Outputs:
      - <synth_dir>/proposal_matrix.json (schema-validated)
      - <synth_dir>/proposal_matrix.md   (rendered markdown grouped
        by category)

.PARAMETER ConfigPath
    Orchestrator config (live or example).
.PARAMETER EpochId
    Stable identifier for the epoch the matrix belongs to.
.PARAMETER IterationId
    The CARRIER iteration ID (typically the last iteration in the
    epoch); synthesis + matrix sit under iterations/<id>/.
.PARAMETER IncludeCodex
    Include codex findings if iterations/<id>/codex/findings.json
    or its latest <utc>_codex_findings.json exists. Default: $true.
.PARAMETER IncludeExternal
    Include valid external review imports for this epoch if any
    exist. Default: $true.
.PARAMETER IterationIds
    Optional explicit list of iteration IDs that contributed to the
    epoch. If omitted, we discover from
    iterations/<carrier>/external_reviews/epoch_<id>/evidence/epoch_evidence.json.
#>
[CmdletBinding()]
param(
    [string]   $ConfigPath = '',
    [string]   $EpochId = '',
    [string]   $IterationId = '',
    [bool]     $IncludeCodex = $true,
    [bool]     $IncludeExternal = $true,
    [string[]] $IterationIds = @()
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/external_review/ProviderProfiles.ps1')

function _Pmx-NowUtc { return (Get-Date).ToUniversalTime().ToString('o') }
function _Pmx-FieldOr {
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

function _Pmx-CategoryFor {
    <#
    .SYNOPSIS
    Best-effort category classification for a proposal: peeks at the
    proposal title + approach + the source role to decide.
    #>
    param([string] $Role, [string] $Title, [string] $Approach)
    $hay = ($Title + ' ' + $Approach).ToLowerInvariant()
    switch -Regex ($Role) {
        '^security$'    { return 'security' }
        '^reliability$' { return 'reliability' }
    }
    if ($hay -match 'test|coverage|fixture|mock|spec') { return 'test_coverage' }
    if ($hay -match 'doc|readme|comment|markdown|readme') { return 'docs' }
    if ($hay -match 'perf|latency|throughput|benchmark') { return 'performance' }
    if ($hay -match 'cli|automation|script|gate|pipeline|workflow') { return 'automation' }
    if ($hay -match 'ux|ui|cockpit|button') { return 'ux' }
    if ($hay -match 'architect|module|boundary|abstraction|refactor|layer|coupling') { return 'architecture' }
    return 'other'
}

function _Pmx-ReadJsonOrNull {
    param([string] $Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $null }
    try { return (Get-Content -Raw -Path $Path -Encoding UTF8 | ConvertFrom-Json) } catch { return $null }
}

function _Pmx-EachProposal {
    <#
    .SYNOPSIS
    Yield each proposal entry from a parsed review/external/codex
    object. Returns an array of pscustomobject with normalized fields.
    #>
    param(
        $Obj,
        [string] $SourceKind,
        [string] $SourceProvider,
        [string] $SourceRole,
        [string] $SourceId
    )
    $out = New-Object System.Collections.Generic.List[object]
    if ($null -eq $Obj) { return $out.ToArray() }
    $proposals = $null
    if ($SourceKind -eq 'codex') {
        # Codex schema uses 'proposals' (separate from 'findings').
        $proposals = $Obj.PSObject.Properties['proposals'] | ForEach-Object { $_.Value }
    } else {
        # Internal Claude + external reviewers use 'suggested_next_actions'.
        $proposals = $Obj.PSObject.Properties['suggested_next_actions'] | ForEach-Object { $_.Value }
    }
    if ($null -eq $proposals) { return $out.ToArray() }
    foreach ($p in @($proposals)) {
        if ($null -eq $p) { continue }
        $rec = [pscustomobject]@{
            source_kind                       = $SourceKind
            source_provider                   = $SourceProvider
            source_role                       = $SourceRole
            source_iteration_id_or_import_id = $SourceId
            original_proposal_id              = if ($p.PSObject.Properties['id']) { [string]$p.id } else { '' }
            title                             = if ($p.PSObject.Properties['title']) { [string]$p.title } else { '' }
            rationale                         = if ($p.PSObject.Properties['rationale']) { [string]$p.rationale } else { '' }
            approach                          = if ($p.PSObject.Properties['approach']) { [string]$p.approach } else { '' }
            estimated_effort                  = if ($p.PSObject.Properties['estimated_effort']) { [string]$p.estimated_effort } else { 'medium' }
            risks                             = if ($p.PSObject.Properties['risks']) { [string]$p.risks } else { '' }
            expected_payoff                   = if ($p.PSObject.Properties['expected_payoff']) { [string]$p.expected_payoff } else { '' }
            findings_in_source                = if ($Obj.PSObject.Properties['findings']) { @($Obj.findings | ForEach-Object { if ($_.PSObject.Properties['id']) { [string]$_.id } }) } else { @() }
        }
        # Patch in defaults for required fields if blank (schema needs minLength=1).
        foreach ($f in 'title','rationale','approach','risks','expected_payoff','original_proposal_id') {
            if ([string]::IsNullOrWhiteSpace([string]$rec.$f)) {
                $rec.$f = ('(unspecified ' + $f + ')')
            }
        }
        if ($rec.estimated_effort -notin @('small','medium','large')) {
            $rec.estimated_effort = 'medium'
        }
        $out.Add($rec) | Out-Null
    }
    return $out.ToArray()
}

function Build-WaggleProposalMatrix {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $ConfigPath,
        [Parameter(Mandatory)] [string] $EpochId,
        [Parameter(Mandatory)] [string] $IterationId,
        [bool]     $IncludeCodex = $true,
        [bool]     $IncludeExternal = $true,
        [string[]] $IterationIds = @()
    )

    if (-not (Test-Path -LiteralPath $ConfigPath)) { throw "config not found: $ConfigPath" }
    $cfg = Get-Content -Raw -Path $ConfigPath -Encoding UTF8 | ConvertFrom-Json
    $er  = Get-WaggleExternalReviewConfig -Config $cfg

    $projectRoot   = $cfg.projectRoot
    $iterationsDir = if ($cfg.PSObject.Properties['iterationsDir'] -and $cfg.iterationsDir) { [string]$cfg.iterationsDir } else { 'iterations' }
    $iterRoot      = Join-Path $projectRoot $iterationsDir
    $carrierDir    = Join-Path $iterRoot $IterationId
    if (-not (Test-Path -LiteralPath $carrierDir)) { throw "carrier iteration folder missing: $carrierDir" }

    # ---- Discover iteration list from the epoch_evidence manifest if not supplied
    $epochJson = Join-Path $carrierDir ('external_reviews/epoch_' + $EpochId + '/evidence/epoch_evidence.json')
    $iterIds = @()
    if ($IterationIds -and $IterationIds.Count -gt 0) {
        $iterIds = $IterationIds
    } elseif (Test-Path -LiteralPath $epochJson) {
        $ev = _Pmx-ReadJsonOrNull -Path $epochJson
        if ($null -ne $ev -and $ev.PSObject.Properties['iterations']) {
            $iterIds = @($ev.iterations | ForEach-Object { [string]$_.iteration_id })
        }
    }
    if (-not $iterIds -or $iterIds.Count -eq 0) {
        # Fall back to just the carrier.
        $iterIds = @($IterationId)
    }

    # ---- Load existing regression ledger (if it exists) so we can
    # populate linked_regressions from finding-id matches. Optional.
    $ledger = _Pmx-ReadJsonOrNull -Path (Join-Path $projectRoot 'state/regression_ledger.json')
    $ledgerByLinkedFinding = @{}
    if ($null -ne $ledger -and $ledger.PSObject.Properties['regressions']) {
        foreach ($reg in @($ledger.regressions)) {
            $regId = [string]$reg.id
            if ($reg.PSObject.Properties['linked_findings']) {
                foreach ($lf in @($reg.linked_findings)) {
                    $key = [string]$lf
                    if (-not $ledgerByLinkedFinding.ContainsKey($key)) {
                        $ledgerByLinkedFinding[$key] = New-Object System.Collections.Generic.List[string]
                    }
                    [void]$ledgerByLinkedFinding[$key].Add($regId)
                }
            }
        }
    }

    # ---- Load committed phase_fix_ledger.json so we can populate
    # linked_ledger_tags from text matches in proposal IDs / findings.
    $fixLedger = _Pmx-ReadJsonOrNull -Path (Join-Path $projectRoot 'docs/design/phase_fix_ledger.json')
    $fixTags = @()
    if ($null -ne $fixLedger -and $fixLedger.PSObject.Properties['rows']) {
        $fixTags = @($fixLedger.rows | ForEach-Object { [string]$_.tag })
    }

    # ---- Aggregate proposals
    $allRows = New-Object System.Collections.Generic.List[object]
    $cntInternal = 0; $cntCodex = 0; $cntExternal = 0

    # 1. Internal Claude reviews
    foreach ($iid in $iterIds) {
        $iterDir = Join-Path $iterRoot $iid
        if (-not (Test-Path -LiteralPath $iterDir)) { continue }
        $rDir = Join-Path $iterDir 'reviews'
        if (-not (Test-Path -LiteralPath $rDir)) { continue }
        foreach ($role in 'architect','security','reliability') {
            $jp = Join-Path $rDir ($role + '.json')
            if (-not (Test-Path -LiteralPath $jp)) { continue }
            $obj = _Pmx-ReadJsonOrNull -Path $jp
            $entries = _Pmx-EachProposal -Obj $obj -SourceKind 'claude_internal' -SourceProvider 'claude_code' -SourceRole $role -SourceId $iid
            foreach ($e in $entries) {
                $allRows.Add($e) | Out-Null
                $cntInternal++
            }
        }
    }

    # 2. Codex Scout findings (optional)
    if ($IncludeCodex) {
        $codexDir = Join-Path $carrierDir 'codex'
        if (Test-Path -LiteralPath $codexDir) {
            $codexFile = $null
            $candidate = Join-Path $codexDir 'findings.json'
            if (Test-Path -LiteralPath $candidate) {
                $codexFile = $candidate
            } else {
                $latest = @(Get-ChildItem -LiteralPath $codexDir -Filter '*_codex_findings.json' -File -ErrorAction SilentlyContinue |
                    Sort-Object Name -Descending | Select-Object -First 1)
                if ($latest.Count -gt 0) { $codexFile = $latest[0].FullName }
            }
            if ($codexFile) {
                $cobj = _Pmx-ReadJsonOrNull -Path $codexFile
                if ($null -ne $cobj) {
                    $entries = _Pmx-EachProposal -Obj $cobj -SourceKind 'codex' -SourceProvider 'codex' -SourceRole 'scout' -SourceId ([System.IO.Path]::GetFileName($codexFile))
                    foreach ($e in $entries) {
                        $allRows.Add($e) | Out-Null
                        $cntCodex++
                    }
                }
            }
        }
    }

    # 3. External imports (latest valid per provider/role for this epoch)
    if ($IncludeExternal) {
        $importedRel = [string]$er.imported_dir_relative
        if (-not $importedRel) { $importedRel = 'external_reviews/imported' }
        $importedDir = Join-Path $carrierDir ($importedRel.TrimEnd('/','\'))
        if (Test-Path -LiteralPath $importedDir) {
            # Group by (provider, role); keep latest.
            $byKey = @{}
            foreach ($f in @(Get-ChildItem -LiteralPath $importedDir -Filter '*.metadata.json' -File -ErrorAction SilentlyContinue)) {
                if ($f.Name -like '*.invalid.metadata.json') { continue }
                $m = _Pmx-ReadJsonOrNull -Path $f.FullName
                if ($null -eq $m) { continue }
                if (-not $m.ok) { continue }
                if ($m.PSObject.Properties['epoch_id'] -and [string]$m.epoch_id -ne $EpochId) { continue }
                $k = [string]$m.provider + '|' + [string]$m.role
                if (-not $byKey.ContainsKey($k) -or [string]$byKey[$k].import_id -lt [string]$m.import_id) {
                    $byKey[$k] = $m
                }
            }
            foreach ($k in $byKey.Keys) {
                $m = $byKey[$k]
                $jsonPath = [string]$m.json_path
                $robj = _Pmx-ReadJsonOrNull -Path $jsonPath
                if ($null -eq $robj) { continue }
                $entries = _Pmx-EachProposal -Obj $robj -SourceKind 'external' -SourceProvider ([string]$m.provider) -SourceRole ([string]$m.role) -SourceId ([string]$m.import_id)
                foreach ($e in $entries) {
                    $allRows.Add($e) | Out-Null
                    $cntExternal++
                }
            }
        }
    }

    # ---- Build the matrix rows in the schema shape
    $rows = New-Object System.Collections.Generic.List[object]
    $idx = 0
    foreach ($e in $allRows) {
        $idx++
        $idPrefix = switch ($e.source_kind) {
            'claude_internal' { 'PM-CL' }
            'codex'           { 'PM-CDEX' }
            'external'        { 'PM-EXT' }
            default           { 'PM-OTHER' }
        }
        $row = [ordered]@{
            id                                = ('{0}-{1:000}' -f $idPrefix, $idx)
            source_kind                       = $e.source_kind
            source_provider                   = $e.source_provider
            source_role                       = $e.source_role
            source_iteration_id_or_import_id = $e.source_iteration_id_or_import_id
            original_proposal_id              = $e.original_proposal_id
            title                             = $e.title
            rationale                         = $e.rationale
            approach                          = $e.approach
            estimated_effort                  = $e.estimated_effort
            risks                             = $e.risks
            expected_payoff                   = $e.expected_payoff
            linked_findings                   = @($e.findings_in_source)
            linked_ledger_tags                = @()
            linked_regressions                = @()
            category                          = (_Pmx-CategoryFor -Role $e.source_role -Title $e.title -Approach $e.approach)
            matrix_status                     = 'candidate'
        }
        # Populate linked_ledger_tags: any committed ledger tag whose
        # text appears in title/approach.
        $hay = ($row.title + ' ' + $row.approach + ' ' + $row.rationale)
        foreach ($t in $fixTags) {
            if ([string]::IsNullOrWhiteSpace($t)) { continue }
            if ($hay -match ('\b' + [regex]::Escape($t) + '\b')) {
                if ($row.linked_ledger_tags -notcontains $t) {
                    $row.linked_ledger_tags = @($row.linked_ledger_tags + $t)
                }
            }
        }
        # Populate linked_regressions from finding-id matches.
        foreach ($f in @($e.findings_in_source)) {
            $key = [string]$f
            if ($ledgerByLinkedFinding.ContainsKey($key)) {
                foreach ($r in $ledgerByLinkedFinding[$key]) {
                    if ($row.linked_regressions -notcontains $r) {
                        $row.linked_regressions = @($row.linked_regressions + $r)
                    }
                }
            }
        }
        $rows.Add(([pscustomobject]$row)) | Out-Null
    }

    # ---- Compose final matrix object
    $matrix = [ordered]@{
        format_version    = '1.0'
        epoch_id          = $EpochId
        iteration_id      = $IterationId
        generated_at_utc  = (_Pmx-NowUtc)
        sources_summary   = [ordered]@{
            claude_internal_count = $cntInternal
            codex_count           = $cntCodex
            external_count        = $cntExternal
            total_proposals       = ($cntInternal + $cntCodex + $cntExternal)
        }
        rows              = $rows.ToArray()
    }

    # ---- Write outputs
    $synthRel = [string]$er.synthesis_dir_relative
    if (-not $synthRel) { $synthRel = 'external_reviews/synthesis' }
    $outDir = Join-Path $carrierDir ($synthRel.TrimEnd('/','\') + '/' + $EpochId)
    if (-not (Test-Path -LiteralPath $outDir)) {
        New-Item -ItemType Directory -Path $outDir -Force | Out-Null
    }
    $jsonPath = Join-Path $outDir 'proposal_matrix.json'
    $mdPath   = Join-Path $outDir 'proposal_matrix.md'
    Set-Content -Path $jsonPath -Value (([pscustomobject]$matrix) | ConvertTo-Json -Depth 16) -Encoding UTF8

    # Render markdown (grouped by category, descending count first)
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine('# Proposal matrix — epoch ' + $EpochId)
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('Generated: ' + $matrix.generated_at_utc)
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('Sources: claude_internal=' + $cntInternal + ', codex=' + $cntCodex + ', external=' + $cntExternal + ', total=' + $matrix.sources_summary.total_proposals)
    [void]$sb.AppendLine('')
    if ($matrix.sources_summary.total_proposals -eq 0) {
        [void]$sb.AppendLine('_No proposals across any source. The synthesizer should rely on findings + ledger entries instead._')
    } else {
        $byCat = $rows.ToArray() | Group-Object category | Sort-Object Count -Descending
        foreach ($g in $byCat) {
            [void]$sb.AppendLine('## ' + $g.Name + '  (' + $g.Count + ')')
            [void]$sb.AppendLine('')
            [void]$sb.AppendLine('| ID | Source | Role | Title | Effort | Payoff | Risk | Linked |')
            [void]$sb.AppendLine('|----|--------|------|-------|--------|--------|------|--------|')
            foreach ($r in $g.Group) {
                $links = @()
                if ($r.linked_findings.Count -gt 0)   { $links += ('findings:' + ($r.linked_findings -join ',')) }
                if ($r.linked_ledger_tags.Count -gt 0){ $links += ('ledger:'   + ($r.linked_ledger_tags -join ',')) }
                if ($r.linked_regressions.Count -gt 0){ $links += ('reg:'      + ($r.linked_regressions -join ',')) }
                $linkStr = if ($links.Count -gt 0) { $links -join '; ' } else { '-' }
                $sourceStr = $r.source_provider + '/' + $r.source_kind
                $titleEsc = ($r.title -replace '\|', '\|')
                $payoffEsc = ($r.expected_payoff -replace '\|', '\|')
                $riskEsc = ($r.risks -replace '\|', '\|')
                [void]$sb.AppendLine('| ' + $r.id + ' | ' + $sourceStr + ' | ' + $r.source_role + ' | ' + $titleEsc + ' | ' + $r.estimated_effort + ' | ' + $payoffEsc + ' | ' + $riskEsc + ' | ' + $linkStr + ' |')
            }
            [void]$sb.AppendLine('')
        }
    }
    Set-Content -Path $mdPath -Value $sb.ToString() -Encoding UTF8

    return [pscustomobject]@{
        ok = $true
        epoch_id = $EpochId
        iteration_id = $IterationId
        json_path = $jsonPath
        md_path = $mdPath
        sources_summary = ([pscustomobject]$matrix.sources_summary)
        row_count = $rows.Count
    }
}

# CLI wrapper
if ($MyInvocation.InvocationName -ne '.' -and $ConfigPath -and $EpochId -and $IterationId) {
    $r = Build-WaggleProposalMatrix -ConfigPath $ConfigPath -EpochId $EpochId -IterationId $IterationId -IncludeCodex $IncludeCodex -IncludeExternal $IncludeExternal -IterationIds $IterationIds
    if ($r.ok) {
        Write-Host ('Proposal matrix built:')
        Write-Host ('  json   : ' + $r.json_path)
        Write-Host ('  md     : ' + $r.md_path)
        Write-Host ('  rows   : ' + $r.row_count + ' (' + $r.sources_summary.claude_internal_count + ' internal, ' + $r.sources_summary.codex_count + ' codex, ' + $r.sources_summary.external_count + ' external)')
        exit 0
    } else {
        Write-Host 'Proposal matrix build failed' -ForegroundColor Red
        exit 1
    }
}
