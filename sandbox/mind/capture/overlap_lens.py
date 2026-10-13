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
* ``unknown``: no shared path is known to differ, but at least one could not be read.

Commit authors are the git author names the context recorded: declared by whoever committed, not verified.

With about 200 open PRs most pairs are old and diverged, so a lane's lines show only ACTIVE pairs: both PRs have
a commit (committedDate from the cached commit list) within ``--active-hours`` of the context observation. Older
pairs are only counted. A PR with no readable commit date is never called active.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
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
NOTE = ("same = identical file at both heads; diverged = the two heads hold different versions of a shared path; "
        "UNKNOWN = a head or path not in the local object store, never fetched; authors are declared commit authors")


def git(git_dir, *options, data=None):
    done = subprocess.run(["git", "-C", str(git_dir), *options], input=data, capture_output=True, timeout=60)
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
    try:
        value = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return value.astimezone(dt.timezone.utc) if value.tzinfo is not None else None


def open_prs(context, cache_dir):
    """number -> {head, authors, files (set or None), truncated} for every OPEN row of the context."""
    rows = {}
    for key, row in sorted(context["prs"].items(), key=lambda item: int(item[0])):
        if row.get("state") != "OPEN" or not ws.HEAD.fullmatch(row.get("head") or ""):
            continue
        files, truncated, last = None, False, None
        cache = cache_dir / f"{key}-{row['head']}.v2.json"
        if cache.exists():
            view = json.loads(cache.read_bytes())
            if view.get("headRefOid") == row["head"] and isinstance(view.get("files"), list):
                files = {item["path"] for item in view["files"] if isinstance(item, dict) and item.get("path")}
                truncated = len(view["files"]) >= FILES_LIMIT
                dates = [parse_utc(item.get("committedDate")) for item in view.get("commits") or []
                         if isinstance(item, dict)]
                dates = [date for date in dates if date is not None]
                last = max(dates) if dates else None
        rows[int(key)] = {"head": row["head"], "authors": sorted(row.get("commit_authors") or []), "files": files,
                          "truncated": truncated, "last_commit": last}
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
        relation = "diverged" if diverged else ("unknown" if unknown else "same")
        pairs.append({"a": a, "b": b, "relation": relation, "same": same, "diverged": diverged, "unknown": unknown})
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
    mine = {n for n, row in prs.items() if lane in row["authors"]}
    rows, older = [], 0
    for pair in pairs:
        if not mine & {pair["a"], pair["b"]}:
            continue
        if not (active(prs[pair["a"]], now, hours) and active(prs[pair["b"]], now, hours)):
            older += 1
            continue
        own, other = (pair["a"], pair["b"]) if pair["a"] in mine else (pair["b"], pair["a"])
        who = ", ".join(prs[other]["authors"]) or "UNKNOWN"
        if pair["relation"] == "diverged":
            text = f"diverged on {short(pair['diverged'])}"
        elif pair["relation"] == "unknown":
            text = f"UNKNOWN on {short(pair['unknown'])} (head not in the local store)"
        else:
            text = f"same file on {short(pair['same'])}"
        order = {"diverged": 0, "unknown": 1, "same": 2}[pair["relation"]]
        rows.append((order, -len(pair["diverged"]), own, other, f"OVERLAP your PR{own} with PR{other} ({who}): {text}"))
    rows.sort()
    lines = [row[-1] for row in rows[:LANE_LINES]]
    if len(rows) > LANE_LINES:
        lines.append(f"OVERLAP: {len(rows) - LANE_LINES} more active pairs for {lane}")
    if not rows:
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
    raw = args.context.read_bytes()
    context = json.loads(raw)
    prs, pairs, files_unknown, truncated, heads_missing = project(context, args.files_cache, args.git_dir)
    counts = {relation: sum(1 for pair in pairs if pair["relation"] == relation)
              for relation in ("diverged", "unknown", "same")}
    head = (f"overlap observed with context {context.get('observed_utc', 'UNKNOWN')}: {len(prs)} open PRs, "
            f"{len(pairs)} pairs share a path ({counts['diverged']} diverged, {counts['unknown']} UNKNOWN, "
            f"{counts['same']} same); file list UNKNOWN for {len(files_unknown)}, maybe cut off for {len(truncated)}")
    now = parse_utc(context.get("observed_utc"))
    lanes = {lane: [head] + lane_lines(lane, prs, pairs, now, args.active_hours) for lane in args.lane}
    view = {"schema": LENS_SCHEMA, "authority": "none", "scope": "shadow_observation_not_gate",
            "context_sha256": hashlib.sha256(raw).hexdigest(), "context_observed_utc": context.get("observed_utc"),
            "open_prs": len(prs), "counts": counts, "pairs": pairs, "files_unknown": files_unknown,
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
