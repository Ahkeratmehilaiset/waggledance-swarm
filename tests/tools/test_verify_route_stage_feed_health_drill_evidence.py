# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import json

import tools.verify_route_stage_feed_health_drill_evidence as verifier


COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _feed_health() -> dict:
    return {
        "source": "prometheus_alertmanager_adapter",
        "status": "warning",
        "configured": True,
        "available": True,
        "cache_enabled": True,
        "cache_present": True,
        "cache_stale": True,
        "backoff_active": True,
        "cache_ttl_seconds": 30.0,
        "failure_backoff_seconds": 30.0,
        "timeout_seconds": 3.0,
        "max_response_bytes": 1000000.0,
        "last_response_bytes": 1200.0,
        "cache_hit_count": 1.0,
        "cache_miss_count": 2.0,
        "fetch_success_count": 3.0,
        "fetch_failure_count": 2.0,
        "backoff_skip_count": 1.0,
        "last_success_at": "2026-06-01T16:00:00Z",
        "last_failure_at": "2026-06-01T16:01:00Z",
        "last_failure_reason": "FEED_READ_FAILED",
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
    }


def _slo_panels(feed_health: dict) -> list[dict]:
    panels = []
    for panel in verifier.ROUTE_STAGE_LATENCY_FEED_SLO_PANELS:
        panel_id = str(panel["id"])
        panels.append({
            **panel,
            "current_value": verifier._route_stage_latency_feed_slo_current_value(
                panel_id,
                feed_health,
            ),
            "status": verifier._route_stage_latency_feed_slo_panel_status(
                panel_id,
                feed_health,
            ),
            "controls_present": False,
        })
    return panels


def _valid_package() -> dict:
    feed_health = _feed_health()
    return {
        "schema_version": verifier.PACKAGE_SCHEMA_VERSION,
        "commit": COMMIT,
        "collected_at_utc": "2026-06-01T16:02:00Z",
        "metrics_scrape": {
            "source": "/metrics",
            "fields": {
                "waggledance_route_stage_latency_feed_status": "warning",
                "waggledance_route_stage_latency_feed_failure_reason": (
                    "FEED_READ_FAILED"
                ),
                "waggledance_route_stage_latency_feed_backoff_active": 1,
            },
        },
        "api_ops": {
            "route_stage_latency": {
                "feed_state": {
                    "feed_health": feed_health,
                    "slo_panels": _slo_panels(feed_health),
                    "drill_evidence": verifier._route_stage_latency_feed_drill_evidence(),
                },
            },
        },
        "operator_log_window": {
            "timestamp": "2026-06-01T16:02:00Z",
            "commit": COMMIT,
            "sanitized_reason": "FEED_READ_FAILED",
        },
    }


def test_route_stage_feed_health_drill_evidence_verifier_accepts_package(
    tmp_path,
) -> None:
    package_path = tmp_path / "route-stage-drill.json"
    package_path.write_text(json.dumps(_valid_package()), encoding="utf-8")

    report = verifier.build_report(evidence_package=package_path)

    assert report["ok"] is True
    assert report["verified"] is True
    assert report["blockers"] == []
    assert report["checks"]["feed_health_contract_ok"] is True
    assert report["checks"]["slo_panels_contract_ok"] is True
    assert report["checks"]["drill_evidence_contract_ok"] is True
    assert report["network_access_performed"] is False
    assert report["controls_present"] is False
    assert report["runtime_authority_granted"] is False
    assert report["external_writes_applied"] is False
    assert report["evidence_package"] == "<redacted>"
    assert str(tmp_path) not in json.dumps(report)


def test_route_stage_feed_health_drill_evidence_verifier_blocks_authority_flags(
    tmp_path,
) -> None:
    package = _valid_package()
    feed_state = package["api_ops"]["route_stage_latency"]["feed_state"]
    feed_state["feed_health"]["runtime_authority_granted"] = True
    feed_state["feed_health"]["controls_present"] = "true"
    feed_state["slo_panels"][0]["controls_present"] = True
    feed_state["drill_evidence"]["external_writes_applied"] = True
    package_path = tmp_path / "route-stage-drill.json"
    package_path.write_text(json.dumps(package), encoding="utf-8")

    report = verifier.build_report(evidence_package=package_path)

    assert report["ok"] is False
    assert "feed_health_runtime_authority_granted_authority_flag_true" in (
        report["blockers"]
    )
    assert "feed_health_controls_present_authority_flag_true" in report["blockers"]
    assert "slo_panel_controls_present_authority_flag_true" in report["blockers"]
    assert "drill_evidence_contract_mismatch" in report["blockers"]
    assert "drill_evidence_external_writes_applied_authority_flag_true" in (
        report["blockers"]
    )


def test_route_stage_feed_health_drill_evidence_verifier_blocks_raw_url_and_path(
    tmp_path,
) -> None:
    package = _valid_package()
    package["operator_log_window"]["sanitized_reason"] = (
        "http://example.test/private?token=secret"
    )
    package["api_ops"]["route_stage_latency"]["feed_state"]["feed_health"][
        "source"
    ] = "C:\\private\\prometheus-token"
    package_path = tmp_path / "route-stage-drill.json"
    package_path.write_text(json.dumps(package), encoding="utf-8")

    report = verifier.build_report(evidence_package=package_path)
    encoded = json.dumps(report)

    assert report["ok"] is False
    assert "forbidden_raw_payload_value" in report["blockers"]
    assert "http://example.test" not in encoded
    assert "prometheus-token" not in encoded
    assert str(tmp_path) not in encoded


def test_route_stage_feed_health_drill_evidence_verifier_blocks_slo_drift(
    tmp_path,
) -> None:
    package = _valid_package()
    package["api_ops"]["route_stage_latency"]["feed_state"]["slo_panels"][1][
        "query"
    ] = "increase(waggledance_route_stage_latency_feed_fetch_failures_total[15m])"
    package_path = tmp_path / "route-stage-drill.json"
    package_path.write_text(json.dumps(package), encoding="utf-8")

    report = verifier.build_report(evidence_package=package_path)

    assert report["ok"] is False
    assert "slo_panel_template_mismatch" in report["blockers"]


def test_route_stage_feed_health_drill_evidence_verifier_blocks_raw_labels(
    tmp_path,
) -> None:
    package = _valid_package()
    package["metrics_scrape"]["fields"][
        "waggledance_route_stage_latency_feed_status"
    ] = {"labels": {"status": "warning"}, "value": 1}
    package_path = tmp_path / "route-stage-drill.json"
    package_path.write_text(json.dumps(package), encoding="utf-8")

    report = verifier.build_report(evidence_package=package_path)

    assert report["ok"] is False
    assert "metrics_scrape_field_value_invalid" in report["blockers"]
    assert "forbidden_raw_payload_key" in report["blockers"]


def test_route_stage_feed_health_drill_evidence_verifier_blocks_non_finite_json(
    tmp_path,
) -> None:
    package_path = tmp_path / "route-stage-drill.json"
    package_path.write_text('{"schema_version": NaN}', encoding="utf-8")

    report = verifier.build_report(evidence_package=package_path)

    assert report["ok"] is False
    assert report["blockers"] == ["evidence_package_invalid_json:ValueError"]
    assert report["evidence_package"] == "<redacted>"
    assert str(tmp_path) not in json.dumps(report)


def test_route_stage_feed_health_drill_evidence_verifier_cli_writes_report(
    tmp_path,
) -> None:
    package_path = tmp_path / "route-stage-drill.json"
    output = tmp_path / "verification.json"
    package_path.write_text(json.dumps(_valid_package()), encoding="utf-8")

    rc = verifier.main([
        "--evidence-package",
        str(package_path),
        "--output",
        str(output),
        "--json",
    ])

    report = json.loads(output.read_text(encoding="utf-8"))
    assert rc == 0
    assert report["ok"] is True
    assert str(tmp_path) not in json.dumps(report)
