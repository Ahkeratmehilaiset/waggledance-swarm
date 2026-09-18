#requires -Version 5.1
function Get-BridgeWorkerClass {
    param([string]$Agent)
    if ($Agent -cin @('codex-lead-1','codex-tools-1','claude-rco-1','claude-rco-2','fable-5')) { return 'active' }
    if ($Agent -ceq 'grok-scout-1') { return 'on_demand' }
    return 'historical'
}
