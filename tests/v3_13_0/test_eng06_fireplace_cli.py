# SPDX-License-Identifier: BUSL-1.1
"""Tests for the offline ENG-06 fireplace summary CLI."""
from __future__ import annotations

import io
import json
from pathlib import Path

from waggledance.adapters.cli.eng06_fireplace import main, run_from_payload


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_INPUT = ROOT / "examples" / "eng06" / "burn_log_sample.json"


def _payload() -> dict:
    return json.loads(EXAMPLE_INPUT.read_text(encoding="utf-8"))


def test_sample_burn_log_matches_expected_summary() -> None:
    result = run_from_payload(_payload())

    assert result["case_id"] == "ENG-06__cottage_fireplace_advisor__cottage"
    assert result["result_marker"] == "OK"
    assert result["fire_event_count_30d"] == 4
    assert result["days_with_fire"] == 3
    assert result["average_chimney_temp_c"] == 89.6
    assert result["peak_chimney_temp_c"] == 176.8
    assert result["horizon_start_utc"] == "2026-01-01T00:00:00Z"
    assert result["horizon_end_utc"] == "2026-01-30T00:00:00Z"


def test_run_from_payload_defaults_horizon_to_first_and_last_row() -> None:
    payload = _payload()
    payload.pop("horizon_start_utc")
    payload.pop("horizon_end_utc")

    result = run_from_payload(payload)

    assert result["result_marker"] == "OK"
    assert result["horizon_start_utc"] == "2026-01-01T00:00:00Z"
    assert result["horizon_end_utc"] == "2026-01-30T00:00:00Z"


def test_main_prints_compact_json() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(["--input", str(EXAMPLE_INPUT)], stdout=stdout,
                     stderr=stderr)
    output = json.loads(stdout.getvalue())

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert output["result_marker"] == "OK"
    assert output["fire_event_count_30d"] == 4


def test_main_pretty_output(tmp_path: Path) -> None:
    input_path = tmp_path / "burn_log.json"
    input_path.write_text(json.dumps(_payload()), encoding="utf-8")
    stdout = io.StringIO()

    exit_code = main(["--input", str(input_path), "--pretty"], stdout=stdout)

    assert exit_code == 0
    assert "\n  " in stdout.getvalue()


def test_main_can_override_horizon_and_stale_threshold(tmp_path: Path) -> None:
    input_path = tmp_path / "burn_log.json"
    input_path.write_text(
        json.dumps({
            "burn_log": [
                {
                    "day_utc": "2026-01-11T00:00:00Z",
                    "fire_event_count": 2,
                    "peak_chimney_temp_c": 176.8,
                    "average_chimney_temp_c": 96.5,
                },
            ],
        }),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(
        [
            "--input", str(input_path),
            "--horizon-start-utc", "2026-01-11T00:00:00Z",
            "--horizon-end-utc", "2026-01-11T00:00:00Z",
            "--stale-threshold-hours", "24",
        ],
        stdout=stdout,
        stderr=stderr,
    )
    output = json.loads(stdout.getvalue())

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert output["result_marker"] == "OK"
    assert output["horizon_start_utc"] == "2026-01-11T00:00:00Z"
    assert output["horizon_end_utc"] == "2026-01-11T00:00:00Z"


def test_main_preserves_solver_refusal_marker(tmp_path: Path) -> None:
    payload = _payload()
    for row in payload["burn_log"]:
        row["fire_event_count"] = 0
    input_path = tmp_path / "burn_log.json"
    input_path.write_text(json.dumps(payload), encoding="utf-8")
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(["--input", str(input_path)], stdout=stdout,
                     stderr=stderr)
    output = json.loads(stdout.getvalue())

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert output["result_marker"] == "NO_FIRES_IN_HORIZON_REFUSED"
    assert output["refusal_reason"] == "no_fire_events_in_horizon"


def test_main_refuses_missing_input_file_with_json_error(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "missing.json"
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(["--input", str(input_path)], stdout=stdout,
                     stderr=stderr)
    output = json.loads(stderr.getvalue())

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert output["result_marker"] == "INVALID_INPUT_REFUSED"
    assert "No such file" in output["error"]


def test_main_refuses_malformed_json_with_json_error(tmp_path: Path) -> None:
    input_path = tmp_path / "burn_log.json"
    input_path.write_text("{bad-json", encoding="utf-8")
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(["--input", str(input_path)], stdout=stdout,
                     stderr=stderr)
    output = json.loads(stderr.getvalue())

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert output["result_marker"] == "INVALID_INPUT_REFUSED"
    assert "Expecting property name" in output["error"]


def test_main_refuses_non_object_input_with_json_error(tmp_path: Path) -> None:
    input_path = tmp_path / "burn_log.json"
    input_path.write_text(json.dumps([]), encoding="utf-8")
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(["--input", str(input_path)], stdout=stdout,
                     stderr=stderr)
    output = json.loads(stderr.getvalue())

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert output["result_marker"] == "INVALID_INPUT_REFUSED"
    assert output["error"] == "input JSON must be an object"


def test_main_refuses_missing_burn_log_with_json_error(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "burn_log.json"
    input_path.write_text(
        json.dumps({
            "horizon_start_utc": "2026-01-01T00:00:00Z",
            "horizon_end_utc": "2026-01-30T00:00:00Z",
        }),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(["--input", str(input_path)], stdout=stdout,
                     stderr=stderr)
    output = json.loads(stderr.getvalue())

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert output["result_marker"] == "INVALID_INPUT_REFUSED"
    assert output["error"] == "input JSON must contain burn_log list"


def test_main_refuses_non_list_burn_log_with_json_error(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "burn_log.json"
    input_path.write_text(
        json.dumps({
            "horizon_start_utc": "2026-01-01T00:00:00Z",
            "horizon_end_utc": "2026-01-30T00:00:00Z",
            "burn_log": {},
        }),
        encoding="utf-8",
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main(["--input", str(input_path)], stdout=stdout,
                     stderr=stderr)
    output = json.loads(stderr.getvalue())

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert output["result_marker"] == "INVALID_INPUT_REFUSED"
    assert output["error"] == "input JSON must contain burn_log list"
