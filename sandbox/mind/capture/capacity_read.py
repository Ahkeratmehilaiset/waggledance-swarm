"""On-demand capacity read for any lane: the sanitized capacity lens over the installed observer, read-only.

usage: python -B sandbox/mind/capture/capacity_read.py [--json]

A fixed function. It runs the installed read-only locator ``C:\\Python\\Get-WdCapacityStatus.ps1 -Json`` with the
capacity lens's own fixed argv and 60 s deadline (the locator verifies its own pins and runs the observer's
collector in ``--status`` mode only: it never collects, starts or repairs anything), projects the status with the
lens and prints the lens text, or with ``--json`` the lens view, to stdout. No argument selects another script,
directory, file or lane mapping, and nothing is written: no cycle dir, no publish dir, no raw status. What it prints
is what the lens guards: provider, window, used percent, reset, observation age, observer labels, collector health,
alert counts, per-provider activity counts and the age of the whole status (FRESH or STALE); no account, plan,
token count, payload, catalog, thread id, alert id or unsafe label. A stale status is printed with every figure
line labelled STALE (the caller decides what to do with it); a future status is refused.

The locator is pinned (CR2): before the run its bytes must hash to the sha256 the installer recorded for
Get-WdCapacityStatus.ps1 in the installed integrity record ``C:\\Python\\WD_REBOOT_INTEGRITY_CURRENT.json``, and
again after the run. The route reads that pin and never derives or invents one: a record that is missing, a link,
unreadable, not schema 1 or without a sha256 for the locator refuses (status_locator_unpinned), a locator whose
bytes differ refuses (status_locator_pin_mismatch), and one that changed during the run refuses
(status_locator_changed). The record is as trustworthy as the install that wrote it: it is no signature, and the
same account can rewrite both files; a swap that is undone within the run is not seen.

Exit 0 with the text or view on stdout. A status the lens refuses, or a locator that is not a regular file at its
installed path or does not match its pin, exits with ``refused: <fixed reason>`` and prints nothing; a bad argument
exits 2.
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capacity_lens as lens  # noqa: E402  (it puts the repo root on sys.path for the workspace guard)
from tools import bridge_mind_workspace as ws  # noqa: E402

STATUS_SCRIPT = Path(r"C:\Python\Get-WdCapacityStatus.ps1")  # the installed read-only locator, never an argument
INTEGRITY = Path(r"C:\Python\WD_REBOOT_INTEGRITY_CURRENT.json")  # the installer's sha256 of each installed file
SHA256 = re.compile(r"[0-9A-Fa-f]{64}")


def regular_bytes(path, reason):
    """The bytes of a regular file that is not a link, else LensRefused(reason)."""
    try:
        if path.is_symlink() or not path.is_file():
            raise lens.LensRefused(reason)
        return path.read_bytes()
    except OSError:
        raise lens.LensRefused(reason) from None


def installed_pin():
    """CR2: the locator's sha256 as the installer recorded it (lowercase), else LensRefused status_locator_unpinned."""
    try:
        record = json.loads(regular_bytes(INTEGRITY, "status_locator_unpinned").decode("utf-8-sig"))
    except (RecursionError, ValueError):  # the decode and JSON errors are ValueErrors
        raise lens.LensRefused("status_locator_unpinned") from None
    version = record.get("schema_version") if isinstance(record, dict) else None
    files = record.get("files") if type(version) is int and version == 1 else None
    pin = files.get(STATUS_SCRIPT.name) if isinstance(files, dict) else None
    if not isinstance(pin, str) or not SHA256.fullmatch(pin):
        raise lens.LensRefused("status_locator_unpinned")
    return pin.lower()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print the lens view instead of the lens text")
    args = parser.parse_args(argv)
    try:
        located = regular_bytes(STATUS_SCRIPT, "status_locator_missing")
        pin = installed_pin()
        if hashlib.sha256(located).hexdigest() != pin:
            raise lens.LensRefused("status_locator_pin_mismatch")
        raw, status = lens.read_status(STATUS_SCRIPT)
        if hashlib.sha256(regular_bytes(STATUS_SCRIPT, "status_locator_changed")).hexdigest() != pin:
            raise lens.LensRefused("status_locator_changed")
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
