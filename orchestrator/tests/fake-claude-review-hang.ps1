#requires -Version 5.1
# Phase 2A-4 REL-005 fixture: a fake review-mode child that writes
# nothing and sleeps forever (well, 5 minutes). The parent runner
# must time out at TimeoutSeconds and bound the stdout/stderr task
# wait so the orchestrator never hangs.
#
# DO NOT add [CmdletBinding()] -- PowerShell would interpret the
# orchestrator-supplied `-p` (claude print-mode flag) as the common
# parameter -PipelineVariable.
param(
    [Parameter(ValueFromRemainingArguments=$true)] [string[]] $Rest
)
$ErrorActionPreference = 'Continue'
$null = [Console]::In.ReadToEnd()
[Console]::Out.WriteLine('fake-claude: hang scenario starting; writing nothing further and sleeping')
[Console]::Out.Flush()
Start-Sleep -Seconds 300
exit 0
