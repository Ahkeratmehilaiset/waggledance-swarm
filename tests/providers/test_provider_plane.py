"""Targeted tests for the Phase 10 P3 provider plane orchestrator."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from waggledance.core.providers import (
    ClaudeCodeBuilder,
    ClaudeCodeBuilderUnavailable,
    MentorForge,
    ProviderConfig,
    ProviderDispatchResult,
    ProviderPlane,
    ProviderPlaneError,
    ProviderPlaneRegistry,
    RepairForge,
    BuilderJobQueue,
    validate_request,
)
from waggledance.core.providers.builder_job_queue import BuilderJobSubmission
from waggledance.core.builder_lane.mentor_forge import MentorPrompt
from waggledance.core.builder_lane.repair_forge import RepairContext
from waggledance.core.storage import ControlPlaneDB


VALID_REQUEST: dict = {
    "schema_version": 1,
    "request_id": "0123456789ab",
    "task_class": "code_or_repair",
    "provider_priority_list": [
        "claude_code_builder_lane",
        "anthropic_api",
        "gpt_api",
        "local_model_service",
    ],
    "intent": "Smoke-dispatch the provider plane.",
    "input_payload": {},
    "budget": {"max_calls": 1, "max_latency_ms": 30000},
    "no_runtime_mutation": True,
    "provenance": {
        "branch_name": "phase10/foundation-truth-builder-lane",
        "base_commit_hash": "8bf1869",
        "pinned_input_manifest_sha256": "sha256:unknown",
    },
}


@pytest.fixture()
def cp(tmp_path: Path) -> ControlPlaneDB:
    db = ControlPlaneDB(db_path=tmp_path / "cp.db")
    yield db
    db.close()


def test_dispatch_falls_back_to_dry_run_stub_when_no_credentials(cp: ControlPlaneDB) -> None:
    registry = ProviderPlaneRegistry(control_plane=cp)
    # Register a Claude Code lane WITHOUT credentials. The plane must
    # fall through to dry_run_stub.
    registry.register(
        ProviderConfig(
            provider_id="claude_code_builder_lane_default",
            provider_type="claude_code_builder_lane",
            enabled=True,
            has_credentials=False,
            daily_budget_calls=10.0,
        ),
        capabilities=("code_or_repair",),
        warm=True,
    )
    plane = ProviderPlane(registry=registry, section="P3-test")
    result = plane.dispatch(VALID_REQUEST, require_warm=True)
    assert isinstance(result, ProviderDispatchResult)
    assert result.provider_type_used == "dry_run_stub"
    assert result.response.trust_layer_state == "raw_quarantine"
    assert result.response.no_direct_mutation is True
    # The control plane should have one provider_jobs row.
    stats = cp.stats()
    assert stats.table_counts["provider_jobs"] == 1


def test_dispatch_with_warm_claude_code_uses_dry_run_when_cli_absent(
    cp: ControlPlaneDB, tmp_path: Path
) -> None:
    registry = ProviderPlaneRegistry(control_plane=cp)
    registry.register(
        ProviderConfig(
            provider_id="claude_code_builder_lane_default",
            provider_type="claude_code_builder_lane",
            enabled=True,
            has_credentials=True,  # configured, but CLI may still be absent
            daily_budget_calls=10.0,
        ),
        capabilities=("code_or_repair",),
        warm=True,
    )
    builder = ClaudeCodeBuilder(
        cli_path=None,  # force unavailable
        invocation_log_path=tmp_path / "builder_invocation_log.jsonl",
        allow_dry_run=True,
    )
    assert not builder.cli_available
    plane = ProviderPlane(
        registry=registry,
        claude_code_builder=builder,
        section="P3-test",
    )
    payload = deepcopy(VALID_REQUEST)
    payload["input_payload"] = {
        "isolated_worktree_path": str(tmp_path / "wt"),
        "isolated_branch_name": "phase10-builder/0123",
    }
    result = plane.dispatch(payload)
    assert result.provider_type_used == "claude_code_builder_lane"
    assert result.response.raw_payload["dry_run"] is True
    # Invocation log was written.
    log = tmp_path / "builder_invocation_log.jsonl"
    assert log.is_file()
    assert "dry_run" in log.read_text(encoding="utf-8")


def test_dispatch_validates_response_shape(cp: ControlPlaneDB) -> None:
    registry = ProviderPlaneRegistry(control_plane=cp)
    plane = ProviderPlane(registry=registry, section="P3-test")
    # No registered providers → falls through to dry_run_stub. Response
    # validation runs as the final gate.
    result = plane.dispatch(VALID_REQUEST)
    assert result.response.no_direct_mutation is True
    assert result.response.trust_layer_state == "raw_quarantine"


def test_claude_code_builder_dry_run_disabled_raises(tmp_path: Path) -> None:
    builder = ClaudeCodeBuilder(
        cli_path=None,
        invocation_log_path=tmp_path / "log.jsonl",
        allow_dry_run=False,
    )
    req = validate_request(VALID_REQUEST)
    with pytest.raises(ClaudeCodeBuilderUnavailable):
        builder.invoke(
            req,
            isolated_worktree_path=tmp_path,
            isolated_branch_name="phase10-builder/test",
            max_wall_seconds=60,
        )


def test_claude_code_builder_rejects_extreme_timeout(tmp_path: Path) -> None:
    builder = ClaudeCodeBuilder(invocation_log_path=tmp_path / "log.jsonl")
    req = validate_request(VALID_REQUEST)
    with pytest.raises(ValueError):
        builder.invoke(
            req,
            isolated_worktree_path=tmp_path,
            isolated_branch_name="phase10-builder/test",
            max_wall_seconds=999_999,
        )


def test_builder_job_queue_lifecycle(cp: ControlPlaneDB) -> None:
    queue = BuilderJobQueue(cp)
    job = queue.submit(BuilderJobSubmission(worktree_path="C:/tmp/wt", branch="phase10-builder/abc"))
    assert job.status == "queued"
    assert queue.stats() == {"queued": 1}

    running = queue.update_status(job.id, status="running", started_at="2026-04-28T00:00:00+00:00")
    assert running.status == "running"

    done = queue.update_status(
        job.id, status="completed", completed_at="2026-04-28T00:05:00+00:00"
    )
    assert done.status == "completed"
    assert queue.stats() == {"completed": 1}


def test_mentor_forge_advisory_payload_is_locked_advisory(cp: ControlPlaneDB) -> None:
    forge = MentorForge(control_plane=cp, section="P3-test")
    payload = forge.compile_advisory_payload(
        topic="path resolver coverage",
        content="Consider rerouting hybrid_retrieval through PathResolver.",
        evidence_refs=("docs/journal/2026-04-28_storage_runtime_truth.md",),
    )
    assert payload.is_advisory_only is True
    assert payload.lifecycle_status == "advisory"
    assert payload.promotion_state == "supportive"
    ir = payload.to_ir_payload()
    assert ir["lifecycle_status"] == "advisory"
    assert ir["payload"]["is_advisory_only"] is True

    rec = forge.record_advisory_request(
        MentorPrompt(topic="path resolver coverage", why_relevant="grounded by audit")
    )
    assert rec is not None
    assert rec.request_kind == "mentor_note"


def test_repair_forge_validates_task_kind(cp: ControlPlaneDB) -> None:
    forge = RepairForge(control_plane=cp, section="P3-test")
    ctx = RepairContext(
        affected_file="core/faiss_store.py",
        defect_kind="hard_coded_path",
        failing_test_paths=(),
        rationale="route through PathResolver instead",
    )
    with pytest.raises(ValueError):
        forge.record_repair_request(ctx, task_kind="not_a_repair_kind")
    rec = forge.record_repair_request(ctx, task_kind="repair_adapter")
    assert rec is not None
    assert rec.request_kind == "repair:repair_adapter"


def test_dispatch_records_failure_when_adapter_raises(cp: ControlPlaneDB) -> None:
    """RULE 14 fail-loud: an adapter exception bubbles up but the
    control-plane row is updated to status='failed' before re-raise."""

    class _ExplodingAdapter:
        PROVIDER_TYPE = "dry_run_stub"

        def dispatch(self, request):  # noqa: D401
            raise RuntimeError("kaboom")

    registry = ProviderPlaneRegistry(control_plane=cp)
    plane = ProviderPlane(
        registry=registry,
        adapters={"dry_run_stub": _ExplodingAdapter()},
        section="P3-test",
    )
    with pytest.raises(RuntimeError, match="kaboom"):
        plane.dispatch(VALID_REQUEST)
    # control plane row in 'failed' state
    rows = cp._conn.execute(  # noqa: SLF001 — test access
        "SELECT status, error FROM provider_jobs"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert "kaboom" in str(rows[0]["error"])
