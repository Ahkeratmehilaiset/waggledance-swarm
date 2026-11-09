"""Real Git content identity; no live bridge, merge, or installed runtime."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest


def _api():
    # Deliberately lazy: each retained RED names an absent behavior, rather than
    # turning the entire file into a collection error before the implementation.
    from tools import bridge_approval_carry_forward
    return bridge_approval_carry_forward


@pytest.fixture(autouse=True)
def pinned_fleet(tmp_path, monkeypatch):
    """Synthetic launcher authority; never reads or changes installed pins."""
    root = tmp_path / ".codex-audit" / ("d" * 40)
    root.mkdir(parents=True)
    executable = Path(r"C:\Program Files\Git\cmd\git.exe") if os.name == "nt" else Path("/usr/bin/git")
    fleet = json.dumps({"schema_version": 2, "git_executable": str(executable)}).encode()
    (root / "wd-fleet.json").write_bytes(fleet)
    manifest = json.dumps({"source_commit": root.name, "files": {
        "wd-fleet.json": hashlib.sha256(fleet).hexdigest()}}).encode()
    (root / "deployment-manifest.json").write_bytes(manifest)
    monkeypatch.setenv("WD_BRIDGE_BUNDLE_ROOT", str(root))
    monkeypatch.setenv("WD_REBOOT_EXPECTED_MANIFEST_HASH", hashlib.sha256(manifest).hexdigest())
    return root, executable


def _build_history(tmp_path, filename="sample.txt"):
    root = tmp_path / "git-fixture"
    root.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_AUTHOR_NAME="Synthetic", GIT_AUTHOR_EMAIL="test@example.invalid",
               GIT_COMMITTER_NAME="Synthetic", GIT_COMMITTER_EMAIL="test@example.invalid")
    def git(*args, data=None):
        return subprocess.check_output(["git", *args], cwd=root, env=env, input=data,
                                       stderr=subprocess.PIPE)
    git("init", "--quiet")
    git("config", "core.autocrlf", "false")
    target = root / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"before\n")
    git("add", filename)
    git("commit", "--quiet", "-m", "base")
    base = git("rev-parse", "HEAD").decode().strip()
    target.write_bytes(b"alpha gamma\n")
    git("add", filename)
    git("commit", "--quiet", "-m", "old reviewed content")
    old = git("rev-parse", "HEAD").decode().strip()
    tree = git("rev-parse", "HEAD^{tree}").decode().strip()
    new = git("commit-tree", tree, "-p", base, data=b"new message only\n").decode().strip()
    return root, git, dict(old_base=base, old_head=old, new_base=base, new_head=new)


@pytest.fixture
def history(tmp_path):
    return _build_history(tmp_path)


def test_real_message_only_change_has_rederivable_identity(history):
    root, git, pair = history
    assert pair["old_head"] != pair["new_head"]
    proof = _api().derive_content_identity(repo_root=root, **pair)
    assert proof["schema"] == "wd.bridge-content-identity.v1"
    assert proof["old_merge_base"] == proof["new_merge_base"] == pair["old_base"]
    assert proof["old_diff_sha256"] == proof["new_diff_sha256"]
    assert proof["old_patch_id"] == proof["new_patch_id"]
    assert proof["old_entries"] == proof["new_entries"]
    assert _api().verify_content_identity(proof=proof, repo_root=root, **pair) == proof


def test_real_rebase_onto_advanced_base_preserves_identity(history):
    root, git, pair = history
    git("checkout", "--quiet", pair["old_base"])
    (root / "unrelated.txt").write_bytes(b"new main content\n")
    git("add", "unrelated.txt")
    git("commit", "--quiet", "-m", "advance base")
    pair["new_base"] = git("rev-parse", "HEAD").decode().strip()
    (root / "sample.txt").write_bytes(b"alpha gamma\n")
    git("add", "sample.txt")
    git("commit", "--quiet", "-m", "same patch rebased")
    pair["new_head"] = git("rev-parse", "HEAD").decode().strip()
    proof = _api().derive_content_identity(repo_root=root, **pair)
    assert proof["old_merge_base"] == pair["old_base"]
    assert proof["new_merge_base"] == pair["new_base"]
    assert proof["old_entries"] == proof["new_entries"]


def test_actual_whitespace_patch_id_collision_refuses(history):
    root, git, pair = history
    git("checkout", "--quiet", pair["old_base"])
    (root / "sample.txt").write_bytes(b"alpha    gamma\n")
    git("add", "sample.txt")
    git("commit", "--quiet", "-m", "content differs despite same patch id")
    pair["new_head"] = git("rev-parse", "HEAD").decode().strip()
    before = git("diff", "--no-ext-diff", "--no-textconv", pair["old_base"], pair["old_head"])
    after = git("diff", "--no-ext-diff", "--no-textconv", pair["new_base"], pair["new_head"])
    assert hashlib.sha256(before).digest() != hashlib.sha256(after).digest()
    assert git("patch-id", "--stable", data=before).split()[0] == git("patch-id", "--stable", data=after).split()[0]
    with pytest.raises(ValueError, match="content|diff|identity"):
        _api().derive_content_identity(repo_root=root, **pair)


@pytest.mark.parametrize("field", ["old_head", "new_head", "old_base", "new_base"])
def test_missing_git_object_refuses_instead_of_accepting_caller_claim(history, field):
    root, _, pair = history
    pair[field] = "f" * 40
    with pytest.raises(ValueError):
        _api().derive_content_identity(repo_root=root, **pair)


@pytest.mark.parametrize("field", ["old_diff_sha256", "new_patch_id", "old_merge_base", "new_entries"])
def test_rederivation_rejects_resealed_or_mutated_evidence(history, field):
    root, _, pair = history
    proof = _api().derive_content_identity(repo_root=root, **pair)
    proof = copy.deepcopy(proof)
    proof[field] = [] if field == "new_entries" else "0" * 64
    with pytest.raises(ValueError):
        _api().verify_content_identity(proof=proof, repo_root=root, **pair)


def test_empty_diff_has_no_approval_carry_identity(history):
    root, _, pair = history
    pair["old_head"] = pair["new_head"] = pair["old_base"]
    with pytest.raises(ValueError, match="empty|patch|identity"):
        _api().derive_content_identity(repo_root=root, **pair)


def test_config_and_diff_driver_cannot_replace_git_content(history):
    root, git, pair = history
    git("config", "diff.noprefix", "true")
    git("config", "diff.mnemonicPrefix", "true")
    git("config", "diff.algorithm", "histogram")
    proof = _api().derive_content_identity(repo_root=root, **pair)
    git("config", "diff.noprefix", "false")
    git("config", "diff.mnemonicPrefix", "false")
    git("config", "diff.algorithm", "myers")
    assert _api().verify_content_identity(proof=proof, repo_root=root, **pair) == proof


def test_ambiguous_criss_cross_merge_bases_refuse(history):
    root, git, pair = history
    tree = git("rev-parse", pair["old_base"] + "^{tree}").decode().strip()
    a = git("commit-tree", tree, "-p", pair["old_base"], data=b"A\n").decode().strip()
    b = git("commit-tree", tree, "-p", pair["old_base"], data=b"B\n").decode().strip()
    left = git("commit-tree", tree, "-p", a, "-p", b, data=b"left\n").decode().strip()
    right = git("commit-tree", tree, "-p", b, "-p", a, data=b"right\n").decode().strip()
    assert len(git("merge-base", "--all", left, right).splitlines()) == 2
    pair.update(old_base=left, old_head=right)
    with pytest.raises(ValueError, match="merge.base|ambiguous"):
        _api().derive_content_identity(repo_root=root, **pair)


def test_path_substitution_cannot_choose_identity_proof_executable(history, monkeypatch):
    root, _, pair = history
    api = _api()
    selected = []
    def malicious_path_lookup(name):
        selected.append(name)
        return str(root / "untrusted-git.exe")
    monkeypatch.setattr(shutil, "which", malicious_path_lookup)
    assert api.derive_content_identity(repo_root=root, **pair)
    assert selected == [], "production identity reader must never consult PATH"


@pytest.mark.parametrize("fault", ["missing_root", "missing_pin", "wrong_manifest", "wrong_fleet"])
def test_untrusted_launcher_authority_refuses_before_git(history, pinned_fleet, monkeypatch, fault):
    root, _, pair = history
    bundle, _ = pinned_fleet
    if fault == "missing_root":
        monkeypatch.delenv("WD_BRIDGE_BUNDLE_ROOT")
    elif fault == "missing_pin":
        monkeypatch.delenv("WD_REBOOT_EXPECTED_MANIFEST_HASH")
    elif fault == "wrong_manifest":
        monkeypatch.setenv("WD_REBOOT_EXPECTED_MANIFEST_HASH", "0" * 64)
    else:
        (bundle / "wd-fleet.json").write_bytes(b'{"git_executable":"untrusted.exe"}')
    api = _api()
    def no_spawn(*args, **kwargs):
        pytest.fail("untrusted bundle/fleet must refuse before any child process")
    monkeypatch.setattr(api.subprocess, "Popen", no_spawn)
    with pytest.raises(ValueError):
        api.derive_content_identity(repo_root=root, **pair)


def test_native_git_hash_is_measured_and_recorded(history, pinned_fleet):
    root, _, pair = history
    _, executable = pinned_fleet
    proof = _api().derive_content_identity(repo_root=root, **pair)
    assert proof["git_executable_sha256"] == hashlib.sha256(executable.read_bytes()).hexdigest()
    assert proof["git_executable_trust"] == "anchored_fleet_path_measured_sha256"


def test_executable_change_during_git_reads_refuses(history, monkeypatch):
    root, _, pair = history
    api = _api()
    measured = getattr(api, "_native_git_snapshot", lambda path: {"sha256": "1" * 64})
    count = 0
    def changed(path):
        nonlocal count
        value = dict(measured(path))
        count += 1
        if count > 1:
            value["sha256"] = "0" * 64
        return value
    monkeypatch.setattr(api, "_native_git_snapshot", changed, raising=False)
    with pytest.raises(ValueError, match="executable|Git|identity"):
        api.derive_content_identity(repo_root=root, **pair)


def test_canonical_diff_ignores_repository_order_file(history):
    root, git, pair = history
    (root / "second.txt").write_bytes(b"second changed path\n")
    git("add", "second.txt")
    git("commit", "--quiet", "-m", "two-file patch")
    pair["old_head"] = git("rev-parse", "HEAD").decode().strip()
    tree = git("rev-parse", "HEAD^{tree}").decode().strip()
    pair["new_head"] = git("commit-tree", tree, "-p", pair["old_base"], data=b"same two files\n").decode().strip()
    proof = _api().derive_content_identity(repo_root=root, **pair)
    order = root / ".git" / "order.txt"
    order.write_bytes(b"second.txt\nsample.txt\n")
    git("config", "diff.orderFile", str(order))
    assert _api().verify_content_identity(proof=proof, repo_root=root, **pair) == proof


@pytest.fixture
def approval_evidence(history):
    from waggledance.core.bridge_identity_registry import load_bridge_identity_registry
    root, _, pair = history
    roster = load_bridge_identity_registry()
    side = dict(repository="Ahkeratmehilaiset/waggledance-swarm", pr_number=9001, base_ref="main")
    task = "fable-5/carry-fixture-9001"
    events = [dict(agent=agent, agent_uuid=roster[agent], type="decision", status=status,
                   task_id=task, ts_utc="2026-11-09T01:00:00Z", message="",
                   payload=dict(head=pair["old_head"], pr=9001))
              for agent, status in (("codex-lead-1", "build_consensus"),
                                    ("codex-tools-1", "build_consensus"),
                                    ("claude-rco-1", "rco_pass"))]
    return dict(schema="wd.bridge-approval-carry-forward-evidence.v1", repo_path=str(root),
                task_id=task, pr_author="fable-5", roster=roster,
                recognized_rco_agents=["claude-rco-1", "claude-rco-2", "claude-rco-3"],
                approved=dict(side, head=pair["old_head"], base_sha=pair["old_base"]),
                proposed=dict(side, head=pair["new_head"], base_sha=pair["new_base"]),
                events=events, accepted_queue_complete=True,
                ci=dict(required=["unit", "lint"], runs=[dict(head=pair["new_head"], name=name,
                        status="completed", conclusion="success") for name in ("unit", "lint")]))


def _event_hash(event):
    return hashlib.sha256(json.dumps(event, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=True).encode("ascii")).hexdigest()


def _veto(evidence, *, agent="claude-rco-2", head="old", status="changes_requested", kind="finding"):
    return dict(agent=agent, agent_uuid=evidence["roster"][agent], type=kind, status=status,
                task_id="descriptive-old-head-finding", ts_utc="2000-01-01T00:00:00Z", message="",
                payload=dict(exact_head=evidence["approved" if head == "old" else "proposed"]["head"]))


def test_approval_carry_binds_original_refs_and_new_ci(approval_evidence):
    evidence = approval_evidence
    result = _api().evaluate_approval_carry_forward(evidence)
    assert result["carry"] is True
    assert {row["kind"] for row in result["carried"]} == {"build_consensus", "rco_pass"}
    assert {row["event_sha256"] for row in result["carried"]} == {_event_hash(e) for e in evidence["events"]}
    assert all(row["approved_head"] == evidence["approved"]["head"] for row in result["carried"])
    receipt = result["receipt"]
    assert receipt["approved_head"] == evidence["approved"]["head"]
    assert receipt["proposed_head"] == receipt["ci_head"] == evidence["proposed"]["head"]
    assert receipt["veto_scan_complete"] is True
    assert _api().verify_carry_forward_receipt(receipt, evidence) is True


@pytest.mark.parametrize("fault", ["wrong_pr", "wrong_repository", "wrong_base_ref", "missing_build", "missing_rco",
                                  "forged_uuid", "missing_uuid", "wrong_task", "wrong_roster", "author_rco",
                                  "ci_old_head", "ci_red", "ci_pending", "ci_missing", "ci_empty_required",
                                  "queue_partial", "queue_untyped", "wrong_schema"])
def test_approval_carry_refuses_unproven_authority(approval_evidence, fault):
    e = approval_evidence
    if fault in {"wrong_pr", "wrong_repository", "wrong_base_ref"}:
        key = {"wrong_pr": "pr_number", "wrong_repository": "repository", "wrong_base_ref": "base_ref"}[fault]
        e["proposed"][key] = 9002 if key == "pr_number" else "other"
    elif fault == "missing_build":
        del e["events"][1]
    elif fault == "missing_rco":
        del e["events"][2]
    elif fault == "forged_uuid":
        e["events"][2]["agent_uuid"] = "00000000-0000-4000-8000-000000000000"
    elif fault == "missing_uuid":
        del e["events"][2]["agent_uuid"]
    elif fault == "wrong_task":
        e["events"][1]["task_id"] = "other-task"
    elif fault == "wrong_roster":
        e["roster"]["claude-rco-1"] = "00000000-0000-4000-8000-000000000000"
    elif fault == "author_rco":
        e["pr_author"] = "claude-rco-1"
    elif fault == "ci_old_head":
        e["ci"]["runs"][0]["head"] = e["approved"]["head"]
    elif fault == "ci_red":
        e["ci"]["runs"][0]["conclusion"] = "failure"
    elif fault == "ci_pending":
        e["ci"]["runs"][0]["status"] = "in_progress"
    elif fault == "ci_missing":
        e["ci"]["runs"].pop()
    elif fault == "ci_empty_required":
        e["ci"]["required"] = []
    elif fault == "queue_partial":
        e["accepted_queue_complete"] = False
    elif fault == "queue_untyped":
        e["accepted_queue_complete"] = 1
    elif fault == "wrong_schema":
        e["schema"] = "untrusted-schema"
    result = _api().evaluate_approval_carry_forward(e)
    assert result["carry"] is False
    assert result["carried"] == []


@pytest.mark.parametrize("head", ["old", "new"])
@pytest.mark.parametrize("uuid_valid", [True, False])
def test_approval_carry_preserves_head_scoped_vetoes(approval_evidence, head, uuid_valid):
    e = approval_evidence
    event = _veto(e, head=head)
    if not uuid_valid:
        event["agent_uuid"] = "00000000-0000-4000-8000-000000000000"
    e["events"].append(event)
    assert _api().evaluate_approval_carry_forward(e)["carry"] is False


@pytest.mark.parametrize("clear", ["same_head", "wrong_head", "wrong_agent", "before_veto", "forged"])
def test_approval_carry_explicit_clear_is_same_owner_scope_and_append_order(approval_evidence, clear):
    e = approval_evidence
    veto = _veto(e)
    cleared = _veto(e, head="new" if clear == "wrong_head" else "old",
                    agent="claude-rco-1" if clear == "wrong_agent" else "claude-rco-2",
                    status="changes_requested_cleared", kind="decision")
    if clear == "forged":
        cleared["agent_uuid"] = "00000000-0000-4000-8000-000000000000"
    e["events"].extend([cleared, veto] if clear == "before_veto" else [veto, cleared])
    result = _api().evaluate_approval_carry_forward(e)
    assert result["carry"] is (clear == "same_head"), result


@pytest.mark.parametrize("author", ["codex-lead-1", "codex-tools-1"])
def test_approval_carry_preserves_build_author_waiver_without_carrying_own_vote(approval_evidence, author):
    e = approval_evidence
    e["pr_author"] = author
    e["events"] = [event for event in e["events"] if event["agent"] != author]
    result = _api().evaluate_approval_carry_forward(e)
    assert result["carry"] is True
    assert author not in {row["agent"] for row in result["carried"]}
    assert result["receipt"]["build_author_slot_waivers"] == [author]


@pytest.mark.parametrize("field", ["approved_head", "proposed_head", "patch_id", "approval_event_sha256", "ci_head"])
def test_carry_receipt_rederives_instead_of_trusting_resealed_fields(approval_evidence, field):
    e = approval_evidence
    result = _api().evaluate_approval_carry_forward(e)
    receipt = copy.deepcopy(result["receipt"])
    receipt[field] = ["0" * 64] if field == "approval_event_sha256" else "0" * 40
    receipt["ok"] = receipt["carry"] = True  # these caller flags are not evidence
    assert _api().verify_carry_forward_receipt(receipt, e) is False


def test_carry_receipt_new_late_backdated_veto_invalidates_old_receipt(approval_evidence):
    e = approval_evidence
    receipt = _api().evaluate_approval_carry_forward(e)["receipt"]
    e["events"].append(_veto(e))  # timestamp is deliberately older than every pass
    assert _api().verify_carry_forward_receipt(receipt, e) is False


def test_approval_carry_clear_does_not_reissue_same_reviewers_approval(approval_evidence):
    e = approval_evidence
    e["events"].extend([_veto(e, agent="claude-rco-1"),
                        _veto(e, agent="claude-rco-1", kind="decision", status="changes_requested_cleared")])
    # check_rco_pass_present requires a fresh pass after that same RCO's veto;
    # transferring a pre-veto approval must not manufacture that fresh pass.
    assert _api().evaluate_approval_carry_forward(e)["carry"] is False


@pytest.mark.parametrize("form", ["prose_old", "prose_both", "structured_old"])
def test_approval_carry_preserves_old_head_binding_without_counting_prose_twice(approval_evidence, form):
    e = approval_evidence
    old, new = e["approved"]["head"], e["proposed"]["head"]
    if form != "structured_old":
        e["events"][2]["payload"] = {}
    e["events"][2]["message"] = old + (" and " + new if form == "prose_both" else "")
    assert _api().evaluate_approval_carry_forward(e)["carry"] is (form != "prose_both")


@pytest.mark.parametrize("status", ["operator_signature", "rule10_approval"])
def test_approval_carry_never_transfers_operator_or_rule10_authority(approval_evidence, status):
    e = approval_evidence
    e["events"].append(dict(agent="operator", type="decision", status=status,
                            task_id=e["task_id"], payload=dict(head=e["approved"]["head"])))
    result = _api().evaluate_approval_carry_forward(e)
    assert result["carry"] is True
    assert {row["kind"] for row in result["carried"]} <= {"build_consensus", "rco_pass"}
    assert "operator" not in {row["agent"] for row in result["carried"]}


def test_approval_carry_plain_cleared_token_does_not_weaken_existing_taxonomy(approval_evidence):
    e = approval_evidence
    e["events"].extend([_veto(e), _veto(e, kind="decision", status="cleared")])
    assert _api().evaluate_approval_carry_forward(e)["carry"] is False


@pytest.mark.parametrize("kind", ["heartbeat", "unrelated_message", "forged_positive"])
def test_carry_receipt_ignores_nonmaterial_appends_but_keeps_original_refs(approval_evidence, kind):
    e = approval_evidence
    receipt = _api().evaluate_approval_carry_forward(e)["receipt"]
    original_refs = copy.deepcopy(receipt["approval_event_sha256"])
    if kind == "forged_positive":
        # A bad positive identity changes legacy diagnostic rows, not authority.
        extra = copy.deepcopy(e["events"][2])
        extra["agent_uuid"] = "00000000-0000-4000-8000-000000000000"
    else:
        extra = dict(agent="fable-5", agent_uuid=e["roster"]["fable-5"],
                     type="heartbeat" if kind == "heartbeat" else "message", status="active",
                     task_id="unrelated-task", message="ordinary append", payload={})
    e["events"].append(extra)
    assert _api().verify_carry_forward_receipt(receipt, e) is True
    assert receipt["approval_event_sha256"] == original_refs


@pytest.mark.parametrize("mutation", ["edit", "reorder", "remove_prefix", "truncate_tail"])
def test_carry_receipt_requires_the_original_complete_prefix(approval_evidence, mutation):
    e = approval_evidence
    e["events"].append(dict(agent="fable-5", type="heartbeat", task_id="elsewhere"))
    receipt = _api().evaluate_approval_carry_forward(e)["receipt"]
    if mutation == "edit":
        e["events"][-1]["message"] = "mutated even though nonmaterial"
    elif mutation == "reorder":
        e["events"][0], e["events"][1] = e["events"][1], e["events"][0]
    elif mutation == "remove_prefix":
        del e["events"][0]
    else:
        e["events"].pop()
    assert _api().verify_carry_forward_receipt(receipt, e) is False


@pytest.mark.parametrize("head", ["old", "new"])
@pytest.mark.parametrize("uuid_valid", [True, False])
def test_carry_receipt_scans_all_later_backdated_vetoes(approval_evidence, head, uuid_valid):
    e = approval_evidence
    receipt = _api().evaluate_approval_carry_forward(e)["receipt"]
    e["events"].append(dict(agent="fable-5", type="heartbeat", task_id="elsewhere"))
    event = _veto(e, head=head)
    if not uuid_valid:
        event["agent_uuid"] = "00000000-0000-4000-8000-000000000000"
    e["events"].append(event)
    assert _api().verify_carry_forward_receipt(receipt, e) is False


@pytest.mark.parametrize("count", [None, True, -1, 0, 999, "3"])
def test_carry_receipt_prefix_count_is_required_and_typed(approval_evidence, count):
    e = approval_evidence
    receipt = _api().evaluate_approval_carry_forward(e)["receipt"]
    if count is None:
        receipt.pop("accepted_event_count", None)
    else:
        receipt["accepted_event_count"] = count
    assert _api().verify_carry_forward_receipt(receipt, e) is False


def test_carry_receipt_cannot_reselect_a_new_original_approval(approval_evidence):
    e = approval_evidence
    receipt = _api().evaluate_approval_carry_forward(e)["receipt"]
    extra = copy.deepcopy(e["events"][2])
    extra["ts_utc"] = "2026-11-09T02:00:00Z"
    e["events"].append(extra)
    assert _api().verify_carry_forward_receipt(receipt, e) is False


def _reseal_prefix(receipt, events, count):
    updated = copy.deepcopy(receipt)
    updated["accepted_event_count"] = count
    updated["accepted_events_sha256"] = _event_hash(events[:count])
    return updated


@pytest.mark.parametrize("excluded_agent", ["codex-lead-1", "codex-tools-1", "claude-rco-1"])
def test_carry_receipt_resealed_prefix_must_contain_each_selected_approval(approval_evidence, excluded_agent):
    e = approval_evidence
    e["events"].sort(key=lambda event: event["agent"] == excluded_agent)
    result = _api().evaluate_approval_carry_forward(e)
    assert result["carry"] is True
    receipt = _reseal_prefix(result["receipt"], e["events"], len(e["events"]) - 1)
    assert _api().verify_carry_forward_receipt(receipt, e) is False


@pytest.mark.parametrize("count", [3, 4])
def test_carry_receipt_resealed_prefix_must_contain_each_review_control(approval_evidence, count):
    e = approval_evidence
    e["events"].extend([_veto(e), _veto(e, kind="decision", status="changes_requested_cleared")])
    result = _api().evaluate_approval_carry_forward(e)
    assert result["carry"] is True
    assert len(result["receipt"]["veto_controls"]) == 2
    # All three selected approvals are inside this shortened prefix, while one
    # or both recorded review-control events lie outside it.
    receipt = _reseal_prefix(result["receipt"], e["events"], count)
    assert _api().verify_carry_forward_receipt(receipt, e) is False


@pytest.mark.parametrize("author", ["codex-lead-1", "codex-tools-1"])
def test_carry_receipt_prefix_coverage_respects_author_waiver_without_dropping_rco(approval_evidence, author):
    e = approval_evidence
    e["pr_author"] = author
    e["events"] = [event for event in e["events"] if event["agent"] != author]
    receipt = _api().evaluate_approval_carry_forward(e)["receipt"]
    assert _api().verify_carry_forward_receipt(receipt, e) is True
    assert receipt["accepted_event_count"] == 2
    assert _api().verify_carry_forward_receipt(_reseal_prefix(receipt, e["events"], 1), e) is False


def test_carry_receipt_prefix_cannot_use_an_inside_hash_for_an_outside_selected_duplicate(approval_evidence):
    e = approval_evidence
    receipt = _api().evaluate_approval_carry_forward(e)["receipt"]
    e["events"].append(copy.deepcopy(e["events"][2]))
    # The later selected occurrence has the same event digest. Its actual
    # append index must still be within the receipt's recorded prefix.
    current = _api().evaluate_approval_carry_forward(e)
    assert current["carry"] is True
    assert current["carried"] == receipt["carried"]
    assert _api().verify_carry_forward_receipt(receipt, e) is False
    assert _api().verify_carry_forward_receipt(current["receipt"], e) is True


def test_carry_receipt_covered_controls_keep_harmless_append_valid(approval_evidence):
    e = approval_evidence
    e["events"].extend([_veto(e), _veto(e, kind="decision", status="changes_requested_cleared")])
    receipt = _api().evaluate_approval_carry_forward(e)["receipt"]
    assert _api().verify_carry_forward_receipt(receipt, e) is True
    e["events"].append(dict(agent="fable-5", type="heartbeat", task_id="elsewhere"))
    assert _api().verify_carry_forward_receipt(receipt, e) is True


@pytest.fixture
def live_factory(tmp_path):
    from waggledance.core.bridge_identity_registry import load_bridge_identity_registry
    path = "tools/agent_next_task.py"
    root, git, pair = _build_history(tmp_path, path)
    repo, task, pr = "example/carry", "fable-5/carry-fixture-9001", 9001
    git("remote", "add", "origin", f"https://github.com/{repo}.git")
    proof = _api().derive_content_identity(repo_root=root, **pair)
    basis = dict(schema="wd.bridge-reviewed-basis.v1", repository=repo, pr_number=pr,
                 task_id=task, base_ref="main", head=pair["old_head"], base_sha=pair["old_base"],
                 merge_base=proof["old_merge_base"], diff_sha256=proof["old_diff_sha256"], patch_id=proof["old_patch_id"])
    roster = load_bridge_identity_registry()
    claim = dict(agent="fable-5", agent_uuid=roster["fable-5"], type="claim", status="active",
                 task_id=task, ts_utc="2026-11-09T01:00:00Z", write_scope=[path], payload=dict(pr=pr))
    events = [claim] + [dict(agent=agent, agent_uuid=roster[agent], type="decision", status=status,
                            task_id=task, ts_utc=f"2026-11-09T01:0{i}:00Z", message="",
                            payload=dict(head=pair["old_head"], exact_head=pair["old_head"], pr=pr, review_basis=copy.deepcopy(basis),
                                         review_basis_sha256=_event_hash(basis)))
                        for i, (agent, status) in enumerate((("codex-lead-1", "build_consensus"),
                                                            ("codex-tools-1", "build_consensus"),
                                                            ("claude-rco-1", "rco_pass")), 1)]
    context = dict(root=root, git=git, pair=pair, repo=repo, task=task, pr=pr, path=path, basis=basis,
                   events=events, events_path=tmp_path / "events.jsonl", calls=[], fault="", queue=True)
    context["request"] = dict(approved_head=pair["old_head"], approved_base_sha=pair["old_base"], repo_root=root)

    def runner(command):
        command = list(command)
        context["calls"].append(command)
        fault = context["fault"]
        head, base = pair["new_head"], pair["new_base"]
        if command[:3] == ["gh", "pr", "view"]:
            payload = dict(number=pr, title="carry factory", headRefOid=head, headRefName=context["task"],
                           baseRefOid=base, baseRefName="main", mergeable="MERGEABLE", state="OPEN", isDraft=False,
                           url=f"https://github.com/{repo}/pull/{pr}", updatedAt="2026-11-09T01:30:00Z",
                           changedFiles=1, statusCheckRollup=[dict(name="unit", state="SUCCESS")],
                           author=dict(login="Ahkeratmehilaiset", name="", email=""),
                           commits=[dict(oid=head, authors=[dict(name="Jani", email="jani@jkhservice.fi", login="")])])
        elif command[:3] == ["gh", "pr", "diff"]:
            return SimpleNamespace(returncode=0, stdout=f"diff --git a/{path} b/{path}\n", stderr="")
        elif command[:2] == ["gh", "api"]:
            endpoint = command[4]
            if "/git/ref/heads/" in endpoint:
                payload = dict(ref="refs/heads/main", object=dict(type="commit", sha=base))
            elif endpoint.endswith("/protection/required_status_checks"):
                payload = dict(strict=True, contexts=["unit"], checks=[dict(context="unit", app_id=15368)])
            elif "/rules/branches/" in endpoint:
                payload = []
            elif endpoint.endswith("/check-runs"):
                run = dict(id=1, name="unit", head_sha="0" * 40 if fault == "ci_old" else head,
                           app=dict(id=15368), check_suite=dict(id=2), status="completed",
                           conclusion="failure" if fault == "ci_failed" else "success",
                           url=f"https://api.github.com/repos/{repo}/check-runs/1")
                payload = dict(total_count=1, check_runs=[run])
            elif endpoint.endswith("/files"):
                payload = [dict(filename="tools/wrong.py" if fault == "paths" else path, status="modified")]
            else:
                raise AssertionError(command)
        else:
            raise AssertionError(f"factory must never merge: {command}")
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
    context["runner"] = runner
    return context


def _run_live_factory(context):
    context["events_path"].write_text("\n".join(json.dumps(e) for e in context["events"]), encoding="utf-8")
    return _api().derive_live_carry_forward(
        request=context["request"], events_path=context["events_path"], repo=context["repo"],
        pr_number=context["pr"], task_id=context["task"], expected_head=context["pair"]["new_head"],
        expected_base_sha=context["pair"]["new_base"], runner=context["runner"],
        accepted_queue_checker=lambda **_: dict(ok=True, complete=context["queue"]))


def _descriptor_origin(context, *, commitments=True, retain_basis=False):
    for event in context["events"]:
        if event["type"] == "decision":
            if not retain_basis:
                event["payload"].pop("review_basis", None)
            if not commitments:
                event["payload"].pop("review_basis_sha256", None)
    # Descriptor references the actual original events after removing the direct
    # route; an independent accepted binding event anchors the immutable bytes.
    refs = [_event_hash(e) for e in context["events"] if e["type"] == "decision"]
    folder = context["root"] / ".codex-audit"
    folder.mkdir()
    path = folder / "original-review.json"
    body = dict(schema="wd.bridge-review-snapshot.v1", review_basis=context["basis"], approval_event_sha256=refs)
    raw = json.dumps(body).encode()
    path.write_bytes(raw)
    descriptor = dict(path=str(path), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    binding = dict(agent="codex-lead-1", agent_uuid=context["events"][1]["agent_uuid"], type="decision",
                   status="review_basis_bound", task_id=context["task"], ts_utc="2026-11-09T01:20:00Z",
                   payload=dict(review_origin=dict(schema="wd.bridge-review-origin.v1", descriptor=descriptor,
                                                   review_basis=copy.deepcopy(context["basis"]),
                                                   approval_event_sha256=refs)))
    context["events"].append(binding)
    context["request"]["origin_binding_event_sha256"] = _event_hash(binding)
    return path, binding


@pytest.mark.parametrize("origin", ["direct", "descriptor"])
def test_live_factory_reconstructs_origin_queue_and_strict_ci(live_factory, origin):
    c = live_factory
    if origin == "descriptor":
        _descriptor_origin(c)
    result = _run_live_factory(c)
    assert result["carry"] is True, result
    assert result["review_origin"]["mode"] == origin
    assert result["ci_evidence"]["head_sha"] == c["pair"]["new_head"]
    assert result["ci_evidence"]["all_required_success"] is True
    assert result["receipt"]["approved_base_sha"] == c["basis"]["base_sha"]
    assert all(row["kind"] in {"build_consensus", "rco_pass"} for row in result["carried"])
    assert any(call[:2] == ["gh", "api"] for call in c["calls"])


@pytest.mark.parametrize("fault", ["missing_basis", "missing_one_basis", "wrong_basis_hash", "wrong_repo",
                                  "wrong_pr", "wrong_task", "wrong_ref", "wrong_old_base", "wrong_merge_base",
                                  "wrong_diff", "wrong_patch", "forged_vote", "forged_claim", "queue_partial",
                                  "ci_failed", "ci_old", "paths", "remote", "different_vote_basis"])
def test_live_factory_refuses_unproven_original_or_current_facts(live_factory, fault):
    c = live_factory
    if fault == "missing_basis":
        for event in c["events"][1:]:
            event["payload"].pop("review_basis")
            event["payload"].pop("review_basis_sha256")
    elif fault == "missing_one_basis":
        c["events"][2]["payload"].pop("review_basis")
    elif fault == "wrong_basis_hash":
        c["events"][1]["payload"]["review_basis_sha256"] = "0" * 64
    elif fault == "different_vote_basis":
        payload = c["events"][3]["payload"]
        payload["review_basis"]["diff_sha256"] = "0" * 64
        payload["review_basis_sha256"] = _event_hash(payload["review_basis"])
    elif fault.startswith("wrong_"):
        field = {"wrong_repo":"repository", "wrong_pr":"pr_number", "wrong_task":"task_id", "wrong_ref":"base_ref",
                 "wrong_old_base":"base_sha", "wrong_merge_base":"merge_base", "wrong_diff":"diff_sha256", "wrong_patch":"patch_id"}[fault]
        value = 9002 if field == "pr_number" else ("0" * 64 if field == "diff_sha256" else "0" * 40)
        for event in c["events"][1:]:
            event["payload"]["review_basis"][field] = value
            event["payload"]["review_basis_sha256"] = _event_hash(event["payload"]["review_basis"])
    elif fault in {"forged_vote", "forged_claim"}:
        c["events"][1 if fault == "forged_vote" else 0]["agent_uuid"] = "00000000-0000-4000-8000-000000000000"
    elif fault == "queue_partial":
        c["queue"] = False
        c["request"]["accepted_queue_complete"] = True
    elif fault == "remote":
        c["git"]("remote", "set-url", "origin", "https://github.com/other/repo.git")
    else:
        c["fault"] = fault
    result = _run_live_factory(c)
    assert result["carry"] is False, result
    if fault == "queue_partial":
        assert c["calls"] == []


@pytest.mark.parametrize("fault", ["unbound", "forged_issuer", "wrong_refs", "tamper", "reseal", "outside_root",
                                  "author_issuer", "wrong_binding_task", "body_wrong_basis", "contradicts_direct"])
def test_live_factory_descriptor_requires_canonical_binding_and_immutable_bytes(live_factory, fault):
    c = live_factory
    path, binding = _descriptor_origin(c)
    origin = binding["payload"]["review_origin"]
    if fault == "unbound":
        c["request"]["origin_binding_event_sha256"] = "0" * 64
    elif fault == "forged_issuer":
        binding["agent_uuid"] = "00000000-0000-4000-8000-000000000000"
    elif fault == "wrong_refs":
        origin["approval_event_sha256"] = ["0" * 64]
    elif fault in {"tamper", "reseal"}:
        path.write_bytes(path.read_bytes() + b" ")
        if fault == "reseal":
            c["request"]["descriptor"] = dict(origin["descriptor"], bytes=path.stat().st_size,
                                             sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    elif fault == "outside_root":
        outside = c["root"].parent / "unapproved.json"
        outside.write_bytes(path.read_bytes())
        origin["descriptor"]["path"] = str(outside)
    elif fault == "author_issuer":
        binding.update(agent="fable-5", agent_uuid=c["events"][0]["agent_uuid"])
    elif fault == "wrong_binding_task":
        binding["task_id"] = "unrelated"
    else:
        body = json.loads(path.read_bytes())
        if fault == "body_wrong_basis":
            body["review_basis"]["base_sha"] = "0" * 40
        else:
            conflicting = dict(c["basis"], diff_sha256="0" * 64)
            c["events"][3]["payload"].update(review_basis=conflicting, review_basis_sha256=_event_hash(conflicting))
            refs = [_event_hash(e) for e in c["events"][1:4]]
            origin["approval_event_sha256"] = refs
            body["approval_event_sha256"] = refs
        raw = json.dumps(body).encode()
        path.write_bytes(raw)
        origin["descriptor"].update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    if fault != "unbound":
        c["request"]["origin_binding_event_sha256"] = _event_hash(binding)
    assert _run_live_factory(c)["carry"] is False


def test_live_factory_does_not_take_a_side_commit_as_the_target_base(live_factory):
    c = live_factory
    git, root, pair = c["git"], c["root"], c["pair"]
    git("checkout", "--quiet", pair["old_base"])
    (root / "side.txt").write_bytes(b"not reviewed\n")
    git("add", "side.txt"); git("commit", "--quiet", "-m", "unreviewed side")
    side = git("rev-parse", "HEAD").decode().strip()
    (root / c["path"]).write_bytes(b"alpha gamma\n")
    git("add", c["path"]); git("commit", "--quiet", "-m", "reapply reviewed patch")
    pair["new_head"] = git("rev-parse", "HEAD").decode().strip()
    c["request"]["proposed_base_sha"] = side
    assert _run_live_factory(c)["carry"] is False


def test_live_factory_real_plan_wires_both_gates_but_keeps_apply_unavailable(live_factory):
    from tools.idle_consensus_auto_merge import evaluate_auto_merge_gate
    c = live_factory
    derived = _run_live_factory(c)
    assert derived["carry"] is True
    kwargs = dict(pr_status=derived["_fresh_snapshot"], expected_head=c["pair"]["new_head"],
                  expected_base_sha=c["pair"]["new_base"], consensus_proposal_id=c["task"],
                  events_path=c["events_path"], bridge_task_id=c["task"], repo=c["repo"],
                  from_agent="codex-lead-1", require_bridge_consensus=True,
                  receipt_bundle_path="synthetic/manifest.json", runner=c["runner"],
                  accepted_queue_checker=lambda **_: dict(ok=True, complete=True))
    kwargs["pr_status"]["receipt_verified"] = True  # legacy plan metadata, never carry proof
    plain = evaluate_auto_merge_gate(**kwargs)
    assert plain["ok"] is False
    result = evaluate_auto_merge_gate(**kwargs, carry_forward=c["request"])
    assert result["decision"] == "auto_merge_plan_ready", result.get("reasons")
    assert result["rco_pass_gate"]["ok"] and result["bridge_consensus"]["ok"]
    for key in ("rco_pass_gate", "bridge_consensus"):
        assert result[key]["carried_from_head"] == c["pair"]["old_head"]
        assert result[key]["carried_to_head"] == c["pair"]["new_head"]
    assert result["carry_forward"]["receipt"]["proposed_head"] == c["pair"]["new_head"]
    applied = evaluate_auto_merge_gate(**kwargs, carry_forward=c["request"], apply=True)
    assert applied["ok"] is False and applied["would_merge"] is False
    assert not any(call[:3] == ["gh", "pr", "merge"] for call in c["calls"])


@pytest.mark.parametrize("legacy", [True, False])
def test_live_factory_never_reconstructs_reviewed_base_from_current_stacked_target(live_factory, legacy):
    c = live_factory
    git, root, pair = c["git"], c["root"], c["pair"]
    original_base = pair["old_base"]
    git("checkout", "--quiet", original_base)
    (root / "feature.txt").write_bytes(b"feature F\n")
    git("add", "feature.txt")
    git("commit", "--quiet", "-m", "unmerged feature F")
    feature = git("rev-parse", "HEAD").decode().strip()
    (root / c["path"]).write_bytes(b"alpha gamma\n")
    git("add", c["path"])
    git("commit", "--quiet", "-m", "reviewed F plus P")
    old = git("rev-parse", "HEAD").decode().strip()
    tree = git("rev-parse", "HEAD^{tree}").decode().strip()
    alternate = git("commit-tree", tree, "-p", original_base, data=b"review-basis twin\n").decode().strip()
    reviewed = _api().derive_content_identity(repo_root=root, old_base=original_base, old_head=old,
                                             new_base=original_base, new_head=alternate)
    pair.update(old_head=old, new_base=feature,
                new_head=git("commit-tree", tree, "-p", feature, data=b"P after F landed\n").decode().strip())
    c["basis"].update(head=old, base_sha=original_base, merge_base=original_base,
                       diff_sha256=reviewed["old_diff_sha256"], patch_id=reviewed["old_patch_id"])
    c["request"].update(approved_head=old, approved_base_sha=feature if legacy else original_base)
    for event in c["events"][1:]:
        event["payload"].update(head=old, exact_head=old)
        if legacy:
            event["payload"].pop("review_basis")
            event["payload"].pop("review_basis_sha256")
        else:
            event["payload"]["review_basis"] = copy.deepcopy(c["basis"])
            event["payload"]["review_basis_sha256"] = _event_hash(c["basis"])
    assert git("merge-base", old, feature).decode().strip() == feature
    assert _run_live_factory(c)["carry"] is False


@pytest.mark.parametrize("fault", ["ci_failed", "ci_old", "paths"])
def test_live_factory_real_gate_does_not_trust_the_callers_old_green_snapshot(live_factory, fault):
    from tools.idle_consensus_auto_merge import evaluate_auto_merge_gate
    c = live_factory
    observed = _run_live_factory(c)
    assert observed["carry"] is True
    stale = observed["_fresh_snapshot"]
    stale["receipt_verified"] = True
    c["fault"] = fault
    result = evaluate_auto_merge_gate(
        pr_status=stale, expected_head=c["pair"]["new_head"], expected_base_sha=c["pair"]["new_base"],
        consensus_proposal_id=c["task"], events_path=c["events_path"], bridge_task_id=c["task"],
        repo=c["repo"], from_agent="codex-lead-1", require_bridge_consensus=True,
        receipt_bundle_path="synthetic/manifest.json", runner=c["runner"],
        accepted_queue_checker=lambda **_: dict(ok=True, complete=True), carry_forward=c["request"])
    assert result["ok"] is False


@pytest.mark.parametrize("policy", ["rule12", "no_full_consensus"])
def test_live_factory_real_gate_refuses_unsupported_carry_policy_without_fetch(live_factory, policy):
    from tools.idle_consensus_auto_merge import REVIEW_POLICY_LEGACY, evaluate_auto_merge_gate
    c = live_factory
    observed = _run_live_factory(c)
    assert observed["carry"] is True
    calls_before = len(c["calls"])
    result = evaluate_auto_merge_gate(
        pr_status=observed["_fresh_snapshot"], expected_head=c["pair"]["new_head"],
        expected_base_sha=c["pair"]["new_base"], consensus_proposal_id=c["task"],
        events_path=c["events_path"], bridge_task_id=c["task"], repo=c["repo"],
        from_agent="codex-lead-1", require_bridge_consensus=policy != "no_full_consensus",
        review_policy="rule12" if policy == "rule12" else REVIEW_POLICY_LEGACY, runner=c["runner"],
        accepted_queue_checker=lambda **_: dict(ok=True, complete=True), carry_forward=c["request"])
    assert result["ok"] is False
    assert len(c["calls"]) == calls_before


@pytest.mark.parametrize("missing", ["all", "tools", "rco"])
def test_live_factory_descriptor_cannot_proxy_a_missing_original_reviewer_commitment(live_factory, missing):
    c = live_factory
    if missing == "all":
        _descriptor_origin(c, commitments=False)
    else:
        index = 2 if missing == "tools" else 3
        c["events"][index]["payload"].pop("review_basis")
        c["events"][index]["payload"].pop("review_basis_sha256")
        _descriptor_origin(c, retain_basis=True)
    assert _run_live_factory(c)["carry"] is False


@pytest.mark.parametrize("shape", ["head_only", "prose", "mixed_selected_weak"])
def test_live_factory_preserves_the_existing_strict_rco_pass_shape(live_factory, shape):
    from waggledance.core.bridge_identity_registry import load_bridge_identity_registry
    c = live_factory
    rco = c["events"][3]
    rco["payload"].pop("exact_head")
    if shape == "prose":
        rco["message"] = c["pair"]["old_head"]
    elif shape == "mixed_selected_weak":
        valid = copy.deepcopy(rco)
        valid.update(agent="claude-rco-2", agent_uuid=load_bridge_identity_registry()["claude-rco-2"])
        valid["payload"]["exact_head"] = c["pair"]["old_head"]
        c["events"].insert(3, valid)
    result = _run_live_factory(c)
    assert result["carry"] is (shape == "prose"), result.get("reason")
