"""PowerShell exact withdrawal: a requester closure with `withdraws` closes only
the request version it names by descriptor and reader-row digest.

Reader-row digest (Lead decision 318100B1): lowercase SHA-256 of the strict
UTF-8 row split on LF with at most one trailing CR removed. Rows are written as
raw bytes, so every digest below is computed from bytes the selector reads.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / ".agent-bridge" / "bin"
SHELLS = list(dict.fromkeys(filter(None, [shutil.which("pwsh"), shutil.which("powershell.exe")])))
REGISTRY = json.loads((ROOT / "configs" / "bridge_identity_registry.json").read_text(encoding="utf-8"))["identities"]

OWNER = "fable-5"
TARGET = "codex-tools-1"
TASK = "fable-5/withdrawal-fixture"
NOW = "2026-10-06T19:00:00Z"
V1_TS = "2026-10-06T17:20:00Z"
V2_TS = "2026-10-06T17:29:29.9136087Z"
W_TS = "2026-10-06T18:17:12.6680487Z"


def row(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def request(stamp, status, **extra):
    event = {"ts_utc": stamp, "agent": OWNER, "type": "message", "task_id": TASK, "status": status,
             "to": f"claude-rco-1,{TARGET}", "message": f"fixture request {status} äö",
             "payload": {}, "agent_uuid": REGISTRY[OWNER]}
    event.update(extra)
    return event


def descriptor(target, raw):
    return {"agent": target["agent"], "type": target["type"], "status": target["status"],
            "task_id": target["task_id"], "ts_utc": target["ts_utc"],
            "raw_line_sha256": hashlib.sha256(raw).hexdigest()}


def withdrawal(target=None, **extra):
    event = {"ts_utc": W_TS, "agent": OWNER, "type": "message", "task_id": TASK, "status": "withdrawn",
             "to": TARGET, "message": "fixture withdrawal", "payload": {}, "agent_uuid": REGISTRY[OWNER]}
    if target is not None:
        event["payload"] = {"withdraws": target}
    event.update(extra)
    return event


V1 = request(V1_TS, "request")
V2 = request(V2_TS, "review_requested")


def scenario(case):
    """Return (rows as bytes, expected open request timestamps, expected diagnostic kinds)."""
    v1, v2 = row(V1), row(V2)
    exact = descriptor(V2, v2)
    if case == "d1_exact_newer_of_two":
        return [v1, v2, row(withdrawal(exact))], [V1_TS], []
    if case == "d1_exact_older_of_two":
        return [v1, v2, row(withdrawal(descriptor(V1, v1)))], [V2_TS], []
    if case == "d2_single_ts_mismatch":
        return [v2, row(withdrawal(dict(exact, ts_utc="2026-10-06T16:00:00Z")))], [V2_TS], []
    if case == "exact_single":
        return [v2, row(withdrawal(exact))], [], []
    if case == "hash_mismatch":
        return [v2, row(withdrawal(dict(exact, raw_line_sha256="0" * 64)))], [V2_TS], []
    if case in {"status_mismatch", "type_mismatch", "agent_mismatch", "task_mismatch"}:
        key = case.split("_")[0]
        key = "task_id" if key == "task" else key
        return [v2, row(withdrawal(dict(exact, **{key: "other"})))], [V2_TS], []
    if case == "ts_equal_instant_other_spelling":
        return [v2, row(withdrawal(dict(exact, ts_utc="2026-10-06T20:29:29.9136087+03:00")))], [], []
    if case == "non_owner":
        return [v2, row(withdrawal(exact, agent="codex-lead-1", agent_uuid=REGISTRY["codex-lead-1"]))], [V2_TS], []
    if case == "withdrawal_before_request":
        return [row(withdrawal(exact, ts_utc="2026-10-06T17:00:00Z")), v2], [V2_TS], []
    if case == "malformed_not_object":
        return [v2, row(withdrawal("v2"))], [V2_TS], ["malformed_withdrawal"]
    if case == "malformed_null":
        event = withdrawal()
        event["payload"] = {"withdraws": None}
        return [v2, row(event)], [V2_TS], ["malformed_withdrawal"]
    if case == "malformed_missing_ts":
        bad = dict(exact)
        del bad["ts_utc"]
        return [v2, row(withdrawal(bad))], [V2_TS], ["malformed_withdrawal"]
    if case == "malformed_naive_ts":
        return [v2, row(withdrawal(dict(exact, ts_utc="2026-10-06T17:29:29.9136087")))], [V2_TS], ["malformed_withdrawal"]
    if case == "malformed_uppercase_hex":
        return [v2, row(withdrawal(dict(exact, raw_line_sha256=exact["raw_line_sha256"].upper())))], [V2_TS], ["malformed_withdrawal"]
    if case == "malformed_hex_trailing_newline":
        return [v2, row(withdrawal(dict(exact, raw_line_sha256=exact["raw_line_sha256"] + "\n")))], [V2_TS], ["malformed_withdrawal"]
    if case == "malformed_top_level_conflict":
        return [v2, row(withdrawal(exact, withdraws=dict(exact, status="other")))], [V2_TS], ["malformed_withdrawal"]
    if case == "top_level_equal_payload":
        return [v2, row(withdrawal(exact, withdraws=dict(exact)))], [], []
    if case == "top_level_only":
        event = withdrawal(withdraws=exact)
        return [v2, row(event)], [], []
    if case == "identity_missing_uuid":
        event = withdrawal(exact)
        del event["agent_uuid"]
        return [v2, row(event)], [V2_TS], ["withdrawal_identity_unverified"]
    if case == "identity_foreign_uuid":
        return [v2, row(withdrawal(exact, agent_uuid=REGISTRY["codex-lead-1"]))], [V2_TS], ["withdrawal_identity_unverified"]
    if case == "identity_uppercase_uuid":
        return [v2, row(withdrawal(exact, agent_uuid=REGISTRY[OWNER].upper()))], [], []
    if case == "identity_request_missing_uuid":
        bare = dict(V2)
        del bare["agent_uuid"]
        return [row(bare), row(withdrawal(descriptor(bare, row(bare))))], [V2_TS], ["withdrawal_identity_unverified"]
    if case == "identity_request_foreign_uuid":
        foreign = dict(V2, agent_uuid=REGISTRY["codex-lead-1"])
        return [row(foreign), row(withdrawal(descriptor(foreign, row(foreign))))], [V2_TS], ["withdrawal_identity_unverified"]
    if case == "crlf_request_row_digest_without_cr":
        return [v2 + b"\r", row(withdrawal(exact))], [], []
    if case == "crlf_request_row_digest_with_cr":
        return [v2 + b"\r", row(withdrawal(descriptor(V2, v2 + b"\r")))], [V2_TS], []
    if case == "double_cr_request_row":
        return [v2 + b"\r\r", row(withdrawal(exact))], [V2_TS], ["withdrawal_unverifiable"]
    if case == "bare_cr_inside_request_row":
        return [v2 + b"\r{\"note\":1}", row(withdrawal(exact))], [V2_TS], ["withdrawal_unverifiable"]
    if case == "bom_request_row":
        return [b"\xef\xbb\xbf" + v2, row(withdrawal(exact))], [V2_TS], ["withdrawal_unverifiable"]
    if case == "duplicate_identical_rows":
        return [v2, v2, row(withdrawal(exact))], [V2_TS, V2_TS], ["withdrawal_unverifiable", "withdrawal_unverifiable"]
    if case == "duplicate_lf_and_crlf_rows":
        return [v2, v2 + b"\r", row(withdrawal(exact))], [V2_TS, V2_TS], ["withdrawal_unverifiable", "withdrawal_unverifiable"]
    if case == "bound_request_id":
        bound = request(V2_TS, "review_requested", request_id="fixture-r1")
        return [row(bound), row(withdrawal(descriptor(bound, row(bound))))], [V2_TS], []
    if case == "legacy_closure_without_withdraws":
        return [v2, row(withdrawal())], [], []
    if case == "legacy_ambiguous_without_withdraws":
        return [v1, v2, row(withdrawal())], [V1_TS, V2_TS], []
    if case == "legacy_ambiguous_request_ts_utc":
        return [v1, v2, row(withdrawal(request_ts_utc=V2_TS))], [V1_TS], []
    if case == "unrelated_task_withdrawal":
        other = descriptor(dict(V2, task_id="fable-5/other"), v2)
        return [v2, row(withdrawal(other, task_id="fable-5/other"))], [V2_TS], []
    raise AssertionError(case)


# Expected on the 9645e93d base, which ignores `withdraws`: the routed request
# timestamps differ exactly where D1/D2 and the fail-closed rules bite.
CASES = {
    "d1_exact_newer_of_two": [V1_TS, V2_TS],
    "d1_exact_older_of_two": [V1_TS, V2_TS],
    "d2_single_ts_mismatch": [],
    "exact_single": [],
    "hash_mismatch": [],
    "status_mismatch": [],
    "type_mismatch": [],
    "agent_mismatch": [],
    "task_mismatch": [],
    "ts_equal_instant_other_spelling": [],
    "non_owner": [V2_TS],
    "withdrawal_before_request": [V2_TS],
    "malformed_not_object": [],
    "malformed_null": [],
    "malformed_missing_ts": [],
    "malformed_naive_ts": [],
    "malformed_uppercase_hex": [],
    "malformed_hex_trailing_newline": [],
    "malformed_top_level_conflict": [],
    "top_level_equal_payload": [],
    "top_level_only": [],
    "identity_missing_uuid": [],
    "identity_foreign_uuid": [],
    "identity_uppercase_uuid": [],
    "identity_request_missing_uuid": [],
    "identity_request_foreign_uuid": [],
    "crlf_request_row_digest_without_cr": [],
    "crlf_request_row_digest_with_cr": [],
    "double_cr_request_row": [],
    "bare_cr_inside_request_row": [],
    "bom_request_row": [],
    "duplicate_identical_rows": [],
    "duplicate_lf_and_crlf_rows": [],
    "bound_request_id": [V2_TS],
    "legacy_closure_without_withdraws": [],
    "legacy_ambiguous_without_withdraws": [V1_TS, V2_TS],
    "legacy_ambiguous_request_ts_utc": [V1_TS],
    "unrelated_task_withdrawal": [V2_TS],
}


def child_env(runtime_root):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("AGENT_BRIDGE_") and k.upper() != "PSMODULEPATH"}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime_root)
    return env


def run_selector(tmp_path, shell, rows, tail=None):
    shared = tmp_path / "shared"
    shared.mkdir(exist_ok=True)
    (shared / "events.jsonl").write_bytes(b"".join(r + b"\n" for r in rows))
    command = [shell, "-NoProfile", "-NonInteractive", "-File", str(BIN / "Get-BridgeNextAction.ps1"),
               "-Agent", TARGET, "-Json", "-Now", NOW]
    if tail is not None:
        command += ["-Tail", str(tail)]
    result = subprocess.run(command, env=child_env(tmp_path), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout[result.stdout.index("{"):])


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("case", sorted(CASES))
def test_powershell_exact_withdrawal(tmp_path, shell, case):
    rows, expected_open, expected_diagnostics = scenario(case)
    data = run_selector(tmp_path, shell, rows)
    assert data["open_incoming_count"] == len(expected_open), data
    if expected_open:
        assert data["action"] == "answer_incoming"
        assert data["incoming"]["ts_utc"] == expected_open[-1]
        assert data["incoming"]["task_id"] == TASK
    else:
        assert data["action"] == "claim_unblocked_work", data
    kinds = [item["kind"] for item in data.get("withdrawal_diagnostics", [])]
    assert kinds == expected_diagnostics, data
    for item in data.get("withdrawal_diagnostics", []):
        assert item["task_id"] == TASK and item["withdrawal_ts_utc"] == W_TS


def test_base_expectations_differ_only_where_the_fix_applies():
    """Documents the RED set: on 9645e93d these cases routed differently."""
    changed = sorted(case for case, base in CASES.items() if base != scenario(case)[1])
    assert changed == sorted([
        "d1_exact_newer_of_two", "d1_exact_older_of_two", "d2_single_ts_mismatch", "hash_mismatch",
        "status_mismatch", "type_mismatch", "agent_mismatch", "task_mismatch",
        "malformed_not_object", "malformed_null", "malformed_missing_ts", "malformed_naive_ts",
        "malformed_uppercase_hex", "malformed_hex_trailing_newline", "malformed_top_level_conflict",
        "identity_missing_uuid", "identity_foreign_uuid", "identity_request_missing_uuid",
        "identity_request_foreign_uuid", "crlf_request_row_digest_with_cr", "double_cr_request_row",
        "bare_cr_inside_request_row", "bom_request_row", "duplicate_identical_rows",
        "duplicate_lf_and_crlf_rows",
    ])


def mixed_log():
    lines = [row(V1), b"", row(V2) + b"\r", row(dict(V2, ts_utc="2026-10-06T17:40:00Z")) + b"\r" + row(V1),
             b"not json", "{\"x\":\"ä\"}".encode("utf-8"), b"\xff\xfe broken", row(withdrawal())]
    return b"".join(line + b"\n" for line in lines) + row(dict(V1, ts_utc="2026-10-06T17:50:00Z"))


READER_PROBE = r"""
param([string] $Script, [string] $Path, [int] $Tail)
$ErrorActionPreference = 'Stop'
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Script, [ref]$null, [ref]$null)
foreach ($name in 'Read-BridgeTailBytes', 'Read-BridgeEventObjects') {
    $fn = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
    . ([scriptblock]::Create($fn.Extent.Text))
}
$digests = New-Object System.Collections.Generic.List[string]
$events = @(Read-BridgeEventObjects -Path $Path -MaxLines $Tail -RowSha256 $digests)
[pscustomobject]@{
    events = @($events | ForEach-Object { [string]$_.ts_utc + '|' + [string]$_.status })
    digests = @($digests | ForEach-Object { if ($null -eq $_) { '' } else { $_ } })
} | ConvertTo-Json -Compress
"""

# The mixed log as Get-Content cuts it (LF, CRLF and bare CR end a line), with
# each line's event key and expected reader-row digest ("" = not addressable:
# a CR remains in the LF row, here the bare-CR split row). Get-Content -Tail
# itself is not the oracle: on this log it truncates the first line of some
# windows (observed on pwsh 7.6.6 and 5.1), which the base selector inherited.
MIXED_LINES = [
    ("2026-10-06T17:20:00Z|request", hashlib.sha256(row(V1)).hexdigest()),
    None,
    (V2_TS + "|review_requested", hashlib.sha256(row(V2)).hexdigest()),
    ("2026-10-06T17:40:00Z|review_requested", ""),
    ("2026-10-06T17:20:00Z|request", ""),
    None,
    None,
    None,
    (W_TS + "|withdrawn", hashlib.sha256(row(withdrawal())).hexdigest()),
    ("2026-10-06T17:50:00Z|request", hashlib.sha256(row(dict(V1, ts_utc="2026-10-06T17:50:00Z"))).hexdigest()),
]


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("tail", [0, 1, 2, 3, 4, 5, 6, 9, 10, 50])
def test_reader_window_and_reader_row_digests(tmp_path, shell, tail):
    log = tmp_path / "events.jsonl"
    log.write_bytes(mixed_log())
    probe = tmp_path / "probe.ps1"
    probe.write_text(READER_PROBE, encoding="utf-8-sig")
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(probe),
                             "-Script", str(BIN / "Get-BridgeNextAction.ps1"), "-Path", str(log), "-Tail", str(tail)],
                            env=child_env(tmp_path), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    events = data["events"] if isinstance(data["events"], list) else [data["events"]]
    digests = data["digests"] if isinstance(data["digests"], list) else [data["digests"]]
    window = MIXED_LINES if tail <= 0 else MIXED_LINES[-tail:]
    assert list(zip(events, digests)) == [line for line in window if line is not None]
    assert len(events) == len(digests)