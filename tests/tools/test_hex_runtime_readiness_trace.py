# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from tools.run_hex_runtime_readiness_trace import (
    OUTPUT_FILENAME,
    REPORT_VERSION,
    TRACE_STATUS,
    build_hex_runtime_readiness_trace,
    evaluate_runtime_readiness_trace_binding,
)

SCRIPT = Path("tools/run_hex_runtime_readiness_trace.py")

_GOOD_DIGEST = "sha256:" + "a" * 64
_OTHER_DIGEST = "sha256:" + "b" * 64


def _matched_binding_kwargs() -> dict:
    """A fully-consistent binding input set: every check evaluates True."""
    return dict(
        canonical_execution_request_digest=_GOOD_DIGEST,
        canonical_plan_id="subdiv_abc1234567",
        solver_plan_id="subdiv_abc1234567",
        routing_target_parent_cell_id="root",
        solver_parent_cell_id="root",
        routing_delivered=True,
        routing_transport_applied=False,
        readiness_ok=True,
        readiness_execution_request_digest=_GOOD_DIGEST,
        readiness_admission_request_digest=_GOOD_DIGEST,
        readiness_authority_false_everywhere=True,
        readiness_production_activation_ready=False,
        readiness_runtime_mutation_authority=False,
        readiness_scheduler_enqueue_allowed=False,
        readiness_forbidden_true_flag_paths=[],
        rollup_ok=True,
        rollup_pipeline_request_digest=_GOOD_DIGEST,
        rollup_admission_request_digest=_GOOD_DIGEST,
        rollup_production_activation_ready=False,
        rollup_runtime_mutation_authority=False,
        rollup_forbidden_true_flag_paths=[],
    )


def test_trace_writes_report_and_binds_single_digest(tmp_path):
    out_dir = tmp_path / "trace"

    report = build_hex_runtime_readiness_trace(out_dir=out_dir)

    assert report["ok"] is True
    assert report["blockers"] == []
    assert report["report_version"] == REPORT_VERSION
    assert report["trace_status"] == TRACE_STATUS

    # every binding check holds
    assert all(report["trace_checks"].values())
    assert len(report["trace_checks"]) == 17

    # the ONE shared digest threads through every linked stage
    shared = report["shared_execution_request_digest"]
    assert shared.startswith("sha256:") and len(shared) == 71
    links = report["trace_links"]
    assert links["executor_admission_dry_run"]["execution_request_digest"] == shared
    assert (
        links["executor_admission_dry_run"]["runtime_execution_request_digest"]
        == shared
    )
    assert links["observability_rollup"]["pipeline_execution_request_digest"] == shared
    assert (
        links["observability_rollup"]["executor_admission_execution_request_digest"]
        == shared
    )
    # solver verdict reproduces the plan that drives the request; routing targets it
    assert links["solver_verdict"]["plan_id"] == report["plan_id"]
    assert links["routing_intent"]["target_parent_cell_id"] == (
        links["solver_verdict"]["parent_cell_id"]
    )
    assert links["routing_intent"]["delivered"] is True
    assert links["routing_intent"]["transport_applied"] is False

    # dormancy held across the trace
    assert report["production_activation_ready"] is False
    assert report["runtime_mutation_authority"] is False
    assert all(v is False for v in report["authority_boundary"].values())

    # file round-trips exactly
    proof_path = out_dir / OUTPUT_FILENAME
    assert proof_path.exists()
    assert json.loads(proof_path.read_text(encoding="utf-8")) == report

    # the LINKED evidence is referenced by digest, not by filesystem path:
    # no sub-report path leaks into trace_links.
    links_encoded = json.dumps(links)
    assert "C:\\Python" not in links_encoded
    assert str(tmp_path) not in links_encoded


def test_binding_all_matched_is_true():
    checks = evaluate_runtime_readiness_trace_binding(**_matched_binding_kwargs())
    assert all(checks.values())


def test_binding_fails_closed_when_one_link_digest_diverges():
    # Perturb ONLY the roll-up's pipeline digest. "Each other link valid" must
    # NOT be enough: the chain-binding check must fail closed. (Regression guard
    # for the #1421 readiness-composition bug: links must form ONE chain.)
    kwargs = _matched_binding_kwargs()
    kwargs["rollup_pipeline_request_digest"] = _OTHER_DIGEST
    checks = evaluate_runtime_readiness_trace_binding(**kwargs)

    assert checks["rollup_pipeline_digest_bound"] is False
    assert checks["single_shared_execution_request_digest"] is False
    # the per-stage proofs still "pass" individually -- which is exactly why a
    # naive per-link check would have wrongly accepted this divergent chain.
    assert checks["readiness_proof_ok"] is True
    assert checks["rollup_proof_ok"] is True
    assert checks["readiness_request_digest_bound"] is True


def test_binding_fails_when_solver_verdict_plan_id_diverges():
    kwargs = _matched_binding_kwargs()
    kwargs["solver_plan_id"] = "subdiv_999deadbee"
    checks = evaluate_runtime_readiness_trace_binding(**kwargs)
    assert checks["solver_verdict_reproduces_canonical_plan_id"] is False


def test_binding_fails_when_routing_intent_targets_other_parent():
    kwargs = _matched_binding_kwargs()
    kwargs["routing_target_parent_cell_id"] = "some_other_cell"
    checks = evaluate_runtime_readiness_trace_binding(**kwargs)
    assert checks["routing_intent_targets_solver_parent"] is False


def test_binding_fails_on_malformed_canonical_digest():
    kwargs = _matched_binding_kwargs()
    kwargs["canonical_execution_request_digest"] = "not-a-digest"
    checks = evaluate_runtime_readiness_trace_binding(**kwargs)
    assert checks["canonical_execution_request_digest_well_formed"] is False
    assert checks["single_shared_execution_request_digest"] is False


def test_binding_rejects_fake_non_hex_digest_shared_by_every_link():
    # Regression (lead build-review #1435): 'sha256:' + 64 NON-hex chars has the
    # right prefix and length but is not a real digest. Even if every link shares
    # that fake value, the well-formedness AND single-shared checks must fail --
    # a prefix+length check alone would fail open and forge the digest proof.
    fake = "sha256:" + "z" * 64
    kwargs = _matched_binding_kwargs()
    for key in (
        "canonical_execution_request_digest",
        "readiness_execution_request_digest",
        "readiness_admission_request_digest",
        "rollup_pipeline_request_digest",
        "rollup_admission_request_digest",
    ):
        kwargs[key] = fake
    checks = evaluate_runtime_readiness_trace_binding(**kwargs)
    assert checks["canonical_execution_request_digest_well_formed"] is False
    assert checks["single_shared_execution_request_digest"] is False


def test_trace_refuses_existing_out_dir(tmp_path):
    out_dir = tmp_path / "trace"
    out_dir.mkdir()

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--out-dir",
            str(out_dir),
            "--json",
        ],
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 1
    assert "out_dir must not exist" in proc.stderr


def test_trace_cli_json(tmp_path):
    out_dir = tmp_path / "trace"
    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--out-dir",
            str(out_dir),
            "--now",
            "2026-06-30T00:00:00Z",
            "--json",
        ],
        check=False,
        cwd=".",
        text=True,
        capture_output=True,
    )

    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["ok"] is True
    assert report["generated_at_utc"] == "2026-06-30T00:00:00Z"
    assert report["report_version"] == REPORT_VERSION
