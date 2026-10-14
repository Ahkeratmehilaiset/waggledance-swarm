"""Bridge Mind sandbox capture producer: the one sandbox component with input I/O; everything else is pure.

usage: python -B sandbox/mind/mind_capture_producer.py --src <code tree> --live-dir <dir inside a .codex-audit root>
           --bin <pinned .agent-bridge/bin> [--cycles N] [--interval S] [--window W] [--workers K] [--keep K]

Every cycle:
  1. FENCE-FREE BRIDGE CAPTURE through the inherited pinned reader, compact path only (a non-Compact read, -Raw
     included, drains the accepted queue under the global publication fence; the compact path is a pure read):
     ``Read-AgentBridge.ps1 -Compact -AfterEvent <position cursor>`` lists the new event refs, and
     ``Read-AgentBridge.ps1 -Compact -EventId <ref>`` returns each full row, whose canonical ref must equal the
     listed one. The first cycle seeds the window from ``-Compact -ContinuityTail <window>``; with --seed-from, a
     row of an earlier capture whose own canonical ref equals a listed ref is reused instead of read again. The
     window slides; it is persisted under <live-dir>/state so a restart continues from the cursor. A cursor the
     reader refuses reseeds. The capture's reader_stdout_sha256 is the sha256 of the manifest (ref, canonical row
     sha256, source) in window order, kept next to the capture.
     Each cycle's capture-provenance.json binds that capture and manifest by sha256 and retains the exact prior
     and end reader cursors, reader scope/stats and bounded-window loss. It is evidence for later coverage
     analysis, not full history or a full-log hash; historical captures cannot acquire missing provenance.
  2. PR CONTEXT (observational, authority none; the only network touch, read-only GitHub metadata): one
     ``gh pr list`` (state, head, branch); for OPEN PRs ``gh pr view --json files,headRefOid,commits`` cached per
     PR and head: the changed paths classified with the code tree's own charter (evaluate_paths) and classifier
     (classify_ab), and the declared commit author names. charter.loaded is true only when the charter loads and a
     positive and a negative classifier control hold (an unreadable charter makes every path an a_hit).
  3. tools/wd_bridge_mind_shadow.py run() over those files (core, workspace, delta, board text).
  4. The standard path <live-dir>: latest-board.txt, latest.publication.json (read it with the core's
     read_snapshot), latest-receipt.json and latest.json (written last, atomically; names every digest).
Writes only under <live-dir>. Never writes a bridge event, never runs gate code, never echoes event content in
an error. Old cycle captures beyond --keep are pruned (their run outputs and digests stay).
"""

import argparse
import base64
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
REPORT_TASK = "fable-5/bridge-mind-shadow-live-20261013"
LANE = re.compile(r"[a-z][a-z0-9-]{1,40}")
DETAIL_TAIL = 600


class CaptureRefused(RuntimeError):
    """Fixed reasons only."""


def utc_now(seconds=False):
    now = dt.datetime.now(dt.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ") if seconds else now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


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


def reader(*options):
    """One pinned compact reader call; its stdout bytes, or a fixed refusal."""
    script = str(bin_dir / "Read-AgentBridge.ps1").replace("'", "''")
    command = "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); & '" + script + "' -Compact " + \
        " ".join(options)
    done = subprocess.run(["pwsh", "-NoProfile", "-Command", command], capture_output=True, timeout=300)
    if done.returncode != 0:
        raise CaptureRefused("compact_reader_failed")
    return done.stdout


def compact_list(*options):
    # Match the compact reader's input alphabet before interpolating a persisted token into its shell call.
    if options and options[0] == "-AfterEvent" and (len(options[1]) > 2048
                                                  or not re.fullmatch(r"bc1\.[A-Za-z0-9_-]+", options[1])):
        raise CaptureRefused("compact_cursor_invalid")
    data = reader(*options)
    view = json.loads(data.decode("utf-8"))
    if view.get("schema") != "wd.bridge.compact-view.v1" or not isinstance(view.get("cursor"), str):
        raise CaptureRefused("compact_view_shape")
    return ([item["ref"] for item in view["events"]], view["cursor"], view.get("stats", {}),
            {"scope": view.get("scope"), "reader": view.get("reader")})


def cursor_position(token):
    """Extract only the current compact reader's canonical bc1 cursor; otherwise UNKNOWN (None).

    tools/bridge_compact_view.py::_read_view_rows encodes sorted compact UTF-8 JSON of BridgeCursor, unpadded
    base64url. bridge_log_reader._file_identity uses an open-handle Windows volume/file-index or POSIX dev/inode,
    never a pathname, row hash or log hash. Generation is an optional external sidecar token: null stays unknown.
    Extraction never replaces the opaque token we retain and send back to the reader.
    """
    if not isinstance(token, str) or len(token) > 2048 or not re.fullmatch(r"bc1\.[A-Za-z0-9_-]+", token):
        return None
    encoded = token[4:]
    try:
        value = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)).decode("utf-8"))
        if not isinstance(value, dict) or set(value) != {"offset", "file_identity", "generation"}:
            return None
        identity, generation, offset = value["file_identity"], value["generation"], value["offset"]
        if (type(offset) is not int or offset < 0 or not isinstance(identity, str)
                or not re.fullmatch(r"windows-v1:[0-9a-f]{8}:[0-9a-f]{16}|posix-v1:[0-9a-f]+:[0-9a-f]+", identity)
                or (generation is not None and (not isinstance(generation, str)
                                               or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", generation)))):
            return None
        body = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        canonical_token = "bc1." + base64.urlsafe_b64encode(body.encode("utf-8")).decode("ascii").rstrip("=")
        return value if token == canonical_token else None
    except (ValueError, RecursionError):
        return None


def row_digest(event):
    return sha(json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def details(refs, tail, seeded=None):
    """Full rows for refs, in order: {ref, row sha256, source, event}; every canonical ref must match.

    ``seeded`` maps refs to rows of an earlier capture whose own canonical ref equals the listed one (the same row,
    so no reader call is needed); every other ref is read with -Compact -EventId."""
    seeded = seeded or {}

    def one(ref):
        if ref in seeded:
            return {"ref": ref, "row_sha256": row_digest(seeded[ref]), "source": "seed", "event": seeded[ref]}
        data = reader("-ContinuityTail", str(tail), "-EventId", ref)
        report = json.loads(data.decode("utf-8"))
        event = report.get("event") if report.get("schema") == "wd.bridge.event-detail.v1" else None
        if not isinstance(event, dict) or report.get("ref") != ref or event_id(event) != ref:
            raise CaptureRefused("detail_ref_mismatch")
        return {"ref": ref, "row_sha256": row_digest(event), "source": "detail", "event": event}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        return list(pool.map(one, refs))


def seed_rows():
    """Rows of an earlier capture (--seed-from) keyed by their own canonical ref."""
    if args.seed_from is None:
        return {}
    capture = json.loads(args.seed_from.read_text(encoding="utf-8"))
    return {event_id(event): event for event in capture.get("events") or [] if isinstance(event, dict)}


def advance(state_dir):
    """The sliding window and retained read metadata after one fence-free read."""
    window_file, cursor_file = state_dir / "window.jsonl", state_dir / "cursor.txt"
    rows = []
    prior = cursor_file.read_bytes().decode("ascii") if cursor_file.exists() else None
    mode = "reseed" if window_file.exists() or cursor_file.exists() else "seed"
    reseed_reason = "state_incomplete" if mode == "reseed" else None
    if window_file.exists() and cursor_file.exists():
        rows = [json.loads(line) for line in window_file.read_text(encoding="utf-8").splitlines() if line]
        try:
            refs, cursor, stats, context = compact_list("-AfterEvent", prior)
            mode = "delta"
            reseed_reason = None
        except CaptureRefused as exc:
            rows = []
            reseed_reason = "cursor_invalid" if str(exc) == "compact_cursor_invalid" else "compact_reader_refused"
    if mode != "delta":
        refs, cursor, stats, context = compact_list("-ContinuityTail", str(args.window))
    known = {row["ref"] for row in rows}
    fresh = [ref for ref in refs if ref not in known]
    fetched = details(fresh, args.window + 500 if mode != "delta" else DETAIL_TAIL,
                      seed_rows() if mode != "delta" else None)
    dropped = max(0, len(rows) + len(fetched) - args.window)
    rows = (rows + fetched)[-args.window:]
    start, end = cursor_position(prior), cursor_position(cursor)
    identity_change = None
    if start is not None and end is not None:
        if start["file_identity"] != end["file_identity"]:
            identity_change = "file_identity_changed"
        elif start["generation"] != end["generation"]:
            identity_change = ("generation_configuration_changed" if None in (start["generation"], end["generation"])
                               else "generation_changed")
    provenance = {"cursor_prior": prior, "cursor_start": prior if mode == "delta" else None,
                  "cursor_end": cursor, "position_prior": start,
                  "position_start": start if mode == "delta" else None, "position_end": end,
                  "file_identity_status": "KNOWN" if end is not None else "UNKNOWN",
                  "generation_status": "KNOWN" if end is not None and end["generation"] is not None else "UNKNOWN",
                  "identity_change": identity_change, "reseed_reason": reseed_reason,
                  "reader_scope": context["scope"], "reader_stats": context["reader"], "listed_refs": refs,
                  "coverage": "PARTIAL", "window_scope": "sliding_window_not_complete_history",
                  "window_limit": args.window, "retained_rows": len(rows), "dropped_rows": dropped,
                  "manifest_digest_scope": "retained_ref_row_digest_source_manifest"}
    atomic_write(window_file, "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                                      for row in rows).encode("utf-8"))
    atomic_write(cursor_file, cursor.encode("ascii"))
    return rows, {"mode": mode, "new_rows": len(fresh), "stats": stats,
                  "detail_calls": sum(1 for row in fetched if row["source"] == "detail"), "provenance": provenance}


def write_capture(cycle_dir, rows, read_metadata):
    # capture v1 has strict consumers: keep its exact schema and bind the additive evidence in a sidecar.
    provenance_path = cycle_dir / "capture-provenance.json"
    if provenance_path.exists():
        raise CaptureRefused("capture_provenance_exists")
    manifest = "\n".join(f"{row['ref']} {row['row_sha256']} {row['source']}" for row in rows).encode("ascii")
    capture = {"schema": "wd.bridge-mind-capture.v1", "reader": READER, "reader_stdout_sha256": sha(manifest),
               "observed_utc": utc_now(seconds=True), "events": [row["event"] for row in rows]}
    path = cycle_dir / "capture.json"
    path.write_text(json.dumps(capture, ensure_ascii=False), encoding="utf-8")
    (cycle_dir / "detail-manifest.txt").write_bytes(manifest)
    provenance = {"schema": "wd.bridge-mind-capture-provenance.v1", "authority": "none",
                  "capture_sha256": sha(path.read_bytes()), "detail_manifest_sha256": sha(manifest),
                  "read": read_metadata}
    with provenance_path.open("xb") as handle:
        handle.write(canonical(provenance))
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


live.mkdir(parents=True, exist_ok=True)
for name in ("cycles", "files-cache", "state"):
    (live / name).mkdir(exist_ok=True)
log = live / "live-log.jsonl"
charter, loaded = charter_state()
latest_file = live / "latest.json"
previous, last_ok = None, None
if latest_file.exists():
    last_ok = json.loads(latest_file.read_text(encoding="ascii")).get("last_ok")
    if last_ok and (live / "cycles" / last_ok["cycle_dir"] / "run" / "reflected.publication.json").exists():
        previous = live / "cycles" / last_ok["cycle_dir"] / "run" / "reflected.publication.json"
for cycle in range(1, args.cycles + 1):
    tick = time.perf_counter()
    cycle_dir = live / "cycles" / utc_now(seconds=True).replace(":", "").replace("-", "")
    cycle_dir.mkdir()
    rec = {"cycle": cycle, "started_utc": utc_now(), "cycle_dir": cycle_dir.name, "reader": READER}
    try:
        started = time.perf_counter()
        rows, rec["read"] = advance(live / "state")
        capture_path = write_capture(cycle_dir, rows, rec["read"])
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
    latest = {"schema": "wd.fable5.mind-shadow-live-latest.v1", "authority": "none",
              "scope": "shadow_observation_not_gate", "cycle": cycle, "status": rec["status"],
              "reason": rec.get("reason"), "updated_utc": rec["finished_utc"], "last_ok": last_ok,
              "files": {"board": "latest-board.txt", "publication": "latest.publication.json",
                        "receipt": "latest-receipt.json", "log": "live-log.jsonl"},
              "verify": "read latest.publication.json with tools/wd_bridge_mind.py read_snapshot(path, audit_root="
                        "<this .codex-audit>); its snapshot sha256 must equal last_ok.snapshot_sha256",
              "report_to": {"type": "message", "task_id": REPORT_TASK},
              "advisory": "observe only; before any vote or pass check the canonical tools"}
    try:  # the privacy guard on every output this producer writes itself
        ws.guard_object(latest)
        ws.guard_object(rec)
    except ws.WorkspaceError:
        rec = {"cycle": cycle, "status": "refused", "reason": "output_guard", "cycle_dir": cycle_dir.name}
        latest = {"schema": "wd.fable5.mind-shadow-live-latest.v1", "authority": "none", "cycle": cycle,
                  "status": "refused", "reason": "output_guard", "last_ok": None}
    with open(log, "a", encoding="ascii") as handle:
        handle.write(json.dumps(rec, sort_keys=True, ensure_ascii=True) + "\n")
    atomic_write(latest_file, canonical(latest))
    print(json.dumps({k: rec.get(k) for k in ("cycle", "status", "reason", "cycle_ms")}), flush=True)
    if cycle < args.cycles:
        time.sleep(max(5, args.interval - (time.perf_counter() - tick)))
