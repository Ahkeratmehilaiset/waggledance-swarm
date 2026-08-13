# SPDX-License-Identifier: BUSL-1.1
"""Cause-B conformance harness: a recognized-RCO veto can NOT be cleared by a
mistokened / free-text / negated status (the #1387 latch-bypass class).

This is the CI-green proof required by the standing-sign activation precondition
(BRIDGE_CONSENSUS_APPROVAL_V1.md v1.1, rco-2 fence #1396): element 4 ("no
unretracted recognized-RCO veto") must NOT be clearable by a finding whose status
string lacks block-vocab. The #1387 incident was exactly that -- a recognized-RCO
``finding`` with a ``content_pass`` token in its status silently failed open.

The fix wires the single-source P2/D5 taxonomy (``BLOCK_BY_TYPE`` /
``RCO_GATED_TYPES``) so a recognized-RCO ``finding`` (and any ``blocked``-type
event) latches a veto BY TYPE, regardless of the status string. Every NEGATIVE
vector below is enforced: it MUST route to blocked.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.check_bridge_changes_requested import (  # noqa: E402
    check_bridge_clear_to_merge,
    _RECOGNIZED_RCOS,
    _TAXONOMY_BLOCK_BY_TYPE,
    _TAXONOMY_RCO_GATED_TYPES,
)

AGENT_UUIDS = {
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    "claude-rco-2": "76739997-0058-41a2-8514-78ff295537aa",
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
}


def _ev(ts: str, agent: str, type_: str, status: str, task: str = "T") -> dict:
    e = {
        "ts_utc": ts, "agent": agent, "type": type_, "task_id": task,
        "status": status, "message": "", "to": "",
    }
    if agent in AGENT_UUIDS:
        e["agent_uuid"] = AGENT_UUIDS[agent]
    return e


def _clear(events: list[dict]) -> bool:
    return check_bridge_clear_to_merge(
        events=events, task_id="T", merging_agent="codex-lead-1"
    )["clear_to_merge"]


# --- NEGATIVE-ENFORCED: a recognized-RCO finding must BLOCK regardless of status ---
@pytest.mark.parametrize(
    "status",
    [
        "rco1_safety_latch_content_pass_hold",   # #1387 EXACT vector (content_pass token)
        "rco2_concern_needs_reconcile",          # free-text, no block vocab
        "",                                      # empty status
        "block_not_resolved",                    # negated block form
        "not_cleared",                           # negated clear form
        "hold",                                  # bare non-latching token
        "rco1_content_pass",                     # pass-token in a finding
        "info",                                  # neutral-looking
        "no_changes_requested",                  # approval-looking
        "no_changes_requested_approved",         # approval-looking compound
        "changes_requested_resolved",            # clear-looking
        "changes_requested_cleared",             # clear-looking
    ],
)
def test_recognized_rco_finding_blocks_by_type_regardless_of_status(status: str) -> None:
    events = [_ev("2026-06-25T01:00:00Z", "claude-rco-1", "finding", status)]
    assert _clear(events) is False, f"finding status={status!r} must latch a veto"


def test_either_recognized_rco_finding_blocks() -> None:
    for rco in _RECOGNIZED_RCOS:
        assert _clear([_ev("2026-06-25T01:00:00Z", rco, "finding", "x_pass")]) is False


def test_recognized_rco_veto_is_not_skipped_as_merging_agent() -> None:
    result = check_bridge_clear_to_merge(
        events=[
            _ev(
                "2026-06-25T01:00:00Z",
                "claude-rco-1",
                "finding",
                "changes_requested_cleared",
            )
        ],
        task_id="T",
        merging_agent="claude-rco-1",
    )

    assert result["clear_to_merge"] is False


def test_single_custom_rco_string_is_normalized_as_one_identity() -> None:
    result = check_bridge_clear_to_merge(
        events=[
            _ev(
                "2026-06-25T01:00:00Z",
                "claude-rco-1",
                "finding",
                "changes_requested_cleared",
            )
        ],
        task_id="T",
        merging_agent="codex-lead-1",
        recognized_rco_agents="claude-rco-1",
    )

    assert result["clear_to_merge"] is False


def test_blocked_type_is_not_skipped_as_merging_agent() -> None:
    result = check_bridge_clear_to_merge(
        events=[
            _ev(
                "2026-06-25T01:00:00Z",
                "codex-tools-1",
                "blocked",
                "no_changes_requested",
            )
        ],
        task_id="T",
        merging_agent="codex-tools-1",
    )

    assert result["clear_to_merge"] is False


def test_author_status_veto_is_not_skipped_as_merging_agent() -> None:
    result = check_bridge_clear_to_merge(
        events=[
            _ev(
                "2026-06-25T01:00:00Z",
                "codex-tools-1",
                "decision",
                "changes_requested",
            )
        ],
        task_id="T",
        merging_agent="codex-tools-1",
        author_agent="codex-tools-1",
    )

    assert result["clear_to_merge"] is False


def test_unverified_blocked_type_vetoes_fail_closed() -> None:
    event = _ev(
        "2026-06-25T01:00:00Z",
        "codex-tools-1",
        "blocked",
        "no_changes_requested",
    )
    event.pop("agent_uuid")

    result = check_bridge_clear_to_merge(
        events=[event],
        task_id="T",
        merging_agent="codex-lead-1",
    )

    assert result["clear_to_merge"] is False
    assert result["unverified_type_block_events"][0][
        "unverified_veto_fail_closed"
    ] is True


@pytest.mark.parametrize(
    "status", ["info", "no_changes_requested", "changes_requested_cleared"]
)
def test_type_blocked_blocks_by_type_any_identity(status: str) -> None:
    assert _clear(
        [_ev("2026-06-25T01:00:00Z", "codex-tools-1", "blocked", status)]
    ) is False


# --- CONTROLS (must NOT over-block) ---
def test_non_rco_finding_does_not_veto() -> None:
    # a build agent's finding is advisory, not an RCO veto
    assert _clear([_ev("2026-06-25T01:00:00Z", "codex-tools-1", "finding",
                       "tools_concern")]) is True


def test_rco_finding_then_same_rco_rco_pass_supersedes() -> None:
    events = [
        _ev("2026-06-25T01:00:00Z", "claude-rco-1", "finding", "rco1_concern"),
        _ev("2026-06-25T02:00:00Z", "claude-rco-1", "decision", "rco_pass"),
    ]
    assert _clear(events) is True


@pytest.mark.parametrize(
    "status", ["changes_requested_resolved", "changes_requested_cleared"]
)
def test_decision_clear_still_retracts_same_identity_block(status: str) -> None:
    events = [
        _ev(
            "2026-06-25T01:00:00Z",
            "claude-rco-1",
            "decision",
            "changes_requested",
        ),
        _ev("2026-06-25T02:00:00Z", "claude-rco-1", "decision", status),
    ]
    assert _clear(events) is True


def test_rco_finding_then_other_rco_pass_veto_stands_per_identity() -> None:
    # rco-1 vetoes; rco-2 passes -> rco-1's veto is NOT out-voted (per-identity)
    events = [
        _ev("2026-06-25T01:00:00Z", "claude-rco-1", "finding", "rco1_concern"),
        _ev("2026-06-25T02:00:00Z", "claude-rco-2", "decision", "rco_pass"),
    ]
    assert _clear(events) is False


def test_clean_rco_pass_only_is_clear() -> None:
    assert _clear([_ev("2026-06-25T01:00:00Z", "claude-rco-1", "decision",
                       "rco_pass")]) is True


# --- SINGLE-SOURCE / DRIFT GUARDS ---
def test_block_by_type_is_taxonomy_single_source() -> None:
    from tools.bridge_event_taxonomy import BLOCK_BY_TYPE, RCO_GATED_TYPES
    assert _TAXONOMY_BLOCK_BY_TYPE is BLOCK_BY_TYPE
    assert _TAXONOMY_RCO_GATED_TYPES is RCO_GATED_TYPES
    assert "finding" in _TAXONOMY_BLOCK_BY_TYPE
    assert "finding" in _TAXONOMY_RCO_GATED_TYPES


def test_recognized_rco_set_matches_canonical_no_drift() -> None:
    # locally defined to avoid a circular import; must not drift from the canonical
    from tools.check_rco_pass_present import DEFAULT_RCO_AGENTS
    assert _RECOGNIZED_RCOS == frozenset(DEFAULT_RCO_AGENTS)
