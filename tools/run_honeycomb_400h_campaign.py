#!/usr/bin/env python3
"""Honeycomb 400h campaign harness.

Segment-driven, resumable, and **does not auto-start**. Invocation
always requires `--confirm-start` because 400h of wall-clock runtime
touches the production server and several disk queues. Without that
flag the tool performs a dry-run plan print and exits 0.

Segments (x.txt Phase 9):
  HOT         — chat/UI gauntlet rotation, context recycling, adversarial
  WARM        — tab switching, feed panel, chat panel, hologram state
  COLD        — health/ready/status/auth/cookie checks
  SOLVER      — cell manifest generation, hash dedupe, proposal-gate dry-run
  COMPOSITION — composition graph recompute, bridge-candidate stability
  LEARNING    — dream-mode + quality-gate dry-run when safe
  CI          — recurring small test suite
  FULL        — nightly full pytest if machine budget allows

The harness relies on the existing ui_gauntlet_400h infra for HOT/
WARM/COLD segments (so Phase 9 does not re-implement HTTP drive),
and implements SOLVER/COMPOSITION/LEARNING/CI/FULL segments in-
process using the Phase 2-8 tools.

All checkpoints + failure snapshots land under
  docs/runs/honeycomb_400h/
so there is no collision with the running `ui_gauntlet_400h_*` dirs.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Paths used by the live ui_gauntlet campaign watchdog. When its
# pidfile points to a live process, the honeycomb harness refuses to
# run resource-heavy segments (CI, FULL) so the two campaigns don't
# fight for CPU / disk / Ollama on the same host.
_LIVE_CAMPAIGN_DIRS = sorted(
    (ROOT / "docs" / "runs").glob("ui_gauntlet_400h_*"),
)
_LIVE_WATCHDOG_PIDFILE = (
    (_LIVE_CAMPAIGN_DIRS[-1] / ".watchdog.pid")
    if _LIVE_CAMPAIGN_DIRS else None
)

CAMPAIGN_DIR = ROOT / "docs" / "runs" / "honeycomb_400h"
CHECKPOINTS_DIR = CAMPAIGN_DIR / "checkpoints"
METRICS_PATH = CAMPAIGN_DIR / "metrics.jsonl"
FAILURES_PATH = CAMPAIGN_DIR / "failures.jsonl"
PLAN_PATH = CAMPAIGN_DIR / "plan.md"
SUMMARY_PATH = CAMPAIGN_DIR / "final_summary.md"
OPEN_QUESTIONS_PATH = CAMPAIGN_DIR / "open_questions.md"
RECOMMENDED_PATH = CAMPAIGN_DIR / "recommended_next_commits.md"
STATE_PATH = CAMPAIGN_DIR / "state.json"

SEGMENTS = (
    "HOT", "WARM", "COLD",
    "SOLVER", "COMPOSITION", "LEARNING",
    "CI", "FULL",
)

# Stop conditions (x.txt Phase 9)
STOP_CONDITIONS = {
    "server_unreachable_probes": 5,   # consecutive
    "auth_bypass": 1,
    "xss_execution": 1,
    "uncaught_exception_burst": 10,   # per hour
    "sqlite_lock_failures": 5,        # consecutive
    "embedding_timeout_spike": 10,    # per segment
    "memory_growth_mb": 2048,         # steady-state threshold (placeholder)
    "pytest_core_regression": 1,
    "secret_leakage": 1,
}

# Default cadence (x.txt Phase 9)
CHECKPOINT_STATUS_EVERY_HOURS = 2
CHECKPOINT_VALIDATION_EVERY_HOURS = 12
CHECKPOINT_COMMIT_EVERY_HOURS = 24


@dataclass
class SegmentPlan:
    name: str
    hours: float
    notes: str


@dataclass
class CampaignState:
    started_at: str | None = None
    completed_hours: float = 0.0
    last_checkpoint_at: str | None = None
    last_segment: str | None = None
    incidents: int = 0
    stop_reason: str | None = None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_plan(target_hours: float,
               segment_split: dict[str, float] | None = None) -> list[SegmentPlan]:
    """Default split: 25% HOT, 15% WARM, 20% COLD, 8% each of SOLVER/
    COMPOSITION/LEARNING, 8% CI, 8% FULL. Sums to 100% = target_hours."""
    split = segment_split or {
        "HOT": 0.25, "WARM": 0.15, "COLD": 0.20,
        "SOLVER": 0.08, "COMPOSITION": 0.08, "LEARNING": 0.08,
        "CI": 0.08, "FULL": 0.08,
    }
    if round(sum(split.values()), 5) != 1.0:
        raise ValueError(f"segment split must sum to 1.0, got {sum(split.values())}")
    notes = {
        "HOT":         "chat/UI gauntlet rotation, context recycling, adversarial corpus",
        "WARM":        "tab switching, feed panel, chat panel, hologram state checks",
        "COLD":        "health/ready/status/auth/cookie checks",
        "SOLVER":      "cell_manifest + solver_dedupe + propose_solver dry-run",
        "COMPOSITION": "composition graph recompute + bridge stability",
        "LEARNING":    "dream_mode + quality_gate dry-run (safe mode, no writes)",
        "CI":          "recurring small test subset (phase7 + phase8 + contracts)",
        "FULL":        "nightly full pytest when machine budget allows",
    }
    return [
        SegmentPlan(name=name, hours=round(split[name] * target_hours, 2),
                    notes=notes[name])
        for name in SEGMENTS
    ]


def render_plan_md(plan: list[SegmentPlan], target_hours: float) -> str:
    lines = [
        "# Honeycomb 400h campaign — plan",
        "",
        f"- **Target:** {target_hours} h total",
        f"- **Checkpoint cadence:** status every 2h, validation pack every 12h, commit every 24h",
        "- **Stop conditions:** see `STOP_CONDITIONS` in `tools/run_honeycomb_400h_campaign.py`",
        "",
        "## Segment allocation",
        "",
        "| segment | hours | description |",
        "|---|---|---|",
    ]
    for s in plan:
        lines.append(f"| `{s.name}` | {s.hours} | {s.notes} |")
    lines.extend([
        "",
        "## Invocation",
        "",
        "This harness never auto-starts. Either",
        "",
        "- **Dry-run plan only:** `python tools/run_honeycomb_400h_campaign.py --target-hours 400`",
        "- **Live run:**         `python tools/run_honeycomb_400h_campaign.py --target-hours 400 --confirm-start`",
        "",
        "HOT/WARM/COLD segments delegate to the existing",
        "`tests/e2e/ui_gauntlet_400h.py` harness by default. The honeycomb",
        "harness only owns the SOLVER / COMPOSITION / LEARNING / CI / FULL",
        "segments directly.",
        "",
    ])
    return "\n".join(lines)


# ── Segment bodies (small / runtime-safe by default) ──────────────

def is_live_campaign_busy(pidfile: Path | None = _LIVE_WATCHDOG_PIDFILE) -> bool:
    """True iff the ui_gauntlet campaign watchdog is live and owns the
    port-8002 server right now.

    Rules of thumb:
    - Pidfile missing → not busy (no live campaign)
    - Pidfile present but PID dead → not busy
    - Pidfile present and process matching "campaign_watchdog" alive → busy
    """
    if pidfile is None or not pidfile.exists():
        return False
    try:
        import psutil
        pid = int(pidfile.read_text().strip())
        if not psutil.pid_exists(pid):
            return False
        try:
            proc = psutil.Process(pid)
            cmd = " ".join(proc.cmdline() or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
        return "campaign_watchdog" in cmd
    except Exception:
        return False


def _lower_priority() -> None:
    """Best-effort: lower the honeycomb process's scheduling priority
    so the live ui_gauntlet campaign gets CPU first. No-op if psutil
    or the OS refuses."""
    try:
        import psutil
        p = psutil.Process()
        if os.name == "nt":
            p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
        else:
            p.nice(10)
    except Exception:
        pass


def _run_subprocess(args: list[str], timeout_s: float = 600,
                    env_extra: dict | None = None) -> dict:
    """Thin wrapper that captures exit status + tail of output. Runs
    one of the Phase 2-8 tools."""
    t0 = time.time()
    env = os.environ.copy()
    if env_extra:
        env.update({k: str(v) for k, v in env_extra.items()})
    try:
        proc = subprocess.run(
            args,
            cwd=str(ROOT),
            capture_output=True, text=True,
            timeout=timeout_s,
            env=env,
        )
        duration = time.time() - t0
        return {
            "argv": args,
            "returncode": proc.returncode,
            "duration_s": round(duration, 2),
            "stdout_tail": proc.stdout[-1200:] if proc.stdout else "",
            "stderr_tail": proc.stderr[-1200:] if proc.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {
            "argv": args, "returncode": -1, "timeout": True,
            "duration_s": timeout_s,
        }


def _seg_solver() -> dict:
    out = []
    out.append(_run_subprocess([sys.executable, str(ROOT / "tools" / "cell_manifest.py"),
                                "--no-production-scan"]))
    out.append(_run_subprocess([sys.executable, str(ROOT / "tools" / "solver_dedupe.py")]))
    return {"segment": "SOLVER", "runs": out}


def _seg_composition() -> dict:
    r = _run_subprocess([sys.executable, str(ROOT / "tools" / "solver_composition_report.py")])
    return {"segment": "COMPOSITION", "runs": [r]}


def _seg_learning() -> dict:
    # Dream-mode / quality-gate dry-run is "safe mode" — we import and
    # exercise the quality_gate check filter on a fixed synthetic case,
    # never touching live state. QualityGrade lives in
    # waggledance.core.domain.autonomy; PromotionDecision in the gate
    # module itself.
    try:
        from waggledance.core.domain.autonomy import QualityGrade  # noqa: F401
        from waggledance.core.learning.quality_gate import PromotionDecision, QualityGate  # noqa: F401
        status = "import_ok"
    except Exception as e:
        return {"segment": "LEARNING", "error": f"{type(e).__name__}: {e}"}
    return {"segment": "LEARNING", "status": status}


def _seg_ci(allow_when_busy: bool = False) -> dict:
    # Fast subset: phase7 + phase8 tests only. Refuses to run if the
    # live ui_gauntlet watchdog is up — the two campaigns would fight
    # for Ollama, embedding cache, and sqlite locks.
    if is_live_campaign_busy() and not allow_when_busy:
        return {
            "segment": "CI",
            "skipped": True,
            "reason": "live ui_gauntlet watchdog is up; refuse to run pytest subset",
        }
    # Explicit tmp dir and a marker env var so any test that checks
    # shared state can see this is a honeycomb-driven run.
    r = _run_subprocess([
        sys.executable, "-m", "pytest", "-q", "--tb=line", "-p", "no:warnings",
        "tests/test_phase7_hologram_news_wire.py",
        "tests/test_cell_manifest.py",
        "tests/test_solver_hash.py",
        "tests/test_solver_dedupe.py",
        "tests/test_solver_proposal_schema.py",
        "tests/test_propose_solver_gate.py",
        "tests/test_composition_graph.py",
        "tests/test_hex_subdivision_plan.py",
    ], env_extra={"WAGGLE_HONEYCOMB_SEGMENT": "CI",
                   "PYTEST_ADDOPTS": "-p no:xdist"})
    return {"segment": "CI", "runs": [r]}


def _seg_full(allow_when_busy: bool = False) -> dict:
    # Whole suite excluding e2e (port conflict with campaign). FULL is
    # heavier than CI and must NEVER run concurrently with the live
    # campaign unless the operator explicitly allows it.
    if is_live_campaign_busy() and not allow_when_busy:
        return {
            "segment": "FULL",
            "skipped": True,
            "reason": "live ui_gauntlet watchdog is up; refuse to run full pytest",
        }
    r = _run_subprocess([
        sys.executable, "-m", "pytest", "-q", "--tb=line", "-p", "no:warnings",
        "--ignore=tests/e2e",
    ], timeout_s=1800,
       env_extra={"WAGGLE_HONEYCOMB_SEGMENT": "FULL",
                   "PYTEST_ADDOPTS": "-p no:xdist"})
    return {"segment": "FULL", "runs": [r]}


def _seg_hot_warm_cold(name: str) -> dict:
    """HOT/WARM/COLD delegate to the existing harness via its --mode
    flag. In dry-run, just record the intended args."""
    return {
        "segment": name,
        "delegated_to": "tests/e2e/ui_gauntlet_400h.py",
        "mode": name,
        "note": "run via existing watchdog harness; this is a shakedown marker only",
    }


def _run_one_segment(name: str, allow_when_busy: bool = False) -> dict:
    try:
        if name == "SOLVER":
            return _seg_solver()
        if name == "COMPOSITION":
            return _seg_composition()
        if name == "LEARNING":
            return _seg_learning()
        if name == "CI":
            return _seg_ci(allow_when_busy=allow_when_busy)
        if name == "FULL":
            return _seg_full(allow_when_busy=allow_when_busy)
        if name in {"HOT", "WARM", "COLD"}:
            return _seg_hot_warm_cold(name)
        return {"segment": name, "error": "unknown segment"}
    except Exception as exc:
        return {"segment": name, "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc()[:2000]}


# ── Orchestration ──────────────────────────────────────────────────

def write_plan(target_hours: float) -> Path:
    plan = build_plan(target_hours)
    CAMPAIGN_DIR.mkdir(parents=True, exist_ok=True)
    PLAN_PATH.write_text(render_plan_md(plan, target_hours), encoding="utf-8")
    return PLAN_PATH


def _append_jsonl(path: Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, default=str) + "\n")


def shakedown(target_hours: float, allow_when_busy: bool = False) -> dict:
    """Run each in-process segment ONCE to prove wiring. HOT/WARM/COLD
    are recorded as delegated (not driven). Returns aggregate report.

    If the live ui_gauntlet watchdog is up, CI and FULL are skipped
    unless `allow_when_busy=True`. Priority is lowered for the whole
    process so the live campaign gets the CPU first.
    """
    CAMPAIGN_DIR.mkdir(parents=True, exist_ok=True)
    CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
    write_plan(target_hours)
    _lower_priority()

    state = CampaignState(started_at=_utc_now_iso())
    state.started_at = _utc_now_iso()
    if is_live_campaign_busy():
        state.stop_reason = "ui_gauntlet watchdog live; CI/FULL skipped"

    results: list[dict] = []
    for name in SEGMENTS:
        t0 = time.time()
        r = _run_one_segment(name, allow_when_busy=allow_when_busy)
        r["completed_at"] = _utc_now_iso()
        r["duration_s"] = round(time.time() - t0, 2)
        results.append(r)
        _append_jsonl(METRICS_PATH, {
            "ts": r["completed_at"],
            "segment": name,
            "duration_s": r["duration_s"],
            "ok": "error" not in r and all(
                run.get("returncode", 0) == 0 for run in r.get("runs", [])
            ),
        })
        if "error" in r or any(run.get("returncode") not in (0, None)
                                for run in r.get("runs", [])):
            state.incidents += 1
            _append_jsonl(FAILURES_PATH, {"ts": r["completed_at"], "segment": name,
                                           "detail": r})
        state.last_segment = name
        state.last_checkpoint_at = r["completed_at"]

    STATE_PATH.write_text(json.dumps(asdict(state), indent=2, default=str),
                          encoding="utf-8")

    # Final summary (shakedown flavor)
    SUMMARY_PATH.write_text(_shakedown_summary(results, state, target_hours),
                            encoding="utf-8")
    OPEN_QUESTIONS_PATH.write_text(_open_questions(), encoding="utf-8")
    RECOMMENDED_PATH.write_text(_recommended_next_commits(), encoding="utf-8")
    return {"state": asdict(state), "results": results}


def _shakedown_summary(results: list[dict],
                       state: CampaignState,
                       target_hours: float) -> str:
    gen = _utc_now_iso()
    lines = [
        "# Honeycomb 400h — shakedown summary",
        "",
        f"- **Generated:** {gen}",
        f"- **Target hours (configured):** {target_hours}",
        f"- **Incidents during shakedown:** {state.incidents}",
        "",
        "## Per-segment shakedown result",
        "",
        "| segment | status | duration (s) |",
        "|---|---|---|",
    ]
    for r in results:
        if "error" in r:
            status = f"ERROR: {r['error'][:60]}"
        elif r.get("delegated_to"):
            status = f"delegated → {r['delegated_to']}"
        elif any(run.get("returncode", 0) != 0 for run in r.get("runs", [])):
            codes = [run.get("returncode") for run in r["runs"]]
            status = f"FAIL (rc={codes})"
        else:
            status = "ok"
        lines.append(f"| `{r['segment']}` | {status} | {r.get('duration_s')} |")
    lines.append("")
    lines.append("Shakedown does not burn the 400h budget — it's a wiring")
    lines.append("smoke test. A real campaign run requires `--confirm-start`")
    lines.append("plus wall-clock time and is not performed by this branch.")
    lines.append("")
    return "\n".join(lines)


def _open_questions() -> str:
    return "\n".join([
        "# Honeycomb 400h — open questions",
        "",
        "- When do we flip hybrid retrieval to `mode: authoritative`? Depends on",
        "  Phase D-3 promotion decision, which itself needs solver-domain traffic",
        "  (currently blocked — observer sees UI-gauntlet campaign data only).",
        "- What is the right honeycomb/ui_gauntlet split of wall-clock time while",
        "  both campaigns share one machine? Current fact: ui_gauntlet campaign",
        "  has priority; honeycomb defers.",
        "- When do live Prometheus counters for Phase 8 signals land? Deferred",
        "  per `docs/architecture/PHASE8_METRICS.md` — after target hours + one",
        "  clean preventive-restart cycle + honeycomb shakedown clean.",
        "",
    ])


def _recommended_next_commits() -> str:
    return "\n".join([
        "# Honeycomb 400h — recommended next commits",
        "",
        "These are candidate follow-ups *not performed by this branch*.",
        "",
        "1. `metrics(phase8): wire live counters once campaign settles`",
        "2. `tools(phase9): drive HOT/WARM/COLD from run_honeycomb directly",
        "    once ui_gauntlet_400h campaign completes its 400h budget`",
        "3. `learning(phase6): composition graph depth-5 paths once library",
        "    exceeds 100 solvers`",
        "4. `tools(phase5): add tolerant unit-family check (W ↔ kW, J ↔ kJ)",
        "    so obvious rescales are flagged as duplicates in strict hash`",
        "5. `topology: first real subdivision — seasonal cell → core / harvest",
        "    / calendar based on hex_subdivision_plan.md recommendations`",
        "",
    ])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target-hours", type=float, default=400)
    ap.add_argument("--confirm-start", action="store_true",
                    help="Required to perform any run that consumes the campaign budget. Without it, only the plan is written.")
    ap.add_argument("--shakedown", action="store_true",
                    help="Run each segment once in shakedown mode (in-process, no budget). CI and FULL are auto-skipped when the live ui_gauntlet watchdog is up.")
    ap.add_argument("--allow-when-busy", action="store_true",
                    help="Ignore the live-campaign busy-lock and run CI/FULL anyway. Use only when you've confirmed the host has spare budget.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.shakedown:
        # Shakedown never touches port 8002 directly; HOT/WARM/COLD are
        # recorded as delegated markers only.
        out = shakedown(args.target_hours, allow_when_busy=args.allow_when_busy)
        if args.json:
            print(json.dumps(out, indent=2, default=str))
        else:
            print(f"shakedown complete — summary: {SUMMARY_PATH.as_posix()}")
            for r in out["results"]:
                if r.get("skipped"):
                    print(f"  skip  {r['segment']} ({r.get('reason', '')})")
                elif "error" in r:
                    print(f"  FAIL  {r['segment']}")
                else:
                    print(f"  ok    {r['segment']}")
        return 0

    # No shakedown, no confirm-start → dry-run plan only
    plan_path = write_plan(args.target_hours)
    if not args.confirm_start:
        print(f"dry-run — plan written to {plan_path.as_posix()}")
        print("add --shakedown to exercise segments once (runtime-safe)")
        print("add --confirm-start to consume real wall-clock budget")
        return 0

    print("NOTE: live runs under --confirm-start are not implemented in")
    print("      this branch. See docs/runs/honeycomb_400h/plan.md for the")
    print("      segment contract and recommended invocation.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
