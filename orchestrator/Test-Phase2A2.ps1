#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2A-2 integration assertions. Verifies the on-disk artefacts
    + safety properties that the master prompt's DoD requires before
    the PR can be opened. PS 5.1 compatible.
#>
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot

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

# ----------------- required files exist -----------------

$required = @(
    'orchestrator/Invoke-WaggleReview.ps1',
    'orchestrator/Run-WaggleHardeningGates.ps1',
    'orchestrator/Test-Phase2A2.ps1',
    'orchestrator/Test-ReviewAdapter.ps1',
    'orchestrator/Test-ReviewSchema.ps1',
    'orchestrator/Test-ReviewRunner.ps1',
    'orchestrator/lib/review/ReviewAdapter.ps1',
    'orchestrator/lib/review/ReviewSchema.ps1',
    'orchestrator/lib/review/Adapter.Architect.ps1',
    'orchestrator/lib/review/Adapter.Security.ps1',
    'orchestrator/lib/review/Adapter.Reliability.ps1',
    'schemas/review.schema.json',
    'prompts/review/architect.md',
    'prompts/review/security.md',
    'prompts/review/reliability.md',
    'orchestrator.config.review.example.json',
    'docs/design/phase2a2_review_runner_design.md'
)
foreach ($rel in $required) {
    $p = Join-Path $repoRoot $rel
    Assert-True ("required file exists: $rel") (Test-Path -LiteralPath $p) ($p)
}

# ----------------- review schema parses + has key fields -----------------

$schemaPath = Join-Path $repoRoot 'schemas/review.schema.json'
$schema = $null
$ok = $true
try { $schema = Get-Content -Raw -Path $schemaPath -Encoding UTF8 | ConvertFrom-Json } catch { $ok = $false }
Assert-True 'schemas/review.schema.json parses as JSON' $ok
if ($ok -and $schema) {
    Assert-True 'schema has top-level required[]' (@($schema.required).Count -gt 0)
    Assert-True 'schema requires role'                ($schema.required -contains 'role')
    Assert-True 'schema requires target_iteration_id' ($schema.required -contains 'target_iteration_id')
    Assert-True 'schema requires verdict'             ($schema.required -contains 'verdict')
    Assert-True 'schema requires findings'            ($schema.required -contains 'findings')
    Assert-True 'schema requires metrics'             ($schema.required -contains 'metrics')
    Assert-True 'schema requires completed'           ($schema.required -contains 'completed')
    $rolesEnum    = @($schema.properties.role.enum)
    $verdictsEnum = @($schema.properties.verdict.enum)
    Assert-True 'schema role enum complete' (($rolesEnum -contains 'architect') -and ($rolesEnum -contains 'security') -and ($rolesEnum -contains 'reliability'))
    Assert-True 'schema verdict enum complete' (($verdictsEnum -contains 'pass') -and ($verdictsEnum -contains 'fail') -and ($verdictsEnum -contains 'needs_attention') -and ($verdictsEnum -contains 'pass_with_notes'))
}

# ----------------- review config has safe profile -----------------

$cfgPath = Join-Path $repoRoot 'orchestrator.config.review.example.json'
$cfg = Get-Content -Raw -Path $cfgPath -Encoding UTF8 | ConvertFrom-Json

Assert-True 'review config: safeMode=true'                    ($cfg.safeMode -eq $true)
Assert-True 'review config: allowBash=false'                  ($cfg.allowBash -eq $false)
Assert-True 'review config: dangerouslySkipPermissions=false' ($cfg.dangerouslySkipPermissions -eq $false)
Assert-True 'review config: requireUniqueArtifact=false'      ($cfg.requireUniqueArtifact -eq $false)
Assert-True 'review config: sanitizeEnvironment=true'         ($cfg.sanitizeEnvironment -eq $true)
Assert-True 'review config: requireExitMarker=true'           ($cfg.requireExitMarker -eq $true)
Assert-True 'review config: exitMarker=REVIEW-COMPLETE'       ($cfg.exitMarker -eq 'REVIEW-COMPLETE')

$cfgAllowed = @($cfg.allowedTools)
$cfgDisallowed = @($cfg.disallowedTools)
Assert-True 'review config: Bash NOT in allowedTools'  ($cfgAllowed -notcontains 'Bash')
Assert-True 'review config: Write NOT in allowedTools' ($cfgAllowed -notcontains 'Write')
Assert-True 'review config: Edit NOT in allowedTools'  ($cfgAllowed -notcontains 'Edit')
Assert-True 'review config: Read in allowedTools'      ($cfgAllowed -contains 'Read')
Assert-True 'review config: Glob in allowedTools'      ($cfgAllowed -contains 'Glob')
Assert-True 'review config: Grep in allowedTools'      ($cfgAllowed -contains 'Grep')
Assert-True 'review config: Bash in disallowedTools'   ($cfgDisallowed -contains 'Bash')
Assert-True 'review config: Write in disallowedTools'  ($cfgDisallowed -contains 'Write')
Assert-True 'review config: Edit in disallowedTools'   ($cfgDisallowed -contains 'Edit')

# ----------------- normal smoke flow still requires unique artifact by default -----------------

$invIter = Join-Path $repoRoot 'orchestrator/Invoke-WaggleIteration.ps1'
$invIterText = Get-Content -Raw -Path $invIter -Encoding UTF8
Assert-True 'normal smoke: requireUniqueArtifact default true' ($invIterText -match '\$requireUniqueArtifact\s*=\s*\$true')
Assert-True 'normal smoke: SMOKE ARTIFACT CONTRACT block present' ($invIterText -match 'SMOKE ARTIFACT CONTRACT')

# ----------------- ReviewAdapter parses a known-good stdout -----------------

$libDir = Join-Path $PSScriptRoot 'lib\review'
. (Join-Path $libDir 'ReviewAdapter.ps1')

$itid = '2026-05-06_19-45-54'
$goodOut = @"
``````review-json
{
  "role": "architect",
  "target_iteration_id": "$itid",
  "source_package_path": "iterations/$itid/llm_input_package.md",
  "summary": "ok",
  "verdict": "pass",
  "findings": [],
  "metrics": { "files_reviewed": 1, "lines_reviewed": 1, "review_duration_seconds": 1 },
  "completed": true
}
``````

REVIEW-COMPLETE
"@
$res = Invoke-WaggleReviewParseAndValidate -StdoutText $goodOut -ExpectedRole 'architect' -ExpectedIterationId $itid
Assert-True 'ReviewAdapter parses known-good stdout' ($res.ok -and $res.errors.Count -eq 0) ($res.errors -join '; ')

# ----------------- Invoke-WaggleReview dry run works -----------------

. (Join-Path $PSScriptRoot 'Invoke-WaggleReview.ps1')

$tmp = Join-Path $env:TEMP ('waggle-test-phase2a2-' + [guid]::NewGuid().ToString('N'))
[void](New-Item -ItemType Directory -Path $tmp -Force)
try {
    $iterDir = Join-Path (Join-Path $tmp 'iterations') $itid
    [void](New-Item -ItemType Directory -Path $iterDir -Force)
    Set-Content -Path (Join-Path $iterDir 'llm_input_package.md') -Value 'dry-run package' -Encoding UTF8
    [void](New-Item -ItemType Directory -Path (Join-Path $tmp 'state') -Force)
    $tplDest = Join-Path $tmp 'prompts\review'
    [void](New-Item -ItemType Directory -Path $tplDest -Force)
    Copy-Item -Path (Join-Path $repoRoot 'prompts/review/*.md') -Destination $tplDest

    $liveCfg = [ordered]@{
        projectRoot=$tmp; transcriptDir='transcripts'; iterationsDir='iterations'; stateDir='state'
        reportFile='r.md'; executionMode='print'; claudeCommand='claude'; model='opus'; outputFormat='text'
        maxTurns=10; permissionMode='default'; safeMode=$false; allowBash=$true
        allowedTools=@('Read','Write','Edit','Glob','Grep','Bash'); disallowedTools=@()
        dangerouslySkipPermissions=$true; sanitizeEnvironment=$true; envDenylist=$null; envAllowList=@()
        killOnInteractivePrompt=$true; runnerPollSeconds=1; tailLineCount=1000; fullTranscriptMaxBytes=10485760
        pollIntervalSeconds=1; stableThresholdSeconds=5; runTimeoutMinutes=1
        llmPackageMaxChars=200000; perSectionMaxChars=60000; requireExitMarker=$false
        requireReport=$false; requireClaudeAuthStatus=$false
        interactivePromptPatterns=@(); completedPromptPatterns=@(); exitMarker='##X##'
    }
    $liveCfgPath = Join-Path $tmp 'orchestrator.config.json'
    $liveCfg | ConvertTo-Json -Depth 10 | Set-Content -Path $liveCfgPath -Encoding UTF8

    $r = Invoke-WaggleReview -ConfigPath $liveCfgPath -SourceIterationId $itid -Role 'architect' -DryRun -NoLock
    Assert-True 'Invoke-WaggleReview dry run returns ok' ($r.ok -and $r.dry_run)
    Assert-True 'Invoke-WaggleReview dry run prompt exists' (Test-Path -LiteralPath $r.prompt_path)
    $promptText = Get-Content -Raw -Path $r.prompt_path -Encoding UTF8
    Assert-True 'Invoke-WaggleReview dry run prompt has UNTRUSTED delimiters' ($promptText -match 'UNTRUSTED PACKAGE BEGIN' -and $promptText -match 'UNTRUSTED PACKAGE END')
    Assert-True 'Invoke-WaggleReview dry run prompt has REVIEW-COMPLETE contract' ($promptText -match 'REVIEW-COMPLETE')
} finally {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $tmp
}

# ----------------- no obvious secret patterns in committed templates -----------------

$tokenPatterns = @(
    'gho_',
    'ghp_',
    'github_pat_',
    'Authorization:\s*Bearer ',
    'password\s*=',
    'PRIVATE KEY',
    'GMAIL_APP_PASSWORD\s*='
)
$watchFiles = @(
    'orchestrator.config.review.example.json',
    'prompts/review/architect.md',
    'prompts/review/security.md',
    'prompts/review/reliability.md',
    'docs/design/phase2a2_review_runner_design.md'
)
$secretHits = 0
foreach ($rel in $watchFiles) {
    $p = Join-Path $repoRoot $rel
    if (-not (Test-Path -LiteralPath $p)) { continue }
    $text = Get-Content -Raw -Path $p -Encoding UTF8
    foreach ($pat in $tokenPatterns) {
        if ($text -match $pat) {
            $secretHits++
            # Do NOT print the matched value -- name the file + pattern only.
            Write-Host ('  secret-pattern hit: file=' + $rel + ' pattern=' + $pat) -ForegroundColor Yellow
        }
    }
}
Assert-True 'no token patterns in committed templates/docs' ($secretHits -eq 0)

# ----------------- gitignore unignore policy works for orchestrator/lib -----------------

# We don't assume git is on PATH -- we read .gitignore directly and look
# for the unignore lines we added in P1.
$gi = Get-Content -Raw -Path (Join-Path $repoRoot '.gitignore') -Encoding UTF8
Assert-True '.gitignore has !orchestrator/lib/ unignore'    ($gi -match '!orchestrator/lib/')
Assert-True '.gitignore has !orchestrator/lib/review/ unignore' ($gi -match '!orchestrator/lib/review/')

# ----------------- summary -----------------

Write-Host ''
Write-Host "Result: $Script:Pass/$($Script:Pass + $Script:Fail) tests passed" -ForegroundColor Cyan
if ($Script:Fail -gt 0) { exit 1 } else { exit 0 }
