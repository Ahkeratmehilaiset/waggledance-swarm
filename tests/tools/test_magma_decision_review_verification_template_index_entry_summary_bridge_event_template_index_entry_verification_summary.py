import copy
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from tools.build_magma_decision_review_verification_template_index_entry import (
    INDEX_ENTRY_VERSION as SOURCE_INDEX_ENTRY_VERSION,
)
from tools.build_magma_decision_review_verification_template_index_entry_summary import (
    build_magma_decision_review_verification_template_index_entry_summary,
)
from tools.build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template import (
    build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template,
)
from tools.build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry import (
    INDEX_ENTRY_VERSION,
    build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry,
)
from tools.build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary import (
    SUMMARY_VERSION,
    build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary,
    render_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary_markdown,
)
from tools.verify_magma_decision_review_verification_template_index_entry import (
    VERIFICATION_VERSION as SOURCE_VERIFICATION_VERSION,
)
from tools.verify_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry import (
    VERIFICATION_VERSION,
    verify_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry,
)


def _joined(*parts: str) -> str:
    return "".join(parts)


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / (
        "build_magma_decision_review_verification_template_index_entry_summary_"
        "bridge_event_template_index_entry_verification_summary.py"
    )
)
COMMIT_SHA = "f" * 40
DECISION_REF = "bridge:operator-decision:pending-review"
FIXED_NOW = datetime(2026, 5, 29, 8, 45, tzinfo=timezone.utc)
SENSITIVE_PATH_SEGMENT_FIXTURE = _chars(112, 114, 105, 118, 97, 116, 101)
SENSITIVE_TOKEN_PREFIX_FIXTURE = _chars(80, 82, 73, 86, 65, 84, 69, 95)
WEB_SCHEME_FIXTURE = _joined(_chars(104, 116, 116, 112), ":", "/", "/")
WEB_SCHEME_TLS_FIXTURE = _joined(_chars(104, 116, 116, 112, 115), ":", "/", "/")
FORBIDDEN_OUTPUT_SNIPPETS = (
    _joined("C", ":", "/", SENSITIVE_PATH_SEGMENT_FIXTURE),
    SENSITIVE_TOKEN_PREFIX_FIXTURE,
    WEB_SCHEME_FIXTURE,
    WEB_SCHEME_TLS_FIXTURE,
)
RELATIVE_PATH_FIXTURE = _joined("relative", "/", "report.json")
FORBIDDEN_PATH_FIXTURE = _joined(
    "C", ":", "/", SENSITIVE_PATH_SEGMENT_FIXTURE, "/", "source.json"
)
FORBIDDEN_PAYLOAD_FIXTURE = _joined(SENSITIVE_TOKEN_PREFIX_FIXTURE, "payload")


def test_summary_bridge_event_template_index_entry_verification_summary_is_path_free_review_context() -> None:
    report = _verification_report()

    summary = build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:decision-reference-review-template-index-summary-template-index-verifier",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is True
    assert summary["summary_version"] == SUMMARY_VERSION
    assert summary["created_at_utc"] == "2026-05-29T08:45:00Z"
    assert summary["release_ref"] == "pr:775"
    assert summary["commit_sha"] == COMMIT_SHA
    assert summary["ci_run_ref"] == "gh:run:decision-reference-review-summary-template-index-verifier"
    reviewer = summary["reviewer_ownership"]
    assert reviewer["reviewer_agent_id"] == "claude-rco-1"
    assert reviewer["manual_review_required"] is True
    verification = summary[
        "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification"
    ]
    assert verification["verification_ok"] is True
    assert verification["verification_version"] == VERIFICATION_VERSION
    assert verification["index_entry_version"] == INDEX_ENTRY_VERSION
    assert verification["artifact_count_checked"] == 2
    assert set(verification["digest_checks"].values()) == {"match"}
    assert set(verification["size_checks"].values()) == {"match"}
    assert set(verification["schema_version_checks"].values()) == {"match"}
    assert verification["source_contract_check"] == "match"
    assert verification["rebuilt_index_entry_check"] == "match"
    assert verification["bridge_event_schema_check"] == "match"
    assert verification["template_only"] is True
    assert verification["blocker_count"] == 0
    reference = summary["operator_decision_reference_review"]
    assert reference["decision_reference"] == DECISION_REF
    assert reference["expected_decision_reference"] == DECISION_REF
    assert reference["decision_reference_verified"] is True
    assert reference["decision_reference_is_approval"] is False
    assert reference["decision_reference_is_release_decision"] is False
    boundary = summary["operator_boundary"]
    assert boundary["verification_report_boundary_ok"] is True
    assert boundary["boundary_blockers"] == []
    assert summary["manual_review_required"] is True
    assert summary["approval_granted"] is False
    assert summary["release_decision_made"] is False
    assert summary["automatic_release_decision"] is False
    assert summary["direct_bridge_write_performed"] is False
    assert summary["transport_added"] is False
    assert summary["external_fetch_performed"] is False
    assert summary["runtime_controls_added"] is False
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False
    assert not any(
        marker in json.dumps(summary, sort_keys=True) for marker in FORBIDDEN_OUTPUT_SNIPPETS
    )


def test_summary_bridge_event_template_index_entry_verification_summary_markdown_is_path_free() -> None:
    summary = build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=_verification_report(),
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:decision-reference-review-template-index-summary-template-index-verifier",
        now_utc=FIXED_NOW,
    )

    markdown = render_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary_markdown(
        summary
    )

    assert "MAGMA Summary Bridge-Template Index-Entry Verification Summary" in markdown
    assert "Approval granted: `false`" in markdown
    assert "Artifact payloads included: `false`" in markdown
    assert "Local paths recorded: `false`" in markdown
    assert not any(marker in markdown for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_summary_bridge_event_template_index_entry_verification_summary_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "verification_report.json"
    report_path.write_bytes(_json_bytes(_verification_report()))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            str(report_path),
            "--reviewer-agent",
            "claude-rco-1",
            "--handoff-ref",
            "bridge:handoff:decision-reference-review-template-index-summary-template-index-verifier",
            "--now",
            "2026-05-29T08:45:00Z",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["summary_version"] == SUMMARY_VERSION
    assert payload["direct_bridge_write_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    assert report_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_summary_bridge_event_template_index_entry_verification_summary_rejects_fail_open_flags() -> None:
    report = _verification_report()
    report["ok"] = "true"
    report["operator_decision_reference_review"]["decision_reference_verified"] = True

    summary = build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:decision-reference-review-template-index-summary-template-index-verifier",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "verification_report_not_ok" in summary["blockers"]
    assert (
        summary["operator_decision_reference_review"]["decision_reference_verified"]
        is False
    )
    assert summary["approval_granted"] is False
    assert summary["release_decision_made"] is False


def test_summary_bridge_event_template_index_entry_verification_summary_rejects_nested_authority() -> None:
    report = _verification_report()
    report["runtime_controls_added"] = True

    summary = build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:decision-reference-review-template-index-summary-template-index-verifier",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "verification_report_runtime_controls_added_not_false" in summary[
        "blockers"
    ]
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is False
    assert summary["runtime_controls_added"] is False
    assert summary["direct_bridge_write_performed"] is False


def test_summary_bridge_event_template_index_entry_verification_summary_rejects_benign_raw_payload_key() -> None:
    report = _verification_report()
    report["raw_payload"] = {"artifact": "inline-json", "size": 1}

    summary = build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:decision-reference-review-template-index-summary-template-index-verifier",
        now_utc=FIXED_NOW,
    )

    serialized = json.dumps(summary, sort_keys=True)
    assert summary["ok"] is False
    assert "verification_report_forbidden_payload_key:raw_payload" in summary[
        "blockers"
    ]
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is False
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False
    assert "inline-json" not in serialized
    assert not any(marker in serialized for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_summary_bridge_event_template_index_entry_verification_summary_rejects_benign_local_path_key() -> None:
    report = _verification_report()
    report["artifact_index"] = {"local_path": RELATIVE_PATH_FIXTURE}

    summary = build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:decision-reference-review-template-index-summary-template-index-verifier",
        now_utc=FIXED_NOW,
    )

    serialized = json.dumps(summary, sort_keys=True)
    assert summary["ok"] is False
    assert "verification_report_forbidden_path_key:local_path" in summary[
        "blockers"
    ]
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is False
    assert summary["local_paths_recorded"] is False
    assert RELATIVE_PATH_FIXTURE not in serialized
    assert not any(marker in serialized for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_summary_bridge_event_template_index_entry_verification_summary_rejects_nested_authority_structures() -> None:
    report = _verification_report()
    report["operator_boundary"] = {
        "approval_granted": True,
        "release_decision_made": True,
    }
    report["reviewer_ownership"] = {"approval_granted": True}

    summary = build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:decision-reference-review-template-index-summary-template-index-verifier",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "verification_report_forbidden_authority_container:operator_boundary" in summary[
        "blockers"
    ]
    assert "verification_report_forbidden_authority_container:reviewer_ownership" in summary[
        "blockers"
    ]
    assert "verification_report_nested_authority_field_not_false:approval_granted" in summary[
        "blockers"
    ]
    assert "verification_report_nested_authority_field_not_false:release_decision_made" in summary[
        "blockers"
    ]
    assert summary["operator_boundary"]["verification_report_boundary_ok"] is False
    assert summary["approval_granted"] is False
    assert summary["release_decision_made"] is False
    assert summary["automatic_release_decision"] is False


def test_summary_bridge_event_template_index_entry_verification_summary_rejects_top_level_reference_authority_flags() -> None:
    for field in (
        "decision_reference_is_approval",
        "decision_reference_is_release_decision",
    ):
        report = _verification_report()
        report[field] = True

        summary = build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary(
            verification_report=report,
            reviewer_agent_id="claude-rco-1",
            handoff_ref="bridge:handoff:decision-reference-review-template-index-summary-template-index-verifier",
            now_utc=FIXED_NOW,
        )

        assert summary["ok"] is False
        assert f"verification_report_{field}_not_false" in summary["blockers"]
        assert summary["operator_boundary"]["verification_report_boundary_ok"] is False
        assert summary["operator_decision_reference_review"][
            "decision_reference_is_approval"
        ] is False
        assert summary["operator_decision_reference_review"][
            "decision_reference_is_release_decision"
        ] is False
        assert summary["approval_granted"] is False
        assert summary["release_decision_made"] is False


def test_summary_bridge_event_template_index_entry_verification_summary_rejects_check_mismatch() -> None:
    report = _verification_report()
    report["digest_checks"] = dict(report["digest_checks"])
    report["digest_checks"][_REQUIRED_TEMPLATE_ARTIFACT_ID] = "mismatch"

    summary = build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry_verification_summary(
        verification_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:decision-reference-review-template-index-summary-template-index-verifier",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert (
        f"verification_check_not_match:digest_checks:{_REQUIRED_TEMPLATE_ARTIFACT_ID}"
        in summary["blockers"]
    )
    verification = summary[
        "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification"
    ]
    assert verification["digest_checks"][_REQUIRED_TEMPLATE_ARTIFACT_ID] == "mismatch"
    assert summary["artifact_payloads_included"] is False


def test_summary_bridge_event_template_index_entry_verification_summary_rejects_forbidden_marker_payload_path_free(
    tmp_path: Path,
) -> None:
    report = _verification_report()
    report["raw_payload"] = {
        "local_path": FORBIDDEN_PATH_FIXTURE,
        "payload": FORBIDDEN_PAYLOAD_FIXTURE,
    }
    report_path = tmp_path / "verification_report.json"
    report_path.write_bytes(_json_bytes(report))

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            str(report_path),
            "--reviewer-agent",
            "claude-rco-1",
            "--handoff-ref",
            "bridge:handoff:decision-reference-review-template-index-summary-template-index-verifier",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_failed:verification_report_forbidden_marker"
    ]
    assert payload["approval_granted"] is False
    assert payload["direct_bridge_write_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    assert report_path.name not in combined
    assert "source.json" not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_summary_bridge_event_template_index_entry_verification_summary_missing_input_is_path_free(
    tmp_path: Path,
) -> None:
    missing_path = tmp_path / "missing.json"

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            str(missing_path),
            "--reviewer-agent",
            "claude-rco-1",
            "--handoff-ref",
            "bridge:handoff:decision-reference-review-template-index-summary-template-index-verifier",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_failed:verification_report_unreadable"
    ]
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    assert missing_path.name not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_summary_bridge_event_template_index_entry_verification_summary_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "verification_report.json"
    report_path.write_text('{"ok": NaN}', encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--verification-json",
            str(report_path),
            "--reviewer-agent",
            "claude-rco-1",
            "--handoff-ref",
            "bridge:handoff:decision-reference-review-template-index-summary-template-index-verifier",
            "--json",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "operator_decision_reference_review_bundle_verification_bridge_event_template_index_entry_verification_summary_bridge_event_template_index_entry_verification_summary_failed:verification_report_json_error"
    ]
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    assert report_path.name not in combined
    assert "Traceback" not in combined
    assert not any(marker in combined for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def _verification_report() -> dict:
    artifacts = _artifact_set()
    index_entry = _index_entry(artifacts)
    raw = _artifact_bytes(artifacts)
    return verify_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry(
        index_entry=index_entry,
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
    )


def _artifact_set() -> dict[str, dict]:
    summary = _summary()
    template = build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template(
        summary=summary,
        agent_id="codex-lead-1",
        task_id="wd-image1-decision-review-template-index-summary-template-index-verifier",
        to="operator,claude-rco-1",
        run_id="codex-lead-1-20260529T083500Z",
        session_id="codex-lead-1-20260529T083500Z",
        now_utc=datetime(2026, 5, 29, 8, 35, tzinfo=timezone.utc),
    )
    return {
        "summary": summary,
        "template": template,
    }


def _summary() -> dict:
    return build_magma_decision_review_verification_template_index_entry_summary(
        verification_report=_source_verification_report(),
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:decision-reference-review-template-index-summary",
        now_utc=datetime(2026, 5, 29, 8, 30, tzinfo=timezone.utc),
    )


def _source_verification_report() -> dict:
    return {
        "ok": True,
        "verification_version": SOURCE_VERIFICATION_VERSION,
        "index_entry_version": SOURCE_INDEX_ENTRY_VERSION,
        "release_ref": "pr:775",
        "commit_sha": COMMIT_SHA,
        "ci_run_ref": "gh:run:decision-reference-review-summary-template-index-verifier",
        "operator_decision_reference_review": {
            "decision_reference": DECISION_REF,
            "expected_decision_reference": DECISION_REF,
            "decision_reference_verified": True,
            "decision_reference_is_approval": False,
            "decision_reference_is_release_decision": False,
            "decision_must_be_recorded_separately": True,
            "review_context_only": True,
            "manual_review_required": True,
        },
        "artifact_count_checked": 2,
        "digest_checks": _source_checks("match"),
        "size_checks": _source_checks("match"),
        "schema_version_checks": _source_checks("match"),
        "source_contract_check": "match",
        "rebuilt_index_entry_check": "match",
        "bridge_event_schema_check": "match",
        "template_only": True,
        "manual_review_required": True,
        "approval_granted": False,
        "release_decision_made": False,
        "automatic_release_decision": False,
        "direct_bridge_write_performed": False,
        "transport_added": False,
        "external_fetch_performed": False,
        "runtime_controls_added": False,
        "artifact_payloads_included": False,
        "local_paths_recorded": False,
        "blockers": [],
        "warnings": [],
    }


def _index_entry(artifacts: dict[str, dict]) -> dict:
    raw = _artifact_bytes(artifacts)
    return build_magma_decision_review_verification_template_index_entry_summary_bridge_event_template_index_entry(
        index_entry_verification_summary=artifacts["summary"],
        summary_bridge_event_template_report=artifacts["template"],
        index_entry_verification_summary_bytes=raw["summary"],
        summary_bridge_event_template_bytes=raw["template"],
        now_utc=datetime(2026, 5, 29, 8, 40, tzinfo=timezone.utc),
    )


def _source_checks(status: str) -> dict[str, str]:
    return {
        artifact_id: status
        for artifact_id in (
            "operator_decision_reference_review_bundle_verification_summary",
            "operator_decision_reference_review_bundle_verification_bridge_event_template",
        )
    }


def _artifact_bytes(artifacts: dict[str, dict]) -> dict[str, bytes]:
    return {
        artifact_id: _json_bytes(artifact)
        for artifact_id, artifact in artifacts.items()
    }


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True).encode("utf-8")


_REQUIRED_TEMPLATE_ARTIFACT_ID = (
    "operator_decision_reference_review_bundle_verification_bridge_event_template_"
    "index_entry_verification_summary_bridge_event_template"
)
