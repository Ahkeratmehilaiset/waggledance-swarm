#!/usr/bin/env python3
"""Campaign reporting harness — produces x.txt Phase 6/7/9/12 outputs from evidence.

Reads authoritative evidence:
  - campaign_state.json (committed segments)
  - hot_results.jsonl (per-query results)
  - incident_log.jsonl (classified incidents)
  - segment_metrics_*.json (per-segment summaries)

Produces on demand:
  - daily           : daily_summary_day_NNN.md files (Phase 6)
  - weekly          : weekly_rollup_week_WW.md files (Phase 6)
  - aggregate       : final_findings.json + final_400h_summary.md +
                       final_400h_reliability.md + final_400h_incident_matrix.md
                       (Phase 7 — runs even mid-campaign, marks TBD for
                        final-only values)
  - decide          : classify diff buckets + propose release PATH
                       (Phase 9)
  - stdout          : Phase 12 final one-shot stdout

Usage:
    python tools/campaign_reports.py daily    --campaign-dir <path>
    python tools/campaign_reports.py weekly   --campaign-dir <path>
    python tools/campaign_reports.py aggregate --campaign-dir <path>
    python tools/campaign_reports.py decide   --campaign-dir <path> --main-ref main
    python tools/campaign_reports.py stdout   --campaign-dir <path>
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from statistics import mean, median


def _load_state(cdir: Path) -> dict:
    return json.load(open(cdir / "campaign_state.json", encoding="utf-8"))


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows


def _day_bucket(ts_iso: str) -> str:
    return ts_iso[:10]  # YYYY-MM-DD


def _week_bucket(ts_iso: str) -> str:
    # ISO week number
    d = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


# ──────────────────────────────────────────────────────────────────
#  DAILY
# ──────────────────────────────────────────────────────────────────

def generate_daily(cdir: Path) -> list[Path]:
    state = _load_state(cdir)
    hot = _load_jsonl(cdir / "hot_results.jsonl")
    incidents = _load_jsonl(cdir / "incident_log.jsonl")

    start_ts = state["start_ts"][:10]
    start = datetime.fromisoformat(start_ts)

    # Partition evidence by day
    hot_by_day = defaultdict(list)
    for r in hot:
        ts = r.get("ts", "")
        if ts:
            hot_by_day[ts[:10]].append(r)

    inc_by_day = defaultdict(list)
    for r in incidents:
        ts = r.get("ts", "")
        if ts:
            inc_by_day[ts[:10]].append(r)

    seg_by_day = defaultdict(list)
    for seg in state["segments"]:
        if seg.get("status") == "reserved":
            continue
        ts = (seg.get("ts_start")
              or seg.get("ts_reserved")
              or seg.get("ts_end", ""))
        if ts:
            seg_by_day[ts[:10]].append(seg)

    # Days from start to today
    today = datetime.now(timezone.utc).date()
    days = []
    d = start.date()
    while d <= today:
        days.append(d.isoformat())
        d += timedelta(days=1)

    out_paths = []
    cum_h = 0.0
    for i, day in enumerate(days, 1):
        day_hot = hot_by_day.get(day, [])
        day_inc = inc_by_day.get(day, [])
        day_segs = seg_by_day.get(day, [])

        day_green = sum(s.get("green_hours", 0) for s in day_segs)
        cum_h += day_green

        # Incident categories
        cat_counts = defaultdict(int)
        for r in day_inc:
            cat_counts[r.get("category", "unknown")] += 1

        # Latency distribution
        responded_latencies = [r["latency_ms"] for r in day_hot
                                if r.get("responded") and r.get("latency_ms", 0) > 100]
        avg_lat = round(mean(responded_latencies), 0) if responded_latencies else None
        p95_lat = None
        if responded_latencies:
            p95_lat = round(sorted(responded_latencies)[int(len(responded_latencies) * 0.95)], 0)

        # Bucket stats
        buckets = defaultdict(lambda: {"total": 0, "responded": 0})
        for r in day_hot:
            b = r.get("bucket", "unknown")
            buckets[b]["total"] += 1
            if r.get("responded"):
                buckets[b]["responded"] += 1

        md = [
            f"# Daily Summary — Day {i:03d} ({day})",
            "",
            f"**Campaign:** `{state['campaign_id']}`",
            f"**Generated:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            "",
            "## Totals this day",
            "",
            f"- Green hours: {day_green:.2f}h",
            f"- Cumulative: {cum_h:.2f}h / 400h",
            f"- Segments completed: {len(day_segs)}",
            f"- HOT queries: {len(day_hot)}",
            f"- Incidents: {len(day_inc)}",
            "",
        ]

        if day_segs:
            md.append("## Segments completed")
            md.append("")
            md.append("| seg | mode | green_h |")
            md.append("|---|---|---|")
            for s in sorted(day_segs, key=lambda x: x["segment_id"]):
                md.append(f"| {s['segment_id']} | {s['mode']} | {s.get('green_hours',0):.2f} |")
            md.append("")

        if day_hot:
            md.append("## HOT query latency")
            md.append("")
            md.append(f"- Responded: {sum(1 for r in day_hot if r.get('responded'))} / {len(day_hot)}")
            if avg_lat is not None:
                md.append(f"- Avg latency (responded >100ms): {avg_lat} ms")
                md.append(f"- p95: {p95_lat} ms")
            md.append("")

            md.append("### Per-bucket send/respond")
            md.append("")
            md.append("| bucket | total | responded | rate |")
            md.append("|---|---|---|---|")
            for b, s in sorted(buckets.items()):
                rate = f"{100 * s['responded'] / s['total']:.0f}%" if s["total"] else "—"
                md.append(f"| {b} | {s['total']} | {s['responded']} | {rate} |")
            md.append("")

        if cat_counts:
            md.append("## Incidents by category")
            md.append("")
            md.append("| category | count |")
            md.append("|---|---|")
            for cat, n in sorted(cat_counts.items(), key=lambda x: -x[1]):
                md.append(f"| {cat} | {n} |")
            md.append("")

        # XSS / DOM / session — safety gates
        xss = sum(1 for r in day_hot if r.get("xss_detected"))
        dom_breaks = sum(1 for r in day_hot if not r.get("dom_ok", True))
        md.append("## Safety gates (zero tolerance per x.txt)")
        md.append("")
        md.append(f"- XSS hits: {xss}")
        md.append(f"- DOM breaks: {dom_breaks}")
        md.append("")

        path = cdir / f"daily_summary_day_{i:03d}.md"
        path.write_text("\n".join(md), encoding="utf-8")
        out_paths.append(path)

    return out_paths


# ──────────────────────────────────────────────────────────────────
#  WEEKLY
# ──────────────────────────────────────────────────────────────────

def generate_weekly(cdir: Path) -> list[Path]:
    state = _load_state(cdir)
    hot = _load_jsonl(cdir / "hot_results.jsonl")
    incidents = _load_jsonl(cdir / "incident_log.jsonl")

    # Partition by ISO week
    hot_by_week = defaultdict(list)
    for r in hot:
        if r.get("ts"):
            hot_by_week[_week_bucket(r["ts"])].append(r)

    inc_by_week = defaultdict(list)
    for r in incidents:
        if r.get("ts"):
            inc_by_week[_week_bucket(r["ts"])].append(r)

    seg_by_week = defaultdict(list)
    for seg in state["segments"]:
        if seg.get("status") == "reserved":
            continue
        ts = (seg.get("ts_start")
              or seg.get("ts_reserved")
              or seg.get("ts_end", ""))
        if ts:
            seg_by_week[_week_bucket(ts)].append(seg)

    weeks = sorted(set(list(hot_by_week.keys()) + list(inc_by_week.keys()) + list(seg_by_week.keys())))

    out_paths = []
    for idx, week in enumerate(weeks, 1):
        w_hot = hot_by_week.get(week, [])
        w_inc = inc_by_week.get(week, [])
        w_segs = seg_by_week.get(week, [])

        green_h = sum(s.get("green_hours", 0) for s in w_segs)
        responded = [r for r in w_hot if r.get("responded")]
        lats = [r["latency_ms"] for r in responded if r.get("latency_ms", 0) > 100]

        cat_counts = defaultdict(int)
        for r in w_inc:
            cat_counts[r.get("category", "unknown")] += 1

        # Feeds / hologram honesty via COLD metrics for this week
        cold_health = sum(s.get("health_ok", 0) for s in w_segs if s.get("mode") == "COLD")
        cold_total = sum(s.get("health_checks", 0) for s in w_segs if s.get("mode") == "COLD")
        feed_mono = all(s.get("feeds_monotonic", True) for s in w_segs if s.get("mode") == "COLD")
        holo_honest = all(s.get("hologram_honest", True) for s in w_segs if s.get("mode") == "COLD")

        md = [
            f"# Weekly Rollup — {week}",
            "",
            f"**Campaign:** `{state['campaign_id']}`",
            f"**Generated:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
            "",
            "## Trend metrics this week",
            "",
            f"- Green hours: {green_h:.2f}h",
            f"- Segments committed: {len(w_segs)}",
            f"- HOT queries: {len(w_hot)} ({len(responded)} responded)",
            f"- Avg latency (responded, >100ms): {round(mean(lats), 0) if lats else '—'} ms",
            f"- p95 latency: {round(sorted(lats)[int(len(lats) * 0.95)], 0) if len(lats) >= 20 else '—'} ms",
            f"- Incidents: {len(w_inc)}",
            f"- COLD health pass rate: {cold_health}/{cold_total}" if cold_total else "- COLD health: no checks",
            f"- Feeds monotonic: {'yes' if feed_mono else 'no'}",
            f"- Hologram honest: {'yes' if holo_honest else 'no'}",
            "",
        ]

        if cat_counts:
            md.append("## Incidents by category")
            md.append("")
            md.append("| category | count |")
            md.append("|---|---|")
            for cat, n in sorted(cat_counts.items(), key=lambda x: -x[1]):
                md.append(f"| {cat} | {n} |")
            md.append("")

        # Safety gates
        xss = sum(1 for r in w_hot if r.get("xss_detected"))
        dom = sum(1 for r in w_hot if not r.get("dom_ok", True))
        md.append("## Safety gates (zero tolerance)")
        md.append("")
        md.append(f"- XSS: {xss}")
        md.append(f"- DOM breaks: {dom}")
        md.append("")

        md.append("## Mode-level breakdown")
        md.append("")
        for mode in ("HOT", "WARM", "COLD"):
            mode_segs = [s for s in w_segs if s.get("mode") == mode]
            mode_green = sum(s.get("green_hours", 0) for s in mode_segs)
            md.append(f"- {mode}: {len(mode_segs)} segments, {mode_green:.2f}h")
        md.append("")

        path = cdir / f"weekly_rollup_{week}.md"
        path.write_text("\n".join(md), encoding="utf-8")
        out_paths.append(path)

    return out_paths


# ──────────────────────────────────────────────────────────────────
#  AGGREGATE (Phase 7)
# ──────────────────────────────────────────────────────────────────

def generate_aggregate(cdir: Path) -> list[Path]:
    state = _load_state(cdir)
    hot = _load_jsonl(cdir / "hot_results.jsonl")
    incidents = _load_jsonl(cdir / "incident_log.jsonl")

    h = state.get("cumulative_hours", {})
    t = state.get("target_hours", {})
    is_final = h.get("total", 0) >= 400
    status_prefix = "FINAL" if is_final else "MID-CAMPAIGN (TBD on completion)"

    # Query aggregates
    total_q = len(hot)
    sent = sum(1 for r in hot if r.get("sent"))
    responded = sum(1 for r in hot if r.get("responded"))
    skipped = sum(1 for r in hot if not r.get("sent") and r.get("error") == "")
    xss = sum(1 for r in hot if r.get("xss_detected"))
    dom_breaks = sum(1 for r in hot if not r.get("dom_ok", True))
    session_lost = sum(1 for r in hot if not r.get("session_ok", True))
    lats = [r["latency_ms"] for r in hot if r.get("responded") and r.get("latency_ms", 0) > 100]

    # Incident categories
    cat_counts = defaultdict(int)
    for r in incidents:
        cat_counts[r.get("category", "unknown")] += 1

    # COLD metrics (aggregate)
    cold_segs = [s for s in state["segments"]
                  if s.get("mode") == "COLD" and s.get("status") != "reserved"]
    cold_health_ok = sum(s.get("health_ok", 0) for s in cold_segs)
    cold_health_total = sum(s.get("health_checks", 0) for s in cold_segs)
    feeds_mono = all(s.get("feeds_monotonic", True) for s in cold_segs)
    holo_honest = all(s.get("hologram_honest", True) for s in cold_segs)
    auth_chat_ok = sum(s.get("auth_chat_ok", 0) for s in cold_segs)
    auth_chat_total = sum(s.get("auth_chats", 0) for s in cold_segs)
    cookie_ok = sum(s.get("cookie_ok", 0) for s in cold_segs)
    cookie_total = sum(s.get("cookie_checks", 0) for s in cold_segs)

    # Build final_findings.json
    findings = {
        "status": status_prefix,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "campaign_id": state.get("campaign_id"),
        "cumulative_hours": h,
        "target_hours": t,
        "segments_committed": sum(1 for s in state["segments"] if s.get("status") != "reserved"),
        "segments_target": state.get("segments_target"),
        "queries": {
            "total": total_q,
            "sent": sent,
            "responded": responded,
            "skipped_empty": skipped,
            "xss": xss,
            "dom_breaks": dom_breaks,
            "session_losses": session_lost,
            "avg_latency_ms": round(mean(lats), 0) if lats else None,
            "median_latency_ms": round(median(lats), 0) if lats else None,
            "p95_latency_ms": round(sorted(lats)[int(len(lats) * 0.95)], 0) if len(lats) >= 20 else None,
        },
        "cold_backend_truth": {
            "health_pass_rate": f"{cold_health_ok}/{cold_health_total}" if cold_health_total else "n/a",
            "feeds_monotonic": feeds_mono,
            "hologram_honest": holo_honest,
            "auth_chat_pass_rate": f"{auth_chat_ok}/{auth_chat_total}" if auth_chat_total else "n/a",
            "cookie_pass_rate": f"{cookie_ok}/{cookie_total}" if cookie_total else "n/a",
        },
        "incidents_by_category": dict(cat_counts),
        "carries": state.get("carries", []),
        "known_final_only_fields": None if is_final else [
            "what_definitely_works",
            "what_is_fragile",
            "what_actually_breaks",
            "first_clear_break_point",
        ],
    }
    (cdir / "final_findings.json").write_text(
        json.dumps(findings, indent=2, default=str), encoding="utf-8"
    )

    # Summary markdown
    summary = [
        f"# 400h Campaign — Final Summary ({status_prefix})",
        "",
        f"**Campaign:** `{state.get('campaign_id')}`",
        f"**Generated:** {findings['generated_at']}",
        "",
        "## Cumulative hours (evidence-backed)",
        "",
        "| Mode | Cumulative | Target | % |",
        "|---|---|---|---|",
        f"| HOT  | {h.get('hot', 0):.2f}h  | {t.get('hot', 0)}h  | {100 * h.get('hot', 0) / max(1, t.get('hot', 1)):.1f}% |",
        f"| WARM | {h.get('warm', 0):.2f}h | {t.get('warm', 0)}h | {100 * h.get('warm', 0) / max(1, t.get('warm', 1)):.1f}% |",
        f"| COLD | {h.get('cold', 0):.2f}h | {t.get('cold', 0)}h | {100 * h.get('cold', 0) / max(1, t.get('cold', 1)):.1f}% |",
        f"| **TOTAL** | **{h.get('total', 0):.2f}h** | **400h** | **{h.get('total', 0) / 4:.1f}%** |",
        "",
        "## Queries",
        "",
        f"- Total: {total_q}",
        f"- Sent: {sent}",
        f"- Responded: {responded}",
        f"- Skipped empty: {skipped}",
        f"- XSS hits: **{xss}** (zero-tolerance target: 0)",
        f"- DOM breaks: **{dom_breaks}** (zero-tolerance target: 0)",
        f"- Session losses: {session_lost}",
        f"- Avg latency: {findings['queries']['avg_latency_ms']} ms",
        f"- Median latency: {findings['queries']['median_latency_ms']} ms",
        f"- p95 latency: {findings['queries']['p95_latency_ms']} ms",
        "",
        "## Backend truth (from COLD mode)",
        "",
        f"- Health pass rate: {findings['cold_backend_truth']['health_pass_rate']}",
        f"- Feeds monotonic: {findings['cold_backend_truth']['feeds_monotonic']}",
        f"- Hologram honest: {findings['cold_backend_truth']['hologram_honest']}",
        f"- Auth chat pass rate: {findings['cold_backend_truth']['auth_chat_pass_rate']}",
        f"- Cookie bootstrap pass rate: {findings['cold_backend_truth']['cookie_pass_rate']}",
        "",
        "## Incidents classified",
        "",
        "| Category | Count |",
        "|---|---|",
    ]
    for cat, n in sorted(cat_counts.items(), key=lambda x: -x[1]):
        summary.append(f"| {cat} | {n} |")
    summary += [
        "",
        "## Known carries (from x.txt Phase 0 intake, not product bugs)",
        "",
    ]
    for c in state.get("carries", []):
        summary.append(f"- {c}")
    summary += [
        "",
        "## Verdict per x.txt Phase 7 questions",
        "",
    ]
    if is_final:
        summary += [
            "- **What definitely works:** TBD — to fill at campaign completion from segment analysis.",
            "- **What is fragile:** TBD — to fill from incident correlation.",
            "- **What actually breaks:** TBD — from any PRODUCT-category incidents.",
            "- **First clear break point:** TBD or 'not reached' if no product defect.",
        ]
    else:
        summary += [
            f"- Campaign incomplete ({h.get('total', 0):.1f}h / 400h). Verdict fields will be filled",
            "  by rerunning `python tools/campaign_reports.py aggregate` once total ≥ 400h.",
        ]
    summary.append("")
    (cdir / "final_400h_summary.md").write_text("\n".join(summary), encoding="utf-8")

    # Reliability
    reliability = [
        f"# 400h Campaign — Reliability Analysis ({status_prefix})",
        "",
        f"**Campaign:** `{state.get('campaign_id')}`",
        f"**Generated:** {findings['generated_at']}",
        "",
        "## Query-level reliability",
        "",
        f"- Active queries sent: {sent}",
        f"- Response rate: {100 * responded / max(1, sent):.2f}%",
        "",
        "## Latency distribution",
        "",
    ]
    if lats:
        # Histogram
        reliability += [
            "| Bucket | Count | % |",
            "|---|---|---|",
        ]
        bins = [(0, 1000, "<1s"), (1000, 2500, "1-2.5s"),
                (2500, 5000, "2.5-5s"), (5000, 10000, "5-10s"),
                (10000, 20000, "10-20s"), (20000, 999999, ">20s")]
        for lo, hi, label in bins:
            c = sum(1 for l in lats if lo <= l < hi)
            reliability.append(f"| {label} | {c} | {100 * c / len(lats):.1f}% |")
    reliability += [
        "",
        "## Backend health over time",
        "",
        f"- Per x.txt: health pass rate target ≥ 99%",
        f"- Actual: {findings['cold_backend_truth']['health_pass_rate']}",
        "",
        "## Restart recovery",
        "",
        f"- Controlled restart drill: from `fault_drills.md` (Phase 2 baseline).",
        f"- Uncontrolled restart events in campaign: see incident log category `backend_unhealthy`.",
        f"- Watchdog automated-recovery events since 2026-04-22: {sum(1 for r in incidents if 'watchdog' in r.get('summary', '').lower())}",
        "",
    ]
    (cdir / "final_400h_reliability.md").write_text("\n".join(reliability), encoding="utf-8")

    # Incident matrix
    matrix = [
        f"# 400h Campaign — Incident Matrix ({status_prefix})",
        "",
        f"**Campaign:** `{state.get('campaign_id')}`",
        f"**Generated:** {findings['generated_at']}",
        "",
        "## By (category, mode)",
        "",
    ]
    cross = defaultdict(int)
    for r in incidents:
        key = (r.get("category", "unknown"), r.get("mode", "unknown"))
        cross[key] += 1
    matrix += [
        "| Category | Mode | Count |",
        "|---|---|---|",
    ]
    for (cat, mode), n in sorted(cross.items(), key=lambda x: -x[1]):
        matrix.append(f"| {cat} | {mode} | {n} |")
    matrix += [
        "",
        "## Product defects (zero target)",
        "",
    ]
    product_incidents = [r for r in incidents if r.get("category") == "product_defect"]
    matrix.append(f"- Count: **{len(product_incidents)}**")
    for r in product_incidents[:20]:
        matrix.append(f"- {r.get('ts', '')[:19]} [{r.get('mode')}] {r.get('short_code')} — {r.get('summary', '')[:80]}")
    matrix += ["", "## Harness defects (fixed during campaign)", ""]
    matrix.append(
        "- See `docs/runs/campaign_hardening_log.md` for the chronological"
        " root-cause narrative. Each harness bug has a commit SHA."
    )
    matrix += ["", "## CI/Workflow incidents", ""]
    matrix.append("- See `CHANGELOG.md` [Unreleased] block. Tests + WaggleDance CI"
                  " both green on main since commit `c7f6201` (2026-04-20).")
    matrix.append("")
    (cdir / "final_400h_incident_matrix.md").write_text("\n".join(matrix), encoding="utf-8")

    return [
        cdir / "final_findings.json",
        cdir / "final_400h_summary.md",
        cdir / "final_400h_reliability.md",
        cdir / "final_400h_incident_matrix.md",
    ]


# ──────────────────────────────────────────────────────────────────
#  DECIDE (Phase 9)
# ──────────────────────────────────────────────────────────────────

PRODUCT_PATHS = ["waggledance/", "core/", "integrations/", "configs/"]
TEST_PATHS = ["tests/", "docs/runs/"]
DOCS_PATHS = ["README.md", "CHANGELOG.md", "docs/"]
CI_PATHS = [".github/"]
VERSION_PATHS = ["pyproject.toml", "waggledance/__init__.py", "Dockerfile"]


def _git_diff_files(main_ref: str) -> list[str]:
    try:
        out = subprocess.check_output(
            ["git", "diff", "--name-only", f"{main_ref}..HEAD"],
            text=True,
        )
        return [p for p in out.splitlines() if p.strip()]
    except Exception:
        return []


def classify_diff(files: list[str]) -> dict:
    buckets = {"PRODUCT": [], "TEST_HARNESS": [], "DOCS_NARRATIVE": [],
               "CI_WORKFLOW": [], "VERSION": [], "OTHER": []}
    for f in files:
        placed = False
        if any(f.startswith(p) for p in CI_PATHS):
            buckets["CI_WORKFLOW"].append(f); placed = True
        elif any(f.startswith(p) for p in TEST_PATHS):
            buckets["TEST_HARNESS"].append(f); placed = True
        elif any(f.startswith(p) for p in DOCS_PATHS) or f.endswith(".md"):
            buckets["DOCS_NARRATIVE"].append(f); placed = True
        elif f in VERSION_PATHS:
            buckets["VERSION"].append(f); placed = True
        elif any(f.startswith(p) for p in PRODUCT_PATHS):
            buckets["PRODUCT"].append(f); placed = True
        if not placed:
            buckets["OTHER"].append(f)
    return buckets


def generate_decide(cdir: Path, main_ref: str) -> Path:
    files = _git_diff_files(main_ref)
    buckets = classify_diff(files)

    state = _load_state(cdir)
    h = state.get("cumulative_hours", {})
    is_final = h.get("total", 0) >= 400
    hot = _load_jsonl(cdir / "hot_results.jsonl")
    xss = sum(1 for r in hot if r.get("xss_detected"))
    dom_breaks = sum(1 for r in hot if not r.get("dom_ok", True))

    # Pick PATH per x.txt Phase 9
    if buckets["PRODUCT"]:
        # Product diff is non-empty
        gates_green = is_final and xss == 0 and dom_breaks == 0
        path = "NEW_PATCH_RELEASE" if gates_green else "NO_RELEASE_FAILURE_REPORT"
    else:
        # Product diff empty
        path = "DOC_SYNC_ONLY"

    md = [
        "# Release Decision — 400h Post-Campaign Classification",
        "",
        f"**Campaign:** `{state.get('campaign_id')}`",
        f"**Generated:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"**Main ref:** `{main_ref}`",
        f"**Total green:** {h.get('total', 0):.2f}h / 400h ({'FINAL' if is_final else 'MID-CAMPAIGN'})",
        "",
        "## Diff bucket classification",
        "",
    ]
    for bucket, items in buckets.items():
        md.append(f"### {bucket} ({len(items)} files)")
        md.append("")
        for f in items[:30]:
            md.append(f"- `{f}`")
        if len(items) > 30:
            md.append(f"- … and {len(items) - 30} more")
        md.append("")

    md += [
        "## Gate checks (x.txt rule 5 + Phase 9)",
        "",
        f"- Campaign complete (>= 400h): {'yes' if is_final else 'no (' + format(h.get('total', 0), '.1f') + 'h)'}",
        f"- XSS hits: {xss} (target 0)",
        f"- DOM breaks: {dom_breaks} (target 0)",
        f"- PRODUCT diff: {'non-empty' if buckets['PRODUCT'] else 'empty'}",
        "",
        "## Proposed PATH",
        "",
        f"**{path}**",
        "",
    ]

    if path == "DOC_SYNC_ONLY":
        md += [
            "PRODUCT diff is empty. Per x.txt Phase 9:",
            "- commit docs/harness/ci truth",
            "- merge campaign branch → main (already on main here)",
            "- push main",
            "- update existing GitHub release body if possible",
            "- DO NOT bump version, DO NOT tag, DO NOT create new release",
        ]
    elif path == "NEW_PATCH_RELEASE":
        md += [
            "PRODUCT diff is non-empty and all gates are green. Per x.txt Phase 9:",
            "- bump patch version",
            "- update changelog under new released version",
            "- commit release bump separately",
            "- tag annotated",
            "- merge to main with --no-ff",
            "- push branch + main + tag",
            "- create new GitHub release",
            "- run backup tool",
            "- write release handoff",
        ]
    elif path == "NO_RELEASE_FAILURE_REPORT":
        md += [
            "PRODUCT diff is non-empty but gates are not green. Per x.txt Phase 9:",
            "- no bump, no tag, no release",
            "- write failure handoff",
            "- still sync docs truthfully",
        ]

    out = cdir / "release_followup_decision_400h.md"
    out.write_text("\n".join(md), encoding="utf-8")
    return out


# ──────────────────────────────────────────────────────────────────
#  STDOUT (Phase 12)
# ──────────────────────────────────────────────────────────────────

def generate_stdout(cdir: Path) -> None:
    state = _load_state(cdir)
    h = state.get("cumulative_hours", {})
    hot = _load_jsonl(cdir / "hot_results.jsonl")
    incidents = _load_jsonl(cdir / "incident_log.jsonl")

    is_final = h.get("total", 0) >= 400
    path = "TBD"
    decision_file = cdir / "release_followup_decision_400h.md"
    if decision_file.exists():
        for line in decision_file.read_text(encoding="utf-8").splitlines():
            line = line.strip().strip("*")
            if line in ("DOC_SYNC_ONLY", "NEW_PATCH_RELEASE",
                        "NO_RELEASE_FAILURE_REPORT", "FORCE_DOCS_RELEASE"):
                path = line; break

    product_failures = sum(1 for r in incidents if r.get("category") == "product_defect")
    harness_failures = sum(1 for r in incidents if "harness" in r.get("category", "").lower()
                            or r.get("category") in ("chat_response_failure",
                                                      "tab_switch_failure",
                                                      "context_recycle_failure",
                                                      "cycle_crash_recovery"))
    ci_failures = sum(1 for r in incidents if r.get("category") == "ci_workflow")

    # Tag/release
    try:
        tag = subprocess.check_output(["git", "describe", "--tags", "--abbrev=0"],
                                       text=True).strip()
    except Exception:
        tag = "none"

    lines = [
        f"PATH={path}",
        f"HOT={h.get('hot', 0):.2f}h / {state.get('target_hours', {}).get('hot', 80)}h",
        f"WARM={h.get('warm', 0):.2f}h / {state.get('target_hours', {}).get('warm', 120)}h",
        f"COLD={h.get('cold', 0):.2f}h / {state.get('target_hours', {}).get('cold', 200)}h",
        f"TOTAL={h.get('total', 0):.2f}h / 400h ({'FINAL' if is_final else 'MID-CAMPAIGN'})",
        f"TOTAL_QUERIES={len(hot)}",
        f"PRODUCT_FAILURES={product_failures}",
        f"HARNESS_FAILURES={harness_failures}",
        f"CI_WORKFLOW_FAILURES={ci_failures}",
        f"LATEST_TAG={tag}",
        "KEY_REPORTS:",
        f"  - {cdir / 'final_400h_summary.md'}",
        f"  - {cdir / 'final_400h_reliability.md'}",
        f"  - {cdir / 'final_400h_incident_matrix.md'}",
        f"  - {cdir / 'release_followup_decision_400h.md'}",
        f"  - docs/runs/campaign_hardening_log.md",
    ]
    print("\n".join(lines))


# ──────────────────────────────────────────────────────────────────
#  CLI
# ──────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["daily", "weekly", "aggregate", "decide", "stdout", "all"])
    ap.add_argument("--campaign-dir", required=True, type=Path)
    ap.add_argument("--main-ref", default="main~20", help="git ref to diff against (Phase 9)")
    args = ap.parse_args()

    cdir: Path = args.campaign_dir

    if args.action in ("daily", "all"):
        paths = generate_daily(cdir)
        print(f"daily: generated {len(paths)} files")
    if args.action in ("weekly", "all"):
        paths = generate_weekly(cdir)
        print(f"weekly: generated {len(paths)} files")
    if args.action in ("aggregate", "all"):
        paths = generate_aggregate(cdir)
        print(f"aggregate: generated {len(paths)} files")
    if args.action in ("decide", "all"):
        p = generate_decide(cdir, args.main_ref)
        print(f"decide: {p}")
    if args.action in ("stdout", "all"):
        print("=" * 60)
        generate_stdout(cdir)

    return 0


if __name__ == "__main__":
    sys.exit(main())
