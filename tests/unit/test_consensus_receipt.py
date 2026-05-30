# SPDX-License-Identifier: BUSL-1.1
"""Forge-probe tests for the bridge-consensus receipt (T0b-followup)."""
from __future__ import annotations

import copy

from waggledance.core.magma.canonical import sha256_digest
from waggledance.core.magma.consensus_receipt import (
    _CORE_KEYS,
    build_bridge_consensus_receipt,
    verify_bridge_consensus_receipt,
)

HEAD = "1234567890abcdef1234567890abcdef12345678"
OTHER_HEAD = "00000000000000000000000000000000deadbeef"
TASK = "codex-lead/some-task-20260530"
CHARTER_DIGEST = "charterdigest0000"


def _events():
    return [
        {"agent": "codex-lead-1", "type": "decision", "status": "build_consensus",
         "task_id": TASK, "ts_utc": "2026-05-30T01:00:00Z", "message": f"build {HEAD}"},
        {"agent": "codex-tools-1", "type": "decision", "status": "build_consensus",
         "task_id": TASK, "ts_utc": "2026-05-30T01:01:00Z", "message": f"build {HEAD}"},
        {"agent": "claude-rco-1", "type": "decision", "status": "rco_pass",
         "task_id": TASK, "ts_utc": "2026-05-30T01:02:00Z", "message": f"rco_pass {HEAD}"},
    ]


def _verdict(*, ok=True):
    return {
        "ok": ok,
        "decision": "bridge_consensus_verified" if ok else "bridge_consensus_incomplete",
        "reasons": [],
        "head_sha": HEAD,
        "identities": {
            "build_lead": {"agent": "codex-lead-1", "approved": True, "approval_index": 0, "block_index": None},
            "build_tools": {"agent": "codex-tools-1", "approved": True, "approval_index": 1, "block_index": None},
            "rco": {"agent": "claude-rco-1", "approved": True, "approval_index": 2, "block_index": None},
        },
    }


def _receipt(events=None, verdict=None):
    return build_bridge_consensus_receipt(
        pr_number=900, head_sha=HEAD, task_id=TASK,
        verdict=verdict or _verdict(), events=events or _events(),
        charter_path="docs/architecture/IDLE_AUTONOMY_CHARTER.md",
        charter_digest=CHARTER_DIGEST,
        ci_status={"allgreen": True},
    )


def _reseal(receipt):
    core = {k: receipt[k] for k in _CORE_KEYS}
    receipt["canonical_digest"] = sha256_digest(core)
    return receipt


def _verify(receipt, *, events=None, expected_head=HEAD, charter_digest=CHARTER_DIGEST, verdict=None):
    return verify_bridge_consensus_receipt(
        receipt=receipt, events=events or _events(), expected_head=expected_head,
        charter_digest=charter_digest, rederived_verdict=verdict or _verdict(),
    )


def test_happy_path_verifies():
    assert _verify(_receipt())["ok"] is True


def test_missing_or_empty_receipt_refuses():
    for bad in ({}, None, [], "x"):
        assert _verify(bad)["ok"] is False


def test_tampered_field_breaks_canonical_digest():
    r = _receipt()
    r["pr_number"] = 999  # tamper without re-seal
    res = _verify(r)
    assert res["ok"] is False
    assert any("canonical_digest" in x for x in res["reasons"])


def test_snapshot_ok_but_rederive_false_refuses():
    # stored snapshot says ok=True, but the fresh re-derivation says not-ok.
    res = _verify(_receipt(), verdict=_verdict(ok=False))
    assert res["ok"] is False
    assert any("re-derived" in x for x in res["reasons"])


def test_two_of_three_identity_refuses():
    # duplicate agent (lead stands in for tools) -> not 3 distinct.
    r = _receipt()
    r["identities"]["build_tools"]["agent"] = "codex-lead-1"
    _reseal(r)
    assert _verify(r)["ok"] is False


def test_head_mismatch_refuses():
    assert _verify(_receipt(), expected_head=OTHER_HEAD)["ok"] is False


def test_forged_event_digest_refuses():
    r = _receipt()
    r["identities"]["rco"]["event_digest"] = "0" * 64  # forged ref
    _reseal(r)  # pass the digest check; event-binding must still catch it
    res = _verify(r)
    assert res["ok"] is False
    assert any("event_digest" in x for x in res["reasons"])


def test_rco_status_not_pass_refuses():
    events = _events()
    events[2]["status"] = "approved"  # not an RCO_PASS-family status
    r = _receipt(events=events)
    assert _verify(r, events=events)["ok"] is False


def test_type_confused_identities_refuses():
    r = _receipt()
    r["identities"] = [r["identities"]["build_lead"]]  # list, not mapping
    _reseal(r)
    assert _verify(r)["ok"] is False


def test_charter_digest_mismatch_refuses():
    assert _verify(_receipt(), charter_digest="different")["ok"] is False
