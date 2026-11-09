"""Default-off, stdin-configured v3 status reader; not a fleet or startup route.

The launch caller must independently authenticate all three pins and their
pairing. A caller choosing its own files and hashes proves no provenance,
approval authority, identity, privacy of paths or freshness. The caller label
is an unverified declaration. No process, model or network is started here.
Paths and pins travel on stdin, not argv; Python traceback locals, the caller
and the output stream remain outside this module's confidentiality guarantee.
"""
import json
import re
import sys

from tools import wd_mind_status_reader_v3 as reader


MAX_REQUEST_BYTES = 16384
MAX_APPROVAL_BYTES = 65536
MAX_OUTPUT_BYTES = 1024 * 1024
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_LABELS = frozenset({"codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"})
_KEYS = frozenset({"projection_path", "projection_sha256", "descriptor_path", "descriptor_sha256",
                   "approval_path", "approval_sha256", "caller_label"})


def _read_request(stream):
    raw = stream.read(MAX_REQUEST_BYTES + 1)
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_REQUEST_BYTES:
        raise ValueError()
    value = reader.session._object(raw)
    if (set(value) != _KEYS or type(value["caller_label"]) is not str or value["caller_label"] not in _LABELS
            or any(type(value[key]) is not str or not value[key] for key in
                   ("projection_path", "descriptor_path", "approval_path"))
            or any(type(value[key]) is not str or _HEX.fullmatch(value[key]) is None for key in
                   ("projection_sha256", "descriptor_sha256", "approval_sha256"))):
        raise ValueError()
    return value


def _encode(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")
    if len(raw) + 1 > MAX_OUTPUT_BYTES:
        raise ValueError()
    return raw + b"\n"


def main(*, argv=None, stdin=None, stdout=None):
    """In-process entrypoint. ``--enabled`` is the sole permitted argument."""
    if argv is None:
        argv = sys.argv[1:]
    if stdout is None:
        try:
            stdout = sys.stdout.buffer
        except Exception:
            return 2
    if argv == []:
        payload, exit_code = b'{"status":"OFF"}\n', 0
    elif argv != ["--enabled"]:
        payload, exit_code = b'{"status":"REFUSED"}\n', 2
    else:
        payload, exit_code = b'{"status":"REFUSED"}\n', 2
        try:
            if stdin is None:
                stdin = sys.stdin.buffer
            request = _read_request(stdin)
            approval = reader._read(request["approval_path"], request["approval_sha256"], MAX_APPROVAL_BYTES)
            result = reader.read_projection(enabled=True, projection_path=request["projection_path"],
                expected_projection_sha256=request["projection_sha256"], descriptor_path=request["descriptor_path"],
                expected_descriptor_sha256=request["descriptor_sha256"], approval_bytes=approval,
                expected_approval_sha256=request["approval_sha256"])
            payload, exit_code = _encode(result), 0
        except Exception:
            pass
    try:
        written = stdout.write(payload)
        if type(written) is not int or written != len(payload):
            return 2
        stdout.flush()
    except Exception:
        return 2
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
