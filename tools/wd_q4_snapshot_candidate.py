"""Default-off, caller-pinned filesystem capture; not a verified Mind snapshot.

The caller independently authorizes the root, every relative path and every
digest, and protects that root, the fixed-volume files and the checkpoint's
persistent lock against noncooperating replacement. No additional root or pin
is added. All reads occur while holding the same cooperative slot as checkpoint
writers. This is not authentication, freshness, query validation, native FAISS
validation, or protection from hostile same-user filesystem races, including
lock ABA or replacement of a file after its bytes were read. The returned
bundle can contain sensitive bytes: keep it private. Only the fixed peer can
subsequently verify an actual query. There is no CLI or live integration here.
"""
import hashlib
import hmac
import re
from pathlib import Path, PureWindowsPath

from sandbox.mind.step2 import shared_index_checkpoint as checkpoint
from sandbox.mind.step2.reader_slot import reader_slot
from tools import wd_q4_secure_transport as transport

_HEX = re.compile(r"[0-9a-f]{64}\Z")
_MAX_RAW = 8 * 1024 * 1024
_MAX_MAP = 2048
_KEYS = {"pointer_bytes", "policy_bytes", "roster_bytes", "public_policy_bytes",
         "active_artifact_bytes", "history_bytes", "cell_snapshots"}


class CandidateRefused(RuntimeError):
    """Fixed error that does not echo caller-provided data."""


def _relative(descriptor):
    if type(descriptor) is not dict or set(descriptor) != {"path", "sha256"}:
        raise ValueError()
    path, digest = descriptor["path"], descriptor["sha256"]
    if type(path) is not str or not path or type(digest) is not str or not _HEX.fullmatch(digest):
        raise ValueError()
    pure = PureWindowsPath(path)
    if (pure.is_absolute() or pure.drive or pure.root or not pure.parts or
            any(part in (".", "..") or part.endswith((" ", ".")) or ":" in part for part in pure.parts)):
        raise ValueError()
    return Path(*pure.parts), digest


def _capture(*, root, descriptors, enabled=False):
    """Capture exact caller-pinned files, returning CANDIDATE_UNVERIFIED only.

    Every descriptor has exactly {path, sha256}; paths are relative to the
    caller-authorized root. The pointer's sibling checkpoint lock must already
    exist. A changed/missing file or mismatching hash refuses the entire capture.
    Native or semantic verification is deliberately deferred to the fixed peer.
    """
    try:
        if enabled is not True or type(descriptors) is not dict or set(descriptors) != _KEYS:
            raise ValueError()
        root = checkpoint._secure(root)
        if not root.is_dir():
            raise ValueError()
        pointer, _ = _relative(descriptors["pointer_bytes"])
        if pointer != Path(checkpoint.POINTER):
            raise ValueError()
        lock = checkpoint._secure(root / checkpoint.LOCK)
        if not lock.is_file():
            raise ValueError()
        lock_stat = lock.stat()
        used = [0]

        def read(descriptor):
            rel, digest = _relative(descriptor)
            remaining = _MAX_RAW - used[0]
            if remaining < 0:
                raise ValueError()
            raw = checkpoint._read(root / rel, max_bytes=remaining)
            used[0] += len(raw)
            if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), digest):
                raise ValueError()
            return raw

        def read_map(mapping):
            if type(mapping) is not dict or len(mapping) > _MAX_MAP or any(type(k) is not str for k in mapping):
                raise ValueError()
            return {key: read(value) for key, value in mapping.items()}

        with reader_slot(lock, timeout_seconds=0.2):
            checkpoint._secure(lock)
            locked_stat = lock.stat()
            if (locked_stat.st_dev, locked_stat.st_ino) != (lock_stat.st_dev, lock_stat.st_ino):
                raise ValueError()
            parts = {key: read(descriptors[key]) for key in
                     ("pointer_bytes", "policy_bytes", "roster_bytes", "public_policy_bytes")}
            parts["active_artifact_bytes"] = read_map(descriptors["active_artifact_bytes"])
            parts["history_bytes"] = read_map(descriptors["history_bytes"])
            cells = descriptors["cell_snapshots"]
            if type(cells) is not dict or len(cells) > _MAX_MAP:
                raise ValueError()
            parts["cell_snapshots"] = {}
            for key, value in cells.items():
                if type(key) is not str or type(value) is not dict or set(value) != {"pointer", "log", "artifacts"}:
                    raise ValueError()
                parts["cell_snapshots"][key] = {"pointer": read(value["pointer"]), "log": read(value["log"]),
                                                "artifacts": read_map(value["artifacts"])}
            checkpoint._secure(lock)
            after = lock.stat()
            if (after.st_dev, after.st_ino) != (locked_stat.st_dev, locked_stat.st_ino):
                raise ValueError()
            bundle = transport.fixed_snapshot_bundle(**parts)
            return {"status": "CANDIDATE_UNVERIFIED", "bundle_bytes": bundle,
                    "bundle_sha256": hashlib.sha256(bundle).hexdigest()}
    except Exception:
        pass
    raise CandidateRefused("snapshot_candidate_refused") from None


def capture(*, root, descriptors, enabled=False):
    """Return a private unverified candidate, or a sanitized refusal.

    The refusal traceback for this outer frame does not retain the supplied
    root, descriptors or captured bytes. Caller frames and other exceptions
    outside this boundary are not sanitized.
    """
    result = None
    try:
        result = _capture(root=root, descriptors=descriptors, enabled=enabled)
    except Exception:
        pass
    finally:
        del root, descriptors, enabled
    if result is not None:
        return result
    raise CandidateRefused("snapshot_candidate_refused") from None
