# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from copy import deepcopy

import pytest

from tools.bridge_pr_author import (
    GIT_IDENTITY_EVIDENCE_SCHEMA,
    github_pr_git_identity_evidence,
    github_pr_git_identities,
    resolve_bridge_pr_author,
)

HEAD = "1234567890abcdef1234567890abcdef12345678"
BASE = "abcdef1234567890abcdef1234567890abcdef12"
TASK = "codex-lead-1/unified-author-fixture"
PATHS = ["tools/one.py", "tests/tools/test_one.py"]
REGISTRY = {
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
}


def _git_material(*, commit_name: str = "Jani") -> tuple[list[dict], dict]:
    material = github_pr_git_identity_evidence(
        {
            "author": {
                "login": "Ahkeratmehilaiset",
                "name": "",
                "email": "",
            },
            "commits": [
                {
                    "oid": HEAD,
                    "authors": [
                        {
                            "name": commit_name,
                            "email": "jani@jkhservice.fi",
                            "login": "",
                        }
                    ],
                }
            ],
        },
        expected_head_sha=HEAD,
    )
    identities = list(material.pop("identities"))
    return identities, material


def _claim(
    agent: str = "codex-lead-1",
    *,
    task_id: str = TASK,
    scope: list[str] | None = None,
    payload: dict | None = None,
) -> dict:
    return {
        "agent": agent,
        "agent_uuid": REGISTRY[agent],
        "type": "claim",
        "status": "active",
        "task_id": task_id,
        "write_scope": PATHS if scope is None else scope,
        "payload": {} if payload is None else payload,
    }


def _resolve(
    *,
    events: list[dict] | None = None,
    task_id: str = TASK,
    head_ref_name: str = TASK,
    changed_paths: list[str] | None = None,
    git_identities: list[dict] | None = None,
    git_identity_evidence: dict | None = None,
    asserted_author_agent: object = "",
    registry: dict[str, str] | None = None,
    expected_head_sha: str = HEAD,
    expected_base_sha: str = BASE,
    pr_number: object = 1551,
) -> dict:
    default_identities, default_evidence = _git_material()
    identities = default_identities if git_identities is None else git_identities
    evidence = (
        default_evidence
        if git_identity_evidence is None and git_identities is None
        else git_identity_evidence
    )
    return resolve_bridge_pr_author(
        events=[_claim()] if events is None else events,
        pr_number=pr_number,  # type: ignore[arg-type]
        task_id=task_id,
        head_ref_name=head_ref_name,
        head_sha=HEAD,
        base_sha=BASE,
        expected_head_sha=expected_head_sha,
        expected_base_sha=expected_base_sha,
        changed_paths=PATHS if changed_paths is None else changed_paths,
        git_identities=identities,
        git_identity_evidence=evidence,
        asserted_author_agent=asserted_author_agent,  # type: ignore[arg-type]
        identity_registry=REGISTRY if registry is None else registry,
    )


def test_resolves_uuid_bound_exact_task_claim_with_complete_scope() -> None:
    report = _resolve()

    assert report["ok"] is True
    assert report["decision"] == "resolved"
    assert report["author_agent"] == "codex-lead-1"
    assert report["branch_prefix_agent"] == "codex-lead-1"
    assert report["covered_paths"] == sorted(PATHS)
    assert report["uncovered_paths"] == []


def test_human_git_identity_is_metadata_not_an_agent_alias() -> None:
    report = _resolve()

    assert report["ok"] is True
    assert report["recognized_git_agents"] == []
    assert report["unbound_git_identities"][1]["name"] == "Jani"


def test_recognized_git_agent_conflict_routes_operator_review() -> None:
    identities, evidence = _git_material(commit_name="codex-tools-1")
    report = _resolve(
        git_identities=identities,
        git_identity_evidence=evidence,
    )

    assert report["ok"] is False
    assert report["operator_review_required"] is True
    assert "conflicts with canonical owner" in report["reasons"][-1]


def test_multiple_canonical_claim_owners_never_use_first_or_last() -> None:
    report = _resolve(events=[_claim(), _claim("codex-tools-1")])

    assert report["ok"] is False
    assert report["canonical_owner_candidates"] == [
        "codex-lead-1",
        "codex-tools-1",
    ]
    assert "multiple canonical claim owners" in report["reasons"][0]


def test_noncanonical_contributor_claim_binding_pr_routes_review() -> None:
    contributor = _claim(
        "codex-tools-1",
        task_id="codex-tools-1/contributor",
        scope=["tools/one.py"],
        payload={"pr": 1551},
    )
    report = _resolve(events=[_claim(), contributor])

    assert report["ok"] is False
    assert report["canonical_owner_candidates"] == ["codex-lead-1"]
    assert report["contributor_claim_agents"] == ["codex-tools-1"]
    assert "non-canonical contributor claim" in report["reasons"][0]


def test_claim_payload_with_other_exact_head_is_conflicting_evidence() -> None:
    report = _resolve(events=[_claim(payload={"head": "0" * 40})])

    assert report["ok"] is False
    assert "conflicting structured binding" in report["reasons"][0]
    assert "head" in report["reasons"][0]


def test_every_changed_path_must_be_covered_by_valid_claim_scope() -> None:
    report = _resolve(events=[_claim(scope=["tools/one.py"])])

    assert report["ok"] is False
    assert report["uncovered_paths"] == ["tests/tools/test_one.py"]


def test_absolute_or_parent_escaping_claim_scope_fails_closed() -> None:
    absolute = _resolve(events=[_claim(scope=["/tools/one.py", *PATHS])])
    parent = _resolve(events=[_claim(scope=["tools/../tests", *PATHS])])

    assert absolute["ok"] is False
    assert "safe repository path" in absolute["reasons"][0]
    assert parent["ok"] is False
    assert "safe repository path" in parent["reasons"][0]


def test_changed_paths_cannot_use_scope_wildcards() -> None:
    report = _resolve(changed_paths=["*"])

    assert report["ok"] is False
    assert report["decision"] == "invalid_author_evidence"
    assert "wildcard" in report["reasons"][0]


def test_same_owner_claim_scopes_may_combine_without_order_dependence() -> None:
    first = _claim(scope=["tools/one.py"])
    second = _claim(scope=["tests/tools/test_one.py"])

    forward = _resolve(events=[first, second])
    reverse = _resolve(events=[second, first])

    assert forward["ok"] is True
    assert reverse["ok"] is True
    assert forward["author_agent"] == reverse["author_agent"] == "codex-lead-1"


def test_payload_scope_is_accepted_when_top_level_scope_is_empty() -> None:
    claim = _claim(scope=[], payload={"write_scope": PATHS, "task": TASK})

    report = _resolve(events=[claim])

    assert report["ok"] is True


def test_disagreeing_top_level_and_payload_scope_fails_closed() -> None:
    claim = _claim(
        scope=["tools/one.py"],
        payload={"write_scope": ["tests/tools/test_one.py"]},
    )

    report = _resolve(events=[claim])

    assert report["ok"] is False
    assert "write_scope disagree" in report["reasons"][0]


def test_uuid_mismatch_and_missing_registry_fail_closed() -> None:
    forged = _claim()
    forged["agent_uuid"] = REGISTRY["codex-tools-1"]
    forged_report = _resolve(events=[forged])
    missing_report = _resolve(registry={})

    assert forged_report["ok"] is False
    assert "identity binding is mismatch_uuid" in forged_report["reasons"][0]
    assert missing_report["ok"] is False
    assert missing_report["decision"] == "invalid_author_evidence"


def test_task_branch_prefix_and_expected_ref_drift_fail_closed() -> None:
    task_mismatch = _resolve(head_ref_name="codex-lead-1/other")
    prefix_mismatch = _resolve(
        task_id="codex-tools-1/unified-author-fixture",
        head_ref_name="codex-tools-1/unified-author-fixture",
        events=[
            _claim(
                task_id="codex-tools-1/unified-author-fixture",
            )
        ],
    )
    head_drift = _resolve(expected_head_sha="0" * 40)
    base_drift = _resolve(expected_base_sha="f" * 40)

    assert task_mismatch["ok"] is False
    assert "exactly equal" in task_mismatch["reasons"][0]
    assert prefix_mismatch["ok"] is False
    assert "branch prefix conflicts" in prefix_mismatch["reasons"][0]
    assert "head drifted" in head_drift["reasons"][0]
    assert "base drifted" in base_drift["reasons"][0]


def test_asserted_author_is_only_conflict_evidence_not_ownership() -> None:
    conflict = _resolve(asserted_author_agent="codex-tools-1")
    no_claim = _resolve(events=[], asserted_author_agent="codex-lead-1")

    assert conflict["ok"] is False
    assert "asserted author_agent conflicts" in conflict["reasons"][0]
    assert no_claim["ok"] is False
    assert "no valid UUID-bound canonical write claim" in no_claim["reasons"][0]


@pytest.mark.parametrize(
    "case",
    [
        "event_type",
        "event_task",
        "agent",
        "payload_task",
        "payload_branch",
        "payload_head",
        "scope",
    ],
)
def test_padded_claim_identity_bindings_and_scope_never_authorize(
    case: str,
) -> None:
    claim = _claim()
    if case == "event_type":
        claim["type"] = " claim"
    elif case == "event_task":
        claim["task_id"] = f" {TASK}"
    elif case == "agent":
        claim["agent"] = " codex-lead-1"
    elif case == "payload_task":
        claim["payload"] = {"task_id": f" {TASK}"}
    elif case == "payload_branch":
        claim["payload"] = {"headRefName": f"{TASK} "}
    elif case == "payload_head":
        claim["payload"] = {"head": f" {HEAD}"}
    elif case == "scope":
        claim["write_scope"] = [f" {PATHS[0]}", PATHS[1]]

    report = _resolve(events=[claim])

    assert report["ok"] is False
    assert report["author_agent"] == ""
    assert report["operator_review_required"] is True


def test_padded_asserted_author_never_normalizes_into_authority() -> None:
    report = _resolve(asserted_author_agent=" codex-lead-1")

    assert report["ok"] is False
    assert report["decision"] == "invalid_author_evidence"
    assert "exact string" in report["reasons"][0]


@pytest.mark.parametrize(
    "path",
    [
        "tools/one.py/",
        "tools//one.py",
        "tools/./one.py",
        "tools/../tools/one.py",
        r"tools\one.py",
    ],
)
def test_noncanonical_exact_scope_paths_never_normalize_into_coverage(
    path: str,
) -> None:
    report = _resolve(
        events=[_claim(scope=[path, "tests/tools/test_one.py"])]
    )

    assert report["ok"] is False
    assert report["decision"] == "manual_operator_review_required"
    assert "claim write_scope" in report["reasons"][0]


def test_padded_git_identity_never_normalizes_into_registered_agent() -> None:
    identities, evidence = _git_material(commit_name="codex-lead-1")
    identities[1]["name"] = " codex-lead-1"

    report = _resolve(
        git_identities=identities,
        git_identity_evidence=evidence,
    )

    assert report["ok"] is False
    assert report["decision"] == "invalid_author_evidence"
    assert "exact string" in report["reasons"][0]


def test_non_string_identity_assertion_and_fractional_pr_ids_fail_closed() -> None:
    identities, evidence = _git_material()
    identities[1]["name"] = 7

    bad_identity = _resolve(
        git_identities=identities,
        git_identity_evidence=evidence,
    )
    bad_assertion = _resolve(asserted_author_agent=["codex-lead-1"])
    fractional_pr = _resolve(pr_number=1551.5)
    payload_fraction = _resolve(events=[_claim(payload={"pr": 1551.0})])

    assert bad_identity["decision"] == "invalid_author_evidence"
    assert "name must be a string" in bad_identity["reasons"][0]
    assert bad_assertion["decision"] == "invalid_author_evidence"
    assert "asserted_author_agent must be a string" in bad_assertion["reasons"][0]
    assert fractional_pr["decision"] == "invalid_author_evidence"
    assert "positive integer" in fractional_pr["reasons"][0]
    assert payload_fraction["ok"] is False
    assert "conflicting structured binding" in payload_fraction["reasons"][0]


def test_absent_empty_or_incomplete_git_identity_evidence_fails_closed() -> None:
    identities, evidence = _git_material()
    incomplete = dict(evidence)
    incomplete.pop("commit_oids")

    absent = _resolve(
        git_identities=identities,
        git_identity_evidence=None,
    )
    empty = _resolve(
        git_identities=[],
        git_identity_evidence={
            **evidence,
            "identity_count": 0,
            "commit_author_count": 0,
        },
    )
    malformed = _resolve(
        git_identities=identities,
        git_identity_evidence=incomplete,
    )

    assert absent["decision"] == "invalid_author_evidence"
    assert empty["decision"] == "invalid_author_evidence"
    assert malformed["decision"] == "invalid_author_evidence"
    assert "incomplete or unknown schema" in malformed["reasons"][0]


def test_duplicate_commit_author_source_cannot_mask_incomplete_evidence() -> None:
    identities, evidence = _git_material()
    identities.append(deepcopy(identities[1]))
    evidence = {
        **evidence,
        "commit_author_count": 2,
        "identity_count": 3,
    }

    report = _resolve(
        git_identities=identities,
        git_identity_evidence=evidence,
    )

    assert report["decision"] == "invalid_author_evidence"
    assert "repeats an author source" in report["reasons"][0]


def test_github_identity_normalizer_requires_author_commits_and_commit_authors() -> None:
    view = {
        "author": {"login": "Ahkeratmehilaiset", "name": "", "email": ""},
        "commits": [
            {
                "oid": HEAD,
                "authors": [
                    {
                        "name": "Jani",
                        "email": "jani@jkhservice.fi",
                        "login": "",
                    }
                ],
            }
        ],
    }

    identities = github_pr_git_identities(view)

    assert identities[0]["source"] == "pr_author"
    assert identities[1]["source"] == "commit_author:1:1"
    assert identities[1]["commit_oid"] == HEAD

    malformed = deepcopy(view)
    malformed["commits"][0]["authors"] = []
    try:
        github_pr_git_identities(malformed)
    except ValueError as exc:
        assert "authors must be a non-empty list" in str(exc)
    else:
        raise AssertionError("malformed commit authors must fail closed")


def test_git_identity_evidence_must_end_at_exact_head() -> None:
    view = {
        "author": {"login": "Ahkeratmehilaiset", "name": "", "email": ""},
        "commits": [
            {
                "oid": "0" * 40,
                "authors": [{"name": "Jani", "email": "", "login": ""}],
            }
        ],
    }

    with pytest.raises(ValueError, match="does not end at the exact head"):
        github_pr_git_identity_evidence(view, expected_head_sha=HEAD)


@pytest.mark.parametrize("value", [None, False, 0, [], {}])
def test_git_identity_expected_head_rejects_falsey_non_strings(
    value: object,
) -> None:
    view = {
        "author": {"login": "Ahkeratmehilaiset", "name": "", "email": ""},
        "commits": [
            {
                "oid": HEAD,
                "authors": [{"name": "Jani", "email": "", "login": ""}],
            }
        ],
    }

    with pytest.raises(ValueError, match="expected_head_sha must be a string"):
        github_pr_git_identity_evidence(
            view,
            expected_head_sha=value,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_head_sha", None),
        ("expected_head_sha", False),
        ("expected_head_sha", 0),
        ("expected_base_sha", None),
        ("expected_base_sha", False),
        ("expected_base_sha", 0),
        ("expected_head_sha", HEAD.upper()),
        ("expected_base_sha", f" {BASE}"),
    ],
)
def test_resolver_expected_sha_inputs_are_exact(
    field: str,
    value: object,
) -> None:
    report = _resolve(**{field: value})  # type: ignore[arg-type]

    assert report["ok"] is False
    assert report["decision"] == "invalid_author_evidence"


@pytest.mark.parametrize("registry", [False, 0, [], "registry"])
def test_non_object_identity_registry_fails_closed(registry: object) -> None:
    report = _resolve(registry=registry)  # type: ignore[arg-type]

    assert report["ok"] is False
    assert report["decision"] == "invalid_author_evidence"
    assert "identity_registry must be an object" in report["reasons"][0]
