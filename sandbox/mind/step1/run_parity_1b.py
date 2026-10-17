"""Hex-Mind step 1b acceptance run: the adapter over frozen captures, written to parity.json.

usage: python -B sandbox/mind/step1/run_parity_1b.py freeze [--source <live cycles dir>] [--loop <live-loop.jsonl>]
       python -B sandbox/mind/step1/run_parity_1b.py run

``freeze`` copies the consecutive live captures (every cycle dir that still has its capture.json) and the old
producer's per-cycle log into ``<data>/frozen-live/`` with a manifest of sha256; the originals are only read.
``run`` verifies both manifests, then uses only the P1 frozen cycles and those copies:

A. parity: the 22 captures in order (2 P1, 20 live), the checkpoint reloaded from disk before every step; after each
   step the reloaded state and its snapshot equal (canonical sha256) a full_rebuild over the union input as of the
   same observation time. The union is built independently (new refs in window order).
B. identity stability: the same window observed again (capture bytes and time differ) and two real consecutive
   captures; the old shadow identity ("capture-window:" + capture sha256) and the old delta lines for contrast.
C. restart: a hard crash (child process, os._exit) between the temp file and the replace of the core step file and
   of adapter.json, then a restart; duplicate and stale delivery after the last step.
D. negative controls: a gap, a hole in the overlap, a changed declared identity, corrupted checkpoints.
E. reader cursor mode: the same rows written to a sandbox log and read with the real compact reader (tail origin,
   then --after deltas with heartbeats in between); parity, duplicate, gap, overlap, generation change, rotation.
F. fold time and bytes written per cycle against the old core path and the old producer's own log.

Writes only under <data> (F:/waggledance_memory/sandbox/fable-5/hexmind/step1/adapter). Outputs carry refs,
hashes, counts, times and sizes, never event text. No bridge read or write, no gate code, no network.
"""

import argparse
import contextlib
import datetime as dt
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import adapter  # noqa: E402
from tools import wd_bridge_mind as core  # noqa: E402
from tools import wd_bridge_mind_delta_push as delta_push  # noqa: E402
from tools.bridge_compact_view import event_id  # noqa: E402
from waggledance.core.bridge_log_reader import parse_bridge_json_object  # noqa: E402

DATA = Path("F:/waggledance_memory/sandbox/fable-5/hexmind/step1/adapter")
LIVE = Path("F:/waggledance_memory/sandbox/fable-5/tournament/.codex-audit/live/cycles")
LOOP = Path("F:/waggledance_memory/sandbox/fable-5/tournament/live-loop.jsonl")
P1 = Path("F:/waggledance_memory/sandbox/fable-5/hexmind/frozen/p1-cycles-20261014")
P1_MANIFEST_PREFIX = "58c27e0b"  # as recorded in the fable-5 notes; the full digest is computed here
CYCLE_FILES = ("capture.json", "capture-record.json", "detail-manifest.txt", "run/reflected.publication.json",
               "run/receipt.json", "run/board.txt", "run/delta.jsonl")
HEARTBEAT = {"agent": "fable-5", "type": "heartbeat", "task_id": "fable-5/step1b-synthetic-log", "status": "alive",
             "message": "", "payload": {}}

# A hard crash at one replace: the child dies inside os.replace, so its temp file stays and nothing is cleaned up.
CRASH_CHILD = r"""
import json, os, sys
from pathlib import Path
sys.path.insert(0, {step1!r})
import adapter
real = os.replace
def crash(src, dst, *args, **kwargs):
    if Path(dst).name.startswith({target!r}):
        os._exit(77)
    return real(src, dst, *args, **kwargs)
os.replace = crash
page = json.loads(Path({page!r}).read_text(encoding="utf-8"))
adapter.step(Path({directory!r}), Path({audit_root!r}), page)
os._exit(0)
"""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def log(*parts):
    print(time.strftime("%H:%M:%S"), *parts, file=sys.stderr, flush=True)


def utc_now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def quantiles(values):
    values = sorted(values)
    if not values:
        return None
    pick = lambda q: values[min(len(values) - 1, int(round(q * (len(values) - 1))))]  # noqa: E731
    return {"n": len(values), "p50": pick(0.5), "p95": pick(0.95), "max": values[-1],
            "mean": round(statistics.fmean(values), 3)}


# -- freeze ----------------------------------------------------------------------------------------------------------

def copy_exclusive(source, target):
    """Copy bytes into a new file and prove the copy; the source is only read."""
    data = source.read_bytes()
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "xb") as handle:
        handle.write(data)
    if sha(target.read_bytes()) != sha(data):
        raise RuntimeError("copy_mismatch")
    return {"sha256": sha(data), "bytes": len(data)}


def freeze(source, loop, dest):
    if dest.exists():
        raise SystemExit("frozen-live already exists; verify it instead of copying again")
    cycles = sorted(path.name for path in source.iterdir() if (path / "capture.json").is_file())
    manifest = {"schema": "wd.hexmind-step1b-frozen-live.v1", "authority": "none", "copied_utc": utc_now(),
                "source": source.as_posix(), "cycles": []}
    for name in cycles:
        files = {}
        for relative in CYCLE_FILES:
            src = source / name / relative
            files[relative] = copy_exclusive(src, dest / "cycles" / name / relative) if src.is_file() else None
        manifest["cycles"].append({"cycle": name, "files": files})
    manifest["live_loop"] = copy_exclusive(loop, dest / "live-loop.jsonl")
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="ascii")
    return {"cycles": len(cycles), "first": cycles[0], "last": cycles[-1],
            "manifest_sha256": sha((dest / "manifest.json").read_bytes())}


# -- shared helpers (also used by test_adapter.py) ---------------------------------------------------------------------

def reader_view(log, *, tail, after=""):
    """The pinned compact reader's own list output over a sandbox log (Read-AgentBridge.ps1 -Compact runs this same
    main), or its fixed refusal reason."""
    from tools import bridge_compact_view as compact
    argv = ["--events", str(log), "--tail", str(tail)] + (["--after", after] if after else [])
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = compact.main(argv)
    if code != 0:
        return None, json.loads(err.getvalue()).get("reason", "refused")
    return json.loads(out.getvalue()), None


def append_rows(log, rows):
    with open(log, "ab") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
        handle.flush()
        os.fsync(handle.fileno())


def digests(state, as_of):
    return {"state_sha256": sha(core.state_bytes(state)),
            "snapshot_sha256": sha(core.canonical_bytes(core.snapshot(state, as_of_utc=as_of)))}


def full_rebuild(rows, identity, as_of):
    envelopes = [{"ref": event_id(row), "event": row} for row in rows]
    return core.full_rebuild(envelopes, source=adapter._source(identity, 0, len(envelopes)), as_of_utc=as_of)


def tree_bytes(directory):
    return {path.name: sha(path.read_bytes()) for path in sorted(Path(directory).iterdir())}


def crash_child(page, target, directory, audit_root, scratch):
    """Run one step in a child that dies inside the replace of ``target``; its exit code (77 = died there)."""
    page_file = scratch / ("page-%s.json" % sha(page["input_sha256"].encode())[:12])
    page_file.write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
    code = CRASH_CHILD.format(step1=str(HERE), target=target, page=str(page_file), directory=str(directory),
                              audit_root=str(audit_root))
    done = subprocess.run([sys.executable, "-B", "-I", "-c", code], capture_output=True, timeout=600)
    page_file.unlink()
    return done.returncode


def summary(receipt):
    keep = ("action", "reason", "coverage", "proof", "step", "rows_folded", "duplicates_folded", "core_cursor",
            "reader_offset", "page_sha256", "input_sha256", "core_checkpoint_sha256", "written", "ms")
    return {key: receipt[key] for key in keep}


# -- inputs ------------------------------------------------------------------------------------------------------------

def verify_p1():
    data = (P1 / "manifest.json").read_bytes()
    manifest = json.loads(data)
    verified, absent, mismatches, captures = 0, 0, [], []
    for cycle in manifest["cycles"]:
        for name, expected in cycle["files"].items():
            path = P1 / cycle["cycle"] / name.replace("\\", "/")
            if expected is None:
                absent += not path.exists()
                mismatches += [cycle["cycle"] + "/" + name] if path.exists() else []
            elif sha(path.read_bytes()) == expected:
                verified += 1
            else:
                mismatches.append(cycle["cycle"] + "/" + name)
        if cycle["files"].get("capture.json"):
            captures.append(cycle["cycle"])
    return {"path": P1.as_posix(), "manifest_sha256": sha(data),
            "matches_recorded_prefix": sha(data).startswith(P1_MANIFEST_PREFIX), "cycles": len(manifest["cycles"]),
            "files_verified": verified, "absent_as_recorded": absent, "mismatches": mismatches,
            "cycles_with_capture": captures,
            "note": "capture-record.json is not in this manifest; it is bound by its own files.capture.json digest to "
                    "the manifest-verified capture"}


def verify_frozen_live(root):
    data = (root / "manifest.json").read_bytes()
    manifest = json.loads(data)
    verified, mismatches = 0, []
    for cycle in manifest["cycles"]:
        for name, expected in cycle["files"].items():
            path = root / "cycles" / cycle["cycle"] / name
            if expected is None:
                continue
            if sha(path.read_bytes()) == expected["sha256"]:
                verified += 1
            else:
                mismatches.append(cycle["cycle"] + "/" + name)
    loop_ok = sha((root / "live-loop.jsonl").read_bytes()) == manifest["live_loop"]["sha256"]
    return {"path": root.as_posix(), "manifest_sha256": sha(data), "copied_utc": manifest["copied_utc"],
            "source": manifest["source"], "cycles": [c["cycle"] for c in manifest["cycles"]],
            "files_verified": verified + loop_ok, "mismatches": mismatches + ([] if loop_ok else ["live-loop.jsonl"])}, \
        manifest


def load_pages(dirs):
    pages, parse_ms, contents = [], [], []
    for directory in dirs:
        started = time.perf_counter()
        capture, record = (directory / "capture.json").read_bytes(), (directory / "capture-record.json").read_bytes()
        pages.append(adapter.capture_page(capture, record))
        parse_ms.append(round((time.perf_counter() - started) * 1000, 3))
        top = json.loads(capture.decode("utf-8"))
        contents.append({"cycle": directory.name, "capture_keys": sorted(top), "record_keys": sorted(json.loads(record)),
                         "record_bridge_keys": sorted(json.loads(record)["bridge"]),
                         "event_keys": sorted({key for row in top["events"] for key in row}),
                         "events": len(top["events"]), "capture_sha256": sha(capture),
                         "observed_utc": top["observed_utc"]})
    return pages, parse_ms, contents


def what_captures_carry(contents):
    reader_fields = ("cursor", "reader_cursor", "file_identity", "generation", "offset", "scope", "position",
                     "snapshot_length", "unconsumed_bytes", "bytes_read")
    found = sorted({field for c in contents for keys in (c["capture_keys"], c["record_keys"], c["record_bridge_keys"],
                                                         c["event_keys"]) for field in reader_fields if field in keys})
    return {"capture_json_keys": sorted({k for c in contents for k in c["capture_keys"]}),
            "capture_record_keys": sorted({k for c in contents for k in c["record_keys"]}),
            "capture_record_bridge_keys": sorted({k for c in contents for k in c["record_bridge_keys"]}),
            "event_row_keys": sorted({k for c in contents for k in c["event_keys"]}),
            "reader_cursor_fields_found": found,
            "why": "capture_round.bridge_capture keeps only view['events'] refs of the compact reader output and "
                   "drops its cursor, reader and scope; reader_stdout_sha256 is the sha256 of the detail manifest "
                   "(ref, row sha256, source), not of the reader's stdout",
            "identity_material_available": {"repo": "capture-record.json repo",
                                            "reader": "capture.json reader label (pinned compact reader)",
                                            "rows": "event rows with content refs (overlap anchor only)",
                                            "observation_time": "capture.json observed_utc (provenance only)",
                                            "capture_sha256": "provenance only, never identity"}}


# -- scenarios ---------------------------------------------------------------------------------------------------------

def scenario_parity(pages, audit_root, run_dir):
    directory = run_dir / "declared"
    union, seen, steps, new_by_step = [], set(), [], []
    for page in pages:
        receipt = adapter.step(directory, audit_root, page)
        new = [row for row in page["rows"] if event_id(row) not in seen]
        union += new
        seen |= {event_id(row) for row in new}
        new_by_step.append(new)
        started = time.perf_counter()
        state, record = adapter.load(directory, audit_root)
        reload_ms = round((time.perf_counter() - started) * 1000, 3)
        started = time.perf_counter()
        reference = full_rebuild(union, page["identity"], page["observed_utc"])
        full_ms = round((time.perf_counter() - started) * 1000, 3)
        mine, theirs = digests(state, page["observed_utc"]), digests(reference, page["observed_utc"])
        steps.append(dict(summary(receipt), observed_utc=page["observed_utc"], union_rows=len(union),
                          union_new_rows=len(new), incremental=mine, full_rebuild=theirs, parity=mine == theirs,
                          reload_ms=reload_ms, full_rebuild_ms=full_ms))
        log("A", len(steps), receipt["action"], receipt["rows_folded"], "parity", mine == theirs,
            "step_ms", receipt["ms"]["total"], "reload_ms", reload_ms, "full_ms", full_ms)
    final = steps[-1]
    return {"state_dir": directory.as_posix(), "steps": steps,
            "pass": all(s["parity"] for s in steps) and all(s["rows_folded"] == s["union_new_rows"] for s in steps)
            and not any(s["action"] in ("rebuild_required", "refused") for s in steps),
            "final": {"observed_utc": final["observed_utc"], "union_rows": final["union_rows"], **final["incremental"]},
            "pages_chain_sha256": record["pages"]["chain_sha256"], "anchor_last_ref": record["anchor"][-1]}, \
        new_by_step, union


def scenario_identity(pages, cycle_dirs, frozen_root, audit_root, run_dir):
    """Same window observed again; real consecutive captures; old identity and old delta lines for contrast."""
    first = cycle_dirs[2]  # the first live capture
    capture = json.loads((first / "capture.json").read_bytes().decode("utf-8"))
    record = json.loads((first / "capture-record.json").read_bytes().decode("utf-8"))
    later = (adapter._utc(capture["observed_utc"]) + dt.timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    capture["observed_utc"] = later
    again = json.dumps(capture, ensure_ascii=False).encode("utf-8")
    record["bridge"]["observed_utc"] = later
    record["files"]["capture.json"] = sha(again)
    reobserved = adapter.capture_page(again, json.dumps(record, indent=2, sort_keys=True).encode("ascii"))
    directory = run_dir / "identity"
    origin = adapter.step(directory, audit_root, pages[2])
    same = adapter.step(directory, audit_root, reobserved)
    grown = next(i for i in range(3, len(pages)) if _new_rows(pages[2], pages[i]))  # the first later window with rows
    onward = adapter.step(directory, audit_root, pages[grown])
    real_pairs = []
    for a, b, da, db in zip(pages[2:], pages[3:], cycle_dirs[2:], cycle_dirs[3:]):
        if [event_id(r) for r in a["rows"]] == [event_id(r) for r in b["rows"]]:  # content refs: rows byte-equal
            top_a, top_b = (json.loads((d / "capture.json").read_bytes().decode("utf-8")) for d in (da, db))
            other = sorted(k for k in set(top_a) | set(top_b) if k != "observed_utc" and top_a.get(k) != top_b.get(k))
            real_pairs.append({"from": da.name, "to": db.name, "identical_rows": True,
                               "only_observed_utc_differs": not other, "other_differing_capture_keys": other,
                               "manifest_lines_differing_only_in_source_label": _source_label_flips(da, db),
                               "old_identity_equal": a["input_sha256"] == b["input_sha256"],
                               "new_identity_equal": a["identity"] == b["identity"]})
    old_lines = []
    for directory_ in cycle_dirs[2:]:
        delta = directory_ / "run" / "delta.jsonl"
        for line in delta.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            if item.get("type") == "rebuild_required":
                old_lines.append(item.get("reason"))
    publications = [parse_bridge_json_object((d / "run" / "reflected.publication.json").read_text(encoding="utf-8"))
                    for d in cycle_dirs[2:]]
    recomputed = []
    for previous, current in zip(publications, publications[1:]):
        for pub in (previous, current):
            if sha(core.canonical_bytes(pub["snapshot"])) != pub["snapshot_sha256"]:
                raise RuntimeError("old publication digest mismatch")
        change = delta_push.delta(previous["snapshot"], current["snapshot"])
        recomputed.append(change[1]["reason"] if len(change) > 1 and change[1]["type"] == "rebuild_required" else
                          "typed_delta")
    old_ids = {p["snapshot"]["source"]["log_identity"].split(":", 1)[0] for p in publications}
    return {"same_window_reobserved": {"capture_sha256_before": pages[2]["input_sha256"],
                                       "capture_sha256_after": reobserved["input_sha256"],
                                       "observed_utc_before": pages[2]["observed_utc"], "observed_utc_after": later,
                                       "old_identity_changes": pages[2]["input_sha256"] != reobserved["input_sha256"],
                                       "new_identity": reobserved["identity"],
                                       "new_identity_equal": pages[2]["identity"] == reobserved["identity"],
                                       "origin": summary(origin), "second": summary(same),
                                       "next_window_with_new_rows": dict(summary(onward),
                                                                         cycle=cycle_dirs[grown].name)},
            "real_consecutive_identical_windows": real_pairs,
            "old_producer_delta_lines": {"cycles": len(cycle_dirs) - 2, "rebuild_required": len(old_lines),
                                         "reasons": sorted(set(old_lines))},
            "old_snapshots_delta_push_recomputed": {"pairs": len(recomputed),
                                                    "rebuild_required": sum(r != "typed_delta" for r in recomputed),
                                                    "reasons": sorted(set(recomputed)),
                                                    "old_log_identity_kind": sorted(old_ids)},
            "pass": (same["action"] == "no_change" and same["coverage"] == "contiguous"
                     and onward["action"] == "advance" and onward["rows_folded"] > 0
                     and pages[2]["identity"] == reobserved["identity"]
                     and any(p["only_observed_utc_differs"] for p in real_pairs)
                     and all(p["new_identity_equal"] and not p["old_identity_equal"] for p in real_pairs))}


def scenario_restart(pages, audit_root, run_dir, clean_final):
    scratch = run_dir / "scratch"
    scratch.mkdir()
    final_time = pages[-1]["observed_utc"]
    cases = []
    # Crash only where the step really commits: the 75-minute jump (P1 to live) and the last minute with new rows.
    later = max(i for i in range(3, len(pages)) if _new_rows(pages[i - 1], pages[i]))
    for target, index in (("core-", 2), (adapter.POINTER, 2), ("core-", later), (adapter.POINTER, later)):
        directory = run_dir / ("crash-%s-%02d" % (target.strip("-.").replace(".", "_"), index))
        for page in pages[:index]:
            adapter.step(directory, audit_root, page)
        before = tree_bytes(directory)
        code = crash_child(pages[index], target, directory, audit_root, scratch)
        after = tree_bytes(directory)
        orphans = sorted(name for name in after if name not in before)
        pointer_kept = after[adapter.POINTER] == before[adapter.POINTER]
        for page in pages[index:]:
            adapter.step(directory, audit_root, page)
        final = digests(adapter.load(directory, audit_root)[0], final_time)
        cases.append({"crash_inside_replace_of": target, "at_step": index, "child_exit": code,
                      "pointer_unchanged_after_crash": pointer_kept,
                      "orphans_left_by_crash": [("temp" if n.startswith(".mind-") else "step_file") for n in orphans],
                      "orphans_after_restart": sorted(n for n in tree_bytes(directory) if n.startswith(".mind-")),
                      "final": final, "same_final_as_clean_run": final == clean_final})
    directory = run_dir / "declared"
    files = tree_bytes(directory)
    duplicate = adapter.step(directory, audit_root, pages[-1])
    stale = adapter.step(directory, audit_root, pages[5])
    unchanged = tree_bytes(directory) == files
    return {"crash_cases": cases, "reload_between_every_step": "scenario A reloads before each step",
            "duplicate_last_page": summary(duplicate), "stale_older_page": summary(stale),
            "files_unchanged_by_duplicate_and_stale": unchanged,
            "pass": all(c["child_exit"] == 77 and c["pointer_unchanged_after_crash"] and c["same_final_as_clean_run"]
                        and not c["orphans_after_restart"] for c in cases)
            and duplicate["action"] == "no_change" and stale["action"] == "already_applied" and unchanged}


def scenario_negative(pages, audit_root, run_dir, frozen_root, cycle_dirs):
    cases = []
    gap_at = next(i for i in range(3, len(pages)) if _new_rows(pages[i - 1], pages[i]) >= 2)  # two new rows: one is lost
    base = run_dir / "neg-base"
    for page in pages[:gap_at]:
        adapter.step(base, audit_root, page)
    record = adapter.load(base, audit_root)[1]
    rows = pages[gap_at]["rows"]
    anchor_at = next(i for i in range(len(rows) - 1, -1, -1) if event_id(rows[i]) == record["anchor"][-1])
    variants = {
        "gap_window_starts_after_a_missing_row": dict(pages[gap_at], rows=rows[anchor_at + 2:]),
        "hole_inside_the_overlap": dict(pages[gap_at], rows=rows[:anchor_at - 3] + rows[anchor_at - 2:]),
        "older_window_after_newer_checkpoint": pages[2],  # predates the rows accepted since, all of its refs seen
    }
    capture = (cycle_dirs[gap_at] / "capture.json").read_bytes()
    other = json.loads((cycle_dirs[gap_at] / "capture-record.json").read_text(encoding="ascii"))
    other["repo"] = "someone-else/another-swarm"
    variants["declared_identity_changed"] = adapter.capture_page(capture, json.dumps(other).encode("ascii"))
    expected = {"gap_window_starts_after_a_missing_row": ("rebuild_required", "window_does_not_cover_checkpoint"),
                "hole_inside_the_overlap": ("rebuild_required", "window_overlap_mismatch"),
                "older_window_after_newer_checkpoint": ("already_applied", "window_refs_all_seen"),
                "declared_identity_changed": ("rebuild_required", "log_identity_changed")}
    for name, page in variants.items():
        files = tree_bytes(base)
        receipt = adapter.step(base, audit_root, page)
        cases.append({"case": name, "at_step": gap_at, "action": receipt["action"], "reason": receipt["reason"],
                      "coverage": receipt["coverage"], "checkpoint_unchanged": tree_bytes(base) == files,
                      "pass": (receipt["action"], receipt["reason"]) == expected[name] and tree_bytes(base) == files})
    final = run_dir / "declared"
    pointer = json.loads((final / adapter.POINTER).read_text(encoding="utf-8"))
    step_file = pointer["core_checkpoint"]["file"]

    def flip_core(d):
        data = bytearray((d / step_file).read_bytes())
        index = data.index(b'"duplicates":') + len(b'"duplicates":')
        data[index] = ord("7") if data[index] != ord("7") else ord("8")
        (d / step_file).write_bytes(bytes(data))

    def flip_pointer(d):
        data, anchor = (d / adapter.POINTER).read_bytes(), pointer["anchor"][-1].encode("ascii")
        (d / adapter.POINTER).write_bytes(data.replace(anchor, anchor[::-1], 1))  # one field, bytes exact

    def truncate_pointer(d):
        data = (d / adapter.POINTER).read_bytes()
        (d / adapter.POINTER).write_bytes(data[:len(data) // 2])

    def drop_core(d):
        (d / step_file).unlink()

    def swap_core(d):
        older = [p for p in sorted(d.glob("core-*.json")) if p.name != step_file]
        (d / step_file).write_bytes(older[-1].read_bytes())

    def rebind(d):
        changed = dict(pointer, anchor=pointer["anchor"][:-1] + ["0" * 64])
        changed["record_sha256"] = adapter._record_digest(changed)
        (d / adapter.POINTER).write_bytes(core.canonical_bytes(changed))

    def code_changed(d):
        changed = dict(pointer, code=dict(pointer["code"], adapter_version="1b.0"))
        changed["record_sha256"] = adapter._record_digest(changed)
        (d / adapter.POINTER).write_bytes(core.canonical_bytes(changed))

    corruptions = [("core_step_file_byte_flipped", flip_core, "checkpoint_core_mismatch"),
                   ("pointer_byte_flipped", flip_pointer, "checkpoint_digest_mismatch"),
                   ("pointer_truncated", truncate_pointer, "checkpoint_pointer_unreadable"),
                   ("core_step_file_missing", drop_core, "checkpoint_core_missing"),
                   ("core_step_file_swapped_for_previous", swap_core, "checkpoint_core_mismatch"),
                   ("pointer_rebound_with_fresh_digest", rebind, "checkpoint_binding_mismatch"),
                   ("code_version_changed", code_changed, "checkpoint_code_changed")]
    for name, corrupt, reason in corruptions:
        copy = run_dir / ("neg-" + name)
        shutil.copytree(final, copy)
        if name == "core_step_file_swapped_for_previous" and len(list(copy.glob("core-*.json"))) < 2:
            continue
        corrupt(copy)
        try:
            adapter.load(copy, audit_root)
            refused = None
        except adapter.AdapterError as exc:
            refused = str(exc)
        files = tree_bytes(copy)
        receipt = adapter.step(copy, audit_root, pages[-1])
        cases.append({"case": "corrupted_checkpoint:" + name, "load_refused": refused, "action": receipt["action"],
                      "reason": receipt["reason"], "nothing_written": tree_bytes(copy) == files,
                      "pass": refused == reason and receipt["action"] == "rebuild_required"
                      and receipt["reason"] == "checkpoint_refused:" + reason and tree_bytes(copy) == files})
    return {"cases": cases, "pass": all(c["pass"] for c in cases)}


def _source_label_flips(a, b):
    """Detail-manifest lines (ref, row sha256, source) that differ only in the source label (seed or detail);
    None when any line differs in more than that."""
    lines_a, lines_b = ((d / "detail-manifest.txt").read_bytes().splitlines() for d in (a, b))
    if len(lines_a) != len(lines_b):
        return None
    flips = [x.split()[:2] == y.split()[:2] for x, y in zip(lines_a, lines_b) if x != y]
    return len(flips) if all(flips) else None


def _new_rows(previous, current):
    seen = {event_id(row) for row in previous["rows"]}
    return sum(1 for row in current["rows"] if event_id(row) not in seen)


def scenario_cursor(pages, new_by_step, union, audit_root, run_dir, declared_final_snapshot):
    """The same rows through the real compact reader: a sandbox log grows by each step's new rows plus heartbeats."""
    log = run_dir / "synthetic-log" / "events.jsonl"
    log.parent.mkdir()
    log.with_name("events.generation.json").write_text('{"generation":"g1"}', encoding="ascii")
    log.write_bytes(b"")
    directory = run_dir / "cursor"
    by_ref = {event_id(row): row for row in union}
    steps, accepted = [], []

    def page_from(observed, after=""):
        view, refused = reader_view(log, tail=5000, after=after)
        if refused:
            return None, refused
        rows = [by_ref[item["ref"]] for item in view["events"]]
        return adapter.reader_page(view, rows, observed_utc=observed, after=after), None

    pages_seen = []
    for index, (page, new) in enumerate(zip(pages, new_by_step)):
        beat = dict(HEARTBEAT, ts_utc=page["observed_utc"])
        append_rows(log, new + ([beat] if index % 2 else []))
        accepted += new
        after = "" if index == 0 else adapter.load(directory, audit_root)[1]["reader_cursor"]["token"]
        started = time.perf_counter()
        current, refused = page_from(page["observed_utc"], after)
        read_ms = round((time.perf_counter() - started) * 1000, 3)
        receipt = adapter.step(directory, audit_root, current)
        pages_seen.append(current)
        state, record = adapter.load(directory, audit_root)
        mine = digests(state, page["observed_utc"])
        theirs = digests(full_rebuild(accepted, current["identity"], page["observed_utc"]), page["observed_utc"])
        steps.append(dict(summary(receipt), scope="delta" if after else "window", read_ms=read_ms,
                          reader_offset_is_log_size=record["reader_cursor"]["offset"] == log.stat().st_size,
                          parity=mine == theirs, incremental=mine))
    final_state = adapter.load(directory, audit_root)[0]
    content = core.snapshot(final_state, as_of_utc=pages[-1]["observed_utc"])
    same_content = {k: v for k, v in content.items() if k != "source"} == \
        {k: v for k, v in declared_final_snapshot.items() if k != "source"}
    negatives = []

    def expect(name, receipt, action, reason, files=None):
        ok = (receipt["action"], receipt["reason"]) == (action, reason)
        unchanged = None if files is None else tree_bytes(directory) == files
        negatives.append({"case": name, "action": receipt["action"], "reason": receipt["reason"],
                          "coverage": receipt["coverage"], "checkpoint_unchanged": unchanged,
                          "pass": ok and unchanged is not False})

    files = tree_bytes(directory)
    expect("duplicate_delta_page", adapter.step(directory, audit_root, pages_seen[-1]), "already_applied",
           "page_at_or_before_checkpoint", files)
    mark = adapter.load(directory, audit_root)[1]["reader_cursor"]["token"]
    append_rows(log, [dict(HEARTBEAT, ts_utc=pages[-1]["observed_utc"])])
    skipped, _ = page_from(pages[-1]["observed_utc"], mark)
    append_rows(log, [dict(HEARTBEAT, ts_utc=pages[-1]["observed_utc"])])
    after_skip, _ = page_from(pages[-1]["observed_utc"], skipped["token"])
    expect("delta_gap_page_skipped", adapter.step(directory, audit_root, after_skip), "rebuild_required", "cursor_gap",
           files)
    older = pages_seen[-2]["token"] if len(pages_seen) > 1 else pages_seen[0]["token"]
    overlap, _ = page_from(pages[-1]["observed_utc"], older)
    expect("delta_from_an_older_cursor", adapter.step(directory, audit_root, overlap), "refused",
           "page_overlaps_checkpoint", files)
    log.with_name("events.generation.json").write_text('{"generation":"g2"}', encoding="ascii")
    _, reader_refusal_generation = page_from(pages[-1]["observed_utc"], mark)
    fresh, _ = page_from(pages[-1]["observed_utc"])
    expect("generation_changed", adapter.step(directory, audit_root, fresh), "rebuild_required", "generation_changed",
           files)
    log.with_name("events.generation.json").write_text('{"generation":"g1"}', encoding="ascii")
    rotated = log.with_name("events.rotated")
    rotated.write_bytes(log.read_bytes())
    os.replace(rotated, log)
    _, reader_refusal_rotation = page_from(pages[-1]["observed_utc"], mark)
    fresh, _ = page_from(pages[-1]["observed_utc"])
    expect("file_rotated_new_file_identity", adapter.step(directory, audit_root, fresh), "rebuild_required",
           "file_identity_changed", files)
    expect("declared_capture_against_reader_checkpoint", adapter.step(directory, audit_root, pages[-1]),
           "rebuild_required", "identity_kind_changed", files)
    identity = adapter.load(directory, audit_root)[1]["identity"]
    return {"log": {"path": log.as_posix(), "bytes": log.stat().st_size,
                    "rows_written": len(union), "heartbeats_written": sum(1 for i in range(len(pages)) if i % 2) + 2},
            "identity": identity, "steps": steps,
            "content_equal_to_declared_mode_snapshot_except_source": same_content,
            "reader_refused_old_cursor": {"after_generation_change": reader_refusal_generation,
                                          "after_rotation": reader_refusal_rotation},
            "negatives": negatives,
            "pass": all(s["parity"] and s["reader_offset_is_log_size"] for s in steps) and same_content
            and all(n["pass"] for n in negatives) and steps[0]["action"] == "origin"
            and all(s["action"] in ("advance", "no_change") and s["proof"] in ("reader_cursor", None)
                    for s in steps[1:])}


def scenario_old_core(pages, cycle_dirs, audit_root, run_dir):
    """The old path per cycle on the same live captures: full_rebuild over the window under a capture-hash identity,
    snapshot, publication. (The old producer also ran the workspace, board and lenses; see the loop log.)"""
    out = run_dir / "old-core"
    out.mkdir()
    ms, written = [], []
    for page, directory in zip(pages[2:], cycle_dirs[2:]):
        started = time.perf_counter()
        envelopes = [{"ref": event_id(row), "event": row} for row in page["rows"]]
        source = {"log_identity": "capture-window:" + page["input_sha256"], "generation": None, "cursor_start": 0,
                  "cursor_end": len(envelopes), "event_count": len(envelopes), "coverage": "complete"}
        state = core.full_rebuild(envelopes, source=source, as_of_utc=page["observed_utc"])
        snapshot = core.snapshot(state, as_of_utc=page["observed_utc"])
        target = out / (directory.name + ".publication.json")
        core.write_snapshot(snapshot, target, audit_root=audit_root)
        ms.append(round((time.perf_counter() - started) * 1000, 3))
        written.append(target.stat().st_size)
    return {"ms": quantiles(ms), "bytes_per_cycle": quantiles(written)}


def old_producer(frozen_root, manifest):
    cycles = {c["cycle"]: c for c in manifest["cycles"]}
    rows = [json.loads(line) for line in (frozen_root / "live-loop.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    mine = [r for r in rows if r.get("status") == "ok" and r.get("cycle_dir") in cycles]
    run_bytes, capture_bytes = [], []
    for name, cycle in cycles.items():
        files = cycle["files"]
        run_bytes.append(sum(files[n]["bytes"] for n in ("run/reflected.publication.json", "run/board.txt",
                                                          "run/receipt.json", "run/delta.jsonl") if files.get(n)))
        capture_bytes.append(sum(files[n]["bytes"] for n in ("capture.json", "detail-manifest.txt") if files.get(n)))
    return {"cycles": len(mine), "cycle_ms": quantiles([r["cycle_ms"] for r in mine]),
            "run_ms": quantiles([r["stage_ms"]["run"] for r in mine]),
            "bridge_ms": quantiles([r["stage_ms"]["bridge"] for r in mine]),
            "run_output_bytes_per_cycle": quantiles(run_bytes),
            "capture_bytes_per_cycle": quantiles(capture_bytes),
            "note": "run_ms = the old shadow run (core full_rebuild + workspace + publication + board) every minute"}


def timing(parity, parse_ms, cursor):
    incremental = parity["steps"][1:]
    live = parity["steps"][3:]  # live-to-live minute steps

    def block(steps, parses, parse_name="capture_parse_ms"):
        return {"steps": len(steps), "actions": {a: sum(s["action"] == a for s in steps) for a in
                                                 sorted({s["action"] for s in steps})},
                "fold_ms_plan_plus_fold": quantiles([s["ms"]["plan"] + s["ms"].get("fold", 0) for s in steps]),
                "commit_ms": quantiles([s["ms"].get("commit", 0) for s in steps]),
                "load_ms_restart_path": quantiles([s["ms"]["load"] for s in steps]),
                "step_total_ms_with_reload": quantiles([s["ms"]["total"] for s in steps]),
                parse_name: quantiles(parses),
                "bytes_written_per_cycle": quantiles([s["written"]["core_bytes"] + s["written"]["pointer_bytes"]
                                                      for s in steps]),
                "cycles_writing_nothing": sum(1 for s in steps if not s["written"]["core_bytes"]
                                              and not s["written"]["pointer_bytes"])}

    origin = parity["steps"][0]
    deltas = cursor["steps"][1:]
    return {"origin_once": {"ms": origin["ms"], "written": origin["written"]},
            "all_incremental_steps": block(incremental, parse_ms[1:]),
            "live_minute_steps": block(live, parse_ms[3:]),
            "reader_cursor_delta_steps": dict(block(deltas, [s["read_ms"] for s in deltas], "reader_read_ms"),
                                              note="sandbox log, the reader reads only the bytes after the cursor; "
                                                   "a heartbeat moves the cursor, so the pointer alone is written")}


def run():
    stamp = utc_now().replace(":", "").replace("-", "")
    audit_root = DATA / ".codex-audit"
    audit_root.mkdir(parents=True, exist_ok=True)
    run_dir = audit_root / ("run-" + stamp)
    run_dir.mkdir()
    frozen_root = DATA / "frozen-live"
    p1 = verify_p1()
    live, manifest = verify_frozen_live(frozen_root)
    if p1["mismatches"] or not p1["matches_recorded_prefix"] or live["mismatches"]:
        raise SystemExit("input verification failed")
    cycle_dirs = [P1 / name for name in p1["cycles_with_capture"]] + \
        [frozen_root / "cycles" / name for name in live["cycles"]]
    log("inputs verified", p1["files_verified"], live["files_verified"])
    pages, parse_ms, contents = load_pages(cycle_dirs)
    log("pages", len(pages))
    parity, new_by_step, union = scenario_parity(pages, audit_root, run_dir)
    clean_final = {k: parity["final"][k] for k in ("state_sha256", "snapshot_sha256")}
    declared_state = adapter.load(run_dir / "declared", audit_root)[0]
    declared_snapshot = core.snapshot(declared_state, as_of_utc=pages[-1]["observed_utc"])
    result = {"schema": "wd.hexmind-step1b-parity.v1", "authority": "none", "scope": "sandbox_measurement_not_gate",
              "generated_utc": utc_now(), "run_dir": run_dir.as_posix(), "code": adapter.CODE,
              "inputs": {"p1_frozen": p1, "frozen_live": live,
                         "sequence": [{"cycle": c["cycle"], "capture_sha256": c["capture_sha256"],
                                       "events": c["events"], "observed_utc": c["observed_utc"]} for c in contents]},
              "captures_carry": what_captures_carry(contents),
              "identity_design": {
                  "reader_cursor": "identity bridge-log:<file_identity> + generation; progress = reader offset; "
                                   "delta pages must start at the checkpoint cursor",
                  "declared_no_reader_cursor": "identity declared-bridge-log:<repo> + generation not-captured; "
                                               "progress = accepted rows; contiguity = the window holds the last 8 "
                                               "accepted refs in order (content overlap)",
                  "core_cursor_unit": "accepted_rows_since_origin", "anchor_refs": adapter.ANCHOR,
                  "never_in_identity": ["capture sha256", "observed_utc", "event refs"]},
              "acceptance": {}}
    result["acceptance"]["parity"] = parity
    log("A parity", parity["pass"])
    result["acceptance"]["identity_stability"] = scenario_identity(pages, cycle_dirs, frozen_root, audit_root,
                                                                   run_dir)
    log("B identity", result["acceptance"]["identity_stability"]["pass"])
    result["acceptance"]["restart"] = scenario_restart(pages, audit_root, run_dir, clean_final)
    log("C restart", result["acceptance"]["restart"]["pass"])
    result["acceptance"]["negative_controls"] = scenario_negative(pages, audit_root, run_dir, frozen_root, cycle_dirs)
    log("D negative", result["acceptance"]["negative_controls"]["pass"])
    result["acceptance"]["reader_cursor_mode"] = scenario_cursor(pages, new_by_step, union, audit_root, run_dir,
                                                                 declared_snapshot)
    log("E cursor", result["acceptance"]["reader_cursor_mode"]["pass"])
    result["timing_bytes"] = {"new": timing(parity, parse_ms, result["acceptance"]["reader_cursor_mode"]),
                              "old_core_path_same_captures": scenario_old_core(pages, cycle_dirs, audit_root, run_dir),
                              "old_producer_log_same_cycles": old_producer(frozen_root, manifest)}
    log("F timing done")
    result["pass"] = all(item["pass"] for item in result["acceptance"].values())
    text = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=True) + "\n"
    messages = [row.get("message") for row in union]
    if any(isinstance(m, str) and len(m) > 24 and json.dumps(m, ensure_ascii=True)[1:-1] in text for m in messages):
        raise SystemExit("event text in the output")  # refs, hashes, counts and sizes only
    for target in (run_dir / "parity.json", DATA / "parity.json"):
        target.write_bytes(text.encode("ascii"))  # bytes: the printed digest is the file's digest (no CRLF)
    return {"pass": result["pass"], "parity_json_sha256": sha(text.encode("ascii")), "run_dir": run_dir.as_posix(),
            **{name: item["pass"] for name, item in result["acceptance"].items()}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    frz = sub.add_parser("freeze")
    frz.add_argument("--source", type=Path, default=LIVE)
    frz.add_argument("--loop", type=Path, default=LOOP)
    sub.add_parser("run")
    args = parser.parse_args(argv)
    if args.command == "freeze":
        print(json.dumps(freeze(args.source, args.loop, DATA / "frozen-live"), sort_keys=True))
    else:
        print(json.dumps(run(), sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
