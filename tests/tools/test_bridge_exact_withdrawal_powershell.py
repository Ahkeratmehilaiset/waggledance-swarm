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


def case_parity_scenario(case, v2, exact):
    malformed = ["malformed_withdrawal"]
    wrong = dict(exact, raw_line_sha256="0" * 64)
    if case == "case_owner_name":
        owner = dict(V2, agent="Fable-5")
        raw = row(owner)
        return [raw, row(withdrawal(descriptor(owner, raw), agent="Fable-5"))], [V2_TS], ["withdrawal_identity_unverified"]
    if case in ("case_descriptor_agent_key", "case_descriptor_digest_key"):
        key = "agent" if case.endswith("agent_key") else "raw_line_sha256"
        bad = dict(exact)
        bad[key.upper()] = bad.pop(key)
        return [v2, row(withdrawal(bad))], [V2_TS], malformed
    if case == "case_closure_uuid_key":
        event = withdrawal(exact)
        event["Agent_Uuid"] = event.pop("agent_uuid")
        return [v2, row(event)], [V2_TS], ["withdrawal_identity_unverified"]
    if case in ("case_top_member_exact", "case_top_member_wrong_digest"):
        event = withdrawal()
        event["Withdraws"] = exact if case.endswith("exact") else wrong
        return [v2, row(event)], [V2_TS], malformed
    if case in ("case_payload_member_exact", "case_payload_member_wrong_digest"):
        event = withdrawal()
        event["payload"] = {"WITHDRAWS": exact if case.endswith("exact") else wrong}
        return [v2, row(event)], [V2_TS], malformed
    if case == "case_payload_key_variant":
        event = withdrawal()
        event.pop("payload")
        event["Payload"] = {"withdraws": exact}
        return [v2, row(event)], [V2_TS], malformed
    if case == "case_exact_top_plus_variant_payload":
        # Same object holding withdraws and Withdraws: PS 5.1 and 7
        # ConvertFrom-Json both reject keys that differ only in case, so the
        # row is no event at all (non-closing, nothing to diagnose).
        event = withdrawal(exact)
        event["withdraws"] = exact
        event["payload"] = {"withdraws": exact, "Withdraws": exact}
        return [v2, row(event)], [V2_TS], []
    if case == "case_exact_payload_plus_variant_top":
        event = withdrawal(exact)
        event["Withdraws"] = exact
        return [v2, row(event)], [V2_TS], malformed
    if case == "case_target_answer_variant":
        answer = {"ts_utc": W_TS, "agent": TARGET, "type": "message", "task_id": TASK, "status": "answered",
                  "to": OWNER, "message": "fixture answer", "payload": {"Withdraws": exact},
                  "agent_uuid": REGISTRY[TARGET]}
        return [v2, row(answer)], [V2_TS], []
    if case == "case_bound_request_variant":
        bound = request(V2_TS, "review_requested", nonce="n-1")
        raw = row(bound)
        event = withdrawal()
        event["payload"] = {"Withdraws": descriptor(bound, raw)}
        return [raw, row(event)], [V2_TS], []
    if case == "case_control_request_variant":
        control = request(V2_TS, "changes_requested", type="finding")
        raw = row(control)
        event = withdrawal()
        event["payload"] = {"Withdraws": descriptor(control, raw)}
        # Still open; the variant member is reported as malformed.
        return [raw, row(event)], [V2_TS], malformed
    raise AssertionError(case)


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
        # S1 parity: ts_utc is compared as written, so an equal instant in
        # another spelling is a mismatch.
        return [v2, row(withdrawal(dict(exact, ts_utc="2026-10-06T20:29:29.9136087+03:00")))], [V2_TS], []
    if case == "ts_extra_fraction_digit":
        return [v2, row(withdrawal(dict(exact, ts_utc="2026-10-06T17:29:29.91360870Z")))], [V2_TS], []
    if case == "control_signal_exact_withdrawal":
        control = request(V2_TS, "changes_requested", type="finding")
        raw = row(control)
        return [raw, row(withdrawal(descriptor(control, raw)))], [V2_TS], []
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
        return [v2, row(withdrawal(exact, agent_uuid=REGISTRY[OWNER].upper()))], [V2_TS], ["withdrawal_identity_unverified"]
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
    if case in {"idle_progress_wrong_withdraws", "idle_progress_without_withdraws"}:
        # Lead 05:05Z D2 sibling: an idle-protocol response must not turn a
        # mismatched withdrawal into progress. PS has no idle-progress path;
        # this pins that the withdrawal path still closes nothing.
        idle = request(V2_TS, "review_requested",
                       payload={"protocol_version": "idle-protocol.v1", "proposal_id": "p"})
        raw = row(idle)
        response = withdrawal()
        response["payload"] = {"protocol_version": "idle-protocol.v1", "responds_to": "p"}
        if case == "idle_progress_wrong_withdraws":
            response["payload"]["withdraws"] = dict(descriptor(idle, raw), ts_utc="2026-10-06T16:00:00Z",
                                                    raw_line_sha256="1" * 64)
            return [raw, row(response)], [V2_TS], []
        return [raw, row(response)], [], []
    if case.startswith("partially_bound_"):
        # Lead 05:20Z: any correlation field binds the request, so an exact
        # withdrawal descriptor must not close it, and the withdraws-bearing
        # closure needs explicit correlation (expected_responders alone has
        # none; the base closed it).
        field = case[len("partially_bound_"):]
        values = {"nonce": "n-1", "token": "t-1", "task_revision": "r-1",
                  "expected_responders": {TARGET: {"agent_uuid": REGISTRY[TARGET]}}}
        if field == "payload_nonce":
            bound = request(V2_TS, "review_requested", payload={"nonce": "n-1"})
        else:
            bound = request(V2_TS, "review_requested", **{field: values[field]})
        raw = row(bound)
        return [raw, row(withdrawal(descriptor(bound, raw)))], [V2_TS], []
    if case == "correlated_expected_responders_withdrawal":
        bound = request(V2_TS, "review_requested", expected_responders={TARGET: {"agent_uuid": REGISTRY[TARGET]}})
        raw = row(bound)
        return [raw, row(withdrawal(descriptor(bound, raw), request_ts_utc=V2_TS))], [], []
    if case in {"target_answer_with_withdraws", "target_answer_without_withdraws"}:
        # RCO1 S2D-F1 (Python test :317 analogue): an answer by the selector
        # target that carries withdraws must not close an unbound request.
        answer = {"ts_utc": W_TS, "agent": TARGET, "type": "message", "task_id": TASK, "status": "answered",
                  "to": OWNER, "message": "fixture answer", "payload": {}, "agent_uuid": REGISTRY[TARGET]}
        if case == "target_answer_with_withdraws":
            answer["payload"] = {"withdraws": exact}
            return [v2, row(answer)], [V2_TS], []
        return [v2, row(answer)], [], []
    if case == "bound_full_answer_with_withdraws":
        bound = request(V2_TS, "review_requested", request_id="fixture-r2")
        raw = row(bound)
        answer = {"ts_utc": W_TS, "agent": TARGET, "type": "message", "task_id": TASK, "status": "answered",
                  "to": OWNER, "message": "fixture bound answer", "agent_uuid": REGISTRY[TARGET],
                  "in_reply_to_request_id": "fixture-r2",
                  "in_reply_to_requester": {"agent": OWNER, "agent_uuid": REGISTRY[OWNER]},
                  "payload": {"withdraws": descriptor(bound, raw)}}
        return [raw, row(answer)], [], []
    if case.startswith("case_"):
        # C69-L1 (plan 97083993): withdrawal-path names are ordinal; any ASCII
        # case variant of withdraws is a present, malformed member.
        return case_parity_scenario(case, v2, exact)
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
    "ts_extra_fraction_digit": [],
    "control_signal_exact_withdrawal": [V2_TS],
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
    "identity_uppercase_uuid": [V2_TS],
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
    "partially_bound_nonce": [V2_TS],
    "partially_bound_token": [V2_TS],
    "partially_bound_task_revision": [V2_TS],
    "partially_bound_expected_responders": [],
    "partially_bound_payload_nonce": [V2_TS],
    "correlated_expected_responders_withdrawal": [],
    "target_answer_with_withdraws": [],
    "target_answer_without_withdraws": [],
    "bound_full_answer_with_withdraws": [],
    "idle_progress_wrong_withdraws": [],
    "idle_progress_without_withdraws": [],
    "case_owner_name": [],
    "case_descriptor_agent_key": [],
    "case_descriptor_digest_key": [],
    "case_closure_uuid_key": [],
    "case_top_member_exact": [],
    "case_top_member_wrong_digest": [],
    "case_payload_member_exact": [],
    "case_payload_member_wrong_digest": [],
    "case_payload_key_variant": [],
    "case_exact_top_plus_variant_payload": [V2_TS],
    "case_exact_payload_plus_variant_top": [],
    "case_target_answer_variant": [],
    "case_bound_request_variant": [V2_TS],
    "case_control_request_variant": [V2_TS],
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
        "duplicate_lf_and_crlf_rows", "idle_progress_wrong_withdraws", "ts_equal_instant_other_spelling",
        "ts_extra_fraction_digit", "partially_bound_expected_responders", "target_answer_with_withdraws",
        "case_owner_name", "case_descriptor_agent_key", "case_descriptor_digest_key", "case_closure_uuid_key",
        "case_top_member_exact", "case_top_member_wrong_digest", "case_payload_member_exact",
        "case_payload_member_wrong_digest", "case_payload_key_variant", "case_exact_payload_plus_variant_top",
        "case_target_answer_variant",
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

PURE_API_PROBE = r"""
param([string] $Contract, [string] $Field, [string] $Placement)
$ErrorActionPreference = 'Stop'
. $Contract
$uuid = 'f8b1e5c0-3d2a-4e6b-9c1f-7a0d5e2b4c80'
$request = [pscustomobject]@{ agent = 'fable-5'; type = 'message'; status = 'review_requested'; task_id = 't'
    ts_utc = '2026-10-06T17:29:29Z'; agent_uuid = $uuid; payload = [pscustomobject]@{} }
$value = if ($Field -eq 'expected_responders') { [pscustomobject]@{ 'codex-tools-1' = [pscustomobject]@{ agent_uuid = 'x' } } } else { 'v-1' }
if ($Field -ne 'none') {
    if ($Placement -eq 'payload') { $request.payload | Add-Member -NotePropertyName $Field -NotePropertyValue $value }
    else { $request | Add-Member -NotePropertyName $Field -NotePropertyValue $value }
}
$digest = 'a' * 64
$closure = [pscustomobject]@{ agent = 'fable-5'; type = 'message'; status = 'withdrawn'; task_id = 't'
    ts_utc = '2026-10-06T18:00:00Z'; agent_uuid = $uuid
    payload = [pscustomobject]@{ withdraws = [pscustomobject]@{ agent = 'fable-5'; type = 'message'; status = 'review_requested'
        task_id = 't'; ts_utc = '2026-10-06T17:29:29Z'; raw_line_sha256 = $digest } } }
Get-BridgeWithdrawalTarget -Request $request -Closure $closure -RequestRawSha256 $digest -RequestPosition 0 -ClosurePosition 1 -RegisteredAgentUuid $uuid
"""


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("field,placement,expected", [
    ("none", "top", "exact"),
    ("request_id", "top", "mismatch"),
    ("nonce", "top", "mismatch"),
    ("token", "top", "mismatch"),
    ("task_revision", "top", "mismatch"),
    ("expected_responders", "top", "mismatch"),
    ("nonce", "payload", "mismatch"),
    ("expected_responders", "payload", "mismatch"),
])
def test_pure_api_bound_requests_are_never_withdrawn(tmp_path, shell, field, placement, expected):
    probe = tmp_path / "pure.ps1"
    probe.write_text(PURE_API_PROBE, encoding="utf-8-sig")
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(probe),
                             "-Contract", str(BIN / "BridgeRequestContract.ps1"), "-Field", field, "-Placement", placement],
                            env=child_env(tmp_path), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


SELECTOR_FILES = ("Get-BridgeNextAction.ps1", "BridgeRequestContract.ps1", "BridgeRoster.ps1", "BridgeEventClassifier.ps1")


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("registry", ["missing", "invalid_json", "agent_absent", "not_a_uuid", "unreadable_directory", "valid"])
def test_identity_registry_failures_never_close(tmp_path, shell, registry):
    """A missing or unreadable registry gives withdrawal_identity_unverified, never a closure."""
    install = tmp_path / "install"
    (install / ".agent-bridge" / "bin").mkdir(parents=True)
    for name in SELECTOR_FILES:
        shutil.copyfile(BIN / name, install / ".agent-bridge" / "bin" / name)
    configs = install / "configs"
    configs.mkdir()
    path = configs / "bridge_identity_registry.json"
    identities = dict(REGISTRY)
    if registry == "invalid_json":
        path.write_text("{not json", encoding="utf-8")
    elif registry == "agent_absent":
        identities.pop(OWNER)
        path.write_text(json.dumps({"identities": identities}), encoding="utf-8")
    elif registry == "not_a_uuid":
        identities[OWNER] = "fable-5"
        path.write_text(json.dumps({"identities": identities}), encoding="utf-8")
    elif registry == "unreadable_directory":
        path.mkdir()
    elif registry == "valid":
        path.write_text(json.dumps({"identities": identities}), encoding="utf-8")
    runtime = tmp_path / "runtime"
    (runtime / "shared").mkdir(parents=True)
    v2 = row(V2)
    (runtime / "shared" / "events.jsonl").write_bytes(v2 + b"\n" + row(withdrawal(descriptor(V2, v2))) + b"\n")
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File",
                             str(install / ".agent-bridge" / "bin" / "Get-BridgeNextAction.ps1"),
                             "-Agent", TARGET, "-Json", "-Now", NOW],
                            env=child_env(runtime), capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=60)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout[result.stdout.index("{"):])
    kinds = [item["kind"] for item in data.get("withdrawal_diagnostics", [])]
    if registry == "valid":
        assert data["open_incoming_count"] == 0 and kinds == []
    else:
        assert data["open_incoming_count"] == 1, data
        assert kinds == ["withdrawal_identity_unverified"]