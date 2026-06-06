from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import subprocess
import sys
from pathlib import Path

from tools.build_counterfactual_observability_guard_reviewer_summary import (
    PROOF_ID,
    SUMMARY_VERSION,
    build_counterfactual_observability_guard_reviewer_summary,
    render_counterfactual_observability_guard_reviewer_summary_markdown,
)
from tools.verify_counterfactual_observability_guard import (
    GUARD_SCHEMA_VERSION,
    verify_counterfactual_observability_artifact,
)
from waggledance.core.autonomy_growth.counterfactual_replay import (
    COUNTERFACTUAL_DELTA_SCHEMA,
)
from waggledance.core.magma.canonical import sha256_digest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT / "tools" / "build_counterfactual_observability_guard_reviewer_summary.py"
)
FIXED_NOW = datetime(2026, 6, 6, 6, 20, tzinfo=timezone.utc)


def _joined(*parts: str) -> str:
    return "".join(parts)


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


SENSITIVE_PATH_SEGMENT_FIXTURE = _chars(112, 114, 105, 118, 97, 116, 101)
SENSITIVE_TOKEN_PREFIX_FIXTURE = _chars(80, 82, 73, 86, 65, 84, 69, 95)
FORBIDDEN_PATH_PREFIX = _joined("C", ":", "/", SENSITIVE_PATH_SEGMENT_FIXTURE)
FORBIDDEN_REPORT_PATH = _joined(
    FORBIDDEN_PATH_PREFIX,
    "/",
    "counterfactual-guard-report.json",
)
FORBIDDEN_OUTPUT_SNIPPETS = (
    FORBIDDEN_PATH_PREFIX,
    SENSITIVE_TOKEN_PREFIX_FIXTURE,
    _joined(_chars(104, 116, 116, 112), ":", "/", "/"),
    _joined(_chars(104, 116, 116, 112, 115), ":", "/", "/"),
)


def _raw_delta() -> dict[str, object]:
    core: dict[str, object] = {
        "schema_version": COUNTERFACTUAL_DELTA_SCHEMA,
        "candidate_hash": "sha256:" + "a" * 64,
        "incumbent_hash": "sha256:" + "b" * 64,
        "sample_count": 24,
        "candidate_sample_set_digest": "sha256:" + "c" * 64,
        "incumbent_sample_set_digest": "sha256:" + "c" * 64,
        "oracle_kind": "formula_recompute",
        "deterministic": True,
        "divergence_count": 3,
        "no_delta": False,
        "per_arm": {
            "candidate": {"results": [{"inputs": {"fixture": "do-not-export"}}]},
            "incumbent": {"results": []},
        },
        "divergences": [{"candidate_output": "new", "incumbent_output": "old"}],
    }
    return {**core, "canonical_digest": sha256_digest(core)}


def _guard_report() -> dict[str, object]:
    return verify_counterfactual_observability_artifact(_raw_delta())


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_counterfactual_guard_reviewer_summary_renders_without_authority() -> None:
    report = _guard_report()

    summary = build_counterfactual_observability_guard_reviewer_summary(
        guard_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:counterfactual-observability-guard",
        now_utc=FIXED_NOW,
    )

    assert summary["proof_id"] == PROOF_ID
    assert summary["ok"] is True
    assert summary["summary_version"] == SUMMARY_VERSION
    assert summary["created_at_utc"] == "2026-06-06T06:20:00Z"
    guard = summary["counterfactual_observability_guard"]
    assert guard["guard_ok"] is True
    assert guard["guard_schema_version"] == GUARD_SCHEMA_VERSION
    assert guard["runtime_measured_claim_safe"] is True
    observability = summary["observability_summary"]
    assert observability["status"] == "runtime_measured"
    assert observability["a3_label"] == "RUNTIME_MEASURED"
    assert observability["sample_count"] == 24
    assert observability["divergence_count"] == 3
    boundary = summary["operator_boundary"]
    assert boundary["guard_report_boundary_ok"] is True
    assert boundary["boundary_blockers"] == []
    assert summary["manual_review_required"] is True
    assert summary["approval_granted"] is False
    assert summary["release_decision_made"] is False
    assert summary["automatic_release_decision"] is False
    assert summary["direct_bridge_write_performed"] is False
    assert summary["transport_added"] is False
    assert summary["external_fetch_performed"] is False
    assert summary["runtime_controls_added"] is False
    assert summary["controls_present"] is False
    assert summary["runtime_authority_granted"] is False
    assert summary["external_writes_applied"] is False
    assert summary["network_access_performed"] is False
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False
    rendered = json.dumps(summary, sort_keys=True)
    assert "per_arm" not in rendered
    assert "divergences" not in rendered
    assert "candidate_hash" not in rendered
    assert "do-not-export" not in rendered
    assert not any(marker in rendered for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_counterfactual_guard_reviewer_summary_markdown_is_path_free() -> None:
    summary = build_counterfactual_observability_guard_reviewer_summary(
        guard_report=_guard_report(),
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:counterfactual-observability-guard",
        now_utc=FIXED_NOW,
    )

    markdown = render_counterfactual_observability_guard_reviewer_summary_markdown(
        summary
    )

    assert "Counterfactual Observability Guard Reviewer Summary" in markdown
    assert "Approval granted: `false`" in markdown
    assert "Artifact payloads included: `false`" in markdown
    assert "Local paths recorded: `false`" in markdown
    assert not any(marker in markdown for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_counterfactual_guard_reviewer_summary_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "guard-report.json"
    report_path.write_text(json.dumps(_guard_report()), encoding="utf-8")

    result = _run(
        "--guard-report-json",
        str(report_path),
        "--reviewer-agent",
        "claude-rco-1",
        "--handoff-ref",
        "bridge:handoff:counterfactual-observability-guard",
        "--now",
        "2026-06-06T06:20:00Z",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["direct_bridge_write_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    assert report_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_counterfactual_guard_reviewer_summary_rejects_non_list_blockers() -> None:
    report = _guard_report()
    report["blockers"] = "not-a-list"

    summary = build_counterfactual_observability_guard_reviewer_summary(
        guard_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:counterfactual-observability-guard",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "guard_report_blockers_not_list" in summary["blockers"]
    assert summary["operator_boundary"]["guard_report_boundary_ok"] is False
    assert summary["approval_granted"] is False


def test_counterfactual_guard_reviewer_summary_rejects_unsafe_blocker_item() -> None:
    report = _guard_report()
    report["blockers"] = [FORBIDDEN_REPORT_PATH]

    summary = build_counterfactual_observability_guard_reviewer_summary(
        guard_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:counterfactual-observability-guard",
        now_utc=FIXED_NOW,
    )

    rendered = json.dumps(summary, sort_keys=True)
    assert summary["ok"] is False
    assert "guard_report_blockers_item_not_safe_token" in summary["blockers"]
    assert FORBIDDEN_REPORT_PATH not in rendered


def test_counterfactual_guard_reviewer_summary_rejects_authority_escalation() -> None:
    report = _guard_report()
    report["runtime_authority_granted"] = True

    summary = build_counterfactual_observability_guard_reviewer_summary(
        guard_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:counterfactual-observability-guard",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "guard_report_runtime_authority_granted_not_false" in summary["blockers"]
    assert "guard_report_nested_authority_field_not_false:runtime_authority_granted" in (
        summary["blockers"]
    )
    assert summary["runtime_authority_granted"] is False


def test_counterfactual_guard_reviewer_summary_rejects_reviewer_ownership_container() -> None:
    report = _guard_report()
    report["reviewer_ownership"] = {"manual_review_required": False}

    summary = build_counterfactual_observability_guard_reviewer_summary(
        guard_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:counterfactual-observability-guard",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "guard_report_forbidden_authority_container:reviewer_ownership" in (
        summary["blockers"]
    )
    assert summary["operator_boundary"]["guard_report_boundary_ok"] is False
    assert summary["automatic_release_decision"] is False


def test_counterfactual_guard_reviewer_summary_rejects_path_keys(
    tmp_path: Path,
) -> None:
    report = _guard_report()
    report["source_path"] = FORBIDDEN_REPORT_PATH

    result = _run(
        "--guard-report-json",
        _write_temp_json(tmp_path, "cfog-path-report.json", report),
        "--reviewer-agent",
        "claude-rco-1",
        "--handoff-ref",
        "bridge:handoff:counterfactual-observability-guard",
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["blockers"] == ["guard_report_forbidden_path_key:source_path"]
    assert FORBIDDEN_REPORT_PATH not in result.stdout


def test_counterfactual_guard_reviewer_summary_rejects_payload_field_export() -> None:
    report = _guard_report()
    observability = report["observability_summary"]
    assert isinstance(observability, dict)
    observability["payload_fields_exported"] = True

    summary = build_counterfactual_observability_guard_reviewer_summary(
        guard_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:counterfactual-observability-guard",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "guard_report_nested_authority_field_not_false:payload_fields_exported" in (
        summary["blockers"]
    )
    assert summary["observability_summary"]["payload_fields_exported"] is False


def test_counterfactual_guard_reviewer_summary_rejects_non_finite_json(
    tmp_path: Path,
) -> None:
    report_path = tmp_path / "non-finite-guard-report.json"
    report_path.write_text(
        '{"schema_version":"wd.counterfactual_observability_guard.v1","ok":NaN}',
        encoding="utf-8",
    )

    result = _run(
        "--guard-report-json",
        str(report_path),
        "--reviewer-agent",
        "claude-rco-1",
        "--handoff-ref",
        "bridge:handoff:counterfactual-observability-guard",
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["blockers"] == ["guard_report_json_invalid_or_non_finite"]
    assert str(tmp_path) not in result.stdout
    assert report_path.name not in result.stdout


def test_counterfactual_guard_reviewer_summary_rejects_claim_inconsistent_report() -> None:
    report = deepcopy(_guard_report())
    observability = report["observability_summary"]
    assert isinstance(observability, dict)
    observability["deterministic"] = False

    summary = build_counterfactual_observability_guard_reviewer_summary(
        guard_report=report,
        reviewer_agent_id="claude-rco-1",
        handoff_ref="bridge:handoff:counterfactual-observability-guard",
        now_utc=FIXED_NOW,
    )

    assert summary["ok"] is False
    assert "runtime_measured_claim_determinism_not_proven" in summary["blockers"]
    assert summary["release_decision_made"] is False


def _write_temp_json(directory: Path, name: str, payload: object) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return str(path)
