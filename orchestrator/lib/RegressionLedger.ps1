# RegressionLedger.ps1
#
# Phase 2B-Revision (REL-012): track every detected regression with
# severity score (0-100), trajectory, and a status state machine.
# Schema: schemas/regression_ledger.schema.json
#
# Atomic writes: Save-WaggleRegressionLedger writes to a sibling
# .tmp file then renames over the target. Runs the schema-shape
# check before save.
#
# Status transitions are enforced by Update-WaggleRegressionEntry.

$ErrorActionPreference = 'Stop'

$Script:RLSeverities  = @('info','low','medium','high','critical')
$Script:RLFixability  = @('trivial','clear','ambiguous','strategic','unsafe')
$Script:RLStatuses    = @(
    'open','classified_trivial','classified_local_repair','classified_external','classified_manual',
    'repair_prompt_generated','repair_iteration_in_progress','fix_attempted',
    'verification_pending','verified','still_failing','escalated_to_external_review',
    'reopened','mitigated','fixed','false_positive','backlog'
)
$Script:RLCategories  = @(
    'test_failure','hardening_gate_failure','ci_failure','runtime_crash',
    'lock_state_signal','security_redaction','no_work_stall',
    'source_supplement_sparse','doc_report_mismatch','other'
)
$Script:RLEvents      = @(
    'introduced','detected','classified',
    'repair_prompt_generated','repair_iteration_started','attempted_fix',
    'verification_pending','verification_clean','verification_failed',
    'escalated_to_external_review','root_cause_required',
    'reopened','mitigated','verified'
)

# Allowed status transitions. Map: current -> [allowed next].
$Script:RLAllowedTransitions = @{
    'open'                          = @('classified_trivial','classified_local_repair','classified_external','classified_manual','fix_attempted','verification_pending','verified','still_failing','escalated_to_external_review','mitigated','false_positive','backlog')
    'classified_trivial'            = @('repair_prompt_generated','escalated_to_external_review','classified_external','open','backlog')
    'classified_local_repair'       = @('repair_prompt_generated','escalated_to_external_review','classified_external','open','backlog')
    'classified_external'           = @('escalated_to_external_review','open','backlog','mitigated')
    'classified_manual'             = @('mitigated','reopened','open','backlog','escalated_to_external_review')
    'repair_prompt_generated'       = @('repair_iteration_in_progress','still_failing','classified_external','open','escalated_to_external_review')
    'repair_iteration_in_progress'  = @('fix_attempted','still_failing','escalated_to_external_review','open')
    'fix_attempted'                 = @('verification_pending','still_failing','escalated_to_external_review','verified')
    'verification_pending'          = @('verified','still_failing','escalated_to_external_review')
    'verified'                      = @('reopened','fixed','still_failing')
    'still_failing'                 = @('repair_prompt_generated','escalated_to_external_review','fix_attempted')
    'escalated_to_external_review'  = @('reopened','verified','mitigated','fixed','open')
    'reopened'                      = @('open','classified_trivial','classified_local_repair','classified_external','classified_manual','fix_attempted','verification_pending','still_failing','escalated_to_external_review')
    'mitigated'                     = @('reopened','open','escalated_to_external_review')
    'fixed'                         = @('reopened','open')
    'false_positive'                = @('reopened','open')
    'backlog'                       = @('open','classified_trivial','classified_local_repair','classified_external','classified_manual','escalated_to_external_review')
}

$Script:RLScoringRubric = @(
    @{ category = 'hardening_gate_failure';           weight = 40 }
    @{ category = 'ci_failure';                       weight = 30 }
    @{ category = 'previously_passing_test_now_failing'; weight = 25 }
    @{ category = 'runtime_crash';                    weight = 20 }
    @{ category = 'lock_state_signal';                weight = 15 }
    @{ category = 'security_redaction';               weight = 15 }
    @{ category = 'no_work_stall';                    weight = 10 }
    @{ category = 'source_supplement_sparse';         weight = 10 }
    @{ category = 'doc_report_mismatch';              weight = 5 }
)

function _Rl-NowUtc { return (Get-Date).ToUniversalTime().ToString('o') }

function _Rl-Sha256Hex {
    param([string] $Text)
    if (-not $Text) { return '' }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Text)
        $hash = $sha.ComputeHash($bytes)
        return ([System.BitConverter]::ToString($hash) -replace '-', '').ToLowerInvariant()
    } finally { $sha.Dispose() }
}

function Get-WaggleIssueSignature {
    [CmdletBinding()]
    param(
        [string] $IterationIdIntroduced = '',
        [string] $FindingId = '',
        [string] $FailingTestOrFile = ''
    )
    $payload = ($IterationIdIntroduced + '|' + $FindingId + '|' + $FailingTestOrFile)
    return (_Rl-Sha256Hex -Text $payload)
}

function Get-WaggleRegressionLedger {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return [pscustomobject]@{ format_version = '1.0'; generated_at_utc = (_Rl-NowUtc); regressions = @() }
    }
    try {
        $obj = Get-Content -Raw -Path $Path -Encoding UTF8 | ConvertFrom-Json
    } catch {
        throw "regression ledger malformed: $Path : $($_.Exception.Message)"
    }
    if ($null -eq $obj) {
        return [pscustomobject]@{ format_version = '1.0'; generated_at_utc = (_Rl-NowUtc); regressions = @() }
    }
    if (-not ($obj.PSObject.Properties['format_version'])) {
        $obj | Add-Member -NotePropertyName format_version -NotePropertyValue '1.0' -Force
    }
    if (-not ($obj.PSObject.Properties['regressions'])) {
        $obj | Add-Member -NotePropertyName regressions -NotePropertyValue @() -Force
    }
    return $obj
}

function _Rl-ValidateLedgerShape {
    param($Ledger)
    if ($null -eq $Ledger) { throw 'ledger is null' }
    if (-not $Ledger.PSObject.Properties['format_version']) { throw 'ledger missing format_version' }
    if ([string]$Ledger.format_version -ne '1.0') { throw "ledger format_version must be '1.0'" }
    if (-not $Ledger.PSObject.Properties['regressions']) { throw 'ledger missing regressions[]' }
    foreach ($e in @($Ledger.regressions)) {
        if (-not $e.PSObject.Properties['id'])               { throw 'regression missing id' }
        if (-not $e.PSObject.Properties['detected_in_iteration']) { throw "regression $($e.id) missing detected_in_iteration" }
        if (-not $e.PSObject.Properties['status'])           { throw "regression $($e.id) missing status" }
        if ($Script:RLStatuses -notcontains [string]$e.status) { throw "regression $($e.id) invalid status: $($e.status)" }
        if (-not $e.PSObject.Properties['severity'])         { throw "regression $($e.id) missing severity" }
        if ($Script:RLSeverities -notcontains [string]$e.severity) { throw "regression $($e.id) invalid severity: $($e.severity)" }
        if (-not $e.PSObject.Properties['score'])            { throw "regression $($e.id) missing score" }
        $s = [int]$e.score
        if ($s -lt 0 -or $s -gt 100) { throw "regression $($e.id) score out of range 0..100" }
        if (-not $e.PSObject.Properties['category'])         { throw "regression $($e.id) missing category" }
        if ($Script:RLCategories -notcontains [string]$e.category) { throw "regression $($e.id) invalid category: $($e.category)" }
        if (-not $e.PSObject.Properties['history'])          { throw "regression $($e.id) missing history" }
    }
}

function Save-WaggleRegressionLedger {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $Path,
        [Parameter(Mandatory)] $Ledger
    )
    _Rl-ValidateLedgerShape -Ledger $Ledger
    $Ledger.generated_at_utc = (_Rl-NowUtc)
    $tmp = $Path + '.tmp'
    Set-Content -Path $tmp -Value (([pscustomobject]$Ledger) | ConvertTo-Json -Depth 16) -Encoding UTF8
    if (Test-Path -LiteralPath $Path) { Remove-Item -LiteralPath $Path -Force }
    Move-Item -LiteralPath $tmp -Destination $Path -Force
}

function Get-WaggleRegressionScore {
    <#
    .SYNOPSIS
    Compute a regression score 0..100 from a category-set. The
    Entry parameter is the regression entry; we look at its
    `category` plus optional `score_categories[]` (when an entry
    hits multiple categories simultaneously).
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)] $Entry)

    $cats = New-Object System.Collections.Generic.List[string]
    if ($Entry.PSObject.Properties['category'])         { [void]$cats.Add([string]$Entry.category) }
    if ($Entry.PSObject.Properties['score_categories']) {
        foreach ($c in @($Entry.score_categories)) { [void]$cats.Add([string]$c) }
    }
    $sum = 0
    foreach ($c in $cats | Select-Object -Unique) {
        $row = $Script:RLScoringRubric | Where-Object { $_.category -eq $c } | Select-Object -First 1
        if ($row) { $sum += [int]$row.weight }
    }
    if ($sum -gt 100) { $sum = 100 }
    return $sum
}

function _Rl-SeverityFromScore {
    param([int] $Score)
    if ($Score -ge 80) { return 'critical' }
    if ($Score -ge 60) { return 'high' }
    if ($Score -ge 40) { return 'medium' }
    if ($Score -ge 20) { return 'low' }
    return 'info'
}

function _Rl-NextRegId {
    param($Ledger, [string] $Date = '')
    if (-not $Date) { $Date = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd') }
    $prefix = 'REG-' + $Date + '-'
    $maxN = 0
    foreach ($e in @($Ledger.regressions)) {
        $id = [string]$e.id
        if ($id -like ($prefix + '*')) {
            $tail = $id.Substring($prefix.Length)
            $n = 0
            if ([int]::TryParse($tail, [ref]$n) -and $n -gt $maxN) { $maxN = $n }
        }
    }
    return ($prefix + ('{0:000}' -f ($maxN + 1)))
}

function Add-WaggleRegressionEntry {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Ledger,
        [Parameter(Mandatory)] $Entry
    )
    if (-not $Entry.PSObject.Properties['id'] -or -not $Entry.id) {
        $newId = _Rl-NextRegId -Ledger $Ledger
        if ($Entry -is [System.Collections.IDictionary]) {
            $Entry['id'] = $newId
        } else {
            $Entry | Add-Member -NotePropertyName id -NotePropertyValue $newId -Force
        }
    }
    if (-not $Entry.PSObject.Properties['status']) {
        $Entry | Add-Member -NotePropertyName status -NotePropertyValue 'open' -Force
    }
    if (-not $Entry.PSObject.Properties['severity']) {
        $Entry | Add-Member -NotePropertyName severity -NotePropertyValue 'medium' -Force
    }
    if (-not $Entry.PSObject.Properties['score']) {
        $Entry | Add-Member -NotePropertyName score -NotePropertyValue (Get-WaggleRegressionScore -Entry $Entry) -Force
    }
    if (-not $Entry.PSObject.Properties['repair_attempts']) {
        $Entry | Add-Member -NotePropertyName repair_attempts -NotePropertyValue 0 -Force
    }
    if (-not $Entry.PSObject.Properties['affected_files'])  { $Entry | Add-Member -NotePropertyName affected_files -NotePropertyValue @() -Force }
    if (-not $Entry.PSObject.Properties['failing_tests'])   { $Entry | Add-Member -NotePropertyName failing_tests -NotePropertyValue @() -Force }
    if (-not $Entry.PSObject.Properties['linked_findings']) { $Entry | Add-Member -NotePropertyName linked_findings -NotePropertyValue @() -Force }
    if (-not $Entry.PSObject.Properties['linked_proposals']){ $Entry | Add-Member -NotePropertyName linked_proposals -NotePropertyValue @() -Force }
    if (-not $Entry.PSObject.Properties['verified_by'])     { $Entry | Add-Member -NotePropertyName verified_by -NotePropertyValue @() -Force }
    if (-not $Entry.PSObject.Properties['notes'])           { $Entry | Add-Member -NotePropertyName notes -NotePropertyValue '' -Force }
    if (-not $Entry.PSObject.Properties['history']) {
        $hist = @([pscustomobject]@{
            iteration_id = [string]$Entry.detected_in_iteration
            event = 'detected'
            issue_signature = if ($Entry.PSObject.Properties['issue_signature']) { [string]$Entry.issue_signature } else { '' }
            repair_attempt_index = 0
            score_delta = [int]$Entry.score
            notes = ''
            at_utc = (_Rl-NowUtc)
        })
        $Entry | Add-Member -NotePropertyName history -NotePropertyValue $hist -Force
    }
    # Recompute severity from score if it's lower than the rubric implies.
    $sevFromScore = _Rl-SeverityFromScore -Score ([int]$Entry.score)
    $Entry.severity = $sevFromScore

    $arr = @($Ledger.regressions) + @($Entry)
    $Ledger.regressions = $arr
    return $Entry
}

function Update-WaggleRegressionEntry {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Ledger,
        [Parameter(Mandatory)] [string] $RegId,
        [Parameter(Mandatory)] [hashtable] $Update
    )
    $idx = -1
    for ($i = 0; $i -lt @($Ledger.regressions).Count; $i++) {
        if ([string]$Ledger.regressions[$i].id -eq $RegId) { $idx = $i; break }
    }
    if ($idx -lt 0) { throw "regression entry not found: $RegId" }
    $entry = $Ledger.regressions[$idx]
    if ($Update.ContainsKey('status')) {
        $newStatus = [string]$Update['status']
        $oldStatus = [string]$entry.status
        if ($Script:RLStatuses -notcontains $newStatus) {
            throw "invalid target status: $newStatus"
        }
        if ($newStatus -ne $oldStatus) {
            $allowed = $Script:RLAllowedTransitions[$oldStatus]
            if (-not $allowed -or $allowed -notcontains $newStatus) {
                throw ("illegal status transition: " + $oldStatus + ' -> ' + $newStatus)
            }
            $entry.status = $newStatus
        }
    }
    if ($Update.ContainsKey('repair_attempts')) {
        $entry.repair_attempts = [int]$Update['repair_attempts']
    }
    if ($Update.ContainsKey('fixability')) {
        $fb = [string]$Update['fixability']
        if ($Script:RLFixability -notcontains $fb) { throw "invalid fixability: $fb" }
        if ($entry.PSObject.Properties['fixability']) { $entry.fixability = $fb }
        else { $entry | Add-Member -NotePropertyName fixability -NotePropertyValue $fb -Force }
    }
    if ($Update.ContainsKey('history_event')) {
        $ev = $Update['history_event']
        if ($null -eq $ev) { throw 'history_event must be a hashtable' }
        if ($null -eq $ev['event']) { throw 'history_event.event is required' }
        if ($Script:RLEvents -notcontains [string]$ev['event']) { throw "invalid history event: $($ev['event'])" }
        $rec = [pscustomobject]@{
            iteration_id          = if ($ev.ContainsKey('iteration_id')) { [string]$ev['iteration_id'] } else { '' }
            event                 = [string]$ev['event']
            issue_signature       = if ($ev.ContainsKey('issue_signature')) { [string]$ev['issue_signature'] } else { '' }
            repair_attempt_index  = if ($ev.ContainsKey('repair_attempt_index')) { [int]$ev['repair_attempt_index'] } else { 0 }
            score_delta           = if ($ev.ContainsKey('score_delta')) { [int]$ev['score_delta'] } else { 0 }
            notes                 = if ($ev.ContainsKey('notes')) { [string]$ev['notes'] } else { '' }
            at_utc                = (_Rl-NowUtc)
        }
        $entry.history = @($entry.history) + @($rec)
    }
    if ($Update.ContainsKey('verified_by_iteration')) {
        $iid = [string]$Update['verified_by_iteration']
        $vlist = @($entry.verified_by) + @($iid)
        $entry.verified_by = $vlist
    }
    if ($Update.ContainsKey('fixed_in_iteration')) {
        if ($entry.PSObject.Properties['fixed_in_iteration']) { $entry.fixed_in_iteration = [string]$Update['fixed_in_iteration'] }
        else { $entry | Add-Member -NotePropertyName fixed_in_iteration -NotePropertyValue ([string]$Update['fixed_in_iteration']) -Force }
    }
    if ($Update.ContainsKey('add_linked_finding')) {
        $f = [string]$Update['add_linked_finding']
        if ($entry.linked_findings -notcontains $f) {
            $entry.linked_findings = @($entry.linked_findings) + @($f)
        }
    }
    if ($Update.ContainsKey('add_linked_proposal')) {
        $f = [string]$Update['add_linked_proposal']
        if ($entry.linked_proposals -notcontains $f) {
            $entry.linked_proposals = @($entry.linked_proposals) + @($f)
        }
    }
    if ($Update.ContainsKey('score_delta')) {
        $newScore = [Math]::Min(100, [Math]::Max(0, [int]$entry.score + [int]$Update['score_delta']))
        $entry.score = $newScore
        $entry.severity = _Rl-SeverityFromScore -Score $newScore
    }
    if ($Update.ContainsKey('notes')) {
        $entry.notes = [string]$Update['notes']
    }
    return $entry
}

function Format-WaggleRegressionLedgerExcerpt {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Ledger,
        [int] $MaxItems = 10
    )
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine('# Regression ledger excerpt')
    [void]$sb.AppendLine('')
    $allRegs = @($Ledger.regressions)
    $open = @($allRegs | Where-Object { @('verified','fixed','false_positive') -notcontains $_.status })
    $closed = @($allRegs | Where-Object { @('verified','fixed') -contains $_.status })
    $byScore = $open | Sort-Object -Property @{Expression='score'; Descending=$true}, @{Expression='id'; Descending=$true}
    [void]$sb.AppendLine('## Open (' + @($open).Count + ')')
    [void]$sb.AppendLine('')
    if (@($open).Count -eq 0) {
        [void]$sb.AppendLine('_No open regressions._')
    } else {
        [void]$sb.AppendLine('| ID | Severity | Score | Status | Category | Title / Symptom |')
        [void]$sb.AppendLine('|----|----------|-------|--------|----------|-----------------|')
        $shown = 0
        foreach ($r in $byScore) {
            if ($shown -ge $MaxItems) { break }
            $sym = if ($r.PSObject.Properties['first_symptom']) { [string]$r.first_symptom } else { '' }
            $sym = ($sym -replace '\|','\|')
            [void]$sb.AppendLine('| ' + $r.id + ' | ' + $r.severity + ' | ' + $r.score + ' | ' + $r.status + ' | ' + $r.category + ' | ' + $sym + ' |')
            $shown++
        }
    }
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('## Closed (' + @($closed).Count + ')')
    [void]$sb.AppendLine('')
    if (@($closed).Count -eq 0) {
        [void]$sb.AppendLine('_No closed regressions._')
    } else {
        [void]$sb.AppendLine('| ID | Severity | Status | Verified by | First symptom |')
        [void]$sb.AppendLine('|----|----------|--------|-------------|---------------|')
        foreach ($r in @($closed | Select-Object -First $MaxItems)) {
            $sym = if ($r.PSObject.Properties['first_symptom']) { [string]$r.first_symptom } else { '' }
            $sym = ($sym -replace '\|','\|')
            $vb = if ($r.PSObject.Properties['verified_by']) { ((@($r.verified_by)) -join ', ') } else { '' }
            [void]$sb.AppendLine('| ' + $r.id + ' | ' + $r.severity + ' | ' + $r.status + ' | ' + $vb + ' | ' + $sym + ' |')
        }
    }
    return $sb.ToString()
}

function Write-WaggleRegressionLedgerMarkdown {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] $Ledger,
        [Parameter(Mandatory)] [string] $Path
    )
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine('# Regression ledger')
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('Auto-rendered from `state/regression_ledger.json`.')
    [void]$sb.AppendLine('Last regenerated: ' + (_Rl-NowUtc))
    [void]$sb.AppendLine('')
    $sections = @(
        @{ name = 'Open Critical';   pred = { param($r) $r.severity -eq 'critical' -and @('verified','fixed','false_positive') -notcontains $r.status } }
        @{ name = 'Open High';       pred = { param($r) $r.severity -eq 'high'     -and @('verified','fixed','false_positive') -notcontains $r.status } }
        @{ name = 'Open Medium';     pred = { param($r) $r.severity -eq 'medium'   -and @('verified','fixed','false_positive') -notcontains $r.status } }
        @{ name = 'Open Low / Info'; pred = { param($r) @('low','info') -contains $r.severity -and @('verified','fixed','false_positive') -notcontains $r.status } }
        @{ name = 'Recently Fixed';  pred = { param($r) @('verified','fixed') -contains $r.status } }
        @{ name = 'Closed Other';    pred = { param($r) @('false_positive','mitigated','backlog') -contains $r.status } }
    )
    foreach ($sec in $sections) {
        $items = @($Ledger.regressions | Where-Object (& $sec.pred $_))
        # Defensive: in PS 5.1 the above pattern can quirk; use a more tolerant filter:
        $items = @()
        foreach ($r in @($Ledger.regressions)) {
            if (& $sec.pred $r) { $items += $r }
        }
        [void]$sb.AppendLine('## ' + $sec.name + ' (' + $items.Count + ')')
        [void]$sb.AppendLine('')
        if ($items.Count -eq 0) {
            [void]$sb.AppendLine('_None._')
        } else {
            [void]$sb.AppendLine('| ID | Score | Status | Category | Title / Symptom |')
            [void]$sb.AppendLine('|----|-------|--------|----------|-----------------|')
            foreach ($r in ($items | Sort-Object -Property @{Expression='score';Descending=$true}, id)) {
                $sym = if ($r.PSObject.Properties['first_symptom']) { [string]$r.first_symptom } else { '' }
                $sym = ($sym -replace '\|','\|')
                [void]$sb.AppendLine('| ' + $r.id + ' | ' + $r.score + ' | ' + $r.status + ' | ' + $r.category + ' | ' + $sym + ' |')
            }
        }
        [void]$sb.AppendLine('')
    }
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Set-Content -Path $Path -Value $sb.ToString() -Encoding UTF8
}

function Add-WaggleRegressionFromHardeningGateFailure {
    <#
    .SYNOPSIS
    Convenience helper: call this from the hardening gate driver
    after a failure. Adds (or updates) a regression entry of
    category 'hardening_gate_failure'.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $LedgerPath,
        [Parameter(Mandatory)] [string] $GateName,
        [Parameter(Mandatory)] [string] $IterationId,
        [string] $Symptom = ''
    )
    try {
        $ledger = Get-WaggleRegressionLedger -Path $LedgerPath
        # Stable across iterations on purpose: same gate failing twice
        # should hit the same signature so the hook can dedup.
        $signature = Get-WaggleIssueSignature -IterationIdIntroduced '' -FindingId ('GATE-' + $GateName) -FailingTestOrFile $GateName
        # Find an open entry with matching signature; if exists, increment its history.
        $existing = @($ledger.regressions | Where-Object { $_.PSObject.Properties['issue_signature'] -and [string]$_.issue_signature -eq $signature -and @('verified','fixed','false_positive') -notcontains [string]$_.status })
        if ($existing.Count -gt 0) {
            $regId = [string]$existing[0].id
            try {
                Update-WaggleRegressionEntry -Ledger $ledger -RegId $regId -Update @{
                    history_event = @{ iteration_id = $IterationId; event = 'detected'; issue_signature = $signature; notes = ('repeat hardening_gate_failure: ' + $GateName) }
                } | Out-Null
            } catch {}
        } else {
            $entry = [pscustomobject]@{
                detected_in_iteration = $IterationId
                category = 'hardening_gate_failure'
                first_symptom = if ($Symptom) { $Symptom } else { ('hardening gate failed: ' + $GateName) }
                affected_files = @()
                failing_tests = @($GateName)
                linked_findings = @()
                linked_proposals = @()
                issue_signature = $signature
                score_categories = @('hardening_gate_failure')
            }
            $entry | Add-Member -NotePropertyName score -NotePropertyValue (Get-WaggleRegressionScore -Entry $entry) -Force
            $entry | Add-Member -NotePropertyName severity -NotePropertyValue (_Rl-SeverityFromScore -Score ([int]$entry.score)) -Force
            Add-WaggleRegressionEntry -Ledger $ledger -Entry $entry | Out-Null
        }
        Save-WaggleRegressionLedger -Path $LedgerPath -Ledger $ledger
    } catch {
        # Hooks must NEVER break the host operation.
        Write-Warning ("regression-ledger hook failed: " + $_.Exception.Message)
    }
}

function Add-WaggleRegressionFromInternalFinding {
    <#
    .SYNOPSIS
    Convenience helper for hooks in Invoke-WaggleReview.ps1 on a
    critical/high finding of category security or reliability.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $LedgerPath,
        [Parameter(Mandatory)] [string] $IterationId,
        [Parameter(Mandatory)] [string] $Role,
        [Parameter(Mandatory)] [string] $FindingId,
        [string] $Severity = 'high',
        [string] $Symptom = '',
        [string[]] $AffectedFiles = @()
    )
    try {
        $ledger = Get-WaggleRegressionLedger -Path $LedgerPath
        # Stable across iterations: same finding ID + same first
        # affected file = same signature, so repeat detections dedup.
        $signature = Get-WaggleIssueSignature -IterationIdIntroduced '' -FindingId $FindingId -FailingTestOrFile (($AffectedFiles | Select-Object -First 1) -as [string])
        $existing = @($ledger.regressions | Where-Object { $_.PSObject.Properties['issue_signature'] -and [string]$_.issue_signature -eq $signature -and @('verified','fixed','false_positive') -notcontains [string]$_.status })
        if ($existing.Count -gt 0) {
            $regId = [string]$existing[0].id
            try {
                Update-WaggleRegressionEntry -Ledger $ledger -RegId $regId -Update @{
                    history_event = @{ iteration_id = $IterationId; event = 'detected'; issue_signature = $signature; notes = ('repeat internal critical/high: ' + $FindingId) }
                } | Out-Null
            } catch {}
        } else {
            $catScore = if ($Role -eq 'security') { 'security_redaction' } elseif ($Role -eq 'reliability') { 'lock_state_signal' } else { 'other' }
            $entry = [pscustomobject]@{
                detected_in_iteration = $IterationId
                category = if ($catScore -eq 'security_redaction') { 'security_redaction' } elseif ($catScore -eq 'lock_state_signal') { 'lock_state_signal' } else { 'other' }
                first_symptom = if ($Symptom) { $Symptom } else { ('internal critical/high finding: ' + $FindingId) }
                affected_files = $AffectedFiles
                failing_tests = @()
                linked_findings = @($FindingId)
                linked_proposals = @()
                issue_signature = $signature
                score_categories = @($catScore)
            }
            $entry | Add-Member -NotePropertyName score -NotePropertyValue (Get-WaggleRegressionScore -Entry $entry) -Force
            $entry | Add-Member -NotePropertyName severity -NotePropertyValue (_Rl-SeverityFromScore -Score ([int]$entry.score)) -Force
            Add-WaggleRegressionEntry -Ledger $ledger -Entry $entry | Out-Null
        }
        Save-WaggleRegressionLedger -Path $LedgerPath -Ledger $ledger
    } catch {
        Write-Warning ("regression-ledger hook failed: " + $_.Exception.Message)
    }
}
