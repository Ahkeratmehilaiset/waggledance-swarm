"""Writer-side ASCII object-key guard (2026-10-10 11:54Z fleet read outage).

BridgeLogReader's JsonContractValidator refuses any decoded object key above
0x7F, so one such row blocked every reader. Write-AgentEvent (and therefore
Write-BridgeTaskReply, which writes through it) must refuse the row before any
canonical append, WAL or outbox write. Every invocation uses an isolated runtime
and a fixture copy of the bin, on Windows PowerShell 5.1 and PowerShell 7.

Named mutants (applied to fixture copies only; each must make a test fail):
  MUT_ASCII_KEY_GUARD_CALL_DROPPED     - the guard is never called on the row
  MUT_ASCII_KEY_VALUES_TREATED_AS_KEYS - the ':' follow check is dropped, so
                                         non-ASCII string VALUES are refused too
A \\u-escaped non-ASCII key is refused as well, but ConvertTo-Json re-emits it
raw on both shells, so the guard's escape decoding is defence in depth: an
escape-blind mutant is equivalent through the public writer (measured).
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

GUARD_CALL = "Assert-BridgeJsonObjectKeysAscii -Json $line\n"
COLON_CHECK = "        if ($next -ge $Json.Length -or $Json[$next] -ne ':') { continue }\n"
MUTANTS = {
    "MUT_ASCII_KEY_GUARD_CALL_DROPPED": (GUARD_CALL, ""),
    "MUT_ASCII_KEY_VALUES_TREATED_AS_KEYS": (COLON_CHECK, ""),
}


def fixture_bin(runtime, mutant=None):
    code = runtime / "fixture-code" / ".agent-bridge" / "bin"
    if not code.exists():
        shutil.copytree(ROOT / ".agent-bridge/bin", code)
        configs = code.parent.parent / "configs"
        configs.mkdir()
        shutil.copy2(ROOT / "configs/bridge_identity_registry.json", configs)
        # Runtime directories do not isolate machine-wide mutex names; rewrite fixture copies only.
        prefix = "Local\\WdAsciiKeyGuardTest-" + uuid.uuid4().hex + "-"
        for script in code.glob("*.ps1"):
            source = script.read_text(encoding="utf-8-sig")
            if "Global\\WaggleDanceBridge" in source:
                script.write_text(source.replace("Global\\WaggleDanceBridge", prefix), encoding="utf-8-sig")
        if mutant:
            writer = code / "Write-AgentEvent.ps1"
            source = writer.read_text(encoding="utf-8-sig")
            before, after = MUTANTS[mutant]
            anchored = source.replace("\r\n", "\n")
            assert anchored.count(before) == 1, f"{mutant} anchor must match exactly once"
            writer.write_text(anchored.replace(before, after), encoding="utf-8-sig")
    return code


def env_for(runtime):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENT_BRIDGE_", "WD_BRIDGE_"))}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime)
    return env


def write(shell, runtime, payload_text, mutant=None, message="fixture"):
    code = fixture_bin(runtime, mutant)
    return subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(code / "Write-AgentEvent.ps1"),
         "-Agent", "operator", "-TaskId", "fixture/ascii-key-guard", "-SessionId", "current-session",
         "-RunId", "current-run", "-Type", "status", "-Status", "evidence", "-Message", message,
         "-PayloadJson", payload_text],
        env=env_for(runtime), capture_output=True, text=True, encoding="utf-8", timeout=60,
    )


def written(runtime):
    paths = [runtime / "shared/events.jsonl"]
    paths += list((runtime / "outbox").rglob("*")) if (runtime / "outbox").exists() else []
    paths += list((runtime / "shared").glob("last_*.json")) if (runtime / "shared").exists() else []
    return {str(p): p.read_bytes() for p in paths if p.is_file()}


def all_keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from all_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from all_keys(item)


REFUSED = {
    "top_level_raw": '{"pitkä erä": 1}',
    "nested_in_array_raw": '{"items": [{"ok": 1}, {"E10 Grok-erä": "x"}]}',
    "escaped_u00e4": '{"Q5 pitk\\u00e4": true}',
    "escaped_astral": '{"k\\ud83d\\ude00": 1}',
}


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("case", sorted(REFUSED))
def test_non_ascii_object_key_is_refused_before_any_write(tmp_path, shell, case):
    result = write(shell, tmp_path, REFUSED[case])
    assert result.returncode != 0, result.stdout
    assert "JSON object keys must be ASCII before writing" in result.stderr
    assert written(tmp_path) == {}


@pytest.mark.parametrize("shell", SHELLS)
def test_non_ascii_values_and_key_lookalikes_inside_values_are_written(tmp_path, shell):
    payload = {"summary": "Grok-erä valmis ≠ \"pitkä\": 1", "items": ["äö", {"note": "ä\":"}]}
    result = write(shell, tmp_path, json.dumps(payload, ensure_ascii=False), message="erä \"ä\": valmis")
    assert result.returncode == 0, result.stderr
    row = json.loads((tmp_path / "shared/events.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["payload"] == payload
    assert all(key.isascii() for key in all_keys(row))


@pytest.mark.parametrize("shell", SHELLS)
def test_task_reply_with_non_ascii_result_key_is_refused_before_write(tmp_path, shell):
    code = fixture_bin(tmp_path)
    agent = "codex-tools-1"
    agent_uuid = json.loads((ROOT / "configs/bridge_identity_registry.json").read_text())["identities"][agent]
    request = dict(ts_utc="2026-01-01T00:00:00Z", request_id="ascii-key-request", request_digest="digest",
                   agent="operator", session_id="op-session", run_id="op-run", to=agent, type="wake_request",
                   status="request", task_id="fixture/ascii-key-guard",
                   expected_responders={agent: dict(agent_uuid=agent_uuid, session_id="tools-session", run_id="tools-run")},
                   payload=dict(nonce="exact-nonce"))
    env = env_for(tmp_path)
    env.update(AGENT_BRIDGE_AGENT=agent, AGENT_BRIDGE_AGENT_UUID=agent_uuid,
               AGENT_BRIDGE_SESSION_ID="tools-session", AGENT_BRIDGE_RUN_ID="tools-run")
    results = {}
    for name, result_json in (("refused", {"E10 Grok-erä": "x"}), ("control", {"e10": "Grok-erä"})):
        script = tmp_path / f"reply-{name}.ps1"
        # Same fixture shape as test_bridge_task_result: launcher ancestry is not under test here.
        script.write_text(
            "function Get-CimInstance { return $null }\n"
            f"& '{code / 'Write-BridgeTaskReply.ps1'}' -Agent '{agent}' "
            f"-RequestEventJson (Get-Content -Raw -Encoding UTF8 '{tmp_path / 'request.json'}') "
            f"-ResultJson (Get-Content -Raw -Encoding UTF8 '{tmp_path / (name + '.json')}')\n",
            encoding="utf-8-sig")
        (tmp_path / "request.json").write_text(json.dumps(request), encoding="utf-8")
        (tmp_path / f"{name}.json").write_text(json.dumps(result_json, ensure_ascii=False), encoding="utf-8")
        results[name] = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(script)],
                                       env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
        if name == "refused":
            assert results[name].returncode != 0, results[name].stdout
            assert "JSON object keys must be ASCII before writing" in results[name].stderr
            assert written(tmp_path) == {}
    assert results["control"].returncode == 0, results["control"].stderr
    row = json.loads((tmp_path / "shared/events.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["payload"]["result"] == {"e10": "Grok-erä"}


@pytest.mark.parametrize("shell", SHELLS)
def test_mutant_guard_call_dropped_lets_the_poison_row_through(tmp_path, shell):
    # The refusal tests are discriminating: on this mutant the same input is appended.
    result = write(shell, tmp_path, REFUSED["top_level_raw"], mutant="MUT_ASCII_KEY_GUARD_CALL_DROPPED")
    assert result.returncode == 0, result.stderr
    row = json.loads((tmp_path / "shared/events.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert not all(key.isascii() for key in all_keys(row))


@pytest.mark.parametrize("shell", SHELLS)
def test_mutant_values_treated_as_keys_over_blocks(tmp_path, shell):
    # The values test is discriminating: on this mutant a non-ASCII VALUE is refused.
    result = write(shell, tmp_path, json.dumps({"summary": "Grok-er\u00e4"}, ensure_ascii=False),
                   mutant="MUT_ASCII_KEY_VALUES_TREATED_AS_KEYS")
    assert result.returncode != 0, result.stdout
    assert "JSON object keys must be ASCII before writing" in result.stderr
    assert written(tmp_path) == {}


def read_tail(shell, runtime):
    code = fixture_bin(runtime)
    return subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(code / "Read-AgentBridge.ps1"),
         "-Raw", "-NoAckReceived", "-NoContinuity", "-Tail", "5"],
        env=env_for(runtime), capture_output=True, text=True, encoding="utf-8", timeout=60,
    )


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("mutant", [None, "MUT_ASCII_KEY_GUARD_CALL_DROPPED"])
def test_regression_twin_reader_survives_only_with_the_guard(tmp_path, shell, mutant):
    # Twin of the 2026-10-10 11:54Z outage: a good row, then the poison payload.
    # With the guard the reader still reads the tail; on the mutant the row lands
    # and the real reader refuses the whole tail.
    assert write(shell, tmp_path, '{"ok": 1}', mutant=mutant).returncode == 0
    poisoned = write(shell, tmp_path, REFUSED["top_level_raw"], mutant=mutant)
    assert (poisoned.returncode == 0) is (mutant is not None), poisoned.stderr
    tail = read_tail(shell, tmp_path)
    if mutant is None:
        assert tail.returncode == 0, tail.stderr
    else:
        assert tail.returncode != 0, tail.stdout
        assert "invalid_json" in tail.stderr + tail.stdout