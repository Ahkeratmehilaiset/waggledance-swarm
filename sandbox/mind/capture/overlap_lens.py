"""OVERLAP lens: open PRs that change the same path, and whether that path is the same file at both heads.

usage: python -B sandbox/mind/capture/overlap_lens.py --context <cycle dir>/context.json --files-cache <dir>
           --git-dir <repo> --out-dir <cycle dir>/hook-overlap [--publish-dir <live dir>/overlap] [--lane <lane>]...

Why (fable-5, 2026-10-13 13:10Z): PR1878 (a train) and PR1898 (one of its components) each got a different fix
for the same compact request-binding code within an hour, and nobody saw it until a train composition hit the
overlap. This lens reads only what a capture already holds: the context (open PRs, heads, commit authors) and the
file list of each open PR at its head in the files cache. Whether a shared path is the same file at both heads
comes from the local git object store, read-only (``git cat-file --batch-check`` and ``git ls-tree``). A head
that is not in the local store gives UNKNOWN for its paths, never a guess, and nothing is fetched. No network,
no bridge event, no gate code.

Relation of two open PRs that share at least one path:

* ``same``: every shared path is the same blob (or absent) at both heads, e.g. a train composed from its
  component at the component's head.
* ``diverged``: at least one shared path differs at the two heads: the PRs change that file differently, so one
  may duplicate, undo or conflict with the other. This is the line a lane should read before editing.
* ``unknown``: no shared path is known to differ, but at least one could not be read, or the file list of one
  side may be cut off (``FILES_LIMIT`` entries), so another shared path is UNKNOWN: ``same`` is never claimed then.

A lane's "no overlap" line is stated only when no file list that could hide an active overlap of that lane is
UNKNOWN (no cached list, or one not read whole) or may be cut off, and no active pair includes a PR whose commit
authors are UNKNOWN, since that PR may be the lane's; otherwise the lane gets an UNKNOWN line, never an absence.

Commit authors are the git author names the context recorded: declared by whoever committed, not verified. They
are UNKNOWN when the context holds null (the producer's commit list may be incomplete) or nothing (a v1 context);
an empty list is known: no lane-shaped author.

The context is read whole or refused before any write (``refused: context_unreadable`` when it is not readable
JSON, ``refused: context_shape_unexpected`` when it is not of the shape read here): a v1 or v2 context schema, an
aware ``observed_utc``, and ``prs`` an object keyed by PR numbers of at most 9 digits whose rows have a known
state; each OPEN row with a 40-hex head and commit authors that are null, absent or a list of lane-shaped names.
``--active-hours`` below 0 or beyond what a timedelta holds is refused before any write too. A cached files view
is read whole or not at all: one that is not readable JSON for that head, whose files are not a list of objects
with a non-empty git path, or whose commits are not a list of objects, gives UNKNOWN files. A git read that fails
(a timeout, or git not started) reads as a head not in the local store: UNKNOWN.

With about 200 open PRs most pairs are old and diverged, so a lane's lines show only ACTIVE pairs: both PRs have
a commit (committedDate from the cached commit list) at most ``--active-hours`` before the context observation,
or after it: the rule is one-sided, so a declared date after the observation counts as active. Older pairs are
only counted. A PR with no readable commit date is never called active. A commit date is declared by
whoever committed, not verified, so "active" is this reading rule, not proof of work or of inactivity: the
header line names it with its hours, and the view keeps ``active_hours``.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
from itertools import combinations
from pathlib import Path

SRC = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SRC))
from tools import bridge_mind_workspace as ws  # noqa: E402
from tools import wd_bridge_mind as core  # noqa: E402

LENS_SCHEMA = "wd.bridge-mind-overlap-lens.v1"
FILES_LIMIT = 100  # gh pr view --json files: a list of this many may be cut off
LANE_LINES = 12
ACTIVE_HOURS = 48
MAX_ACTIVE_HOURS = dt.timedelta.max // dt.timedelta(hours=1)  # the widest window in whole hours a timedelta holds
CONTEXT_SCHEMAS = (ws.CONTEXT_SCHEMA, ws.CONTEXT_SCHEMA_V2)
PR_KEY = re.compile(r"[1-9][0-9]{0,8}")
NOTE = ("same = identical file at both heads; diverged = the two heads hold different versions of a shared path; "
        "UNKNOWN = a head or path not in the local object store, never fetched, or a file list that may be cut off; "
        "authors are declared commit authors, UNKNOWN when the context did not record them or its list may be "
        "incomplete")


class LensRefused(Exception):
    """The context cannot be read whole; the reason is a fixed token and nothing is written."""


def git(git_dir, *options, data=None):
    """(exit code, stdout) of one read-only git call; -1 when it does not finish (a timeout, or git not started)."""
    try:
        done = subprocess.run(["git", "-C", str(git_dir), *options], input=data, capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return -1, ""
    return done.returncode, done.stdout.decode("utf-8", "replace")


def present_commits(git_dir, heads):
    """The heads whose commit object is in the local store (one batch call, read-only)."""
    if not heads:
        return set()
    code, out = git(git_dir, "cat-file", "--batch-check", data="".join(f"{h}^{{commit}}\n" for h in heads).encode())
    if code != 0:
        return set()
    return {line.split()[0] for line in out.splitlines() if len(line.split()) == 3 and line.split()[1] == "commit"}


def blobs_at(git_dir, head, paths):
    """path -> blob id at head, or None when the path is absent at that head; one ls-tree call per head."""
    code, out = git(git_dir, "ls-tree", "-r", "-z", head, "--", *sorted(paths))
    if code != 0:
        return None
    found = {}
    for record in out.split("\0"):
        if not record:
            continue
        meta, _, path = record.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob":
            found[path] = parts[2]
    return {path: found.get(path) for path in paths}


def parse_utc(text):
    """An aware ISO time in UTC, or None: not a string, not ISO, naive, or out of range once in UTC."""
    if not isinstance(text, str):
        return None
    try:
        value = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return value.astimezone(dt.timezone.utc) if value.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def readable_row(key, row):
    """A context row of the shape read here: a PR number key and a known state; an OPEN row also has a 40-hex head and
    commit authors that are null, absent or a list of lane-shaped names."""
    if not (PR_KEY.fullmatch(key) and isinstance(row, dict) and row.get("state") in ws.PR_STATES):
        return False
    if row["state"] != "OPEN":
        return True
    head, authors = row.get("head"), row.get("commit_authors")
    return (isinstance(head, str) and ws.HEAD.fullmatch(head) is not None
            and (authors is None or isinstance(authors, list)
                 and all(isinstance(name, str) and ws.LANE_ID.fullmatch(name) for name in authors)))


def read_context(path):
    """(raw bytes, context, observation time in UTC) of a context read whole, else LensRefused: never read in part."""
    try:
        raw = path.read_bytes()
        context = json.loads(raw)
    except (OSError, ValueError, RecursionError):
        raise LensRefused("context_unreadable") from None
    if not isinstance(context, dict):
        raise LensRefused("context_shape_unexpected")
    now, rows = parse_utc(context.get("observed_utc")), context.get("prs")
    if context.get("schema") not in CONTEXT_SCHEMAS or now is None or not isinstance(rows, dict) \
            or not all(readable_row(key, row) for key, row in rows.items()):
        raise LensRefused("context_shape_unexpected")
    return raw, context, now


def cached_view(path, head):
    """(files, truncated, last commit) of the cached files view of a PR at its head, or (None, False, None) when there
    is none for that head or it is not read whole: UNKNOWN files, never a partial list."""
    try:
        view = json.loads(path.read_bytes())
    except (OSError, ValueError, RecursionError):
        return None, False, None
    if not isinstance(view, dict) or view.get("headRefOid") != head:
        return None, False, None
    files, commits = view.get("files"), view.get("commits")
    if not (isinstance(files, list) and all(isinstance(item, dict) and isinstance(item.get("path"), str)
                                            and item["path"] and "\0" not in item["path"] for item in files)
            and isinstance(commits, list) and all(isinstance(item, dict) for item in commits)):
        return None, False, None
    dates = [date for date in (parse_utc(item.get("committedDate")) for item in commits) if date is not None]
    return {item["path"] for item in files}, len(files) >= FILES_LIMIT, max(dates) if dates else None


def open_prs(context, cache_dir):
    """number -> {head, authors (None when UNKNOWN), files (set or None), truncated, last_commit} for every OPEN row
    of a context read whole."""
    rows = {}
    for key, row in sorted(context["prs"].items(), key=lambda item: int(item[0])):
        if row["state"] != "OPEN":
            continue
        files, truncated, last = cached_view(cache_dir / f"{key}-{row['head']}.v2.json", row["head"])
        authors = row.get("commit_authors")
        rows[int(key)] = {"head": row["head"], "authors": None if authors is None else sorted(authors),
                          "files": files, "truncated": truncated, "last_commit": last}
    return rows


def project(context, cache_dir, git_dir):
    prs = open_prs(context, cache_dir)
    by_path = {}
    for number, row in prs.items():
        for path in row["files"] or ():
            by_path.setdefault(path, set()).add(number)
    shared = {}
    for path, numbers in by_path.items():
        for a, b in combinations(sorted(numbers), 2):
            shared.setdefault((a, b), set()).add(path)
    wanted = {}
    for (a, b), paths in shared.items():
        wanted.setdefault(a, set()).update(paths)
        wanted.setdefault(b, set()).update(paths)
    present = present_commits(git_dir, sorted({prs[n]["head"] for n in wanted}))
    blobs = {n: (blobs_at(git_dir, prs[n]["head"], paths) if prs[n]["head"] in present else None)
             for n, paths in wanted.items()}
    pairs = []
    for (a, b), paths in sorted(shared.items()):
        same, diverged, unknown = [], [], []
        for path in sorted(paths):
            left = None if blobs[a] is None else blobs[a].get(path, None)
            right = None if blobs[b] is None else blobs[b].get(path, None)
            if blobs[a] is None or blobs[b] is None:
                unknown.append(path)
            elif left == right:
                same.append(path)
            else:
                diverged.append(path)
        cut_off = [n for n in (a, b) if prs[n]["truncated"]]  # another shared path may be beyond a cut-off list
        relation = "diverged" if diverged else ("unknown" if unknown or cut_off else "same")
        pair = {"a": a, "b": b, "relation": relation, "same": same, "diverged": diverged, "unknown": unknown}
        if cut_off:  # absent when none, so a pair of two complete lists keeps its exact shape
            pair["cut_off"] = cut_off
        pairs.append(pair)
    files_unknown = sorted(n for n, row in prs.items() if row["files"] is None)
    truncated = sorted(n for n, row in prs.items() if row["truncated"])
    heads_missing = sorted(n for n in wanted if prs[n]["head"] not in present)
    return prs, pairs, files_unknown, truncated, heads_missing


def short(paths, limit=3):
    shown = ", ".join(paths[:limit])
    return shown + (f" and {len(paths) - limit} more" if len(paths) > limit else "")


def active(row, now, hours):
    return now is not None and row["last_commit"] is not None and now - row["last_commit"] <= dt.timedelta(hours=hours)


def lane_lines(lane, prs, pairs, now, hours=ACTIVE_HOURS):
    mine = {n for n, row in prs.items() if row["authors"] is not None and lane in row["authors"]}
    unknown = {n for n, row in prs.items() if row["authors"] is None}  # may be any lane's PR
    rows, older = [], 0
    for pair in pairs:
        if not mine & {pair["a"], pair["b"]}:
            continue
        if not (active(prs[pair["a"]], now, hours) and active(prs[pair["b"]], now, hours)):
            older += 1
            continue
        own, other = (pair["a"], pair["b"]) if pair["a"] in mine else (pair["b"], pair["a"])
        authors = prs[other]["authors"]
        who = "UNKNOWN" if authors is None else ", ".join(authors) or "no lane-shaped author"
        cut = ", ".join(f"PR{n}" for n in pair.get("cut_off", ()))
        if pair["relation"] == "diverged":
            text = f"diverged on {short(pair['diverged'])}"
        elif pair["unknown"]:
            text = f"UNKNOWN on {short(pair['unknown'])} (head not in the local store)" \
                + (f"; the file list of {cut} may be cut off" if cut else "")
        elif cut:
            text = f"same file on {short(pair['same'])} so far, but the file list of {cut} may be cut off: " \
                "other shared paths UNKNOWN"
        else:
            text = f"same file on {short(pair['same'])}"
        order = {"diverged": 0, "unknown": 1, "same": 2}[pair["relation"]]
        rows.append((order, -len(pair["diverged"]), own, other, f"OVERLAP your PR{own} with PR{other} ({who}): {text}"))
    rows.sort()
    lines = [row[-1] for row in rows[:LANE_LINES]]
    if len(rows) > LANE_LINES:
        lines.append(f"OVERLAP: {len(rows) - LANE_LINES} more active pairs for {lane}")
    if not rows:
        # The absence is shown only when proven: a PR that may be the lane's (its own, or one whose commit authors are
        # UNKNOWN) and may be active (no cached list, so its commit dates are UNKNOWN too, or a recent commit) with any
        # list that could hide an active pair, or an active pair with a PR whose authors are UNKNOWN, make it UNKNOWN.
        could = [n for n in sorted(mine | unknown) if prs[n]["files"] is None or active(prs[n], now, hours)]
        unread = sorted(n for n, row in prs.items()
                        if row["files"] is None or (row["truncated"] and active(row, now, hours)))
        hidden = sorted({n for pair in pairs for n in (pair["a"], pair["b"]) if n in unknown
                         and active(prs[pair["a"]], now, hours) and active(prs[pair["b"]], now, hours)})
        if could and unread:
            lines.append(f"OVERLAP: UNKNOWN for {lane}: the file list of {short([f'PR{n}' for n in unread])} is "
                         "UNKNOWN or may be cut off, so an active overlap may be missing")
        if hidden:
            lines.append(f"OVERLAP: UNKNOWN for {lane}: the commit authors of {short([f'PR{n}' for n in hidden])} are "
                         "UNKNOWN and each is in an active pair, so an active overlap may be missing")
        if not hidden and not (could and unread):
            lines.append(f"OVERLAP: no active open PR of {lane} shares a path with another active open PR")
    if older:
        lines.append(f"OVERLAP: {older} older pairs for {lane} not shown (one side has no commit in {hours} h)")
    return lines


def publish(directory, name, data):
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / f".{name}.{os.getpid()}.tmp"
    tmp.write_bytes(data)
    core._sharing_retry(lambda: os.replace(tmp, directory / name))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--files-cache", type=Path, required=True)
    parser.add_argument("--git-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--publish-dir", type=Path, default=None)
    parser.add_argument("--lane", action="append", default=[])
    parser.add_argument("--active-hours", type=int, default=ACTIVE_HOURS)
    args = parser.parse_args(argv)
    if not all(ws.LANE_ID.fullmatch(lane) for lane in args.lane):
        raise SystemExit("refused: lane id invalid")
    if not 0 <= args.active_hours <= MAX_ACTIVE_HOURS:
        raise SystemExit("refused: active hours invalid")
    try:
        raw, context, now = read_context(args.context)
    except LensRefused as refused:
        raise SystemExit(f"refused: {refused}") from None
    prs, pairs, files_unknown, truncated, heads_missing = project(context, args.files_cache, args.git_dir)
    authors_unknown = sorted(n for n, row in prs.items() if row["authors"] is None)
    counts = {relation: sum(1 for pair in pairs if pair["relation"] == relation)
              for relation in ("diverged", "unknown", "same")}
    head = (f"overlap observed with context {context['observed_utc']}: {len(prs)} open PRs, "
            f"{len(pairs)} pairs share a path ({counts['diverged']} diverged, {counts['unknown']} UNKNOWN, "
            f"{counts['same']} same); file list UNKNOWN for {len(files_unknown)}, maybe cut off for {len(truncated)}"
            f"; commit authors UNKNOWN for {len(authors_unknown)}; active = a declared commit date at most "
            f"{args.active_hours} h before the context observation or after it, not proof of work")
    lanes = {lane: [head] + lane_lines(lane, prs, pairs, now, args.active_hours) for lane in args.lane}
    view = {"schema": LENS_SCHEMA, "authority": "none", "scope": "shadow_observation_not_gate",
            "context_sha256": hashlib.sha256(raw).hexdigest(), "context_observed_utc": context["observed_utc"],
            "open_prs": len(prs), "active_hours": args.active_hours, "counts": counts, "pairs": pairs,
            "files_unknown": files_unknown, "authors_unknown": authors_unknown,
            "files_maybe_truncated": truncated, "heads_not_in_local_store": heads_missing, "lanes": lanes,
            "note": NOTE}
    ws.guard_object(view)
    body = json.dumps(view, indent=1, sort_keys=True, ensure_ascii=True).encode("ascii")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "overlap-lens.json").write_bytes(body)
    if args.publish_dir is not None:
        publish(args.publish_dir, "overlap-lens.json", body)
        for lane, lines in lanes.items():
            publish(args.publish_dir, f"lens-overlap.{lane}.txt", ("\n".join(lines) + "\n").encode("ascii", "replace"))
    print(json.dumps({"open_prs": len(prs), "pairs": len(pairs), **counts, "heads_missing": len(heads_missing)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
