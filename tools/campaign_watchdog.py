#!/usr/bin/env python3
"""Campaign watchdog — keeps gauntlet server + 3 harness modes alive.

Lessons learned from 2026-04-21 incident:
- Server died 2026-04-21 ~02:00 UTC after 15h runtime (slow resource leak)
- Harness processes died with it (can't run without backend)
- No one restarted → 23h silent downtime before user noticed

Watchdog checks every 60s:
1. Is /health reachable on port 8002? If not, restart the server.
2. Is each mode's pidfile+process alive? If not, restart that mode.
3. Has server been running > SERVER_RESTART_HOURS? Force restart to prevent
   leak accumulation (preventive, not reactive).

Run with:
    python tools/campaign_watchdog.py \
        --campaign-dir docs/runs/ui_gauntlet_400h_20260413_092800 \
        --server-restart-hours 12

Stop with Ctrl-C or by deleting the `.watchdog.pid` file in campaign dir.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER_LAUNCHER = ROOT / "docs" / "runs" / "ui_gauntlet_20260412" / "_launch_gauntlet_server.py"
HARNESS = ROOT / "tests" / "e2e" / "ui_gauntlet_400h.py"
PYTHON_EXE = ROOT / ".venv" / "Scripts" / "python.exe"
HEALTH_URL = "http://localhost:8002/health"
HEALTH_TIMEOUT_S = 10
CHECK_INTERVAL_S = 60

MODES = {
    "HOT":  {"segment_hours": 8, "target_hours": 200},
    "WARM": {"segment_hours": 8, "target_hours": 120},
    "COLD": {"segment_hours": 4, "target_hours": 200},
}


def log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


def server_healthy() -> bool:
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=HEALTH_TIMEOUT_S) as r:
            data = json.loads(r.read().decode())
            return data.get("status") == "ok"
    except Exception:
        return False


def kill_python_matching(pattern: str) -> int:
    """Kill python.exe processes whose CommandLine contains pattern. Returns count."""
    try:
        import psutil
        killed = 0
        for p in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if p.info["name"] and "python" in p.info["name"].lower():
                    cmd = " ".join(p.info.get("cmdline") or [])
                    if pattern in cmd:
                        p.kill()
                        killed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return killed
    except Exception as e:
        log(f"kill_python_matching error: {e}")
        return 0


def start_server() -> None:
    log("starting gauntlet server...")
    subprocess.Popen(
        [str(PYTHON_EXE), str(SERVER_LAUNCHER)],
        creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Wait up to 180s for health
    deadline = time.time() + 180
    while time.time() < deadline:
        if server_healthy():
            log("server is healthy")
            return
        time.sleep(5)
    log("WARNING: server did not become healthy in 180s")


def pidfile_alive(pidfile: Path) -> bool:
    if not pidfile.exists():
        return False
    try:
        import psutil
        pid = int(pidfile.read_text().strip())
        return psutil.pid_exists(pid)
    except Exception:
        return False


def start_harness_mode(mode: str, campaign_dir: Path, segment_hours: int, target_hours: int) -> None:
    log(f"starting harness {mode} (segment={segment_hours}h, target={target_hours}h)")
    args = [
        str(PYTHON_EXE), str(HARNESS),
        "--mode", mode,
        "--segment-hours", str(segment_hours),
        "--loop",
        "--target-hours", str(target_hours),
        "--campaign-dir", str(campaign_dir),
    ]
    subprocess.Popen(
        args,
        creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign-dir", required=True, type=Path)
    ap.add_argument("--server-restart-hours", type=float, default=12.0,
                    help="Force server restart after this many hours (0 = never)")
    args = ap.parse_args()

    campaign_dir: Path = args.campaign_dir
    campaign_dir.mkdir(parents=True, exist_ok=True)

    # Single-watchdog pid file
    wd_pid = campaign_dir / ".watchdog.pid"
    if wd_pid.exists():
        try:
            import psutil
            old_pid = int(wd_pid.read_text().strip())
            if psutil.pid_exists(old_pid):
                log(f"another watchdog already running (pid={old_pid}), exiting")
                return 1
        except Exception:
            pass
    wd_pid.write_text(str(os.getpid()))

    log(f"watchdog starting, campaign_dir={campaign_dir}")
    log(f"preventive server restart every {args.server_restart_hours}h")

    server_started_at = time.time()

    try:
        while True:
            # 1. Server health check
            if not server_healthy():
                log("server unhealthy, killing any stale server processes and restarting")
                kill_python_matching("_launch_gauntlet_server")
                time.sleep(3)
                start_server()
                server_started_at = time.time()
                # Harness will auto-detect backend and reconnect; nothing to do here.

            # 2. Preventive restart (defense against slow memory leak)
            if args.server_restart_hours > 0:
                hours_alive = (time.time() - server_started_at) / 3600
                if hours_alive >= args.server_restart_hours:
                    log(f"preventive server restart after {hours_alive:.1f}h (leak defense)")
                    # Stop harness first, then server, then restart both
                    for mode in MODES:
                        kill_python_matching(f"--mode {mode}")
                    time.sleep(2)
                    kill_python_matching("_launch_gauntlet_server")
                    for pf in campaign_dir.glob("*.pid"):
                        if pf.name != ".watchdog.pid":
                            pf.unlink(missing_ok=True)
                    time.sleep(3)
                    start_server()
                    server_started_at = time.time()

            # 3. Per-mode harness health check
            for mode, cfg in MODES.items():
                pf = campaign_dir / f"{mode.lower()}.pid"
                if not pidfile_alive(pf):
                    # Clear stale pidfile if present
                    pf.unlink(missing_ok=True)
                    if server_healthy():
                        start_harness_mode(mode, campaign_dir,
                                           cfg["segment_hours"], cfg["target_hours"])

            time.sleep(CHECK_INTERVAL_S)
    except KeyboardInterrupt:
        log("watchdog stopped by user")
        return 0
    finally:
        try:
            wd_pid.unlink(missing_ok=True)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
