"""Request view keys for conflicting / non-string request_id values (fable-5, composed from the RCO2 view-key proposal).

A valid request_id is an exact non-empty string and keeps its key; null and "" keep the legacy key. Any other non-null
request_id (a top/payload conflict, a number, a bool, a list, an object) is never a valid id: its key is its own typed
canonical request content, so it can neither alias a valid id ("7" vs 7, "req-7" vs ["req-7"]) nor collapse with another
invalid request, while exact repeats still coalesce. Same-id duplicates compare their content ORDINALLY.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from tools import bridge_next_action as nxt
from tools import bridge_v2_request_contract as port
from waggledance.core import bridge_request_contract as core

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / ".agent-bridge" / "bin" / "BridgeRequestContract.ps1"
SELECTOR = ROOT / ".agent-bridge" / "bin" / "Get-BridgeNextAction.ps1"
SHELLS = list(dict.fromkeys(filter(None, [shutil.which("powershell.exe"), shutil.which("pwsh")])))
LEAD = {"agent": "codex-lead-1", "agent_uuid": "uuid-lead-1", "session_id": "sess-a", "run_id": "run-a"}
SOFT = "­"
ZW = "​"


def req(rid, task="codex-lead-1/t", ts="2026-10-02T02:50:00Z", payload_rid=None, message="m", **over):
    row = dict(LEAD, ts_utc=ts, type="wake_request", status="assigned", task_id=task, to="codex-tools-1",
               message=message, request_digest="a" * 64, payload={"task_revision": "r1"})
    if rid is not None:
        row["request_id"] = rid
    if payload_rid is not None:
        row["payload"]["request_id"] = payload_rid
    row.update(over)
    return row


def conflicting(task, top, pay, **kw):
    return req(top, task=task, payload_rid=pay, **kw)


def dedup(rows):
    return nxt._deduplicate_repeated_wake_requests(copy.deepcopy(rows), agent="codex-tools-1")


def _env():
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("AGENT_BRIDGE_", "WD_", "PSMODULEPATH"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


# --- Python: request_key (core == port) ------------------------------------------------------------------------------

KEY_ROWS = {
    "valid": req("req-7"),
    "valid_retry": req("req-7"),
    "seven_str": req("7"),
    "seven_int": req(7),
    "seven_float": req(7.0),
    "true": req(True),
    "one": req(1),
    "false": req(False),
    "zero": req(0),
    "empty_list": req([]),
    "list": req(["req-7"]),
    "object": req({"id": "req-7"}),
    "none": req(None),
    "empty": req(""),
    "conflict_a": conflicting("codex-lead-1/a", "a1", "a2"),
    "conflict_a_retry": conflicting("codex-lead-1/a", "a1", "a2"),
    "conflict_b": conflicting("codex-lead-1/b", "b1", "b2"),
    "conflict_case": conflicting("codex-lead-1/a", "A1", "a2"),
    "conflict_soft": conflicting("codex-lead-1/a", "a1" + SOFT, "a2"),
    "conflict_zw": conflicting("codex-lead-1/a", "a1" + ZW, "a2"),
}


@pytest.mark.parametrize("module", [core, port], ids=["core", "port"])
def test_py_request_key_valid_and_legacy_shapes_are_unchanged(module):
    assert module.request_key(KEY_ROWS["valid"], "t") == ("id", "codex-lead-1", "req-7", "t")
    legacy = ("legacy", "codex-lead-1", "codex-lead-1/t", "assigned", "t")
    assert module.request_key(KEY_ROWS["none"], "t") == legacy
    assert module.request_key(KEY_ROWS["empty"], "t") == legacy


@pytest.mark.parametrize("module", [core, port], ids=["core", "port"])
def test_py_invalid_ids_get_typed_content_keys(module):
    keys = {name: module.request_key(row, "t") for name, row in KEY_ROWS.items()}
    invalid = [n for n in KEY_ROWS if n not in ("valid", "valid_retry", "seven_str", "none", "empty")]
    for name in invalid:
        assert keys[name] == ("invalid-id", "codex-lead-1", module.request_content(KEY_ROWS[name]), "t"), name
    assert keys["valid"] == keys["valid_retry"] and keys["conflict_a"] == keys["conflict_a_retry"]
    distinct = [n for n in KEY_ROWS if n not in ("valid_retry", "conflict_a_retry", "empty")]
    assert len({keys[n] for n in distinct}) == len(distinct), keys
    assert all(type(part) is str for key in keys.values() for part in key)


def test_py_core_equals_port_and_keys_are_process_stable():
    for row in KEY_ROWS.values():
        assert core.request_key(row, "t") == port.request_key(row, "t")
    code = ("import sys, json; from waggledance.core.bridge_request_contract import request_key; "
            "rows = json.loads(sys.stdin.read()); print(json.dumps([request_key(r, 't') for r in rows]))")
    rows = json.dumps(list(KEY_ROWS.values()))
    runs = {subprocess.run([sys.executable, "-c", code], input=rows, capture_output=True, text=True, check=True,
                           cwd=ROOT, env=_env()).stdout for _ in range(2)}
    assert len(runs) == 1, runs
    assert json.loads(runs.pop()) == [list(core.request_key(r, "t")) for r in KEY_ROWS.values()]


def test_py_two_distinct_conflicting_requests_stay_apart():
    out = dedup([conflicting("codex-lead-1/a", "a1", "a2"), conflicting("codex-lead-1/b", "b1", "b2")])
    assert len(out) == 2 and not any(r.get("request_binding_conflict") for r in out), out


def test_py_exact_duplicate_conflicting_request_coalesces():
    row = conflicting("codex-lead-1/a", "a1", "a2")
    assert len(dedup([row, copy.deepcopy(row)])) == 1


@pytest.mark.parametrize("bad", [7, ["7"], {"id": "7"}, True, 0, False, []])
def test_py_non_string_request_id_never_poisons_a_valid_id(bad):
    out = dedup([req("7", message="valid"), req(bad, task="codex-lead-1/other", message="malformed")])
    first = [r for r in out if r.get("message") == "valid"]
    assert len(out) == 2 and len(first) == 1 and not first[0].get("request_binding_conflict"), out


def test_py_valid_same_id_different_content_is_still_a_visible_conflict():
    for other in ("two", "one" + SOFT, "one" + ZW):
        out = dedup([req("r-1", message="one"), req("r-1", message=other)])
        assert len(out) == 1 and out[0].get("request_binding_conflict") is True, other


def test_py_valid_exact_retry_and_legacy_unchanged():
    assert len(dedup([req("r-1"), req("r-1")])) == 1
    assert len(dedup([req(None, ts="2026-10-02T02:50:00Z"), req(None, ts="2026-10-02T02:51:00Z")])) == 1
    assert len(dedup([req("", ts="2026-10-02T02:50:00Z"), req("", ts="2026-10-02T02:51:00Z")])) == 1


# --- PowerShell: Get-BridgeRequestViewKey / Set-BridgeRequestViewEntry ------------------------------------------------

def _ps(shell, tmp_path, body, rows):
    fixture = tmp_path / "rows.json"
    fixture.write_text(json.dumps(rows), encoding="utf-8")
    command = (f". '{CONTRACT}'; $rows = @(Get-Content -LiteralPath '{fixture}' -Raw -Encoding UTF8 | ConvertFrom-Json | ForEach-Object {{ $_ }}); "
               + body)
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True,
                            text=True, encoding="utf-8", timeout=180, env=_env())
    assert result.returncode == 0, result.stderr
    return result.stdout.lstrip("﻿")


KEYS_BODY = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
             "ConvertTo-Json -Compress -InputObject @($rows | ForEach-Object { [string](Get-BridgeRequestViewKey $_ 't') })")


@pytest.mark.parametrize("shell", SHELLS)
def test_ps_view_keys_are_typed_and_never_alias(shell, tmp_path):
    names = list(KEY_ROWS)
    keys = dict(zip(names, json.loads(_ps(shell, tmp_path, KEYS_BODY, list(KEY_ROWS.values())))))
    assert keys["valid"] == "t|id|codex-lead-1|req-7" and keys["seven_str"] == "t|id|codex-lead-1|7"
    assert keys["none"] == keys["empty"] == "t|legacy|codex-lead-1|codex-lead-1/t"
    for name in names:
        if name not in ("valid", "valid_retry", "seven_str", "none", "empty"):
            assert keys[name].startswith("t|invalid-id|codex-lead-1|{"), (name, keys[name])
    assert keys["valid"] == keys["valid_retry"] and keys["conflict_a"] == keys["conflict_a_retry"]
    # 7.0 parses to the same PowerShell number text as 7 in this shell family; every other invalid id is distinct
    distinct = [n for n in names if n not in ("valid_retry", "conflict_a_retry", "empty", "seven_float")]
    assert len({keys[n] for n in distinct}) == len(distinct), keys
    rerun = dict(zip(names, json.loads(_ps(shell, tmp_path, KEYS_BODY, list(KEY_ROWS.values())))))
    assert rerun == keys


@pytest.mark.parametrize("shell", SHELLS)
def test_ps_same_id_entry_compares_content_ordinally(shell, tmp_path):
    body = ("$map = [System.Collections.Generic.Dictionary[string,object]]::new([StringComparer]::Ordinal); "
            "foreach ($r in $rows) { Set-BridgeRequestViewEntry $map (Get-BridgeRequestViewKey $r 't') $r }; "
            "ConvertTo-Json -Compress -InputObject @($map.Values | ForEach-Object { "
            "[bool]($null -ne $_.PSObject.Properties['request_binding_conflict']) })")
    for other, conflict in (("one", False), ("two", True), ("one" + SOFT, True), ("one" + ZW, True)):
        flags = json.loads(_ps(shell, tmp_path, body, [req("r-1", message="one"), req("r-1", message=other)]))
        assert flags == [conflict], (other, flags)
    # same content, request_digest differing only invisibly / by type is a different binding as well
    for digest, conflict in (("a" * 64, False), ("a" * 64 + SOFT, True), ("a" * 64 + ZW, True), (7, True)):
        flags = json.loads(_ps(shell, tmp_path, body, [req("r-1"), req("r-1", request_digest=digest)]))
        assert flags == [conflict], (repr(digest), flags)


# --- PowerShell: the real selector (fresh and stale paths) -----------------------------------------------------------

def select(shell, tmp_path, rows):
    (tmp_path / "shared").mkdir(parents=True, exist_ok=True)
    (tmp_path / "shared" / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    env = _env()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(tmp_path)
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                             str(SELECTOR), "-Agent", "codex-tools-1", "-Now", "2026-10-02T03:00:00Z", "-Json"],
                            env=env, capture_output=True, text=True, encoding="utf-8", timeout=180)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("ids", [("a1", "a2", "b1", "b2"), ("r1", "r1" + SOFT, "r2", "r2" + ZW)],
                         ids=["plain", "invisible_only"])
def test_ps_two_distinct_conflicting_requests_stay_apart(shell, tmp_path, ids):
    # invisible_only: with the d09b ordinal getters alone these are two conflict markers and collapsed into one key
    rows = [conflicting("codex-lead-1/a", ids[0], ids[1]), conflicting("codex-lead-1/b", ids[2], ids[3])]
    out = select(shell, tmp_path, rows)
    assert out["open_incoming_count"] == 2 and out["open_incoming_event_count"] == 2, out
    old = "2026-10-01T10:00:00Z"
    stale = select(shell, tmp_path, [dict(r, ts_utc=old) for r in rows])
    assert stale["stale_incoming_request_count"] == 2, stale


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("bad", [7, ["req-7"], False], ids=["int", "one_element_list", "false"])
def test_ps_malformed_request_id_never_poisons_the_valid_id(shell, tmp_path, bad):
    valid = req("7" if bad == 7 else "req-7", message="valid")
    # a falsy request_id (false) is request-like for the PS classifier only with a request status
    out = select(shell, tmp_path, [valid, req(bad, task="codex-lead-1/other", message="malformed", status="request")])
    assert out["open_incoming_count"] == 2 and out["open_incoming_event_count"] == 2, out


@pytest.mark.parametrize("shell", SHELLS)
def test_ps_exact_duplicates_coalesce_and_valid_retries_are_unchanged(shell, tmp_path):
    row = conflicting("codex-lead-1/a", "a1", "a2")
    dup = select(shell, tmp_path, [row, copy.deepcopy(row)])
    assert dup["open_incoming_count"] == 1 and dup["open_incoming_event_count"] == 2, dup
    out = select(shell, tmp_path, [req("r-1"), req("r-1", ts="2026-10-02T02:51:00Z")])
    assert out["open_incoming_count"] == 1 and "request_binding_conflict" not in out["incoming"], out


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("other", ["two", "one" + SOFT, "one" + ZW], ids=["plain", "soft_hyphen", "zero_width"])
def test_ps_fresh_same_id_different_content_is_a_visible_conflict(shell, tmp_path, other):
    out = select(shell, tmp_path, [req("r-1", message="one"), req("r-1", message=other, ts="2026-10-02T02:51:00Z")])
    assert out["open_incoming_count"] == 1 and out["incoming"].get("request_binding_conflict") is True, out


@pytest.mark.parametrize("shell", SHELLS)
def test_ps_stale_path_keeps_distinct_invalid_requests_apart(shell, tmp_path):
    old = "2026-10-01T10:00:00Z"
    rows = [conflicting("codex-lead-1/a", "a1", "a2", ts=old), conflicting("codex-lead-1/b", "b1", "b2", ts=old),
            req("7", task="codex-lead-1/c", ts=old, message="valid"), req(7, task="codex-lead-1/d", ts=old)]
    out = select(shell, tmp_path, rows)
    assert out["open_incoming_count"] == 0 and out["stale_incoming_request_count"] == 4, out
    dup = select(shell, tmp_path, [rows[0], copy.deepcopy(rows[0])])
    assert dup["stale_incoming_request_count"] == 1, dup


@pytest.mark.parametrize("shell", SHELLS)
def test_ps_legacy_replay_check_is_ordinal(shell, tmp_path):
    """Same legacy wake key and ts: only an ORDINALLY identical replay keeps the first row; a row differing by an
    invisible character is a different request and replaces it, as Python _deduplicate_repeated_wake_requests does.
    (An unbound wake_request is request-like for the PS classifier only with a request status.)"""
    rows = [req(None, message="one", status="request"), req(None, message="one" + SOFT, status="request")]
    out = select(shell, tmp_path, rows)
    assert out["open_incoming_count"] == 1 and out["incoming"]["message"] == "one" + SOFT, out
    py = dedup([req(None, message="one"), req(None, message="one" + SOFT)])
    assert [r["message"] for r in py] == ["one" + SOFT]
