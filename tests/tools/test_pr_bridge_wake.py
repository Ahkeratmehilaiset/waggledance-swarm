# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.pr_bridge_wake import (
    PrBridgeWakeError,
    build_pr_review_wake_event,
    emit_bridge_event,
    resolve_pr_head,
)


HEAD = "1234567890abcdef1234567890abcdef12345678"
OTHER_HEAD = "abcdef1234567890abcdef1234567890abcdef12"


def _runner(payload: dict, calls: list[list[str]] | None = None):
    def runner(command: list[str]) -> SimpleNamespace:
        if calls is not None:
            calls.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))

    return runner


def _payload(**overrides: object) -> dict:
    payload = {
        "number": 1505,
        "headRefOid": HEAD,
        "headRefName": "codex-lead-1/phase2e-chatserved-claim-window-evidence-20260704",
        "url": "https://github.example/pr/1505",
    }
    payload.update(overrides)
    return payload


def test_resolve_pr_head_uses_structured_github_head_ref_oid() -> None:
    calls: list[list[str]] = []

    snapshot = resolve_pr_head(
        pr_number=1505,
        repo="Ahkeratmehilaiset/waggledance-swarm",
        runner=_runner(_payload(), calls),
    )

    assert calls == [
        [
            "gh",
            "pr",
            "view",
            "1505",
            "--json",
            "number,headRefName,headRefOid,url",
            "--repo",
            "Ahkeratmehilaiset/waggledance-swarm",
        ]
    ]
    assert snapshot["head"] == HEAD
    assert snapshot["head_ref"].startswith("codex-lead-1/")


def test_build_event_binds_message_status_and_payload_to_authoritative_head() -> None:
    event = build_pr_review_wake_event(
        pr_number=1505,
        agent="codex-lead-1",
        task_id="codex-lead-1/phase2e",
        to="claude-rco-1,codex-tools-1",
        status="review_pr1505",
        body="Focus evidence independence.",
        declared_head=HEAD,
        runner=_runner(_payload()),
    )

    assert event["type"] == "wake_request"
    assert event["status"] == "review_pr1505"
    assert HEAD in event["message"]
    assert "Focus evidence independence." in event["message"]
    assert event["payload"]["head"] == HEAD
    assert event["payload"]["head_source"] == "gh_pr_view.headRefOid"
    assert event["payload"]["declared_head_checked"] is True


def test_declared_head_must_match_github_head() -> None:
    with pytest.raises(PrBridgeWakeError) as excinfo:
        build_pr_review_wake_event(
            pr_number=1505,
            agent="codex-lead-1",
            task_id="codex-lead-1/phase2e",
            to="claude-rco-1",
            declared_head=OTHER_HEAD,
            runner=_runner(_payload()),
        )

    assert excinfo.value.report["decision"] == "declared_head_mismatch"


def test_short_or_malformed_heads_are_refused() -> None:
    with pytest.raises(PrBridgeWakeError) as excinfo:
        build_pr_review_wake_event(
            pr_number=1505,
            agent="codex-lead-1",
            task_id="codex-lead-1/phase2e",
            to="claude-rco-1",
            declared_head="12345678",
            runner=_runner(_payload()),
        )
    assert excinfo.value.report["decision"] == "invalid_declared_head"

    with pytest.raises(PrBridgeWakeError) as excinfo:
        resolve_pr_head(
            pr_number=1505,
            runner=_runner(_payload(headRefOid="12345678")),
        )
    assert excinfo.value.report["decision"] == "invalid_head_sha"


def test_emit_bridge_event_invokes_writer_with_authoritative_payload(tmp_path: Path) -> None:
    writer = tmp_path / "bin" / "Write-AgentEvent.ps1"
    writer.parent.mkdir()
    writer.write_text("# test writer\n", encoding="utf-8")
    event = build_pr_review_wake_event(
        pr_number=1505,
        agent="codex-lead-1",
        task_id="codex-lead-1/phase2e",
        to="claude-rco-1",
        runner=_runner(_payload()),
    )
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    **event,
                    "_bridge_delivery": {
                        "schema": "waggledance.bridge.delivery-receipt.v1",
                        "accepted": True,
                        "delivery_status": "canonical",
                        "canonical_durable": True,
                        "retained_wal_path": None,
                        "retained_wal_sha256": None,
                    },
                }
            ),
        )

    report = emit_bridge_event(event, bridge_root=tmp_path, run_id="run-1", runner=runner)

    assert report == {
        "returncode": 0,
        "delivery_status": "canonical",
        "canonical_durable": True,
        "retained_wal_path": None,
        "retained_wal_sha256": None,
    }
    command = calls[0]
    payload = json.loads(command[command.index("-PayloadJson") + 1])
    assert command[command.index("-Type") + 1] == "wake_request"
    assert command[command.index("-RunId") + 1] == "run-1"
    assert "-ReceiptJson" in command
    assert command[command.index("-WarningAction") + 1] == "SilentlyContinue"
    assert payload["head"] == HEAD


def test_emit_bridge_event_reports_queued_receipt_without_claiming_canonical(
    tmp_path: Path,
) -> None:
    writer = tmp_path / "bin" / "Write-AgentEvent.ps1"
    writer.parent.mkdir()
    writer.write_text("# test writer\n", encoding="utf-8")
    event = build_pr_review_wake_event(
        pr_number=1505,
        agent="codex-lead-1",
        task_id="codex-lead-1/phase2e",
        to="claude-rco-1",
        runner=_runner(_payload()),
    )
    wal_path = tmp_path / "spool" / "accepted-v1" / "ready" / (
        "bridge-wal-v1-" + "1" * 32 + ".jsonl"
    )

    def runner(command: list[str]) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(
                {
                    **event,
                    "_bridge_delivery": {
                        "schema": "waggledance.bridge.delivery-receipt.v1",
                        "accepted": True,
                        "delivery_status": "queued",
                        "canonical_durable": False,
                        "retained_wal_path": str(wal_path),
                        "retained_wal_sha256": "a" * 64,
                    },
                }
            ),
        )

    report = emit_bridge_event(event, bridge_root=tmp_path, runner=runner)

    assert report == {
        "returncode": 0,
        "delivery_status": "queued",
        "canonical_durable": False,
        "retained_wal_path": str(wal_path),
        "retained_wal_sha256": "a" * 64,
    }


@pytest.mark.parametrize(
    "delivery",
    [
        {
            "schema": "waggledance.bridge.delivery-receipt.v1",
            "accepted": True,
            "delivery_status": "canonical",
            "canonical_durable": False,
            "retained_wal_path": None,
            "retained_wal_sha256": None,
        },
        {
            "schema": "waggledance.bridge.delivery-receipt.v1",
            "accepted": True,
            "delivery_status": "queued",
            "canonical_durable": False,
            "retained_wal_path": None,
            "retained_wal_sha256": None,
        },
    ],
)
def test_emit_bridge_event_rejects_inconsistent_delivery_receipt(
    tmp_path: Path,
    delivery: dict[str, object],
) -> None:
    writer = tmp_path / "bin" / "Write-AgentEvent.ps1"
    writer.parent.mkdir()
    writer.write_text("# test writer\n", encoding="utf-8")
    event = build_pr_review_wake_event(
        pr_number=1505,
        agent="codex-lead-1",
        task_id="codex-lead-1/phase2e",
        to="claude-rco-1",
        runner=_runner(_payload()),
    )

    def runner(command: list[str]) -> SimpleNamespace:
        del command
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({**event, "_bridge_delivery": delivery}),
        )

    with pytest.raises(PrBridgeWakeError) as excinfo:
        emit_bridge_event(event, bridge_root=tmp_path, runner=runner)

    assert excinfo.value.report["decision"] == "invalid_writer_receipt"
