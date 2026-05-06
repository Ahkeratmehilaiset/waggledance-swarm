# Redactor.ps1
# Strip credentials/secrets out of text before LLM-bound packaging.
#
# Phase 1.6 additions:
#  - sk-proj-* style keys
#  - Slack tokens (xox[apbsr]-...)
#  - Stripe keys
#  - Google API keys (AIza...)
#  - .env-style heuristics widened
#  - Optional email & Windows-path masking (off by default; set EnableOptional)
#  - Long-base64 heuristic (off by default unless EnableLongBase64)
#  - The redaction report only contains COUNTS, never the matched text.
#
# Compatible with PowerShell 5.1.

Set-StrictMode -Version Latest

$Script:DefaultRedactionRules = @(
    @{ name = 'ANTHROPIC_KEY';   pattern = 'sk-ant-(?:api\d{2}-)?[A-Za-z0-9_\-]{20,}' }
    @{ name = 'OPENAI_PROJ_KEY'; pattern = 'sk-proj-[A-Za-z0-9_\-]{20,}' }
    @{ name = 'OPENAI_KEY';      pattern = 'sk-[A-Za-z0-9]{32,}' }
    @{ name = 'GITHUB_PAT';      pattern = 'gh[psouri]_[A-Za-z0-9]{36,}' }
    @{ name = 'GITHUB_OAUTH';    pattern = 'gho_[A-Za-z0-9]{36,}' }
    @{ name = 'SLACK_TOKEN';     pattern = 'xox[apbsr]-[A-Za-z0-9-]{10,}' }
    @{ name = 'STRIPE_KEY';      pattern = '(?:rk|sk|pk)_(?:live|test)_[A-Za-z0-9]{20,}' }
    @{ name = 'GOOGLE_API_KEY';  pattern = 'AIza[0-9A-Za-z_\-]{35}' }
    @{ name = 'AWS_ACCESS_KEY';  pattern = 'AKIA[0-9A-Z]{16}' }
    @{ name = 'AWS_SECRET_KEY';  pattern = '(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])' }
    @{ name = 'JWT';             pattern = 'eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}' }
    @{ name = 'BEARER_TOKEN';    pattern = '(?i)(authorization\s*[:=]\s*)?bearer\s+[A-Za-z0-9._\-]{20,}' }
    @{ name = 'BASIC_AUTH';      pattern = '(?i)basic\s+[A-Za-z0-9+/=]{20,}' }
    @{ name = 'PASSWORD_KV';     pattern = '(?i)(password|passwd|pwd)\s*[=:]\s*["'']?[^\s"''<>]{4,}' }
    @{ name = 'API_KEY_KV';      pattern = '(?i)(api[_\-]?key|api[_\-]?token|access[_\-]?token|secret[_\-]?key)\s*[=:]\s*["'']?[^\s"''<>]{8,}' }
    @{ name = 'ENV_KV_SECRET';   pattern = '(?im)^[A-Z][A-Z0-9_]*_(?:KEY|TOKEN|SECRET|PASSWORD|PWD|CREDENTIAL|CREDENTIALS|AUTH)\s*=\s*[^\s]+' }
    @{ name = 'COOKIE_HEADER';   pattern = '(?i)cookie:\s*[^\r\n]+' }
    @{ name = 'SET_COOKIE';      pattern = '(?i)set-cookie:\s*[^\r\n]+' }
    @{ name = 'PRIVATE_KEY';     pattern = '-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----' }
)

$Script:OptionalRedactionRules = @(
    @{ name = 'EMAIL';           pattern = '[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}' }
    @{ name = 'WINDOWS_PATH';    pattern = '[A-Z]:\\Users\\[^\s"''<>]+' }
)

$Script:LongBase64Rule = @{ name = 'LONG_BASE64'; pattern = '(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{120,}(?![A-Za-z0-9/+=])' }

# Contextual git SHA allowlist (Phase 2A-1 P2 hardening).
# A 40-hex git SHA is a strict subset of the AWS_SECRET_KEY character class
# and was being redacted as a false positive (e.g. git_metadata.commit
# became "[REDACTED:AWS_SECRET_KEY]"). To keep the AWS detector strong,
# we preserve only SHAs that appear in known SHA-bearing FIELD CONTEXTS,
# not all 40-hex strings everywhere.
#
# Each rule has a regex with a single capture group that points at the
# 40-hex value. We rewrite the SHA to a sentinel before redaction runs,
# then restore the SHA after redaction. The sentinel is chosen so it
# cannot itself match any default rule.
$Script:GitShaSentinelPrefix = "@@WAGGLE_GIT_SHA_"
$Script:GitShaSentinelSuffix = "@@"

# Field names that legitimately carry a 40-hex commit/oid value.
# Matches JSON-ish, YAML-ish, and bare prefix forms:
#   "commit": "abc...", commit=abc..., commit: abc..., git_metadata.commit "abc..."
$Script:GitShaContextRules = @(
    @{ name = 'JSON_FIELD';   pattern = '("(?:commit|sha|oid|headRefOid|targetCommitish|target)"\s*:\s*")([0-9a-fA-F]{40})(")' }
    @{ name = 'JSON_NESTED';  pattern = '("(?:mergeCommit|target|object)"\s*:\s*\{[^}]*?"oid"\s*:\s*")([0-9a-fA-F]{40})(")' }
    @{ name = 'YAML_FIELD';   pattern = '(?im)^(\s*(?:commit|sha|oid|headRefOid|targetCommitish|target)\s*:\s*)([0-9a-fA-F]{40})(\s*$)' }
    @{ name = 'KV_FIELD';     pattern = '(?i)((?:^|[\s,;])(?:commit|sha|oid|headRefOid|targetCommitish)\s*=\s*)([0-9a-fA-F]{40})(?![0-9a-fA-F])' }
    @{ name = 'GIT_LOG_LINE'; pattern = '(?im)^(commit\s+)([0-9a-fA-F]{40})(\b)' }
    @{ name = 'BARE_SHA_TAG'; pattern = '(?i)((?:^|\s)sha[:=]\s*)([0-9a-fA-F]{40})(?![0-9a-fA-F])' }
)

function Protect-GitShaContexts {
    <#
    .SYNOPSIS
    Replace SHA-bearing values inside known git fields with sentinels so
    the redaction pass leaves them alone. Returns the mutated text plus a
    map of sentinel->original-sha for restoration.

    The SHA-bearing capture group is identified by content (40 hex chars)
    rather than by group index, so patterns can have any number of head /
    tail capture groups without breaking restoration.
    #>
    [CmdletBinding()]
    param([Parameter(Mandatory)] [AllowEmptyString()] [string] $Text)

    $map = @{}
    $current = $Text
    # Local copies of the prefix / suffix so they survive into the
    # MatchEvaluator scriptblock via GetNewClosure(). $Script: scope is
    # not reliably visible inside a delegate-bound scriptblock.
    $prefix = $Script:GitShaSentinelPrefix
    $suffix = $Script:GitShaSentinelSuffix
    foreach ($rule in $Script:GitShaContextRules) {
        $eval = {
            param($m)
            $shaGroup = $null
            for ($k = 1; $k -lt $m.Groups.Count; $k++) {
                $val = $m.Groups[$k].Value
                if ($val.Length -eq 40 -and $val -match '^[0-9a-fA-F]{40}$') {
                    $shaGroup = $m.Groups[$k]
                    break
                }
            }
            if ($null -eq $shaGroup) { return $m.Value }
            $sentinel = $prefix + [string]$map.Count + $suffix
            $map[$sentinel] = $shaGroup.Value
            $relStart = $shaGroup.Index - $m.Index
            $head = $m.Value.Substring(0, $relStart)
            $tail = $m.Value.Substring($relStart + $shaGroup.Length)
            return $head + $sentinel + $tail
        }.GetNewClosure()
        $current = [regex]::Replace($current, $rule.pattern, $eval, 'IgnoreCase, Multiline')
    }
    return [pscustomobject]@{ text = $current; map = $map }
}

function Restore-GitShaContexts {
    <#
    .SYNOPSIS
    Inverse of Protect-GitShaContexts. Restore SHAs.
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $Text,
        [Parameter(Mandatory)] [hashtable] $Map
    )
    $out = $Text
    foreach ($k in $Map.Keys) {
        $out = $out.Replace($k, $Map[$k])
    }
    return $out
}

function Get-DefaultRedactionRules { return ,$Script:DefaultRedactionRules }

function Invoke-WaggleRedaction {
    <#
    .SYNOPSIS
    Redacts secrets from text. Returns @{ text; report } where report has
    only COUNT integers, never raw values.

    .PARAMETER Text
    Text to scan.
    .PARAMETER Rules
    Override the default rules.
    .PARAMETER ExtraPatterns
    Additional rules ON TOP of defaults.
    .PARAMETER EnableOptional
    Adds email + Windows-path masking.
    .PARAMETER EnableLongBase64
    Adds the LONG_BASE64 catch-all (false-positive heavy; off by default).
    #>
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)] [AllowEmptyString()] [string] $Text,
        [object[]] $Rules = $null,
        [object[]] $ExtraPatterns = @(),
        [switch]   $EnableOptional,
        [switch]   $EnableLongBase64
    )

    $allRules = @()
    if ($null -eq $Rules) { $allRules += $Script:DefaultRedactionRules } else { $allRules += $Rules }
    if ($EnableOptional)   { $allRules += $Script:OptionalRedactionRules }
    if ($EnableLongBase64) { $allRules += $Script:LongBase64Rule }
    if ($ExtraPatterns)    { $allRules += $ExtraPatterns }

    # Phase 2A-1 P2: protect SHA-bearing git fields with sentinels BEFORE
    # counting/redacting. Sentinels do not match any default rule, so
    # AWS_SECRET_KEY (and friends) cannot eat them. SHAs are restored at
    # the end. Counts are taken on the protected text so the AWS_SECRET_KEY
    # report number reflects the post-protection truth, not the false
    # positive.
    $protected = Protect-GitShaContexts -Text $Text
    $textForScan = $protected.text

    $report = [ordered]@{}
    foreach ($rule in $allRules) {
        $matches = [regex]::Matches($textForScan, $rule.pattern, 'IgnoreCase, Multiline')
        $report[$rule.name] = $matches.Count
    }

    $redacted = $textForScan
    foreach ($rule in $allRules) {
        $literal = "[REDACTED:$($rule.name)]"
        $redacted = [regex]::Replace($redacted, $rule.pattern, $literal, 'IgnoreCase, Multiline')
    }

    $redacted = Restore-GitShaContexts -Text $redacted -Map $protected.map

    return [pscustomobject]@{
        text   = $redacted
        report = [pscustomobject]$report
    }
}

function Save-RedactionReport {
    [CmdletBinding()]
    param([Parameter(Mandatory)] [string] $Path, [Parameter(Mandatory)] $Report)
    $dir = Split-Path -Parent $Path
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $obj = [pscustomobject]@{
        generated_at = (Get-Date).ToUniversalTime().ToString('o')
        counts       = $Report
    }
    $obj | ConvertTo-Json -Depth 5 | Set-Content -Path $Path -Encoding UTF8
}
