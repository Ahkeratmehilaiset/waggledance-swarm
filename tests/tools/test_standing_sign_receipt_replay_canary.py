# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.standing_sign_receipt_replay_canary import (
    replay_standing_sign_receipt,
)
from waggledance.core.magma.canonical import sha256_digest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "standing_sign_receipt_replay_canary.py"
HEAD = "1234567890abcdef1234567890abcdef12345678"
OTHER_HEAD = "fedcba9876543210fedcba9876543210fedcba98"
BASE = "abcdef1234567890abcdef1234567890abcdef12"
TASK = "idle-consensus-001"
AGENT_UUIDS = {
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    "claude-rco-2": "76739997-0058-41a2-8514-78ff295537aa",
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
    "fable-5": "f8b1e5c0-3d2a-4e6b-9c1f-7a0d5e2b4c80",
}


def test_replays_standing_sign_receipt_pass(tmp_path: Path) -> None:
    events_path = _events_path(tmp_path, _dual_rco_events())

    report = replay_standing_sign_receipt(
        pr_status=_status(),
        receipt_payload=_receipt_payload(),
        events_path=events_path,
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        bridge_task_id=TASK,
    )

    assert report["decision"] == "standing_sign_receipt_replay_pass"
    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["gate_decision"] == "auto_merge_plan_ready"
    assert report["standing_consensus_sign"]["admitted"] is True
    assert report["bridge_consensus_decision"] == "bridge_consensus_verified"
    assert report["receipt_replay"]["matched"] is True
    assert report["authority_boundary"] == {
        "read_only": True,
        "writes_receipt_bundle": False,
        "emits_bridge_events": False,
        "runs_gh_merge": False,
        "grants_runtime_authority": False,
        "skips_gate": False,
    }


def test_stale_head_blocks_replay(tmp_path: Path) -> None:
    report = replay_standing_sign_receipt(
        pr_status=_status(),
        receipt_payload=_receipt_payload(),
        events_path=_events_path(tmp_path, _dual_rco_events()),
        expected_head=OTHER_HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        bridge_task_id=TASK,
    )

    assert report["ok"] is False
    assert any("exact head mismatch" in blocker for blocker in report["blockers"])
    assert any("head_sha mismatch" in blocker for blocker in report["blockers"])


def test_missing_second_rco_blocks_standing_sign_replay(tmp_path: Path) -> None:
    report = replay_standing_sign_receipt(
        pr_status=_status(),
        receipt_payload=_receipt_payload(),
        events_path=_events_path(tmp_path, _dual_rco_events()[:-1]),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        bridge_task_id=TASK,
    )

    assert report["ok"] is False
    assert any("DUAL-RCO incomplete" in blocker for blocker in report["blockers"])


def test_author_rco_slot_blocks_dual_rco_replay(tmp_path: Path) -> None:
    events = _dual_rco_events()
    events[0] = _claim("claude-rco-1", TASK, ts="2026-06-07T17:30:00Z")

    report = replay_standing_sign_receipt(
        pr_status=_status(author_agent="claude-rco-1"),
        receipt_payload=_receipt_payload(),
        events_path=_events_path(tmp_path, events),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        bridge_task_id=TASK,
    )

    assert report["ok"] is False
    assert any("DUAL-RCO incomplete" in blocker for blocker in report["blockers"])


def test_no_ci_blocks_replay(tmp_path: Path) -> None:
    report = replay_standing_sign_receipt(
        pr_status=_status(checks=[]),
        receipt_payload=_receipt_payload(),
        events_path=_events_path(tmp_path, _dual_rco_events()),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        bridge_task_id=TASK,
    )

    assert report["ok"] is False
    assert any("status checks snapshot is required" in blocker for blocker in report["blockers"])
    assert any("CI not all-required-green" in blocker for blocker in report["blockers"])


def test_a_class_changed_path_blocks_replay(tmp_path: Path) -> None:
    status = _status(changed_paths=["tools/idle_consensus_auto_merge.py"])
    payload = _receipt_payload(changed_paths=["tools/idle_consensus_auto_merge.py"])

    report = replay_standing_sign_receipt(
        pr_status=status,
        receipt_payload=payload,
        events_path=_events_path(tmp_path, _dual_rco_events()),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        bridge_task_id=TASK,
    )

    assert report["ok"] is False
    assert any("path gate failed" in blocker for blocker in report["blockers"])
    assert report["standing_consensus_sign"]["ab_class"] == "a"


def test_receipt_changed_paths_mismatch_blocks_replay(tmp_path: Path) -> None:
    payload = _receipt_payload(changed_paths=["docs/runs/other_board.md"])

    report = replay_standing_sign_receipt(
        pr_status=_status(),
        receipt_payload=payload,
        events_path=_events_path(tmp_path, _dual_rco_events()),
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        bridge_task_id=TASK,
    )

    assert report["ok"] is False
    assert any("changed_paths mismatch" in blocker for blocker in report["blockers"])


def test_cli_json_returns_exit_three_on_blocker(tmp_path: Path) -> None:
    status_path = tmp_path / "status.json"
    payload_path = tmp_path / "payload.json"
    status_path.write_text(json.dumps(_status(checks=[])), encoding="utf-8")
    payload_path.write_text(json.dumps(_receipt_payload()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--pr-status-file",
            str(status_path),
            "--receipt-payload-file",
            str(payload_path),
            "--events",
            str(_events_path(tmp_path, _dual_rco_events())),
            "--expected-head",
            HEAD,
            "--expected-base-sha",
            BASE,
            "--consensus-proposal-id",
            TASK,
            "--bridge-task-id",
            TASK,
            "--json",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 3
    report = json.loads(result.stdout)
    assert report["decision"] == "standing_sign_receipt_replay_blocked"
    assert report["authority_boundary"]["runs_gh_merge"] is False


def _status(**overrides: object) -> dict[str, object]:
    status: dict[str, object] = {
        "pr_number": 477,
        "head_sha": HEAD,
        "base_sha": BASE,
        "title": "Standing sign proof",
        "mergeable": "clean",
        "author_agent": "fable-5",
        "operator_approved": False,
        "receipt_verified": True,
        "changed_paths": [
            "tools/run_hex_readiness_proof.py",
            "docs/runs/board.md",
        ],
        "diff_text": "+ proof = True\n",
        "checks": [
            {"name": "test (3.13)", "state": "success"},
            {"name": "unified", "state": "success"},
        ],
    }
    status.update(overrides)
    return status


def _receipt_payload(**overrides: object) -> dict[str, object]:
    changed_paths = list(
        overrides.pop(
            "changed_paths",
            [
                "tools/run_hex_readiness_proof.py",
                "docs/runs/board.md",
            ],
        )
    )
    payload: dict[str, object] = {
        "artifact_version": "wd.bridge_consensus_merge_receipt.v0",
        "created_at_utc": _iso(datetime(2026, 6, 7, 18, 0, tzinfo=timezone.utc)),
        "repo": "example/repo",
        "pr_number": 477,
        "task_id": TASK,
        "head_sha": HEAD,
        "base_sha": BASE,
        "gate_decision": "auto_merge_plan_ready",
        "changed_paths": changed_paths,
        "diff_digest": sha256_digest(str("+ proof = True\n")),
        "receipt_manifest_planned": "docs/receipts/manifest.json",
        "bridge_consensus": {
            "decision": "bridge_consensus_verified",
            "head_sha": HEAD,
            "canonical_task_id": TASK,
        },
    }
    payload.update(overrides)
    return payload


def _events_path(tmp_path: Path, events: list[dict[str, object]]) -> Path:
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events),
        encoding="utf-8",
    )
    return path


def _dual_rco_events() -> list[dict[str, object]]:
    return [
        _claim("fable-5", TASK, ts="2026-06-07T17:30:00Z"),
        _event(
            "codex-lead-1",
            "decision",
            "build_consensus_pass",
            "2026-06-07T17:34:11Z",
            payload={"exact_head": HEAD},
        ),
        _event(
            "codex-tools-1",
            "decision",
            "build_consensus_pass",
            "2026-06-07T17:38:40Z",
            payload={"pr": 477, "exact_head": HEAD},
        ),
        _event(
            "claude-rco-1",
            "decision",
            "rco_pass",
            "2026-06-07T17:39:47Z",
            payload={"pr": 477, "exact_head": HEAD},
        ),
        _event(
            "claude-rco-2",
            "decision",
            "rco_pass",
            "2026-06-07T17:40:10Z",
            payload={"pr": 477, "exact_head": HEAD},
        ),
    ]


def _claim(agent: str, task_id: str, *, ts: str) -> dict[str, object]:
    return _event(agent, "claim", "active", ts, task_id=task_id)


def _event(
    agent: str,
    type_: str,
    status: str,
    ts_utc: str,
    *,
    task_id: str = TASK,
    payload: dict[str, object] | None = None,
) -> dict[str, object]:
    event: dict[str, object] = {
        "ts_utc": ts_utc,
        "agent": agent,
        "type": type_,
        "status": status,
        "task_id": task_id,
        "message": f"{status} for {HEAD}",
        "payload": payload or {},
    }
    if agent in AGENT_UUIDS:
        event["agent_uuid"] = AGENT_UUIDS[agent]
    return event


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
