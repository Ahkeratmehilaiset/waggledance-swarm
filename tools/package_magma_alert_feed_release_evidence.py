# SPDX-License-Identifier: BUSL-1.1
"""Package sanitized MAGMA alert-feed release evidence for operator review."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


PACKAGE_VERSION = "magma_alert_feed_release_evidence.v1"

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._-]{0,127}$")
_METRIC_LINE_RE = re.compile(
    r"^(?P<name>[A-Za-z_:][A-Za-z0-9_:]*)"
    r"(?:\{(?P<labels>[^}]*)\})?\s+"
    r"(?P<value>[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?)"
    r"(?:\s|$)"
)

SLO_PANELS: tuple[dict[str, str], ...] = (
    {
        "id": "magma_alert_feed_availability_5m",
        "title": "MAGMA alert feed availability",
        "metric": "waggledance_magma_handoff_alert_feed_available",
        "query": (
            "avg_over_time("
            "waggledance_magma_handoff_alert_feed_available[5m])"
        ),
        "window": "5m",
        "objective": "available == 1",
    },
    {
        "id": "magma_alert_feed_fetch_failures_15m",
        "title": "MAGMA alert feed fetch failures",
        "metric": "waggledance_magma_handoff_alert_feed_fetch_failures_total",
        "query": (
            "increase("
            "waggledance_magma_handoff_alert_feed_fetch_failures_total[15m])"
        ),
        "window": "15m",
        "objective": "increase == 0",
    },
    {
        "id": "magma_alert_feed_backoff_15m",
        "title": "MAGMA alert feed backoff active",
        "metric": "waggledance_magma_handoff_alert_feed_backoff_active",
        "query": (
            "max_over_time("
            "waggledance_magma_handoff_alert_feed_backoff_active[15m])"
        ),
        "window": "15m",
        "objective": "max == 0",
    },
    {
        "id": "magma_alert_feed_cache_stale_15m",
        "title": "MAGMA alert feed cache stale",
        "metric": "waggledance_magma_handoff_alert_feed_cache_stale",
        "query": (
            "max_over_time("
            "waggledance_magma_handoff_alert_feed_cache_stale[15m])"
        ),
        "window": "15m",
        "objective": "max == 0",
    },
)

MANUAL_GATE_CHECKS: tuple[dict[str, str], ...] = (
    {
        "id": "availability",
        "panel_id": "magma_alert_feed_availability_5m",
        "sample_metric": "waggledance_magma_handoff_alert_feed_available",
        "manual_pass_condition": (
            "avg_over_time("
            "waggledance_magma_handoff_alert_feed_available[5m]) == 1"
        ),
    },
    {
        "id": "fetch_failures",
        "panel_id": "magma_alert_feed_fetch_failures_15m",
        "sample_metric": (
            "waggledance_magma_handoff_alert_feed_fetch_failures_total"
        ),
        "manual_pass_condition": (
            "increase("
            "waggledance_magma_handoff_alert_feed_fetch_failures_total[15m])"
            " == 0"
        ),
    },
    {
        "id": "bounded_backoff",
        "panel_id": "magma_alert_feed_backoff_15m",
        "sample_metric": "waggledance_magma_handoff_alert_feed_backoff_active",
        "manual_pass_condition": (
            "max_over_time("
            "waggledance_magma_handoff_alert_feed_backoff_active[15m]) == 0"
        ),
    },
    {
        "id": "cache_freshness",
        "panel_id": "magma_alert_feed_cache_stale_15m",
        "sample_metric": "waggledance_magma_handoff_alert_feed_cache_stale",
        "manual_pass_condition": (
            "max_over_time("
            "waggledance_magma_handoff_alert_feed_cache_stale[15m]) == 0"
        ),
    },
    {
        "id": "runtime_authority_boundary",
        "panel_id": "manual_boundary",
        "sample_metric": "waggledance_magma_handoff_runtime_authority_granted",
        "manual_pass_condition": (
            "waggledance_magma_handoff_runtime_authority_granted == 0"
        ),
    },
    {
        "id": "payload_boundary",
        "panel_id": "manual_boundary",
        "sample_metric": "waggledance_magma_handoff_payload_files_imported",
        "manual_pass_condition": (
            "waggledance_magma_handoff_payload_files_imported == 0"
        ),
    },
    {
        "id": "local_path_boundary",
        "panel_id": "manual_boundary",
        "sample_metric": "waggledance_magma_handoff_local_paths_recorded",
        "manual_pass_condition": (
            "waggledance_magma_handoff_local_paths_recorded == 0"
        ),
    },
)

METRIC_NAMES = frozenset(
    check["sample_metric"] for check in MANUAL_GATE_CHECKS
) | frozenset({
    "waggledance_magma_handoff_controls_present",
    "waggledance_magma_handoff_alert_feed_controls_present",
    "waggledance_magma_handoff_alert_feed_runtime_authority_granted",
    "waggledance_magma_handoff_alert_feed_external_writes_applied",
})

ALERT_METRICS = {
    "MagmaHandoffMetricsSourceDown": "waggledance_magma_handoff_provider_up",
    "MagmaHandoffSnapshotInvalid": "waggledance_magma_handoff_snapshot_valid",
    "MagmaHandoffFreshnessStale": (
        "waggledance_magma_handoff_freshness_source_stale"
    ),
    "MagmaHandoffRetentionDropped": (
        "waggledance_magma_handoff_history_dropped_count"
    ),
    "MagmaHandoffPrivateMaterialRecorded": (
        "waggledance_magma_handoff_local_paths_recorded"
    ),
    "MagmaHandoffRuntimeAuthorityReported": (
        "waggledance_magma_handoff_runtime_authority_granted"
    ),
    "MagmaHandoffPayloadImported": (
        "waggledance_magma_handoff_payload_files_imported"
    ),
    "MagmaHandoffProviderUnavailable": (
        "waggledance_magma_handoff_provider_alert_active"
    ),
    "MagmaHandoffFreshnessSourceUnavailable": (
        "waggledance_magma_handoff_provider_alert_active"
    ),
    "MagmaHandoffAlertFeedBackoffActive": (
        "waggledance_magma_handoff_alert_feed_backoff_active"
    ),
    "MagmaHandoffAlertFeedFetchFailures": (
        "waggledance_magma_handoff_alert_feed_fetch_failures_total"
    ),
}

SAFE_STATUSES = frozenset({
    "not_configured",
    "nominal",
    "warning",
    "critical",
    "none",
    "unknown",
})
SAFE_SOURCES = frozenset({
    "not_configured",
    "prometheus_alertmanager_snapshot",
    "prometheus_alertmanager_unavailable",
    "prometheus_alertmanager_invalid",
    "operator_runbook",
    "unknown",
})
SAFE_FAILURE_REASONS = frozenset({
    "none",
    "BACKOFF_ACTIVE",
    "NETWORK_TIMEOUT",
    "NETWORK_REQUEST_FAILED",
    "RESPONSE_STATUS_REFUSED",
    "RESPONSE_JSON_REFUSED",
    "RESPONSE_TOO_LARGE",
    "RESPONSE_CONTENT_TYPE_REFUSED",
    "MAGMA_HANDOFF_METRICS_ALERT_FEED_UNAVAILABLE",
    "FEED_READ_FAILED",
    "UNKNOWN",
})
SAFE_SEVERITIES = frozenset({"none", "warning", "critical", "unknown"})
SLO_STATUSES = frozenset({"not_configured", "nominal", "warning", "unknown"})

FEED_HEALTH_BOOL_FIELDS = (
    "configured",
    "available",
    "cache_enabled",
    "cache_present",
    "cache_stale",
    "backoff_active",
    "controls_present",
    "runtime_authority_granted",
    "external_writes_applied",
)
FEED_HEALTH_NUMERIC_FIELDS = (
    "cache_ttl_seconds",
    "failure_backoff_seconds",
    "cache_hit_count",
    "cache_miss_count",
    "fetch_success_count",
    "fetch_failure_count",
    "backoff_skip_count",
)
DRILL_ARTIFACTS = {
    "metrics_scrape": (
        "waggledance_magma_handoff_alert_feed_status",
        "waggledance_magma_handoff_alert_feed_failure_reason",
        "waggledance_magma_handoff_alert_feed_backoff_active",
    ),
    "ops_snapshot": (
        "provider_health.metrics_alert_state.feed_health",
        "provider_health.metrics_alert_state.slo_panels",
    ),
    "runtime_window_logs": ("timestamp", "commit", "sanitized_reason"),
}
PRIVACY_EXCLUSIONS = (
    "urls",
    "hosts",
    "headers",
    "filesystem_paths",
    "exception_text",
    "raw_alertmanager_labels",
)

FORBIDDEN_OUTPUT_MARKERS = (
    "http://",
    "https://",
    "C:/",
    "C:\\",
    "\\\\",
    "/home/",
    "/Users/",
    "PRIVATE_",
    "Authorization",
    "Bearer ",
    "secret",
    "password",
    "generatorURL",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ops-json", required=True, type=Path)
    parser.add_argument("--metrics-scrape", required=True, type=Path)
    parser.add_argument(
        "--out-dir",
        required=True,
        type=Path,
        help="New output directory. It must not already exist.",
    )
    parser.add_argument("--release-ref", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--operator-agent", required=True)
    parser.add_argument("--bridge-event-ref", required=True)
    parser.add_argument("--ci-run-ref", default="unspecified")
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override such as 2026-05-28T08:00:00Z.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = write_magma_alert_feed_release_evidence_package(
            ops_json_path=args.ops_json,
            metrics_scrape_path=args.metrics_scrape,
            out_dir=args.out_dir,
            release_ref=args.release_ref,
            commit_sha=args.commit_sha,
            operator_agent_id=args.operator_agent,
            bridge_event_ref=args.bridge_event_ref,
            ci_run_ref=args.ci_run_ref,
            now_utc=_parse_utc(args.now) if args.now else None,
        )
    except (OSError, ValueError) as exc:
        print(
            f"MAGMA alert-feed release evidence package FAILED: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        artifact_names = ", ".join(report["artifact_names"])
        print(f"MAGMA alert-feed release evidence package OK: {artifact_names}")
    return 0


def write_magma_alert_feed_release_evidence_package(
    *,
    ops_json_path: Path,
    metrics_scrape_path: Path,
    out_dir: Path,
    release_ref: str,
    commit_sha: str,
    operator_agent_id: str,
    bridge_event_ref: str,
    ci_run_ref: str = "unspecified",
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    if out_dir.exists():
        raise ValueError("--out-dir must be a new directory")

    ops_bytes = ops_json_path.read_bytes()
    metrics_bytes = metrics_scrape_path.read_bytes()
    ops_payload = json.loads(ops_bytes.decode("utf-8"))
    if not isinstance(ops_payload, dict):
        raise ValueError("--ops-json must contain a JSON object")

    package = build_magma_alert_feed_release_evidence_package(
        ops_payload=ops_payload,
        metrics_text=metrics_bytes.decode("utf-8"),
        release_ref=release_ref,
        commit_sha=commit_sha,
        operator_agent_id=operator_agent_id,
        bridge_event_ref=bridge_event_ref,
        ci_run_ref=ci_run_ref,
        now_utc=now_utc,
        ops_sha256=_sha256_hex(ops_bytes),
        ops_size_bytes=len(ops_bytes),
        metrics_sha256=_sha256_hex(metrics_bytes),
        metrics_size_bytes=len(metrics_bytes),
    )
    markdown = render_evidence_package_markdown(package)

    _assert_no_forbidden_output(json.dumps(package, sort_keys=True))
    _assert_no_forbidden_output(markdown)

    out_dir.mkdir(parents=False)
    package_path = out_dir / "magma_alert_feed_release_evidence.json"
    markdown_path = out_dir / "magma_alert_feed_release_evidence.md"
    package_path.write_text(
        json.dumps(package, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(markdown, encoding="utf-8")
    return {
        "ok": True,
        "package_version": PACKAGE_VERSION,
        "release_ref": release_ref,
        "commit_sha": commit_sha,
        "artifact_names": [package_path.name, markdown_path.name],
        "artifact_digests": {
            package_path.name: _sha256_hex(package_path.read_bytes()),
            markdown_path.name: _sha256_hex(markdown_path.read_bytes()),
        },
        "manual_review_required": True,
        "automatic_release_decision": False,
        "runtime_controls_added": False,
        "external_fetch_performed": False,
    }


def build_magma_alert_feed_release_evidence_package(
    *,
    ops_payload: Mapping[str, Any],
    metrics_text: str,
    release_ref: str,
    commit_sha: str,
    operator_agent_id: str,
    bridge_event_ref: str,
    ci_run_ref: str = "unspecified",
    now_utc: datetime | None = None,
    ops_sha256: str | None = None,
    ops_size_bytes: int | None = None,
    metrics_sha256: str | None = None,
    metrics_size_bytes: int | None = None,
) -> dict[str, Any]:
    _validate_commit_sha(commit_sha)
    for label, value in (
        ("release_ref", release_ref),
        ("operator_agent_id", operator_agent_id),
        ("bridge_event_ref", bridge_event_ref),
        ("ci_run_ref", ci_run_ref),
    ):
        _validate_safe_ref(label, value)

    created_at = _utc_iso(now_utc or datetime.now(timezone.utc))
    metrics_samples = _parse_prometheus_samples(metrics_text)
    alert_state = _extract_metrics_alert_state(ops_payload)
    feed_health = _sanitize_feed_health(_mapping(alert_state.get("feed_health")))
    ops_evidence = _sanitize_ops_alert_state(alert_state, feed_health)
    manual_gate = _build_manual_gate(metrics_samples)
    authority = _build_authority_summary(feed_health, metrics_samples)
    privacy = _privacy_summary()

    package = {
        "package_version": PACKAGE_VERSION,
        "ok": True,
        "created_at_utc": created_at,
        "release_ref": release_ref,
        "commit_sha": commit_sha,
        "ci_run_ref": ci_run_ref,
        "operator_ownership": {
            "operator_agent_id": operator_agent_id,
            "bridge_event_ref": bridge_event_ref,
            "manual_review_required": True,
            "automatic_release_decision": False,
        },
        "input_artifacts": {
            "ops_json": {
                "sha256": ops_sha256 or _sha256_hex(
                    json.dumps(ops_payload, sort_keys=True).encode("utf-8")
                ),
                "size_bytes": ops_size_bytes,
                "raw_payload_included": False,
            },
            "metrics_scrape": {
                "sha256": metrics_sha256 or _sha256_hex(
                    metrics_text.encode("utf-8")
                ),
                "size_bytes": metrics_size_bytes,
                "raw_scrape_included": False,
            },
        },
        "ops_evidence": ops_evidence,
        "metrics_evidence": {
            "sample_count": len(metrics_samples),
            "samples": metrics_samples,
            "raw_scrape_included": False,
        },
        "manual_gate": manual_gate,
        "authority": authority,
        "privacy": privacy,
    }
    serialized = json.dumps(package, sort_keys=True)
    forbidden = _forbidden_output_markers(serialized)
    package["privacy"]["forbidden_tokens_found"] = forbidden
    if forbidden:
        raise ValueError(
            "sanitized package still contains forbidden markers: "
            + ", ".join(forbidden)
        )
    return package


def render_evidence_package_markdown(package: Mapping[str, Any]) -> str:
    manual_gate = _mapping(package.get("manual_gate"))
    authority = _mapping(package.get("authority"))
    hold_reasons = manual_gate.get("current_sample_hold_reasons") or []
    checks = manual_gate.get("checks") or []
    lines = [
        "# MAGMA Alert Feed Release Evidence",
        "",
        f"- Package version: `{package.get('package_version')}`",
        f"- Release ref: `{package.get('release_ref')}`",
        f"- Commit SHA: `{package.get('commit_sha')}`",
        f"- CI run ref: `{package.get('ci_run_ref')}`",
        f"- Created at UTC: `{package.get('created_at_utc')}`",
        "- Manual review required: `true`",
        "- Automatic release decision: `false`",
        "- Runtime controls added: `false`",
        "- External fetch performed: `false`",
        "",
        "## Current Sample Hold Reasons",
        "",
    ]
    if hold_reasons:
        lines.extend(f"- `{reason}`" for reason in hold_reasons)
    else:
        lines.append("- `none_from_current_samples`")
    lines.extend([
        "",
        "## Manual Gate Inputs",
        "",
        "| Check | Sample metric | Current sample | Manual pass condition |",
        "| --- | --- | --- | --- |",
    ])
    for check in checks:
        if not isinstance(check, Mapping):
            continue
        lines.append(
            "| `{}` | `{}` | `{}` | `{}` |".format(
                check.get("id"),
                check.get("sample_metric"),
                check.get("current_sample"),
                check.get("manual_pass_condition"),
            )
        )
    lines.extend([
        "",
        "## Authority Boundary",
        "",
        f"- Runtime authority granted: `{authority.get('runtime_authority_granted')}`",
        f"- Payload files imported: `{authority.get('payload_files_imported')}`",
        f"- Local paths recorded: `{authority.get('local_paths_recorded')}`",
        f"- Controls present: `{authority.get('controls_present')}`",
        f"- External writes applied: `{authority.get('external_writes_applied')}`",
        "",
        "This package is an operator-owned evidence artifact only. It does not "
        "merge, promote, write configuration, call endpoints, import or replay "
        "payloads, control feeds, or grant runtime authority.",
        "",
    ])
    return "\n".join(lines)


def _sanitize_ops_alert_state(
    alert_state: Mapping[str, Any],
    feed_health: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "source": _safe_enum(alert_state.get("source"), SAFE_SOURCES),
        "status": _safe_enum(alert_state.get("status"), SAFE_STATUSES),
        "severity": _safe_enum(alert_state.get("severity"), SAFE_SEVERITIES),
        "prometheus_alertmanager_feed": _as_bool(
            alert_state.get("prometheus_alertmanager_feed")
        ),
        "active_count": _as_nonnegative_int(alert_state.get("active_count")),
        "active": _sanitize_active_alerts(alert_state.get("active")),
        "feed_health": feed_health,
        "slo_panels": _sanitize_slo_panels(alert_state.get("slo_panels")),
        "drill_evidence": _sanitize_drill_evidence(
            alert_state.get("drill_evidence")
        ),
        "controls_present": False,
        "raw_payload_included": False,
    }


def _sanitize_feed_health(feed_health: Mapping[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for field in FEED_HEALTH_BOOL_FIELDS:
        sanitized[field] = _as_bool(feed_health.get(field))
    for field in FEED_HEALTH_NUMERIC_FIELDS:
        value = _as_nonnegative_float(feed_health.get(field))
        if value is not None:
            sanitized[field] = value
    sanitized["status"] = _safe_enum(feed_health.get("status"), SAFE_STATUSES)
    sanitized["failure_reason"] = _safe_enum(
        feed_health.get("failure_reason"),
        SAFE_FAILURE_REASONS,
    )
    return sanitized


def _sanitize_slo_panels(raw_panels: Any) -> list[dict[str, Any]]:
    raw_by_id: dict[str, Mapping[str, Any]] = {}
    if isinstance(raw_panels, list):
        for raw in raw_panels:
            if isinstance(raw, Mapping) and isinstance(raw.get("id"), str):
                raw_by_id[str(raw["id"])] = raw
    panels: list[dict[str, Any]] = []
    for panel in SLO_PANELS:
        raw = raw_by_id.get(panel["id"], {})
        current_value = _as_nonnegative_float(raw.get("current_value"))
        panels.append({
            **panel,
            "current_value": current_value,
            "status": _safe_enum(raw.get("status"), SLO_STATUSES),
            "controls_present": False,
        })
    return panels


def _sanitize_drill_evidence(raw: Any) -> dict[str, Any]:
    raw_required = []
    if isinstance(raw, Mapping):
        maybe_required = raw.get("required_artifacts")
        if isinstance(maybe_required, list):
            raw_required = maybe_required
    present_ids = {
        item.get("id")
        for item in raw_required
        if isinstance(item, Mapping) and item.get("id") in DRILL_ARTIFACTS
    }
    if not present_ids:
        present_ids = set(DRILL_ARTIFACTS)
    return {
        "source": "operator_runbook",
        "required_artifacts": [
            {"id": artifact_id, "fields": list(DRILL_ARTIFACTS[artifact_id])}
            for artifact_id in sorted(present_ids)
        ],
        "privacy_exclusions": list(PRIVACY_EXCLUSIONS),
        "controls_present": False,
        "runtime_authority_granted": False,
        "external_writes_applied": False,
    }


def _sanitize_active_alerts(raw_active: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_active, list):
        return []
    alerts: list[dict[str, Any]] = []
    for raw in raw_active:
        if not isinstance(raw, Mapping):
            continue
        alert_id = raw.get("id")
        if not isinstance(alert_id, str) or alert_id not in ALERT_METRICS:
            continue
        alert = {
            "id": alert_id,
            "severity": _safe_enum(raw.get("severity"), SAFE_SEVERITIES),
            "metric": ALERT_METRICS[alert_id],
        }
        value = _as_nonnegative_float(raw.get("value"))
        if value is not None:
            alert["value"] = value
        alerts.append(alert)
    return alerts


def _build_manual_gate(samples: Mapping[str, float]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    hold_reasons: list[str] = []
    missing: list[str] = []
    for check in MANUAL_GATE_CHECKS:
        metric = check["sample_metric"]
        sample = samples.get(metric)
        if sample is None:
            missing.append(metric)
            hold_reasons.append(f"missing_sample:{metric}")
        elif _sample_suggests_hold(check["id"], sample):
            hold_reasons.append(f"current_sample_hold:{check['id']}")
        checks.append({
            "id": check["id"],
            "panel_id": check["panel_id"],
            "sample_metric": metric,
            "current_sample": sample,
            "manual_pass_condition": check["manual_pass_condition"],
            "automatic_pass_decision": False,
        })
    return {
        "status": "operator_review_required",
        "manual_review_required": True,
        "automatic_release_decision": False,
        "checks": checks,
        "missing_required_samples": missing,
        "current_sample_hold_reasons": hold_reasons,
    }


def _build_authority_summary(
    feed_health: Mapping[str, Any],
    samples: Mapping[str, float],
) -> dict[str, Any]:
    runtime_authority = _sample_true(
        samples,
        "waggledance_magma_handoff_runtime_authority_granted",
    ) or _sample_true(
        samples,
        "waggledance_magma_handoff_alert_feed_runtime_authority_granted",
    ) or _as_bool(feed_health.get("runtime_authority_granted"))
    payload_files = _as_nonnegative_float(
        samples.get("waggledance_magma_handoff_payload_files_imported")
    )
    local_paths = _sample_true(
        samples,
        "waggledance_magma_handoff_local_paths_recorded",
    )
    controls = _sample_true(
        samples,
        "waggledance_magma_handoff_controls_present",
    ) or _sample_true(
        samples,
        "waggledance_magma_handoff_alert_feed_controls_present",
    ) or _as_bool(feed_health.get("controls_present"))
    external_writes = _sample_true(
        samples,
        "waggledance_magma_handoff_alert_feed_external_writes_applied",
    ) or _as_bool(feed_health.get("external_writes_applied"))
    return {
        "runtime_authority_granted": runtime_authority,
        "payload_files_imported": payload_files or 0.0,
        "local_paths_recorded": local_paths,
        "controls_present": controls,
        "external_writes_applied": external_writes,
        "runtime_controls_added": False,
        "configuration_writes_applied": False,
        "import_or_replay_performed": False,
        "auto_merge_or_promotion_performed": False,
    }


def _privacy_summary() -> dict[str, Any]:
    return {
        "raw_ops_payload_included": False,
        "raw_metrics_scrape_included": False,
        "urls_recorded": False,
        "hosts_recorded": False,
        "headers_recorded": False,
        "filesystem_paths_recorded": False,
        "exception_text_recorded": False,
        "raw_alertmanager_labels_recorded": False,
        "payload_material_recorded": False,
        "forbidden_tokens_found": [],
    }


def _extract_metrics_alert_state(ops_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    magma_section = _mapping(ops_payload.get("magma_share_import_handoff"))
    provider_health = _mapping(magma_section.get("provider_health"))
    alert_state = _mapping(provider_health.get("metrics_alert_state"))
    if alert_state:
        return alert_state
    provider_health = _mapping(ops_payload.get("provider_health"))
    return _mapping(provider_health.get("metrics_alert_state"))


def _parse_prometheus_samples(text: str) -> dict[str, float]:
    samples: dict[str, float] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _METRIC_LINE_RE.match(line)
        if match is None:
            continue
        name = match.group("name")
        if name not in METRIC_NAMES or match.group("labels"):
            continue
        value = _as_finite_float(match.group("value"))
        if value is not None:
            samples[name] = value
    return samples


def _sample_suggests_hold(check_id: str, sample: float) -> bool:
    if check_id == "availability":
        return sample != 1.0
    return sample > 0.0


def _sample_true(samples: Mapping[str, float], name: str) -> bool:
    value = samples.get(name)
    return value is not None and value > 0.0


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value) != 0.0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no", ""}:
            return False
    return False


def _as_nonnegative_int(value: Any) -> int:
    numeric = _as_nonnegative_float(value)
    return int(numeric) if numeric is not None else 0


def _as_nonnegative_float(value: Any) -> float | None:
    numeric = _as_finite_float(value)
    if numeric is None or numeric < 0:
        return None
    return float(numeric)


def _as_finite_float(value: Any) -> float | None:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return float(numeric)


def _safe_enum(value: Any, allowed: frozenset[str]) -> str:
    if isinstance(value, str) and value in allowed:
        return value
    return "unknown"


def _validate_commit_sha(value: str) -> None:
    if not _COMMIT_RE.match(value):
        raise ValueError("--commit-sha must be a 40-character lowercase hex SHA")


def _validate_safe_ref(label: str, value: str) -> None:
    if not _SAFE_REF_RE.match(value):
        raise ValueError(f"{label} must be a safe operator reference")


def _parse_utc(raw: str) -> datetime:
    if not raw.endswith("Z"):
        raise ValueError("--now must be a UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(raw[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("invalid --now timestamp") from exc
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timezone.utc.utcoffset(parsed)
    ):
        raise ValueError("--now must be in UTC")
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def _sha256_hex(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _forbidden_output_markers(text: str) -> list[str]:
    lower_text = text.lower()
    return sorted(
        marker
        for marker in FORBIDDEN_OUTPUT_MARKERS
        if marker.lower() in lower_text
    )


def _assert_no_forbidden_output(text: str) -> None:
    found = _forbidden_output_markers(text)
    if found:
        raise ValueError(
            "sanitized output contains forbidden markers: " + ", ".join(found)
        )


if __name__ == "__main__":
    raise SystemExit(main())
