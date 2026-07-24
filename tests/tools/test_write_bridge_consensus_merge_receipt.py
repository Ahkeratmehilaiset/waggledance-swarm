# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from tools.bridge_pr_author import github_pr_git_identity_evidence
from tools.verify_magma_receipt import verify_manifest
from tools.write_bridge_consensus_merge_receipt import (
    BridgeConsensusMergeReceiptError,
    write_bridge_consensus_merge_receipt,
)


HEAD = "1234567890abcdef1234567890abcdef12345678"
BASE = "fedcba0987654321fedcba0987654321fedcba09"
TASK = "claude-rco-2/bridge-consensus-receipt-test"
NOW = datetime(2026, 6, 13, 22, 50, tzinfo=timezone.utc)
AGENT_UUIDS = {
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    "claude-rco-2": "76739997-0058-41a2-8514-78ff295537aa",
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
}


def test_writes_verified_bridge_consensus_merge_receipt(tmp_path: Path) -> None:
    events_path = _events_path(tmp_path, _full_consensus())
    report = write_bridge_consensus_merge_receipt(
        pr_status=_pr_status(),
        events_path=events_path,
        out_dir=tmp_path / "receipt",
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        repo="example/repo",
        from_agent="codex-lead-1",
        bridge_task_id=TASK,
        now_utc=NOW,
    )

    assert report["decision"] == "bridge_consensus_merge_receipt_written"
    assert report["ok"] is True
    bundle = report["receipt_bundle"]
    assert bundle["verifier_report"] == {
        "ok": True,
        "receipt_count": 1,
        "errors": [],
    }
    manifest = Path(report["receipt_bundle_path"])
    assert verify_manifest(manifest)["ok"] is True
    payload = json.loads(
        (manifest.parent / "payload-001-merge.json").read_text(encoding="utf-8")
    )
    receipt = json.loads(
        (manifest.parent / "receipt-001-merge.json").read_text(encoding="utf-8")
    )
    assert payload["bridge_consensus"]["decision"] == "bridge_consensus_verified"
    assert payload["diff_digest"].startswith("sha256:")
    assert receipt["risk_class"] == "external_effect"
    assert receipt["operator_gate_required"] is True
    assert receipt["approval_id"] == f"bridge:consensus:pr1174:{HEAD[:12]}"


def test_refuses_without_exact_head_rco_pass(tmp_path: Path) -> None:
    events = _full_consensus()[:-1]
    with pytest.raises(BridgeConsensusMergeReceiptError) as excinfo:
        write_bridge_consensus_merge_receipt(
            pr_status=_pr_status(),
            events_path=_events_path(tmp_path, events),
            out_dir=tmp_path / "receipt",
            expected_head=HEAD,
            expected_base_sha=BASE,
            consensus_proposal_id=TASK,
            repo="example/repo",
            from_agent="codex-lead-1",
            bridge_task_id=TASK,
            now_utc=NOW,
        )

    report = excinfo.value.report
    assert report["decision"] == "merge_plan_not_receipt_eligible"
    assert report["ok"] is False
    assert any("bridge consensus incomplete" in error for error in report["errors"])
    assert not (tmp_path / "receipt").exists()


def _pr_status() -> dict:
    material = github_pr_git_identity_evidence(
        {
            "author": {
                "login": "Ahkeratmehilaiset",
                "name": "",
                "email": "",
            },
            "commits": [
                {
                    "oid": HEAD,
                    "authors": [
                        {
                            "name": "Jani",
                            "email": "jani@jkhservice.fi",
                            "login": "",
                        }
                    ],
                }
            ],
        },
        expected_head_sha=HEAD,
    )
    identities = material.pop("identities")
    return {
        "pr_number": 1174,
        "title": "fix(bridge): test receipt writer",
        "head_sha": HEAD,
        "base_sha": BASE,
        "head_ref": TASK,
        "state": "OPEN",
        "is_draft": False,
        "mergeable": "MERGEABLE",
        "receipt_verified": True,
        "author_agent": "claude-rco-2",
        "changed_paths": ["tools/agent_next_task.py"],
        "diff_text": "+ diagnostic_commands = []\n",
        "checks": [
            {"name": "unified", "conclusion": "SUCCESS", "status": "COMPLETED"},
            {"name": "test (3.13)", "conclusion": "SUCCESS", "status": "COMPLETED"},
        ],
        "git_identities": identities,
        "git_identity_evidence": material,
    }


def _events_path(tmp_path: Path, events: list[dict]) -> Path:
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events),
        encoding="utf-8",
    )
    return path


def _full_consensus() -> list[dict]:
    return [
        _claim("claude-rco-2", "2026-06-13T22:39:00Z"),
        _event("codex-lead-1", "build_consensus_pass", "2026-06-13T22:40:00Z"),
        _event("codex-tools-1", "build_consensus_pass", "2026-06-13T22:41:00Z"),
        _event("claude-rco-1", "rco_pass", "2026-06-13T22:42:00Z"),
    ]


def _claim(agent: str, ts_utc: str) -> dict:
    event = _event(agent, "active", ts_utc)
    event["type"] = "claim"
    event["write_scope"] = ["*"]
    return event


def _event(agent: str, status: str, ts_utc: str) -> dict:
    return {
        "ts_utc": ts_utc,
        "agent": agent,
        "agent_uuid": AGENT_UUIDS[agent],
        "type": "decision",
        "status": status,
        "task_id": TASK,
        "message": f"{status} at head {HEAD}",
        "payload": {"head": HEAD, "pr": 1174},
    }
