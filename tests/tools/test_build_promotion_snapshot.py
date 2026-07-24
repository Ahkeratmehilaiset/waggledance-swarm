# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

import tools.build_promotion_snapshot as promotion_snapshot_tool
import tools.pr_status_snapshot as pr_status_tool
from tools.build_promotion_snapshot import (
    PromotionSnapshotError,
    _read_events_fail_closed,
    _run,
    build_promotion_snapshot,
    main,
)
from tools.pr_status_snapshot import GH_JSON_FIELDS

REPO = "Ahkeratmehilaiset/waggledance-swarm"
PR = 901
TASK = "fable-5/promotion-snapshot-fixture-20260605"
HEAD = "1234567890abcdef1234567890abcdef12345678"
BASE = "abcdef1234567890abcdef1234567890abcdef12"
OTHER_BASE = "fedcba9876543210fedcba9876543210fedcba98"
PATHS = ["tools/idle_daily_summary.py"]
DIFF = "+ def helper():\n+     return 1\n"
AGENT_UUIDS = {
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    "claude-rco-2": "76739997-0058-41a2-8514-78ff295537aa",
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
    "fable-5": "f8b1e5c0-3d2a-4e6b-9c1f-7a0d5e2b4c80",
}


def _completed(stdout: str) -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


def test_default_runner_decodes_child_output_as_strict_utf8(monkeypatch) -> None:
    """GitHub CLI emits UTF-8 even when the Windows locale is cp1252."""

    observed: dict[str, object] = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return SimpleNamespace(
            returncode=0,
            stdout="\uff10\n".encode("utf-8"),
            stderr=b"",
        )

    monkeypatch.setattr(
        "tools.build_promotion_snapshot.subprocess.run",
        fake_run,
    )

    completed = _run(("gh", "pr", "diff", "1557"), runner=None)

    assert completed.stdout == "\uff10\n"
    assert observed == {
        "command": ["gh", "pr", "diff", "1557"],
        "check": False,
        "capture_output": True,
    }


@pytest.mark.parametrize(
    ("stream_name", "stdout", "stderr"),
    [
        ("stdout", b"\x80", b""),
        ("stderr", b"ok\n", b"\x80"),
    ],
)
def test_default_runner_rejects_invalid_utf8_on_every_stream(
    monkeypatch,
    stream_name: str,
    stdout: bytes,
    stderr: bytes,
) -> None:
    """Malformed child output must fail closed even when exit status is zero."""

    monkeypatch.setattr(
        "tools.build_promotion_snapshot.subprocess.run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=stdout,
            stderr=stderr,
        ),
    )

    with pytest.raises(
        PromotionSnapshotError,
        match=rf"invalid UTF-8 on {stream_name}: gh",
    ):
        _run(("gh", "pr", "diff", "1557"), runner=None)


@pytest.mark.parametrize("stream_name,fd", [("stdout", 1), ("stderr", 2)])
@pytest.mark.parametrize("returncode", [0, 7])
def test_real_child_invalid_utf8_fails_closed(
    stream_name: str,
    fd: int,
    returncode: int,
) -> None:
    """Decode in the parent thread for consistent Windows/POSIX behavior."""

    script = (
        f"import os; os.write({fd}, b'\\x80'); "
        f"raise SystemExit({returncode})"
    )

    with pytest.raises(
        PromotionSnapshotError,
        match=rf"invalid UTF-8 on {stream_name}: ",
    ):
        _run((sys.executable, "-c", script), runner=None)


def _pr_view(
    *,
    base: str = BASE,
    checks: list[dict] | None = None,
    task: str = TASK,
    changed_files: int = 1,
) -> dict:
    return {
        "number": PR,
        "title": "fix(bridge): promotion snapshot fixture",
        "headRefName": task,
        "headRefOid": HEAD,
        "baseRefOid": base,
        "baseRefName": "main",
        "mergeable": "MERGEABLE",
        "state": "OPEN",
        "isDraft": True,
        "url": f"https://github.example/pull/{PR}",
        "reviewDecision": "",
        "updatedAt": "2026-07-24T09:00:00Z",
        "changedFiles": changed_files,
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
        "statusCheckRollup": (
            checks
            if checks is not None
            else [
                {"name": "unified", "status": "COMPLETED", "conclusion": "SUCCESS"},
                {"name": "test (3.13)", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ]
        ),
    }


def _runner(
    *,
    base: str = BASE,
    checks: list[dict] | None = None,
    paths: list[str] | None = None,
    file_records: list[dict] | None = None,
    diff: str = DIFF,
    task: str = TASK,
):
    paths = paths or PATHS
    records = file_records or [
        {"filename": path, "status": "modified"}
        for path in paths
    ]
    commands = {
        (
            "gh",
            "pr",
            "view",
            str(PR),
            "--json",
            f"{GH_JSON_FIELDS},state",
            "--repo",
            REPO,
        ): _completed(
            json.dumps(
                _pr_view(
                    base=base,
                    checks=checks,
                    task=task,
                    changed_files=len(records),
                )
            )
        ),
        (
            "gh",
            "api",
            "--method",
            "GET",
            f"repos/{REPO}/git/ref/heads/main",
        ): _completed(
            json.dumps(
                {
                    "ref": "refs/heads/main",
                    "object": {"type": "commit", "sha": base},
                }
            )
        ),
        (
            "gh",
            "api",
            "--method",
            "GET",
            f"repos/{REPO}/pulls/{PR}/files",
            "-f",
            "per_page=100",
            "-f",
            "page=1",
        ): _completed(
            json.dumps(
                records
            )
        ),
        (
            "gh",
            "pr",
            "diff",
            str(PR),
            "--patch",
            "--repo",
            REPO,
        ): _completed(diff),
    }

    def run(command: tuple[str, ...]) -> SimpleNamespace:
        key = tuple(command)
        if key not in commands:
            raise AssertionError(f"unexpected command: {key!r}")
        return commands[key]

    return run


def _event(
    agent: str,
    status: str,
    *,
    type_: str = "decision",
    task_id: str = TASK,
    head: str = HEAD,
    ts: str = "2026-06-05T05:30:00Z",
    write_scope: list[str] | None = None,
) -> dict:
    event = {
        "ts_utc": ts,
        "agent": agent,
        "type": type_,
        "status": status,
        "task_id": task_id,
        "message": f"{status} PR #{PR} exact head {head}",
        "paths": [f"github:pull-requests/{PR}", *PATHS],
        "write_scope": write_scope or [],
        "payload": {"head": head, "pr": PR},
    }
    if agent in AGENT_UUIDS:
        event["agent_uuid"] = AGENT_UUIDS[agent]
    return event


def _events(
    *, include_consensus: bool = True, include_claim: bool = True
) -> list[dict]:
    events: list[dict] = []
    if include_claim:
        events.append(
            _event(
                "fable-5",
                "active",
                type_="claim",
                ts="2026-06-05T05:29:00Z",
                write_scope=list(PATHS),
            )
        )
    if include_consensus:
        events.extend(
            [
                _event(
                    "codex-lead-1",
                    "build_consensus_pass",
                    ts="2026-06-05T05:30:00Z",
                ),
                _event(
                    "codex-tools-1",
                    "build_consensus_pass",
                    ts="2026-06-05T05:31:00Z",
                ),
                _event("claude-rco-1", "rco_pass", ts="2026-06-05T05:32:00Z"),
            ]
        )
    return events


def _events_path(tmp_path: Path, events: list[dict]) -> Path:
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events),
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    "raw",
    [
        '{"agent":"intruder","agent":"fable-5","payload":{}}',
        '{"agent":"fable-5","payload":{"head":"old","head":"new"}}',
    ],
)
def test_event_loader_rejects_duplicate_keys_at_any_nesting_without_leaks(
    tmp_path: Path,
    raw: str,
) -> None:
    path = tmp_path / "sensitive-events.jsonl"
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(PromotionSnapshotError) as raised:
        _read_events_fail_closed(path)

    message = str(raised.value)
    assert message == (
        "invalid bridge events JSON at line 1: duplicate object key"
    )
    assert str(path) not in message
    assert raw not in message


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_event_loader_rejects_all_nonfinite_json_constants(
    tmp_path: Path,
    constant: str,
) -> None:
    path = tmp_path / "events.jsonl"
    path.write_text(
        f'{{"agent":"fable-5","payload":{{"score":{constant}}}}}',
        encoding="utf-8",
    )

    with pytest.raises(
        PromotionSnapshotError,
        match=(
            r"^invalid bridge events JSON at line 1: "
            r"non-finite numeric constant$"
        ),
    ):
        _read_events_fail_closed(path)


def test_event_loader_maps_invalid_utf8_to_path_safe_controlled_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sensitive-events.jsonl"
    path.write_bytes(b'{"agent":"secret-\x80"}\n')

    with pytest.raises(PromotionSnapshotError) as raised:
        _read_events_fail_closed(path)

    message = str(raised.value)
    assert message == "bridge events file is not valid UTF-8"
    assert str(path) not in message
    assert "secret" not in message


def test_event_loader_maps_malformed_json_to_path_safe_controlled_error(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sensitive-events.jsonl"
    raw = '{"agent":"secret-value","payload":'
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(PromotionSnapshotError) as raised:
        _read_events_fail_closed(path)

    message = str(raised.value)
    assert message == "invalid bridge events JSON at line 1"
    assert str(path) not in message
    assert "secret-value" not in message
    assert raw not in message


def test_event_loader_maps_read_failure_to_path_safe_controlled_error(
    tmp_path: Path,
) -> None:
    with pytest.raises(PromotionSnapshotError) as raised:
        _read_events_fail_closed(tmp_path)

    message = str(raised.value)
    assert message == "bridge events file could not be read"
    assert str(tmp_path) not in message


def test_duplicate_security_identity_fails_closed_end_to_end(
    tmp_path: Path,
) -> None:
    lines = [json.dumps(event, sort_keys=True) for event in _events()]
    lines[0] = lines[0].replace(
        '"agent": "fable-5"',
        '"agent": "intruder", "agent": "fable-5"',
        1,
    )
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")

    report = build_promotion_snapshot(
        repo=REPO,
        pr_number=PR,
        events_path=path,
        task_id=TASK,
        origin_main_sha=BASE,
        runner=_runner(),
    )

    assert report["eligible"] is False
    assert report["decision"] == "invalid_input"
    assert report["undraft_cmd"] == []
    assert report["merge_cmd"] == []
    assert report["errors"] == [
        "invalid bridge events JSON at line 1: duplicate object key"
    ]


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_nonfinite_event_json_fails_closed_end_to_end(
    tmp_path: Path,
    constant: str,
) -> None:
    lines = [json.dumps(event, sort_keys=True) for event in _events()]
    lines[0] = lines[0].replace(
        '"payload": {',
        f'"payload": {{"risk_score": {constant}, ',
        1,
    )
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(lines), encoding="utf-8")

    report = build_promotion_snapshot(
        repo=REPO,
        pr_number=PR,
        events_path=path,
        task_id=TASK,
        origin_main_sha=BASE,
        runner=_runner(),
    )

    assert report["eligible"] is False
    assert report["decision"] == "invalid_input"
    assert report["undraft_cmd"] == []
    assert report["merge_cmd"] == []
    assert report["errors"] == [
        "invalid bridge events JSON at line 1: non-finite numeric constant"
    ]


def _build(
    tmp_path: Path,
    *,
    events: list[dict] | None = None,
    base: str = BASE,
    origin_main_sha: str = BASE,
    author_agent: str = "",
    paths: list[str] | None = None,
    file_records: list[dict] | None = None,
    diff: str = DIFF,
    task: str = TASK,
    rco_agents: object = None,
) -> dict:
    event_rows = events if events is not None else _events()
    if events is None and paths is not None:
        for event in event_rows:
            if event.get("type") == "claim":
                event["write_scope"] = list(paths)
    return build_promotion_snapshot(
        repo=REPO,
        pr_number=PR,
        events_path=_events_path(tmp_path, event_rows),
        task_id=task,
        origin_main_sha=origin_main_sha,
        author_agent=author_agent,
        rco_agents=rco_agents,  # type: ignore[arg-type]
        runner=_runner(
            base=base,
            paths=paths,
            file_records=file_records,
            diff=diff,
            task=task,
        ),
    )


def test_builds_eligible_dry_run_snapshot_from_gh_and_bridge_claim(
    tmp_path: Path,
) -> None:
    report = _build(tmp_path)

    assert report["eligible"] is True
    assert report["decision"] == "promotion_eligible"
    assert report["queue_route"] == "autonomous_promotion_ready"
    assert report["next_action"] == "run_promotion_executor_with_match_head"
    assert report["operator_required"] is False
    assert report["author_agent"] == "fable-5"
    assert report["pr_status"]["head_sha"] == HEAD
    assert report["pr_status"]["base_sha"] == BASE
    assert report["pr_status"]["changed_paths"] == PATHS
    assert report["pr_status"]["checks"][0]["conclusion"] == "SUCCESS"
    assert report["undraft_cmd"] == ["gh", "pr", "ready", str(PR), "--repo", REPO]
    assert report["merge_cmd"] == [
        "gh",
        "pr",
        "merge",
        str(PR),
        "--repo",
        REPO,
        "--match-head-commit",
        HEAD,
        "--squash",
    ]
    assert report["external_effect"] is False
    assert report["would_execute"] is False


def test_build_uses_canonical_rename_source_and_target_paths(
    tmp_path: Path,
) -> None:
    paths = ["tools/new_name.py", "tools/old_name.py"]
    report = _build(
        tmp_path,
        paths=paths,
        file_records=[
            {
                "filename": "tools/new_name.py",
                "previous_filename": "tools/old_name.py",
                "status": "renamed",
            }
        ],
    )

    assert report["eligible"] is True
    assert report["pr_status"]["changed_paths"] == paths


def test_stale_base_returns_not_eligible_without_commands(tmp_path: Path) -> None:
    report = _build(tmp_path, base=OTHER_BASE, origin_main_sha=BASE)

    assert report["eligible"] is False
    assert report["decision"] == "promotion_not_eligible"
    assert report["queue_route"] == "refresh_base_required"
    assert report["next_action"] == "attempt_content_identical_rebase_then_recheck_ci"
    assert report["operator_required"] is False
    assert "base is stale" in report["reasons"]
    assert report["undraft_cmd"] == []
    assert report["merge_cmd"] == []


def test_missing_bridge_consensus_returns_not_eligible(tmp_path: Path) -> None:
    report = _build(tmp_path, events=_events(include_consensus=False))

    assert report["eligible"] is False
    assert report["decision"] == "promotion_not_eligible"
    assert report["queue_route"] == "await_bridge_consensus"
    assert report["next_action"] == "request_missing_head_bound_build_or_rco_consensus"
    assert report["operator_required"] is False
    assert "bridge consensus incomplete" in report["reasons"]
    assert (
        "missing exact-head RCO_PASS from recognized non-author RCO"
        in report["reasons"]
    )


def test_lead_authored_tools_and_rco_satisfy_build_author_slot_waiver(
    tmp_path: Path,
) -> None:
    task = "codex-lead-1/promotion-snapshot-fixture-20260605"
    events = [
        _event(
            "codex-lead-1",
            "active",
            type_="claim",
            task_id=task,
            ts="2026-06-05T05:29:00Z",
            write_scope=list(PATHS),
        ),
        _event(
            "codex-tools-1",
            "build_consensus_pass",
            task_id=task,
            ts="2026-06-05T05:31:00Z",
        ),
        _event(
            "claude-rco-1",
            "rco_pass",
            task_id=task,
            ts="2026-06-05T05:32:00Z",
        ),
    ]

    report = _build(
        tmp_path,
        events=events,
        author_agent="codex-lead-1",
        task=task,
    )

    assert report["eligible"] is True
    assert report["decision"] == "promotion_eligible"
    assert report["queue_route"] == "autonomous_promotion_ready"
    assert report["gate_diagnostics"] == []
    consensus = report["eligibility"]["gate_results"]["bridge_consensus"]["by_agent"][
        "claude-rco-1"
    ]
    assert consensus["build_author_slot_waivers"] == ["codex-lead-1"]
    assert consensus["identities"]["build_lead"]["approved"] is True
    assert consensus["identities"]["build_lead"]["direct_approval"] is False
    assert (
        consensus["identities"]["build_lead"]["build_author_slot_waived"] is True
    )
    assert consensus["identities"]["build_tools"]["approved"] is True


def test_missing_author_claim_fails_closed(tmp_path: Path) -> None:
    report = _build(tmp_path, events=_events(include_claim=False))

    assert report["eligible"] is False
    assert report["decision"] == "operator_review_required"
    assert report["queue_route"] == "manual_triage_required"
    assert report["next_action"] == "inspect_pr_author_evidence"
    assert report["operator_required"] is True
    assert "no valid UUID-bound canonical write claim" in report["errors"][0]
    assert report["undraft_cmd"] == []
    assert report["merge_cmd"] == []


def test_operator_gated_path_routes_to_operator_signature(
    tmp_path: Path,
) -> None:
    report = _build(
        tmp_path,
        author_agent="fable-5",
        paths=["CLAUDE.md"],
        diff="diff --git a/CLAUDE.md b/CLAUDE.md\n+gate policy\n",
    )

    assert report["eligible"] is False
    assert report["decision"] == "promotion_not_eligible"
    assert report["queue_route"] == "operator_signature_required"
    assert report["next_action"] == "leave_pr_for_operator_gated_review"
    assert report["operator_required"] is True
    assert "path gate failed: denylist hit" in report["reasons"]
    assert report["undraft_cmd"] == []
    assert report["merge_cmd"] == []


def test_pending_ci_routes_to_ci_wait_or_debug(tmp_path: Path) -> None:
    report = build_promotion_snapshot(
        repo=REPO,
        pr_number=PR,
        events_path=_events_path(tmp_path, _events()),
        origin_main_sha=BASE,
        runner=_runner(checks=[{"name": "unified", "state": "pending"}]),
    )

    assert report["eligible"] is False
    assert report["decision"] == "promotion_not_eligible"
    assert report["queue_route"] == "await_ci_green"
    assert report["next_action"] == "wait_for_or_debug_required_status_checks"
    assert report["operator_required"] is False
    assert "status checks not green: unified" in report["reasons"]


def test_pr_head_drift_during_snapshot_capture_fails_closed(
    tmp_path: Path,
) -> None:
    stable_runner = _runner()
    view_calls = 0

    def drift_runner(command: tuple[str, ...]) -> SimpleNamespace:
        nonlocal view_calls
        if tuple(command[:3]) == ("gh", "pr", "view"):
            view_calls += 1
            if view_calls == 2:
                moved = _pr_view()
                moved["headRefOid"] = "0" * 40
                return _completed(json.dumps(moved))
        return stable_runner(command)

    report = build_promotion_snapshot(
        repo=REPO,
        pr_number=PR,
        events_path=_events_path(tmp_path, _events()),
        origin_main_sha=BASE,
        runner=drift_runner,
    )

    assert report["eligible"] is False
    assert report["decision"] == "invalid_input"
    assert "headRefOid changed during snapshot capture" in report["errors"][0]


@pytest.mark.parametrize("pr_number", [True, 901.0, 901.5, "901"])
def test_non_integral_pr_number_fails_before_runner(
    tmp_path: Path,
    pr_number: object,
) -> None:
    def runner(command):
        raise AssertionError(f"runner should not be called: {command}")

    report = build_promotion_snapshot(
        repo=REPO,
        pr_number=pr_number,  # type: ignore[arg-type]
        events_path=_events_path(tmp_path, _events()),
        origin_main_sha=BASE,
        runner=runner,
    )

    assert report["decision"] == "invalid_input"
    assert "positive integer" in report["errors"][0]


@pytest.mark.parametrize("task_id", [False, 0, None, [], {}])
def test_falsey_nonstring_task_id_does_not_default_to_head_ref(
    tmp_path: Path,
    task_id: object,
) -> None:
    report = build_promotion_snapshot(
        repo=REPO,
        pr_number=PR,
        events_path=_events_path(tmp_path, _events()),
        task_id=task_id,  # type: ignore[arg-type]
        origin_main_sha=BASE,
        runner=_runner(),
    )

    assert report["eligible"] is False
    assert report["decision"] == "invalid_input"
    assert report["errors"] == ["task_id must be a string"]
    assert report["undraft_cmd"] == []
    assert report["merge_cmd"] == []


@pytest.mark.parametrize("origin_main_sha", [False, 0, None, [], {}])
def test_falsey_nonstring_origin_main_sha_fails_before_runner(
    tmp_path: Path,
    origin_main_sha: object,
) -> None:
    def runner(command):
        raise AssertionError(f"runner should not be called: {command}")

    report = build_promotion_snapshot(
        repo=REPO,
        pr_number=PR,
        events_path=_events_path(tmp_path, _events()),
        origin_main_sha=origin_main_sha,  # type: ignore[arg-type]
        runner=runner,
    )

    assert report["eligible"] is False
    assert report["decision"] == "invalid_input"
    assert report["errors"] == ["origin_main_sha must be a string"]
    assert report["undraft_cmd"] == []
    assert report["merge_cmd"] == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("repo", f" {REPO}"),
        ("repo", False),
        ("events_path", False),
        ("charter_path", False),
        ("task_id", f" {TASK}"),
        ("author_agent", False),
        ("author_agent", 7),
        ("author_agent", " fable-5"),
        ("from_agent", False),
        ("from_agent", 7),
        ("from_agent", " codex-lead-1"),
        ("prior_approved_head", False),
        ("prior_approved_head", 7),
        ("prior_approved_diff_file", False),
        ("rco_agents", False),
        ("rco_agents", "claude-rco-1"),
        ("rco_agents", []),
        ("rco_agents", [1]),
        ("rco_agents", [" claude-rco-1"]),
    ],
)
def test_public_inputs_reject_wrong_types_and_padded_identities_before_runner(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...]):
        calls.append(tuple(command))
        raise AssertionError("invalid input must fail before runner")

    kwargs: dict[str, object] = {
        "repo": REPO,
        "pr_number": PR,
        "events_path": _events_path(tmp_path, _events()),
        "origin_main_sha": BASE,
        "runner": runner,
    }
    kwargs[field] = value

    report = build_promotion_snapshot(**kwargs)  # type: ignore[arg-type]

    assert calls == []
    assert report["eligible"] is False
    assert report["decision"] == "invalid_input"
    assert report["undraft_cmd"] == []
    assert report["merge_cmd"] == []


@pytest.mark.parametrize("runner_value", [False, 0, 7, [], object()])
def test_noncallable_runner_never_falls_back_to_live_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runner_value: object,
) -> None:
    def forbidden(*_args, **_kwargs):
        raise AssertionError("default runner must not be selected")

    monkeypatch.setattr(promotion_snapshot_tool.subprocess, "run", forbidden)
    monkeypatch.setattr(pr_status_tool, "_run_command", forbidden)

    report = build_promotion_snapshot(
        repo=REPO,
        pr_number=PR,
        events_path=_events_path(tmp_path, _events()),
        origin_main_sha=BASE,
        runner=runner_value,  # type: ignore[arg-type]
    )

    assert report["decision"] == "invalid_input"
    assert report["eligible"] is False
    assert report["undraft_cmd"] == []
    assert report["merge_cmd"] == []


def test_falsey_callable_runner_is_used_instead_of_default(
    tmp_path: Path,
) -> None:
    delegate = _runner()
    calls: list[tuple[str, ...]] = []

    class FalseyRunner:
        def __bool__(self) -> bool:
            return False

        def __call__(self, command: tuple[str, ...]) -> SimpleNamespace:
            calls.append(tuple(command))
            return delegate(tuple(command))

    report = build_promotion_snapshot(
        repo=REPO,
        pr_number=PR,
        events_path=_events_path(tmp_path, _events()),
        origin_main_sha=BASE,
        runner=FalseyRunner(),
    )

    assert report["eligible"] is True
    assert calls


@pytest.mark.parametrize("returncode", [None, False, "0", 0.0])
def test_runner_returncode_requires_an_exact_integer(
    returncode: object,
) -> None:
    result = SimpleNamespace(stdout="", stderr="")
    if returncode is not None:
        result.returncode = returncode

    with pytest.raises(
        PromotionSnapshotError,
        match="runner result returncode must be an integer",
    ):
        _run(("gh", "pr", "view", "1"), runner=lambda _command: result)


def test_nonzero_runner_result_validates_stream_types_before_formatting() -> None:
    with pytest.raises(
        PromotionSnapshotError,
        match="runner result streams must be text",
    ):
        _run(
            ("gh", "pr", "view", "1"),
            runner=lambda _command: SimpleNamespace(
                returncode=1,
                stdout="",
                stderr={"sensitive": "value"},
            ),
        )


@pytest.mark.parametrize("kind", ["missing", "invalid_utf8"])
def test_prior_diff_read_failures_are_controlled_path_safe_and_pre_runner(
    tmp_path: Path,
    kind: str,
) -> None:
    path = tmp_path / "sensitive-prior.diff"
    if kind == "invalid_utf8":
        path.write_bytes(b"+ secret-\x80\n")
    calls: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...]):
        calls.append(tuple(command))
        raise AssertionError("invalid prior diff must fail before runner")

    report = build_promotion_snapshot(
        repo=REPO,
        pr_number=PR,
        events_path=_events_path(tmp_path, _events()),
        origin_main_sha=BASE,
        prior_approved_diff_file=path,
        runner=runner,
    )

    assert calls == []
    assert report["decision"] == "invalid_input"
    assert report["eligible"] is False
    assert report["undraft_cmd"] == []
    assert report["merge_cmd"] == []
    message = report["errors"][0]
    assert str(path) not in message
    assert "secret" not in message


def test_unregistered_configured_rco_cannot_authorize_promotion(
    tmp_path: Path,
) -> None:
    events = _events()
    events[-1]["agent"] = "evil-rco"
    events[-1].pop("agent_uuid")

    report = _build(
        tmp_path,
        events=events,
        rco_agents=["evil-rco"],
    )

    assert report["eligible"] is False
    assert report["undraft_cmd"] == []
    assert report["merge_cmd"] == []
    bridge = report["eligibility"]["gate_results"]["bridge_consensus"]
    assert bridge["ok"] is False
    assert bridge["decision"] == "invalid_consensus_config"


def test_mixed_registered_and_unregistered_rco_config_fails_closed(
    tmp_path: Path,
) -> None:
    report = _build(
        tmp_path,
        events=_events(),
        rco_agents=["claude-rco-1", "evil-rco"],
    )

    assert report["eligible"] is False
    assert report["undraft_cmd"] == []
    assert report["merge_cmd"] == []
    bridge = report["eligibility"]["gate_results"]["bridge_consensus"]
    assert bridge["ok"] is False
    assert bridge["decision"] == "invalid_consensus_config"
    assert bridge["by_agent"]["claude-rco-1"]["ok"] is True
    assert (
        bridge["by_agent"]["evil-rco"]["decision"]
        == "invalid_consensus_config"
    )


def test_cli_exit_codes_follow_eligibility(tmp_path: Path, capsys) -> None:
    eligible_events = _events_path(tmp_path, _events())
    eligible = main(
        [
            "--repo",
            REPO,
            "--pr-number",
            str(PR),
            "--events",
            str(eligible_events),
            "--origin-main-sha",
            BASE,
            "--json",
        ],
        runner=_runner(),
    )

    assert eligible == 0
    assert json.loads(capsys.readouterr().out)["eligible"] is True

    missing_claim_events = _events_path(tmp_path, _events(include_claim=False))
    invalid = main(
        [
            "--repo",
            REPO,
            "--pr-number",
            str(PR),
            "--events",
            str(missing_claim_events),
            "--origin-main-sha",
            BASE,
            "--json",
        ],
        runner=_runner(),
    )

    assert invalid == 3
    assert (
        json.loads(capsys.readouterr().out)["decision"]
        == "operator_review_required"
    )


def test_cli_default_events_uses_runtime_bridge_root_env(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    bridge_root = tmp_path / "runtime" / ".agent-bridge"
    events_path = bridge_root / "shared" / "events.jsonl"
    events_path.parent.mkdir(parents=True)
    events_path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in _events()),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(bridge_root))

    result = main(
        [
            "--repo",
            REPO,
            "--pr-number",
            str(PR),
            "--origin-main-sha",
            BASE,
            "--json",
        ],
        runner=_runner(),
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out)["eligible"] is True


def test_cli_explicit_events_overrides_runtime_bridge_root_env(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    bridge_root = tmp_path / "runtime" / ".agent-bridge"
    runtime_events_path = bridge_root / "shared" / "events.jsonl"
    runtime_events_path.parent.mkdir(parents=True)
    runtime_events_path.write_text(
        "\n".join(
            json.dumps(event, sort_keys=True)
            for event in _events(include_consensus=False)
        ),
        encoding="utf-8",
    )
    explicit_dir = tmp_path / "explicit"
    explicit_dir.mkdir()
    explicit_events = _events_path(explicit_dir, _events())
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(bridge_root))

    result = main(
        [
            "--repo",
            REPO,
            "--pr-number",
            str(PR),
            "--events",
            str(explicit_events),
            "--origin-main-sha",
            BASE,
            "--json",
        ],
        runner=_runner(),
    )

    assert result == 0
    assert json.loads(capsys.readouterr().out)["eligible"] is True
