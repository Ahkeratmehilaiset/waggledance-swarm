#requires -Version 5.1
# Phase 2A-2 review-mode fake-claude wrapper. Self-contained: parses
# the prompt's REVIEW METADATA block, emits a schema-valid
# `review-json` block, finishes with REVIEW-COMPLETE.
#
# DO NOT add [CmdletBinding()] -- PowerShell would then interpret the
# orchestrator-supplied `-p` (claude print-mode flag) as the common
# parameter `-PipelineVariable`, which causes the script to fail
# immediately with "Missing an argument for parameter 'PipelineVariable'".
param(
    [Parameter(ValueFromRemainingArguments=$true)] [string[]] $Rest
)
$ErrorActionPreference = 'Continue'
$prompt = [Console]::In.ReadToEnd()

$role = 'architect'
$tid  = 'unknown_iter'
$spp  = 'unknown'
$rm = [regex]::Match($prompt, '(?ms)^- role:\s*`([^`]+)`')
if ($rm.Success) { $role = $rm.Groups[1].Value }
$im = [regex]::Match($prompt, '(?ms)^- target_iteration_id:\s*`([^`]+)`')
if ($im.Success) { $tid = $im.Groups[1].Value }
$sm = [regex]::Match($prompt, '(?ms)^- source_package_path:\s*`([^`]+)`')
if ($sm.Success) { $spp = $sm.Groups[1].Value }

$idPrefix = switch ($role) {
    'architect'   { 'ARCH' }
    'security'    { 'SEC' }
    'reliability' { 'REL' }
    default       { 'ANY' }
}

$reviewObj = @{
    role                 = $role
    target_iteration_id  = $tid
    source_package_path  = $spp
    summary              = "fake-claude review for $role over $tid -- ok."
    verdict              = 'pass_with_notes'
    findings             = @(
        @{
            id                  = "$idPrefix-001"
            severity            = 'low'
            title               = 'fake finding for test harness'
            where               = 'fake-claude-review-success.ps1'
            evidence            = 'this is a placeholder finding emitted by the test harness'
            why_it_matters      = 'the test harness must produce a schema-valid review even with no real reviewer'
            recommended_action  = 'no action -- this is a fake review scenario'
        }
    )
    metrics              = @{ files_reviewed = 1; lines_reviewed = 1; review_duration_seconds = 0 }
    completed            = $true
}
$jsonText = $reviewObj | ConvertTo-Json -Depth 10
Write-Output 'fake-claude: review_success scenario active'
Write-Output '```review-json'
Write-Output $jsonText
Write-Output '```'
Write-Output ''
Write-Output '## Verdict'
Write-Output 'fake-claude review verdict: pass_with_notes'
Write-Output '## Critical issues'
Write-Output '_None._'
Write-Output '## Important issues'
Write-Output '_None._'
Write-Output '## Minor issues'
Write-Output "- $idPrefix-001: fake finding for test harness"
Write-Output '## Evidence references'
Write-Output '- fake-claude-review-success.ps1 (the test harness itself)'
Write-Output '## Suggested next actions'
Write-Output '1. ignore this review; it is fake'
Write-Output '## Confidence'
Write-Output 'low -- this is a test harness, not a real reviewer'
Write-Output ''
Write-Output 'REVIEW-COMPLETE'
exit 0
