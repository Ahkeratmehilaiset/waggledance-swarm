#requires -Version 5.1
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

# Synthetic test fixtures. Token-shaped literals are split across string
# concats so GitHub's secret scanner doesn't flag them as real
# credentials. The runtime values still match the redactor's regexes.

# Anthropic key
$ant = 'sk-' + 'ant-api03-' + ('A' * 44)
$r = Invoke-WaggleRedaction -Text "before $ant after"
_t 'Anthropic key marker present' ($r.text -match 'REDACTED:ANTHROPIC_KEY')
_t 'Anthropic key value gone'    ($r.text -notmatch [regex]::Escape($ant))

# OpenAI sk-proj
$proj = 'sk-' + 'proj-' + ('a' * 37)
$r = Invoke-WaggleRedaction -Text "K: $proj"
_t 'sk-proj key redacted' ($r.text -match 'REDACTED')
_t 'sk-proj value gone'    ($r.text -notmatch [regex]::Escape($proj))

# OpenAI sk- generic
$openai = 'sk-' + ('a' * 40)
$r = Invoke-WaggleRedaction -Text $openai
_t 'OpenAI sk- key redacted' ($r.text -match 'REDACTED')

# JWT (synthetic three-segment shape)
$jwtH = 'eyJ' + 'hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9'
$jwtP = 'eyJ' + 'zdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkphbmkifQ'
$jwtS = 'SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c'
$jwt = $jwtH + '.' + $jwtP + '.' + $jwtS
$r = Invoke-WaggleRedaction -Text "tok: $jwt end"
_t 'JWT marker present' ($r.text -match 'REDACTED:JWT')
_t 'JWT body gone'      ($r.text -notmatch 'eyJzdWIiOiIxMjM')

# Bearer (synthetic)
$bearerVal = 'abcdefghijklmnopqrstuvwxyz' + '0123456789'
$bearer = ('Authorization' + ': ' + 'Bearer ') + $bearerVal
$r = Invoke-WaggleRedaction -Text $bearer
_t 'Bearer redacted' ($r.text -match 'REDACTED:BEARER_TOKEN')
_t 'Bearer value gone' ($r.text -notmatch [regex]::Escape($bearerVal))

# GitHub PAT (synthetic)
$pat = 'ghp_' + ('a' * 40)
$r = Invoke-WaggleRedaction -Text "GH: $pat"
_t 'GitHub PAT redacted' ($r.text -match 'REDACTED:GITHUB_PAT')

# Slack (synthetic)
$slack = 'xoxb' + '-' + '1234567890' + '-' + ('a' * 24)
$r = Invoke-WaggleRedaction -Text "S: $slack"
_t 'Slack redacted' ($r.text -match 'REDACTED:SLACK_TOKEN')

# Google API key (already concat)
$gkey = 'AIza' + ('a' * 35)
$r = Invoke-WaggleRedaction -Text "G: $gkey"
_t 'Google API key redacted' ($r.text -match 'REDACTED:GOOGLE_API_KEY')

# AWS access (synthetic)
$awsKey = 'AKIA' + '1234567890ABCDEF'
$r = Invoke-WaggleRedaction -Text "AWS=$awsKey"
_t 'AWS access redacted' ($r.text -match 'REDACTED:AWS_ACCESS_KEY')

# .env-style (synthetic)
$envText = 'DATABASE_' + 'PASSWORD' + '=hunter2hunter' + "`n" + 'MY_' + 'SECRET_KEY' + '=foobar123'
$r = Invoke-WaggleRedaction -Text $envText
_t 'env DATABASE_PASSWORD redacted' ($r.text -match 'REDACTED' -and $r.text -notmatch 'hunter2hunter')
_t 'env MY_SECRET_KEY redacted'     ($r.text -notmatch 'foobar123')

# Cookie
$r = Invoke-WaggleRedaction -Text 'Cookie: session=abcde; tracking=xyz'
_t 'Cookie redacted' ($r.text -match 'REDACTED:COOKIE_HEADER')

# Plain text untouched
$plain = 'Hello world this is normal log line.'
$r = Invoke-WaggleRedaction -Text $plain
_t 'Plain text not redacted' ($r.text -notmatch 'REDACTED')

# Optional: email + Windows path off by default
$r = Invoke-WaggleRedaction -Text 'Contact jani@example.com or C:\Users\jani\file.txt'
_t 'Email NOT redacted by default'  ($r.text -match 'jani@example.com')
_t 'Win path NOT redacted by default' ($r.text -match 'C:\\Users\\jani')

$r = Invoke-WaggleRedaction -Text 'Contact jani@example.com or C:\Users\jani\file.txt' -EnableOptional
_t 'Email redacted with -EnableOptional'  ($r.text -match 'REDACTED:EMAIL')
_t 'Win path redacted with -EnableOptional' ($r.text -match 'REDACTED:WINDOWS_PATH')

# Report contains COUNTS only, never the secret value itself
$r = Invoke-WaggleRedaction -Text "$ant and $jwt"
$json = $r.report | ConvertTo-Json -Depth 5
_t 'Report has ANTHROPIC count >= 1' ($r.report.ANTHROPIC_KEY -ge 1)
_t 'Report has JWT count >= 1'        ($r.report.JWT -ge 1)
_t 'Report JSON does NOT contain secret value' ($json -notmatch [regex]::Escape($ant))
_t 'Report JSON does NOT contain JWT body'      ($json -notmatch 'eyJzdWIi')

# Output text doesn't accidentally retain partial token slices
_t 'Output does not retain Anthropic prefix sk-ant-' ($r.text -notmatch 'sk-ant-A')

Write-Host ""
Write-Host ("Result: {0}/{1} tests passed" -f $script:passes, $script:tests) -ForegroundColor Cyan
if ($script:fails.Count -gt 0) { exit 1 }
exit 0
