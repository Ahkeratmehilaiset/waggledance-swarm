#requires -Version 5.1
[CmdletBinding()] param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot 'lib/PathValidation.ps1')

$script:tests = 0; $script:passes = 0; $script:fails = @()
function Pass($n) { $script:tests++; $script:passes++; Write-Host "PASS  $n" -ForegroundColor Green }
function Fail($n, $detail) { $script:tests++; Write-Host "FAIL  $n : $detail" -ForegroundColor Red; $script:fails += $n }

# Valid IDs
$validIds = @(
    '2026-05-06_14-23-00',
    'iter-1',
    'a',
    ('a' * 80),
    'A_B_C.001',
    'iter-2026.05'
)
foreach ($id in $validIds) {
    if (Test-IterationIdValid -Id $id) { Pass "valid: $id" } else { Fail "valid: $id" 'rejected' }
}

# Invalid IDs - should all reject
$invalidIds = @(
    '',
    ' ',
    '..',
    '../etc/passwd',
    'iter/1',
    'iter\1',
    'C:\foo',
    '/abs',
    '\abs',
    ('a' * 81),
    '-leading-dash',
    '.leading-dot',
    'trailing.',
    'iter:1',
    'CON',
    'PRN.txt',
    'aux',
    'a b',
    'a@b',
    'a*b'
)
foreach ($id in $invalidIds) {
    if (Test-IterationIdValid -Id $id) { Fail "invalid: '$id'" 'should have been rejected' }
    else { Pass "invalid: '$id'" }
}

# Path traversal containment
$tmpRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("waggle-pv-" + [Guid]::NewGuid().ToString('N'))
$iterRoot = Join-Path $tmpRoot 'iterations'
New-Item -ItemType Directory -Path $iterRoot -Force | Out-Null
try {
    $folder = Get-SafeIterationFolder -IterationsRoot $iterRoot -IterationId 'safe-id-1'
    if ($folder.StartsWith((Resolve-Path $iterRoot).ProviderPath)) { Pass 'safe folder under root' }
    else { Fail 'safe folder under root' "got: $folder" }

    $threw = $false
    try { Get-SafeIterationFolder -IterationsRoot $iterRoot -IterationId '../escape' | Out-Null } catch { $threw = $true }
    if ($threw) { Pass 'rejects ../escape' } else { Fail 'rejects ../escape' 'no throw' }
}
finally {
    Remove-Item -Path $tmpRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
Write-Host ("Result: {0}/{1} tests passed" -f $script:passes, $script:tests) -ForegroundColor Cyan
if ($script:fails.Count -gt 0) { exit 1 }
exit 0
