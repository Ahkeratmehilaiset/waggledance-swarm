"""Falsy request_id values in the PowerShell event classifier (.agent-bridge/bin/BridgeEventClassifier.ps1).

A request_id that is present, non-null and not exactly "" is an explicit (possibly invalid) request id. false, 0, 0.0
and [] used to fail the truthiness test, so an assigned wake_request carrying them was not request-like and stayed
invisible, while {} or [7] were visible. Visibility is not binding: reply binding still refuses any non-string id.
null, "" and an absent request_id keep the legacy no-id path. Self-contained; runs in powershell.exe 5.1 and pwsh 7.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / ".agent-bridge" / "bin"
SHELLS = [s for s in dict.fromkeys(filter(None, [shutil.which("powershell.exe"), shutil.which("pwsh")]))]
pytestmark = pytest.mark.skipif(not SHELLS, reason="needs Windows PowerShell or pwsh")
LEAD = {"agent": "codex-lead-1", "agent_uuid": "uuid-lead-1", "session_id": "sess-a", "run_id": "run-a"}
TARGET = "codex-tools-1"
FALSY = {"false": False, "zero": 0, "zero_float": 0.0, "empty_list": []}
TRUTHY_INVALID = {"empty_object": {}, "list7": [7], "true": True}
MISSING = object()


def event(rid=MISSING, *, type_="wake_request", status="assigned", task="codex-lead-1/t", message="m",
          ts="2026-10-02T04:00:00Z", **extra):
    row = dict(LEAD, ts_utc=ts, type=type_, status=status, task_id=task, to=TARGET, message=message,
               request_digest="a" * 64, payload={})
    if rid is not MISSING:
        row["request_id"] = rid
    row.update(extra)
    return row


def scrubbed_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("AGENT_BRIDGE_", "WD_"))}
    env.update(extra)
    return env


PROBE = r"""
param([string]$Bin, [string]$Rows)
$ErrorActionPreference = 'Stop'
. (Join-Path $Bin 'BridgeEventClassifier.ps1')
. (Join-Path $Bin 'BridgeRequestContract.ps1')
foreach ($line in [IO.File]::ReadAllLines($Rows)) {
    $case = $line | ConvertFrom-Json
    $e = $case.event
    $out = [ordered]@{ name = $case.name; request_like = [bool](Test-BridgeRequestLikeEvent -Event $e);
        answer = [bool](Test-BridgeAnswerEvent -Event $e); wake = [bool](Test-BridgeWakeEligible -Event $e) }
    if ($null -ne $case.reply) {
        $out.binds = [bool](Test-BridgeReplyBinding -Request $e -Reply $case.reply -Target 'codex-tools-1')
    }
    $out | ConvertTo-Json -Compress
}
"""


def classify(shell, tmp_path, cases):
    script = tmp_path / "probe.ps1"
    script.write_text(PROBE, encoding="utf-8")
    rows = tmp_path / "rows.jsonl"
    rows.write_text("".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")
    r = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(script),
                        "-Bin", str(BIN), "-Rows", str(rows)], env=scrubbed_env(), capture_output=True, text=True,
                       encoding="utf-8", timeout=180)
    assert r.returncode == 0, r.stderr
    return {d["name"]: d for d in (json.loads(l) for l in r.stdout.splitlines() if l.startswith("{"))}


def reply_for(rid):
    return dict(agent=TARGET, agent_uuid="uuid-tools", session_id="s-t", run_id="r-t", ts_utc="2026-10-02T04:01:00Z",
                type="message", status="answered", task_id="codex-lead-1/t", to=LEAD["agent"], message="done",
                in_reply_to_request_id=rid, in_reply_to_request_digest="a" * 64,
                in_reply_to_requester={k: LEAD[k] for k in ("agent", "agent_uuid", "session_id", "run_id")})


def cases():
    out = []
    for name, rid in {**FALSY, **TRUTHY_INVALID}.items():
        out.append({"name": f"assigned_{name}", "event": event(rid), "reply": reply_for(rid)})
    out.append({"name": "assigned_valid", "event": event("r-1"), "reply": reply_for("r-1")})
    for name, rid in {"null": None, "empty": "", "absent": MISSING}.items():
        out.append({"name": f"assigned_{name}", "event": event(rid)})
        out.append({"name": f"request_status_{name}", "event": event(rid, status="request")})
    for name, rid in FALSY.items():
        out += [
            {"name": f"cancelled_{name}", "event": event(rid, status="cancelled")},
            {"name": f"withdrawn_{name}", "event": event(rid, status="withdrawn_by_lead")},
            {"name": f"decision_approved_{name}", "event": event(rid, type_="decision", status="approved")},
            {"name": f"heartbeat_{name}", "event": event(rid, type_="heartbeat")},
            {"name": f"ack_{name}", "event": event(rid, status="received")},
            {"name": f"answered_{name}", "event": event(rid, type_="message", status="answered")},
            {"name": f"no_task_{name}", "event": event(rid, task="")},
            {"name": f"unaddressed_{name}", "event": event(rid, to="")},
            {"name": f"list_status_{name}", "event": event(rid, status=["cancelled"])},
            {"name": f"informational_{name}", "event": event(rid, type_="message", status="notice",
                                                             payload={"notification": "informational"})},
        ]
    return out


@pytest.fixture(scope="module", params=SHELLS, ids=lambda s: "ps51" if "WindowsPowerShell" in s else "ps7")
def results(request, tmp_path_factory):
    return classify(request.param, tmp_path_factory.mktemp("cls"), cases())


@pytest.mark.parametrize("name", list(FALSY) + list(TRUTHY_INVALID))
def test_an_assigned_wake_with_any_non_null_non_empty_id_is_request_like_but_never_binds(results, name):
    r = results[f"assigned_{name}"]
    assert r["request_like"] is True and r["wake"] is True and r["answer"] is False, r
    assert r["binds"] is False, r


def test_positive_control_valid_string_id_is_request_like_and_binds(results):
    assert results["assigned_valid"]["request_like"] is True and results["assigned_valid"]["binds"] is True


@pytest.mark.parametrize("name", ["null", "empty", "absent"])
def test_null_empty_and_absent_ids_keep_the_legacy_status_rule(results, name):
    assert results[f"assigned_{name}"]["request_like"] is False       # assigned is not a legacy request status
    assert results[f"request_status_{name}"]["request_like"] is True


@pytest.mark.parametrize("name", list(FALSY))
@pytest.mark.parametrize("control", ["cancelled", "withdrawn", "decision_approved", "heartbeat", "ack", "answered",
                                     "no_task", "unaddressed"])
def test_falsy_ids_do_not_revive_closures_acks_answers_or_unrouted_rows(results, name, control):
    assert results[f"{control}_{name}"]["request_like"] is False, results[f"{control}_{name}"]


@pytest.mark.parametrize("name", list(FALSY))
def test_falsy_id_matches_a_truthy_invalid_id_under_the_status_coercion_guard(results, name):
    # A non-string status reads as '' (never a closure word); with an explicit id the row is request-like exactly as
    # it already was for [7], so the falsy id gains no different authority.
    assert results[f"list_status_{name}"]["request_like"] is True


@pytest.mark.parametrize("name", list(FALSY))
def test_an_informational_notice_with_a_falsy_id_is_never_an_answer(results, name):
    r = results[f"informational_{name}"]
    assert r["answer"] is False and r["request_like"] is True, r


# ---------------------------------------------------------------- the real selector on an isolated runtime root
def select(shell, root, rows, now):
    (root / "shared").mkdir(parents=True, exist_ok=True)
    (root / "shared" / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    r = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                        str(BIN / "Get-BridgeNextAction.ps1"), "-Agent", TARGET, "-Now", now, "-Json"],
                       env=scrubbed_env(AGENT_BRIDGE_RUNTIME_ROOT=str(root)), capture_output=True, text=True,
                       encoding="utf-8", timeout=180)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout[r.stdout.index("{"):])


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: "ps51" if "WindowsPowerShell" in s else "ps7")
@pytest.mark.parametrize("name", list(FALSY))
def test_selector_sees_two_distinct_falsy_id_wakes_fresh_and_stale(shell, tmp_path, name):
    rid = FALSY[name]
    rows = [event(rid, task="codex-lead-1/a", message="one"), event(rid, task="codex-lead-1/b", message="two")]
    assert select(shell, tmp_path / "f", rows, "2026-10-02T04:10:00Z")["open_incoming_count"] == 2
    assert select(shell, tmp_path / "s", rows, "2026-10-02T17:00:00Z")["stale_incoming_count"] == 2
    same_task = [event(rid, message="one"), event(rid, message="two", ts="2026-10-02T04:01:00Z")]
    assert select(shell, tmp_path / "t", same_task, "2026-10-02T04:10:00Z")["open_incoming_count"] == 2


@pytest.mark.parametrize("shell", SHELLS, ids=lambda s: "ps51" if "WindowsPowerShell" in s else "ps7")
def test_selector_controls_legacy_and_cancelled_falsy_wakes_stay_quiet(shell, tmp_path):
    legacy = [event(None, task="codex-lead-1/a"), event(MISSING, task="codex-lead-1/b")]
    assert select(shell, tmp_path / "l", legacy, "2026-10-02T04:10:00Z")["open_incoming_count"] == 0
    cancelled = [event(0, status="cancelled", task="codex-lead-1/a"), event(False, status="cancelled", task="codex-lead-1/b")]
    assert select(shell, tmp_path / "c", cancelled, "2026-10-02T04:10:00Z")["open_incoming_count"] == 0
