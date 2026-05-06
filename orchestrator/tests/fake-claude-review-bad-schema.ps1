#requires -Version 5.1
# DO NOT add [CmdletBinding()] -- see fake-claude-review-success.ps1.
param(
    [Parameter(ValueFromRemainingArguments=$true)] [string[]] $Rest
)
$ErrorActionPreference = 'Continue'
$null = [Console]::In.ReadToEnd()

# Has block + marker, but schema-invalid (missing required field).
$bad = @{
    role                 = 'architect'
    target_iteration_id  = 'unknown_iter'
    source_package_path  = 'fake'
    summary              = 'missing verdict'
    findings             = @()
    metrics              = @{ files_reviewed = 0; lines_reviewed = 0; review_duration_seconds = 0 }
    completed            = $true
}
[Console]::Out.WriteLine('```review-json')
[Console]::Out.WriteLine(($bad | ConvertTo-Json -Depth 10))
[Console]::Out.WriteLine('```')
[Console]::Out.WriteLine('')
[Console]::Out.WriteLine('REVIEW-COMPLETE')
[Console]::Out.Flush()
exit 0
