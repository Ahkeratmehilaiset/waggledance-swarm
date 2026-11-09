"""Retired legacy Q4 session outer entry. All invocations refuse before input.

The guarded caller candidate is tools.wd_q4_lane_caller_cli. Its supporting
validators remain importable, but this legacy process route is not active.
"""

import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import sys

_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_QUERY_UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.000000Z\Z")
_LANES = frozenset({"codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"})
_BINDING_KEYS = ("pointer_sha256", "source_anchor_sha256", "encoder_sha256", "policy_sha256", "roster_sha256",
                 "public_policy_sha256", "descriptor_sha256", "projection_sha256")
_WORKER_KEYS = ("lane", "bundle-root", "public-plan", "interpreter", "interpreter-sha256", "interpreter-max-bytes",
                "cwd", "timeout-seconds", "max-output-bytes", "fleet", "fleet-sha256", "binding", "binding-sha256",
                "fixed-plan", "fixed-plan-sha256", "snapshot", "snapshot-sha256", "runtime-manifest",
                "runtime-manifest-sha256")
_OUTER_KEYS = ("outer-path", "outer-sha256", "runtime-root", "runtime-generation", "runtime-revision",
               "verifier-sha256", "public-outer-sha256", "public-child-sha256", "public-query-sha256",
               "worker-sha256")
_EXPECTED_KEYS = tuple("expected-" + key.replace("_", "-") for key in _BINDING_KEYS)
_OPTIONS = frozenset(_WORKER_KEYS + _OUTER_KEYS + _EXPECTED_KEYS)
_PATHS = ("outer-path", "runtime-root", "runtime-manifest", "bundle-root", "public-plan", "interpreter", "cwd",
          "fleet", "binding", "fixed-plan", "snapshot")
_OUTER_LIMIT = 2 * 1024 * 1024
_QUERY_LIMIT = 16000
_OUTPUT_LIMIT = 65536
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


def _parse(argv):
    if type(argv) is not list or not argv or argv[0] != "--enabled":
        raise ValueError()
    if not (sys.flags.isolated and sys.flags.no_site and sys.flags.dont_write_bytecode):
        raise ValueError()
    if len(argv) not in (1 + 2 * len(_OPTIONS), 3 + 2 * len(_OPTIONS)):
        raise ValueError()
    options = {}
    for i in range(1, len(argv), 2):
        key, value = argv[i:i + 2]
        if (type(key) is not str or not key.startswith("--") or key[2:] not in _OPTIONS | {"query-utc"}
                or key[2:] in options or type(value) is not str or not value or len(value) > 32767 or "\x00" in value):
            raise ValueError()
        options[key[2:]] = value
    if set(options) - {"query-utc"} != _OPTIONS or options["lane"] not in _LANES:
        raise ValueError()
    if "query-utc" in options and not _QUERY_UTC.fullmatch(options["query-utc"]):
        raise ValueError()
    for key in _OPTIONS:
        if (key.endswith("sha256") or key.startswith("expected-")) and not _HEX64.fullmatch(options[key]):
            raise ValueError()
    for key in ("runtime-generation", "runtime-revision"):
        if not _IDENTITY.fullmatch(options[key]):
            raise ValueError()
    for key in _PATHS:
        value = Path(options[key])
        if not value.is_absolute() or ".." in value.parts or str(value).startswith(("\\\\", "//")):
            raise ValueError()
    for key, limit in (("interpreter-max-bytes", 128 * 1024 * 1024), ("timeout-seconds", 1800),
                       ("max-output-bytes", _OUTPUT_LIMIT)):
        value = options[key]
        if not value.isascii() or not value.isdecimal() or len(value) > 10 or not 1 <= int(value) <= limit:
            raise ValueError()
    wanted = Path(options["runtime-root"]) / "assets" / "code" / "tools" / "wd_q4_session_outer_synthetic.py"
    if os.path.normcase(os.path.abspath(wanted)) != os.path.normcase(os.path.abspath(options["outer-path"])):
        raise ValueError()
    return options


def _read_outer(path_text, expected_sha256):
    path = Path(path_text)
    for item in (path, *path.parents):
        info = os.lstat(item)
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
            raise ValueError()
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or not 0 < before.st_size <= _OUTER_LIMIT:
        raise ValueError()
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(_OUTER_LIMIT + 1)
        after = os.fstat(stream.fileno())
    final = os.lstat(path)
    ident = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_nlink)
    if (ident(before) != ident(opened) or ident(opened) != ident(after) or ident(after) != ident(final)
            or len(raw) > _OUTER_LIMIT or not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), expected_sha256)):
        raise ValueError()
    return raw


def _is_outer_exception(namespace, name, error):
    if type(namespace) is not dict:
        return False
    cls = namespace.get(name)
    return isinstance(cls, type) and issubclass(cls, Exception) and isinstance(error, cls)


def _verify_interpreter(options):
    """Check caller-pinned executable on disk; does not attest loaded memory or stdlib."""
    path = Path(options["interpreter"])
    if os.path.normcase(os.path.abspath(path)) != os.path.normcase(os.path.abspath(sys.executable)):
        raise ValueError()
    for item in (path, *path.parents):
        info = os.lstat(item)
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
            raise ValueError()
    before = os.lstat(path)
    if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or not 0 < before.st_size <= int(options["interpreter-max-bytes"])):
        raise ValueError()
    digest = hashlib.sha256()
    count = 0
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            count += len(block)
            if count > before.st_size:
                raise ValueError()
            digest.update(block)
        after = os.fstat(stream.fileno())
    final = os.lstat(path)
    ident = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_nlink)
    if (count != before.st_size or ident(before) != ident(opened) or ident(opened) != ident(after)
            or ident(after) != ident(final) or not hmac.compare_digest(digest.hexdigest(), options["interpreter-sha256"])):
        raise ValueError()


def _preflight_runtime(options):
    """Verify the caller-pinned runtime before accepting any query bytes.

    This is a point-in-time integrity check, not a lock on the runtime. The
    outer repeats verification later; same-user replacement remains possible.
    """
    _verify_interpreter(options)
    root = Path(options["runtime-root"])
    code_root = root / "assets" / "code"
    verifier = code_root / "sandbox" / "mind" / "step2" / "runtime_manifest.py"
    verifier_raw = _read_outer(str(verifier), options["verifier-sha256"])
    scope = {"__name__": "_wd_entry_runtime_verifier", "__file__": str(verifier)}
    exec(compile(verifier_raw, str(verifier), "exec"), scope)
    scope["verify_runtime"](root, options["runtime-manifest"], options["runtime-manifest-sha256"],
                            expected_generation=options["runtime-generation"],
                            expected_revision=options["runtime-revision"])
    for relative, pin in (("tools/wd_q4_public_outer.py", "public-outer-sha256"),
                          ("tools/wd_q4_public_child.py", "public-child-sha256"),
                          ("tools/wd_q4_public_query.py", "public-query-sha256"),
                          ("tools/wd_q4_lane_session_cli.py", "worker-sha256")):
        _read_outer(str(code_root.joinpath(*relative.split("/"))), options[pin])
    if _read_outer(str(verifier), options["verifier-sha256"]) != verifier_raw:
        raise ValueError()


def main(argv=None):
    """Retired legacy entry: refuse before loading runtime code or stdin."""
    try:
        sys.stderr.write("session_entry_refused\n")
    except Exception:
        pass
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
