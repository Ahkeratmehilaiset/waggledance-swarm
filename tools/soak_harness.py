#!/usr/bin/env python3
"""Hardened soak harness for WaggleDance verification.

Key improvements over ad-hoc soak scripts:
- Monotonic hard deadline — stops sending queries BEFORE wall-clock expiry
- WD launched in new process group (Windows: immune to console-close)
- Always writes final report, even on crash
- Cleanly stops only child processes it started

Usage:
    python tools/soak_harness.py --hours 2 --output C:/WaggleDance_Soak/run1
    python tools/soak_harness.py --hours 10 --api-key my-key
"""

import argparse
import json
import os
import random
import signal
import sqlite3
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "http://localhost:8000"

QUERIES = [
    "What is 15 + 27?", "Paljonko on 3 * 17?", "Calculate 100 / 4",
    "Onko -5 astetta pakkasriski?", "What is the frost risk at -10 celsius?",
    "Is 35 degrees too hot for a beehive?",
    "How to treat varroa mites?", "Miten hoidetaan varroa-häätö?",
    "What sensors detect moisture in a beehive?",
    "Mikä on mehiläispesän paras lämpötila?",
    "How does the solver router work?", "Explain the MAGMA audit trail",
    "Tell me about specialist models", "Miten yöoppiminen toimii?",
    "How to optimize energy consumption?",
    "What is WaggleDance?", "Explain autonomy layers",
    "What is a CaseTrajectory?", "How does night learning work?",
    "How do I add a new capability?",
    "Hello", "Moi", "Kerro trust-pisteistä",
    "Paljonko on 100 fahrenheit celsiuksina?",
    "Mikä on 256 - 89?", "What is the current hardware tier?",
]

SMOKE_ENDPOINTS = [
    "/api/status", "/api/learning", "/api/consciousness",
    "/api/capabilities/state", "/api/learning/state-machine",
    "/api/ops", "/api/micro_model",
]

# Monotonic time constants
QUERY_INTERVAL = (20, 30)
HEALTH_INTERVAL = 60
SMOKE_INTERVAL = 1800
LEARNING_CHECK_INTERVAL = 1800
REPORT_INTERVAL = 300
# Stop sending queries this many seconds before hard deadline
QUERY_CUTOFF_MARGIN = 120


def log(soak_log, msg):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(soak_log, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def api_get(api_key, path, timeout=15):
    try:
        req = urllib.request.Request(f"{BASE}{path}")
        req.add_header("Authorization", f"Bearer {api_key}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def api_post_chat(api_key, query, timeout=60):
    try:
        data = json.dumps({"query": query}).encode()
        req = urllib.request.Request(f"{BASE}/api/chat", data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {api_key}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "status_code": e.code}
    except Exception as e:
        return {"error": str(e)}


def sqlite_count(db_path, table):
    full = os.path.join(REPO, db_path)
    if not os.path.exists(full):
        return -1
    try:
        conn = sqlite3.connect(full, timeout=5)
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        return -1


def funnel_counts():
    return {
        "case_trajectories": sqlite_count("data/case_store.db", "case_trajectories"),
        "verifier_results": sqlite_count("data/verifier_store.db", "verifier_results"),
        "procedures": sqlite_count("data/procedural_store.db", "procedures"),
    }


def wd_alive():
    try:
        req = urllib.request.Request(f"{BASE}/api/status")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


def run_smoke(api_key):
    passed = 0
    for ep in SMOKE_ENDPOINTS:
        if api_get(api_key, ep, timeout=10) is not None:
            passed += 1
    return passed, len(SMOKE_ENDPOINTS)


def start_wd(api_key, output_dir):
    """Start WD in a new process group (immune to console-close on Windows)."""
    env = os.environ.copy()
    env["WAGGLE_API_KEY"] = api_key
    stdout = open(os.path.join(output_dir, "wd_stdout.log"), "w")
    stderr = open(os.path.join(output_dir, "wd_stderr.log"), "w")

    kwargs = dict(
        cwd=REPO, env=env, stdout=stdout, stderr=stderr,
    )
    if sys.platform == "win32":
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        DETACHED_PROCESS = 0x00000008
        kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS

    proc = subprocess.Popen(
        [sys.executable, "start_waggledance.py"],
        **kwargs,
    )
    return proc


def _is_wd_process(pid):
    """Check if PID is still a WaggleDance process (not recycled)."""
    try:
        if sys.platform == "win32":
            r = subprocess.run(
                ["wmic", "process", "where", f"ProcessId={pid}",
                 "get", "CommandLine", "/value"],
                capture_output=True, text=True, timeout=5,
            )
            return "start_waggledance" in r.stdout
        else:
            with open(f"/proc/{pid}/cmdline", "r") as f:
                return "start_waggledance" in f.read()
    except Exception:
        return False


def stop_wd(pid):
    """Stop WD process after verifying it is still a WD instance."""
    if pid is None:
        return
    if not _is_wd_process(pid):
        return  # PID recycled — do not kill
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, timeout=10)
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


_shutdown_requested = False


def main():
    parser = argparse.ArgumentParser(description="WaggleDance soak harness")
    parser.add_argument("--hours", type=float, default=2.0, help="Soak duration in hours")
    parser.add_argument("--output", default=None,
                        help="Output directory (default: C:\\WaggleDance_Soak\\<timestamp>)")
    parser.add_argument("--api-key", default="waggle-soak-verify", help="API key")
    parser.add_argument("--max-restarts", type=int, default=2, help="Max WD restarts")
    args = parser.parse_args()

    # Default output to C: (durable) instead of U: (volatile RAM disk)
    if args.output is None:
        ts = time.strftime("%Y%m%d_%H%M%S")
        args.output = os.path.join("C:\\WaggleDance_Soak", ts)

    output_dir = args.output
    os.makedirs(output_dir, exist_ok=True)

    # Register signal handlers for graceful shutdown
    global _shutdown_requested

    def _signal_handler(signum, frame):
        global _shutdown_requested
        _shutdown_requested = True

    signal.signal(signal.SIGTERM, _signal_handler)
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, _signal_handler)

    soak_log = os.path.join(output_dir, "soak.log")
    traffic_file = os.path.join(output_dir, "traffic.ndjson")
    metrics_file = os.path.join(output_dir, "metrics.ndjson")
    report_file = os.path.join(output_dir, "SOAK_REPORT.md")

    log(soak_log, "=" * 60)
    log(soak_log, f"SOAK HARNESS: {args.hours}h on main")
    log(soak_log, f"PID: {os.getpid()}, Output: {output_dir}")
    log(soak_log, "=" * 60)

    # Start WD if not already running
    wd_pid = None
    wd_started_by_us = False
    if not wd_alive():
        log(soak_log, "Starting WaggleDance (detached process group)...")
        proc = start_wd(args.api_key, output_dir)
        wd_pid = proc.pid
        wd_started_by_us = True
        for _ in range(60):
            time.sleep(1)
            if wd_alive():
                break
        if wd_alive():
            log(soak_log, f"WD started: PID {wd_pid}")
        else:
            log(soak_log, f"FATAL: WD won't start (PID {wd_pid})")
            return 1
    else:
        log(soak_log, "WD already running")

    # Baseline
    baseline_funnel = funnel_counts()
    learning = api_get(args.api_key, "/api/autonomy/learning/status") or {}
    baseline = {
        "pending_cases": learning.get("pending_cases", -1),
        "total_cycles": learning.get("total_cycles", 0),
        "scheduler_active": learning.get("scheduler", {}).get("active", False),
    }
    log(soak_log, f"Baseline funnel: {baseline_funnel}")
    log(soak_log, f"Baseline learning: {baseline}")

    stats = {
        "wd_pid": wd_pid,
        "soak_pid": os.getpid(),
        "start_time": time.time(),
        "start_mono": time.monotonic(),
        "sent": 0, "ok": 0, "fail": 0, "errors_5xx": 0,
        "routes": {},
        "latencies": [],
        "baseline_funnel": baseline_funnel,
        "baseline": baseline,
        "restarts": 0,
        "smokes": [],
        "learning_checks": [],
        "wd_started_by_us": wd_started_by_us,
    }

    duration_s = args.hours * 3600
    deadline_mono = stats["start_mono"] + duration_s
    query_cutoff_mono = deadline_mono - QUERY_CUTOFF_MARGIN

    last_health_mono = 0
    last_smoke_mono = 0
    last_learning_mono = 0
    last_report_mono = 0
    consecutive_health_fails = 0

    try:
        while True:
            now_mono = time.monotonic()

            # Signal-based graceful shutdown
            if _shutdown_requested:
                log(soak_log, "Shutdown signal received — stopping gracefully")
                stats["stop_reason"] = "signal"
                break

            # Hard deadline check (monotonic)
            if now_mono >= deadline_mono:
                log(soak_log, "Hard deadline reached — stopping")
                break

            # Only send queries before cutoff
            if now_mono < query_cutoff_mono:
                query = random.choice(QUERIES)
                t0 = time.monotonic()
                resp = api_post_chat(args.api_key, query, timeout=60)
                lat = (time.monotonic() - t0) * 1000
                stats["sent"] += 1

                entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                         "query": query[:100], "latency_ms": round(lat, 1)}

                if resp and "error" not in resp:
                    stats["ok"] += 1
                    stats["latencies"].append(lat)
                    source = resp.get("source", "unknown")
                    stats["routes"][source] = stats["routes"].get(source, 0) + 1
                    entry["status"] = "ok"
                    entry["source"] = source
                else:
                    stats["fail"] += 1
                    sc = resp.get("status_code", 0) if resp else 0
                    if sc >= 500:
                        stats["errors_5xx"] += 1
                    entry["status"] = f"fail_{sc}" if sc else "fail"

                with open(traffic_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            else:
                # Past cutoff — just wait for deadline
                remaining = deadline_mono - now_mono
                log(soak_log, f"Query cutoff reached, waiting {remaining:.0f}s for deadline")
                time.sleep(min(remaining, 10))
                continue

            # Health check
            if now_mono - last_health_mono >= HEALTH_INTERVAL:
                last_health_mono = now_mono
                alive = wd_alive()
                fc = funnel_counts()
                m = {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "alive": alive, "sent": stats["sent"],
                    "ok": stats["ok"], "fail": stats["fail"],
                    **fc,
                    "case_delta": fc["case_trajectories"] - baseline_funnel["case_trajectories"],
                    "routes": dict(stats["routes"]),
                }
                with open(metrics_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(m) + "\n")

                if alive:
                    consecutive_health_fails = 0
                else:
                    consecutive_health_fails += 1
                    log(soak_log, f"Health FAIL #{consecutive_health_fails}")
                    if consecutive_health_fails >= 3 and wd_started_by_us:
                        if stats["restarts"] < args.max_restarts:
                            log(soak_log, "Restarting WD (detached)...")
                            proc = start_wd(args.api_key, output_dir)
                            wd_pid = proc.pid
                            stats["wd_pid"] = wd_pid
                            stats["restarts"] += 1
                            consecutive_health_fails = 0
                            time.sleep(30)
                        else:
                            stats["stop_reason"] = f"unrecoverable: >{args.max_restarts} restarts"
                            break

            # Smoke test
            if now_mono - last_smoke_mono >= SMOKE_INTERVAL:
                last_smoke_mono = now_mono
                p, t = run_smoke(args.api_key)
                stats["smokes"].append({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "p": p, "t": t})
                log(soak_log, f"Smoke: {p}/{t}")

            # Learning check
            if now_mono - last_learning_mono >= LEARNING_CHECK_INTERVAL:
                last_learning_mono = now_mono
                ls = api_get(args.api_key, "/api/autonomy/learning/status") or {}
                pending = ls.get("pending_cases", -1)
                cycles = ls.get("total_cycles", 0)
                sched = ls.get("scheduler", {})
                stats["learning_checks"].append({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "pending": pending, "cycles": cycles,
                    "scheduler_active": sched.get("active", False),
                })
                log(soak_log, f"Learning: pending={pending}, cycles={cycles}, sched={sched.get('active', False)}")

            # Running report
            if now_mono - last_report_mono >= REPORT_INTERVAL:
                last_report_mono = now_mono
                _write_report(report_file, stats, baseline_funnel, baseline, final=False)

            time.sleep(random.uniform(*QUERY_INTERVAL))

    except KeyboardInterrupt:
        stats["stop_reason"] = "user interrupt"
    except Exception as e:
        log(soak_log, f"Exception: {e}\n{traceback.format_exc()}")
        stats["stop_reason"] = f"exception: {e}"

    stats.setdefault("stop_reason", "scheduled end")

    # Always write final report
    final_funnel = funnel_counts()
    final_ls = api_get(args.api_key, "/api/autonomy/learning/status") or {}
    _write_report(report_file, stats, baseline_funnel, baseline,
                  final=True, final_funnel=final_funnel, final_ls=final_ls)

    elapsed_h = (time.time() - stats["start_time"]) / 3600
    cd = final_funnel["case_trajectories"] - baseline_funnel["case_trajectories"]
    log(soak_log, "=" * 60)
    log(soak_log, f"SOAK END ({elapsed_h:.1f}h): {stats['ok']}/{stats['sent']} OK, cases +{cd}")
    log(soak_log, f"Stop reason: {stats['stop_reason']}")
    log(soak_log, f"Report: {report_file}")
    log(soak_log, "=" * 60)

    # Stop WD only if we started it
    if wd_started_by_us and wd_pid:
        log(soak_log, f"Stopping WD PID {wd_pid}")
        stop_wd(wd_pid)

    return 0


def _categorize_stderr(stderr_path):
    """Categorize stderr lines into known categories.

    Returns dict of category -> count. Traceback continuation lines
    are attributed to the preceding error category.
    """
    if not os.path.exists(stderr_path):
        return {}
    cats = {}
    try:
        with open(stderr_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return {}

    current_cat = None
    for line in lines:
        ls = line.strip()
        if not ls:
            continue
        # Classify primary error lines
        if "WinError 10054" in line or "ConnectionResetError" in line:
            current_cat = "WinError_10054"
        elif "sklearn" in line.lower() or "UserWarning" in line:
            current_cat = "sklearn_warning"
        elif "Ollama embed" in line or "Read timed out" in line:
            current_cat = "ollama_timeout"
        elif "Query embedding failed" in line:
            current_cat = "embed_failed"
        elif "UndefinedMetricWarning" in line or "R^2 score" in line:
            current_cat = "r2_undefined"
        elif "forrtl" in line or "window-CLOSE" in line.lower():
            current_cat = "forrtl_abort"
        elif "ERROR" in line and "asyncio" in line:
            current_cat = "asyncio_error"
        elif "Traceback" in line or "File \"" in line or "handle:" in line:
            # Traceback continuation — attribute to current_cat
            pass
        elif line.startswith(" ") or line.startswith("\t"):
            # Indented continuation
            pass
        else:
            current_cat = "other"

        if current_cat:
            cats[current_cat] = cats.get(current_cat, 0) + 1

    return cats


def _write_report(path, stats, bl_f, bl, final=False,
                  final_funnel=None, final_ls=None):
    fc = final_funnel or funnel_counts()
    ls = final_ls or {}
    lats = sorted(stats["latencies"][-500:]) if stats["latencies"] else [0]
    p50 = lats[len(lats) // 2] if lats else 0
    p95 = lats[int(len(lats) * 0.95)] if lats else 0
    elapsed_h = (time.time() - stats["start_time"]) / 3600
    cd = fc["case_trajectories"] - bl_f["case_trajectories"]

    cycles_after = ls.get("total_cycles", stats["learning_checks"][-1]["cycles"]
                          if stats["learning_checks"] else 0)
    learning_flowing = cycles_after > bl.get("total_cycles", 0) and cd > 0

    title = "# SOAK REPORT" if final else "# Running Report"
    r = f"""{title}

**Updated:** {time.strftime('%Y-%m-%dT%H:%M:%S')}
**Uptime:** {elapsed_h:.1f}h | **Restarts:** {stats['restarts']}
**Stop reason:** {stats.get('stop_reason', 'running')}

## Traffic

| Metric | Value |
|--------|-------|
| Sent | {stats['sent']} |
| OK | {stats['ok']} |
| Fail | {stats['fail']} |
| 5xx | {stats['errors_5xx']} |
| p50 | {p50:.0f} ms |
| p95 | {p95:.0f} ms |

## Routes

| Source | Count | % |
|--------|-------|---|
"""
    for s, c in sorted(stats["routes"].items(), key=lambda x: -x[1]):
        pct = (c / stats["ok"] * 100) if stats["ok"] > 0 else 0
        r += f"| {s} | {c} | {pct:.1f}% |\n"

    r += f"""
## Funnel

| Store | Before | After | Delta |
|-------|--------|-------|-------|
| case_trajectories | {bl_f['case_trajectories']} | {fc['case_trajectories']} | +{cd} |
| verifier_results | {bl_f['verifier_results']} | {fc['verifier_results']} | +{fc['verifier_results'] - bl_f['verifier_results']} |

## Learning

| Signal | Value |
|--------|-------|
| pending | {ls.get('pending_cases', '?')} |
| cycles | {cycles_after} |
| learning_flowing | {learning_flowing} |
"""
    if stats["learning_checks"]:
        r += "\n## Cycle Progression\n\n| Time | Pending | Cycles |\n|------|---------|--------|\n"
        for c in stats["learning_checks"]:
            r += f"| {c['ts']} | {c['pending']} | {c['cycles']} |\n"

    if stats["smokes"]:
        r += "\n## Smoke Tests\n\n| Time | Result |\n|------|--------|\n"
        for s in stats["smokes"]:
            r += f"| {s['ts']} | {s['p']}/{s['t']} |\n"

    if final:
        # Categorize stderr noise
        stderr_path = os.path.join(os.path.dirname(path), "wd_stderr.log")
        stderr_cats = _categorize_stderr(stderr_path)
        if stderr_cats:
            r += "\n## Stderr Categories\n\n| Category | Count |\n|----------|-------|\n"
            for cat, cnt in sorted(stderr_cats.items(), key=lambda x: -x[1]):
                r += f"| {cat} | {cnt} |\n"

        if stats["fail"] > stats["sent"] * 0.10:
            verdict = "HARNESS ISSUE — rerun needed"
        elif learning_flowing:
            verdict = "SOAK CLEAN"
        elif cd > 0:
            verdict = "BLOCKED — learning cycles not advancing"
        else:
            verdict = "HARNESS ISSUE — rerun needed"
        r += f"\n## Verdict\n\n**{verdict}**\n"

    with open(path, "w", encoding="utf-8") as f:
        f.write(r)


if __name__ == "__main__":
    sys.exit(main())
