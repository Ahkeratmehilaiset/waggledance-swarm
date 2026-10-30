# SPDX-License-Identifier: BUSL-1.1
"""Tests for the dormant, offline MAGMA merge receipt -> GitHub commit-status tool.

The fixtures build a REAL receipt bundle with ``write_bridge_consensus_merge_receipt``
(the only receipt shape the tool accepts), reusing that module's own test material
by file import so no second fixture copy drifts. Nothing here touches the network.
"""
from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

import pytest

from tools.magma_receipt_commit_status import (
    RECEIPT_ARTIFACT_VERSION,
    STATUS_CONTEXT,
    ReceiptStatusError,
    build_commit_status,
    main,
    verify_fetched_status,
)
from tools.write_bridge_consensus_merge_receipt import (
    write_bridge_consensus_merge_receipt,
)
from waggledance.core.magma.canonical import sha256_digest

ROOT = Path(__file__).resolve().parents[2]
TOOL_PATH = ROOT / "tools" / "magma_receipt_commit_status.py"
_WRITER_TESTS = Path(__file__).with_name("test_write_bridge_consensus_merge_receipt.py")


def _writer_fixtures():
    spec = importlib.util.spec_from_file_location("_writer_receipt_fixtures", _WRITER_TESTS)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


FX = _writer_fixtures()
HEAD = FX.HEAD
BASE = FX.BASE
OTHER = "0000000000000000000000000000000000000001"


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    report = write_bridge_consensus_merge_receipt(
        pr_status=FX._pr_status(),
        events_path=FX._events_path(tmp_path, FX._full_consensus()),
        out_dir=tmp_path / "receipt",
        expected_head=HEAD,
        expected_base_sha=BASE,
        consensus_proposal_id=FX.TASK,
        repo="example/repo",
        from_agent="codex-lead-1",
        bridge_task_id=FX.TASK,
        now_utc=FX.NOW,
    )
    return Path(report["receipt_bundle_path"])


def _edit_payload(manifest: Path, mutate) -> None:
    """Change the payload and re-bind the tail receipt digest (verification stays green)."""
    directory = manifest.parent
    payload_path = directory / "payload-001-merge.json"
    receipt_path = directory / "receipt-001-merge.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    mutate(payload)
    payload_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["canonical_payload_digest"] = sha256_digest(payload)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _refusal(manifest: Path, **kwargs) -> dict:
    args = {"expected_head": HEAD, "expected_base": BASE}
    args.update(kwargs)
    with pytest.raises(ReceiptStatusError) as excinfo:
        build_commit_status(manifest, **args)
    report = excinfo.value.report
    assert report["ok"] is False
    return report


def test_builds_status_payload_from_verified_receipt(manifest: Path) -> None:
    report = build_commit_status(manifest, expected_head=HEAD, expected_base=BASE)
    receipt = json.loads((manifest.parent / "receipt-001-merge.json").read_text(encoding="utf-8"))
    receipt_hex = sha256_digest(receipt).split(":", 1)[1]

    assert report["ok"] is True
    assert report["decision"] == "commit_status_payload_built"
    assert report["mode"] == "dry_run"
    assert report["status_sha"] == HEAD
    assert report["receipt_manifest"] == str(manifest.resolve())
    assert report["receipt_sha256"] == receipt_hex
    assert report["rco_agent"] == "claude-rco-1"
    assert report["identities"] == {
        "build_lead": "codex-lead-1",
        "build_tools": "codex-tools-1",
        "rco": "claude-rco-1",
    }
    status = report["status"]
    assert status["state"] == "success"
    assert status["context"] == STATUS_CONTEXT
    assert status["target_url"] is None
    assert receipt_hex[:16] in status["description"]
    assert "rco=claude-rco-1" in status["description"]
    assert HEAD[:12] in status["description"]
    assert len(status["description"]) <= 140
    command = report["gh_command_text"]
    assert command.startswith(f"gh api --method POST repos/example/repo/statuses/{HEAD} ")
    assert f"context={STATUS_CONTEXT}" in command
    assert isinstance(command, str)


def test_refuses_head_and_base_mismatch(manifest: Path) -> None:
    assert _refusal(manifest, expected_head=OTHER)["decision"] == "head_mismatch"
    assert _refusal(manifest, expected_base=OTHER)["decision"] == "base_mismatch"
    assert _refusal(manifest, expected_head="not-a-sha")["decision"] == "invalid_input"
    assert _refusal(manifest, status_sha="abc")["decision"] == "invalid_input"


def test_status_sha_targets_the_squash_merge_commit(manifest: Path) -> None:
    report = build_commit_status(manifest, expected_head=HEAD, expected_base=BASE, status_sha=OTHER)
    assert report["status_sha"] == OTHER
    assert report["head_sha"] == HEAD
    assert f"repos/example/repo/statuses/{OTHER} " in report["gh_command_text"]
    assert f"head={HEAD[:12]}" in report["status"]["description"]
    assert f"merge={OTHER[:12]}" in report["status"]["description"]
    assert len(report["status"]["description"]) <= 140


def test_symlinked_manifest_reads_the_verified_bundle(manifest: Path, tmp_path: Path) -> None:
    import os

    link_dir = tmp_path / "link"
    link_dir.mkdir()
    try:
        os.symlink(manifest, link_dir / "manifest.json")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not permitted on this host")
    for name in ("payload-001-merge.json", "receipt-001-merge.json"):
        decoy = json.loads((manifest.parent / name).read_text(encoding="utf-8"))
        decoy["head_sha"] = OTHER
        (link_dir / name).write_text(json.dumps(decoy), encoding="utf-8")
    real_receipt = json.loads((manifest.parent / "receipt-001-merge.json").read_text(encoding="utf-8"))

    report = build_commit_status(link_dir / "manifest.json", expected_head=HEAD, expected_base=BASE)

    assert report["receipt_sha256"] == sha256_digest(real_receipt).split(":", 1)[1]
    assert report["receipt_manifest"] == str(manifest.resolve())


def test_refuses_rule12_receipts_by_name(manifest: Path) -> None:
    def mutate(payload: dict) -> None:
        payload["bridge_consensus"]["review_policy"] = "rule12"

    _edit_payload(manifest, mutate)
    report = _refusal(manifest)
    assert report["decision"] == "unsupported_receipt_shape"
    assert "rule12" in " ".join(report["errors"])


def test_refuses_empty_author(manifest: Path) -> None:
    def mutate(payload: dict) -> None:
        payload["bridge_consensus"]["author_agent"] = ""

    _edit_payload(manifest, mutate)
    assert _refusal(manifest)["decision"] == "author_unknown"


def test_refuses_tampered_receipt(manifest: Path) -> None:
    receipt_path = manifest.parent / "receipt-001-merge.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["canonical_payload_digest"] = "sha256:" + "0" * 64
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = _refusal(manifest)
    assert report["decision"] == "receipt_verification_failed"
    assert any("canonical_payload_digest" in error for error in report["errors"])


def test_refuses_other_receipt_shape_even_when_verification_passes(manifest: Path) -> None:
    def mutate(payload: dict) -> None:
        payload["artifact_version"] = "wd.some_other_receipt.v9"

    _edit_payload(manifest, mutate)
    report = _refusal(manifest)
    assert report["decision"] == "unsupported_receipt_shape"
    assert RECEIPT_ARTIFACT_VERSION in " ".join(report["errors"])


def test_autonomous_gate_pass_claimed_must_be_boolean_when_present(manifest: Path) -> None:
    def mutate_bad(payload: dict) -> None:
        payload["autonomous_gate_pass_claimed"] = "false"

    _edit_payload(manifest, mutate_bad)
    assert _refusal(manifest)["decision"] == "autonomous_gate_pass_claimed_not_boolean"

    def mutate_good(payload: dict) -> None:
        payload["autonomous_gate_pass_claimed"] = False

    _edit_payload(manifest, mutate_good)
    report = build_commit_status(manifest, expected_head=HEAD, expected_base=BASE)
    assert report["autonomous_gate_pass_claimed"] is False


def test_refuses_author_equal_to_any_voter(manifest: Path) -> None:
    def mutate(payload: dict) -> None:
        payload["bridge_consensus"]["author_agent"] = "codex-tools-1"

    _edit_payload(manifest, mutate)
    report = _refusal(manifest)
    assert report["decision"] == "author_is_voter"
    assert "codex-tools-1" in " ".join(report["errors"])


def test_refuses_missing_or_unapproved_identities(manifest: Path) -> None:
    def drop_rco(payload: dict) -> None:
        del payload["bridge_consensus"]["identities"]["rco"]

    _edit_payload(manifest, drop_rco)
    assert _refusal(manifest)["decision"] == "missing_identities"


def test_refuses_unapproved_build_identity(manifest: Path) -> None:
    def unapprove(payload: dict) -> None:
        payload["bridge_consensus"]["identities"]["build_tools"]["approved"] = False

    _edit_payload(manifest, unapprove)
    assert _refusal(manifest)["decision"] == "missing_identities"


def test_refuses_rco_ref_disagreeing_with_identity(manifest: Path) -> None:
    def swap(payload: dict) -> None:
        payload["bridge_consensus"]["rco_pass_ref"]["agent"] = "claude-rco-2"

    _edit_payload(manifest, swap)
    assert _refusal(manifest)["decision"] == "missing_identities"


def test_verify_reports_equal_and_named_differences(manifest: Path, tmp_path: Path) -> None:
    built = build_commit_status(manifest, expected_head=HEAD, expected_base=BASE)
    fetched = tmp_path / "fetched.json"
    fetched.write_text(json.dumps([dict(built["status"])]), encoding="utf-8")
    equal = verify_fetched_status(fetched, manifest, expected_head=HEAD, expected_base=BASE)
    assert equal["ok"] is True
    assert equal["decision"] == "EQUAL"
    assert equal["differences"] == []
    assert equal["matching_statuses"] == 1
    assert equal["status_sha"] == HEAD

    changed = dict(built["status"])
    changed["description"] = "something else"
    fetched.write_text(json.dumps([changed, {"context": "ci/other", "state": "success"}]), encoding="utf-8")
    different = verify_fetched_status(fetched, manifest, expected_head=HEAD, expected_base=BASE)
    assert different["ok"] is False
    assert different["decision"] == "DIFFERENT"
    assert different["differences"] == [
        {"field": "description", "expected": built["status"]["description"], "fetched": "something else"}
    ]

    fetched.write_text(json.dumps([{"context": "ci/other", "state": "success"}]), encoding="utf-8")
    absent = verify_fetched_status(fetched, manifest, expected_head=HEAD, expected_base=BASE)
    assert absent["decision"] == "DIFFERENT"
    assert absent["differences"][0]["field"] == "context"

    # /commits/{sha}/statuses lists newest first: a reposted history compares the newest entry.
    stale = dict(built["status"])
    stale["state"] = "pending"
    fetched.write_text(json.dumps([dict(built["status"]), stale]), encoding="utf-8")
    history = verify_fetched_status(fetched, manifest, expected_head=HEAD, expected_base=BASE)
    assert history["decision"] == "EQUAL"
    assert history["matching_statuses"] == 2

    # the combined /commits/{sha}/status object carries one entry per context under "statuses"
    fetched.write_text(json.dumps({"state": "success", "statuses": [dict(built["status"])]}), encoding="utf-8")
    combined = verify_fetched_status(fetched, manifest, expected_head=HEAD, expected_base=BASE)
    assert combined["decision"] == "EQUAL"

    # a status posted on the squash merge commit is re-derived with the same status_sha
    merged = build_commit_status(manifest, expected_head=HEAD, expected_base=BASE, status_sha=OTHER)
    fetched.write_text(json.dumps(merged["status"]), encoding="utf-8")
    on_merge = verify_fetched_status(fetched, manifest, expected_head=HEAD, expected_base=BASE, status_sha=OTHER)
    assert on_merge["decision"] == "EQUAL"
    assert verify_fetched_status(fetched, manifest, expected_head=HEAD, expected_base=BASE)["decision"] == "DIFFERENT"


def test_cli_is_dry_run_only_and_uses_exit_codes(
    manifest: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base_args = ["--receipt-manifest", str(manifest), "--expected-head", HEAD, "--expected-base", BASE]
    assert main(base_args) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "dry_run"
    assert report["status"]["context"] == STATUS_CONTEXT

    assert main(["--receipt-manifest", str(manifest), "--expected-head", OTHER, "--expected-base", BASE]) == 2
    assert json.loads(capsys.readouterr().out)["decision"] == "head_mismatch"

    assert main(base_args + ["--status-sha", OTHER]) == 0
    assert json.loads(capsys.readouterr().out)["status_sha"] == OTHER

    fetched = tmp_path / "fetched.json"
    fetched.write_text(json.dumps({"context": STATUS_CONTEXT, "state": "failure", "description": "x"}), encoding="utf-8")
    assert main(base_args + ["--verify", str(fetched)]) == 1
    assert json.loads(capsys.readouterr().out)["decision"] == "DIFFERENT"

    with pytest.raises(SystemExit):
        main(base_args + ["--apply"])


def test_module_has_no_network_subprocess_or_apply_surface() -> None:
    source = TOOL_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {
        "subprocess", "urllib", "http", "socket", "requests", "httpx", "ssl",
        "asyncio", "os", "ftplib", "smtplib", "xmlrpc", "webbrowser",
    }
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert not (imported & forbidden), sorted(imported & forbidden)
    assert "tools" in imported and "waggledance" in imported
    assert "--apply" not in source
    assert source.count("\n") <= 300
    assert "subprocess" not in source
