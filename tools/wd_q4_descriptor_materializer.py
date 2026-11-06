"""Default-off, exclusive-write materializer for ONE ``wd.q4-shared-session-config.v2`` generation on disk.

The producers stay in memory by design: ``tools/wd_q4_shared_descriptor_producer.py`` returns descriptor bytes
plus their sha256 and says that the caller writes the bytes and pins the digest independently; the fixed plan
(``tools/wd_q4_fixed_plan_producer.py``), the lane binding and the metadata-only public snapshot are bytes too.
The launcher (``start-wd-agent.ps1`` through ``Get-WdQ4SharedSessionConfig.ps1``) and the lane caller read only
WD_Q4_SHARED_DESCRIPTOR_PATH + WD_Q4_SHARED_DESCRIPTOR_SHA256 and then the files the descriptor pins. This module
is that missing writer and nothing more; it is default-off and every value is an explicit caller argument.

What it does. ``plan_generation_paths`` is pure (no I/O) and returns the deterministic file paths of one
generation under the caller's target directory. ``materialize_descriptor`` checks that the descriptor bytes are one
strict canonical ASCII JSON object of the v2 schema whose artifact paths (binding, fixed plan, snapshot) are exactly
the planned paths and whose artifact pins are exactly the sha256 of the supplied artifact bytes (the runtime manifest
path and pin are operator arguments checked by the launcher validator, not here), that the target directory exists with no
reparse point anywhere on its chain, and that the generation directory does not exist yet. It then creates the
directory, records the (device, file id) identity of the target directory and of the new generation directory, and
publishes each file with no part file and no os.replace: the final file itself is opened with O_EXCL (an existing
file at that path, however it got there, is a refusal and its bytes are never touched), written, fsynced, re-read
and re-hashed; the descriptor is written last so a descriptor on disk always names files that are already complete.
The target chain is walked again before every write for reparse points and both directory identities are compared
with the recorded ones; any difference (a swapped parent, a junction or symlink in place of the generation
directory, another directory under the same name) is a conservative refusal that writes nothing more.
Nothing may exist at a final path before its create (a dangling link included, because the C runtime would follow
it), the created handle must be the file found at that path afterwards, and the descriptor is created only after
every artifact has been re-read byte for byte at the last re-read immediately before the descriptor create; the
returned digests and sizes come from the re-read bytes. A volume that reports no file identity (file id 0) is
refused; a volume clone remounted under the same letter with the same serial and file ids, or a share that reuses
file ids, defeats the identity compare and is outside this module.
It never overwrites and never deletes (a failed generation directory is left for inspection), never logs and never
reads the environment. Residual, stated plainly: the system calls between a recheck and its write, and between the last
artifact re-read and the descriptor create (a writer inside the generation directory can replace artifact bytes
there and the descriptor would still name the verified digests); a writer inside the target directory who swaps the
generation directory between mkdir and the identity sample, or who plants the caller's own descriptor bytes after a
refusal, is outside this module: the target directory's access control is
that boundary and stays the launcher validator's and the operator's job. The launcher validator re-checks the chain
again when it reads.
Every refusal is one closed reason and never a caller value, path or byte.

What it is not. A matching hash is not authentication, not freshness and not a runtime proof. Metadata-only content
is the snapshot producer's responsibility (``sandbox/mind/step2/shared_public_producer.py`` and its public policy
filter); this writer is content-agnostic beyond JSON shape, size and pins. The runtime manifest is produced and
pinned elsewhere (``runtime_manifest.py``): the descriptor references it, this module does not write it. There is
no scheduler, no installer, no activation and no enable-flag change here; the operator, or a caller that passes
``enabled=True`` explicitly, decides when a generation is materialized and with which path/pin pair a launcher is
started. Fixed-drive and ACL checks need the launcher validator and stay its job.
"""
import hashlib
import json
import os
import re
import stat

SCHEMA = "wd.q4-shared-session-config.v2"
ARTIFACTS = ("binding", "fixed_plan", "snapshot")
FILE_NAMES = {"binding": "lane-binding.v1.json", "fixed_plan": "fixed-plan.v2.json",
              "snapshot": "public-snapshot.v1.json", "descriptor": "shared-session-config.v2.json"}
LIMITS = {"binding": 65536, "fixed_plan": 4096, "snapshot": 16 * 1024 * 1024, "descriptor": 65536}
REASONS = frozenset({"disabled", "path_refused", "generation_refused", "exists_refused", "artifact_refused",
                     "descriptor_refused", "pin_refused", "write_refused"})
MAX_PATH_CHARS = 512
_DESCRIPTOR_KEYS = frozenset({
    "schema", "fleet_sha256", "lanes", "bundle_root", "public_plan", "interpreter", "cwd",
    "binding_path", "fixed_plan_path", "snapshot_path", "runtime_manifest_path",
    "interpreter_sha256", "binding_sha256", "fixed_plan_sha256", "snapshot_sha256",
    "runtime_manifest_sha256", "interpreter_max_bytes", "timeout_seconds", "max_output_bytes",
    "outer", "expected_binding",
})
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_DRIVE = re.compile(r"[A-Za-z]:\\")
_DEVICE = re.compile(r"(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?\Z", re.IGNORECASE | re.DOTALL)
_BAD_CHARS = re.compile(r'[\x00-\x1f<>|"*?/]')
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
_READ_FLAGS = os.O_RDONLY | getattr(os, "O_BINARY", 0)


class MaterializeRefused(Exception):
    """Closed reason only; never carries a caller value."""


def _refuse(reason):
    raise MaterializeRefused(reason) from None


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _part_ok(part):
    return (part != "" and part not in (".", "..") and part[-1] not in ". "
            and not _DEVICE.match(part))


def _target_ok(value):
    if type(value) is not str or not 4 <= len(value) <= MAX_PATH_CHARS or not value.isascii():
        return False
    if not _DRIVE.match(value) or _BAD_CHARS.search(value) or ":" in value[2:]:
        return False
    parts = value.split("\\")
    return len(parts) >= 2 and all(_part_ok(part) for part in parts[1:])


def plan_generation_paths(target_dir, generation):
    """Pure: the deterministic paths of one generation under ``target_dir``, or ``MaterializeRefused``."""
    if not _target_ok(target_dir):
        _refuse("path_refused")
    if type(generation) is not str or not _IDENTITY.match(generation):
        _refuse("generation_refused")
    generation_dir = target_dir + "\\" + generation
    plan = {"generation_dir": generation_dir}
    for name in (*ARTIFACTS, "descriptor"):
        plan[name + "_path"] = generation_dir + "\\" + FILE_NAMES[name]
    if any(len(path) > MAX_PATH_CHARS for path in plan.values()):
        _refuse("path_refused")
    return plan


def _artifacts_ok(artifacts):
    if type(artifacts) is not dict or set(artifacts) != set(ARTIFACTS):
        _refuse("artifact_refused")
    for name in ARTIFACTS:
        data = artifacts[name]
        if type(data) is not bytes or not 1 <= len(data) <= LIMITS[name]:
            _refuse("artifact_refused")
    return {name: artifacts[name] for name in ARTIFACTS}


def _pairs(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            _refuse("descriptor_refused")
        document[key] = value
    return document


def _document(raw):
    if type(raw) is not bytes or not 1 <= len(raw) <= LIMITS["descriptor"]:
        _refuse("descriptor_refused")
    try:
        document = json.loads(raw.decode("ascii"), object_pairs_hook=_pairs, parse_constant=_bad_constant)
    except MaterializeRefused:
        raise
    except Exception:  # noqa: BLE001 - any decode or shape failure is the same closed reason
        _refuse("descriptor_refused")
    if type(document) is not dict or set(document) != _DESCRIPTOR_KEYS:
        _refuse("descriptor_refused")
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                           allow_nan=False).encode("ascii")
    if canonical != raw or document["schema"] != SCHEMA:
        _refuse("descriptor_refused")
    return document


def _bad_constant(_value):
    _refuse("descriptor_refused")


def _is_reparse(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & _REPARSE)


def _directory_identity(path):
    """(device, file id) of a plain directory with no reparse attribute; anything else is a path refusal."""
    try:
        info = os.lstat(path)
    except OSError:
        _refuse("path_refused")
    if not stat.S_ISDIR(info.st_mode) or _is_reparse(info) or not info.st_ino:
        _refuse("path_refused")
    return (info.st_dev, info.st_ino)


def _chain_ok(target_dir):
    """Target is a plain directory and no ancestor is a reparse point; returns the target identity."""
    identity = _directory_identity(target_dir)
    current = target_dir
    try:
        while True:
            parent = current.rsplit("\\", 1)[0]
            if parent == current or len(parent) <= 2:
                break
            current = parent
            if _is_reparse(os.lstat(current)):
                _refuse("path_refused")
    except OSError:
        _refuse("path_refused")
    return identity


def _still_the_same(target_dir, target_identity, generation_dir, generation_identity):
    """Re-identify the target chain and the generation directory before every write (RCO2 M2)."""
    if _chain_ok(target_dir) != target_identity or _directory_identity(generation_dir) != generation_identity:
        _refuse("path_refused")


def _read_back(final, size):
    """The bytes at ``final`` (at most ``size`` + 1, so a longer file is detected), regular file only."""
    try:
        handle = os.open(final, _READ_FLAGS)
        try:
            if not stat.S_ISREG(os.fstat(handle).st_mode):
                _refuse("write_refused")
            chunks, remaining = [], size + 1
            while remaining > 0:
                chunk = os.read(handle, remaining)
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        finally:
            os.close(handle)
    except OSError:
        _refuse("write_refused")
    return b"".join(chunks)


def _write_exclusively(final, payload):
    """Create the final file itself exclusively (RCO2 M1: no part file, no replace), fsync, bind, re-read, compare.

    Nothing may exist at ``final`` first (a dangling link would be followed by the C runtime's exclusive create);
    after the write the file found at ``final`` must be the one that was created.
    """
    try:
        try:
            os.lstat(final)
        except FileNotFoundError:
            pass
        else:
            _refuse("write_refused")
        handle = os.open(final, _CREATE_FLAGS, 0o600)
        try:
            created = os.fstat(handle)
            view = memoryview(payload)
            while view:
                view = view[os.write(handle, view):]
            os.fsync(handle)
        finally:
            os.close(handle)
        placed = os.lstat(final)
    except OSError:
        _refuse("write_refused")
    if (not stat.S_ISREG(created.st_mode) or not stat.S_ISREG(placed.st_mode) or _is_reparse(placed) or not placed.st_ino
            or (placed.st_dev, placed.st_ino) != (created.st_dev, created.st_ino)):
        _refuse("write_refused")
    if _read_back(final, len(payload)) != payload:
        _refuse("write_refused")


def materialize_descriptor(*, enabled=False, target_dir, generation, artifacts, descriptor_bytes,
                           expected_descriptor_sha256):
    """OFF unless ``enabled is True``. Enabled: validate, then write the planned files exclusively, descriptor last.

    Returns ``{"status": "MATERIALIZED", "generation_dir", "descriptor_path", "descriptor_sha256", "files"}`` where
    ``files`` maps each artifact and ``descriptor`` to its ``path``, ``sha256`` and ``bytes`` as re-read from disk.
    The returned path/pin pair is what a launcher is started with; the pair is not authority or freshness.
    """
    if enabled is not True:
        return {"status": "OFF"}
    plan = plan_generation_paths(target_dir, generation)
    files = _artifacts_ok(artifacts)
    if type(expected_descriptor_sha256) is not str or not _HEX64.match(expected_descriptor_sha256):
        _refuse("pin_refused")
    if type(descriptor_bytes) is not bytes or not 1 <= len(descriptor_bytes) <= LIMITS["descriptor"]:
        _refuse("descriptor_refused")
    if _sha256(descriptor_bytes) != expected_descriptor_sha256:
        _refuse("pin_refused")
    document = _document(descriptor_bytes)
    for name in ARTIFACTS:
        if document[name + "_path"] != plan[name + "_path"]:
            _refuse("descriptor_refused")
    for name in ARTIFACTS:
        if document[name + "_sha256"] != _sha256(files[name]):
            _refuse("pin_refused")
    target_identity = _chain_ok(target_dir)
    generation_dir = plan["generation_dir"]
    try:
        os.lstat(generation_dir)
    except FileNotFoundError:
        pass
    except OSError:
        _refuse("path_refused")
    else:
        _refuse("exists_refused")
    try:
        os.mkdir(generation_dir)
    except FileExistsError:
        _refuse("exists_refused")
    except OSError:
        _refuse("write_refused")
    generation_identity = _directory_identity(generation_dir)
    payloads = {**files, "descriptor": descriptor_bytes}
    for name in ARTIFACTS:
        _still_the_same(target_dir, target_identity, generation_dir, generation_identity)
        _write_exclusively(plan[name + "_path"], payloads[name])
    written = {}
    _still_the_same(target_dir, target_identity, generation_dir, generation_identity)
    for name in ARTIFACTS:  # every artifact must still be in place, byte for byte, right before the descriptor
        again = _read_back(plan[name + "_path"], len(payloads[name]))
        if again != payloads[name]:
            _refuse("write_refused")
        written[name] = {"path": plan[name + "_path"], "sha256": _sha256(again), "bytes": len(again)}
    _still_the_same(target_dir, target_identity, generation_dir, generation_identity)
    _write_exclusively(plan["descriptor_path"], descriptor_bytes)
    again = _read_back(plan["descriptor_path"], len(descriptor_bytes))
    if again != descriptor_bytes:
        _refuse("write_refused")
    written["descriptor"] = {"path": plan["descriptor_path"], "sha256": _sha256(again), "bytes": len(again)}
    return {"status": "MATERIALIZED", "generation_dir": generation_dir, "descriptor_path": plan["descriptor_path"],
            "descriptor_sha256": expected_descriptor_sha256, "files": written}
