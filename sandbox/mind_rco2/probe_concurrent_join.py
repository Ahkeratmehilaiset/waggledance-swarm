import json, sys, time, statistics
from multiprocessing import Process, Queue
from pathlib import Path
REPO = r"C:\Python\waggledance-agent-worktrees\claude-rco-2-sandbox-v3"
sys.path.insert(0, REPO)
ROOT = Path(r"F:\waggledance_memory\sandbox\claude-rco-2\.codex-audit")
ROUND = ROOT / "rounds" / "round-2"
PUB = Path(r"F:\waggledance_memory\sandbox\claude-rco-2\shared-mind\round-2-join-test")
LANES = ["codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"]

def writer(q, seconds):
    from sandbox.mind_rco2 import v3_lane_inbox as v3
    from tools import wd_bridge_mind as core, bridge_mind_workspace as ws
    t0 = time.perf_counter()
    snapshot = core.read_snapshot(ROUND / "v0-run" / "reflected.publication.json", audit_root=ROOT)
    context, _ = ws.read_context(json.loads((ROUND / "context.json").read_text(encoding="utf-8")), ws.parse_time(snapshot["source"]["as_of_utc"]))
    capture = v3.Capture(json.loads((ROUND / "capture.json").read_text(encoding="utf-8"))["events"])
    fold = v3.build_fold(snapshot, context, capture, snapshot["source"]["as_of_utc"])
    build_ms = (time.perf_counter() - t0) * 1000
    n, fails, pub_ms = 0, 0, []
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        a = time.perf_counter()
        try:
            v3.publish_shared(fold, LANES, PUB)
        except Exception:
            fails += 1
        pub_ms.append((time.perf_counter() - a) * 1000)
        n += 1
    q.put({"role": "writer", "load_and_fold_ms": round(build_ms), "publishes": n, "fails": fails,
           "publish_ms_p50": round(statistics.median(pub_ms), 2), "fold_bytes": (PUB / "fold.json").stat().st_size})

def reader(q, lane, seconds):
    from sandbox.mind_rco2 import v3_lane_inbox as v3
    n, fails, torn, ms = 0, 0, 0, []
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        a = time.perf_counter()
        try:
            text = v3.read_lens(PUB, lane)
            if not text.startswith("V3 inbox ") or not text.endswith("\n"):
                torn += 1
        except Exception:
            fails += 1
        ms.append((time.perf_counter() - a) * 1000)
        n += 1
    q.put({"role": "reader", "lane": lane, "reads": n, "fails": fails, "torn": torn,
           "read_ms_p50": round(statistics.median(ms), 3), "read_ms_p99": round(sorted(ms)[int(len(ms) * 0.99)], 3)})

if __name__ == "__main__":
    from sandbox.mind_rco2 import v3_lane_inbox as v3
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 15
    # seed one publication so readers have files from the start
    w0 = Queue(); p = Process(target=writer, args=(w0, 0.01)); p.start(); p.join(); print(w0.get())
    q = Queue()
    procs = [Process(target=writer, args=(q, seconds))] + [Process(target=reader, args=(q, lane, seconds)) for lane in LANES]
    for proc in procs: proc.start()
    for proc in procs: proc.join()
    while not q.empty(): print(json.dumps(q.get()))
