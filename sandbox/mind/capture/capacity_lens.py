"""LIMITS and ACTIVITY lens from the installed capacity observer (read-only), one live-loop hook per cycle.

usage: python -B sandbox/mind/capture/capacity_lens.py --status-script <pinned Get-WdCapacityStatus.ps1>
           --out-dir <cycle dir>/hook-capacity --publish-dir <live dir>/capacity [--declared <thread8>=<lane>]...

Operator 10:47Z (relayed by RCO1): the Mind must be aware of usage limits. RCO1 10:56Z (operator: reuse the
collectors that already exist): read the installed capacity observer each live cycle instead of building one.
``Get-WdCapacityStatus.ps1 -Json`` is the installed read-only locator: it verifies its own pins and runs the
observer's collector in ``--status`` mode over its own observation store; it never collects, starts or repairs
anything, and it reads no bridge log and no credentials. This hook adds nothing to that: it keeps the raw status
in the cycle dir and publishes two short lens files.

* ``LIMITS``: per provider and window, each thread's used percent and the observed reset time (UTC and minutes
  left). Same-provider threads may share one pool, so they are listed, never summed. A window with no observed
  reset says UNKNOWN; nothing estimates a reset or a run-out time it did not observe. A provider whose collection
  never succeeded says so with the observer's own state.
* ``ACTIVITY``: per native thread, the observer's activity state, the last successful turn and the first error.

Threads are named by their first 8 hex digits. A lane name appears only when ``--declared`` gives one, labelled
"declared" (the observer binds no thread to a lane). Never writes a bridge event, never runs gate code.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SRC))
from tools import bridge_mind_workspace as ws  # noqa: E402
from tools import wd_bridge_mind as core  # noqa: E402

STATUS_SCHEMA = "wd.capacity-status.v1"
LENS_SCHEMA = "wd.bridge-mind-capacity-lens.v1"
OLD_IDLE_MIN = 12 * 60  # idle, error-free threads older than this fold into one ACTIVITY line
NOTE = ("reset times are recheck times, not readiness promises; same-provider threads may share one pool and are "
        "never summed; thread to lane binding unverified unless declared")


def utc(value):
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(text):
    when = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def read_status(script):
    done = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Json"],
                          capture_output=True, timeout=60)
    if done.returncode != 0:
        raise RuntimeError("status_failed")
    status = json.loads(done.stdout.decode("utf-8-sig"))
    if status.get("schema") != STATUS_SCHEMA or status.get("execution_allowed") is not False:
        raise RuntimeError("status_schema_unexpected")
    return done.stdout, status


def windows(observation):
    for window in observation.get("quota_windows") or []:
        if isinstance(window, dict) and isinstance(window.get("name"), str):
            yield window


def project(status, declared):
    now = parse_time(status["observed_at"])
    name = {thread: f"thread {thread} ({lane}, declared)" for thread, lane in declared.items()}

    def label(thread):
        short = str(thread or "")[:8]
        return name.get(short, f"thread {short or ws.UNKNOWN}")

    limits = {}
    for observation in status.get("observations") or []:
        provider = str(observation.get("provider"))
        for window in windows(observation):
            reset = window.get("resets_at")
            reset_at = (dt.datetime.fromtimestamp(reset, dt.timezone.utc)
                        if isinstance(reset, (int, float)) and not isinstance(reset, bool) else None)
            limits.setdefault((provider, window["name"]), []).append({
                "thread": label(observation.get("native_thread_id")),
                "used_percent": window.get("used_percent"),
                "reset_utc": None if reset_at is None else utc(reset_at),
                "minutes_left": None if reset_at is None else round((reset_at - now).total_seconds() / 60),
                "observation_age_s": observation.get("observation_age_seconds")})
    collection = status.get("collection") or {}
    silent = {provider: row for provider, row in collection.items()
              if isinstance(row, dict) and not row.get("last_success")}
    activity = []
    for row in status.get("native_activity") or []:
        if not isinstance(row, dict):
            continue
        last = row.get("last_successful_turn_at")
        activity.append({"thread": label(row.get("native_thread_id")), "provider": row.get("provider"),
                         "activity_state": row.get("activity_state"),
                         "availability_state": row.get("availability_state"),
                         "last_successful_turn_utc": None if not last else utc(parse_time(last)),
                         "minutes_since_success": None if not last else round(
                             (now - parse_time(last)).total_seconds() / 60),
                         "error_type": row.get("error_type"), "first_error_utc": row.get("first_error_at")})
    return now, limits, silent, activity


def text_lines(now, limits, silent, activity):
    lines = [f"capacity (installed observer, read-only) observed {utc(now)}; {NOTE}"]
    for (provider, window), rows in sorted(limits.items()):
        resets = sorted({(row["reset_utc"], row["minutes_left"]) for row in rows if row["reset_utc"]})
        used = ", ".join(f"{row['thread'][len('thread '):]} {row['used_percent']}%" for row in rows)
        when = ("; ".join(f"resets {reset[:16]}Z (in {left} min)" for reset, left in resets)
                if resets else "reset UNKNOWN (not observed)")
        top = max((row["used_percent"] for row in rows if isinstance(row["used_percent"], (int, float))), default=None)
        risk = " RISK" if isinstance(top, (int, float)) and top >= 80 else ""
        lines.append(f"LIMITS {provider} {window}:{risk} {used}; {when}")
    for provider, row in sorted(silent.items()):
        lines.append(f"LIMITS {provider}: UNKNOWN (observer has no successful collection; state "
                     f"{row.get('collection_state')}, last attempt {str(row.get('last_attempt'))[:16]})")
    old = [row for row in activity if row["error_type"] is None and row["activity_state"] == "idle_observed"
           and isinstance(row["minutes_since_success"], int) and row["minutes_since_success"] > OLD_IDLE_MIN]
    for row in sorted((row for row in activity if row not in old), key=lambda item: item["thread"]):
        since = (f"last success {row['last_successful_turn_utc'][11:16]}Z ({row['minutes_since_success']} min ago)"
                 if row["last_successful_turn_utc"] else "no successful turn observed")
        error = f"; error {row['error_type']} since {str(row['first_error_utc'])[:16]}" if row["error_type"] else ""
        lines.append(f"ACTIVITY {row['provider']} {row['thread']}: {row['activity_state']}; {since}{error}")
    if old:
        lines.append(f"ACTIVITY: {len(old)} more threads idle with no error, last success over "
                     f"{OLD_IDLE_MIN // 60} h ago (oldest {max(row['minutes_since_success'] for row in old) // 60} h)")
    return lines


def publish(directory, name, data):
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f".{name}.{os.getpid()}.tmp"
    tmp.write_bytes(data)
    core._sharing_retry(lambda: os.replace(tmp, directory / name))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--status-script", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--publish-dir", type=Path, required=True)
    parser.add_argument("--declared", action="append", default=[], help="<thread first 8 hex>=<lane>")
    args = parser.parse_args()
    declared = {}
    for item in args.declared:
        thread, _, lane = item.partition("=")
        if not (ws.SAFE_ID.fullmatch(lane) and len(thread) == 8 and all(c in "0123456789abcdef" for c in thread)):
            raise SystemExit("refused: declared mapping invalid")
        declared[thread] = lane
    raw, status = read_status(args.status_script)
    now, limits, silent, activity = project(status, declared)
    lines = text_lines(now, limits, silent, activity)
    view = {"schema": LENS_SCHEMA, "authority": "none", "scope": "shadow_observation_not_gate",
            "observed_utc": utc(now), "status_sha256": hashlib.sha256(raw).hexdigest(),
            "limits": [{"provider": provider, "window": window, "threads": rows}
                       for (provider, window), rows in sorted(limits.items())],
            "collection_silent": sorted(silent), "activity": activity, "note": NOTE}
    ws.guard_object(view)
    ws.guard_object(lines)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "capacity-status.json").write_bytes(raw)
    body = json.dumps(view, indent=1, sort_keys=True).encode("ascii")
    (args.out_dir / "capacity-lens.json").write_bytes(body)
    publish(args.publish_dir, "capacity-lens.json", body)
    publish(args.publish_dir, "lens-limits.txt", ("\n".join(lines) + "\n").encode("ascii", "replace"))
    print(json.dumps({"lines": len(lines), "limits": len(view["limits"]), "activity": len(activity)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
