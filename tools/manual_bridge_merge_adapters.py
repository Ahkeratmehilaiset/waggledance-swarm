# SPDX-License-Identifier: BUSL-1.1
"""Read-only evidence adapters for the manual (a)-class merge route (MANUAL-A, G3a).

Fourth slice of the manual merge + MAGMA receipt route (operator decision
8A480508, Lead plan F9D23F10, fable-5 plan 87F9441C, bounded Lead plan
DB0CFED7 + test amendment 1A51D265).  The module is UNWIRED: neither the
preview nor the receipt module imports it, so it changes no route behaviour.

What it does, and only from its own reads:

* :func:`verify_trusted_sources` compares the bytes of the public gate modules
  this process imported with the blobs of the trusted base commit, read from
  existing local git objects (no fetch, no network);
* :func:`load_trusted_registry` reads ``configs/bridge_identity_registry.json``
  from the trusted base commit and validates it like the canonical loader;
* :func:`read_bridge_snapshot` loops the public ``read_bridge_log`` cursor
  from byte zero to a complete end of file;
* :func:`assess_controls` requires BOTH the canonical
  ``check_bridge_clear_to_merge`` (called with the trusted-base registry) and
  the stricter local withdrawal rule below to be clear;
* :func:`assess_g3a` combines them.  Its verdict is ``unknown`` or
  ``refused``; it has no admitted verdict and no admit bit.

What it never establishes (each stays UNKNOWN, never relabelled):

* authenticated session identity: the registry binds ``agent`` to
  ``agent_uuid`` only (D2);
* origin authentication of bridge rows: file identity, byte boundaries and a
  digest prove observed content, not who wrote it (D4);
* complete concept lineage: Git authors and claims are observations (D3);
* an independent exact-head RCO approval and exact-head CI (not read here).

Local withdrawal rule (D1).  Recognized-RCO controls in the negative scope are
taken in snapshot order.  A block is lifted only by a LATER ``decision`` or
``rco_review`` event of the SAME agent whose ``agent_uuid`` equals both the
block's and the trusted-base registry's, with an exact canonical clear status
(:data:`LOCAL_CLEAR_STATUSES`, a subset of the canonical clear vocabulary),
``payload.retracts_event_id`` equal to the block's event id,
``payload.exact_head`` equal to the head, and a strictly later parseable
``ts_utc``.  A later approval (``rco_pass`` or any other) never lifts a block.
Any other recognized-RCO control in scope, including free text and the G2
``finding_retracted`` vocabulary, is a block.

Evidence objects are frozen and issued only by this module.  A copy,
``dataclasses.replace``, a caller-built instance, a caller-chosen provenance
label or a mutated inner mapping is not an issued object, and every check that
depends on it is UNKNOWN.  Injected readers or runners make the evidence
``unit_mock``, which never counts.

Importing this module has no side effects beyond the repository-root
``sys.path`` entry that every ``tools`` module uses.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import MappingProxyType
from typing import Any, Callable, Mapping, Sequence
import weakref

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.check_bridge_changes_requested import check_bridge_clear_to_merge  # noqa: E402
from tools.manual_bridge_merge_receipt import (  # noqa: E402
    DECISION_TYPE,
    EVENT_TS_RE,
    PR_PAYLOAD_KEYS,
    RECOGNIZED_RCO_AGENTS,
    ReceiptError,
    bridge_event_id,
)
from tools.manual_bridge_merge_statement import RunResult, Runner  # noqa: E402
from waggledance.core.bridge_identity_registry import (  # noqa: E402
    AGENT_ID_PATTERN,
    AGENT_UUID_PATTERN,
    bridge_identity_binding_status,
)
from waggledance.core.bridge_log_reader import BridgeReadStatus, read_bridge_log  # noqa: E402

ADAPTERS_SCHEMA = "wd.manual-merge-a.g3a-adapters.v1"
CHECK_PASS = "pass"
CHECK_REFUSE = "refuse"
CHECK_UNKNOWN = "unknown"
VERDICT_REFUSED = "refused"
VERDICT_UNKNOWN = "unknown"
ADMIT_AVAILABLE = False

PROVENANCE_LOCAL_READ = "local_read"
PROVENANCE_UNIT_MOCK = "unit_mock"

REGISTRY_PATH = "configs/bridge_identity_registry.json"
# Every repository file whose code runs when the canonical gate, the registry
# helpers and the log reader are imported (closure drift-guarded by a test).
PINNED_SOURCE_PATHS: tuple[str, ...] = (
    "tools/__init__.py",
    "tools/check_bridge_changes_requested.py",
    "tools/bridge_event_taxonomy.py",
    "tools/bridge_accepted_queue_preflight.py",
    "tools/bridge_named_mutex.py",
    "waggledance/__init__.py",
    "waggledance/core/__init__.py",
    "waggledance/core/bridge_identity_registry.py",
    "waggledance/core/bridge_log_reader.py",
    "waggledance/core/bridge_resource_scope.py",
    "waggledance/core/work_queue.py",
)
# Trust gaps G3a cannot close; they are reported UNKNOWN on every call.
TRUST_GAPS: tuple[tuple[str, str], ...] = (
    ("session_identity", "no_trusted_session_source"),
    ("bridge_origin_authentication", "observed_content_is_not_origin"),
    ("concept_lineage", "no_concept_lineage_source"),
    ("independent_rco_exact_head", "not_assessed_in_g3a"),
    ("exact_head_ci", "not_assessed_in_g3a"),
)

# Exact canonical clear statuses accepted locally: each is a canonical clear at
# the trusted base (changes_requested_ + retracted/withdrawn) and an exact clear
# in the pending cause-B train.  Exact strings; no normalization.
LOCAL_CLEAR_STATUSES = frozenset({"changes_requested_retracted", "changes_requested_withdrawn"})
LOCAL_CLEAR_TYPES = frozenset({DECISION_TYPE, "rco_review"})
CONTROL_TYPES = frozenset({DECISION_TYPE, "rco_review", "finding", "blocked"})
# Positive candidates: never a block here, never a clear either.
APPROVAL_STATUSES = frozenset({"rco_pass", "rco_pass_pending_ci", "approved", "approved_ci_green", "build_consensus_pass"})
# Non-control event types still block when they carry one of these statuses.
BLOCK_STATUS_WORDS = frozenset(
    {
        "changes_requested", "request_changes", "rco_veto", "veto", "rco_fail", "rco_block",
        "blocked", "rco_blocked", "block_requested", "rco_pass_withheld", "hold", "finding",
    }
)
SHA256_RE = re.compile(r"[0-9a-f]{64}")
SHA1_RE = re.compile(r"[0-9a-f]{40}")
GIT_TIMEOUT_SECONDS = 30.0
MAX_REGISTRY_BYTES = 64 * 1024
MAX_SOURCE_BYTES = 4 * 1024 * 1024
SNAPSHOT_MAX_BYTES = 4 * 1024 * 1024
SNAPSHOT_MAX_ROWS = 10_000
SNAPSHOT_MAX_TOTAL_ROWS = 200_000
SNAPSHOT_MAX_CALLS = 1_000

LogReader = Callable[..., Any]


class AdapterError(ValueError):
    """Fail-closed adapter refusal with a stable reason code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class SourceOriginEvidence:
    trusted_commit: str
    matches: tuple[tuple[str, str], ...]  # (path, "exact" | "crlf_normalized")
    provenance: str


@dataclass(frozen=True)
class RegistryEvidence:
    trusted_commit: str
    identities: Mapping[str, str]
    blob_sha1: str
    provenance: str


@dataclass(frozen=True)
class SnapshotEvidence:
    events: tuple[Mapping[str, Any], ...]
    file_identity: str
    generation: str | None
    snapshot_length: int
    read_calls: int
    reserialized_rows_sha256: str  # canonical re-serialization: content, never raw origin
    provenance: str


# Issued evidence: id -> (weak reference, content digest).  Only the object this
# module returned, unchanged, verifies; copies and mutations do not.
_ISSUED: dict[int, tuple[weakref.ref, str]] = {}


def _evidence_digest(evidence: Any) -> str:
    if isinstance(evidence, SnapshotEvidence):
        body: Any = [
            "snapshot", [bridge_event_id(dict(event)) for event in evidence.events], evidence.file_identity,
            evidence.generation, evidence.snapshot_length, evidence.read_calls,
            evidence.reserialized_rows_sha256, evidence.provenance,
        ]
    elif isinstance(evidence, RegistryEvidence):
        body = ["registry", evidence.trusted_commit, sorted(evidence.identities.items()), evidence.blob_sha1, evidence.provenance]
    elif isinstance(evidence, SourceOriginEvidence):
        body = ["sources", evidence.trusted_commit, [list(item) for item in evidence.matches], evidence.provenance]
    else:
        raise AdapterError("evidence_type_unknown", type(evidence).__name__)
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=True).encode("ascii")).hexdigest()


def _issue(evidence: Any) -> Any:
    key = id(evidence)
    _ISSUED[key] = (weakref.ref(evidence, lambda _ref, key=key: _ISSUED.pop(key, None)), _evidence_digest(evidence))
    return evidence


def is_issued(evidence: Any) -> bool:
    """True only for an unchanged evidence object returned by this module."""
    entry = _ISSUED.get(id(evidence))
    if entry is None or entry[0]() is not evidence:
        return False
    try:
        return _evidence_digest(evidence) == entry[1]
    except (AdapterError, ReceiptError, TypeError, ValueError, AttributeError):
        return False


def counts_as_local_read(evidence: Any, kind: type) -> bool:
    """Issued by this module, of the expected type, from its own default reader."""
    return (
        type(evidence) is kind
        and is_issued(evidence)
        and evidence.provenance == PROVENANCE_LOCAL_READ
    )


def _subprocess_runner(
    argv: Sequence[str],
    *,
    input_bytes: bytes | None,
    timeout: float,
    env: Mapping[str, str] | None,
) -> RunResult:
    completed = subprocess.run(  # noqa: S603 - argument list, never a shell
        list(argv),
        input=input_bytes,
        capture_output=True,
        timeout=timeout,
        env=dict(env) if env is not None else None,
        shell=False,
        check=False,
    )
    return RunResult(completed.returncode, completed.stdout, completed.stderr)


def _git_env() -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def require_read_only_git(argv: Sequence[str]) -> None:
    """Refuse every ``git`` argv except the two local object reads built here.

    Accepted after ``git -C <repo> --no-replace-objects``: ``rev-parse --verify
    --quiet <40-hex>^{commit}`` and ``cat-file blob <40-hex>:<pinned path>``.
    Neither can fetch, write a ref or touch the network.
    """
    if not all(type(arg) is str for arg in argv):
        raise AdapterError("effect_refused", "git argv holds a non-string element")
    if len(argv) < 4 or argv[1] != "-C" or argv[3] != "--no-replace-objects":
        raise AdapterError("effect_refused", "git argv is not on the read-only allowlist")
    args = list(argv[4:])
    if (
        len(args) == 4
        and args[:3] == ["rev-parse", "--verify", "--quiet"]
        and re.fullmatch(r"[0-9a-f]{40}\^\{commit\}", args[3])
    ):
        return
    if len(args) == 3 and args[:2] == ["cat-file", "blob"]:
        commit, sep, path = args[2].partition(":")
        if sep and SHA1_RE.fullmatch(commit) and path in (*PINNED_SOURCE_PATHS, REGISTRY_PATH):
            return
    raise AdapterError("effect_refused", "git argv is not on the read-only allowlist: " + " ".join(args[:2]))


def _run_git(runner: Runner, git_executable: str, repo_root: Path, args: Sequence[str]) -> RunResult:
    argv = [git_executable, "-C", str(repo_root), "--no-replace-objects", *args]
    require_read_only_git(argv)
    try:
        result = runner(argv, input_bytes=None, timeout=GIT_TIMEOUT_SECONDS, env=_git_env())
    except subprocess.TimeoutExpired as exc:
        raise AdapterError("git_unavailable", "git timed out") from exc
    except OSError as exc:
        raise AdapterError("git_unavailable", type(exc).__name__) from exc
    if not isinstance(result, RunResult) or type(result.stdout) is not bytes:
        raise AdapterError("git_unavailable", "runner returned an unexpected result")
    return result


def _require_commit(runner: Runner, git_executable: str, repo_root: Path, trusted_commit: Any) -> str:
    if type(trusted_commit) is not str or SHA1_RE.fullmatch(trusted_commit) is None:
        raise AdapterError("trusted_commit_invalid", "a full lowercase 40-hex commit is required")
    result = _run_git(runner, git_executable, repo_root, ["rev-parse", "--verify", "--quiet", f"{trusted_commit}^{{commit}}"])
    if result.returncode != 0 or result.stdout.strip() != trusted_commit.encode("ascii"):
        raise AdapterError("trusted_commit_missing", "the trusted commit is not a local commit object")
    return trusted_commit


def _blob(runner: Runner, git_executable: str, repo_root: Path, trusted_commit: str, path: str, limit: int) -> bytes:
    result = _run_git(runner, git_executable, repo_root, ["cat-file", "blob", f"{trusted_commit}:{path}"])
    if result.returncode != 0:
        raise AdapterError("trusted_blob_missing", path)
    if len(result.stdout) > limit:
        raise AdapterError("trusted_blob_too_large", path)
    return result.stdout


def verify_trusted_sources(
    repo_root: Path,
    trusted_commit: str,
    *,
    source_root: Path = ROOT,
    runner: Runner | None = None,
    git_executable: str = "git",
) -> SourceOriginEvidence:
    """Each pinned file under ``source_root`` must equal its blob at the trusted commit.

    Only existing local objects are read.  A CRLF working copy matches when
    replacing CRLF by LF gives the blob exactly (Python reads both the same);
    that is recorded per path.  Any missing object or difference refuses.
    """
    provenance = PROVENANCE_LOCAL_READ if runner is None else PROVENANCE_UNIT_MOCK
    run = runner or _subprocess_runner
    commit = _require_commit(run, git_executable, Path(repo_root), trusted_commit)
    if Path(source_root).resolve() == ROOT:
        # The modules this process actually imported must be the files compared below.
        for path in PINNED_SOURCE_PATHS:
            name = path[: -len(".py")].replace("/", ".").removesuffix(".__init__")
            module = sys.modules.get(name)
            loaded_from = getattr(module, "__file__", None)
            if type(loaded_from) is not str or Path(loaded_from).resolve() != (ROOT / path).resolve():
                raise AdapterError("loaded_module_origin_mismatch", name)
    matches = []
    for path in PINNED_SOURCE_PATHS:
        blob = _blob(run, git_executable, Path(repo_root), commit, path, MAX_SOURCE_BYTES)
        try:
            loaded = (Path(source_root) / path).read_bytes()
        except OSError as exc:
            raise AdapterError("loaded_source_unreadable", path) from exc
        if loaded == blob:
            matches.append((path, "exact"))
        elif b"\r\n" in loaded and loaded.replace(b"\r\n", b"\n") == blob:
            matches.append((path, "crlf_normalized"))
        else:
            raise AdapterError("loaded_source_differs_from_trusted_base", path)
    return _issue(SourceOriginEvidence(commit, tuple(matches), provenance))


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AdapterError("registry_invalid", f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(token: str) -> Any:
    raise AdapterError("registry_invalid", f"non-finite constant {token}")


def load_trusted_registry(
    repo_root: Path,
    trusted_commit: str,
    *,
    runner: Runner | None = None,
    git_executable: str = "git",
) -> RegistryEvidence:
    """The identity registry blob at the trusted commit, validated like the canonical loader.

    Stricter than the loader: duplicate keys, non-finite constants, an empty
    identity map and case-colliding agent ids or uuids refuse.  There is no
    session field to read; session identity stays UNKNOWN.
    """
    provenance = PROVENANCE_LOCAL_READ if runner is None else PROVENANCE_UNIT_MOCK
    run = runner or _subprocess_runner
    commit = _require_commit(run, git_executable, Path(repo_root), trusted_commit)
    data = _blob(run, git_executable, Path(repo_root), commit, REGISTRY_PATH, MAX_REGISTRY_BYTES)
    try:
        payload = json.loads(data.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AdapterError("registry_invalid", type(exc).__name__) from exc
    identities = payload.get("identities") if isinstance(payload, dict) else None
    if not isinstance(identities, dict) or not identities:
        raise AdapterError("registry_invalid", "a non-empty identities object is required")
    registry: dict[str, str] = {}
    for agent, agent_uuid in identities.items():
        if type(agent) is not str or not AGENT_ID_PATTERN.fullmatch(agent):
            raise AdapterError("registry_invalid", f"invalid agent id {agent!r}")
        if type(agent_uuid) is not str or not AGENT_UUID_PATTERN.fullmatch(agent_uuid):
            raise AdapterError("registry_invalid", f"invalid agent_uuid for {agent}")
        registry[agent] = agent_uuid
    if len({agent.casefold() for agent in registry}) != len(registry) or len(
        {value.casefold() for value in registry.values()}
    ) != len(registry):
        raise AdapterError("registry_invalid", "case-colliding agent ids or uuids")
    blob_sha1 = hashlib.sha1(b"blob %d\x00" % len(data) + data, usedforsecurity=False).hexdigest()  # git object id
    return _issue(RegistryEvidence(commit, MappingProxyType(dict(registry)), blob_sha1, provenance))


def read_bridge_snapshot(
    log_path: Path,
    *,
    generation_path: Path | None = None,
    reader: LogReader | None = None,
    max_total_rows: int = SNAPSHOT_MAX_TOTAL_ROWS,
) -> SnapshotEvidence:
    """Every complete row from byte zero to a stable end of file, or a refusal.

    Loops the public reader with its candidate cursor.  RETRY, BLOCKED, a
    missing log, a trailing partial record, a moving file identity or
    generation, or more than ``max_total_rows`` rows refuse; there is no
    partial snapshot.
    """
    provenance = PROVENANCE_LOCAL_READ if reader is None else PROVENANCE_UNIT_MOCK
    read = reader or read_bridge_log
    events: list[Mapping[str, Any]] = []
    cursor = None
    identity: str | None = None
    generation: str | None = None
    calls = 0
    while True:
        calls += 1
        if calls > SNAPSHOT_MAX_CALLS:
            raise AdapterError("bridge_snapshot_unknown", "read call limit")
        result = read(
            log_path, cursor=cursor, max_bytes=SNAPSHOT_MAX_BYTES, max_rows=SNAPSHOT_MAX_ROWS,
            generation_path=generation_path,
        )
        status = getattr(result, "status", None)
        candidate = getattr(result, "candidate_cursor", None)
        if status not in (BridgeReadStatus.OK, BridgeReadStatus.IDLE) or candidate is None:
            raise AdapterError("bridge_snapshot_unknown", f"{status}:{getattr(result, 'reason', '')}")
        if identity is None:
            identity, generation = candidate.file_identity, candidate.generation
        elif (candidate.file_identity, candidate.generation) != (identity, generation):
            raise AdapterError("bridge_snapshot_unknown", "file identity or generation changed")
        if status == BridgeReadStatus.OK:
            rows = getattr(result, "rows", ())
            if type(rows) is not tuple or not rows or not all(type(row) is dict for row in rows):
                raise AdapterError("bridge_snapshot_unknown", "OK read without rows")
            events.extend(rows)
            if len(events) > max_total_rows:
                raise AdapterError("bridge_snapshot_unknown", "row limit")
            cursor = candidate
            continue
        length = getattr(result, "snapshot_length", None)
        if type(length) is not int or candidate.offset != length:
            raise AdapterError("bridge_snapshot_unknown", "incomplete end of file")
        break
    frozen = tuple(MappingProxyType(dict(event)) for event in events)
    digest = hashlib.sha256(b"\n".join(_event_bytes(event) for event in frozen)).hexdigest()
    return _issue(SnapshotEvidence(frozen, identity, generation, length, calls, digest, provenance))


def _event_bytes(event: Mapping[str, Any]) -> bytes:
    return json.dumps(dict(event), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _task_alias(task_id: str) -> str:
    return task_id.replace("/", "-").casefold()


def in_negative_scope(event: Mapping[str, Any], *, task_id: str, pull_request: int) -> bool:
    """Conservative union: task, slash/hyphen alias, PR in task text, PR payload keys (int, float, str)."""
    event_task = event.get("task_id")
    if type(event_task) is str:
        if event_task == task_id or _task_alias(event_task) == _task_alias(task_id):
            return True
        if re.search(rf"(?i)(?:pr|#)[-_ ]?0*{pull_request}(?![0-9])", event_task):
            return True
    payload = event.get("payload")
    if isinstance(payload, Mapping):
        for key in PR_PAYLOAD_KEYS:
            value = payload.get(key)
            if type(value) in (int, float) and value == pull_request:
                return True
            if type(value) is str and re.search(rf"(?<![0-9])0*{pull_request}(?![0-9])", value):
                return True
    return False


def _event_time(value: Any) -> datetime | None:
    if type(value) is not str:
        return None
    match = EVENT_TS_RE.fullmatch(value)
    if match is None:
        return None
    micros = int((match.group(7) or "").ljust(6, "0")[:6])
    try:
        return datetime(*(int(part) for part in match.groups()[:6]), micros, tzinfo=timezone.utc)
    except ValueError:
        return None


def _is_local_clear_shape(event: Mapping[str, Any]) -> bool:
    return event.get("type") in LOCAL_CLEAR_TYPES and event.get("status") in LOCAL_CLEAR_STATUSES


def _local_clear_target(
    event: Mapping[str, Any],
    blocking: Mapping[str, Mapping[str, Any]],
    *,
    head_sha: str,
    registry: Mapping[str, str],
) -> str | None:
    payload = event.get("payload")
    if not isinstance(payload, Mapping) or payload.get("exact_head") != head_sha:
        return None
    target = payload.get("retracts_event_id")
    if type(target) is not str or SHA256_RE.fullmatch(target) is None or target not in blocking:
        return None
    block = blocking[target]
    uuid = event.get("agent_uuid")
    block_time, clear_time = _event_time(block.get("ts_utc")), _event_time(event.get("ts_utc"))
    if (
        event.get("agent") == block.get("agent")
        and type(uuid) is str
        and uuid
        and block.get("agent_uuid") == uuid
        and registry.get(event.get("agent")) == uuid
        and block_time is not None
        and clear_time is not None
        and clear_time > block_time
    ):
        return target
    return None


def local_withdrawal_state(
    events: Sequence[Mapping[str, Any]],
    *,
    task_id: str,
    pull_request: int,
    head_sha: str,
    registry: Mapping[str, str],
) -> dict[str, Any]:
    """The strict local rule (D1) in snapshot order; returns open blocks and the ordered control ids."""
    blocking: dict[str, Mapping[str, Any]] = {}
    control_ids: list[str] = []
    unmatched_clears: list[str] = []
    for event in events:
        if event.get("agent") not in RECOGNIZED_RCO_AGENTS or not in_negative_scope(
            event, task_id=task_id, pull_request=pull_request
        ):
            continue
        event_type, status = event.get("type"), event.get("status")
        if event_type not in CONTROL_TYPES and status not in BLOCK_STATUS_WORDS:
            continue  # messages and status notes are not controls
        event_id = bridge_event_id(event)
        control_ids.append(event_id)
        if event_type == DECISION_TYPE and status in APPROVAL_STATUSES:
            continue  # a positive candidate: never a block, never a clear
        if _is_local_clear_shape(event):
            target = _local_clear_target(event, blocking, head_sha=head_sha, registry=registry)
            if target is None:
                unmatched_clears.append(event_id)
            else:
                del blocking[target]
            continue
        blocking[event_id] = event
    return {
        "blocking_ids": list(blocking),
        "unmatched_clear_ids": unmatched_clears,
        "ordered_controls_sha256": hashlib.sha256("\n".join(control_ids).encode("ascii")).hexdigest(),
    }


def assess_controls(
    snapshot: SnapshotEvidence,
    registry: RegistryEvidence,
    sources: SourceOriginEvidence,
    *,
    task_id: str,
    pull_request: int,
    head_sha: str,
    merging_agent: str,
    author_agent: str,
) -> dict[str, Any]:
    """Canonical gate AND strict local rule over the same issued snapshot.

    ``pass`` needs issued local-read evidence, the trusted-base source pin
    for the same commit as the registry, a canonical ``clear`` and no open
    local block.  Either side blocking refuses; anything unverifiable is
    UNKNOWN.  ``pass`` here is one control check, never an admission.
    """
    missing = [
        name for name, evidence, kind in (
            ("snapshot", snapshot, SnapshotEvidence),
            ("registry", registry, RegistryEvidence),
            ("sources", sources, SourceOriginEvidence),
        )
        if not counts_as_local_read(evidence, kind)
    ]
    if missing:
        return {"check": "rco_controls", "status": CHECK_UNKNOWN, "reason": "evidence_not_issued_local_read", "detail": missing}
    if sources.trusted_commit != registry.trusted_commit:
        return {"check": "rco_controls", "status": CHECK_UNKNOWN, "reason": "trusted_commit_mismatch"}
    if (
        type(pull_request) is not int or pull_request <= 0
        or type(head_sha) is not str or SHA1_RE.fullmatch(head_sha) is None
        or type(task_id) is not str or not task_id
    ):
        return {"check": "rco_controls", "status": CHECK_REFUSE, "reason": "scope_invalid"}
    events = [dict(event) for event in snapshot.events]
    registry_map = dict(registry.identities)
    try:
        canonical = check_bridge_clear_to_merge(
            events=events, task_id=task_id, merging_agent=merging_agent, author_agent=author_agent,
            pr_number=pull_request, identity_registry=registry_map,
        )
    except Exception as exc:  # noqa: BLE001 - any canonical failure is UNKNOWN, never a pass
        return {"check": "rco_controls", "status": CHECK_UNKNOWN, "reason": "canonical_gate_error", "detail": type(exc).__name__}
    try:
        local = local_withdrawal_state(
            events, task_id=task_id, pull_request=pull_request, head_sha=head_sha, registry=registry_map,
        )
    except ReceiptError as exc:
        return {"check": "rco_controls", "status": CHECK_UNKNOWN, "reason": "event_not_canonical", "detail": exc.reason}
    canonical_ok = isinstance(canonical, Mapping) and canonical.get("ok") is True
    canonical_clear = canonical_ok and canonical.get("clear_to_merge") is True and canonical.get("decision") == "clear"
    result = {
        "check": "rco_controls",
        "canonical_decision": canonical.get("decision") if isinstance(canonical, Mapping) else None,
        "local_blocking_ids": local["blocking_ids"],
        "local_unmatched_clear_ids": local["unmatched_clear_ids"],
        "ordered_controls_sha256": local["ordered_controls_sha256"],
    }
    if local["blocking_ids"] or (canonical_ok and not canonical_clear):
        result.update(status=CHECK_REFUSE, reason="rco_veto_active")
    elif not canonical_ok:
        result.update(status=CHECK_UNKNOWN, reason="canonical_gate_not_ok")
    else:
        result.update(status=CHECK_PASS, reason="")
    return result


def assess_g3a(
    snapshot: SnapshotEvidence,
    registry: RegistryEvidence,
    sources: SourceOriginEvidence,
    *,
    task_id: str,
    pull_request: int,
    head_sha: str,
    merging_agent: str,
    author_agent: str,
) -> dict[str, Any]:
    """Combined G3a report.  The verdict is ``refused`` or ``unknown``; never admitted."""
    checks: list[dict[str, Any]] = []
    for name, evidence, kind in (
        ("trusted_source_pin", sources, SourceOriginEvidence),
        ("trusted_registry", registry, RegistryEvidence),
        ("bridge_snapshot_observed", snapshot, SnapshotEvidence),
    ):
        ok = counts_as_local_read(evidence, kind)
        checks.append({"check": name, "status": CHECK_PASS if ok else CHECK_UNKNOWN, "reason": "" if ok else "evidence_not_issued_local_read"})
    checks.append(
        assess_controls(
            snapshot, registry, sources, task_id=task_id, pull_request=pull_request, head_sha=head_sha,
            merging_agent=merging_agent, author_agent=author_agent,
        )
    )
    checks.extend({"check": name, "status": CHECK_UNKNOWN, "reason": reason} for name, reason in TRUST_GAPS)
    statuses = [check["status"] for check in checks]
    verdict = VERDICT_REFUSED if CHECK_REFUSE in statuses else VERDICT_UNKNOWN
    return {"schema": ADAPTERS_SCHEMA, "verdict": verdict, "admitted": False, "checks": checks}


__all__ = [
    "ADAPTERS_SCHEMA",
    "ADMIT_AVAILABLE",
    "APPROVAL_STATUSES",
    "AdapterError",
    "CHECK_PASS",
    "CHECK_REFUSE",
    "CHECK_UNKNOWN",
    "LOCAL_CLEAR_STATUSES",
    "PINNED_SOURCE_PATHS",
    "PROVENANCE_LOCAL_READ",
    "PROVENANCE_UNIT_MOCK",
    "REGISTRY_PATH",
    "RegistryEvidence",
    "SnapshotEvidence",
    "SourceOriginEvidence",
    "TRUST_GAPS",
    "VERDICT_REFUSED",
    "VERDICT_UNKNOWN",
    "assess_controls",
    "assess_g3a",
    "counts_as_local_read",
    "in_negative_scope",
    "is_issued",
    "load_trusted_registry",
    "local_withdrawal_state",
    "read_bridge_snapshot",
    "require_read_only_git",
    "verify_trusted_sources",
]
