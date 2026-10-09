# SPDX-License-Identifier: BUSL-1.1
"""Tests for tools/bridge_regional_review_aggregate.py (inert I1/I2 diagnostic).

The fixture repos are built with git plumbing only (hash-object, update-index
--cacheinfo, write-tree, commit-tree) so symlinks, gitlinks, mode-only changes
and non-ASCII paths are exact on every platform. Offline and deterministic.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "tools" / "bridge_regional_review_aggregate.py"

sys.path.insert(0, str(ROOT))

from tools import bridge_regional_review_aggregate as agg  # noqa: E402
from tools.check_rco_pass_present import (  # noqa: E402
    DEFAULT_RCO_AGENTS,
    check_rco_pass_present,
)

GIT_ENV = {
    "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
    "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
    "GIT_AUTHOR_DATE": "2026-10-09T00:00:00Z", "GIT_COMMITTER_DATE": "2026-10-09T00:00:00Z",
}
AGENT_UUIDS = {
    "claude-rco-1": "2b2f6ff9-06c2-4ec8-b526-f10071ce7103",
    "claude-rco-2": "76739997-0058-41a2-8514-78ff295537aa",
}
GATE_MODULES = (
    "idle_consensus_auto_merge.py", "merge_with_bridge_receipt.py",
    "write_bridge_consensus_merge_receipt.py", "check_rco_pass_present.py",
    "check_bridge_changes_requested.py", "verify_bridge_consensus.py",
    "check_proven_safe_autosign_class.py",
)


def _git(repo: Path, *args: str, data: bytes | None = None, index: Path | None = None) -> str:
    env = {**os.environ, **GIT_ENV}
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    result = subprocess.run(["git", "-c", "core.autocrlf=false", "-C", str(repo), *args],
                            input=data, capture_output=True, check=True, env=env)
    return result.stdout.decode("utf-8").strip()


def _commit(repo: Path, files: dict[str, tuple[str, bytes | str]], parent: str | None, name: str) -> str:
    """files: path -> (mode, content bytes, or a commit sha for a 160000 gitlink)."""
    index = repo / ".git" / f"fixture-{name}.index"
    for path, (mode, content) in files.items():
        sha = content if mode == agg.GITLINK else _git(repo, "hash-object", "-w", "--stdin", data=content)
        _git(repo, "update-index", "--add", "--cacheinfo", f"{mode},{sha},{path}", index=index)
    tree = _git(repo, "write-tree", index=index)
    return _git(repo, "commit-tree", tree, "-m", name, *(["-p", parent] if parent else []))


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str | Path]:
    root = tmp_path_factory.mktemp("regional")
    _git(root, "init", "-q")
    base_files = {
        ".gitattributes": ("100644", b"policy.md binary\n"),
        "a.py": ("100644", b"x = 1\ny = 2\n"),
        "del.txt": ("100644", b"gone\n"),
        "old_name.txt": ("100644", b"moved content\n"),
        "mode.sh": ("100644", b"echo hi\n"),
        "policy.md": ("100644", b"# policy\n"),
    }
    base = _commit(root, base_files, None, "base")
    head_files = {
        ".gitattributes": base_files[".gitattributes"],
        "a.py": ("100644", b"x = 1\ny = 3\nz = 4\n"),
        "new_name.txt": ("100644", b"moved content\n"),
        "mode.sh": ("100755", b"echo hi\n"),
        "policy.md": ("100644", "# policy\nrule ä\n".encode("utf-8")),
        "B.txt": ("100644", b"upper\n"),
        "ä.txt": ("100644", b"umlaut path\n"),
        "zero.bin": ("100644", b"a\0b\n"),
        "latin.txt": ("100644", b"caf\xe9\n"),
        "link": (agg.SYMLINK, b"a.py"),
        "sub": (agg.GITLINK, base),
    }
    head = _commit(root, head_files, base, "head")
    clean_head = _commit(root, {**base_files, "a.py": ("100644", b"x = 1\ny = 5\n"),
                                "added.txt": ("100644", b"one\ntwo\n"), "mode.sh": ("100755", b"echo hi\n")},
                         base, "clean")
    # Same tree and blobs as clean_head, but a different commit: review must not carry forward.
    same_tree_head = _git(root, "commit-tree", _git(root, "rev-parse", clean_head + "^{tree}"),
                          "-p", base, "-m", "clean again")
    return {"root": root, "base": base, "head": head, "clean_head": clean_head, "same_tree_head": same_tree_head}


@pytest.fixture(scope="module")
def inventory(repo: dict) -> dict:
    return agg.build_inventory(repo["root"], repo["base"], repo["head"])


@pytest.fixture(scope="module")
def clean(repo: dict) -> dict:
    return agg.build_inventory(repo["root"], repo["base"], repo["clean_head"])


def _entry(inv: dict, path: str) -> dict:
    return next(e for e in inv["entries"] if e["path"] == path)


def _region(inv: dict, reviewer: str = "claude-rco-1", **overrides) -> dict:
    record = {"kind": agg.RCO_REGION, "reviewer": reviewer, "base": inv["base"], "head": inv["head"],
              "tree": inv["tree"], "independence_attested": True, "eligibility_basis": ["fixture positive evidence"],
              "lines": [{"path": e["path"], **line} for e in inv["entries"] for line in e["changed_lines"]]}
    record.update(overrides)
    return record


def _interaction(inv: dict, reviewer: str = "claude-rco-2", **overrides) -> dict:
    record = {"kind": agg.RCO_INTERACTION, "reviewer": reviewer, "base": inv["base"], "head": inv["head"],
              "tree": inv["tree"], "independence_attested": True, "eligibility_basis": ["fixture positive evidence"],
              "group_identity": inv["group_identity"]}
    record.update(overrides)
    return record


def _manifest(inv: dict) -> dict:
    return {"group_identity": inv["group_identity"],
            "entries": [[e["path"], e["status"], e["old_mode"], e["new_mode"], e["base_blob"], e["head_blob"]]
                        for e in inv["entries"]]}


def _assert_inert(result: dict) -> None:
    assert result["authority_effect"] == "none"
    assert result["allowed_to_merge"] is False
    assert result["approval_granted"] is False
    assert result["rco_pass"] is False
    assert result["origin_policy"] == "deny"
    assert result["mode"] == "diagnostic"


# --- I2 / D1 / D2 inventory ---------------------------------------------------


def test_inventory_is_every_changed_path_in_bytewise_order_with_full_blobs(inventory: dict) -> None:
    paths = [e["path"] for e in inventory["entries"]]
    expected = sorted(["a.py", "del.txt", "old_name.txt", "new_name.txt", "mode.sh", "policy.md", "B.txt",
                       "ä.txt", "zero.bin", "latin.txt", "link", "sub"], key=lambda p: p.encode("utf-8"))
    assert paths == expected
    assert paths[0] == "B.txt" and paths[-1] == "ä.txt"   # bytewise, not casefold/culture order
    for entry in inventory["entries"]:
        assert agg.SHA1_RE.fullmatch(entry["base_blob"]) and agg.SHA1_RE.fullmatch(entry["head_blob"])
    assert agg.SHA1_RE.fullmatch(inventory["tree"])


def test_rename_endpoints_and_metadata_only_change_are_retained(inventory: dict) -> None:
    assert _entry(inventory, "old_name.txt")["status"] == "D"
    assert _entry(inventory, "new_name.txt")["status"] == "A"
    mode = _entry(inventory, "mode.sh")
    assert (mode["old_mode"], mode["new_mode"], mode["status"]) == ("100644", "100755", "M")
    assert mode["metadata_only"] is True and mode["changed_lines"] == []


def test_changed_lines_carry_side_line_and_content_hash(inventory: dict) -> None:
    lines = _entry(inventory, "a.py")["changed_lines"]
    assert [(line["side"], line["line"]) for line in lines] == [("base", 2), ("head", 2), ("head", 3)]
    assert lines[1]["sha256"] == agg._sha256(b"y = 3")


def test_d1_attribute_binary_utf8_file_is_inventoried_and_assessable(inventory: dict) -> None:
    policy = _entry(inventory, "policy.md")   # .gitattributes says binary; content is UTF-8 text
    assert policy["unsupported_reason"] is None
    assert policy["changed_lines"] == [{"side": "head", "line": 2,
                                        "sha256": agg._sha256("rule ä".encode("utf-8"))}]


def test_d1_nul_non_utf8_symlink_and_gitlink_are_unsupported(inventory: dict) -> None:
    reasons = {e["path"]: e["unsupported_reason"] for e in inventory["entries"] if e["unsupported_reason"]}
    assert reasons == {"zero.bin": "nul_byte_in_head_blob", "latin.txt": "invalid_utf8_in_head_blob",
                       "link": "symlink", "sub": "gitlink"}


def test_unsupported_change_stays_uncovered_even_with_full_line_coverage(inventory: dict) -> None:
    evidence = {"region_records": [_region(inventory)], "interaction_records": [_interaction(inventory)]}
    result = agg.assess(inventory, evidence)
    assert result["uncovered_lines"] == 0
    assert result["content_complete"] is False
    assert result["interaction"]["complete"] is False
    assert result["diagnostic_complete"] is False
    assert {u["path"] for u in result["unsupported_paths"]} == {"zero.bin", "latin.txt", "link", "sub"}
    _assert_inert(result)


def test_d2_group_identity_changes_with_order_or_abbreviated_blobs(inventory: dict) -> None:
    entries = inventory["entries"]
    args = (inventory["base"], inventory["head"], inventory["tree"])
    assert agg.group_identity(*args, entries) == inventory["group_identity"]
    assert agg.group_identity(*args, list(reversed(entries))) != inventory["group_identity"]
    short = [{**e, "head_blob": e["head_blob"][:12]} for e in entries]
    assert agg.group_identity(*args, short) != inventory["group_identity"]


# --- I2 interaction group -------------------------------------------------------


def _complete_clean_evidence(clean: dict) -> dict:
    return {"region_records": [_region(clean)], "interaction_records": [_interaction(clean)],
            "group_manifest": _manifest(clean)}


def test_clean_pair_with_full_evidence_completes_but_stays_inert(clean: dict) -> None:
    result = agg.assess(clean, _complete_clean_evidence(clean))
    assert result["content_complete"] is True and result["interaction"]["complete"] is True
    assert result["diagnostic_complete"] is True
    _assert_inert(result)


@pytest.mark.parametrize("mutate", ["omit", "add", "reorder", "abbreviate", "identity"])
def test_supplied_manifest_that_differs_from_git_refuses_interaction(clean: dict, mutate: str) -> None:
    manifest = _manifest(clean)
    entries = manifest["entries"]
    if mutate == "omit":
        entries.pop()
    elif mutate == "add":
        entries.append(["extra.txt", "A", "000000", "100644", agg.NULL_BLOB, "1" * 40])
    elif mutate == "reorder":
        entries.reverse()
    elif mutate == "abbreviate":
        entries[0][5] = entries[0][5][:7]
    else:
        manifest["group_identity"] = "0" * 64
    evidence = {**_complete_clean_evidence(clean), "group_manifest": manifest}
    result = agg.assess(clean, evidence)
    assert result["interaction"]["complete"] is False
    assert result["diagnostic_complete"] is False
    assert "supplied group manifest differs" in result["interaction"]["reasons"][0]


def test_manifest_omitting_a_path_cannot_create_coverage(clean: dict) -> None:
    manifest = _manifest(clean)
    omitted = manifest["entries"].pop(0)[0]
    region = _region(clean, lines=[line for line in _region(clean)["lines"] if line["path"] != omitted])
    result = agg.assess(clean, {"region_records": [region], "interaction_records": [_interaction(clean)],
                                "group_manifest": manifest})
    assert result["uncovered_lines"] > 0
    assert result["content_complete"] is False and result["diagnostic_complete"] is False


@pytest.mark.parametrize("variant", ["removed", "caller_split", "missing_identity"])
def test_whole_diff_group_removed_or_caller_split_refuses(clean: dict, variant: str) -> None:
    evidence = _complete_clean_evidence(clean)
    if variant == "removed":
        evidence["interaction_records"] = []
    elif variant == "caller_split":
        part = clean["entries"][:1]
        split_id = agg.group_identity(clean["base"], clean["head"], clean["tree"], part)
        evidence["interaction_records"] = [_interaction(clean, group_identity=split_id)]
    else:
        record = _interaction(clean)
        record.pop("group_identity")
        evidence["interaction_records"] = [record]
    result = agg.assess(clean, evidence)
    assert result["content_complete"] is True
    assert result["interaction"]["complete"] is False
    assert result["diagnostic_complete"] is False


def test_all_regional_content_without_interaction_evidence_is_incomplete(clean: dict) -> None:
    result = agg.assess(clean, {"region_records": [_region(clean)]})
    assert result["content_complete"] is True
    assert result["interaction"]["complete"] is False
    assert result["interaction"]["reasons"] == ["no whole-group interaction review evidence"]
    assert result["diagnostic_complete"] is False


def test_unknown_membership_kind_and_malformed_records_cover_nothing(clean: dict) -> None:
    evidence = {"region_records": [{"kind": "region"}, "text", _region(clean, kind="rco_regionx")],
                "interaction_records": [{"kind": "interaction", "group_identity": clean["group_identity"]}]}
    result = agg.assess(clean, evidence)
    assert result["covered_lines"] == 0 and result["diagnostic_complete"] is False
    assert len(result["ignored_records"]) == 3
    assert agg.assess(clean, {"region_records": {"not": "a list"}})["covered_lines"] == 0


# --- record eligibility (positive evidence only) --------------------------------


@pytest.mark.parametrize("overrides", [
    {"reviewer": "fable-5"}, {"reviewer": "codex-lead-1"}, {"reviewer": "grok-scout-1"},
    {"reviewer": "Claude-RCO-1"}, {"head": "f" * 40}, {"base": "f" * 40}, {"tree": "f" * 40},
    {"independence_attested": "unknown"}, {"independence_attested": None}, {"eligibility_basis": []},
    {"eligibility_basis": ""}, {"eligibility_basis": [""]}, {"participation_disclosed": ["design"]},
    {"participation_disclosed": "author"}, {"participation_disclosed": None},
],ids=lambda o: "-".join(f"{k}={v}" for k, v in o.items()))
def test_region_record_without_positive_binding_covers_nothing(clean: dict, overrides: dict) -> None:
    result = agg.assess(clean, {"region_records": [_region(clean, **overrides)]})
    assert result["covered_lines"] == 0
    assert result["ignored_records"][0]["index"] == 0


def test_line_hash_mismatch_or_unknown_line_is_not_counted(clean: dict) -> None:
    lines = _region(clean)["lines"]
    forged = [{**lines[0], "sha256": "0" * 64}, {"path": "a.py", "side": "head", "line": 99, "sha256": "0" * 64},
              {"path": "nope.txt", "side": "head", "line": 1, "sha256": lines[0]["sha256"]},
              {**lines[0], "path": [lines[0]["path"]]}, {**lines[0], "line": True}, {**lines[0], "line": "1"},
              {**lines[0], "side": None}, "a.py:2"]
    result = agg.assess(clean, {"region_records": [_region(clean, lines=forged)]})
    assert result["covered_lines"] == 0


def test_record_bound_to_another_head_is_not_carried_forward(inventory: dict, clean: dict) -> None:
    stale = _region(inventory)   # bound to the other head of the same base
    result = agg.assess(clean, {"region_records": [stale], "interaction_records": [_interaction(inventory)]})
    assert result["covered_lines"] == 0 and result["interaction"]["complete"] is False


def test_same_tree_new_head_never_inherits_earlier_head_review(repo: dict, clean: dict) -> None:
    moved = agg.build_inventory(repo["root"], repo["base"], repo["same_tree_head"])
    assert moved["tree"] == clean["tree"] and moved["head"] != clean["head"]
    assert [e["head_blob"] for e in moved["entries"]] == [e["head_blob"] for e in clean["entries"]]
    result = agg.assess(moved, _complete_clean_evidence(clean))
    assert result["covered_lines"] == 0 and result["diagnostic_complete"] is False


def test_fresh_recheck_is_refusal_only(repo: dict, clean: dict) -> None:
    result = agg.assess(clean, _complete_clean_evidence(clean))
    same = agg.fresh_recheck(repo["root"], result, repo["clean_head"])
    assert same["still_current"] is True
    _assert_inert(same)
    with pytest.raises(agg.DiagnosticRefused, match="head moved"):
        agg.fresh_recheck(repo["root"], result, repo["same_tree_head"])
    with pytest.raises(agg.DiagnosticRefused):
        agg.fresh_recheck(repo["root"], {**result, "group_identity": "0" * 64}, repo["clean_head"])
    with pytest.raises(agg.DiagnosticRefused):
        agg.fresh_recheck(repo["root"], {}, repo["clean_head"])


def test_recognized_rcos_match_the_merge_gate_set() -> None:
    assert agg.RECOGNIZED_RCOS == tuple(DEFAULT_RCO_AGENTS)


# --- I1 inertness ----------------------------------------------------------------


def test_complete_dual_rco_coverage_still_cannot_satisfy_any_route(clean: dict) -> None:
    evidence = {"region_records": [_region(clean), _region(clean, "claude-rco-2")],
                "interaction_records": [_interaction(clean, "claude-rco-1"), _interaction(clean)],
                "group_manifest": _manifest(clean), "allowed_to_merge": True, "approval_granted": True,
                "authority_effect": "merge", "rco_pass": True, "origin_policy": "allow", "mode": "active"}
    result = agg.assess(clean, evidence)
    assert result["diagnostic_complete"] is True
    _assert_inert(result)


def test_complete_dual_recused_grok_coverage_is_still_inert_and_uncounted(clean: dict) -> None:
    grok = _region(clean, kind=agg.GROK_REGION, reviewer="grok-scout-1", dual_rco_recused=True)
    result = agg.assess(clean, {"region_records": [grok, {**grok}], "group_manifest": _manifest(clean)})
    assert result["covered_lines"] == 0
    assert result["content_complete"] is False and result["diagnostic_complete"] is False
    assert all("origin_policy=deny" in item["reason"] for item in result["ignored_records"])
    _assert_inert(result)


@pytest.mark.parametrize("kwargs", [{"origin_policy": "allow"}, {"origin_policy": "grok"},
                                    {"origin_policy": "DENY"}, {"origin_policy": ""},
                                    {"mode": "active"}, {"mode": "enforce"}, {"mode": "Diagnostic"}])
def test_every_active_policy_attempt_refuses_in_the_library(clean: dict, kwargs: dict) -> None:
    with pytest.raises(agg.DiagnosticRefused):
        agg.assess(clean, {}, **kwargs)


@pytest.mark.parametrize("extra", [["--origin-policy", "allow"], ["--mode", "active"],
                                   ["--origin-policy", "grok", "--mode", "enforce"]])
def test_every_active_policy_attempt_refuses_in_the_cli(repo: dict, extra: list[str], capsys) -> None:
    code = agg.main(["--repo", str(repo["root"]), "--base", repo["base"], "--head", repo["clean_head"], *extra])
    out = json.loads(capsys.readouterr().out)
    assert code == 2 and out["refused"] is True
    _assert_inert(out)


def test_module_reads_no_environment_or_config_and_offers_no_active_switch() -> None:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    imported = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                for alias in node.names}
    imported |= {node.module.split(".")[0] for node in ast.walk(tree)
                 if isinstance(node, ast.ImportFrom) and node.module}
    assert imported <= {"__future__", "argparse", "hashlib", "json", "re", "subprocess", "sys", "pathlib", "typing"}
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "environ" not in names and "getenv" not in MODULE.read_text(encoding="utf-8")
    assert agg.ORIGIN_POLICIES == frozenset({"deny"}) and agg.MODES == frozenset({"diagnostic"})


def test_cli_diagnostic_is_inert_and_exit_zero(repo: dict, clean: dict, tmp_path: Path, capsys) -> None:
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps(_complete_clean_evidence(clean)), encoding="utf-8")
    code = agg.main(["--repo", str(repo["root"]), "--base", repo["base"], "--head", repo["clean_head"],
                     "--evidence", str(evidence)])
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["diagnostic_complete"] is True
    _assert_inert(out)


def test_existing_authority_gates_do_not_reference_the_diagnostic() -> None:
    for name in GATE_MODULES:
        path = ROOT / "tools" / name
        if path.exists():
            assert "bridge_regional_review_aggregate" not in path.read_text(encoding="utf-8"), name
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert not any(module.startswith(("tools", "waggledance")) for module in modules)


def test_diagnostic_cannot_substitute_for_an_rco_pass_or_merge_receipt(clean: dict) -> None:
    result = agg.assess(clean, _complete_clean_evidence(clean))
    head = clean["head"]
    task = "fable-5/regional-review-diagnostic-fixture"
    genuine = {"ts_utc": "2026-10-09T00:00:00Z", "agent": "claude-rco-1", "agent_uuid": AGENT_UUIDS["claude-rco-1"],
               "type": "decision", "status": "rco_pass", "task_id": task, "message": "", "payload": {"exact_head": head}}
    disguised = [{**genuine, "type": "message", "status": "diagnostic", "payload": {**result, "exact_head": head}},
                 {**result, "agent": "claude-rco-1", "agent_uuid": AGENT_UUIDS["claude-rco-1"], "task_id": task}]

    def gate(events: list[dict]) -> bool:
        return check_rco_pass_present(events=events, task_id=task, head=head, author_agent="fable-5",
                                      identity_registry=AGENT_UUIDS)["ok"]

    assert gate([genuine]) is True   # positive control: the gate call itself can pass
    assert gate(disguised) is False
    veto = {**genuine, "agent": "claude-rco-2", "agent_uuid": AGENT_UUIDS["claude-rco-2"],
            "type": "finding", "status": "changes_requested"}
    assert gate([genuine, *disguised, veto]) is False   # a genuine veto still blocks despite full coverage
    receipt_keys = {"artifact_version", "bridge_consensus", "rco_pass_gate", "gate_decision", "head_sha",
                    "receipt_manifest_planned", "merge_command"}
    assert not receipt_keys & set(result)
    assert result["schema"] == agg.SCHEMA and not result["schema"].startswith("wd.bridge_consensus")


@pytest.mark.parametrize("evidence", [[], "", False, 0], ids=["list", "str", "false", "zero"])
def test_falsy_non_object_evidence_refuses_instead_of_defaulting(clean: dict, evidence: object) -> None:
    with pytest.raises(agg.DiagnosticRefused, match="JSON object"):
        agg.assess(clean, evidence)


@pytest.mark.parametrize("evidence", [None, {}], ids=["none", "empty"])
def test_none_or_empty_object_evidence_is_an_empty_diagnostic(clean: dict, evidence: object) -> None:
    result = agg.assess(clean, evidence)
    assert result["covered_lines"] == 0 and result["diagnostic_complete"] is False
    _assert_inert(result)


@pytest.mark.parametrize("evidence", [b'{"region_records": ["\xff"]}', b"[" * 100000],
                         ids=["invalid_utf8", "deep_nesting"])
def test_unreadable_evidence_file_is_an_inert_cli_refusal(repo: dict, tmp_path: Path, capsys,
                                                          evidence: bytes) -> None:
    path = tmp_path / "evidence.json"
    path.write_bytes(evidence)
    code = agg.main(["--repo", str(repo["root"]), "--base", repo["base"], "--head", repo["clean_head"],
                     "--evidence", str(path)])
    out = json.loads(capsys.readouterr().out)
    assert code == 2 and out["refused"] is True
    _assert_inert(out)


def _fake_git(raw: bytes):
    def fake(repo: Path, *args: str) -> bytes:
        if args[0] == "rev-parse":
            return ("1" * 40 + "\n").encode()
        if args[0] == "diff":
            return raw
        raise AssertionError(args)
    return fake


@pytest.mark.parametrize("raw", [
    b":000000 100644 " + b"0" * 40 + b" " + b"2" * 40 + b" A\x00bad-\xff-path.txt\x00",
    b":000000 100644 " + b"0" * 40 + b" A\x00short-record.txt\x00",
], ids=["non_utf8_path", "malformed_record"])
def test_undecodable_git_output_is_a_refusal_in_library_and_cli(monkeypatch, capsys, raw: bytes) -> None:
    monkeypatch.setattr(agg, "_git", _fake_git(raw))
    with pytest.raises(agg.DiagnosticRefused):
        agg.build_inventory(Path("."), "1" * 40, "2" * 40)
    code = agg.main(["--repo", ".", "--base", "1" * 40, "--head", "2" * 40])
    out = json.loads(capsys.readouterr().out)
    assert code == 2 and out["refused"] is True
    _assert_inert(out)


def test_invalid_refs_refuse_without_a_result(repo: dict) -> None:
    with pytest.raises(agg.DiagnosticRefused):
        agg.build_inventory(repo["root"], repo["base"], "f" * 40)
