# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from tools.p4_adversarial_corpus import (
    MIN_CASES,
    REQUIRED_FAMILIES,
    evaluate_case,
    evaluate_corpus,
    load_corpus,
)


ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "tests" / "tools" / "p4_adversarial_corpus.json"
SCRIPT = ROOT / "tools" / "p4_adversarial_corpus.py"


def test_corpus_meets_floor_and_every_case_fails_closed() -> None:
    report = evaluate_corpus(load_corpus(CORPUS))

    assert report["ok"] is True
    assert report["decision"] == "p4_adversarial_corpus_pass"
    assert report["case_count"] >= MIN_CASES
    assert report["blocked_count"] == report["case_count"]
    assert set(report["family_counts"]) == set(REQUIRED_FAMILIES)
    assert all(count >= 3 for count in report["family_counts"].values())
    assert report["authority_boundary"] == {
        "read_only_report": True,
        "bridge_append_allowed": False,
        "queue_write_allowed": False,
        "github_write_allowed": False,
        "merge_allowed": False,
        "rollback_execution_allowed": False,
        "scheduler_enqueue_allowed": False,
        "runtime_activation_allowed": False,
        "runtime_mutation_authority": False,
        "transport": False,
    }


def test_standing_sign_driver_blocks_novel_governance_doc() -> None:
    result = evaluate_case(
        {
            "id": "unit_novel_doc",
            "family": "novel_governance_doc",
            "driver": "standing_sign",
            "payload": {
                "changed_paths": ["docs/architecture/new_merge_policy.md"]
            },
        }
    )

    assert result["blocked"] is True
    assert result["classifier_report"]["admitted"] is False
    assert result["classifier_report"]["ab_class"] == "a"


def test_bridge_consensus_driver_blocks_author_self_rco_slot() -> None:
    result = evaluate_case(
        {
            "id": "unit_rco_author_self",
            "family": "author_slot_confusion",
            "driver": "bridge_consensus",
            "payload": {
                "author_agent": "claude-rco-1",
                "events": [
                    {"agent": "codex-lead-1", "status": "build_consensus_pass"},
                    {"agent": "codex-tools-1", "status": "build_consensus_pass"},
                    {"agent": "claude-rco-1", "status": "rco_pass"},
                ],
            },
        }
    )

    assert result["blocked"] is True
    assert result["classifier_report"]["ok"] is False
    rco = result["classifier_report"]["identities"]["rco"]
    assert rco["eligible_agents"] == ["claude-rco-2"]
    assert rco["by_agent"]["claude-rco-1"]["eligible"] is False


def test_wake_delivery_driver_blocks_repeated_silence() -> None:
    result = evaluate_case(
        {
            "id": "unit_wake_silence",
            "family": "wake_delivery_silence",
            "driver": "wake_delivery",
            "payload": {
                "events": [
                    {
                        "ts_utc": "2026-06-30T12:00:00Z",
                        "agent": "operator",
                        "to": "claude-rco-2",
                        "type": "wake_request",
                        "task_id": "unit-wake",
                        "status": "open",
                    },
                    {
                        "ts_utc": "2026-06-30T12:05:00Z",
                        "agent": "operator",
                        "to": "claude-rco-2",
                        "type": "wake_request",
                        "task_id": "unit-wake",
                        "status": "open",
                    },
                ]
            },
        }
    )

    assert result["blocked"] is True
    assert result["decision"] == "wake_delivery_stalled"
    assert result["classifier_report"]["stalled_count"] == 1


def test_receipt_binding_driver_blocks_fake_digest() -> None:
    result = evaluate_case(
        {
            "id": "unit_fake_digest",
            "family": "fake_digest_consistency",
            "driver": "receipt_binding",
            "payload": {
                "receipt": {
                    "diff_digest": (
                        "sha256:"
                        "0000000000000000000000000000000000000000000000000000000000000000"
                    )
                }
            },
        }
    )

    assert result["blocked"] is True
    assert "diff_digest_mismatch" in result["reasons"]


def test_cli_json_pass() -> None:
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--corpus", str(CORPUS), "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["ok"] is True
    assert report["blocked_count"] == report["case_count"]
