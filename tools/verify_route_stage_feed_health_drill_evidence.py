#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify a local route-stage feed-health drill evidence package."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from waggledance.adapters.http.routes.compat_dashboard import (  # noqa: E402
    ROUTE_STAGE_LATENCY_FEED_HEALTH_REASONS,
    ROUTE_STAGE_LATENCY_FEED_SLO_PANELS,
    _route_stage_latency_feed_drill_evidence,
    _route_stage_latency_feed_slo_current_value,
    _route_stage_latency_feed_slo_panel_status,
)

PACKAGE_SCHEMA_VERSION = "waggledance.route_stage_feed_health_drill_evidence.v1"
VERIFICATION_SCHEMA_VERSION = (
    "waggledance.route_stage_feed_health_drill_evidence_verification.v1"
)

REQUIRED_METRICS_FIELDS = (
    "waggledance_route_stage_latency_feed_status",
    "waggledance_route_stage_latency_feed_failure_reason",
    "waggledance_route_stage_latency_feed_backoff_active",
)
REQUIRED_API_OPS_FIELDS = (
    "route_stage_latency.feed_state.feed_health",
    "route_stage_latency.feed_state.slo_panels",
    "route_stage_latency.feed_state.drill_evidence",
)
AUTHORITY_FALSE_FIELDS = (
    "controls_present",
    "runtime_authority_granted",
    "external_writes_applied",
)

_TOP_LEVEL_FIELDS = {
    "schema_version",
    "commit",
    "collected_at_utc",
    "metrics_scrape",
    "api_ops",
    "operator_log_window",
}
_METRICS_SCRAPE_FIELDS = {"source", "fields"}
_API_OPS_FIELDS = {"route_stage_latency"}
_ROUTE_STAGE_LATENCY_FIELDS = {"feed_state"}
_FEED_STATE_FIELDS = {"feed_health", "slo_panels", "drill_evidence"}
_OPERATOR_LOG_WINDOW_FIELDS = {"timestamp", "commit", "sanitized_reason"}
_FEED_HEALTH_FIELDS = {
    "source",
    "status",
    "configured",
    "available",
    "cache_enabled",
    "cache_present",
    "cache_stale",
    "backoff_active",
    "cache_ttl_seconds",
    "failure_backoff_seconds",
    "timeout_seconds",
    "max_response_bytes",
    "last_response_bytes",
    "cache_hit_count",
    "cache_miss_count",
    "fetch_success_count",
    "fetch_failure_count",
    "backoff_skip_count",
    "last_success_at",
    "last_failure_at",
    "last_failure_reason",
    "controls_present",
    "runtime_authority_granted",
    "external_writes_applied",
}
_FEED_HEALTH_BOOL_FIELDS = {
    "configured",
    "available",
    "cache_enabled",
    "cache_present",
    "cache_stale",
    "backoff_active",
    "controls_present",
    "runtime_authority_granted",
    "external_writes_applied",
}
_FEED_HEALTH_NUMBER_FIELDS = {
    "cache_ttl_seconds",
    "failure_backoff_seconds",
    "timeout_seconds",
    "max_response_bytes",
    "last_response_bytes",
    "cache_hit_count",
    "cache_miss_count",
    "fetch_success_count",
    "fetch_failure_count",
    "backoff_skip_count",
}
_FEED_STATUSES = {"not_configured", "nominal", "warning"}
_PANEL_STATUSES = {"not_configured", "nominal", "warning"}
_ALLOWED_FAILURE_REASONS = set(ROUTE_STAGE_LATENCY_FEED_HEALTH_REASONS) | {
    "none"
}


class DrillEvidenceVerificationError(ValueError):
    """Raised when an evidence package cannot be read safely."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-package", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_report(evidence_package=args.evidence_package)
    encoded = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    if args.json or report["ok"]:
        print(encoded, end="")
    else:
        print(
            "route-stage feed-health drill evidence verification FAILED: "
            + ", ".join(report["blockers"]),
            file=sys.stderr,
        )
    return 0 if report["ok"] else 1


def build_report(*, evidence_package: Path | str) -> dict[str, Any]:
    evidence_path = Path(evidence_package)
    try:
        payload_bytes, package = _load_json_artifact(evidence_path)
    except DrillEvidenceVerificationError as exc:
        return _failure_report(exc.code)

    report = verify_route_stage_feed_health_drill_evidence(package)
    report["evidence_package"] = "<redacted>"
    report["evidence_sha256"] = hashlib.sha256(payload_bytes).hexdigest()
    report["evidence_size_bytes"] = len(payload_bytes)
    return report


def verify_route_stage_feed_health_drill_evidence(
    package: Mapping[str, Any],
) -> dict[str, Any]:
    blockers: list[str] = []
    checks = {
        "schema_version_ok": False,
        "top_level_contract_ok": False,
        "metrics_scrape_contract_ok": False,
        "api_ops_contract_ok": False,
        "feed_health_contract_ok": False,
        "slo_panels_contract_ok": False,
        "drill_evidence_contract_ok": False,
        "operator_log_window_contract_ok": False,
        "authority_guardrails_ok": False,
        "no_forbidden_raw_payload": False,
        "offline_only": True,
    }

    if not isinstance(package, Mapping):
        _append_once(blockers, "evidence_package_not_object")
        return _verification_report(blockers, checks, None)

    checks["schema_version_ok"] = package.get("schema_version") == PACKAGE_SCHEMA_VERSION
    if not checks["schema_version_ok"]:
        _append_once(blockers, "schema_version_mismatch")

    top_level_ok = _validate_exact_keys(
        package,
        _TOP_LEVEL_FIELDS,
        "top_level",
        blockers,
    )
    checks["top_level_contract_ok"] = top_level_ok
    commit = package.get("commit")
    if not _is_commit_ref(commit):
        _append_once(blockers, "commit_invalid")
    if not _is_timestamp(package.get("collected_at_utc")):
        _append_once(blockers, "collected_at_utc_invalid")

    metrics_ok = _validate_metrics_scrape(package.get("metrics_scrape"), blockers)
    checks["metrics_scrape_contract_ok"] = metrics_ok

    api_ops = package.get("api_ops")
    feed_state = _feed_state_from_api_ops(api_ops, blockers)
    checks["api_ops_contract_ok"] = feed_state is not None
    feed_health: Mapping[str, Any] | None = None
    if feed_state is not None:
        feed_health_value = feed_state.get("feed_health")
        if isinstance(feed_health_value, Mapping):
            feed_health = feed_health_value
        checks["feed_health_contract_ok"] = _validate_feed_health(
            feed_health_value,
            blockers,
        )
        checks["slo_panels_contract_ok"] = _validate_slo_panels(
            feed_state.get("slo_panels"),
            feed_health if feed_health is not None else {},
            blockers,
        )
        checks["drill_evidence_contract_ok"] = _validate_drill_evidence(
            feed_state.get("drill_evidence"),
            blockers,
        )

    checks["operator_log_window_contract_ok"] = _validate_operator_log_window(
        package.get("operator_log_window"),
        commit,
        blockers,
    )
    checks["authority_guardrails_ok"] = not any(
        blocker.endswith("_authority_flag_true") for blocker in blockers
    )
    checks["no_forbidden_raw_payload"] = _validate_no_forbidden_raw_payload(
        package,
        blockers,
    )
    return _verification_report(blockers, checks, package.get("schema_version"))


def _verification_report(
    blockers: list[str],
    checks: Mapping[str, bool],
    package_schema_version: object,
) -> dict[str, Any]:
    ok = not blockers and all(checks.values())
    return {
        "schema_version": VERIFICATION_SCHEMA_VERSION,
        "ok": ok,
        "verified": ok,
        "blockers": blockers,
        "warnings": [],
        "package_schema_version": (
            package_schema_version if isinstance(package_schema_version, str) else None
        ),
        "checks": dict(checks),
        "required_artifacts": {
            "metrics_scrape": list(REQUIRED_METRICS_FIELDS),
            "api_ops": list(REQUIRED_API_OPS_FIELDS),
            "operator_log_window": ["timestamp", "commit", "sanitized_reason"],
        },
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
        "network_access_performed": False,
    }


def _failure_report(blocker: str) -> dict[str, Any]:
    checks = {
        "schema_version_ok": False,
        "top_level_contract_ok": False,
        "metrics_scrape_contract_ok": False,
        "api_ops_contract_ok": False,
        "feed_health_contract_ok": False,
        "slo_panels_contract_ok": False,
        "drill_evidence_contract_ok": False,
        "operator_log_window_contract_ok": False,
        "authority_guardrails_ok": False,
        "no_forbidden_raw_payload": False,
        "offline_only": True,
    }
    report = _verification_report([blocker], checks, None)
    report["evidence_package"] = "<redacted>"
    report["evidence_sha256"] = None
    report["evidence_size_bytes"] = 0
    return report


def _load_json_artifact(path: Path) -> tuple[bytes, Mapping[str, Any]]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise DrillEvidenceVerificationError(
            f"evidence_package_unreadable:{exc.__class__.__name__}"
        ) from exc
    try:
        loaded = json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise DrillEvidenceVerificationError(
            f"evidence_package_invalid_json:{exc.__class__.__name__}"
        ) from exc
    if not isinstance(loaded, Mapping):
        raise DrillEvidenceVerificationError("evidence_package_not_object")
    return payload, loaded


def _reject_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non_finite_json_constant:{value}")


def _validate_metrics_scrape(value: object, blockers: list[str]) -> bool:
    if not isinstance(value, Mapping):
        _append_once(blockers, "metrics_scrape_not_object")
        return False
    ok = _validate_exact_keys(
        value,
        _METRICS_SCRAPE_FIELDS,
        "metrics_scrape",
        blockers,
    )
    if value.get("source") != "/metrics":
        _append_once(blockers, "metrics_scrape_source_mismatch")
        ok = False
    fields = value.get("fields")
    if not isinstance(fields, Mapping):
        _append_once(blockers, "metrics_scrape_fields_not_object")
        return False
    ok = _validate_exact_keys(
        fields,
        set(REQUIRED_METRICS_FIELDS),
        "metrics_scrape_fields",
        blockers,
    ) and ok
    for metric_name in REQUIRED_METRICS_FIELDS:
        if metric_name in fields and not _is_safe_metric_field_value(fields[metric_name]):
            _append_once(blockers, "metrics_scrape_field_value_invalid")
            ok = False
    return ok


def _feed_state_from_api_ops(
    value: object,
    blockers: list[str],
) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        _append_once(blockers, "api_ops_not_object")
        return None
    ok = _validate_exact_keys(value, _API_OPS_FIELDS, "api_ops", blockers)
    route_stage_latency = value.get("route_stage_latency")
    if not isinstance(route_stage_latency, Mapping):
        _append_once(blockers, "route_stage_latency_not_object")
        return None
    ok = _validate_exact_keys(
        route_stage_latency,
        _ROUTE_STAGE_LATENCY_FIELDS,
        "route_stage_latency",
        blockers,
    ) and ok
    feed_state = route_stage_latency.get("feed_state")
    if not isinstance(feed_state, Mapping):
        _append_once(blockers, "feed_state_not_object")
        return None
    ok = _validate_exact_keys(feed_state, _FEED_STATE_FIELDS, "feed_state", blockers) and ok
    return feed_state if ok else feed_state


def _validate_feed_health(value: object, blockers: list[str]) -> bool:
    if not isinstance(value, Mapping):
        _append_once(blockers, "feed_health_not_object")
        return False
    ok = _validate_exact_keys(value, _FEED_HEALTH_FIELDS, "feed_health", blockers)
    if value.get("status") not in _FEED_STATUSES:
        _append_once(blockers, "feed_health_status_invalid")
        ok = False
    for field in _FEED_HEALTH_BOOL_FIELDS:
        if not isinstance(value.get(field), bool):
            _append_once(blockers, "feed_health_bool_field_invalid")
            ok = False
    for field in _FEED_HEALTH_NUMBER_FIELDS:
        if not _is_nonnegative_finite_number(value.get(field)):
            _append_once(blockers, "feed_health_number_field_invalid")
            ok = False
    for field in ("last_success_at", "last_failure_at"):
        raw = value.get(field)
        if raw is not None and not _is_timestamp(raw):
            _append_once(blockers, "feed_health_timestamp_field_invalid")
            ok = False
    reason = value.get("last_failure_reason")
    if reason is not None and reason not in _ALLOWED_FAILURE_REASONS:
        _append_once(blockers, "feed_health_last_failure_reason_invalid")
        ok = False
    for field in AUTHORITY_FALSE_FIELDS:
        if value.get(field) is not False:
            _append_once(blockers, f"feed_health_{field}_authority_flag_true")
            ok = False
    return ok


def _validate_slo_panels(
    value: object,
    feed_health: Mapping[str, Any],
    blockers: list[str],
) -> bool:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        _append_once(blockers, "slo_panels_not_array")
        return False
    if len(value) != len(ROUTE_STAGE_LATENCY_FEED_SLO_PANELS):
        _append_once(blockers, "slo_panels_count_mismatch")
        return False
    ok = True
    expected_fields = {
        "id",
        "title",
        "metric",
        "query",
        "window",
        "objective",
        "current_value",
        "status",
        "controls_present",
    }
    for observed, expected in zip(value, ROUTE_STAGE_LATENCY_FEED_SLO_PANELS):
        if not isinstance(observed, Mapping):
            _append_once(blockers, "slo_panel_not_object")
            ok = False
            continue
        ok = _validate_exact_keys(
            observed,
            expected_fields,
            "slo_panel",
            blockers,
        ) and ok
        for field in ("id", "title", "metric", "query", "window", "objective"):
            if observed.get(field) != expected.get(field):
                _append_once(blockers, "slo_panel_template_mismatch")
                ok = False
        panel_id = str(expected["id"])
        if observed.get("status") not in _PANEL_STATUSES:
            _append_once(blockers, "slo_panel_status_invalid")
            ok = False
        expected_status = _route_stage_latency_feed_slo_panel_status(
            panel_id,
            feed_health,
        )
        if observed.get("status") != expected_status:
            _append_once(blockers, "slo_panel_status_mismatch")
            ok = False
        current_value = observed.get("current_value")
        if not _is_nonnegative_finite_number(current_value):
            _append_once(blockers, "slo_panel_current_value_invalid")
            ok = False
        else:
            expected_value = _route_stage_latency_feed_slo_current_value(
                panel_id,
                feed_health,
            )
            if float(current_value) != expected_value:
                _append_once(blockers, "slo_panel_current_value_mismatch")
                ok = False
        if observed.get("controls_present") is not False:
            _append_once(blockers, "slo_panel_controls_present_authority_flag_true")
            ok = False
    return ok


def _validate_drill_evidence(value: object, blockers: list[str]) -> bool:
    if not isinstance(value, Mapping):
        _append_once(blockers, "drill_evidence_not_object")
        return False
    expected = _route_stage_latency_feed_drill_evidence()
    ok = value == expected
    if not ok:
        _append_once(blockers, "drill_evidence_contract_mismatch")
    for field in AUTHORITY_FALSE_FIELDS:
        if value.get(field) is not False:
            _append_once(blockers, f"drill_evidence_{field}_authority_flag_true")
            ok = False
    return ok


def _validate_operator_log_window(
    value: object,
    commit: object,
    blockers: list[str],
) -> bool:
    if not isinstance(value, Mapping):
        _append_once(blockers, "operator_log_window_not_object")
        return False
    ok = _validate_exact_keys(
        value,
        _OPERATOR_LOG_WINDOW_FIELDS,
        "operator_log_window",
        blockers,
    )
    if not _is_timestamp(value.get("timestamp")):
        _append_once(blockers, "operator_log_window_timestamp_invalid")
        ok = False
    if value.get("commit") != commit:
        _append_once(blockers, "operator_log_window_commit_mismatch")
        ok = False
    reason = value.get("sanitized_reason")
    if not isinstance(reason, str) or not reason.strip():
        _append_once(blockers, "operator_log_window_sanitized_reason_invalid")
        ok = False
    return ok


def _validate_no_forbidden_raw_payload(
    value: object,
    blockers: list[str],
) -> bool:
    before = len(blockers)
    _collect_forbidden_raw_payload(value, blockers, ())
    return len(blockers) == before


def _collect_forbidden_raw_payload(
    value: object,
    blockers: list[str],
    path: tuple[str, ...],
) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_text = str(key)
            if _is_forbidden_key(key_text, path):
                _append_once(blockers, "forbidden_raw_payload_key")
            _collect_forbidden_raw_payload(item, blockers, (*path, key_text))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            _collect_forbidden_raw_payload(item, blockers, (*path, str(index)))
    elif isinstance(value, str) and _is_forbidden_string(value, path):
        _append_once(blockers, "forbidden_raw_payload_value")
    elif isinstance(value, float) and not math.isfinite(value):
        _append_once(blockers, "forbidden_non_finite_number")


def _is_forbidden_key(key: str, path: tuple[str, ...]) -> bool:
    lowered = key.lower()
    if lowered == "query" and "slo_panels" in path:
        return False
    if lowered == "runbook_path" and "drill_evidence" in path:
        return False
    if lowered in {"raw_prometheus_results", "raw_alertmanager_labels"}:
        return True
    if lowered in {"headers", "host", "hostname", "url", "external_url"}:
        return True
    if lowered in {"exception", "traceback", "stack_trace", "stacktrace"}:
        return True
    if lowered in {"labels", "raw_labels", "raw_query"}:
        return True
    return False


def _is_forbidden_string(value: str, path: tuple[str, ...]) -> bool:
    if _is_expected_contract_string(value, path):
        return False
    lowered = value.lower()
    if re.search(r"\bhttps?://", lowered):
        return True
    if re.search(r"[a-z]:\\", value, flags=re.IGNORECASE):
        return True
    if "\\\\" in value or "../" in value:
        return True
    if any(token in value for token in ("/etc/", "/home/", "/Users/", "/var/", "/tmp/")):
        return True
    if "traceback (most recent call last)" in lowered:
        return True
    if "authorization:" in lowered or "bearer " in lowered or "cookie:" in lowered:
        return True
    return False


def _is_expected_contract_string(value: str, path: tuple[str, ...]) -> bool:
    if value in {"/metrics", "/api/ops", "operator_log_window"}:
        return True
    if value == "docs/operations/ROUTE_STAGE_LATENCY_RUNBOOK.md":
        return True
    if "privacy_exclusions" in path:
        return True
    if "slo_panels" in path and path[-1:] == ("query",):
        return True
    return False


def _validate_exact_keys(
    value: Mapping[str, Any],
    expected: set[str],
    section: str,
    blockers: list[str],
) -> bool:
    actual = set(value)
    ok = True
    if expected - actual:
        _append_once(blockers, f"{section}_missing_fields")
        ok = False
    if actual - expected:
        _append_once(blockers, f"{section}_unexpected_fields")
        ok = False
    return ok


def _is_safe_metric_field_value(value: object) -> bool:
    if isinstance(value, Mapping):
        return False
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return False
    if isinstance(value, float):
        return math.isfinite(value)
    return isinstance(value, (str, int, bool)) or value is None


def _is_nonnegative_finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value)) and float(value) >= 0.0


def _is_commit_ref(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{7,64}", value) is not None


def _is_timestamp(value: object) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        normalized = value.strip().replace("Z", "+00:00")
        parsed = dt.datetime.fromisoformat(normalized)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _append_once(blockers: list[str], blocker: str) -> None:
    if blocker not in blockers:
        blockers.append(blocker)


if __name__ == "__main__":
    raise SystemExit(main())
