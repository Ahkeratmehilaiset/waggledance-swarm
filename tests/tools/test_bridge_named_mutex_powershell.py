"""Use unique kernel names only; never touch production bridge locks."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHELLS = list(dict.fromkeys(filter(None, [shutil.which("powershell.exe"), shutil.which("pwsh")])))
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows kernel mutex policy")


def run(shell, body):
    helper = str(ROOT / ".agent-bridge/bin/BridgeNamedMutex.ps1").replace("'", "''")
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command",
        "$ErrorActionPreference='Stop'; . '" + helper + "'; Initialize-BridgeNamedMutexType; " + body],
        capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("shell", SHELLS)
def test_creation_sddl_has_no_user_full_control(shell):
    actual = run(shell, "[WaggleDance.BridgeNamedMutexV1]::BuildSddl('S-1-5-21-1-2-3-1001', [string[]]@('S-1-5-5-10-20')) | ConvertTo-Json -Compress")
    assert actual == "D:(A;;GA;;;SY)(A;;GA;;;BA)(A;;0x00100001;;;S-1-5-5-10-20)"


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("groups", ["@()", "@('S-1-5-5-1-2','S-1-5-5-1-3')", "@('S-1-1-0')"])
def test_missing_multiple_or_non_logon_sid_refused(shell, groups):
    result = run(shell, "$refused=$false; try { [void][WaggleDance.BridgeNamedMutexV1]::BuildSddl('S-1-5-21-1-2-3-1001', [string[]]" + groups + ") } catch { $refused=$true }; $refused | ConvertTo-Json")
    assert result is True


@pytest.mark.parametrize("shell", SHELLS)
def test_real_new_and_existing_unique_mutex(shell):
    result = run(shell, r"""
        $name='Local\WdNamedMutexTest-'+[guid]::NewGuid().ToString('N')
        $new1=$false; $new2=$true
        $m1=[WaggleDance.BridgeNamedMutexV1]::Create($name,[ref]$new1)
        try {
            $m2=[WaggleDance.BridgeNamedMutexV1]::Create($name,[ref]$new2)
            try {
                $ok=$m2.WaitOne(0)
                if($ok){$m2.ReleaseMutex()}
                $sddl=[WaggleDance.BridgeNamedMutexV1]::GetCreationSddl()
                @{created=$new1; reopened=$new2; acquired=$ok; sddl=$sddl; diagnostic=[WaggleDance.BridgeNamedMutexV1]::InspectDacl($name,$sddl)}|ConvertTo-Json -Compress
            } finally {$m2.Dispose()}
        } finally {$m1.Dispose()}
    """)
    assert result["created"] is True
    assert result["reopened"] is False
    assert result["acquired"] is True
    assert result["sddl"].count("(A;") == 3
    assert result["diagnostic"] == ""


@pytest.mark.parametrize("shell", SHELLS)
def test_foreign_shape_visible_and_not_rewritten(shell):
    result = run(shell, r"""
        $name='Local\WdForeignMutexTest-'+[guid]::NewGuid().ToString('N')
        $foreign=New-Object System.Threading.Mutex($false,$name)
        try {
            $sddl=[WaggleDance.BridgeNamedMutexV1]::GetCreationSddl()
            $before=[WaggleDance.BridgeNamedMutexV1]::InspectDacl($name,$sddl)
            $warnings=@(); $m=New-BridgeNamedMutex -Name $name -WarningVariable warnings -WarningAction SilentlyContinue
            try {
                $after=[WaggleDance.BridgeNamedMutexV1]::InspectDacl($name,$sddl)
                @{before=$before; after=$after; warnings=@($warnings|ForEach-Object {$_.ToString()})}|ConvertTo-Json -Compress
            } finally {$m.Dispose()}
        } finally {$foreign.Dispose()}
    """)
    assert result["before"].startswith("bridge_mutex_acl_mismatch:")
    assert result["after"] == result["before"]
    assert len(result["warnings"]) == 1


@pytest.mark.parametrize("shell", SHELLS)
def test_timeout_then_abandoned_mutex_semantics(shell):
    helper = str(ROOT / ".agent-bridge/bin/BridgeNamedMutex.ps1").replace("'", "''")
    name = "Local\\WdAbandonedMutexTest-" + uuid.uuid4().hex
    holder = subprocess.Popen([shell, "-NoProfile", "-NonInteractive", "-Command",
        f"$ErrorActionPreference='Stop'; . '{helper}'; $m=New-BridgeNamedMutex -Name '{name}'; "
        "try { [void]$m.WaitOne(); [Console]::Out.WriteLine('READY'); "
        "[Console]::Out.Flush(); Start-Sleep -Seconds 40 } finally { $m.Dispose() }"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "READY"
        result = run(shell, f"""
            $m=New-BridgeNamedMutex -Name '{name}'
            try {{
                $timedOut= -not $m.WaitOne(10)
                $holder=[Diagnostics.Process]::GetProcessById({holder.pid})
                $holder.Kill(); $holder.WaitForExit()
                $abandoned=$false
                try {{ [void]$m.WaitOne(5000) }} catch [Threading.AbandonedMutexException] {{$abandoned=$true}}
                $m.ReleaseMutex()
                @{{timedOut=$timedOut; abandoned=$abandoned}}|ConvertTo-Json -Compress
            }} finally {{$m.Dispose()}}
        """)
        assert result == {"timedOut": True, "abandoned": True}
    finally:
        if holder.poll() is None:
            holder.kill()
        holder.communicate(timeout=10)
