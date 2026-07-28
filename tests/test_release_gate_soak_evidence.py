# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import datetime as dt
import json

import pytest

import tools.check_release_gate as release_gate
from tools.check_release_gate import evaluate_release_gate, main as gate_main


REAL_LOCAL_ARTIFACT_REVALIDATION = (
    release_gate._revalidate_local_artifact_evidence
)


@pytest.fixture(autouse=True)
def _stub_local_artifact_revalidation(monkeypatch) -> None:
    monkeypatch.setattr(
        release_gate,
        "_revalidate_local_artifact_evidence",
        lambda *args, **kwargs: {
            "verified": True,
            "reason": "verified",
            "mismatches": [],
        },
    )


def _valid_evidence() -> dict[str, object]:
    return {
        "schema_version": "waggledance.release_soak.v1",
        "collection_mode": "local_artifacts",
        "target_version": "v3.12.0",
        "commit": "4f49564bea93df5432238661e1daf21530915a16",
        "started_at_utc": "2026-05-10T00:00:00Z",
        "ended_at_utc": "2026-05-24T00:00:00Z",
        "duration_hours": 336,
        "result": "pass",
        "silent_failures": 0,
        "error_log_clean": True,
        "ci_status": "pass",
        "profile_s_smoke": "pass",
        "security_privacy_gate": "pass",
        "axis_a_regression": "pass",
        "axis_b_gate": "pass",
        "docker_stable_policy": "finalized",
        "release_notes_anti_claims": "pass",
    }


def test_current_release_readiness_holds_until_soak_end() -> None:
    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        today=dt.date(2026, 5, 12),
    )

    assert result["decision"] == "hold"
    assert "before_no_earlier_than_date" in result["blockers"]
    assert "soak_window_incomplete" in result["blockers"]
    assert "soak_evidence_missing" in result["blockers"]
    assert result["target_version"] == "v3.12.0"


def test_release_gate_passes_with_valid_soak_evidence_after_window(tmp_path) -> None:
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(json.dumps(_valid_evidence()), encoding="utf-8")

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 24),
        checked_at_utc=dt.datetime(2026, 5, 24, tzinfo=dt.UTC),
    )

    assert result["decision"] == "pass"
    assert result["blockers"] == []
    assert result["soak_window"]["required_hours"] == 336


def test_release_gate_rejects_manual_collection_mode(tmp_path) -> None:
    evidence = _valid_evidence()
    evidence["collection_mode"] = "manual"
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        checked_at_utc=dt.datetime(2026, 5, 24, tzinfo=dt.UTC),
    )

    assert result["decision"] == "hold"
    assert "soak_evidence_collection_mode_invalid" in result["blockers"]


def test_self_declared_local_mode_does_not_replace_artifact_proof(
    tmp_path,
    monkeypatch,
) -> None:
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(json.dumps(_valid_evidence()), encoding="utf-8")
    monkeypatch.setattr(
        release_gate,
        "_revalidate_local_artifact_evidence",
        REAL_LOCAL_ARTIFACT_REVALIDATION,
    )

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        checked_at_utc=dt.datetime(2026, 5, 24, tzinfo=dt.UTC),
        source_root=tmp_path,
    )

    assert result["decision"] == "hold"
    assert "soak_evidence_local_artifacts_not_verified" in result["blockers"]


@pytest.mark.parametrize("duration", [float("inf"), float("-inf"), True])
def test_release_gate_rejects_nonfinite_or_boolean_duration(
    tmp_path,
    duration,
) -> None:
    evidence = _valid_evidence()
    evidence["duration_hours"] = duration
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 24),
        checked_at_utc=dt.datetime(2026, 5, 24, tzinfo=dt.UTC),
    )

    assert result["decision"] == "hold"
    assert "soak_evidence_duration_lt_336h" in result["blockers"]


def test_release_gate_huge_json_integer_fails_closed(tmp_path) -> None:
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(
        '{"duration_hours":' + ("9" * 5000) + "}",
        encoding="utf-8",
    )

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        checked_at_utc=dt.datetime(2026, 5, 24, tzinfo=dt.UTC),
    )

    assert result["decision"] == "hold"
    assert "soak_evidence_unreadable:ValueError" in result["blockers"]


def test_release_gate_duplicate_json_key_fails_closed(tmp_path) -> None:
    encoded = json.dumps(_valid_evidence())
    encoded = encoded.replace(
        '"duration_hours": 336',
        '"duration_hours": 1, "duration_hours": 336',
    )
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(encoded, encoding="utf-8")

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 24),
        checked_at_utc=dt.datetime(2026, 5, 24, tzinfo=dt.UTC),
    )

    assert result["decision"] == "hold"
    assert "soak_evidence_unreadable:ValueError" in result["blockers"]


def test_release_gate_recomputes_elapsed_duration_from_timestamps(
    tmp_path,
) -> None:
    evidence = _valid_evidence()
    evidence.update({
        "started_at_utc": "2026-05-23T23:59:59Z",
        "ended_at_utc": "2026-05-24T00:00:00Z",
        "duration_hours": 336,
    })
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 24),
    )

    assert result["decision"] == "hold"
    assert "soak_evidence_elapsed_duration_lt_336h" in result["blockers"]
    assert "soak_evidence_duration_mismatch" in result["blockers"]
    assert "soak_evidence_started_after_required_soak_start" in result[
        "blockers"
    ]


def test_release_gate_rejects_soak_end_after_evaluation_date(
    tmp_path,
) -> None:
    evidence = _valid_evidence()
    evidence.update({
        "started_at_utc": "2099-01-01T00:00:00Z",
        "ended_at_utc": "2099-01-15T00:00:00Z",
    })
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        checked_at_utc=dt.datetime(2026, 5, 24, tzinfo=dt.UTC),
    )

    assert result["decision"] == "hold"
    assert "soak_evidence_ended_in_future" in result["blockers"]


@pytest.mark.parametrize(
    "ended_at_utc",
    [
        "2026-05-24T00:00:01Z",
        "2026-05-24T02:00:01+02:00",
    ],
)
def test_release_gate_rejects_same_day_or_offset_future_timestamp(
    tmp_path,
    ended_at_utc,
) -> None:
    evidence = _valid_evidence()
    evidence["ended_at_utc"] = ended_at_utc
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        checked_at_utc=dt.datetime(2026, 5, 24, tzinfo=dt.UTC),
    )

    assert result["decision"] == "hold"
    assert "soak_evidence_ended_in_future" in result["blockers"]


def test_release_gate_cli_honors_exact_checked_at_utc(
    tmp_path,
    capsys,
) -> None:
    evidence = _valid_evidence()
    evidence["ended_at_utc"] = "2026-05-24T02:00:01+02:00"
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    return_code = gate_main([
        "--release-readiness",
        "docs/release/RELEASE_READINESS.md",
        "--soak-evidence",
        str(evidence_path),
        "--checked-at-utc",
        "2026-05-24T00:00:00Z",
    ])
    result = json.loads(capsys.readouterr().out)

    assert return_code == 1
    assert result["decision"] == "hold"
    assert "soak_evidence_ended_in_future" in result["blockers"]


def test_release_gate_rejects_future_evaluation_override(tmp_path) -> None:
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(json.dumps(_valid_evidence()), encoding="utf-8")

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        checked_at_utc=dt.datetime(2999, 1, 1, tzinfo=dt.UTC),
    )

    assert result["decision"] == "hold"
    assert "checked_at_utc_in_future" in result["blockers"]


@pytest.mark.parametrize(
    ("started_at", "ended_at"),
    [
        (
            "0001-01-01T00:00:00+14:00",
            "2026-05-24T00:00:00Z",
        ),
        (
            "2026-05-10T00:00:00Z",
            "9999-12-31T23:59:59-14:00",
        ),
    ],
)
def test_release_gate_rejects_timestamp_normalization_overflow(
    tmp_path,
    started_at,
    ended_at,
) -> None:
    evidence = _valid_evidence()
    evidence["started_at_utc"] = started_at
    evidence["ended_at_utc"] = ended_at
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 24),
    )

    assert result["decision"] == "hold"
    assert (
        "soak_evidence_started_at_invalid" in result["blockers"]
        or "soak_evidence_ended_at_invalid" in result["blockers"]
    )


@pytest.mark.parametrize("silent_failures", [False, 0.0, -0.0])
def test_release_gate_requires_integer_zero_silent_failures(
    tmp_path,
    silent_failures,
) -> None:
    evidence = _valid_evidence()
    evidence["silent_failures"] = silent_failures
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 24),
    )

    assert result["decision"] == "hold"
    assert "soak_evidence_silent_failures_nonzero" in result["blockers"]


def test_release_gate_treats_commit_as_evidence_subject(tmp_path) -> None:
    evidence = _valid_evidence()
    evidence["commit"] = "1111111111111111111111111111111111111111"
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 24),
    )

    assert result["decision"] == "pass"
    assert result["blockers"] == []


@pytest.mark.parametrize(
    "commit",
    [True, 1, "A" * 40, "a" * 39, "g" * 40],
)
def test_release_gate_requires_canonical_subject_commit(
    tmp_path,
    commit,
) -> None:
    evidence = _valid_evidence()
    evidence["commit"] = commit
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 24),
        checked_at_utc=dt.datetime(2026, 5, 24, tzinfo=dt.UTC),
    )

    assert result["decision"] == "hold"
    assert "soak_evidence_commit_invalid" in result["blockers"]


def test_release_gate_redacts_unreadable_release_readiness_path(tmp_path) -> None:
    readiness_path = tmp_path / "missing_readiness_DO_NOT_LEAK.md"

    result = evaluate_release_gate(readiness_path=readiness_path)
    encoded = json.dumps(result)

    assert result["decision"] == "hold"
    assert result["blockers"] == [
        "release_readiness_unreadable:FileNotFoundError"
    ]
    assert str(readiness_path) not in encoded
    assert readiness_path.name not in encoded


def test_release_gate_redacts_unreadable_soak_evidence_path(tmp_path) -> None:
    evidence_path = tmp_path / "missing_soak_DO_NOT_LEAK.json"

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 24),
    )
    encoded = json.dumps(result)

    assert result["decision"] == "hold"
    assert "soak_evidence_unreadable:FileNotFoundError" in result["blockers"]
    assert result["soak_evidence_diagnostics"] == {
        "provided": True,
        "readable": False,
        "object": False,
    }
    assert str(evidence_path) not in encoded
    assert evidence_path.name not in encoded


def test_release_gate_rejects_partial_or_dirty_soak_evidence(tmp_path) -> None:
    evidence = _valid_evidence()
    evidence.update({
        "duration_hours": 12,
        "ended_at_utc": "2026-05-11T12:00:00Z",
        "silent_failures": 1,
        "error_log_clean": False,
        "docker_stable_policy": "draft",
        "axis_b_gate": "hold",
    })
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 24),
    )

    assert result["decision"] == "hold"
    assert "soak_evidence_duration_lt_336h" in result["blockers"]
    assert "soak_evidence_ended_before_required_soak_end" in result["blockers"]
    assert "soak_evidence_silent_failures_nonzero" in result["blockers"]
    assert "soak_evidence_error_log_not_clean" in result["blockers"]
    assert "soak_evidence_docker_policy_not_finalized" in result["blockers"]
    assert "soak_evidence_axis_b_gate_not_pass" in result["blockers"]
    diagnostics = result["soak_evidence_diagnostics"]
    assert diagnostics["provided"] is True
    assert diagnostics["readable"] is True
    assert diagnostics["object"] is True
    assert diagnostics["duration_hours"] == 12
    assert diagnostics["required_duration_hours"] == 336
    assert diagnostics["ended_at_date"] == "2026-05-11"
    assert diagnostics["required_soak_end"] == "2026-05-24"
    assert diagnostics["silent_failures"] == 1
    assert diagnostics["expected_silent_failures"] == 0
    assert diagnostics["error_log_clean"] is False
    assert diagnostics["expected_error_log_clean"] is True
    assert diagnostics["docker_stable_policy"] == "draft"
    assert diagnostics["expected_docker_stable_policy"] == "finalized"
    assert diagnostics["status_fields"]["axis_b_gate"] == {
        "actual": "hold",
        "expected": "pass",
    }


def test_release_gate_diagnostics_redact_unexpected_status_values(tmp_path) -> None:
    evidence = _valid_evidence()
    evidence["ci_status"] = {"token": "DO_NOT_LEAK"}
    evidence["axis_a_regression"] = "secret-status-DO_NOT_LEAK"
    evidence_path = tmp_path / "release_soak_evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = evaluate_release_gate(
        readiness_path="docs/release/RELEASE_READINESS.md",
        soak_evidence_path=evidence_path,
        today=dt.date(2026, 5, 24),
    )
    encoded = json.dumps(result)

    assert result["decision"] == "hold"
    assert result["soak_evidence_diagnostics"]["status_fields"]["ci_status"] == {
        "actual": "<redacted>",
        "expected": "pass",
    }
    assert result["soak_evidence_diagnostics"]["status_fields"][
        "axis_a_regression"
    ] == {
        "actual": "<redacted>",
        "expected": "pass",
    }
    assert "DO_NOT_LEAK" not in encoded
