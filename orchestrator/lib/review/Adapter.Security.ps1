# Adapter.Security.ps1
#
# Phase 2A-2 security role descriptor. Tiny declarative shim;
# all logic lives in ReviewAdapter.ps1.

$ErrorActionPreference = 'Stop'

function Get-WaggleSecurityRoleSpec {
    return [pscustomobject]@{
        role          = 'security'
        templateFile  = 'security.md'
        idPrefix      = 'SEC'
        focusSummary  = 'prompt injection, redaction gaps, secret leakage, path traversal, command injection, environment leaks, tool boundary'
    }
}
