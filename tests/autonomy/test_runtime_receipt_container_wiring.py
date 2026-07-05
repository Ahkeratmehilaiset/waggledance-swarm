# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import asyncio
from pathlib import Path

from waggledance.adapters.config.settings_loader import WaggleSettings
from waggledance.bootstrap.container import Container
from waggledance.core.magma.chat_served_accounting import coverage_from_ledger
from waggledance.core.magma.chat_served_emitter import new_served_id
from waggledance.core.magma.runtime_summary_receipt import (
    build_handle_query_runtime_summary,
)


def _settings_with_runtime_receipts(config: dict | None = None) -> WaggleSettings:
    extras = {"runtime_receipts": dict(config or {})} if config is not None else {}
    return WaggleSettings(profile="TEST", _extras=extras)


def _settings_with_chat_served_receipts(config: dict | None = None) -> WaggleSettings:
    extras = {"chat_served_receipts": dict(config or {})} if config is not None else {}
    return WaggleSettings(profile="TEST", _extras=extras)


def _summary_payload() -> dict:
    return build_handle_query_runtime_summary(
        query="private runtime query DO_NOT_LEAK",
        context={"operator_note": "context secret DO_NOT_LEAK"},
        profile="TEST",
        intent="detect",
        quality_path="gold",
        capability_id="detect.fixture",
        action_id="action:runtime-receipt-container-test:001",
        approved=True,
        executed=True,
        needs_approval=False,
        decision_reason="private decision DO_NOT_LEAK",
        elapsed_ms=12.34,
        snapshot_id="snapshot:runtime-receipt-container-test:001",
        case_id="case:runtime-receipt-container-test:001",
        verifier_passed=True,
        verifier_confidence=0.91,
        result_keys=["success", "value"],
        solver_call_trace=[
            {
                "stage": "solver_call",
                "status": "selected",
                "intent": "detect",
                "capability_id": "detect.fixture",
                "selected_index": 0,
                "quality_path": "gold",
                "execution_boundary": "safe_action_bus",
            }
        ],
    )


def test_runtime_receipt_sink_is_default_off() -> None:
    container = Container(settings=_settings_with_runtime_receipts(), stub=True)

    assert container.runtime_receipt_sink is None
    runtime = container.autonomy_service._runtime
    assert runtime.runtime_receipt_sink is None
    assert runtime.runtime_receipt_metrics_snapshot()["sink_configured"] is False


def test_chat_served_emitter_is_default_off() -> None:
    container = Container(settings=_settings_with_chat_served_receipts(), stub=True)

    assert container.chat_served_emitter is None


def test_chat_served_emitter_opt_in_writes_eligible_chain(tmp_path: Path) -> None:
    receipt_root = tmp_path / "chat-served"
    container = Container(
        settings=_settings_with_chat_served_receipts(
            {
                "enabled": True,
                "out_dir": str(receipt_root),
            }
        ),
        stub=True,
    )
    emitter = container.chat_served_emitter
    assert emitter is not None

    sid = new_served_id()
    assert emitter.record_pending(sid, source="solver", route_type="solver",
                                  language="fi", profile="HOME", agent_id=None) is True

    async def run() -> None:
        emitter.schedule_receipt(sid, query="private q DO_NOT_LEAK",
                                 response="private a DO_NOT_LEAK",
                                 source="solver", route_type="solver",
                                 confidence=0.95, latency_ms=1.0,
                                 cached=False, round_table=False, agent_id=None,
                                 language="fi", profile="HOME")
        await asyncio.gather(*list(emitter._tasks))

    asyncio.run(run())
    report = coverage_from_ledger(
        str(receipt_root / "ledger.jsonl"),
        pending_failure_ledger_path=emitter.pending_failure_ledger_path,
    )
    assert report.eligible is True
    assert report.served == 1
    assert report.receipts == 1
    emitted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(receipt_root.rglob("*.json"))
    )
    assert "DO_NOT_LEAK" not in emitted


def test_runtime_receipt_sink_treats_string_false_as_disabled() -> None:
    container = Container(
        settings=_settings_with_runtime_receipts({"enabled": "false"}),
        stub=True,
    )

    assert container.runtime_receipt_sink is None


def test_runtime_receipt_sink_writes_verified_bundle_and_returns_path_free_result(
    tmp_path: Path,
) -> None:
    receipt_root = tmp_path / "runtime-receipts"
    container = Container(
        settings=_settings_with_runtime_receipts(
            {
                "enabled": True,
                "out_dir": str(receipt_root),
                "evaluation_version": "magma.evaluation_result.v0",
            }
        ),
        stub=True,
    )

    sink = container.runtime_receipt_sink
    assert callable(sink)
    assert container.autonomy_service._runtime.runtime_receipt_sink is sink

    result = sink(_summary_payload())

    assert result == {
        "receipt_count": 1,
        "verifier_report": {"ok": True, "receipt_count": 1, "errors": []},
        "sink": "configured_local_runtime_summary_receipts",
        "paths_returned": False,
        "payloads_returned": False,
        "default_runtime_receipt_emission_changed": False,
        "runtime_authority_changed": False,
    }
    assert "out_dir" not in result
    assert "manifest" not in result
    receipt_dirs = list(receipt_root.iterdir())
    assert len(receipt_dirs) == 1

    emitted_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(receipt_root.rglob("*.json"))
    )
    assert "solver_call_trace" in emitted_text
    assert "solver_call_trace_digest" in emitted_text
    assert "private runtime query" not in emitted_text
    assert "context secret" not in emitted_text
    assert "private decision" not in emitted_text
    assert "DO_NOT_LEAK" not in emitted_text


def test_runtime_receipt_sink_returns_path_free_verifier_error_summaries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import tools.verify_magma_receipt as verify_magma_receipt

    raw_error = (
        "manifest failed at C:\\Users\\janik\\private\\receipt.json "
        "and /tmp/private/receipt.json with query DO_NOT_LEAK"
    )

    def fake_verify_manifest(_manifest_path: Path) -> dict:
        return {"ok": False, "receipt_count": 1, "errors": [raw_error]}

    monkeypatch.setattr(verify_magma_receipt, "verify_manifest", fake_verify_manifest)
    container = Container(
        settings=_settings_with_runtime_receipts(
            {
                "enabled": True,
                "out_dir": str(tmp_path / "runtime-receipts"),
                "evaluation_version": "magma.evaluation_result.v0",
            }
        ),
        stub=True,
    )

    result = container.runtime_receipt_sink(_summary_payload())

    assert result["verifier_report"]["ok"] is False
    assert result["verifier_report"]["errors"][0].startswith("verifier_error:")
    public_text = "\n".join(result["verifier_report"]["errors"])
    assert "C:\\Users" not in public_text
    assert "/tmp/private" not in public_text
    assert "query" not in public_text
    assert "DO_NOT_LEAK" not in public_text
