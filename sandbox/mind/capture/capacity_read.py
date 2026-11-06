"""On-demand capacity read for any lane: the sanitized capacity lens over the installed observer, read-only.

usage: python -B sandbox/mind/capture/capacity_read.py [--json]

A fixed function. It runs the installed read-only locator ``C:\\Python\\Get-WdCapacityStatus.ps1 -Json`` with the
capacity lens's own fixed argv and 60 s deadline (the locator verifies its own pins and runs the observer's
collector in ``--status`` mode only: it never collects, starts or repairs anything), projects the status with the
lens and prints the lens text, or with ``--json`` the lens view, to stdout. No argument selects another script,
directory, file or lane mapping, and nothing is written: no cycle dir, no publish dir, no raw status. What it prints
is what the lens guards: provider, window, used percent, reset, observation age, observer labels, collector health,
alert counts, per-provider activity counts and the age of the whole status (FRESH, STALE or FUTURE (invalid)); no
account, plan, token count, payload, catalog, thread id, alert id or unsafe label. A stale or future status is
printed with that state; the caller decides what to do with it.

Exit 0 with the text or view on stdout. A status the lens refuses, or a locator that is not a regular file at its
installed path, exits with ``refused: <fixed reason>`` and prints nothing; a bad argument exits 2.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capacity_lens as lens  # noqa: E402  (it puts the repo root on sys.path for the workspace guard)
from tools import bridge_mind_workspace as ws  # noqa: E402

STATUS_SCRIPT = Path(r"C:\Python\Get-WdCapacityStatus.ps1")  # the installed read-only locator, never an argument


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print the lens view instead of the lens text")
    args = parser.parse_args(argv)
    try:
        if STATUS_SCRIPT.is_symlink() or not STATUS_SCRIPT.is_file():
            raise lens.LensRefused("status_locator_missing")
        raw, status = lens.read_status(STATUS_SCRIPT)
        projected = lens.readable(status, {})
    except lens.LensRefused as exc:
        raise SystemExit(f"refused: {exc}") from None
    view, lines = lens.view_of(raw, projected), projected[-1]
    ws.guard_object(view)
    ws.guard_object(lines)
    text = json.dumps(view, indent=1, sort_keys=True) if args.json else "\n".join(lines)
    sys.stdout.write(text.encode("ascii", "replace").decode("ascii") + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
