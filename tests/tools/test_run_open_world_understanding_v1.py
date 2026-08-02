# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json

from tools.run_open_world_understanding_v1 import (
    CHECK_NAMES,
    CLAIM_GATES,
    CLAIM_LABEL,
    FIXED_NOW,
    REPORT_KEYS,
    REPORT_SCHEMA,
    SYNTHETIC_SECRET_MARKER,
    build_acceptance_report,
    main,
)
from waggledance.core.magma.canonical import sha256_digest


def test_acceptance_report_passes_every_named_invariant(tmp_path) -> None:
    report = build_acceptance_report(
        scratch_dir=tmp_path,
        generated_at_utc=FIXED_NOW,
    )

    assert report["ok"] is True
    assert report["schema_version"] == REPORT_SCHEMA
    assert report["claim_label"] == CLAIM_LABEL
    assert report["input_class"] == "synthetic_local_only"
    assert set(report["checks"]) == set(CHECK_NAMES)
    assert all(result is True for result in report["checks"].values())
    assert report["measurements"] == {
        "ledger_event_count": 11,
        "projection_ticket_count": 3,
        "raw_reveal_event_count": 2,
        "ring_cell_count": 7,
        "accepted_revision_signal_count": 3,
        "rejected_revision_signal_count": 2,
        "recovery_witness_count": 3,
    }


def test_acceptance_report_is_closed_raw_free_and_non_authoritative(tmp_path) -> None:
    report = build_acceptance_report(
        scratch_dir=tmp_path,
        generated_at_utc=FIXED_NOW,
    )
    serialized = json.dumps(report, sort_keys=True)

    assert set(report) == REPORT_KEYS
    assert SYNTHETIC_SECRET_MARKER not in serialized
    for forbidden in (
        '"value"',
        '"predicted_value"',
        '"expected_value"',
        '"commitment_nonce"',
        '"auth_tag"',
    ):
        assert forbidden not in serialized
    for gate in CLAIM_GATES:
        assert report[gate] is False


def test_acceptance_report_digest_rederives_exactly(tmp_path) -> None:
    report = build_acceptance_report(
        scratch_dir=tmp_path,
        generated_at_utc=FIXED_NOW,
    )
    core = {key: value for key, value in report.items() if key != "report_digest"}

    assert report["report_digest"] == sha256_digest(
        {"domain": "wd.open_world_understanding.acceptance.digest.v1", **core}
    )


def test_acceptance_report_is_deterministic_despite_fresh_nonces(tmp_path) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()

    first = build_acceptance_report(
        scratch_dir=first_dir,
        generated_at_utc=FIXED_NOW,
    )
    second = build_acceptance_report(
        scratch_dir=second_dir,
        generated_at_utc=FIXED_NOW,
    )

    assert first == second


def test_cli_json_and_out_file_match(tmp_path, capsys) -> None:
    out = tmp_path / "acceptance.json"

    result = main(
        [
            "--scratch-root",
            str(tmp_path),
            "--now",
            FIXED_NOW,
            "--out",
            str(out),
            "--json",
        ]
    )

    assert result == 0
    stdout_report = json.loads(capsys.readouterr().out)
    file_report = json.loads(out.read_text(encoding="utf-8"))
    assert stdout_report == file_report
    assert stdout_report["ok"] is True


def test_cli_human_summary_states_no_authority(tmp_path, capsys) -> None:
    result = main(
        ["--scratch-root", str(tmp_path), "--now", FIXED_NOW]
    )

    assert result == 0
    output = capsys.readouterr().out
    assert "PASS" in output
    assert "no authority granted" in output


def test_cli_rejects_noncanonical_time_and_missing_scratch_root(
    tmp_path, capsys
) -> None:
    assert main(["--now", "2026-08-02 12:00:00"]) == 2
    assert "invalid --now" in capsys.readouterr().err

    missing = tmp_path / "missing"
    assert main(["--scratch-root", str(missing)]) == 2
    assert "must be an existing directory" in capsys.readouterr().err
