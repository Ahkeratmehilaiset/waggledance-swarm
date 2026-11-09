"""Inactive CLI for a pinned public-status session; not a startup integration.

Environment values, including pins and lane identity, are caller assertions,
not authentication. A trusted launcher must independently supply and protect
them; this command does not verify the source or prove source freshness.
The enable flag is WD_MIND_PUBLIC_ENABLED=1; other settings are
WD_MIND_PUBLIC_AUDIT_ROOT, WD_MIND_PUBLIC_MAX_AGE_SECONDS, and pairs
WD_MIND_PUBLIC_{ANCHOR,POLICY,ROSTER,PUBLIC_POLICY}_{PATH,SHA256}, plus the
AGENT_BRIDGE_AGENT and AGENT_BRIDGE_AGENT_UUID session values. Path checks do
not defeat hostile replacement between verification and use.
Without WD_MIND_PUBLIC_READ_VERSION the v2 publication is read unchanged. The
explicit value v3 reads the v3 series publication instead, with the ANCHOR pin
being the fixed root anchor of the series; any other value refuses before any
pin is read. The opt-in never enables the reader by itself.
"""
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import stat
import sys

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_SECONDS = re.compile(r"[1-9][0-9]{0,2}\Z")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_REFUSAL = '{"error":"public_session_refused","authority":"none"}'
_PIN_LIMITS = {"ANCHOR": 16384, "POLICY": 65536, "ROSTER": 1024 * 1024, "PUBLIC_POLICY": 65536}


def _required(name):
    value = os.environ.get(name)
    if not isinstance(value, str) or not value or len(value) > 32767:
        raise ValueError()
    return value


def _bounded_pin(name, limit):
    path = Path(_required("WD_MIND_PUBLIC_" + name + "_PATH"))
    text = str(path)
    if (not path.is_absolute() or ".." in path.parts
            or (os.name == "nt" and (text.startswith(("\\\\", "//")) or ":" in text[2:]))):
        raise ValueError()
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
            raise ValueError()
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError()
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError()
    digest = _required("WD_MIND_PUBLIC_" + name + "_SHA256").lower()
    if not _HEX.fullmatch(digest):
        raise ValueError()
    return raw, digest


def main(argv=None):
    try:
        if argv is None:
            argv = sys.argv[1:]
        if argv or os.environ.get("WD_MIND_PUBLIC_ENABLED") != "1":
            raise ValueError()
        version = os.environ.get("WD_MIND_PUBLIC_READ_VERSION")
        if version is not None and version != "v3":
            raise ValueError()
        age_text = _required("WD_MIND_PUBLIC_MAX_AGE_SECONDS")
        if not _SECONDS.fullmatch(age_text) or int(age_text) > 900:
            raise ValueError()
        lane = _required("AGENT_BRIDGE_AGENT")
        uuid = _required("AGENT_BRIDGE_AGENT_UUID")
        audit_root = Path(_required("WD_MIND_PUBLIC_AUDIT_ROOT"))
        pins = {}
        for name, limit in _PIN_LIMITS.items():
            data, digest = _bounded_pin(name, limit)
            pins[name.lower() + "_bytes"] = data
            pins[name.lower() + "_sha256"] = digest
        from tools.wd_mind_public_session import read_status, read_status_v3

        route = read_status if version is None else read_status_v3
        result = route(audit_root, lane=lane, agent_uuid=uuid, as_of=datetime.now(timezone.utc),
                       max_age_seconds=int(age_text), enabled=True, **pins)
        output = result.decode("ascii") + "\n"
        written = sys.stdout.write(output)
        if type(written) is not int or written != len(output):
            raise OSError("stdout_incomplete")
        # A partial output cannot be retracted; failure is reported without a
        # retry or a false success when a pipe closes or buffering fails.
        sys.stdout.flush()
        return 0
    except Exception:
        sys.stderr.write(_REFUSAL + "\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
