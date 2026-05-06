#requires -Version 5.1
# DO NOT add [CmdletBinding()] -- see fake-claude-review-success.ps1.
param(
    [Parameter(ValueFromRemainingArguments=$true)] [string[]] $Rest
)
$ErrorActionPreference = 'Continue'
$null = [Console]::In.ReadToEnd()

[Console]::Out.WriteLine('I forgot to emit a review-json block.')
[Console]::Out.WriteLine('')
[Console]::Out.WriteLine('REVIEW-COMPLETE')
[Console]::Out.Flush()
exit 0
