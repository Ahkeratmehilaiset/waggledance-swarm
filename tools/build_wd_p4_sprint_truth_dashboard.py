# SPDX-License-Identifier: BUSL-1.1
"""Build a read-only truth dashboard for the WD P4 sprint.

The dashboard is a status artifact only. It accepts already-collected seed
status input, validates it, and renders JSON or Markdown. It never reads from
GitHub, appends bridge events, claims queue work, merges, rolls back, schedules
runtime work, or grants runtime authority.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence


REPORT_VERSION = "wd.p4_sprint_truth_dashboard.v0"
DEFAULT_SPRINT_TASK_ID = "codex-lead-1/wd-p4-runtime-readiness-sprint-20260629"
DEFAULT_STARTING_READINESS = 42
DEFAULT_TARGET_READINESS = 52
DEFAULT_STRETCH_READINESS = 55
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
READINESS_COUNT_STATES = frozenset(
    {"merged", "complete_existing", "superseded_by_stronger_merged"}
)
TERMINAL_DONE_STATES = READINESS_COUNT_STATES | frozenset({"operator_gated_done"})
OPEN_STATES = frozenset(
    {"planned", "pr_open", "ci_pending", "consensus_pending", "blocked"}
)
EXPECTED_SEEDS = (
    1,
    2,
    3,
    4,
    5,
    6,
    7,
)
LOCAL_PATH_RE = re.compile(r"[A-Za-z]:\\")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a path-free WD P4 sprint truth dashboard.",
    )
    parser.add_argument(
        "--status-file",
        type=Path,
        required=True,
        help="JSON file containing a list of P4 seed status records.",
    )
    parser.add_argument("--sprint-task-id", default=DEFAULT_SPRINT_TASK_ID)
    parser.add_argument("--starting-readiness", type=int, default=DEFAULT_STARTING_READINESS)
    parser.add_argument("--target-readiness", type=int, default=DEFAULT_TARGET_READINESS)
    parser.add_argument("--stretch-readiness", type=int, default=DEFAULT_STRETCH_READINESS)
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override for deterministic output.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = build_wd_p4_sprint_truth_dashboard(
        seed_statuses=_read_status_file(args.status_file),
        sprint_task_id=args.sprint_task_id,
        starting_readiness=args.starting_readiness,
        target_readiness=args.target_readiness,
        stretch_readiness=args.stretch_readiness,
        now_utc=_parse_utc(args.now) if args.now else None,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0 if report["ok"] else 1


def build_wd_p4_sprint_truth_dashboard(
    *,
    seed_statuses: Sequence[Mapping[str, Any]],
    sprint_task_id: str = DEFAULT_SPRINT_TASK_ID,
    starting_readiness: int = DEFAULT_STARTING_READINESS,
    target_readiness: int = DEFAULT_TARGET_READINESS,
    stretch_readiness: int = DEFAULT_STRETCH_READINESS,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated_at_utc = generated_at.isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    blockers: list[str] = []
    normalized = _normalize_seed_statuses(seed_statuses, blockers=blockers)
    counted_points = sum(item["readiness_points"] for item in normalized if item["counts"])
    current_readiness = starting_readiness + counted_points
    merged_count = sum(1 for item in normalized if item["state"] in TERMINAL_DONE_STATES)
    open_count = sum(1 for item in normalized if item["state"] in OPEN_STATES)
    blocked_count = sum(1 for item in normalized if item["state"] == "blocked")
    standing_sign_proven = any(
        item["seed"] == 5 and item["state"] in READINESS_COUNT_STATES
        for item in normalized
    )
    finish_line_complete = (
        not blockers
        and merged_count == len(EXPECTED_SEEDS)
        and standing_sign_proven
        and current_readiness >= target_readiness
    )

    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": generated_at_utc,
        "ok": not blockers,
        "blockers": blockers,
        "sprint_task_id": sprint_task_id,
        "readiness": {
            "starting_percent": starting_readiness,
            "current_percent": current_readiness,
            "target_percent": target_readiness,
            "stretch_percent": stretch_readiness,
            "counted_points": counted_points,
            "target_met": current_readiness >= target_readiness,
            "stretch_met": current_readiness >= stretch_readiness,
            "inflates_open_work": False,
        },
        "seeds": normalized,
        "summary": {
            "seed_count": len(normalized),
            "merged_or_complete_count": merged_count,
            "open_count": open_count,
            "blocked_count": blocked_count,
            "standing_sign_proven": standing_sign_proven,
            "finish_line_complete": finish_line_complete,
        },
        "authority_boundary": _authority_boundary(),
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    readiness = _mapping(report.get("readiness"))
    summary = _mapping(report.get("summary"))
    authority = _mapping(report.get("authority_boundary"))
    lines = [
        "# WD P4 Sprint Truth Dashboard",
        "",
        f"- report version: `{report.get('report_version')}`",
        f"- generated at: `{report.get('generated_at_utc')}`",
        f"- sprint task: `{report.get('sprint_task_id')}`",
        f"- input ok: `{_bool_text(report.get('ok') is True)}`",
        f"- WD readiness: `{readiness.get('current_percent')}%` "
        f"(start `{readiness.get('starting_percent')}%`, target "
        f"`{readiness.get('target_percent')}%`, stretch "
        f"`{readiness.get('stretch_percent')}%`)",
        f"- finish line complete: `{_bool_text(summary.get('finish_line_complete') is True)}`",
        f"- runtime activation allowed: `{_bool_text(authority.get('runtime_activation_allowed') is True)}`",
        f"- production activation allowed: `{_bool_text(authority.get('production_activation_allowed') is True)}`",
        "",
        "## Seeds",
        "",
        "| Seed | State | PR | Head | CI | Gate | Readiness Counted | Blockers |",
        "| ---: | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in report.get("seeds", []):
        seed = _mapping(item)
        blockers = seed.get("blockers") or []
        lines.append(
            "| {seed} | `{state}` | {pr} | `{head}` | `{ci}` | `{gate}` | `{counted}` | {blockers} |".format(
                seed=seed.get("seed"),
                state=seed.get("state"),
                pr=_pr_text(seed.get("pr")),
                head=seed.get("head_short") or "",
                ci=seed.get("ci") or "",
                gate=seed.get("gate") or "",
                counted=_bool_text(seed.get("counts") is True),
                blockers="<br>".join(f"`{blocker}`" for blocker in blockers) or "",
            )
        )
    lines.extend(
        [
            "",
            "## Authority Boundary",
            "",
        ]
    )
    for key, value in authority.items():
        lines.append(f"- {key}: `{_bool_text(value is True)}`")
    if report.get("blockers"):
        lines.extend(["", "## Dashboard Blockers", ""])
        for blocker in report["blockers"]:
            lines.append(f"- `{blocker}`")
    return "\n".join(lines) + "\n"


def _normalize_seed_statuses(
    seed_statuses: Sequence[Mapping[str, Any]], *, blockers: list[str]
) -> list[dict[str, Any]]:
    seen: set[int] = set()
    normalized: list[dict[str, Any]] = []
    for raw in seed_statuses:
        if not isinstance(raw, Mapping):
            blockers.append("seed_status_not_object")
            continue
        seed = _int_field(raw, "seed", blockers=blockers)
        if seed is None:
            continue
        if seed in seen:
            blockers.append(f"duplicate_seed:{seed}")
        seen.add(seed)
        state = str(raw.get("state") or "planned")
        if state not in TERMINAL_DONE_STATES and state not in OPEN_STATES:
            blockers.append(f"unknown_state:seed_{seed}:{state}")
        pr = raw.get("pr")
        if pr is not None and not isinstance(pr, int):
            blockers.append(f"invalid_pr:seed_{seed}")
            pr = None
        head = str(raw.get("head") or "")
        if pr is not None and not SHA40_RE.fullmatch(head):
            blockers.append(f"invalid_head_sha:seed_{seed}")
        if _contains_local_path(raw):
            blockers.append(f"local_path_leak:seed_{seed}")
        readiness_points = _int_field(raw, "readiness_points", blockers=blockers)
        if readiness_points is None:
            readiness_points = 0
        runtime_activation = bool(raw.get("runtime_activation_allowed", False))
        production_activation = bool(raw.get("production_activation_allowed", False))
        runtime_mutation = bool(raw.get("runtime_mutation_authority", False))
        scheduler_enqueue = bool(raw.get("scheduler_enqueue_allowed", False))
        if runtime_activation:
            blockers.append(f"runtime_activation_true:seed_{seed}")
        if production_activation:
            blockers.append(f"production_activation_true:seed_{seed}")
        if runtime_mutation:
            blockers.append(f"runtime_mutation_authority_true:seed_{seed}")
        if scheduler_enqueue:
            blockers.append(f"scheduler_enqueue_allowed_true:seed_{seed}")
        counts = state in READINESS_COUNT_STATES
        normalized.append(
            {
                "seed": seed,
                "title": str(raw.get("title") or ""),
                "owner": str(raw.get("owner") or ""),
                "state": state,
                "pr": pr,
                "head": head,
                "head_short": head[:12] if head else "",
                "ci": str(raw.get("ci") or ""),
                "gate": str(raw.get("gate") or ""),
                "tests": list(raw.get("tests") or []),
                "blockers": [str(item) for item in raw.get("blockers") or []],
                "readiness_points": readiness_points,
                "counts": counts,
                "runtime_activation_allowed": runtime_activation,
                "production_activation_allowed": production_activation,
                "runtime_mutation_authority": runtime_mutation,
                "scheduler_enqueue_allowed": scheduler_enqueue,
            }
        )
    missing = sorted(set(EXPECTED_SEEDS) - seen)
    for seed in missing:
        blockers.append(f"missing_seed:{seed}")
    extra = sorted(seen - set(EXPECTED_SEEDS))
    for seed in extra:
        blockers.append(f"unexpected_seed:{seed}")
    normalized.sort(key=lambda item: item["seed"])
    return normalized


def _read_status_file(path: Path) -> list[Mapping[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    if not isinstance(data, list):
        raise ValueError("status_file_must_be_list")
    return data


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp_must_be_timezone_aware")
    return parsed.astimezone(timezone.utc)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non_finite_json:{value}")


def _int_field(
    raw: Mapping[str, Any], field: str, *, blockers: list[str]
) -> int | None:
    value = raw.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        blockers.append(f"invalid_integer:{field}")
        return None
    return value


def _contains_local_path(value: Any) -> bool:
    if isinstance(value, str):
        return bool(LOCAL_PATH_RE.search(value))
    if isinstance(value, Mapping):
        return any(_contains_local_path(child) for child in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_local_path(child) for child in value)
    return False


def _authority_boundary() -> dict[str, bool]:
    return {
        "read_only_report": True,
        "bridge_append_allowed": False,
        "queue_write_allowed": False,
        "scheduler_enqueue_allowed": False,
        "scheduler_tick_allowed": False,
        "runtime_activation_allowed": False,
        "runtime_mutation_authority": False,
        "production_activation_allowed": False,
        "rollback_execution_allowed": False,
        "merge_allowed": False,
        "network_required": False,
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _pr_text(value: Any) -> str:
    return "" if value is None else f"#{value}"


def _bool_text(value: bool) -> str:
    return "true" if value else "false"


if __name__ == "__main__":
    raise SystemExit(main())
