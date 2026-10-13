"""Lesson cards: the fleet's curated lessons as small structured cards, frozen once per round; offline.

usage: python -B sandbox/mind/lesson_cards.py --notes <lessons dir> --as-of <UTC time> --out <new .json>

Cards-first (Lead 08:46Z): no vectors, backend, model or raw-message corpus. The source is the lanes' own
curated lesson notes (one Markdown file per lesson with a ``name``/``description``/``type`` front matter), never
bridge message text. A card keeps the note's name, type, author lane (from the file name suffix), modification
time, its description (capped) and the PR numbers it names, plus keywords from its name. Only ``feedback`` and
``reference`` notes modified at or before ``--as-of`` become cards, so a round's cards cannot know what happened
after its capture. Every card passes the workspace's decoded privacy guard; a note that fails it is left out and
counted. The output is local sandbox data and never goes to git.
"""

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SRC))
from tools import bridge_mind_workspace as ws  # noqa: E402

CARDS_SCHEMA = "wd.bridge-mind-lesson-cards.v1"
LESSON_TYPES = ("feedback", "reference")
DESCRIPTION_LIMIT = 300
LANE_SUFFIX = {"rco1": "claude-rco-1", "rco2": "claude-rco-2", "fable5": "fable-5", "rco": "claude-rco"}
PR_NUMBER = re.compile(r"(?:PR|#|pr)\s?(1[0-9]{3})\b")
STOP = frozenset("a an and are as at be by for from in into is it its of on or the to with not no never only "
                 "must when then than this that use used my our".split())


def front_matter(text):
    if not text.startswith("---"):
        return None
    head = text.split("\n---", 1)[0]
    fields = {}
    for line in head.splitlines()[1:]:
        match = re.match(r"\s*(name|description|type|modified):\s*(.*)$", line)
        if match and match.group(1) not in fields:
            value = match.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1].replace('\\"', '"')
            fields[match.group(1)] = value
    return fields


def card(path, fields, mtime):
    stem = path.stem
    lane = next((lane for suffix, lane in LANE_SUFFIX.items()
                 if re.search(rf"_{suffix}(?:_\d{{8}})?$", stem)), ws.UNKNOWN)
    description = fields.get("description", "")
    words = [word for word in re.split(r"[^a-z0-9]+", fields.get("name", stem).lower())
             if len(word) > 2 and word not in STOP and not word.isdigit()]
    return {"id": "L" + hashlib.sha256(stem.encode("utf-8")).hexdigest()[:8], "name": stem,
            "type": fields["type"], "lane": lane, "modified_utc": mtime,
            "description": description if len(description) <= DESCRIPTION_LIMIT else
            description[:DESCRIPTION_LIMIT - 3] + "...",
            "prs": sorted({int(number) for number in PR_NUMBER.findall(description + " " + stem)}),
            "keywords": sorted(set(words))}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--notes", type=Path, required=True)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    as_of = ws.parse_time(args.as_of)
    if as_of is None:
        raise SystemExit("as-of is not a UTC time")
    cards, skipped, source = [], {"not_lesson": 0, "after_as_of": 0, "no_front_matter": 0, "guard": 0}, []
    for path in sorted(args.notes.glob("*.md")):
        data = path.read_bytes()
        fields = front_matter(data.decode("utf-8", errors="replace").replace("\r\n", "\n"))
        if fields is None or "type" not in fields:
            skipped["no_front_matter"] += 1
            continue
        if fields["type"] not in LESSON_TYPES:
            skipped["not_lesson"] += 1
            continue
        modified = ws.parse_time(fields.get("modified")) or dt.datetime.fromtimestamp(path.stat().st_mtime,
                                                                                         dt.timezone.utc)
        if modified > as_of:
            skipped["after_as_of"] += 1
            continue
        item = card(path, fields, modified.strftime("%Y-%m-%dT%H:%M:%SZ"))
        try:
            ws.guard_object(item)
        except ws.WorkspaceError:
            skipped["guard"] += 1
            continue
        cards.append(item)
        source.append(f"{path.name} {hashlib.sha256(data).hexdigest()}")
    cards.sort(key=lambda item: (item["modified_utc"], item["id"]), reverse=True)
    value = {"schema": CARDS_SCHEMA, "authority": "none", "as_of_utc": args.as_of,
             "source_sha256": hashlib.sha256("\n".join(source).encode("utf-8")).hexdigest(),
             "counts": dict(skipped, cards=len(cards)), "cards": cards}
    ws.guard_object(value)
    with open(args.out, "xb") as handle:
        handle.write((json.dumps(value, indent=1, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii"))
    print(json.dumps({"out": str(args.out), "counts": value["counts"],
                      "sha256": hashlib.sha256(args.out.read_bytes()).hexdigest()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
