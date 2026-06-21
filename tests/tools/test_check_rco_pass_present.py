# SPDX-License-Identifier: BUSL-1.1
"""Tests for tools/check_rco_pass_present.py.

Covers the exact forged cases required for the RCO pass presence gate
(Rule 9a: RCO absence = NO merge). All tests offline/deterministic, no network.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "check_rco_pass_present.py"

sys.path.insert(0, str(ROOT))

from tools.check_rco_pass_present import (  # noqa: E402
    check_rco_pass_present as _check_rco_pass_present,
    DEFAULT_EVENTS_PATH,
    _is_blocking_status as _rco_gate_is_blocking_status,
    _read_events,
)
from tools.check_bridge_changes_requested import (  # noqa: E402
    _is_blocking_status as _peer_gate_is_blocking_status,
)
from tools.idle_consensus_auto_merge import (  # noqa: E402
    _is_consensus_block as _consensus_is_blocking_status,
)
import waggledance.core.bridge_identity_registry as identity_registry_module  # noqa: E402
from waggledance.core.bridge_identity_registry import (  # noqa: E402
    load_bridge_identity_registry,
)

AGENT_UUIDS = {
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    "claude-rco-2": "76739997-0058-41a2-8514-78ff295537aa",
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
    "fable-5": "f8b1e5c0-3d2a-4e6b-9c1f-7a0d5e2b4c80",
}


def _seed_events(tmp_path: Path, events: list[dict]) -> Path:
    """Write a minimal events.jsonl under a temp .agent-bridge for CLI tests."""
    bridge = tmp_path / ".agent-bridge"
    shared = bridge / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    events_path = shared / "events.jsonl"
    with events_path.open("w", encoding="utf-8", newline="\n") as fh:
        for ev in events:
            fh.write(json.dumps(ev, sort_keys=True) + "\n")
    return events_path


def test_read_events_skips_bare_null_event_line(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    event = _rco_event()
    events_path.write_text(
        "\n".join(["null", json.dumps(event, sort_keys=True)]),
        encoding="utf-8",
    )

    assert _read_events(events_path) == [event]


def _rco_event(
    *,
    ts: str = "2026-06-03T12:00:00Z",
    agent: str = "claude-rco-1",
    type_: str = "decision",
    status: str = "rco_pass",
    task_id: str = "waggledance/grok-scout-1/rco-pass-presence-gate-20260603",
    message: str = "",
    payload: dict | None = None,
) -> dict:
    ev = {
        "ts_utc": ts,
        "agent": agent,
        "type": type_,
        "status": status,
        "task_id": task_id,
        "message": message,
        "payload": payload or {},
        "severity": "",
        "to": "",
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": 0,
        "cwd": "",
    }
    if agent in AGENT_UUIDS:
        ev["agent_uuid"] = AGENT_UUIDS[agent]
    return ev


HEAD = "abcdef1234567890abcdef1234567890abcdef12"
OTHER_HEAD = "0000000000000000000000000000000000000000"
TASK = "waggledance/grok-scout-1/rco-pass-presence-gate-20260603"
AUTHOR = "codex-lead-1"


def check_rco_pass_present(*args, **kwargs):
    kwargs.setdefault("author_agent", AUTHOR)
    return _check_rco_pass_present(*args, **kwargs)


# --- unit tests on the library function -----------------------------------


def test_missing_identity_registry_requires_explicit_offline_opt_in(
    tmp_path: Path,
) -> None:
    missing_registry = tmp_path / "missing_bridge_identity_registry.json"

    with pytest.raises(ValueError, match="bridge identity registry not found"):
        load_bridge_identity_registry(missing_registry)

    assert load_bridge_identity_registry(missing_registry, allow_missing=True) == {}


def test_missing_default_identity_registry_refuses_rco_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing_registry = tmp_path / "missing_bridge_identity_registry.json"
    monkeypatch.setattr(
        identity_registry_module,
        "DEFAULT_BRIDGE_IDENTITY_REGISTRY_PATH",
        missing_registry,
    )
    events = [
        _rco_event(
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD}",
        )
    ]

    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)

    assert result["ok"] is False
    assert result["decision"] == "invalid_identity_registry"
    assert result["has_qualifying_rco_pass_at_head"] is False
    assert "bridge identity registry not found" in result["error"]


def test_pass_at_head_present_returns_ok() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD} for the task.",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is True
    assert result["decision"] == "rco_pass_present"
    assert result["has_qualifying_rco_pass_at_head"] is True
    assert result["latest_rco_is_veto"] is False
    assert result["rco_pass_event"] is not None
    # All claim gates false per hard rule
    for key in (
        "claim_gate_satisfied",
        "claim_safe",
        "literal_future_claim_safe",
        "controls_present",
        "runtime_authority_granted",
        "external_writes_applied",
        "required_runtime_evidence_present",
    ):
        assert result[key] is False


def test_no_pass_silence_refuses() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_requested",  # not a pass
            type_="handoff",
            message="review requested",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is False
    assert result["decision"] in {"no_qualifying_pass", "rco_pass_absent"}
    assert result["has_qualifying_rco_pass_at_head"] is False


def test_pass_at_different_old_head_is_stale_refuse() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {OTHER_HEAD}.",  # different head
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is False
    assert result["has_qualifying_rco_pass_at_head"] is False
    assert result["decision"] == "no_qualifying_pass"
    assert result["has_stale_rco_pass_at_other_head"] is True
    assert result["latest_stale_rco_pass_event"]["referenced_heads"] == [OTHER_HEAD]
    assert result["rco_reemit_guidance"] == {
        "required": True,
        "reason": "stale_rco_pass_head",
        "preferred_task_id": TASK,
        "accepted_task_ids": [TASK],
        "head": HEAD,
        "stale_heads": [OTHER_HEAD],
        "target_rco_agents": ["claude-rco-1", "claude-rco-2"],
        "legacy_request_status": "rco_requested",
    }


def test_stale_pass_diagnostic_does_not_block_later_exact_pass() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at old head {OTHER_HEAD}.",
        ),
        _rco_event(
            ts="2026-06-03T10:05:00Z",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD}.",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is True
    assert result["decision"] == "rco_pass_present"
    assert result["has_stale_rco_pass_at_other_head"] is True
    assert result["latest_stale_rco_pass_event"]["referenced_heads"] == [OTHER_HEAD]
    assert result["rco_reemit_guidance"] is None


def test_changes_requested_after_pass_refuses() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD}.",
        ),
        _rco_event(
            ts="2026-06-03T10:05:00Z",
            status="changes_requested",
            type_="decision",
            message="found issues after review",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is False
    assert result["decision"] == "vetoed_after_pass"
    assert result["latest_rco_is_veto"] is True
    assert (
        result["has_qualifying_rco_pass_at_head"] is True
    )  # pass existed but superseded


def test_message_changes_requested_after_pass_does_not_veto() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD}.",
        ),
        _rco_event(
            ts="2026-06-03T10:05:00Z",
            status="changes_requested",
            type_="message",
            message="bridge conversation mention, not an authoritative veto",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)

    assert result["ok"] is True
    assert result["decision"] == "rco_pass_present"
    assert result["latest_rco_is_veto"] is False


@pytest.mark.parametrize(
    "status",
    [
        "changes_requested_concurrence",
        "changes_requested_resolved",
        "changes_requested_resolved_ci_green",
        "changes_requested_resolved_ci_pending",
        "changes_requested_cleared",
        "changes_requested_cleared_ci_green",
        "changes_requested_cleared_ci_pending",
        "changes_requested_retracted",
        "changes_requested_withdrawn",
        "rco_changes_requested_cleared",
        "rco_changes_requested_retracted",
        "rco_changes_requested_withdrawn",
    ],
)
def test_neutral_changes_requested_status_after_pass_does_not_veto(
    status: str,
) -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD}.",
        ),
        _rco_event(
            ts="2026-06-03T10:05:00Z",
            status=status,
            type_="message",
            message="neutral follow-up; not a veto",
        ),
    ]

    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)

    assert result["ok"] is True
    assert result["decision"] == "rco_pass_present"
    assert result["latest_rco_is_veto"] is False


def test_finding_info_after_pass_does_not_veto() -> None:
    # finding/info is an advisory note, not a veto: a prior exact-head rco_pass
    # must stand (the finding/info vetoed_after_pass bug).
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD}.",
        ),
        _rco_event(
            ts="2026-06-03T10:05:00Z",
            status="info",
            type_="finding",
            message="advisory governance note after pass; not a veto",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is True
    assert result["decision"] == "rco_pass_present"
    assert result["latest_rco_is_veto"] is False
    assert result["has_qualifying_rco_pass_at_head"] is True


@pytest.mark.parametrize(
    "status",
    [
        "tools_peer_block_clear_needed_after_reattribution",
        "peer_block_is_g4_classifier_artifact_no_real_veto",
        "approved_waiver_block_cleared",
        "fable_1368_failclosed_endorse_verify_block_cleared_coverage",
        "block_cleared",
        "block_resolved",
        "block_cleared_no_remaining_issues",
        "block_resolved_still_monitoring",
        "block_cleared_open_followup",
    ],
)
def test_bridge_non_veto_finding_status_after_pass_does_not_veto(
    status: str,
) -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD}.",
        ),
        _rco_event(
            ts="2026-06-03T10:05:00Z",
            status=status,
            type_="finding",
            message="diagnostic bridge status, not a veto",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)

    assert result["ok"] is True
    assert result["decision"] == "rco_pass_present"
    assert result["latest_rco_is_veto"] is False


@pytest.mark.parametrize(
    "status",
    [
        "changes_requested_NOT_resolved",
        "rco_changes_requested_not_cleared",
    ],
)
def test_negated_changes_requested_status_after_pass_still_vetoes(
    status: str,
) -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD}.",
        ),
        _rco_event(
            ts="2026-06-03T10:05:00Z",
            status=status,
            type_="finding",
            message="still blocked; not resolved",
        ),
    ]

    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)

    assert result["ok"] is False
    assert result["decision"] == "vetoed_after_pass"
    assert result["latest_rco_is_veto"] is True


def test_finding_changes_requested_after_pass_still_vetoes() -> None:
    # Positive control: a real veto-finding (type=finding/changes_requested)
    # STILL blocks -- the informational exemption must not fail-open.
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD}.",
        ),
        _rco_event(
            ts="2026-06-03T10:05:00Z",
            status="changes_requested",
            type_="finding",
            message="real veto raised after review",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is False
    assert result["decision"] == "vetoed_after_pass"
    assert result["latest_rco_is_veto"] is True


def test_not_blocked_clarification_status_after_pass_does_not_veto() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD}.",
        ),
        _rco_event(
            ts="2026-06-03T10:05:00Z",
            status="rco1_clarify_1283_not_blocked_on_rco2",
            type_="message",
            message="clarifying that another RCO is not a hard blocker",
        ),
    ]

    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)

    assert result["ok"] is True
    assert result["decision"] == "rco_pass_present"
    assert result["latest_rco_is_veto"] is False


def test_finding_ambiguous_status_after_pass_still_vetoes_fail_closed() -> None:
    # Fail-closed control: an ambiguous/unknown finding status (NOT in the tight
    # informational allowlist, not an approval, not lexically blocking) still
    # vetoes -- only explicitly-informational statuses are exempted.
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD}.",
        ),
        _rco_event(
            ts="2026-06-03T10:05:00Z",
            status="operator_review_required",
            type_="finding",
            message="ambiguous status must remain a veto (fail-closed)",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is False
    assert result["decision"] == "vetoed_after_pass"
    assert result["latest_rco_is_veto"] is True


@pytest.mark.parametrize("status", ["blocked_no_fix_yet", "block_without_fix"])
def test_block_status_with_negation_context_after_pass_still_vetoes(
    status: str,
) -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD}.",
        ),
        _rco_event(
            ts="2026-06-03T10:05:00Z",
            status=status,
            type_="finding",
            message="still blocked",
        ),
    ]

    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)

    assert result["ok"] is False
    assert result["decision"] == "vetoed_after_pass"
    assert result["latest_rco_is_veto"] is True


@pytest.mark.parametrize(
    ("event_type", "status", "expected"),
    [
        ("finding", "tools_peer_block_clear_needed_after_reattribution", False),
        ("finding", "peer_block_is_g4_classifier_artifact_no_real_veto", False),
        ("finding", "approved_waiver_block_cleared", False),
        (
            "finding",
            "fable_1368_failclosed_endorse_verify_block_cleared_coverage",
            False,
        ),
        ("finding", "block_cleared", False),
        ("finding", "block_resolved", False),
        ("finding", "block_cleared_no_remaining_issues", False),
        ("finding", "changes_requested", True),
        ("finding", "rco_changes_requested_not_cleared", True),
        ("finding", "block_not_resolved", True),
        ("finding", "block_incomplete_clear", True),
        ("message", "changes_requested", False),
        ("blocked", "blocked", True),
    ],
)
def test_bridge_veto_classifiers_share_status_taxonomy(
    event_type: str,
    status: str,
    expected: bool,
) -> None:
    assert _peer_gate_is_blocking_status(status, event_type=event_type) is expected
    assert _consensus_is_blocking_status(status, event_type=event_type) is expected
    assert _rco_gate_is_blocking_status(status, event_type=event_type) is expected


def test_type_blocked_with_info_status_still_vetoes() -> None:
    # The informational exemption is type=finding only; type=blocked is
    # semantically a block and vetoes regardless of its status.
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD}.",
        ),
        _rco_event(
            ts="2026-06-03T10:05:00Z",
            status="info",
            type_="blocked",
            message="type=blocked is a block regardless of status",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is False
    assert result["decision"] == "vetoed_after_pass"
    assert result["latest_rco_is_veto"] is True


def test_pass_present_no_later_veto_ok() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"approved at head {HEAD}",
        ),
        # a later non-veto non-pass signal does not supersede
        _rco_event(
            ts="2026-06-03T10:10:00Z",
            status="rco_pass_pending_ci",
            type_="decision",
            message="noted CI pending but already passed at head",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is True
    assert result["has_qualifying_rco_pass_at_head"] is True
    assert result["decision"] == "rco_pass_present"


def test_wrong_rco_agent_identity_not_counted() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            agent="codex-tools-1",  # wrong identity
            status="rco_pass",
            type_="decision",
            message=f"at head {HEAD}",
        ),
        _rco_event(
            ts="2026-06-03T10:01:00Z",
            agent="claude-rco-1",
            status="rco_requested",
            type_="handoff",
            message="request only",
        ),
    ]
    result = check_rco_pass_present(
        events=events, task_id=TASK, head=HEAD, rco_agent="claude-rco-1"
    )
    assert result["ok"] is False
    assert result["has_qualifying_rco_pass_at_head"] is False


def test_backup_rco_pass_satisfies_default_rco_set() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            agent="claude-rco-2",
            status="rco_pass",
            type_="decision",
            message=f"backup RCO_PASS at exact head {HEAD}",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is True
    assert result["satisfying_rco_agent"] == "claude-rco-2"
    assert result["eligible_rco_agents"] == ["claude-rco-1", "claude-rco-2"]


def test_registered_rco_uuid_mismatch_does_not_count() -> None:
    events = [
        _rco_event(
            agent="claude-rco-2",
            status="rco_pass",
            type_="decision",
            message=f"forged backup RCO_PASS at exact head {HEAD}",
        )
        | {"agent_uuid": AGENT_UUIDS["fable-5"]},
    ]

    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)

    assert result["ok"] is False
    assert result["has_qualifying_rco_pass_at_head"] is False
    assert result["ignored_identity_mismatch_events"][0]["agent"] == "claude-rco-2"
    assert (
        result["ignored_identity_mismatch_events"][0]["identity_binding_status"]
        == "mismatch_uuid"
    )


def test_registered_rco_missing_uuid_veto_does_not_override_genuine_pass() -> None:
    forged_veto = _rco_event(
        ts="2026-06-03T10:01:00Z",
        status="changes_requested",
        type_="finding",
        message="unsigned veto",
    )
    forged_veto.pop("agent_uuid")
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD}",
        ),
        forged_veto,
    ]

    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)

    assert result["ok"] is True
    assert result["has_qualifying_rco_pass_at_head"] is True
    assert (
        result["ignored_identity_mismatch_events"][0]["identity_binding_status"]
        == "missing_uuid"
    )


def test_author_rco_self_pass_is_excluded_fail_closed() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            agent="claude-rco-2",
            status="rco_pass",
            type_="decision",
            message=f"self RCO_PASS at exact head {HEAD}",
        ),
    ]
    result = check_rco_pass_present(
        events=events,
        task_id=TASK,
        head=HEAD,
        author_agent="claude-rco-2",
    )
    assert result["ok"] is False
    assert result["decision"] == "no_qualifying_pass"
    assert result["eligible_rco_agents"] == ["claude-rco-1"]
    assert result["satisfying_rco_agent"] is None


def test_veto_from_other_recognized_rco_blocks_backup_set() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            agent="claude-rco-1",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD}",
        ),
        _rco_event(
            ts="2026-06-03T10:01:00Z",
            agent="claude-rco-2",
            status="changes_requested",
            type_="finding",
            message="backup RCO veto at same head",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is False
    assert result["decision"] == "vetoed_after_pass"
    assert result["blocking_rco_agents"] == ["claude-rco-2"]


def test_stale_other_rco_veto_before_fresh_pass_does_not_block_rco_slot() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T09:00:00Z",
            agent="claude-rco-2",
            status="open",
            type_="finding",
            message="initial finding before the current reviewed head",
        ),
        _rco_event(
            ts="2026-06-03T09:10:00Z",
            agent="claude-rco-2",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at stale head {OTHER_HEAD}",
        ),
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            agent="claude-rco-1",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD}",
        ),
    ]

    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)

    assert result["ok"] is True
    assert result["decision"] == "rco_pass_present"
    assert result["satisfying_rco_agent"] == "claude-rco-1"
    assert result["blocking_rco_agents"] == []
    assert result["has_stale_rco_pass_at_other_head"] is True


def test_veto_then_fresh_pass_at_head_allows() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T09:00:00Z",
            status="changes_requested",
            type_="finding",
            message="initial block",
        ),
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="rco_review",
            message=f"re-reviewed; RCO_PASS at exact head {HEAD}",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is True
    assert result["has_qualifying_rco_pass_at_head"] is True
    assert result["latest_rco_is_veto"] is False


def test_type_blocked_counts_as_veto_after_pass() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"pass at {HEAD}",
        ),
        {
            "ts_utc": "2026-06-03T10:05:00Z",
            "agent": "claude-rco-1",
            "agent_uuid": AGENT_UUIDS["claude-rco-1"],
            "type": "blocked",
            "status": "blocked",
            "task_id": TASK,
            "message": "veto via blocked type",
            "payload": {},
        },
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is False
    assert result["decision"] == "vetoed_after_pass"


def test_non_decision_type_pass_ignored() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="message",  # not decision/rco_review
            message=f"pass at head {HEAD}",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is False
    assert result["has_qualifying_rco_pass_at_head"] is False


def test_payload_head_without_exact_head_does_not_qualify() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message="RCO_PASS (head not mentioned in text)",
            payload={
                "head": HEAD
            },
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is False
    assert result["has_qualifying_rco_pass_at_head"] is False


def test_payload_exact_head_qualifies_without_message_sha() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message="RCO_PASS for reviewed head",
            payload={"exact_head": HEAD},
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is True
    assert result["decision"] == "rco_pass_present"
    assert result["has_qualifying_rco_pass_at_head"] is True


def test_payload_exact_head_allows_case_and_whitespace() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message="RCO_PASS for reviewed head",
            payload={"exact_head": f"  {HEAD.upper()}  "},
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is True
    assert result["decision"] == "rco_pass_present"
    assert result["has_qualifying_rco_pass_at_head"] is True


def test_non_string_payload_exact_head_does_not_qualify() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message="RCO_PASS for reviewed head",
            payload={"exact_head": {"sha": HEAD}},
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is False
    assert result["decision"] == "no_qualifying_pass"
    assert result["has_qualifying_rco_pass_at_head"] is False


def test_other_task_events_ignored() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass",
            type_="decision",
            message=f"pass at {HEAD}",
            task_id="some/other-task",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is False


def test_author_task_slash_alias_pass_counts_without_mismatch() -> None:
    target_task = "codex-lead-1-promotion-canonical-consensus-regressions-20260607"
    slash_task = "codex-lead-1/promotion-canonical-consensus-regressions-20260607"
    events = [
        _rco_event(
            ts="2026-06-07T18:10:00Z",
            agent="claude-rco-2",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD}",
            task_id=slash_task,
        ),
    ]

    result = check_rco_pass_present(events=events, task_id=target_task, head=HEAD)

    assert result["ok"] is True
    assert result["decision"] == "rco_pass_present"
    assert result["has_qualifying_rco_pass_at_head"] is True
    assert result["has_task_id_mismatch_rco_pass_at_head"] is False
    assert result["rco_reemit_guidance"] is None
    assert result["task_id_aliases"] == [slash_task]
    assert result["rco_pass_event"]["task_id"] == slash_task
    assert result["accepted_task_id_alias_rco_events"] == [
        {
            "ts_utc": "2026-06-07T18:10:00Z",
            "agent": "claude-rco-2",
            "agent_uuid": AGENT_UUIDS["claude-rco-2"],
            "type": "decision",
            "status": "rco_pass",
            "task_id": slash_task,
        }
    ]


def test_author_task_alias_pass_is_blocked_by_later_canonical_veto() -> None:
    target_task = "codex-lead-1-promotion-canonical-consensus-regressions-20260607"
    slash_task = "codex-lead-1/promotion-canonical-consensus-regressions-20260607"
    events = [
        _rco_event(
            ts="2026-06-07T18:10:00Z",
            agent="claude-rco-2",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD}",
            task_id=slash_task,
        ),
        _rco_event(
            ts="2026-06-07T18:11:00Z",
            agent="claude-rco-2",
            status="changes_requested",
            type_="decision",
            message="later canonical veto",
            task_id=target_task,
        ),
    ]

    result = check_rco_pass_present(events=events, task_id=target_task, head=HEAD)

    assert result["ok"] is False
    assert result["decision"] == "vetoed_after_pass"
    assert result["has_qualifying_rco_pass_at_head"] is True
    assert result["blocking_rco_agents"] == ["claude-rco-2"]


def test_unrelated_task_exact_head_pass_is_reported_without_counting() -> None:
    target_task = "codex-lead-1-promotion-canonical-consensus-regressions-20260607"
    other_task = "codex-tools-1/promotion-canonical-consensus-regressions-20260607"
    events = [
        _rco_event(
            ts="2026-06-07T18:10:00Z",
            agent="claude-rco-2",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD}",
            task_id=other_task,
        ),
    ]

    result = check_rco_pass_present(events=events, task_id=target_task, head=HEAD)

    assert result["ok"] is False
    assert result["decision"] == "no_rco_events_for_task"
    assert result["has_qualifying_rco_pass_at_head"] is False
    assert result["has_task_id_mismatch_rco_pass_at_head"] is True
    assert result["rco_reemit_guidance"] == {
        "required": True,
        "reason": "task_id_mismatch_rco_pass_at_head",
        "preferred_task_id": target_task,
        "accepted_task_ids": [
            target_task,
            "codex-lead-1/promotion-canonical-consensus-regressions-20260607",
        ],
        "rejected_task_ids": [other_task],
        "head": HEAD,
        "target_rco_agents": ["claude-rco-1", "claude-rco-2"],
        "legacy_request_status": "rco_requested",
    }
    assert result["task_id_mismatch_rco_events"] == [
        {
            "ts_utc": "2026-06-07T18:10:00Z",
            "agent": "claude-rco-2",
            "agent_uuid": AGENT_UUIDS["claude-rco-2"],
            "type": "decision",
            "status": "rco_pass",
            "task_id": other_task,
        }
    ]


def test_other_task_self_rco_pass_is_not_reported_as_mismatch() -> None:
    other_task = "codex-lead-1/promotion-canonical-consensus-regressions-20260607"
    events = [
        _rco_event(
            ts="2026-06-07T18:10:00Z",
            agent="claude-rco-2",
            status="rco_pass",
            type_="decision",
            message=f"RCO_PASS at exact head {HEAD}",
            task_id=other_task,
        ),
    ]

    result = check_rco_pass_present(
        events=events,
        task_id=TASK,
        head=HEAD,
        author_agent="claude-rco-2",
    )

    assert result["ok"] is False
    assert result["has_task_id_mismatch_rco_pass_at_head"] is False
    assert result["task_id_mismatch_rco_events"] == []


def test_wrong_status_not_pass() -> None:
    events = [
        _rco_event(
            ts="2026-06-03T10:00:00Z",
            status="rco_pass_pending_ci",  # not the strict {rco_pass}
            type_="decision",
            message=f"pass at {HEAD}",
        ),
    ]
    result = check_rco_pass_present(events=events, task_id=TASK, head=HEAD)
    assert result["ok"] is False
    assert result["has_qualifying_rco_pass_at_head"] is False


# --- CLI tests (subprocess, exit codes, output) ---------------------------


def test_cli_exit_0_when_pass_at_head_present(tmp_path: Path) -> None:
    events_path = _seed_events(
        tmp_path,
        [
            _rco_event(
                status="rco_pass",
                type_="decision",
                message=f"RCO_PASS present at exact head {HEAD}",
            ),
        ],
    )
    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            TASK,
            "--head",
            HEAD,
            "--events",
            str(events_path),
            "--rco-agent",
            "claude-rco-1",
            "--author-agent",
            AUTHOR,
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0, f"stderr={res.stderr} stdout={res.stdout}"
    assert "RCO_PASS present at exact head" in res.stdout


def test_cli_accepts_utf8_bom_events_file(tmp_path: Path) -> None:
    events_path = _seed_events(
        tmp_path,
        [
            _rco_event(
                status="rco_pass",
                type_="decision",
                message=f"RCO_PASS present at exact head {HEAD}",
            ),
        ],
    )
    events_path.write_bytes(b"\xef\xbb\xbf" + events_path.read_bytes())

    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            TASK,
            "--head",
            HEAD,
            "--events",
            str(events_path),
            "--rco-agent",
            "claude-rco-1",
            "--author-agent",
            AUTHOR,
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )

    assert res.returncode == 0, f"stderr={res.stderr} stdout={res.stdout}"
    payload = json.loads(res.stdout)
    assert payload["ok"] is True
    assert payload["decision"] == "rco_pass_present"


def test_cli_default_events_uses_runtime_bridge_root_env_from_other_cwd(
    tmp_path: Path,
) -> None:
    events_path = _seed_events(
        tmp_path / "runtime",
        [
            _rco_event(
                status="rco_pass",
                type_="decision",
                message=f"RCO_PASS present at exact head {HEAD}",
            ),
        ],
    )
    other_cwd = tmp_path / "other-cwd"
    other_cwd.mkdir()
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(events_path.parent.parent)
    env.pop("AGENT_BRIDGE_ROOT", None)

    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            TASK,
            "--head",
            HEAD,
            "--rco-agent",
            "claude-rco-1",
            "--author-agent",
            AUTHOR,
            "--json",
        ],
        cwd=str(other_cwd),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert res.returncode == 0, f"stderr={res.stderr} stdout={res.stdout}"
    payload = json.loads(res.stdout)
    assert payload["ok"] is True
    assert payload["decision"] == "rco_pass_present"


def test_cli_refuse_on_no_pass_silence(tmp_path: Path) -> None:
    events_path = _seed_events(
        tmp_path,
        [
            _rco_event(status="rco_requested", type_="handoff", message="request"),
        ],
    )
    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            TASK,
            "--head",
            HEAD,
            "--events",
            str(events_path),
            "--author-agent",
            AUTHOR,
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode != 0
    assert "REFUSED" in (res.stderr or "") or "REFUSED" in (res.stdout or "")


def test_cli_refuse_on_stale_head(tmp_path: Path) -> None:
    events_path = _seed_events(
        tmp_path,
        [
            _rco_event(
                status="rco_pass",
                type_="decision",
                message=f"pass at old head {OTHER_HEAD}",
            ),
        ],
    )
    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            TASK,
            "--head",
            HEAD,
            "--events",
            str(events_path),
            "--author-agent",
            AUTHOR,
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode != 0
    payload = json.loads(res.stdout)
    assert payload["has_qualifying_rco_pass_at_head"] is False
    assert payload["has_stale_rco_pass_at_other_head"] is True
    assert payload["latest_stale_rco_pass_event"]["referenced_heads"] == [OTHER_HEAD]
    assert payload["rco_reemit_guidance"]["reason"] == "stale_rco_pass_head"
    assert payload["rco_reemit_guidance"]["stale_heads"] == [OTHER_HEAD]
    for key in CLAIM_GATES:
        assert payload[key] is False


def test_cli_json_accepts_author_task_slash_alias_pass(
    tmp_path: Path,
) -> None:
    target_task = "codex-lead-1-promotion-canonical-consensus-regressions-20260607"
    slash_task = "codex-lead-1/promotion-canonical-consensus-regressions-20260607"
    events_path = _seed_events(
        tmp_path,
        [
            _rco_event(
                agent="claude-rco-2",
                status="rco_pass",
                type_="decision",
                message=f"RCO_PASS present at exact head {HEAD}",
                task_id=slash_task,
            ),
        ],
    )

    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            target_task,
            "--head",
            HEAD,
            "--events",
            str(events_path),
            "--author-agent",
            AUTHOR,
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )

    assert res.returncode == 0, f"stderr={res.stderr} stdout={res.stdout}"
    payload = json.loads(res.stdout)
    assert payload["ok"] is True
    assert payload["decision"] == "rco_pass_present"
    assert payload["has_qualifying_rco_pass_at_head"] is True
    assert payload["has_task_id_mismatch_rco_pass_at_head"] is False
    assert payload["rco_reemit_guidance"] is None
    assert payload["task_id_aliases"] == [slash_task]
    assert payload["rco_pass_event"]["task_id"] == slash_task
    assert payload["accepted_task_id_alias_rco_events"][0]["agent"] == "claude-rco-2"


def test_cli_refuse_reports_other_task_mismatch_to_stderr(tmp_path: Path) -> None:
    target_task = "codex-lead-1-promotion-canonical-consensus-regressions-20260607"
    unrelated_task = "codex-tools-1/promotion-canonical-consensus-regressions-20260607"
    events_path = _seed_events(
        tmp_path,
        [
            _rco_event(
                agent="claude-rco-2",
                status="rco_pass",
                type_="decision",
                message=f"RCO_PASS present at exact head {HEAD}",
                task_id=unrelated_task,
            ),
        ],
    )

    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            target_task,
            "--head",
            HEAD,
            "--events",
            str(events_path),
            "--author-agent",
            AUTHOR,
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )

    assert res.returncode != 0
    assert "task-id mismatch pass candidate" in res.stderr
    assert unrelated_task in res.stderr
    assert (
        "re-emit RCO_PASS on accepted task_id: "
        "codex-lead-1-promotion-canonical-consensus-regressions-20260607"
    ) in res.stderr


def test_cli_json_reports_rco_reemit_guidance_for_task_mismatch(
    tmp_path: Path,
) -> None:
    target_task = "codex-lead-1-promotion-canonical-consensus-regressions-20260607"
    unrelated_task = "codex-tools-1/promotion-canonical-consensus-regressions-20260607"
    events_path = _seed_events(
        tmp_path,
        [
            _rco_event(
                agent="claude-rco-2",
                status="rco_pass",
                type_="decision",
                message=f"RCO_PASS present at exact head {HEAD}",
                task_id=unrelated_task,
            ),
        ],
    )

    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            target_task,
            "--head",
            HEAD,
            "--events",
            str(events_path),
            "--author-agent",
            AUTHOR,
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )

    assert res.returncode != 0
    payload = json.loads(res.stdout)
    assert payload["ok"] is False
    assert payload["has_task_id_mismatch_rco_pass_at_head"] is True
    assert payload["rco_reemit_guidance"] == {
        "required": True,
        "reason": "task_id_mismatch_rco_pass_at_head",
        "preferred_task_id": target_task,
        "accepted_task_ids": [
            target_task,
            "codex-lead-1/promotion-canonical-consensus-regressions-20260607",
        ],
        "rejected_task_ids": [unrelated_task],
        "head": HEAD,
        "target_rco_agents": ["claude-rco-1", "claude-rco-2"],
        "legacy_request_status": "rco_requested",
    }


def test_cli_refuse_on_changes_requested_after_pass(tmp_path: Path) -> None:
    events_path = _seed_events(
        tmp_path,
        [
            _rco_event(
                ts="2026-06-03T10:00Z",
                status="rco_pass",
                type_="decision",
                message=f"pass {HEAD}",
            ),
            _rco_event(
                ts="2026-06-03T10:05Z",
                status="changes_requested",
                type_="decision",
                message="veto",
            ),
        ],
    )
    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            TASK,
            "--head",
            HEAD,
            "--events",
            str(events_path),
            "--author-agent",
            AUTHOR,
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode != 0
    payload = json.loads(res.stdout)
    assert payload["decision"] == "vetoed_after_pass"
    assert payload["ok"] is False


def test_cli_exit_0_on_pass_present_no_later_veto(tmp_path: Path) -> None:
    events_path = _seed_events(
        tmp_path,
        [
            _rco_event(
                status="rco_pass",
                type_="decision",
                message=f"good at {HEAD}",
            ),
        ],
    )
    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            TASK,
            "--head",
            HEAD,
            "--events",
            str(events_path),
            "--author-agent",
            AUTHOR,
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0


def test_cli_wrong_rco_agent_not_counted(tmp_path: Path) -> None:
    events_path = _seed_events(
        tmp_path,
        [
            _rco_event(
                agent="someone-else",
                status="rco_pass",
                type_="decision",
                message=f"pass {HEAD}",
            ),
        ],
    )
    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            TASK,
            "--head",
            HEAD,
            "--events",
            str(events_path),
            "--rco-agent",
            "claude-rco-1",
            "--author-agent",
            AUTHOR,
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode != 0
    payload = json.loads(res.stdout)
    assert payload["has_qualifying_rco_pass_at_head"] is False


def test_cli_missing_events_file_fails_closed(tmp_path: Path) -> None:
    missing = tmp_path / ".agent-bridge" / "shared" / "events.jsonl"
    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            TASK,
            "--head",
            HEAD,
            "--events",
            str(missing),
            "--author-agent",
            AUTHOR,
            "--json",
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode != 0
    # may be 3 or 2; must not be 0
    payload = json.loads(res.stdout)
    assert payload["ok"] is False
    assert payload["claim_gate_satisfied"] is False


def test_cli_invalid_head_rejected(tmp_path: Path) -> None:
    events_path = _seed_events(tmp_path, [])
    res = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--task-id",
            TASK,
            "--head",
            "not-a-40-char-sha",
            "--events",
            str(events_path),
            "--author-agent",
            AUTHOR,
        ],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 2
    assert "40-char" in (res.stderr or "")


CLAIM_GATES = (
    "claim_gate_satisfied",
    "claim_safe",
    "literal_future_claim_safe",
    "controls_present",
    "runtime_authority_granted",
    "external_writes_applied",
    "required_runtime_evidence_present",
)
