import json
from pathlib import Path

from tools.idle_consensus_auto_merge import evaluate_auto_merge_gate


HEAD = "1234567890abcdef1234567890abcdef12345678"
TASK = "codex-tools-1/operator-gate-regression-20260606"
LEAD = "codex-lead-1"
TOOLS = "codex-tools-1"
RCO = "claude-rco-1"
AUTHOR = "claude-rco-2"
NON_RCO_AUTHOR = "fable-5"
AGENT_UUIDS = {
    LEAD: "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    TOOLS: "7a8af68d-20bc-4598-9953-23c5dd98b102",
    RCO: "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    AUTHOR: "76739997-0058-41a2-8514-78ff295537aa",
    NON_RCO_AUTHOR: "f8b1e5c0-3d2a-4e6b-9c1f-7a0d5e2b4c80",
}


def _approval(agent: str, status: str, *, ts: str) -> dict:
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": "decision",
        "status": status,
        "task_id": TASK,
        "message": f"{status} at exact head {HEAD}",
        "payload": {"head": HEAD},
        "agent_uuid": AGENT_UUIDS.get(agent),
    }


def _full_bridge_consensus() -> list[dict]:
    return [
        _approval(LEAD, "build_consensus_pass", ts="2026-06-06T01:00:00Z"),
        _approval(TOOLS, "build_consensus_pass", ts="2026-06-06T01:01:00Z"),
        _approval(RCO, "rco_pass", ts="2026-06-06T01:02:00Z"),
    ]


def _best_possible_consensus() -> list[dict]:
    return [
        *_full_bridge_consensus(),
        _approval(AUTHOR, "rco_pass", ts="2026-06-06T01:03:00Z"),
    ]


def _events_path(tmp_path: Path, events: list[dict]) -> Path:
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events),
        encoding="utf-8",
    )
    return path


def _status(**overrides) -> dict:
    status = {
        "pr_number": 907,
        "head_sha": HEAD,
        "title": "[codex] add operator feedback scheduler preflight",
        "mergeable": "clean",
        "receipt_verified": True,
        "author_agent": AUTHOR,
        "changed_paths": ["tests/tools/test_operator_gate_regression.py"],
        "diff_text": "+ def regression():\n+     return True\n",
        "checks": [
            {"name": "test (3.13)", "state": "success"},
            {"name": "unified", "state": "success"},
        ],
    }
    status.update(overrides)
    return status


def test_off_allowlist_change_uses_standing_consensus_sign_when_author_is_rco(
    tmp_path: Path,
) -> None:
    report = evaluate_auto_merge_gate(
        pr_status=_status(
            changed_paths=[
                "waggledance/core/autonomy_growth/operator_feedback_amplifier.py",
                "docs/eig2/contracts/operator_feedback_amplifier.json",
            ],
        ),
        expected_head=HEAD,
        consensus_proposal_id=TASK,
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, _full_bridge_consensus()),
        require_bridge_consensus=True,
    )

    assert report["bridge_consensus"]["ok"] is True
    assert report["rco_pass_gate"]["ok"] is True
    assert report["path_gate"]["allowed"] is False
    assert report["standing_consensus_sign"]["ok"] is True
    assert report["standing_consensus_sign"]["path_gate_waived"] is True
    assert report["standing_consensus_sign"]["dual_rco_pass_gate"][
        "required_rco_agents"
    ] == [RCO]
    assert report["ok"] is True
    assert report["would_merge"] is True
    assert report["external_effect"] is False
    assert report["operator_review_required"] is False
    assert report["decision"] == "auto_merge_plan_ready"
    assert report["reasons"] == ["all auto-merge gates passed"]


def test_off_allowlist_change_requires_dual_rco_for_non_rco_author(
    tmp_path: Path,
) -> None:
    report = evaluate_auto_merge_gate(
        pr_status=_status(
            author_agent=NON_RCO_AUTHOR,
            changed_paths=["docs/runs/48h_hex_mesh_autonomy_status.md"],
        ),
        expected_head=HEAD,
        consensus_proposal_id=TASK,
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, _full_bridge_consensus()),
        require_bridge_consensus=True,
    )

    assert report["bridge_consensus"]["ok"] is True
    assert report["path_gate"]["allowed"] is False
    assert report["standing_consensus_sign"]["ok"] is False
    assert report["standing_consensus_sign"]["eligible"] is True
    assert report["standing_consensus_sign"]["dual_rco_pass_gate"][
        "required_rco_agents"
    ] == [RCO, AUTHOR]
    assert report["ok"] is False
    assert report["operator_review_required"] is True
    assert report["decision"] == "operator_review_required"
    assert any(
        reason == "standing consensus sign incomplete: dual RCO_PASS incomplete: "
        f"{AUTHOR}: exact-head RCO_PASS required"
        for reason in report["reasons"]
    )


def test_off_allowlist_change_uses_standing_consensus_sign_with_dual_rco(
    tmp_path: Path,
) -> None:
    report = evaluate_auto_merge_gate(
        pr_status=_status(
            author_agent=NON_RCO_AUTHOR,
            changed_paths=["docs/runs/48h_hex_mesh_autonomy_status.md"],
        ),
        expected_head=HEAD,
        consensus_proposal_id=TASK,
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, _best_possible_consensus()),
        require_bridge_consensus=True,
    )

    assert report["bridge_consensus"]["ok"] is True
    assert report["path_gate"]["allowed"] is False
    assert report["standing_consensus_sign"]["ok"] is True
    assert report["standing_consensus_sign"]["path_gate_waived"] is True
    assert report["standing_consensus_sign"]["dual_rco_pass_gate"][
        "required_rco_agents"
    ] == [RCO, AUTHOR]
    assert report["ok"] is True
    assert report["operator_review_required"] is False
    assert report["decision"] == "auto_merge_plan_ready"


def test_bridge_bin_change_stays_operator_gated_even_with_full_consensus(
    tmp_path: Path,
) -> None:
    changed_path = ".agent-bridge/bin/Get-BridgeNextAction.ps1"
    report = evaluate_auto_merge_gate(
        pr_status=_status(
            changed_paths=[changed_path],
            diff_text=(
                "diff --git a/.agent-bridge/bin/Get-BridgeNextAction.ps1 "
                "b/.agent-bridge/bin/Get-BridgeNextAction.ps1\n"
                "--- a/.agent-bridge/bin/Get-BridgeNextAction.ps1\n"
                "+++ b/.agent-bridge/bin/Get-BridgeNextAction.ps1\n"
                "@@ -1,2 +1,3 @@\n"
                "+# bridge gate script edit remains operator-gated\n"
            ),
        ),
        expected_head=HEAD,
        consensus_proposal_id=TASK,
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, _full_bridge_consensus()),
        require_bridge_consensus=True,
    )

    assert report["bridge_consensus"]["ok"] is True
    assert report["rco_pass_gate"]["ok"] is True
    assert report["path_gate"]["allowed"] is False
    assert report["path_gate"]["blocked_paths"] == [changed_path]
    assert report["ok"] is False
    assert report["would_merge"] is False
    assert report["external_effect"] is False
    assert report["operator_review_required"] is True
    assert report["decision"] == "operator_review_required"
    assert "path gate failed: denylist hit" in report["reasons"]


def test_gate_skip_claim_stays_operator_gated_even_on_allowlist_with_consensus(
    tmp_path: Path,
) -> None:
    report = evaluate_auto_merge_gate(
        pr_status=_status(
            diff_text=(
                "diff --git a/tests/tools/test_operator_gate_regression.py "
                "b/tests/tools/test_operator_gate_regression.py\n"
                "--- a/tests/tools/test_operator_gate_regression.py\n"
                "+++ b/tests/tools/test_operator_gate_regression.py\n"
                "@@ -1,2 +1,4 @@\n"
                "+ gate_skip = True\n"
                "+ fast_track_grants_runtime_authority = true\n"
            ),
        ),
        expected_head=HEAD,
        consensus_proposal_id=TASK,
        receipt_bundle_path="docs/receipts/manifest.json",
        events_path=_events_path(tmp_path, _full_bridge_consensus()),
        require_bridge_consensus=True,
    )

    assert report["bridge_consensus"]["ok"] is True
    assert report["rco_pass_gate"]["ok"] is True
    assert report["path_gate"]["allowed"] is True
    assert report["diff_gate"]["allowed"] is False
    assert report["ok"] is False
    assert report["would_merge"] is False
    assert report["external_effect"] is False
    assert report["operator_review_required"] is True
    assert report["decision"] == "operator_review_required"
    assert any(
        "gate_skip=True" in hit or "fast_track_grants_runtime_authority=True" in hit
        for hit in report["diff_gate"]["code_pattern_hits"]
    )
    assert "diff gate failed: code pattern denylist hit" in report["reasons"]
