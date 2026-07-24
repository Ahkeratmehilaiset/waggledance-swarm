# SPDX-License-Identifier: BUSL-1.1
"""Fail-closed bridge-agent ownership resolution for pull requests.

GitHub authorship is metadata in this repository because the operator account
often pushes commits produced by bridge agents.  Merge gates therefore derive
one canonical agent owner from UUID-bound bridge claims and use Git identities
only as conflict evidence.  This module is intentionally pure and read-only so
every promotion surface can apply the same policy.
"""

from __future__ import annotations

from pathlib import PurePosixPath
import re
from typing import Any, Mapping, Sequence

from waggledance.core.bridge_identity_registry import (
    AGENT_ID_PATTERN,
    AGENT_UUID_PATTERN,
    bridge_identity_binding_status,
    load_bridge_identity_registry,
)

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
GIT_IDENTITY_EVIDENCE_SCHEMA = "wd.github_pr_git_identity_evidence.v1"
_COMMIT_AUTHOR_SOURCE_RE = re.compile(r"^commit_author:([1-9][0-9]*):([1-9][0-9]*)$")
_TASK_PAYLOAD_KEYS = ("canonical_task_id", "task_id", "task")
_BRANCH_PAYLOAD_KEYS = ("branch", "branch_name", "head_ref", "headRefName")
_HEAD_PAYLOAD_KEYS = (
    "head",
    "head_sha",
    "exact_head",
    "head_oid",
    "headRefOid",
)
_PR_PAYLOAD_KEYS = ("pr", "pr_number")


def github_pr_git_identities(view: Mapping[str, Any]) -> list[dict[str, str]]:
    """Normalize public PR/commit author metadata from ``gh pr view``.

    ``author`` and ``commits`` must both be present.  Empty or malformed commit
    authors fail closed instead of silently removing possible conflict
    evidence.
    """

    material = github_pr_git_identity_evidence(view)
    return list(material["identities"])


def github_pr_git_identity_evidence(
    view: Mapping[str, Any],
    *,
    expected_head_sha: str = "",
) -> dict[str, Any]:
    """Normalize complete PR/commit identity evidence.

    The caller may bind the material to an exact PR head.  When bound, the
    final commit record must be that head; an incomplete or stale commit list
    is refused instead of silently treated as contributor evidence.
    """

    if not isinstance(view, Mapping):
        raise ValueError("GitHub PR view must be an object")
    if "author" not in view:
        raise ValueError("GitHub PR view missing author metadata")
    if "commits" not in view:
        raise ValueError("GitHub PR view missing commits metadata")
    if type(expected_head_sha) is not str:
        raise ValueError("expected_head_sha must be a string")
    if expected_head_sha:
        expected_head_sha = _required_sha(
            expected_head_sha,
            "expected_head_sha",
        )

    author = view.get("author")
    if not isinstance(author, Mapping):
        raise ValueError("GitHub PR author metadata must be an object")
    identities = [
        _normalize_git_identity(author, source="pr_author", commit_oid="")
    ]

    commits = view.get("commits")
    if not isinstance(commits, list) or not commits:
        raise ValueError("GitHub PR view commits must be a non-empty list")
    commit_oids: list[str] = []
    commit_author_count = 0
    for index, commit in enumerate(commits, 1):
        if not isinstance(commit, Mapping):
            raise ValueError(f"GitHub PR commit {index} must be an object")
        oid = commit.get("oid")
        if type(oid) is not str or not SHA_RE.fullmatch(oid):
            raise ValueError(f"GitHub PR commit {index} has invalid oid")
        if oid in commit_oids:
            raise ValueError(f"GitHub PR commit {index} repeats oid {oid}")
        commit_oids.append(oid)
        authors = commit.get("authors")
        if not isinstance(authors, list) or not authors:
            raise ValueError(
                f"GitHub PR commit {index} authors must be a non-empty list"
            )
        for author_index, commit_author in enumerate(authors, 1):
            identities.append(
                _normalize_git_identity(
                    commit_author,
                    source=f"commit_author:{index}:{author_index}",
                    commit_oid=oid,
                )
            )
            commit_author_count += 1
    if expected_head_sha and commit_oids[-1] != expected_head_sha:
        raise ValueError(
            "GitHub PR commit evidence does not end at the exact head"
        )
    head_sha = expected_head_sha or commit_oids[-1]
    return {
        "schema": GIT_IDENTITY_EVIDENCE_SCHEMA,
        "complete": True,
        "head_sha": head_sha,
        "pr_author_count": 1,
        "commit_count": len(commit_oids),
        "commit_oids": commit_oids,
        "commit_author_count": commit_author_count,
        "identity_count": len(identities),
        "identities": identities,
    }


def resolve_bridge_pr_author(
    *,
    events: Sequence[Mapping[str, Any]],
    pr_number: int,
    task_id: str,
    head_ref_name: str,
    head_sha: str,
    base_sha: str,
    changed_paths: Sequence[str],
    expected_head_sha: str = "",
    expected_base_sha: str = "",
    git_identities: Sequence[Mapping[str, Any]] = (),
    git_identity_evidence: Mapping[str, Any] | None = None,
    asserted_author_agent: str = "",
    identity_registry: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve exactly one UUID-bound canonical PR owner.

    The result never guesses.  ``ok=False`` always routes the caller to manual
    operator review.  A human Git identity that maps to no registered bridge
    agent is retained in ``unbound_git_identities`` but does not block or grant
    ownership.
    """

    base_report: dict[str, Any] = {
        "ok": False,
        "decision": "manual_operator_review_required",
        "operator_review_required": True,
        "author_agent": "",
        "reasons": [],
        "pr_number": pr_number if type(pr_number) is int else None,
        "task_id": task_id if type(task_id) is str else "",
        "head_ref_name": head_ref_name if type(head_ref_name) is str else "",
        "head_sha": head_sha if type(head_sha) is str else "",
        "base_sha": base_sha if type(base_sha) is str else "",
        "branch_prefix_agent": "",
        "canonical_owner_candidates": [],
        "contributor_claim_agents": [],
        "covered_paths": [],
        "uncovered_paths": [],
        "recognized_git_agents": [],
        "unbound_git_identities": [],
        "claim_event_indexes": [],
        "ignored_irrelevant_claim_indexes": [],
    }
    try:
        registry = _validated_registry(identity_registry)
        pr_number = _positive_int(pr_number, "pr_number")
        task_id = _required_exact_string(task_id, "task_id")
        head_ref_name = _required_exact_string(head_ref_name, "head_ref_name")
        head_sha = _required_sha(head_sha, "head_sha")
        base_sha = _required_sha(base_sha, "base_sha")
        expected_head_sha = _optional_expected_sha(
            expected_head_sha,
            "expected_head_sha",
            default=head_sha,
        )
        expected_base_sha = _optional_expected_sha(
            expected_base_sha,
            "expected_base_sha",
            default=base_sha,
        )
        if task_id != head_ref_name:
            return _refusal(
                base_report,
                "canonical task_id must exactly equal the PR headRefName",
            )
        if head_sha != expected_head_sha:
            return _refusal(base_report, "PR head drifted from the expected head")
        if base_sha != expected_base_sha:
            return _refusal(base_report, "PR base drifted from the expected base")

        normalized_paths = _normalize_repo_paths(
            changed_paths,
            "changed_paths",
            allow_wildcard=False,
        )
        if not normalized_paths:
            raise ValueError("changed_paths must be a non-empty list")
        if not isinstance(events, Sequence) or isinstance(
            events, (str, bytes, bytearray)
        ):
            raise ValueError("events must be a sequence of objects")

        canonical_claims: list[dict[str, Any]] = []
        contributor_claims: list[dict[str, Any]] = []
        claim_errors: list[str] = []
        irrelevant_claim_indexes: list[int] = []
        for index, event in enumerate(events):
            if not isinstance(event, Mapping):
                raise ValueError(f"events item {index} must be an object")
            if event.get("type") != "claim":
                continue
            classification = _classify_claim(
                event=event,
                index=index,
                registry=registry,
                pr_number=pr_number,
                task_id=task_id,
                head_ref_name=head_ref_name,
                head_sha=head_sha,
                changed_paths=normalized_paths,
            )
            if classification["kind"] == "irrelevant":
                irrelevant_claim_indexes.append(index)
                continue
            if classification["kind"] == "invalid":
                claim_errors.extend(classification["reasons"])
                continue
            if classification["kind"] == "canonical":
                canonical_claims.append(classification)
            else:
                contributor_claims.append(classification)

        base_report["ignored_irrelevant_claim_indexes"] = irrelevant_claim_indexes
        base_report["claim_event_indexes"] = [
            int(item["index"]) for item in (*canonical_claims, *contributor_claims)
        ]
        if claim_errors:
            return _refusal(base_report, *claim_errors)
        owners = sorted({str(item["agent"]) for item in canonical_claims})
        base_report["canonical_owner_candidates"] = owners
        if contributor_claims:
            agents = sorted({str(item["agent"]) for item in contributor_claims})
            base_report["contributor_claim_agents"] = agents
            return _refusal(
                base_report,
                "non-canonical contributor claim(s) bind this PR: "
                + ", ".join(agents),
            )

        if not owners:
            return _refusal(
                base_report,
                "no valid UUID-bound canonical write claim covers this PR",
            )
        if len(owners) != 1:
            return _refusal(
                base_report,
                "multiple canonical claim owners require operator review: "
                + ", ".join(owners),
            )
        owner = owners[0]

        covered = {
            path
            for claim in canonical_claims
            if claim["agent"] == owner
            for path in claim["covered_paths"]
        }
        uncovered = sorted(set(normalized_paths) - covered)
        base_report["covered_paths"] = sorted(covered)
        base_report["uncovered_paths"] = uncovered
        if uncovered:
            return _refusal(
                base_report,
                "canonical claim scope does not cover every changed path: "
                + ", ".join(uncovered),
            )

        prefix = head_ref_name.split("/", 1)[0]
        branch_prefix_agent = prefix if prefix in registry else ""
        base_report["branch_prefix_agent"] = branch_prefix_agent
        if branch_prefix_agent and branch_prefix_agent != owner:
            return _refusal(
                base_report,
                "registered branch prefix conflicts with canonical claim owner: "
                f"{branch_prefix_agent} != {owner}",
            )

        if type(asserted_author_agent) is not str:
            raise ValueError("asserted_author_agent must be a string")
        if asserted_author_agent != asserted_author_agent.strip():
            raise ValueError("asserted_author_agent must be an exact string")
        asserted = asserted_author_agent
        if asserted:
            if asserted not in registry:
                return _refusal(
                    base_report,
                    f"asserted author_agent is not registered: {asserted}",
                )
            if asserted != owner:
                return _refusal(
                    base_report,
                    f"asserted author_agent conflicts with canonical owner: "
                    f"{asserted} != {owner}",
                )

        git_report = _classify_git_identities(
            git_identities=git_identities,
            git_identity_evidence=git_identity_evidence,
            head_sha=head_sha,
            registry=registry,
        )
        base_report["recognized_git_agents"] = git_report["recognized_agents"]
        base_report["unbound_git_identities"] = git_report["unbound_identities"]
        if git_report["reasons"]:
            return _refusal(base_report, *git_report["reasons"])
        recognized_agents = set(git_report["recognized_agents"])
        if recognized_agents and recognized_agents != {owner}:
            return _refusal(
                base_report,
                "recognized Git-agent identity conflicts with canonical owner: "
                + ", ".join(sorted(recognized_agents))
                + f" != {owner}",
            )

        return {
            **base_report,
            "ok": True,
            "decision": "resolved",
            "operator_review_required": False,
            "author_agent": owner,
            "reasons": [],
            "pr_number": pr_number,
            "task_id": task_id,
            "head_ref_name": head_ref_name,
            "head_sha": head_sha,
            "base_sha": base_sha,
        }
    except ValueError as exc:
        return {
            **base_report,
            "decision": "invalid_author_evidence",
            "reasons": [str(exc)],
        }


def _classify_claim(
    *,
    event: Mapping[str, Any],
    index: int,
    registry: Mapping[str, str],
    pr_number: int,
    task_id: str,
    head_ref_name: str,
    head_sha: str,
    changed_paths: Sequence[str],
) -> dict[str, Any]:
    event_task_value = event.get("task_id", "")
    event_task = event_task_value if type(event_task_value) is str else ""
    payload_raw = event.get("payload", {})
    payload = payload_raw if isinstance(payload_raw, Mapping) else None
    exact_task = event_task == task_id
    if payload is None:
        if exact_task:
            return _invalid_claim(index, "canonical claim payload must be an object")
        return {"kind": "irrelevant", "index": index}

    payload_task_matches = _payload_exact_match(
        payload, _TASK_PAYLOAD_KEYS, {task_id, head_ref_name}
    )
    payload_branch_matches = _payload_exact_match(
        payload, _BRANCH_PAYLOAD_KEYS, {head_ref_name}
    )
    payload_pr_matches = _payload_int_match(payload, _PR_PAYLOAD_KEYS, pr_number)
    payload_head_matches = _payload_exact_match(
        payload, _HEAD_PAYLOAD_KEYS, {head_sha}
    )
    relevant = (
        exact_task
        or payload_task_matches
        or payload_branch_matches
        or payload_pr_matches
        or payload_head_matches
    )
    if not relevant:
        return {"kind": "irrelevant", "index": index}

    conflicts: list[str] = []
    conflicts.extend(
        _payload_conflicts(payload, _TASK_PAYLOAD_KEYS, {task_id, head_ref_name})
    )
    conflicts.extend(
        _payload_conflicts(payload, _BRANCH_PAYLOAD_KEYS, {head_ref_name})
    )
    conflicts.extend(_payload_conflicts(payload, _HEAD_PAYLOAD_KEYS, {head_sha}))
    conflicts.extend(_payload_int_conflicts(payload, _PR_PAYLOAD_KEYS, pr_number))
    if (
        type(event_task_value) is not str
        or not event_task_value
        or event_task_value != event_task_value.strip()
    ):
        conflicts.append("task_id")
    if conflicts:
        return _invalid_claim(
            index,
            "claim has conflicting structured binding(s): " + ", ".join(conflicts),
        )

    agent_value = event.get("agent", "")
    if (
        type(agent_value) is not str
        or not agent_value
        or agent_value != agent_value.strip()
    ):
        return _invalid_claim(
            index,
            "claim agent must be a non-empty exact string",
        )
    agent = agent_value
    if agent not in registry:
        return _invalid_claim(
            index,
            f"claim agent is not registered: {agent or '<missing>'}",
        )
    binding = bridge_identity_binding_status(
        event,
        registry=registry,
        restricted_agents=set(registry),
    )
    if binding != "valid":
        return _invalid_claim(
            index,
            f"claim identity binding is {binding} for {agent}",
        )
    try:
        scope = _claim_scope(event, payload)
    except ValueError as exc:
        return _invalid_claim(index, str(exc))
    covered = sorted(
        path
        for path in changed_paths
        if any(_scope_covers_path(scope_entry, path) for scope_entry in scope)
    )
    if not exact_task:
        if not covered:
            return {"kind": "irrelevant", "index": index}
        return {
            "kind": "contributor",
            "index": index,
            "agent": agent,
            "covered_paths": covered,
        }
    return {
        "kind": "canonical",
        "index": index,
        "agent": agent,
        "covered_paths": covered,
    }


def _claim_scope(
    event: Mapping[str, Any], payload: Mapping[str, Any]
) -> tuple[str, ...]:
    top_present = "write_scope" in event
    payload_present = "write_scope" in payload
    top = (
        _normalize_repo_paths(
            event.get("write_scope"),
            "claim write_scope",
            allow_wildcard=True,
        )
        if top_present
        else ()
    )
    nested = (
        _normalize_repo_paths(
            payload.get("write_scope"),
            "claim payload.write_scope",
            allow_wildcard=True,
        )
        if payload_present
        else ()
    )
    if top and nested and set(top) != set(nested):
        raise ValueError("claim top-level and payload write_scope disagree")
    scope = top or nested
    if not scope:
        raise ValueError("claim write_scope must cover at least one repository path")
    return scope


def _classify_git_identities(
    *,
    git_identities: Sequence[Mapping[str, Any]],
    git_identity_evidence: Mapping[str, Any] | None,
    head_sha: str,
    registry: Mapping[str, str],
) -> dict[str, Any]:
    if not isinstance(git_identities, Sequence) or isinstance(
        git_identities, (str, bytes, bytearray)
    ):
        raise ValueError("git_identities must be a sequence of objects")
    _validate_git_identity_evidence(
        git_identity_evidence,
        git_identities=git_identities,
        head_sha=head_sha,
    )
    recognized: set[str] = set()
    unbound: list[dict[str, str]] = []
    reasons: list[str] = []
    for index, value in enumerate(git_identities):
        if not isinstance(value, Mapping):
            raise ValueError(f"git_identities item {index} must be an object")
        source = value.get("source")
        commit_oid = value.get("commit_oid")
        if type(source) is not str or not source:
            raise ValueError(
                f"git_identities item {index} source must be a non-empty string"
            )
        if type(commit_oid) is not str:
            raise ValueError(
                f"git_identities item {index} commit_oid must be a string"
            )
        identity = _normalize_git_identity(
            value,
            source=source,
            commit_oid=commit_oid,
        )
        candidates = _git_identity_candidates(identity, registry=registry)
        if len(candidates) > 1:
            reasons.append(
                "one Git identity maps to multiple registered agents: "
                + ", ".join(sorted(candidates))
            )
        elif candidates:
            recognized.update(candidates)
        else:
            unbound.append(identity)
    if len(recognized) > 1:
        reasons.append(
            "multiple recognized Git-agent contributors require operator review: "
            + ", ".join(sorted(recognized))
        )
    return {
        "recognized_agents": sorted(recognized),
        "unbound_identities": unbound,
        "reasons": reasons,
    }


def _git_identity_candidates(
    identity: Mapping[str, str], *, registry: Mapping[str, str]
) -> set[str]:
    name = identity.get("name", "").casefold()
    login = identity.get("login", "").casefold()
    email = identity.get("email", "").casefold()
    email_local = email.split("@", 1)[0] if "@" in email else ""
    matches: set[str] = set()
    for agent in registry:
        token = agent.casefold()
        if name == token or login == token:
            matches.add(agent)
        if email_local == token or email_local.startswith(token + "+"):
            matches.add(agent)
    return matches


def _normalize_git_identity(
    value: object, *, source: str, commit_oid: str
) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{source} Git identity must be an object")
    if type(source) is not str or not source:
        raise ValueError("Git identity source must be a non-empty string")
    if type(commit_oid) is not str:
        raise ValueError(f"{source} Git identity commit_oid must be a string")
    normalized_fields: dict[str, str] = {}
    for key in ("name", "email", "login"):
        raw = value.get(key, "")
        if type(raw) is not str:
            raise ValueError(f"{source} Git identity {key} must be a string")
        if raw != raw.strip():
            raise ValueError(
                f"{source} Git identity {key} must be an exact string"
            )
        normalized_fields[key] = raw
    if source != source.strip():
        raise ValueError("Git identity source must be an exact string")
    normalized = {
        "source": source,
        "commit_oid": commit_oid,
        **normalized_fields,
    }
    if normalized["commit_oid"] and not SHA_RE.fullmatch(normalized["commit_oid"]):
        raise ValueError(f"{source} Git identity has invalid commit_oid")
    if not any(normalized[key] for key in ("name", "email", "login")):
        raise ValueError(f"{source} Git identity has no name, email, or login")
    return normalized


def _validate_git_identity_evidence(
    value: Mapping[str, Any] | None,
    *,
    git_identities: Sequence[Mapping[str, Any]],
    head_sha: str,
) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("git_identity_evidence must be a complete evidence object")
    expected_keys = {
        "schema",
        "complete",
        "head_sha",
        "pr_author_count",
        "commit_count",
        "commit_oids",
        "commit_author_count",
        "identity_count",
    }
    if set(value) != expected_keys:
        raise ValueError("git_identity_evidence has an incomplete or unknown schema")
    if value.get("schema") != GIT_IDENTITY_EVIDENCE_SCHEMA:
        raise ValueError("git_identity_evidence schema is not recognized")
    if value.get("complete") is not True:
        raise ValueError("git_identity_evidence is not complete")
    if value.get("head_sha") != head_sha:
        raise ValueError("git_identity_evidence does not match the exact head")

    counts: dict[str, int] = {}
    for key in (
        "pr_author_count",
        "commit_count",
        "commit_author_count",
        "identity_count",
    ):
        raw = value.get(key)
        if type(raw) is not int or raw < 1:
            raise ValueError(f"git_identity_evidence {key} must be a positive integer")
        counts[key] = raw
    if counts["pr_author_count"] != 1:
        raise ValueError("git_identity_evidence must contain exactly one PR author")
    if counts["identity_count"] != len(git_identities):
        raise ValueError("git_identity_evidence identity count does not match")
    if counts["identity_count"] != (
        counts["pr_author_count"] + counts["commit_author_count"]
    ):
        raise ValueError("git_identity_evidence author counts do not add up")

    commit_oids = value.get("commit_oids")
    if not isinstance(commit_oids, list):
        raise ValueError("git_identity_evidence commit_oids must be a list")
    if len(commit_oids) != counts["commit_count"]:
        raise ValueError("git_identity_evidence commit count does not match")
    if any(type(oid) is not str or not SHA_RE.fullmatch(oid) for oid in commit_oids):
        raise ValueError("git_identity_evidence contains an invalid commit oid")
    if len(set(commit_oids)) != len(commit_oids):
        raise ValueError("git_identity_evidence contains duplicate commit oids")
    if commit_oids[-1] != head_sha:
        raise ValueError("git_identity_evidence does not end at the exact head")

    pr_author_sources = 0
    authors_by_commit: dict[int, set[int]] = {}
    seen_sources: set[tuple[int, int]] = set()
    for index, identity in enumerate(git_identities):
        if not isinstance(identity, Mapping):
            raise ValueError(f"git_identities item {index} must be an object")
        source = identity.get("source")
        commit_oid = identity.get("commit_oid")
        if source == "pr_author":
            pr_author_sources += 1
            if commit_oid != "":
                raise ValueError("PR author identity must not bind a commit oid")
            continue
        if type(source) is not str:
            raise ValueError(f"git_identities item {index} source must be a string")
        match = _COMMIT_AUTHOR_SOURCE_RE.fullmatch(source)
        if match is None:
            raise ValueError(
                f"git_identities item {index} has an unknown source"
            )
        commit_index = int(match.group(1))
        author_index = int(match.group(2))
        source_index = (commit_index, author_index)
        if source_index in seen_sources:
            raise ValueError("git identity evidence repeats an author source")
        seen_sources.add(source_index)
        if commit_index > len(commit_oids):
            raise ValueError("git identity references an unknown commit index")
        if commit_oid != commit_oids[commit_index - 1]:
            raise ValueError("git identity commit oid does not match its source")
        authors_by_commit.setdefault(commit_index, set()).add(author_index)
    if pr_author_sources != 1:
        raise ValueError("git identity evidence must include exactly one PR author")
    if set(authors_by_commit) != set(range(1, len(commit_oids) + 1)):
        raise ValueError("git identity evidence omits a commit author list")
    if len(seen_sources) != counts["commit_author_count"]:
        raise ValueError("git identity evidence commit author count does not match")
    for author_indexes in authors_by_commit.values():
        if author_indexes != set(range(1, len(author_indexes) + 1)):
            raise ValueError("git identity evidence has incomplete author indexes")


def _validated_registry(
    identity_registry: Mapping[str, str] | None,
) -> dict[str, str]:
    if identity_registry is not None and not isinstance(
        identity_registry, Mapping
    ):
        raise ValueError("identity_registry must be an object")
    raw = (
        load_bridge_identity_registry()
        if identity_registry is None
        else dict(identity_registry)
    )
    if not raw:
        raise ValueError("bridge identity registry must not be empty")
    registry: dict[str, str] = {}
    seen_uuids: set[str] = set()
    for agent, agent_uuid in raw.items():
        if not isinstance(agent, str) or not AGENT_ID_PATTERN.fullmatch(agent):
            raise ValueError(f"invalid bridge identity registry agent: {agent!r}")
        if not isinstance(agent_uuid, str) or not AGENT_UUID_PATTERN.fullmatch(
            agent_uuid
        ):
            raise ValueError(f"invalid bridge identity registry UUID for {agent}")
        normalized_uuid = agent_uuid.lower()
        if normalized_uuid in seen_uuids:
            raise ValueError("bridge identity registry UUIDs must be unique")
        seen_uuids.add(normalized_uuid)
        registry[agent] = agent_uuid
    return registry


def _normalize_repo_paths(
    value: object,
    field: str,
    *,
    allow_wildcard: bool,
) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray)
    ):
        raise ValueError(f"{field} must be a list of repository paths")
    normalized: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if type(item) is not str:
            raise ValueError(f"{field} item {index} must be a string")
        if item != item.strip():
            raise ValueError(f"{field} item {index} must be an exact path")
        if "\\" in item:
            raise ValueError(
                f"{field} item {index} is not a canonical repository path"
            )
        path = item
        if not path:
            raise ValueError(f"{field} item {index} must not be empty")
        if path.startswith("/") or any(ord(character) < 32 for character in path):
            raise ValueError(f"{field} item {index} is not a safe repository path")
        if path == "*" and allow_wildcard:
            candidate = path
        else:
            if path.endswith("/") or "//" in path:
                raise ValueError(
                    f"{field} item {index} is not a canonical repository path"
                )
            if "*" in path:
                raise ValueError(f"{field} item {index} contains a wildcard")
            pure = PurePosixPath(path)
            if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
                raise ValueError(f"{field} item {index} is not a safe repository path")
            if ":" in path:
                raise ValueError(f"{field} item {index} is not a repository path")
            candidate = pure.as_posix()
            if candidate != path:
                raise ValueError(
                    f"{field} item {index} is not a canonical repository path"
                )
        if candidate not in seen:
            seen.add(candidate)
            normalized.append(candidate)
    return tuple(normalized)


def _scope_covers_path(scope: str, path: str) -> bool:
    return scope == "*" or scope == path or path.startswith(scope + "/")


def _payload_exact_match(
    payload: Mapping[str, Any], keys: Sequence[str], expected: set[str]
) -> bool:
    return any(
        type(payload.get(key)) is str
        and payload.get(key) in expected
        for key in keys
        if key in payload
    )


def _payload_int_match(
    payload: Mapping[str, Any], keys: Sequence[str], expected: int
) -> bool:
    for key in keys:
        if key not in payload:
            continue
        value = payload.get(key)
        if type(value) is int and value == expected:
            return True
    return False


def _payload_conflicts(
    payload: Mapping[str, Any], keys: Sequence[str], expected: set[str]
) -> list[str]:
    conflicts: list[str] = []
    for key in keys:
        if key not in payload:
            continue
        value = payload.get(key)
        if type(value) is not str or value not in expected:
            conflicts.append(key)
    return conflicts


def _payload_int_conflicts(
    payload: Mapping[str, Any], keys: Sequence[str], expected: int
) -> list[str]:
    conflicts: list[str] = []
    for key in keys:
        if key not in payload:
            continue
        value = payload.get(key)
        if type(value) is not int or value != expected:
            conflicts.append(key)
    return conflicts


def _invalid_claim(index: int, reason: str) -> dict[str, Any]:
    return {
        "kind": "invalid",
        "index": index,
        "reasons": [f"claim event {index}: {reason}"],
    }


def _refusal(base: Mapping[str, Any], *reasons: str) -> dict[str, Any]:
    return {
        **base,
        "ok": False,
        "decision": "manual_operator_review_required",
        "operator_review_required": True,
        "author_agent": "",
        "reasons": [str(reason) for reason in reasons if str(reason)],
    }


def _required_exact_string(value: object, field: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{field} must be a non-empty exact string")
    return value


def _required_sha(value: object, field: str) -> str:
    if type(value) is not str or not SHA_RE.fullmatch(value):
        raise ValueError(f"{field} must be a 40-char lowercase sha")
    return value


def _optional_expected_sha(
    value: object,
    field: str,
    *,
    default: str,
) -> str:
    if type(value) is not str:
        raise ValueError(f"{field} must be a string")
    if not value:
        return default
    return _required_sha(value, field)


def _positive_int(value: object, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


__all__ = [
    "GIT_IDENTITY_EVIDENCE_SCHEMA",
    "github_pr_git_identity_evidence",
    "github_pr_git_identities",
    "resolve_bridge_pr_author",
]
