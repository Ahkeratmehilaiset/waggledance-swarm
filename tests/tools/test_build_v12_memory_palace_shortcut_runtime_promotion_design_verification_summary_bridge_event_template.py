# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.build_v12_memory_palace_shortcut_runtime_promotion_design_verification_summary import (
    build_memory_palace_shortcut_runtime_promotion_design_verification_summary,
)
from tools.build_v12_memory_palace_shortcut_runtime_promotion_design_verification_summary_bridge_event_template import (
    EVENT_STATUS,
    SOURCE_SUMMARY_VERSION,
    TEMPLATE_VERSION,
    build_memory_palace_shortcut_runtime_promotion_design_verification_summary_bridge_event_template,
)
from tools.run_v12_memory_palace_shortcut_runtime_promotion_design import (
    build_memory_palace_shortcut_runtime_promotion_design,
)
from tools.verify_v12_memory_palace_shortcut_runtime_promotion_design import (
    verify_memory_palace_shortcut_runtime_promotion_design,
)
from waggledance.core.bridge_event_schema import validate_event


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "build_v12_memory_palace_shortcut_runtime_promotion_design_verification_summary_bridge_event_template.py"
)


def _joined(*parts: str) -> str:
    return "".join(parts)


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


SENSITIVE_PATH_SEGMENT_FIXTURE = _chars(112, 114, 105, 118, 97, 116, 101)
SENSITIVE_TOKEN_PREFIX_FIXTURE = _chars(80, 82, 73, 86, 65, 84, 69, 95)
FORBIDDEN_PATH_PREFIX = _joined("C", ":", "/", SENSITIVE_PATH_SEGMENT_FIXTURE)
FORBIDDEN_SUMMARY_PATH = _joined(
    FORBIDDEN_PATH_PREFIX,
    "/",
    "verification-summary.json",
)
FORBIDDEN_REPORT_PATH = _joined(FORBIDDEN_PATH_PREFIX, "/", "summary.json")
FORBIDDEN_OUTPUT_SNIPPETS = (
    FORBIDDEN_PATH_PREFIX,
    SENSITIVE_TOKEN_PREFIX_FIXTURE,
    _joined(_chars(104, 116, 116, 112), ":", "/", "/"),
    _joined(_chars(104, 116, 116, 112, 115), ":", "/", "/"),
)


def test_memory_palace_verification_summary_bridge_event_template_validates_schema() -> None:
    report = build_memory_palace_shortcut_runtime_promotion_design_verification_summary_bridge_event_template(
        summary=_valid_summary(),
        agent_id="codex-lead-1",
        task_id="codex-lead-1/memory-palace-verification-summary-template-20260608",
        to="operator,claude-rco-1,claude-rco-2,codex-tools-1",
        run_id="codex-lead-1-20260608T073000Z",
        session_id="codex-lead-1-20260608T073000Z",
        now_utc=datetime(2026, 6, 8, 7, 30, tzinfo=timezone.utc),
    )

    event = report["bridge_event_template"]
    validate_event(event)
    json.dumps(event, allow_nan=False)
    assert report["ok"] is True
    assert report["template_version"] == TEMPLATE_VERSION
    assert report["template_only"] is True
    assert report["manual_review_required"] is True
    assert report["operator_gate_required_for_runtime_promotion"] is True
    assert report["direct_bridge_write_performed"] is False
    assert report["bridge_append_performed"] is False
    assert report["scheduler_enqueue_performed"] is False
    assert report["gate_skip_performed"] is False
    assert report["artifact_payloads_included"] is False
    assert report["local_paths_recorded"] is False
    assert report["runtime_authority_granted"] is False
    assert event["type"] == "handoff"
    assert event["status"] == EVENT_STATUS
    assert event["paths"] == []
    assert event["write_scope"] == []
    assert event["cwd"] == "template_not_emitted"
    assert event["pid"] == 0

    payload = event["payload"]
    assert payload["schema_version"] == TEMPLATE_VERSION
    assert payload["source_summary_version"] == SOURCE_SUMMARY_VERSION
    assert payload["template_only"] is True
    assert payload["direct_bridge_write_performed"] is False
    assert payload["bridge_append_performed"] is False
    assert payload["scheduler_enqueue_performed"] is False
    assert payload["gate_skip_performed"] is False
    assert payload["transport_added"] is False
    assert payload["external_fetch_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert payload["approval_granted"] is False
    assert payload["release_decision_made"] is False
    assert payload["runtime_authority_granted"] is False
    summary = payload["memory_palace_runtime_promotion_design_verification_summary"]
    assert summary["summary_ok"] is True
    assert summary["source_verification_ok"] is True
    assert summary["payload_included"] is False
    assert summary["runtime_promotion_design_count_checked"] == 2
    assert summary["blocker_count"] == 0
    assert all(summary["checks"].values())
    assert all(summary["required_true_flags"].values())
    assert all(summary["authority_boundary"].values())


def test_memory_palace_verification_summary_bridge_event_template_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "verification-summary.json"
    summary_path.write_text(
        json.dumps(_valid_summary(), sort_keys=True),
        encoding="utf-8",
    )

    result = _run(
        "--verification-summary-json",
        str(summary_path),
        "--agent",
        "codex-lead-1",
        "--task-id",
        "codex-lead-1/memory-palace-verification-summary-template-20260608",
        "--to",
        "operator,claude-rco-1",
        "--run-id",
        "codex-lead-1-20260608T073000Z",
        "--session-id",
        "codex-lead-1-20260608T073000Z",
        "--now",
        "2026-06-08T07:30:00Z",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    event = payload["bridge_event_template"]
    validate_event(event)
    assert payload["direct_bridge_write_performed"] is False
    assert payload["approval_granted"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    assert summary_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_memory_palace_verification_summary_bridge_event_template_missing_input_is_path_free() -> None:
    result = _run(
        "--summary-json",
        FORBIDDEN_SUMMARY_PATH,
        "--agent",
        "codex-lead-1",
        "--task-id",
        "codex-lead-1/memory-palace-verification-summary-template-20260608",
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "memory_palace_shortcut_runtime_promotion_design_verification_summary_"
        "bridge_event_template_failed:verification_summary_unreadable"
    ]
    assert payload["direct_bridge_write_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert "verification-summary.json" not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_memory_palace_verification_summary_bridge_event_template_rejects_unsafe_bridge_fields() -> None:
    report = build_memory_palace_shortcut_runtime_promotion_design_verification_summary_bridge_event_template(
        summary=_valid_summary(),
        agent_id="Codex",
        task_id="codex-lead-1/memory-palace-verification-summary-template-20260608",
        to="operator,claude-rco-1",
    )

    assert report["ok"] is False
    assert report["blockers"] == [
        "memory_palace_shortcut_runtime_promotion_design_verification_summary_"
        "bridge_event_template_failed:agent_unsafe"
    ]
    assert report["direct_bridge_write_performed"] is False
    assert report["artifact_payloads_included"] is False


def test_memory_palace_verification_summary_bridge_event_template_blocks_unsafe_summary_contract() -> None:
    cases: tuple[tuple[str, Callable[[dict], None], str], ...] = (
        (
            "summary_not_ok",
            lambda summary: summary.__setitem__("ok", False),
            "verification_summary_not_ok",
        ),
        (
            "blocker_present",
            lambda summary: summary.__setitem__("blockers", ["not_safe"]),
            "verification_summary_blockers_present",
        ),
        (
            "blockers_string_not_sequence",
            lambda summary: summary.__setitem__("blockers", "not-a-list"),
            "verification_summary_blockers_invalid",
        ),
        (
            "blockers_non_string_entry",
            lambda summary: summary.__setitem__(
                "blockers",
                [{"unexpected": "non_string_blocker"}],
            ),
            "verification_summary_blockers_invalid",
        ),
        (
            "top_level_authority_flag",
            lambda summary: summary.__setitem__("runtime_authority_granted", True),
            "verification_summary_top_level_runtime_authority_granted_not_false",
        ),
        (
            "nested_authority_flag",
            lambda summary: summary["authority_boundary"].__setitem__(
                "gate_skip_performed",
                True,
            ),
            "verification_summary_gate_skip_performed_not_false",
        ),
        (
            "check_mismatch",
            lambda summary: summary["checks"].__setitem__(
                "guardrail_check",
                "mismatch",
            ),
            "verification_summary_check_guardrail_check_not_match",
        ),
        (
            "required_true_missing",
            lambda summary: summary["required_true_flags"].__setitem__(
                "manual_review_required",
                False,
            ),
            "verification_summary_manual_review_required_not_true",
        ),
        (
            "count_invalid",
            lambda summary: summary.__setitem__(
                "runtime_promotion_design_count_checked",
                -1,
            ),
            "verification_summary_design_count_invalid",
        ),
    )

    for _name, mutate, expected_reason in cases:
        summary = deepcopy(_valid_summary())
        mutate(summary)
        report = build_memory_palace_shortcut_runtime_promotion_design_verification_summary_bridge_event_template(
            summary=summary,
            agent_id="codex-lead-1",
            task_id="codex-lead-1/memory-palace-verification-summary-template-20260608",
            to="operator,claude-rco-1",
        )

        assert report["ok"] is False
        assert report["blockers"] == [
            "memory_palace_shortcut_runtime_promotion_design_verification_summary_"
            f"bridge_event_template_failed:{expected_reason}"
        ]
        assert report["direct_bridge_write_performed"] is False
        assert report["approval_granted"] is False
        assert report["artifact_payloads_included"] is False


def test_memory_palace_verification_summary_bridge_event_template_rejects_path_markers_path_free() -> None:
    summary = _valid_summary()
    summary["warnings"] = [FORBIDDEN_REPORT_PATH]

    report = build_memory_palace_shortcut_runtime_promotion_design_verification_summary_bridge_event_template(
        summary=summary,
        agent_id="codex-lead-1",
        task_id="codex-lead-1/memory-palace-verification-summary-template-20260608",
        to="operator,claude-rco-1",
    )
    encoded = json.dumps(report, sort_keys=True)

    assert report["ok"] is False
    assert report["blockers"] == [
        "memory_palace_shortcut_runtime_promotion_design_verification_summary_"
        "bridge_event_template_failed:verification_summary_not_path_free"
    ]
    assert "summary.json" not in encoded
    assert FORBIDDEN_PATH_PREFIX not in encoded


def test_memory_palace_verification_summary_bridge_event_template_rejects_duplicate_json_keys_path_free(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "unsafe-summary.json"
    summary_path.write_text(
        '{"summary_version":"x","summary_version":"y"}',
        encoding="utf-8",
    )

    result = _run(
        "--summary-json",
        str(summary_path),
        "--agent",
        "codex-lead-1",
        "--task-id",
        "codex-lead-1/memory-palace-verification-summary-template-20260608",
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "memory_palace_shortcut_runtime_promotion_design_verification_summary_"
        "bridge_event_template_failed:verification_summary_json_error"
    ]
    assert str(tmp_path) not in result.stdout
    assert summary_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_memory_palace_verification_summary_bridge_event_template_non_finite_json_is_path_free(
    tmp_path: Path,
) -> None:
    summary_path = tmp_path / "verification-summary.json"
    summary_path.write_text('{"summary_version": NaN}', encoding="utf-8")

    result = _run(
        "--summary-json",
        str(summary_path),
        "--agent",
        "codex-lead-1",
        "--task-id",
        "codex-lead-1/memory-palace-verification-summary-template-20260608",
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "memory_palace_shortcut_runtime_promotion_design_verification_summary_"
        "bridge_event_template_failed:verification_summary_json_error"
    ]
    assert str(tmp_path) not in result.stdout
    assert summary_path.name not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def _valid_summary() -> dict[str, object]:
    report = build_memory_palace_shortcut_runtime_promotion_design(
        now_utc=datetime(2026, 6, 8, 7, 30, tzinfo=timezone.utc),
    )
    verification = verify_memory_palace_shortcut_runtime_promotion_design(report)
    return build_memory_palace_shortcut_runtime_promotion_design_verification_summary(
        verification,
    )


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
