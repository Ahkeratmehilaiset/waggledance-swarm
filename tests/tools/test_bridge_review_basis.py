"""Read-only, real-Git contract checks for the review-basis producer."""
from __future__ import annotations
import copy
import importlib
import importlib.util
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "_basis_contract_support", ROOT / "tests/tools/test_bridge_approval_carry_forward.py")
support = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(support)
pinned_fleet = support.pinned_fleet
live_factory = support.live_factory

def api():
    module = importlib.import_module("tools.bridge_review_basis")
    assert Path(module.__file__).resolve() == ROOT / "tools/bridge_review_basis.py"
    return module

def kwargs(c):
    return dict(repo_root=c["root"], repo=c["repo"], pr_number=c["pr"],
                task_id=c["task"], base_ref="main", base_sha=c["pair"]["old_base"],
                head=c["pair"]["old_head"], runner=c["runner"])

def produce(c):
    old_new = c["pair"]["new_head"]
    c["pair"]["new_head"] = c["pair"]["old_head"]
    try:
        return api().produce_review_basis(**kwargs(c))
    finally:
        c["pair"]["new_head"] = old_new

def test_real_basis_is_consumed_by_current_live_adapter(live_factory):
    c = live_factory
    payload = produce(c)
    assert payload["review_basis"] == c["basis"]
    assert payload["review_basis_sha256"] == support._event_hash(c["basis"])
    for event in c["events"][1:]:
        event["payload"].update(copy.deepcopy(payload))
    result = support._run_live_factory(c)
    assert result["carry"] is True, result
    assert c["git"]("status", "--porcelain") == b""
    assert all("github.com/" + c["repo"] in call if call[:2] == ["gh", "pr"]
               else "--hostname" in call and "github.com" in call for call in c["calls"])

@pytest.mark.parametrize("field,value", [
    ("head_sha", "1" * 40), ("base_sha", "1" * 40), ("base_tip_sha", "1" * 40),
    ("base_ref", "other"), ("pr_number", 9002),
    ("url", "https://github.com/other/repo/pull/9001"), ("state", "CLOSED"),
])
def test_snapshot_identity_faults_refuse(live_factory, monkeypatch, field, value):
    module = api()
    original = module.status_snapshot.build_pr_status_snapshot
    def altered(**values):
        result = original(**values)
        result[field] = value
        return result
    monkeypatch.setattr(module.status_snapshot, "build_pr_status_snapshot", altered)
    with pytest.raises(ValueError):
        produce(live_factory)

@pytest.mark.parametrize("field,value", [
    ("head", "HEAD"), ("base_sha", "x" * 40), ("pr_number", True),
    ("repo", "other/repo/extra"), ("task_id", ""), ("base_ref", ""),
])
def test_malformed_inputs_refuse_before_read(live_factory, monkeypatch, field, value):
    module = api()
    monkeypatch.setattr(module.status_snapshot, "build_pr_status_snapshot",
                        lambda **values: pytest.fail("GitHub read before input validation"))
    values = kwargs(live_factory)
    values[field] = value
    with pytest.raises(ValueError):
        module.produce_review_basis(**values)

def test_snapshot_drift_during_git_read_refuses(live_factory, monkeypatch):
    module = api()
    original = module.status_snapshot.build_pr_status_snapshot
    calls = []
    def drift(**values):
        result = original(**values)
        calls.append(True)
        if len(calls) > 1:
            result["head_sha"] = "f" * 40
        return result
    monkeypatch.setattr(module.status_snapshot, "build_pr_status_snapshot", drift)
    with pytest.raises(ValueError):
        produce(live_factory)
    assert len(calls) == 2

@pytest.mark.parametrize("reason", ["ambiguous merge-base", "Git executable drift", "missing object"])
def test_git_identity_faults_propagate(live_factory, monkeypatch, reason):
    module = api()
    def refuse(**values):
        raise ValueError(reason)
    monkeypatch.setattr(module.carry, "derive_content_identity", refuse)
    with pytest.raises(ValueError, match=reason):
        produce(live_factory)

def test_wrong_git_origin_refuses(live_factory):
    live_factory["git"]("remote", "set-url", "origin", "https://github.com/other/repo.git")
    with pytest.raises(ValueError):
        produce(live_factory)

def test_basis_has_no_vote_or_authority_and_preserves_pinned_flags(live_factory, monkeypatch):
    module = api()
    proof = module.carry.derive_content_identity(
        repo_root=live_factory["root"], old_base=live_factory["pair"]["old_base"],
        old_head=live_factory["pair"]["old_head"], new_base=live_factory["pair"]["old_base"],
        new_head=live_factory["pair"]["old_head"])
    monkeypatch.setattr(Path, "write_text", lambda *a, **k: pytest.fail("producer wrote a file"))
    payload = produce(live_factory)
    assert set(payload) == {"head", "pr", "review_basis", "review_basis_sha256"}
    assert payload["review_basis"]["diff_sha256"] == proof["old_diff_sha256"]
    assert payload["review_basis"]["patch_id"] == proof["old_patch_id"]
    assert payload["review_basis"]["merge_base"] == proof["old_merge_base"]
    assert payload["head"] == live_factory["pair"]["old_head"]

def test_cli_refusal_is_json_without_a_basis(monkeypatch, capsys):
    module = api()
    def refuse(**values):
        raise ValueError("snapshot drift")
    monkeypatch.setattr(module, "produce_review_basis", refuse)
    code = module.main(["--repo-root", str(ROOT), "--repo", "example/carry", "--pr", "9001",
                        "--task-id", "example/task", "--base-ref", "main",
                        "--base-sha", "a" * 40, "--head", "b" * 40])
    assert code != 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "HOLD"
    assert "review_basis" not in result

