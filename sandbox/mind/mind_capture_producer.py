"""Bridge Mind sandbox capture producer: the live loop that reads the bridge and GitHub and runs the shadow.

usage: python -B sandbox/mind/mind_capture_producer.py --src <code tree> --live-dir <dir inside a .codex-audit root>
           --bin <pinned .agent-bridge/bin> [--cycles N] [--interval S] [--window W] [--workers K] [--keep K]

Every cycle:
  1. FENCE-FREE BRIDGE CAPTURE through the inherited pinned reader, compact path only (a non-Compact read, -Raw
     included, drains the accepted queue under the global publication fence; the compact path is a pure read):
     ``Read-AgentBridge.ps1 -Compact -AfterEvent <position cursor>`` lists the new event refs, and
     ``Read-AgentBridge.ps1 -Compact -EventId <ref>`` returns each full row, whose canonical ref must equal the
     listed one. The reader options must be one of the three calls this producer makes, slot by slot, or no
     process starts (``reader_call``; no -Raw or other flag can get through); --window is held to
     1..TAIL_CAP - SEED_MARGIN lines and --keep to at least 1 at parse time, before the code tree's imports and any
     write or process start (``count_problem``). The first cycle seeds the window from
     ``-Compact -ContinuityTail <window>``; with --seed-from, a row of an earlier capture whose own canonical ref
     equals a listed ref is reused instead of read again: that file is read whole once, after the code tree's
     imports and before any write or process start, and one it cannot read whole is refused with a fixed reason
     and exit 2 (``read_seed``; a seed file changed later is not read again). The window slides; it is persisted under
     <live-dir>/state so a restart continues from the cursor, but only when every persisted row still verifies
     against its own canonical ref and sha256 and no ref repeats. A missing, undecodable or unverified state, an
     empty window or a delta the reader refuses (its listing, or a detail read: -EventId reads only the last
     DETAIL_TAIL lines) reseeds; after a refused delta the reseed reuses the verified rows it lists again, and the
     read record's reseed_after names the refused read and its fixed reason, never a ref (no gap or coverage
     record); the record counts the final mode only. A reader that times out or raises OSError, an output that is
     not one JSON object, a listing whose events are not a list of objects each with a scalar ref and a detail row
     whose canonical ref cannot be built refuse the cycle with a fixed reason and never reseed (``CycleRefused``).
     The capture's reader_stdout_sha256 is the sha256 of the manifest (ref, canonical row sha256, source) in
     window order, kept next to the capture.
  2. PR CONTEXT (observational, authority none; the only network touch, read-only GitHub metadata): one
     ``gh pr list`` (state, head, branch); for OPEN PRs ``gh pr view --json files,headRefOid,commits`` cached per
     PR and head: the changed paths classified with the code tree's own charter (evaluate_paths) and classifier
     (classify_ab), and the declared commit author names. charter.loaded is true only when the charter loads and a
     positive and a negative classifier control hold (an unreadable charter makes every path an a_hit).
  3. tools/wd_bridge_mind_shadow.py run() over those files (core, workspace, delta, board text).
  4. The standard path <live-dir>: latest-board.txt, latest.publication.json (read it with the core's
     read_snapshot), latest-receipt.json and latest.json (written last, atomically; names every digest).
At start, <live-dir>/latest.json is read whole once, right after the seed file and before any write or process
start (``read_latest``): a missing one is a first start; one this producer cannot read whole, one of another
schema (another tool's live dir) and one whose last_ok is neither null nor an object naming one cycle dir are
refused with a fixed reason and exit 2. Its last_ok names the previous publication for the shadow run (none when
that cycle's run/reflected.publication.json is gone) and is carried forward as read until a cycle succeeds.
Right after the live dir is made and before any other write or process start, <live-dir>/producer.lock is locked
for the process's life (``lock_live_dir``; the OS releases it when the process ends): a live dir another producer
holds is refused with a fixed reason and exit 2, so one producer at a time writes and prunes there.
Writes only under <live-dir>. Never writes a bridge event, never runs gate code, never echoes event content in
an error. Each cycle's dir under <live-dir>/cycles is named by the UTC second its cycle starts, or by the second
after the newest cycle dir's name when that is not later (``new_cycle_dir``: the clock set back, a restart within
the same second), so a name never repeats and old cycle captures beyond --keep are pruned in the order their dirs
were made (their run outputs and digests stay).
"""

import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# The compact tool refuses a --tail outside 0..100000 (tools/bridge_compact_view.py, its argument check; its 0 is
# the bounded full snapshot, not a count of lines), and only after the reader process has started. A seed lists the
# last --window lines and reads each listed row from the last --window + SEED_MARGIN lines, a delta each fresh row
# from the last DETAIL_TAIL: every count this producer passes is 1..TAIL_CAP lines, checked before any start.
TAIL_CAP = 100000
SEED_MARGIN = 500


def count_problem(options):
    """Why the parsed count options are refused at parse time, or None. --window must be 1..TAIL_CAP - SEED_MARGIN:
    0 would list the tool's bounded full snapshot and trim no row, and above the bound each detail read of a seed,
    and above TAIL_CAP its listing too, would be refused (fail closed: refused also when every listed row would be
    reused). --keep must be at least 1: 0 would prune no cycle capture, and a negative -k only the k oldest."""
    if not 1 <= options.window <= TAIL_CAP - SEED_MARGIN:
        return f"--window must be 1..{TAIL_CAP - SEED_MARGIN} lines"
    if options.keep < 1:
        return "--keep must be at least 1"
    return None


parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--src", type=Path, required=True)
parser.add_argument("--live-dir", type=Path, required=True)
parser.add_argument("--bin", type=Path, required=True)
parser.add_argument("--cycles", type=int, default=36)
parser.add_argument("--interval", type=int, default=300)
parser.add_argument("--window", type=int, default=3000)
parser.add_argument("--workers", type=int, default=4)
parser.add_argument("--keep", type=int, default=12)
parser.add_argument("--seed-from", type=Path, default=None,
                    help="An earlier capture.json whose rows are reused when their own canonical ref matches.")
parser.add_argument("--repo", default="Ahkeratmehilaiset/waggledance-swarm")
args = parser.parse_args()
problem = count_problem(args)
if problem is not None:
    parser.error(problem)  # exit 2 with the usage, before the code tree's imports and any write or process start
src, live, bin_dir = args.src.resolve(), Path(os.path.abspath(args.live_dir)), args.bin
audit_root = next(parent for parent in live.parents if parent.name == ".codex-audit")
sys.path.insert(0, str(src))
from tools import wd_bridge_mind_shadow as shadow  # noqa: E402
from tools import bridge_mind_workspace as ws  # noqa: E402
from tools.bridge_compact_view import event_id  # noqa: E402
from tools.check_standing_consensus_sign_class import classify_ab  # noqa: E402
from waggledance.core.idle_consensus_charter import DEFAULT_CHARTER_PATH, evaluate_paths, load_charter  # noqa: E402

CHARTER_FILE = DEFAULT_CHARTER_PATH
CLASSIFIER_FILE = src / "tools" / "check_standing_consensus_sign_class.py"
assert CHARTER_FILE.resolve().is_relative_to(src), "charter outside the code tree"
READER = "Read-AgentBridge.ps1 -Compact -EventId"
# Every reader option is joined into a PowerShell command line, so the options must be one of the three calls this
# producer makes, slot by slot (``reader_call``), or no process starts: no other flag (no -Raw), no quote, space or
# other character the command line could parse. A count is plain ASCII decimal without a leading zero, six digits
# at most (TAIL_CAP's width), so int() never reads a long string (it refuses one beyond the configured digit limit,
# 4300 by default); a position cursor is at most 2048 characters in all, the compact tool's own bound.
COUNT = re.compile(r"[1-9][0-9]{0,5}")
EVENT_REF = re.compile(r"[0-9a-f]{64}")
CURSOR = re.compile(r"bc1\.[A-Za-z0-9_-]{1,2044}")
REPORT_TASK = "fable-5/bridge-mind-shadow-live-20261013"
LANE = re.compile(r"[a-z][a-z0-9-]{1,40}")
DETAIL_TAIL = 600
LATEST_SCHEMA = "wd.fable5.mind-shadow-live-latest.v1"
CYCLE_DIR = re.compile(r"[0-9]{8}T[0-9]{6}Z")  # a cycle_name(): ASCII digits only (\d matches other scripts' too)
CYCLE_TIME = "%Y%m%dT%H%M%SZ"  # the UTC second a cycle dir name reads as (cycle_name's form, new_cycle_dir)
# The one byte a Windows live dir lock holds (``lock_live_dir``), past any byte a reader of the empty lock file asks
# for: Windows locks are mandatory and a read covering a locked byte is refused, so a lock of the first byte would
# refuse every read or copy of the lock file while a producer runs.
LOCK_BYTE = 2 ** 30


class CaptureRefused(RuntimeError):
    """Fixed reasons only. Raised by a read of a delta, it reseeds (``advance``)."""


class CycleRefused(CaptureRefused):
    """Fixed reasons only: a reader that times out or raises OSError, or an output this producer cannot read. It
    refuses the cycle in either mode and never reseeds (whether any of these should reseed is the stall-or-reseed
    choice, not made here)."""


class SeedRefused(RuntimeError):
    """Fixed reasons only: the --seed-from file, read whole once before any write or process start (``read_seed``),
    is refused whole, and the producer exits 2 with its usage."""


class LatestRefused(RuntimeError):
    """Fixed reasons only: the live dir's latest.json, read whole once before any write or process start
    (``read_latest``), is refused whole, and the producer exits 2 with its usage."""


class LiveDirRefused(RuntimeError):
    """Fixed reasons only: the live dir's lock, taken right after the live dir is made and before any other write or
    process start (``lock_live_dir``), is refused, and the producer exits 2 with its usage."""


def utc_now(seconds=False):
    now = dt.datetime.now(dt.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ") if seconds else now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def cycle_name():
    """The UTC second a cycle starts, without its separators (CYCLE_DIR, CYCLE_TIME): its new cycle dir's name,
    unless ``new_cycle_dir`` names the dir after the newest one."""
    return utc_now(seconds=True).replace(":", "").replace("-", "")


def new_cycle_dir(cycles_dir):
    """Make this cycle's dir in ``cycles_dir`` and return it. It is named by the UTC second the cycle starts
    (``cycle_name``), unless that name is not after the newest name there that reads as a UTC second (CYCLE_DIR and
    CYCLE_TIME; the clock set back, or a restart within the second of the last cycle): then by the second after
    that newest name. So a name never repeats and the names sort in the order the dirs were made, so that
    ``prune``, which keeps the last --keep names, keeps the captures of the last --keep dirs made. Such a name runs
    ahead of the clock until the clock passes it; the cycle's log record keeps the clock's started_utc. An entry
    whose name does not read as a UTC second is not counted."""
    name = cycle_name()
    for newest in sorted((path.name for path in cycles_dir.iterdir() if CYCLE_DIR.fullmatch(path.name)),
                         reverse=True):
        try:
            after = dt.datetime.strptime(newest, CYCLE_TIME) + dt.timedelta(seconds=1)
        except ValueError:  # not a UTC second (a month 13, for one): not a name this producer makes
            continue
        if name <= newest:
            name = after.strftime(CYCLE_TIME)
        break
    path = cycles_dir / name
    path.mkdir()
    return path


def sha(data):
    return hashlib.sha256(data).hexdigest()


def blob_sha1(path):
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def atomic_write(path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def canonical(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")


def reader_call(options):
    """True only for the three reader calls this producer makes, slot by slot: -AfterEvent and a bc1 position
    cursor, or -ContinuityTail and a count of 1..TAIL_CAP lines, then optionally -EventId and a canonical 64-hex
    event ref. Each option must be a str itself (its type, not isinstance(), which a faked __class__ passes)."""
    if len(options) not in (2, 4) or not all(type(option) is str for option in options):
        return False
    if options[0] == "-AfterEvent":
        return len(options) == 2 and CURSOR.fullmatch(options[1]) is not None
    if options[0] != "-ContinuityTail" or not COUNT.fullmatch(options[1]) or int(options[1]) > TAIL_CAP:
        return False
    return len(options) == 2 or (options[2] == "-EventId" and EVENT_REF.fullmatch(options[3]) is not None)


def reader(*options):
    """One pinned compact reader call; its stdout bytes, or a fixed refusal."""
    if not reader_call(options):
        raise CaptureRefused("reader_option_invalid")
    script = str(bin_dir / "Read-AgentBridge.ps1").replace("'", "''")
    command = "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); & '" + script + "' -Compact " + \
        " ".join(options)
    try:
        done = subprocess.run(["pwsh", "-NoProfile", "-Command", command], capture_output=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise CycleRefused("compact_reader_timed_out") from None
    except OSError:  # its start failing (pwsh not found or not permitted), or rarely one later in the run
        raise CycleRefused("compact_reader_not_started") from None
    if done.returncode != 0:
        raise CaptureRefused("compact_reader_failed")
    return done.stdout


def reader_object(data):
    """The one JSON object a reader call printed, or a fixed refusal of the cycle."""
    try:
        value = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):  # UnicodeDecodeError and JSONDecodeError are ValueErrors
        raise CycleRefused("compact_output_unparseable") from None
    if type(value) is not dict:
        raise CycleRefused("compact_output_unparseable")
    return value


def compact_list(*options):
    view = reader_object(reader(*options))
    if view.get("schema") != "wd.bridge.compact-view.v1" or not isinstance(view.get("cursor"), str):
        raise CaptureRefused("compact_view_shape")
    # Each listed ref must be a scalar: one of another shape than a ref (a number, a boolean, null, another string)
    # is refused by the detail read's option check, which reseeds.
    events = view.get("events")
    if type(events) is not list or not all(
            type(item) is dict and "ref" in item and type(item["ref"]) not in (list, dict) for item in events):
        raise CycleRefused("compact_events_shape")
    return [item["ref"] for item in events], view["cursor"], view.get("stats", {})


def row_digest(event):
    return sha(json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def verified_ref(event):
    """A detail row's canonical ref, or a fixed refusal of the cycle when it cannot be built: the canonical form
    allows no non-finite number and no lone surrogate."""
    try:
        return event_id(event)
    except ValueError:  # allow_nan=False, and UnicodeEncodeError is a ValueError
        raise CycleRefused("detail_ref_unverifiable") from None


def details(refs, tail, seeded=None):
    """Full rows for refs, in order: {ref, row sha256, source, event}; every canonical ref must match.

    ``seeded`` maps refs to rows of an earlier capture whose own canonical ref equals the listed one (the same row,
    so no reader call is needed); every other ref is read with -Compact -EventId."""
    seeded = seeded or {}

    def one(ref):
        if ref in seeded:
            return {"ref": ref, "row_sha256": row_digest(seeded[ref]), "source": "seed", "event": seeded[ref]}
        report = reader_object(reader("-ContinuityTail", str(tail), "-EventId", ref))
        event = report.get("event") if report.get("schema") == "wd.bridge.event-detail.v1" else None
        if not isinstance(event, dict) or report.get("ref") != ref or verified_ref(event) != ref:
            raise CaptureRefused("detail_ref_mismatch")
        return {"ref": ref, "row_sha256": row_digest(event), "source": "detail", "event": event}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        return list(pool.map(one, refs))


def read_seed(path):
    """The rows of an earlier capture (--seed-from), each keyed by its own canonical ref, or a fixed refusal of the
    whole file: one that cannot be read (seed_unreadable); bytes that are not UTF-8 or not JSON, a leading BOM,
    nesting beyond the parser's depth and an int literal beyond the int digit limit included (seed_unparseable);
    JSON that is not an object whose events are a list of objects (seed_shape_unexpected); and a row whose canonical
    ref cannot be built, for a non-finite number or a lone surrogate (seed_ref_unverifiable). Its schema and other
    keys are not read: a row is reused only when its own canonical ref equals a listed ref (``details``)."""
    try:
        data = path.read_bytes()
    except OSError:  # missing, a directory, not permitted
        raise SeedRefused("seed_unreadable") from None
    try:
        capture = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):  # UnicodeDecodeError and JSONDecodeError are ValueErrors
        raise SeedRefused("seed_unparseable") from None
    events = capture.get("events") if type(capture) is dict else None
    if type(events) is not list or not all(type(event) is dict for event in events):
        raise SeedRefused("seed_shape_unexpected")
    try:
        return {event_id(event): event for event in events}
    except (ValueError, RecursionError):  # allow_nan=False, and UnicodeEncodeError is a ValueError
        raise SeedRefused("seed_ref_unverifiable") from None


def read_latest(path):
    """The last_ok of this producer's own latest.json at ``path``: None when there is no such file (a first start)
    or when it names none (the cycles so far failed, or the privacy guard refused the last one). The file is read
    whole once (its bytes, one ASCII decode, json.loads) and refused whole with a fixed reason: latest_unreadable
    for an OSError other than a missing file (a directory, not permitted), latest_unparseable for bytes that are
    not ASCII JSON of the kind this producer writes (a BOM, nesting beyond the parser's depth, an int literal
    beyond the configured int digit limit, a NaN or an infinite number), latest_shape_unexpected for anything but
    an object of this producer's schema (LATEST_SCHEMA: a latest.json of another tool's live dir is refused) whose
    last_ok is null or an object with a cycle_dir that is a cycle dir name (CYCLE_DIR: one directory under cycles,
    never a path). The other last_ok keys are not read: the loop carries them forward as read."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:  # a directory, not permitted
        raise LatestRefused("latest_unreadable") from None
    try:
        latest = json.loads(data.decode("ascii"))  # UnicodeDecodeError and JSONDecodeError are ValueErrors
        json.dumps(latest, allow_nan=False)  # a NaN or an infinite number, which this producer never writes
    except (ValueError, RecursionError):
        raise LatestRefused("latest_unparseable") from None
    if type(latest) is not dict or latest.get("schema") != LATEST_SCHEMA or "last_ok" not in latest:
        raise LatestRefused("latest_shape_unexpected")
    last_ok = latest["last_ok"]
    if last_ok is not None and (type(last_ok) is not dict or type(last_ok.get("cycle_dir")) is not str
                                or not CYCLE_DIR.fullmatch(last_ok["cycle_dir"])):
        raise LatestRefused("latest_shape_unexpected")
    return last_ok


def latest_object(cycle, rec, last_ok):
    """The latest.json object of a finished cycle, ``rec`` its log record; ``rec`` None: the privacy guard refused
    the cycle's outputs, and the object names no last_ok. read_latest reads either back on the next start."""
    if rec is None:
        return {"schema": LATEST_SCHEMA, "authority": "none", "cycle": cycle, "status": "refused",
                "reason": "output_guard", "last_ok": None}
    return {"schema": LATEST_SCHEMA, "authority": "none", "scope": "shadow_observation_not_gate", "cycle": cycle,
            "status": rec["status"], "reason": rec.get("reason"), "updated_utc": rec["finished_utc"],
            "last_ok": last_ok,
            "files": {"board": "latest-board.txt", "publication": "latest.publication.json",
                      "receipt": "latest-receipt.json", "log": "live-log.jsonl"},
            "verify": "read latest.publication.json with tools/wd_bridge_mind.py read_snapshot(path, audit_root="
                      "<this .codex-audit>); its snapshot sha256 must equal last_ok.snapshot_sha256",
            "report_to": {"type": "message", "task_id": REPORT_TASK},
            "advisory": "observe only; before any vote or pass check the canonical tools"}


def lock_live_dir(path):
    """One producer per live dir: ``path`` (<live-dir>/producer.lock) is opened, created when missing and never
    truncated or written, and locked without waiting: on Windows one byte, LOCK_BYTE, past the end of the file, with
    msvcrt (which locks from the descriptor's position), elsewhere the whole file with an exclusive flock. The open
    descriptor is returned for the caller to keep for the process's life; it is not inheritable (Python's default),
    so no process this producer starts holds it, and the OS releases the lock after the process ends, a crash
    included, so no stale lock is left. Refused with a fixed reason: live_dir_unlockable when the file cannot be
    opened (a directory, not permitted); live_dir_in_use on a PermissionError or a BlockingIOError of the seek or
    the lock (msvcrt answers a lock that another process, or another descriptor of this one, holds with EACCES,
    flock with EWOULDBLOCK); live_dir_unlockable on any other OSError there. A refused descriptor is closed."""
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        raise LiveDirRefused("live_dir_unlockable") from None
    try:
        if os.name == "nt":
            import msvcrt  # each platform's own module, imported where it is used
            os.lseek(fd, LOCK_BYTE, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, PermissionError):  # held: EACCES from msvcrt, EWOULDBLOCK from flock
        os.close(fd)
        raise LiveDirRefused("live_dir_in_use") from None
    except OSError:
        os.close(fd)
        raise LiveDirRefused("live_dir_unlockable") from None
    return fd


def persisted_window(window_file, cursor_file):
    """The persisted window rows and cursor, or None when either file is missing or does not decode, or any row does
    not verify: each row must be one this producer wrote ({event, ref, row_sha256, source}) whose ref and sha256 are
    still those of its own event, and no ref may repeat. The order of the rows is not verified. Nothing of an
    unverified state is reused."""
    if not (window_file.exists() and cursor_file.exists()):
        return None
    try:
        rows = [json.loads(line) for line in window_file.read_text(encoding="utf-8").splitlines() if line]
        cursor = cursor_file.read_text(encoding="ascii").strip()
        verified = all(isinstance(row, dict) and set(row) == {"event", "ref", "row_sha256", "source"}
                       and isinstance(row["event"], dict) and row["source"] in ("seed", "detail")
                       and row["ref"] == event_id(row["event"]) and row["row_sha256"] == row_digest(row["event"])
                       for row in rows) and len({row["ref"] for row in rows}) == len(rows)
    except (ValueError, RecursionError):  # undecodable, not JSON or nested too deep, or no canonical ref
        return None
    return (rows, cursor) if verified else None


def advance(state_dir, seeded=None):
    """One fence-free read of the sliding window: (rows, its read record: mode, new_rows, stats, detail_calls,
    reseed_after). ``seeded`` holds the rows of the --seed-from file, read once before the first cycle
    (``read_seed``).

    With a verified persisted window the read continues after its cursor ("delta"): the refs listed after it, then
    each fresh one with -EventId, which reads only the last DETAIL_TAIL lines of the log. With no or an unverified
    state or an empty window the window is listed again from -ContinuityTail ("seed"), and so it is when the reader
    refuses the delta's listing or one of its detail reads (a ref none of whose rows lies in those last lines is
    refused, and the same delta would be refused at every cycle). After a refused delta, reseed_after names the
    refused read and its fixed reason, and the seed reuses each verified persisted row whose ref is listed again, as
    it reuses a ``seeded`` row, and reads only the others with -EventId. reseed_after names no ref and records
    neither a gap nor that the reseed covered the earlier history. The record counts the final mode only: new_rows
    the distinct listed refs not already in the window (after a reseed every distinct listed ref, the reused rows
    included), detail_calls its -EventId reads, stats its listing's; the reads of an abandoned delta are neither
    counted nor reused. A refused seed read, a CycleRefused in either mode (a reader that times out or raises OSError,
    an output this producer cannot read) and any error that is not a reader refusal refuse the cycle; when a read
    raises it, before the window and cursor writes below, those two files are left as they were and the next cycle
    starts from the same state. A failed write of either file is outside this."""
    window_file, cursor_file = state_dir / "window.jsonl", state_dir / "cursor.txt"
    state = persisted_window(window_file, cursor_file)
    rows, mode, kept, reseed_after = [], "seed", {}, None
    if state and state[0]:
        read = "listing"
        try:
            refs, cursor, stats = compact_list("-AfterEvent", state[1])
            known = {row["ref"] for row in state[0]}
            fresh = list(dict.fromkeys(ref for ref in refs if ref not in known))  # a repeated ref is read once
            read = "detail"
            fetched = details(fresh, DETAIL_TAIL)
            rows, mode = state[0], "delta"
        except CycleRefused:  # a reader that times out or raises OSError, or an output it cannot read: no reseed
            raise
        except CaptureRefused as exc:
            kept, reseed_after = {row["ref"]: row["event"] for row in state[0]}, {"read": read, "reason": str(exc)}
    if mode == "seed":
        refs, cursor, stats = compact_list("-ContinuityTail", str(args.window))
        fresh = list(dict.fromkeys(refs))
        fetched = details(fresh, args.window + SEED_MARGIN, {**(seeded or {}), **kept})
    rows = (rows + fetched)[-args.window:]
    atomic_write(window_file, "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                                      for row in rows).encode("utf-8"))
    atomic_write(cursor_file, cursor.encode("ascii"))
    return rows, {"mode": mode, "new_rows": len(fresh), "stats": stats,
                  "detail_calls": sum(1 for row in fetched if row["source"] == "detail"), "reseed_after": reseed_after}


def write_capture(cycle_dir, rows):
    manifest = "\n".join(f"{row['ref']} {row['row_sha256']} {row['source']}" for row in rows).encode("ascii")
    capture = {"schema": "wd.bridge-mind-capture.v1", "reader": READER, "reader_stdout_sha256": sha(manifest),
               "observed_utc": utc_now(seconds=True), "events": [row["event"] for row in rows]}
    path = cycle_dir / "capture.json"
    path.write_text(json.dumps(capture, ensure_ascii=False), encoding="utf-8")
    (cycle_dir / "detail-manifest.txt").write_bytes(manifest)
    return path


def gh(*options):
    done = subprocess.run(["gh", *options], capture_output=True, timeout=120)
    if done.returncode != 0:
        raise CaptureRefused("gh_failed")
    return done.stdout


def charter_state():
    try:
        charter = load_charter(CHARTER_FILE)
    except Exception:
        return None, False
    negative = classify_ab(["tools/wd_pr_missing_slot_board.py"])["a_hits"] == []
    positive = classify_ab(["CLAUDE.md"])["a_hits"] == ["claude.md"]
    return charter, bool(charter.allowlist) and bool(charter.file_denylist) and negative and positive


def path_class(charter, paths):
    if not paths or len(paths) >= 100 or not all(ws.SAFE_PATH.fullmatch(p) for p in paths):
        return None  # empty, possibly truncated at the 100-file page, or outside the safe alphabet: UNKNOWN
    result = classify_ab(paths)
    denied = [p for p in paths if not evaluate_paths(charter, [p]).allowed
              and evaluate_paths(charter, [p]).reason == "denylist hit"]
    row = {"charter_allowed": evaluate_paths(charter, paths).allowed, "denylist_hits": sorted(set(denied)),
           "a_hits": list(result["a_hits"]), "unrecognized": list(result["unrecognized"])}
    if not all(ws.SAFE_PATH.fullmatch(p) for name in ("denylist_hits", "a_hits", "unrecognized") for p in row[name]):
        return None
    return row


def commit_authors(view):
    """Declared git author names of the PR commits (lane-shaped only), or None when the list may be incomplete."""
    commits = view.get("commits")
    if not isinstance(commits, list) or not commits or len(commits) >= 100:
        return None
    names = set()
    for commit in commits:
        for author in commit.get("authors") or []:
            name = author.get("name") if isinstance(author, dict) else None
            if isinstance(name, str) and LANE.fullmatch(name):
                names.add(name)
    return sorted(names)


def pr_context(cycle_dir, cache_dir, charter, loaded):
    started = time.perf_counter()
    data = gh("pr", "list", "--repo", args.repo, "--state", "all", "--limit", "400", "--json",
              "number,state,headRefOid,headRefName")
    observed = utc_now(seconds=True)
    (cycle_dir / "gh-pr-list.json").write_bytes(data)
    rows, skipped, fetched = {}, 0, 0
    for item in json.loads(data):
        number, head, branch, state = item["number"], item["headRefOid"], item["headRefName"], item["state"]
        if state not in ws.PR_STATES or not ws.HEAD.fullmatch(head or "") or not ws.TASK.fullmatch(branch or ""):
            skipped += 1
            continue
        klass, authors = None, None
        if state == "OPEN":
            cache = cache_dir / f"{number}-{head}.v2.json"
            if not cache.exists():
                cache.write_bytes(gh("pr", "view", str(number), "--repo", args.repo, "--json",
                                     "files,headRefOid,commits"))
                fetched += 1
            view = json.loads(cache.read_bytes())
            if view.get("headRefOid") == head:
                if loaded:
                    klass = path_class(charter, [f["path"] for f in view.get("files") or []])
                authors = commit_authors(view)
        rows[str(number)] = {"state": state, "head": head, "head_ref_name": branch, "path_class": klass,
                             "commit_authors": authors}
    context = {"schema": "wd.bridge-mind-pr-context.v2", "authority": "none", "capture_sha256": sha(data),
               "observed_utc": observed,
               "charter": {"loaded": loaded, "charter_blob": blob_sha1(CHARTER_FILE),
                           "classifier_blob": blob_sha1(CLASSIFIER_FILE)},
               "prs": rows}
    path = cycle_dir / "context.json"
    path.write_bytes(canonical(context))
    return path, {"ms": round((time.perf_counter() - started) * 1000), "prs": len(rows), "skipped": skipped,
                  "views_fetched": fetched, "charter_loaded": loaded}


def prune(cycles_dir, keep):
    for old in sorted(path for path in cycles_dir.iterdir() if path.is_dir())[:-keep]:
        for name in ("capture.json", "reader-stdout.bin"):
            target = old / name
            if target.exists():
                target.unlink()


latest_file = live / "latest.json"
try:  # each whole, once, before any write or process start; a seed changed later is not read again
    seeded = {} if args.seed_from is None else read_seed(args.seed_from)
    last_ok = read_latest(latest_file)  # this producer's own
except SeedRefused as exc:
    parser.error(f"--seed-from refused: {exc}")  # exit 2 with the usage
except LatestRefused as exc:
    parser.error(f"latest.json refused: {exc}")  # exit 2 with the usage
live.mkdir(parents=True, exist_ok=True)
try:  # kept for the process's life, right after the live dir and before any other write or process start
    lock = lock_live_dir(live / "producer.lock")
except LiveDirRefused as exc:
    parser.error(f"live dir refused: {exc}")  # exit 2 with the usage
for name in ("cycles", "files-cache", "state"):
    (live / name).mkdir(exist_ok=True)
log = live / "live-log.jsonl"
charter, loaded = charter_state()
previous = None  # the last ok cycle's publication, while it is there
if last_ok is not None and (live / "cycles" / last_ok["cycle_dir"] / "run" / "reflected.publication.json").exists():
    previous = live / "cycles" / last_ok["cycle_dir"] / "run" / "reflected.publication.json"
for cycle in range(1, args.cycles + 1):
    tick = time.perf_counter()
    cycle_dir = new_cycle_dir(live / "cycles")
    rec = {"cycle": cycle, "started_utc": utc_now(), "cycle_dir": cycle_dir.name, "reader": READER}
    try:
        started = time.perf_counter()
        rows, rec["read"] = advance(live / "state", seeded)
        capture_path = write_capture(cycle_dir, rows)
        rec["capture"] = {"ms": round((time.perf_counter() - started) * 1000), "events": len(rows),
                          "capture_bytes": capture_path.stat().st_size}
        context_path, rec["context"] = pr_context(cycle_dir, live / "files-cache", charter, loaded)
        started = time.perf_counter()
        receipt = shadow.run(capture_path, audit_root, cycle_dir / "run", context_path=context_path,
                             previous_path=previous)
        rec["run_ms"] = round((time.perf_counter() - started) * 1000)
        run_dir = cycle_dir / "run"
        for name, target in (("board.txt", "latest-board.txt"), ("reflected.publication.json",
                                                                  "latest.publication.json"),
                             ("receipt.json", "latest-receipt.json")):
            atomic_write(live / target, (run_dir / name).read_bytes())
        previous = run_dir / "reflected.publication.json"
        last_ok = {
            "cycle_dir": cycle_dir.name, "observed_utc": receipt["capture"]["observed_utc"],
            "capture_file": f"cycles/{cycle_dir.name}/capture.json",
            "context_file": f"cycles/{cycle_dir.name}/context.json",
            "capture_sha256": receipt["capture"]["sha256"], "context_sha256": receipt["context"]["sha256"],
            "reader": READER, "snapshot_sha256": receipt["publication"]["snapshot_sha256"],
            "board_text_sha256": receipt["board_text_sha256"], "counts": receipt["counts"],
            "global_question": receipt["global_question"],
            "measures": {"capture_ms": rec["capture"]["ms"], "context_ms": rec["context"]["ms"],
                         "run_ms": rec["run_ms"], "events": len(rows), "new_rows": rec["read"]["new_rows"],
                         "capture_bytes": rec["capture"]["capture_bytes"],
                         "board_bytes": (run_dir / "board.txt").stat().st_size,
                         "publication_bytes": (run_dir / "reflected.publication.json").stat().st_size},
        }
        rec["status"], rec["ok"] = "ok", last_ok
    except shadow.ShadowRefused as exc:
        rec["status"], rec["reason"] = "refused", str(exc)
    except CaptureRefused as exc:
        rec["status"], rec["reason"] = "refused", str(exc)
    except Exception as exc:  # never echo content: the type only
        rec["status"], rec["reason"] = "refused", type(exc).__name__
    prune(live / "cycles", args.keep)
    rec["cycle_ms"] = round((time.perf_counter() - tick) * 1000)
    rec["finished_utc"] = utc_now()
    latest = latest_object(cycle, rec, last_ok)
    try:  # the privacy guard on every output this producer writes itself
        ws.guard_object(latest)
        ws.guard_object(rec)
    except ws.WorkspaceError:
        rec = {"cycle": cycle, "status": "refused", "reason": "output_guard", "cycle_dir": cycle_dir.name}
        latest = latest_object(cycle, None, None)
    with open(log, "a", encoding="ascii") as handle:
        handle.write(json.dumps(rec, sort_keys=True, ensure_ascii=True) + "\n")
    atomic_write(latest_file, canonical(latest))
    print(json.dumps({k: rec.get(k) for k in ("cycle", "status", "reason", "cycle_ms")}), flush=True)
    if cycle < args.cycles:
        time.sleep(max(5, args.interval - (time.perf_counter() - tick)))
