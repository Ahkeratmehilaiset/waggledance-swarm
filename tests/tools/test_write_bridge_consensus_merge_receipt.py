# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

import tools.write_bridge_consensus_merge_receipt as receipt_tool
from tools.bridge_pr_author import github_pr_git_identity_evidence
from tools.verify_magma_receipt import verify_manifest
from tools.write_bridge_consensus_merge_receipt import (
    BridgeConsensusMergeReceiptError,
    write_bridge_consensus_merge_receipt,
)


HEAD = "1234567890abcdef1234567890abcdef12345678"
BASE = "fedcba0987654321fedcba0987654321fedcba09"
TASK = "claude-rco-2/bridge-consensus-receipt-test"
NOW = datetime(2026, 6, 13, 22, 50, tzinfo=timezone.utc)
AGENT_UUIDS = {
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    "claude-rco-2": "76739997-0058-41a2-8514-78ff295537aa",
    "codex-lead-1": "d3c9d1d1-96a9-4eb8-a8e2-6f05f9d1a101",
    "codex-tools-1": "7a8af68d-20bc-4598-9953-23c5dd98b102",
}


def test_explicit_operator_path_receipt_is_truthful(tmp_path: Path) -> None:
    from waggledance.core.magma.canonical import sha256_digest
    status = _pr_status()
    status["changed_paths"] = ["ops/windows/reboot/wd_supervisor.ps1"]
    grant = {
        "schema": "wd.operator-path-exception.v1", "repo": "example/repo",
        "pr_number": 1174, "head": HEAD, "base": BASE,
        "diff_digest": sha256_digest(status["diff_text"]),
        "paths": status["changed_paths"], "approval_reference": "operator-test-instruction",
        "issued_at": NOW.isoformat(), "expires_at": (NOW + timedelta(hours=1)).isoformat(),
    }
    report = write_bridge_consensus_merge_receipt(
        pr_status=status, events_path=_events_path(tmp_path, _full_consensus()),
        out_dir=tmp_path / "receipt", expected_head=HEAD, expected_base_sha=BASE,
        consensus_proposal_id=TASK, repo="example/repo", from_agent="codex-lead-1",
        bridge_task_id=TASK, now_utc=NOW, operator_path_exception=grant,
        rco_enrollment_context=_unknown_enrollment_context(),
    )
    directory = Path(report["receipt_bundle_path"]).parent
    payload = json.loads((directory / "payload-001-merge.json").read_text())
    assert payload["path_gate"]["allowed"] is False
    assert payload["operator_path_exception"]["grant"] == grant
    assert payload["original_gate"]["ok"] is False
    assert verify_manifest(Path(report["receipt_bundle_path"]))["ok"] is True
    bundle_text = "".join(p.read_text() for p in directory.glob("*.json"))
    assert "path_gate:allowlist_clean" not in bundle_text
    assert "path_gate:explicit_operator_exception" in bundle_text


def _main_args(tmp_path: Path, status_path: Path) -> list[str]:
    return [
        "--pr-status-file",
        str(status_path),
        "--out-dir",
        str(tmp_path / "receipt"),
        "--expected-head",
        HEAD,
        "--expected-base-sha",
        BASE,
        "--consensus-proposal-id",
        TASK,
        "--json",
    ]


def test_cli_default_events_uses_runtime_bridge_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text("{}", encoding="utf-8")
    bridge_root = tmp_path / "runtime" / ".agent-bridge"
    captured: dict[str, Path] = {}

    def fake_write(**kwargs):
        captured["events_path"] = kwargs["events_path"]
        return {
            "ok": True,
            "decision": "bridge_consensus_merge_receipt_written",
            "receipt_bundle_path": "receipt/manifest.json",
        }

    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(bridge_root))
    monkeypatch.setattr(
        receipt_tool,
        "write_bridge_consensus_merge_receipt",
        fake_write,
    )

    exit_code = receipt_tool.main(_main_args(tmp_path, status_path))

    assert exit_code == 0
    assert captured["events_path"] == bridge_root / "shared" / "events.jsonl"
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_cli_mismatched_events_fails_before_receipt_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    status_path = tmp_path / "status.json"
    status_path.write_text("{}", encoding="utf-8")
    bridge_root = tmp_path / "runtime" / ".agent-bridge"
    outside = tmp_path / "snapshot" / "events.jsonl"
    outside.parent.mkdir()
    outside.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        receipt_tool,
        "write_bridge_consensus_merge_receipt",
        lambda **kwargs: pytest.fail("receipt writer must not be called"),
    )

    exit_code = receipt_tool.main(
        _main_args(tmp_path, status_path)
        + ["--bridge-root", str(bridge_root), "--events", str(outside)]
    )

    assert exit_code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "invalid_input"


def test_writes_verified_bridge_consensus_merge_receipt(tmp_path: Path) -> None:
    events_path = _events_path(tmp_path, _full_consensus())
    report = write_bridge_consensus_merge_receipt(
        pr_status=_pr_status(),
        events_path=events_path,
        out_dir=tmp_path / "receipt",
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        repo="example/repo",
        from_agent="codex-lead-1",
        bridge_task_id=TASK,
        now_utc=NOW,
        rco_enrollment_context=_unknown_enrollment_context(),
    )

    assert report["decision"] == "bridge_consensus_merge_receipt_written"
    assert report["ok"] is True
    bundle = report["receipt_bundle"]
    assert bundle["verifier_report"] == {
        "ok": True,
        "receipt_count": 1,
        "errors": [],
    }
    manifest = Path(report["receipt_bundle_path"])
    assert verify_manifest(manifest)["ok"] is True
    payload = json.loads(
        (manifest.parent / "payload-001-merge.json").read_text(encoding="utf-8")
    )
    receipt = json.loads(
        (manifest.parent / "receipt-001-merge.json").read_text(encoding="utf-8")
    )
    assert payload["bridge_consensus"]["decision"] == "bridge_consensus_verified"
    assert payload["diff_digest"].startswith("sha256:")
    assert receipt["risk_class"] == "external_effect"
    assert receipt["operator_gate_required"] is True
    assert receipt["approval_id"] == f"bridge:consensus:pr1174:{HEAD[:12]}"


def test_refuses_without_exact_head_rco_pass(tmp_path: Path) -> None:
    events = _full_consensus()[:-1]
    with pytest.raises(BridgeConsensusMergeReceiptError) as excinfo:
        write_bridge_consensus_merge_receipt(
            pr_status=_pr_status(),
            events_path=_events_path(tmp_path, events),
            out_dir=tmp_path / "receipt",
            expected_head=HEAD,
            expected_base_sha=BASE,
            consensus_proposal_id=TASK,
            repo="example/repo",
            from_agent="codex-lead-1",
            bridge_task_id=TASK,
            now_utc=NOW,
            rco_enrollment_context=_unknown_enrollment_context(),
        )

    report = excinfo.value.report
    assert report["decision"] == "merge_plan_not_receipt_eligible"
    assert report["ok"] is False
    assert any("bridge consensus incomplete" in error for error in report["errors"])
    assert not (tmp_path / "receipt").exists()


@pytest.mark.parametrize(
    "now_utc",
    [False, 0, 7, "2026-06-13T22:50:00Z", datetime(2026, 6, 13, 22, 50)],
)
def test_receipt_writer_rejects_nonaware_datetime_before_writes(
    tmp_path: Path,
    now_utc: object,
) -> None:
    out_dir = tmp_path / "receipt"

    with pytest.raises(BridgeConsensusMergeReceiptError) as excinfo:
        write_bridge_consensus_merge_receipt(
            pr_status=_pr_status(),
            events_path=_events_path(tmp_path, _full_consensus()),
            out_dir=out_dir,
            expected_head=HEAD,
            expected_base_sha=BASE,
            consensus_proposal_id=TASK,
            repo="example/repo",
            from_agent="codex-lead-1",
            bridge_task_id=TASK,
            now_utc=now_utc,  # type: ignore[arg-type]
        )

    assert excinfo.value.report["decision"] == "invalid_input"
    assert not out_dir.exists()


def test_receipt_writer_normalizes_aware_time_to_utc(tmp_path: Path) -> None:
    local_time = NOW.astimezone(timezone(timedelta(hours=2)))
    out_dir = tmp_path / "receipt"

    write_bridge_consensus_merge_receipt(
        pr_status=_pr_status(),
        events_path=_events_path(tmp_path, _full_consensus()),
        out_dir=out_dir,
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=TASK,
        repo="example/repo",
        from_agent="codex-lead-1",
        bridge_task_id=TASK,
        now_utc=local_time,
        rco_enrollment_context=_unknown_enrollment_context(),
    )

    payload = json.loads(
        (out_dir / "payload-001-merge.json").read_text(encoding="utf-8")
    )
    assert payload["created_at_utc"] == "2026-06-13T22:50:00Z"


def _pr_status() -> dict:
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
                            "name": "Jani",
                            "email": "jani@jkhservice.fi",
                            "login": "",
                        }
                    ],
                }
            ],
        },
        expected_head_sha=HEAD,
    )
    identities = material.pop("identities")
    return {
        "pr_number": 1174,
        "title": "fix(bridge): test receipt writer",
        "head_sha": HEAD,
        "base_sha": BASE,
        "head_ref": TASK,
        "state": "OPEN",
        "is_draft": False,
        "mergeable": "MERGEABLE",
        "receipt_verified": True,
        "author_agent": "claude-rco-2",
        "changed_paths": ["tools/agent_next_task.py"],
        "diff_text": "+ diagnostic_commands = []\n",
        "checks": [
            {"name": "unified", "conclusion": "SUCCESS", "status": "COMPLETED"},
            {"name": "test (3.13)", "conclusion": "SUCCESS", "status": "COMPLETED"},
        ],
        "git_identities": identities,
        "git_identity_evidence": material,
    }


def _events_path(tmp_path: Path, events: list[dict]) -> Path:
    path = tmp_path / "events.jsonl"
    path.write_text(
        "\n".join(json.dumps(event, sort_keys=True) for event in events),
        encoding="utf-8",
    )
    return path


def _full_consensus() -> list[dict]:
    return [
        _claim("claude-rco-2", "2026-06-13T22:39:00Z"),
        _event("codex-lead-1", "build_consensus_pass", "2026-06-13T22:40:00Z"),
        _event("codex-tools-1", "build_consensus_pass", "2026-06-13T22:41:00Z"),
        _event("claude-rco-1", "rco_pass", "2026-06-13T22:42:00Z"),
    ]


def _claim(agent: str, ts_utc: str) -> dict:
    event = _event(agent, "active", ts_utc)
    event["type"] = "claim"
    event["write_scope"] = ["*"]
    return event


def _event(agent: str, status: str, ts_utc: str) -> dict:
    return {
        "ts_utc": ts_utc,
        "agent": agent,
        "agent_uuid": AGENT_UUIDS[agent],
        "type": "decision",
        "status": status,
        "task_id": TASK,
        "message": f"{status} at head {HEAD}",
        "payload": {"head": HEAD, "pr": 1174},
    }


def test_injected_enrollment_context_avoids_installed_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tools import bridge_rco_enrollment as enrollment

    context = enrollment.load_rco_enrollment_context(
        bundle_root="", expected_manifest_sha256="",
    )
    assert context["status"] == "unknown"

    def forbidden_manifest_read(root):
        pytest.fail("explicit synthetic enrollment must not read an installed manifest")

    monkeypatch.setattr(enrollment, "_manifest_bytes", forbidden_manifest_read)
    report = write_bridge_consensus_merge_receipt(
        pr_status=_pr_status(), events_path=_events_path(tmp_path, _full_consensus()),
        out_dir=tmp_path / "receipt", expected_head=HEAD, expected_base_sha=BASE,
        consensus_proposal_id=TASK, repo="example/repo", from_agent="codex-lead-1",
        bridge_task_id=TASK, now_utc=NOW, rco_enrollment_context=context,
    )
    assert report["ok"] is True
    assert verify_manifest(Path(report["receipt_bundle_path"]))["ok"] is True
    assert enrollment.rco_enrollment_status("claude-rco-3", context=context) == "unknown"


def _unknown_enrollment_context():
    from tools import bridge_rco_enrollment as enrollment
    return enrollment.load_rco_enrollment_context(
        bundle_root="", expected_manifest_sha256="",
    )


@pytest.mark.parametrize("kind", ["serialized", "mutated", "uninitialized"])
def test_receipt_refuses_nonissued_or_mutated_enrollment(tmp_path, kind):
    from tools import bridge_rco_enrollment as enrollment
    context = _unknown_enrollment_context()
    if kind == "serialized":
        context = dict(context)
    elif kind == "mutated":
        dict.__setitem__(context, "status", "enrolled")
    else:
        context = dict.__new__(type(context))
    with pytest.raises(ValueError, match="invalid_rco_enrollment_context"):
        write_bridge_consensus_merge_receipt(
            pr_status=_pr_status(), events_path=tmp_path / "not-read.jsonl",
            out_dir=tmp_path / "receipt", expected_head=HEAD,
            consensus_proposal_id=TASK, now_utc=NOW,
            rco_enrollment_context=context,
        )
    assert not (tmp_path / "receipt").exists()


@pytest.mark.parametrize("failure", ["unprovisioned_rco3", "wrong_uuid", "veto"])
def test_injected_unknown_context_preserves_identity_and_veto_gates(tmp_path, failure):
    events = _full_consensus()
    if failure == "unprovisioned_rco3":
        events[-1]["agent"] = "claude-rco-3"
        events[-1]["agent_uuid"] = "3c7f49d1-5ac4-4ca3-8ff5-6c97c07bcd50"
    elif failure == "wrong_uuid":
        events[-1]["agent_uuid"] = "00000000-0000-4000-8000-000000000000"
    else:
        events.append(_event("claude-rco-1", "changes_requested", "2026-06-13T22:43:00Z"))
    with pytest.raises(BridgeConsensusMergeReceiptError) as caught:
        write_bridge_consensus_merge_receipt(
            pr_status=_pr_status(), events_path=_events_path(tmp_path, events),
            out_dir=tmp_path / "receipt", expected_head=HEAD, expected_base_sha=BASE,
            consensus_proposal_id=TASK, repo="example/repo", from_agent="codex-lead-1",
            bridge_task_id=TASK, now_utc=NOW,
            rco_enrollment_context=_unknown_enrollment_context(),
        )
    assert caught.value.report["ok"] is False
    assert not (tmp_path / "receipt").exists()


@pytest.mark.parametrize("raises", [False, True])
def test_enrollment_scope_restores_after_success_exception_and_nesting(raises):
    from tools import bridge_rco_enrollment as enrollment
    first, second = _unknown_enrollment_context(), _unknown_enrollment_context()
    assert enrollment._SCOPED_ENROLLMENT_CONTEXT.get() is None
    observed_error = False
    try:
        with enrollment.using_rco_enrollment_context(first):
            assert enrollment.load_rco_enrollment_context() is first
            with enrollment.using_rco_enrollment_context(second):
                assert enrollment.load_rco_enrollment_context() is second
            assert enrollment.load_rco_enrollment_context() is first
            if raises:
                raise RuntimeError("synthetic scope exception")
    except RuntimeError as exc:
        observed_error = True
        assert raises and str(exc) == "synthetic scope exception"
    assert observed_error is raises
    assert enrollment._SCOPED_ENROLLMENT_CONTEXT.get() is None


def test_enrollment_scope_isolated_between_execution_contexts_and_async_tasks():
    import asyncio
    from contextvars import Context
    from tools import bridge_rco_enrollment as enrollment
    first, second = _unknown_enrollment_context(), _unknown_enrollment_context()

    async def compare_tasks():
        ready, release = asyncio.Event(), asyncio.Event()

        async def scoped_task():
            with enrollment.using_rco_enrollment_context(first):
                ready.set()
                await release.wait()
                assert enrollment.load_rco_enrollment_context() is first

        async def other_task():
            await ready.wait()
            try:
                assert enrollment._SCOPED_ENROLLMENT_CONTEXT.get() is None
                with enrollment.using_rco_enrollment_context(second):
                    assert enrollment.load_rco_enrollment_context() is second
            finally:
                release.set()

        await asyncio.gather(scoped_task(), other_task())

    with enrollment.using_rco_enrollment_context(first):
        assert Context().run(enrollment._SCOPED_ENROLLMENT_CONTEXT.get) is None
    asyncio.run(compare_tasks())
    assert enrollment._SCOPED_ENROLLMENT_CONTEXT.get() is None


def test_enrollment_scope_revalidates_context_before_each_load():
    from tools import bridge_rco_enrollment as enrollment
    context = _unknown_enrollment_context()
    with enrollment.using_rco_enrollment_context(context):
        dict.__setitem__(context, "status", "enrolled")
        with pytest.raises(ValueError, match="invalid_rco_enrollment_context"):
            enrollment.load_rco_enrollment_context()
    assert enrollment._SCOPED_ENROLLMENT_CONTEXT.get() is None


def test_enrollment_scope_never_replaces_explicit_loader_inputs(tmp_path, monkeypatch):
    import hashlib
    from tools import bridge_rco_enrollment as enrollment
    generation = "a" * 40
    bundle = tmp_path / generation
    bundle.mkdir()
    data = json.dumps({"source_commit": generation}).encode("utf-8")
    (bundle / "deployment-manifest.json").write_bytes(data)
    context = _unknown_enrollment_context()
    with enrollment.using_rco_enrollment_context(context):
        explicit = enrollment.load_rco_enrollment_context(
            bundle_root=bundle, expected_manifest_sha256=hashlib.sha256(data).hexdigest(),
        )
        assert explicit["status"] == "not_provisioned"
        assert explicit is not context
        assert _unknown_enrollment_context() is not context
        # Explicit None still selects the original environment-default path.
        # Stub only its IO boundary: never open the installed manifest in this test.
        def synthetic_unreadable(root):
            raise OSError("synthetic default-reader failure")
        monkeypatch.setattr(enrollment, "_manifest_bytes", synthetic_unreadable)
        assert enrollment.load_rco_enrollment_context(
            bundle_root=None, expected_manifest_sha256=None,
        ) is not context
        assert enrollment.load_rco_enrollment_context() is context


@pytest.mark.parametrize("explicit_none", [False, True])
def test_receipt_default_has_no_enrollment_override(tmp_path, monkeypatch, explicit_none):
    from tools import bridge_rco_enrollment as enrollment
    seen = []

    def synthetic_gate(**kwargs):
        seen.append(enrollment._SCOPED_ENROLLMENT_CONTEXT.get())
        return {"ok": False, "reasons": ["synthetic stop before receipt writes"]}

    monkeypatch.setattr(receipt_tool, "_merge_plan_report", synthetic_gate)
    optional = {"rco_enrollment_context": None} if explicit_none else {}
    with pytest.raises(BridgeConsensusMergeReceiptError):
        write_bridge_consensus_merge_receipt(
            pr_status=_pr_status(), events_path=tmp_path / "not-read.jsonl",
            out_dir=tmp_path / "receipt", expected_head=HEAD,
            consensus_proposal_id=TASK, now_utc=NOW, **optional,
        )
    assert seen == [None]
    assert not (tmp_path / "receipt").exists()
