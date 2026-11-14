"""LIMITS and ACTIVITY lens from the installed capacity observer (read-only), one live-loop hook per cycle.

usage: python -B sandbox/mind/capture/capacity_lens.py --status-script <pinned Get-WdCapacityStatus.ps1>
           --out-dir <cycle dir>/hook-capacity --publish-dir <live dir>/capacity [--declared <thread8>=<lane>]...

Operator 10:47Z (relayed by RCO1): the Mind must be aware of usage limits. RCO1 10:56Z (operator: reuse the
collectors that already exist): read the installed capacity observer each live cycle instead of building one.
``Get-WdCapacityStatus.ps1 -Json`` is the installed read-only locator: it verifies its own pins and runs the
observer's collector in ``--status`` mode over its own observation store; it never collects, starts or repairs
anything, and it reads no bridge log and no credentials. This hook adds nothing to that: it writes the lens view
to the cycle dir and publishes two short lens files. The raw status is never written anywhere (it carries account,
plan, token-count, rate-limit payload and model catalog fields); the view keeps the sha256 of its raw bytes as the
source digest, and nothing of the status reaches a file but the figures and labels named here.

* ``LIMITS``: per provider and window, each observation's used percent and the observed reset time (UTC and
  minutes left), with no thread identifier. Same-provider observations may share one pool, so they are listed,
  never summed. A window with no observed reset says UNKNOWN; nothing estimates a reset or a run-out time it did not
  observe. Observations whose resets differ get each their own; a reset at or before the status time says it
  passed (the figure may predate it); a missing, non-finite or unrepresentable percent, reset or age is UNKNOWN
  (null in the view), never a crash. Each line ends with the observer's own labels for its figures, never
  dropped: freshness, quota state and the oldest observation's age (the installed observer labels every Claude
  figure provider_timestamp_unknown, so its quota state is unknown). A provider whose collection never succeeded
  says so with the observer's own state.
* ``COLLECTION``: per provider the observer collects (and per failed provider), one health word from the observer's
  own figures: FAILED (collection state failed, or a failed provider), NO_SUCCESS (no successful collection), STALE
  (an attempt after the last success, or a last success older than the observer's own freshness TTL), UNKNOWN (no
  usable TTL: the age is shown, staleness is not judged) or OK; then the state, the last success and its age, the
  last attempt and the TTL. ``ALERTS``: the observer's alerts counted by provider and state; no alert id, thread or
  time is kept.
* ``ACTIVITY``: per provider, never per thread: the number of native threads, their activity and availability
  states counted, the latest successful turn and its age, and the errors counted by type with the earliest first
  error.

A status the lens cannot read whole is refused whole with a fixed reason (``refused: status_...``) and nothing is
written: one that json.loads refuses (not UTF-8 JSON, nested too deep, or an int literal longer than the
interpreter's int digit limit: 4300 digits unless -X int_max_str_digits or PYTHONINTMAXSTRDIGITS sets another, 0
for none), one that is not the observer's object, and one with a list, row or time of the wrong shape anywhere
(false, a string or one object where a list belongs; a row that is not an object; a window without a string name; a
failed provider that is not a string; a time that is not an ISO time string, or one the lens shows that has no UTC
time). An absent or null list, collection or time is one the observer did not give; the status time is required.
Every time the lens shows is in UTC. A status run that times out, raises an OSError or exits nonzero is refused the
same way.

No thread identifier, alert id, account, plan, token count or provider payload reaches a written or published
byte. Every observer string the lens copies is a known provider (claude, codex) or a short safe label (lowercase
words of up to 16 letters joined by underscores, at most six words); anything else, an e-mail, a path, a token or
an unknown provider, reads UNKNOWN, so labels that differ only there are merged. The header gives the age of the
whole status against the lens's own UTC clock: FRESH up to 120 s (the status comes from the call the lens just
made, with a 60 s deadline), STALE beyond, and FUTURE (invalid) when the status time is after the clock.
A lane name appears only when ``--declared`` gives one for an observation's thread (by its first 8 hex
digits), labelled "declared" (the observer binds no thread to a lane); the thread id itself is never written.
Never writes a bridge event, never runs gate code.
"""

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SRC))
from tools import bridge_mind_workspace as ws  # noqa: E402
from tools import wd_bridge_mind as core  # noqa: E402

STATUS_SCHEMA = "wd.capacity-status.v1"
LENS_SCHEMA = "wd.bridge-mind-capacity-lens.v1"
PROVIDERS = frozenset({"claude", "codex"})
SAFE_LABEL = re.compile(r"[a-z]{1,16}(?:_[a-z]{1,16}){0,5}")
STATUS_MAX_AGE_S = 120  # the status comes from the call the lens just made (60 s deadline)
NOTE = ("reset times are recheck times, not readiness promises; same-provider threads may share one pool and are "
        "never summed; thread to lane binding unverified unless declared; each line keeps the observer's own "
        "freshness and quota labels and the age of its oldest figure")


def clock():
    """The lens's own UTC clock; the status age is measured against it (tests inject a fixed one)."""
    return dt.datetime.now(dt.timezone.utc)


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


def given_text(value):
    """A label the observer gave as a short safe label, else UNKNOWN: never an e-mail, a path or a token."""
    return value if isinstance(value, str) and SAFE_LABEL.fullmatch(value) else ws.UNKNOWN


def provider_label(value):
    """A provider the lens knows, else UNKNOWN."""
    return value if isinstance(value, str) and value in PROVIDERS else ws.UNKNOWN


def given_label(value):
    """given_text() of a label the observer gave, None when it gave none (absent or null)."""
    return None if value is None else given_text(value)


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
    limits = {}
    for observation in rows(status.get("observations")):
        provider = provider_label(observation.get("provider"))
        # The observer's own labels for this observation's figures, as it gave them (None when it gave none).
        labels = {key: given_label(observation.get(key)) for key in ("freshness", "quota_state")}
        age = observation.get("observation_age_seconds")
        for window in windows(observation):
            used, reset_at = window.get("used_percent"), reset_time(window.get("resets_at"))
            limits.setdefault((provider, given_text(window["name"])), []).append({
                "lane": declared.get(str(observation.get("native_thread_id") or "")[:8]),
                "used_percent": used if number(used) else None,
                "reset_utc": None if reset_at is None else utc(reset_at),
                "minutes_left": None if reset_at is None else round((reset_at - now).total_seconds() / 60),
                "reset_passed": None if reset_at is None else reset_at <= now,
                "observation_age_s": age if number(age) and age >= 0 else None, **labels})
    failed = given(status.get("failed_providers"), list)
    if not all(isinstance(provider, str) for provider in failed):
        raise TypeError("a failed provider is not a string")
    failed = {provider_label(provider) for provider in failed}
    collection = {provider_label(provider): row for provider, row in given(status.get("collection"), dict).items()}
    health = {}
    for provider in sorted(set(collection) | set(failed)):
        row = collection.get(provider, {})
        if not isinstance(row, dict):
            raise TypeError("a collection row is not an object")
        success, attempt = given_time(row.get("last_success")), given_time(row.get("last_attempt"))
        state = given_label(row.get("collection_state"))
        ttl = row.get("freshness_ttl_seconds")
        ttl = ttl if number(ttl) and ttl > 0 else None
        after = None if attempt is None or success is None else attempt > success
        if state == "failed" or provider in failed:
            word = "FAILED"
        elif success is None:
            word = "NO_SUCCESS"
        elif after or (ttl is not None and (now - success).total_seconds() > ttl):
            word = "STALE"
        else:
            word = "UNKNOWN" if ttl is None else "OK"
        health[provider] = {"health": word, "collection_state": state, "freshness_ttl_s": ttl,
                            "last_success_utc": None if success is None else utc(success),
                            "minutes_since_success": (None if success is None
                                                      else round((now - success).total_seconds() / 60)),
                            "last_attempt_utc": None if attempt is None else utc(attempt),
                            "attempt_after_success": after, "failed_provider": provider in failed}
    counted = {}
    for alert in rows(status.get("alerts")):
        key = (provider_label(alert.get("provider")), given_text(alert.get("state")))
        counted[key] = counted.get(key, 0) + 1
    alerts = [{"provider": provider, "state": state, "count": count}
              for (provider, state), count in sorted(counted.items())]
    groups = {}
    for row in rows(status.get("native_activity")):
        last, first_error = given_time(row.get("last_successful_turn_at")), given_time(row.get("first_error_at"))
        group = groups.setdefault(provider_label(row.get("provider")), {"threads": 0, "activity_states": {},
                                                                    "availability_states": {}, "latest": None,
                                                                    "errors": {}})
        group["threads"] += 1
        for key, field in (("activity_states", "activity_state"), ("availability_states", "availability_state")):
            state = given_text(row.get(field))
            group[key][state] = group[key].get(state, 0) + 1
        if last is not None and (group["latest"] is None or last > group["latest"]):
            group["latest"] = last
        if row.get("error_type") not in (None, ""):
            count, first = group["errors"].get(given_text(row.get("error_type")), (0, None))
            if first_error is not None and (first is None or first_error < first):
                first = first_error
            group["errors"][given_text(row.get("error_type"))] = (count + 1, first)
    activity = [{"provider": provider, "threads": group["threads"],
                 **{key: [{"state": state, "count": count} for state, count in sorted(group[key].items())]
                    for key in ("activity_states", "availability_states")},
                 "latest_success_utc": None if group["latest"] is None else utc(group["latest"]),
                 "minutes_since_latest_success": (None if group["latest"] is None
                                                  else round((now - group["latest"]).total_seconds() / 60)),
                 "errors": [{"error_type": kind, "count": count,
                             "first_error_utc": None if first is None else utc(first)}
                            for kind, (count, first) in sorted(group["errors"].items())]}
                for provider, group in sorted(groups.items())]
    checked = clock()
    age_s = (checked - now).total_seconds()
    age = {"checked_utc": utc(checked), "age_s": age_s,
           "state": "FUTURE" if age_s < 0 else "STALE" if age_s > STATUS_MAX_AGE_S else "FRESH"}
    return now, limits, health, activity, alerts, age


def readable(status, declared):
    """project() of a whole status, else LensRefused status_shape_unexpected: a list, row or time of the wrong shape
    anywhere refuses the whole status; then text_lines() of what it kept. text_lines() sees nothing of the status but
    what project() kept, so the catch covers project() alone: an error in the lens's own text is never shown as a
    status of the wrong shape."""
    try:
        now, limits, health, activity, alerts, age = project(status, declared)
    except (KeyError, OverflowError, TypeError, ValueError):  # each one a test case reaches
        raise LensRefused("status_shape_unexpected") from None
    return now, limits, health, activity, alerts, age, text_lines(now, limits, health, activity, alerts, age)


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


def collection_text(provider, row):
    success = (f"last success {row['last_success_utc'][:16]}Z ({row['minutes_since_success']} min ago)"
               if row["last_success_utc"] else "no successful collection")
    attempt = (f"last attempt {row['last_attempt_utc'][:16]}Z" + (" (after the last success)"
                                                                  if row["attempt_after_success"] else "")
               if row["last_attempt_utc"] else "no attempt recorded")
    ttl = (f"observer TTL {row['freshness_ttl_s']} s" if row["freshness_ttl_s"] is not None
           else "no observer TTL (staleness not judged)")
    failed = "; failed provider" if row["failed_provider"] else ""
    return (f"COLLECTION {provider}: {row['health']}; state {row['collection_state'] or ws.UNKNOWN}; {success}; "
            f"{attempt}; {ttl}{failed}")


def figure(row):
    """One observation's percent, after its declared lane when one was given; never a thread id."""
    return (f"{row['lane']} (declared) " if row["lane"] else "") + percent(row["used_percent"])


def minutes(seconds):
    return "under 1 min" if seconds < 60 else f"{round(seconds / 60)} min"


def age_text(age):
    if age["state"] == "FUTURE":
        return f"status time {minutes(-age['age_s'])} ahead of the lens clock, FUTURE (invalid)"
    return f"status age {minutes(age['age_s'])}, {age['state']}"


def text_lines(now, limits, health, activity, alerts, age):
    lines = [f"capacity (installed observer, read-only) observed {utc(now)} ({age_text(age)}); {NOTE}"]
    for (provider, window), rows in sorted(limits.items()):
        used = ", ".join(figure(row) for row in rows)
        if len({row["reset_utc"] for row in rows}) > 1:  # one observation's reset is never implied for another
            when = "resets differ by observation: " + ", ".join(f"{figure(row)} {reset_text(row)}" for row in rows)
        elif rows[0]["reset_utc"] is None:
            when = "reset UNKNOWN (no readable reset observed)"
        else:
            when = ("reset " if rows[0]["reset_passed"] else "resets ") + reset_text(rows[0])
        top = max((row["used_percent"] for row in rows if row["used_percent"] is not None), default=None)
        risk = " RISK" if top is not None and top >= 80 else ""
        lines.append(f"LIMITS {provider} {window}:{risk} {used}; {when}; {observer_text(rows)}")
    for provider, row in sorted(health.items()):
        if row["last_success_utc"] is None:
            lines.append(f"LIMITS {provider}: UNKNOWN (observer has no successful collection; state "
                         f"{row['collection_state']}, last attempt {str(row['last_attempt_utc'])[:16]})")
    lines.extend(collection_text(provider, row) for provider, row in sorted(health.items()))
    if alerts:
        lines.append(f"ALERTS: {sum(row['count'] for row in alerts)} ("
                     + ", ".join(f"{row['provider']} {row['state']} x{row['count']}" for row in alerts) + ")")
    for row in activity:
        states = ", ".join(f"{item['state']} x{item['count']}" for item in row["activity_states"])
        since = (f"latest success {row['latest_success_utc'][:16]}Z ({row['minutes_since_latest_success']} min ago)"
                 if row["latest_success_utc"] else "no successful turn observed")
        errors = "".join(f"; error {item['error_type']} x{item['count']}"
                         + (f" since {item['first_error_utc'][:16]}" if item["first_error_utc"] else "")
                         for item in row["errors"])
        lines.append(f"ACTIVITY {row['provider']}: threads {row['threads']}; {states}; {since}{errors}")
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
        now, limits, health, activity, alerts, age, lines = readable(status, declared)
    except LensRefused as exc:
        raise SystemExit(f"refused: {exc}") from None
    view = {"schema": LENS_SCHEMA, "authority": "none", "scope": "shadow_observation_not_gate",
            "observed_utc": utc(now), "status_age": age, "status_sha256": hashlib.sha256(raw).hexdigest(),
            "limits": [{"provider": provider, "window": window, "observations": rows}
                       for (provider, window), rows in sorted(limits.items())],
            "collection_silent": sorted(provider for provider, row in health.items()
                                        if row["last_success_utc"] is None),
            "collection": [dict(row, provider=provider) for provider, row in sorted(health.items())],
            "failed_providers": sorted(provider for provider, row in health.items() if row["failed_provider"]),
            "alerts": alerts, "activity": activity, "note": NOTE}
    ws.guard_object(view)
    ws.guard_object(lines)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    body = json.dumps(view, indent=1, sort_keys=True).encode("ascii")
    (args.out_dir / "capacity-lens.json").write_bytes(body)
    publish(args.publish_dir, "capacity-lens.json", body)
    publish(args.publish_dir, "lens-limits.txt", ("\n".join(lines) + "\n").encode("ascii", "replace"))
    print(json.dumps({"lines": len(lines), "limits": len(view["limits"]), "collection": len(health),
                      "activity": len(activity)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
