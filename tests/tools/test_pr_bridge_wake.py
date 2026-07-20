# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from tools import pr_bridge_wake
from tools.pr_bridge_wake import (
    PrBridgeWakeError,
    build_pr_review_wake_event,
    emit_bridge_event,
    resolve_pr_head,
)


HEAD = "1234567890abcdef1234567890abcdef12345678"
OTHER_HEAD = "abcdef1234567890abcdef1234567890abcdef12"


@pytest.fixture(autouse=True)
def _clear_runtime_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_BRIDGE_RUNTIME_ROOT", raising=False)


def _runner(payload: dict, calls: list[list[str]] | None = None):
    def runner(command: list[str]) -> SimpleNamespace:
        if calls is not None:
            calls.append(command)
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload))

    return runner


def _payload(**overrides: object) -> dict:
    payload = {
        "number": 1505,
        "headRefOid": HEAD,
        "headRefName": "codex-lead-1/phase2e-chatserved-claim-window-evidence-20260704",
        "url": "https://github.example/pr/1505",
    }
    payload.update(overrides)
    return payload


def _event() -> dict:
    return {
        "agent": "codex-lead-1",
        "type": "wake_request",
        "task_id": "codex-lead-1/phase2e",
        "status": "review_pr1505",
        "to": "claude-rco-1",
        "message": f"Wake: review PR #1505 head {HEAD}.",
        "payload": {"head": HEAD},
    }


def _write_test_writer(root: Path) -> Path:
    writer = root / "bin" / "Write-AgentEvent.ps1"
    writer.parent.mkdir(parents=True)
    writer.write_text("# test writer\n", encoding="utf-8")
    return writer


def _make_directory_link(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
        return
    except (NotImplementedError, OSError) as exc:
        if os.name != "nt":
            pytest.skip(f"directory symlinks unavailable: {exc}")
    result = subprocess.run(
        ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"directory junctions unavailable: {result.stderr.strip()}")


def test_resolve_pr_head_uses_structured_github_head_ref_oid() -> None:
    calls: list[list[str]] = []

    snapshot = resolve_pr_head(
        pr_number=1505,
        repo="Ahkeratmehilaiset/waggledance-swarm",
        runner=_runner(_payload(), calls),
    )

    assert calls == [
        [
            "gh",
            "pr",
            "view",
            "1505",
            "--json",
            "number,headRefName,headRefOid,url",
            "--repo",
            "Ahkeratmehilaiset/waggledance-swarm",
        ]
    ]
    assert snapshot["head"] == HEAD
    assert snapshot["head_ref"].startswith("codex-lead-1/")


def test_build_event_binds_message_status_and_payload_to_authoritative_head() -> None:
    event = build_pr_review_wake_event(
        pr_number=1505,
        agent="codex-lead-1",
        task_id="codex-lead-1/phase2e",
        to="claude-rco-1,codex-tools-1",
        status="review_pr1505",
        body="Focus evidence independence.",
        declared_head=HEAD,
        runner=_runner(_payload()),
    )

    assert event["type"] == "wake_request"
    assert event["status"] == "review_pr1505"
    assert HEAD in event["message"]
    assert "Focus evidence independence." in event["message"]
    assert event["payload"]["head"] == HEAD
    assert event["payload"]["head_source"] == "gh_pr_view.headRefOid"
    assert event["payload"]["declared_head_checked"] is True


def test_declared_head_must_match_github_head() -> None:
    with pytest.raises(PrBridgeWakeError) as excinfo:
        build_pr_review_wake_event(
            pr_number=1505,
            agent="codex-lead-1",
            task_id="codex-lead-1/phase2e",
            to="claude-rco-1",
            declared_head=OTHER_HEAD,
            runner=_runner(_payload()),
        )

    assert excinfo.value.report["decision"] == "declared_head_mismatch"


def test_short_or_malformed_heads_are_refused() -> None:
    with pytest.raises(PrBridgeWakeError) as excinfo:
        build_pr_review_wake_event(
            pr_number=1505,
            agent="codex-lead-1",
            task_id="codex-lead-1/phase2e",
            to="claude-rco-1",
            declared_head="12345678",
            runner=_runner(_payload()),
        )
    assert excinfo.value.report["decision"] == "invalid_declared_head"

    with pytest.raises(PrBridgeWakeError) as excinfo:
        resolve_pr_head(
            pr_number=1505,
            runner=_runner(_payload(headRefOid="12345678")),
        )
    assert excinfo.value.report["decision"] == "invalid_head_sha"


def test_emit_bridge_event_invokes_writer_with_authoritative_payload(tmp_path: Path) -> None:
    _write_test_writer(tmp_path)
    event = build_pr_review_wake_event(
        pr_number=1505,
        agent="codex-lead-1",
        task_id="codex-lead-1/phase2e",
        to="claude-rco-1",
        runner=_runner(_payload()),
    )
    calls: list[list[str]] = []

    def runner(command: list[str]) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="")

    report = emit_bridge_event(event, bridge_root=tmp_path, run_id="run-1", runner=runner)

    assert report == {"returncode": 0}
    command = calls[0]
    payload = json.loads(command[command.index("-PayloadJson") + 1])
    assert command[command.index("-Type") + 1] == "wake_request"
    assert command[command.index("-RunId") + 1] == "run-1"
    assert payload["head"] == HEAD


def test_runtime_root_selects_only_its_deployed_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_writer = _write_test_writer(runtime_root)
    source_root = tmp_path / "source"
    _write_test_writer(source_root / ".agent-bridge")
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setattr(pr_bridge_wake, "ROOT", source_root)
    calls: list[list[str]] = []

    report = emit_bridge_event(
        _event(),
        runner=lambda command: (
            calls.append(list(command))
            or SimpleNamespace(returncode=0, stdout="")
        ),
    )

    assert report == {"returncode": 0}
    assert calls[0][calls[0].index("-File") + 1] == str(runtime_writer.resolve())


def test_conflicting_explicit_root_is_refused_when_runtime_root_is_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    explicit_root = tmp_path / "explicit"
    _write_test_writer(runtime_root)
    _write_test_writer(explicit_root)
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime_root))
    calls: list[list[str]] = []

    with pytest.raises(PrBridgeWakeError) as excinfo:
        emit_bridge_event(
            _event(),
            bridge_root=explicit_root,
            runner=lambda command: calls.append(list(command)),
        )

    assert excinfo.value.report["decision"] == "ambiguous_bridge_root"
    assert calls == []


def test_missing_runtime_writer_refuses_without_source_fallback_or_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    source_root = tmp_path / "source"
    source_writer = _write_test_writer(source_root / ".agent-bridge")
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime_root))
    monkeypatch.setattr(pr_bridge_wake, "ROOT", source_root)
    calls: list[list[str]] = []

    with pytest.raises(PrBridgeWakeError) as excinfo:
        emit_bridge_event(
            _event(),
            runner=lambda command: calls.append(list(command)),
        )

    assert excinfo.value.report["decision"] == "missing_writer"
    assert source_writer.is_file()
    assert not (runtime_root / "bin").exists()
    assert calls == []


def test_unset_runtime_root_preserves_safe_source_root_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "source"
    source_writer = _write_test_writer(source_root / ".agent-bridge")
    monkeypatch.setattr(pr_bridge_wake, "ROOT", source_root)
    calls: list[list[str]] = []

    emit_bridge_event(
        _event(),
        runner=lambda command: (
            calls.append(list(command))
            or SimpleNamespace(returncode=0, stdout="")
        ),
    )

    assert calls[0][calls[0].index("-File") + 1] == str(source_writer.resolve())


def test_lexically_escaped_runtime_root_is_refused_before_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside_root = tmp_path / "outside"
    _write_test_writer(outside_root)
    lexical_escape = tmp_path / "runtime" / ".." / "outside"
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(lexical_escape))
    calls: list[list[str]] = []

    with pytest.raises(PrBridgeWakeError) as excinfo:
        emit_bridge_event(
            _event(),
            runner=lambda command: calls.append(list(command)),
        )

    assert excinfo.value.report["decision"] == "invalid_bridge_root"
    assert calls == []


def test_reparse_writer_escape_is_refused_before_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    outside_root = tmp_path / "outside"
    outside_bin = _write_test_writer(outside_root).parent
    _make_directory_link(runtime_root / "bin", outside_bin)
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime_root))
    calls: list[list[str]] = []

    with pytest.raises(PrBridgeWakeError) as excinfo:
        emit_bridge_event(
            _event(),
            runner=lambda command: calls.append(list(command)),
        )

    assert excinfo.value.report["decision"] == "unsafe_writer_path"
    assert calls == []


def test_non_file_writer_is_refused_before_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_root = tmp_path / "runtime"
    writer = runtime_root / "bin" / "Write-AgentEvent.ps1"
    writer.mkdir(parents=True)
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime_root))
    calls: list[list[str]] = []

    with pytest.raises(PrBridgeWakeError) as excinfo:
        emit_bridge_event(
            _event(),
            runner=lambda command: calls.append(list(command)),
        )

    assert excinfo.value.report["decision"] == "unsafe_writer_path"
    assert calls == []
