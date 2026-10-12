"""Writer-side safe-integer guard: the PowerShell writer must not write a row
the reader refuses.

BridgeLogReader's JsonContractValidator refuses an integer token outside
+/-9007199254740991, and such a row blocks every reader, like the non-ASCII key
of 2026-10-10 11:54Z. Measured 2026-10-11: Write-AgentEvent wrote 2^53 on
Windows PowerShell 5.1 and PowerShell 7, and a 30-digit integer raw on PS7; the
real reader then refused each row. Every invocation here uses an isolated
runtime and a fixture copy of the bin, on both shells, and checks the writer
against the real Read-AgentBridge.ps1.

Named mutants (applied to fixture copies only; each must make a test fail):
  MUT_SAFE_INTEGER_GUARD_CALL_DROPPED - the guard is never called on the row
  MUT_SAFE_INTEGER_STRINGS_SCANNED    - string tokens are not skipped, so digits
                                        inside keys and values are refused too
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[2]
SHELLS = list(dict.fromkeys(filter(None, [shutil.which("pwsh"), shutil.which("powershell.exe")])))
pytestmark = pytest.mark.skipif(os.name != "nt", reason="canonical writer append is Windows-only")

SAFE = 9007199254740991
REFUSAL = "JSON integers must be within +/-9007199254740991 before writing"
GUARD_CALL = "Assert-BridgeJsonIntegersSafe -Json $line\n"
STRING_SKIP = "if ($text[0] -eq '\"' -or $text.IndexOfAny([char[]]'.eE') -ge 0) { continue }"
MUTANTS = {
    "MUT_SAFE_INTEGER_GUARD_CALL_DROPPED": (GUARD_CALL, ""),
    "MUT_SAFE_INTEGER_STRINGS_SCANNED": (STRING_SKIP, "if ($text.IndexOfAny([char[]]'.eE') -ge 0) { continue }"),
}


def fixture_bin(runtime, mutant=None):
    code = runtime / "fixture-code" / ".agent-bridge" / "bin"
    if not code.exists():
        shutil.copytree(ROOT / ".agent-bridge/bin", code)
        configs = code.parent.parent / "configs"
        configs.mkdir()
        shutil.copy2(ROOT / "configs/bridge_identity_registry.json", configs)
        # Runtime directories do not isolate machine-wide mutex names; rewrite fixture copies only.
        prefix = "Local\\WdSafeIntegerGuardTest-" + uuid.uuid4().hex + "-"
        for script in code.glob("*.ps1"):
            source = script.read_text(encoding="utf-8-sig")
            if "Global\\WaggleDanceBridge" in source:
                script.write_text(source.replace("Global\\WaggleDanceBridge", prefix), encoding="utf-8-sig")
        if mutant:
            writer = code / "Write-AgentEvent.ps1"
            source = writer.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
            before, after = MUTANTS[mutant]
            assert source.count(before) == 1, f"{mutant} anchor must match exactly once"
            writer.write_text(source.replace(before, after), encoding="utf-8-sig")
    return code


def env_for(runtime):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENT_BRIDGE_", "WD_BRIDGE_"))}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime)
    return env


def write(shell, runtime, payload_text, mutant=None):
    code = fixture_bin(runtime, mutant)
    return subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(code / "Write-AgentEvent.ps1"),
         "-Agent", "operator", "-TaskId", "fixture/safe-integer-guard", "-SessionId", "current-session",
         "-RunId", "current-run", "-Type", "status", "-Status", "evidence", "-Message", "fixture",
         "-PayloadJson", payload_text],
        env=env_for(runtime), capture_output=True, text=True, encoding="utf-8", timeout=60,
    )


def read(shell, runtime):
    code = fixture_bin(runtime)
    return subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(code / "Read-AgentBridge.ps1"),
         "-Raw", "-NoAckReceived", "-NoContinuity", "-Tail", "5"],
        env=env_for(runtime), capture_output=True, text=True, encoding="utf-8", timeout=120,
    )


def written(runtime):
    paths = [runtime / "shared/events.jsonl"]
    paths += list((runtime / "outbox").rglob("*")) if (runtime / "outbox").exists() else []
    paths += list((runtime / "shared").glob("last_*.json")) if (runtime / "shared").exists() else []
    return {str(p): p.read_bytes() for p in paths if p.is_file()}


def last_row(runtime):
    # Split on LF bytes only: str.splitlines() also splits at U+2028/U+2029.
    return json.loads((runtime / "shared/events.jsonl").read_bytes().split(b"\n")[-2])


REFUSED_ON_BOTH_SHELLS = {
    "over_safe": '{"n": %d}' % (SAFE + 1),
    "under_safe": '{"n": %d}' % (-SAFE - 1),
    "nested_in_array": '{"items": [1, {"ticks": 638640000000000000}]}',
}
KEPT = {
    "boundaries": '{"n": %d, "m": %d, "zero": 0, "negative_zero": -0}' % (SAFE, -SAFE),
    "digits_in_strings": '{"s": "%d", "%d": "key", "t": "x\\"%d"}' % (SAFE + 2, SAFE + 3, SAFE + 4),
    "floats": '{"f": 1.5e300, "g": -0.25, "h": 1e-300}',
}
# Shell-dependent: PS5 turns this into a double, PS7 keeps the BigInteger.
EITHER_REFUSED_OR_READABLE = {
    "thirty_digits": '{"n": 123456789012345678901234567890}',
}


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("case", sorted(REFUSED_ON_BOTH_SHELLS))
def test_unsafe_integer_is_refused_before_any_write(tmp_path, shell, case):
    result = write(shell, tmp_path, REFUSED_ON_BOTH_SHELLS[case])
    assert result.returncode != 0, result.stdout
    assert REFUSAL in result.stderr
    assert written(tmp_path) == {}


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("case", sorted(KEPT))
def test_safe_numbers_and_digit_strings_are_written_and_read(tmp_path, shell, case):
    result = write(shell, tmp_path, KEPT[case])
    assert result.returncode == 0, result.stderr
    assert last_row(tmp_path)["payload"] == json.loads(KEPT[case])
    tail = read(shell, tmp_path)
    assert tail.returncode == 0, tail.stderr


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("case", sorted(EITHER_REFUSED_OR_READABLE))
def test_every_row_the_writer_accepts_is_read_by_the_real_reader(tmp_path, shell, case):
    result = write(shell, tmp_path, EITHER_REFUSED_OR_READABLE[case])
    if result.returncode != 0:
        assert REFUSAL in result.stderr
        assert written(tmp_path) == {}
        return
    tail = read(shell, tmp_path)
    assert tail.returncode == 0, tail.stderr


@pytest.mark.parametrize("shell", SHELLS)
def test_mutant_guard_call_dropped_lands_a_row_the_reader_refuses(tmp_path, shell):
    result = write(shell, tmp_path, REFUSED_ON_BOTH_SHELLS["over_safe"],
                   mutant="MUT_SAFE_INTEGER_GUARD_CALL_DROPPED")
    assert result.returncode == 0, result.stderr
    assert last_row(tmp_path)["payload"] == {"n": SAFE + 1}
    tail = read(shell, tmp_path)
    assert tail.returncode != 0, tail.stdout
    assert "invalid_json" in tail.stderr + tail.stdout


@pytest.mark.parametrize("shell", SHELLS)
def test_mutant_strings_scanned_refuses_digit_strings(tmp_path, shell):
    # Discriminates the string skip: the same payload is kept by the real guard.
    result = write(shell, tmp_path, KEPT["digits_in_strings"], mutant="MUT_SAFE_INTEGER_STRINGS_SCANNED")
    assert result.returncode != 0, result.stdout
    assert REFUSAL in result.stderr
