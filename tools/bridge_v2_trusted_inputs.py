#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Bridge v2: the external trusted-caller provenance adapter for F0 decisions (F15/F16).

Default OFF and fixture-only. Nothing in the runtime imports this module; it launches
nothing, writes no bridge event and grants nothing. It builds the ``evidence.f0`` block
that ``tools/bridge_v2_switch_evidence.switch_activation`` validates (switch interface
contract, section 3, "REQUIRED and NOT BUILT"), from inputs whose provenance it checks:

* The pins come from ONE operator activation packet (schema
  ``wd.bridge-v2-activation-packet.v1``), read as exact bytes whose SHA-256 the caller
  supplies out of band: ``trusted_policy_sha256``, ``expected_head``, ``expected_tree``, the
  revocation floor, the features it covers and its validity window. The packet is never
  the activation config it authorizes, and no pin is ever derived from that config (for
  example from ``signature.policy_sha256``): the file must not authorize itself.
* The deployed bundle must agree with the packet: the deployment manifest's bytes hash to
  the externally anchored manifest digest (the installer's final pair), the packet names
  that same digest, and the manifest's ``source_commit`` equals the packet's head. A file
  merely being installed, a valid bootstrap or a live process is never a pin.
* ``min_revocation_version`` is the persisted high-water mark: the larger of the stored mark
  and the packet floor. It only ratchets up, through a compare-and-swap under an exclusive
  OS lock. After F0 decided, ONE locked step re-reads the durable mark: a decision below a
  mark another caller raised meanwhile is refused, and a decision above it advances it
  (RCO1 N8). A missing mark is initialized from the packet floor; a corrupt one refuses.
* The activation config and the revocation state are read ONCE, as exact bytes, and F0
  decides on those same bytes (``activation.evaluate_bytes``, RCO1 N9): the decision, the
  returned ``document`` and the returned ``revocation`` all come from that one read, so no swap
  of a file around a second read (an A-B-A) can make them disagree. A file changed after the
  read is simply not part of this decision; the next call reads it.

Residual limit, stated rather than claimed solved (RCO1 N10):

* The mark is only as durable as its file: deleting it resets the floor to the packet's
  ``min_revocation_version`` (so each signed packet must carry a current floor), and on Windows
  ``os.replace`` without a directory flush can be lost on power failure, with the same effect.

Every failure raises ``TrustRefusal`` with a stable ``code``. A disabled Decision is still
returned as evidence (the switch adapter refuses it first). The adapter never enables a
feature: the shipped activation config keeps every feature off and unsigned.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat as stat_module
from typing import Any, Callable, Iterator, Mapping
import uuid

from tools import bridge_v2_activation as activation

PACKET_SCHEMA = "wd.bridge-v2-activation-packet.v1"
HIGH_WATER_SCHEMA = "wd.bridge-v2-revocation-high-water.v1"
HIGH_WATER_RELATIVE = Path("bridge_v2") / "revocation_high_water.json"
PACKET_KEYS = frozenset({"schema", "trusted_policy_sha256", "expected_head", "expected_tree",
                         "deployment_manifest_sha256", "min_revocation_version", "features",
                         "issued_utc", "expires_utc"})
HIGH_WATER_KEYS = frozenset({"schema", "version", "updated_utc"})
POINTER_KEYS = ("source_commit", "final_commit", "manifest_sha256", "final_manifest_sha256", "active_bundle")
SHA256_ANY_CASE = re.compile(r"[0-9a-fA-F]{64}")
MAX_PACKET_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_POINTER_BYTES = 64 * 1024
MAX_HIGH_WATER_BYTES = 4 * 1024
MAX_PACKET_LIFETIME = timedelta(days=31)


class TrustRefusal(Exception):
    """The trusted inputs cannot be established; ``code`` is a stable reason."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _refuse(condition: bool, code: str) -> None:
    if not condition:
        raise TrustRefusal(code)


def _aware_utc(moment: Any) -> datetime:
    """Exactly a datetime with ONE offset read that is exactly a timedelta; no astimezone."""
    _refuse(type(moment) is datetime, "time_unknown")
    try:
        offset = moment.utcoffset()
        current = None if type(offset) is not timedelta else \
            (moment.replace(tzinfo=None) - offset).replace(tzinfo=timezone.utc)
    except Exception:  # noqa: BLE001 - a broken tzinfo or an unrepresentable time is not a time
        current = None
    _refuse(current is not None, "time_unknown")
    return current


def _read_bytes(path: Path, limit: int, code: str) -> bytes:
    """Bounded bytes of one regular, non-link file; the opened file must be the inspected one."""
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        raise TrustRefusal(code + "_missing") from None
    except (OSError, ValueError):
        raise TrustRefusal(code + "_unreadable") from None
    reparse = getattr(before, "st_file_attributes", 0) & getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    _refuse(stat_module.S_ISREG(before.st_mode) and not reparse, code + "_not_a_regular_file")
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except (OSError, ValueError):
        raise TrustRefusal(code + "_unreadable") from None
    try:
        opened = os.fstat(descriptor)
        _refuse(stat_module.S_ISREG(opened.st_mode) and (opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                code + "_changed_while_opening")
        chunks, size = [], 0
        while size <= limit:
            chunk = os.read(descriptor, min(65536, limit + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
    except OSError:
        raise TrustRefusal(code + "_unreadable") from None
    finally:
        os.close(descriptor)
    _refuse(size <= limit, code + "_oversized")
    return b"".join(chunks)


def _read_exact(path: Path, expected_sha256: Any, limit: int, code: str) -> bytes:
    """The file's bytes, only when they hash to the digest the caller obtained out of band."""
    _refuse(isinstance(expected_sha256, str) and SHA256_ANY_CASE.fullmatch(expected_sha256) is not None,
            code + "_digest_invalid")
    raw = _read_bytes(path, limit, code)
    _refuse(hashlib.sha256(raw).hexdigest() == expected_sha256.lower(), code + "_digest_mismatch")
    return raw


def _strict_json(raw: bytes, code: str) -> Any:
    """F0's own parsing rules: strict UTF-8 (no BOM), no duplicate key, no NaN or Infinity."""
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=activation._unique_pairs,
                          parse_constant=activation._reject_constant)
    except (UnicodeError, ValueError, RecursionError, activation.ActivationError):
        raise TrustRefusal(code + "_malformed") from None


def _time(value: Any, code: str) -> datetime:
    try:
        return activation._parse_utc(value, code)
    except activation.ActivationError:
        raise TrustRefusal(code + "_malformed") from None


def load_packet(packet_path: Path, packet_sha256: Any, now: datetime) -> dict:
    """The operator activation packet, verified against its out-of-band digest and window."""
    packet = _strict_json(_read_exact(Path(packet_path), packet_sha256, MAX_PACKET_BYTES, "packet"), "packet")
    _refuse(isinstance(packet, dict) and set(packet) == PACKET_KEYS and packet["schema"] == PACKET_SCHEMA,
            "packet_malformed")
    _refuse(isinstance(packet["trusted_policy_sha256"], str)
            and activation.HEX64.fullmatch(packet["trusted_policy_sha256"]) is not None
            and all(isinstance(packet[key], str) and activation.HEX40.fullmatch(packet[key]) is not None
                    for key in ("expected_head", "expected_tree"))
            and isinstance(packet["deployment_manifest_sha256"], str)
            and SHA256_ANY_CASE.fullmatch(packet["deployment_manifest_sha256"]) is not None
            and activation._is_int(packet["min_revocation_version"]) and packet["min_revocation_version"] >= 1,
            "packet_malformed")
    features = packet["features"]
    _refuse(isinstance(features, list) and features and len(features) == len(set(features))
            and all(isinstance(name, str) and activation.FEATURE_NAME.fullmatch(name) for name in features),
            "packet_malformed")
    issued = _time(packet["issued_utc"], "packet_issued_utc")
    expires = _time(packet["expires_utc"], "packet_expires_utc")
    _refuse(issued < expires <= issued + MAX_PACKET_LIFETIME, "packet_window_invalid")
    _refuse(issued - now <= activation.MAX_FUTURE_SKEW, "packet_from_the_future")
    _refuse(now < expires, "packet_expired")
    return packet


def read_deployment_anchor(pointer_path: Path) -> dict:
    """The installer's recorded final pair (WD_REBOOT_STATE_CURRENT.json), consistent in itself.

    This is recorded operator input, not authentication: it only tells the caller which
    manifest digest and bundle the packet must match."""
    pointer = _strict_json(_read_bytes(Path(pointer_path), MAX_POINTER_BYTES, "pointer").removeprefix(b"\xef\xbb\xbf"),
                           "pointer")
    _refuse(isinstance(pointer, dict) and all(isinstance(pointer.get(key), str) for key in POINTER_KEYS),
            "pointer_malformed")
    commit, manifest = pointer["final_commit"], pointer["final_manifest_sha256"]
    _refuse(activation.HEX40.fullmatch(commit) is not None and SHA256_ANY_CASE.fullmatch(manifest) is not None
            and pointer["source_commit"] == commit and pointer["manifest_sha256"].lower() == manifest.lower(),
            "pointer_inconsistent")
    return {"commit": commit, "manifest_sha256": manifest.lower(),
            "manifest_path": Path(pointer["active_bundle"]) / "deployment-manifest.json"}


def verify_deployment(manifest_path: Path, manifest_anchor_sha256: Any, packet: dict) -> None:
    """The deployed manifest is the anchored one, the packet names it, and it carries the pinned head."""
    _refuse(isinstance(manifest_anchor_sha256, str) and SHA256_ANY_CASE.fullmatch(manifest_anchor_sha256) is not None
            and manifest_anchor_sha256.lower() == packet["deployment_manifest_sha256"].lower(),
            "deployment_anchor_mismatch")
    raw = _read_exact(Path(manifest_path), manifest_anchor_sha256, MAX_MANIFEST_BYTES, "deployment_manifest")
    # PowerShell writes the manifest; one optional UTF-8 BOM is tolerated, nothing else is.
    manifest = _strict_json(raw[3:] if raw.startswith(b"\xef\xbb\xbf") else raw, "deployment_manifest")
    _refuse(isinstance(manifest, dict) and manifest.get("source_commit") == packet["expected_head"],
            "deployment_head_mismatch")


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    """A non-blocking exclusive OS lock that the OS releases when the process dies."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
    except OSError:
        raise TrustRefusal("high_water_unwritable") from None
    locked = False
    try:
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked = True
        except OSError:
            raise TrustRefusal("high_water_locked") from None
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass  # closing the descriptor below releases the lock as well
        os.close(descriptor)


class HighWaterStore:
    """The durable, monotonic revocation high-water mark (compare-and-swap under a lock)."""

    def __init__(self, runtime_root: Path) -> None:
        self.path = Path(runtime_root) / HIGH_WATER_RELATIVE
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def read(self) -> int | None:
        """The stored version, or None when no mark exists yet. Anything else refuses."""
        try:
            raw = _read_bytes(self.path, MAX_HIGH_WATER_BYTES, "high_water")
        except TrustRefusal as refusal:
            if refusal.code == "high_water_missing":
                return None
            raise
        record = _strict_json(raw, "high_water")
        _refuse(isinstance(record, dict) and set(record) == HIGH_WATER_KEYS and record["schema"] == HIGH_WATER_SCHEMA
                and activation._is_int(record["version"]) and record["version"] >= 1, "high_water_corrupt")
        _time(record["updated_utc"], "high_water_updated_utc")
        return record["version"]

    def advance(self, expected: int | None, new: int, now: datetime) -> int:
        """Replace ``expected`` with ``new`` atomically; never lowers the mark, never races a writer."""
        _refuse(activation._is_int(new) and new >= 1 and (expected is None or activation._is_int(expected)),
                "high_water_value_invalid")
        with _exclusive_lock(self.lock_path):
            current = self.read()
            _refuse(current == expected, "high_water_cas_conflict")
            _refuse(current is None or new >= current, "high_water_rollback")
            if new == current:
                return current
            self._write_locked(new, now)
            return new

    def settle_after_decision(self, pin: int, version: Any, now: datetime) -> int:
        """ONE locked step after F0 decided with ``pin`` (RCO1 N8): the durable mark must still be at least
        ``pin``; if another caller raised it meanwhile, a decision below the new mark is refused; a decision
        above it advances it. Returns the mark after the step."""
        with _exclusive_lock(self.lock_path):
            durable = self.read()
            _refuse(durable is not None and durable >= pin, "high_water_rollback")
            if durable > pin and not (activation._is_int(version) and version >= durable):
                raise TrustRefusal("high_water_advanced_during_decision")
            if activation._is_int(version) and version > durable:
                self._write_locked(version, now)
                return version
            return durable

    def _write_locked(self, new: int, now: datetime) -> None:
        """Temp file, fsync, atomic replace; the caller holds the lock."""
        stamp = _aware_utc(now).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        payload = json.dumps({"schema": HIGH_WATER_SCHEMA, "version": new, "updated_utc": stamp},
                             sort_keys=True).encode("utf-8") + b"\n"
        temporary = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
            try:
                view = memoryview(payload)
                while view:
                    written = os.write(descriptor, view)
                    _refuse(written > 0, "high_water_unwritable")   # a zero-progress write never spins (RCO1 N10)
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.replace(temporary, self.path)
        except OSError:
            raise TrustRefusal("high_water_unwritable") from None
        finally:
            if temporary.exists():
                try:
                    os.unlink(temporary)
                except OSError:
                    pass


def load_trusted_inputs(feature: str, *, packet_path: Path, packet_sha256: Any, manifest_path: Path,
                        manifest_anchor_sha256: Any, config_path: Path, runtime_root: Path, now: Any,
                        environ: Mapping[str, str] | None = None,
                        evaluate_bytes: Callable[..., activation.Decision] | None = None) -> dict:
    """Assemble ``evidence.f0`` = {decision, pins, document, revocation} for one feature.

    Raises TrustRefusal. ``evaluate_bytes`` defaults to F0's own and is injectable only for tests."""
    _refuse(isinstance(feature, str) and activation.FEATURE_NAME.fullmatch(feature) is not None, "feature_unknown")
    current = _aware_utc(now)
    config_path, runtime_root = Path(config_path), Path(runtime_root)
    _refuse(os.path.normcase(os.path.abspath(packet_path)) != os.path.normcase(os.path.abspath(config_path)),
            "packet_is_the_config")
    packet = load_packet(Path(packet_path), packet_sha256, current)
    _refuse(feature in packet["features"], "packet_feature_not_authorized")
    verify_deployment(Path(manifest_path), manifest_anchor_sha256, packet)

    # The rollback floor is durable BEFORE the decision: a later crash cannot lower it.
    store = HighWaterStore(runtime_root)
    stored = store.read()
    pin = max(stored or 0, packet["min_revocation_version"])
    if stored is None or pin > stored:
        store.advance(stored, pin, current)

    revocation_path = runtime_root / activation.REVOCATION_RELATIVE
    config_raw = _read_bytes(config_path, activation.MAX_FILE_BYTES, "config")
    revocation_raw = _read_bytes(revocation_path, activation.MAX_FILE_BYTES, "revocation")
    document = _strict_json(config_raw, "config")
    revocation = _strict_json(revocation_raw, "revocation")
    # F0 decides on the SAME captured bytes the evidence returns (RCO1 N9): no second read of the paths,
    # so no A-B-A swap around a read can make the decision differ from the returned document and state.
    decision = (activation.evaluate_bytes if evaluate_bytes is None else evaluate_bytes)(
        feature, config_bytes=config_raw, revocation_bytes=revocation_raw,
        trusted_policy_sha256=packet["trusted_policy_sha256"], now=current, environ=environ,
        min_revocation_version=pin, expected_head=packet["expected_head"], expected_tree=packet["expected_tree"])
    _refuse(type(decision) is activation.Decision and decision.feature == feature, "decision_invalid")
    # The durable floor is re-read and advanced in ONE locked step; a decision below a floor another caller
    # raised meanwhile is refused (RCO1 N8).
    store.settle_after_decision(pin, decision.revocation_version, current)
    return {"decision": asdict(decision),
            "pins": {"trusted_policy_sha256": packet["trusted_policy_sha256"],
                     "expected_head": packet["expected_head"], "expected_tree": packet["expected_tree"],
                     "min_revocation_version": pin},
            "document": document, "revocation": revocation}
