"""Default-off, caller-pinned site staging; NOT an installer or closure proof.

Only explicit, per-file mappings from two independently identity-pinned roots
are copied to a new audit directory. Same-user filesystem races, ACLs, source
provenance, dynamic imports and native DLL closure remain the caller's duty.
An error after output creation may leave partial data: reconcile manually;
never automatically retry, delete, or activate. Free-space checks are sampled,
not a reservation against other writers or filesystem overhead. Preflight
exceptions and interruptions become a fixed refusal; after output creation is
attempted they become output-state unknown. Caller frames are not sanitized.
"""
import hashlib
import os
from pathlib import Path
import shutil

from sandbox.mind.step2 import runtime_builder, runtime_manifest as manifest
from tools import build_q4_runtime_candidate as candidate, build_q4_runtime_plan as planner

_FLOOR = 7 * 1024**3 // 2
_CHUNK = 1024 * 1024


class OverlayRefused(ValueError):
    """Request refused before output creation was attempted."""


class OverlayOutputStateUnknown(RuntimeError):
    """Output creation was attempted; caller must reconcile any partial output."""


def _hash_exact(path, expected_size):
    """Reject an oversized opened file before reading, then bind bytes and identity."""
    manifest._check_chain(path)
    manifest._check_no_named_streams(path)
    stream, initial = manifest._open_regular(path, expected_size)
    with stream:
        if initial.st_size != expected_size:
            raise ValueError("input_size_mismatch")
        raw = stream.raw  # Avoid buffered prefetch outside the pinned length.
        digest = hashlib.sha256()
        remaining = expected_size
        while remaining:
            block = raw.read(min(_CHUNK, remaining))
            if not block:
                raise ValueError("input_changed")
            digest.update(block)
            remaining -= len(block)
        if raw.read(1) or not manifest._same_file(path, stream, initial):
            raise ValueError("input_changed")
        return initial.st_size, digest.hexdigest(), manifest._stable_identity(initial)


def stage(*, enabled=False, roots=None, root_pins=None, mapping=None, output=None, max_bytes=None):
    """Return a small receipt or a sanitized, phase-specific refusal."""
    if enabled is False:
        return {"status": "OFF"}
    state = [False]
    result = None
    try:
        result = _stage(enabled=enabled, roots=roots, root_pins=root_pins, mapping=mapping,
                        output=output, max_bytes=max_bytes, _state=state)
    except BaseException:
        pass
    if result is not None:
        return result
    enabled = roots = root_pins = mapping = output = max_bytes = None
    if state[0]:
        raise OverlayOutputStateUnknown("site_overlay_output_state_unknown") from None
    raise OverlayRefused("site_overlay_refused") from None


def _stage(*, enabled, roots, root_pins, mapping, output, max_bytes, _state):
    if (enabled is not True or type(roots) is not dict or set(roots) != {"base", "cpu"}
            or type(root_pins) is not dict or set(root_pins) != set(roots)
            or type(mapping) is not list or type(max_bytes) is not int or max_bytes < 0):
        raise ValueError("inputs_invalid")
    checked = {key: planner._root(value) for key, value in roots.items()}
    for key, root in checked.items():
        pin = root_pins[key]
        if (type(pin) is not dict or set(pin) != {"device", "inode"}
                or type(pin["device"]) is not int or type(pin["inode"]) is not int
                or planner._identity(root) != (pin["device"], pin["inode"])):
            raise ValueError("root_pin_mismatch")
    if planner._related(checked["base"], checked["cpu"]) or planner._physically_related(checked["base"], checked["cpu"]):
        raise ValueError("root_overlap")
    target = candidate._new_path(output)
    if ".codex-audit" not in (part.casefold() for part in target.parts):
        raise ValueError("audit_root_required")
    for root in checked.values():
        identity = planner._identity(root)
        if (planner._related(root, target)
                or any(planner._identity(parent) == identity for parent in (target.parent, *target.parent.parents))):
            raise ValueError("output_overlap")

    records, items = [], []
    for item in mapping:
        if (type(item) is not dict or set(item) != {"root", "source", "target", "size", "sha256"}
                or item["root"] not in checked or type(item["source"]) is not str
                or type(item["target"]) is not str):
            raise ValueError("mapping_invalid")
        source_parts = manifest._path_parts("site/" + item["source"])[1:]
        target_parts = manifest._path_parts("site/" + item["target"])[1:]
        records.append({"path": "site/" + item["target"], "size": item["size"], "sha256": item["sha256"]})
        items.append((item, checked[item["root"]].joinpath(*source_parts), target_parts))
    files, dirs = manifest._validate_records(records)
    total = sum(size for size, _ in files.values())
    if total > max_bytes or shutil.disk_usage(target.parent).free < _FLOOR + total:
        raise ValueError("budget_or_disk_floor")
    identities = {}
    for item, source, _ in items:
        size, digest, identity = _hash_exact(source, item["size"])
        if (size, digest) != (item["size"], item["sha256"].lower()):
            raise ValueError("input_pin_mismatch")
        identities[item["target"]] = identity

    _state[0] = True  # mkdir can succeed even when the caller observes a failure.
    os.mkdir(target)
    for item, source, parts in sorted(items, key=lambda entry: entry[0]["target"]):
        destination = target.joinpath(*parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        manifest._check_chain(destination.parent)
        manifest._check_chain(source)
        manifest._check_no_named_streams(source)
        stream, initial = manifest._open_regular(source, item["size"])
        with stream:
            if manifest._stable_identity(initial) != identities[item["target"]]:
                raise ValueError("input_changed")
            digest = hashlib.sha256()
            remaining = item["size"]
            raw = stream.raw
            with destination.open("xb") as out:
                while remaining:
                    block = raw.read(min(_CHUNK, remaining))
                    if not block:
                        raise ValueError("input_changed")
                    if shutil.disk_usage(destination.parent).free < _FLOOR + len(block):
                        raise ValueError("disk_floor")
                    if out.write(block) != len(block):
                        raise ValueError("short_write")
                    digest.update(block)
                    remaining -= len(block)
                if raw.read(1) or not manifest._same_file(source, stream, initial):
                    raise ValueError("input_changed")
                out.flush()
                os.fsync(out.fileno())
            if digest.hexdigest() != item["sha256"].lower():
                raise ValueError("input_changed")
    expected_files = {name[len("site/"):] for name in files}
    expected_dirs = {name[len("site/"):] for name in dirs if name != "site"}
    if manifest._tree(target) != (expected_files, expected_dirs):
        raise ValueError("output_membership_mismatch")
    for item, source, parts in items:
        size, digest, _ = _hash_exact(target.joinpath(*parts), item["size"])
        if (size, digest) != (item["size"], item["sha256"].lower()):
            raise ValueError("output_pin_mismatch")
        if _hash_exact(source, item["size"]) != (size, digest, identities[item["target"]]):
            raise ValueError("input_changed")
    for key, root in checked.items():
        if planner._identity(root) != (root_pins[key]["device"], root_pins[key]["inode"]):
            raise ValueError("root_pin_mismatch")
    return {"status": "STAGED_UNVERIFIED", "files": len(files), "bytes": total}
