"""Bridge v2 F0 durable revocation and freeze state: offline library writer.

Library only. No production caller, CLI or scheduled task invokes it, and it writes only
when a trusted caller passes an explicit absolute runtime root. It publishes exactly the
schema that ``tools/bridge_v2_activation.py`` evaluates (``wd.bridge-v2-revocation.v1``),
and every existing state it accepts and every state it publishes is read through that
evaluator's own loader.

Every transition runs under one bounded OS lock, uses a strictly increasing version,
publishes atomically (exclusive temp file, fsync, read-back, compare-and-swap against the
bytes read under the lock, os.replace, directory fsync where the OS supports it) and
verifies the result through the evaluator. A refused transition publishes nothing. The
compare-and-swap re-read narrows, but cannot close, a race with a writer that ignores the
lock.

* ``initialize_frozen`` publishes ``frozen=true`` bound to one exact, validated policy.
  It never replaces an existing file silently: replacing requires the SHA-256 of the
  exact existing bytes, keeps those bytes as evidence, and still publishes frozen, so it
  can never grant anything. An unreadable existing state also needs the caller's known
  version high-water mark, so the new version stays above anything the caller accepted.
  Without a READABLE previous state (first initialization, missing or corrupt file) every
  feature the policy declares starts REVOKED: a lost denial can never come back as a
  grant, and each feature needs its own authorized ``unrevoke`` (RCO2 R1). A readable
  previous state keeps its revocations.
* ``freeze`` and ``revoke`` are deny-only. They need no authorization, and they carry the
  previous ``updated_utc`` forward: a deny action never refreshes the freshness that the
  policy's ``revocation_max_age_seconds`` checks, so it cannot re-enable a stale state.
  They refuse when the state is missing, corrupt or bound to another policy (the
  evaluator already denies everything then); use ``initialize_frozen``.
* ``unfreeze``, ``unrevoke`` and ``reattest`` are grants. They refuse unless the caller
  passes an ``OperatorAuthorization`` bound to this exact policy digest, current version,
  SHA-256 of the current state bytes (so a reused version number cannot replay a grant,
  RCO2 R2), action and feature set, unexpired and short-lived, AND a provenance verifier
  injected by the trusted caller returns exactly ``True`` for it. No verifier ships with
  this module, so no operational grant is executable until a provenance adapter is
  reviewed. A string such as "operator" is never an authorization. A grant is single-use:
  it binds the current state, which the grant itself replaces. Only ``reattest`` refreshes
  ``updated_utc``; ``unfreeze`` and ``unrevoke`` carry it forward, so they never silently
  re-attest a stale state (RCO2 R3).

In-process capabilities are not a security boundary against code running in the same
process. The authenticity of an operator grant must come from the reviewed provenance
adapter, never from this module. Not runtime-tested: written under the operator's
no-runs directive (2026-09-29).
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import stat
import time

if __package__:
    from . import bridge_v2_activation as activation
else:  # loaded by path: take the sibling evaluator by exact path, never via sys.path
    _spec = importlib.util.spec_from_file_location(
        "bridge_v2_activation", Path(__file__).resolve().with_name("bridge_v2_activation.py"))
    activation = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(activation)

RECEIPT_SCHEMA = "wd.bridge-v2-revocation-receipt.v1"
LOCK_RELATIVE = Path("bridge_v2") / "revocation.lock"
DEFAULT_LOCK_TIMEOUT = 10.0
MAX_LOCK_TIMEOUT = 60.0
LOCK_POLL_SECONDS = 0.05
MAX_EVIDENCE_BYTES = 16 * 1024 * 1024
REPLACE_ATTEMPTS = 20
MAX_AUTHORIZATION_LIFETIME = timedelta(minutes=15)
GRANT_ACTIONS = ("unfreeze", "unrevoke", "reattest")
NO_MAX_AGE = {"revocation_max_age_seconds": None}  # the evaluator's loader without the freshness window


class RevocationError(activation.ActivationError):
    """A transition was refused; nothing was published."""


class StateMissing(RevocationError):
    """No revocation state exists (the evaluator denies everything)."""


class StateInvalid(RevocationError):
    """The existing state is corrupt, unreadable or bound to another policy."""


class AuthorizationRefused(RevocationError):
    """A grant lacked a valid, verified operator authorization."""


class PublicationError(RevocationError):
    """Publication failed BEFORE the replace: nothing was published (``published`` is False)."""
    published = False


class PublishedButUnverified(PublicationError):
    """Raised AFTER the replace: the new bytes ARE live (``published`` is True), but the
    directory fsync, the read-back or the evaluator check failed. Re-read the on-disk state;
    the evaluator fails closed on anything it cannot parse (RCO2 N2)."""
    published = True


@dataclass(frozen=True)
class OperatorAuthorization:
    """Capability a trusted caller builds from reviewed operator provenance.

    ``provenance`` is opaque here; only the injected verifier interprets it.
    """
    action: str
    policy_sha256: str
    from_version: int
    from_state_sha256: str
    features: tuple
    expires_utc: str
    provenance: object


# ---------------------------------------------------------------------------
# Small strict helpers
# ---------------------------------------------------------------------------

def _now(now: datetime | None) -> datetime:
    current = datetime.now(timezone.utc) if now is None else now
    if not isinstance(current, datetime) or current.tzinfo is None:
        raise RevocationError("transition time must be a timezone-aware datetime")
    return current.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _digest(value: str) -> str:
    if not isinstance(value, str) or not activation.HEX64.fullmatch(value):
        raise RevocationError("policy_sha256 must be 64 lowercase hex characters")
    return value


def _positive_int(value, what: str) -> int:
    if type(value) is not int or value < 1:
        raise RevocationError(what + " must be an integer >= 1")
    return value


def _feature_set(features) -> tuple:
    if not isinstance(features, (list, tuple)) or not features:
        raise RevocationError("features must be a non-empty list of feature names")
    for name in features:
        if not isinstance(name, str) or not activation.FEATURE_NAME.fullmatch(name):
            raise RevocationError("unknown feature name: " + str(name)[:32])
    return tuple(sorted(set(features)))


def _paths(runtime_root) -> tuple[Path, Path, Path, Path]:
    root = Path(runtime_root)
    if not root.is_absolute():
        raise RevocationError("runtime_root must be an absolute path")
    if not root.is_dir():
        raise RevocationError("runtime_root does not exist")
    return root, root / "bridge_v2", root / activation.REVOCATION_RELATIVE, root / LOCK_RELATIVE


def _refuse_reparse(path: Path, what: str) -> None:
    """A symlink or reparse point (junction) under the runtime root could redirect the lock,
    the state or its directory; refuse it (RCO2 N3). A missing path is fine."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise StateInvalid(what + " cannot be inspected: " + type(exc).__name__) from exc
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise StateInvalid(what + " is a symlink or reparse point")


def _check_local_paths(directory: Path, state_path: Path, lock_path: Path) -> None:
    _refuse_reparse(directory, "bridge_v2 directory")
    _refuse_reparse(state_path, "revocation state")
    _refuse_reparse(lock_path, "revocation lock")


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return  # no directory fsync through Python on Windows; NTFS journals the rename
    fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _replace(source: Path, target: Path) -> None:
    """os.replace with a short bounded retry: on Windows a concurrent reader's open handle
    (Python opens without FILE_SHARE_DELETE) makes the rename fail transiently."""
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt + 1 == REPLACE_ATTEMPTS:
                raise
            time.sleep(LOCK_POLL_SECONDS)


def _read_raw_or_none(path: Path, limit: int = MAX_EVIDENCE_BYTES) -> bytes | None:
    try:
        with path.open("rb") as stream:
            data = stream.read(limit + 1)
    except FileNotFoundError:
        return None
    except OSError as exc:  # a directory, a locked or unreadable path: never trusted
        raise StateInvalid("revocation state path is unreadable: " + type(exc).__name__) from exc
    if len(data) > limit:
        raise StateInvalid("revocation state file exceeds " + str(limit) + " bytes")
    return data


# ---------------------------------------------------------------------------
# Bounded OS lock
# ---------------------------------------------------------------------------

if os.name == "nt":
    import msvcrt

    def _try_lock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


@contextmanager
def _locked(lock_path: Path, timeout):
    if type(timeout) not in (int, float) or not 0 < timeout <= MAX_LOCK_TIMEOUT:
        raise RevocationError("lock timeout must be a number in (0, 60] seconds")
    _refuse_reparse(lock_path.parent, "bridge_v2 directory")
    _refuse_reparse(lock_path, "revocation lock")
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                _try_lock(fd)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RevocationError("revocation lock is busy; bounded wait expired") from None
                time.sleep(LOCK_POLL_SECONDS)
        try:
            yield
        finally:
            _unlock(fd)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Reading and publishing through the evaluator
# ---------------------------------------------------------------------------

def _parse_state(raw: bytes, runtime_root: Path) -> dict:
    """The exact state the evaluator accepts (freshness window neutralised), else StateInvalid."""
    if len(raw) > activation.MAX_FILE_BYTES:
        raise StateInvalid("revocation state exceeds the evaluator's size cap")
    try:
        state = json.loads(raw.decode("utf-8"), object_pairs_hook=activation._unique_pairs,
                           parse_constant=activation._reject_constant)
        if not isinstance(state, dict):
            raise activation.ActivationError("revocation state must be an object")
        _digest(state.get("policy_sha256"))
        updated = activation._parse_utc(state.get("updated_utc"), "revocation updated_utc")
        verified = activation.load_revocation(runtime_root, NO_MAX_AGE, state["policy_sha256"], updated)
    except (UnicodeError, json.JSONDecodeError, RecursionError, activation.ActivationError) as exc:
        raise StateInvalid("revocation state rejected: " + str(exc)[:160]) from exc
    if verified != state:
        raise StateInvalid("revocation state changed while it was read")
    return state


def _preserve_evidence(directory: Path, raw: bytes) -> Path:
    path = directory / ("revocation.replaced-" + hashlib.sha256(raw).hexdigest() + ".bin")
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    except FileExistsError:
        if _read_raw_or_none(path) != raw:
            raise PublicationError("an evidence file with different bytes already exists") from None
        return path
    try:
        _write_all(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_directory(directory)
    return path


def _publish(runtime_root: Path, directory: Path, state_path: Path, state: dict,
             previous_raw: bytes | None) -> tuple[bytes, str]:
    data = (json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                       allow_nan=False) + "\n").encode("ascii")
    temp = directory / ("revocation.json.tmp-" + secrets.token_hex(8))
    published = False
    try:
        fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        try:
            _write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        if _read_raw_or_none(temp) != data:
            raise PublicationError("temporary state read-back differs from the intended bytes")
        # Compare-and-swap: the file must still hold exactly the bytes read under the lock.
        if _read_raw_or_none(state_path) != previous_raw:
            raise RevocationError("revocation state changed outside the lock; refusing to publish")
        _replace(temp, state_path)
        published = True
    finally:
        if not published:
            try:
                temp.unlink()
            except OSError:
                pass  # a leftover temp file is inert; never mask the original error
    # From here on the new bytes are live: every failure says so (PublishedButUnverified).
    try:
        _fsync_directory(directory)
        if _read_raw_or_none(state_path) != data:
            raise PublishedButUnverified("published state read-back differs from the intended bytes")
        verified = activation.load_revocation(
            runtime_root, NO_MAX_AGE, state["policy_sha256"],
            activation._parse_utc(state["updated_utc"], "revocation updated_utc"), min_version=state["version"])
    except PublishedButUnverified:
        raise
    except (OSError, activation.ActivationError) as exc:
        raise PublishedButUnverified("published, but verification failed: " + type(exc).__name__ + ": "
                                     + str(exc)[:160]) from exc
    if verified != state:
        raise PublishedButUnverified("published, but the evaluator reads a different state")
    return data, hashlib.sha256(data).hexdigest()


def _receipt(action: str, previous: dict | None, previous_raw: bytes | None, state: dict,
             sha256: str, now: datetime, evidence: Path | None = None) -> dict:
    return {"schema": RECEIPT_SCHEMA, "action": action,
            "previous_version": previous["version"] if previous else None,
            "previous_sha256": hashlib.sha256(previous_raw).hexdigest() if previous_raw is not None else None,
            "version": state["version"], "sha256": sha256, "policy_sha256": state["policy_sha256"],
            "frozen": state["frozen"], "revoked": list(state["revoked"]), "updated_utc": state["updated_utc"],
            "transition_utc": _stamp(now), "evidence_path": str(evidence) if evidence else None}


def read_state(runtime_root, policy_sha256: str) -> dict:
    """Read-only: the current state exactly as the evaluator parses it, bound to this policy."""
    root, directory, state_path, lock_path = _paths(runtime_root)
    _check_local_paths(directory, state_path, lock_path)
    raw = _read_raw_or_none(state_path)
    if raw is None:
        raise StateMissing("no revocation state exists")
    state = _parse_state(raw, root)
    if state["policy_sha256"] != _digest(policy_sha256):
        raise StateInvalid("revocation state is bound to another policy")
    return state


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------

def initialize_frozen(runtime_root, policy: dict, policy_sha256: str, *, now: datetime | None = None,
                      replace_existing_sha256: str | None = None, min_version: int | None = None,
                      lock_timeout=DEFAULT_LOCK_TIMEOUT) -> dict:
    """Publish frozen=true bound to one exact validated policy; never a grant."""
    activation.validate_policy(policy)
    if activation.canonical_sha256(policy) != _digest(policy_sha256):
        raise RevocationError("policy_sha256 is not the canonical digest of this policy")
    if min_version is not None:
        _positive_int(min_version, "min_version")
    current_time = _now(now)
    root, directory, state_path, lock_path = _paths(runtime_root)
    directory.mkdir(exist_ok=True)
    with _locked(lock_path, lock_timeout):
        _refuse_reparse(state_path, "revocation state")
        raw = _read_raw_or_none(state_path)
        # R1: without a readable previous state, every declared feature starts revoked.
        previous, revoked, evidence = None, sorted(policy["features"]), None
        if raw is None:
            if replace_existing_sha256 is not None:
                raise RevocationError("nothing to replace: no revocation state exists")
            version = 1 if min_version is None else min_version + 1
        else:
            if replace_existing_sha256 is None:
                raise RevocationError("a revocation state exists; initialization never replaces it silently")
            if replace_existing_sha256 != hashlib.sha256(raw).hexdigest():
                raise RevocationError("the existing state differs from the bytes named for replacement")
            try:
                previous = _parse_state(raw, root)
            except StateInvalid:
                previous = None
            if previous is not None:
                version = previous["version"] + 1
                revoked = sorted(set(previous["revoked"]))  # keep every existing denial
            elif min_version is None:
                raise RevocationError("replacing an unreadable state needs the caller's known version high-water mark")
            else:
                version = min_version + 1
            if min_version is not None and version <= min_version:
                version = min_version + 1
            evidence = _preserve_evidence(directory, raw)
        state = {"schema": activation.REVOCATION_SCHEMA, "version": version, "policy_sha256": policy_sha256,
                 "frozen": True, "revoked": revoked, "updated_utc": _stamp(current_time)}
        _data, sha256 = _publish(root, directory, state_path, state, raw)
        return _receipt("initialize_frozen", previous, raw, state, sha256, current_time, evidence)


def _transition(action: str, runtime_root, policy_sha256: str, change, *, now, expected_version,
                lock_timeout, authorization=None, verifier=None, features: tuple = ()) -> dict:
    _digest(policy_sha256)
    if expected_version is not None:
        _positive_int(expected_version, "expected_version")
    current_time = _now(now)
    root, directory, state_path, lock_path = _paths(runtime_root)
    if not directory.is_dir():
        raise StateMissing("no revocation state exists; use initialize_frozen")
    with _locked(lock_path, lock_timeout):
        _refuse_reparse(state_path, "revocation state")
        raw = _read_raw_or_none(state_path)
        if raw is None:
            raise StateMissing("no revocation state exists; use initialize_frozen")
        current = _parse_state(raw, root)
        if current["policy_sha256"] != policy_sha256:
            raise StateInvalid("revocation state is bound to another policy; use initialize_frozen")
        if expected_version is not None and current["version"] != expected_version:
            raise RevocationError("revocation version changed (compare-and-swap refused)")
        grant = action in GRANT_ACTIONS
        if grant:
            _check_authorization(authorization, verifier, action, policy_sha256, current, raw, features,
                                 current_time)
        state = change(dict(current, revoked=sorted(set(current["revoked"]))))
        state["version"] = current["version"] + 1
        state["revoked"] = sorted(set(state["revoked"]))
        # Only an authorized reattest attests freshness (R3); every other transition carries it forward.
        state["updated_utc"] = _stamp(current_time) if action == "reattest" else current["updated_utc"]
        _data, sha256 = _publish(root, directory, state_path, state, raw)
        return _receipt(action, current, raw, state, sha256, current_time)


def _check_authorization(authorization, verifier, action: str, policy_sha256: str, current: dict,
                         raw: bytes, features: tuple, now: datetime) -> None:
    if type(authorization) is not OperatorAuthorization:
        raise AuthorizationRefused("an OperatorAuthorization capability is required; strings and look-alikes are refused")
    if verifier is None or not callable(verifier):
        raise AuthorizationRefused("no reviewed provenance verifier was injected; grants are refused by default")
    if (authorization.action != action or authorization.policy_sha256 != policy_sha256
            or type(authorization.from_version) is not int or authorization.from_version != current["version"]
            or type(authorization.from_state_sha256) is not str
            or authorization.from_state_sha256 != hashlib.sha256(raw).hexdigest()
            or type(authorization.features) is not tuple or authorization.features != features):
        raise AuthorizationRefused("authorization is bound to another action, policy, state or feature set")
    try:
        expires = activation._parse_utc(authorization.expires_utc, "authorization expires_utc")
    except activation.ActivationError as exc:
        raise AuthorizationRefused(str(exc)) from exc
    if not now < expires <= now + MAX_AUTHORIZATION_LIFETIME:
        raise AuthorizationRefused("authorization is expired or outlives the 15-minute maximum")
    try:
        verdict = verifier(authorization)
    except Exception as exc:  # a failing verifier is a refusal, never a pass
        raise AuthorizationRefused("provenance verifier failed: " + type(exc).__name__) from exc
    if verdict is not True:
        raise AuthorizationRefused("provenance verifier did not return exactly True")


def freeze(runtime_root, policy_sha256: str, *, now: datetime | None = None,
           expected_version: int | None = None, lock_timeout=DEFAULT_LOCK_TIMEOUT) -> dict:
    """Deny-only: freeze every feature."""
    return _transition("freeze", runtime_root, policy_sha256, lambda s: dict(s, frozen=True),
                       now=now, expected_version=expected_version, lock_timeout=lock_timeout)


def revoke(runtime_root, policy_sha256: str, features, *, now: datetime | None = None,
           expected_version: int | None = None, lock_timeout=DEFAULT_LOCK_TIMEOUT) -> dict:
    """Deny-only: add features to the revoked set."""
    names = _feature_set(features)
    return _transition("revoke", runtime_root, policy_sha256,
                       lambda s: dict(s, revoked=sorted(set(s["revoked"]) | set(names))),
                       now=now, expected_version=expected_version, lock_timeout=lock_timeout)


def unfreeze(runtime_root, policy_sha256: str, *, authorization, verifier, now: datetime | None = None,
             lock_timeout=DEFAULT_LOCK_TIMEOUT) -> dict:
    """Grant: lift the freeze. Refused without a verified operator authorization."""
    return _transition("unfreeze", runtime_root, policy_sha256, lambda s: dict(s, frozen=False),
                       now=now, expected_version=None, lock_timeout=lock_timeout,
                       authorization=authorization, verifier=verifier, features=())


def unrevoke(runtime_root, policy_sha256: str, features, *, authorization, verifier,
             now: datetime | None = None, lock_timeout=DEFAULT_LOCK_TIMEOUT) -> dict:
    """Grant: remove currently revoked features. Refused without a verified operator authorization."""
    names = _feature_set(features)

    def change(state):
        missing = [name for name in names if name not in state["revoked"]]
        if missing:
            raise RevocationError("features are not revoked: " + ",".join(missing))
        return dict(state, revoked=[name for name in state["revoked"] if name not in names])

    return _transition("unrevoke", runtime_root, policy_sha256, change, now=now, expected_version=None,
                       lock_timeout=lock_timeout, authorization=authorization, verifier=verifier, features=names)


def reattest(runtime_root, policy_sha256: str, *, authorization, verifier, now: datetime | None = None,
             lock_timeout=DEFAULT_LOCK_TIMEOUT) -> dict:
    """Grant: re-attest freshness (updated_utc) without other changes. Refused without authorization."""
    return _transition("reattest", runtime_root, policy_sha256, lambda s: dict(s), now=now,
                       expected_version=None, lock_timeout=lock_timeout,
                       authorization=authorization, verifier=verifier, features=())
