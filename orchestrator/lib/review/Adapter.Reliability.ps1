# Adapter.Reliability.ps1
#
# Phase 2A-2 reliability role descriptor. Tiny declarative shim;
# all logic lives in ReviewAdapter.ps1.

$ErrorActionPreference = 'Stop'

function Get-WaggleReliabilityRoleSpec {
    return [pscustomobject]@{
        role          = 'reliability'
        templateFile  = 'reliability.md'
        idPrefix      = 'REL'
        focusSummary  = 'crash modes, timeout behavior, lock contention, stale artifact risk, resume behavior, idempotency, partial state recovery'
    }
}
