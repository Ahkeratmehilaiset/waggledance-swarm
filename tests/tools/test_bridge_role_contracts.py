# SPDX-License-Identifier: BUSL-1.1
"""F2 repository role contracts and their lint (RCO1, Lead request d79f933d).

Every mutation runs on a tmp_path copy of the contracts; the real directory is only read.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest

from tools import lint_role_contracts as lint_module
from tools.lint_role_contracts import COMMON, EXPECTED, lint, main, role_receipts

REAL = lint_module.DEFAULT_DIR
NOW = datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc)


def blob_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def snapshot(directory: Path) -> dict:
    return {p.relative_to(directory).as_posix(): p.read_bytes() for p in sorted(directory.rglob("*")) if p.is_file()}


@pytest.fixture
def contracts(tmp_path):
    copy = tmp_path / "contracts"
    shutil.copytree(REAL, copy)
    return copy


def rules(report):
    return {(error["path"], error["rule"]) for error in report["errors"]}


def test_the_repository_contracts_pass_and_hash_their_lf_normalized_bytes():
    before = snapshot(REAL)
    report = lint(REAL)
    assert report["ok"] is True and report["errors"] == [], report["errors"]
    assert [entry["path"] for entry in report["files"]] == list(EXPECTED)
    for entry in report["files"]:
        assert entry["sha256"] == blob_sha(REAL.joinpath(*entry["path"].split("/")))
    assert snapshot(REAL) == before                                          # read-only


def test_crlf_and_lf_checkouts_hash_identically(contracts):
    lf = lint(contracts)
    for path in contracts.rglob("*.md"):
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    crlf = lint(contracts)
    assert crlf["ok"] and crlf["files"] == lf["files"] and crlf["source_digest"] == lf["source_digest"]


def edit(contracts, rel, old, new):
    path = contracts.joinpath(*rel.split("/"))
    text = path.read_bytes().replace(b"\r\n", b"\n").decode("ascii")
    assert old in text, old
    path.write_bytes(text.replace(old, new, 1).encode("utf-8"))


def append(contracts, rel, line):
    path = contracts.joinpath(*rel.split("/"))
    path.write_bytes(path.read_bytes() + line.encode("utf-8") + b"\n")


@pytest.mark.parametrize("line,rule", [
    ("Start on claude-opus-5-5 at high effort.", "model_pin"),
    ("Launch with --model and keep it.", "model_flag"),
    ("Run /model to switch.", "model_flag"),
    ("Keep one CronCreate job as the backstop.", "idle_timer"),
    ("End every turn with ScheduleWakeup.", "idle_timer"),
    ("Poll the inbox every 5 minutes.", "poll_cadence"),
    ("Run .\\.agent-bridge\\bin\\Read-AgentBridge.ps1 first.", "worktree_helper"),
    ("Run python tools/bridge_next_action.py directly.", "bare_interpreter"),
    ("Read C:\\Users\\someone\\notes.md first.", "user_profile_path"),
], ids=["model_pin", "model_flag", "slash_model", "cron", "self_wake", "poll", "worktree_helper", "bare_python",
        "profile_path"])
@pytest.mark.parametrize("rel", [COMMON, "roles/rco-security.v1.md"], ids=["common", "role"])
def test_stale_or_pinned_instructions_are_refused_in_every_contract(contracts, rel, line, rule):
    assert lint(contracts)["ok"]                                               # success twin
    append(contracts, rel, line)
    report = lint(contracts)
    assert report["ok"] is False and (rel, rule) in rules(report)


@pytest.mark.parametrize("mutate,rel,rule", [
    (lambda c: c.joinpath(COMMON).write_bytes(b"\xef\xbb\xbf" + c.joinpath(COMMON).read_bytes()), COMMON, "bom"),
    (lambda c: c.joinpath(COMMON).write_bytes(c.joinpath(COMMON).read_bytes() + b"stray\rreturn\n"), COMMON,
     "lone_cr"),
    (lambda c: append(c, COMMON, "caf\u00e9"), COMMON, "non_ascii"),
    (lambda c: c.joinpath(COMMON).write_bytes(c.joinpath(COMMON).read_bytes().rstrip(b"\r\n")), COMMON,
     "no_final_newline"),
    (lambda c: c.joinpath(COMMON).write_bytes(c.joinpath(COMMON).read_bytes() + b"x" * (33 * 1024) + b"\n"),
     COMMON, "oversized"),
    (lambda c: c.joinpath("roles", "tools-tests.v1.md").unlink(), "roles/tools-tests.v1.md", "missing"),
    (lambda c: c.joinpath("roles", "extra.v1.md").write_bytes(b"# extra\n"), "roles/extra.v1.md",
     "unexpected_entry"),
    (lambda c: c.joinpath("notes").mkdir(), "notes", "unexpected_entry"),
    (lambda c: edit(c, COMMON, "## Waking\n", "## Wake\n"), COMMON, "section_missing"),
    (lambda c: edit(c, COMMON, "native/default", "native"), COMMON, "phrase_missing"),
    (lambda c: edit(c, COMMON, "contract: wd.bridge-role-contract.v1", "contract: v1"), COMMON, "marker_missing"),
    (lambda c: edit(c, "roles/lead-impl.v1.md", "role=lead-impl", "role=lead"), "roles/lead-impl.v1.md",
     "marker_missing"),
    (lambda c: edit(c, "roles/lead-impl.v1.md", "# Role contract v1: lead-impl", "# Lead"), "roles/lead-impl.v1.md",
     "title_invalid"),
    (lambda c: edit(c, "roles/fable-producer.v1.md", "role-contract.v1.md", "the common file"),
     "roles/fable-producer.v1.md", "common_reference_missing"),
], ids=["bom", "lone_cr", "non_ascii", "final_newline", "oversized", "missing", "extra_file", "extra_dir",
        "section", "phrase", "marker", "role_marker", "title", "common_reference"])
def test_format_and_structure_violations_are_refused(contracts, mutate, rel, rule):
    mutate(contracts)
    report = lint(contracts)
    assert report["ok"] is False and (rel, rule) in rules(report), report["errors"]


def test_a_linked_contract_is_refused_not_followed(contracts, tmp_path):
    target = tmp_path / "elsewhere.md"
    target.write_bytes(contracts.joinpath(COMMON).read_bytes())
    contracts.joinpath(COMMON).unlink()
    try:
        os.symlink(target, contracts.joinpath(COMMON))
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not permitted here")
    assert (COMMON, "not_a_regular_file") in rules(lint(contracts))


def test_receipts_verify_only_explicit_known_roles_on_clean_contracts(contracts):
    report = lint(contracts)
    receipts = {r["worker"]: r for r in role_receipts(report, {
        "claude-rco-1": ["rco-security"], "codex-tools-1": ["tools-tests", "tools-tests"],
        "odd-lane": ["rco-security", "no-such-role"], "empty-lane": []}, NOW)}
    good = receipts["claude-rco-1"]
    assert good["verified"] is True and good["roles"] == ["rco-security"] and good["errors"] == []
    assert good["observed_utc"] == "2026-09-30T18:00:00.000000Z"
    assert set(good["contracts"]) == {COMMON, "roles/rco-security.v1.md"} and len(good["source_digest"]) == 64
    assert receipts["codex-tools-1"]["roles"] == ["tools-tests"] and receipts["codex-tools-1"]["verified"] is True
    assert receipts["odd-lane"]["verified"] is False and "unknown_role:no-such-role" in receipts["odd-lane"]["errors"]
    assert receipts["odd-lane"]["source_digest"] is None
    assert receipts["empty-lane"]["verified"] is False and receipts["empty-lane"]["errors"] == ["no_role_assigned"]
    append(contracts, "roles/rco-security.v1.md", "Use claude-opus-5-5.")    # a broken role file
    broken = role_receipts(lint(contracts), {"claude-rco-1": ["rco-security"], "codex-tools-1": ["tools-tests"]}, NOW)
    assert [(r["worker"], r["verified"]) for r in broken] == [("claude-rco-1", False), ("codex-tools-1", True)]


def test_receipt_time_and_worker_are_exact(contracts):
    report = lint(contracts)
    helsinki = timezone(timedelta(hours=3))
    [receipt] = role_receipts(report, {"fable-5": ["fable-producer"]}, datetime(2026, 9, 30, 21, 0, tzinfo=helsinki))
    assert receipt["observed_utc"] == "2026-09-30T18:00:00.000000Z"
    with pytest.raises(ValueError, match="aware"):
        role_receipts(report, {"fable-5": ["fable-producer"]}, datetime(2026, 9, 30, 18, 0))
    with pytest.raises(ValueError, match="worker"):
        role_receipts(report, {"Bad Worker": ["lead-impl"]}, NOW)


def test_cli_exit_codes_and_json(contracts, tmp_path, capsys):
    assert main(["--contracts-dir", str(contracts), "--assign", "claude-rco-1=rco-security"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True and out["receipts"][0]["worker"] == "claude-rco-1"
    append(contracts, COMMON, "Keep one CronCreate job.")
    assert main(["--contracts-dir", str(contracts)]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert main(["--contracts-dir", str(tmp_path / "absent")]) == 2
    assert json.loads(capsys.readouterr().out)["error"] == "FileNotFoundError"


def test_the_linter_never_writes(contracts):
    before = snapshot(contracts)
    append_target = contracts.joinpath("roles", "rco-security.v1.md")
    lint(contracts)
    main(["--contracts-dir", str(contracts), "--assign", "claude-rco-1=rco-security"])
    assert snapshot(contracts) == before and append_target.exists()
