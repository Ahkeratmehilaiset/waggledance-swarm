# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json

from tools.idle_consensus_artifact import (
    COUNTERFACTUAL_EVAL_BINDING_VERSION,
    build_idle_consensus_candidate_diff_replay_admission,
)
from waggledance.core.magma.canonical import sha256_digest


def _replay_seed() -> dict:
    return {
        "seed_version": "idle_consensus_replay_seed.v0",
        "purpose": "future_counterfactual_candidate_diff_replay",
        "dry_run_only": True,
        "candidate_diff_included": False,
        "external_effect": False,
        "writes_applied": False,
        "would_create_task": False,
        "would_create_branch": False,
        "would_create_pr": False,
        "would_merge": False,
        "consensus_artifact": {
            "artifact_version": "idle_consensus_operator_review.v1",
            "artifact_id": "contract-replay-seed",
            "digest": "sha256:" + ("1" * 64),
        },
        "convergence_digest": "sha256:" + ("2" * 64),
        "transcript_digest": "sha256:" + ("3" * 64),
        "policy_ref": "policy:idle_consensus_artifact:v1",
        "charter_ref": "charter:idle_autonomy:v1",
    }


def _candidate_diff() -> tuple[list[str], str]:
    changed_paths = ["docs/architecture/consensus_artifacts/contract.md"]
    diff_text = """diff --git a/docs/architecture/consensus_artifacts/contract.md b/docs/architecture/consensus_artifacts/contract.md
new file mode 100644
--- /dev/null
+++ b/docs/architecture/consensus_artifacts/contract.md
@@ -0,0 +1,2 @@
+# Contract
+Receipt binding must be digest-only and path-free.
"""
    return changed_paths, diff_text


def _diff_digest(changed_paths: list[str], diff_text: str) -> str:
    return sha256_digest({"changed_paths": changed_paths, "diff_text": diff_text})


def _receipt(*, replay_seed_digest: str, candidate_diff_digest: str) -> dict:
    return {
        "schema_version": "magma.counterfactual_promotion_summary.v0",
        "status": "computed",
        "a3_label": "RUNTIME_MEASURED",
        "sample_count": 20,
        "divergence_count": 1,
        "same_sample_set": True,
        "deterministic": True,
        "no_delta": False,
        "delta_digest": "sha256:" + ("4" * 64),
        "replay_binding": {
            "schema_version": COUNTERFACTUAL_EVAL_BINDING_VERSION,
            "replay_seed_digest": replay_seed_digest,
            "candidate_diff_digest": candidate_diff_digest,
        },
    }


def test_counterfactual_receipt_must_bind_replay_seed_and_candidate_diff() -> None:
    seed = _replay_seed()
    changed_paths, diff_text = _candidate_diff()
    receipt = _receipt(
        replay_seed_digest=sha256_digest(seed),
        candidate_diff_digest=_diff_digest(changed_paths, diff_text),
    )

    admission = build_idle_consensus_candidate_diff_replay_admission(
        replay_seed=seed,
        changed_paths=changed_paths,
        candidate_diff_text=diff_text,
        counterfactual_eval_receipt=receipt,
    )

    assert admission["counterfactual_eval"]["observability_satisfies_replay_gate"]
    assert admission["counterfactual_eval"]["binding"]["matches"] is True
    assert admission["counterfactual_eval"]["satisfies_replay_gate"] is True
    assert admission["draft_pr_gate_blockers"] == [
        "operator_review_gate_required",
    ]


def test_mismatched_counterfactual_receipt_binding_blocks_replay_gate() -> None:
    seed = _replay_seed()
    changed_paths, diff_text = _candidate_diff()
    wrong_digest = "sha256:" + ("0" * 64)
    receipt = _receipt(
        replay_seed_digest=sha256_digest(seed),
        candidate_diff_digest=wrong_digest,
    )

    admission = build_idle_consensus_candidate_diff_replay_admission(
        replay_seed=seed,
        changed_paths=changed_paths,
        candidate_diff_text=diff_text,
        counterfactual_eval_receipt=receipt,
    )
    serialized = json.dumps(admission, sort_keys=True)

    assert admission["counterfactual_eval"]["observability_satisfies_replay_gate"]
    assert admission["counterfactual_eval"]["binding"]["provided"] is True
    assert admission["counterfactual_eval"]["binding"][
        "candidate_diff_digest_matches"
    ] is False
    assert admission["counterfactual_eval"]["binding"]["matches"] is False
    assert admission["counterfactual_eval"]["satisfies_replay_gate"] is False
    assert admission["draft_pr_gate_blockers"] == [
        "counterfactual_eval_receipt_binding_mismatch",
        "operator_review_gate_required",
    ]
    assert admission["next_required_gates"][0] == (
        "counterfactual_eval_receipt_binding"
    )
    assert "diff --git" not in serialized
    assert "Receipt binding must be digest-only" not in serialized
    assert wrong_digest not in serialized
