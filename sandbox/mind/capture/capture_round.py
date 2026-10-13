"""External capture step for one tournament round: pinned compact bridge read plus read-only GitHub metadata.

usage: python -B sandbox/mind/capture/capture_round.py --bin <pinned .agent-bridge/bin> --out <new cycle dir>
           --files-cache <dir> [--seed-from <earlier capture.json>] [--window N] [--workers K] [--detail-tail N]
           [--reuse-context <earlier capture dir>]

This is the capture step outside the experiment loop (operator 10:17Z: a fresh pinned capture per round; Lead
09:17Z B1/B3/E4). Experiment code (freeze, driver, variants) never imports it and stays offline; it imports the
immutable files written here, each with its sha256, time and window.

* Bridge: only the inherited pinned ``Read-AgentBridge.ps1 -Compact`` path. ``-ContinuityTail <window>`` lists
  the refs, and ``-Compact -EventId <ref>`` returns each full row whose own canonical ref must equal the listed
  one. A row of ``--seed-from`` whose own canonical ref equals a listed ref is the same row (content hash) and is
  reused. A non-Compact read drains the accepted queue under the global publication fence, so none is made.
* GitHub: read-only metadata through ``gh`` (``pr list`` for state, head and branch; ``pr view`` for the files
  and commits of each OPEN PR at its head, cached immutably per PR and head in ``--files-cache``). The changed
  paths are classified here, once, with the code tree's charter (``evaluate_paths``) and classifier
  (``classify_ab``): this is the precomputed path-class oracle that the experiment reads frozen (E4).
  ``charter.loaded`` is true only when the charter loads and a positive and a negative classifier control hold.

Writes only the new ``--out`` directory (``capture.json``, ``detail-manifest.txt``, ``gh-pr-list.json``,
``context.json``, ``capture-record.json``) and new cache files. Never writes a bridge event, never runs gate
verdict code, never echoes event content in an error.
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

SRC = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SRC))
from tools import bridge_mind_workspace as ws  # noqa: E402
from tools.bridge_compact_view import event_id  # noqa: E402
from tools.check_standing_consensus_sign_class import classify_ab  # noqa: E402
from waggledance.core.idle_consensus_charter import DEFAULT_CHARTER_PATH, evaluate_paths, load_charter  # noqa: E402

READER = "Read-AgentBridge.ps1 -Compact -EventId"
REPO = "Ahkeratmehilaiset/waggledance-swarm"
LANE = re.compile(r"[a-z][a-z0-9-]{1,40}")
CLASSIFIER_FILE = SRC / "tools" / "check_standing_consensus_sign_class.py"
assert DEFAULT_CHARTER_PATH.resolve().is_relative_to(SRC), "charter outside the code tree"


class CaptureRefused(RuntimeError):
    """Fixed reasons only."""


def utc_now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def blob_sha1(path):
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def row_digest(event):
    return sha(json.dumps(event, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def reader(bin_dir, *options):
    script = str(bin_dir / "Read-AgentBridge.ps1").replace("'", "''")
    command = "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); & '" + script + "' -Compact " + \
        " ".join(options)
    done = subprocess.run(["pwsh", "-NoProfile", "-Command", command], capture_output=True, timeout=300)
    if done.returncode != 0:
        raise CaptureRefused("compact_reader_failed")
    return done.stdout


def bridge_capture(args):
    view = json.loads(reader(args.bin, "-ContinuityTail", str(args.window)).decode("utf-8"))
    if view.get("schema") != "wd.bridge.compact-view.v1":
        raise CaptureRefused("compact_view_shape")
    refs = [item["ref"] for item in view["events"]]
    seeded = {}
    if args.seed_from is not None:
        earlier = json.loads(args.seed_from.read_text(encoding="utf-8"))
        seeded = {event_id(event): event for event in earlier.get("events") or [] if isinstance(event, dict)}

    def one(ref):
        if ref in seeded:
            return {"ref": ref, "row_sha256": row_digest(seeded[ref]), "source": "seed", "event": seeded[ref]}
        report = json.loads(reader(args.bin, "-ContinuityTail", str(args.detail_tail), "-EventId", ref)
                            .decode("utf-8"))
        event = report.get("event") if report.get("schema") == "wd.bridge.event-detail.v1" else None
        if not isinstance(event, dict) or report.get("ref") != ref or event_id(event) != ref:
            raise CaptureRefused("detail_ref_mismatch")
        return {"ref": ref, "row_sha256": row_digest(event), "source": "detail", "event": event}

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        rows = list(pool.map(one, refs))
    manifest = "\n".join(f"{row['ref']} {row['row_sha256']} {row['source']}" for row in rows).encode("ascii")
    capture = {"schema": "wd.bridge-mind-capture.v1", "reader": READER, "reader_stdout_sha256": sha(manifest),
               "observed_utc": utc_now(), "events": [row["event"] for row in rows]}
    (args.out / "capture.json").write_text(json.dumps(capture, ensure_ascii=False), encoding="utf-8")
    (args.out / "detail-manifest.txt").write_bytes(manifest)
    return {"events": len(rows), "detail_calls": sum(row["source"] == "detail" for row in rows),
            "seed_rows": sum(row["source"] == "seed" for row in rows), "observed_utc": capture["observed_utc"]}


def gh(*options):
    done = subprocess.run(["gh", *options], capture_output=True, timeout=120)
    if done.returncode != 0:
        raise CaptureRefused("gh_failed")
    return done.stdout


def charter_state():
    try:
        charter = load_charter(DEFAULT_CHARTER_PATH)
    except Exception:
        return None, False
    negative = classify_ab(["tools/wd_pr_missing_slot_board.py"])["a_hits"] == []
    positive = classify_ab(["CLAUDE.md"])["a_hits"] == ["claude.md"]
    return charter, bool(charter.allowlist) and bool(charter.file_denylist) and negative and positive


def path_class(charter, paths):
    if not paths or len(paths) >= 100 or not all(ws.SAFE_PATH.fullmatch(p) for p in paths):
        return None  # empty, possibly truncated at the 100-file page, or outside the safe alphabet: UNKNOWN
    result = classify_ab(paths)
    denied = [p for p in paths if evaluate_paths(charter, [p]).reason == "denylist hit"]
    row = {"charter_allowed": evaluate_paths(charter, paths).allowed, "denylist_hits": sorted(set(denied)),
           "a_hits": list(result["a_hits"]), "unrecognized": list(result["unrecognized"])}
    if not all(ws.SAFE_PATH.fullmatch(p) for name in ("denylist_hits", "a_hits", "unrecognized") for p in row[name]):
        return None
    return row


def commit_authors(view):
    commits = view.get("commits")
    if not isinstance(commits, list) or not commits or len(commits) >= 100:
        return None
    names = {author.get("name") for commit in commits for author in commit.get("authors") or []
             if isinstance(author, dict)}
    return sorted(name for name in names if isinstance(name, str) and LANE.fullmatch(name))


def github_context(args):
    charter, loaded = charter_state()
    data = gh("pr", "list", "--repo", REPO, "--state", "all", "--limit", "400", "--json",
              "number,state,headRefOid,headRefName")
    observed = utc_now()
    (args.out / "gh-pr-list.json").write_bytes(data)
    rows, fetched = {}, 0
    for item in json.loads(data):
        number, head, branch, state = item["number"], item["headRefOid"], item["headRefName"], item["state"]
        if state not in ws.PR_STATES or not ws.HEAD.fullmatch(head or "") or not ws.TASK.fullmatch(branch or ""):
            continue
        klass, authors = None, None
        if state == "OPEN":
            cache = args.files_cache / f"{number}-{head}.v2.json"
            if not cache.exists():
                cache.write_bytes(gh("pr", "view", str(number), "--repo", REPO, "--json", "files,headRefOid,commits"))
                fetched += 1
            view = json.loads(cache.read_bytes())
            if view.get("headRefOid") == head:
                klass = path_class(charter, [f["path"] for f in view.get("files") or []]) if loaded else None
                authors = commit_authors(view)
        rows[str(number)] = {"state": state, "head": head, "head_ref_name": branch, "path_class": klass,
                             "commit_authors": authors}
    context = {"schema": "wd.bridge-mind-pr-context.v2", "authority": "none", "capture_sha256": sha(data),
               "observed_utc": observed,
               "charter": {"loaded": loaded, "charter_blob": blob_sha1(DEFAULT_CHARTER_PATH),
                           "classifier_blob": blob_sha1(CLASSIFIER_FILE)},
               "prs": rows}
    (args.out / "context.json").write_bytes(
        (json.dumps(context, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii"))
    return {"prs": len(rows), "views_fetched": fetched, "charter_loaded": loaded, "observed_utc": observed}


CHECK_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._ ()/:+-]{0,99}")
MERGE_STATES = frozenset({"CLEAN", "BLOCKED", "BEHIND", "DIRTY", "UNSTABLE", "HAS_HOOKS", "DRAFT", "UNKNOWN"})


BADGE = re.compile(r"!\[(P[0-3]) Badge\]")
MENTION = re.compile(r"(?<![\w/])#([0-9]{3,5})\b")
THREADS_QUERY = ("query($owner:String!,$name:String!,$n:Int!){repository(owner:$owner,name:$name){pullRequest(number:$n)"
                 "{headRefOid reviewThreads(first:100){totalCount nodes{isResolved isOutdated "
                 "comments(first:1){nodes{author{__typename} body}}}}}}}")


def bot_threads(number):
    """Bot review threads of one PR by state (open, outdated, resolved) and badge severity; no comment text kept."""
    owner, name = REPO.split("/")
    data = json.loads(gh("api", "graphql", "-f", "query=" + THREADS_QUERY, "-f", "owner=" + owner, "-f", "name=" + name,
                         "-F", f"n={number}"))
    pr = data["data"]["repository"]["pullRequest"]
    nodes = pr["reviewThreads"]["nodes"]
    counts = {}
    for node in nodes:
        first = (node["comments"]["nodes"] or [None])[0]
        if not isinstance(first, dict) or (first.get("author") or {}).get("__typename") != "Bot":
            continue
        match = BADGE.search(first.get("body") or "")
        state = "resolved" if node["isResolved"] else ("outdated" if node["isOutdated"] else "open")
        severity = match.group(1) if match else "unrated"
        counts.setdefault(state, {})[severity] = counts.get(state, {}).get(severity, 0) + 1
    if not ws.HEAD.fullmatch(pr.get("headRefOid") or ""):
        raise CaptureRefused("thread_head_invalid")
    return {"head": pr["headRefOid"], "complete": pr["reviewThreads"]["totalCount"] <= len(nodes), "bot": counts}


def mentions():
    """Open PR number -> the PR numbers its title or body mentions (#NNNN); the text itself is never kept."""
    data = gh("pr", "list", "--repo", REPO, "--state", "open", "--limit", "250", "--json", "number,title,body")
    out = {}
    for item in json.loads(data):
        if type(item.get("number")) is int:
            text = f"{item.get('title') or ''}\n{item.get('body') or ''}"
            out[str(item["number"])] = sorted({int(n) for n in MENTION.findall(text)} - {item["number"]})
    return out


def github_status(args):
    """Required checks per open PR head as GitHub reports them, and the merge state; observed, never a verdict.

    The required set is the branch protection's own list for main. A PR is ``all_required_success`` true only when
    every required name is present and every row of it is SUCCESS; false when one is not; null when the required
    set could not be read. Merge states outside the known set are UNKNOWN.
    """
    required = json.loads(gh("api", f"repos/{REPO}/branches/main", "--jq",
                             ".protection.required_status_checks.contexts"))
    if not isinstance(required, list) or not required or not all(isinstance(name, str) and CHECK_NAME.fullmatch(name)
                                                                 for name in required):
        required = None
    data = gh("pr", "list", "--repo", REPO, "--state", "open", "--limit", "250", "--json",
              "number,headRefOid,mergeStateStatus,statusCheckRollup")
    observed = utc_now()
    rows = {}
    for item in json.loads(data):
        head = item.get("headRefOid")
        if type(item.get("number")) is not int or not ws.HEAD.fullmatch(head or ""):
            continue
        seen = {}
        for check in item.get("statusCheckRollup") or []:
            name = (check.get("name") or check.get("context")) if isinstance(check, dict) else None
            if required is not None and name in required:
                result = check.get("conclusion") or check.get("state") or "PENDING"
                seen.setdefault(name, set()).add(result if result in ("SUCCESS", "FAILURE", "PENDING", "NEUTRAL",
                                                                      "CANCELLED", "SKIPPED", "TIMED_OUT",
                                                                      "ACTION_REQUIRED", "ERROR", "EXPECTED",
                                                                      "STALE", "STARTUP_FAILURE") else "UNKNOWN")
        missing = [] if required is None else [name for name in required if name not in seen]
        rows[str(item["number"])] = {
            "head": head,
            "merge_state": item.get("mergeStateStatus") if item.get("mergeStateStatus") in MERGE_STATES else "UNKNOWN",
            "required_results": {name: sorted(results) for name, results in sorted(seen.items())},
            "missing_required": missing,
            "all_required_success": None if required is None else (not missing and all(
                results == {"SUCCESS"} for results in seen.values()))}
    # Lead 10:45Z: bot-review coverage and replacement stay separate facts beside the votes. Each part that fails
    # is recorded as refused for that part only; the required checks above never depend on it.
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        # Only PRs whose required checks all passed at the head: the others cannot be vote-complete on GitHub's side.
        futures = {number: pool.submit(bot_threads, number) for number, row in rows.items()
                   if row["all_required_success"] is True}
        for number, future in futures.items():
            try:
                rows[number]["bot_threads"] = future.result()
            except (CaptureRefused, ValueError, KeyError, TypeError):
                rows[number]["bot_threads"] = {"refused": "thread_query_failed"}
    try:
        mentioned = mentions()
        for number, row in rows.items():
            row["mentions"] = mentioned.get(number, [])
    except (CaptureRefused, ValueError, KeyError, TypeError):
        mentioned = None
    status = {"schema": "wd.bridge-mind-gh-status.v1", "authority": "none", "observed_utc": observed,
              "required_contexts": required, "list_sha256": sha(data), "prs": rows,
              "mentions_captured": mentioned is not None}
    ws.guard_object(status)
    (args.out / "gh-open-status.json").write_bytes(data)
    (args.out / "observed-status.json").write_bytes(
        (json.dumps(status, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii"))
    return {"open_prs": len(rows), "observed_utc": observed}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bin", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--files-cache", type=Path, required=True)
    parser.add_argument("--seed-from", type=Path, default=None)
    parser.add_argument("--window", type=int, default=3000)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--detail-tail", type=int, default=600, help="tail searched for each -EventId detail read")
    parser.add_argument("--with-status", action="store_true",
                        help="also capture required checks and merge state of every open PR (about 20 s)")
    parser.add_argument("--reuse-context", type=Path, default=None,
                        help="an earlier capture dir whose gh-pr-list.json and context.json are copied, no gh call")
    args = parser.parse_args()
    args.out = Path(os.path.abspath(args.out))
    args.out.mkdir(parents=True)  # exclusive: one capture per directory
    args.files_cache.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    record = {"schema": "wd.bridge-mind-capture-record.v1", "authority": "none", "repo": REPO,
              "bridge": bridge_capture(args)}
    record["bridge"]["ms"] = round((time.perf_counter() - started) * 1000)
    started = time.perf_counter()
    if args.reuse_context is not None:
        for name in ("gh-pr-list.json", "context.json", "gh-open-status.json", "observed-status.json"):
            if (args.reuse_context / name).exists() or name in ("gh-pr-list.json", "context.json"):
                (args.out / name).write_bytes((args.reuse_context / name).read_bytes())
        reused = json.loads((args.out / "context.json").read_text(encoding="ascii"))
        record["github"] = {"reused_from": args.reuse_context.name, "observed_utc": reused["observed_utc"]}
    else:
        record["github"] = github_context(args)
        if args.with_status:
            # Optional and heavy (GitHub sometimes times the rollup query out): a failure leaves the status UNKNOWN
            # (no observed-status.json in this capture) and never refuses the capture itself.
            started = time.perf_counter()
            try:
                record["github"]["status"] = github_status(args)
            except (CaptureRefused, ValueError, KeyError, TypeError):
                record["github"]["status"] = {"refused": "status_capture_failed"}
            record["github"]["status"]["ms"] = round((time.perf_counter() - started) * 1000)
    record["github"]["ms"] = round((time.perf_counter() - started) * 1000)
    record["files"] = {name: sha((args.out / name).read_bytes())
                       for name in ("capture.json", "detail-manifest.txt", "gh-pr-list.json", "context.json",
                                    "gh-open-status.json", "observed-status.json") if (args.out / name).exists()}
    ws.guard_object(record)
    (args.out / "capture-record.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n",
                                                  encoding="ascii")
    print(json.dumps({k: record[k] for k in ("bridge", "github")}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CaptureRefused as exc:
        print(json.dumps({"refused": str(exc)}))
        sys.exit(2)
