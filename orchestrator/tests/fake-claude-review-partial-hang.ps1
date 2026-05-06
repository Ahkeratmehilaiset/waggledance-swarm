#requires -Version 5.1
# Phase 2A-4 REL-005 fixture: writes a partial review-json fragment to
# stdout, then hangs. The parent runner must capture the partial
# stdout, time out, and not deadlock on ReadToEndAsync.
param(
    [Parameter(ValueFromRemainingArguments=$true)] [string[]] $Rest
)
$ErrorActionPreference = 'Continue'
$null = [Console]::In.ReadToEnd()
[Console]::Out.WriteLine('fake-claude: partial-hang scenario active')
[Console]::Out.WriteLine('```review-json')
[Console]::Out.WriteLine('{ "role": "architect", "target_iteration_id": "X"')
[Console]::Out.Flush()
Start-Sleep -Seconds 300
exit 0
