#requires -Version 5.1
<#
.SYNOPSIS
    Phase 2A-1 P2 hardening: extra redaction tests focused on the contextual
    git-SHA allowlist. The legacy Test-Redactor.ps1 covers the broad redaction
    contract; this file isolates the SHA preserve/redact split so future
    regressions are obvious.
#>
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$libDir = Join-Path $PSScriptRoot 'lib'
. (Join-Path $libDir 'Redactor.ps1')

$script:tests = 0; $script:passes = 0; $script:fails = @()
function _t([string]$name, [bool]$cond, [string]$detail = '') {
    $script:tests++
    if ($cond) { $script:passes++; Write-Host "PASS  $name" -ForegroundColor Green }
    else { Write-Host "FAIL  $name $detail" -ForegroundColor Red; $script:fails += $name }
}

# --- Phase 2A-1 P2: SHA preservation in known git contexts ----------------

$sha40 = '7210a7e012345678901234567890123456789abc' # 40 hex
_t 'fixture is exactly 40 hex chars' ($sha40.Length -eq 40 -and $sha40 -match '^[0-9a-fA-F]{40}$')

# 1. JSON git_metadata.commit must survive
$json1 = '{"branch":"main","commit":"' + $sha40 + '","is_dirty":false}'
$r = Invoke-WaggleRedaction -Text $json1
_t 'JSON commit field preserves 40-hex SHA' ($r.text -match [regex]::Escape($sha40))
_t 'JSON commit not mis-redacted as AWS' ($r.text -notmatch 'REDACTED:AWS_SECRET_KEY')

# 2. JSON sha field must survive
$json2 = '{"name":"v1","sha":"' + $sha40 + '"}'
$r = Invoke-WaggleRedaction -Text $json2
_t 'JSON sha field preserves SHA' ($r.text -match [regex]::Escape($sha40))

# 3. headRefOid (GitHub PR API)
$json3 = '{"headRefOid":"' + $sha40 + '"}'
$r = Invoke-WaggleRedaction -Text $json3
_t 'headRefOid preserves SHA' ($r.text -match [regex]::Escape($sha40))

# 4. mergeCommit nested oid (GitHub PR API)
$json4 = '{"mergeCommit":{"oid":"' + $sha40 + '"}}'
$r = Invoke-WaggleRedaction -Text $json4
_t 'mergeCommit.oid preserves SHA' ($r.text -match [regex]::Escape($sha40))

# 5. YAML-ish field
$yaml = "branch: main`ncommit: $sha40`nis_dirty: false"
$r = Invoke-WaggleRedaction -Text $yaml
_t 'YAML commit: preserves SHA' ($r.text -match [regex]::Escape($sha40))

# 6. git log line "commit <sha>"
$logLine = "commit $sha40`nAuthor: Jani"
$r = Invoke-WaggleRedaction -Text $logLine
_t 'git log line preserves SHA' ($r.text -match [regex]::Escape($sha40))

# 7. KV style
$kv = "ref=main, commit=$sha40, dirty=no"
$r = Invoke-WaggleRedaction -Text $kv
_t 'kv commit= preserves SHA' ($r.text -match [regex]::Escape($sha40))

# 8. targetCommitish JSON
$json5 = '{"targetCommitish":"' + $sha40 + '"}'
$r = Invoke-WaggleRedaction -Text $json5
_t 'targetCommitish preserves SHA' ($r.text -match [regex]::Escape($sha40))

# --- Phase 2A-1 P2: real secrets must STILL redact ------------------------

# 9. gho_ token redacted (either GITHUB_OAUTH or GITHUB_PAT class is OK;
# the GITHUB_PAT pattern gh[psouri]_ accepts 'o' so it may match first.
# What matters is that the bearer value is gone and SOME redaction fires.)
$gho = 'gho_' + ('a' * 40)
$r = Invoke-WaggleRedaction -Text "tok=$gho end"
_t 'gho_ token still redacted' (
    ($r.text -match 'REDACTED:GITHUB_OAUTH') -or ($r.text -match 'REDACTED:GITHUB_PAT')
)
_t 'gho_ value gone' ($r.text -notmatch [regex]::Escape($gho))

# 10. classic GitHub PAT (ghp_) — 36-char body
# Phase 2B-R3 P10 (Codex SEC-002 fix): the comment used to claim
# "github_pat_ token redacted" but the fixture was always a classic
# ghp_ token. Comment corrected; fine-grained github_pat_ coverage
# is exercised separately in Test-Redactor.ps1 (PROP-002 fix).
$pat = 'ghp_' + ('a' * 40)
$r = Invoke-WaggleRedaction -Text "Authorization: token $pat"
_t 'ghp_ token still redacted' ($r.text -match 'REDACTED:GITHUB_PAT')
_t 'ghp_ value gone' ($r.text -notmatch [regex]::Escape($pat))

# 11. Authorization: Bearer (synthetic; literal split to avoid scanner FPs)
$bearerVal = 'abcdefghijklmnopqrstuvwxyz' + '0123456789'
$bearer = ('Authorization' + ': ' + 'Bearer ') + $bearerVal
$r = Invoke-WaggleRedaction -Text $bearer
_t 'Bearer still redacted' ($r.text -match 'REDACTED:BEARER_TOKEN')

# 12. password= (synthetic)
$pwLine = 'DATABASE_' + 'PASSWORD' + '=hunter2hunter'
$r = Invoke-WaggleRedaction -Text $pwLine
_t 'password= still redacted' ($r.text -notmatch 'hunter2hunter')

# 13. private key block (synthetic; PEM body is x-padding, not real)
$pkBegin = '-----BEGIN ' + 'RSA PRIVATE KEY-----'
$pkEnd   = '-----END ' + 'RSA PRIVATE KEY-----'
$pkBody  = 'MIIEpAIBAAKCAQEA' + ('x' * 31)
$pk = $pkBegin + "`n" + $pkBody + "`n" + $pkEnd
$r = Invoke-WaggleRedaction -Text $pk
_t 'private key block redacted' ($r.text -match 'REDACTED:PRIVATE_KEY')
_t 'private key body gone' ($r.text -notmatch 'MIIEpAIBAAKCAQEA')

# 14. Bare suspicious 40-char base64-ish secret in NON-git context still redacts.
# Use a string that does NOT start with AKIA (else AWS_ACCESS_KEY fires first)
# and is not 40 hex (else it would also pass the SHA shape — the test point
# is that AWS_SECRET_KEY's broad class still catches non-SHA tokens).
$bare = 'X9q3Lp+VuT=mYn7Sk/oP0aGzCdQbFhJrEi8Wt1Az'  # 40 chars, mixed b64-ish
_t 'fixture is 40 chars and not pure hex' (
    $bare.Length -eq 40 -and ($bare -notmatch '^[0-9a-fA-F]{40}$')
)
$nonGitContext = "Some opaque blob: $bare end."
$r = Invoke-WaggleRedaction -Text $nonGitContext
_t 'Bare 40-char non-SHA in plain text is treated as AWS secret' ($r.text -match 'REDACTED:AWS_SECRET_KEY')

# 15. Ordinary text unchanged (no SHA, no secret)
$plain = 'Hello world this is a normal log line with no secret.'
$r = Invoke-WaggleRedaction -Text $plain
_t 'Plain text not redacted' ($r.text -notmatch 'REDACTED')
_t 'Plain text byte-identical' ($r.text -eq $plain)

# 16. Redaction report contains COUNTS only (never raw values)
$mixBearer = ('Authorization' + ': ' + 'Bearer ') + ('abcdefghijklmnopqrstuvwxyz' + '0123456789')
$mix = "commit: $sha40`n" + $mixBearer
$r = Invoke-WaggleRedaction -Text $mix
$json = $r.report | ConvertTo-Json -Depth 5
_t 'Report does NOT contain SHA literal' ($json -notmatch [regex]::Escape($sha40))
_t 'Report does NOT contain bearer body' ($json -notmatch 'abcdefghijklmnopqrstuvwxyz0123456789')

# 17. AWS_SECRET_KEY count NOT incremented for SHA in commit field
$justSha = '{"commit":"' + $sha40 + '"}'
$r = Invoke-WaggleRedaction -Text $justSha
_t 'AWS_SECRET_KEY count is 0 when only a SHA is present in a git field' (
    [int]$r.report.AWS_SECRET_KEY -eq 0
)

# 18. Sentinel restoration is exact (no leftover sentinel residue)
$json6 = '{"commit":"' + $sha40 + '","sha":"' + $sha40 + '"}'
$r = Invoke-WaggleRedaction -Text $json6
_t 'No sentinel residue in output' ($r.text -notmatch 'WAGGLE_GIT_SHA_')
_t 'Both SHA fields restored' (([regex]::Matches($r.text, [regex]::Escape($sha40))).Count -eq 2)

Write-Host ""
Write-Host ("Result: {0}/{1} tests passed" -f $script:passes, $script:tests) -ForegroundColor Cyan
if ($script:fails.Count -gt 0) { exit 1 }
exit 0
