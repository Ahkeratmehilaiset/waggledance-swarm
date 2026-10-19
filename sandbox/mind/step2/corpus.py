"""HEX MEMORY STEP 2, corpus: the frozen records one hex memory build is made from, each with its provenance.

usage: python -B sandbox/mind/step2/corpus.py --lessons <dir> --capture <capture.json> [--capture ...]
           --cutoff 2026-10-14T00:00:00Z --out <dir>

Every record is strictly older than the cutoff, so a situation at or after it cannot leak into the memory:

* lesson: one record per shared memory note (``*.md`` except ``MEMORY.md``) whose FILE MTIME is before the cutoff.
  The frontmatter ``modified`` field is not used, because a shell append does not update it (fable-5, 2026-10-14).
  A note in ``STRIP_AFTER_CUTOFF`` with a later mtime keeps only its text before a known post-cutoff block; the
  removed block is hashed, and the kept text must not mention the cutoff date.
* event: one record per distinct bridge event in the frozen captures, with ``ts_utc`` before the cutoff and the
  skip rules of ``hexmind/hex_memory.py`` (no claim/release/heartbeat/wake bookkeeping, no empty message),
  deduplicated by its Compact event id (sha256 of the canonical JSON).

Provenance per record: the source (note file name, or capture path and index), the sha256 of the source bytes, the
sha256 of the bytes actually used, the transform if any, and for events the Compact event id and the
``ts_utc|agent|type|task_id`` ref.

Text per record: ``text`` (the first 4000 characters, what is embedded and searched), ``summary`` (a second, short
view: a lesson's own frontmatter ``description``, an event's first 600 characters) and ``stored_text`` (``text``, or
its longest whole-line prefix that passes the decoded privacy guard of the workspace; such a cut is flagged). Every
stored field passes that guard; a refused record is counted, never stored. Writes only a NEW ``--out`` directory,
whole or not at all (``publish_new``; an existing one is refused before any input is read). No network, no bridge
event, no gate code.
"""

import argparse
import datetime as dt
import errno
import hashlib
import json
import os
import re
import secrets
import sys
from collections import Counter
from pathlib import Path

SRC = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SRC))
from tools import bridge_mind_workspace as ws  # noqa: E402
from tools.bridge_compact_view import event_id  # noqa: E402

SCHEMA = "wd.bridge-mind-hex-corpus.v2"
SKIP_TYPES = {"claim", "release", "heartbeat", "wake", "wake_request"}
CONSTRAINT = re.compile(r"(?i)\b(finding|veto|changes_requested|blocked)\b")
FILE_DATE = re.compile(r"(20\d{6})")
DESCRIPTION = re.compile(r"^description:\s*(.+)$", re.M)
TEXT_CAP = 4000
SUMMARY_CAP = 600
# A note appended after the cutoff keeps its pre-cutoff text: name -> the exact start of the appended block.
STRIP_AFTER_CUTOFF = {
    "feedback_producer_sweep_siblings_after_closing_a_class_20260826.md": "\n\n**Recurrence as a producer, 2026-10-14",
}


class CorpusError(ValueError):
    """A corpus input that cannot be used as given."""


def parse_utc(text):
    try:
        value = dt.datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return value.astimezone(dt.timezone.utc) if value.tzinfo is not None else None


def utc_text(value):
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def guarded(value):
    try:
        ws.guard_object(value)
    except ws.WorkspaceError:
        return False
    return True


def guarded_prefix(text):
    """The longest whole-line prefix of ``text`` that passes the privacy guard (the stored text of a record)."""
    if guarded({"text": text}):
        return text, False
    lines = text.split("\n")
    low, high = 0, len(lines)
    while low < high:
        middle = (low + high + 1) // 2
        if guarded({"text": "\n".join(lines[:middle])}):
            low = middle
        else:
            high = middle - 1
    return "\n".join(lines[:low]), True


def summary_of(record_kind, text):
    """A short second view of a record: a lesson's own frontmatter description, an event's first 600 characters."""
    if record_kind == "lesson":
        match = DESCRIPTION.search(text[:3000])
        if match:
            return match.group(1).strip()[:SUMMARY_CAP]
    return text[:SUMMARY_CAP]


def lesson_time(name, text, mtime):
    """The time a note claims for itself: the date in its file name, else its frontmatter modified, else mtime."""
    stamp = FILE_DATE.search(Path(name).stem)
    if stamp:
        try:
            return dt.datetime.strptime(stamp.group(1), "%Y%m%d").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            pass
    match = re.search(r"^\s*modified:\s*(\S+)", text[:2000], re.M)
    when = parse_utc(match.group(1)) if match else None
    return when or mtime


def lesson_records(directory, cutoff):
    """Yield (record, None) for each usable note and (None, (name, reason)) for each excluded one."""
    cutoff_day = cutoff.strftime("%Y-%m-%d")
    for path in sorted(directory.glob("*.md")):
        if path.name == "MEMORY.md":
            continue
        source = path.read_bytes()
        mtime = dt.datetime.fromtimestamp(os.stat(path).st_mtime, dt.timezone.utc)
        used, transform = source, None
        if mtime >= cutoff:
            marker = STRIP_AFTER_CUTOFF.get(path.name)
            text = source.decode("utf-8", "replace")
            if marker is None:
                yield None, (path.name, "mtime_at_or_after_cutoff")
                continue
            if text.count(marker) != 1:
                yield None, (path.name, "strip_marker_not_found_once")
                continue
            kept = text[:text.index(marker)] + "\n"
            if cutoff_day in kept:
                yield None, (path.name, "stripped_text_mentions_cutoff_day")
                continue
            used = kept.encode("utf-8")
            if not source.startswith(used):
                yield None, (path.name, "stripped_text_not_a_byte_prefix")
                continue
            transform = {"kind": "strip_after_cutoff", "removed_sha256": sha256(source[len(used):]),
                         "removed_bytes": len(source) - len(used), "marker": marker.strip()}
        text = used.decode("utf-8", "replace")
        used_sha = sha256(used)
        record = {
            "record_id": f"lesson:{path.stem}@{used_sha[:12]}",
            "kind": "lesson",
            "intent": "lesson",
            "time_utc": utc_text(lesson_time(path.name, text, mtime)),
            "text": text[:TEXT_CAP],
            "summary": summary_of("lesson", text),
            "meta": {"kind": "lesson", "file": path.name},
            "provenance": {"source_kind": "memory_note", "file": path.name, "mtime_utc": utc_text(mtime),
                           "source_sha256": sha256(source), "used_sha256": used_sha, "transform": transform},
        }
        yield record, None


def event_records(captures, cutoff):
    """Yield one record per distinct event before the cutoff, first capture wins; also count every occurrence."""
    first, seen_in = {}, Counter()
    for capture_path, capture_sha, capture in captures:
        for index, event in enumerate(capture.get("events") or []):
            if not isinstance(event, dict) or event.get("type") in SKIP_TYPES or not event.get("message"):
                continue
            when = parse_utc(event.get("ts_utc"))
            if when is None or when >= cutoff:
                continue
            compact = event_id(event)
            seen_in[compact] += 1
            if compact in first:
                continue
            head = f"{event.get('type')} {event.get('status') or ''} {event.get('agent')} {event.get('task_id') or ''}"
            text = (head + ": " + str(event["message"]))[:TEXT_CAP]
            constraint = event.get("type") == "finding" or CONSTRAINT.search(str(event.get("status") or ""))
            ref = "|".join(str(event.get(key) or "") for key in ("ts_utc", "agent", "type", "task_id"))
            first[compact] = {
                "record_id": f"event:{compact[:16]}",
                "kind": "event",
                "intent": "constraint" if constraint else "chat",
                "time_utc": utc_text(when),
                "text": text,
                "summary": summary_of("event", text),
                "meta": {"kind": "event", "ts_utc": event.get("ts_utc"), "agent": event.get("agent"),
                         "type": event.get("type"), "task_id": event.get("task_id")},
                "provenance": {"source_kind": "bridge_event_capture", "capture": str(capture_path),
                               "capture_sha256": capture_sha, "index": index, "compact_event_id": compact,
                               "event_ref": ref},
            }
    for compact, record in first.items():
        record["provenance"]["captures_seen"] = seen_in[compact]
        yield record


def load_captures(paths):
    captures = []
    for path in paths:
        data = path.read_bytes()
        capture = json.loads(data)
        if not isinstance(capture, dict) or not isinstance(capture.get("events"), list):
            raise CorpusError("capture_without_events_list")
        captures.append((path, sha256(data), capture, capture.get("observed_utc")))
    captures.sort(key=lambda row: (str(row[3]), str(row[0])))
    return [(path, digest, capture) for path, digest, capture, _ in captures]


def publish_new(out_dir, files, error):
    """D21 (RCO1's R5 class, write-new): publish ``files`` ({name: bytes}, written in that order) as the NEW directory
    ``out_dir``, whole, or nothing at that path; corpus.build and encode.build both publish with it. An existing
    ``out_dir`` raises ``error("output_exists")`` and is never replaced or written into; any other OS failure raises
    ``error("output_unwritten")``. Each file is created exclusively in a fresh sibling staging directory
    ``.<name>.staging-<16 random hex>``, written, flushed, synced, closed and read back, and only then is the staging
    directory renamed onto ``out_dir``. That rename refuses an existing file, link or directory on Windows and an
    existing file, link or non-empty directory on POSIX, and a failed rename while the name is taken is
    ``output_exists``; on POSIX it replaces an EMPTY directory made after the check (a stated limit), and the
    directories themselves are not synced. After a failure the staging directory this call made is removed, best
    effort; one already at the staging name fails its create and is never used or removed, and a hard crash leaves its
    staging directory behind, never published and never touched by a later build. Not defended: another process of
    the same user that changes the staging directory or a parent directory while this call runs."""
    out_dir = Path(out_dir)
    if os.path.lexists(out_dir):
        raise error("output_exists")
    staging = out_dir.parent / f".{out_dir.name}.staging-{secrets.token_hex(8)}"
    try:
        out_dir.parent.mkdir(parents=True, exist_ok=True)
        staging.mkdir()
    except OSError:
        raise error("output_unwritten") from None
    reason = "output_unwritten"
    try:
        for name, data in files.items():
            with (staging / name).open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if (staging / name).read_bytes() != data:  # a short or altered write is never published
                raise OSError(errno.EIO, "read-back differs")
        try:
            os.rename(staging, out_dir)
        except OSError:
            if os.path.lexists(out_dir):  # D21.1: the name is taken (Windows FileExistsError, POSIX ENOTEMPTY,
                reason = "output_exists"  # EEXIST or ENOTDIR for a file or link): another writer's output, kept
            raise
    except OSError:
        for name in files:
            try:
                (staging / name).unlink()
            except OSError:
                pass
        try:
            staging.rmdir()
        except OSError:
            pass
        raise error(reason) from None


def build(lessons_dir, capture_paths, cutoff, out_dir):
    if cutoff is None:
        raise CorpusError("cutoff_required")
    if os.path.lexists(out_dir):
        raise CorpusError("output_exists")  # before any input is read: a previous build is never replaced
    captures = load_captures(capture_paths)
    records, excluded, refused = [], [], Counter()
    for record, skip in lesson_records(lessons_dir, cutoff):
        if skip is not None:
            excluded.append({"file": skip[0], "reason": skip[1]})
        else:
            records.append(record)
    records.extend(event_records(captures, cutoff))
    kept = []
    for record in records:
        record["stored_text"], cut = guarded_prefix(record["text"])
        record["provenance"]["stored_text_cut_by_guard"] = cut
        visible = {key: record[key] for key in ("record_id", "stored_text", "summary", "meta", "provenance")}
        if guarded(visible) and record["stored_text"]:
            kept.append(record)
        else:
            refused[record["kind"]] += 1
    kept.sort(key=lambda record: record["record_id"])
    if len({record["record_id"] for record in kept}) != len(kept):
        raise CorpusError("duplicate_record_id")
    body = "".join(canonical(record) + "\n" for record in kept).encode("utf-8")
    manifest = {
        "schema": SCHEMA, "authority": "none", "scope": "shadow_memory_not_gate",
        "cutoff_utc": utc_text(cutoff), "lessons_dir": str(lessons_dir),
        "lesson_records": sum(1 for r in kept if r["kind"] == "lesson"),
        "event_records": sum(1 for r in kept if r["kind"] == "event"),
        "records": len(kept), "excluded_lessons": excluded,
        "stripped_lessons": [{"file": r["provenance"]["file"], **r["provenance"]["transform"],
                              "used_sha256": r["provenance"]["used_sha256"]}
                             for r in kept if r["kind"] == "lesson" and r["provenance"]["transform"]],
        "captures": [{"path": str(path), "sha256": digest, "events": len(capture.get("events") or []),
                      "observed_utc": capture.get("observed_utc")} for path, digest, capture in captures],
        "refused_by_privacy_guard": dict(refused),
        "stored_text_cut_by_guard": sorted(r["record_id"] for r in kept if r["provenance"]["stored_text_cut_by_guard"]),
        "record_ids_sha256": sha256("\n".join(r["record_id"] for r in kept).encode("utf-8")),
        "corpus_sha256": sha256(body),
    }
    ws.guard_object(manifest)
    text = json.dumps(manifest, indent=1, sort_keys=True)
    publish_new(out_dir, {"corpus.jsonl": body, "corpus-manifest.json": text.encode("utf-8")}, CorpusError)
    return manifest


def read_corpus(out_dir):
    """The records of a built corpus, after checking the file against its manifest."""
    manifest = json.loads((out_dir / "corpus-manifest.json").read_text(encoding="utf-8"))
    body = (out_dir / "corpus.jsonl").read_bytes()
    if sha256(body) != manifest["corpus_sha256"]:
        raise CorpusError("corpus_sha256_mismatch")
    records = [json.loads(line) for line in body.decode("utf-8").splitlines() if line]
    if len(records) != manifest["records"]:
        raise CorpusError("corpus_count_mismatch")
    return manifest, records


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lessons", type=Path, required=True)
    parser.add_argument("--capture", type=Path, action="append", required=True)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = build(args.lessons, args.capture, parse_utc(args.cutoff), args.out)
    print(json.dumps({key: manifest[key] for key in ("records", "lesson_records", "event_records",
                                                     "refused_by_privacy_guard", "corpus_sha256")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
