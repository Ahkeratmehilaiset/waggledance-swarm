# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from tools.build_memory_palace_sample_projection import (
    build_memory_palace_sample_projection,
)
from tools.build_memory_palace_visualization_export import (
    build_memory_palace_visualization_export,
)
from tools.build_memory_palace_visualization_export_verification_summary import (
    SUMMARY_VERSION,
    build_memory_palace_visualization_export_verification_summary,
    render_markdown,
)
from tools.verify_memory_palace_visualization_export import (
    verify_memory_palace_visualization_export,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "tools"
    / "build_memory_palace_visualization_export_verification_summary.py"
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_summary_accepts_valid_verification_without_authority() -> None:
    summary = build_memory_palace_visualization_export_verification_summary(
        _valid_verification(),
    )

    assert summary["ok"] is True
    assert summary["summary_version"] == SUMMARY_VERSION
    assert summary["source_verification_ok"] is True
    assert set(summary["checks"].values()) == {"match"}
    assert summary["counts"] == {
        "node_count_checked": 6,
        "edge_count_checked": 5,
        "shortcut_edge_count_checked": 2,
    }
    assert set(summary["required_true_flags"].values()) == {True}
    assert set(summary["authority_boundary"].values()) == {False}
    assert summary["runtime_route_changed"] is False
    assert summary["storage_write_performed"] is False
    assert summary["bridge_append_performed"] is False
    assert summary["solver_call_performed"] is False
    assert summary["scheduler_enqueue_performed"] is False
    assert summary["promotion_performed"] is False
    assert summary["gate_skip_performed"] is False
    assert summary["network_access_performed"] is False
    assert summary["runtime_authority_granted"] is False
    assert summary["artifact_payloads_included"] is False
    assert summary["local_paths_recorded"] is False
    assert summary["blockers"] == []


def test_summary_rejects_source_failure_and_redacts_unsafe_blockers() -> None:
    verification = _valid_verification()
    verification["ok"] = False
    verification["artifact_payloads_included"] = True
    verification["blockers"] = [
        "guardrail_not_gate_skip_not_true",
        r"C:\operator\private\verification.json",
    ]

    summary = build_memory_palace_visualization_export_verification_summary(
        verification,
    )
    encoded = json.dumps(summary, sort_keys=True)

    assert summary["ok"] is False
    assert "source_verification_not_ok" in summary["blockers"]
    assert "artifact_payloads_included_not_false" in summary["blockers"]
    assert "source_verification_blockers_present" in summary["blockers"]
    assert "guardrail_not_gate_skip_not_true" in summary["blockers"]
    assert "unsafe_blocker_redacted" in summary["blockers"]
    assert summary["artifact_payloads_included"] is False
    assert r"C:\operator\private\verification.json" not in encoded
    assert "verification.json" not in encoded


def test_summary_rejects_source_export_not_ok() -> None:
    verification = _valid_verification()
    verification["source_export_ok"] = False

    summary = build_memory_palace_visualization_export_verification_summary(
        verification,
    )
    encoded = json.dumps(summary, sort_keys=True)

    assert summary["ok"] is False
    assert "source_export_not_ok" in summary["blockers"]
    assert '"source_export_ok"' not in encoded
    assert summary["runtime_authority_granted"] is False
    assert summary["gate_skip_performed"] is False


def test_summary_rejects_unexpected_source_payload_fields_without_echoing() -> None:
    for key in ("metadata", "source_refs", "selectors", "matched_values"):
        verification = _valid_verification()
        verification[key] = {"secret": "do not echo"}

        summary = build_memory_palace_visualization_export_verification_summary(
            verification,
        )
        encoded = json.dumps(summary, sort_keys=True)

        assert summary["ok"] is False, key
        assert "verification_unexpected_field_present" in summary["blockers"]
        assert key not in encoded
        assert "do not echo" not in encoded


def test_summary_fails_closed_on_missing_version() -> None:
    summary = build_memory_palace_visualization_export_verification_summary({})

    assert summary["ok"] is False
    assert summary["source_verification_version"] == ""
    assert "verification_version_mismatch" in summary["blockers"]
    assert "source_verification_not_ok" in summary["blockers"]
    assert summary["runtime_authority_granted"] is False


def test_summary_fails_closed_on_non_mapping_inputs() -> None:
    for unsafe in (None, [], ["x"], "not-json-object"):
        summary = build_memory_palace_visualization_export_verification_summary(
            unsafe,
        )

        assert summary["ok"] is False
        assert summary["blockers"] == [
            "memory_palace_visualization_export_verification_summary_failed:"
            "memory_palace_visualization_export_verification_not_object",
        ]
        assert summary["runtime_authority_granted"] is False


def test_render_markdown_reports_summary_without_release_decision() -> None:
    summary = build_memory_palace_visualization_export_verification_summary(
        _valid_verification(),
    )

    markdown = render_markdown(summary)

    assert "Memory Palace Visualization Export Verification Summary" in markdown
    assert "ok: `true`" in markdown
    assert "node_count_checked: `6`" in markdown
    assert "This does not dispatch a solver" in markdown


def test_cli_json_summarizes_verification_path_free(tmp_path: Path) -> None:
    verification_path = tmp_path / "verification.json"
    _write_json(verification_path, _valid_verification())

    result = _run("--verification-json", str(verification_path), "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["counts"]["edge_count_checked"] == 5
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    assert verification_path.name not in combined


def test_cli_rejects_duplicate_json_keys_path_free(tmp_path: Path) -> None:
    verification_path = tmp_path / "unsafe_verification.json"
    verification_path.write_text(
        '{"verification_version":"x","verification_version":"y"}',
        encoding="utf-8",
    )

    result = _run("--verification-json", str(verification_path), "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["blockers"] == [
        "memory_palace_visualization_export_verification_summary_failed:"
        "memory_palace_visualization_export_verification_json_error",
    ]
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    assert verification_path.name not in combined


def test_cli_rejects_non_finite_json_path_free(tmp_path: Path) -> None:
    verification_path = tmp_path / "nan_verification.json"
    verification_path.write_text(
        '{"verification_version":"x","ok":NaN}',
        encoding="utf-8",
    )

    result = _run("--verification-json", str(verification_path), "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["blockers"] == [
        "memory_palace_visualization_export_verification_summary_failed:"
        "memory_palace_visualization_export_verification_json_error",
    ]
    combined = result.stdout + result.stderr
    assert str(tmp_path) not in combined
    assert verification_path.name not in combined


def test_cli_missing_input_is_path_free() -> None:
    missing = Path("C:/operator/private/missing_verification.json")

    result = _run("--verification-json", str(missing), "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["blockers"] == [
        "memory_palace_visualization_export_verification_summary_failed:"
        "memory_palace_visualization_export_verification_unreadable",
    ]
    combined = result.stdout + result.stderr
    assert str(missing) not in combined
    assert "missing_verification.json" not in combined


def _valid_verification() -> dict[str, object]:
    export = build_memory_palace_visualization_export(
        build_memory_palace_sample_projection(),
    )
    return verify_memory_palace_visualization_export(export)


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
