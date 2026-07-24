# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from tools.build_promotion_snapshot import (
    PromotionSnapshotError,
    _run,
    build_promotion_snapshot,
    main,
)

REPO = "Ahkeratmehilaiset/waggledance-swarm"
PR = 901
TASK = "codex-tools-1/promotion-snapshot-fixture-20260605"
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


def _pr_view(*, base: str = BASE, checks: list[dict] | None = None) -> dict:
    return {
        "number": PR,
        "headRefName": TASK,
        "headRefOid": HEAD,
        "baseRefOid": base,
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
    diff: str = DIFF,
):
    paths = paths or PATHS
    commands = {
        (
            "gh",
            "pr",
            "view",
            str(PR),
            "--repo",
            REPO,
            "--json",
            "number,headRefName,headRefOid,baseRefOid,statusCheckRollup",
        ): _completed(json.dumps(_pr_view(base=base, checks=checks))),
        (
            "gh",
            "pr",
            "diff",
            str(PR),
            "--repo",
            REPO,
            "--name-only",
        ): _completed("\n".join(paths) + "\n"),
        (
            "gh",
            "pr",
            "diff",
            str(PR),
            "--repo",
            REPO,
            "--patch",
        ): _completed(diff),
    }

    def run(command: tuple[str, ...]) -> SimpleNamespace:
        if command not in commands:
            raise AssertionError(f"unexpected command: {command!r}")
        return commands[command]

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


def _build(
    tmp_path: Path,
    *,
    events: list[dict] | None = None,
    base: str = BASE,
    origin_main_sha: str = BASE,
    author_agent: str = "",
    paths: list[str] | None = None,
    diff: str = DIFF,
) -> dict:
    return build_promotion_snapshot(
        repo=REPO,
        pr_number=PR,
        events_path=_events_path(tmp_path, events if events is not None else _events()),
        origin_main_sha=origin_main_sha,
        author_agent=author_agent,
        runner=_runner(base=base, paths=paths, diff=diff),
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
    events = [
        _event("codex-tools-1", "build_consensus_pass", ts="2026-06-05T05:31:00Z"),
        _event("claude-rco-1", "rco_pass", ts="2026-06-05T05:32:00Z"),
    ]

    report = _build(tmp_path, events=events, author_agent="codex-lead-1")

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
    assert report["decision"] == "invalid_input"
    assert report["queue_route"] == "manual_triage_required"
    assert report["next_action"] == "fix_snapshot_input_then_rerun"
    assert report["operator_required"] is False
    assert "author_agent could not be derived" in report["errors"][0]
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

    assert invalid == 2
    assert json.loads(capsys.readouterr().out)["decision"] == "invalid_input"


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
