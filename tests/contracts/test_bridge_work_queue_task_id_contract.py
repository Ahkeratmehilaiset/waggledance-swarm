# SPDX-License-Identifier: BUSL-1.1
"""Cross-surface task_id contract for bridge events and work_queue claims."""
from __future__ import annotations

from pathlib import Path

from waggledance.core.bridge_event_schema import validate_event
from waggledance.core.work_queue import claim_task, release_task


def _bridge_event(task_id: str) -> dict[str, object]:
    return {
        "ts_utc": "2026-06-13T04:20:00Z",
        "agent": "codex-tools-1",
        "type": "claim",
        "task_id": task_id,
        "status": "active",
        "severity": "",
        "to": "",
        "message": "Claim bridge-compatible task.",
        "paths": [],
        "write_scope": [],
        "run_id": "codex-tools-1-20260601T113511Z",
        "pid": 12345,
        "cwd": "C:\\Python\\project2-master",
        "payload": {},
    }


def test_bridge_namespaced_task_id_is_work_queue_claimable(
    tmp_path: Path,
) -> None:
    task_id = "codex-tools-1/magma-share-admission-status-bridge-template-20260613"
    bridge = tmp_path / ".agent-bridge"

    event = validate_event(_bridge_event(task_id))
    claim = claim_task(
        agent="codex-tools-1",
        task_id=event.task_id,
        summary="same task id as bridge event",
        bridge_root=bridge,
    )
    release = release_task(
        agent="codex-tools-1",
        task_id=event.task_id,
        release_status="done",
        bridge_root=bridge,
    )

    assert claim.task_id == task_id
    assert release.task_id == task_id
    assert not (bridge / "work_queue" / "claims" / "codex-tools-1").exists()
