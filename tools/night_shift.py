#!/usr/bin/env python3
"""
WaggleDance Night Shift Monitor v1.0
======================================
Standalone script for automated night-time operation.
Starts WaggleDance in production mode, monitors health,
and writes a morning report.

Usage:
    python tools/night_shift.py                    # Run 8 hours (default)
    python tools/night_shift.py --duration 4       # Run 4 hours
    python tools/night_shift.py --report-only      # Generate report from existing data

Windows Task Scheduler:
    schtasks /create /sc DAILY /st 00:00 /tn "WaggleDance NightShift" ^
        /tr "python U:\\project2\\tools\\night_shift.py --duration 8"
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# ── Windows UTF-8 ────────────────────────────────────────────────
if sys.platform == "win32":
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
HEALTH_URL = "http://localhost:8000/api/consciousness"
FEEDS_URL = "http://localhost:8000/api/feeds"
LOG_FILE = DATA_DIR / "night_shift.log"
CHECK_INTERVAL_S = 300  # 5 minutes
MAX_CONSECUTIVE_FAILURES = 3
STARTUP_WAIT_S = 60  # wait for WaggleDance to boot


def log(msg: str):
    """Write timestamped message to log file and stdout."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        DATA_DIR.mkdir(exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def health_check() -> dict | None:
    """GET /api/consciousness, return parsed JSON or None on failure."""
    try:
        req = urllib.request.Request(HEALTH_URL, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def feeds_check() -> dict | None:
    """GET /api/feeds, return parsed JSON or None."""
    try:
        req = urllib.request.Request(FEEDS_URL, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def start_waggledance() -> subprocess.Popen:
    """Start WaggleDance in production mode as a subprocess."""
    cmd = [sys.executable, str(PROJECT_ROOT / "main.py")]
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    log(f"Starting WaggleDance: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    log(f"WaggleDance PID: {proc.pid}")
    return proc


def stop_waggledance(proc: subprocess.Popen):
    """Gracefully stop WaggleDance subprocess."""
    if proc.poll() is not None:
        return
    log("Stopping WaggleDance...")
    try:
        if sys.platform == "win32":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.send_signal(signal.SIGINT)
        proc.wait(timeout=30)
    except (subprocess.TimeoutExpired, OSError):
        log("Graceful stop failed, killing process")
        proc.kill()
        proc.wait(timeout=10)
    log("WaggleDance stopped")


def collect_learning_progress() -> dict:
    """Read learning_progress.json."""
    try:
        path = DATA_DIR / "learning_progress.json"
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def collect_metrics_summary() -> dict:
    """Summarize learning_metrics.jsonl."""
    summary = {
        "total_entries": 0,
        "enrichment_cycles": 0,
        "total_facts_stored": 0,
        "unique_agents": set(),
        "recent_topics": [],
    }
    try:
        path = DATA_DIR / "learning_metrics.jsonl"
        if not path.exists():
            return summary
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                summary["total_entries"] += 1
                try:
                    entry = json.loads(line)
                    if entry.get("event") == "enrichment_cycle":
                        summary["enrichment_cycles"] += 1
                        summary["total_facts_stored"] = max(
                            summary["total_facts_stored"],
                            entry.get("total_session_stored", 0))
                        agent = entry.get("agent_id", "")
                        if agent:
                            summary["unique_agents"].add(agent)
                        topic = entry.get("topic", "")
                        if topic:
                            summary["recent_topics"].append(topic[:30])
                            if len(summary["recent_topics"]) > 10:
                                summary["recent_topics"] = summary["recent_topics"][-10:]
                except json.JSONDecodeError:
                    pass
    except Exception:
        pass
    summary["unique_agents"] = len(summary["unique_agents"])
    return summary


def generate_report(
    start_time: float,
    health_history: list[dict],
    restarts: int,
    errors: list[str],
) -> str:
    """Generate morning report markdown."""
    now = datetime.now()
    uptime_h = (time.time() - start_time) / 3600
    progress = collect_learning_progress()
    metrics = collect_metrics_summary()

    first_health = health_history[0] if health_history else {}
    last_health = health_history[-1] if health_history else {}

    lines = [
        f"# WaggleDance Night Shift Report — {now.strftime('%Y-%m-%d')}",
        "",
        f"## Summary",
        f"- **Uptime:** {uptime_h:.1f} hours",
        f"- **Restarts:** {restarts}",
        f"- **Errors:** {len(errors)}",
        f"- **Health checks:** {len(health_history)}",
        "",
        "## NightEnricher",
        f"- Facts learned (persistent): {progress.get('night_mode_facts_learned', '?')}",
        f"- Enricher session stored: {progress.get('enricher_session_stored', '?')}",
        f"- Enrichment cycles: {metrics['enrichment_cycles']}",
        f"- Max session stored: {metrics['total_facts_stored']}",
        f"- Unique agents enriched: {metrics['unique_agents']}",
        f"- Recent topics: {', '.join(metrics['recent_topics'][-5:])}",
        "",
        "## Health",
        f"- First check: memory={first_health.get('memory_count', '?')}, "
        f"cache_hit={first_health.get('cache_hit_rate', '?')}",
        f"- Last check: memory={last_health.get('memory_count', '?')}, "
        f"cache_hit={last_health.get('cache_hit_rate', '?')}",
        "",
        "## Metrics Log",
        f"- Total entries in learning_metrics.jsonl: {metrics['total_entries']}",
        "",
    ]

    if errors:
        lines.append("## Errors")
        for e in errors[-10:]:
            lines.append(f"- {e}")
        lines.append("")

    lines.append(f"---")
    lines.append(f"*Generated: {now.strftime('%Y-%m-%d %H:%M')} by night_shift.py v1.0*")
    return "\n".join(lines)


def run_night_shift(duration_hours: float):
    """Main night shift loop."""
    start_time = time.time()
    end_time = start_time + duration_hours * 3600
    health_history = []
    restarts = 0
    errors = []
    consecutive_failures = 0

    log(f"Night shift starting — duration: {duration_hours}h")
    log(f"Expected end: {datetime.fromtimestamp(end_time).strftime('%H:%M')}")

    proc = start_waggledance()

    # Wait for startup
    log(f"Waiting {STARTUP_WAIT_S}s for WaggleDance to boot...")
    time.sleep(STARTUP_WAIT_S)

    try:
        while time.time() < end_time:
            # Check subprocess
            if proc.poll() is not None:
                exit_code = proc.returncode
                msg = f"WaggleDance exited with code {exit_code}"
                log(f"CRASH: {msg}")
                errors.append(msg)
                restarts += 1

                if restarts > 5:
                    log("Too many restarts (>5), aborting night shift")
                    break

                log("Restarting in 30s...")
                time.sleep(30)
                proc = start_waggledance()
                time.sleep(STARTUP_WAIT_S)
                consecutive_failures = 0
                continue

            # Health check
            health = health_check()
            if health:
                health_history.append(health)
                consecutive_failures = 0
                mem = health.get("memory_count", "?")
                halluc = health.get("hallucination_rate", "?")
                log(f"Health OK — memory: {mem}, halluc: {halluc}")

                # Check hallucination rate
                if isinstance(halluc, (int, float)) and halluc > 5.0:
                    msg = f"Hallucination rate HIGH: {halluc}%"
                    log(f"WARNING: {msg}")
                    errors.append(msg)
            else:
                consecutive_failures += 1
                log(f"Health check failed ({consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})")

                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    msg = f"Health check failed {MAX_CONSECUTIVE_FAILURES}x — restarting"
                    log(msg)
                    errors.append(msg)
                    stop_waggledance(proc)
                    restarts += 1
                    time.sleep(10)
                    proc = start_waggledance()
                    time.sleep(STARTUP_WAIT_S)
                    consecutive_failures = 0

            # Sleep until next check
            remaining = end_time - time.time()
            sleep_time = min(CHECK_INTERVAL_S, max(0, remaining))
            if sleep_time > 0:
                time.sleep(sleep_time)

    except KeyboardInterrupt:
        log("Night shift interrupted by user")
    finally:
        stop_waggledance(proc)

    # Generate report
    report = generate_report(start_time, health_history, restarts, errors)
    report_name = f"night_shift_report_{datetime.now().strftime('%Y%m%d')}.md"
    report_path = DATA_DIR / report_name
    report_path.write_text(report, encoding="utf-8")
    log(f"Morning report written: {report_path}")

    # Also print summary
    print("\n" + "=" * 55)
    print("  NIGHT SHIFT COMPLETE")
    print("=" * 55)
    print(f"  Uptime: {(time.time() - start_time) / 3600:.1f}h")
    print(f"  Restarts: {restarts}")
    print(f"  Errors: {len(errors)}")
    print(f"  Health checks: {len(health_history)}")
    print(f"  Report: {report_path.name}")
    print("=" * 55)


def report_only():
    """Generate report from existing data without running WaggleDance."""
    report = generate_report(time.time(), [], 0, [])
    report_name = f"night_shift_report_{datetime.now().strftime('%Y%m%d')}.md"
    report_path = DATA_DIR / report_name
    report_path.write_text(report, encoding="utf-8")
    print(f"Report generated: {report_path}")
    print(report)


def main():
    parser = argparse.ArgumentParser(
        description="WaggleDance Night Shift Monitor v1.0")
    parser.add_argument(
        "--duration", type=float, default=8.0,
        help="Duration in hours (default: 8)")
    parser.add_argument(
        "--report-only", action="store_true",
        help="Generate report from existing data")
    args = parser.parse_args()

    if args.report_only:
        report_only()
    else:
        run_night_shift(args.duration)


if __name__ == "__main__":
    main()
