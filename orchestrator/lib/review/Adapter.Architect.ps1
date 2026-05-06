# Adapter.Architect.ps1
#
# Phase 2A-2 architect role descriptor. Tiny declarative shim;
# all logic lives in ReviewAdapter.ps1.

$ErrorActionPreference = 'Stop'

function Get-WaggleArchitectRoleSpec {
    return [pscustomobject]@{
        role          = 'architect'
        templateFile  = 'architect.md'
        idPrefix      = 'ARCH'
        focusSummary  = 'boundaries, layering, contracts, maintainability, duplication, missing abstractions'
    }
}
