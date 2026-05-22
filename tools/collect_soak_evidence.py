#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Collect a fail-closed release soak evidence draft.

This tool does not decide release readiness. It writes evidence in the schema
validated by tools/check_release_gate.py and intentionally defaults incomplete
signals to a hold posture until the operator supplies explicit pass evidence.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.check_release_gate import (
    SCHEMA_VERSION,
    STATUS_PASS_FIELDS,
    parse_release_readiness,
)


UNKNOWN_STATUS = "unknown"
BLOCKED_STATUS = "blocked"

DEFAULT_EVIDENCE_ROOT = Path("docs/runs/release_soak_evidence")
DEFAULT_RELEASE_NOTES = Path("docs/releases/v3.12.0.md")

FINAL_BANDIT_REPORTS = (
    "v3.12.0_bandit_report_after_static_hardening_zero_medium.json",
    "v3.12.0_bandit_report.json",
)
FINAL_PIP_AUDIT_REPORTS = (
    "v3.12.0_pip_audit_report_lock_after_prune_osv.json",
    "v3.12.0_pip_audit_report_lock_after_prune.json",
    "v3.12.0_pip_audit_report_after_fixable_deps.json",
    "v3.12.0_pip_audit_report_after_direct_ci_deps.json",
    "v3.12.0_pip_audit_report.json",
)
PRIVACY_PRECHECK = "v3.12.0_security_privacy_precheck.md"
REQUIRED_RELEASE_NOTE_ANTI_CLAIMS = (
    "Does **not** claim AGI, consciousness, model superiority",
    "States Docker `:latest` will remain `v3.8.0`",
)
FORBIDDEN_RELEASE_NOTE_CLAIMS = (
    "beats all competitors",
    "branch isolation is solved",
    "3d topology ships",
    "per-cell sharding ships",
)


def _parse_timestamp(value: str) -> dt.datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def _utc_midnight(value: dt.date) -> dt.datetime:
    return dt.datetime.combine(value, dt.time(), tzinfo=dt.UTC)


def _format_utc(value: dt.datetime) -> str:
    normalized = value.astimezone(dt.UTC).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def _duration_hours(started_at: dt.datetime, ended_at: dt.datetime) -> int | float:
    hours = (ended_at - started_at).total_seconds() / 3600
    if hours.is_integer():
        return int(hours)
    return round(hours, 3)


def _current_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return completed.stdout.strip()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _first_existing(root: Path, names: tuple[str, ...]) -> Path | None:
    for name in names:
        candidate = root / name
        if candidate.exists():
            return candidate
    return None


def _bandit_high_medium_clean(report_path: Path) -> bool | None:
    report = _read_json(report_path)
    if report is None:
        return None
    totals = report.get("metrics", {}).get("_totals", {})
    if not isinstance(totals, dict):
        return None
    try:
        high = int(totals.get("SEVERITY.HIGH", 0))
        medium = int(totals.get("SEVERITY.MEDIUM", 0))
    except (TypeError, ValueError):
        return None
    return high == 0 and medium == 0


def _pip_audit_blocker_count(report_path: Path) -> int | None:
    report = _read_json(report_path)
    if report is None:
        return None
    dependencies = report.get("dependencies")
    if not isinstance(dependencies, list):
        return None
    count = 0
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            return None
        if "skip_reason" in dependency:
            count += 1
            continue
        vulns = dependency.get("vulns")
        if not isinstance(vulns, list):
            return None
        count += len(vulns)
    return count


def _privacy_precheck_ok(path: Path) -> bool | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if "74 passed" in text and "SMOKE_OK" in text:
        return True
    return False


def _security_privacy_status(evidence_root: Path) -> str:
    bandit_report = _first_existing(evidence_root, FINAL_BANDIT_REPORTS)
    pip_audit_report = _first_existing(evidence_root, FINAL_PIP_AUDIT_REPORTS)
    privacy_precheck = evidence_root / PRIVACY_PRECHECK

    if bandit_report is None or pip_audit_report is None:
        return UNKNOWN_STATUS

    bandit_clean = _bandit_high_medium_clean(bandit_report)
    pip_blockers = _pip_audit_blocker_count(pip_audit_report)
    privacy_ok = _privacy_precheck_ok(privacy_precheck)

    if bandit_clean is None or pip_blockers is None or privacy_ok is None:
        return UNKNOWN_STATUS
    if not bandit_clean or pip_blockers > 0 or not privacy_ok:
        return BLOCKED_STATUS
    return "pass"


def _profile_s_smoke_status(evidence_root: Path) -> str:
    privacy_ok = _privacy_precheck_ok(evidence_root / PRIVACY_PRECHECK)
    if privacy_ok is None:
        return UNKNOWN_STATUS
    return "pass" if privacy_ok else BLOCKED_STATUS


def _release_notes_anti_claims_status(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return UNKNOWN_STATUS
    missing = [claim for claim in REQUIRED_RELEASE_NOTE_ANTI_CLAIMS if claim not in text]
    lowered = text.lower()
    forbidden = [claim for claim in FORBIDDEN_RELEASE_NOTE_CLAIMS if claim in lowered]
    if missing or forbidden:
        return BLOCKED_STATUS
    return "pass"


def local_artifact_statuses(
    *,
    evidence_root: Path | str = DEFAULT_EVIDENCE_ROOT,
    release_notes: Path | str = DEFAULT_RELEASE_NOTES,
) -> dict[str, str]:
    """Derive release statuses from local artifacts without manual stubs."""

    evidence_root = Path(evidence_root)
    release_notes = Path(release_notes)
    return {
        "profile_s_smoke": _profile_s_smoke_status(evidence_root),
        "security_privacy_gate": _security_privacy_status(evidence_root),
        "release_notes_anti_claims": _release_notes_anti_claims_status(
            release_notes
        ),
    }


def _read_readiness(readiness_path: Path) -> Any:
    text = readiness_path.read_text(encoding="utf-8")
    readiness, blockers = parse_release_readiness(text)
    if readiness is None or blockers:
        raise ValueError(
            "release readiness could not be parsed: " + ", ".join(blockers)
        )
    return readiness


def _derive_result(evidence: dict[str, Any], required_hours: int) -> str:
    statuses_pass = all(
        evidence.get(field) == expected
        for field, expected in STATUS_PASS_FIELDS.items()
    )
    release_pass = (
        bool(evidence.get("commit"))
        and evidence.get("duration_hours", 0) >= required_hours
        and evidence.get("silent_failures") == 0
        and evidence.get("error_log_clean") is True
        and evidence.get("docker_stable_policy") == "finalized"
        and statuses_pass
    )
    return "pass" if release_pass else "hold"


def build_soak_evidence(
    release_readiness: Path | str,
    *,
    commit: str | None = None,
    started_at_utc: dt.datetime | None = None,
    ended_at_utc: dt.datetime | None = None,
    status_overrides: dict[str, str] | None = None,
    silent_failures: int | None = None,
    error_log_clean: bool = False,
    docker_stable_policy: str = "draft",
    use_local_artifacts: bool = False,
    evidence_root: Path | str = DEFAULT_EVIDENCE_ROOT,
    release_notes: Path | str = DEFAULT_RELEASE_NOTES,
) -> dict[str, Any]:
    """Build a soak evidence object in the release-gate schema."""

    readiness = _read_readiness(Path(release_readiness))
    started_at_utc = started_at_utc or _utc_midnight(readiness.soak_start)
    ended_at_utc = ended_at_utc or dt.datetime.now(dt.UTC)
    status_values = {field: UNKNOWN_STATUS for field in STATUS_PASS_FIELDS}

    for field, value in (status_overrides or {}).items():
        if field not in STATUS_PASS_FIELDS:
            raise ValueError(f"unknown release status field: {field}")
        status_values[field] = value
    if use_local_artifacts:
        status_values.update(
            local_artifact_statuses(
                evidence_root=evidence_root,
                release_notes=release_notes,
            )
        )

    required_hours = (readiness.soak_end - readiness.soak_start).days * 24
    evidence: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "target_version": readiness.target_version,
        "commit": commit if commit is not None else _current_commit(),
        "started_at_utc": _format_utc(started_at_utc),
        "ended_at_utc": _format_utc(ended_at_utc),
        "duration_hours": _duration_hours(started_at_utc, ended_at_utc),
        "silent_failures": silent_failures,
        "error_log_clean": error_log_clean,
        "docker_stable_policy": docker_stable_policy,
        **status_values,
    }
    evidence["result"] = _derive_result(evidence, required_hours)
    return evidence


def write_soak_evidence(
    evidence: dict[str, Any],
    *,
    output: Path | None = None,
    history: Path | None = None,
) -> None:
    encoded = json.dumps(evidence, indent=2, sort_keys=True) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")
    if history is not None:
        history.parent.mkdir(parents=True, exist_ok=True)
        with history.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(evidence, sort_keys=True) + "\n")


def _parse_status_override(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("status override must be FIELD=VALUE")
    field, status = value.split("=", 1)
    field = field.strip()
    status = status.strip()
    if field not in STATUS_PASS_FIELDS:
        allowed = ", ".join(sorted(STATUS_PASS_FIELDS))
        raise argparse.ArgumentTypeError(
            f"unknown status field {field!r}; expected one of: {allowed}"
        )
    if not status:
        raise argparse.ArgumentTypeError("status value must not be empty")
    return field, status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release-readiness",
        default="docs/release/RELEASE_READINESS.md",
        type=Path,
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--history", type=Path)
    parser.add_argument("--commit", default=None)
    parser.add_argument("--started-at-utc", type=_parse_timestamp)
    parser.add_argument("--ended-at-utc", type=_parse_timestamp)
    parser.add_argument(
        "--status",
        action="append",
        default=[],
        type=_parse_status_override,
        metavar="FIELD=VALUE",
    )
    parser.add_argument("--silent-failures", type=int, default=None)
    parser.add_argument("--error-log-clean", action="store_true")
    parser.add_argument("--docker-stable-policy", default="draft")
    parser.add_argument(
        "--use-local-artifacts",
        action="store_true",
        help=(
            "Derive supported status fields from local evidence artifacts. "
            "Local evidence overrides manual --status values for those fields."
        ),
    )
    parser.add_argument("--evidence-root", default=DEFAULT_EVIDENCE_ROOT, type=Path)
    parser.add_argument("--release-notes", default=DEFAULT_RELEASE_NOTES, type=Path)
    args = parser.parse_args(argv)

    status_overrides = dict(args.status)
    try:
        evidence = build_soak_evidence(
            args.release_readiness,
            commit=args.commit,
            started_at_utc=args.started_at_utc,
            ended_at_utc=args.ended_at_utc,
            status_overrides=status_overrides,
            silent_failures=args.silent_failures,
            error_log_clean=args.error_log_clean,
            docker_stable_policy=args.docker_stable_policy,
            use_local_artifacts=args.use_local_artifacts,
            evidence_root=args.evidence_root,
            release_notes=args.release_notes,
        )
        write_soak_evidence(evidence, output=args.output, history=args.history)
    except (OSError, ValueError) as exc:
        print(f"collect_soak_evidence: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(evidence, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
