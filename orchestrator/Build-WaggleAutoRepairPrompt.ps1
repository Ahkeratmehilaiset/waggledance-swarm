#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2B-Revision (REL-014): generate a tightly-constrained
    auto-repair prompt for a TRIVIAL_AUTO_FIX or LOCAL_REPAIR
    finding.

    The output prompt strictly limits scope: fix only the named
    finding, do not refactor unrelated code, cap at MAX_FILES,
    add a test that proves the fix, escalate via
    `repair_escalated.txt` if scope grows.
#>
[CmdletBinding()]
param(
    [string] $ConfigPath = '',
    [string] $FindingId = '',
    [string] $EpochId = '',
    [string] $IterationId = '',
    [string] $OutputPromptPath = '',
    [string] $RepairClass = 'LOCAL_REPAIR',
    [string] $Severity = 'medium',
    [string] $Fixability = 'clear',
    [string] $Title = '',
    [string] $Where = '',
    [string] $Evidence = '',
    [int]    $RepairAttemptIndex = 1,
    [string] $TargetTestFile = ''
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib/external_review/FindingClassifier.ps1')

function Build-WaggleAutoRepairPrompt {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [string] $ConfigPath,
        [Parameter(Mandatory)] [string] $FindingId,
        [Parameter(Mandatory)] [string] $EpochId,
        [Parameter(Mandatory)] [string] $IterationId,
        [Parameter(Mandatory)] [string] $RepairClass,
        [Parameter(Mandatory)] [string] $Severity,
        [Parameter(Mandatory)] [string] $Fixability,
        [string] $Title = '',
        [string] $Where = '',
        [string] $Evidence = '',
        [int]    $RepairAttemptIndex = 1,
        [string] $TargetTestFile = '',
        [string] $OutputPromptPath = ''
    )
    if (-not (Test-Path -LiteralPath $ConfigPath)) { throw "config not found: $ConfigPath" }
    $cfg = Get-Content -Raw -Path $ConfigPath -Encoding UTF8 | ConvertFrom-Json
    $clf = Get-WaggleFindingClassifierConfig -Config $cfg

    $maxFiles = switch ($RepairClass) {
        'TRIVIAL_AUTO_FIX' { [int]$clf.max_files_for_trivial_auto_fix }
        'LOCAL_REPAIR'     { [int]$clf.max_files_for_local_repair }
        default            { [int]$clf.max_files_for_local_repair }
    }

    $sb = New-Object System.Text.StringBuilder
    [void]$sb.AppendLine('You are running a LOCAL REPAIR ITERATION inside a WaggleDance epoch.')
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('REPAIR SCOPE - NARROW')
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('Fix only this specific finding:')
    [void]$sb.AppendLine('- Finding ID: ' + $FindingId)
    [void]$sb.AppendLine('- Title: ' + $Title)
    [void]$sb.AppendLine('- Where: ' + $Where)
    [void]$sb.AppendLine('- Severity: ' + $Severity)
    [void]$sb.AppendLine('- Fixability classification: ' + $Fixability)
    [void]$sb.AppendLine('- Repair class: ' + $RepairClass)
    [void]$sb.AppendLine('- Repair attempt index: ' + $RepairAttemptIndex)
    [void]$sb.AppendLine('- Max files this iteration may touch: ' + $maxFiles)
    [void]$sb.AppendLine('')
    if ($Evidence) {
        [void]$sb.AppendLine('Evidence:')
        [void]$sb.AppendLine('```text')
        [void]$sb.AppendLine($Evidence)
        [void]$sb.AppendLine('```')
        [void]$sb.AppendLine('')
    }
    [void]$sb.AppendLine('Rules (HARD):')
    [void]$sb.AppendLine('1. Fix ONLY ' + $FindingId + '. Do not refactor unrelated code.')
    [void]$sb.AppendLine('2. Do not add new features.')
    [void]$sb.AppendLine('3. Do not change public behavior except as required to fix this regression.')
    [void]$sb.AppendLine('4. Add or update at least one test that proves this fix.')
    [void]$sb.AppendLine('5. After making the change:')
    [void]$sb.AppendLine('   a. Run the specific failing test or gate that produced this finding')
    if ($TargetTestFile) {
        [void]$sb.AppendLine('   b. Run the relevant Test-* file (Test-' + $TargetTestFile + ')')
    } else {
        [void]$sb.AppendLine('   b. Run the relevant Test-* file targeting the affected surface')
    }
    [void]$sb.AppendLine('   c. Confirm both pass')
    [void]$sb.AppendLine('6. If the fix would require touching more than ' + $maxFiles + ' files, STOP and write `iterations/' + $IterationId + '/repair_escalated.txt` with reason: "exceeded max_files_for_' + $RepairClass.ToLower() + '".')
    [void]$sb.AppendLine('7. If the fix would require new dependencies, STOP and write `repair_escalated.txt` with reason "dependency_required".')
    [void]$sb.AppendLine('8. If during the fix you discover that the finding''s diagnosis was wrong, STOP and write `repair_escalated.txt` with reason "diagnosis_incorrect" plus your alternative diagnosis.')
    [void]$sb.AppendLine('9. Update the regression ledger entry for ' + $FindingId + ':')
    [void]$sb.AppendLine('   - status: fix_attempted (when fix is in place)')
    [void]$sb.AppendLine('   - status: verification_pending (when test passes locally)')
    [void]$sb.AppendLine('   - history event: attempted_fix with repair_attempt_index = ' + $RepairAttemptIndex)
    [void]$sb.AppendLine('10. Write a brief raportti.md describing exactly what was changed and which test verifies it.')
    [void]$sb.AppendLine('11. The next iteration after this one is automatically a verification iteration. Do not preempt that work in this iteration.')
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('DO NOT:')
    [void]$sb.AppendLine('- Add features')
    [void]$sb.AppendLine('- Refactor unrelated code')
    [void]$sb.AppendLine('- Touch core files (Phase 2A-2/2A-3/2A-4/2A-5 frozen list)')
    [void]$sb.AppendLine('- Modify orchestrator.config.json')
    [void]$sb.AppendLine('- Push to remote')
    [void]$sb.AppendLine('- Open PR')
    [void]$sb.AppendLine('- Create tag or release')
    [void]$sb.AppendLine('')
    [void]$sb.AppendLine('SCOPE LIMIT: at most ' + $maxFiles + ' files in the diff. Anything more is an escalation, not a repair.')

    $body = $sb.ToString()
    if ($OutputPromptPath) {
        $dir = Split-Path -Parent $OutputPromptPath
        if ($dir -and -not (Test-Path -LiteralPath $dir)) {
            New-Item -ItemType Directory -Path $dir -Force | Out-Null
        }
        Set-Content -Path $OutputPromptPath -Value $body -Encoding UTF8
    }
    return [pscustomobject]@{
        ok = $true
        finding_id = $FindingId
        repair_class = $RepairClass
        max_files = $maxFiles
        prompt_text = $body
        prompt_path = $OutputPromptPath
        repair_attempt_index = $RepairAttemptIndex
    }
}

# CLI wrapper
if ($MyInvocation.InvocationName -ne '.' -and $ConfigPath -and $FindingId -and $EpochId -and $IterationId) {
    if (-not $OutputPromptPath) {
        $OutputPromptPath = Join-Path (Join-Path (Split-Path -Parent $ConfigPath) 'iterations') (Join-Path $IterationId ('auto_repair_prompt_' + $FindingId + '.md'))
    }
    $r = Build-WaggleAutoRepairPrompt -ConfigPath $ConfigPath -FindingId $FindingId `
            -EpochId $EpochId -IterationId $IterationId -RepairClass $RepairClass `
            -Severity $Severity -Fixability $Fixability -Title $Title -Where $Where `
            -Evidence $Evidence -RepairAttemptIndex $RepairAttemptIndex `
            -TargetTestFile $TargetTestFile -OutputPromptPath $OutputPromptPath
    if ($r.ok) {
        Write-Host ('Auto-repair prompt built: ' + $r.prompt_path)
        exit 0
    } else {
        exit 1
    }
}
