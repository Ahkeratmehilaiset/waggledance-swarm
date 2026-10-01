# SPDX-License-Identifier: BUSL-1.1
"""F26 S-C: Get-BridgeCurrentLogIdentity.ps1 on OWN child runtime fixtures (PS 5.1 and pwsh 7). SYNTHETIC rows only."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / ".agent-bridge" / "bin" / "Get-BridgeCurrentLogIdentity.ps1"
SHELLS = list(dict.fromkeys(filter(None, (shutil.which("pwsh"), shutil.which("powershell.exe")))))
FIELDS = ["log_generation", "file_identity", "log_bytes", "prefix_sha256", "observed_utc", "complete"]
SECRET = "s-c-fixture-secret-7f3a"
ROWS = [{"agent": "codex-lead-1", "type": "wake_request", "task_id": "t/one", "message": SECRET},
        {"agent": "fable-5", "type": "message", "task_id": "t/one", "message": "reply"}]
UNKNOWN = {"log_generation": None, "file_identity": None, "log_bytes": None, "prefix_sha256": None,
           "observed_utc": None, "complete": False}

pytestmark = pytest.mark.skipif(not SHELLS or os.name != "nt", reason="needs Windows PowerShell")


def _line(row: dict) -> bytes:
    return (json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    shared = tmp_path / "runtime" / "shared"
    shared.mkdir(parents=True)
    (shared / "events.jsonl").write_bytes(b"".join(_line(row) for row in ROWS))
    (shared / "events.generation.json").write_bytes(b'{"generation":"gen-a"}')
    return tmp_path / "runtime"


def measure(shell: str, root: Path | None, *extra: str, script: Path = SCRIPT,
            seam: dict | None = None) -> tuple[dict, str]:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("AGENT_BRIDGE_", "WD_", "CLAUDE_CODE_", "GIT_", "S_C_SEAM_"))}
    if root is not None:
        env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(root)
    env.update(seam or {})
    run = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
                          "-Json", *extra], capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
    assert run.returncode == 0, run.stderr
    out = run.stdout.strip()
    result = json.loads(out)
    assert list(result) == FIELDS
    return result, run.stdout + run.stderr


def log(root: Path) -> Path:
    return root / "shared" / "events.jsonl"


def assert_complete(result: dict, data: bytes, generation: str = "gen-a") -> None:
    assert result["complete"] is True
    assert result["log_generation"] == generation
    assert re.fullmatch(r"windows-v1:[0-9a-f]{8}:[0-9a-f]{16}", result["file_identity"])
    assert result["log_bytes"] == len(data) and type(result["log_bytes"]) is int
    assert result["prefix_sha256"] == hashlib.sha256(data).hexdigest()
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{7}Z", result["observed_utc"])


@pytest.mark.parametrize("shell", SHELLS)
def test_an_unchanged_stable_log_has_a_complete_repeatable_identity_and_no_content(shell, root):
    first, text = measure(shell, root)
    second, _ = measure(shell, root)
    assert_complete(first, log(root).read_bytes())
    assert {k: v for k, v in first.items() if k != "observed_utc"} == {k: v for k, v in second.items()
                                                                        if k != "observed_utc"}
    assert SECRET not in text and "wake_request" not in text and "t/one" not in text


@pytest.mark.parametrize("shell", SHELLS)
def test_an_appended_cancel_changes_bytes_and_hash_but_not_the_file(shell, root):
    before, _ = measure(shell, root)
    with open(log(root), "ab") as handle:
        handle.write(_line({"agent": "codex-lead-1", "status": "cancelled", "task_id": "t/one", "payload": {}}))
    after, _ = measure(shell, root)
    assert_complete(after, log(root).read_bytes())
    assert after["file_identity"] == before["file_identity"]
    assert after["log_bytes"] > before["log_bytes"] and after["prefix_sha256"] != before["prefix_sha256"]


@pytest.mark.parametrize("shell", SHELLS)
def test_an_unfinished_tail_row_is_unknown(shell, root):
    with open(log(root), "ab") as handle:
        handle.write(b'{"agent":"codex-lead-1","status":"cancel')
    assert measure(shell, root)[0] == UNKNOWN


@pytest.mark.parametrize("shell", SHELLS)
def test_an_empty_log_is_a_complete_empty_prefix(shell, root):
    log(root).write_bytes(b"")
    assert_complete(measure(shell, root)[0], b"")


@pytest.mark.parametrize("shell", SHELLS)
def test_a_same_length_in_place_rewrite_changes_the_prefix_hash(shell, root):
    before, _ = measure(shell, root)
    data = log(root).read_bytes().replace(b"reply", b"REPLY")
    with open(log(root), "r+b") as handle:
        handle.write(data)
    after, _ = measure(shell, root)
    assert_complete(after, data)
    assert after["log_bytes"] == before["log_bytes"] and after["prefix_sha256"] != before["prefix_sha256"]


@pytest.mark.parametrize("shell", SHELLS)
def test_a_truncation_changes_bytes_and_hash(shell, root):
    before, _ = measure(shell, root)
    data = _line(ROWS[0])
    with open(log(root), "r+b") as handle:
        handle.truncate(len(data))
    after, _ = measure(shell, root)
    assert_complete(after, data)
    assert after["log_bytes"] < before["log_bytes"]


@pytest.mark.parametrize("shell", SHELLS)
def test_a_rotation_to_identical_bytes_changes_the_file_identity(shell, root):
    before, _ = measure(shell, root)
    data = log(root).read_bytes()
    log(root).rename(root / "shared" / "events.rotated.jsonl")
    log(root).write_bytes(data)
    after, _ = measure(shell, root)
    assert_complete(after, data)
    assert after["prefix_sha256"] == before["prefix_sha256"] and after["file_identity"] != before["file_identity"]


@pytest.mark.parametrize("shell", SHELLS)
def test_a_generation_change_is_reported_and_its_absence_is_unknown(shell, root):
    sidecar = root / "shared" / "events.generation.json"
    sidecar.write_bytes(b'{"generation":"gen-b"}')
    assert_complete(measure(shell, root)[0], log(root).read_bytes(), "gen-b")
    sidecar.unlink()
    assert measure(shell, root)[0] == UNKNOWN


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("raw", [b'{"generation":""}', b'{"generation":"a b"}', b'{"generation":"g","x":1}',
                                 b"\xef\xbb\xbf{\"generation\":\"g\"}", b"not json", b'{"generation":1}'])
def test_an_invalid_generation_sidecar_is_unknown(shell, root, raw):
    (root / "shared" / "events.generation.json").write_bytes(raw)
    assert measure(shell, root)[0] == UNKNOWN


@pytest.mark.parametrize("shell", SHELLS)
def test_a_missing_log_or_unset_or_relative_root_is_unknown(shell, root):
    assert measure(shell, None)[0] == UNKNOWN
    assert measure(shell, Path("runtime"))[0] == UNKNOWN
    log(root).unlink()
    assert measure(shell, root)[0] == UNKNOWN


@pytest.mark.parametrize("shell", SHELLS)
def test_an_unreadable_log_is_unknown(shell, root):
    log(root).unlink()
    log(root).mkdir()
    assert measure(shell, root)[0] == UNKNOWN


@pytest.mark.parametrize("shell", SHELLS)
def test_a_log_held_without_read_sharing_is_unknown(shell, root):
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    handle = kernel32.CreateFileW(str(log(root)), 0x80000000, 0, None, 3, 0x80, None)   # GENERIC_READ, no sharing
    assert handle not in (None, wintypes.HANDLE(-1).value)
    try:
        assert measure(shell, root)[0] == UNKNOWN
    finally:
        assert kernel32.CloseHandle(handle)
    assert measure(shell, root)[0]["complete"] is True


@pytest.mark.parametrize("shell", SHELLS)
def test_a_reparse_point_shared_directory_is_refused_but_the_real_directory_is_not(shell, root, tmp_path):
    real = tmp_path / "elsewhere"
    (root / "shared").rename(real)
    made = subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "shared"), str(real)], capture_output=True)
    assert made.returncode == 0
    assert measure(shell, root)[0] == UNKNOWN
    (root / "shared").rmdir()                  # removes the junction only
    real.rename(root / "shared")
    assert measure(shell, root)[0]["complete"] is True


# --- deterministic seams (after fable-5 59f): a COPY of the bin whose BridgeReplyIndex.ps1 wraps the shared prefix
# hash and performs exactly ONE side effect after hash call number S_C_SEAM_CALL returns. No loop, no race; the
# real helper bytes are copied unchanged and every fixture is two rows.
SEAM = r"""
if ($env:S_C_SEAM_ACTION) {
    $script:SeamOriginalHash = ${function:Get-BridgeReplyStreamPrefixHash}
    $script:SeamCalls = 0
    function Get-BridgeReplyStreamPrefixHash {
        param([IO.Stream]$Stream,[int64]$Length)
        $value = & $script:SeamOriginalHash -Stream $Stream -Length $Length
        $script:SeamCalls++
        if ($script:SeamCalls -ne [int]$env:S_C_SEAM_CALL) { return $value }
        $shared = Join-Path $env:AGENT_BRIDGE_RUNTIME_ROOT 'shared'
        $log = Join-Path $shared 'events.jsonl'
        $share = [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
        switch ($env:S_C_SEAM_ACTION) {
            'none' { }
            'append' { [IO.File]::AppendAllText($log, "{`"agent`":`"codex-lead-1`",`"status`":`"cancelled`"}`n") }
            'rotate' {
                $bytes = [IO.File]::ReadAllBytes($log)
                [IO.File]::Move($log, (Join-Path $shared 'events.rotated.jsonl'))
                [IO.File]::WriteAllBytes($log, $bytes)
            }
            'generation' {
                [IO.File]::WriteAllBytes((Join-Path $shared 'events.generation.json'),
                    [Text.Encoding]::ASCII.GetBytes('{"generation":"gen-b"}'))
            }
            'rewrite' {
                $bytes = [IO.File]::ReadAllBytes($log)
                $new = [Text.Encoding]::UTF8.GetBytes([Text.Encoding]::UTF8.GetString($bytes).Replace('reply', 'REPLY'))
                $w = [IO.File]::Open($log, [IO.FileMode]::Open, [IO.FileAccess]::Write, $share)
                try { $w.Write($new, 0, $new.Length) } finally { $w.Dispose() }
            }
            'truncate' {
                $w = [IO.File]::Open($log, [IO.FileMode]::Open, [IO.FileAccess]::Write, $share)
                $first = [Array]::IndexOf([IO.File]::ReadAllBytes($log), [byte]10)
                try { $w.SetLength($first + 1) } finally { $w.Dispose() }
            }
        }
        return $value
    }
}
"""
SEAM_FILES = ("Get-BridgeCurrentLogIdentity.ps1", "BridgeIncrementalReader.ps1", "BridgeLogReader.ps1",
              "BridgeReplyIndex.ps1", "BridgeRequestContract.ps1", "BridgeResourceScope.ps1")


@pytest.fixture(scope="module")
def seam_script(tmp_path_factory) -> Path:
    copy = tmp_path_factory.mktemp("seam-bin")
    for name in SEAM_FILES:
        shutil.copyfile(SCRIPT.parent / name, copy / name)
    with open(copy / "BridgeReplyIndex.ps1", "ab") as handle:
        handle.write(SEAM.encode("ascii"))
    assert (copy / SCRIPT.name).read_bytes() == SCRIPT.read_bytes()
    return copy / SCRIPT.name


def seamed(shell: str, root: Path, script: Path, action: str, call: int = 1) -> dict:
    return measure(shell, root, script=script, seam={"S_C_SEAM_ACTION": action, "S_C_SEAM_CALL": str(call)})[0]


@pytest.mark.parametrize("shell", SHELLS)
def test_the_seam_copy_with_no_side_effect_is_complete_and_current(shell, root, seam_script):
    assert_complete(seamed(shell, root, seam_script, "none"), log(root).read_bytes())


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("action", ["append", "rotate", "generation", "rewrite", "truncate"])
def test_a_change_after_the_first_hash_is_unknown(shell, root, seam_script, action):
    # rewrite is RCO1/fable-5 SC-F1: same length, same file, same generation; only the bytes differ.
    assert seamed(shell, root, seam_script, action, call=1) == UNKNOWN
    # the safe twin: the same file measured again afterwards is complete and describes the changed log
    after = measure(shell, root)[0]
    assert after["complete"] is True and after["prefix_sha256"] == hashlib.sha256(log(root).read_bytes()).hexdigest()


@pytest.mark.parametrize("shell", SHELLS)
def test_a_rewrite_after_the_last_hash_returns_is_the_documented_after_return_limit(shell, root, seam_script):
    # Not a guarantee: a change after the final hash returned is seen only by a later measurement. The result
    # still describes the bytes that WERE measured twice, never a mixed identity.
    data = log(root).read_bytes()
    out = seamed(shell, root, seam_script, "rewrite", call=2)
    assert_complete(out, data)
    assert out["prefix_sha256"] != hashlib.sha256(log(root).read_bytes()).hexdigest()


@pytest.mark.parametrize("shell", SHELLS)
def test_the_byte_cap_is_explicit_and_exact(shell, root):
    size = len(log(root).read_bytes())
    assert measure(shell, root, "-MaxBytes", str(size))[0]["complete"] is True
    assert measure(shell, root, "-MaxBytes", str(size - 1))[0] == UNKNOWN


def test_the_helper_writes_nothing_and_starts_no_thread_process_or_provider():
    text = SCRIPT.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    for token in ("Set-Content", "Add-Content", "Out-File", "New-Item", "Remove-Item", "Move-Item", "Copy-Item",
                  "WriteAllText", "WriteAllBytes", "FileAccess]::Write", "FileMode]::Create", "Start-Process",
                  "Start-Job", "Start-ThreadJob", "Runspace", "Invoke-Expression", "Invoke-WebRequest",
                  "Invoke-RestMethod", "Write-AgentEvent", "Write-Bridge", "CachePath", "Thread"):
        assert token not in code, token
