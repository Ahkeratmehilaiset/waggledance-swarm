# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tools.check_promotion_eligible import (
    _find_private_marker,
    evaluate_promotion_eligibility,
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
    head: str = HEAD,
    origin_main_sha: str = BASE,
    prior_approved_head: str = "",
    prior_approved_diff_text: str | None = None,
    author_agent: str = "fable-5",
) -> dict:
    return evaluate_promotion_eligibility(
        pr_status=status or _status(),
        events=events if events is not None else _full_events(),
        task_id=TASK,
        head=head,
        origin_main_sha=origin_main_sha,
        prior_approved_head=prior_approved_head,
        prior_approved_diff_text=prior_approved_diff_text,
        author_agent=author_agent,
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
    ("author_agent", "role"),
    [
        ("codex-lead-1", "build_lead"),
        ("codex-tools-1", "build_tools"),
    ],
)
def test_author_build_self_pass_does_not_count(
    author_agent: str,
    role: str,
) -> None:
    report = _evaluate(author_agent=author_agent)

    identity = report["gate_results"]["bridge_consensus"]["by_agent"][
        "claude-rco-1"
    ]["identities"][role]
    assert report["eligible"] is False
    assert "bridge consensus incomplete" in report["reasons"]
    assert identity["eligible"] is False
    assert identity["approved"] is False
    assert identity["self_approval_ignored"] is True


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
