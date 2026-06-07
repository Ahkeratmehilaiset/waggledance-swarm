import json
from pathlib import Path

from tools.idle_consensus_auto_merge import evaluate_auto_merge_gate


HEAD = "1234567890abcdef1234567890abcdef12345678"
TASK = "codex-tools-1/operator-gate-regression-20260606"
LEAD = "codex-lead-1"
TOOLS = "codex-tools-1"
RCO = "claude-rco-1"


def _approval(agent: str, status: str, *, ts: str) -> dict:
    return {
        "ts_utc": ts,
        "agent": agent,
        "type": "decision",
        "status": status,
        "task_id": TASK,
        "message": f"{status} at exact head {HEAD}",
        "payload": {"head": HEAD},
    }


def _full_bridge_consensus() -> list[dict]:
    return [
        _approval(LEAD, "build_consensus_pass", ts="2026-06-06T01:00:00Z"),
        _approval(TOOLS, "build_consensus_pass", ts="2026-06-06T01:01:00Z"),
        _approval(RCO, "rco_pass", ts="2026-06-06T01:02:00Z"),
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
        "author_agent": LEAD,
        "changed_paths": ["tests/tools/test_operator_gate_regression.py"],
        "diff_text": "+ def regression():\n+     return True\n",
        "checks": [
            {"name": "test (3.13)", "state": "success"},
            {"name": "unified", "state": "success"},
        ],
    }
    status.update(overrides)
    return status


def test_off_allowlist_change_stays_operator_gated_even_with_full_consensus(
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
    assert report["ok"] is False
    assert report["would_merge"] is False
    assert report["external_effect"] is False
    assert report["operator_review_required"] is True
    assert report["decision"] == "operator_review_required"
    assert any(
        reason.startswith("path gate failed: paths not on allowlist")
        for reason in report["reasons"]
    )


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
