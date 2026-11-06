"""Default-off CLI for an explicitly pinned Q4 lane session candidate.

Every path and SHA256 is supplied by the caller; hashing is integrity checking,
not authentication. The fixed snapshot must already exist as a coherent bundle
and match the independently pinned fixed plan. No bridge data, roots, policies,
pins, or lane identity are discovered here. The lane is only a declaration.
The caller protects pins, stdin and the output and supplies process identity.
Although byte counts are bounded, stdin.read, stdout.write and stdout.flush can
each block indefinitely. The per-query timeout covers the fixed-peer query, not
these I/O operations. A whole-process external watchdog is not implemented here.
A future launcher must put the owned CLI and its descendants in an appropriate
process-tree containment, service pipes without deadlocking, enforce a monotonic
whole-call deadline, terminate only that owned tree on expiry, and report cleanup
UNKNOWN if it cannot verify termination. Path checks do not defeat same-user
replacement races. This is neither a launcher nor an install.

Output bytes cannot retract after a short write or a failed flush, even when a
partial output parses as JSON. A caller may accept only exit 0 plus exactly one
complete newline-framed JSON document; on any nonzero exit it must discard stdout
entirely. Exit 3 means cleanup UNKNOWN, 4 timeout, 130 interruption. An upstream
SystemExit retains only an ordinary nonzero integer status excluding these reserved
codes; a zero, reserved or non-integer exit maps to refusal code 2.

Without ``--query-utc`` stdin is one raw situation and this CLI chooses the
query time itself with datetime.now. The opt-in ``--query-utc`` option instead
takes one canonical ``YYYY-MM-DDTHH:MM:SSZ``-shaped UTC instant with exactly
``.000000`` fractional digits; stdin is then one strict JSON document with
exactly ``situation`` and ``time``, whose second-precision time must denote the
same whole second. Any mismatch or malformed document is refused before the
session query and its transport start, and the wall clock is not read. The time
is a caller-supplied value, not authentication, a trusted clock or freshness.
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
    global _QUARANTINED
    if _QUARANTINED:
        _error("cleanup_unknown")
        return 3
    try:
        options = _parse(sys.argv[1:] if argv is None else argv)
        if options is None:
            _error("disabled")
            return 2
        for key in ("bundle-root", "public-plan", "interpreter", "cwd"):
            path = Path(options[key])
            if not path.is_absolute() or ".." in path.parts or str(path).startswith(("\\\\", "//")):
                raise ValueError()
        files = {name: _file(options[name], options[name + "-sha256"], limit) for name, limit in _FILES.items()}
        if _HEX.fullmatch(options["interpreter-sha256"]) is None:
            raise ValueError()
        max_interpreter = _number(options["interpreter-max-bytes"], 128 * 1024 * 1024)
        timeout = _number(options["timeout-seconds"], 1800)
        max_output = _number(options["max-output-bytes"], MAX_OUTPUT)
        raw = sys.stdin.buffer.read(MAX_STDIN + 1)
        if type(raw) is not bytes or not raw or len(raw) > MAX_STDIN:
            raise ValueError()
        if raw.endswith(b"\r\n"):
            raw = raw[:-2]
        elif raw.endswith(b"\n"):
            raw = raw[:-1]
        situation = raw.decode("utf-8", "strict")
        if "query-utc" in options:
            query_utc, when = options["query-utc"]
            situation = _exact_situation(situation, query_utc)
        else:
            when = datetime.now(timezone.utc)
        answer = session.query_session(
            lane=options["lane"], situation=situation, time=when,
            fleet_bytes=files["fleet"], expected_fleet_sha256=options["fleet-sha256"],
            binding_bytes=files["binding"], expected_binding_sha256=options["binding-sha256"],
            bundle_root=options["bundle-root"], public_plan_path=options["public-plan"],
            executable=options["interpreter"], expected_interpreter_sha256=options["interpreter-sha256"],
            interpreter_max_bytes=max_interpreter, fixed_plan_bytes=files["fixed-plan"],
            expected_fixed_plan_sha256=options["fixed-plan-sha256"], fixed_bundle_bytes=files["snapshot"],
            runtime_manifest_bytes=files["runtime-manifest"], cwd=options["cwd"],
            timeout_seconds=timeout, max_output_bytes=max_output, enabled=True)
        if type(answer) is not dict or answer.get("status") not in ("ANSWER", "ABSTAIN", "DEGRADED", "REFUSED"):
            raise ValueError()
        output = json.dumps(answer, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                            allow_nan=False).encode("ascii") + b"\n"
        if len(output) > MAX_OUTPUT or sys.stdout.buffer.write(output) != len(output):
            raise OSError()
        sys.stdout.buffer.flush()
        return 2 if answer["status"] == "REFUSED" else 0
    except AttestationCleanupUnknown as exc:
        _QUARANTINED = True
        _HOLD.append(exc.lease)
        _error("cleanup_unknown")
        return 3
    except TransportCleanupUnknown:
        _QUARANTINED = True
        _error("cleanup_unknown")
        return 3
    except TimeoutError:
        _error("timeout")
        return 4
    except KeyboardInterrupt:
        _error("interrupted")
        return 130
    except SystemExit as exc:
        code = exc.code if type(exc.code) is int and 1 <= exc.code <= 255 and exc.code not in (3, 4, 130) else 2
        _error("session_exit")
        return code
    except Exception:
        _error("session_refused")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
