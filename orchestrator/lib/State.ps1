# State.ps1
# State constants and small helpers. Pure functions, no I/O.
#
# Phase 1.6:
# - Replace single COMPLETED with a verified/unverified split.
# - Add NEEDS_REVIEW_CONFLICT for incompatible signals.
# - Define which terminal states are AUTO-PROCEED (i.e., Phase 2 may run on them).
#
# Guiding principle: prefer NEEDS_REVIEW over COMPLETED whenever any signal
# is missing or contradicts another. False positive COMPLETED states are
# strictly worse than extra human attention.

Set-StrictMode -Version Latest

$Script:WaggleStates = [ordered]@{
    Idle                = 'IDLE'
    Running             = 'RUNNING'
    NeedsManualAction   = 'NEEDS_MANUAL_ACTION'
    Completed           = 'COMPLETED'
    CompletedUnverified = 'COMPLETED_UNVERIFIED'
    NeedsReviewConflict = 'NEEDS_REVIEW_CONFLICT'
    Failed              = 'FAILED'
    Timeout             = 'TIMEOUT'
}

$Script:TerminalStates = @(
    'COMPLETED',
    'COMPLETED_UNVERIFIED',
    'NEEDS_REVIEW_CONFLICT',
    'FAILED',
    'TIMEOUT',
    'NEEDS_MANUAL_ACTION'
)

# Only this set is safe to auto-proceed from. Everything else requires human review.
$Script:AutoProceedStates = @('COMPLETED')

function Get-WaggleStates {
    [CmdletBinding()]
    param()
    return $Script:WaggleStates
}

function Get-TerminalStates {
    [CmdletBinding()]
    param()
    return $Script:TerminalStates
}

function Get-AutoProceedStates {
    [CmdletBinding()]
    param()
    return $Script:AutoProceedStates
}

function Test-IsTerminalState {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $State)
    return $Script:TerminalStates -contains $State
}

function Test-IsAutoProceedState {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $State)
    return $Script:AutoProceedStates -contains $State
}

function Get-IterationId {
    [CmdletBinding()]
    param()
    return (Get-Date -Format 'yyyy-MM-dd_HH-mm-ss')
}

function Get-UtcIso8601 {
    [CmdletBinding()]
    param([datetime] $At = [datetime]::UtcNow)
    return $At.ToUniversalTime().ToString('o')
}
