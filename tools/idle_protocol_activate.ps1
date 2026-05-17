#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $RemainingArgs = @()
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
$script = Join-Path $PSScriptRoot 'idle_protocol_activate.py'
$candidates = @()
if ($env:PYTHON) {
    $candidates += [string] $env:PYTHON
}
$candidates += (Join-Path $repoRoot '.venv\Scripts\python.exe')
$candidates += 'python'

$python = $null
foreach ($candidate in $candidates) {
    if (-not $candidate) {
        continue
    }
    if ($candidate -eq 'python') {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($cmd) {
            $python = $candidate
            break
        }
        continue
    }
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
        $python = $candidate
        break
    }
}

if (-not $python) {
    throw 'No Python interpreter found. Set $env:PYTHON or create .venv\Scripts\python.exe.'
}

& $python $script @RemainingArgs
exit $LASTEXITCODE
