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


def _with_voter_basis(evidence):
    old, new = evidence["approved"], evidence["proposed"]
    proof = _api().derive_content_identity(repo_root=Path(evidence["repo_path"]),
        old_base=old["base_sha"], old_head=old["head"], new_base=new["base_sha"], new_head=new["head"])
    basis = dict(schema="wd.bridge-reviewed-basis.v1", repository=old["repository"],
        pr_number=old["pr_number"], task_id=evidence["task_id"], base_ref=old["base_ref"],
        head=old["head"], base_sha=old["base_sha"], merge_base=proof["old_merge_base"],
        diff_sha256=proof["old_diff_sha256"], patch_id=proof["old_patch_id"])
    for event in evidence["events"]:
        event["payload"].update(review_basis=copy.deepcopy(basis), review_basis_sha256=_event_hash(basis))
    return basis


@pytest.mark.parametrize("fault", ["base_sha", "merge_base", "diff_sha256", "patch_id",
                                    "digest", "partial", "malformed"])
def test_helper_requires_present_voter_basis_to_match_derived_git(approval_evidence, history, fault):
    evidence = approval_evidence
    _, git, pair = history
    git("update-ref", "refs/heads/main", pair["new_base"])
    _with_voter_basis(evidence)
    for event in evidence["events"]:
        payload = event["payload"]
        if fault in {"base_sha", "merge_base", "patch_id", "diff_sha256"}:
            payload["review_basis"][fault] = "f" * (64 if fault == "diff_sha256" else 40)
            payload["review_basis_sha256"] = _event_hash(payload["review_basis"])
        elif fault == "digest":
            payload["review_basis_sha256"] = "f" * 64
        elif fault == "malformed":
            payload["review_basis"] = None
    if fault == "partial":
        evidence["events"][0]["payload"].pop("review_basis")
    result = _api().evaluate_approval_carry_forward(evidence)
    assert result["carry"] is False, result
    assert "basis" in result["reason"] or "base" in result["reason"], result
    assert _api().verify_carry_forward_receipt(result.get("receipt") or {}, evidence) is False


@pytest.mark.parametrize("fault", ["missing", "stale", "invalid_ref"])
def test_helper_requires_proposed_base_on_named_local_branch(approval_evidence, history, fault):
    evidence = approval_evidence
    _, git, pair = history
    if fault == "stale":
        tree = git("rev-parse", pair["old_base"] + "^{tree}").decode().strip()
        unrelated = git("commit-tree", tree, data=b"unrelated main\n").decode().strip()
        git("update-ref", "refs/heads/main", unrelated)
    elif fault == "invalid_ref":
        evidence["approved"]["base_ref"] = evidence["proposed"]["base_ref"] = "../main"
    else:
        git("update-ref", "-d", "refs/heads/main")
    result = _api().evaluate_approval_carry_forward(evidence)
    assert result["carry"] is False, result
    assert "base" in result["reason"], result


@pytest.mark.parametrize("basis", [False, True])
def test_helper_local_base_and_optional_voter_basis_honest_control(approval_evidence, history, basis):
    evidence = approval_evidence
    _, git, pair = history
    git("update-ref", "refs/heads/main", pair["new_base"])
    if basis:
        _with_voter_basis(evidence)
    result = _api().evaluate_approval_carry_forward(evidence)
    assert result["carry"] is True, result
    # No-basis acceptance is diagnostic evidence_only, not authenticated origin.
    assert result["authority"] == "evidence_only"
    assert _api().verify_carry_forward_receipt(result["receipt"], evidence) is True


def _boundary_corpus(name):
    import importlib.util
    path = Path(__file__).resolve().parent / (name + ".py")
    spec = importlib.util.spec_from_file_location("tools_boundary_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def exact_boundary_histories(tmp_path_factory):
    forged = _boundary_corpus("test_consensus_carry_forward_origin_forgery")
    executors = _boundary_corpus("test_consensus_carry_forward_apply_executors")
    return forged, executors, forged.histories.__wrapped__(tmp_path_factory)


@pytest.mark.parametrize("attack", ["side", "stacked_rebased"])
def test_exact_corpus_attack_reaches_real_magma_and_idle_apply(tmp_path, exact_boundary_histories, attack, monkeypatch):
    """Same corpus histories/payloads; integrity-valid untrusted MAGMA, real consumers.

    The wrapper has no caller-receipt input. Test its live factory boundary via
    the unchanged corpus, and offer the actual forged bundle to idle apply and
    verify_carry_forward_apply. Every effect assertion uses the invoked runner.
    """
    from datetime import datetime, timezone
    from dataclasses import replace
    import tools.idle_consensus_auto_merge as idle
    import tools.write_bridge_consensus_merge_receipt as writer
    from tools.verify_magma_receipt import verify_manifest
    forged, executors, histories = exact_boundary_histories
    of = forged.of
    # Keep the original src/feature.txt histories and payloads byte-exact. Give
    # this synthetic gate context that one path instead of relabelling the
    # GitHub diff or rewriting history to fit the production allowlist. All
    # other charter checks and the actual gate/consumer implementations run.
    charter = idle.load_charter()
    fixture_charter = replace(charter, allowlist=(*charter.allowlist, of.CHANGED_PATH))
    monkeypatch.setattr(idle, "load_charter", lambda path: fixture_charter)
    legitimate = executors.Env(histories["legit"], tmp_path / "control")
    manifest = executors._write_receipt(legitimate)
    payload = json.loads((manifest.parent / "payload-001-merge.json").read_bytes())
    material = copy.deepcopy(payload["carry_forward"])
    h = histories[attack]
    target = executors.Env(h, tmp_path / "attack")
    expected_base = h.live_base
    if attack == "side":
        # Drive the unchanged adversarial probe with these EXACT commits first.
        original = dict(repo=h.repo, approved=dict(head=h.old, base_sha=h.old_base),
                        proposed=dict(head=h.new, base_sha=h.side_base))
        of.corpus.test_gate_refuses_side_commit_base_substitution({"side_commit_base_substitution": original})
        proof = _api().derive_content_identity(repo_root=Path(h.repo.path), old_base=h.old_base,
                   old_head=h.old, new_base=h.side_base, new_head=h.new)
        untrusted = copy.deepcopy(material["approval_receipt"])
        untrusted.update(approved_head=h.old, proposed_head=h.new, approved_base_sha=h.old_base,
                         proposed_base_sha=h.side_base, approved_merge_base=proof["old_merge_base"],
                         proposed_merge_base=proof["new_merge_base"], content_identity=proof,
                         approved_diff_sha256=proof["old_diff_sha256"], proposed_diff_sha256=proof["new_diff_sha256"],
                         patch_id=proof["old_patch_id"], diff_argv=proof["diff_argv"])
        target.request["proposed_base_sha"] = expected_base = h.side_base
    else:
        (tmp_path / "template").mkdir()
        live = of._run(tmp_path / "template", histories["legit"], of._all_bound(histories["legit"]))
        assert live.carry is True, live.text
        evidence = forged._forged_stacked_evidence(live.result["_evidence"], h, approved_base=h.feature)
        forged.test_helper_forged_stacked_receipt_and_evidence((h, live.result["_evidence"]))
        # Evidence-only construction without a committed basis remains possible;
        # it is explicitly untrusted and cannot become live authorization.
        unbound = copy.deepcopy(evidence)
        for event in unbound["events"]:
            event.get("payload", {}).pop("review_basis", None)
            event.get("payload", {}).pop("review_basis_sha256", None)
        diagnostic = _api().evaluate_approval_carry_forward(unbound)
        assert diagnostic["carry"] is True and diagnostic["authority"] == "evidence_only", diagnostic
        untrusted = diagnostic["receipt"]
        refs = {_event_hash(before): _event_hash(after) for before, after in zip(unbound["events"], evidence["events"])}
        for row in untrusted["carried"]:
            row["event_sha256"] = refs[row["event_sha256"]]
        untrusted["approval_event_sha256"] = [row["event_sha256"] for row in untrusted["carried"]]
        untrusted["accepted_events_sha256"] = _event_hash(evidence["events"])
        untrusted["original_approval_basis_sha256"] = _event_hash(dict(
            carried=untrusted["carried"], build_author_slot_waivers=untrusted["build_author_slot_waivers"]))
        assert untrusted["approved_base_sha"] == h.feature != h.old_base
        material["review_origin"] = dict(material["review_origin"], review_basis=of._basis(h, base=h.feature))
    assert untrusted["approved_diff_sha256"] == untrusted["proposed_diff_sha256"]
    material["approval_receipt"] = untrusted
    payload.update(repo=of.REPO_NAME, pr_number=of.PR, task_id=of.TASK, head_sha=h.new,
                   base_sha=expected_base, carry_forward=material)
    bundle = writer._write_receipt_bundle(payload=payload, bridge_consensus=payload["bridge_consensus"],
                  out_dir=tmp_path / "untrusted", now_utc=datetime.now(timezone.utc))
    forged_manifest = Path(bundle["manifest"])
    assert verify_manifest(forged_manifest)["ok"] is True
    consumed = writer.verify_carry_forward_apply(manifest_path=forged_manifest, carry_forward=target.request,
        events_path=target.events_path(), repo=of.REPO_NAME, pr_number=of.PR, task_id=of.TASK,
        expected_head=h.new, expected_base_sha=expected_base, runner=target.gh,
        accepted_queue_checker=target.queue_checker())
    assert consumed["ok"] is False, consumed
    reason = " ".join(consumed.get("errors", []))
    assert any(word in reason for word in ("basis", "base", "diff bytes")), consumed
    assert not any(word in reason for word in ("schema", "integrity", "malformed", "parse")), consumed
    refused = executors._outcome(lambda: executors._run_idle_executor(target, forged_manifest, expected_base=expected_base))
    assert refused.get("ok") is not True, refused
    assert not executors._merges(target.gh)
    assert not forged._gh_effects(target.gh.calls)
    control = executors._outcome(lambda: executors._run_idle_executor(legitimate, manifest))
    assert control.get("ok") is True, control
    merges = executors._merges(legitimate.gh)
    assert len(merges) == 1 and f"--match-head-commit={legitimate.h.new}" in merges[0]


_CARRY_CLIS = ("tools.idle_consensus_auto_merge", "tools.merge_with_bridge_receipt",
               "tools.write_bridge_consensus_merge_receipt")


def _carry_cli_context(tmp_path, monkeypatch, module_name):
    import importlib
    module = importlib.import_module(module_name)
    bridge = tmp_path / "bridge"
    (bridge / "shared").mkdir(parents=True)
    status = tmp_path / "status.json"
    status.write_text(json.dumps(dict(pr_number=9001)))
    monkeypatch.setattr(module, "resolve_bridge_root", lambda _: bridge)
    args = ["--repo", "example/carry", "--expected-head", "b" * 40,
            "--expected-base-sha", "c" * 40, "--consensus-proposal-id", "fable-5/carry-cli", "--json"]
    target = ("evaluate_auto_merge_gate" if "idle_consensus" in module_name else
              "merge_with_bridge_receipt" if "merge_with" in module_name else "write_bridge_consensus_merge_receipt")
    if "merge_with" in module_name:
        args = ["9001", "--out-dir", str(tmp_path / "out"), *args]
    else:
        args += ["--pr-status-file", str(status)]
        if "write_bridge" in module_name:
            args += ["--out-dir", str(tmp_path / "out")]
        else:
            args += ["--require-bridge-consensus"]
    calls = []
    def receiver(**kwargs):
        calls.append(kwargs)
        return dict(ok=True, decision="prepared", receipt_bundle_path="fixture", exit_code=0)
    monkeypatch.setattr(module, target, receiver)
    return module, args, calls


@pytest.mark.parametrize("module_name", _CARRY_CLIS)
def test_carry_cli_default_is_off(tmp_path, monkeypatch, module_name):
    module, args, calls = _carry_cli_context(tmp_path, monkeypatch, module_name)
    assert module.main(args) == 0
    assert len(calls) == 1 and "carry_forward" not in calls[0]


@pytest.mark.parametrize("module_name", _CARRY_CLIS)
def test_carry_cli_builds_reference_only_request(tmp_path, monkeypatch, module_name):
    module, args, calls = _carry_cli_context(tmp_path, monkeypatch, module_name)
    args += ["--carry-approved-head", "a" * 40, "--carry-repo-root", str(tmp_path),
             "--carry-approved-base-sha", "d" * 40, "--carry-origin-binding-event-sha256", "e" * 64]
    assert module.main(args) == 0
    assert calls[0]["carry_forward"] == dict(approved_head="a" * 40, repo_root=tmp_path,
                                             approved_base_sha="d" * 40, origin_binding_event_sha256="e" * 64)


@pytest.mark.parametrize("module_name", _CARRY_CLIS)
@pytest.mark.parametrize("fault", ["head_only", "root_only", "base_only", "origin_only", "bad_head", "bad_base", "bad_origin", "relative_root"])
def test_carry_cli_refuses_incomplete_or_ambiguous_reference_before_work(tmp_path, monkeypatch, module_name, fault):
    module, args, calls = _carry_cli_context(tmp_path, monkeypatch, module_name)
    flags = dict(head_only=["--carry-approved-head", "a" * 40], root_only=["--carry-repo-root", str(tmp_path)],
                 base_only=["--carry-approved-base-sha", "d" * 40], origin_only=["--carry-origin-binding-event-sha256", "e" * 64])
    extra = flags.get(fault, ["--carry-approved-head", "a" * 40, "--carry-repo-root", str(tmp_path)])
    if fault == "bad_head":
        extra[1] = "A" * 40
    elif fault == "bad_base":
        extra += ["--carry-approved-base-sha", "x"]
    elif fault == "bad_origin":
        extra += ["--carry-origin-binding-event-sha256", "x"]
    elif fault == "relative_root":
        extra[-1] = "relative/repo"
    assert module.main(args + extra) == 2
    assert not calls


def test_carry_idle_cli_writes_the_actual_magma_receipt(tmp_path, monkeypatch):
    import tools.write_bridge_consensus_merge_receipt as writer
    module, args, calls = _carry_cli_context(tmp_path, monkeypatch, _CARRY_CLIS[0])
    writes = []
    def write(**kwargs):
        writes.append(kwargs)
        return dict(ok=True, receipt_bundle_path="real-carry")
    monkeypatch.setattr(writer, "write_bridge_consensus_merge_receipt", write)
    def gate(**kwargs):
        assert kwargs["artifact_writer"] is not None
        assert kwargs["artifact_writer"]()["receipt_bundle_path"] == "real-carry"
        return dict(ok=True, decision="prepared")
    monkeypatch.setattr(module, "evaluate_auto_merge_gate", gate)
    assert module.main(args + ["--apply", "--receipt-out-dir", str(tmp_path / "magma"),
                                "--carry-approved-head", "a" * 40, "--carry-repo-root", str(tmp_path)]) == 0
    assert writes[0]["carry_forward"] == dict(approved_head="a" * 40, repo_root=tmp_path)
    assert writes[0]["expected_head"] == "b" * 40
    assert writes[0]["expected_base_sha"] == "c" * 40


def test_carry_idle_cli_refusal_is_nonzero(tmp_path, monkeypatch):
    module, args, calls = _carry_cli_context(tmp_path, monkeypatch, _CARRY_CLIS[0])
    monkeypatch.setattr(module, "evaluate_auto_merge_gate", lambda **_: dict(ok=False, decision="operator_review_required"))
    assert module.main(args + ["--carry-approved-head", "a" * 40, "--carry-repo-root", str(tmp_path)]) == 1


@pytest.mark.parametrize("apply", [False, True])
def test_carry_idle_cli_refuses_legacy_artifact_destination(tmp_path, monkeypatch, apply):
    module, args, calls = _carry_cli_context(tmp_path, monkeypatch, _CARRY_CLIS[0])
    extra = ["--carry-approved-head", "a" * 40, "--carry-repo-root", str(tmp_path),
             "--artifact-out-dir", str(tmp_path / "legacy"), "--receipt-out-dir", str(tmp_path / "magma")]
    assert module.main(args + extra + (["--apply"] if apply else [])) == 2
    assert not calls


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
    git("update-ref", "refs/heads/main", base)
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


@pytest.mark.parametrize("key", ["pr", "pr_number", "pull_request", "pull_request_number"])
@pytest.mark.parametrize("value", [True, 1.0, "01", "1.0", " 1", None])
def test_carry_original_vote_pr_binding_is_json_typed(approval_evidence, key, value):
    evidence = approval_evidence
    evidence["approved"]["pr_number"] = evidence["proposed"]["pr_number"] = 1
    for event in evidence["events"]:
        event["payload"]["pr"] = 1
    evidence["events"][0]["payload"][key] = value
    result = _api().evaluate_approval_carry_forward(evidence)
    assert result["carry"] is False, result


def _install_fake_carry_merge(c):
    """Exercise the real entrypoints and receipt consumer without a GH effect."""
    from types import SimpleNamespace
    original = c["runner"]
    c["merges"] = []
    c["effect_armed"] = False

    def runner(command):
        command = list(command)
        if command[:3] == ["gh", "pr", "merge"]:
            c["merges"].append(command)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if command[:3] == ["gh", "pr", "view"] and "mergeCommit" in command[command.index("--json") + 1]:
            return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(dict(
                number=c["pr"], state="MERGED", mergeCommit=dict(oid="f" * 40),
                headRefOid=c["pair"]["new_head"], headRefName=c["task"], baseRefName="main")))
        return original(command)
    c["runner"] = runner


def _run_actual_carry_apply(c, out_dir, route):
    fresh = _run_live_factory(c)
    assert fresh["carry"] is True, fresh
    common = dict(events_path=c["events_path"], expected_head=c["pair"]["new_head"],
                  expected_base_sha=c["pair"]["new_base"], consensus_proposal_id=c["task"],
                  repo=c["repo"], from_agent="codex-lead-1", bridge_task_id=c["task"],
                  apply=True, carry_forward=c["request"], runner=c["runner"],
                  accepted_queue_checker=lambda **_: dict(ok=True, complete=c["queue"]))
    if route == "wrapper":
        from tools.merge_with_bridge_receipt import merge_with_bridge_receipt
        return merge_with_bridge_receipt(pr_number=c["pr"], out_dir=out_dir, **common)
    from tools.idle_consensus_auto_merge import evaluate_auto_merge_gate
    return evaluate_auto_merge_gate(
        pr_status=dict(fresh["_fresh_snapshot"], receipt_verified=True),
        require_bridge_consensus=True,
        artifact_writer=lambda: _write_live_merge_bundle(c, out_dir), **common)


@pytest.mark.parametrize("route", ["wrapper", "idle"])
def test_actual_carry_apply_consumes_magma_before_exact_head_merge(live_factory, tmp_path, route):
    c = live_factory
    _install_fake_carry_merge(c)
    result = _run_actual_carry_apply(c, tmp_path / "apply", route)
    assert result["ok"] is True, result
    assert len(c["merges"]) == 1
    command = c["merges"][0]
    assert "--match-head-commit=" + c["pair"]["new_head"] in command
    assert command[command.index("--repo") + 1] == "github.com/" + c["repo"]
    from pathlib import Path
    manifest = Path(result["receipt_bundle_path"])
    payload = json.loads((manifest.parent / "payload-001-merge.json").read_bytes())
    assert payload["artifact_version"] == "wd.bridge_consensus_merge_receipt.v1"
    assert result["carry_apply_verification"]["ok"] is True
    assert result["carry_apply_verification"]["target_base_observed"] == c["pair"]["new_base"]
    assert result["carry_apply_verification"]["atomic_base_guard"] is False


@pytest.mark.parametrize("route", ["wrapper", "idle"])
@pytest.mark.parametrize("fault", ["old-veto", "new-veto", "queue", "ci_failed", "ci_old", "paths", "receipt", "late-base"])
def test_actual_carry_apply_refuses_changed_authority_before_effect(live_factory, tmp_path, monkeypatch, route, fault):
    from pathlib import Path
    import tools.write_bridge_consensus_merge_receipt as writer
    import tools.merge_with_bridge_receipt as wrapper
    c = live_factory
    _install_fake_carry_merge(c)
    original_writer = writer.write_bridge_consensus_merge_receipt
    original_verify = writer.verify_carry_forward_merge_receipt

    def changed_writer(**kwargs):
        result = original_writer(**kwargs)
        if fault.endswith("veto"):
            event = copy.deepcopy(c["events"][-1])
            event.update(type="finding", status="changes_requested")
            event["payload"] = dict(pr=c["pr"], head=c["pair"]["old_head" if fault == "old-veto" else "new_head"])
            c["events"].append(event)
            c["events_path"].write_text("\n".join(json.dumps(e) for e in c["events"]), encoding="utf-8")
        elif fault == "queue":
            c["queue"] = False
        elif fault == "receipt":
            (Path(result["receipt_bundle_path"]).parent / "payload-001-merge.json").write_text("{}", encoding="utf-8")
        elif fault != "late-base":
            c["fault"] = fault
        return result

    def changed_verify(**kwargs):
        result = original_verify(**kwargs)
        if fault == "late-base" and result["ok"]:
            c["late_base"] = True
        return result
    monkeypatch.setattr(writer, "write_bridge_consensus_merge_receipt", changed_writer)
    monkeypatch.setattr(wrapper, "write_bridge_consensus_merge_receipt", changed_writer)
    monkeypatch.setattr(writer, "verify_carry_forward_merge_receipt", changed_verify)
    runner = c["runner"]

    def late_runner(command):
        result = runner(command)
        if c.get("late_base") and list(command)[:2] == ["gh", "api"] and "/git/ref/heads/" in command[4]:
            data = json.loads(result.stdout)
            data["object"]["sha"] = "e" * 40
            result.stdout = json.dumps(data)
        return result
    c["runner"] = late_runner
    result = _run_actual_carry_apply(c, tmp_path / "changed", route)
    assert result["ok"] is False, result
    assert not c["merges"], result


def _scan_carry_references(root, tracked=None):
    """Inventory literal references in all tracked code/launchers; no parse skips.

    This is a reviewed-reference tripwire, not proof against arbitrary dynamic
    Python construction. Production still requires the live factory/consumer.
    """
    import ast
    module = "tools.bridge_approval_carry_forward"
    targets = {"evaluate_approval_carry_forward", "verify_carry_forward_receipt",
               "derive_live_carry_forward", "verify_live_carry_forward_receipt"}
    if tracked is None:
        tracked = _api()._git_reader(root)("ls-files", "-z", "--", "*.py", "*.ps1", "*.cmd", "*.json").decode("utf-8").split("\0")
    found = set()
    for relative in tracked:
        if not relative or relative.startswith("tests/"):
            continue  # Test-owned attack construction is intentionally separate.
        path = root / relative
        source = path.read_text(encoding="utf-8-sig")
        if path.suffix != ".py":
            for target in targets | {module}:
                if target in source:
                    found.add((relative, "<text>", target))
            continue
        tree = ast.parse(source)
        aliases = {node.asname or node.name: node.name for node in ast.walk(tree)
                   if isinstance(node, ast.alias) and node.name in targets}

        class References(ast.NodeVisitor):
            owner = "<module>"

            def record(self, target):
                if target in targets or target == module:
                    found.add((relative, self.owner, target))

            def visit_FunctionDef(self, node):
                old, self.owner = self.owner, node.name
                self.generic_visit(node)
                self.owner = old

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Name(self, node):
                self.record(aliases.get(node.id, node.id))

            def visit_Attribute(self, node):
                self.record(node.attr)
                self.generic_visit(node)

            def visit_Constant(self, node):
                if type(node.value) is str:
                    self.record(node.value)

            def visit_Import(self, node):
                for alias in node.names:
                    self.record(alias.name)

            def visit_ImportFrom(self, node):
                if node.module == module or (node.module == "tools" and any(
                        alias.name == "bridge_approval_carry_forward" for alias in node.names)):
                    self.record(module)
                for alias in node.names:
                    self.record(alias.name)

        References().visit(tree)
    return found


def test_pure_carry_evidence_callers_are_reviewed():
    """Any new production use must undergo an origin/authority review."""
    allowed = {
        ("ops/windows/reboot/bridge-code-files.json", "<text>", "tools.bridge_approval_carry_forward"),
        ("tools/bridge_approval_carry_forward.py", "verify_carry_forward_receipt", "evaluate_approval_carry_forward"),
        ("tools/bridge_approval_carry_forward.py", "derive_live_carry_forward", "evaluate_approval_carry_forward"),
        ("tools/bridge_approval_carry_forward.py", "verify_live_carry_forward_receipt", "verify_carry_forward_receipt"),
        ("tools/bridge_approval_carry_forward.py", "verify_live_carry_forward_receipt", "derive_live_carry_forward"),
        ("tools/idle_consensus_auto_merge.py", "build_parser", "tools.bridge_approval_carry_forward"),
        ("tools/idle_consensus_auto_merge.py", "main", "tools.bridge_approval_carry_forward"),
        ("tools/idle_consensus_auto_merge.py", "evaluate_auto_merge_gate", "tools.bridge_approval_carry_forward"),
        ("tools/idle_consensus_auto_merge.py", "evaluate_auto_merge_gate", "derive_live_carry_forward"),
        ("tools/idle_consensus_auto_merge.py", "evaluate_auto_merge_gate", "verify_carry_forward_receipt"),
        ("tools/merge_with_bridge_receipt.py", "build_parser", "tools.bridge_approval_carry_forward"),
        ("tools/merge_with_bridge_receipt.py", "main", "tools.bridge_approval_carry_forward"),
        ("tools/write_bridge_consensus_merge_receipt.py", "build_parser", "tools.bridge_approval_carry_forward"),
        ("tools/write_bridge_consensus_merge_receipt.py", "main", "tools.bridge_approval_carry_forward"),
        ("tools/write_bridge_consensus_merge_receipt.py", "write_bridge_consensus_merge_receipt", "tools.bridge_approval_carry_forward"),
        ("tools/write_bridge_consensus_merge_receipt.py", "write_bridge_consensus_merge_receipt", "derive_live_carry_forward"),
        ("tools/write_bridge_consensus_merge_receipt.py", "_receipt_payload", "tools.bridge_approval_carry_forward"),
        ("tools/write_bridge_consensus_merge_receipt.py", "_historical_carry_verdicts", "tools.bridge_approval_carry_forward"),
        ("tools/write_bridge_consensus_merge_receipt.py", "verify_carry_forward_merge_receipt", "tools.bridge_approval_carry_forward"),
        ("tools/write_bridge_consensus_merge_receipt.py", "verify_carry_forward_merge_receipt", "verify_live_carry_forward_receipt"),
    }
    found = _scan_carry_references(Path(__file__).resolve().parents[2])
    assert found == allowed, (found - allowed, allowed - found)


@pytest.mark.parametrize("relative,source,target", [
    ("tools/probe.py", "module.verify_carry_forward_receipt({}, {})", "verify_carry_forward_receipt"),
    ("tools/probe.py", "callback = module.verify_carry_forward_receipt", "verify_carry_forward_receipt"),
    ("tools/probe.py", "from tools.bridge_approval_carry_forward import verify_carry_forward_receipt as f\ncallback = f", "verify_carry_forward_receipt"),
    ("tools/probe.py", "getattr(module, 'verify_carry_forward_receipt')", "verify_carry_forward_receipt"),
    ("tools/probe.py", "module.__dict__['derive_live_carry_forward']", "derive_live_carry_forward"),
    ("tools/probe.py", "functools.partial(module.verify_live_carry_forward_receipt, {})", "verify_live_carry_forward_receipt"),
    ("root_probe.py", "verify_carry_forward_receipt({}, {})", "verify_carry_forward_receipt"),
    (".agent-bridge/bin/probe.py", "verify_carry_forward_receipt({}, {})", "verify_carry_forward_receipt"),
    ("scripts/probe.py", "evaluate_approval_carry_forward({})", "evaluate_approval_carry_forward"),
    ("tools/probe.py", "import tools.bridge_approval_carry_forward as module", "tools.bridge_approval_carry_forward"),
    ("tools/probe.py", "importlib.import_module('tools.bridge_approval_carry_forward')", "tools.bridge_approval_carry_forward"),
    ("tools/probe.ps1", 'python -c "verify_carry_forward_receipt({}, {})"', "verify_carry_forward_receipt"),
    ("tools/probe.cmd", 'python -c "evaluate_approval_carry_forward({})"', "evaluate_approval_carry_forward"),
    ("ops/probe.json", '{"handler":"tools.bridge_approval_carry_forward"}', "tools.bridge_approval_carry_forward"),
])
def test_carry_reference_guard_covers_tracked_aliases_and_launchers(tmp_path, relative, source, target):
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    found = _scan_carry_references(tmp_path, tracked=[relative])
    assert any(row[0] == relative and row[2] == target for row in found), found


def test_carry_reference_guard_uses_git_inventory_and_ignores_untracked(history):
    root, git, _ = history
    (root / "tools").mkdir()
    for name in ("tracked.py", "untracked.py"):
        (root / "tools" / name).write_text("verify_carry_forward_receipt({}, {})", encoding="utf-8")
    git("add", "tools/tracked.py")
    git("commit", "--quiet", "-m", "tracked guard fixture")
    found = _scan_carry_references(root)
    assert found == {("tools/tracked.py", "<module>", "verify_carry_forward_receipt")}


@pytest.mark.parametrize("raw,error", [(b"\xff", UnicodeError), (b"def broken(:", SyntaxError)])
def test_carry_reference_guard_never_skips_unreadable_python(tmp_path, raw, error):
    path = tmp_path / "tools/probe.py"
    path.parent.mkdir()
    path.write_bytes(raw)
    with pytest.raises(error):
        _scan_carry_references(tmp_path, tracked=["tools/probe.py"])


@pytest.mark.parametrize("key", ["pr", "pr_number", "pull_request", "pull_request_number"])
@pytest.mark.parametrize("value", [True, 1.0])
def test_carry_pr_type_hardening_does_not_drop_ambiguous_veto(approval_evidence, key, value):
    e = approval_evidence
    e["approved"]["pr_number"] = e["proposed"]["pr_number"] = 1
    for event in e["events"]:
        event["payload"]["pr"] = 1
    veto = _veto(e)
    veto["payload"][key] = value
    e["events"].append(veto)
    assert _api().evaluate_approval_carry_forward(e)["carry"] is False


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
                                  "author_issuer", "wrong_binding_task", "body_wrong_basis", "contradicts_direct",
                                  "typed_body", "typed_direct"])
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
        elif fault == "typed_body":
            body["review_basis"]["pr_number"] = float(c["pr"])
        else:
            conflicting = (dict(c["basis"], pr_number=float(c["pr"])) if fault == "typed_direct"
                           else dict(c["basis"], diff_sha256="0" * 64))
            c["events"][3]["payload"].update(review_basis=conflicting,
                review_basis_sha256=_event_hash(c["basis"] if fault == "typed_direct" else conflicting))
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


def _receipt_material(context):
    return _api().carry_forward_receipt_material(_run_live_factory(context))


def _verify_live_material(context, material):
    context["events_path"].write_text("\n".join(json.dumps(e) for e in context["events"]), encoding="utf-8")
    return _api().verify_live_carry_forward_receipt(
        material=material, request=context["request"], events_path=context["events_path"],
        repo=context["repo"], pr_number=context["pr"], task_id=context["task"],
        expected_head=context["pair"]["new_head"], expected_base_sha=context["pair"]["new_base"],
        runner=context["runner"], accepted_queue_checker=lambda **_: dict(ok=True, complete=context["queue"]))


@pytest.mark.parametrize("append", [False, True])
def test_semantic_carry_material_roundtrip_and_harmless_append(live_factory, append):
    c = live_factory
    material = _receipt_material(c)
    assert set(material) == {"schema", "approval_receipt", "review_origin", "ci_evidence"}
    assert material["approval_receipt"]["proposed_head"] == c["pair"]["new_head"]
    if append:
        c["events"].append(dict(agent="unrelated", type="heartbeat", task_id="other"))
    assert _verify_live_material(c, material)["ok"] is True


@pytest.mark.parametrize("fault", ["old_head", "new_head", "old_base", "new_base", "patch", "raw_diff",
                                  "approval_ref", "short_prefix", "origin", "ci_head", "ci_app", "extra_authority",
                                  "typed_flag"])
def test_semantic_carry_material_resealed_changes_refuse(live_factory, fault):
    c = live_factory
    m = _receipt_material(c)
    proof = m["approval_receipt"]
    if fault in {"old_head", "new_head", "old_base", "new_base"}:
        field = {"old_head":"approved_head", "new_head":"proposed_head",
                 "old_base":"approved_base_sha", "new_base":"proposed_base_sha"}[fault]
        proof[field] = "0" * 40
    elif fault == "patch":
        proof["content_identity"]["new_patch_id"] = "0" * 40
    elif fault == "raw_diff":
        proof["content_identity"]["new_diff_sha256"] = "0" * 64
    elif fault == "approval_ref":
        proof["carried"][0]["event_sha256"] = "0" * 64
    elif fault == "short_prefix":
        proof["accepted_event_count"] = 1
        proof["accepted_events_sha256"] = _event_hash(c["events"][:1])
    elif fault == "origin":
        m["review_origin"]["review_basis"]["base_sha"] = "0" * 40
    elif fault == "ci_head":
        m["ci_evidence"]["head_sha"] = "0" * 40
    elif fault == "ci_app":
        m["ci_evidence"]["required"][0]["app_id"] = 123
    elif fault == "typed_flag":
        proof["veto_scan_complete"] = 1
    else:
        m["operator_signature"] = dict(approved=True)
    assert _verify_live_material(c, m)["ok"] is False


@pytest.mark.parametrize("fault", ["old_veto", "new_veto", "ci_failed", "ci_old", "paths", "queue"])
def test_semantic_carry_material_checks_live_changes(live_factory, fault):
    c = live_factory
    material = _receipt_material(c)
    if fault.endswith("veto"):
        ev = copy.deepcopy(c["events"][3])
        ev.update(type="finding", status="changes_requested", message="late objection")
        head = c["pair"]["old_head" if fault == "old_veto" else "new_head"]
        ev["payload"] = dict(head=head, exact_head=head)
        c["events"].append(ev)
    elif fault == "queue":
        c["queue"] = False
    else:
        c["fault"] = fault
    assert _verify_live_material(c, material)["ok"] is False


def _write_live_merge_bundle(c, out_dir):
    from tools.write_bridge_consensus_merge_receipt import write_bridge_consensus_merge_receipt
    fresh = _run_live_factory(c)
    snapshot = dict(fresh["_fresh_snapshot"], receipt_verified=True)
    # Spoofed caller metadata must not replace the writer's own live snapshot.
    snapshot["diff_text"] = "caller stale metadata"
    return write_bridge_consensus_merge_receipt(
        pr_status=snapshot, events_path=c["events_path"], out_dir=out_dir,
        expected_head=c["pair"]["new_head"], expected_base_sha=c["pair"]["new_base"],
        consensus_proposal_id=c["task"], repo=c["repo"], from_agent="codex-lead-1", bridge_task_id=c["task"],
        carry_forward=c["request"], runner=c["runner"],
        accepted_queue_checker=lambda **_: dict(ok=True, complete=c["queue"]))


def _verify_live_merge_bundle(c, manifest):
    from tools.write_bridge_consensus_merge_receipt import verify_carry_forward_merge_receipt
    c["events_path"].write_text("\n".join(json.dumps(e) for e in c["events"]), encoding="utf-8")
    return verify_carry_forward_merge_receipt(
        manifest_path=manifest, carry_forward=c["request"], events_path=c["events_path"],
        repo=c["repo"], pr_number=c["pr"], task_id=c["task"], expected_head=c["pair"]["new_head"],
        expected_base_sha=c["pair"]["new_base"], runner=c["runner"],
        accepted_queue_checker=lambda **_: dict(ok=True, complete=c["queue"]))


def test_actual_magma_writer_binds_carry_and_semantic_consumer(live_factory, tmp_path):
    from pathlib import Path
    from tools.verify_magma_receipt import verify_manifest
    from waggledance.core.magma.canonical import sha256_digest
    c = live_factory
    result = _write_live_merge_bundle(c, tmp_path / "magma")
    manifest = Path(result["receipt_bundle_path"])
    assert verify_manifest(manifest)["ok"] is True
    payload = json.loads((manifest.parent / "payload-001-merge.json").read_bytes())
    assert payload["artifact_version"] == "wd.bridge_consensus_merge_receipt.v1"
    assert payload["carry_forward"]["approval_receipt"]["approved_head"] == c["pair"]["old_head"]
    assert payload["carry_forward"]["approval_receipt"]["proposed_head"] == c["pair"]["new_head"]
    assert payload["diff_digest"] == sha256_digest(f"diff --git a/{c['path']} b/{c['path']}\n")
    assert result["carry_semantic_verification"]["ok"] is True
    assert _verify_live_merge_bundle(c, manifest)["ok"] is True
    c["events"].append(dict(agent="unrelated", type="heartbeat", task_id="other"))
    assert _verify_live_merge_bundle(c, manifest)["ok"] is True


@pytest.mark.parametrize("fault", ["head", "base", "repo", "task", "pr", "proof", "vote", "paths", "missing",
                                  "extra_payload_authority", "rco_gate", "bridge_flag", "type_confused_index"])
def test_actual_magma_consumer_rejects_resealed_semantic_tamper(live_factory, tmp_path, fault):
    from pathlib import Path
    from datetime import datetime, timezone
    import tools.write_bridge_consensus_merge_receipt as writer
    from tools.verify_magma_receipt import verify_manifest
    c = live_factory
    first = _write_live_merge_bundle(c, tmp_path / "original")
    original = Path(first["receipt_bundle_path"])
    payload = json.loads((original.parent / "payload-001-merge.json").read_bytes())
    if fault in {"head", "base", "repo", "task", "pr"}:
        field = {"head":"head_sha", "base":"base_sha", "repo":"repo", "task":"task_id", "pr":"pr_number"}[fault]
        payload[field] = 1 if fault == "pr" else "0" * 40
    elif fault == "proof":
        payload["carry_forward"]["approval_receipt"]["content_identity"]["new_diff_sha256"] = "0" * 64
    elif fault == "vote":
        payload["bridge_consensus"]["identities"]["rco"]["agent"] = "codex-lead-1"
    elif fault == "paths":
        payload["changed_paths"] = []
    elif fault == "extra_payload_authority":
        payload["operator_signature"] = dict(approved=True, head=c["pair"]["new_head"])
    elif fault == "rco_gate":
        payload["rco_pass_gate"]["satisfying_rco_agent"] = "codex-lead-1"
    elif fault == "bridge_flag":
        payload["bridge_consensus"]["ok"] = False
    elif fault == "type_confused_index":
        assert payload["bridge_consensus"]["identities"]["build_lead"]["approval_index"] == 1
        payload["bridge_consensus"]["identities"]["build_lead"]["approval_index"] = True
    else:
        payload.pop("carry_forward")
    bundle = writer._write_receipt_bundle(payload=payload, bridge_consensus=payload["bridge_consensus"],
                                           out_dir=tmp_path / "resealed", now_utc=datetime.now(timezone.utc))
    manifest = Path(bundle["manifest"])
    assert verify_manifest(manifest)["ok"] is True  # integrity alone cannot establish authorization
    assert _verify_live_merge_bundle(c, manifest)["ok"] is False


@pytest.mark.parametrize("fault", ["late_veto", "ci_failed", "queue"])
def test_actual_magma_consumer_refuses_stale_authority(live_factory, tmp_path, fault):
    from pathlib import Path
    c = live_factory
    result = _write_live_merge_bundle(c, tmp_path / "magma")
    if fault == "late_veto":
        ev = copy.deepcopy(c["events"][3])
        ev.update(type="finding", status="changes_requested", message="new objection")
        c["events"].append(ev)
    elif fault == "queue":
        c["queue"] = False
    else:
        c["fault"] = fault
    assert _verify_live_merge_bundle(c, Path(result["receipt_bundle_path"]))["ok"] is False


@pytest.mark.parametrize("layout", ["traversal", "multiple_entries"])
def test_actual_magma_consumer_rejects_manifest_layout_changes(live_factory, tmp_path, layout):
    from pathlib import Path
    c = live_factory
    result = _write_live_merge_bundle(c, tmp_path / "magma")
    path = Path(result["receipt_bundle_path"])
    manifest = json.loads(path.read_bytes())
    if layout == "traversal":
        manifest["entries"][0]["payload"] = "../outside.json"
    else:
        manifest["entries"].append(dict(manifest["entries"][0]))
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert _verify_live_merge_bundle(c, path)["ok"] is False


@pytest.mark.parametrize("form", ["rco1_heartbeat", "rco1_message", "rco2_heartbeat", "rco2_message"])
def test_actual_magma_scoped_rco_chatter_does_not_expire_receipt(live_factory, tmp_path, form):
    from pathlib import Path
    from waggledance.core.bridge_identity_registry import load_bridge_identity_registry
    c = live_factory
    result = _write_live_merge_bundle(c, tmp_path / "magma")
    event = copy.deepcopy(c["events"][3])
    agent = "claude-rco-1" if form.startswith("rco1") else "claude-rco-2"
    event.update(agent=agent, agent_uuid=load_bridge_identity_registry()[agent],
                 type=form.split("_", 1)[1], status="in_progress", message="ordinary progress, no review change")
    c["events"].append(event)
    assert _verify_live_merge_bundle(c, Path(result["receipt_bundle_path"]))["ok"] is True


@pytest.mark.parametrize("append_kind", ["chatter", "veto"])
def test_actual_magma_emission_append_preserves_chatter_but_refuses_veto(live_factory, tmp_path, monkeypatch, append_kind):
    import tools.write_bridge_consensus_merge_receipt as writer
    c = live_factory
    original = writer._receipt_payload
    def append_after_plan(**kwargs):
        payload = original(**kwargs)
        event = copy.deepcopy(c["events"][3])
        event.update(type="message" if append_kind == "chatter" else "finding",
                     status="in_progress" if append_kind == "chatter" else "changes_requested",
                     message="progress" if append_kind == "chatter" else "real objection")
        c["events"].append(event)
        c["events_path"].write_text("\n".join(json.dumps(e) for e in c["events"]), encoding="utf-8")
        return payload
    monkeypatch.setattr(writer, "_receipt_payload", append_after_plan)
    if append_kind == "veto":
        with pytest.raises(writer.BridgeConsensusMergeReceiptError):
            _write_live_merge_bundle(c, tmp_path / "magma")
    else:
        assert _write_live_merge_bundle(c, tmp_path / "magma")["carry_semantic_verification"]["ok"] is True


_OPTIONAL_GIT_PIN_ABSENT = object()


def _set_optional_git_pin(pinned_fleet, monkeypatch, value):
    """Re-seal only test-owned fleet/manifest bytes; no installed pin is used."""
    import hashlib
    import json

    root, executable = pinned_fleet
    fleet = {"schema_version": 2, "git_executable": str(executable)}
    if value is not _OPTIONAL_GIT_PIN_ABSENT:
        fleet["git_sha256"] = value
    raw = json.dumps(fleet).encode()
    (root / "wd-fleet.json").write_bytes(raw)
    manifest = json.dumps({"source_commit": root.name, "files": {
        "wd-fleet.json": hashlib.sha256(raw).hexdigest()}}).encode()
    (root / "deployment-manifest.json").write_bytes(manifest)
    monkeypatch.setenv("WD_REBOOT_EXPECTED_MANIFEST_HASH", hashlib.sha256(manifest).hexdigest())
    return executable


def _synthetic_git_reader(api, tmp_path, monkeypatch):
    """Only the three reader preflight replies; no Git process can run."""
    from types import SimpleNamespace

    calls = []
    def run(argv, **kwargs):
        calls.append(list(argv))
        if argv[-2:] == ["rev-parse", "--show-toplevel"]:
            out = str(tmp_path).encode() + b"\n"
        elif argv[-2:] == ["rev-parse", "--is-shallow-repository"]:
            out = b"false\n"
        elif argv[-3:] == ["rev-parse", "--git-path", "info/grafts"]:
            out = str(tmp_path / "no-grafts").encode() + b"\n"
        else:
            pytest.fail(f"unexpected synthetic Git read: {argv}")
        return SimpleNamespace(returncode=0, stdout=out, stderr=b"")
    monkeypatch.setattr(api.subprocess, "run", run)
    return calls


@pytest.mark.parametrize("form", ["absent", "matching-lower", "matching-upper"])
def test_optional_git_pin_preserves_legacy_and_matching_authority(pinned_fleet, tmp_path, monkeypatch, form):
    import hashlib

    executable = pinned_fleet[1]
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    value = (_OPTIONAL_GIT_PIN_ABSENT if form == "absent"
             else digest.upper() if form == "matching-upper" else digest)
    _set_optional_git_pin(pinned_fleet, monkeypatch, value)
    api = _api()
    calls = _synthetic_git_reader(api, tmp_path, monkeypatch)
    reader = api._git_reader(tmp_path)
    assert len(calls) == 3
    path, measured, authority = reader.authority
    assert path == executable and measured["sha256"] == digest
    assert set(authority) == {"manifest_sha256", "fleet_sha256", "generation"}


@pytest.mark.parametrize("value", [
    pytest.param(None, id="null"),
    pytest.param(True, id="bool-true"),
    pytest.param(False, id="bool-false"),
    pytest.param(1, id="integer"),
    pytest.param(1.0, id="float"),
    pytest.param([], id="array"),
    pytest.param({}, id="object"),
    pytest.param("", id="empty"),
    pytest.param(" " * 64, id="whitespace"),
    pytest.param("g" * 64, id="non-hex"),
    pytest.param("f" * 63, id="short"),
    pytest.param("f" * 65, id="long"),
    pytest.param("0" * 64, id="mismatched-lower"),
    pytest.param("F" * 64, id="mismatched-upper"),
])
def test_optional_git_pin_malformed_or_mismatch_refuses_before_git(pinned_fleet, tmp_path, monkeypatch, value):
    _set_optional_git_pin(pinned_fleet, monkeypatch, value)
    api = _api()
    calls = _synthetic_git_reader(api, tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="git_sha256|Git.*digest|Git.*pin"):
        api._git_reader(tmp_path)
    assert calls == [], "a malformed or mismatched declared pin must stop before Git"


@pytest.mark.parametrize("changed", [
    pytest.param(None, id="became-null"),
    pytest.param("0" * 64, id="became-mismatch"),
    pytest.param("invalid", id="became-malformed"),
])
def test_optional_git_pin_recheck_rejects_changed_declaration(pinned_fleet, monkeypatch, changed):
    import hashlib

    digest = hashlib.sha256(pinned_fleet[1].read_bytes()).hexdigest()
    _set_optional_git_pin(pinned_fleet, monkeypatch, digest)
    api = _api()
    assert api._fleet_git_authority()[1]["sha256"] == digest
    _set_optional_git_pin(pinned_fleet, monkeypatch, changed)
    with pytest.raises(ValueError, match="git_sha256|Git.*digest|Git.*pin"):
        api._fleet_git_authority()


def test_optional_git_pin_matching_mixed_case(pinned_fleet, tmp_path, monkeypatch):
    import hashlib

    digest = hashlib.sha256(pinned_fleet[1].read_bytes()).hexdigest()
    mixed = "".join(c.upper() if i % 2 else c for i, c in enumerate(digest))
    _set_optional_git_pin(pinned_fleet, monkeypatch, mixed)
    api = _api()
    calls = _synthetic_git_reader(api, tmp_path, monkeypatch)
    assert api._git_reader(tmp_path).authority[1]["sha256"] == digest
    assert len(calls) == 3


def test_optional_git_pin_padded_value_refuses_before_git(pinned_fleet, tmp_path, monkeypatch):
    import hashlib

    digest = hashlib.sha256(pinned_fleet[1].read_bytes()).hexdigest()
    _set_optional_git_pin(pinned_fleet, monkeypatch, " " + digest + " ")
    api = _api()
    calls = _synthetic_git_reader(api, tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="git_sha256|Git.*digest|Git.*pin"):
        api._git_reader(tmp_path)
    assert calls == []


@pytest.mark.parametrize("key", ["git_sha256", "git_executable"])
def test_optional_git_pin_duplicate_authority_key_refuses(pinned_fleet, tmp_path, monkeypatch, key):
    import hashlib
    import json

    root, executable = pinned_fleet
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    _set_optional_git_pin(pinned_fleet, monkeypatch, digest)
    value = digest if key == "git_sha256" else str(executable)
    raw = (root / "wd-fleet.json").read_bytes()[:-1] + (", " + json.dumps(key) + ": " + json.dumps(value) + "}").encode()
    (root / "wd-fleet.json").write_bytes(raw)
    manifest = json.dumps({"source_commit": root.name, "files": {
        "wd-fleet.json": hashlib.sha256(raw).hexdigest()}}).encode()
    (root / "deployment-manifest.json").write_bytes(manifest)
    monkeypatch.setenv("WD_REBOOT_EXPECTED_MANIFEST_HASH", hashlib.sha256(manifest).hexdigest())
    api = _api()
    calls = _synthetic_git_reader(api, tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        api._git_reader(tmp_path)
    assert calls == []


def test_optional_git_pin_environment_cannot_override_bad_fleet_pin(pinned_fleet, tmp_path, monkeypatch):
    import hashlib

    digest = hashlib.sha256(pinned_fleet[1].read_bytes()).hexdigest()
    _set_optional_git_pin(pinned_fleet, monkeypatch, "0" * 64)
    monkeypatch.setenv("WD_GIT_SHA256", digest)
    monkeypatch.setenv("GIT_SHA256", digest)
    api = _api()
    calls = _synthetic_git_reader(api, tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="git_sha256|Git.*digest|Git.*pin"):
        api._git_reader(tmp_path)
    assert calls == []


def test_optional_git_pin_launcher_mutation_after_success_refuses(pinned_fleet, tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace

    root, _ = pinned_fleet
    executable = tmp_path / "test-owned-git.exe"
    executable.write_bytes(b"test-owned launcher bytes")
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    _set_optional_git_pin((root, executable), monkeypatch, digest)
    api = _api()
    calls = []
    def run(argv, **kwargs):
        calls.append(list(argv))
        if len(calls) == 1:
            return SimpleNamespace(returncode=0, stdout=str(tmp_path).encode() + b"\n", stderr=b"")
        executable.write_bytes(b"replaced after the first successful synthetic command")
        return SimpleNamespace(returncode=0, stdout=b"false\n", stderr=b"")
    monkeypatch.setattr(api.subprocess, "run", run)
    with pytest.raises(ValueError, match="Git executable identity changed during command"):
        api._git_reader(tmp_path)
    assert len(calls) == 2


_EFFECT_CLASS_CASES = [
    ('default_get', ['gh', 'api', 'repos/example/repo/issues/1/comments'], True),
    ('long_get', ['gh', 'api', '--method', 'GET', 'repos/example/repo/issues/1/comments'], True),
    ('short_get', ['gh', 'api', '-X', 'GET', 'repos/example/repo/issues/1/comments'], True),
    ('long_get_query', ['gh', 'api', '--method', 'GET', 'repos/example/repo/issues/1/comments', '-f', 'page=1'], True),
    ('short_get_typed_query', ['gh', 'api', '-X', 'GET', 'repos/example/repo/issues/1/comments', '-F', 'page=1'], True),
    ('explicit_post', ['gh', 'api', '--method', 'POST', 'repos/example/repo/issues/1/comments'], False),
    ('short_post', ['gh', 'api', '-X', 'POST', 'repos/example/repo/issues/1/comments'], False),
    ('short_patch', ['gh', 'api', '-X', 'PATCH', 'repos/example/repo/issues/1/comments'], False),
    ('short_delete', ['gh', 'api', '-X', 'DELETE', 'repos/example/repo/issues/1/comments'], False),
    ('long_equal_post', ['gh', 'api', '--method=POST', 'repos/example/repo/issues/1/comments'], False),
    ('attached_short_post', ['gh', 'api', '-XPOST', 'repos/example/repo/issues/1/comments'], False),
    ('equal_short_post', ['gh', 'api', '-X=POST', 'repos/example/repo/issues/1/comments'], False),
    ('raw_field_implicit_post', ['gh', 'api', 'repos/example/repo/issues/1/comments', '-f', 'body=value'], False),
    ('typed_field_implicit_post', ['gh', 'api', 'repos/example/repo/issues/1/comments', '-F', 'body=value'], False),
    ('long_field_implicit_post', ['gh', 'api', 'repos/example/repo/issues/1/comments', '--field', 'body=value'], False),
    ('long_raw_field_implicit_post', ['gh', 'api', 'repos/example/repo/issues/1/comments', '--raw-field', 'body=value'], False),
    ('equal_field_implicit_post', ['gh', 'api', 'repos/example/repo/issues/1/comments', '--field=body=value'], False),
    ('equal_raw_field_implicit_post', ['gh', 'api', 'repos/example/repo/issues/1/comments', '--raw-field=body=value'], False),
    ('attached_raw_implicit_post', ['gh', 'api', 'repos/example/repo/issues/1/comments', '-fbody=value'], False),
    ('attached_typed_implicit_post', ['gh', 'api', 'repos/example/repo/issues/1/comments', '-Fbody=value'], False),
    ('input_implicit_post', ['gh', 'api', 'repos/example/repo/issues/1/comments', '--input', '-'], False),
    ('equal_input_implicit_post', ['gh', 'api', 'repos/example/repo/issues/1/comments', '--input=-'], False),
    ('explicit_get_input', ['gh', 'api', '--method', 'GET', 'repos/example/repo/issues/1/comments', '--input', '-'], True),
    ('ambiguous_methods', ['gh', 'api', '--method', 'GET', '-X', 'POST', 'repos/example/repo/issues/1/comments'], False),
    ('missing_method', ['gh', 'api', 'repos/example/repo/issues/1/comments', '-X'], False),
    ('unknown_option', ['gh', 'api', 'repos/example/repo/issues/1/comments', '--unknown-body-option', 'value'], False),
]


@pytest.fixture(scope="module")
def recorded_gh_effect_classifiers():
    import ast
    forged = _boundary_corpus("test_consensus_carry_forward_origin_forgery")
    replay = _boundary_corpus("test_consensus_carry_forward_n4_replay")
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8-sig"))
    function = next(node for node in tree.body if isinstance(node,ast.FunctionDef)
                    and node.name == "test_exact_corpus_attack_reaches_real_magma_and_idle_apply")
    expression = next(node.test for node in ast.walk(function) if isinstance(node,ast.Assert)
                      and "forged._gh_effects(target.gh.calls)" in ast.unparse(node.test))
    code = compile(ast.Expression(expression),__file__,"eval")
    return {"origin": lambda calls: not forged._gh_effects(calls),
            "n4": lambda calls: not replay._effects(SimpleNamespace(calls=calls)),
            "own": lambda calls: bool(eval(code,{"forged":forged,"target":SimpleNamespace(gh=SimpleNamespace(calls=calls))}))}


@pytest.mark.parametrize("name,command,expected_read_only", _EFFECT_CLASS_CASES)
@pytest.mark.parametrize("detector", ["origin","n4","own"])
def test_recorded_gh_effect_class_is_fail_closed(name, command, expected_read_only, detector, recorded_gh_effect_classifiers):
    assert recorded_gh_effect_classifiers[detector]([command]) is expected_read_only, name
