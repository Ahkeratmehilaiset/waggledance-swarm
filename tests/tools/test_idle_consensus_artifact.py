# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools.idle_consensus_artifact import (
    ArtifactError,
    CANDIDATE_DIFF_REPLAY_ADMISSION_VERSION,
    COUNTERFACTUAL_EVAL_ADMISSION_SUMMARY_VERSION,
    REPLAY_SEED_REQUIRED_FALSE_KEYS,
    build_idle_consensus_candidate_diff_replay_admission,
    build_idle_consensus_replay_seed,
    write_idle_consensus_artifact,
)
from tools.verify_magma_receipt import verify_manifest
from waggledance.core.magma.canonical import sha256_digest

NOW = datetime(2026, 5, 18, 9, 0, tzinfo=timezone.utc)
PROHIBITED_HINTS = (
    "create pr",
    "open pr",
    "git checkout",
    "git switch",
    "new branch",
    "scaffold code",
    "work_queue",
)


def _base(proposal_id: str, event_type: str, round_number: int) -> dict:
    return {
        "protocol_version": "idle-protocol.v1",
        "event_type": event_type,
        "proposal_id": proposal_id,
        "round_number": round_number,
        "proposes_substrate_change": True,
        "problem_statement": "Idle consensus needs an operator review artifact before work begins.",
        "tradeoff_axis": "Evidence handoff versus automatic implementation conversion.",
        "simulation_evidence": {
            "kind": "scenario_simulation",
            "summary": "A completed transcript can be written as review evidence only.",
        },
        "charter_alignment": {
            "compatible": True,
            "reasoning": "The artifact preserves operator approval and has no execution path.",
        },
    }


def _proposal() -> dict:
    payload = _base("idle-artifact-001", "idle_proposal", 1)
    payload["proposal"] = (
        "Write an operator review artifact for completed idle consensus."
    )
    return payload


def _counter(proposal_id: str = "idle-artifact-002", round_number: int = 2) -> dict:
    payload = _base(proposal_id, "idle_counter_proposal", round_number)
    payload["responds_to"] = (
        "idle-artifact-001"
        if round_number == 2
        else f"idle-artifact-{round_number - 1:03d}"
    )
    payload["alternative_proposal"] = (
        "Use a read only artifact and require the operator to decide separately."
    )
    payload["reasoning_points"] = [
        "If artifact creation makes a task, the operator gate has been bypassed.",
        "When the artifact is evidence only, review remains independent.",
        "If privacy markers are present, the artifact must not be written.",
    ]
    return payload


def _adversarial() -> dict:
    payload = _base("idle-artifact-003", "idle_adversarial_review", 3)
    payload["responds_to"] = "idle-artifact-002"
    payload["proposes_substrate_change"] = False
    payload["counterexamples"] = [
        "If the artifact includes branch commands, it pressures implementation.",
        "When transcript data contains private markers, writing the artifact leaks data.",
    ]
    return payload


def _consensus(proposal_id: str) -> dict:
    payload = _base(proposal_id, "idle_consensus_reached", 5)
    payload["proposes_substrate_change"] = False
    payload["consensus_target_proposal_id"] = "idle-artifact-002"
    payload["operator_gate_required"] = True
    payload["auto_execute"] = False
    return payload


def _charter_violation() -> dict:
    payload = _base("idle-artifact-004", "idle_charter_violation", 4)
    payload["proposes_substrate_change"] = False
    payload["violating_proposal_id"] = "idle-artifact-002"
    payload["violation_reason"] = (
        "The reviewed idea would turn consensus into automatic work."
    )
    payload["terminate_protocol"] = True
    payload["operator_escalation_required"] = True
    payload["charter_alignment"] = {
        "compatible": False,
        "reasoning": "Automatic work conversion bypasses operator approval.",
    }
    return payload


def _soft_events() -> list[dict]:
    return [
        _proposal(),
        _counter(),
        _adversarial(),
        _consensus("idle-artifact-005a"),
        _consensus("idle-artifact-005b"),
    ]


def _hard_events() -> list[dict]:
    events = [_proposal(), _counter(), _adversarial()]
    for round_number in range(4, 11):
        events.append(_counter(f"idle-artifact-{round_number:03d}", round_number))
    return events


def _bridge_event(payload: dict) -> dict:
    return {
        "ts_utc": "2026-05-18T09:00:00Z",
        "agent": "codex",
        "type": "message",
        "task_id": "idle-artifact-test",
        "status": payload["event_type"],
        "severity": "",
        "to": "claude",
        "message": "Idle artifact test event with substantive content.",
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": 1234,
        "cwd": "C:\\Python\\project2-master",
        "payload": payload,
    }


def _write_events(path: Path, payloads: list[dict]) -> None:
    path.write_text(
        "".join(
            json.dumps(_bridge_event(payload), sort_keys=True) + "\n"
            for payload in payloads
        ),
        encoding="utf-8",
    )


def _write_artifact(tmp_path: Path, payloads: list[dict]) -> dict:
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, payloads)
    return write_idle_consensus_artifact(
        events_path=events_path,
        out_dir=tmp_path / "artifacts",
        now_utc=NOW,
    )


def _write_artifact_with_receipt(tmp_path: Path, payloads: list[dict]) -> dict:
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, payloads)
    return write_idle_consensus_artifact(
        events_path=events_path,
        out_dir=tmp_path / "artifacts",
        receipt_out_dir=tmp_path / "receipt-bundle",
        now_utc=NOW,
    )


def _measured_counterfactual_receipt() -> dict:
    return {
        "schema_version": "magma.counterfactual_promotion_summary.v0",
        "status": "computed",
        "a3_label": "RUNTIME_MEASURED",
        "sample_count": 20,
        "divergence_count": 3,
        "same_sample_set": True,
        "deterministic": True,
        "no_delta": False,
        "delta_digest": "sha256:raw-delta-digest-not-exported",
        "per_arm": {
            "candidate": [
                {"inputs": {"secret": "SAMPLE_PAYLOAD_DO_NOT_EXPORT"}}
            ]
        },
        "divergences": [
            {"inputs": {"secret": "DIVERGENCE_PAYLOAD_DO_NOT_EXPORT"}}
        ],
    }


def test_soft_consensus_writes_operator_review_artifact(tmp_path: Path) -> None:
    report = _write_artifact(tmp_path, _soft_events())

    assert report["decision"] == "operator_review_required"
    assert report["convergence_status"] == "soft_convergence"
    assert report["auto_execute"] is False
    artifact = json.loads(Path(report["json_path"]).read_text(encoding="utf-8"))
    markdown = Path(report["markdown_path"]).read_text(encoding="utf-8")
    assert artifact["operator_gate_required"] is True
    assert artifact["auto_execute"] is False
    assert len(artifact["transcript"]) == 5
    assert "Operator review required" in markdown
    assert "no task creation" in markdown
    assert all(hint not in markdown.lower() for hint in PROHIBITED_HINTS)


def test_artifact_includes_digest_only_replay_seed(tmp_path: Path) -> None:
    report = _write_artifact(tmp_path, _soft_events())

    artifact = json.loads(Path(report["json_path"]).read_text(encoding="utf-8"))
    seed = artifact["replay_seed"]
    artifact_without_seed = dict(artifact)
    artifact_without_seed.pop("replay_seed")
    seed_text = json.dumps(seed, sort_keys=True)
    markdown = Path(report["markdown_path"]).read_text(encoding="utf-8")

    assert seed["seed_version"] == "idle_consensus_replay_seed.v0"
    assert seed["purpose"] == "future_counterfactual_candidate_diff_replay"
    assert seed["candidate_diff_included"] is False
    assert seed["dry_run_only"] is True
    assert seed["external_effect"] is False
    assert seed["writes_applied"] is False
    assert seed["would_create_task"] is False
    assert seed["would_create_branch"] is False
    assert seed["would_create_pr"] is False
    assert seed["would_merge"] is False
    assert seed["consensus_artifact"] == {
        "artifact_version": "idle_consensus_operator_review.v1",
        "artifact_id": artifact["artifact_id"],
        "digest": sha256_digest(artifact_without_seed),
    }
    assert seed["convergence_digest"] == sha256_digest(artifact["convergence"])
    assert seed["transcript_digest"] == sha256_digest(artifact["transcript"])
    assert seed["policy_ref"] == "policy:idle_consensus_artifact:v1"
    assert seed["charter_ref"] == "charter:idle_autonomy:v1"
    assert seed["required_future_inputs"] == [
        "changed_paths",
        "candidate_diff_digest",
        "candidate_diff_charter_gates",
        "counterfactual_eval_receipt",
        "operator_review_decision",
    ]
    assert seed["next_required_gates"] == [
        "candidate_changed_paths_confinement",
        "candidate_diff_digest_rederived",
        "candidate_diff_charter_gate",
        "counterfactual_eval_receipt",
        "operator_review_gate",
    ]
    assert "diff --git" not in seed_text
    assert "candidate_changed_paths" not in seed
    assert "Replay Seed" in markdown
    assert seed["consensus_artifact"]["digest"] in markdown


def test_replay_seed_refuses_artifact_without_operator_gate() -> None:
    with pytest.raises(ArtifactError) as excinfo:
        build_idle_consensus_replay_seed(
            {
                "artifact_version": "idle_consensus_operator_review.v1",
                "artifact_id": "unsafe",
                "operator_gate_required": False,
                "auto_execute": False,
                "convergence": {},
                "transcript": [],
            }
        )

    assert excinfo.value.report["decision"] == "replay_seed_refused"
    assert excinfo.value.report["errors"] == [
        "operator gate is required for replay seed"
    ]


def test_replay_seed_refuses_candidate_material_source() -> None:
    with pytest.raises(ArtifactError) as excinfo:
        build_idle_consensus_replay_seed(
            {
                "artifact_version": "idle_consensus_operator_review.v1",
                "artifact_id": "unsafe",
                "operator_gate_required": True,
                "auto_execute": False,
                "convergence": {},
                "transcript": [],
                "candidate_diff_text": "diff --git a/x b/x",
            }
        )

    assert excinfo.value.report["decision"] == "replay_seed_refused"
    assert excinfo.value.report["errors"] == [
        "candidate diff material is not allowed in replay seed source"
    ]
    assert excinfo.value.report["candidate_material_keys"] == ["candidate_diff_text"]


def test_replay_seed_refuses_existing_seed() -> None:
    with pytest.raises(ArtifactError) as excinfo:
        build_idle_consensus_replay_seed(
            {
                "artifact_version": "idle_consensus_operator_review.v1",
                "artifact_id": "unsafe",
                "operator_gate_required": True,
                "auto_execute": False,
                "convergence": {},
                "transcript": [],
                "replay_seed": {"candidate_diff_text": "diff --git a/x b/x"},
            }
        )

    assert excinfo.value.report["decision"] == "replay_seed_refused"
    assert excinfo.value.report["errors"] == [
        "existing replay_seed must not be provided"
    ]


def test_candidate_diff_replay_admission_reports_charter_gates_without_writes(
    tmp_path: Path,
) -> None:
    report = _write_artifact(tmp_path, _soft_events())
    artifact = json.loads(Path(report["json_path"]).read_text(encoding="utf-8"))
    seed = artifact["replay_seed"]
    diff_text = """diff --git a/docs/architecture/consensus_artifacts/replay.md b/docs/architecture/consensus_artifacts/replay.md
new file mode 100644
--- /dev/null
+++ b/docs/architecture/consensus_artifacts/replay.md
@@ -0,0 +1,3 @@
+# Replay
+
+Candidate diff is admitted for later operator review only.
"""

    admission = build_idle_consensus_candidate_diff_replay_admission(
        replay_seed=seed,
        changed_paths=["docs/architecture/consensus_artifacts/replay.md"],
        candidate_diff_text=diff_text,
    )
    serialized = json.dumps(admission, sort_keys=True)

    assert admission["report_version"] == CANDIDATE_DIFF_REPLAY_ADMISSION_VERSION
    assert admission["ok"] is True
    assert admission["decision"] == "candidate_diff_charter_passed"
    assert admission["dry_run"] is True
    assert admission["external_effect"] is False
    assert admission["writes_applied"] is False
    assert admission["would_create_task"] is False
    assert admission["would_create_branch"] is False
    assert admission["would_create_pr"] is False
    assert admission["would_merge"] is False
    assert admission["candidate_diff_charter_allowed"] is True
    assert admission["replay_seed"] == {
        "seed_version": "idle_consensus_replay_seed.v0",
        "digest": sha256_digest(seed),
        "consensus_artifact_digest": seed["consensus_artifact"]["digest"],
        "transcript_digest": seed["transcript_digest"],
        "convergence_digest": seed["convergence_digest"],
    }
    assert admission["candidate_diff"] == {
        "changed_paths": ["docs/architecture/consensus_artifacts/replay.md"],
        "digest": sha256_digest(
            {
                "changed_paths": ["docs/architecture/consensus_artifacts/replay.md"],
                "diff_text": diff_text,
            }
        ),
        "line_count": len(diff_text.splitlines()),
        "diff_text_included": False,
    }
    assert admission["path_gate"]["allowed"] is True
    assert admission["diff_gate"]["allowed"] is True
    assert admission["counterfactual_eval"] == {
        "summary_version": COUNTERFACTUAL_EVAL_ADMISSION_SUMMARY_VERSION,
        "provided": False,
        "source_digest": None,
        "receipt_payload_included": False,
        "satisfies_replay_gate": False,
        "dry_run_only": True,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "observability": {
            "schema_version": "magma.counterfactual_observability_status.v0",
            "source_available": False,
            "compute_status": "unavailable",
            "status": "unavailable",
            "a3_label": "INSUFFICIENT",
            "sample_count": 0,
            "divergence_count": 0,
            "same_sample_set": False,
            "deterministic": False,
            "no_delta": False,
            "delta_digest_present": False,
            "controls_present": False,
            "runtime_authority_granted": False,
            "external_writes_applied": False,
            "payload_fields_exported": False,
        },
    }
    assert admission["eligible_for_draft_pr_gate"] is False
    assert admission["draft_pr_gate_blockers"] == [
        "counterfactual_eval_receipt_missing",
        "operator_review_gate_required",
    ]
    assert admission["next_required_gates"] == [
        "counterfactual_eval_receipt",
        "operator_review_gate",
        "draft_pr_creation",
        "ci_green",
        "mergeable_clean",
        "exact_head_merge",
    ]
    assert "diff --git" not in serialized
    assert "Candidate diff is admitted" not in serialized


def test_candidate_diff_replay_admission_summarizes_counterfactual_receipt(
    tmp_path: Path,
) -> None:
    report = _write_artifact(tmp_path, _soft_events())
    artifact = json.loads(Path(report["json_path"]).read_text(encoding="utf-8"))
    receipt = _measured_counterfactual_receipt()
    diff_text = """diff --git a/docs/architecture/consensus_artifacts/replay.md b/docs/architecture/consensus_artifacts/replay.md
--- a/docs/architecture/consensus_artifacts/replay.md
+++ b/docs/architecture/consensus_artifacts/replay.md
@@ -1 +1,2 @@
 # Replay
+Measured counterfactual evidence is summarized without raw payload.
"""

    admission = build_idle_consensus_candidate_diff_replay_admission(
        replay_seed=artifact["replay_seed"],
        changed_paths=["docs/architecture/consensus_artifacts/replay.md"],
        candidate_diff_text=diff_text,
        counterfactual_eval_receipt=receipt,
    )
    summary = admission["counterfactual_eval"]
    serialized = json.dumps(admission, sort_keys=True)

    assert summary["summary_version"] == COUNTERFACTUAL_EVAL_ADMISSION_SUMMARY_VERSION
    assert summary["provided"] is True
    assert summary["source_digest"] == sha256_digest(receipt)
    assert summary["receipt_payload_included"] is False
    assert summary["satisfies_replay_gate"] is True
    assert summary["dry_run_only"] is True
    assert summary["runtime_authority_granted"] is False
    assert summary["external_writes_applied"] is False
    assert summary["observability"] == {
        "schema_version": "magma.counterfactual_observability_status.v0",
        "source_available": True,
        "compute_status": "computed",
        "status": "runtime_measured",
        "a3_label": "RUNTIME_MEASURED",
        "sample_count": 20,
        "divergence_count": 3,
        "same_sample_set": True,
        "deterministic": True,
        "no_delta": False,
        "delta_digest_present": True,
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "payload_fields_exported": False,
    }
    assert admission["draft_pr_gate_blockers"] == ["operator_review_gate_required"]
    assert admission["next_required_gates"] == [
        "operator_review_gate",
        "draft_pr_creation",
        "ci_green",
        "mergeable_clean",
        "exact_head_merge",
    ]
    assert "SAMPLE_PAYLOAD_DO_NOT_EXPORT" not in serialized
    assert "DIVERGENCE_PAYLOAD_DO_NOT_EXPORT" not in serialized
    assert "raw-delta-digest-not-exported" not in serialized
    assert "per_arm" not in serialized
    assert "divergences" not in serialized


def test_candidate_diff_replay_admission_blocks_insufficient_counterfactual_receipt(
    tmp_path: Path,
) -> None:
    report = _write_artifact(tmp_path, _soft_events())
    artifact = json.loads(Path(report["json_path"]).read_text(encoding="utf-8"))

    admission = build_idle_consensus_candidate_diff_replay_admission(
        replay_seed=artifact["replay_seed"],
        changed_paths=["docs/architecture/consensus_artifacts/replay.md"],
        candidate_diff_text=(
            "diff --git a/docs/architecture/consensus_artifacts/replay.md "
            "b/docs/architecture/consensus_artifacts/replay.md\n"
        ),
        counterfactual_eval_receipt={
            "schema_version": "magma.counterfactual_promotion_summary.v0",
            "status": "computed",
            "a3_label": "INSUFFICIENT",
            "same_sample_set": True,
            "deterministic": True,
            "delta_digest": "sha256:present-but-insufficient",
        },
    )

    assert admission["counterfactual_eval"]["provided"] is True
    assert admission["counterfactual_eval"]["satisfies_replay_gate"] is False
    assert admission["draft_pr_gate_blockers"] == [
        "counterfactual_eval_receipt_insufficient",
        "operator_review_gate_required",
    ]
    assert admission["next_required_gates"][0] == "counterfactual_eval_receipt"


def test_candidate_diff_replay_admission_refuses_private_counterfactual_receipt(
    tmp_path: Path,
) -> None:
    report = _write_artifact(tmp_path, _soft_events())
    artifact = json.loads(Path(report["json_path"]).read_text(encoding="utf-8"))

    with pytest.raises(ArtifactError) as excinfo:
        build_idle_consensus_candidate_diff_replay_admission(
            replay_seed=artifact["replay_seed"],
            changed_paths=["docs/architecture/consensus_artifacts/replay.md"],
            candidate_diff_text=(
                "diff --git a/docs/architecture/consensus_artifacts/replay.md "
                "b/docs/architecture/consensus_artifacts/replay.md\n"
            ),
            counterfactual_eval_receipt={"note": "PRIVATE_MARKER"},
        )

    assert excinfo.value.report["decision"] == "privacy_marker_detected"
    assert excinfo.value.report["errors"] == [
        "counterfactual eval receipt contains PRIVATE_MARKER"
    ]


def test_cli_candidate_diff_replay_admission_reports_without_writes(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    report = _write_artifact(tmp_path, _soft_events())
    artifact = json.loads(Path(report["json_path"]).read_text(encoding="utf-8"))
    seed_path = tmp_path / "replay-seed.json"
    seed_path.write_text(
        json.dumps(artifact["replay_seed"], sort_keys=True),
        encoding="utf-8",
    )
    diff_text = """diff --git a/docs/architecture/consensus_artifacts/replay.md b/docs/architecture/consensus_artifacts/replay.md
new file mode 100644
--- /dev/null
+++ b/docs/architecture/consensus_artifacts/replay.md
@@ -0,0 +1,3 @@
+# Replay
+
+Candidate diff is admitted for later operator review only.
"""
    diff_path = tmp_path / "candidate.patch"
    diff_path.write_text(diff_text, encoding="utf-8")
    artifact_out_dir = tmp_path / "should-not-write-artifacts"
    receipt_out_dir = tmp_path / "should-not-write-receipts"

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "tools" / "idle_consensus_artifact.py"),
            "--candidate-diff-replay-admission",
            "--replay-seed",
            str(seed_path),
            "--candidate-diff",
            str(diff_path),
            "--changed-path",
            "docs/architecture/consensus_artifacts/replay.md",
            "--out-dir",
            str(artifact_out_dir),
            "--receipt-out-dir",
            str(receipt_out_dir),
            "--json",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    admission = json.loads(completed.stdout)
    assert admission["ok"] is True
    assert admission["decision"] == "candidate_diff_charter_passed"
    assert admission["exit_code"] == 0
    assert admission["dry_run"] is True
    assert admission["external_effect"] is False
    assert admission["writes_applied"] is False
    assert admission["would_create_task"] is False
    assert admission["would_create_branch"] is False
    assert admission["would_create_pr"] is False
    assert admission["would_merge"] is False
    assert admission["candidate_diff"]["diff_text_included"] is False
    assert admission["candidate_diff"]["digest"] == sha256_digest(
        {
            "changed_paths": ["docs/architecture/consensus_artifacts/replay.md"],
            "diff_text": diff_text,
        }
    )
    assert "diff --git" not in completed.stdout
    assert "Candidate diff is admitted" not in completed.stdout
    assert not artifact_out_dir.exists()
    assert not receipt_out_dir.exists()


def test_cli_candidate_diff_replay_admission_accepts_counterfactual_receipt(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    report = _write_artifact(tmp_path, _soft_events())
    artifact = json.loads(Path(report["json_path"]).read_text(encoding="utf-8"))
    seed_path = tmp_path / "replay-seed.json"
    seed_path.write_text(
        json.dumps(artifact["replay_seed"], sort_keys=True),
        encoding="utf-8",
    )
    diff_text = """diff --git a/docs/architecture/consensus_artifacts/replay.md b/docs/architecture/consensus_artifacts/replay.md
--- a/docs/architecture/consensus_artifacts/replay.md
+++ b/docs/architecture/consensus_artifacts/replay.md
@@ -1 +1,2 @@
 # Replay
+Measured counterfactual receipt available.
"""
    diff_path = tmp_path / "candidate.patch"
    diff_path.write_text(diff_text, encoding="utf-8")
    receipt = _measured_counterfactual_receipt()
    receipt_path = tmp_path / "counterfactual-receipt.json"
    receipt_path.write_text(json.dumps(receipt, sort_keys=True), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "tools" / "idle_consensus_artifact.py"),
            "--candidate-diff-replay-admission",
            "--replay-seed",
            str(seed_path),
            "--candidate-diff",
            str(diff_path),
            "--changed-path",
            "docs/architecture/consensus_artifacts/replay.md",
            "--counterfactual-eval-receipt",
            str(receipt_path),
            "--json",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    admission = json.loads(completed.stdout)
    assert admission["counterfactual_eval"]["provided"] is True
    assert admission["counterfactual_eval"]["source_digest"] == sha256_digest(receipt)
    assert admission["counterfactual_eval"]["satisfies_replay_gate"] is True
    assert admission["draft_pr_gate_blockers"] == ["operator_review_gate_required"]
    assert "SAMPLE_PAYLOAD_DO_NOT_EXPORT" not in completed.stdout
    assert "raw-delta-digest-not-exported" not in completed.stdout


def test_cli_candidate_diff_replay_admission_returns_one_for_operator_gate(
    tmp_path: Path,
) -> None:
    root = Path(__file__).resolve().parents[2]
    report = _write_artifact(tmp_path, _soft_events())
    diff_text = (
        "diff --git a/configs/bridge_event_validation_waivers.json "
        "b/configs/bridge_event_validation_waivers.json\n"
    )
    diff_path = tmp_path / "candidate.patch"
    diff_path.write_text(diff_text, encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "tools" / "idle_consensus_artifact.py"),
            "--candidate-diff-replay-admission",
            "--replay-seed",
            str(report["json_path"]),
            "--candidate-diff",
            str(diff_path),
            "--changed-path",
            "configs/bridge_event_validation_waivers.json",
            "--json",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1, completed.stderr
    admission = json.loads(completed.stdout)
    assert admission["ok"] is False
    assert admission["decision"] == "operator_review_required"
    assert admission["exit_code"] == 1
    assert admission["path_gate"]["allowed"] is False
    assert admission["path_gate"]["blocked_paths"] == [
        "configs/bridge_event_validation_waivers.json"
    ]
    assert admission["diff_gate"]["allowed"] is True
    assert admission["writes_applied"] is False
    assert "diff --git" not in completed.stdout


def test_candidate_diff_replay_admission_blocks_charter_denied_path(
    tmp_path: Path,
) -> None:
    report = _write_artifact(tmp_path, _soft_events())
    artifact = json.loads(Path(report["json_path"]).read_text(encoding="utf-8"))

    admission = build_idle_consensus_candidate_diff_replay_admission(
        replay_seed=artifact["replay_seed"],
        changed_paths=["configs/bridge_event_validation_waivers.json"],
        candidate_diff_text=(
            "diff --git a/configs/bridge_event_validation_waivers.json "
            "b/configs/bridge_event_validation_waivers.json\n"
        ),
    )

    assert admission["ok"] is False
    assert admission["decision"] == "operator_review_required"
    assert admission["candidate_diff_charter_allowed"] is False
    assert admission["path_gate"]["allowed"] is False
    assert admission["path_gate"]["blocked_paths"] == [
        "configs/bridge_event_validation_waivers.json"
    ]
    assert admission["diff_gate"]["allowed"] is True
    assert admission["writes_applied"] is False
    assert "diff --git" not in json.dumps(admission, sort_keys=True)


def test_candidate_diff_replay_admission_refuses_private_marker(
    tmp_path: Path,
) -> None:
    report = _write_artifact(tmp_path, _soft_events())
    artifact = json.loads(Path(report["json_path"]).read_text(encoding="utf-8"))

    with pytest.raises(ArtifactError) as excinfo:
        build_idle_consensus_candidate_diff_replay_admission(
            replay_seed=artifact["replay_seed"],
            changed_paths=["docs/architecture/consensus_artifacts/replay.md"],
            candidate_diff_text="diff --git a/x b/x\n+PRIVATE_MARKER",
        )

    assert excinfo.value.report["decision"] == "privacy_marker_detected"
    assert excinfo.value.report["errors"] == ["candidate diff contains PRIVATE_MARKER"]


@pytest.mark.parametrize("flag", REPLAY_SEED_REQUIRED_FALSE_KEYS)
def test_candidate_diff_replay_admission_refuses_replay_seed_authority_tamper(
    tmp_path: Path,
    flag: str,
) -> None:
    report = _write_artifact(tmp_path, _soft_events())
    artifact = json.loads(Path(report["json_path"]).read_text(encoding="utf-8"))
    tampered_seed = dict(artifact["replay_seed"])
    tampered_seed[flag] = True

    with pytest.raises(ArtifactError) as excinfo:
        build_idle_consensus_candidate_diff_replay_admission(
            replay_seed=tampered_seed,
            changed_paths=["docs/architecture/consensus_artifacts/replay.md"],
            candidate_diff_text=(
                "diff --git a/docs/architecture/consensus_artifacts/replay.md "
                "b/docs/architecture/consensus_artifacts/replay.md\n"
            ),
        )

    assert excinfo.value.report["decision"] == "candidate_diff_replay_refused"
    assert excinfo.value.report["errors"] == [f"replay seed {flag} must be false"]


def test_candidate_diff_replay_admission_refuses_replay_seed_dry_run_tamper(
    tmp_path: Path,
) -> None:
    report = _write_artifact(tmp_path, _soft_events())
    artifact = json.loads(Path(report["json_path"]).read_text(encoding="utf-8"))
    tampered_seed = dict(artifact["replay_seed"])
    tampered_seed["dry_run_only"] = False

    with pytest.raises(ArtifactError) as excinfo:
        build_idle_consensus_candidate_diff_replay_admission(
            replay_seed=tampered_seed,
            changed_paths=["docs/architecture/consensus_artifacts/replay.md"],
            candidate_diff_text=(
                "diff --git a/docs/architecture/consensus_artifacts/replay.md "
                "b/docs/architecture/consensus_artifacts/replay.md\n"
            ),
        )

    assert excinfo.value.report["decision"] == "candidate_diff_replay_refused"
    assert excinfo.value.report["errors"] == ["replay seed dry_run_only must be true"]


def test_candidate_diff_replay_admission_refuses_replay_seed_candidate_material(
    tmp_path: Path,
) -> None:
    report = _write_artifact(tmp_path, _soft_events())
    artifact = json.loads(Path(report["json_path"]).read_text(encoding="utf-8"))
    tampered_seed = dict(artifact["replay_seed"])
    tampered_seed["candidate_diff_text"] = "diff --git a/x b/x\n"

    with pytest.raises(ArtifactError) as excinfo:
        build_idle_consensus_candidate_diff_replay_admission(
            replay_seed=tampered_seed,
            changed_paths=["docs/architecture/consensus_artifacts/replay.md"],
            candidate_diff_text=(
                "diff --git a/docs/architecture/consensus_artifacts/replay.md "
                "b/docs/architecture/consensus_artifacts/replay.md\n"
            ),
        )

    assert excinfo.value.report["decision"] == "candidate_diff_replay_refused"
    assert excinfo.value.report["errors"] == [
        "candidate diff material is not allowed in replay seed"
    ]
    assert excinfo.value.report["candidate_material_keys"] == ["candidate_diff_text"]


def test_consensus_target_colon_is_sanitized_before_artifact_write(
    tmp_path: Path,
) -> None:
    events = _soft_events()
    for payload in events:
        if payload["event_type"] == "idle_consensus_reached":
            payload["consensus_target_proposal_id"] = "idle-artifact:ads"

    report = _write_artifact(tmp_path, events)

    assert report["artifact_id"] == "idle-consensus-idle-artifact-ads"
    json_path = Path(report["json_path"])
    markdown_path = Path(report["markdown_path"])
    assert ":" not in json_path.name
    assert ":" not in markdown_path.name
    assert sorted(path.name for path in json_path.parent.iterdir()) == [
        "idle-consensus-idle-artifact-ads.json",
        "idle-consensus-idle-artifact-ads.md",
    ]


def test_soft_consensus_receipt_bundle_is_verified_and_bound_to_artifact(
    tmp_path: Path,
) -> None:
    report = _write_artifact_with_receipt(tmp_path, _soft_events())

    bundle = report["receipt_bundle"]
    assert bundle["verifier_report"] == {
        "ok": True,
        "receipt_count": 1,
        "errors": [],
    }
    assert verify_manifest(Path(bundle["manifest"]))["ok"] is True
    artifact = json.loads(Path(report["json_path"]).read_text(encoding="utf-8"))
    payload = json.loads(
        (tmp_path / "receipt-bundle" / "payload-001-artifact.json").read_text(
            encoding="utf-8"
        )
    )
    evaluation = json.loads(
        (tmp_path / "receipt-bundle" / "evaluation-001-artifact.json").read_text(
            encoding="utf-8"
        )
    )
    receipt = json.loads(
        (tmp_path / "receipt-bundle" / "receipt-001-artifact.json").read_text(
            encoding="utf-8"
        )
    )
    assert payload == artifact
    assert evaluation["actual_gate"] == "review"
    assert evaluation["operator_required"] is True
    assert receipt["risk_class"] == "local_artifact"
    assert receipt["operator_gate_required"] is True
    assert receipt["approval_id"] is None


def test_no_receipt_bundle_by_default(tmp_path: Path) -> None:
    report = _write_artifact(tmp_path, _soft_events())

    assert "receipt_bundle" not in report
    assert not (tmp_path / "receipt-bundle").exists()


def test_existing_receipt_dir_refuses_before_artifact_write(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    receipt_dir = tmp_path / "receipt-bundle"
    receipt_dir.mkdir()
    _write_events(events_path, _soft_events())

    with pytest.raises(ArtifactError) as excinfo:
        write_idle_consensus_artifact(
            events_path=events_path,
            out_dir=tmp_path / "artifacts",
            receipt_out_dir=receipt_dir,
            now_utc=NOW,
        )

    assert excinfo.value.report["decision"] == "refuse_receipt_overwrite"
    assert not (tmp_path / "artifacts").exists()


def test_receipt_verifier_failure_blocks_artifact_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import tools.idle_consensus_artifact as artifact_tool

    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, _soft_events())

    def fake_verify_manifest(path: Path) -> dict[str, object]:
        return {
            "ok": False,
            "receipt_count": 1,
            "errors": ["simulated receipt failure"],
        }

    monkeypatch.setattr(artifact_tool, "verify_manifest", fake_verify_manifest)

    with pytest.raises(ArtifactError) as excinfo:
        write_idle_consensus_artifact(
            events_path=events_path,
            out_dir=tmp_path / "artifacts",
            receipt_out_dir=tmp_path / "receipt-bundle",
            now_utc=NOW,
        )

    assert excinfo.value.report["decision"] == "invalid_receipt_bundle"
    assert not (tmp_path / "artifacts").exists()


def test_hard_consensus_writes_finalist_artifact(tmp_path: Path) -> None:
    report = _write_artifact(tmp_path, _hard_events())

    artifact = json.loads(Path(report["json_path"]).read_text(encoding="utf-8"))
    assert report["convergence_status"] == "hard_convergence"
    assert artifact["convergence"]["finalist_proposal_ids"] == [
        "idle-artifact-010",
        "idle-artifact-009",
        "idle-artifact-008",
    ]
    assert artifact["prohibited_actions"] == [
        "no_task_creation",
        "no_branch_creation",
        "no_pull_request_creation",
        "no_external_effect",
    ]


def test_charter_violation_refuses_artifact(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [_proposal(), _counter(), _charter_violation()])

    with pytest.raises(ArtifactError) as excinfo:
        write_idle_consensus_artifact(
            events_path=events_path,
            out_dir=tmp_path / "artifacts",
            now_utc=NOW,
        )

    assert excinfo.value.report["decision"] == "charter_violation"
    assert not (tmp_path / "artifacts").exists()


def test_no_consensus_refuses_without_output(tmp_path: Path) -> None:
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [_proposal(), _counter()])

    with pytest.raises(ArtifactError) as excinfo:
        write_idle_consensus_artifact(
            events_path=events_path,
            out_dir=tmp_path / "artifacts",
            now_utc=NOW,
        )

    assert excinfo.value.report["decision"] == "no_consensus"
    assert not (tmp_path / "artifacts").exists()


def test_privacy_marker_refuses_without_output(tmp_path: Path) -> None:
    payload = _proposal()
    payload["simulation_evidence"]["summary"] = "PRIVATE_MARKER must not be written."
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, [payload])

    with pytest.raises(ArtifactError) as excinfo:
        write_idle_consensus_artifact(
            events_path=events_path,
            out_dir=tmp_path / "artifacts",
            now_utc=NOW,
        )

    assert excinfo.value.report["decision"] == "privacy_marker_detected"
    assert not (tmp_path / "artifacts").exists()


def test_existing_artifact_refuses_overwrite(tmp_path: Path) -> None:
    report = _write_artifact(tmp_path, _soft_events())

    with pytest.raises(ArtifactError) as excinfo:
        _write_artifact(tmp_path, _soft_events())

    assert excinfo.value.report["decision"] == "refuse_overwrite"
    assert Path(report["json_path"]).exists()


def test_cli_runs_by_file_path_from_repo_root(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    events_path = tmp_path / "events.jsonl"
    _write_events(events_path, _soft_events())

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "tools" / "idle_consensus_artifact.py"),
            "--events",
            str(events_path),
            "--out-dir",
            str(tmp_path / "artifacts"),
            "--receipt-out-dir",
            str(tmp_path / "receipt-bundle"),
            "--now",
            "2026-05-18T09:00:00Z",
            "--json",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["decision"] == "operator_review_required"
    assert Path(report["json_path"]).exists()
    assert report["receipt_bundle"]["verifier_report"]["ok"] is True
