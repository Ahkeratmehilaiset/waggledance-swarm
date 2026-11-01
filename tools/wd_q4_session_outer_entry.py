"""Default-off, explicitly pinned session-outer entry candidate.

The caller must authenticate/pin these entry bytes, the interpreter, every
supplied option and the selected runtime before launch. No environment-derived
pins or latest-generation discovery exist here. The trusted descriptor-to-entry
tuple builder and parent Job/cleanup protocol are not implemented here. The
query is read only from stdin and can block; there is no whole-call watchdog.
Same-user races, native output and caller/interpreter/stdlib trust remain open.
On nonzero exit the parent must discard stdout, including partial writes.
The opt-in ``--query-utc`` option is checked syntactically here and forwarded
unchanged as the last worker argument pair; the worker then requires stdin to
be the matching JSON query document. Its value is not a trusted clock.
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
    options = namespace = query = answer = raw = binding = worker_argv = encoded = None
    code = 2
    try:
        options = _parse(sys.argv[1:] if argv is None else argv)
        raw = _read_outer(options["outer-path"], options["outer-sha256"])
        _preflight_runtime(options)
        if _read_outer(options["outer-path"], options["outer-sha256"]) != raw:
            raise ValueError()
        namespace = {"__name__": "_wd_session_outer_verified", "__file__": options["outer-path"]}
        exec(compile(raw, options["outer-path"], "exec"), namespace)
        if not callable(namespace.get("run_session")) or not callable(namespace.get("_validate_lane_answer")):
            raise ValueError()
        for name in ("SessionOuterCleanupUnknown", "SessionOuterTimeout", "SessionOuterInterrupted"):
            value = namespace.get(name)
            if not isinstance(value, type) or not issubclass(value, Exception):
                raise ValueError()
        query = sys.stdin.buffer.read(_QUERY_LIMIT + 1)
        if type(query) is not bytes or not query or len(query) > _QUERY_LIMIT:
            raise ValueError()
        binding = {key: options["expected-" + key.replace("_", "-")] for key in _BINDING_KEYS}
        worker_argv = ["--enabled"]
        for key in _WORKER_KEYS:
            worker_argv.extend(("--" + key, options[key]))
        if "query-utc" in options:
            worker_argv.extend(("--query-utc", options["query-utc"]))
        answer = namespace["run_session"](
            enabled=True, runtime_root=options["runtime-root"], runtime_manifest=options["runtime-manifest"],
            runtime_manifest_sha256=options["runtime-manifest-sha256"], runtime_generation=options["runtime-generation"],
            runtime_revision=options["runtime-revision"], verifier_sha256=options["verifier-sha256"],
            outer_sha256=options["public-outer-sha256"], child_sha256=options["public-child-sha256"],
            query_sha256=options["public-query-sha256"], worker_sha256=options["worker-sha256"],
            argv=worker_argv, query=query, expected_lane=options["lane"], expected_binding=binding)
        if namespace["_validate_lane_answer"](answer, options["lane"], binding) != answer:
            raise ValueError()
        encoded = json.dumps(answer, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                             allow_nan=False).encode("ascii") + b"\n"
        if len(encoded) > _OUTPUT_LIMIT or sys.stdout.buffer.write(encoded) != len(encoded):
            raise ValueError()
        sys.stdout.buffer.flush()
        return 0
    except BaseException as exc:
        if _is_outer_exception(namespace, "SessionOuterCleanupUnknown", exc):
            code = 3
        elif _is_outer_exception(namespace, "SessionOuterTimeout", exc):
            code = 4
        elif isinstance(exc, KeyboardInterrupt) or _is_outer_exception(namespace, "SessionOuterInterrupted", exc):
            code = 130
    options = namespace = query = answer = raw = binding = worker_argv = encoded = None
    try:
        sys.stderr.write({2: "session_entry_refused\n", 3: "worker_cleanup_unknown\n",
                          4: "worker_timeout\n", 130: "worker_interrupted\n"}[code])
    except Exception:
        pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
