"""Live shared Mind: one fold per minute from the pinned compact reader, every lane's lens beside it.

usage: python -B sandbox/mind/capture/live_mind.py --bin <pinned .agent-bridge/bin> --data-root <own sandbox dir>
           --seed-from <earlier capture.json> [--interval 60] [--cycles N] [--context-every 600] [--keep 20]
           [--variant <tracked variant .py>]... [--lens-hook <json file>]

Operator 10:26Z/10:30Z: an efficient shared mind that the lanes join at the same time, useful for current work.
A frozen round is up to 30 minutes old; this republishes the shared fold every ``--interval`` seconds.

Each cycle, in ``<data-root>/tournament/.codex-audit/live/cycles/<stamp>/``:

1. ``capture/capture_round.py`` (the external capture step): the pinned ``-Compact`` list and ``-EventId``
   details, seeded from the previous cycle's capture (only new rows are read). GitHub metadata is captured again
   only every ``--context-every`` seconds (the workspace refuses a context more than 15 minutes from the capture);
   in between the previous cycle's ``gh-pr-list.json`` and ``context.json`` are reused as they are.
2. ``tools/wd_bridge_mind_shadow.py`` ``run`` over those files (core, workspace, board), into ``run/``.
3. Lenses: every ``--variant`` (a tracked, clean file with ``VARIANT`` and ``render``, loaded by the tournament
   driver's own checks) for every lane, into ``lenses/<VARIANT>/<lane>/``; then each ``--lens-hook`` command (a
   JSON list of ``{"name", "tree", "argv"}`` whose argv may use ``{publication}``, ``{context}``, ``{capture}``,
   ``{audit_root}``, ``{cycle_dir}``; a tracked script of its lane's own branch, run as its own process only while that tree is clean; its head
   is recorded).
4. ``live/latest.json`` (written last, atomically, with a bounded retry for the Windows sharing window: RCO1 and
   RCO2 10:30Z) names the cycle dir, every digest, the times and the lens files. Readers read it first, then the
   immutable cycle dir it names; a cycle dir is never rewritten. Old captures beyond ``--keep`` cycles are pruned
   (their snapshot, lenses and digests stay).

One JSON line per cycle on stdout. Never writes a bridge event, never runs gate code, never echoes event content.
"""

import argparse
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MIND = HERE.parent
SRC = MIND.parents[1]
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(MIND))
from tools import bridge_mind_workspace as ws  # noqa: E402
from tools import wd_bridge_mind as core  # noqa: E402
from tools import wd_bridge_mind_shadow as shadow  # noqa: E402
import tournament_driver as driver  # noqa: E402

LATEST_SCHEMA = "wd.bridge-mind-live-latest.v1"
PLACEHOLDERS = ("publication", "context", "capture", "audit_root", "cycle_dir")


def stamp():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def replace_with_retry(tmp, target, tries=12):
    delay = 0.005
    for attempt in range(tries):
        try:
            os.replace(tmp, target)
            return attempt
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(delay)
            delay = min(0.1, delay * 2)


def capture(args, cycle_dir, seed, previous_dir, context_age):
    command = [sys.executable, "-B", str(HERE / "capture_round.py"), "--bin", str(args.bin), "--out", str(cycle_dir),
               "--files-cache", str(args.files_cache), "--seed-from", str(seed)]
    if previous_dir is not None and context_age is not None and context_age < args.context_every:
        command += ["--reuse-context", str(previous_dir)]
    else:
        command += ["--with-status"]
    done = subprocess.run(command, capture_output=True, timeout=600)
    if done.returncode != 0:
        raise RuntimeError("capture_exit_%d" % done.returncode)
    return json.loads((cycle_dir / "capture-record.json").read_text(encoding="ascii"))


def lenses(args, cycle_dir, inputs):
    out, timings = {}, {}
    for path in args.variant:
        module, meta = driver.load_variant(path)
        out[meta["variant"]] = {"variant": meta}
        for lane in driver.LANES:
            record = driver.render_lane(module, inputs, lane, cycle_dir / "lenses" / meta["variant"] / lane)
            out[meta["variant"]][lane] = record["files"]
            timings[f"{meta['variant']}/{lane}"] = record["render_ms"]
    return out, timings


def hooks(args, cycle_dir, audit_root):
    results = {}
    if args.lens_hook is None:
        return results
    values = {"publication": cycle_dir / "run" / "reflected.publication.json", "context": cycle_dir / "context.json",
              "capture": cycle_dir / "capture.json", "audit_root": audit_root, "cycle_dir": cycle_dir}
    for hook in json.loads(args.lens_hook.read_text(encoding="utf-8")):
        argv = [str(part).format(**{key: str(value) for key, value in values.items()}) for part in hook["argv"]]
        # The hook's code is the tracked, clean tree of its own lane branch; its head is recorded, a dirty tree skips.
        tree = Path(hook["tree"])
        head = driver.git(tree, "rev-parse", "HEAD")
        if driver.git(tree, "status", "--porcelain", "--untracked-files=no"):
            results[hook["name"]] = {"skipped": "tree_not_clean", "head": head}
            continue
        started = time.perf_counter()
        done = subprocess.run(argv, capture_output=True, timeout=120)
        results[hook["name"]] = {"exit": done.returncode, "head": head,
                                 "ms": round((time.perf_counter() - started) * 1000)}
    return results


def prune(cycles, keep):
    for old in sorted(path for path in cycles.iterdir() if path.is_dir())[:-keep]:
        for name in ("capture.json", "detail-manifest.txt"):
            if (old / name).exists():
                (old / name).unlink()


def stale_after(observed_utc, seconds):
    """The time after which a reader treats this cycle as stale: observed plus three intervals."""
    observed = dt.datetime.strptime(observed_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    return (observed + dt.timedelta(seconds=3 * seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def publish_latest(live, document):
    tmp = live / f".latest.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="ascii")
    return replace_with_retry(tmp, live / "latest.json")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bin", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--seed-from", type=Path, required=True)
    parser.add_argument("--files-cache", type=Path, default=None)
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--cycles", type=int, default=720)
    parser.add_argument("--context-every", type=int, default=600)
    parser.add_argument("--keep", type=int, default=20)
    parser.add_argument("--variant", type=Path, action="append", default=[])
    parser.add_argument("--lens-hook", type=Path, default=None)
    parser.add_argument("--until", default=None, help="stop before starting a cycle at or after this UTC time")
    args = parser.parse_args()
    audit_root = Path(os.path.abspath(args.data_root)) / "tournament" / ".codex-audit"
    live = audit_root / "live"
    cycles = live / "cycles"
    cycles.mkdir(parents=True, exist_ok=True)
    args.files_cache = args.files_cache or args.data_root / "captures" / "files-cache"
    seed, previous_dir, previous_pub, context_at, last_ok = args.seed_from, None, None, None, None
    until = None if args.until is None else dt.datetime.strptime(args.until, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc)
    for number in range(1, args.cycles + 1):
        if until is not None and dt.datetime.now(dt.timezone.utc) >= until:
            # Withhold currentness: a stopped producer never leaves an "ok" pointer behind.
            publish_latest(live, {"schema": LATEST_SCHEMA, "authority": "none", "scope": "shadow_observation_not_gate",
                                  "status": "stopped", "reason": "until_reached", "stopped_utc": stamp(),
                                  "last_ok": None if last_ok is None else {key: last_ok[key] for key in (
                                      "cycle", "cycle_dir", "observed_utc", "snapshot_sha256", "publication")},
                                  "read": "the producer stopped; every earlier cycle is stale"})
            print(json.dumps({"cycle": number, "status": "stopped", "reason": "until_reached"}), flush=True)
            break
        tick = time.perf_counter()
        cycle_dir = cycles / stamp()
        line = {"cycle": number, "cycle_dir": cycle_dir.name}
        try:
            age = None if context_at is None else time.time() - context_at
            record = capture(args, cycle_dir, seed, previous_dir, age)
            if "reused_from" not in record["github"]:
                context_at = time.time()
            started = time.perf_counter()
            receipt = shadow.run(cycle_dir / "capture.json", audit_root, cycle_dir / "run",
                                 context_path=cycle_dir / "context.json", previous_path=previous_pub)
            run_ms = round((time.perf_counter() - started) * 1000)
            publication = cycle_dir / "run" / "reflected.publication.json"
            snapshot = core.read_snapshot(publication, audit_root=audit_root)
            capture_bytes = (cycle_dir / "capture.json").read_bytes()
            context_bytes = (cycle_dir / "context.json").read_bytes()
            inputs = {"events": json.loads(capture_bytes.decode("utf-8"))["events"], "round": "live:" + cycle_dir.name,
                      "snapshot": snapshot, "snapshot_sha256": sha(core.canonical_bytes(snapshot)),
                      "context": json.loads(context_bytes.decode("utf-8")), "capture_sha256": sha(capture_bytes),
                      "context_sha256": sha(context_bytes), "side": None,
                      "observed_status": (json.loads((cycle_dir / "observed-status.json").read_text(encoding="ascii"))
                                          if (cycle_dir / "observed-status.json").exists() else None)}
            started = time.perf_counter()
            views, render_ms = lenses(args, cycle_dir, inputs)
            lens_ms = round((time.perf_counter() - started) * 1000)
            hook_results = hooks(args, cycle_dir, audit_root)
            latest = {"schema": LATEST_SCHEMA, "authority": "none", "scope": "shadow_observation_not_gate",
                      "status": "ok", "cycle": number, "cycle_dir": f"live/cycles/{cycle_dir.name}",
                      "observed_utc": record["bridge"]["observed_utc"],
                      "stale_after_utc": stale_after(record["bridge"]["observed_utc"], args.interval),
                      "producer_until_utc": args.until,
                      "context_observed_utc": inputs["context"]["observed_utc"],
                      "capture_sha256": inputs["capture_sha256"], "context_sha256": inputs["context_sha256"],
                      "snapshot_sha256": inputs["snapshot_sha256"],
                      "publication": f"live/cycles/{cycle_dir.name}/run/reflected.publication.json",
                      "lenses": {variant: {lane: sorted(files) for lane, files in rows.items() if lane != "variant"}
                                 for variant, rows in views.items()},
                      "variants": {variant: rows["variant"] for variant, rows in views.items()},
                      "hooks": hook_results, "counts": receipt["counts"],
                      "measures": {"bridge_ms": record["bridge"]["ms"], "detail_calls": record["bridge"]["detail_calls"],
                                   "github": record["github"], "run_ms": run_ms, "lens_ms": lens_ms,
                                   "render_ms": render_ms},
                      "read": "read this file, then the cycle dir it names; check the publication with "
                              "tools/wd_bridge_mind.py read_snapshot(path, audit_root=<this .codex-audit>)",
                      "advisory": "observe only; before any vote or pass check the canonical tools"}
            ws.guard_object(latest)
            retries = publish_latest(live, latest)
            seed, previous_dir, previous_pub = cycle_dir / "capture.json", cycle_dir, publication
            last_ok = latest
            line.update(status="ok", events=record["bridge"]["events"], detail=record["bridge"]["detail_calls"],
                        context="reused" if "reused_from" in record["github"] else "fresh",
                        snapshot=inputs["snapshot_sha256"][:16], attention=receipt["counts"]["attention"],
                        replace_retries=retries)
        except Exception as exc:  # a refused cycle is a gap, published as such so no lane reads old data silently
            line.update(status="refused", reason=(type(exc).__name__ + ":" + str(exc))[:120])
            failed = {"schema": LATEST_SCHEMA, "authority": "none", "scope": "shadow_observation_not_gate",
                      "status": "refused", "reason": type(exc).__name__, "cycle": number, "refused_utc": stamp(),
                      "last_ok": None if last_ok is None else {key: last_ok[key] for key in (
                          "cycle", "cycle_dir", "observed_utc", "snapshot_sha256", "publication", "lenses")},
                      "read": "the last good cycle is older than this refusal; treat its lenses as stale"}
            try:
                publish_latest(live, failed)
            except OSError:
                pass
        prune(cycles, args.keep)
        line["cycle_ms"] = round((time.perf_counter() - tick) * 1000)
        print(json.dumps(line), flush=True)
        if number < args.cycles:
            time.sleep(max(5, args.interval - (time.perf_counter() - tick)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
