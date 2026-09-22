from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "tools" / "CheckedPowerShellResult.ps1"


@pytest.fixture(scope="module", params=("pwsh", "powershell"))
def powershell_executable(request) -> str:
    executable = shutil.which(request.param)
    if executable:
        return executable
    pytest.skip(f"{request.param} is unavailable")


def run_powershell(powershell_executable: str, body: str) -> subprocess.CompletedProcess[str]:
    helper_path = str(HELPER).replace("'", "''")
    script = f"""$ErrorActionPreference = 'Stop'
. '{helper_path}'
{body}
"""
    return subprocess.run(
        [powershell_executable, "-NoProfile", "-NonInteractive", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def assert_powershell_succeeds(powershell_executable: str, body: str) -> None:
    completed = run_powershell(powershell_executable, body)
    assert completed.returncode == 0, (
        f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    )


def test_stale_lastexitcode_rejects_old_guard_but_not_checked_result(
    powershell_executable: str,
) -> None:
    assert_powershell_succeeds(
        powershell_executable,
        """
$global:LASTEXITCODE = 7
$payload = [pscustomobject]@{ passed = $true; value = 'valid' }
$oldGuardRejected = $false
try {
    if ($LASTEXITCODE -ne 0 -or -not $payload.passed) {
        throw 'old guard rejected a valid result because LASTEXITCODE was stale'
    }
}
catch {
    $oldGuardRejected = $true
}
if (-not $oldGuardRejected) { throw 'old guard unexpectedly accepted the result' }
$checked = Invoke-CheckedResult { $payload }
if ($checked.value -ne 'valid') { throw 'checked result did not return the payload' }
""",
    )


def test_valid_results_ignore_stale_lastexitcode_and_accept_dictionary(
    powershell_executable: str,
) -> None:
    assert_powershell_succeeds(
        powershell_executable,
        """
foreach ($staleExitCode in @($null, 0, 7)) {
    $global:LASTEXITCODE = $staleExitCode
    $checked = Invoke-CheckedResult { [pscustomobject]@{ passed = $true; value = 'ok' } }
    if ($checked.value -ne 'ok') { throw 'valid custom object was rejected' }
}
$dictionary = Invoke-CheckedResult { @{ passed = $true; value = 'dictionary' } }
if ($dictionary.value -ne 'dictionary') { throw 'valid dictionary was rejected' }
""",
    )


def test_rejects_malformed_results_errors_and_restores_preference(
    powershell_executable: str,
) -> None:
    assert_powershell_succeeds(
        powershell_executable,
        """
function Assert-Rejected([scriptblock] $Action) {
    $rejected = $false
    try { Invoke-CheckedResult -Action $Action | Out-Null } catch { $rejected = $true }
    if (-not $rejected) { throw 'malformed or failed action was accepted' }
}

$originalPreference = $ErrorActionPreference
$ErrorActionPreference = 'SilentlyContinue'
try {
    $success = Invoke-CheckedResult { [pscustomobject]@{ passed = $true } }
    if (-not $success.passed) { throw 'valid result was rejected' }
    if ($ErrorActionPreference -ne 'SilentlyContinue') { throw 'preference changed after success' }

    Assert-Rejected { }
    Assert-Rejected { $null }
    Assert-Rejected { [pscustomobject]@{ passed = $true }; [pscustomobject]@{ passed = $true } }
    Assert-Rejected { [pscustomobject]@{ value = 'missing passed' } }
    Assert-Rejected { [pscustomobject]@{ passed = 'true' } }
    Assert-Rejected { [pscustomobject]@{ passed = $false } }
    Assert-Rejected { Write-Error 'explicit nonterminating error' -ErrorAction Continue; [pscustomobject]@{ passed = $true } }
    Assert-Rejected { throw 'terminating error' }

    if ($ErrorActionPreference -ne 'SilentlyContinue') { throw 'preference changed after failure' }
}
finally {
    $ErrorActionPreference = $originalPreference
}
""",
    )


def test_saturated_error_history_does_not_hide_callback_error(powershell_executable: str) -> None:
    assert_powershell_succeeds(
        powershell_executable,
        """
1..300 | ForEach-Object { Write-Error seed -ErrorAction SilentlyContinue }
$rejected = $false
try {
    Invoke-CheckedResult {
        Write-Error fresh -ErrorAction Continue
        [pscustomobject]@{ passed = $true }
    } | Out-Null
} catch { $rejected = $true }
if (-not $rejected) { throw 'saturated error history hid callback error' }
""",
    )


def test_corrected_json_validator_call(powershell_executable: str) -> None:
    assert_powershell_succeeds(
        powershell_executable,
        """
$global:LASTEXITCODE = 7
function Test-Validator { '{"passed":true,"round":1}' }
$result = Invoke-CheckedResult { Test-Validator | ConvertFrom-Json }
if ($result.round -ne 1) { throw 'JSON validator result lost' }
""",
    )
