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
  reset says UNKNOWN; nothing estimates a reset or a run-out time it did not observe. Threads whose resets differ
  get each their own; a reset at or before the status time says it passed (the figure may predate it); a missing,
  non-finite or unrepresentable percent, reset or age is UNKNOWN (null in the view), never a crash. Each line
  ends with the observer's own labels for its figures, never dropped: freshness, quota state and the oldest
  observation's age (the installed observer labels every Claude figure provider_timestamp_unknown, so its quota
  state is unknown). A provider whose collection never succeeded says so with the observer's own state.
* ``ACTIVITY``: per native thread, the observer's activity state, the last successful turn and the first error.

A status the lens cannot read whole is refused whole with a fixed reason (``refused: status_...``) and nothing is
written: one that json.loads refuses (not UTF-8 JSON, nested too deep, or an int literal longer than the
interpreter's int digit limit: 4300 digits unless -X int_max_str_digits or PYTHONINTMAXSTRDIGITS sets another, 0
for none), one that is not the observer's object, and one with a list, row or time of the wrong shape anywhere
(false, a string or one object where a list belongs; a row that is not an object; a window without a string name; a
time that is not an ISO time string, or one the lens shows that has no UTC time). An absent or null list, collection
or time is one the observer did not give; the status time is required. Every time the lens shows is in UTC. A status
run that times out, raises an OSError or exits nonzero is refused the same way.

Threads are named by their first 8 hex digits. A lane name appears only when ``--declared`` gives one, labelled
"declared" (the observer binds no thread to a lane). Never writes a bridge event, never runs gate code.
"""

import argparse
import datetime as dt
import hashlib
import json
import math
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
        "never summed; thread to lane binding unverified unless declared; each line keeps the observer's own "
        "freshness and quota labels and the age of its oldest figure")


def utc(value):
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(text):
    """An observed time, aware (a naive one is read as UTC): TypeError when it is not a string, ValueError when it
    is not an ISO time (str() of a number was one: 20261013 is an ISO basic date)."""
    if not isinstance(text, str):
        raise TypeError("a time is not a string")
    when = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)


def given_time(value):
    """A time the observer may leave out: None when it gave none (absent or null), else parse_time(); false or
    empty text is never read as none."""
    return None if value is None else parse_time(value)


class LensRefused(RuntimeError):
    """Fixed reasons only, safe to print: a status is shown whole or refused whole, never in part."""


def read_status(script):
    """The status script's raw output and its status document, else LensRefused: status_timed_out after 60 s;
    status_not_started for an OSError out of subprocess.run (as when powershell.exe is missing or may not run; the
    name does not prove that no process started); status_failed for a nonzero exit."""
    try:
        done = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                               "-Json"], capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise LensRefused("status_timed_out") from None
    except OSError:
        raise LensRefused("status_not_started") from None
    if done.returncode != 0:
        raise LensRefused("status_failed")
    return done.stdout, parse_status(done.stdout)


def parse_status(raw):
    """The observer's status document from its raw bytes, else LensRefused: status_unreadable when json.loads refuses
    it (not UTF-8 JSON, nested too deep, or an int literal over the int digit limit), status_schema_unexpected when
    it is not the observer's object."""
    try:
        status = json.loads(raw.decode("utf-8-sig"))
    except (RecursionError, ValueError):  # the decode, JSON and int-digit errors are all ValueErrors
        raise LensRefused("status_unreadable") from None
    if (not isinstance(status, dict) or status.get("schema") != STATUS_SCHEMA
            or status.get("execution_allowed") is not False):
        raise LensRefused("status_schema_unexpected")
    return status


def given(value, kind):
    """The list or object the observer gave, empty when it gave none (absent or null); any other value is the wrong
    shape and raises TypeError (false, a string or one object where a list belongs is never read as no rows)."""
    if value is None:
        return kind()
    if not isinstance(value, kind):
        raise TypeError(f"not a {kind.__name__}")
    return value


def rows(value):
    """The observer's list of row objects; a row that is not an object raises TypeError, it is never skipped."""
    items = given(value, list)
    if not all(isinstance(item, dict) for item in items):
        raise TypeError("a row is not an object")
    return items


def windows(observation):
    """The observation's quota windows; a window that is not an object with a string name raises TypeError."""
    found = rows(observation.get("quota_windows"))
    if not all(isinstance(window.get("name"), str) for window in found):
        raise TypeError("a window has no name")
    return found


def number(value):
    """A real, finite number (never a bool) inside the float range: the only kind of percent, reset or age the lens
    shows. json.loads reads an int literal up to the int digit limit (a longer one refuses the whole status, see
    parse_status), and an int beyond the float range is no such number."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # an int too large for a float
        return False


def reset_time(value):
    """An observed reset as a UTC time; None when it is absent, not a number or not a representable epoch second."""
    if not number(value):
        return None
    try:
        return dt.datetime.fromtimestamp(value, dt.timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def project(status, declared):
    now = parse_time(status["observed_at"]).astimezone(dt.timezone.utc)  # one with no UTC time stops here
    name = {thread: f"thread {thread} ({lane}, declared)" for thread, lane in declared.items()}

    def label(thread):
        short = str(thread or "")[:8]
        return name.get(short, f"thread {short or ws.UNKNOWN}")

    limits = {}
    for observation in rows(status.get("observations")):
        provider = str(observation.get("provider"))
        # The observer's own labels for this observation's figures, as it gave them (None when it gave none).
        labels = {key: observation.get(key) if isinstance(observation.get(key), str) else None
                  for key in ("freshness", "quota_state")}
        age = observation.get("observation_age_seconds")
        for window in windows(observation):
            used, reset_at = window.get("used_percent"), reset_time(window.get("resets_at"))
            limits.setdefault((provider, window["name"]), []).append({
                "thread": label(observation.get("native_thread_id")),
                "used_percent": used if number(used) else None,
                "reset_utc": None if reset_at is None else utc(reset_at),
                "minutes_left": None if reset_at is None else round((reset_at - now).total_seconds() / 60),
                "reset_passed": None if reset_at is None else reset_at <= now,
                "observation_age_s": age if number(age) and age >= 0 else None, **labels})
    silent = {}
    for provider, row in given(status.get("collection"), dict).items():
        if not isinstance(row, dict):
            raise TypeError("a collection row is not an object")
        success, attempt = given_time(row.get("last_success")), given_time(row.get("last_attempt"))
        if success is None:
            silent[provider] = {"collection_state": row.get("collection_state"),
                                "last_attempt_utc": None if attempt is None else utc(attempt)}
    activity = []
    for row in rows(status.get("native_activity")):
        last, first_error = given_time(row.get("last_successful_turn_at")), given_time(row.get("first_error_at"))
        activity.append({"thread": label(row.get("native_thread_id")), "provider": row.get("provider"),
                         "activity_state": row.get("activity_state"),
                         "availability_state": row.get("availability_state"),
                         "last_successful_turn_utc": None if last is None else utc(last),
                         "minutes_since_success": None if last is None else round((now - last).total_seconds() / 60),
                         "error_type": row.get("error_type"),
                         "first_error_utc": None if first_error is None else utc(first_error)})
    return now, limits, silent, activity


def readable(status, declared):
    """project() of a whole status, else LensRefused status_shape_unexpected: a list, row or time of the wrong shape
    anywhere refuses the whole status; then text_lines() of what it kept. text_lines() sees nothing of the status but
    what project() kept, so the catch covers project() alone: an error in the lens's own text is never shown as a
    status of the wrong shape."""
    try:
        now, limits, silent, activity = project(status, declared)
    except (KeyError, OverflowError, TypeError, ValueError):  # each one a test case reaches
        raise LensRefused("status_shape_unexpected") from None
    return now, limits, silent, activity, text_lines(now, limits, silent, activity)


def percent(value):
    return f"{value}%" if number(value) else ws.UNKNOWN


def reset_text(row):
    if row["reset_utc"] is None:
        return ws.UNKNOWN
    if row["reset_passed"]:
        return f"{row['reset_utc'][:16]}Z passed {-row['minutes_left']} min ago"
    return f"{row['reset_utc'][:16]}Z (in {row['minutes_left']} min)"


def observer_text(rows):
    """The observer's own labels for the figures of one line, never dropped; UNKNOWN where it gave none."""
    freshness = ", ".join(sorted({row["freshness"] or ws.UNKNOWN for row in rows}))
    quota = ", ".join(sorted({row["quota_state"] or ws.UNKNOWN for row in rows}))
    ages = [row["observation_age_s"] for row in rows]
    if None in ages:
        age = "oldest age UNKNOWN"
    else:
        oldest = max(ages)
        age = "under 1 min old" if oldest < 60 else f"up to {round(oldest / 60)} min old"
    return f"observer {freshness}, quota {quota}, {age}"


def text_lines(now, limits, silent, activity):
    lines = [f"capacity (installed observer, read-only) observed {utc(now)}; {NOTE}"]
    for (provider, window), rows in sorted(limits.items()):
        used = ", ".join(f"{row['thread'][len('thread '):]} {percent(row['used_percent'])}" for row in rows)
        if len({row["reset_utc"] for row in rows}) > 1:  # one thread's reset is never implied for another
            when = "resets differ by thread: " + ", ".join(
                f"{row['thread'][len('thread '):]} {reset_text(row)}" for row in rows)
        elif rows[0]["reset_utc"] is None:
            when = "reset UNKNOWN (no readable reset observed)"
        else:
            when = ("reset " if rows[0]["reset_passed"] else "resets ") + reset_text(rows[0])
        top = max((row["used_percent"] for row in rows if row["used_percent"] is not None), default=None)
        risk = " RISK" if top is not None and top >= 80 else ""
        lines.append(f"LIMITS {provider} {window}:{risk} {used}; {when}; {observer_text(rows)}")
    for provider, row in sorted(silent.items()):
        lines.append(f"LIMITS {provider}: UNKNOWN (observer has no successful collection; state "
                     f"{row['collection_state']}, last attempt {str(row['last_attempt_utc'])[:16]})")
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
    try:
        raw, status = read_status(args.status_script)
        now, limits, silent, activity, lines = readable(status, declared)
    except LensRefused as exc:
        raise SystemExit(f"refused: {exc}") from None
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
