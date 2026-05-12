# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import importlib
import json
from pathlib import Path


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n",
        encoding="utf-8",
    )


def test_build_plan_summarizes_claims_consensus_and_unresolved_threads(tmp_path: Path) -> None:
    mod = importlib.import_module("tools.build_agent_flight_plan")
    events_path = tmp_path / "events.jsonl"
    _write_jsonl(
        events_path,
        [
            {
                "ts_utc": "2026-05-12T00:00:00Z",
                "agent": "claude",
                "to": "codex",
                "type": "finding",
                "status": "open",
                "task_id": "thread-answered",
                "message": "Please review PR #1",
            },
            {
                "ts_utc": "2026-05-12T00:01:00Z",
                "agent": "codex",
                "to": "claude",
                "type": "message",
                "status": "answered",
                "task_id": "thread-answered",
                "message": "Reviewed.",
            },
            {
                "ts_utc": "2026-05-12T00:02:00Z",
                "agent": "codex",
                "to": "claude",
                "type": "claim",
                "status": "active",
                "task_id": "thread-claimed",
                "message": "Claiming write scope.",
            },
            {
                "ts_utc": "2026-05-12T00:03:00Z",
                "agent": "claude",
                "to": "codex",
                "type": "message",
                "status": "open",
                "task_id": "thread-unresolved",
                "message": "Need Codex action. timeout latency regression",
            },
            {
                "ts_utc": "2026-05-12T00:04:00Z",
                "agent": "codex",
                "to": "claude",
                "type": "status",
                "status": "",
                "task_id": "thread-unresolved",
                "message": "Codex saw this.",
            },
            {
                "ts_utc": "2026-05-12T00:05:00Z",
                "agent": "claude",
                "to": "codex",
                "type": "decision",
                "status": "consensus_accepted",
                "task_id": "thread-consensus",
                "message": "Consensus accepted.",
            },
        ],
    )

    plan = mod.build_plan(
        events_path=events_path,
        objective="test release",
        target_agent="codex",
        now_utc="2026-05-12T00:10:00Z",
    )

    assert plan["schema_version"] == "agent-flight-plan-v1"
    assert plan["metrics"]["task_threads_total"] == 4
    assert plan["metrics"]["threads_with_claim"] == 1
    assert plan["metrics"]["multi_agent_threads_without_claim"] == 2
    assert [item["task_id"] for item in plan["active_claims"]] == ["thread-claimed"]
    assert [item["task_id"] for item in plan["unresolved_threads"]] == ["thread-unresolved"]
    assert [item["task_id"] for item in plan["consensus_topics"]] == ["thread-consensus"]
    assert "Read .agent-bridge/BOOTSTRAP.md" in plan["bootstrap_prompt"]
    assert len(plan["bootstrap_prompt"].splitlines()) <= 40
    assert any(action["task_id"] == "thread-unresolved" for action in plan["next_actions"])


def test_cli_writes_flight_plan(tmp_path: Path) -> None:
    mod = importlib.import_module("tools.build_agent_flight_plan")
    events_path = tmp_path / "events.jsonl"
    output_path = tmp_path / "plan.json"
    _write_jsonl(
        events_path,
        [
            {
                "ts_utc": "2026-05-12T00:00:00Z",
                "agent": "codex",
                "to": "claude",
                "type": "decision",
                "status": "consensus_proposal",
                "task_id": "thread",
                "message": "Proposal",
            }
        ],
    )

    rc = mod.main(
        [
            "--events",
            str(events_path),
            "--output",
            str(output_path),
            "--objective",
            "cli objective",
            "--agent",
            "codex",
        ]
    )

    assert rc == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["objective"] == "cli objective"
    assert payload["formal_statuses"]["consensus_proposal"] == "consensus_proposal"


def test_status_contract_and_schema_files_exist() -> None:
    root = Path(__file__).resolve().parents[2]
    schema = json.loads(
        (root / ".orchestrator" / "agent_flight_plan.schema.json").read_text(encoding="utf-8")
    )
    statuses = json.loads(
        (root / "docs" / "eig2" / "contracts" / "agent_flight_plan_statuses.json").read_text(encoding="utf-8")
    )

    assert schema["properties"]["schema_version"]["const"] == "agent-flight-plan-v1"
    assert set(statuses["statuses"]) == {
        "rco_requested",
        "rco_done",
        "consensus_proposal",
        "consensus_accepted",
        "claim_required",
        "missing_claim",
    }
