"""Concurrent-join probe for one shared Mind publication (RCO1 sandbox; offline, data on the F sandbox only).

Operator 10:26Z: the goal is one efficient shared mind that agents join at the same time. This probe asks whether
the core's atomic publication (tools/wd_bridge_mind.py write_snapshot / read_snapshot: temp file, fsync,
os.replace) holds when one writer republishes while N lane readers read the same file at once on Windows.

usage: python -B probe_concurrent_join.py --src <core source root> --pub-a <reflected publication>
           --root-a <its .codex-audit> --pub-b <another> --root-b <its .codex-audit> --work <F sandbox dir>
           [--readers 5] [--seconds 20] [--write-interval 0.0]

The writer alternates the two valid snapshots A and B; every reader loops read_snapshot and checks that the
snapshot digest it got is A or B (a torn or mixed read would be neither). It reports per role: successes,
failures by fixed reason code, and latency percentiles. Nothing touches the bridge, GitHub or a provider.
"""

import argparse
import hashlib
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path


def _core(src):
    sys.path.insert(0, src)
    sys.dont_write_bytecode = True
    from tools import wd_bridge_mind as core
    return core


def _digest(core, snapshot):
    return hashlib.sha256(core.canonical_bytes(snapshot)).hexdigest()


def _pct(values, q):
    if not values:
        return None
    values = sorted(values)
    return round(values[min(len(values) - 1, int(q * len(values)))] * 1000, 2)


def writer(src, target, root, snapshots, seconds, interval, out, retries=0):
    core = _core(src)
    ok, failed, lat, retried = 0, {}, [], 0
    end = time.perf_counter() + seconds
    index = 0
    while time.perf_counter() < end:
        started = time.perf_counter()
        for attempt in range(retries + 1):
            try:
                core.write_snapshot(snapshots[index % 2], Path(target), audit_root=Path(root))
                ok += 1
                lat.append(time.perf_counter() - started)
                break
            except core.ProjectionError as exc:
                if str(exc) == "snapshot_publication_failed" and attempt < retries:
                    retried += 1
                    time.sleep(0.01 * (attempt + 1))
                    continue
                failed[str(exc)] = failed.get(str(exc), 0) + 1
        index += 1
        if interval:
            time.sleep(interval)
    out.put({"role": "writer", "ok": ok, "failed": failed, "retried": retried, "p50_ms": _pct(lat, 0.5), "p95_ms": _pct(lat, 0.95),
             "max_ms": _pct(lat, 1.0)})


def reader(src, target, root, valid, seconds, name, out, retries=0):
    core = _core(src)
    ok, failed, wrong, lat, retried = 0, {}, 0, [], 0
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        started = time.perf_counter()
        for attempt in range(retries + 1):
            try:
                snapshot = core.read_snapshot(Path(target), audit_root=Path(root))
                lat.append(time.perf_counter() - started)
                if _digest(core, snapshot) in valid:
                    ok += 1
                else:
                    wrong += 1
                break
            except core.ProjectionError as exc:
                if str(exc) == "snapshot_read_refused" and attempt < retries:
                    retried += 1
                    time.sleep(0.005 * (attempt + 1))
                    continue
                failed[str(exc)] = failed.get(str(exc), 0) + 1
    out.put({"role": name, "ok": ok, "failed": failed, "retried": retried, "wrong_digest": wrong, "p50_ms": _pct(lat, 0.5),
             "p95_ms": _pct(lat, 0.95), "max_ms": _pct(lat, 1.0)})


def main():
    parser = argparse.ArgumentParser()
    for name in ("--src", "--pub-a", "--root-a", "--pub-b", "--root-b", "--work"):
        parser.add_argument(name, required=True)
    parser.add_argument("--readers", type=int, default=5)
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--write-interval", type=float, default=0.0)
    parser.add_argument("--retries", type=int, default=0)
    args = parser.parse_args()
    work = Path(args.work).resolve()
    if not str(work).lower().startswith("f:\\waggledance_memory\\sandbox\\claude-rco-2"):
        raise SystemExit("refused: work dir outside the RCO2 F sandbox (RCO2 reproduction of RCO1 b9ca18f0; this guard line is the only change)")
    core = _core(args.src)
    snapshots = [core.read_snapshot(Path(args.pub_a), audit_root=Path(args.root_a)),
                 core.read_snapshot(Path(args.pub_b), audit_root=Path(args.root_b))]
    valid = {_digest(core, value) for value in snapshots}
    root = work / ".codex-audit"
    (root / "shared").mkdir(parents=True, exist_ok=True)
    target = root / "shared" / "mind.publication.json"
    core.write_snapshot(snapshots[0], target, audit_root=root)
    out = mp.Queue()
    procs = [mp.Process(target=writer, args=(args.src, str(target), str(root), snapshots, args.seconds,
                                             args.write_interval, out, args.retries))]
    procs += [mp.Process(target=reader, args=(args.src, str(target), str(root), valid, args.seconds, f"reader{i + 1}", out, args.retries))
              for i in range(args.readers)]
    for proc in procs:
        proc.start()
    results = [out.get() for _ in procs]
    for proc in procs:
        proc.join()
    summary = {"schema": "rco1.mind-concurrent-join-probe.v1", "authority": "none", "readers": args.readers,
               "seconds": args.seconds, "write_interval": args.write_interval, "retries": args.retries,
               "publication_bytes": target.stat().st_size, "valid_digests": sorted(valid),
               "results": sorted(results, key=lambda row: row["role"])}
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    (work / f"concurrent-join-{stamp}-w{args.write_interval}-r{args.retries}.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps(summary["results"], indent=None))


if __name__ == "__main__":
    main()
