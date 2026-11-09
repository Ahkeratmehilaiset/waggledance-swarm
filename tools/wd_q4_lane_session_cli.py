"""Retired legacy Q4 session CLI. Enabled calls refuse before file or stdin access.

The guarded caller candidate is tools.wd_q4_lane_caller_cli. Parsing helpers
remain importable for historical diagnostics; they do not enable this entry.
"""

from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import sys

from tools import wd_q4_lane_session_client as session
from tools.wd_q4_fixed_parent_attestation import AttestationCleanupUnknown
from tools.wd_q4_secure_transport import TransportCleanupUnknown, _MAX_RUNTIME_MANIFEST

MAX_STDIN = 16000
MAX_OUTPUT = 65536
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_QUERY_UTC = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})\.000000Z\Z")
_MAX_SITUATION = 4000
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_FILES = {"fleet": 1024 * 1024, "binding": 65536, "fixed-plan": 4096,
          "snapshot": 16 * 1024 * 1024, "runtime-manifest": _MAX_RUNTIME_MANIFEST}
_OPTIONS = frozenset({"lane", "bundle-root", "public-plan", "interpreter", "interpreter-sha256",
                      "interpreter-max-bytes", "cwd", "timeout-seconds", "max-output-bytes"} |
                     set(_FILES) | {key + "-sha256" for key in _FILES})
_HOLD = []  # Preserve an uncertain attestation lease until this process terminates.
_QUARANTINED = False  # Never dispatch another query after an unconfirmed cleanup in this process.


def _parse(argv):
    if type(argv) is not list or not argv or argv[0] != "--enabled":
        return None
    if len(argv) not in (1 + 2 * len(_OPTIONS), 3 + 2 * len(_OPTIONS)):
        raise ValueError()
    options = {}
    for pos in range(1, len(argv), 2):
        key, value = argv[pos:pos + 2]
        if (type(key) is not str or not key.startswith("--") or key[2:] not in _OPTIONS | {"query-utc"}
                or key[2:] in options or type(value) is not str or not value or len(value) > 32767):
            raise ValueError()
        options[key[2:]] = value
    if set(options) - {"query-utc"} != _OPTIONS:
        raise ValueError()
    if "query-utc" in options:
        options["query-utc"] = _query_time(options["query-utc"])
    return options


def _query_time(text):
    match = _QUERY_UTC.fullmatch(text)
    if match is None:
        raise ValueError()
    return text, datetime(*map(int, match.groups()), tzinfo=timezone.utc)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def _constant(_):
    raise ValueError()


def _exact_situation(situation, query_utc):
    """The wd_q4_agent_answer same-second gate, applied before any session query."""
    query = json.loads(situation, object_pairs_hook=_unique, parse_constant=_constant)
    if (type(query) is not dict or set(query) != {"situation", "time"}
            or type(query["situation"]) is not str or not query["situation"].strip()
            or len(query["situation"]) > _MAX_SITUATION or type(query["time"]) is not str
            or any(ord(char) < 0x20 or ord(char) == 0x7f for char in query["situation"])
            or query["time"] != query_utc[:-8] + "Z"):
        raise ValueError()
    return query["situation"]


def _file(path_text, digest, limit):
    if type(digest) is not str or _HEX.fullmatch(digest) is None:
        raise ValueError()
    path = Path(path_text)
    if (not path.is_absolute() or ".." in path.parts or str(path).startswith(("\\\\", "//"))
            or (os.name == "nt" and ":" in str(path)[2:])):
        raise ValueError()
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
            raise ValueError()
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
        raise ValueError()
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    final = path.stat()
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns)
    if (identity(before) != identity(opened) or identity(opened) != identity(after) or identity(after) != identity(final)
            or len(raw) > limit or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), digest)):
        raise ValueError()
    return raw


def _number(value, maximum):
    if type(value) is not str or not value.isascii() or not value.isdecimal() or len(value) > 10:
        raise ValueError()
    result = int(value)
    if not 1 <= result <= maximum:
        raise ValueError()
    return result


def _error(token):
    try:
        sys.stderr.write(json.dumps({"error": token, "authority": "none"}, separators=(",", ":")) + "\n")
    except Exception:
        pass


def main(argv=None):
    """Retired legacy entry: refuse without reading arguments, files or stdin."""
    _error("disabled")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
