"""Reproduce the Windows sharing window on the shared publication: 1 writer and N readers, core API only.

usage: python -B sandbox/mind/probes/sharing_window_probe.py --audit-root <dir named .codex-audit> [--seconds 15]
           [--readers 5] [--tries 1|12] [--out <result json>]

The writer publishes one snapshot again and again with ``write_snapshot``; each reader process reads it again and
again with ``read_snapshot``. ``--tries`` sets the core's bounded sharing retry (1 = no retry, the control). The
result counts publishes, refused publishes, reads, refused reads and torn reads (a read whose digest is not the
published one), so a run with and one without the retry are a recorded reproduction and its control.
Writes only the given audit root and ``--out``.
"""

import argparse
import hashlib
import json
import multiprocessing
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(SRC))
from tools import wd_bridge_mind as core  # noqa: E402

AS_OF = "2026-10-13T04:00:00Z"
UUID = "7a8af68d-20bc-4598-9953-23c5dd98b102"


def fixture():
    from tools.bridge_compact_view import event_id
    event = dict(agent="codex-tools-1", agent_uuid=UUID, session_id="s", run_id="r", ts_utc="2026-10-13T03:59:00Z",
                 type="message", status="head_updated", task_id="task/1", payload=dict(pr=1900, head="a" * 40))
    source = dict(log_identity="probe", generation="g", cursor_start=0, cursor_end=1, event_count=1,
                  coverage="complete")
    state = core.full_rebuild([dict(ref=event_id(event), event=event)], source=source, as_of_utc=AS_OF)
    return core.snapshot(state, as_of_utc=AS_OF)


def reader(path, audit_root, tries, seconds, digest, queue):
    core._SHARING_TRIES = tries
    reads = refused = torn = 0
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            value = core.read_snapshot(Path(path), audit_root=Path(audit_root))
            reads += 1
            if hashlib.sha256(core.canonical_bytes(value)).hexdigest() != digest:
                torn += 1
        except core.ProjectionError:
            refused += 1
    queue.put({"reads": reads, "refused": refused, "torn": torn})


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--audit-root", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=15)
    parser.add_argument("--readers", type=int, default=5)
    parser.add_argument("--tries", type=int, default=core._SHARING_TRIES)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    core._SHARING_TRIES = args.tries
    snap = fixture()
    path = args.audit_root / f"probe-{args.tries}.json"
    digest = core.write_snapshot(snap, path, audit_root=args.audit_root)
    queue = multiprocessing.Queue()
    workers = [multiprocessing.Process(target=reader, args=(str(path), str(args.audit_root), args.tries,
                                                            args.seconds, digest, queue))
               for _ in range(args.readers)]
    for worker in workers:
        worker.start()
    publishes = failed = 0
    deadline = time.monotonic() + args.seconds
    while time.monotonic() < deadline:
        try:
            core.write_snapshot(snap, path, audit_root=args.audit_root)
            publishes += 1
        except core.ProjectionError:
            failed += 1
    rows = [queue.get() for _ in workers]
    for worker in workers:
        worker.join()
    result = {"schema": "wd.bridge-mind-sharing-probe.v1", "tries": args.tries, "seconds": args.seconds,
              "readers": args.readers, "publishes": publishes, "publish_failed": failed,
              "reads": sum(row["reads"] for row in rows), "read_refused": sum(row["refused"] for row in rows),
              "torn": sum(row["torn"] for row in rows), "platform": sys.platform}
    text = json.dumps(result, sort_keys=True)
    if args.out is not None:
        args.out.write_text(text + "\n", encoding="ascii")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
