"""Bridge Mind capture producer: the shadow loop that reads the bridge and GitHub metadata and runs the shadow.

usage: python -B sandbox/mind/mind_capture_producer.py --src <code tree> --live-dir <dir inside a .codex-audit root>
           --bin <pinned .agent-bridge/bin> [--cycles N] [--interval S] [--window W] [--workers K] [--keep K]
           [--seed-from <an earlier capture.json>] [--repo <owner/name>]

Every cycle builds one immutable candidate dir under <live-dir>/cycles and then commits it with ONE pointer:
  1. FENCE-FREE BRIDGE CAPTURE through the inherited pinned reader, compact path only (a non-Compact read, -Raw
     included, drains the accepted queue under the global publication fence; the compact path is a pure read):
     ``Read-AgentBridge.ps1 -Compact -AfterEvent <position cursor>`` lists the new event refs, and
     ``Read-AgentBridge.ps1 -Compact -ContinuityTail <lines> -EventId <ref>`` returns each full row, whose canonical
     ref must equal the listed one. The reader options must be one of the three calls this producer makes, slot by
     slot, or no process starts (``reader_call``; no -Raw or other flag can get through); --window is held to
     1..TAIL_CAP - SEED_MARGIN lines and --keep to at least 1 at parse time, before the code tree's imports and any
     write or process start (``count_problem``). The window continues from the COMMITTED state: the state.json that
     latest.json names, whose bytes must hash to the sha256 named there and whose every row must still verify against
     its own canonical ref (``read_state``; anything else refuses the cycle, committed_state_unverified). A delta reads
     each fresh row from the last DETAIL_TAIL lines plus the rows the listing read after the cursor, so every listed
     row lies within the read (``advance``; a delta needing more than TAIL_CAP lines refuses). Only a fresh start (no
     latest.json) seeds, from ``-Compact -ContinuityTail <window>``; with --seed-from, a row of an earlier capture
     whose own canonical ref equals a listed ref is reused instead of read again (the file is read whole once, after
     the code tree's imports and before any write or process start, and one it cannot read whole is refused with a
     fixed reason and exit 2; ``read_seed``). A reader refusal, a cursor whose bridge log identity differs from the
     committed one, or any read this producer cannot verify refuses the cycle with a fixed reason: there is no
     reseed and no skip, the next cycle starts from the same committed state, and a stall stays visible in the log
     (recovering from one is an explicit operator step, not code here).
  2. PR CONTEXT (observational, authority none; the only network touch, bounded read-only GitHub metadata): one
     ``gh pr list`` (state, head, branch) and, for OPEN PRs, ``gh pr view --json headRefOid,commits``, kept in
     <live-dir>/files-cache per PR and head as typed metadata only (the head and the lane-shaped commit author names;
     no message, body or path). No classifier, charter or gate code is imported or run: every path_class is null and
     the charter is not loaded, so a computed class stays UNKNOWN. A list of LIST_LIMIT rows or more may leave out
     a PR the snapshot names (gh stops there): each such PR, by the core's own snapshot of the capture, is read with
     one gh pr view of the list's four fields, and one still missing leaves the context UNKNOWN. gh failing, timing
     out or not starting, an output of another shape, or a view past the cycle's VIEW_CAP calls or GH_BUDGET
     seconds, leaves the context, or that PR's authors, UNKNOWN; it never refuses the cycle. An UNKNOWN context
     suppresses nothing: the shadow then runs the bridge-only interface, which can still suggest a vote for a PR
     that merged outside the bridge view. A cached view that does not validate leaves the whole context
     UNKNOWN (no partial reuse); the context is written only when the workspace's own read_context accepts it.
  3. tools/wd_bridge_mind_shadow.py run() over those files, with the previous publication only when its snapshot,
     verified by the core's read_snapshot, hashes to last_ok.snapshot_sha256 (else none, recorded
     previous_identity_mismatch).
  4. COMMIT: every payload (capture, detail manifest, context, state) is guarded and read back the way its consumer
     reads it before the first write (``prewrite``), and created exclusively in the candidate dir; the captures and
     states of old cycle dirs beyond --keep are pruned, never those of last_ok's cycle or of the candidate
     (``prune``); the cycle's record, status prepared (no success claim), is appended to live-log.jsonl; then
     <live-dir>/latest.json is replaced atomically, LAST, as the single commit pointer (``atomic_write``: a unique
     temporary file in the same dir and the core's sharing retry). It embeds last_ok: the cycle's files and their
     sha256, the state's sha256 and its generation. latest.json changes only on a commit, so a refused or
     interrupted cycle never advances the consumed state or loses a row; a log record never commits a cycle.
A refusal is a fixed reason, recorded in live-log.jsonl (flushed and synced); when the log cannot be written, in
<live-dir>/last-refusal.json (that one record, replaced atomically); when neither can be written, the producer writes
one fixed STOP line to stderr and exits STOP_EXIT, committing nothing further.
At start, <live-dir>/latest.json is read whole once, right after the seed file and before any write or process start
(``read_latest``): a missing one is a fresh start; one this producer cannot read whole, or one of another schema (the
latest.json of another tool's live dir, or of the earlier window/cursor producer) is refused with a fixed reason and
exit 2, and so is a live dir that holds the earlier producer's state dir (``refuse_legacy``): fresh production state
only, nothing migrated. Right after the live dir is made and before any other write or process start,
<live-dir>/producer.lock is locked for the process's life (``lock_live_dir``; the OS releases it when the process
ends): a live dir another producer holds is refused with a fixed reason and exit 2, so one producer at a time writes
and prunes there.
Writes only under <live-dir>. Never writes a bridge event, never runs gate, classifier or charter code, never echoes
event content in an error. Each cycle's dir under <live-dir>/cycles is named by the UTC second its cycle starts, or
by the second after the newest cycle dir's name when that is not later (``new_cycle_dir``: the clock set back, a
restart within the same second), so a name never repeats; a dir that cannot be made refuses the cycle.
The program runs only as a script (``__main__``): importing the module (the package's import smoke) defines its
constants, classes and functions and runs nothing, imports nothing from the code tree, writes nothing and starts no
process.
"""

import argparse
import base64
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# The compact tool refuses a --tail outside 0..100000 (tools/bridge_compact_view.py, its argument check; its 0 is
# the bounded full snapshot, not a count of lines), and only after the reader process has started. A seed lists the
# last --window lines and reads each listed row from the last --window + SEED_MARGIN lines, a delta each fresh row
# from the last DETAIL_TAIL lines plus the rows listed after its cursor: every count this producer passes is
# 1..TAIL_CAP lines, checked before any start.
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
LATEST_SCHEMA = "wd.bridge-mind-shadow-latest.v2"
STATE_SCHEMA = "wd.bridge-mind-producer-state.v1"
STATE_KEYS = frozenset({"schema", "generation", "cursor", "source", "window_sha256", "window"})
ROW_KEYS = frozenset({"event", "ref", "row_sha256", "source"})
VIEW_SCHEMA = "wd.bridge-mind-pr-view.v1"
VIEW_KEYS = frozenset({"schema", "number", "head", "commit_authors"})
LIST_FIELDS = frozenset({"number", "state", "headRefOid", "headRefName"})
REFUSAL_SCHEMA = "wd.bridge-mind-shadow-refusal.v1"
VIEW_CAP = 40  # gh pr view calls in one cycle at most; a cached view needs none
GH_TIMEOUT = 60  # seconds for one gh call
GH_BUDGET = 150  # seconds for all gh calls of one cycle
LIST_LIMIT = 400  # gh pr list rows at most: a list that reaches it may leave a PR out (context UNKNOWN)
PRUNED = ("capture.json", "state.json", "detail-manifest.txt")  # what retention removes from an old cycle dir
STOP_EXIT = 3
CYCLE_DIR = re.compile(r"[0-9]{8}T[0-9]{6}Z")  # a cycle_name(): ASCII digits only (\d matches other scripts' too)
CYCLE_TIME = "%Y%m%dT%H%M%SZ"  # the UTC second a cycle dir name reads as (cycle_name's form, new_cycle_dir)
# The one byte a Windows live dir lock holds (``lock_live_dir``), past any byte a reader of the empty lock file asks
# for: Windows locks are mandatory and a read covering a locked byte is refused, so a lock of the first byte would
# refuse every read or copy of the lock file while a producer runs.
LOCK_BYTE = 2 ** 30


class CaptureRefused(RuntimeError):
    """Fixed reasons only: a bridge read this producer cannot use. It refuses the cycle; nothing is reseeded."""


class CycleRefused(CaptureRefused):
    """Fixed reasons only: a reader that times out or raises OSError, an output this producer cannot read, a state
    or candidate it cannot verify, a dir it cannot make, a prune that fails. It refuses the cycle."""


class SeedRefused(RuntimeError):
    """Fixed reasons only: the --seed-from file, read whole once before any write or process start (``read_seed``),
    is refused whole, and the producer exits 2 with its usage."""


class LatestRefused(RuntimeError):
    """Fixed reasons only: the live dir's latest.json, read whole once before any write or process start
    (``read_latest``), is refused whole, and the producer exits 2 with its usage."""


class LiveDirRefused(RuntimeError):
    """Fixed reasons only: a live dir that is not fresh production state (``refuse_legacy``), or its lock, taken
    right after the live dir is made and before any other write or process start (``lock_live_dir``), is refused,
    and the producer exits 2 with its usage."""


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
    whose name does not read as a UTC second is not counted. Its errors (a listing or mkdir OSError, the year after
    9999) reach the caller, which refuses the cycle."""
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


def canonical(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")


def compact(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("ascii")


def atomic_write(path, data):
    """Replace ``path`` with ``data``: a unique temporary file in the same dir (mkstemp), flushed and synced, then
    os.replace through the core's _sharing_retry (which retries only the Windows sharing window, a PermissionError,
    with a bounded backoff). The own temporary file is removed when the replace did not happen. No OSError is
    suppressed: each reaches the caller, which refuses the cycle with its fixed reason."""
    temporary = None
    try:
        fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        core._sharing_retry(lambda: os.replace(temporary, path))
        temporary = None
    finally:
        if temporary is not None:
            os.unlink(temporary)


def write_new(path, data):
    """Create ``path`` exclusively (a cycle file is written once, never replaced), flushed and synced; its sha256."""
    with open(path, "xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    return sha(data)


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
    # is refused by the detail read's option check, which refuses the cycle.
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


def committed_last_ok(last_ok):
    """True for a last_ok of the kind this producer commits: an object naming one cycle dir (CYCLE_DIR), the
    generation of its state (an int of at least 1), the cycle's capture, state, publication, board and receipt files
    under their own names in that dir, and the capture, state and snapshot sha256 (64 lowercase hex)."""
    if type(last_ok) is not dict or type(last_ok.get("cycle_dir")) is not str \
            or not CYCLE_DIR.fullmatch(last_ok["cycle_dir"]):
        return False
    base = f"cycles/{last_ok['cycle_dir']}/"
    named = {"capture_file": base + "capture.json", "state_file": base + "state.json",
             "publication_file": base + "run/reflected.publication.json", "board_file": base + "run/board.txt",
             "receipt_file": base + "run/receipt.json"}
    return (all(last_ok.get(key) == value for key, value in named.items())
            and type(last_ok.get("generation")) is int and last_ok["generation"] >= 1
            and all(type(last_ok.get(key)) is str and EVENT_REF.fullmatch(last_ok[key]) is not None
                    for key in ("capture_sha256", "state_sha256", "snapshot_sha256")))


def read_latest(path):
    """The last_ok of this producer's own latest.json at ``path``, or None when there is no such file (a fresh
    start). The file is read whole once (its bytes, one ASCII decode, json.loads) and refused whole with a fixed
    reason: latest_unreadable for an OSError other than a missing file (a directory, not permitted),
    latest_unparseable for bytes that are not ASCII JSON of the kind this producer writes (a BOM, nesting beyond the
    parser's depth, an int literal beyond the configured int digit limit, a NaN or an infinite number),
    latest_shape_unexpected for anything but an object of this producer's schema (LATEST_SCHEMA: the latest.json of
    another tool's live dir, or of the earlier window/cursor producer, is refused) whose last_ok is one this producer
    commits (``committed_last_ok``). The other last_ok keys are carried as read."""
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
    if type(latest) is not dict or latest.get("schema") != LATEST_SCHEMA \
            or not committed_last_ok(latest.get("last_ok")):
        raise LatestRefused("latest_shape_unexpected")
    return latest["last_ok"]


def latest_object(last_ok):
    """The latest.json object that commits a cycle: its last_ok embedded (one success epoch, no second pointer).
    read_latest reads it back on the next start."""
    return {"schema": LATEST_SCHEMA, "authority": "none", "scope": "shadow_observation_not_gate",
            "committed_utc": utc_now(), "last_ok": last_ok,
            "verify": "read last_ok.publication_file with tools/wd_bridge_mind.py read_snapshot(path, audit_root="
                      "<this .codex-audit>); its snapshot sha256 must equal last_ok.snapshot_sha256, and "
                      "last_ok.state_file must hash to last_ok.state_sha256",
            "report_to": {"type": "message", "task_id": REPORT_TASK},
            "advisory": "observe only; before any vote or pass check the canonical tools"}


def refuse_legacy(path):
    """Fresh production state only: a live dir at ``path`` holding the earlier producer's state dir (its window and
    cursor pair) is refused with live_dir_legacy_state; that state is never read or migrated."""
    if os.path.lexists(path / "state"):
        raise LiveDirRefused("live_dir_legacy_state")


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


def cursor_source(cursor):
    """The bridge log identity a compact position cursor names, {file_identity, log_generation}, or None when the
    cursor is not a bc1 position holding exactly the compact tool's three keys (offset, file_identity,
    generation)."""
    if type(cursor) is not str or not CURSOR.fullmatch(cursor):
        return None
    encoded = cursor[len("bc1."):]
    try:
        position = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    except (ValueError, RecursionError):  # binascii.Error and UnicodeDecodeError are ValueErrors
        return None
    if type(position) is not dict or set(position) != {"offset", "file_identity", "generation"} \
            or type(position["offset"]) is not int or type(position["file_identity"]) is not str \
            or not (position["generation"] is None or type(position["generation"]) is str):
        return None
    return {"file_identity": position["file_identity"], "log_generation": position["generation"]}


def window_bytes(rows):
    """The window rows as the state binds them: one sorted-key JSON line each, UTF-8."""
    return "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows).encode("utf-8")


def state_envelope(generation, cursor, source, rows):
    """The one versioned state a cycle commits: the window rows and their bytes' sha256, the cursor, the bridge log
    identity it names and the generation (1 after a fresh start, then one more per commit)."""
    return {"schema": STATE_SCHEMA, "generation": generation, "cursor": cursor, "source": source,
            "window_sha256": sha(window_bytes(rows)), "window": rows}


def verify_state(state, generation):
    """True for a state envelope of STATE_SCHEMA with exactly STATE_KEYS whose generation is ``generation``, whose
    cursor names the source it holds, whose window rows are each one this producer wrote ({event, ref, row_sha256,
    source}) and still those of its own event, with no ref repeated, and whose window_sha256 is that of those rows'
    window bytes. The order of the rows is not verified."""
    try:
        if type(state) is not dict or set(state) != STATE_KEYS or state["schema"] != STATE_SCHEMA \
                or type(state["generation"]) is not int or state["generation"] != generation \
                or type(state["window"]) is not list:
            return False
        source = cursor_source(state["cursor"])
        rows = state["window"]
        return (source is not None and source == state["source"]
                and all(type(row) is dict and set(row) == ROW_KEYS and type(row["event"]) is dict
                        and row["source"] in ("seed", "detail") and row["ref"] == event_id(row["event"])
                        and row["row_sha256"] == row_digest(row["event"]) for row in rows)
                and len({row["ref"] for row in rows}) == len(rows)
                and state["window_sha256"] == sha(window_bytes(rows)))
    except (ValueError, RecursionError):  # no canonical ref: a non-finite number, a lone surrogate, too deep
        return False


def read_state(path, digest, generation):
    """The committed state at ``path``: its bytes must hash to ``digest`` (the sha256 latest.json names) and parse as
    one strict JSON object (the bridge reader: no duplicate key, no NaN) that ``verify_state`` accepts for
    ``generation``. Read whole once, through the core's sharing retry. Anything else refuses the cycle with the fixed
    committed_state_unverified: nothing of it is reused and nothing is reseeded."""
    try:
        data = core._sharing_retry(path.read_bytes)
        state = parse_bridge_json_object(data.decode("utf-8")) if sha(data) == digest else None
    except (OSError, ValueError, RecursionError):  # missing, unreadable, not UTF-8, not one strict object
        state = None
    if state is None or not verify_state(state, generation):
        raise CycleRefused("committed_state_unverified")
    return state


def advance(state, seeded=None):
    """One fence-free read of the sliding window from the committed ``state``: (rows, cursor, source, read record:
    mode, new_rows, stats, detail_calls). It writes nothing.

    With a committed state the read continues after its cursor ("delta"): the refs listed after it, then each fresh
    one with -EventId over the last DETAIL_TAIL lines plus the rows the listing read after the cursor (its
    stats.snapshot_events, an int), so every listed row lies within the read; a listing without that count refuses
    (compact_view_shape) and a delta needing more than TAIL_CAP lines refuses (delta_beyond_tail_cap). Without one
    (``state`` None: a fresh start) the window is listed from -ContinuityTail ("seed"), reusing each ``seeded`` row
    whose own canonical ref is listed. Any reader refusal, in either mode, refuses the cycle: there is no reseed, and
    the next cycle starts from the same committed state. The new cursor must name a bridge log identity
    (compact_cursor_invalid otherwise), the committed one for a delta (state_source_changed otherwise). new_rows
    counts the distinct listed refs not already in the window, detail_calls the -EventId reads."""
    if state is not None:
        refs, cursor, stats = compact_list("-AfterEvent", state["cursor"])
        listed = stats.get("snapshot_events") if type(stats) is dict else None
        if type(listed) is not int or listed < 0:
            raise CycleRefused("compact_view_shape")
        if DETAIL_TAIL + listed > TAIL_CAP:
            raise CycleRefused("delta_beyond_tail_cap")
        known = {row["ref"] for row in state["window"]}
        fresh = list(dict.fromkeys(ref for ref in refs if ref not in known))  # a repeated ref is read once
        fetched = details(fresh, DETAIL_TAIL + listed)
        rows, mode = state["window"] + fetched, "delta"
    else:
        refs, cursor, stats = compact_list("-ContinuityTail", str(args.window))
        fresh = list(dict.fromkeys(refs))
        fetched = details(fresh, args.window + SEED_MARGIN, seeded)
        rows, mode = fetched, "seed"
    source = cursor_source(cursor)
    if source is None:
        raise CycleRefused("compact_cursor_invalid")
    if state is not None and source != state["source"]:
        raise CycleRefused("state_source_changed")
    return rows[-args.window:], cursor, source, {
        "mode": mode, "new_rows": len(fresh), "stats": stats,
        "detail_calls": sum(1 for row in fetched if row["source"] == "detail")}


def capture_object(rows):
    """The cycle's capture and its detail manifest (ref, canonical row sha256, source per row, in window order),
    whose sha256 is the capture's reader_stdout_sha256."""
    manifest = "\n".join(f"{row['ref']} {row['row_sha256']} {row['source']}" for row in rows).encode("ascii")
    capture = {"schema": "wd.bridge-mind-capture.v1", "reader": READER, "reader_stdout_sha256": sha(manifest),
               "observed_utc": utc_now(seconds=True), "events": [row["event"] for row in rows]}
    return capture, manifest


def gh(timeout, *options):
    """One read-only gh metadata call within ``timeout`` seconds: its stdout bytes, or None when gh exits non-zero,
    times out or cannot start (network, authentication or no gh: UNKNOWN, never a refusal of the cycle)."""
    try:
        done = subprocess.run(["gh", *options], capture_output=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return done.stdout if done.returncode == 0 else None


def pr_rows(data):
    """The rows of one gh pr list output, or None when it is not a JSON list of objects with exactly the four listed
    fields: ({number: (state, head, branch)} for each row whose number is a positive int of at most nine digits
    whose state, head and branch are of the workspace's own shapes, and whose number is not repeated; the count of
    the other rows, skipped)."""
    try:
        items = json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        return None
    if type(items) is not list or not all(type(item) is dict and set(item) == LIST_FIELDS for item in items):
        return None
    rows, skipped = {}, 0
    for item in items:
        number, state, head, branch = item["number"], item["state"], item["headRefOid"], item["headRefName"]
        if type(number) is not int or not 0 < number < 10 ** 9 or str(number) in rows \
                or state not in ws.PR_STATES or type(head) is not str or not ws.HEAD.fullmatch(head) \
                or type(branch) is not str or not ws.TASK.fullmatch(branch):
            skipped += 1
            continue
        rows[str(number)] = (state, head, branch)
    return rows, skipped


def commit_authors(view):
    """Declared git author names of the PR commits (lane-shaped only), or None when the list may be incomplete or is
    not a list of commit objects."""
    commits = view.get("commits")
    if not isinstance(commits, list) or not commits or len(commits) >= 100:
        return None
    names = set()
    for commit in commits:
        if not isinstance(commit, dict):
            return None
        for author in commit.get("authors") or []:
            name = author.get("name") if isinstance(author, dict) else None
            if isinstance(name, str) and LANE.fullmatch(name):
                names.add(name)
    return sorted(names)


def view_object(number, head, authors):
    """The typed metadata kept of one open PR's view: its head and declared commit author names, nothing else."""
    return {"schema": VIEW_SCHEMA, "number": number, "head": head, "commit_authors": authors}


def read_view(path, number, head):
    """A cached view of PR ``number`` at ``head``: the object; None when there is no such file; False when the file
    cannot be read or is not exactly a view of that PR and head whose authors are null or a sorted list of distinct
    lane-shaped names."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        return False
    try:
        view = parse_bridge_json_object(data.decode("ascii"))
    except (ValueError, RecursionError):
        return False
    authors = view.get("commit_authors")
    if set(view) != VIEW_KEYS or type(view["number"]) is not int or view != view_object(number, head, authors):
        return False
    if authors is not None and not (type(authors) is list and all(
            type(name) is str and LANE.fullmatch(name) for name in authors) and authors == sorted(set(authors))):
        return False
    return view


def fetch_view(number, head, timeout):
    """The typed view of one open PR from gh pr view, or None when gh fails or its output is not an object naming
    that head."""
    data = gh(timeout, "pr", "view", str(number), "--repo", args.repo, "--json", "headRefOid,commits")
    try:
        raw = None if data is None else json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError):
        raw = None
    if type(raw) is not dict or raw.get("headRefOid") != head:
        return None
    return view_object(number, head, commit_authors(raw))


def snapshot_prs(capture):
    """The PR numbers the shadow's snapshot of this capture names: the core's full_rebuild and snapshot over its
    events at its observed_utc, made as tools/wd_bridge_mind_shadow.py run() makes them; None when the core
    refuses them (the shadow run then refuses the cycle too)."""
    events = capture["events"]
    source = {"log_identity": "capture-window:" + sha(json.dumps(capture, ensure_ascii=False).encode("utf-8")),
              "generation": None, "cursor_start": 0, "cursor_end": len(events), "event_count": len(events),
              "coverage": "complete"}
    try:
        state = core.full_rebuild([{"ref": event_id(event), "event": event} for event in events], source=source,
                                  as_of_utc=capture["observed_utc"])
        return {row["pr"] for row in core.snapshot(state, as_of_utc=capture["observed_utc"])["prs"].values()}
    except core.ProjectionError:
        return None


def fetch_row(number, timeout):
    """One PR's list row from gh pr view (the list's four fields) and gh's output bytes, or None when gh fails or
    its output is not one object of the list's shapes naming that number."""
    data = gh(timeout, "pr", "view", str(number), "--repo", args.repo, "--json", "number,state,headRefOid,headRefName")
    parsed = None if data is None else pr_rows(b"[" + data + b"]")
    if parsed is None or str(number) not in parsed[0]:
        return None
    return parsed[0][str(number)], data


def pr_context(cache_dir, needed=None):
    """The cycle's PR context and its record; the context is None (UNKNOWN) when gh gives no usable list, when a
    list that reaches LIST_LIMIT leaves out a PR of ``needed`` (the PR numbers of the cycle's snapshot; None: not
    known) that one gh pr view does not add, when a cached view does not validate, or when the workspace's
    read_context refuses it. See the module docstring (2.)."""
    started, deadline = time.perf_counter(), time.monotonic() + GH_BUDGET
    data = gh(GH_TIMEOUT, "pr", "list", "--repo", args.repo, "--state", "all", "--limit", str(LIST_LIMIT),
              "--json", "number,state,headRefOid,headRefName")
    observed = utc_now(seconds=True)
    parsed = None if data is None else pr_rows(data)
    record = {"charter_loaded": False, "observed_utc": observed}
    if parsed is None:
        reason = "gh_list_unavailable" if data is None else "gh_list_unexpected"
        return None, dict(record, status="UNKNOWN", reason=reason, ms=round((time.perf_counter() - started) * 1000))
    rows, skipped = parsed
    calls, added = 0, []
    if len(rows) + skipped >= LIST_LIMIT:  # gh stops at --limit, so a PR the snapshot names may be missing
        for number in [] if needed is None else sorted(set(needed) - {int(key) for key in rows}):
            remaining = deadline - time.monotonic()
            if calls >= VIEW_CAP or remaining <= 0:
                break
            calls += 1
            fetched = fetch_row(number, min(GH_TIMEOUT, remaining))
            if fetched is not None:
                rows[str(number)] = fetched[0]
                added.append(fetched[1])
        if needed is None or set(needed) - {int(key) for key in rows}:
            return None, dict(record, status="UNKNOWN", reason="gh_list_truncated", rows_added=len(added),
                              ms=round((time.perf_counter() - started) * 1000))
    open_prs = [(int(number), head) for number, (state, head, _) in rows.items() if state == "OPEN"]
    views = {}
    for number, head in open_prs:  # reuse: every cached view this cycle needs is validated before any is used
        view = read_view(cache_dir / f"{number}-{head}.view.v1.json", number, head)
        if view is False:
            return None, dict(record, status="UNKNOWN", reason="context_cache_invalid",
                              ms=round((time.perf_counter() - started) * 1000))
        if view is not None:
            views[number] = view
    for number, head in open_prs:
        remaining = deadline - time.monotonic()
        if number in views or calls >= VIEW_CAP or remaining <= 0:
            continue
        calls += 1
        view = fetch_view(number, head, min(GH_TIMEOUT, remaining))
        if view is None:
            continue
        ws.guard_object(view)
        try:
            atomic_write(cache_dir / f"{number}-{head}.view.v1.json", compact(view))
        except OSError:
            raise CycleRefused("view_cache_unwritable") from None
        views[number] = view
    prs = {number: {"state": state, "head": head, "head_ref_name": branch, "path_class": None,
                    "commit_authors": views[int(number)]["commit_authors"] if int(number) in views else None}
           for number, (state, head, branch) in rows.items()}
    # the identity covers the list and every row read on its own, in that order
    context = {"schema": ws.CONTEXT_SCHEMA_V2, "authority": "none", "capture_sha256": sha(b"\n".join([data, *added])),
               "observed_utc": observed, "charter": {"loaded": False, "charter_blob": None, "classifier_blob": None},
               "prs": prs}
    record.update(prs=len(prs), skipped=skipped, views_fetched=calls, rows_added=len(added),
                  views_unknown=sum(1 for number, _ in open_prs if number not in views),
                  ms=round((time.perf_counter() - started) * 1000))
    reason = ws.read_context(context, ws.parse_time(observed))[1]
    if reason is not None:
        return None, dict(record, status="UNKNOWN", reason=reason)
    return context, dict(record, status="observed")


def prewrite(capture, manifest, context, state):
    """B6, file-specific: every payload of the candidate as bytes, each object guarded whole with the workspace's
    guard and read back the way its consumer reads it, before the first write: the capture by the shadow's
    read_capture, the state by ``verify_state``, the context by the workspace's read_context. A guarded word refuses
    the cycle through the guard's own error (output_guard); any other failure refuses it with candidate_invalid.
    Nothing is written here."""
    for value in (capture, state, context):
        ws.guard_object(value)
    payloads = {"capture.json": json.dumps(capture, ensure_ascii=False).encode("utf-8"),
                "detail-manifest.txt": manifest, "state.json": compact(state)}
    if context is not None:
        payloads["context.json"] = canonical(context)
    try:
        shadow.read_capture(parse_bridge_json_object(payloads["capture.json"].decode("utf-8")))
        ok = verify_state(parse_bridge_json_object(payloads["state.json"].decode("ascii")), state["generation"])
        if context is not None:
            ok = ok and ws.read_context(parse_bridge_json_object(payloads["context.json"].decode("ascii")),
                                        ws.parse_time(context["observed_utc"]))[1] is None
    except (shadow.ShadowRefused, ValueError, RecursionError):
        ok = False
    if not ok:
        raise CycleRefused("candidate_invalid")
    return payloads


def previous_publication(last_ok):
    """B4: the last ok cycle's publication for the shadow run and what was found: (path, "verified") only when the
    core's read_snapshot verifies it and its snapshot hashes to last_ok.snapshot_sha256; else (None,
    "previous_identity_mismatch"), never a synthetic delta. A fresh start has none to find: (None, None)."""
    if last_ok is None:
        return None, None
    path = live / last_ok["publication_file"]
    try:
        snapshot = core.read_snapshot(path, audit_root=audit_root)
    except core.ProjectionError:
        return None, "previous_identity_mismatch"
    if sha(core.canonical_bytes(snapshot)) != last_ok["snapshot_sha256"]:
        return None, "previous_identity_mismatch"
    return path, "verified"


def plain(path, directory):
    """True when ``path`` is a real directory (``directory``) or a regular file, and no link or other reparse
    point."""
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return False
    if getattr(status, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        return False
    return stat.S_ISDIR(status.st_mode) if directory else stat.S_ISREG(status.st_mode)


def prune(cycles_dir, keep, protected):
    """B4 retention: remove the PRUNED files (capture, state, detail manifest) of each cycle dir older than the last
    ``keep`` names, never of a ``protected`` one (last_ok's cycle and the active candidate: the prior recoverable
    epoch and the one about to be committed). Only this producer's own paths are touched: a name that reads as a
    cycle dir (CYCLE_DIR) that is a real directory, each file a regular file; a link or other reparse point, at
    either level, is never followed or removed and is counted as skipped. Returns (files removed, entries skipped).
    A listing or a removal that fails raises CycleRefused('prune_failed'): the caller refuses the cycle before its
    commit, so latest.json and last_ok stay."""
    removed = skipped = 0
    try:
        names = sorted(path.name for path in cycles_dir.iterdir() if CYCLE_DIR.fullmatch(path.name))
        for name in names[:-keep]:
            if name in protected:
                continue
            if not plain(cycles_dir / name, True):
                skipped += 1
                continue
            for file_name in PRUNED:
                target = cycles_dir / name / file_name
                if not os.path.lexists(target):
                    continue
                if not plain(target, False):
                    skipped += 1
                    continue
                target.unlink()
                removed += 1
    except OSError:
        raise CycleRefused("prune_failed") from None
    return removed, skipped


def journal(rec):
    """Append one cycle record to <live-dir>/live-log.jsonl, flushed and synced; an OSError reaches the caller."""
    line = (json.dumps(rec, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")
    with open(live / "live-log.jsonl", "ab") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def record_refusal(rec):
    """Make a refused cycle observable: its record appended to the log; when that fails, the bounded fallback, this
    one record's fixed fields in <live-dir>/last-refusal.json (replaced atomically); when that fails too, one fixed
    STOP line on stderr and exit STOP_EXIT: a disk that takes neither cannot honestly hold a logged refusal, and
    nothing further is committed."""
    try:
        journal(rec)
        return
    except OSError:
        pass
    fallback = {"schema": REFUSAL_SCHEMA, "authority": "none", "status": "refused",
                **{key: rec.get(key) for key in ("cycle", "cycle_dir", "reason", "finished_utc")}}
    try:
        atomic_write(live / "last-refusal.json", canonical(fallback))
        return
    except OSError:
        pass
    sys.stderr.write("mind_capture_producer STOP: refusal_unrecordable\n")
    sys.stderr.flush()
    raise SystemExit(STOP_EXIT)


def refused(rec, reason, tick, last_ok):
    """Refuse the cycle of ``rec`` with a fixed ``reason``: its record, without any success field, made observable
    (``record_refusal``), and the last_ok it was given returned unchanged."""
    rec = {key: rec[key] for key in ("cycle", "started_utc", "reader", "cycle_dir", "read", "capture", "context",
                                     "previous", "prune") if key in rec}
    rec.update(status="refused", reason=reason, cycle_ms=round((time.perf_counter() - tick) * 1000),
               finished_utc=utc_now())
    try:  # the privacy guard on every output this producer writes itself
        ws.guard_object(rec)
    except ws.WorkspaceError:
        rec = {key: rec[key] for key in ("cycle", "started_utc", "cycle_dir", "cycle_ms", "finished_utc")
               if key in rec}
        rec.update(status="refused", reason="output_guard")
    record_refusal(rec)
    print(json.dumps({key: rec.get(key) for key in ("cycle", "status", "reason", "cycle_ms")}), flush=True)
    return last_ok


def candidate_cycle(rec, last_ok, seeded):
    """The immutable completed candidate of one cycle under <live-dir>/cycles and the last_ok that commits it. The
    committed state is read first (a bad one refuses before any write), every payload is validated before the first
    write, and the shadow run must have read the very capture and context written. Raises a fixed refusal; the files
    of a refused candidate stay in its dir, which latest.json never names and retention prunes like any old dir."""
    state = None if last_ok is None else read_state(live / last_ok["state_file"], last_ok["state_sha256"],
                                                    last_ok["generation"])
    try:
        cycle_dir = new_cycle_dir(live / "cycles")
    except (OSError, OverflowError, ValueError):  # not permitted, in use, the dir there already, the year 10000
        raise CycleRefused("cycle_dir_unavailable") from None
    rec["cycle_dir"] = cycle_dir.name
    started = time.perf_counter()
    rows, cursor, source, rec["read"] = advance(state, seeded)
    next_state = state_envelope(1 if state is None else state["generation"] + 1, cursor, source, rows)
    capture, manifest = capture_object(rows)
    capture_ms = round((time.perf_counter() - started) * 1000)
    context, rec["context"] = pr_context(live / "files-cache", snapshot_prs(capture))
    payloads = prewrite(capture, manifest, context, next_state)
    try:
        digests = {name: write_new(cycle_dir / name, data) for name, data in payloads.items()}
    except OSError:
        raise CycleRefused("candidate_unwritable") from None
    rec["capture"] = {"ms": capture_ms, "events": len(rows), "capture_bytes": len(payloads["capture.json"])}
    previous, rec["previous"] = previous_publication(last_ok)
    started = time.perf_counter()
    receipt = shadow.run(cycle_dir / "capture.json", audit_root, cycle_dir / "run",
                         context_path=None if context is None else cycle_dir / "context.json", previous_path=previous)
    run_ms = round((time.perf_counter() - started) * 1000)
    if receipt["capture"]["sha256"] != digests["capture.json"] or (receipt["context"] or {}).get("sha256") != \
            digests.get("context.json"):
        raise CycleRefused("candidate_changed")
    run_dir, name = cycle_dir / "run", cycle_dir.name
    return {
        "cycle_dir": name, "generation": next_state["generation"], "observed_utc": receipt["capture"]["observed_utc"],
        "capture_file": f"cycles/{name}/capture.json", "capture_sha256": digests["capture.json"],
        "context_file": None if context is None else f"cycles/{name}/context.json",
        "context_sha256": digests.get("context.json"),
        "state_file": f"cycles/{name}/state.json", "state_sha256": digests["state.json"],
        "publication_file": f"cycles/{name}/run/reflected.publication.json",
        "board_file": f"cycles/{name}/run/board.txt", "receipt_file": f"cycles/{name}/run/receipt.json",
        "reader": READER, "snapshot_sha256": receipt["publication"]["snapshot_sha256"],
        "board_text_sha256": receipt["board_text_sha256"], "counts": receipt["counts"],
        "global_question": receipt["global_question"],
        "measures": {"capture_ms": capture_ms, "context_ms": rec["context"]["ms"], "run_ms": run_ms,
                     "events": len(rows), "new_rows": rec["read"]["new_rows"],
                     "capture_bytes": rec["capture"]["capture_bytes"],
                     "board_bytes": (run_dir / "board.txt").stat().st_size,
                     "publication_bytes": (run_dir / "reflected.publication.json").stat().st_size},
    }


def one_cycle(cycle, last_ok, seeded):
    """One cycle: the candidate (``candidate_cycle``), retention (``prune``), the cycle's record in the log, then
    latest.json replaced LAST as the single commit pointer. Returns the last_ok in force after it: the new one when
    the commit happened, else the one it was given (a refused cycle, a log or pointer write that fails included).
    The log record written before the pointer has status prepared and claims no success: only latest.json commits.
    A log or pointer write that fails appends a refused record after it (``refused``); a process ended between the
    two leaves the prepared record as the cycle's last, with latest.json still naming the earlier epoch."""
    tick = time.perf_counter()
    rec = {"cycle": cycle, "started_utc": utc_now(), "reader": READER}
    try:
        committed = candidate_cycle(rec, last_ok, seeded)
        protected = {committed["cycle_dir"]} | (set() if last_ok is None else {last_ok["cycle_dir"]})
        rec["prune"] = dict(zip(("removed", "skipped"), prune(live / "cycles", args.keep, protected)))
        latest = latest_object(committed)
        rec.update(status="prepared", candidate=committed, cycle_ms=round((time.perf_counter() - tick) * 1000),
                   finished_utc=utc_now())
        ws.guard_object(latest)  # the privacy guard on every output this producer writes itself
        ws.guard_object(rec)
    except shadow.ShadowRefused as exc:
        return refused(rec, str(exc), tick, last_ok)
    except CaptureRefused as exc:
        return refused(rec, str(exc), tick, last_ok)
    except ws.WorkspaceError:
        return refused(rec, "output_guard", tick, last_ok)
    except Exception as exc:  # never echo content: the type only
        return refused(rec, type(exc).__name__, tick, last_ok)
    try:
        journal(rec)
    except OSError:
        return refused(rec, "log_unwritable", tick, last_ok)
    try:
        atomic_write(live / "latest.json", canonical(latest))
    except OSError:
        return refused(rec, "latest_unwritable", tick, last_ok)
    print(json.dumps({"cycle": cycle, "status": "committed", "cycle_ms": rec["cycle_ms"]}), flush=True)
    return committed


# The program runs only as a script: importing this module (the package's import smoke) defines the
# functions and constants above and runs nothing, imports nothing from the code tree, writes nothing and
# starts no process.
if __name__ == "__main__":
    args = parser.parse_args()
    problem = count_problem(args)
    if problem is not None:
        parser.error(problem)  # exit 2 with the usage, before the code tree's imports and any write or process start
    src, live, bin_dir = args.src.resolve(), Path(os.path.abspath(args.live_dir)), args.bin
    audit_root = next(parent for parent in live.parents if parent.name == ".codex-audit")
    sys.path.insert(0, str(src))
    from tools import wd_bridge_mind as core  # noqa: E402
    from tools import wd_bridge_mind_shadow as shadow  # noqa: E402
    from tools import bridge_mind_workspace as ws  # noqa: E402
    from tools.bridge_compact_view import event_id  # noqa: E402
    from waggledance.core.bridge_log_reader import parse_bridge_json_object  # noqa: E402

    latest_file = live / "latest.json"
    try:  # each whole, once, before any write or process start; a seed changed later is not read again
        seeded = {} if args.seed_from is None else read_seed(args.seed_from)
        last_ok = read_latest(latest_file)  # this producer's own
        refuse_legacy(live)
    except SeedRefused as exc:
        parser.error(f"--seed-from refused: {exc}")  # exit 2 with the usage
    except LatestRefused as exc:
        parser.error(f"latest.json refused: {exc}")  # exit 2 with the usage
    except LiveDirRefused as exc:
        parser.error(f"live dir refused: {exc}")  # exit 2 with the usage
    live.mkdir(parents=True, exist_ok=True)
    try:  # kept for the process's life, right after the live dir and before any other write or process start
        lock = lock_live_dir(live / "producer.lock")
    except LiveDirRefused as exc:
        parser.error(f"live dir refused: {exc}")  # exit 2 with the usage
    try:
        for name in ("cycles", "files-cache"):
            (live / name).mkdir(exist_ok=True)
    except OSError:
        parser.error("live dir refused: live_dir_unwritable")  # exit 2 with the usage
    for cycle in range(1, args.cycles + 1):
        tick = time.perf_counter()
        last_ok = one_cycle(cycle, last_ok, seeded)
        if cycle < args.cycles:
            time.sleep(max(5, args.interval - (time.perf_counter() - tick)))
