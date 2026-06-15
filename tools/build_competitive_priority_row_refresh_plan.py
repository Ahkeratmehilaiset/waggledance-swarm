# SPDX-License-Identifier: BUSL-1.1
"""Build a read-only refresh admission plan for competitive priority rows.

The competitive evidence matrix can mark G/J/L as refresh priority before
new evidence exists. This tool turns that stale-but-explicit state into a
machine-readable execution plan without running benchmarks, writing artifacts,
or upgrading freshness labels.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.validate_competitive_evidence_matrix_freshness import (  # noqa: E402
    DEFAULT_MATRIX,
    DEFAULT_MAX_AGE_DAYS,
    validate_matrix_freshness,
)


REPORT_VERSION = "wd.competitive_priority_row_refresh_plan.v0"
DEFAULT_PRIORITY_ROWS = ("G", "J", "L")


PRIORITY_ROW_RECIPES: dict[str, dict[str, Any]] = {
    "G": {
        "title": "10,000-solver capability scale",
        "suggested_owner": "codex-lead-1",
        "claim_label_boundary": "MEASURED only after fresh proof artifact",
        "refresh_commands": [
            (
                "python tools/run_solver_scale_proof.py --out-dir "
                "docs/runs/priority_row_g_solver_scale_<YYYY_MM_DD> "
                "--descriptors 10000 --lookup-pass-count 1000"
            ),
            (
                "python tools/run_phase17b_local_efficiency_benchmark.py "
                "--out-dir docs/runs/priority_row_g_phase17b_<YYYY_MM_DD> "
                "--skip-ollama --scale-descriptors 10000 --scale-lookups 1000"
            ),
        ],
        "required_artifacts": [
            "solver_scale_proof.json",
            "phase17b_local_efficiency_benchmark.json",
        ],
        "admission_checks": [
            "synthetic_solver_descriptors_total >= 10000",
            "lookup_capability_hits_total == lookup_pass_count == 1000",
            "lookup_fifo_fallback_total == 0",
            "lookup_miss_total == 0",
            "provider_jobs_delta == 0 and builder_jobs_delta == 0",
            "synthetic-scale caveat remains explicit",
        ],
        "must_not_claim": [
            "architectural maximum",
            "real production corpus size",
            "frontier model comparison",
        ],
    },
    "J": {
        "title": "LLM / MoE fallback as a hybrid",
        "suggested_owner": "codex-tools-1",
        "claim_label_boundary": "MEASURED-LOCAL-OLLAMA-PANEL only after fresh local panel",
        "refresh_commands": [
            (
                "python tools/run_phase17d_local_model_sweep.py --out-dir "
                "docs/runs/priority_row_j_local_model_sweep_<YYYY_MM_DD> "
                "--models auto --repeat-count 3 --prompt-count 30 --max-models 4"
            ),
        ],
        "required_artifacts": [
            "phase17d_local_model_sweep.json",
            "phase17d_local_model_sweep.md",
        ],
        "admission_checks": [
            "ollama binary is locally available",
            "selected models were already installed before the run",
            "models_measured_count >= 2",
            "no_model_pull_or_download == true",
            "no_cloud_api_calls == true",
            "release_gate_pass == true",
            "rendered prose contains no cross-vendor ranking claim",
        ],
        "must_not_claim": [
            "frontier intelligence ranking",
            "fallback accuracy delta unless a paired benchmark is added",
            "cloud-provider result",
        ],
    },
    "L": {
        "title": "Edge resource use",
        "suggested_owner": "codex-lead-1",
        "claim_label_boundary": "MEASURED image-size only; edge fitness stays inferred without edge host proof",
        "refresh_commands": [
            "docker build -t waggledance:priority-edge-refresh-<YYYY_MM_DD> .",
            (
                "docker images waggledance:priority-edge-refresh-<YYYY_MM_DD> "
                '--format "{{.Repository}}:{{.Tag}} {{.Size}}"'
            ),
            (
                "python tools/run_solver_scale_proof.py --out-dir "
                "docs/runs/priority_row_l_edge_lookup_<YYYY_MM_DD> "
                "--descriptors 10000 --lookup-pass-count 1000"
            ),
        ],
        "required_artifacts": [
            "docker image size capture",
            "solver_scale_proof.json",
            "dependency/runtime note for torch/faiss/playwright boundary",
        ],
        "admission_checks": [
            "image build completed from current Dockerfile",
            "image size was captured on the same day as the matrix refresh",
            "runtime dependency caveat is explicit",
            "lookup proof still has provider_jobs_delta == 0",
            "Pi/arm64 fitness remains INFERRED unless a real edge host run is attached",
        ],
        "must_not_claim": [
            "Raspberry Pi readiness without a Pi or arm64 run",
            "torch/faiss/playwright absence beyond the inspected runtime layer",
            "production fleet deployment",
        ],
    },
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Emit a read-only admission plan for stale competitive matrix "
            "priority rows."
        ),
    )
    parser.add_argument(
        "--matrix",
        type=Path,
        default=DEFAULT_MATRIX,
        help="Competitive evidence matrix Markdown file.",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="Optional UTC timestamp override for deterministic output.",
    )
    parser.add_argument(
        "--max-age-days",
        type=int,
        default=DEFAULT_MAX_AGE_DAYS,
        help="Freshness target for priority-row planning evidence.",
    )
    parser.add_argument(
        "--require-priority-fresh",
        action="store_true",
        help="Fail if the matrix priority rows are not fresh for planning.",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        matrix_text = args.matrix.read_text(encoding="utf-8")
        report = build_priority_row_refresh_plan(
            matrix_text=matrix_text,
            now_utc=_parse_utc(args.now) if args.now else None,
            max_age_days=int(args.max_age_days),
            require_priority_fresh=bool(args.require_priority_fresh),
        )
    except OSError:
        print(
            "competitive priority row refresh plan FAILED: "
            "matrix file could not be read",
            file=sys.stderr,
        )
        return 1
    except ValueError as exc:
        print(
            f"competitive priority row refresh plan FAILED: {exc}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_markdown(report), end="")
    return 0 if report["ok"] else 1


def build_priority_row_refresh_plan(
    *,
    matrix_text: str,
    now_utc: datetime | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    require_priority_fresh: bool = False,
) -> dict[str, Any]:
    if max_age_days <= 0:
        raise ValueError("--max-age-days must be > 0")

    generated_at = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated_at_utc = generated_at.isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    matrix_report = validate_matrix_freshness(
        matrix_text,
        now=generated_at.date(),
        max_age_days=max_age_days,
        require_fresh=False,
    )
    priority_rows = [
        str(row).strip().upper()
        for row in list(matrix_report.get("priority_rows") or DEFAULT_PRIORITY_ROWS)
        if str(row).strip()
    ]

    blockers = [
        "competitive_matrix:" + str(blocker)
        for blocker in list(matrix_report.get("blockers") or [])
    ]
    unknown_rows = [row for row in priority_rows if row not in PRIORITY_ROW_RECIPES]
    if unknown_rows:
        blockers.append("priority_rows_without_refresh_recipe:" + ",".join(unknown_rows))
    if require_priority_fresh and matrix_report.get(
        "priority_rows_fresh_for_planning"
    ) is not True:
        blockers.append("priority_rows_not_fresh_for_planning")

    row_plans = [
        _row_plan(row, matrix_report=matrix_report)
        for row in priority_rows
        if row in PRIORITY_ROW_RECIPES
    ]
    ready_to_update_priority_metadata = bool(row_plans) and all(
        row.get("admission_state") == "fresh_metadata_admitted"
        for row in row_plans
    )

    blockers = sorted(set(blockers))
    return {
        "report_version": REPORT_VERSION,
        "generated_at_utc": generated_at_utc,
        "ok": not blockers,
        "blockers": blockers,
        "freshness": {
            "now_date": generated_at.date().isoformat(),
            "max_age_days": max_age_days,
            "require_priority_fresh": bool(require_priority_fresh),
            "priority_rows": priority_rows,
            "priority_rows_snapshot_date": matrix_report.get(
                "priority_rows_snapshot_date"
            ),
            "priority_rows_snapshot_age_days": matrix_report.get(
                "priority_rows_snapshot_age_days"
            ),
            "priority_rows_freshness_audit_date": matrix_report.get(
                "priority_rows_freshness_audit_date"
            ),
            "priority_rows_fresh_for_planning": (
                matrix_report.get("priority_rows_fresh_for_planning") is True
            ),
            "ready_to_update_priority_metadata": ready_to_update_priority_metadata,
        },
        "competitive_matrix": _competitive_matrix_summary(matrix_report),
        "priority_row_refresh_plan": row_plans,
        "agent_split": _agent_split(row_plans),
        "authority_boundary": _authority_boundary(),
        "next_step": _next_step(blockers, ready_to_update_priority_metadata),
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    freshness = _mapping(report.get("freshness"))
    authority = _mapping(report.get("authority_boundary"))
    lines = [
        "# Competitive Priority Row Refresh Plan",
        "",
        f"ok: `{_bool_text(report.get('ok'))}`",
        (
            "priority rows fresh for planning: "
            f"`{_bool_text(freshness.get('priority_rows_fresh_for_planning'))}`"
        ),
        (
            "ready to update priority metadata: "
            f"`{_bool_text(freshness.get('ready_to_update_priority_metadata'))}`"
        ),
        (
            "priority row age: "
            f"`{freshness.get('priority_rows_snapshot_age_days', 'unknown')}` days"
        ),
        "",
        "## Rows",
    ]
    for row in list(report.get("priority_row_refresh_plan") or []):
        lines.extend(
            [
                (
                    f"- {row['row']}. {row['title']}: "
                    f"`{row['admission_state']}` "
                    f"(owner `{row['suggested_owner']}`)"
                ),
                f"  - command: `{row['refresh_commands'][0]}`",
                f"  - boundary: `{row['claim_label_boundary']}`",
            ]
        )
    lines.extend(
        [
            "",
            "## Authority",
            (
                "benchmark execution authority: "
                f"`{_bool_text(authority.get('benchmark_execution_authority'))}`"
            ),
            (
                "matrix label upgrade authority: "
                f"`{_bool_text(authority.get('matrix_label_upgrade_authority'))}`"
            ),
            f"next step: `{report.get('next_step', '')}`",
        ]
    )
    return "\n".join(lines) + "\n"


def _row_plan(row: str, *, matrix_report: Mapping[str, Any]) -> dict[str, Any]:
    recipe = PRIORITY_ROW_RECIPES[row]
    priority_fresh = matrix_report.get("priority_rows_fresh_for_planning") is True
    admission_state = (
        "fresh_metadata_admitted" if priority_fresh else "refresh_required"
    )
    blockers = [] if priority_fresh else ["current_priority_row_evidence_required"]
    return {
        "row": row,
        "title": str(recipe["title"]),
        "suggested_owner": str(recipe["suggested_owner"]),
        "admission_state": admission_state,
        "blockers": blockers,
        "claim_label_boundary": str(recipe["claim_label_boundary"]),
        "refresh_commands": list(recipe["refresh_commands"]),
        "required_artifacts": list(recipe["required_artifacts"]),
        "admission_checks": list(recipe["admission_checks"]),
        "must_not_claim": list(recipe["must_not_claim"]),
    }


def _competitive_matrix_summary(matrix_report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ok": matrix_report.get("ok") is True,
        "snapshot_date": matrix_report.get("snapshot_date"),
        "snapshot_age_days": matrix_report.get("snapshot_age_days"),
        "fresh_for_planning": matrix_report.get("fresh_for_planning") is True,
        "historical_stale_allowed": (
            matrix_report.get("historical_stale_allowed") is True
        ),
        "priority_rows": list(matrix_report.get("priority_rows") or []),
        "priority_rows_snapshot_date": matrix_report.get(
            "priority_rows_snapshot_date"
        ),
        "priority_rows_snapshot_age_days": matrix_report.get(
            "priority_rows_snapshot_age_days"
        ),
        "priority_rows_fresh_for_planning": (
            matrix_report.get("priority_rows_fresh_for_planning") is True
        ),
        "blockers": [str(item) for item in list(matrix_report.get("blockers") or [])],
    }


def _agent_split(row_plans: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    split: dict[str, list[str]] = {}
    for row in row_plans:
        owner = str(row.get("suggested_owner", "codex-lead-1"))
        split.setdefault(owner, []).append(str(row.get("row", "")))
    return {
        "implementation": split,
        "rco_review": {
            "claude-rco-1": "review claim-boundary and no-overclaim conditions",
            "claude-rco-2": "adversarial review of stale-to-fresh admission checks",
        },
        "unavailable_advisors": {
            "grok-scout-1": "unavailable until July 2026 per operator",
            "fable-5": "disabled/unavailable per operator report",
            "mythos": "disabled/unavailable per operator report",
        },
    }


def _authority_boundary() -> dict[str, bool]:
    return {
        "read_only_plan": True,
        "benchmark_execution_authority": False,
        "artifact_write_authority": False,
        "matrix_label_upgrade_authority": False,
        "priority_freshness_upgrade_authority": False,
        "runtime_authority": False,
        "scheduler_authority": False,
        "bridge_write_authority": False,
        "network_authority": False,
    }


def _next_step(blockers: Sequence[str], ready_to_update_priority_metadata: bool) -> str:
    if blockers:
        return "fix_priority_refresh_plan_blockers_before_execution"
    if ready_to_update_priority_metadata:
        return "prepare_matrix_priority_metadata_update_from_fresh_artifacts"
    return "run_or_claim_refresh_evidence_for_priority_rows_G_J_L"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _bool_text(value: Any) -> str:
    return "true" if value is True else "false"


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("--now must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
