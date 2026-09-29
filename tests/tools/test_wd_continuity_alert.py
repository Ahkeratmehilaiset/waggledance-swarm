# SPDX-License-Identifier: BUSL-1.1
"""Send-WdContinuityAlert.ps1: one operator-visible alert per key, fail closed.

Every case runs the real helper in each available shell against a disposable
fixture bundle whose Write-AgentEvent.ps1 is a mock transport. No live bridge,
runtime root or lane state is touched.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "ops" / "windows" / "reboot" / "Send-WdContinuityAlert.ps1"
SHELLS = [s for s in dict.fromkeys([shutil.which("pwsh"), shutil.which("powershell.exe")]) if s]
pytestmark = pytest.mark.skipif(not SHELLS, reason="no PowerShell available")

AGENT = "codex-lead-1"
TASK = "codex-lead-1/permanent-continuity-integration-20260929"
THREAD = "01a0a654-12af-7d81-85fc-d75d515c5b65"
THREAD2 = "01a0eb82-1b6b-72f1-bf33-006f7bbfa266"
REASON = "hold_possible_control_token"
DIGEST = "a" * 64
BIN = "tools-bootstrap/.agent-bridge/bin/"

MOCK_WRITER = r'''
param([string]$Agent, [string]$Type, [string]$TaskId = '', [string]$Status = '', [string]$Message = '',
      [string]$To = '', [string]$PayloadJson = '{}', [string]$RequestId = '', [switch]$ReceiptJson)
$call = [ordered]@{Agent=$Agent; Type=$Type; TaskId=$TaskId; Status=$Status; Message=$Message; To=$To;
    PayloadJson=$PayloadJson; RequestId=$RequestId; ReceiptJson=[bool]$ReceiptJson;
    Bound=@($PSBoundParameters.Keys | Sort-Object)}
Add-Content -LiteralPath $env:WD_TEST_ALERT_CAPTURE -Value (ConvertTo-Json -InputObject $call -Compress) -Encoding UTF8
$mode = [string]$env:WD_TEST_ALERT_MODE
if ($mode -eq 'crash') { [Environment]::Exit(7) }
if ($mode -eq 'throw') { throw 'mock transport failure' }
if ($mode -eq 'garbage') { 'not json'; return }
# PowerShell names are case-insensitive: never reuse $status here, it IS $Status.
$deliveryState = 'canonical'; $durable = $true
if ($mode -eq 'queued') { $deliveryState = 'queued'; $durable = $false }
if ($mode -eq 'suppressed') { $deliveryState = 'suppressed'; $durable = $false }
if ($mode -eq 'long_delivery') { $deliveryState = 'x' * 5000; $durable = $false }
$echoTo = $To
if ($mode -eq 'wrong_echo') { $echoTo = 'codex-lead-1' }
# Same root resolution as the pinned writer: the env root, else the bundle's own .agent-bridge.
$root = if ($env:AGENT_BRIDGE_RUNTIME_ROOT) { $env:AGENT_BRIDGE_RUNTIME_ROOT } else { Split-Path -Parent $PSScriptRoot }
$eventsPath = Join-Path (Join-Path $root 'shared') 'events.jsonl'
if ($mode -eq 'wrong_root') { $eventsPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'shared\events.jsonl' }
if ($mode -eq 'relative_root') { $eventsPath = 'shared\events.jsonl' }
$delivery = [ordered]@{schema='waggledance.bridge.delivery-receipt.v1'; accepted=$true;
    delivery_status=$deliveryState; canonical_durable=$durable; events_path=$eventsPath}
if ($mode -eq 'no_events_path') { $delivery.Remove('events_path') }
$ts = '2026-09-29T06:00:00.0000000Z'
if ($mode -eq 'long_ts') { $ts = 'x' * 5000 }
$event = [ordered]@{ts_utc=$ts; agent=$Agent; type=$Type; task_id=$TaskId;
    status=$Status; to=$echoTo; message=$Message; payload=($PayloadJson | ConvertFrom-Json);
    _bridge_delivery=$delivery}
ConvertTo-Json -InputObject $event -Compress -Depth 8
'''


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


@pytest.fixture(params=SHELLS, ids=lambda s: Path(s).stem)
def env(request, tmp_path):
    bundle = tmp_path / "bundle"
    bin_dir = bundle / "tools-bootstrap" / ".agent-bridge" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "Write-AgentEvent.ps1").write_text(MOCK_WRITER, encoding="utf-8")
    (bin_dir / "BridgeEventClassifier.ps1").write_text("# pinned sibling helper\n", encoding="utf-8")
    worktree = tmp_path / "lane worktree"
    worktree.mkdir()
    capture = tmp_path / "capture.jsonl"
    capture.write_text("", encoding="utf-8")
    runtime = tmp_path / "decoy runtime"
    runtime.mkdir()

    class Env:
        shell = request.param

        def write_manifest(self, files=None):
            files = files if files is not None else {
                BIN + "Write-AgentEvent.ps1": sha256_file(bin_dir / "Write-AgentEvent.ps1"),
                BIN + "BridgeEventClassifier.ps1": sha256_file(bin_dir / "BridgeEventClassifier.ps1"),
                "tools/other.py": "0" * 64,  # outside the pinned bin prefix: not checked
            }
            manifest = bundle / "deployment-manifest.json"
            manifest.write_text(json.dumps({"schema_version": 1, "files": files}), encoding="utf-8")
            self.manifest_hash = sha256_file(manifest)

        def run(self, *, agent=AGENT, task=TASK, thread=THREAD, reason=REASON, digest=DIGEST,
                wt=None, bundle_root=None, manifest_hash=None, mode="canonical", anchors=True,
                progress=None, runtime_root=""):
            wt = str(worktree) if wt is None else wt
            runtime_root = str(runtime) if runtime_root == "" else runtime_root
            args = {"Agent": agent, "TaskId": task, "ThreadId": thread, "Worktree": wt,
                    "Reason": reason, "CheckpointDigest": digest}
            if progress is not None:
                args["ProgressKey"] = progress
            if anchors:
                args["BundleRoot"] = str(bundle) if bundle_root is None else bundle_root
                args["ExpectedManifestHash"] = self.manifest_hash if manifest_hash is None else manifest_hash
            quoted = " ".join(f"-{k} '{v}'" for k, v in args.items())
            script = (f"$env:WD_TEST_ALERT_CAPTURE = '{capture}'; $env:WD_TEST_ALERT_MODE = '{mode}'; "
                      "Remove-Item Env:WD_BRIDGE_PYTHON_WRAPPER, Env:WD_REBOOT_EXPECTED_MANIFEST_HASH "
                      "-ErrorAction SilentlyContinue; "
                      + ("Remove-Item Env:AGENT_BRIDGE_RUNTIME_ROOT -ErrorAction SilentlyContinue; "
                         if runtime_root is None else f"$env:AGENT_BRIDGE_RUNTIME_ROOT = '{runtime_root}'; ")
                      + f"$r = & '{HELPER}' {quoted}; "
                      "[Console]::Out.WriteLine('RESULT ' + ($r -join '')); "
                      "[Console]::Out.WriteLine('HOST_ALIVE ' + $LASTEXITCODE)")
            proc = subprocess.run([self.shell, "-NoLogo", "-NoProfile", "-NonInteractive",
                                   "-ExecutionPolicy", "Bypass", "-Command", script],
                                  capture_output=True, text=True, timeout=180)
            lines = proc.stdout.splitlines()
            result = next((json.loads(l[7:]) for l in lines if l.startswith("RESULT ")), None)
            alive = next((l for l in lines if l.startswith("HOST_ALIVE ")), None)
            return proc, result, alive

        def calls(self):
            return [json.loads(l) for l in capture.read_text(encoding="utf-8-sig").splitlines() if l.strip()]

        def ledger(self, thread=THREAD):
            path = worktree / ".codex-audit" / "wd-turn-loop" / f"continuity-alert-v1-{thread}.json"
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    e = Env()
    e.bundle, e.bin_dir, e.worktree = bundle, bin_dir, worktree
    e.write_manifest()
    return e


def ok(proc, result, alive, status, code=0):
    assert result is not None, proc.stdout + proc.stderr
    assert result["status"] == status, result
    assert result["schema"] == "wd.continuity-alert-result.v1"
    assert alive == f"HOST_ALIVE {code}", proc.stdout + proc.stderr
    assert proc.returncode == 0  # the invoking host survived: no exit inside the helper


# --- publication ---------------------------------------------------------------------------

def test_publishes_one_operator_only_message_with_bounded_payload(env):
    proc, result, alive = env.run()
    ok(proc, result, alive, "published")
    assert result["delivery_status"] == "canonical"
    assert len(result["alert_key"]) == 64
    [call] = env.calls()
    assert (call["Type"], call["Status"], call["To"]) == ("message", "continuity_alert", "operator")
    assert call["RequestId"] == "" and call["ReceiptJson"] is True
    assert "RequestId" not in call["Bound"] and "ReplyToEventJson" not in call["Bound"]
    payload = json.loads(call["PayloadJson"])
    assert payload == {"schema": "wd.continuity-alert.v1", "agent": AGENT, "task_id": TASK,
                       "reason": REASON, "checkpoint_digest": DIGEST,
                       "alert_key": result["alert_key"], "authority": "none"}
    assert THREAD not in call["Message"] and THREAD not in call["PayloadJson"]
    assert call["Message"].isascii() and "grants no authority" in call["Message"]
    entry = env.ledger()["entries"][0]
    assert (entry["status"], entry["key"]) == ("published", result["alert_key"])
    assert env.ledger()["agent"] == AGENT and env.ledger()["thread_id"] == THREAD


def test_duplicate_key_is_already_reported_without_resend(env):
    ok(*env.run(), "published")
    proc, result, alive = env.run()
    ok(proc, result, alive, "already_reported")
    assert len(env.calls()) == 1


def test_new_checkpoint_digest_or_reason_publishes_again(env):
    ok(*env.run(), "published")
    ok(*env.run(digest="b" * 64), "published")
    ok(*env.run(reason="continuity_stalled_after_recovery"), "published")
    assert len(env.calls()) == 3
    assert len(env.ledger()["entries"]) == 3


def test_new_thread_after_restart_gets_its_own_ledger(env):
    ok(*env.run(mode="suppressed"), "unknown", 1)   # old thread is stuck uncertain
    ok(*env.run(thread=THREAD2), "published")       # the new thread can still publish
    assert env.ledger(THREAD2)["thread_id"] == THREAD2
    assert len(env.calls()) == 2


def test_queued_is_reported_as_queued_never_resent(env):
    proc, result, alive = env.run(mode="queued")
    ok(proc, result, alive, "queued")
    ok(*env.run(), "queued")
    assert len(env.calls()) == 1
    assert env.ledger()["entries"][0]["status"] == "queued"


def test_unavailable_checkpoint_sentinel_publishes_once_per_thread(env):
    zero = "0" * 64
    proc, result, alive = env.run(reason="checkpoint_unavailable", digest=zero,
                                  task="codex-lead-1/continuity-recovery")
    ok(proc, result, alive, "published")
    payload = json.loads(env.calls()[0]["PayloadJson"])
    assert (payload["reason"], payload["checkpoint_digest"]) == ("checkpoint_unavailable", zero)
    ok(*env.run(reason="checkpoint_unavailable", digest=zero, task="codex-lead-1/continuity-recovery"),
       "already_reported")
    assert len(env.calls()) == 1


@pytest.mark.parametrize("reason,digest", [
    ("checkpoint_unavailable", "a" * 64),   # a real checkpoint is never "unavailable"
    (REASON, "0" * 64),                      # the sentinel never stands in for a hash
])
def test_unavailable_sentinel_is_bound_to_its_reason(env, reason, digest):
    proc, result, alive = env.run(reason=reason, digest=digest)
    ok(proc, result, alive, "unknown", 1)
    assert result["reason_code"] == "invalid_checkpoint_sentinel"
    assert env.calls() == []


# --- progress key: heartbeat rewrites do not alert again --------------------------------------

P1, P2 = "1" * 64, "2" * 64


def _key(*parts):
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def test_same_progress_key_publishes_once_despite_new_checkpoint_bytes(env):
    ok(*env.run(digest="a" * 64, progress=P1), "published")
    proc, result, alive = env.run(digest="b" * 64, progress=P1)  # heartbeat rewrite
    ok(proc, result, alive, "already_reported")
    [call] = env.calls()
    assert json.loads(call["PayloadJson"])["checkpoint_digest"] == "a" * 64  # real byte hash kept
    entry = env.ledger()["entries"][0]
    assert (entry["progress_key"], entry["checkpoint_digest"]) == (P1, "a" * 64)


def test_changed_progress_key_publishes_again(env):
    ok(*env.run(progress=P1), "published")
    ok(*env.run(progress=P2), "published")
    assert len(env.calls()) == 2


def test_alert_key_formula_and_domain_separation(env):
    _, legacy, _ = env.run()
    assert legacy["alert_key"] == _key(AGENT, THREAD, REASON, DIGEST)  # unchanged without ProgressKey
    _, tagged, _ = env.run(progress=DIGEST)  # same hex, other key space
    assert tagged["status"] == "published"
    assert tagged["alert_key"] == _key(AGENT, THREAD, REASON, "progress", DIGEST) != legacy["alert_key"]


@pytest.mark.parametrize("progress,code", [
    ("A" * 64, "invalid_progress_key"),
    ("1" * 63, "invalid_progress_key"),
    ("g" * 64, "invalid_progress_key"),
    (" " + "1" * 63, "invalid_progress_key"),
    ("0" * 64, "invalid_checkpoint_sentinel"),  # zero key needs the unavailable sentinel
])
def test_invalid_progress_key_fails_closed(env, progress, code):
    proc, result, alive = env.run(progress=progress)
    ok(proc, result, alive, "unknown", 1)
    assert result["reason_code"] == code
    assert env.calls() == [] and env.ledger() is None


def test_unavailable_sentinel_with_progress_key(env):
    zero = "0" * 64
    ok(*env.run(reason="checkpoint_unavailable", digest=zero, progress=zero), "published")
    proc, result, alive = env.run(reason="checkpoint_unavailable", digest=zero, progress=P1)
    ok(proc, result, alive, "unknown", 1)
    assert result["reason_code"] == "invalid_checkpoint_sentinel"
    assert len(env.calls()) == 1


def test_event_timestamp_is_iso_utc_in_both_shells(env):
    _, result, _ = env.run()
    assert result["event_ts_utc"] == "2026-09-29T06:00:00.0000000Z"
    assert env.ledger()["entries"][0]["event_ts_utc"] == "2026-09-29T06:00:00.0000000Z"


# --- uncertain delivery: visible, never blindly retried ------------------------------------

@pytest.mark.parametrize("mode", ["suppressed", "throw", "garbage", "wrong_echo",
                                  "wrong_root", "relative_root", "no_events_path"])
def test_uncertain_delivery_is_unknown_and_not_retried(env, mode):
    proc, result, alive = env.run(mode=mode)
    ok(proc, result, alive, "unknown", 1)
    assert result["reason_code"] == "delivery_uncertain"
    assert env.ledger()["entries"][0]["status"] == "uncertain"
    proc, result, alive = env.run()
    ok(proc, result, alive, "unknown", 1)
    assert result["reason_code"] == "delivery_uncertain"
    assert len(env.calls()) == 1


def test_crash_between_intent_and_receipt_blocks_blind_retry(env):
    proc, result, _ = env.run(mode="crash")
    assert proc.returncode == 7 and result is None
    assert env.ledger()["entries"][0]["status"] == "submitting"
    proc, result, alive = env.run()
    ok(proc, result, alive, "unknown", 1)
    assert result["reason_code"] == "delivery_uncertain"
    assert len(env.calls()) == 1


# --- runtime root: an unrooted write must never count as operator-visible -----------------

@pytest.mark.parametrize("root", [None, "relative\\root", "C:\\definitely\\missing\\wd-root"])
def test_missing_runtime_root_is_refused_before_any_intent(env, root):
    # Unset, the real writer falls back to the bundle-local log and still says canonical.
    proc, result, alive = env.run(runtime_root=root)
    ok(proc, result, alive, "unknown", 1)
    assert result["reason_code"] == "runtime_root_missing"
    assert env.calls() == [] and env.ledger() is None


# --- trust anchors -------------------------------------------------------------------------

def test_changed_writer_is_refused_before_any_intent(env):
    (env.bin_dir / "Write-AgentEvent.ps1").write_text(MOCK_WRITER + "\n# tampered\n", encoding="utf-8")
    proc, result, alive = env.run()
    ok(proc, result, alive, "unknown", 1)
    assert result["reason_code"] == "helper_hash_mismatch"
    assert env.calls() == [] and env.ledger() is None


def test_changed_sibling_helper_is_refused(env):
    (env.bin_dir / "BridgeEventClassifier.ps1").write_text("# tampered\n", encoding="utf-8")
    proc, result, alive = env.run()
    assert result["reason_code"] == "helper_hash_mismatch"
    assert env.calls() == []


def test_manifest_hash_mismatch_is_refused(env):
    proc, result, alive = env.run(manifest_hash="C" * 64)
    ok(proc, result, alive, "unknown", 1)
    assert result["reason_code"] == "manifest_hash_mismatch"
    assert env.calls() == []


def test_writer_missing_from_manifest_is_refused(env):
    env.write_manifest({BIN + "BridgeEventClassifier.ps1": sha256_file(env.bin_dir / "BridgeEventClassifier.ps1")})
    proc, result, alive = env.run()
    assert result["reason_code"] == "writer_not_pinned"
    assert env.calls() == []


def test_missing_anchors_are_unknown(env):
    proc, result, alive = env.run(anchors=False)
    ok(proc, result, alive, "unknown", 1)
    assert result["reason_code"] == "bundle_anchor_missing"


# --- input validation: codes and digests only ---------------------------------------------

@pytest.mark.parametrize("override,code", [
    ({"agent": "Codex Lead"}, "invalid_agent"),
    ({"task": "bad task id"}, "invalid_task_id"),
    ({"thread": "..\\..\\evil"}, "invalid_thread_id"),
    ({"thread": THREAD.upper()}, "invalid_thread_id"),
    ({"reason": "Continuity evidence unknown; operator reconciliation required"}, "invalid_reason_code"),
    ({"reason": "a+b"}, "invalid_reason_code"),
    ({"digest": "A" * 64}, "invalid_checkpoint_digest"),
    ({"digest": "a" * 63}, "invalid_checkpoint_digest"),
    ({"wt": "relative\\path"}, "invalid_worktree"),
    ({"wt": "C:relative"}, "invalid_worktree"),
])
def test_invalid_parameters_are_unknown_and_publish_nothing(env, override, code):
    proc, result, alive = env.run(**override)
    ok(proc, result, alive, "unknown", 1)
    assert result["reason_code"] == code
    assert env.calls() == []


# --- ledger integrity ----------------------------------------------------------------------

def _ledger_path(env, thread=THREAD):
    path = env.worktree / ".codex-audit" / "wd-turn-loop" / f"continuity-alert-v1-{thread}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.mark.parametrize("content,code", [
    ("{not json", "ledger_corrupt"),
    (json.dumps({"schema": "other", "agent": AGENT, "thread_id": THREAD, "entries": []}), "ledger_corrupt"),
    (json.dumps({"schema": "wd.continuity-alert-ledger.v1", "agent": "codex-tools-1",
                 "thread_id": THREAD, "entries": []}), "ledger_identity_mismatch"),
    (json.dumps({"schema": "wd.continuity-alert-ledger.v1", "agent": AGENT, "thread_id": THREAD,
                 "entries": [{"key": "x", "status": "published"}]}), "ledger_corrupt"),
    (json.dumps({"schema": "wd.continuity-alert-ledger.v1", "agent": AGENT, "thread_id": THREAD,
                 "entries": [{"key": "b" * 64, "status": "delivered"}]}), "ledger_corrupt"),
])
def test_corrupt_or_foreign_ledger_is_unknown(env, content, code):
    _ledger_path(env).write_text(content, encoding="utf-8")
    proc, result, alive = env.run()
    ok(proc, result, alive, "unknown", 1)
    assert result["reason_code"] == code
    assert env.calls() == []


def test_full_ledger_fails_closed(env):
    entries = [{"key": hashlib.sha256(str(i).encode()).hexdigest(), "status": "published"} for i in range(256)]
    _ledger_path(env).write_text(json.dumps({"schema": "wd.continuity-alert-ledger.v1", "agent": AGENT,
                                             "thread_id": THREAD, "entries": entries}), encoding="utf-8")
    proc, result, alive = env.run()
    ok(proc, result, alive, "unknown", 1)
    assert result["reason_code"] == "ledger_full"
    assert env.calls() == []


# --- reserved last slot: the channel closing is itself operator-visible (F4 option 1) -------

def _fill(env, n, status="published", pad=None):
    entries = [{"key": hashlib.sha256(str(i).encode()).hexdigest(), "status": status} for i in range(n)]
    if pad:
        for e in entries:
            e["pad"] = "x" * pad
    _ledger_path(env).write_text(json.dumps({"schema": "wd.continuity-alert-ledger.v1", "agent": AGENT,
                                             "thread_id": THREAD, "entries": entries}), encoding="utf-8")
    return entries


def test_last_slot_publishes_one_ledger_full_notice_instead_of_the_alert(env):
    _fill(env, 255)
    proc, result, alive = env.run()
    ok(proc, result, alive, "unknown", 1)
    assert result["reason_code"] == "ledger_full"
    assert result["ledger_full_notice"] == "published"
    assert result["alert_key"] == _key(AGENT, THREAD, REASON, DIGEST)  # the alert that was NOT sent
    [call] = env.calls()
    assert (call["Type"], call["Status"], call["To"]) == ("message", "continuity_alert", "operator")
    payload = json.loads(call["PayloadJson"])
    assert payload["reason"] == "alert_ledger_full"
    assert payload["alert_key"] == result["notice_key"] == _key(AGENT, THREAD, "alert_ledger_full")
    assert "alert_ledger_full" in call["Message"]
    entries = env.ledger()["entries"]
    assert len(entries) == 256
    assert (entries[-1]["key"], entries[-1]["reason"], entries[-1]["status"]) == \
        (result["notice_key"], "alert_ledger_full", "published")
    assert result["notice_key"] != result["alert_key"]
    assert result["alert_key"] not in {e["key"] for e in entries}  # the requested alert was never sent
    # Repeating the ORIGINAL request must not look delivered: still ledger_full, no call.
    proc, again, alive = env.run()
    ok(proc, again, alive, "unknown", 1)
    assert again["reason_code"] == "ledger_full" and "ledger_full_notice" not in again
    assert len(env.calls()) == 1


def test_ledger_full_notice_is_sent_exactly_once(env):
    _fill(env, 255)
    ok(*env.run(), "unknown", 1)
    for digest in ("b" * 64, "c" * 64):
        proc, result, alive = env.run(digest=digest)
        ok(proc, result, alive, "unknown", 1)
        assert result["reason_code"] == "ledger_full"
        assert "ledger_full_notice" not in result
    assert len(env.calls()) == 1
    assert len(env.ledger()["entries"]) == 256


def test_boundary_254_publishes_normally_then_the_next_alert_takes_the_reserved_slot(env):
    _fill(env, 254)
    ok(*env.run(), "published")
    assert len(env.ledger()["entries"]) == 255
    proc, result, alive = env.run(digest="b" * 64)
    assert (result["reason_code"], result["ledger_full_notice"]) == ("ledger_full", "published")
    assert [json.loads(c["PayloadJson"])["reason"] for c in env.calls()] == [REASON, "alert_ledger_full"]


def test_duplicate_is_answered_before_the_capacity_check(env):
    entries = _fill(env, 255)
    ledger = env.ledger()
    ledger["entries"][0]["key"] = _key(AGENT, THREAD, REASON, DIGEST)
    _ledger_path(env).write_text(json.dumps(ledger), encoding="utf-8")
    ok(*env.run(), "already_reported")
    assert env.calls() == []


@pytest.mark.parametrize("mode,notice", [("queued", "queued"), ("suppressed", "uncertain"),
                                         ("wrong_root", "uncertain")])
def test_ledger_full_notice_delivery_is_reported_and_never_retried(env, mode, notice):
    _fill(env, 255)
    proc, result, alive = env.run(mode=mode)
    ok(proc, result, alive, "unknown", 1)
    assert (result["reason_code"], result["ledger_full_notice"]) == ("ledger_full", notice)
    assert env.ledger()["entries"][-1]["status"] == notice
    ok(*env.run(digest="b" * 64), "unknown", 1)
    assert len(env.calls()) == 1


def test_crash_during_ledger_full_notice_is_not_retried(env):
    _fill(env, 255)
    proc, result, _ = env.run(mode="crash")
    assert proc.returncode == 7 and result is None
    assert env.ledger()["entries"][-1]["status"] == "submitting"
    proc, result, alive = env.run()
    ok(proc, result, alive, "unknown", 1)
    assert result["reason_code"] == "ledger_full"
    assert len(env.calls()) == 1


def test_callers_cannot_use_the_reserved_reason(env):
    proc, result, alive = env.run(reason="alert_ledger_full")
    ok(proc, result, alive, "unknown", 1)
    assert result["reason_code"] == "invalid_reason_code"
    assert env.calls() == []


# --- byte cap: an oversized ledger can never claim a publish --------------------------------

CAP = 262144


def _largest_probe_bytes(pad):
    """Compact JSON of the ledger plus the new entry at its largest possible shape."""
    entry = {"key": _key(AGENT, THREAD, REASON, DIGEST), "status": "submitting", "reason": REASON,
             "task_id": TASK, "checkpoint_digest": DIGEST, "progress_key": "",
             "at_utc": "2026-09-29T06:43:12.1234567+00:00", "delivery_status": "suppressed",
             "event_ts_utc": "x" * 64}
    ledger = {"schema": "wd.continuity-alert-ledger.v1", "agent": AGENT, "thread_id": THREAD,
              "entries": [{"key": hashlib.sha256(b"0").hexdigest(), "status": "published", "pad": "x" * pad},
                          entry]}
    return len(json.dumps(ledger, separators=(",", ":")))


@pytest.mark.parametrize("margin,expect_published", [(24, False), (-48, True)])
def test_ledger_byte_cap_is_checked_at_the_final_entry_size_before_the_call(env, margin, expect_published):
    pad = CAP + margin - _largest_probe_bytes(0)
    assert _largest_probe_bytes(pad) == CAP + margin
    _fill(env, 1, pad=pad)
    before = _ledger_path(env).read_bytes()
    proc, result, alive = env.run()
    if expect_published:  # boundary twin: just under the cap still publishes and records it
        ok(proc, result, alive, "published")
        assert env.ledger()["entries"][-1]["status"] == "published"
    else:
        ok(proc, result, alive, "unknown", 1)
        assert result["reason_code"] == "ledger_oversized"
        assert env.calls() == []
        assert _ledger_path(env).read_bytes() == before


def test_oversized_ledger_file_is_refused(env):
    _fill(env, 1, pad=270000)
    proc, result, alive = env.run()
    ok(proc, result, alive, "unknown", 1)
    assert result["reason_code"] == "ledger_oversized"
    assert env.calls() == []


def test_unknown_receipt_delivery_status_cannot_break_the_final_ledger_write(env):
    _fill(env, 1, pad=CAP - 48 - _largest_probe_bytes(0))
    proc, result, alive = env.run(mode="long_delivery")
    ok(proc, result, alive, "unknown", 1)
    assert result["reason_code"] == "delivery_uncertain"  # recorded, not ledger_oversized
    last = env.ledger()["entries"][-1]
    assert (last["status"], last["delivery_status"]) == ("uncertain", "")


def test_overlong_receipt_timestamp_cannot_break_the_final_ledger_write(env):
    # The receipt is untrusted input: its ts_utc must not grow the entry past the pre-checked size.
    _fill(env, 1, pad=261000 - 700)
    proc, result, alive = env.run(mode="long_ts")
    ok(proc, result, alive, "published")
    assert result["event_ts_utc"] == ""
    assert env.ledger()["entries"][-1]["status"] == "published"
