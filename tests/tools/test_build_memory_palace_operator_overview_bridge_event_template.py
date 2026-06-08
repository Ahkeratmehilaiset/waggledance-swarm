# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from tools.build_memory_palace_operator_overview import (
    OVERVIEW_VERSION,
    build_memory_palace_operator_overview,
)
from tools.build_memory_palace_operator_overview_bridge_event_template import (
    EVENT_STATUS,
    TEMPLATE_VERSION,
    build_memory_palace_operator_overview_bridge_event_template,
)
from tools.build_memory_palace_sample_projection import (
    SAMPLE_MEMORY_ID,
    build_memory_palace_sample_projection,
)
from waggledance.core.bridge_event_schema import validate_event


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "build_memory_palace_operator_overview_bridge_event_template.py"


def _joined(*parts: str) -> str:
    return "".join(parts)


def _chars(*codes: int) -> str:
    return "".join(chr(code) for code in codes)


SENSITIVE_PATH_SEGMENT_FIXTURE = _chars(112, 114, 105, 118, 97, 116, 101)
SENSITIVE_TOKEN_PREFIX_FIXTURE = _chars(80, 82, 73, 86, 65, 84, 69, 95)
FORBIDDEN_PATH_PREFIX = _joined("C", ":", "/", SENSITIVE_PATH_SEGMENT_FIXTURE)
FORBIDDEN_OVERVIEW_PATH = _joined(FORBIDDEN_PATH_PREFIX, "/", "overview.json")
FORBIDDEN_OUTPUT_SNIPPETS = (
    FORBIDDEN_PATH_PREFIX,
    SENSITIVE_TOKEN_PREFIX_FIXTURE,
    _joined(_chars(104, 116, 116, 112), ":", "/", "/"),
    _joined(_chars(104, 116, 116, 112, 115), ":", "/", "/"),
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_operator_overview_bridge_event_template_validates_schema() -> None:
    report = build_memory_palace_operator_overview_bridge_event_template(
        overview=_valid_overview(),
        agent_id="codex-lead-1",
        task_id=(
            "codex-lead-1/"
            "memory-palace-operator-overview-bridge-template-20260609"
        ),
        to="operator,codex-lead-1,codex-tools-1,claude-rco-1,claude-rco-2",
        run_id="codex-lead-1-20260609T010000Z",
        session_id="codex-lead-1-20260609T010000Z",
        now_utc=datetime(2026, 6, 9, 1, 0, tzinfo=timezone.utc),
    )

    event = report["bridge_event_template"]
    validate_event(event)
    json.dumps(event, allow_nan=False)
    assert report["ok"] is True
    assert report["template_version"] == TEMPLATE_VERSION
    assert report["template_only"] is True
    assert report["manual_review_required"] is True
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
    assert payload["source_overview_version"] == OVERVIEW_VERSION
    assert payload["template_only"] is True
    assert payload["manual_review_required"] is True
    assert payload["approval_granted"] is False
    assert payload["release_decision_made"] is False
    assert payload["direct_bridge_write_performed"] is False
    assert payload["bridge_append_performed"] is False
    assert payload["scheduler_enqueue_performed"] is False
    assert payload["gate_skip_performed"] is False
    assert payload["transport_added"] is False
    assert payload["external_fetch_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert payload["runtime_authority_granted"] is False

    overview = payload["memory_palace_operator_overview"]
    assert overview["overview_ok"] is True
    assert overview["memory_count"] == 1
    assert overview["hierarchy_node_count"] == 6
    assert overview["total_candidate_count"] == 2
    assert overview["shortcut_jump_summary"]["total_intermediate_hops_skipped"] == 4
    assert overview["shortcut_jump_summary"]["hop_reduction_ratio"] == 0.666667
    assert all(overview["shortcut_jump_summary"]["authority_boundary"].values())
    assert all(overview["authority_boundary"].values())
    assert all(overview["no_overclaim_guardrails"].values())
    encoded = json.dumps(event, sort_keys=True)
    assert SAMPLE_MEMORY_ID not in encoded
    assert "room.research.pathology" not in encoded
    assert "segmentation" not in encoded
    assert '"matched_values":' not in encoded


def test_operator_overview_bridge_event_template_cli_json_is_path_free(
    tmp_path: Path,
) -> None:
    overview_path = tmp_path / "overview.json"
    overview_path.write_text(
        json.dumps(_valid_overview(), sort_keys=True),
        encoding="utf-8",
    )

    result = _run(
        "--operator-overview-json",
        str(overview_path),
        "--agent",
        "codex-lead-1",
        "--task-id",
        (
            "codex-lead-1/"
            "memory-palace-operator-overview-bridge-template-20260609"
        ),
        "--to",
        "operator,codex-lead-1",
        "--run-id",
        "codex-lead-1-20260609T010000Z",
        "--session-id",
        "codex-lead-1-20260609T010000Z",
        "--now",
        "2026-06-09T01:00:00Z",
        "--json",
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    validate_event(payload["bridge_event_template"])
    assert payload["direct_bridge_write_performed"] is False
    assert payload["approval_granted"] is False
    assert payload["artifact_payloads_included"] is False
    assert payload["local_paths_recorded"] is False
    assert str(tmp_path) not in result.stdout
    assert overview_path.name not in result.stdout
    assert SAMPLE_MEMORY_ID not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_operator_overview_bridge_event_template_missing_input_is_path_free() -> None:
    result = _run(
        "--overview-json",
        FORBIDDEN_OVERVIEW_PATH,
        "--agent",
        "codex-lead-1",
        "--task-id",
        (
            "codex-lead-1/"
            "memory-palace-operator-overview-bridge-template-20260609"
        ),
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "memory_palace_operator_overview_bridge_event_template_failed:"
        "operator_overview_unreadable"
    ]
    assert payload["direct_bridge_write_performed"] is False
    assert payload["artifact_payloads_included"] is False
    assert "overview.json" not in result.stdout
    assert not any(marker in result.stdout for marker in FORBIDDEN_OUTPUT_SNIPPETS)


def test_operator_overview_bridge_event_template_rejects_unsafe_bridge_fields() -> None:
    report = build_memory_palace_operator_overview_bridge_event_template(
        overview=_valid_overview(),
        agent_id="Codex",
        task_id=(
            "codex-lead-1/"
            "memory-palace-operator-overview-bridge-template-20260609"
        ),
        to="operator,codex-lead-1",
    )

    assert report["ok"] is False
    assert report["blockers"] == [
        "memory_palace_operator_overview_bridge_event_template_failed:agent_unsafe"
    ]
    assert report["direct_bridge_write_performed"] is False
    assert report["artifact_payloads_included"] is False


def test_operator_overview_bridge_event_template_blocks_unsafe_contract() -> None:
    cases: tuple[tuple[str, Callable[[dict], None], str], ...] = (
        (
            "overview_not_ok",
            lambda overview: overview.__setitem__("ok", False),
            "operator_overview_not_ok",
        ),
        (
            "blocker_present",
            lambda overview: overview.__setitem__("blockers", ["not_safe"]),
            "operator_overview_blockers_present",
        ),
        (
            "source_of_truth",
            lambda overview: overview.__setitem__("source_of_truth", "storage"),
            "operator_overview_source_of_truth_mismatch",
        ),
        (
            "top_level_authority_flag",
            lambda overview: overview["authority_boundary"].__setitem__(
                "runtime_authority_granted",
                True,
            ),
            "operator_overview_runtime_authority_granted_not_false",
        ),
        (
            "shortcut_authority_flag",
            lambda overview: overview["aggregate"]["shortcut_jump_summary"][
                "authority_boundary"
            ].__setitem__("gate_skip_performed", True),
            "operator_overview_shortcut_gate_skip_performed_not_false",
        ),
        (
            "guardrail_drift",
            lambda overview: overview["no_overclaim_guardrails"].__setitem__(
                "not_bridge_append",
                False,
            ),
            "operator_overview_not_bridge_append_not_true",
        ),
        (
            "candidate_count_mismatch",
            lambda overview: overview["aggregate"].__setitem__(
                "total_candidate_count",
                99,
            ),
            "operator_overview_candidate_count_mismatch",
        ),
    )

    for _name, mutate, expected_reason in cases:
        overview = _valid_overview()
        mutate(overview)
        report = build_memory_palace_operator_overview_bridge_event_template(
            overview=overview,
            agent_id="codex-lead-1",
            task_id=(
                "codex-lead-1/"
                "memory-palace-operator-overview-bridge-template-20260609"
            ),
            to="operator,codex-lead-1",
        )

        assert report["ok"] is False
        assert report["blockers"] == [
            "memory_palace_operator_overview_bridge_event_template_failed:"
            f"{expected_reason}"
        ]
        assert report["direct_bridge_write_performed"] is False
        assert report["approval_granted"] is False
        assert report["artifact_payloads_included"] is False


def test_operator_overview_bridge_event_template_rejects_payload_fields_without_echo() -> None:
    cases = (
        ("metadata", {"value": "do not echo"}),
        ("payload", {"value": "do not echo"}),
        ("source_refs", [{"value": "do not echo"}]),
        ("approval_granted", True),
        ("runtime_route_changed", True),
    )

    for key, value in cases:
        overview = _valid_overview()
        overview[key] = value

        report = build_memory_palace_operator_overview_bridge_event_template(
            overview=overview,
            agent_id="codex-lead-1",
            task_id=(
                "codex-lead-1/"
                "memory-palace-operator-overview-bridge-template-20260609"
            ),
            to="operator,codex-lead-1",
        )
        encoded = json.dumps(report, sort_keys=True)

        assert report["ok"] is False, key
        assert "do not echo" not in encoded
        assert "overview.json" not in encoded
        assert FORBIDDEN_PATH_PREFIX not in encoded
        assert report["direct_bridge_write_performed"] is False
        assert report["approval_granted"] is False
        assert report["artifact_payloads_included"] is False


def test_operator_overview_bridge_event_template_rejects_path_markers_path_free() -> None:
    overview = _valid_overview()
    overview["operator_interpretation"] = FORBIDDEN_OVERVIEW_PATH

    report = build_memory_palace_operator_overview_bridge_event_template(
        overview=overview,
        agent_id="codex-lead-1",
        task_id=(
            "codex-lead-1/"
            "memory-palace-operator-overview-bridge-template-20260609"
        ),
        to="operator,codex-lead-1",
    )
    encoded = json.dumps(report, sort_keys=True)

    assert report["ok"] is False
    assert report["blockers"] == [
        "memory_palace_operator_overview_bridge_event_template_failed:"
        "operator_overview_not_path_free"
    ]
    assert "overview.json" not in encoded
    assert FORBIDDEN_PATH_PREFIX not in encoded


def test_operator_overview_bridge_event_template_rejects_nested_payload_path_url_keys() -> None:
    cases: tuple[tuple[str, Callable[[dict], None]], ...] = (
        (
            "aggregate_raw_payload",
            lambda overview: overview["aggregate"].__setitem__(
                "raw_payload",
                {"value": "do not echo"},
            ),
        ),
        (
            "aggregate_source_path",
            lambda overview: overview["aggregate"].__setitem__(
                "source_path",
                "safe-looking-but-forbidden",
            ),
        ),
        (
            "hierarchy_url",
            lambda overview: overview["hierarchy"].__setitem__(
                "url",
                "safe-looking-but-forbidden",
            ),
        ),
    )

    for _name, mutate in cases:
        overview = _valid_overview()
        mutate(overview)

        report = build_memory_palace_operator_overview_bridge_event_template(
            overview=overview,
            agent_id="codex-lead-1",
            task_id=(
                "codex-lead-1/"
                "memory-palace-operator-overview-bridge-template-20260609"
            ),
            to="operator,codex-lead-1",
        )
        encoded = json.dumps(report, sort_keys=True)

        assert report["ok"] is False
        assert report["blockers"] == [
            "memory_palace_operator_overview_bridge_event_template_failed:"
            "operator_overview_not_path_free"
        ]
        assert "do not echo" not in encoded
        assert "safe-looking-but-forbidden" not in encoded
        assert report["direct_bridge_write_performed"] is False
        assert report["artifact_payloads_included"] is False
        assert report["local_paths_recorded"] is False


def test_operator_overview_bridge_event_template_rejects_duplicate_json_keys_path_free(
    tmp_path: Path,
) -> None:
    overview_path = tmp_path / "unsafe-overview.json"
    overview_path.write_text(
        '{"overview_version":"x","overview_version":"y"}',
        encoding="utf-8",
    )

    result = _run(
        "--overview-json",
        str(overview_path),
        "--agent",
        "codex-lead-1",
        "--task-id",
        (
            "codex-lead-1/"
            "memory-palace-operator-overview-bridge-template-20260609"
        ),
        "--json",
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "memory_palace_operator_overview_bridge_event_template_failed:"
        "operator_overview_json_error"
    ]
    assert "unsafe-overview.json" not in result.stdout
    assert str(tmp_path) not in result.stdout


def _valid_overview() -> dict[str, object]:
    return deepcopy(
        build_memory_palace_operator_overview(
            build_memory_palace_sample_projection(),
            [SAMPLE_MEMORY_ID],
        )
    )
