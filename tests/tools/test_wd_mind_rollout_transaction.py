"""Real file and child-process proofs inside synthetic audit fixtures only."""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import types
import uuid

import pytest

from tools import wd_mind_rollout_policy as policy
from tools import wd_mind_rollout_transaction as transaction
from test_wd_mind_rollout_policy import git, sha


LANES = ("codex-lead-1", "codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5")
CALLER = r'''
import datetime, json, os, pathlib, sys, time
expected=json.loads(os.environ['WD_ROLLOUT_EXPECTED'])
receipt=pathlib.Path(os.environ['WD_ROLLOUT_RECEIPT'])
pointer=json.loads(pathlib.Path(os.environ['WD_ROLLOUT_POINTER']).read_text())
if pointer['bundle_commit']!=expected['bundle_commit'] or pointer['manifest_sha256']!=expected['manifest_sha256']:
    sys.exit(41)
behavior=json.loads(pathlib.Path(__file__).with_name('behavior.json').read_text())
kind=behavior.get(expected['phase'],'ok')
if kind=='timeout':
    time.sleep(0.4)
if kind=='missing':
    sys.exit(0)
if kind=='bad_nonce':
    expected['nonce']='wrong-nonce'
if kind=='bad_generation':
    expected['bundle_commit']='f'*40
if kind=='stale':
    timestamp='2020-01-01T00:00:00Z'
elif kind=='future':
    timestamp='2099-01-01T00:00:00Z'
else:
    timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat()
row={**expected,'schema':'wd.mind-rollout-startup.v1','receipt_observed_utc':timestamp,'read_status':'success'}
if kind=='wrong_kind':
    row['read_kind']='unauthorized-kind'
if kind=='raw_payload':
    row['raw_answer']='synthetic extra field must refuse'
text=json.dumps(row)
if kind=='duplicate':
    text=text[:-1]+',"nonce":"ambiguous"}'
receipt.write_text(text)
sys.exit(7 if kind=='bad_exit' else 0)
'''


@pytest.fixture
def setup(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init", "-q")
    git(source, "config", "user.name", "Synthetic fixture")
    git(source, "config", "user.email", "synthetic@example.invalid")
    (source / ".gitignore").write_text("ignore-empty\n.codex-audit/\n")
    (source / "sandbox/mind").mkdir(parents=True)
    (source / "sandbox/mind/reader.py").write_bytes(b"OLD\n")
    (source / "sandbox/mind/deleted.py").write_bytes(b"OLD DELETE\n")
    git(source, "add", ".")
    git(source, "commit", "-qm", "synthetic installed source")
    old = git(source, "rev-parse", "HEAD")
    (source / "sandbox/mind/reader.py").write_bytes(b"NEW\n")
    (source / "sandbox/mind/deleted.py").unlink()
    (source / "sandbox/mind/new.py").write_bytes(b"NEW FILE\n")
    git(source, "add", ".")
    git(source, "commit", "-qm", "synthetic candidate")
    head = git(source, "rev-parse", "HEAD")
    git(source, "update-ref", "refs/remotes/origin/main", head)
    tree = git(source, "rev-parse", "HEAD^{tree}")
    bundle = tmp_path / "bundles" / old
    code = bundle / "tools-bootstrap/tools"
    code.mkdir(parents=True)
    shutil.copyfile(policy.__file__, code / "wd_mind_rollout_policy.py")
    shutil.copyfile(transaction.__file__, code / "wd_mind_rollout_transaction.py")
    old_reader = bundle / "old-reader.py"
    old_reader.write_bytes(b"OLD\n")
    files = {p.relative_to(bundle).as_posix(): sha(p.read_bytes())
             for p in bundle.rglob("*") if p.is_file()}
    manifest = json.dumps({"files": files}, sort_keys=True).encode()
    (bundle / "deployment-manifest.json").write_bytes(manifest)
    package = "synthetic_rollout_" + uuid.uuid4().hex
    parent = types.ModuleType(package)
    parent.__path__ = [str(code)]
    sys.modules[package] = parent
    for leaf in ("wd_mind_rollout_policy", "wd_mind_rollout_transaction"):
        name = package + "." + leaf
        spec = importlib.util.spec_from_file_location(name, code / (leaf + ".py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    module = sys.modules[package + ".wd_mind_rollout_transaction"]
    machine = tmp_path / "machine"
    machine.mkdir()
    (machine / "reader.py").write_bytes(b"OLD\n")
    (machine / "deleted.py").write_bytes(b"OLD DELETE\n")
    pointer = {"schema": "wd.mind-rollout-pointer.v1", "bundle_commit": old,
               "manifest_sha256": sha(manifest)}
    (machine / "current.json").write_text(json.dumps(pointer))
    previous = {p.name: sha(p.read_bytes()) for p in machine.iterdir()}
    previous["new.py"] = None
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    caller = tmp_path / "caller.py"
    caller.write_text(CALLER)
    behavior = tmp_path / "behavior.json"
    behavior.write_text(json.dumps({"candidate": "ok", "previous": "ok"}))
    launches = {lane: {"argv": [sys.executable, "-B", str(caller)],
                       "files": {str(Path(sys.executable)): sha(Path(sys.executable).read_bytes()),
                                 str(caller): sha(caller.read_bytes())}}
                for lane in LANES}
    config = {
        "schema": "wd.mind-rollout-spec.v1", "transaction_id": uuid.uuid4().hex,
        "nonce": uuid.uuid4().hex, "installed_root": str(bundle),
        "installed_manifest_sha256": sha(manifest), "installed_commit": old,
        "source_root": str(source), "main_commit": head, "main_tree": tree,
        "members": ["sandbox/mind/reader.py", "sandbox/mind/deleted.py", "sandbox/mind/new.py"],
        "targets": {"sandbox/mind/reader.py": "reader.py", "sandbox/mind/deleted.py": "deleted.py",
                    "sandbox/mind/new.py": "new.py"},
        "machine_root": str(machine), "evidence_root": str(evidence), "pointer": "current.json",
        "previous_hashes": previous, "reader_path": "sandbox/mind/reader.py",
        "previous_reader_key": "old-reader.py",
        "lanes": {lane: {"agent_uuid": "uuid-" + lane, "candidate_session": "candidate-" + lane,
                          "candidate_run": "candidate-run-" + lane, "previous_session": "previous-" + lane,
                          "previous_run": "previous-run-" + lane, "read_kind": "status_projection"}
                  for lane in LANES},
        "launches": launches, "lane_timeout_seconds": 5.0,
        "attempt_timeout_seconds": 25.0, "recovery_timeout_seconds": 25.0,
    }
    original = {p.name: p.read_bytes() for p in machine.iterdir()}
    yield module, config, behavior, original
    for key in tuple(sys.modules):
        if key == package or key.startswith(package + "."):
            sys.modules.pop(key, None)


def journal(result):
    return json.loads(Path(result.journal).read_bytes())


def observed_files(config):
    return {p.name: p.read_bytes() for p in Path(config["machine_root"]).iterdir()
            if p.is_file() and p.name != ".wd-mind-rollout.lock"}


def test_success_really_activates_exact_bytes_and_checks_all_five_exits(setup):
    module, config, behavior, original = setup
    result = module.run_transaction(config)
    assert result.status == "COMMITTED" and result.pending == ()
    files = observed_files(config)
    assert files["reader.py"] == b"NEW\n" and files["new.py"] == b"NEW FILE\n"
    assert "deleted.py" not in files
    assert json.loads(files["current.json"])["bundle_commit"] == config["main_commit"]
    rows = journal(result)
    assert rows["states"][:3] == ["BACKUP_VERIFIED", "CANDIDATE_ACTIVE", "COMMITTED"]
    assert [r["lane"] for r in rows["checks"]] == sorted(LANES)
    assert all(r["exit_code"] == 0 and r["receipt_valid"] for r in rows["checks"])
    assert not (Path(config["machine_root"]) / ".wd-mind-rollout.lock").exists()


@pytest.mark.parametrize("broken", ["bad_exit", "missing", "bad_nonce", "bad_generation", "stale", "future",
                                    "wrong_kind", "raw_payload", "duplicate"])
def test_broken_candidate_really_restores_every_file_and_original_absence(setup, broken):
    module, config, behavior, original = setup
    behavior.write_text(json.dumps({"candidate": broken, "previous": "ok"}))
    result = module.run_transaction(config)
    assert result.status == "ROLLED_BACK" and result.pending == ()
    assert observed_files(config) == original
    rows = journal(result)
    assert "CANDIDATE_ACTIVE" in rows["states"] and "ROLLING_BACK" in rows["states"]
    assert rows["writes"][-1] == {"phase": "restore", "path": "current.json"}
    assert any(r["phase"] == "candidate" and not r["ok"] for r in rows["checks"])
    assert sum(r["phase"] == "previous" and r["ok"] for r in rows["checks"]) == 5
    assert not (Path(config["machine_root"]) / ".wd-mind-rollout.lock").exists()


def test_valid_receipt_never_overrides_real_nonzero_exit(setup):
    module, config, behavior, original = setup
    behavior.write_text(json.dumps({"candidate": "bad_exit", "previous": "ok"}))
    result = module.run_transaction(config)
    row = next(r for r in journal(result)["checks"] if r["phase"] == "candidate")
    assert row["exit_code"] == 7 and not row["ok"]
    assert observed_files(config) == original


def test_failed_previous_generation_validation_is_not_reported_as_recovered(setup):
    module, config, behavior, original = setup
    behavior.write_text(json.dumps({"candidate": "missing", "previous": "bad_exit"}))
    result = module.run_transaction(config)
    assert result.status == "ROLLBACK_FAILED_HOLD"
    assert observed_files(config) == original
    assert (Path(config["machine_root"]) / ".wd-mind-rollout.lock").exists()


def test_deadline_never_kills_or_rolls_files_back_under_a_live_caller(setup):
    module, config, behavior, original = setup
    behavior.write_text(json.dumps({"candidate": "timeout", "previous": "ok"}))
    config["lane_timeout_seconds"] = 0.1
    result = module.run_transaction(config)
    assert result.status == "HOLD_ACTIVE_CALLER" and len(result.pending) == 1
    assert json.loads(observed_files(config)["current.json"])["bundle_commit"] == config["main_commit"]
    assert "ROLLING_BACK" not in journal(result)["states"]
    # The exact caller finishes naturally; no orphan or process kill is left by this test.
    assert result.pending[0].wait(timeout=5) == 0
    assert (Path(config["machine_root"]) / ".wd-mind-rollout.lock").exists()


@pytest.mark.parametrize("change", ["old_hash", "missing_lane", "target_collision", "traversal", "bad_anchor",
                                    "wrong_main", "unknown_field", "overlap_roots", "untracked_member"])
def test_preflight_refusals_leave_machine_and_lock_unchanged(setup, change):
    module, config, behavior, original = setup
    if change == "old_hash":
        config["previous_hashes"]["reader.py"] = "0" * 64
    elif change == "missing_lane":
        config["lanes"].pop(LANES[0])
    elif change == "target_collision":
        config["targets"]["sandbox/mind/new.py"] = "Reader.py"
    elif change == "traversal":
        config["pointer"] = "../current.json"
    elif change == "bad_anchor":
        config["installed_manifest_sha256"] = "0" * 64
    elif change == "wrong_main":
        config["main_commit"] = config["installed_commit"]
    elif change == "unknown_field":
        config["raw_answer"] = "synthetic unexpected field"
    elif change == "overlap_roots":
        config["evidence_root"] = config["machine_root"]
    elif change == "untracked_member":
        config["members"].append("sandbox/mind/not-tracked.py")
    with pytest.raises(module.policy.Refused):
        module.run_transaction(config)
    assert observed_files(config) == original
    assert not (Path(config["machine_root"]) / ".wd-mind-rollout.lock").exists()


def test_worktree_copy_cannot_act_as_the_installed_transaction(setup):
    module, config, behavior, original = setup
    with pytest.raises(policy.Refused):
        transaction.run_transaction(config)
    assert observed_files(config) == original


def test_replayed_terminal_request_does_not_repeat_writes_or_processes(setup):
    module, config, behavior, original = setup
    first = module.run_transaction(config)
    before = Path(first.journal).read_bytes()
    receipts = sorted(p.name for p in Path(first.journal).parent.glob("*-receipt.json"))
    second = module.run_transaction(deepcopy(config))
    assert second.status == first.status == "COMMITTED" and second.pending == ()
    assert Path(first.journal).read_bytes() == before
    assert sorted(p.name for p in Path(first.journal).parent.glob("*-receipt.json")) == receipts


def test_backup_write_failure_occurs_before_any_machine_mutation(setup, monkeypatch):
    module, config, behavior, original = setup
    def fail(*args, **kwargs):
        raise OSError("synthetic backup failure")
    monkeypatch.setattr(module, "_write_backup", fail)
    result = module.run_transaction(config)
    assert result.status == "ROLLBACK_FAILED_HOLD"
    assert observed_files(config) == original


def test_restore_write_failure_keeps_evidence_and_hold(setup, monkeypatch):
    module, config, behavior, original = setup
    behavior.write_text(json.dumps({"candidate": "missing", "previous": "ok"}))
    real = module._replace
    def fail(path, data, nonce, *, phase):
        if phase == "restore" and path.name == "reader.py":
            raise OSError("synthetic restore failure")
        return real(path, data, nonce, phase=phase)
    monkeypatch.setattr(module, "_replace", fail)
    result = module.run_transaction(config)
    assert result.status == "ROLLBACK_FAILED_HOLD"
    assert (Path(config["machine_root"]) / ".wd-mind-rollout.lock").exists()
    assert Path(result.journal).exists()


def test_journal_failure_cannot_lose_a_live_caller_and_restore_underneath_it(setup, monkeypatch):
    module, config, behavior, original = setup
    behavior.write_text(json.dumps({"candidate": "timeout", "previous": "ok"}))
    config["lane_timeout_seconds"] = 0.1
    real_record = module._record
    real_popen = module.subprocess.Popen
    children = []
    failed = False
    def capture(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        if args[0][0] == sys.executable:
            children.append(child)
        return child
    def once(path, data, nonce):
        nonlocal failed
        if not failed and data["checks"] and data["checks"][-1]["exit_code"] is None:
            failed = True
            raise OSError("synthetic pending-check journal failure")
        return real_record(path, data, nonce)
    monkeypatch.setattr(module.subprocess, "Popen", capture)
    monkeypatch.setattr(module, "_record", once)
    try:
        result = module.run_transaction(config)
        assert failed
        assert result.status == "HOLD_ACTIVE_CALLER" and len(result.pending) == 1
        assert "ROLLING_BACK" not in journal(result)["states"]
        assert json.loads(observed_files(config)["current.json"])["bundle_commit"] == config["main_commit"]
    finally:
        for child in children:
            child.wait(timeout=5)  # owned synthetic processes finish naturally, including on RED


def test_lost_lock_ownership_never_allows_rollback_target_writes(setup, monkeypatch):
    module, config, behavior, original = setup
    behavior.write_text(json.dumps({"candidate": "missing", "previous": "ok"}))
    real_record = module._record
    changed = False
    def steal_after_failed_check(path, data, nonce):
        nonlocal changed
        result = real_record(path, data, nonce)
        if not changed and data["checks"] and not data["checks"][-1]["ok"]:
            changed = True
            (Path(config["machine_root"]) / ".wd-mind-rollout.lock").write_bytes(b"foreign owner")
        return result
    monkeypatch.setattr(module, "_record", steal_after_failed_check)
    result = module.run_transaction(config)
    assert changed and result.status == "ROLLBACK_FAILED_HOLD"
    assert not any(row["phase"] == "restore" for row in journal(result)["writes"])
    assert json.loads(observed_files(config)["current.json"])["bundle_commit"] == config["main_commit"]


@pytest.mark.parametrize("change", ["members_boolean", "launches_boolean", "reader_list", "blank_identity"])
def test_invalid_contract_shapes_refuse_before_mutation(setup, change):
    module, config, behavior, original = setup
    if change == "members_boolean":
        config["members"] = True
    elif change == "launches_boolean":
        config["launches"] = True
    elif change == "reader_list":
        config["reader_path"] = []
    else:
        config["lanes"][LANES[0]]["candidate_session"] = " "
    with pytest.raises(module.policy.Refused):
        module.run_transaction(config)
    assert observed_files(config) == original
    assert not (Path(config["machine_root"]) / ".wd-mind-rollout.lock").exists()


def test_digest_case_does_not_change_the_reviewed_previous_bytes(setup):
    module, config, behavior, original = setup
    config["previous_hashes"] = {name: value.upper() if value else None
                                 for name, value in config["previous_hashes"].items()}
    result = module.run_transaction(config)
    assert result.status == "COMMITTED" and result.pending == ()
    assert len(journal(result)["checks"]) == 5
