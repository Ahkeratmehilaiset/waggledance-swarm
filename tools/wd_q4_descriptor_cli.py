"""Default-off CLI (OFF unless ``--enable`` is passed, a default-off contract at every layer) that composes the
existing fixed-plan, descriptor and materializer producers from one explicit request and prints only the
descriptor path and sha256.

Usage: ``python -m tools.wd_q4_descriptor_cli [--request <file> [--enable]]``. With no arguments, or without
``--enable``, it prints ``{"status":"OFF"}`` and opens nothing, not even the request file. With ``--enable`` it reads
ONE strict JSON request (ASCII, no duplicate keys, exact key set for the request, the fixed-plan block and the
descriptor block while the outer block is checked on its three shared keys and otherwise by the descriptor producer,
integers only, no booleans and no null, printable characters only, 64 KiB cap, regular file on a single-backslash
drive-absolute path with no reparse point on its chain) naming
the target directory and generation, the fleet, lane-binding and metadata-only public snapshot source files each
with an explicit sha256 pin, the lanes, the fixed-plan arguments and the remaining descriptor arguments. Every value
is explicit: the CLI reads no environment, discovers nothing and defaults nothing. It composes, in order,
``wd_q4_fixed_plan_producer.build_fixed_plan`` (fixed plan bytes + pin), ``wd_q4_descriptor_materializer.
plan_generation_paths`` (the deterministic file paths), ``wd_q4_shared_descriptor_producer.build_shared_descriptor``
(the v2 descriptor on those paths and pins) and ``wd_q4_descriptor_materializer.materialize_descriptor`` (the
exclusive write, descriptor last), then prints exactly one compact ASCII JSON line with ``descriptor path`` and
``descriptor sha256``: the pair an operator hands to the launcher as ``-Q4SharedDescriptorPath`` /
``-Q4SharedDescriptorSha256`` (exported as WD_Q4_SHARED_DESCRIPTOR_PATH + WD_Q4_SHARED_DESCRIPTOR_SHA256 after the
launcher's own validation). The output carries no secret, source pin, source path or plan content; this module writes nothing to stderr, and a
stdout that cannot be written (closed pipe) ends the run with exit 1 and no output at all.

Refusals print ``{"error":"descriptor_cli_refused","reason":<closed reason>}`` and exit 1; the reason names the
stage (argv, request, source, fixed_plan, descriptor, materialize, unexpected) and never a caller value, a path, a
byte or exception text. Every file (request and sources) is opened only after its path passed the single-separator
discipline (no forward slash, no doubled backslash, no colon after the drive, no empty, dot or dot-dot component, no
component ending in a dot or a space and no component whose stem is a reserved device name such as CON, PRN, AUX,
NUL, COM0-9, LPT0-9, CONIN$ or CONOUT$, which Windows would silently alias or open as a device),
its chain was walked for reparse points and the opened handle was compared with the checked path (same device and
file identity, regular, no reparse attribute, same size); the drive root itself is not inspected. The fixed-plan pin
is recomputed from the plan bytes before it is pinned into the descriptor. The values that the fixed plan and the
descriptor both describe (interpreter sha256, runtime manifest path and sha256, runtime root, generation and
revision in the descriptor's outer block, each key present) must be equal in the request, otherwise the request is
refused before any source is read: a generation whose two documents disagree would only fail at run time. A
KeyboardInterrupt ends the run with exit 130 and a SystemExit with its own non-zero integer code (a zero, None or
non-integer code becomes 1, so exit 0 always means the pair was printed); neither writes a line of its own, also
when raised while the line is written, but an interrupt that lands while a refusal or success line is being
written may leave that line whole or as a partial line on stdout, so a caller accepts output only with exit 0. An
interrupt during the write phase may leave a partial generation directory, which the next run refuses as existing.
Default-off behaviour is
preserved at every layer: each producer is called with
``enabled=True`` only inside the enabled branch, and the launcher still validates the pair itself. Boundaries:
no install, no activation of a lane, no repin of a bundle and no read of sealed data; the operator decides
whether and when a materialized generation is used. A matching hash is not authentication, not freshness and
not a runtime proof.
"""
import hashlib
import json
import os
import re
import stat
import sys

REQUEST_SCHEMA = "wd.q4-descriptor-cli-request.v1"
REASONS = frozenset({"argv_refused", "request_refused", "source_refused", "fixed_plan_refused", "descriptor_refused",
                     "materialize_refused", "unexpected_refused"})
LIMITS = {"request": 65536, "fleet": 1048576, "binding": 65536, "snapshot": 16 * 1024 * 1024}
REFUSAL_PREFIX = '{"error":"descriptor_cli_refused","reason":"'
_REQUEST_KEYS = frozenset({"schema", "target_dir", "generation", "fleet_path", "fleet_sha256", "binding_path",
                           "binding_sha256", "snapshot_path", "snapshot_sha256", "lanes", "fixed_plan", "descriptor"})
_FIXED_PLAN_KEYS = frozenset({
    "interpreter_sha256", "peer_code_sha256", "verifier_sha256", "runtime_manifest_sha256", "bundle_sha256",
    "expected_pointer_sha256", "policy_sha256", "roster_sha256", "expected_public_policy_sha256",
    "expected_source_sha256", "expected_encoder_sha256", "runtime_generation", "runtime_revision", "runtime_root",
    "runtime_manifest_path", "bundle_bytes", "approved_tasks"})
_DESCRIPTOR_KEYS = frozenset({
    "bundle_root", "public_plan", "interpreter", "interpreter_sha256", "interpreter_max_bytes", "cwd",
    "timeout_seconds", "max_output_bytes", "runtime_manifest_path", "runtime_manifest_sha256", "outer",
    "expected_binding"})
_SOURCES = (("fleet", "fleet_path", "fleet_sha256"), ("binding", "binding_path", "binding_sha256"),
            ("snapshot", "snapshot_path", "snapshot_sha256"))
_SHARED_PINS = (("interpreter_sha256", "interpreter_sha256"), ("runtime_manifest_sha256", "runtime_manifest_sha256"),
                ("runtime_manifest_path", "runtime_manifest_path"))
_SHARED_OUTER_PINS = (("runtime_root", "runtime-root"), ("runtime_generation", "runtime-generation"),
                      ("runtime_revision", "runtime-revision"))
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_DRIVE = re.compile(r"[A-Za-z]:\\")
_UNPRINTABLE = re.compile(r"[^\x20-\x7E]")
_BAD_PARTS = frozenset({"", ".", ".."})
_DEVICE = re.compile(r"(?i)(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9]|CONIN\$|CONOUT\$) *(?:\.|\Z)")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_READ_FLAGS = os.O_RDONLY | getattr(os, "O_BINARY", 0)


class _Refused(Exception):
    """Closed reason only."""


def _refuse(reason):
    raise _Refused(reason) from None


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _pairs(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            raise ValueError()
        document[key] = value
    return document


def _constant(_value):
    raise ValueError()


def _check_path(path, reason):
    """Single-separator discipline: drive-absolute, backslashes only, no colon after the drive, printable ASCII,
    no empty, dot or dot-dot component (the walk below could otherwise skip an ancestor), no component ending in a dot
    or a space and no component whose stem is a reserved device name (Windows would alias the first two and open the
    third as a device, so none of them is ever handed to a file system call)."""
    if type(path) is not str or not _DRIVE.match(path) or "\\\\" in path or "/" in path or ":" in path[2:]:
        _refuse(reason)
    if _UNPRINTABLE.search(path):
        _refuse(reason)
    for part in path[3:].split("\\"):
        if part in _BAD_PARTS or part[-1] in ". " or _DEVICE.match(part):
            _refuse(reason)


def _read_regular(path, limit, reason):
    """Walk the chain for reparse points, open, compare the handle with the checked path, read within the cap."""
    _check_path(path, reason)
    try:
        current = path
        while True:
            info = os.lstat(current)
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & _REPARSE:
                _refuse(reason)
            parent = current.rsplit("\\", 1)[0]
            if parent == current or len(parent) <= 2:
                break
            current = parent
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
            _refuse(reason)
        handle = os.open(path, _READ_FLAGS)
        try:
            opened = os.fstat(handle)
            if (not stat.S_ISREG(opened.st_mode) or getattr(opened, "st_file_attributes", 0) & _REPARSE
                    or (opened.st_dev, opened.st_ino, opened.st_size) != (info.st_dev, info.st_ino, info.st_size)):
                _refuse(reason)
            chunks, remaining = [], limit + 1
            while remaining > 0:
                chunk = os.read(handle, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        finally:
            os.close(handle)
    except OSError:
        _refuse(reason)
    raw = b"".join(chunks)
    if not 0 < len(raw) <= limit:
        _refuse(reason)
    return raw


def _read_pinned(path, limit, expected, reason):
    """The caller-reader discipline plus the explicit pin."""
    if type(expected) is not str or not _HEX64.match(expected):
        _refuse(reason)
    raw = _read_regular(path, limit, reason)
    if _sha256(raw) != expected:
        _refuse(reason)
    return raw


def _printable(value):
    """Every string printable ASCII, every number an integer (no float, no bool), no null, containers recursively."""
    if type(value) is str:
        return not _UNPRINTABLE.search(value)
    if value is None or type(value) is float or type(value) is bool:
        return False
    if type(value) is dict:
        return all(type(k) is str and _printable(k) and _printable(v) for k, v in value.items())
    if type(value) is list:
        return all(_printable(item) for item in value)
    return True


def _request(path):
    if type(path) is not str or not path:
        _refuse("request_refused")
    raw = _read_regular(path, LIMITS["request"], "request_refused")
    try:
        document = json.loads(raw.decode("ascii"), object_pairs_hook=_pairs, parse_constant=_constant)
    except Exception:  # noqa: BLE001 - any decode or shape failure is the same closed reason
        _refuse("request_refused")
    if type(document) is not dict or set(document) != _REQUEST_KEYS or document["schema"] != REQUEST_SCHEMA:
        _refuse("request_refused")
    if not _printable(document):
        _refuse("request_refused")
    for _name, path_key, pin_key in _SOURCES:
        if type(document[path_key]) is not str or type(document[pin_key]) is not str or not _HEX64.match(document[pin_key]):
            _refuse("request_refused")
        _check_path(document[path_key], "source_refused")  # every source path passes the discipline before any is opened
    if (type(document["target_dir"]) is not str or type(document["generation"]) is not str
            or type(document["lanes"]) is not list):
        _refuse("request_refused")
    fixed, descriptor = document["fixed_plan"], document["descriptor"]
    if type(fixed) is not dict or set(fixed) != _FIXED_PLAN_KEYS:
        _refuse("request_refused")
    if type(descriptor) is not dict or set(descriptor) != _DESCRIPTOR_KEYS:
        _refuse("request_refused")
    outer = descriptor["outer"]
    if type(outer) is not dict:
        _refuse("request_refused")
    for fixed_key, descriptor_key in _SHARED_PINS:
        if fixed[fixed_key] != descriptor[descriptor_key]:
            _refuse("request_refused")
    for fixed_key, outer_key in _SHARED_OUTER_PINS:
        if outer_key not in outer or fixed[fixed_key] != outer[outer_key]:
            _refuse("request_refused")
    return document


def _compose(document):
    from tools import wd_q4_descriptor_materializer as materializer
    from tools import wd_q4_fixed_plan_producer as fixed_plan_producer
    from tools import wd_q4_shared_descriptor_producer as descriptor_producer

    sources = {}
    for name, path_key, pin_key in _SOURCES:
        sources[name] = _read_pinned(document[path_key], LIMITS[name], document[pin_key], "source_refused")
    try:
        fixed = fixed_plan_producer.build_fixed_plan(enabled=True, **document["fixed_plan"])
        if (type(fixed) is not dict or set(fixed) != {"plan_bytes", "plan_sha256"} or type(fixed["plan_bytes"]) is not bytes
                or _sha256(fixed["plan_bytes"]) != fixed["plan_sha256"]):
            raise ValueError()
    except Exception:  # noqa: BLE001 - FixedPlanRefused or any shape failure: one closed reason
        _refuse("fixed_plan_refused")
    try:
        plan = materializer.plan_generation_paths(document["target_dir"], document["generation"])
    except Exception:  # noqa: BLE001
        _refuse("materialize_refused")
    try:
        built = descriptor_producer.build_shared_descriptor(
            enabled=True, fleet_bytes=sources["fleet"], expected_fleet_sha256=document["fleet_sha256"],
            lanes=document["lanes"], binding_path=plan["binding_path"], binding_sha256=_sha256(sources["binding"]),
            fixed_plan_path=plan["fixed_plan_path"], fixed_plan_sha256=fixed["plan_sha256"],
            snapshot_path=plan["snapshot_path"], snapshot_sha256=_sha256(sources["snapshot"]),
            **document["descriptor"])
        if type(built) is not dict or set(built) != {"descriptor_bytes", "descriptor_sha256"}:
            raise ValueError()
    except Exception:  # noqa: BLE001 - DescriptorRefused, a duplicate keyword or any shape failure
        _refuse("descriptor_refused")
    try:
        out = materializer.materialize_descriptor(
            enabled=True, target_dir=document["target_dir"], generation=document["generation"],
            artifacts={"binding": sources["binding"], "fixed_plan": fixed["plan_bytes"], "snapshot": sources["snapshot"]},
            descriptor_bytes=built["descriptor_bytes"], expected_descriptor_sha256=built["descriptor_sha256"])
    except materializer.MaterializeRefused:
        _refuse("materialize_refused")
    if (type(out) is not dict or out.get("status") != "MATERIALIZED"
            or out.get("descriptor_sha256") != built["descriptor_sha256"]
            or type(out.get("descriptor_path")) is not str or out["descriptor_path"] != plan["descriptor_path"]):
        _refuse("unexpected_refused")
    return {"descriptor_path": out["descriptor_path"], "descriptor_sha256": out["descriptor_sha256"]}


def _write(text):
    """True when the line reached stdout. A stdout that cannot be written is pointed at the null device so the
    interpreter's exit flush stays silent; nothing is ever written to stderr."""
    try:
        sys.stdout.write(text)
        sys.stdout.flush()
        return True
    except (OSError, ValueError, AttributeError):
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except Exception:  # noqa: BLE001 - best effort only
            pass
        return False


def _run(argv):
    try:
        argv = list(argv)
        if argv == []:
            return 0 if _write('{"status":"OFF"}\n') else 1
        if len(argv) not in (2, 3) or argv[0] != "--request" or type(argv[1]) is not str or not argv[1]:
            _refuse("argv_refused")
        if len(argv) == 2:
            return 0 if _write('{"status":"OFF"}\n') else 1
        if argv[2] != "--enable":
            _refuse("argv_refused")
        pair = _compose(_request(argv[1]))
        return 0 if _write(json.dumps(pair, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n") else 1
    except _Refused as refused:
        reason = refused.args[0] if refused.args and refused.args[0] in REASONS else "unexpected_refused"
        _write(REFUSAL_PREFIX + reason + '"}\n')
        return 1
    except KeyboardInterrupt:
        return 130
    except SystemExit as exit_:
        return exit_.code if type(exit_.code) is int and exit_.code != 0 else 1
    except BaseException:  # noqa: BLE001 - no detail of any kind leaves the process
        _write(REFUSAL_PREFIX + 'unexpected_refused"}\n')
        return 1


def main(argv=None):
    """The guard around the guard: an interrupt or exit raised while a refusal line is written inside a handler of
    ``_run`` would otherwise escape the process with a diagnostic on the error stream; here it still ends with 130,
    its own non-zero integer code or 1, and the guard itself writes nothing."""
    try:
        if argv is None:
            argv = sys.argv[1:]
        return _run(argv)
    except KeyboardInterrupt:
        return 130
    except SystemExit as exit_:
        return exit_.code if type(exit_.code) is int and exit_.code != 0 else 1
    except BaseException:  # noqa: BLE001 - nothing is written here, the write itself may be what failed
        return 1


if __name__ == "__main__":
    sys.exit(main())
