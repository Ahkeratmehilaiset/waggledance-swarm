# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import tools.check_promotion_eligible as promotion_tool
from tools.check_promotion_eligible import (
    PromotionEligibilityError,
    _find_private_marker,
    _read_events_fail_closed,
    evaluate_promotion_eligibility,
    main,
)

HEAD = "1234567890abcdef1234567890abcdef12345678"
NEW_HEAD = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
BASE = "abcdef1234567890abcdef1234567890abcdef12"
OTHER_BASE = "fedcba9876543210fedcba9876543210fedcba98"
TASK = "codex-lead-1/promotion-eligible-verifier-20260605"
SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "check_promotion_eligible.py"
AGENT_UUIDS = {
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    "claude-rco-2": "76739997-0058-41a2-8514-78ff295537aa",
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
    "fable-5": "f8b1e5c0-3d2a-4e6b-9c1f-7a0d5e2b4c80",
}


def _status(**overrides: object) -> dict:
    status = {
        "pr_number": 901,
        "head_sha": HEAD,
        "base_sha": BASE,
        "state": "OPEN",
        "mergeable": "MERGEABLE",
        "is_draft": True,
        "changed_paths": ["tools/idle_daily_summary.py"],
        "diff_text": "+ def helper():\n+     return 1\n",
        "checks": [
            {"name": "unified", "state": "success"},
            {"name": "test (3.13)", "conclusion": "success"},
        ],
    }
    status.update(overrides)
    return status


def _hex_operator_gate_required_expected() -> bool:
    return True


def _hex_acceptance(**overrides: object) -> dict:
    acceptance = {
        "schema_version": "hex_cell_promotion_acceptance.v0",
        "acceptance_id": "hexcellaccept:hex-thermal:frost-risk:abcdef1234567890",
        "competition_id": "hexcellcomp:hex-thermal:frost-risk:0123456789abcdef",
        "cell_id": "hex-thermal",
        "capability_id": "frost-risk",
        "accepted_candidate_id": "cand-alpha",
        "rejected_candidate_ids": ["cand-beta"],
        "competition_evidence_digest": "sha256:" + "1" * 64,
        "acceptance_digest": "sha256:" + "2" * 64,
        "evidence_digest_algorithm": "magma-jcs-subset-v1",
        "promotion_acceptance_status": "operator_gate_required",
        "required_next_gate": "solver_provenance_operator_activation",
        "operator_gate_required": _hex_operator_gate_required_expected(),
        "operator_gate_cleared": False,
        "runtime_authority_granted": False,
        "runtime_traffic_mutation_applied": False,
        "candidate_state_mutation_applied": False,
    }
    acceptance.update(overrides)
    return acceptance


def _event(
    agent: str,
    status: str,
    *,
    type_: str = "decision",
    task_id: str = TASK,
    head: str = HEAD,
    pr: int = 901,
    ts: str = "2026-06-05T05:30:00Z",
    payload: dict | None = None,
) -> dict:
    event = {
        "ts_utc": ts,
        "agent": agent,
        "type": type_,
        "status": status,
        "task_id": task_id,
        "message": f"{status} exact head {head}",
        "payload": {"head": head, "pr": pr} if payload is None else payload,
    }
    if agent in AGENT_UUIDS:
        event["agent_uuid"] = AGENT_UUIDS[agent]
    return event


def _full_events(*, rco_agent: str = "claude-rco-1") -> list[dict]:
    return [
        _event("codex-lead-1", "build_consensus_pass", ts="2026-06-05T05:30:00Z"),
        _event("codex-tools-1", "build_consensus_pass", ts="2026-06-05T05:31:00Z"),
        _event(rco_agent, "rco_pass", ts="2026-06-05T05:32:00Z"),
    ]


def _evaluate(
    *,
    status: dict | None = None,
    events: list[dict] | None = None,
    task_id: object = TASK,
    head: str = HEAD,
    origin_main_sha: str = BASE,
    pr_number: object = None,
    prior_approved_head: str = "",
    prior_approved_diff_text: str | None = None,
    charter_path: object = promotion_tool.DEFAULT_CHARTER_PATH,
    rco_agents: object = None,
    author_agent: str = "fable-5",
    from_agent: object = "promotion-pipeline",
) -> dict:
    return evaluate_promotion_eligibility(
        pr_status=_status() if status is None else status,
        events=events if events is not None else _full_events(),
        task_id=task_id,
        head=head,
        origin_main_sha=origin_main_sha,
        pr_number=pr_number,
        prior_approved_head=prior_approved_head,
        prior_approved_diff_text=prior_approved_diff_text,
        charter_path=charter_path,
        rco_agents=rco_agents,
        author_agent=author_agent,
        from_agent=from_agent,
    )


def _events_path(tmp_path: Path, events: list[dict]) -> Path:
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events),
        encoding="utf-8",
    )
    return path


def test_all_gates_pass_with_rco1() -> None:
    report = _evaluate()

    assert report["eligible"] is True
    assert report["decision"] == "promotion_eligible"
    assert report["would_undraft"] is True
    assert report["would_merge"] is True
    assert report["external_effect"] is False
    assert report["gate_results"]["rco_pass"]["satisfying_rco_agent"] == "claude-rco-1"
    assert (
        report["gate_results"]["rco_pass"]["by_agent"]["claude-rco-1"]["decision"]
        == "rco_pass_present"
    )
    assert (
        report["gate_results"]["bridge_consensus"]["by_agent"]["claude-rco-1"][
            "decision"
        ]
        == "bridge_consensus_verified"
    )


def test_rco2_can_satisfy_recognized_rco_slot() -> None:
    report = _evaluate(events=_full_events(rco_agent="claude-rco-2"))

    assert report["eligible"] is True
    assert report["gate_results"]["rco_pass"]["satisfying_rco_agent"] == "claude-rco-2"
    assert (
        report["gate_results"]["bridge_consensus"]["satisfying_rco_agent"]
        == "claude-rco-2"
    )


def test_configured_nondefault_rco_can_satisfy_all_gates() -> None:
    report = _evaluate(
        events=_full_events(rco_agent="fable-5"),
        rco_agents=["fable-5"],
        author_agent="codex-lead-1",
    )

    assert report["eligible"] is True
    assert report["decision"] == "promotion_eligible"
    assert report["gate_results"]["rco_pass"]["satisfying_rco_agent"] == "fable-5"
    assert (
        report["gate_results"]["bridge_consensus"]["satisfying_rco_agent"]
        == "fable-5"
    )


def test_configured_nondefault_rco_malformed_current_head_veto_is_invalid() -> None:
    events = _full_events()
    events.append(
        _event(
            "fable-5",
            "operator_review_required",
            type_="finding",
            ts="2026-06-05T05:33:00Z",
            payload={"head": HEAD, "pr": "901"},
        )
    )

    report = _evaluate(
        events=events,
        rco_agents=["fable-5", "claude-rco-1"],
        author_agent="codex-lead-1",
    )

    assert report["eligible"] is False
    assert report["decision"] == "invalid_input"
    assert "pr must be a positive integer" in report["errors"][0]


def test_descriptive_build_consensus_payload_head_fails_promotion() -> None:
    events = [
        _event(
            "codex-lead-1",
            "build_consensus_pass",
            task_id="lead-descriptive-refresh",
            payload={"head": HEAD},
            ts="2026-06-05T05:30:00Z",
        ),
        _event(
            "codex-tools-1",
            "build_consensus_pass",
            task_id="tools-descriptive-refresh",
            payload={"head": HEAD},
            ts="2026-06-05T05:31:00Z",
        ),
        _event("claude-rco-1", "rco_pass", ts="2026-06-05T05:32:00Z"),
    ]

    report = _evaluate(events=events)

    assert report["eligible"] is False
    assert "bridge consensus incomplete" in report["reasons"]
    assert report["gate_results"]["bridge_consensus"]["ok"] is False


def test_descriptive_build_consensus_stale_payload_head_fails_promotion() -> None:
    events = [
        _event(
            "codex-lead-1",
            "build_consensus_pass",
            task_id="lead-descriptive-refresh",
            payload={"head": OTHER_BASE},
            ts="2026-06-05T05:30:00Z",
        ),
        _event(
            "codex-tools-1",
            "build_consensus_pass",
            task_id="tools-descriptive-refresh",
            payload={"head": HEAD},
            ts="2026-06-05T05:31:00Z",
        ),
        _event("claude-rco-1", "rco_pass", ts="2026-06-05T05:32:00Z"),
    ]

    report = _evaluate(events=events)

    assert report["eligible"] is False
    assert report["decision"] == "promotion_not_eligible"
    assert "bridge consensus incomplete" in report["reasons"]


def test_descriptive_build_consensus_payload_head_block_fails_promotion() -> None:
    events = [
        _event(
            "codex-lead-1",
            "build_consensus_pass",
            task_id="lead-descriptive-refresh",
            payload={"head": HEAD},
            ts="2026-06-05T05:30:00Z",
        ),
        _event(
            "codex-tools-1",
            "build_consensus_pass",
            task_id="tools-descriptive-refresh",
            payload={"head": HEAD},
            ts="2026-06-05T05:31:00Z",
        ),
        _event("claude-rco-1", "rco_pass", ts="2026-06-05T05:32:00Z"),
        _event(
            "codex-lead-1",
            "changes_requested",
            task_id="lead-descriptive-block",
            payload={"head": HEAD},
            ts="2026-06-05T05:33:00Z",
        ),
    ]

    report = _evaluate(events=events)

    assert report["eligible"] is False
    assert "bridge consensus incomplete" in report["reasons"]
    assert (
        report["gate_results"]["bridge_consensus"]["by_agent"]["claude-rco-1"][
            "identities"
        ]["build_lead"]["approved"]
        is False
    )


def test_author_rco_self_pass_does_not_count() -> None:
    report = _evaluate(
        events=_full_events(rco_agent="claude-rco-2"),
        author_agent="claude-rco-2",
    )

    assert report["eligible"] is False
    assert (
        "missing exact-head RCO_PASS from recognized non-author RCO"
        in report["reasons"]
    )


@pytest.mark.parametrize(
    ("author_agent", "waived_role", "peer_role"),
    [
        ("codex-lead-1", "build_lead", "build_tools"),
        ("codex-tools-1", "build_tools", "build_lead"),
    ],
)
def test_build_author_slot_waiver_allows_with_independent_peer(
    author_agent: str,
    waived_role: str,
    peer_role: str,
) -> None:
    report = _evaluate(author_agent=author_agent)

    consensus = report["gate_results"]["bridge_consensus"]["by_agent"][
        "claude-rco-1"
    ]
    waived = consensus["identities"][waived_role]
    peer = consensus["identities"][peer_role]
    assert report["eligible"] is True
    assert consensus["build_author_slot_waivers"] == [author_agent]
    assert waived["eligible"] is False
    assert waived["approved"] is True
    assert waived["direct_approval"] is False
    assert waived["build_author_slot_waived"] is True
    assert waived["self_approval_ignored"] is True
    assert peer["approved"] is True


@pytest.mark.parametrize(
    ("author_agent", "waived_role", "peer_role"),
    [
        ("codex-lead-1", "build_lead", "build_tools"),
        ("codex-tools-1", "build_tools", "build_lead"),
    ],
)
def test_build_author_slot_waiver_requires_independent_peer(
    author_agent: str,
    waived_role: str,
    peer_role: str,
) -> None:
    report = _evaluate(
        author_agent=author_agent,
        events=[
            _event(author_agent, "build_consensus_pass", ts="2026-06-05T05:30:00Z"),
            _event("claude-rco-1", "rco_pass", ts="2026-06-05T05:32:00Z"),
        ],
    )

    consensus = report["gate_results"]["bridge_consensus"]["by_agent"][
        "claude-rco-1"
    ]
    waived = consensus["identities"][waived_role]
    peer = consensus["identities"][peer_role]
    assert report["eligible"] is False
    assert "bridge consensus incomplete" in report["reasons"]
    assert consensus["build_author_slot_waivers"] == [author_agent]
    assert waived["approved"] is True
    assert waived["direct_approval"] is False
    assert peer["approved"] is False


def test_missing_author_agent_fails_closed() -> None:
    report = _evaluate(author_agent="")

    assert report["eligible"] is False
    assert report["decision"] == "invalid_input"
    assert "author_agent is required" in report["errors"]


def test_plural_private_marker_helper_name_is_not_input_sentinel() -> None:
    helper_name = "PRIVATE" + "_MARKERS"

    assert _find_private_marker(f"+ assert all({helper_name})\n") is None


def test_plural_private_marker_helper_exception_is_diff_only() -> None:
    marker = "PRIVATE" + "_MARKER"
    helper_name = f"{marker}S"

    assert _find_private_marker(helper_name) == marker
    assert _find_private_marker(f"+ assert all('{helper_name}')\n") == marker
    assert _find_private_marker(f"+ assert all(X{helper_name})\n") == marker
    assert _find_private_marker(f"+ assert all({helper_name}_X)\n") == marker
    assert _find_private_marker(f"+ assert all({marker}_X)\n") == marker


def test_operator_gated_path_refuses() -> None:
    report = _evaluate(status=_status(changed_paths=["CLAUDE.md"]))

    assert report["eligible"] is False
    assert "path gate failed: denylist hit" in report["reasons"]
    assert report["gate_results"]["paths"]["blocked_paths"] == ["CLAUDE.md"]


def test_hex_promotion_acceptance_snapshot_passes_without_authority() -> None:
    report = _evaluate(
        status=_status(hex_cell_promotion_acceptance=_hex_acceptance())
    )

    assert report["eligible"] is True
    gate = report["gate_results"]["hex_promotion_acceptance"]
    assert gate["ok"] is True
    assert gate["decision"] == "hex_promotion_acceptance_valid"
    assert gate["operator_gate_cleared"] is False
    assert gate["runtime_authority_granted"] is False


def test_hex_promotion_acceptance_refuses_runtime_authority() -> None:
    report = _evaluate(
        status=_status(
            hex_cell_promotion_acceptance=_hex_acceptance(
                runtime_authority_granted=True
            )
        )
    )

    assert report["eligible"] is False
    assert (
        "hex promotion acceptance failed: runtime_authority_granted must be false"
        in report["reasons"]
    )
    assert report["gate_results"]["hex_promotion_acceptance"]["ok"] is False


def test_hex_promotion_acceptance_refuses_precleared_operator_gate() -> None:
    report = _evaluate(
        status=_status(
            hex_cell_promotion_acceptance=_hex_acceptance(
                operator_gate_cleared=True
            )
        )
    )

    assert report["eligible"] is False
    assert (
        "hex promotion acceptance failed: operator_gate_cleared must be false"
        in report["reasons"]
    )


def test_hex_promotion_acceptance_refuses_malformed_snapshot() -> None:
    report = _evaluate(status=_status(hex_cell_promotion_acceptance="yes"))

    assert report["eligible"] is False
    assert (
        "hex promotion acceptance failed: hex_cell_promotion_acceptance must be object"
        in report["reasons"]
    )


def test_missing_tools_build_consensus_refuses() -> None:
    events = [
        _event("codex-lead-1", "build_consensus_pass"),
        _event("claude-rco-1", "rco_pass"),
    ]
    report = _evaluate(events=events)

    assert report["eligible"] is False
    assert "bridge consensus incomplete" in report["reasons"]


def test_lead_cosign_waiver_receipt_does_not_replace_lead_build_consensus() -> None:
    events = [
        _event(
            "codex-tools-1",
            "build_consensus_pass",
            ts="2026-06-05T05:31:00Z",
        ),
        _event("claude-rco-1", "rco_pass", ts="2026-06-05T05:32:00Z"),
        _event(
            "claude-rco-1",
            "autonomous_merge_receipt",
            ts="2026-06-05T05:33:00Z",
            payload={"head": HEAD, "pr": 901, "lead_cosign_waived": True},
        ),
    ]

    report = _evaluate(events=events, author_agent="fable-5")

    assert report["eligible"] is False
    assert report["gate_results"]["rco_pass"]["ok"] is True
    assert "bridge consensus incomplete" in report["reasons"]
    consensus = report["gate_results"]["bridge_consensus"]["by_agent"][
        "claude-rco-1"
    ]
    assert consensus["identities"]["build_tools"]["approved"] is True
    assert consensus["identities"]["rco"]["approved"] is True
    assert consensus["identities"]["build_lead"]["approved"] is False


def test_pending_ci_refuses() -> None:
    report = _evaluate(status=_status(checks=[{"name": "unified", "state": "pending"}]))

    assert report["eligible"] is False
    assert "status checks not green: unified" in report["reasons"]


def test_stale_base_refuses() -> None:
    report = _evaluate(origin_main_sha=OTHER_BASE)

    assert report["eligible"] is False
    assert "base is stale" in report["reasons"]
    assert report["base_status"] == "stale"
    assert report["gate_results"]["base"]["origin_main_sha"] == OTHER_BASE


def test_content_identical_rebase_carries_prior_approvals() -> None:
    diff = "+ def helper():\n+     return 1\n"
    report = _evaluate(
        status=_status(head_sha=NEW_HEAD, diff_text=diff),
        events=_full_events(),
        head=NEW_HEAD,
        prior_approved_head=HEAD,
        prior_approved_diff_text=diff,
    )

    assert report["eligible"] is True
    assert report["base_status"] == "content_identical_rebase"
    assert report["carry_forward"] is True
    assert report["approval_head"] == HEAD
    assert report["gate_results"]["ci"]["ok"] is True
    assert report["gate_results"]["rco_pass"]["satisfying_rco_agent"] == "claude-rco-1"


def test_content_identical_rebase_still_requires_current_ci_green() -> None:
    diff = "+ def helper():\n+     return 1\n"
    report = _evaluate(
        status=_status(
            head_sha=NEW_HEAD,
            diff_text=diff,
            checks=[{"name": "unified", "state": "pending"}],
        ),
        events=_full_events(),
        head=NEW_HEAD,
        prior_approved_head=HEAD,
        prior_approved_diff_text=diff,
    )

    assert report["eligible"] is False
    assert report["carry_forward"] is True
    assert "status checks not green: unified" in report["reasons"]


def test_content_changed_repush_forfeits_carry_forward() -> None:
    report = _evaluate(
        status=_status(
            head_sha=NEW_HEAD, diff_text="+ def helper():\n+     return 2\n"
        ),
        events=_full_events(),
        head=NEW_HEAD,
        prior_approved_head=HEAD,
        prior_approved_diff_text="+ def helper():\n+     return 1\n",
    )

    assert report["eligible"] is False
    assert report["base_status"] == "content_changed"
    assert report["carry_forward"] is False
    assert "content changed since prior approved head" in report["reasons"]


def test_missing_prior_diff_for_rebased_head_fails_closed() -> None:
    report = _evaluate(
        status=_status(head_sha=NEW_HEAD),
        events=_full_events(),
        head=NEW_HEAD,
        prior_approved_head=HEAD,
    )

    assert report["eligible"] is False
    assert report["base_status"] == "content_changed"
    assert "prior approved diff required for carry-forward" in report["reasons"]


def test_backup_rco_veto_blocks_even_when_rco1_passes() -> None:
    events = [
        *_full_events(rco_agent="claude-rco-1"),
        _event(
            "claude-rco-2",
            "changes_requested",
            type_="finding",
            ts="2026-06-05T05:33:00Z",
        ),
    ]
    report = _evaluate(events=events)

    assert report["eligible"] is False
    assert any(
        reason.startswith("unresolved peer bridge block")
        for reason in report["reasons"]
    )
    assert report["gate_results"]["peer_veto"]["clear_to_merge"] is False


def test_malformed_status_fails_closed() -> None:
    status = _status()
    status.pop("changed_paths")

    report = _evaluate(status=status)

    assert report["eligible"] is False
    assert report["decision"] == "invalid_input"
    assert "changed_paths must be a list" in report["errors"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("task_id", False),
        ("task_id", f" {TASK}"),
        ("head", False),
        ("head", HEAD.upper()),
        ("head", f"{HEAD} "),
        ("origin_main_sha", 0),
        ("origin_main_sha", BASE.upper()),
        ("prior_approved_head", False),
        ("prior_approved_diff_text", False),
        ("author_agent", False),
        ("author_agent", 0),
        ("author_agent", " fable-5"),
        ("from_agent", False),
        ("from_agent", 0),
        ("from_agent", " promotion-pipeline"),
        ("rco_agents", False),
        ("rco_agents", []),
        ("rco_agents", [" claude-rco-1"]),
        ("rco_agents", [1]),
        ("charter_path", False),
        ("pr_number", False),
        ("pr_number", 0),
    ],
)
def test_direct_public_inputs_fail_before_any_gate(
    monkeypatch,
    field: str,
    value: object,
) -> None:
    gate_calls: list[str] = []

    def forbidden_charter(*_args, **_kwargs):
        gate_calls.append("load_charter")
        raise AssertionError("gate must not run")

    monkeypatch.setattr(promotion_tool, "load_charter", forbidden_charter)
    kwargs: dict[str, object] = {
        "pr_status": _status(),
        "events": _full_events(),
        "task_id": TASK,
        "head": HEAD,
        "origin_main_sha": BASE,
        "pr_number": None,
        "prior_approved_head": "",
        "prior_approved_diff_text": None,
        "charter_path": promotion_tool.DEFAULT_CHARTER_PATH,
        "rco_agents": None,
        "author_agent": "fable-5",
        "from_agent": "promotion-pipeline",
    }
    kwargs[field] = value

    report = evaluate_promotion_eligibility(**kwargs)  # type: ignore[arg-type]

    assert report["eligible"] is False
    assert report["decision"] == "invalid_input"
    assert gate_calls == []


@pytest.mark.parametrize(
    ("status_field", "value"),
    [
        ("head_sha", 123),
        ("head_sha", HEAD.upper()),
        ("base_sha", False),
        ("base_sha", BASE.upper()),
        ("changed_paths", [" tools/idle_daily_summary.py"]),
        ("changed_paths", []),
        ("diff_text", ""),
        ("checks", [{"state": "success"}]),
        (
            "checks",
            [{"name": "unified", "state": " success"}],
        ),
        ("is_draft", 0),
    ],
)
def test_nested_snapshot_authority_inputs_fail_before_any_gate(
    monkeypatch,
    status_field: str,
    value: object,
) -> None:
    gate_calls: list[str] = []
    status = _status()
    status[status_field] = value
    monkeypatch.setattr(
        promotion_tool,
        "load_charter",
        lambda *_args, **_kwargs: gate_calls.append("load_charter"),
    )

    report = _evaluate(status=status)

    assert report["eligible"] is False
    assert report["decision"] == "invalid_input"
    assert gate_calls == []


def test_object_coercion_cannot_forge_head_or_check_success(monkeypatch) -> None:
    class LooksValid:
        def __str__(self) -> str:
            return HEAD

    status = _status(
        head_sha=LooksValid(),
        checks=[{"name": "unified", "state": LooksValid()}],
    )
    gate_calls: list[str] = []
    monkeypatch.setattr(
        promotion_tool,
        "load_charter",
        lambda *_args, **_kwargs: gate_calls.append("load_charter"),
    )

    report = _evaluate(status=status)

    assert report["decision"] == "invalid_input"
    assert gate_calls == []


@pytest.mark.parametrize("state", ["MERGED", "CLOSED"])
def test_non_open_snapshot_state_is_ineligible(state: str) -> None:
    report = _evaluate(status=_status(state=state))

    assert report["eligible"] is False
    assert "PR state snapshot must be OPEN" in report["reasons"]


@pytest.mark.parametrize("mergeable", ["CONFLICTING", "UNKNOWN"])
def test_non_mergeable_snapshot_is_ineligible(mergeable: str) -> None:
    report = _evaluate(status=_status(mergeable=mergeable))

    assert report["eligible"] is False
    assert "PR mergeable snapshot must be MERGEABLE" in report["reasons"]


def test_ready_pr_can_merge_without_claiming_undraft() -> None:
    report = _evaluate(status=_status(is_draft=False))

    assert report["eligible"] is True
    assert report["would_undraft"] is False
    assert report["would_merge"] is True


def test_hex_evidence_ids_must_be_exact() -> None:
    report = _evaluate(
        status=_status(
            hex_cell_promotion_acceptance=_hex_acceptance(
                accepted_candidate_id="cand-alpha ",
            )
        )
    )

    assert report["eligible"] is False
    gate = report["gate_results"]["hex_promotion_acceptance"]
    assert gate["ok"] is False
    assert "exact string" in gate["reason"]


def test_event_privacy_marker_is_refused() -> None:
    events = _full_events()
    events[0]["message"] = "PRIVATE_MARKER"

    report = _evaluate(events=events)

    assert report["decision"] == "invalid_input"
    assert report["eligible"] is False


@pytest.mark.parametrize(
    "mutator",
    [
        lambda status: status.update(extra=b"bytes"),
        lambda status: status.update(extra=object()),
    ],
)
def test_non_json_snapshot_values_are_controlled(
    mutator,
) -> None:
    status = _status()
    mutator(status)

    report = _evaluate(status=status)

    assert report["decision"] == "invalid_input"
    assert report["eligible"] is False


def test_cyclic_snapshot_is_controlled() -> None:
    status = _status()
    status["cycle"] = status

    report = _evaluate(status=status)

    assert report["decision"] == "invalid_input"
    assert report["eligible"] is False
    assert "cycle" in report["errors"][0]


def test_deeply_nested_snapshot_is_controlled() -> None:
    status = _status()
    nested: list[object] = []
    cursor = nested
    for _ in range(70):
        child: list[object] = []
        cursor.append(child)
        cursor = child
    status["nested"] = nested

    report = _evaluate(status=status)

    assert report["decision"] == "invalid_input"
    assert "nesting depth" in report["errors"][0]


def test_malformed_event_envelope_is_controlled_before_gates(
    monkeypatch,
) -> None:
    events = _full_events()
    events[0]["status"] = False
    gate_calls: list[str] = []
    monkeypatch.setattr(
        promotion_tool,
        "load_charter",
        lambda *_args, **_kwargs: gate_calls.append("load_charter"),
    )

    report = _evaluate(events=events)

    assert report["decision"] == "invalid_input"
    assert gate_calls == []


@pytest.mark.parametrize("payload", [[], None])
def test_legacy_event_payload_shapes_and_empty_fields_remain_compatible(
    payload: object,
) -> None:
    events = _full_events()
    events.append(
        {
            "ts_utc": "2026-01-01T00:00:00Z",
            "agent": "legacy-agent",
            "type": "heartbeat",
            "status": "Legacy STATUS",
            "task_id": "",
            "message": "",
            "payload": payload,
            "pid": 0,
        }
    )

    report = _evaluate(events=events)

    assert report["eligible"] is True


def test_irrelevant_legacy_authority_payload_shapes_do_not_poison_history() -> None:
    events = _full_events()
    events.insert(
        0,
        {
            "ts_utc": "2026-01-01T00:00:00Z",
            "agent": "legacy-agent",
            "type": "note",
            "status": "Legacy Review",
            "task_id": "legacy-agent/unrelated-task",
            "message": "historical unrelated event",
            "payload": {
                "exact_head": True,
                "pr": "not-an-integer",
            },
        },
    )

    report = _evaluate(events=events)

    assert report["eligible"] is True


@pytest.mark.parametrize(
    ("payload_key", "payload_value"),
    [
        ("exact_head", False),
        ("exact_head", HEAD.upper()),
        ("pr", "901"),
        ("pr", False),
        ("task_id", False),
    ],
)
def test_relevant_authority_payload_values_require_exact_types(
    payload_key: str,
    payload_value: object,
) -> None:
    events = _full_events()
    events[0]["payload"][payload_key] = payload_value

    report = _evaluate(events=events)

    assert report["decision"] == "invalid_input"
    assert report["eligible"] is False


def test_exact_head_true_flag_is_valid_with_typed_head_binding() -> None:
    events = _full_events()
    events[0]["payload"]["exact_head"] = True

    report = _evaluate(events=events)

    assert report["eligible"] is True


@pytest.mark.parametrize("event_indexes", [(0, 1, 2), (0,), (2,)])
def test_typed_stale_head_cannot_fall_back_to_current_message_head(
    event_indexes: tuple[int, ...],
) -> None:
    events = _full_events()
    for index in event_indexes:
        events[index]["payload"]["head"] = "f" * 40

    report = _evaluate(events=events)

    if event_indexes == (2,):
        assert report["eligible"] is True
        assert report["decision"] == "promotion_eligible"
    else:
        assert report["eligible"] is False
        assert report["decision"] == "promotion_not_eligible"
        assert "bridge consensus incomplete" in report["reasons"]


@pytest.mark.parametrize(
    "clear_status",
    [
        "changes_requested_cleared_ci_green",
        "rco_changes_requested_cleared",
        "approved_waiver_block_cleared",
        "producer_no_block_reemit_required",
    ],
)
def test_peer_consumed_clear_cannot_bypass_malformed_authority_payload(
    clear_status: str,
) -> None:
    events = _full_events()
    events.extend(
        [
            _event(
                "codex-tools-1",
                "changes_requested",
                ts="2026-06-05T05:33:00Z",
            ),
            _event(
                "codex-tools-1",
                clear_status,
                ts="2026-06-05T05:34:00Z",
                payload={"head": HEAD, "pr": "901"},
            ),
        ]
    )

    report = _evaluate(events=events)

    assert report["eligible"] is False
    assert report["decision"] == "invalid_input"
    assert "pr must be a positive integer" in report["errors"][0]


def test_pr_task_scope_malformed_clear_cannot_reopen_current_block() -> None:
    events = _full_events()
    events.extend(
        [
            _event(
                "codex-tools-1",
                "changes_requested",
                ts="2026-06-05T05:33:00Z",
            ),
            _event(
                "codex-tools-1",
                "changes_requested_cleared",
                task_id="review for PR #901",
                ts="2026-06-05T05:34:00Z",
                payload={"head": 123},
            ),
        ]
    )
    events[-1]["message"] = "changes_requested_cleared"

    report = _evaluate(events=events)

    assert report["eligible"] is False
    assert report["decision"] == "invalid_input"
    assert "head must be an exact lowercase sha" in report["errors"][0]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"head": NEW_HEAD, "pr": 901},
            f"changes_requested_cleared exact head {NEW_HEAD}",
        ),
        ({"pr": 901}, "changes_requested_cleared"),
        ({"head": NEW_HEAD, "pr": 901}, "changes_requested_cleared"),
    ],
)
def test_stale_or_headless_clear_cannot_reopen_current_block(
    payload: dict,
    message: str,
) -> None:
    events = _full_events()
    events.extend(
        [
            _event(
                "codex-tools-1",
                "changes_requested",
                ts="2026-06-05T05:33:00Z",
            ),
            _event(
                "codex-tools-1",
                "changes_requested_cleared",
                ts="2026-06-05T05:34:00Z",
                payload=payload,
            ),
        ]
    )
    events[-1]["message"] = message

    report = _evaluate(events=events)

    assert report["eligible"] is False
    assert report["decision"] == "promotion_not_eligible"
    assert "unresolved peer bridge block" in report["reasons"][0]
    assert "bridge consensus incomplete" in report["reasons"]


def test_block_by_type_is_never_filtered_as_stale_enabling_evidence() -> None:
    events = _full_events()
    events.append(
        _event(
            "claude-rco-1",
            "rco_pass",
            type_="finding",
            ts="2026-06-05T05:33:00Z",
            payload={"pr": 901},
        )
    )
    events[-1]["message"] = "headless finding remains a fail-closed veto"

    report = _evaluate(events=events)

    assert report["eligible"] is False
    assert report["decision"] == "promotion_not_eligible"
    assert "unresolved peer bridge block" in report["reasons"][0]


@pytest.mark.parametrize(
    "message",
    [
        f"rco_pass exact head {HEAD}; superseded exact head {NEW_HEAD}",
        f"reviewed base {HEAD}; no exact-head binding",
    ],
)
def test_rco_payload_head_does_not_hide_unsafe_message_fallback(
    message: str,
) -> None:
    events = _full_events()
    events[2]["message"] = message

    report = _evaluate(events=events)

    assert report["eligible"] is False
    assert report["decision"] == "promotion_not_eligible"
    assert (
        "missing exact-head RCO_PASS from recognized non-author RCO"
        in report["reasons"]
    )


def test_rco_exact_head_string_can_dominate_noisy_message() -> None:
    events = _full_events()
    events[2]["payload"]["exact_head"] = HEAD
    events[2]["message"] = (
        f"rco_pass exact head {HEAD}; superseded exact head {NEW_HEAD}"
    )

    report = _evaluate(events=events)

    assert report["eligible"] is True
    assert report["decision"] == "promotion_eligible"


def test_rco_stale_typed_exact_head_cannot_fall_back_to_current_message() -> None:
    events = _full_events()
    events[2]["payload"] = {"exact_head": NEW_HEAD, "pr": 901}
    events[2]["message"] = f"rco_pass exact head {HEAD}"

    report = _evaluate(events=events)

    assert report["eligible"] is False
    assert report["decision"] == "promotion_not_eligible"
    assert (
        "missing exact-head RCO_PASS from recognized non-author RCO"
        in report["reasons"]
    )


@pytest.mark.parametrize("event_index", [0, 1, 2])
@pytest.mark.parametrize(
    "message",
    [
        f"build_consensus_pass superseded exact head {HEAD}",
        f"rco_pass not exact head {HEAD}",
        f"approval base exact head {HEAD}",
        f"exact head {HEAD} is stale",
        f"build_consensus_pass superseded (exact head {HEAD})",
        f"build_consensus_pass not, exact head {HEAD}",
        f"rco_pass isn't exact head {HEAD}",
        f"rco_pass wrong exact head {HEAD}",
        f"rco_pass invalid exact head {HEAD}",
        f"rco_pass cannot approve exact head {HEAD}",
        f"rco_pass exact head {HEAD}, is stale",
        f"rco_pass exact head {HEAD} isn't current",
        f"rco_pass PR #902 exact head {HEAD}",
    ],
)
def test_unsafe_message_head_roles_cannot_enable_authority(
    event_index: int,
    message: str,
) -> None:
    events = _full_events()
    events[event_index]["payload"] = {"pr": 901}
    events[event_index]["message"] = message

    report = _evaluate(events=events)

    assert report["eligible"] is False
    assert report["decision"] == "promotion_not_eligible"
    assert (
        "missing exact-head RCO_PASS from recognized non-author RCO"
        in report["reasons"]
        if event_index == 2
        else "bridge consensus incomplete" in report["reasons"]
    )


@pytest.mark.parametrize(
    "message",
    [
        f"build_consensus_pass exact head {HEAD}",
        f"build_consensus_pass PR #901 exact head {HEAD}",
        f"build_consensus_pass for PR #901 at exact head {HEAD}",
        f"build consensus pass at exact-head {HEAD}.",
        f"no blockers at exact head {HEAD}",
        f"no issues at exact head: {HEAD}.",
    ],
)
def test_canonical_positive_message_head_fallback_remains_valid(
    message: str,
) -> None:
    events = _full_events()
    events[0]["payload"] = {"pr": 901}
    events[0]["message"] = message

    report = _evaluate(events=events)

    assert report["eligible"] is True
    assert report["decision"] == "promotion_eligible"


@pytest.mark.parametrize(
    "status",
    [
        "not_approved",
        "approved_not",
        "rco_not_pass",
        "acknowledged_not",
    ],
)
def test_negated_approval_shaped_status_cannot_clear_peer_block(
    status: str,
) -> None:
    events = _full_events()
    events.extend(
        [
            _event(
                "peer-agent",
                "changes_requested",
                ts="2026-06-05T05:33:00Z",
            ),
            _event(
                "peer-agent",
                status,
                ts="2026-06-05T05:34:00Z",
                payload={"head": HEAD, "pr": 901},
            ),
        ]
    )
    events[-1]["message"] = f"{status} exact head {HEAD}"

    report = _evaluate(events=events)

    assert report["eligible"] is False
    assert report["decision"] == "promotion_not_eligible"
    assert "unresolved peer bridge block" in report["reasons"][0]


@pytest.mark.parametrize(
    "status",
    [
        "ack",
        "ACK",
        "acknowledged",
        "received",
        "seen",
        "wake_ack",
        "received_with_context",
        "wake_acknowledged",
    ],
)
def test_ack_status_token_cannot_enable_promotion_or_clear_peer_block(
    status: str,
) -> None:
    events = _full_events()
    events.extend(
        [
            _event(
                "peer-agent",
                "changes_requested",
                ts="2026-06-05T05:33:00Z",
            ),
            _event(
                "peer-agent",
                status,
                ts="2026-06-05T05:34:00Z",
                payload={"head": HEAD, "pr": 901},
            ),
        ]
    )
    events[-1]["message"] = f"{status} exact head {HEAD}"

    report = _evaluate(events=events)

    assert report["eligible"] is False
    assert report["decision"] == "promotion_not_eligible"
    assert "unresolved peer bridge block" in report["reasons"][0]


def test_custom_rco_informational_finding_does_not_poison_history() -> None:
    events = _full_events(rco_agent="fable-5")
    events.append(
        _event(
            "fable-5",
            "info",
            type_="finding",
            ts="2026-06-05T05:33:00Z",
            payload={"head": HEAD, "pr": "901"},
        )
    )

    report = _evaluate(
        events=events,
        rco_agents=["fable-5"],
        author_agent="codex-lead-1",
    )

    assert report["eligible"] is True
    assert report["decision"] == "promotion_eligible"


def test_ambiguous_explicit_message_head_fallback_is_invalid() -> None:
    events = _full_events()
    events[0]["payload"] = {"pr": 901}
    events[0]["message"] = (
        f"build_consensus_pass exact head {HEAD}; "
        f"superseded exact head {NEW_HEAD}"
    )

    report = _evaluate(events=events)

    assert report["eligible"] is False
    assert report["decision"] == "promotion_not_eligible"
    assert "bridge consensus incomplete" in report["reasons"]


@pytest.mark.parametrize("event_index", [0, 1, 2])
@pytest.mark.parametrize(
    "message",
    [
        f"reviewed exact head {NEW_HEAD}; base {HEAD}",
        f"reviewed exact head {NEW_HEAD}; superseded {HEAD}",
        f"base {HEAD}; no exact-head binding",
    ],
)
def test_loose_current_sha_cannot_satisfy_message_only_authority_binding(
    event_index: int,
    message: str,
) -> None:
    events = _full_events()
    events[event_index]["payload"] = {"pr": 901}
    events[event_index]["message"] = message

    report = _evaluate(events=events)

    assert report["eligible"] is False
    assert report["decision"] == "promotion_not_eligible"
    assert (
        "missing exact-head RCO_PASS from recognized non-author RCO"
        in report["reasons"]
        if event_index == 2
        else "bridge consensus incomplete" in report["reasons"]
    )


def test_message_only_stale_authority_does_not_poison_current_head() -> None:
    events = _full_events()
    for index, (agent, status) in enumerate(
        [
            ("codex-lead-1", "build_consensus_pass"),
            ("codex-tools-1", "build_consensus_pass"),
            ("claude-rco-1", "rco_pass"),
        ],
        start=40,
    ):
        event = _event(
            agent,
            status,
            ts=f"2026-06-05T05:{index}:00Z",
            payload={"pr": 901},
        )
        event["message"] = f"{status} exact head {NEW_HEAD}"
        events.append(event)

    report = _evaluate(events=events)

    assert report["eligible"] is True
    assert report["decision"] == "promotion_eligible"


def test_canonical_pr1551_diagnostic_string_pr_is_not_authority() -> None:
    event = {
        "ts_utc": "2026-07-21T08:30:56.8213241Z",
        "agent": "codex-tools-1",
        "type": "handoff",
        "status": "rco_lane_verification_requested",
        "task_id": (
            "rco-lane-failover-scout-2026-07-21-claude-rco-2-"
            "since-20260720t054533z"
        ),
        "message": (
            "Please verify the inactive lane for PR #1551 at "
            "e6870ebb91b1c30b6278b4d80e261479c325798d."
        ),
        "payload": {
            "authority": "diagnostic_only",
            "head": "e6870ebb91b1c30b6278b4d80e261479c325798d",
            "pr": "1551",
            "task_id": (
                "codex-lead-1/idle-dispatcher-rco-requests-20260720"
            ),
        },
    }
    events = [event]

    promotion_tool._validate_event_envelopes(events)
    promotion_tool._validate_event_authority_consistency(
        events,
        expected_task_id="codex-tools-1/unified-bridge-author-resolver-20260724",
        expected_pr_number=1551,
        expected_head="e6870ebb91b1c30b6278b4d80e261479c325798d",
    )


def test_canonical_pr1557_pass_allows_base_and_superseded_sha_roles() -> None:
    head = "c4f63493968c7ae73fea43c8b0a372ff6e7319af"
    current_head = "edbe8ddf048ce4e58e6d4d47082326e22e9d5b9d"
    base = "ae61cf33eae2d3b9b517663fcb63bdaa61ea4201"
    superseded = "73c7f864d63b9be666644f3e13f1dc2f42c4fefe"
    task = "fable-5/w2a-cell-identity-lineage-contracts-20260724"
    event = {
        "ts_utc": "2026-07-24T06:55:08.2155416Z",
        "agent": "codex-tools-1",
        "type": "decision",
        "status": "build_consensus_pass",
        "task_id": task,
        "message": (
            f"TOOLS independent exact-head PASS for draft PR #1557 "
            f"at exact head {head} (base {base}). "
            f"The superseded {superseded} timestamp gap is fixed."
        ),
        "payload": {
            "base": base,
            "head": head,
            "pr": 1557,
            "superseded_head": superseded,
            "verdict": "build_consensus_pass",
        },
    }
    events = [event]

    promotion_tool._validate_event_envelopes(events)
    promotion_tool._validate_event_authority_consistency(
        events,
        expected_task_id=task,
        expected_pr_number=1557,
        expected_head=current_head,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("type", "Decision"),
        ("status", "RCO_PASS"),
        ("status", "CHANGES_REQUESTED"),
    ],
)
def test_authority_type_and_status_must_be_canonical_lowercase(
    field: str,
    value: str,
) -> None:
    events = _full_events()
    events[2][field] = value

    report = _evaluate(events=events)

    assert report["decision"] == "invalid_input"
    assert report["eligible"] is False


def test_explicit_event_and_rco_tuples_are_rejected() -> None:
    event_report = evaluate_promotion_eligibility(
        pr_status=_status(),
        events=tuple(_full_events()),  # type: ignore[arg-type]
        task_id=TASK,
        head=HEAD,
        origin_main_sha=BASE,
        author_agent="fable-5",
        from_agent="promotion-pipeline",
    )
    rco_report = _evaluate(
        rco_agents=("claude-rco-1", "claude-rco-2"),
    )

    assert event_report["decision"] == "invalid_input"
    assert rco_report["decision"] == "invalid_input"


def test_direct_authority_integer_bounds_are_enforced() -> None:
    pr_report = _evaluate(pr_number=1 << 63)
    events = _full_events()
    events[0]["pid"] = 1 << 63
    pid_report = _evaluate(events=events)

    assert pr_report["decision"] == "invalid_input"
    assert pid_report["decision"] == "invalid_input"


@pytest.mark.parametrize(
    "paths",
    [
        ["tools/A.py", "tools/a.py"],
        ["tools/caf\u00e9.py", "tools/cafe\u0301.py"],
        ["tools/a.py", "tools/a.py"],
    ],
)
def test_changed_path_aliases_are_refused(paths: list[str]) -> None:
    report = _evaluate(status=_status(changed_paths=paths))

    assert report["decision"] == "invalid_input"
    assert report["eligible"] is False


def test_missing_charter_is_controlled_before_path_gate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    gate_calls: list[str] = []
    monkeypatch.setattr(
        promotion_tool,
        "evaluate_paths",
        lambda *_args, **_kwargs: gate_calls.append("evaluate_paths"),
    )

    report = _evaluate(charter_path=tmp_path / "missing-charter.yaml")

    assert report["decision"] == "invalid_input"
    assert report["errors"] == ["charter could not be loaded"]
    assert gate_calls == []


@pytest.mark.parametrize(
    "line",
    [
        '{"agent":"codex-lead-1","agent":"codex-tools-1"}',
        '{"value":NaN}',
        '{"value":Infinity}',
        '{"value":1e999}',
    ],
)
def test_event_loader_rejects_ambiguous_json(
    tmp_path: Path,
    line: str,
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(line, encoding="utf-8")

    with pytest.raises(PromotionEligibilityError) as excinfo:
        _read_events_fail_closed(path)

    assert excinfo.value.report["decision"] == "invalid_input"
    assert str(path) not in " ".join(excinfo.value.report["errors"])


def test_event_loader_rejects_invalid_utf8_without_path_leak(
    tmp_path: Path,
) -> None:
    path = tmp_path / "secret-events.jsonl"
    path.write_bytes(b"\x80")

    with pytest.raises(PromotionEligibilityError) as excinfo:
        _read_events_fail_closed(path)

    assert excinfo.value.report["decision"] == "invalid_input"
    assert str(path) not in " ".join(excinfo.value.report["errors"])


@pytest.mark.parametrize(
    ("target", "raw"),
    [
        ("status", b'{"pr_number":901,"pr_number":902}'),
        ("status", b'{"value":NaN}'),
        ("status", b'{"value":1e999}'),
        ("status", b"\x80"),
        ("status", b"[" * 1100 + b"0" + b"]" * 1100),
        ("status", b'{"value":' + (b"9" * 5000) + b"}"),
        ("events", b'{"agent":"a","agent":"b"}'),
        ("events", b'{"value":Infinity}'),
        ("events", b'{"value":1e999}'),
        ("events", b"\x80"),
        ("events", b'{"value":' + (b"9" * 5000) + b"}"),
        ("events", (b'{"value":' + (b"[" * 5000) + b"0" + (b"]" * 5000) + b"}")),
    ],
)
def test_cli_strict_json_inputs_fail_closed(
    tmp_path: Path,
    capsys,
    target: str,
    raw: bytes,
) -> None:
    status_path = tmp_path / "status.json"
    events_path = tmp_path / "events.jsonl"
    status_path.write_text(json.dumps(_status()), encoding="utf-8")
    events_path.write_text(
        "\n".join(json.dumps(event) for event in _full_events()),
        encoding="utf-8",
    )
    (status_path if target == "status" else events_path).write_bytes(raw)

    exit_code = main(
        [
            "--pr-status-file",
            str(status_path),
            "--events",
            str(events_path),
            "--task-id",
            TASK,
            "--head",
            HEAD,
            "--origin-main-sha",
            BASE,
            "--author-agent",
            "fable-5",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert payload["decision"] == "invalid_input"
    assert payload["eligible"] is False


def test_cli_returns_zero_only_when_eligible(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text(json.dumps(_status()), encoding="utf-8")
    events_path = _events_path(tmp_path, _full_events())

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--pr-status-file",
            str(status_path),
            "--events",
            str(events_path),
            "--task-id",
            TASK,
            "--head",
            HEAD,
            "--origin-main-sha",
            BASE,
            "--author-agent",
            "fable-5",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["eligible"] is True
    assert payload["external_effect"] is False


def test_cli_default_events_uses_runtime_bridge_root_env_from_other_cwd(
    tmp_path: Path,
) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text(json.dumps(_status()), encoding="utf-8")
    bridge_root = tmp_path / "runtime" / ".agent-bridge"
    events_path = bridge_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in _full_events()),
        encoding="utf-8",
    )
    other_cwd = tmp_path / "other-cwd"
    other_cwd.mkdir()
    env = os.environ.copy()
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(bridge_root)
    env.pop("AGENT_BRIDGE_ROOT", None)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--pr-status-file",
            str(status_path),
            "--task-id",
            TASK,
            "--head",
            HEAD,
            "--origin-main-sha",
            BASE,
            "--author-agent",
            "fable-5",
            "--json",
        ],
        cwd=str(other_cwd),
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["eligible"] is True
    assert payload["decision"] == "promotion_eligible"


def test_cli_returns_three_when_not_eligible(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text(
        json.dumps(_status(checks=[{"name": "unified", "state": "pending"}])),
        encoding="utf-8",
    )
    events_path = _events_path(tmp_path, _full_events())

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--pr-status-file",
            str(status_path),
            "--events",
            str(events_path),
            "--task-id",
            TASK,
            "--head",
            HEAD,
            "--origin-main-sha",
            BASE,
            "--author-agent",
            "codex-lead-1",
            "--json",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 3
    payload = json.loads(result.stdout)
    assert payload["eligible"] is False
    assert "status checks not green: unified" in payload["reasons"]
