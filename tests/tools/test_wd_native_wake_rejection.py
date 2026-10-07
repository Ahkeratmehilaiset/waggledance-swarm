"""Native wake refusal reconciliation helper (Lead d496157f and 0b8d211b; AUTHORED, NOT RUN).

Fake twins for ops/windows/reboot/Resolve-WdNativeWakeRejection.ps1 on a fake lane journal under tmp_path: no native
queue, relay, process, model or real journal. Only the relay's exact per-attempt refusal receipt
(wd.native-queue-refusal.v1) beside the snapshot the state names (.wake.<snapshot_id>) is evidence; the legacy record and every
tampered receipt stay UNKNOWN with zero writes.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT.joinpath("ops", "windows", "reboot", "Resolve-WdNativeWakeRejection.ps1")
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(sys.platform != "win32" or PWSH is None, reason="Windows pwsh helper")

UPDATED = "2026-09-29T23:08:06.0577847+00:00"
LINE = ("Error: failed to queue session message: thread/queue/add failed: "
        "queue cannot contain more than 100 submissions (code -32600)")
STATE = {"schema": "wd.native-tools-wake.v1", "status": "submitting", "thread_id": "01a0a654-12af-7d81-85fc-d75d515c5b65",
         "agent": "codex-lead-1", "generation": "8" * 40, "native_pid": 23104, "relay_pid": 19704,
         "delivery_id": "7d8ae8b5561442f1a537b7119897b050", "queue_id": "", "updated_at_utc": UPDATED,
         "task_completion_verified": False, "rejections": 0, "receipt": "model_turn_started", "snapshot_id": "0f" * 16,
         "prompt_mode": "pinned_procedure"}
SNAPSHOT = '{"schema":"wd.bridge-wake-observation.v1","requests":[],"authority_effect":"none"}'
MISSING = object()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest().upper()


RECEIPT = {"schema": "wd.native-queue-refusal.v1", "agent": STATE["agent"], "thread_id": STATE["thread_id"],
           "generation": STATE["generation"], "relay_pid": STATE["relay_pid"], "native_pid": STATE["native_pid"],
           "delivery_id": STATE["delivery_id"], "snapshot_id": STATE["snapshot_id"], "outcome": "rejected", "exit_code": 1, "stdout": "",
           "stderr": LINE + "\n", "stdout_sha256": _sha(""), "stderr_sha256": _sha(LINE + "\n"), "code": -32600,
           "cap": 100, "completed_at_utc": "2026-09-29T23:08:07.7849249+00:00"}
RECEIPT_NAME = "native-bridge-wake.json.refusal-" + STATE["delivery_id"]
SNAPSHOT_NAME = "native-bridge-wake.json.wake." + STATE["snapshot_id"]
NEAR = LINE.replace("-32600", "-32601") + "\n"
MUTATIONS = {"schema": dict(schema="wd.native-queue-refusal.v0"), "outcome": dict(outcome="queued"),
             "exit_zero": dict(exit_code=0), "exit_text": dict(exit_code="1"), "no_exit": dict(exit_code=MISSING),
             "stdout_noise": dict(stdout="x", stdout_sha256=_sha("x")), "near_miss": dict(stderr=NEAR, stderr_sha256=_sha(NEAR)),
             "stderr_sha": dict(stderr_sha256="0" * 64), "null_stderr": dict(stderr=None), "no_stdout": dict(stdout=MISSING),
             "other_thread": dict(thread_id="01a0a654-0000-7d81-85fc-d75d515c5b65"), "other_pid": dict(native_pid=1),
             "other_generation": dict(generation="9" * 40), "other_delivery": dict(delivery_id="f" * 32), "other_snapshot": dict(snapshot_id="1" * 32),
             "no_code": dict(code=MISSING), "cap": dict(cap=99), "early": dict(completed_at_utc="2026-09-29T23:08:05+00:00")}


@pytest.fixture(scope="module")
def apply_supported():
    probe = subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
                            "(Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')"],
                           capture_output=True, timeout=120)
    if probe.stdout.decode("utf-8", "replace").strip() != "True":
        pytest.skip("apply needs ConvertFrom-Json -DateKind (PowerShell 7.5 or later)")


def _journal(tmp_path: Path, state: dict = STATE, receipt: dict | None = RECEIPT, snapshot: bool = True) -> Path:
    journal = tmp_path.joinpath("lane", ".codex-audit", "wd-turn-loop")
    journal.mkdir(parents=True)
    journal.joinpath("native-bridge-wake.json").write_text(json.dumps(state, indent=4), encoding="utf-8")
    if snapshot:
        journal.joinpath(SNAPSHOT_NAME).write_text(SNAPSHOT, encoding="utf-8")
    if receipt is not None:
        journal.joinpath(RECEIPT_NAME).write_text(json.dumps(receipt, indent=4), encoding="utf-8")
    journal.joinpath("native-bridge-wake.lock").write_bytes(b"")
    return journal


def _files(journal: Path) -> dict:
    return {path.name: path.read_bytes() for path in sorted(journal.iterdir()) if path.is_file()}


def _run(journal: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(HELPER), "-Journal",
                           str(journal), *extra], capture_output=True, timeout=120)


def _dry(journal: Path) -> dict:
    done = _run(journal)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    return json.loads(done.stdout.decode("utf-8").strip().splitlines()[-1])


def _apply(journal: Path, dry: dict, status: str = "rejected", **over) -> subprocess.CompletedProcess:
    digests = {"state": dry["state_sha256"] or "", "snapshot": dry["snapshot_sha256"] or "",
               "receipt": dry["receipt_sha256"] or ""}  # a missing file reports null: pass "" (refused by pattern)
    digests.update(over)
    return _run(journal, "-Apply", "-RefusedStatus", status, "-ExpectedStateSha256", digests["state"],
                "-ExpectedSnapshotSha256", digests["snapshot"], "-ExpectedReceiptSha256", digests["receipt"],
                "-Operator", "operator")


def test_the_legacy_record_without_a_receipt_stays_unknown_and_apply_writes_nothing(tmp_path):
    legacy = {key: value for key, value in STATE.items() if key != "snapshot_id"}
    journal = _journal(tmp_path, legacy, receipt=None, snapshot=False)
    journal.joinpath("native-bridge-wake.json.wake").write_text(SNAPSHOT, encoding="utf-8")  # the fixed legacy name
    journal.joinpath("native-terminal.json").write_text(json.dumps(  # the measured legacy text is never evidence
        {"status": "bridge_wake_blocked", "error": "Codex queue did not confirm exact-thread delivery: " + LINE}))
    before = _files(journal)
    dry = _dry(journal)
    assert (dry["classification"], dry["receipt_sha256"]) == ("unknown", None) and dry["reason"]
    done = _apply(journal, dry)
    assert done.returncode != 0 and b"refusing: " in done.stderr
    assert _files(journal) == before


def test_an_exact_receipt_reconciles_to_the_relay_retry_shape(tmp_path, apply_supported):
    journal = _journal(tmp_path)
    before = _files(journal)
    dry = _dry(journal)
    assert (dry["classification"], dry["cap"]) == ("explicit_rejection", 100)
    assert dry["receipt_sha256"] == hashlib.sha256(before[RECEIPT_NAME]).hexdigest().upper()
    done = _apply(journal, dry)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    (audit_path,) = journal.glob("native-bridge-wake.reconciled-" + STATE["delivery_id"] + "-*.json")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    assert {e["name"]: base64.b64decode(e["bytes_base64"]) for e in audit["evidence"]} == {
        name: before[name] for name in ("native-bridge-wake.json", SNAPSHOT_NAME, RECEIPT_NAME)}
    state = json.loads(journal.joinpath("native-bridge-wake.json").read_text(encoding="utf-8"))
    assert (state["status"], state["rejections"], state["queue_id"]) == ("rejected", 1, "")
    assert state["rejected_reason"] == "Codex queue rejected the submission; nothing was queued: " + LINE + "\n"
    assert all(state[key] == STATE[key] for key in ("delivery_id", "thread_id", "agent", "updated_at_utc"))
    assert journal.joinpath(SNAPSHOT_NAME).read_bytes() == before[SNAPSHOT_NAME]  # kept for the relay retry
    assert journal.joinpath(RECEIPT_NAME).read_bytes() == before[RECEIPT_NAME]  # evidence is never altered
    assert _apply(journal, _dry(journal)).returncode != 0  # idempotent: the state is no longer submitting


@pytest.mark.parametrize("case", [*MUTATIONS, "not_json", "directory"])
def test_every_tampered_receipt_is_unknown_and_apply_writes_nothing(tmp_path, case):
    if case in MUTATIONS:
        changed = {**RECEIPT, **MUTATIONS[case]}
        journal = _journal(tmp_path, receipt={key: value for key, value in changed.items() if value is not MISSING})
    else:
        journal = _journal(tmp_path, receipt=None)
        target = journal.joinpath(RECEIPT_NAME)
        if case == "not_json":
            target.write_text("{not json", encoding="utf-8")
        else:
            target.mkdir()
    before = _files(journal)
    dry = _dry(journal)
    assert dry["classification"] == "unknown" and "receipt" in dry["reason"]
    assert _apply(journal, dry).returncode != 0
    assert _files(journal) == before


@pytest.mark.parametrize("case", ["no_queue_id", "null_queue_id", "queue_id_set", "no_schema", "null_status",
                                  "queued", "no_delivery_id", "no_snapshot", "after_rejected_state", "no_snapshot_id",
                                  "upper_snapshot_id"])
def test_state_variants_are_unknown_even_with_an_exact_receipt(tmp_path, case):
    state, snapshot = dict(STATE), True
    if case in ("no_queue_id", "no_schema", "no_delivery_id", "no_snapshot_id"):
        state.pop({"no_queue_id": "queue_id", "no_schema": "schema", "no_delivery_id": "delivery_id",
                   "no_snapshot_id": "snapshot_id"}[case])
    elif case == "upper_snapshot_id":  # the relay mints lowercase hex only
        state["snapshot_id"] = "0F" * 16
    elif case == "null_queue_id":
        state["queue_id"] = None
    elif case == "queue_id_set":
        state["queue_id"] = "01a0f101-7d68-7db3-8717-2e2f65d85270"
    elif case == "null_status":
        state["status"] = None
    elif case == "queued":
        state.update(status="queued", queue_id="01a0f101-7d68-7db3-8717-2e2f65d85270")
    elif case == "no_snapshot":
        snapshot = False
    else:  # the relay already wrote rejected; its receipt cleanup failed
        state.update(status="rejected", rejections=1)
    journal = _journal(tmp_path, state, snapshot=snapshot)
    before = _files(journal)
    dry = _dry(journal)
    assert dry["classification"] == "unknown" and dry["reason"]
    assert _apply(journal, dry).returncode != 0
    assert _files(journal) == before


@pytest.mark.parametrize("which", ["state", "receipt"])
def test_an_oversized_state_or_receipt_fails_closed(tmp_path, which):
    journal = (_journal(tmp_path, dict(STATE, prompt_mode="p" * 33000)) if which == "state"
               else _journal(tmp_path, receipt=dict(RECEIPT, padding="p" * 33000)))
    before = _files(journal)
    for extra in ((), ("-Apply", "-RefusedStatus", "rejected")):
        done = _run(journal, *extra)
        assert done.returncode != 0 and b"over 32768 bytes" in done.stderr
    assert _files(journal) == before


@pytest.mark.parametrize("variant", ["queued_status", "other_status", "no_status", "zero_digests", "held_lock",
                                     "missing_lock"])
def test_no_apply_argument_combination_writes_anything(tmp_path, variant):
    journal = _journal(tmp_path)
    dry = _dry(journal)
    if variant == "missing_lock":
        journal.joinpath("native-bridge-wake.lock").unlink()
    before = _files(journal)
    status = {"queued_status": "queued", "other_status": "refused", "no_status": ""}.get(variant, "rejected")
    over = dict.fromkeys(("state", "snapshot", "receipt"), "0" * 64) if variant == "zero_digests" else {}
    if variant == "held_lock":
        with journal.joinpath("native-bridge-wake.lock").open("r+b"):
            done = _apply(journal, dry, status, **over)
    else:
        done = _apply(journal, dry, status, **over)
    assert done.returncode != 0
    assert _files(journal) == before


@pytest.mark.parametrize("partial", ["stale_audit", "replace_blocked"])
def test_a_partial_apply_stays_blocked_and_a_rerun_completes(tmp_path, apply_supported, partial):
    journal = _journal(tmp_path)
    dry = _dry(journal)
    if partial == "stale_audit":  # a crash right after an earlier audit write
        journal.joinpath("native-bridge-wake.reconciled-" + STATE["delivery_id"] + "-20260930T0000000000000Z.json").write_bytes(b"{}")
    else:  # an open handle without delete sharing makes File.Replace fail and keep the state
        before = _files(journal)
        with journal.joinpath("native-bridge-wake.json").open("rb"):
            blocked = _apply(journal, dry)
        assert blocked.returncode != 0 and b"state is unchanged" in blocked.stderr
        after = _files(journal)
        audits = [name for name in after if name.startswith("native-bridge-wake.reconciled-")]
        assert len(audits) == 1 and not [name for name in after if name.endswith(".tmp")]
        assert {name: data for name, data in after.items() if name not in audits} == before
    done = _apply(journal, dry)
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")
    assert len(list(journal.glob("native-bridge-wake.reconciled-" + STATE["delivery_id"] + "-*.json"))) == 2
    assert json.loads(journal.joinpath("native-bridge-wake.json").read_text(encoding="utf-8"))["status"] == "rejected"
