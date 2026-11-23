"""Dormant rollback core; callers must bootstrap-pin this installed code first.

This API has no CLI and is not wired to the native installer or lane launchers.
It operates only on the caller's exact reviewed file/command tuple. COMMITTED
means files plus one bounded check per lane passed, not ongoing agent readiness.
A live caller at the deadline holds the transaction; this core never kills it.
Windows power-loss durability and a production startup adapter remain separate
integration proofs; file fsync/replace alone is not such a proof.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from . import wd_mind_rollout_policy as policy


TRANSACTION_PATH = "tools-bootstrap/tools/wd_mind_rollout_transaction.py"
LANES = frozenset(("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"))
KEYS = frozenset(("schema", "transaction_id", "nonce", "installed_root", "installed_manifest_sha256",
                  "installed_commit", "source_root", "main_commit", "main_tree", "members", "targets",
                  "machine_root", "evidence_root", "pointer", "previous_hashes", "reader_path",
                  "previous_reader_key", "lanes", "launches", "lane_timeout_seconds",
                  "attempt_timeout_seconds", "recovery_timeout_seconds"))
_TOKEN = re.compile(r"[a-zA-Z0-9_-]{1,64}\Z", re.ASCII)
_TIME = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(?:\.([0-9]{1,9}))?(Z|[+-][0-9]{2}:[0-9]{2})\Z", re.ASCII)


@dataclass(frozen=True)
class Outcome:
    status: str
    journal: str
    pending: tuple[subprocess.Popen, ...] = ()
    replayed: bool = False


def _json_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _root(value) -> Path:
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise policy.Refused("An explicit absolute root is required")
    path = Path(value).absolute()
    policy._unlinked(path)
    if not path.is_dir():
        raise policy.Refused("Root is not a directory")
    return path


def _overlap(a: Path, b: Path) -> bool:
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def _location(root: Path, relative: str) -> Path:
    relative = policy.canonical_path(relative)
    path = root.joinpath(*relative.split("/"))
    policy._unlinked(path.parent)
    if not path.parent.is_dir():
        raise policy.Refused("Target parents must already exist")
    if path.exists() or path.is_symlink():
        policy._file(path)
    return path


def _existing(path: Path) -> bytes | None:
    return policy._file(path) if path.exists() else None


def _digest(data: bytes | None) -> str | None:
    return None if data is None else _sha(data)


def _replace(path: Path, data: bytes | None, nonce: str, *, phase: str) -> None:
    # The preflight and immediate pre-write check reject links and unexpected
    # parents. The controlled machine/evidence roots still require exclusive
    # ownership; this is not an OS-level race-proof directory-handle protocol.
    _location(path.parent, path.name)
    if data is None:
        path.unlink(missing_ok=True)
        return
    temp = path.with_name(path.name + ".wd-rollout-" + nonce + ".tmp")
    created = False
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _location(path.parent, path.name)
        os.replace(temp, path)
        if policy._file(path) != data:
            raise OSError("Write read-back mismatch")
    finally:
        if created and temp.exists():
            policy._unlinked(temp)
            temp.unlink()


def _write_backup(path: Path, data: bytes, nonce: str) -> None:
    _replace(path, data, nonce, phase="backup")
    if policy._file(path) != data:
        raise OSError("Backup read-back mismatch")


def _record(path: Path, data: dict, nonce: str) -> None:
    _replace(path, _json_bytes(data), nonce, phase="journal")


def _state(path: Path, data: dict, nonce: str, state: str) -> None:
    data["status"] = state
    data["states"].append(state)
    _record(path, data, nonce)


def _timestamp_ns(value: str) -> int:
    if not isinstance(value, str) or len(value) > 40 or not (match := _TIME.fullmatch(value)):
        raise policy.Refused("Invalid receipt timestamp")
    try:
        base = dt.datetime.strptime(match[1], "%Y-%m-%dT%H:%M:%S")
        zone = match[3]
        offset = 0
        if zone != "Z":
            hours, minutes = int(zone[1:3]), int(zone[4:6])
            if hours > 23 or minutes > 59:
                raise ValueError("offset")
            offset = (hours * 60 + minutes) * 60 * (1 if zone[0] == "+" else -1)
        return (calendar.timegm(base.timetuple()) - offset) * 1_000_000_000 + int((match[2] or "0").ljust(9, "0"))
    except ValueError as error:
        raise policy.Refused("Invalid receipt calendar time") from error


def _validate_launch(launch: dict) -> None:
    if not isinstance(launch, dict) or set(launch) != {"argv", "files"}:
        raise policy.Refused("Invalid launch descriptor")
    argv, files = launch["argv"], launch["files"]
    if (not isinstance(argv, list) or not 1 <= len(argv) <= 64
            or any(not isinstance(arg, str) or not arg or len(arg) > 4096 or "\0" in arg for arg in argv)
            or not Path(argv[0]).is_absolute() or not isinstance(files, dict) or argv[0] not in files):
        raise policy.Refused("Launch must pin an absolute executable and explicit files")
    folded = set()
    for name, expected in files.items():
        if not isinstance(name, str) or not Path(name).is_absolute() or name.casefold() in folded:
            raise policy.Refused("Invalid launch file set")
        folded.add(name.casefold())
        if _sha(policy._file(Path(name))) != policy._hash(expected):
            raise policy.Refused("Launch artifact hash mismatch")


def _prepare(config):
    try:
        raw = _json_bytes(config)
    except (ValueError, TypeError, RecursionError) as error:
        raise policy.Refused("Spec is not a bounded JSON object") from error
    if len(raw) > 65536:
        raise policy.Refused("Spec exceeds byte budget")
    cfg = policy._json(raw)  # Take a private snapshot; caller mutation does not change this transaction.
    if not isinstance(cfg, dict) or set(cfg) != KEYS or cfg["schema"] != "wd.mind-rollout-spec.v1":
        raise policy.Refused("Unknown or incomplete rollout spec")
    for name in ("nonce", "transaction_id"):
        if not isinstance(cfg[name], str) or not _TOKEN.fullmatch(cfg[name]):
            raise policy.Refused("Invalid transaction identity")
    for name in ("lane_timeout_seconds", "attempt_timeout_seconds", "recovery_timeout_seconds"):
        if type(cfg[name]) not in (int, float) or not 0 < cfg[name] <= 900:
            raise policy.Refused("Invalid bounded timeout")
    if (not isinstance(cfg["members"], list) or not 1 <= len(cfg["members"]) <= policy.MAX_FILES
            or any(not isinstance(name, str) for name in cfg["members"])):
        raise policy.Refused("Members must be a bounded explicit path list")
    reader = policy.canonical_path(cfg["reader_path"])
    roots = {key: _root(cfg[key]) for key in ("installed_root", "source_root", "machine_root", "evidence_root")}
    for a, b in (("machine_root", "evidence_root"), ("machine_root", "installed_root"),
                 ("machine_root", "source_root"), ("evidence_root", "installed_root")):
        if _overlap(roots[a], roots[b]):
            raise policy.Refused("Controlled roots overlap")
    trusted = policy.verify_installed_policy(roots["installed_root"], cfg["installed_manifest_sha256"], cfg["installed_commit"])
    if (Path(__file__).absolute() != roots["installed_root"].joinpath(*TRANSACTION_PATH.split("/"))
            or TRANSACTION_PATH not in trusted.files
            or _sha(policy._file(Path(__file__))) != trusted.files[TRANSACTION_PATH]):
        raise policy.Refused("Transaction must execute from the pinned installed bundle")
    closure = policy.build_tracked_closure(repo=roots["source_root"], installed_commit=cfg["installed_commit"],
                                           main_commit=cfg["main_commit"], main_tree=cfg["main_tree"], members=cfg["members"])
    if (not isinstance(cfg["targets"], dict) or set(cfg["targets"]) != set(closure.files)
            or not isinstance(cfg["previous_hashes"], dict)):
        raise policy.Refused("Targets must cover exactly the tracked closure")
    pointer = policy.canonical_path(cfg["pointer"])
    names = [*cfg["targets"].values(), pointer]
    policy._names(names)  # rejects duplicates, Windows aliases and case collisions
    if any(a != b and b.startswith(a + "/") for a in names for b in names):
        raise policy.Refused("A file target is another target's ancestor")
    if set(cfg["previous_hashes"]) != set(names):
        raise policy.Refused("Previous state hash inventory is incomplete")
    targets = {name: _location(roots["machine_root"], name) for name in names}
    for name, expected in cfg["previous_hashes"].items():
        if expected is not None:
            cfg["previous_hashes"][name] = policy._hash(expected)
    previous_reader = policy.canonical_path(cfg["previous_reader_key"])
    if reader not in closure.files or closure.files[reader] is None or previous_reader not in trusted.files:
        raise policy.Refused("Reader generation is not pinned")
    if (not isinstance(cfg["lanes"], dict) or set(cfg["lanes"]) != LANES
            or not isinstance(cfg["launches"], dict) or set(cfg["launches"]) != LANES):
        raise policy.Refused("Exactly the five reviewed lanes are required")
    fields = {"agent_uuid", "candidate_session", "candidate_run", "previous_session", "previous_run", "read_kind"}
    for lane in sorted(LANES):
        row = cfg["lanes"][lane]
        if (not isinstance(row, dict) or set(row) != fields
                or any(not isinstance(value, str) or not value.strip() or len(value) > 256 or not value.isprintable()
                       for value in row.values()) or row["read_kind"] not in ("status_projection", "semantic_recall")):
            raise policy.Refused("Invalid exact lane identity/read contract")
        _validate_launch(cfg["launches"][lane])
    pointer_bytes = _json_bytes({"schema": "wd.mind-rollout-pointer.v1", "bundle_commit": closure.main_commit,
                                "manifest_sha256": closure.manifest_sha256})
    desired = {cfg["targets"][name]: content for name, content in closure.files.items()}
    desired[pointer] = pointer_bytes
    return cfg, roots, trusted, closure, targets, desired, _sha(raw)


def _receipt(path: Path, expected: dict, started_ns: int) -> None:
    value = policy._json(policy._file(path, 16384))
    keys = set(expected) | {"schema", "receipt_observed_utc", "read_status"}
    if not isinstance(value, dict) or set(value) != keys:
        raise policy.Refused("Receipt has missing/extra fields")
    if value["schema"] != "wd.mind-rollout-startup.v1" or value["read_status"] != "success":
        raise policy.Refused("Receipt did not report the required read")
    if any(value[name] != data or type(value[name]) is not str for name, data in expected.items()):
        raise policy.Refused("Receipt identity/generation mismatch")
    if not started_ns <= _timestamp_ns(value["receipt_observed_utc"]) <= time.time_ns():
        raise policy.Refused("Receipt is stale/future relative to this caller")


def _check_lanes(cfg, roots, trusted, closure, directory, pointer, phase, deadline, record, journal,
                 active, assert_owner):
    for lane in sorted(LANES):
        assert_owner()
        if time.monotonic() >= deadline:
            return False, ()
        identity = cfg["lanes"][lane]
        previous = phase == "previous"
        expected = {
            "transaction_id": cfg["transaction_id"], "nonce": cfg["nonce"], "phase": phase, "lane": lane,
            "agent_uuid": identity["agent_uuid"], "actual_session_id": identity[phase + "_session"],
            "actual_run_id": identity[phase + "_run"], "read_kind": identity["read_kind"],
            "bundle_commit": cfg["installed_commit"] if previous else cfg["main_commit"],
            "manifest_sha256": cfg["installed_manifest_sha256"].lower() if previous else closure.manifest_sha256,
            "policy_sha256": trusted.policy_sha256,
            "reader_sha256": trusted.files[cfg["previous_reader_key"]] if previous else _sha(closure.files[cfg["reader_path"]]),
        }
        receipt = directory / (phase + "-" + lane + "-receipt.json")
        if receipt.exists() or receipt.is_symlink():
            raise policy.Refused("Receipt location already exists")
        launch = cfg["launches"][lane]
        _validate_launch(launch)
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(("AGENT_BRIDGE_", "WD_", "GIT_", "CLAUDE_CODE_", "PYTHON"))}
        env.update(WD_ROLLOUT_EXPECTED=_json_bytes(expected).decode("utf-8"),
                   WD_ROLLOUT_RECEIPT=str(receipt), WD_ROLLOUT_POINTER=str(pointer), PYTHONDONTWRITEBYTECODE="1")
        started_ns = time.time_ns()
        child = subprocess.Popen(launch["argv"], cwd=directory, env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
        active.append(child)  # Track before any fallible journal/receipt operation.
        lane_deadline = min(deadline, time.monotonic() + cfg["lane_timeout_seconds"])
        while child.poll() is None and time.monotonic() < lane_deadline:
            time.sleep(min(0.01, max(0, lane_deadline - time.monotonic())))
        code = child.poll()
        row = {"phase": phase, "lane": lane, "pid": child.pid, "exit_code": code,
               "receipt_valid": False, "ok": False, "reason": "caller_pending" if code is None else "invalid_receipt"}
        if code is None:
            record["checks"].append(row)
            _record(journal, record, cfg["nonce"])
            return False, (child,)  # Preserve the exact handle; never kill it or write underneath it.
        active.remove(child)  # Remove only after a real terminal exit was observed.
        try:
            _receipt(receipt, expected, started_ns)
            row["receipt_valid"] = True
        except (policy.Refused, OSError):
            pass
        row["ok"] = code == 0 and row["receipt_valid"] and time.monotonic() <= lane_deadline
        if row["ok"]:
            row["reason"] = "verified"
        elif code != 0:
            row["reason"] = "nonzero_caller_exit"
        elif time.monotonic() > lane_deadline:
            row["reason"] = "caller_deadline"
        record["checks"].append(row)
        _record(journal, record, cfg["nonce"])
        if not row["ok"]:
            return False, ()
    return True, ()


def run_transaction(config: dict) -> Outcome:
    """Apply one closed tuple or restore its exact prior files, pointer last.

    The private journal is an idempotence record, not a gate approval or a new
    startup measurement on replay. A nonterminal record/foreign lock is never
    stolen. The trusted launcher must pin modules before import and authorize
    the exact tuple; supplying matching hashes does not create that authority.
    """
    began = time.monotonic()
    cfg, roots, trusted, closure, targets, desired, spec_sha = _prepare(config)
    directory = roots["evidence_root"] / cfg["transaction_id"]
    journal = directory / "journal.json"
    if directory.exists():
        policy._unlinked(directory)
        old = policy._json(policy._file(journal))
        if old.get("spec_sha256") != spec_sha or old.get("status") not in ("COMMITTED", "ROLLED_BACK"):
            raise policy.Refused("Existing transaction needs explicit reconciliation")
        hashes = {name: _digest(data) for name, data in desired.items()} if old["status"] == "COMMITTED" else cfg["previous_hashes"]
        if any(_digest(_existing(targets[name])) != value for name, value in hashes.items()):
            raise policy.Refused("Terminal record does not match current files")
        return Outcome(old["status"], str(journal), replayed=True)
    if any(_digest(_existing(targets[name])) != value for name, value in cfg["previous_hashes"].items()):
        raise policy.Refused("Previous file state does not match the reviewed tuple")
    old_pointer = policy._json(policy._file(targets[cfg["pointer"]]))
    if old_pointer != {"schema": "wd.mind-rollout-pointer.v1", "bundle_commit": cfg["installed_commit"],
                       "manifest_sha256": cfg["installed_manifest_sha256"].lower()}:
        raise policy.Refused("Previous pointer generation mismatch")
    if time.monotonic() >= began + cfg["attempt_timeout_seconds"]:
        raise policy.Refused("Preflight exhausted the attempt budget")
    lock = roots["machine_root"] / ".wd-mind-rollout.lock"
    owner = _json_bytes({"transaction_id": cfg["transaction_id"], "nonce": cfg["nonce"],
                         "spec_sha256": spec_sha, "pid": os.getpid()})
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise policy.Refused("Another or unreconciled transaction owns the machine root") from error
    with os.fdopen(fd, "wb") as stream:
        stream.write(owner)
        stream.flush()
        os.fsync(stream.fileno())
    directory.mkdir()
    record = {"schema": "wd.mind-rollout-journal.v1", "spec_sha256": spec_sha,
              "transaction_id": cfg["transaction_id"], "nonce": cfg["nonce"], "states": [],
              "status": "PREPARING", "writes": [], "checks": [], "backups": [],
              "durability": "fsync_files_and_replace; Windows_directory_power_loss_not_proven"}
    backups = {}
    active = []

    def live():
        return tuple(child for child in active if child.poll() is None)

    def owns_lock():
        try:
            return policy._file(lock) == owner
        except (OSError, policy.Refused):
            return False

    def assert_owner():
        if not owns_lock():
            raise policy.Refused("Machine lock ownership changed")

    def finish(status, pending=()):
        if status in ("COMMITTED", "ROLLED_BACK"):
            # Never turn a lock-ownership failure into a restoration under a
            # different owner's lock, or forget a caller because journaling failed.
            if not owns_lock():
                return finish("ROLLBACK_FAILED_HOLD", live())
            if live():
                return finish("HOLD_ACTIVE_CALLER", live())
            _state(journal, record, cfg["nonce"], status)
            lock.unlink()
        else:
            try:
                _state(journal, record, cfg["nonce"], status)
            except (OSError, policy.Refused):
                # Existing nonterminal journal plus retained resource lock still
                # refuses replay. The returned exact handles must survive this error.
                record["journal_error"] = "hold_record_unwritable"
        return Outcome(status, str(journal), tuple(pending))

    try:
        for number, name in enumerate(sorted(targets)):
            assert_owner()
            data = _existing(targets[name])
            if _digest(data) != cfg["previous_hashes"][name]:
                raise policy.Refused("Previous state changed after preflight")
            leaf = "backup-" + str(number) + ".bin"
            if data is not None:
                _write_backup(directory / leaf, data, cfg["nonce"])
            backups[name] = data
            record["backups"].append({"path": name, "present": data is not None,
                                       "sha256": _digest(data), "file": leaf if data is not None else None})
        _state(journal, record, cfg["nonce"], "BACKUP_VERIFIED")
    except (OSError, policy.Refused):
        return finish("ROLLBACK_FAILED_HOLD")
    order = sorted(name for name in targets if name != cfg["pointer"]) + [cfg["pointer"]]
    try:
        for name in order:
            assert_owner()
            _replace(targets[name], desired[name], cfg["nonce"], phase="activate")
            record["writes"].append({"phase": "activate", "path": name})
            _record(journal, record, cfg["nonce"])
        _state(journal, record, cfg["nonce"], "CANDIDATE_ACTIVE")
        ok, pending = _check_lanes(cfg, roots, trusted, closure, directory, targets[cfg["pointer"]], "candidate",
                                   began + cfg["attempt_timeout_seconds"], record, journal, active, assert_owner)
        if pending:
            return finish("HOLD_ACTIVE_CALLER", pending)
        if ok:
            return finish("COMMITTED")
    except (OSError, policy.Refused):
        record["activation_error"] = "candidate_write_or_validation_failed"
    # These guards also cover exceptions between Popen and the ordinary return
    # of _check_lanes. Restoring underneath an untracked live caller is forbidden.
    if live():
        return finish("HOLD_ACTIVE_CALLER", live())
    if not owns_lock():
        return finish("ROLLBACK_FAILED_HOLD")
    try:
        _state(journal, record, cfg["nonce"], "ROLLING_BACK")
        for name in order:
            assert_owner()
            saved = next(row for row in record["backups"] if row["path"] == name)
            if saved["present"]:
                restored = policy._file(directory / saved["file"])
                if _sha(restored) != saved["sha256"]:
                    raise policy.Refused("Backup integrity mismatch")
            else:
                restored = None
            _replace(targets[name], restored, cfg["nonce"], phase="restore")
            if _existing(targets[name]) != backups[name]:
                raise policy.Refused("Restoration verification failed")
            record["writes"].append({"phase": "restore", "path": name})
            _record(journal, record, cfg["nonce"])
        ok, pending = _check_lanes(cfg, roots, trusted, closure, directory, targets[cfg["pointer"]], "previous",
                                   began + cfg["attempt_timeout_seconds"] + cfg["recovery_timeout_seconds"], record, journal,
                                   active, assert_owner)
        if pending:
            return finish("FILES_RESTORED_RUNTIME_UNKNOWN", pending)
        return finish("ROLLED_BACK" if ok else "ROLLBACK_FAILED_HOLD")
    except (OSError, policy.Refused):
        record["restore_error"] = "restore_or_previous_validation_failed"
        if live():
            return finish("FILES_RESTORED_RUNTIME_UNKNOWN", live())
        return finish("ROLLBACK_FAILED_HOLD")
