from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import tools.pr_status_snapshot as snapshot_tool
from tools.pr_status_snapshot import (
    GH_JSON_FIELDS,
    PrStatusSnapshotError,
    build_pr_status_snapshot,
)


HEAD = "1234567890abcdef1234567890abcdef12345678"
BASE = "abcdef1234567890abcdef1234567890abcdef12"
OTHER_BASE = "fedcba9876543210fedcba9876543210fedcba98"


def _base_ref_payload(sha: str = BASE) -> dict:
    return {
        "ref": "refs/heads/main",
        "object": {"type": "commit", "sha": sha},
    }


def _gh_payload(**overrides) -> dict:
    payload = {
        "number": 479,
        "title": "feat(idle): add dry-run auto-merge gate",
        "headRefOid": HEAD,
        "headRefName": "codex/idle-consensus-auto-merge-v1-20260518",
        "baseRefOid": BASE,
        "baseRefName": "main",
        "mergeable": "MERGEABLE",
        "state": "OPEN",
        "isDraft": False,
        "url": "https://github.example/pr/479",
        "reviewDecision": "APPROVED",
        "updatedAt": "2026-07-24T09:00:00Z",
        "changedFiles": 2,
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
                        "name": "Jani",
                        "email": "jani@jkhservice.fi",
                        "login": "",
                    }
                ],
            }
        ],
        "statusCheckRollup": [
            {"name": "test (3.13)", "state": "SUCCESS"},
            {"name": "unified", "status": "COMPLETED", "conclusion": "SUCCESS"},
        ],
    }
    payload.update(overrides)
    return payload


def _runner(
    payload: dict | None = None,
    diff_text: str = "+ def helper():\n",
    recheck_payload: dict | None = None,
    file_records: list[dict] | None = None,
) -> tuple[list[list[str]], object]:
    initial_payload = payload or _gh_payload()
    followup_payload = recheck_payload if recheck_payload is not None else initial_payload
    payloads = [initial_payload, followup_payload]
    files = (
        file_records
        if file_records is not None
        else [
            {"filename": "tools/idle_daily_summary.py", "status": "modified"},
            {
                "filename": "tests/tools/test_idle_consensus_auto_merge.py",
                "status": "modified",
            },
        ]
    )
    view_call = {"index": 0}

    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        command = list(command)
        calls.append(command)
        if command[:2] == ["gh", "api"]:
            if "/git/ref/heads/" in command[4]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(_base_ref_payload()),
                    stderr="",
                )
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(files),
                stderr="",
            )
        if command[:3] == ["gh", "pr", "diff"]:
            return SimpleNamespace(returncode=0, stdout=diff_text, stderr="")
        index = view_call["index"]
        payload_to_use = payloads[min(index, len(payloads) - 1)]
        view_call["index"] += 1
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload_to_use),
            stderr="",
        )

    return calls, runner


def test_snapshot_uses_structured_gh_json_fields() -> None:
    calls, runner = _runner()
    expected_json_fields = f"{GH_JSON_FIELDS},state"

    snapshot = build_pr_status_snapshot(
        pr_number=479,
        repo="Ahkeratmehilaiset/waggledance-swarm",
        operator_approved=True,
        receipt_verified=True,
        expected_base_sha=BASE,
        runner=runner,
    )
    assert calls == [
        [
            "gh",
            "pr",
            "view",
            "479",
            "--json",
            expected_json_fields,
            "--repo",
            "Ahkeratmehilaiset/waggledance-swarm",
        ],
        [
            "gh",
            "api",
            "--method",
            "GET",
            "repos/Ahkeratmehilaiset/waggledance-swarm/git/ref/heads/main",
        ],
        [
            "gh",
            "api",
            "--method",
            "GET",
            "repos/Ahkeratmehilaiset/waggledance-swarm/pulls/479/files",
            "-f",
            "per_page=100",
            "-f",
            "page=1",
        ],
        [
            "gh",
            "pr",
            "diff",
            "479",
            "--patch",
            "--repo",
            "Ahkeratmehilaiset/waggledance-swarm",
        ],
        [
            "gh",
            "api",
            "--method",
            "GET",
            "repos/Ahkeratmehilaiset/waggledance-swarm/git/ref/heads/main",
        ],
        [
            "gh",
            "pr",
            "view",
            "479",
            "--json",
            expected_json_fields,
            "--repo",
            "Ahkeratmehilaiset/waggledance-swarm",
        ],
    ]
    assert snapshot["pr_number"] == 479
    assert snapshot["head_sha"] == HEAD
    assert snapshot["base_sha"] == BASE
    assert snapshot["state"] == "OPEN"
    assert snapshot["operator_approved"] is True
    assert snapshot["receipt_verified"] is True
    assert snapshot["checks"] == [
        {
            "name": "test (3.13)",
            "state": "SUCCESS",
            "status": "",
            "conclusion": "",
        },
        {
            "name": "unified",
            "state": "",
            "status": "COMPLETED",
            "conclusion": "SUCCESS",
        },
    ]
    assert snapshot["changed_paths"] == [
        "tests/tools/test_idle_consensus_auto_merge.py",
        "tools/idle_daily_summary.py",
    ]
    assert snapshot["diff_text"] == "+ def helper():\n"
    assert snapshot["git_identities"][0]["source"] == "pr_author"
    assert snapshot["git_identities"][1]["commit_oid"] == HEAD


def test_gh_failure_does_not_echo_stderr() -> None:
    def runner(command: list[str]) -> SimpleNamespace:
        return SimpleNamespace(returncode=7, stdout="", stderr="PRIVATE_MARKER")

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    report = excinfo.value.report
    assert report["decision"] == "gh_pr_view_failed"
    assert "PRIVATE_MARKER" not in " ".join(report["errors"])


def test_malformed_commit_author_metadata_fails_closed() -> None:
    payload = _gh_payload()
    payload["commits"][0]["authors"] = []
    _, runner = _runner(payload=payload)

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(
            pr_number=479,
            repo="Ahkeratmehilaiset/waggledance-swarm",
            runner=runner,
        )

    assert excinfo.value.report["decision"] == "invalid_git_identities"
    assert "authors must be a non-empty list" in excinfo.value.report["errors"][0]


def test_diff_failure_does_not_echo_stderr() -> None:
    def runner(command: list[str]) -> SimpleNamespace:
        if command[:2] == ["gh", "api"]:
            if "/git/ref/heads/" in command[4]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(_base_ref_payload()),
                    stderr="",
                )
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            "filename": "tools/idle_daily_summary.py",
                            "status": "modified",
                        },
                        {
                            "filename": "tests/tools/test_idle_consensus_auto_merge.py",
                            "status": "modified",
                        },
                    ]
                ),
                stderr="",
            )
        if command[:3] == ["gh", "pr", "diff"]:
            return SimpleNamespace(returncode=7, stdout="", stderr="PRIVATE_MARKER")
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(_gh_payload()),
            stderr="",
        )

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    report = excinfo.value.report
    assert report["decision"] == "gh_pr_diff_failed"
    assert "PRIVATE_MARKER" not in " ".join(report["errors"])


def test_invalid_json_refused_without_raw_echo() -> None:
    def runner(command: list[str]) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout="not json")

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    report = excinfo.value.report
    assert report["decision"] == "invalid_gh_json"
    assert "not json" not in " ".join(report["errors"])


def test_private_marker_refused() -> None:
    def runner(command: list[str]) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(_gh_payload(title="PRIVATE_MARKER")),
        )

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    assert excinfo.value.report["decision"] == "privacy_marker_refused"


def test_private_marker_in_diff_refused() -> None:
    calls, runner = _runner(diff_text="+ PRIVATE_MARKER\n")

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    assert len(calls) == 4
    assert excinfo.value.report["decision"] == "privacy_marker_refused"


def test_plural_private_marker_helper_name_is_not_sensitive_content() -> None:
    helper_name = "PRIVATE" + "_MARKERS"
    diff_text = f"+ assert all({helper_name})\n"
    calls, runner = _runner(diff_text=diff_text)

    snapshot = build_pr_status_snapshot(pr_number=479, runner=runner)

    assert len(calls) == 6
    assert snapshot["diff_text"] == diff_text


def test_plural_private_marker_helper_exception_is_diff_only() -> None:
    marker = "PRIVATE" + "_MARKER"
    helper_name = f"{marker}S"

    assert snapshot_tool._find_private_marker(helper_name) == marker
    assert snapshot_tool._find_private_marker(f"+ assert all('{helper_name}')\n") == marker

    for diff_text in (
        f"+ assert all(X{helper_name})\n",
        f"+ assert all({helper_name}_X)\n",
        f"+ assert all({marker}_X)\n",
    ):
        calls, runner = _runner(diff_text=diff_text)
        with pytest.raises(PrStatusSnapshotError) as excinfo:
            build_pr_status_snapshot(pr_number=479, runner=runner)
        assert len(calls) == 4
        assert excinfo.value.report["decision"] == "privacy_marker_refused"


def test_pr_head_changed_during_snapshot_is_rejected() -> None:
    calls, runner = _runner(
        payload=_gh_payload(),
        recheck_payload=_gh_payload(headRefOid="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
    )

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    report = excinfo.value.report
    assert report["decision"] == "gh_pr_diff_head_drift"
    assert len(calls) == 6


def test_pr_base_changed_during_snapshot_is_rejected() -> None:
    calls, runner = _runner(
        payload=_gh_payload(),
        recheck_payload=_gh_payload(baseRefOid=OTHER_BASE),
    )

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    report = excinfo.value.report
    assert report["decision"] == "gh_pr_diff_base_drift"
    assert len(calls) == 6


def test_expected_base_mismatch_is_rejected() -> None:
    calls, runner = _runner(payload=_gh_payload(baseRefOid=BASE))

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(
            pr_number=479,
            expected_base_sha=OTHER_BASE,
            runner=runner,
        )
    report = excinfo.value.report
    assert report["decision"] == "stale_base_ref"
    assert len(calls) == 6


def test_invalid_expected_base_refused_before_gh_call() -> None:
    def runner(command: list[str]) -> SimpleNamespace:
        raise AssertionError(f"runner should not be called: {command}")

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(
            pr_number=479,
            expected_base_sha="abc123",
            runner=runner,
        )
    assert excinfo.value.report["decision"] == "invalid_expected_base_sha"


def test_missing_full_base_sha_refused() -> None:
    def runner(command: list[str]) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(_gh_payload(baseRefOid="abc1234")),
        )

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    assert excinfo.value.report["decision"] == "invalid_base_sha"


def test_recheck_view_failure_is_reported() -> None:
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        command = list(command)
        calls.append(command)
        if command[:2] == ["gh", "api"]:
            if "/git/ref/heads/" in command[4]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(_base_ref_payload()),
                    stderr="",
                )
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            "filename": "tools/idle_daily_summary.py",
                            "status": "modified",
                        },
                        {
                            "filename": "tests/tools/test_idle_consensus_auto_merge.py",
                            "status": "modified",
                        },
                    ]
                ),
                stderr="",
            )
        if command[:3] == ["gh", "pr", "diff"]:
            return SimpleNamespace(
                returncode=0,
                stdout="+ def helper():\n",
                stderr="",
            )
        if command[:3] == ["gh", "pr", "view"] and len(calls) == 1:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(_gh_payload()),
                stderr="",
            )
        return SimpleNamespace(returncode=13, stdout="", stderr="fetch failed")

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    report = excinfo.value.report
    assert report["decision"] == "gh_pr_view_recheck_failed"
    assert len(calls) == 6


def test_missing_full_head_sha_refused() -> None:
    def runner(command: list[str]) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=json.dumps(_gh_payload(headRefOid="abc1234")))

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    assert excinfo.value.report["decision"] == "invalid_head_sha"


def test_pr_number_mismatch_refused() -> None:
    def runner(command: list[str]) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stdout=json.dumps(_gh_payload(number=480)))

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    assert excinfo.value.report["decision"] == "pr_number_mismatch"


def test_invalid_files_refused() -> None:
    calls, runner = _runner(
        payload=_gh_payload(changedFiles=1),
        file_records=[{"filename": "", "status": "modified"}],
    )

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)
    assert len(calls) == 3
    assert excinfo.value.report["decision"] == "invalid_files"


@pytest.mark.parametrize("status", ["renamed", "copied"])
def test_rename_and_copy_records_include_source_and_target(status: str) -> None:
    calls, runner = _runner(
        payload=_gh_payload(changedFiles=1),
        file_records=[
            {
                "filename": "docs/new-name.md",
                "previous_filename": "tools/old-name.py",
                "status": status,
            }
        ],
    )

    snapshot = build_pr_status_snapshot(pr_number=479, runner=runner)

    assert len(calls) == 6
    assert snapshot["changed_paths"] == [
        "docs/new-name.md",
        "tools/old-name.py",
    ]


@pytest.mark.parametrize(
    "record",
    [
        {"filename": "docs/new-name.md", "status": "renamed"},
        {"filename": "docs/new-name.md", "status": "future_status"},
    ],
)
def test_unknown_or_incomplete_file_status_fails_closed(record: dict) -> None:
    _, runner = _runner(
        payload=_gh_payload(changedFiles=1),
        file_records=[record],
    )

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)

    assert excinfo.value.report["decision"] == "invalid_files"


def test_pull_file_record_count_must_match_changed_files() -> None:
    _, runner = _runner(payload=_gh_payload(changedFiles=3))

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)

    assert excinfo.value.report["decision"] == "invalid_gh_files_count"


@pytest.mark.parametrize("changed_files", [True, 1.0, 1.5, "1", 0, 3001])
def test_changed_files_count_must_be_an_exact_supported_integer(
    changed_files: object,
) -> None:
    _, runner = _runner(payload=_gh_payload(changedFiles=changed_files))

    with pytest.raises(PrStatusSnapshotError):
        build_pr_status_snapshot(pr_number=479, runner=runner)


@pytest.mark.parametrize(
    "record",
    [
        {"filename": r"docs\alias.md", "status": "modified"},
        {"filename": "docs/\ud800.md", "status": "modified"},
        {
            "filename": "docs/same.md",
            "previous_filename": "docs/same.md",
            "status": "renamed",
        },
        {"filename": "docs/./alias.md", "status": "modified"},
    ],
)
def test_noncanonical_file_paths_fail_closed(record: dict) -> None:
    _, runner = _runner(
        payload=_gh_payload(changedFiles=1),
        file_records=[record],
    )

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)

    assert excinfo.value.report["decision"] == "invalid_files"


def test_casefold_path_alias_and_duplicate_target_fail_closed() -> None:
    _, alias_runner = _runner(
        payload=_gh_payload(changedFiles=2),
        file_records=[
            {"filename": "docs/Name.md", "status": "added"},
            {"filename": "docs/name.md", "status": "added"},
        ],
    )
    _, duplicate_runner = _runner(
        payload=_gh_payload(changedFiles=2),
        file_records=[
            {"filename": "docs/name.md", "status": "added"},
            {"filename": "docs/name.md", "status": "modified"},
        ],
    )

    with pytest.raises(PrStatusSnapshotError):
        build_pr_status_snapshot(pr_number=479, runner=alias_runner)
    with pytest.raises(PrStatusSnapshotError):
        build_pr_status_snapshot(pr_number=479, runner=duplicate_runner)


def test_valid_unicode_path_is_preserved_exactly() -> None:
    unicode_path = "docs/Älykkäät-mehiläiset.md"
    _, runner = _runner(
        payload=_gh_payload(changedFiles=1),
        file_records=[{"filename": unicode_path, "status": "modified"}],
    )

    snapshot = build_pr_status_snapshot(pr_number=479, runner=runner)

    assert snapshot["changed_paths"] == [unicode_path]


def test_pull_files_are_strictly_paginated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(snapshot_tool, "GH_FILES_PER_PAGE", 2)
    payload = _gh_payload(changedFiles=3)
    records = [
        {"filename": "tests/one.py", "status": "modified"},
        {"filename": "tests/two.py", "status": "added"},
        {"filename": "tests/three.py", "status": "removed"},
    ]
    calls: list[list[str]] = []
    view_calls = 0

    def runner(command: list[str]) -> SimpleNamespace:
        nonlocal view_calls
        command = list(command)
        calls.append(command)
        if command[:2] == ["gh", "api"]:
            if "/git/ref/heads/" in command[4]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(_base_ref_payload()),
                    stderr="",
                )
            page = int(command[-1].split("=", 1)[1])
            start = (page - 1) * 2
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(records[start : start + 2]),
                stderr="",
            )
        if command[:3] == ["gh", "pr", "diff"]:
            return SimpleNamespace(returncode=0, stdout="+x\n", stderr="")
        view_calls += 1
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )

    snapshot = build_pr_status_snapshot(pr_number=479, runner=runner)

    api_calls = [
        call
        for call in calls
        if call[:2] == ["gh", "api"] and "/pulls/" in call[4]
    ]
    assert [call[-1] for call in api_calls] == ["page=1", "page=2"]
    assert snapshot["changed_paths"] == [
        "tests/one.py",
        "tests/three.py",
        "tests/two.py",
    ]


@pytest.mark.parametrize("changed_files", [1, 99, 100, 101, 200, 2999, 3000])
def test_pull_files_pagination_boundaries_use_exact_page_count(
    changed_files: int,
) -> None:
    payload = _gh_payload(changedFiles=changed_files)
    records = [
        {
            "filename": f"tests/page-boundary-{index:04d}.py",
            "status": "modified",
        }
        for index in range(changed_files)
    ]
    pull_pages: list[int] = []
    view_calls = 0

    def runner(command: list[str]) -> SimpleNamespace:
        nonlocal view_calls
        command = list(command)
        if command[:2] == ["gh", "api"]:
            if "/git/ref/heads/" in command[4]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(_base_ref_payload()),
                    stderr="",
                )
            page = int(command[-1].split("=", 1)[1])
            pull_pages.append(page)
            start = (page - 1) * 100
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(records[start : start + 100]),
                stderr="",
            )
        if command[:3] == ["gh", "pr", "diff"]:
            return SimpleNamespace(returncode=0, stdout="+x\n", stderr="")
        view_calls += 1
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )

    snapshot = build_pr_status_snapshot(pr_number=479, runner=runner)

    expected_pages = (changed_files + 99) // 100
    assert pull_pages == list(range(1, expected_pages + 1))
    assert len(snapshot["changed_paths"]) == changed_files
    assert snapshot["changed_paths"] == sorted(snapshot["changed_paths"])


def test_short_middle_page_fails_closed_without_accepting_partial_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(snapshot_tool, "GH_FILES_PER_PAGE", 2)
    payload = _gh_payload(changedFiles=5)
    pull_pages: list[int] = []

    def runner(command: list[str]) -> SimpleNamespace:
        command = list(command)
        if command[:2] == ["gh", "api"]:
            if "/git/ref/heads/" in command[4]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(_base_ref_payload()),
                    stderr="",
                )
            page = int(command[-1].split("=", 1)[1])
            pull_pages.append(page)
            count = 2 if page == 1 else 1
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            "filename": f"tests/page-{page}-{index}.py",
                            "status": "modified",
                        }
                        for index in range(count)
                    ]
                ),
                stderr="",
            )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)

    assert excinfo.value.report["decision"] == "invalid_gh_files_count"
    assert pull_pages == [1, 2]


def test_repeated_copy_source_across_pages_is_preserved_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(snapshot_tool, "GH_FILES_PER_PAGE", 1)
    payload = _gh_payload(changedFiles=2)
    file_records = [
        {
            "filename": "docs/copy-a.md",
            "previous_filename": "docs/source.md",
            "status": "copied",
        },
        {
            "filename": "docs/copy-b.md",
            "previous_filename": "docs/source.md",
            "status": "copied",
        },
    ]

    def runner(command: list[str]) -> SimpleNamespace:
        command = list(command)
        if command[:2] == ["gh", "api"]:
            if "/git/ref/heads/" in command[4]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(_base_ref_payload()),
                    stderr="",
                )
            page = int(command[-1].split("=", 1)[1])
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps([file_records[page - 1]]),
                stderr="",
            )
        if command[:3] == ["gh", "pr", "diff"]:
            return SimpleNamespace(returncode=0, stdout="+x\n", stderr="")
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(payload),
            stderr="",
        )

    snapshot = build_pr_status_snapshot(pr_number=479, runner=runner)

    assert snapshot["changed_paths"] == [
        "docs/copy-a.md",
        "docs/copy-b.md",
        "docs/source.md",
    ]


@pytest.mark.parametrize("mode", ["api_failure", "api_parse", "api_utf8"])
def test_pull_files_api_evidence_failures_are_closed(mode: str) -> None:
    _, stable_runner = _runner()

    def runner(command: list[str]) -> SimpleNamespace:
        command = list(command)
        if command[:2] == ["gh", "api"] and "/pulls/" in command[4]:
            if mode == "api_failure":
                return SimpleNamespace(returncode=9, stdout="", stderr="secret")
            if mode == "api_parse":
                return SimpleNamespace(returncode=0, stdout="{", stderr="")
            return SimpleNamespace(returncode=0, stdout=b"\x80", stderr=b"")
        return stable_runner(command)

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)

    expected = {
        "api_failure": "gh_pr_files_failed",
        "api_parse": "invalid_gh_files_json",
        "api_utf8": "invalid_utf8",
    }
    assert excinfo.value.report["decision"] == expected[mode]


def test_text_runner_lone_surrogate_is_not_treated_as_valid_utf8() -> None:
    _, stable_runner = _runner()

    def runner(command: list[str]) -> SimpleNamespace:
        if list(command)[:3] == ["gh", "pr", "view"]:
            return SimpleNamespace(returncode=0, stdout="\ud800", stderr="")
        return stable_runner(command)

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)

    assert excinfo.value.report["decision"] == "invalid_utf8"


@pytest.mark.parametrize(
    ("field", "value", "decision"),
    [
        ("number", 480, "gh_pr_diff_number_drift"),
        ("baseRefName", "release", "gh_pr_diff_base_ref_drift"),
        ("updatedAt", "2026-07-24T09:01:00Z", "gh_pr_diff_updated_at_drift"),
        ("state", "CLOSED", "invalid_pr_state"),
        ("isDraft", True, "gh_pr_diff_draft_drift"),
    ],
)
def test_recheck_stabilizes_full_pr_metadata(
    field: str,
    value: object,
    decision: str,
) -> None:
    _, runner = _runner(recheck_payload=_gh_payload(**{field: value}))

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)

    assert excinfo.value.report["decision"] == decision


def test_recheck_stabilizes_status_checks() -> None:
    changed_checks = [
        {"name": "test (3.13)", "state": "PENDING"},
        {"name": "unified", "status": "COMPLETED", "conclusion": "SUCCESS"},
    ]
    _, runner = _runner(
        recheck_payload=_gh_payload(statusCheckRollup=changed_checks),
    )

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)

    assert excinfo.value.report["decision"] == "gh_pr_diff_checks_drift"


def test_base_tip_mismatch_and_drift_fail_closed() -> None:
    _, stable_runner = _runner()
    calls = 0

    def mismatch_runner(command: list[str]) -> SimpleNamespace:
        command = list(command)
        if command[:2] == ["gh", "api"] and "/git/ref/heads/" in command[4]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(_base_ref_payload(OTHER_BASE)),
                stderr="",
            )
        return stable_runner(command)

    with pytest.raises(PrStatusSnapshotError) as mismatch_exc:
        build_pr_status_snapshot(pr_number=479, runner=mismatch_runner)

    _, stable_runner = _runner()

    def drift_runner(command: list[str]) -> SimpleNamespace:
        nonlocal calls
        command = list(command)
        if command[:2] == ["gh", "api"] and "/git/ref/heads/" in command[4]:
            calls += 1
            sha = BASE if calls == 1 else OTHER_BASE
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(_base_ref_payload(sha)),
                stderr="",
            )
        return stable_runner(command)

    with pytest.raises(PrStatusSnapshotError) as drift_exc:
        build_pr_status_snapshot(pr_number=479, runner=drift_runner)

    assert mismatch_exc.value.report["decision"] == "stale_base_ref"
    assert drift_exc.value.report["decision"] == "gh_pr_diff_base_tip_drift"


def test_duplicate_json_keys_are_rejected_in_view_and_files() -> None:
    def duplicate_view_runner(command: list[str]) -> SimpleNamespace:
        return SimpleNamespace(
            returncode=0,
            stdout='{"number":479,"number":480}',
            stderr="",
        )

    with pytest.raises(PrStatusSnapshotError) as view_exc:
        build_pr_status_snapshot(pr_number=479, runner=duplicate_view_runner)

    _, stable_runner = _runner()

    def duplicate_files_runner(command: list[str]) -> SimpleNamespace:
        command = list(command)
        if command[:2] == ["gh", "api"] and "/pulls/" in command[4]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    '[{"filename":"docs/a.md","filename":"docs/b.md",'
                    '"status":"modified"}]'
                ),
                stderr="",
            )
        return stable_runner(command)

    with pytest.raises(PrStatusSnapshotError) as files_exc:
        build_pr_status_snapshot(pr_number=479, runner=duplicate_files_runner)

    assert view_exc.value.report["decision"] == "invalid_gh_json"
    assert files_exc.value.report["decision"] == "invalid_gh_files_json"


def test_head_ref_and_identity_drift_are_rejected() -> None:
    _, head_ref_runner = _runner(
        recheck_payload=_gh_payload(headRefName="codex/other"),
    )
    identity_payload = _gh_payload()
    identity_payload["commits"][0]["authors"][0]["name"] = "Other Human"
    _, identity_runner = _runner(recheck_payload=identity_payload)

    with pytest.raises(PrStatusSnapshotError) as head_ref_exc:
        build_pr_status_snapshot(pr_number=479, runner=head_ref_runner)
    with pytest.raises(PrStatusSnapshotError) as identity_exc:
        build_pr_status_snapshot(pr_number=479, runner=identity_runner)

    assert head_ref_exc.value.report["decision"] == "gh_pr_diff_head_ref_drift"
    assert identity_exc.value.report["decision"] == "gh_pr_identity_drift"


def test_identity_commit_list_must_end_at_head() -> None:
    payload = _gh_payload()
    payload["commits"][0]["oid"] = "0" * 40
    _, runner = _runner(payload=payload)

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=479, runner=runner)

    assert excinfo.value.report["decision"] == "invalid_git_identities"
    assert "does not end at the exact head" in excinfo.value.report["errors"][0]


@pytest.mark.parametrize("pr_number", [True, 479.0, 479.5, "479"])
def test_non_integral_pr_number_input_is_rejected(pr_number: object) -> None:
    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(pr_number=pr_number)  # type: ignore[arg-type]

    assert excinfo.value.report["decision"] == "invalid_pr_number"


def test_cli_writes_snapshot_file(tmp_path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    out_path = tmp_path / "pr-status.json"

    def fake_run(command: list[str]) -> SimpleNamespace:
        if command[:2] == ["gh", "api"]:
            if "/git/ref/heads/" in command[4]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(_base_ref_payload()),
                    stderr="",
                )
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            "filename": "tools/idle_daily_summary.py",
                            "status": "modified",
                        },
                        {
                            "filename": "tests/tools/test_idle_consensus_auto_merge.py",
                            "status": "modified",
                        },
                    ]
                ),
                stderr="",
            )
        if command[:3] == ["gh", "pr", "diff"]:
            return SimpleNamespace(
                returncode=0,
                stdout="+ def helper():\n",
                stderr="",
            )
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(_gh_payload()),
            stderr="",
        )

    monkeypatch.setattr(snapshot_tool, "_run_command", fake_run)
    exit_code = snapshot_tool.main(
        [
            "479",
            "--operator-approved",
            "--receipt-verified",
            "--out",
            str(out_path),
            "--json",
        ]
    )

    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "written"
    assert out_path.exists()
    snapshot = json.loads(out_path.read_text(encoding="utf-8"))
    assert snapshot["head_sha"] == HEAD
    assert snapshot["receipt_verified"] is True
    assert snapshot["changed_paths"] == [
        "tests/tools/test_idle_consensus_auto_merge.py",
        "tools/idle_daily_summary.py",
    ]
    assert snapshot["diff_text"] == "+ def helper():\n"


@pytest.mark.parametrize(
    ("field", "value", "decision"),
    [
        ("operator_approved", "false", "invalid_operator_approved"),
        ("operator_approved", 0, "invalid_operator_approved"),
        ("operator_approved", None, "invalid_operator_approved"),
        ("receipt_verified", "false", "invalid_receipt_verified"),
        ("receipt_verified", 1, "invalid_receipt_verified"),
        ("receipt_verified", [], "invalid_receipt_verified"),
    ],
)
def test_snapshot_boolean_inputs_are_exact_and_never_reach_runner(
    field: str,
    value: object,
    decision: str,
) -> None:
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(list(command))
        raise AssertionError("invalid control input must fail before GitHub")

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(
            pr_number=479,
            runner=runner,
            **{field: value},  # type: ignore[arg-type]
        )

    assert calls == []
    assert excinfo.value.report["decision"] == decision


@pytest.mark.parametrize("expected_base_sha", [BASE.upper(), f" {BASE}", f"{BASE} "])
def test_snapshot_expected_base_sha_is_not_normalized(
    expected_base_sha: str,
) -> None:
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(list(command))
        raise AssertionError("invalid SHA must fail before GitHub")

    with pytest.raises(PrStatusSnapshotError) as excinfo:
        build_pr_status_snapshot(
            pr_number=479,
            expected_base_sha=expected_base_sha,
            runner=runner,
        )

    assert calls == []
    assert excinfo.value.report["decision"] == "invalid_expected_base_sha"
