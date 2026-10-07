# SPDX-License-Identifier: BUSL-1.1
"""Read-only admission preview for the manual (a)-class merge route (MANUAL-A, G2).

Third slice of the manual merge + MAGMA receipt route (operator decision
8A480508, Lead plan F9D23F10, bounded plan CECF5F6D).  It evaluates whether ONE
pull request could be admitted and reports every check.  It never performs an
effect:

* no merge, ready, undraft or any other GitHub mutation;
* no nonce reservation or any nonce-ledger access;
* no bridge event, receipt or MAGMA write;
* no provider call;
* :func:`execute` always refuses with ``execute_unavailable``.

What the preview reads itself (never caller-supplied flags):

* the statement is parsed and verified with ``verify_statement`` from
  ``tools.manual_bridge_merge_statement``: the trust anchor from the live base,
  the G1 ``git diff-tree`` facts for exactly live base..live head, then the SSH
  verifier;
* live PR facts from the raw ``gh pr view --json`` output, dependency PR
  states, the head's check runs, the branch's required checks and the API rate
  limit, each from a read-only ``gh`` call whose argv is checked against a
  read-only allowlist before it runs;
* ``git merge-base --is-ancestor`` for live base..live head.

What the caller hands over is evidence, and it is validated here: bridge
events (approvals, blocking decisions, retractions), the author / contributor
lineage, the identity registry, the other signed statements of the batch and
the autonomous refusal (preserved unchanged, or an explicit UNKNOWN absence;
never built here).  No genuine adapter exists yet for the provenance of that
evidence, so the five integration prerequisites, the DN-A contract, the nonce
state and the evidence-package privacy are always reported UNKNOWN.  The best
possible verdict of this slice is therefore ``unknown``; there is no
``admitted`` verdict.  Injected runners make every fact ``unit_mock``.

Importing this module has no side effects beyond the repository-root
``sys.path`` entry that every ``tools`` module uses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.manual_bridge_merge_receipt import (  # noqa: E402
    BASE_REF_NAME,
    BUILD_CONSENSUS_STATUS,
    DECISION_TYPE,
    EVENT_TS_RE,
    INTEGRATION_PREREQUISITES,
    LEAD_AGENT,
    PR_PAYLOAD_KEYS,
    RCO_PASS_STATUS,
    RECOGNIZED_RCO_AGENTS,
    STRUCTURED_HEAD_KEYS,
    TOOLS_AGENT,
    AutonomousRefusalEvidence,
    ReceiptError,
    assess_refusal,
    bridge_event_id,
)
from tools.manual_bridge_merge_statement import (  # noqa: E402
    ALLOWED_SIGNERS_PATH,
    REPOSITORY,
    RunResult,
    Runner,
    Statement,
    StatementError,
    parse_statement,
    require_genuine_provenance,
    verify_statement,
)

PREVIEW_SCHEMA = "wd.manual-merge-a.admission-preview.v1"
CHECK_PASS = "pass"
CHECK_REFUSE = "refuse"
CHECK_UNKNOWN = "unknown"
VERDICT_REFUSED = "refused"
VERDICT_UNKNOWN = "unknown"
EXECUTE_AVAILABLE = False

# The route cannot admit the pull request that changes the route itself
# (bootstrap: no self-merge, no self-receipt).
ROUTE_PATHS: tuple[str, ...] = (
    "tools/manual_bridge_merge.py",
    "tools/manual_bridge_merge_statement.py",
    "tools/manual_bridge_merge_receipt.py",
    "tests/tools/test_manual_bridge_merge.py",
    "tests/tools/test_manual_bridge_merge_statement.py",
    "tests/tools/test_manual_bridge_merge_receipt.py",
    "docs/architecture/MANUAL_BRIDGE_MERGE_A.md",
    ALLOWED_SIGNERS_PATH,
)
# A recognized-RCO control in the negative scope is a blocking decision unless
# it is exactly an rco_pass (positive candidate) or an exactly bound retraction.
RETRACTION_STATUS = "finding_retracted"
FINDING_TYPE = "finding"
BLOCKING_STATUSES = frozenset(
    {
        "changes_requested",
        "request_changes",
        "rco_veto",
        "veto",
        "rco_fail",
        "rco_block",
        "blocked",
        "rco_pass_withheld",
        "hold",
        "finding",
    }
)
GH_VIEW_FIELDS: tuple[str, ...] = (
    "number",
    "state",
    "isDraft",
    "headRefOid",
    "headRefName",
    "baseRefOid",
    "baseRefName",
    "mergeable",
    "mergeStateStatus",
)
GH_TIMEOUT_SECONDS = 60.0
GIT_TIMEOUT_SECONDS = 30.0
MAX_GH_BYTES = 1024 * 1024
MIN_RATE_REMAINING = 50
SHA1_RE = re.compile(r"[0-9a-f]{40}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
EXPECTED_UNKNOWN_PREREQUISITES: tuple[str, ...] = INTEGRATION_PREREQUISITES + (
    "dn_a_contract_reconciliation",
    "nonce_state",
    "evidence_privacy",
)


class AdmissionError(ValueError):
    """Fail-closed refusal with a stable reason code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    reason: str
    detail: str = ""


@dataclass(frozen=True)
class BridgeSnapshot:
    """Raw bridge events as returned by a pinned reader (provenance UNKNOWN here)."""

    events: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True)
class AuthorLineage:
    """Canonical author and contributor identities of the PR (provenance UNKNOWN here)."""

    authors: tuple[str, ...]
    contributors: tuple[str, ...] = ()


@dataclass(frozen=True)
class IdentityBinding:
    agent_uuid: str
    session_id: str


@dataclass(frozen=True)
class IdentityRegistry:
    """Expected agent -> (agent_uuid, session_id) bindings (provenance UNKNOWN here)."""

    entries: Mapping[str, IdentityBinding]


@dataclass(frozen=True)
class LivePullRequest:
    number: int
    state: str
    is_draft: bool
    head_ref_oid: str
    head_ref_name: str
    base_ref_oid: str
    base_ref_name: str
    mergeable: str
    merge_state_status: str
    raw_sha256: str
    argv: tuple[str, ...]
    provenance: str


@dataclass(frozen=True)
class AdmissionPreview:
    schema: str
    pull_request: int
    verdict: str
    checks: tuple[Check, ...]
    controls_digest: str | None
    execute_available: bool = False
    effects: tuple[str, ...] = field(default=())

    @property
    def refusals(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if check.status == CHECK_REFUSE)

    @property
    def unknowns(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if check.status == CHECK_UNKNOWN)

    def check(self, name: str) -> Check:
        matches = [check for check in self.checks if check.name == name]
        if len(matches) != 1:
            raise KeyError(name)
        return matches[0]


# --- read-only command runners -------------------------------------------------


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


GH_WRITE_FLAGS = frozenset({"-X", "--method", "-f", "-F", "--field", "--raw-field", "--input"})


def require_read_only_gh(argv: Sequence[str]) -> None:
    """Refuse any ``gh`` argv other than ``pr view`` or a plain GET ``api`` read."""
    args = list(argv[1:])
    if args[:2] == ["pr", "view"]:
        return
    if args[:1] == ["api"] and len(args) >= 2 and not any(
        arg in GH_WRITE_FLAGS or arg.startswith(("--method=", "-X")) for arg in args[1:]
    ):
        return
    raise AdmissionError("effect_refused", "gh argv is not on the read-only allowlist: " + " ".join(args[:3]))


def require_read_only_git(argv: Sequence[str]) -> None:
    """Refuse any ``git`` argv other than ``merge-base --is-ancestor`` after ``-C <repo>``."""
    args = list(argv[3:]) if len(argv) > 3 and argv[1] == "-C" else []
    if args[:3] == ["--no-replace-objects", "merge-base", "--is-ancestor"] and len(args) == 5:
        return
    raise AdmissionError("effect_refused", "git argv is not on the read-only allowlist")


def _run_gh(runner: Runner, gh_executable: str, args: Sequence[str]) -> RunResult:
    argv = [gh_executable, *args]
    require_read_only_gh(argv)
    try:
        result = runner(argv, input_bytes=None, timeout=GH_TIMEOUT_SECONDS, env=None)
    except subprocess.TimeoutExpired as exc:
        raise AdmissionError("gh_unavailable", "gh timed out") from exc
    except OSError as exc:
        raise AdmissionError("gh_unavailable", type(exc).__name__) from exc
    if not isinstance(result, RunResult) or type(result.stdout) is not bytes:
        raise AdmissionError("gh_unavailable", "runner returned an unexpected result")
    return result


def _git_env() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AdmissionError("live_fact_malformed", f"duplicate key {key}")
        result[key] = value
    return result


def _reject_constant(token: str) -> Any:
    raise AdmissionError("live_fact_malformed", f"JSON constant {token}")


def _strict_json(data: bytes, label: str) -> Any:
    if not data or len(data) > MAX_GH_BYTES:
        raise AdmissionError("live_fact_malformed", f"{label}: empty or oversized output")
    try:
        return json.loads(
            data.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant
        )
    except AdmissionError:
        raise
    except (UnicodeDecodeError, ValueError) as exc:
        raise AdmissionError("live_fact_malformed", f"{label}: invalid JSON") from exc


def read_live_pull_request(
    pull_request: int, *, runner: Runner | None = None, gh_executable: str = "gh"
) -> LivePullRequest:
    """Strictly parse the raw ``gh pr view <n> --json ...`` output for this repository."""
    if type(pull_request) is not int or pull_request < 1:
        raise AdmissionError("invalid_input", "pull_request must be a positive int")
    args = ["pr", "view", str(pull_request), "--repo", REPOSITORY, "--json", ",".join(GH_VIEW_FIELDS)]
    result = _run_gh(runner if runner is not None else _subprocess_runner, gh_executable, args)
    if result.returncode != 0:
        raise AdmissionError("live_pr_unavailable", f"gh exit {result.returncode}")
    decoded = _strict_json(result.stdout, "gh pr view")
    if not isinstance(decoded, dict) or set(decoded) != set(GH_VIEW_FIELDS):
        raise AdmissionError("live_fact_malformed", "gh pr view fields differ from the requested set")
    for name in ("state", "headRefOid", "headRefName", "baseRefOid", "baseRefName", "mergeable", "mergeStateStatus"):
        if type(decoded[name]) is not str:
            raise AdmissionError("live_fact_malformed", f"{name} must be a string")
    if type(decoded["number"]) is not int or decoded["number"] != pull_request:
        raise AdmissionError("live_fact_malformed", "number differs from the requested PR")
    if type(decoded["isDraft"]) is not bool:
        raise AdmissionError("live_fact_malformed", "isDraft must be a bool")
    for name in ("headRefOid", "baseRefOid"):
        if SHA1_RE.fullmatch(decoded[name]) is None:
            raise AdmissionError("live_fact_malformed", f"{name} must be a full lowercase sha")
    return LivePullRequest(
        number=decoded["number"],
        state=decoded["state"],
        is_draft=decoded["isDraft"],
        head_ref_oid=decoded["headRefOid"],
        head_ref_name=decoded["headRefName"],
        base_ref_oid=decoded["baseRefOid"],
        base_ref_name=decoded["baseRefName"],
        mergeable=decoded["mergeable"],
        merge_state_status=decoded["mergeStateStatus"],
        raw_sha256=hashlib.sha256(result.stdout).hexdigest(),
        argv=(gh_executable, *args),
        provenance="unit_mock" if runner is not None else "subprocess_gh",
    )


# --- checks ---------------------------------------------------------------------


class _Collector:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def add(self, name: str, status: str, reason: str, detail: str = "") -> None:
        self.checks.append(Check(name, status, reason, detail))

    def passed(self, name: str, detail: str = "") -> None:
        self.add(name, CHECK_PASS, "ok", detail)

    def refuse(self, name: str, reason: str, detail: str = "") -> None:
        self.add(name, CHECK_REFUSE, reason, detail)

    def unknown(self, name: str, reason: str, detail: str = "") -> None:
        self.add(name, CHECK_UNKNOWN, reason, detail)


def _check_live_pull_request(out: _Collector, live: LivePullRequest, statement: Statement) -> None:
    if live.state != "OPEN":
        out.refuse("live_pr_state", "pull_request_not_open", live.state)
    else:
        out.passed("live_pr_state")
    if live.is_draft:
        out.refuse("live_pr_draft", "pull_request_draft", "a draft PR is never admitted; agents never undraft")
    else:
        out.passed("live_pr_draft")
    if live.base_ref_name != BASE_REF_NAME:
        out.refuse("live_base_ref", "base_ref_mismatch", live.base_ref_name)
    else:
        out.passed("live_base_ref")
    if statement.base_sha != live.base_ref_oid:
        out.refuse("signed_base", "base_mismatch", f"signed {statement.base_sha} live {live.base_ref_oid}")
    else:
        out.passed("signed_base")
    if statement.head_sha != live.head_ref_oid:
        out.refuse("signed_head", "signed_head_stale", f"signed {statement.head_sha} live {live.head_ref_oid}")
    else:
        out.passed("signed_head")
    if live.mergeable == "MERGEABLE" and live.merge_state_status == "CLEAN":
        out.passed("mergeability", f"{live.mergeable}/{live.merge_state_status}")
    elif live.mergeable == "UNKNOWN" or live.merge_state_status == "UNKNOWN":
        out.unknown("mergeability", "mergeability_unknown", f"{live.mergeable}/{live.merge_state_status}")
    else:
        out.refuse("mergeability", "not_mergeable", f"{live.mergeable}/{live.merge_state_status}")


def _check_statement(
    out: _Collector,
    *,
    statement_bytes: bytes,
    signature_bytes: bytes,
    repo_root: Path,
    live: LivePullRequest,
    now_utc: datetime,
    ssh_keygen: Path,
    ssh_runner: Runner | None,
    git_runner: Runner | None,
    git_executable: str,
) -> tuple[str, ...] | None:
    """Verify with the G1 statement API at live base..live head; return the live paths."""
    try:
        verified = verify_statement(
            statement_bytes=statement_bytes,
            signature_bytes=signature_bytes,
            repo_root=repo_root,
            trusted_commit=live.base_ref_oid,
            expected_head_sha=live.head_ref_oid,
            now_utc=now_utc,
            ssh_keygen=ssh_keygen,
            runner=ssh_runner,
            git_runner=git_runner,
            git_executable=git_executable,
        )
    except StatementError as exc:
        out.refuse("statement_verification", exc.reason, exc.detail)
        return None
    out.passed("statement_verification", verified.statement_sha256)
    try:
        require_genuine_provenance(verified)
    except StatementError as exc:
        out.unknown("statement_provenance", exc.reason, "verification ran on injected runners (unit_mock)")
    else:
        out.passed("statement_provenance")
    # verify_statement refused unless the signed exact_paths equal the live diff paths.
    return verified.statement.exact_paths


def _check_ancestry(
    out: _Collector, *, repo_root: Path, live: LivePullRequest, git_runner: Runner | None, git_executable: str
) -> None:
    argv = [
        git_executable, "-C", str(repo_root), "--no-replace-objects", "merge-base", "--is-ancestor",
        live.base_ref_oid, live.head_ref_oid,
    ]
    require_read_only_git(argv)
    run = git_runner if git_runner is not None else _subprocess_runner
    try:
        result = run(argv, input_bytes=None, timeout=GIT_TIMEOUT_SECONDS, env=_git_env())
    except (subprocess.TimeoutExpired, OSError) as exc:
        out.unknown("head_contains_base", "git_unavailable", type(exc).__name__)
        return
    if not isinstance(result, RunResult):
        out.unknown("head_contains_base", "git_unavailable", "unexpected runner result")
    elif result.returncode == 0:
        out.passed("head_contains_base")
    elif result.returncode == 1:
        out.refuse("head_contains_base", "head_not_based_on_base", "the squash would not apply exactly the signed diff")
    else:
        out.unknown("head_contains_base", "git_unavailable", f"git exit {result.returncode}")


def _check_bootstrap(out: _Collector, live_paths: tuple[str, ...] | None) -> None:
    if live_paths is None:
        out.unknown("bootstrap_self_admission", "live_paths_unavailable")
        return
    touched = sorted(set(live_paths) & set(ROUTE_PATHS))
    if touched:
        out.refuse("bootstrap_self_admission", "route_cannot_admit_itself", ",".join(touched))
    else:
        out.passed("bootstrap_self_admission")


def _check_batch(
    out: _Collector,
    statement: Statement,
    batch_statements: Sequence[bytes],
    *,
    runner: Runner | None,
    gh_executable: str,
) -> None:
    members: dict[int, int] = {statement.batch_order: statement.pull_request}
    for raw in batch_statements:
        try:
            other = parse_statement(raw)
        except StatementError as exc:
            out.refuse("batch_queue", "batch_member_invalid", exc.reason)
            return
        if other.batch_id != statement.batch_id:
            out.refuse("batch_queue", "batch_member_foreign", other.batch_id)
            return
        if other.batch_order in members or other.pull_request in members.values():
            out.refuse("batch_queue", "batch_member_duplicate", str(other.pull_request))
            return
        members[other.batch_order] = other.pull_request
    orders = sorted(members)
    if orders != list(range(1, len(orders) + 1)) or statement.batch_order not in orders:
        out.refuse("batch_queue", "batch_queue_incomplete", f"orders {orders}")
        return
    required = sorted(
        {pr for order, pr in members.items() if order < statement.batch_order} | set(statement.dependencies)
    )
    for number in required:
        try:
            other_live = read_live_pull_request(number, runner=runner, gh_executable=gh_executable)
        except AdmissionError as exc:
            out.unknown("batch_queue", exc.reason, f"PR {number}")
            return
        if other_live.state != "MERGED":
            out.refuse("batch_queue", "prerequisite_not_merged", f"PR {number} {other_live.state}")
            return
    out.passed("batch_queue", f"prerequisites merged: {required}")


def _head_bound(event: Mapping[str, Any], head_sha: str, *, require_exact: bool) -> bool:
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return False
    present = [key for key in STRUCTURED_HEAD_KEYS if key in payload]
    if not present or (require_exact and "exact_head" not in present):
        return False
    return all(payload[key] == head_sha for key in present)


def _task_alias(task_id: str) -> str:
    return task_id.replace("/", "-").casefold()


def _in_negative_scope(event: Mapping[str, Any], *, task_id: str, pull_request: int) -> bool:
    """Conservative union: canonical task, slash/hyphen alias, PR payload keys, PR pattern."""
    event_task = event.get("task_id")
    if type(event_task) is str:
        if event_task == task_id or _task_alias(event_task) == _task_alias(task_id):
            return True
        if re.search(rf"(?i)(?:pr|#)[-_ ]?0*{pull_request}(?![0-9])", event_task):
            return True
    payload = event.get("payload")
    if isinstance(payload, Mapping):
        for key in PR_PAYLOAD_KEYS:
            if type(payload.get(key)) is int and payload.get(key) == pull_request:
                return True
    return False


def _retraction_target(event: Mapping[str, Any], head_sha: str) -> str | None:
    payload = event.get("payload")
    if (
        event.get("type") == DECISION_TYPE
        and event.get("status") == RETRACTION_STATUS
        and isinstance(payload, Mapping)
        and type(payload.get("retracts_event_id")) is str
        and SHA256_RE.fullmatch(payload["retracts_event_id"]) is not None
        and payload.get("exact_head") == head_sha
    ):
        return payload["retracts_event_id"]
    return None


def _event_time(value: Any) -> datetime | None:
    """A bridge ``ts_utc`` as an aware UTC datetime, or None when it is not exact."""
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


def _retraction_matches(block: Mapping[str, Any], retraction: Mapping[str, Any]) -> bool:
    uuid = block.get("agent_uuid")
    block_time, retraction_time = _event_time(block.get("ts_utc")), _event_time(retraction.get("ts_utc"))
    return (
        retraction.get("agent") == block.get("agent")
        and type(uuid) is str
        and bool(uuid)
        and retraction.get("agent_uuid") == uuid
        and block_time is not None
        and retraction_time is not None
        and retraction_time > block_time
    )


def _check_controls(
    out: _Collector,
    snapshot: BridgeSnapshot,
    *,
    task_id: str,
    pull_request: int,
    head_sha: str,
    expected_controls_digest: str | None,
) -> str | None:
    """Recognized-RCO blocking decisions win before and after any PASS; only an exact retraction clears.

    Events are taken in snapshot order.  A retraction clears only a block that
    is already open at that point, from the same agent and ``agent_uuid``, with
    a strictly earlier parseable timestamp; a later replay of the same block
    opens it again.  Nothing is waived when a field is missing or unparseable.
    """
    blocking: dict[str, Mapping[str, Any]] = {}
    control_ids: list[str] = []
    for event in snapshot.events:
        agent = event.get("agent")
        if agent not in RECOGNIZED_RCO_AGENTS or not _in_negative_scope(event, task_id=task_id, pull_request=pull_request):
            continue
        event_type, status = event.get("type"), event.get("status")
        is_decision = event_type in (DECISION_TYPE, FINDING_TYPE)
        if not is_decision and status not in BLOCKING_STATUSES:
            continue  # messages and status notes are not controls
        event_id = bridge_event_id(event)
        control_ids.append(event_id)
        if event_type == DECISION_TYPE and status == RCO_PASS_STATUS:
            continue  # a positive candidate, judged strictly by the approval check
        target = _retraction_target(event, head_sha)
        if target is not None:
            if target in blocking and _retraction_matches(blocking[target], event):
                del blocking[target]
            continue
        blocking[event_id] = event
    if blocking:
        first = next(iter(blocking.values()))
        out.refuse(
            "rco_blocking_decision", "rco_veto_active",
            f"{len(blocking)} unretracted recognized-RCO control(s); first {first.get('agent')} {first.get('type')}/{first.get('status')}",
        )
    else:
        out.passed("rco_blocking_decision")
    digest = hashlib.sha256("\n".join(sorted(control_ids)).encode("ascii")).hexdigest()
    if expected_controls_digest is not None:
        if expected_controls_digest != digest:
            out.refuse("controls_unchanged", "controls_changed", f"expected {expected_controls_digest} now {digest}")
        else:
            out.passed("controls_unchanged")
    return digest


def _select_approval(
    events: Sequence[Mapping[str, Any]],
    *,
    agents: Sequence[str],
    status: str,
    task_id: str,
    head_sha: str,
    excluded: frozenset[str],
    require_exact: bool,
) -> tuple[Mapping[str, Any] | None, str]:
    rejected = "missing"
    for event in events:
        if event.get("type") != DECISION_TYPE or event.get("status") != status:
            continue
        if event.get("agent") not in agents or event.get("task_id") != task_id:
            continue
        if not _head_bound(event, head_sha, require_exact=require_exact):
            rejected = "wrong_or_unbound_head"
            continue
        if event.get("agent") in excluded:
            rejected = "reviewer_is_author_or_contributor"
            continue
        return event, "ok"
    return None, rejected


def _check_approvals(
    out: _Collector,
    snapshot: BridgeSnapshot,
    *,
    task_id: str,
    head_sha: str,
    lineage: AuthorLineage | None,
    registry: IdentityRegistry | None,
    expected_requests: Mapping[str, str] | None,
) -> None:
    if (
        not isinstance(lineage, AuthorLineage)
        or type(lineage.authors) is not tuple
        or not lineage.authors
        or any(type(agent) is not str or not agent for agent in lineage.authors + tuple(lineage.contributors))
    ):
        out.refuse("author_lineage_map", "lineage_missing", "a complete author/contributor map is required")
        return
    out.passed("author_lineage_map", ",".join(lineage.authors))
    excluded = frozenset(lineage.authors) | frozenset(lineage.contributors)
    events = list(snapshot.events)
    slots = (
        ("rco", RECOGNIZED_RCO_AGENTS, RCO_PASS_STATUS, True),
        ("build_lead", (LEAD_AGENT,), BUILD_CONSENSUS_STATUS, False),
        ("build_tools", (TOOLS_AGENT,), BUILD_CONSENSUS_STATUS, False),
    )
    chosen: dict[str, Mapping[str, Any]] = {}
    for role, agents, status, exact in slots:
        event, why = _select_approval(
            events, agents=agents, status=status, task_id=task_id, head_sha=head_sha,
            excluded=excluded if role == "rco" else frozenset(), require_exact=exact,
        )
        if event is None:
            reason = "rco_is_author_or_contributor" if why == "reviewer_is_author_or_contributor" else f"approval_{why}"
            out.refuse(f"approval_{role}", reason, f"no recognized exact-head {status} on {task_id}")
        else:
            chosen[role] = event
            out.passed(f"approval_{role}", f"{event.get('agent')} {bridge_event_id(event)}")
    if len(chosen) != len(slots):
        return
    uuids = [chosen[role].get("agent_uuid") for role, *_ in slots]
    if any(type(value) is not str or not value for value in uuids) or len(set(uuids)) != len(uuids):
        out.refuse("approval_identities_distinct", "approval_identity_duplicate", "three distinct agent_uuid values are required")
    else:
        out.passed("approval_identities_distinct")
    if not isinstance(registry, IdentityRegistry) or not isinstance(registry.entries, Mapping):
        out.unknown("approval_identity_binding", "identity_registry_missing")
    else:
        mismatched = []
        for role, event in chosen.items():
            binding = registry.entries.get(event.get("agent"))
            if (
                not isinstance(binding, IdentityBinding)
                or event.get("agent_uuid") != binding.agent_uuid
                or event.get("session_id") != binding.session_id
            ):
                mismatched.append(role)
        if mismatched:
            out.refuse("approval_identity_binding", "identity_binding_mismatch", ",".join(mismatched))
        else:
            out.passed("approval_identity_binding")
    if expected_requests is None:
        out.unknown("approval_request_binding", "expected_requests_missing")
    else:
        mismatched = []
        for role, event in chosen.items():
            payload = event.get("payload")
            bound = payload.get("lead_request") if isinstance(payload, Mapping) else None
            if type(expected_requests.get(role)) is not str or bound != expected_requests.get(role):
                mismatched.append(role)
        if mismatched:
            out.refuse("approval_request_binding", "request_binding_mismatch", ",".join(mismatched))
        else:
            out.passed("approval_request_binding")


def _check_refusal(out: _Collector, refusal: Any, *, task_id: str, pull_request: int) -> None:
    if refusal is None:
        out.unknown("autonomous_refusal", "refusal_absent_unknown", "not provided; never constructed here")
        return
    try:
        record, _ = assess_refusal(refusal, task_id=task_id, pull_request=pull_request)
    except ReceiptError as exc:
        out.refuse("autonomous_refusal", exc.reason, exc.detail)
        return
    if record["state"] == "absent_unknown":
        out.unknown("autonomous_refusal", "refusal_absent_unknown", record["absence_note"])
    else:
        out.passed("autonomous_refusal", f"preserved unchanged {record['event_id']}")


def _check_ci(out: _Collector, head_sha: str, *, runner: Runner | None, gh_executable: str) -> None:
    run = runner if runner is not None else _subprocess_runner
    try:
        required_raw = _run_gh(
            run, gh_executable, ["api", f"repos/{REPOSITORY}/branches/{BASE_REF_NAME}/protection/required_status_checks"]
        )
        if required_raw.returncode != 0:
            raise AdmissionError("required_checks_unknown", f"gh exit {required_raw.returncode}")
        required_doc = _strict_json(required_raw.stdout, "required checks")
        names = set()
        if isinstance(required_doc, dict):
            for name in required_doc.get("contexts") or []:
                if type(name) is str and name:
                    names.add(name)
            for item in required_doc.get("checks") or []:
                if isinstance(item, dict) and type(item.get("context")) is str and item["context"]:
                    names.add(item["context"])
        if not names:
            raise AdmissionError("required_checks_unknown", "no required check names could be read")
        runs_raw = _run_gh(run, gh_executable, ["api", f"repos/{REPOSITORY}/commits/{head_sha}/check-runs?per_page=100"])
        if runs_raw.returncode != 0:
            raise AdmissionError("ci_unknown", f"gh exit {runs_raw.returncode}")
        runs_doc = _strict_json(runs_raw.stdout, "check runs")
    except AdmissionError as exc:
        out.unknown("ci_required_checks", exc.reason, exc.detail)
        return
    runs = runs_doc.get("check_runs") if isinstance(runs_doc, dict) else None
    total = runs_doc.get("total_count") if isinstance(runs_doc, dict) else None
    if not isinstance(runs, list) or type(total) is not int or total != len(runs):
        out.unknown("ci_required_checks", "ci_partial", "check-run list is missing or paginated")
        return
    problems = []
    for name in sorted(names):
        matching = [item for item in runs if isinstance(item, dict) and item.get("name") == name]
        if not matching:
            problems.append(f"ci_missing:{name}")
            continue
        for item in matching:
            if item.get("head_sha") != head_sha:
                problems.append(f"ci_wrong_head:{name}")
            elif item.get("status") != "completed":
                problems.append(f"ci_pending:{name}")
            elif item.get("conclusion") in ("skipped", "neutral"):
                problems.append(f"ci_required_skipped:{name}")
            elif item.get("conclusion") != "success":
                problems.append(f"ci_failed:{name}")
    if problems:
        out.refuse("ci_required_checks", problems[0], ",".join(sorted(set(problems))))
    else:
        out.passed("ci_required_checks", ",".join(sorted(names)))


def _check_rate(out: _Collector, *, runner: Runner | None, gh_executable: str) -> None:
    try:
        raw = _run_gh(runner if runner is not None else _subprocess_runner, gh_executable, ["api", "rate_limit"])
        if raw.returncode != 0:
            raise AdmissionError("rate_unknown", f"gh exit {raw.returncode}")
        doc = _strict_json(raw.stdout, "rate limit")
        remaining = doc["resources"]["core"]["remaining"] if isinstance(doc, dict) else None
    except (AdmissionError, KeyError, TypeError) as exc:
        out.unknown("api_rate", "rate_unknown", getattr(exc, "detail", type(exc).__name__))
        return
    if type(remaining) is not int:
        out.unknown("api_rate", "rate_unknown", "remaining is not an int")
    elif remaining < MIN_RATE_REMAINING:
        out.refuse("api_rate", "rate_exhausted", str(remaining))
    else:
        out.passed("api_rate", str(remaining))


def preview_admission(
    *,
    pull_request: int,
    statement_bytes: bytes,
    signature_bytes: bytes,
    repo_root: Path,
    ssh_keygen: Path,
    now_utc: datetime,
    bridge: BridgeSnapshot,
    lineage: AuthorLineage | None,
    registry: IdentityRegistry | None,
    autonomous_refusal: AutonomousRefusalEvidence | None,
    expected_requests: Mapping[str, str] | None = None,
    batch_statements: Sequence[bytes] = (),
    expected_controls_digest: str | None = None,
    gh_runner: Runner | None = None,
    git_runner: Runner | None = None,
    ssh_runner: Runner | None = None,
    gh_executable: str = "gh",
    git_executable: str = "git",
) -> AdmissionPreview:
    """Evaluate admission for one PR without any effect; the verdict is ``refused`` or ``unknown``."""
    out = _Collector()
    controls_digest: str | None = None

    def finish() -> AdmissionPreview:
        verdict = VERDICT_REFUSED if any(c.status == CHECK_REFUSE for c in out.checks) else VERDICT_UNKNOWN
        return AdmissionPreview(
            schema=PREVIEW_SCHEMA,
            pull_request=pull_request,
            verdict=verdict,
            checks=tuple(out.checks),
            controls_digest=controls_digest,
        )

    try:
        statement = parse_statement(statement_bytes)
    except StatementError as exc:
        out.refuse("statement_parse", exc.reason, exc.detail)
        return finish()
    if statement.pull_request != pull_request:
        out.refuse("statement_pull_request", "pull_request_mismatch", str(statement.pull_request))
        return finish()
    out.passed("statement_parse")
    try:
        live = read_live_pull_request(pull_request, runner=gh_runner, gh_executable=gh_executable)
    except AdmissionError as exc:
        out.unknown("live_pr", exc.reason, exc.detail)
        return finish()
    out.add(
        "live_pr", CHECK_PASS if live.provenance == "subprocess_gh" else CHECK_UNKNOWN,
        "ok" if live.provenance == "subprocess_gh" else "live_pr_unit_mock", live.raw_sha256,
    )
    task_id = live.head_ref_name
    _check_live_pull_request(out, live, statement)
    live_paths = _check_statement(
        out, statement_bytes=statement_bytes, signature_bytes=signature_bytes, repo_root=repo_root, live=live,
        now_utc=now_utc, ssh_keygen=ssh_keygen, ssh_runner=ssh_runner, git_runner=git_runner,
        git_executable=git_executable,
    )
    _check_ancestry(out, repo_root=repo_root, live=live, git_runner=git_runner, git_executable=git_executable)
    _check_bootstrap(out, live_paths)
    _check_batch(out, statement, batch_statements, runner=gh_runner, gh_executable=gh_executable)
    if not isinstance(bridge, BridgeSnapshot) or not all(isinstance(e, Mapping) for e in bridge.events):
        out.refuse("bridge_snapshot", "bridge_snapshot_missing")
    else:
        controls_digest = _check_controls(
            out, bridge, task_id=task_id, pull_request=pull_request, head_sha=live.head_ref_oid,
            expected_controls_digest=expected_controls_digest,
        )
        _check_approvals(
            out, bridge, task_id=task_id, head_sha=live.head_ref_oid, lineage=lineage, registry=registry,
            expected_requests=expected_requests,
        )
    _check_refusal(out, autonomous_refusal, task_id=task_id, pull_request=pull_request)
    _check_ci(out, live.head_ref_oid, runner=gh_runner, gh_executable=gh_executable)
    _check_rate(out, runner=gh_runner, gh_executable=gh_executable)
    for name in EXPECTED_UNKNOWN_PREREQUISITES:
        out.unknown(f"prerequisite:{name}", "no_genuine_adapter_in_g2")
    return finish()


def execute(*_args: Any, **_kwargs: Any) -> None:
    """Execution is not available in this slice; nothing is merged, reserved or written."""
    raise AdmissionError("execute_unavailable", "G2 delivers a read-only preview only")
