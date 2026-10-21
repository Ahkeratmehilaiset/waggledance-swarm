"""Native Windows publication faults, with synthetic files under .codex-audit.

The race/fault shim is inserted only in the in-memory test script, immediately
after candidate creation. It changes no production file or public test hook.
Git probes run read-only against the enclosing real repository; no git init.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
import uuid


ROOT = Path(os.environ.get("WD_ATOMICITY_REPO", Path(__file__).resolve().parents[2]))
WRITER = Path(os.environ.get("WD_ATOMICITY_WRITER", ROOT / "ops/windows/reboot/Write-WdLaneCurrentState.ps1"))
AUDIT = Path(os.environ.get("WD_ATOMICITY_AUDIT", ROOT / ".codex-audit"))
SHELLS = list(dict.fromkeys(p for p in (
    shutil.which("powershell.exe"), shutil.which("pwsh"),
    str(Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "PowerShell/7/pwsh.exe")
) if p and Path(p).is_file()))
OLD = b'{"schema":"wd.lane-current.v1","agent":"codex-tools-1","task_id":"old","work_held":false,"release_held":true}\r\n'
CREATOR = b'concurrent creator must survive byte-exact\r\n'
BOUNDARY = "[IO.File]::WriteAllText($temporary, $json, $utf8)"


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(os.name == "nt" and SHELLS and shutil.which("git"), "requires Windows PowerShell and Git")
class LaneCurrentStateAtomicity(unittest.TestCase):
    def run_case(self, shell, *, previous=True, fault=""):
        # Every fixture remains on persistent C: and is separate from lane state.
        worktree = AUDIT / ("lane-atomicity-" + uuid.uuid4().hex)
        self.assertEqual(worktree.resolve().drive.upper(), "C:")
        worktree.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, worktree)
        directory = worktree / ".codex-audit"
        directory.mkdir()
        target = directory / "wd-current-state.json"
        if previous:
            target.write_bytes(OLD)
        fault_code = {
            "": "",
            "creator": "[IO.File]::WriteAllBytes($statePath, [Convert]::FromBase64String('" + base64.b64encode(CREATOR).decode() + "'))",
            "candidate_lock": "$script:atomicityLock = [IO.File]::Open($temporary, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)",
        }[fault if fault != "target_lock" else ""]
        setup = ""
        if fault == "target_lock":
            setup = "$script:atomicityLock = [IO.File]::Open(" + quote(target) + ", [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)"
        command = f"""
$ErrorActionPreference = 'Stop'
$script:atomicityLock = $null
$script:atomicityCandidateBase64 = $null
$failed = $false
$message = ''
$nativeCode = 0
try {{
    {setup}
    $source = [IO.File]::ReadAllText({quote(WRITER)})
    $marker = {quote(BOUNDARY)}
    if (($source.Split(@($marker), [StringSplitOptions]::None)).Count -ne 2) {{ throw 'test boundary must occur exactly once' }}
    $injection = {quote(fault_code)}
    $capture = '$script:atomicityCandidateBase64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($temporary))'
    $source = $source.Replace($marker, $marker + [Environment]::NewLine + $capture + [Environment]::NewLine + $injection)
    & ([scriptblock]::Create($source)) -Agent codex-tools-1 -Worktree {quote(worktree)} -TaskId candidate -Status active -NextAction 'Synthetic publication test only' -WorkHeld $false -ReleaseHeld $true | Out-Null
}}
catch {{
    $failed = $true
    $message = $_.Exception.Message
    $nativeCode = $_.Exception.GetBaseException().HResult -band 65535
}}
finally {{ if ($null -ne $script:atomicityLock) {{ $script:atomicityLock.Dispose() }} }}
[pscustomobject]@{{ failed = $failed; message = $message; native_code = $nativeCode; candidate_before_publication = $script:atomicityCandidateBase64 }} | ConvertTo-Json -Compress
if ($failed) {{ exit 1 }}
"""
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", base64.b64encode(command.encode("utf-16le")).decode()],
            cwd=ROOT, capture_output=True, text=True, timeout=45,
        )
        (worktree / "stdout.txt").write_text(result.stdout, encoding="utf-8")
        (worktree / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        receipt = json.loads(result.stdout.strip())
        return target, directory, result, receipt

    def assert_candidate_retained(self, directory, result, receipt):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(receipt["failed"])
        candidates = list(directory.glob(".wd-current-state.*.tmp"))
        self.assertEqual(len(candidates), 1, f"candidate must survive publication failure: {directory}; {receipt}")
        candidate = json.loads(candidates[0].read_text(encoding="utf-8"))
        self.assertEqual(candidate["task_id"], "candidate")
        self.assertIs(candidate["work_held"], False)
        self.assertIs(candidate["release_held"], True)
        self.assertIn(str(candidates[0]), receipt["message"])
        return candidates[0]

    def test_case_cleanup_removes_its_synthetic_fixture(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                target, directory, result, receipt = self.run_case(shell)
                self.assertEqual(result.returncode, 0, receipt)
                fixture = target.parent.parent
                self.assertTrue(fixture.is_dir())
                self.assertTrue(self.doCleanups())
                self.assertFalse(fixture.exists(), f"case cleanup must remove its own synthetic fixture: {fixture}")

    def test_absent_target_publishes_without_backup(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                target, directory, result, receipt = self.run_case(shell, previous=False)
                self.assertEqual(result.returncode, 0, receipt)
                self.assertEqual(json.loads(target.read_bytes())["task_id"], "candidate")
                self.assertFalse(list(directory.glob("*.tmp")))
                self.assertFalse(list(directory.glob("*.previous.json")))

    def test_existing_target_is_replaced_with_byte_exact_backup(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                target, directory, result, receipt = self.run_case(shell)
                self.assertEqual(result.returncode, 0, receipt)
                self.assertEqual(json.loads(target.read_bytes())["task_id"], "candidate")
                backups = list(directory.glob("*.previous.json"))
                self.assertEqual(len(backups), 1)
                self.assertEqual(backups[0].read_bytes(), OLD)
                self.assertFalse(list(directory.glob("*.tmp")))

    def test_target_share_denial_preserves_old_and_candidate(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                target, directory, result, receipt = self.run_case(shell, fault="target_lock")
                self.assert_candidate_retained(directory, result, receipt)
                self.assertEqual(receipt["native_code"], 32, receipt)
                self.assertEqual(target.read_bytes(), OLD)

    def test_candidate_share_denial_never_deletes_old_target(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                target, directory, result, receipt = self.run_case(shell, fault="candidate_lock")
                self.assert_candidate_retained(directory, result, receipt)
                self.assertEqual(receipt["native_code"], 32, receipt)
                self.assertEqual(target.read_bytes(), OLD)

    def test_publication_error_reports_inner_hresult_and_preserves_exact_bytes(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                target, directory, result, receipt = self.run_case(shell, fault="target_lock")
                candidate = self.assert_candidate_retained(directory, result, receipt)
                self.assertEqual(receipt["native_code"], 32, receipt)
                self.assertIn("inner_exception=System.IO.IOException", receipt["message"])
                self.assertIn("inner_hresult=0x80070020", receipt["message"])
                self.assertEqual(target.read_bytes(), OLD)
                self.assertEqual(
                    candidate.read_bytes(),
                    base64.b64decode(receipt["candidate_before_publication"], validate=True),
                )
                self.assertFalse(list(directory.glob("*.previous.json")))

    def test_absent_target_share_denial_retains_candidate(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                target, directory, result, receipt = self.run_case(shell, previous=False, fault="candidate_lock")
                self.assert_candidate_retained(directory, result, receipt)
                self.assertEqual(receipt["native_code"], 32, receipt)
                self.assertFalse(target.exists())

    def test_concurrent_creator_is_not_overwritten(self):
        for shell in SHELLS:
            with self.subTest(shell=shell):
                target, directory, result, receipt = self.run_case(shell, previous=False, fault="creator")
                self.assert_candidate_retained(directory, result, receipt)
                self.assertEqual(target.read_bytes(), CREATOR)
                self.assertFalse(list(directory.glob("*.previous.json")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
