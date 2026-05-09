#requires -Version 5.1
<#
.SYNOPSIS
    Send a liveness or heartbeat event to the bridge.

.DESCRIPTION
    Continuity-protocol helper added 2026-05-09. Wraps Write-AgentEvent
    with the liveness / heartbeat event types so an agent can declare
    "I am awake / I just sent a turn / I am about to sleep" without
    rebuilding the parameter set every time.

    Intended use:

      # at session start (right after Read-AgentBridge.ps1)
      .\bin\Send-Liveness.ps1 -Agent claude -State active

      # while running long work, every 60s
      .\bin\Send-Liveness.ps1 -Agent claude -State active `
                              -Message "fix-impl in progress, 30 of 50 pass"

      # at session end
      .\bin\Send-Liveness.ps1 -Agent claude -State sleeping `
                              -Message "Claude turn done; PR #124 pushed"

      # wake another agent
      .\bin\Send-Liveness.ps1 -Agent claude -Wake -To codex `
                              -Severity high `
                              -Message "PR #124 fix branch ready for re-review" `
                              -TaskId "wake-codex-for-pr124-rereview"

    The -Wake switch emits a `wake_request` event instead of a plain
    liveness event. Severity defaults to "medium".
#>
[CmdletBinding(DefaultParameterSetName = 'Liveness')]
param(
    [Parameter(Mandatory)] [ValidateSet('codex','claude','operator','system')] [string] $Agent,

    [Parameter(ParameterSetName = 'Liveness')]
    [ValidateSet('active','sleeping')] [string] $State = 'active',

    [Parameter(ParameterSetName = 'Heartbeat')]
    [switch] $Heartbeat,

    [Parameter(ParameterSetName = 'Wake')]
    [switch] $Wake,

    [string] $Message = '',
    [string] $TaskId = '',
    [string] $To = '',
    [string] $Severity = '',
    [string[]] $Paths = @()
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$writeEventScript = Join-Path $PSScriptRoot 'Write-AgentEvent.ps1'

$type = 'liveness'
$status = $State
if ($Heartbeat) {
    $type = 'heartbeat'
    $status = 'active'
    if (-not $Severity) { $Severity = '' }
}
if ($Wake) {
    $type = 'wake_request'
    if (-not $Severity) { $Severity = 'medium' }
    $status = 'open'
    if (-not $To) {
        throw "wake_request requires -To <agent>"
    }
}

# Default heartbeat / liveness messages so the bridge is readable
# even when the caller passes no -Message.
if (-not $Message) {
    $Message = switch ($type) {
        'liveness'     { "$Agent $State" }
        'heartbeat'    { "$Agent active heartbeat" }
        'wake_request' { "$Agent requesting wake of $To" }
        default        { "$Agent $type" }
    }
}

# Default TaskId so the bridge is groupable per session.
if (-not $TaskId) {
    $TaskId = switch ($type) {
        'liveness'     { "{0}-liveness-{1:yyyy-MM-dd}" -f $Agent, (Get-Date) }
        'heartbeat'    { "{0}-heartbeat-{1:yyyy-MM-dd}" -f $Agent, (Get-Date) }
        'wake_request' { "{0}-wake-{1}-{2:yyyy-MM-dd-HH-mm-ss}" -f `
                            $Agent, $To, (Get-Date) }
        default        { "{0}-{1}-{2:yyyy-MM-dd}" -f $Agent, $type, (Get-Date) }
    }
}

& $writeEventScript `
    -Agent $Agent `
    -Type $type `
    -Status $status `
    -Severity $Severity `
    -To $To `
    -Message $Message `
    -TaskId $TaskId `
    -Paths $Paths
