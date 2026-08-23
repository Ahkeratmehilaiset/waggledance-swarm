# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy

import pytest

from tools.bridge_pr_author import (
    GIT_IDENTITY_EVIDENCE_SCHEMA,
    github_pr_git_identity_evidence,
    github_pr_git_identities,
    resolve_bridge_pr_author,
)

HEAD = "1234567890abcdef1234567890abcdef12345678"
PRIOR_HEAD = "0123456789abcdef0123456789abcdef01234567"
BASE = "abcdef1234567890abcdef1234567890abcdef12"
TASK = "codex-lead-1/unified-author-fixture"
PATHS = ["tools/one.py", "tests/tools/test_one.py"]
REGISTRY = {
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
}


class _ToggleScopeList(list[str]):
    def __init__(self, first: list[str], second: list[str]) -> None:
        super().__init__()
        self._first = first
        self._second = second
        self.reads = 0

    def __iter__(self) -> Iterator[str]:
        self.reads += 1
        values = self._first if self.reads == 1 else self._second
        return iter(values)


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


def test_exact_task_claim_with_disjoint_scope_cannot_become_an_owner() -> None:
    review_artifact = _claim(
        "codex-tools-1",
        scope=["docs/runs/pr-review.md"],
    )

    report = _resolve(events=[_claim(), review_artifact])

    assert report["ok"] is True
    assert report["author_agent"] == "codex-lead-1"
    assert report["canonical_owner_candidates"] == ["codex-lead-1"]
    assert report["claim_event_indexes"] == [0]
    assert report["ignored_irrelevant_claim_indexes"] == [1]


def test_case_variant_exact_scope_cannot_hide_a_second_owner() -> None:
    case_variant_contributor = _claim(
        "codex-tools-1",
        scope=["Tools/one.py"],
    )

    report = _resolve(events=[_claim(), case_variant_contributor])

    assert report["ok"] is False
    assert report["canonical_owner_candidates"] == [
        "codex-lead-1",
        "codex-tools-1",
    ]
    assert "multiple canonical claim owners" in report["reasons"][0]


@pytest.mark.parametrize(
    ("changed_path", "scope"),
    [
        ("docs/Straße.py", "docs/STRASSE.py"),
        ("docs/İ.py", "docs/i\u0307.py"),
    ],
)
def test_non_ascii_path_evidence_fails_closed_before_case_normalization(
    changed_path: str,
    scope: str,
) -> None:
    report = _resolve(
        events=[_claim(scope=[scope])],
        changed_paths=[changed_path],
    )

    assert report["ok"] is False
    assert report["decision"] == "invalid_author_evidence"
    assert "ASCII repository path" in report["reasons"][0]


@pytest.mark.parametrize(
    "scope",
    ["tools/bridge_pr_autho?.py", "tools/[b]ridge_pr_author.py"],
)
def test_powershell_wildcard_scope_cannot_hide_a_second_owner(
    scope: str,
) -> None:
    changed_path = "tools/bridge_pr_author.py"
    report = _resolve(
        events=[
            _claim(scope=[changed_path]),
            _claim("codex-tools-1", scope=[scope]),
        ],
        changed_paths=[changed_path],
    )

    assert report["ok"] is False
    assert "wildcard" in report["reasons"][0]


def test_windows_short_name_scope_cannot_hide_a_second_owner() -> None:
    changed_path = "waggledance/core/one.py"
    report = _resolve(
        events=[
            _claim(scope=[changed_path]),
            _claim("codex-tools-1", scope=["WAGGLE~1/core/one.py"]),
        ],
        changed_paths=[changed_path],
    )

    assert report["ok"] is False
    assert "canonical repository path" in report["reasons"][0]


def test_scope_sequence_is_frozen_before_glob_disjoint_analysis() -> None:
    toggling_scope = _ToggleScopeList(
        ["tools/*.py"],
        ["docs/runs/pr-review-*.md"],
    )
    foreign_claim = _claim("codex-tools-1", scope=toggling_scope)

    report = _resolve(events=[_claim(), foreign_claim])

    assert report["ok"] is False
    assert "wildcard" in report["reasons"][0]
    assert toggling_scope.reads == 1


def test_malformed_docs_glob_is_ignored_only_when_provably_disjoint() -> None:
    review_artifact = _claim(
        "codex-tools-1",
        scope=["docs/runs/pr-review-*.md"],
    )

    report = _resolve(events=[_claim(), review_artifact])

    assert report["ok"] is True
    assert report["author_agent"] == "codex-lead-1"
    assert report["claim_event_indexes"] == [0]
    assert report["ignored_irrelevant_claim_indexes"] == [1]


def test_empty_scope_or_malformed_payload_is_ignored_when_disjoint() -> None:
    empty_scope = _claim("codex-tools-1", scope=[])
    malformed_payload = _claim(
        "codex-tools-1",
        scope=["docs/runs/pr-review.md"],
    )
    malformed_payload["payload"] = False
    malformed_head = _claim(
        "codex-tools-1",
        scope=["docs/runs/pr-review-head.md"],
        payload={"head": "not-a-sha"},
    )

    report = _resolve(
        events=[_claim(), empty_scope, malformed_payload, malformed_head]
    )

    assert report["ok"] is True
    assert report["author_agent"] == "codex-lead-1"
    assert report["claim_event_indexes"] == [0]
    assert report["ignored_irrelevant_claim_indexes"] == [1, 2, 3]


def test_disagreeing_scope_declarations_are_ignored_when_both_disjoint() -> None:
    review_artifact = _claim(
        "codex-tools-1",
        scope=["docs/runs/review-a.md"],
        payload={"write_scope": ["docs/runs/review-b.md"]},
    )

    report = _resolve(events=[_claim(), review_artifact])

    assert report["ok"] is True
    assert report["claim_event_indexes"] == [0]
    assert report["ignored_irrelevant_claim_indexes"] == [1]


@pytest.mark.parametrize(
    "scope",
    [
        pytest.param(["tools/*.py"], id="overlapping-directory"),
        pytest.param(["Tools/*.py"], id="case-folded-overlap"),
        pytest.param(["tools./*.py"], id="windows-dot-alias"),
        pytest.param(["*.md"], id="no-static-directory-prefix"),
        pytest.param(
            ["docs/../reviews/*.md"],
            id="unsafe-static-directory-prefix",
        ),
        pytest.param(
            ["docs/*.md/../tools/*.py"],
            id="unsafe-suffix-traversal",
        ),
        pytest.param(
            ["docs/reviews/*.md/"],
            id="trailing-slash",
        ),
        pytest.param(
            ["docs/runs/pr-review-*.md", "tools/one.py"],
            id="disjoint-glob-with-overlapping-entry",
        ),
    ],
)
def test_malformed_scope_glob_that_may_overlap_fails_closed(
    scope: list[str],
) -> None:
    report = _resolve(
        events=[_claim(), _claim("codex-tools-1", scope=scope)]
    )

    assert report["ok"] is False
    assert any(
        fragment in report["reasons"][0]
        for fragment in ("wildcard", "canonical repository path")
    )


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


def test_prior_head_claim_by_current_owner_is_historical_evidence() -> None:
    historical = _claim(payload={"head": PRIOR_HEAD})

    forward = _resolve(events=[_claim(), historical])
    reverse = _resolve(events=[historical, _claim()])

    assert forward["ok"] is reverse["ok"] is True
    assert forward["author_agent"] == reverse["author_agent"] == "codex-lead-1"
    assert forward["claim_event_indexes"] == [0]
    assert forward["ignored_irrelevant_claim_indexes"] == [1]
    assert reverse["claim_event_indexes"] == [1]
    assert reverse["ignored_irrelevant_claim_indexes"] == [0]


def test_prior_head_claim_never_fills_current_head_scope_coverage() -> None:
    current = _claim(scope=["tools/one.py"])
    historical = _claim(
        scope=["tests/tools/test_one.py"],
        payload={"head": PRIOR_HEAD},
    )

    report = _resolve(events=[current, historical])

    assert report["ok"] is False
    assert report["covered_paths"] == ["tools/one.py"]
    assert report["uncovered_paths"] == ["tests/tools/test_one.py"]


def test_foreign_prior_head_claim_remains_contributor_evidence() -> None:
    historical = _claim(
        "codex-tools-1",
        scope=["tools/one.py"],
        payload={"head": PRIOR_HEAD},
    )

    report = _resolve(events=[_claim(), historical])

    assert report["ok"] is False
    assert report["contributor_claim_agents"] == ["codex-tools-1"]
    assert report["claim_event_indexes"] == [0, 1]
    assert "historical contributor claim" in report["reasons"][0]


def test_current_and_historical_contributors_are_both_reported() -> None:
    current_contributor = _claim(
        "codex-tools-1",
        task_id="codex-tools-1/contributor",
        scope=["tools/one.py"],
        payload={"pr": 1551},
    )
    historical_contributor = _claim(
        "claude-rco-1",
        scope=["tests/tools/test_one.py"],
        payload={"head": PRIOR_HEAD},
    )

    report = _resolve(
        events=[_claim(), current_contributor, historical_contributor]
    )

    assert report["ok"] is False
    assert report["contributor_claim_agents"] == [
        "claude-rco-1",
        "codex-tools-1",
    ]
    assert report["claim_event_indexes"] == [0, 1, 2]
    assert report["ignored_irrelevant_claim_indexes"] == []
    assert any("non-canonical contributor" in reason for reason in report["reasons"])
    assert any("historical contributor" in reason for reason in report["reasons"])


def test_claim_errors_do_not_suppress_foreign_historical_evidence() -> None:
    invalid_current_claim = _claim(
        "codex-tools-1",
        scope=["tools/one.py"],
        payload={"head": "not-a-sha"},
    )
    historical_contributor = _claim(
        "claude-rco-1",
        scope=["tests/tools/test_one.py"],
        payload={"head": PRIOR_HEAD},
    )

    report = _resolve(
        events=[_claim(), invalid_current_claim, historical_contributor]
    )

    assert report["ok"] is False
    assert report["contributor_claim_agents"] == ["claude-rco-1"]
    assert report["claim_event_indexes"] == [0, 2]
    assert report["ignored_irrelevant_claim_indexes"] == []
    assert any("head binding" in reason for reason in report["reasons"])
    assert any("historical contributor" in reason for reason in report["reasons"])


def test_only_prior_head_claims_cannot_establish_current_owner() -> None:
    report = _resolve(events=[_claim(payload={"head": PRIOR_HEAD})])

    assert report["ok"] is False
    assert report["canonical_owner_candidates"] == []
    assert report["ignored_irrelevant_claim_indexes"] == [0]
    assert "no valid UUID-bound canonical write claim" in report["reasons"][0]


def test_mixed_current_and_prior_head_aliases_fail_closed() -> None:
    report = _resolve(
        events=[
            _claim(
                payload={"head": HEAD, "exact_head": PRIOR_HEAD},
            )
        ]
    )

    assert report["ok"] is False
    assert "mixed" in report["reasons"][0]


def test_distinct_prior_head_aliases_fail_closed() -> None:
    report = _resolve(
        events=[
            _claim(
                payload={"head": PRIOR_HEAD, "exact_head": "f" * 40},
            )
        ]
    )

    assert report["ok"] is False
    assert "mixed" in report["reasons"][0]


@pytest.mark.parametrize(
    "value",
    ["not-a-sha", HEAD.upper(), f" {HEAD}", False, 0, {}],
)
def test_malformed_claim_head_binding_fails_closed(value: object) -> None:
    report = _resolve(events=[_claim(payload={"head": value})])

    assert report["ok"] is False
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
        "tools./one.py",
        "tools /one.py",
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
