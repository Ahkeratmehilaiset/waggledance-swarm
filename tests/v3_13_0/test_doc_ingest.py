# SPDX-License-Identifier: BUSL-1.1
"""Tests for Sprint 2 DocIngest v1 local proposal contract."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from waggledance.core.v3_13_0.doc_ingest import (
    DocIngestError,
    build_doc_ingest_proposal,
)


ROOT = Path(__file__).resolve().parents[2]
SCH_005 = ROOT / "schemas" / "v3_13_0" / "solver_candidate_manifest.schema.json"


def _validate_manifest(seed: dict) -> None:
    schema = json.loads(SCH_005.read_text(encoding="utf-8"))
    errors = list(jsonschema.Draft7Validator(schema).iter_errors(seed))
    assert errors == [], [(error.message, list(error.path)) for error in errors]


def test_home_directory_builds_solver_candidate_proposal(tmp_path: Path) -> None:
    (tmp_path / "profile_config.yaml").write_text(
        "\n".join([
            "schema_version: 1",
            "profile_id: home_demo",
            "profile_kind: home",
            "country: FI",
        ]),
        encoding="utf-8",
    )
    (tmp_path / "tariff_structure.md").write_text("night tariff window", encoding="utf-8")
    (tmp_path / "consumption_sample.csv").write_text(
        "ts,kwh\n2026-05-13T00:00:00Z,1.25\n",
        encoding="utf-8",
    )

    proposal = build_doc_ingest_proposal(
        tmp_path,
        profile_kind="home",
        candidate_id="electricity_spot_optimizer_home_demo_001",
    )

    payload = proposal.to_event_payload()
    assert payload["event_type"] == "solver_candidate_proposal"
    assert "type" not in payload
    assert payload["profile_id"] == "home_demo"
    assert payload["profile_kind"] == "home"
    assert payload["source_docs"] == [
        "doc:consumption_sample",
        "doc:tariff_structure",
    ]
    assert payload["candidate_manifest_seed"]["activation_state"] == "unactivated"
    assert payload["candidate_manifest_seed"]["source_tools"] == []
    _validate_manifest(payload["candidate_manifest_seed"])


def test_cottage_directory_builds_profile_specific_defaults(tmp_path: Path) -> None:
    (tmp_path / "profile_config.json").write_text(
        json.dumps({
            "schema_version": 1,
            "profile_id": "cottage_demo",
            "profile_kind": "cottage",
            "country": "FI",
        }),
        encoding="utf-8",
    )
    (tmp_path / "thermal_model.yaml").write_text(
        "insulation: r3\nthermal_mass: medium\n",
        encoding="utf-8",
    )
    (tmp_path / "sensor_history.csv").write_text(
        "ts,inside_c,outside_c\n2026-05-13T00:00:00Z,8,-5\n",
        encoding="utf-8",
    )

    proposal = build_doc_ingest_proposal(
        tmp_path,
        profile_kind="cottage",
        candidate_id="frost_risk_predictor_cottage_demo_001",
    )

    seed = proposal.to_event_payload()["candidate_manifest_seed"]
    assert seed["training_contracts"] == ["ctr_date", "ctr_vector", "ctr_memory"]
    assert seed["connector_handles"] == ["conn:weather_forecast_public"]
    assert seed["shadow_inputs"] == [
        "synth_cold_snap_24h",
        "synth_thaw_24h",
        "synth_steady_freeze_72h",
    ]
    _validate_manifest(seed)


def test_missing_profile_config_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "tariff_structure.md").write_text("ok", encoding="utf-8")

    with pytest.raises(DocIngestError, match="profile_config"):
        build_doc_ingest_proposal(
            tmp_path,
            profile_kind="home",
            candidate_id="electricity_spot_optimizer_home_demo_001",
        )


def test_unsupported_suffix_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "profile_config.json").write_text(
        json.dumps({"profile_id": "home_demo", "profile_kind": "home"}),
        encoding="utf-8",
    )
    (tmp_path / "tariff_structure.exe").write_text("not a doc", encoding="utf-8")

    with pytest.raises(DocIngestError, match="unsupported suffix"):
        build_doc_ingest_proposal(
            tmp_path,
            profile_kind="home",
            candidate_id="electricity_spot_optimizer_home_demo_001",
        )


def test_nested_directories_are_not_scanned(tmp_path: Path) -> None:
    (tmp_path / "profile_config.json").write_text(
        json.dumps({"profile_id": "home_demo", "profile_kind": "home"}),
        encoding="utf-8",
    )
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "tariff_structure.md").write_text("hidden", encoding="utf-8")

    with pytest.raises(DocIngestError, match="nested directories"):
        build_doc_ingest_proposal(
            tmp_path,
            profile_kind="home",
            candidate_id="electricity_spot_optimizer_home_demo_001",
        )


def test_credential_like_content_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "profile_config.json").write_text(
        json.dumps({"profile_id": "home_demo", "profile_kind": "home"}),
        encoding="utf-8",
    )
    (tmp_path / "tariff_structure.md").write_text(
        'password = "supersecret"\n',
        encoding="utf-8",
    )

    with pytest.raises(DocIngestError, match="credential"):
        build_doc_ingest_proposal(
            tmp_path,
            profile_kind="home",
            candidate_id="electricity_spot_optimizer_home_demo_001",
        )


def test_duplicate_source_refs_fail_closed(tmp_path: Path) -> None:
    (tmp_path / "profile_config.json").write_text(
        json.dumps({"profile_id": "home_demo", "profile_kind": "home"}),
        encoding="utf-8",
    )
    (tmp_path / "tariff.md").write_text("one", encoding="utf-8")
    (tmp_path / "tariff.csv").write_text("two", encoding="utf-8")

    with pytest.raises(DocIngestError, match="duplicate source ref"):
        build_doc_ingest_proposal(
            tmp_path,
            profile_kind="home",
            candidate_id="electricity_spot_optimizer_home_demo_001",
        )


def test_profile_kind_mismatch_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "profile_config.json").write_text(
        json.dumps({"profile_id": "home_demo", "profile_kind": "home"}),
        encoding="utf-8",
    )
    (tmp_path / "tariff.md").write_text("ok", encoding="utf-8")

    with pytest.raises(DocIngestError, match="profile_kind mismatch"):
        build_doc_ingest_proposal(
            tmp_path,
            profile_kind="cottage",
            candidate_id="frost_risk_predictor_cottage_demo_001",
        )


def test_remote_dwelling_profile_kind_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "profile_config.json").write_text(
        json.dumps({"profile_id": "remote_demo", "profile_kind": "remote_dwelling"}),
        encoding="utf-8",
    )
    (tmp_path / "sensor_history.csv").write_text("ts,value\n1,2\n", encoding="utf-8")

    with pytest.raises(DocIngestError, match="unsupported profile_kind"):
        build_doc_ingest_proposal(
            tmp_path,
            profile_kind="remote_dwelling",
            candidate_id="remote_dwelling_demo_001",
        )


def test_profile_config_symlink_escape_fails_closed(tmp_path: Path) -> None:
    external_profile = tmp_path.parent / f"{tmp_path.name}_profile_config.json"
    external_profile.write_text(
        json.dumps({"profile_id": "home_demo", "profile_kind": "home"}),
        encoding="utf-8",
    )
    try:
        (tmp_path / "profile_config.json").symlink_to(external_profile)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"file symlinks are unavailable in this environment: {exc}")
    (tmp_path / "tariff.md").write_text("ok", encoding="utf-8")

    with pytest.raises(DocIngestError, match="escapes input_root"):
        build_doc_ingest_proposal(
            tmp_path,
            profile_kind="home",
            candidate_id="electricity_spot_optimizer_home_demo_001",
        )


def test_oversized_text_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "profile_config.json").write_text(
        json.dumps({"profile_id": "home_demo", "profile_kind": "home"}),
        encoding="utf-8",
    )
    (tmp_path / "tariff.md").write_text("x" * 200_001, encoding="utf-8")

    with pytest.raises(DocIngestError, match="too large"):
        build_doc_ingest_proposal(
            tmp_path,
            profile_kind="home",
            candidate_id="electricity_spot_optimizer_home_demo_001",
        )


def test_non_utf8_text_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "profile_config.json").write_text(
        json.dumps({"profile_id": "home_demo", "profile_kind": "home"}),
        encoding="utf-8",
    )
    (tmp_path / "tariff.csv").write_bytes(b"\xff\xfe\xfa")

    with pytest.raises(DocIngestError, match="UTF-8"):
        build_doc_ingest_proposal(
            tmp_path,
            profile_kind="home",
            candidate_id="electricity_spot_optimizer_home_demo_001",
        )
