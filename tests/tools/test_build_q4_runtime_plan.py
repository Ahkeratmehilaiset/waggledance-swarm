"""Synthetic runtime input plan tests: tiny temporary trees, no real runtime, model or install."""

import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

from sandbox.mind.step2 import runtime_manifest
from tools import build_q4_runtime_plan as plan_mod

GIT = shutil.which("git")
WEIGHTS = b"synthetic-weights"
GENERATION = "a" * 40
REVISION = "b" * 40

pytestmark = pytest.mark.skipif(GIT is None, reason="git executable required")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git(repo, *args):
    result = subprocess.run([GIT, "-C", str(repo), "-c", "core.autocrlf=false", "-c", "user.name=synthetic",
                             "-c", "user.email=synthetic", *args], capture_output=True, check=True)
    return result.stdout.decode().strip()


CODE = {
    "tools/__init__.py": b"",
    "tools/a.py": b"import json\nfrom tools import b\nimport numpy\n",
    "tools/b.py": b"from sandbox.mind.step2 import c\nfrom . import d\n",
    "tools/d.py": b"VALUE = 1\n",
    "sandbox/mind/step2/c.py": b"import importlib\nMOD = importlib.import_module('tools.d')\n",
    "tools/unused.py": b"import os\n",
    "configs/data.json": b"{}\n",
}


def make_repo(root, files=CODE):
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "--template=")
    for name, data in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "synthetic")
    return repo, git(repo, "rev-parse", "HEAD")


def make_tree(root, files):
    root.mkdir(parents=True)
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


@pytest.fixture
def case(tmp_path):
    repo, commit = make_repo(tmp_path)
    site = make_tree(tmp_path / "site", {"pkg/__init__.py": b"", "pkg/mod.py": b"X = 2\n"})
    model = make_tree(tmp_path / "model", {"model.safetensors": WEIGHTS, "config.json": b"{}"})
    kwargs = dict(git_executable=GIT, repository=str(repo), commit=commit, code_roots=["tools/a.py"],
                  extra_code_paths=["configs/data.json"], site_root=str(site), model_root=str(model),
                  expected_model_files=["model.safetensors", "config.json"], expected_weights_sha256=sha(WEIGHTS),
                  generation=GENERATION, revision=REVISION, max_bytes=1024 * 1024, enabled=True)
    return kwargs, repo, site, model


def refused(kwargs, token):
    with pytest.raises(plan_mod.RuntimePlanRefused, match="^" + token + "$") as caught:
        plan_mod.plan_runtime_inputs(**kwargs)
    return caught.value


@pytest.mark.parametrize("error", [
    UnicodeDecodeError("secretcodec", b"\xff", 0, 1, "secret_bytes"),
    RuntimeError("secret_runtime_content"),
    ValueError("secret_value"),
])
def test_unexpected_errors_refuse_with_fixed_token_without_detail(case, monkeypatch, error):
    kwargs, _, _, _ = case

    def fail(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(plan_mod, "_code_plan", fail)
    caught = refused(kwargs, "runtime_plan_refused")
    assert caught.__context__ is None and caught.__cause__ is None
    assert "secret" not in str(caught)


def test_known_manifest_refusal_keeps_its_fixed_token(case, monkeypatch):
    kwargs, _, _, _ = case

    def fail(*_args, **_kwargs):
        raise ValueError("tree_nonregular")

    monkeypatch.setattr(plan_mod, "_code_plan", fail)
    caught = refused(kwargs, "tree_nonregular")
    assert caught.__context__ is None and caught.__cause__ is None


def test_default_off_reads_nothing(monkeypatch):
    monkeypatch.setattr(plan_mod.subprocess, "run", lambda *a, **k: pytest.fail("git ran while disabled"))
    monkeypatch.setattr(plan_mod.subprocess, "Popen", lambda *a, **k: pytest.fail("git ran while disabled"))
    assert plan_mod.plan_runtime_inputs(git_executable="unused", repository="unused", commit="unused",
                                        code_roots=[], site_root="unused", model_root="unused",
                                        expected_model_files=[], expected_weights_sha256="unused",
                                        generation="unused", revision="unused", max_bytes=1) == {"status": "OFF"}


def test_plan_pins_the_static_closure_from_committed_blobs_and_hashes_site_and_model(case):
    kwargs, _, _, _ = case
    plan = plan_mod.plan_runtime_inputs(**kwargs)
    expected_code = ["configs/data.json", "sandbox/mind/step2/c.py", "tools/__init__.py", "tools/a.py",
                     "tools/b.py", "tools/d.py"]
    assert plan["schema"] == "wd.q4-runtime-input-plan.v1" and plan["status"] == "CANDIDATE_UNVERIFIED"
    assert plan["commit"] == kwargs["commit"] and plan["generation"] == GENERATION and plan["revision"] == REVISION
    assert plan["code_paths"] == expected_code  # tools/unused.py is not imported, so not pinned
    assert plan["dynamic_loaders"] == ["sandbox/mind/step2/c.py"] and plan["third_party"] == ["numpy"]
    pins = {"assets/code/" + name: {"size": len(CODE[name]), "sha256": sha(CODE[name])} for name in expected_code}
    pins.update({"site/pkg/__init__.py": {"size": 0, "sha256": sha(b"")},
                 "site/pkg/mod.py": {"size": 6, "sha256": sha(b"X = 2\n")},
                 "model/model.safetensors": {"size": len(WEIGHTS), "sha256": sha(WEIGHTS)},
                 "model/config.json": {"size": 2, "sha256": sha(b"{}")}})
    assert plan["pins"] == pins
    assert plan["file_count"] == len(pins) and plan["total_bytes"] == sum(p["size"] for p in pins.values())
    assert plan["weights_sha256"] == sha(WEIGHTS)
    assert plan["trust"] == {"site_provenance": "UNKNOWN", "dependency_closure": "NOT_VERIFIED",
                             "dynamic_imports": "HEURISTIC_FLAGS_CALLER_LISTED", "git_executable": "CALLER_TRUSTED",
                             "code_closure": "STATIC_UNVERIFIED", "native_dll_closure": "NOT_VERIFIED"}
    records = [{"path": key, **value} for key, value in plan["pins"].items()]
    files, _ = runtime_manifest._validate_records(records)  # the candidate builder's own record policy
    assert set(files) == set(pins)


@pytest.mark.parametrize('include_helper', [True, False])
def test_secure_transport_requires_its_dynamic_controlled_import_helper(tmp_path, include_helper):
    helper = 'sandbox/mind/step2/q4_controlled_imports.py'
    files = {
        'tools/wd_q4_fixed_peer.py': b'from tools import wd_q4_secure_transport\n',
        'tools/wd_q4_secure_transport.py': b'HELPER = "sandbox/mind/step2/q4_controlled_imports.py"\n',
    }
    if include_helper:
        files[helper] = b'VALUE = 1\n'
    repo, commit = make_repo(tmp_path, files)
    site = make_tree(tmp_path / 'site', {'pkg.py': b'VALUE = 1\n'})
    model = make_tree(tmp_path / 'model', {'model.safetensors': WEIGHTS})
    kwargs = dict(git_executable=GIT, repository=str(repo), commit=commit,
                  code_roots=['tools/wd_q4_fixed_peer.py'], site_root=str(site), model_root=str(model),
                  expected_model_files=['model.safetensors'], expected_weights_sha256=sha(WEIGHTS),
                  generation=GENERATION, revision=REVISION, max_bytes=1024 * 1024, enabled=True)
    if include_helper:
        plan = plan_mod.plan_runtime_inputs(**kwargs)
        assert plan['pins']['assets/code/' + helper] == {'size': len(files[helper]), 'sha256': sha(files[helper])}
    else:
        refused(kwargs, 'code_closure_incomplete')


@pytest.mark.parametrize('include_helper', [True, False])
def test_public_reader_requires_controlled_import_helper_without_transport(tmp_path, include_helper):
    reader = 'sandbox/mind/step2/shared_index_checkpoint.py'
    helper = 'sandbox/mind/step2/q4_controlled_imports.py'
    files = {reader: b'VALUE = 1\n'}
    if include_helper:
        files[helper] = b'VALUE = 2\n'
    repo, commit = make_repo(tmp_path, files)
    site = make_tree(tmp_path / 'site', {'pkg.py': b'VALUE = 1\n'})
    model = make_tree(tmp_path / 'model', {'model.safetensors': WEIGHTS})
    kwargs = dict(git_executable=GIT, repository=str(repo), commit=commit,
                  code_roots=[reader], site_root=str(site), model_root=str(model),
                  expected_model_files=['model.safetensors'], expected_weights_sha256=sha(WEIGHTS),
                  generation=GENERATION, revision=REVISION, max_bytes=1024 * 1024, enabled=True)
    if include_helper:
        plan = plan_mod.plan_runtime_inputs(**kwargs)
        assert plan['pins']['assets/code/' + helper] == {'size': len(files[helper]), 'sha256': sha(files[helper])}
    else:
        refused(kwargs, 'code_closure_incomplete')


def test_plan_contains_no_absolute_paths(case):
    kwargs, repo, site, model = case
    text = repr(plan_mod.plan_runtime_inputs(**kwargs))
    for root in (repo, site, model):
        assert str(root) not in text and str(root.parent) not in text


def test_code_pins_are_committed_bytes_not_the_working_copy(case):
    kwargs, repo, _, _ = case
    (repo / "tools" / "d.py").write_bytes(b"VALUE = 1\r\nCHANGED = 2\r\n")  # dirty checkout, not committed
    plan = plan_mod.plan_runtime_inputs(**kwargs)
    assert plan["pins"]["assets/code/tools/d.py"] == {"size": len(CODE["tools/d.py"]), "sha256": sha(CODE["tools/d.py"])}


def test_git_environment_and_replace_objects_cannot_redirect_the_read(case, tmp_path, monkeypatch):
    kwargs, repo, _, _ = case
    other = tmp_path / "other"
    other.mkdir()
    other_repo, _ = make_repo(other, {"tools/a.py": b"OTHER = 1\n"})
    blob = git(repo, "rev-parse", kwargs["commit"] + ":tools/d.py")
    fake = subprocess.run([GIT, "-C", str(repo), "hash-object", "-w", "--stdin"], input=b"REPLACED = 1\n",
                          capture_output=True, check=True).stdout.decode().strip()
    git(repo, "replace", blob, fake)
    monkeypatch.setenv("GIT_DIR", str(other_repo / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other_repo))
    plan = plan_mod.plan_runtime_inputs(**kwargs)
    assert plan["pins"]["assets/code/tools/d.py"]["sha256"] == sha(CODE["tools/d.py"])
    assert plan["pins"]["assets/code/tools/a.py"]["sha256"] == sha(CODE["tools/a.py"])


@pytest.mark.parametrize("value", ["abc", "A" * 40, "g" * 40, "0" * 40])
def test_commit_must_be_one_full_existing_lowercase_commit(case, value):
    kwargs, _, _, _ = case
    refused({**kwargs, "commit": value}, "commit_invalid")


def test_commit_abbreviation_and_tree_object_refuse(case):
    kwargs, repo, _, _ = case
    refused({**kwargs, "commit": kwargs["commit"][:12]}, "commit_invalid")
    refused({**kwargs, "commit": git(repo, "rev-parse", kwargs["commit"] + "^{tree}")}, "commit_invalid")


@pytest.mark.parametrize("roots", [["tools/missing.py"], ["tools/a.py", "tools/a.py"], [], ["configs/data.json"],
                                   ["../tools/a.py"], ["tools\\a.py"]])
def test_bad_code_roots_refuse(case, roots):
    kwargs, _, _, _ = case
    refused({**kwargs, "code_roots": roots}, "code_roots_invalid")


def test_missing_extra_code_path_refuses(case):
    kwargs, _, _, _ = case
    refused({**kwargs, "extra_code_paths": ["configs/missing.json"]}, "code_roots_invalid")


@pytest.mark.parametrize("source", [b"from tools.ghost import value\n", b"import tools.ghost\n"])
def test_unresolved_local_import_refuses_as_incomplete_closure(tmp_path, source):
    repo, commit = make_repo(tmp_path, {"tools/__init__.py": b"", "tools/a.py": source})
    site = make_tree(tmp_path / "site", {"x.py": b""})
    model = make_tree(tmp_path / "model", {"model.safetensors": WEIGHTS})
    refused(dict(git_executable=GIT, repository=str(repo), commit=commit, code_roots=["tools/a.py"],
                 site_root=str(site), model_root=str(model), expected_model_files=["model.safetensors"],
                 expected_weights_sha256=sha(WEIGHTS), generation=GENERATION, revision=REVISION,
                 max_bytes=4096, enabled=True), "code_closure_incomplete")


def test_syntax_error_in_closure_refuses(tmp_path):
    repo, commit = make_repo(tmp_path, {"tools/a.py": b"def broken(:\n"})
    site = make_tree(tmp_path / "site", {"x.py": b""})
    model = make_tree(tmp_path / "model", {"model.safetensors": WEIGHTS})
    refused(dict(git_executable=GIT, repository=str(repo), commit=commit, code_roots=["tools/a.py"],
                 site_root=str(site), model_root=str(model), expected_model_files=["model.safetensors"],
                 expected_weights_sha256=sha(WEIGHTS), generation=GENERATION, revision=REVISION,
                 max_bytes=4096, enabled=True), "code_closure_incomplete")


@pytest.mark.parametrize("mode", ["120000", "160000"])
def test_symlink_or_submodule_entries_in_the_closure_refuse(case, mode):
    kwargs, repo, _, _ = case
    target = git(repo, "rev-parse", kwargs["commit"] + ":tools/d.py") if mode == "120000" else kwargs["commit"]
    git(repo, "update-index", "--add", "--cacheinfo", f"{mode},{target},tools/link.py")
    git(repo, "commit", "-q", "-m", "link")
    commit = git(repo, "rev-parse", "HEAD")
    refused({**kwargs, "commit": commit, "extra_code_paths": ["tools/link.py"]}, "code_entry_invalid")


def test_model_weights_mismatch_refuses(case):
    kwargs, _, _, _ = case
    refused({**kwargs, "expected_weights_sha256": sha(b"other")}, "model_weights_mismatch")


@pytest.mark.parametrize("tree", ["site", "model"])
def test_unexpected_tree_member_refuses_before_any_tree_file_is_opened(case, monkeypatch, tree):
    kwargs, _, site, model = case
    expected_site_pins = {
        "pkg/__init__.py": {"size": 0, "sha256": sha(b"")},
        "pkg/mod.py": {"size": 6, "sha256": sha(b"X = 2\n")},
    }
    kwargs["expected_site_pins"] = expected_site_pins
    (site if tree == "site" else model).joinpath("unexpected.bin").write_bytes(b"synthetic")
    monkeypatch.setattr(plan_mod, "_hash_bounded", lambda *a, **k: pytest.fail("tree file opened before membership check"))
    refused(kwargs, "site_pin_mismatch" if tree == "site" else "model_membership_mismatch")


def test_member_added_after_precheck_refuses_before_opening_it(case, monkeypatch):
    kwargs, _, site, _ = case
    kwargs["expected_site_pins"] = {
        "pkg/__init__.py": {"size": 0, "sha256": sha(b"")},
        "pkg/mod.py": {"size": 6, "sha256": sha(b"X = 2\n")},
    }
    tree = plan_mod.runtime_manifest._tree
    calls = 0

    def changing_tree(root):
        nonlocal calls
        if root == site:
            calls += 1
            if calls == 2:
                (site / "unexpected.bin").write_bytes(b"synthetic")
        return tree(root)

    monkeypatch.setattr(plan_mod.runtime_manifest, "_tree", changing_tree)
    monkeypatch.setattr(plan_mod, "_hash_bounded", lambda *a, **k: pytest.fail("tree file opened after membership changed"))
    refused(kwargs, "site_pin_mismatch")


@pytest.mark.parametrize("change", ["unexpected", "missing", "no_weights_listed"])
def test_model_membership_must_match_exactly(case, change):
    kwargs, _, _, model = case
    if change == "unexpected":
        (model / "extra.bin").write_bytes(b"x")
    if change == "missing":
        (model / "config.json").unlink()
    expected = ["config.json"] if change == "no_weights_listed" else kwargs["expected_model_files"]
    refused({**kwargs, "expected_model_files": expected}, "model_membership_mismatch")


@pytest.mark.parametrize("change", ["mismatch", "missing", "extra"])
def test_expected_site_pins_must_match_exactly(case, change):
    kwargs, _, _, _ = case
    pins = {"pkg/__init__.py": {"size": 0, "sha256": sha(b"")}, "pkg/mod.py": {"size": 6, "sha256": sha(b"X = 2\n")}}
    assert plan_mod.plan_runtime_inputs(**{**kwargs, "expected_site_pins": pins})["status"] == "CANDIDATE_UNVERIFIED"
    if change == "mismatch":
        pins["pkg/mod.py"] = {"size": 6, "sha256": sha(b"X = 3\n")}
    if change == "missing":
        del pins["pkg/mod.py"]
    if change == "extra":
        pins["pkg/other.py"] = {"size": 0, "sha256": sha(b"")}
    refused({**kwargs, "expected_site_pins": pins}, "site_pin_mismatch")


def test_site_hard_link_alias_refuses(case):
    kwargs, _, site, _ = case
    os.link(site / "pkg" / "mod.py", site / "pkg" / "alias.py")
    refused(kwargs, "tree_nonregular")


@pytest.mark.skipif(os.name != "nt", reason="NTFS junction and alternate stream checks")
def test_site_junction_refuses(case, tmp_path):
    import _winapi
    kwargs, _, site, _ = case
    target = make_tree(tmp_path / "elsewhere", {"z.py": b""})
    _winapi.CreateJunction(str(target), str(site / "linked"))
    refused(kwargs, "tree_reparse")


@pytest.mark.skipif(os.name != "nt", reason="NTFS alternate data streams")
def test_site_alternate_data_stream_refuses(case):
    kwargs, _, site, _ = case
    with open(str(site / "pkg" / "mod.py") + ":hidden", "wb") as stream:
        stream.write(b"x")
    with pytest.raises(plan_mod.RuntimePlanRefused):
        plan_mod.plan_runtime_inputs(**kwargs)


@pytest.mark.parametrize("name", ["a b.py", "SHORT~1.py", "nested/x y.txt"])
def test_site_names_outside_the_manifest_policy_refuse(case, name):
    kwargs, _, site, _ = case
    (site / name).parent.mkdir(exist_ok=True)
    (site / name).write_bytes(b"")
    refused(kwargs, "path_invalid")


@pytest.mark.parametrize("target", ["site", "model"])
def test_root_alias_or_overlap_refuses(case, target):
    kwargs, _, site, model = case
    refused({**kwargs, "model_root": str(site)}, "roots_invalid")
    nested = make_tree(site / "nested-model", {"model.safetensors": WEIGHTS})
    refused({**kwargs, "model_root": str(nested), "expected_model_files": ["model.safetensors"]}, "roots_invalid")
    alias = (site if target == "site" else model).parent / "ALIAS~1"
    refused({**kwargs, target + "_root": str(alias)}, "roots_invalid")
    refused({**kwargs, target + "_root": "relative"}, "roots_invalid")


@pytest.mark.parametrize("direction", ["model_inside_site", "site_inside_model"])
def test_a_string_disjoint_alias_of_an_overlapping_root_refuses_by_file_identity(case, monkeypatch, direction):
    kwargs, _, site, model = case
    monkeypatch.setattr(plan_mod, "_related", lambda left, right: False)  # as a SUBST or short-name spelling would be
    if direction == "model_inside_site":
        inner = make_tree(site / "inner-model", {"model.safetensors": WEIGHTS})
        args = {"model_root": str(inner), "expected_model_files": ["model.safetensors"]}
    else:
        inner = make_tree(model / "inner-site", {"x.py": b""})
        args = {"site_root": str(inner)}
    refused({**kwargs, **args}, "roots_invalid")


def test_root_without_a_file_identity_refuses(case, monkeypatch):
    kwargs, _, _, _ = case
    real = os.stat
    monkeypatch.setattr(plan_mod.os, "stat", lambda path, *a, **k: os.stat_result((real(path).st_mode,) + (0,) * 9))
    refused(kwargs, "roots_invalid")


def test_budget_and_verifier_caps_are_enforced(case, monkeypatch):
    kwargs, _, _, _ = case
    total = plan_mod.plan_runtime_inputs(**kwargs)["total_bytes"]
    refused({**kwargs, "max_bytes": total - 1}, "budget_insufficient")
    assert plan_mod.plan_runtime_inputs(**{**kwargs, "max_bytes": total})["total_bytes"] == total
    with monkeypatch.context() as patch:
        patch.setattr(runtime_manifest, "MAX_FILES", 3)
        with pytest.raises(plan_mod.RuntimePlanRefused):
            plan_mod.plan_runtime_inputs(**kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(runtime_manifest, "MAX_FILE_BYTES", len(WEIGHTS) - 1)
        with pytest.raises(plan_mod.RuntimePlanRefused):
            plan_mod.plan_runtime_inputs(**kwargs)


def test_tree_change_during_hashing_refuses(case, monkeypatch):
    kwargs, _, site, _ = case
    original = plan_mod._hash_bounded
    def hash_and_add(path, limit):
        (site / "late.py").write_bytes(b"")
        return original(path, limit)
    monkeypatch.setattr(plan_mod, "_hash_bounded", hash_and_add)
    refused(kwargs, "tree_changed")


@pytest.mark.parametrize("field,value", [("generation", "x"), ("revision", "B" * 40), ("max_bytes", 0),
                                         ("max_bytes", True), ("expected_weights_sha256", "z" * 64),
                                         ("git_executable", "git"), ("enabled", 1)])
def test_invalid_scalar_inputs_refuse(case, field, value):
    kwargs, _, _, _ = case
    refused({**kwargs, field: value}, "inputs_invalid")


def test_refusal_carries_no_path_or_input_text(case):
    kwargs, _, site, _ = case
    error = refused({**kwargs, "expected_weights_sha256": sha(b"other")}, "model_weights_mismatch")
    assert str(site) not in str(error) and error.__cause__ is None and error.__context__ is None


def test_module_documents_limits_without_production_claims():
    doc = plan_mod.__doc__
    assert "default-off" in doc and "not an installer" in doc and "dynamic" in doc
    assert "git executable" in doc and "dependency closure" in doc
    assert "cannot detect all dynamic execution" in doc and "while it is read" in doc
    assert "max_bytes bounds the input bytes read" in doc and "not process peak memory" in doc


def single_module(tmp_path, source):
    repo, commit = make_repo(tmp_path, {"tools/__init__.py": b"", "tools/m.py": source})
    site = make_tree(tmp_path / "site", {"x.py": b""})
    model = make_tree(tmp_path / "model", {"model.safetensors": WEIGHTS})
    return dict(git_executable=GIT, repository=str(repo), commit=commit, code_roots=["tools/m.py"],
                site_root=str(site), model_root=str(model), expected_model_files=["model.safetensors"],
                expected_weights_sha256=sha(WEIGHTS), generation=GENERATION, revision=REVISION,
                max_bytes=1024 * 1024, enabled=True)


@pytest.mark.parametrize("source", [
    b"from importlib import import_module as load\nload('tools.d')\n",
    b"import importlib as il\n",
    b"import importlib.util\n",
    b"from runpy import run_path as r\n",
    b"from importlib import machinery as m\n",
    b"import runpy\n",
    b"import builtins as b\n",
    b"loader = __import__\nloader('json')\n",
    b"import json\nhook = json.import_module\n",
    b"import sys\nfn = getattr(sys.modules['builtins'], '__import__')\n",
    b"import sys\nmod = sys.modules['importlib']\n",
    b"run = eval\n",
    b"code = compile('1', 'x', 'single')\n",
    b"import json\nname = 'exec'\nfn = getattr(json, name)\n",
    b"table = __builtins__\n",
])
def test_aliased_or_indirect_dynamic_loading_is_conservatively_flagged(tmp_path, source):
    plan = plan_mod.plan_runtime_inputs(**single_module(tmp_path, source))
    assert plan["dynamic_loaders"] == ["tools/m.py"]
    assert plan["trust"]["code_closure"] == "STATIC_UNVERIFIED"


def test_ordinary_eval_and_compile_methods_are_not_flagged(tmp_path):
    source = (b"import json\nimport re\nPATTERN = re.compile('x')\nVALUE = json.dumps({'evaluate': 1})\n"
              b"class Model:\n    def eval(self):\n        return self\nModel().eval()\n")
    assert plan_mod.plan_runtime_inputs(**single_module(tmp_path, source))["dynamic_loaders"] == []


def substitute_git(monkeypatch, script):
    real = subprocess.Popen
    started = []
    def fake(command, *args, **kwargs):
        process = real([sys.executable, "-I", "-c", script], *args, **kwargs)
        started.append(process)
        return process
    monkeypatch.setattr(plan_mod.subprocess, "Popen", fake)
    return started


FLOOD = "import sys, time\nchunk = b'x' * 65536\nwhile True:\n    sys.{0}.buffer.write(chunk)\n    sys.{0}.flush()\n    time.sleep(0.01)\n"


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_unbounded_git_output_is_refused_while_it_is_read(case, monkeypatch, stream):
    kwargs, _, _, _ = case
    started = substitute_git(monkeypatch, FLOOD.format(stream))
    begun = time.monotonic()
    refused({**kwargs, "timeout_seconds": 2}, "git_output_too_large")
    assert time.monotonic() - begun < 30 and started and all(item.poll() is not None for item in started)


def test_git_timeout_refuses_and_ends_the_process(case, monkeypatch):
    kwargs, _, _, _ = case
    started = substitute_git(monkeypatch, "import time\ntime.sleep(60)\n")
    begun = time.monotonic()
    refused({**kwargs, "timeout_seconds": 1}, "git_timeout")
    assert time.monotonic() - begun < 5 and started and all(item.poll() is not None for item in started)


def test_oversize_blob_is_refused_before_its_bytes_are_read(case, monkeypatch):
    kwargs, repo, _, _ = case
    oid = git(repo, "rev-parse", kwargs["commit"] + ":tools/a.py")
    real = subprocess.Popen
    commands = []
    def record(command, *args, **kw):
        commands.append([str(item) for item in command])
        return real(command, *args, **kw)
    monkeypatch.setattr(plan_mod.subprocess, "Popen", record)
    monkeypatch.setattr(runtime_manifest, "MAX_FILE_BYTES", len(CODE["tools/a.py"]) - 1)
    refused(kwargs, "code_entry_invalid")
    assert commands and not any("cat-file" in command and oid in command for command in commands)


def record_git(monkeypatch, delay=0.0):
    real = subprocess.Popen
    commands = []
    def record(command, *args, **kw):
        commands.append([str(item) for item in command])
        time.sleep(delay)
        return real(command, *args, **kw)
    monkeypatch.setattr(plan_mod.subprocess, "Popen", record)
    return commands


def test_code_reads_are_budgeted_before_each_blob(case, monkeypatch):
    kwargs, repo, _, _ = case
    oid = git(repo, "rev-parse", kwargs["commit"] + ":tools/a.py")
    commands = record_git(monkeypatch)
    budget = len(CODE["configs/data.json"]) + len(CODE["tools/a.py"]) - 1  # data.json is read first, a.py exceeds
    refused({**kwargs, "max_bytes": budget}, "budget_insufficient")
    assert not any("cat-file" in command and oid in command for command in commands)


def test_site_and_model_are_budgeted_before_hashing(case, monkeypatch):
    kwargs, _, site, _ = case
    plan = plan_mod.plan_runtime_inputs(**kwargs)
    code_total = sum(value["size"] for key, value in plan["pins"].items() if key.startswith("assets/code/"))
    original = plan_mod._hash_bounded
    hashed = []
    def record(path, limit):
        hashed.append(Path(path))
        return original(path, limit)
    monkeypatch.setattr(plan_mod, "_hash_bounded", record)
    refused({**kwargs, "max_bytes": code_total + 1}, "budget_insufficient")  # pkg/__init__.py fits, pkg/mod.py not
    assert site / "pkg" / "mod.py" not in hashed


def test_a_file_growing_after_its_size_check_is_never_read_past_the_remaining_budget(case, monkeypatch):
    kwargs, _, site, _ = case
    plan = plan_mod.plan_runtime_inputs(**kwargs)
    code_total = sum(value["size"] for key, value in plan["pins"].items() if key.startswith("assets/code/"))
    target = site / "pkg" / "mod.py"
    real_chain, real_fdopen = runtime_manifest._check_chain, os.fdopen
    grown, read = [], []
    def grow_then_check(path, *args, **kw):
        if Path(path) == target and not grown:
            grown.append(True)
            with open(target, "ab") as handle:  # grows after the size check, before the open for hashing
                handle.write(b"#" * (1024 * 1024))
        return real_chain(path, *args, **kw)
    class Counting:
        def __init__(self, stream):
            self.stream = stream
        def read(self, size=-1):
            block = self.stream.read(size)
            if os.fstat(self.stream.fileno()).st_size > 1024 * 1024:
                read.append(len(block))
            return block
        @property
        def raw(self):
            return self
        def fileno(self):
            return self.stream.fileno()
        def close(self):
            self.stream.close()
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            self.stream.close()
    monkeypatch.setattr(runtime_manifest, "_check_chain", grow_then_check)
    monkeypatch.setattr(plan_mod.os, "fdopen", lambda fd, *a, **k: Counting(real_fdopen(fd, *a, **k)))
    with pytest.raises(plan_mod.RuntimePlanRefused):
        plan_mod.plan_runtime_inputs(**{**kwargs, "max_bytes": code_total + 6})  # mod.py had 6 bytes at the check
    assert grown and sum(read) <= 6


def test_a_file_growing_after_it_is_opened_refuses_as_changed(case, monkeypatch):
    kwargs, _, site, _ = case
    target = site / "pkg" / "mod.py"
    real_fdopen = os.fdopen
    grown = []
    class GrowOnFirstRead:
        def __init__(self, stream):
            self.stream = stream
        def read(self, size=-1):
            if not grown and os.path.samefile(self.stream.fileno(), target):
                grown.append(True)
                with open(target, "ab") as handle:
                    handle.write(b"#")
            return self.stream.read(size)
        @property
        def raw(self):
            return self
        def fileno(self):
            return self.stream.fileno()
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            self.stream.close()
    monkeypatch.setattr(plan_mod.os, "fdopen", lambda fd, *a, **k: GrowOnFirstRead(real_fdopen(fd, *a, **k)))
    refused(kwargs, "file_changed")
    assert grown


def test_raw_reads_of_a_file_growing_after_open_never_pass_its_opened_size(case, monkeypatch):
    kwargs, _, site, _ = case
    target = site / "pkg" / "mod.py"
    raw_bytes, grown = [], []
    class CountingRaw(io.RawIOBase):  # counts every byte the OS hands over, buffered prefetch included
        def __init__(self, fd):
            self.file = io.FileIO(fd, "rb")
        def readable(self):
            return True
        def readinto(self, buffer):
            mine = os.path.samefile(self.file.fileno(), target)
            if mine and not grown:
                grown.append(True)
                with open(target, "ab") as handle:
                    handle.write(b"#" * 65536)
            count = self.file.readinto(buffer)
            if mine:
                raw_bytes.append(count)
            return count
        def fileno(self):
            return self.file.fileno()
        def close(self):
            self.file.close()
            super().close()
    real_read = os.read
    def counting_read(fd, size):
        data = real_read(fd, size)
        if os.path.samefile(fd, target):
            raw_bytes.append(len(data))
        return data
    monkeypatch.setattr(plan_mod.os, "fdopen", lambda fd, *a, **k: io.BufferedReader(CountingRaw(fd)))
    monkeypatch.setattr(plan_mod.os, "read", counting_read)
    refused(kwargs, "file_changed")
    assert grown and sum(raw_bytes) <= 6 + 1  # opened size plus the one-byte end probe


def test_root_and_extra_modules_pin_their_parent_packages(tmp_path):
    files = {"sandbox/__init__.py": b"", "sandbox/mind/__init__.py": b"X = 1\n", "sandbox/mind/x.py": b"VALUE = 1\n",
             "tools/__init__.py": b"", "tools/extra.py": b"", "tools/unused.py": b""}
    repo, commit = make_repo(tmp_path, files)
    site = make_tree(tmp_path / "site", {"x.py": b""})
    model = make_tree(tmp_path / "model", {"model.safetensors": WEIGHTS})
    plan = plan_mod.plan_runtime_inputs(
        git_executable=GIT, repository=str(repo), commit=commit, code_roots=["sandbox/mind/x.py"],
        extra_code_paths=["tools/extra.py"], site_root=str(site), model_root=str(model),
        expected_model_files=["model.safetensors"], expected_weights_sha256=sha(WEIGHTS), generation=GENERATION,
        revision=REVISION, max_bytes=4096, enabled=True)
    assert plan["code_paths"] == ["sandbox/__init__.py", "sandbox/mind/__init__.py", "sandbox/mind/x.py",
                                  "tools/__init__.py", "tools/extra.py"]


def test_partial_clone_blobs_are_never_lazily_fetched(case, tmp_path):
    kwargs, repo, _, _ = case
    git(repo, "config", "uploadpack.allowFilter", "true")
    clone = tmp_path / "partial"
    subprocess.run([GIT, "-c", "protocol.file.allow=always", "clone", "-q", "--no-checkout", "--filter=blob:none",
                    repo.as_uri(), str(clone)], capture_output=True, check=True)
    before = git(clone, "count-objects", "-v")
    with pytest.raises(plan_mod.RuntimePlanRefused):
        plan_mod.plan_runtime_inputs(**{**kwargs, "repository": str(clone)})
    assert git(clone, "count-objects", "-v") == before  # nothing was fetched from the promisor remote


def test_git_wall_time_is_bounded_across_calls(case, monkeypatch):
    kwargs, _, _, _ = case
    record_git(monkeypatch, delay=0.7)  # each call alone is far below the timeout, three calls are not
    begun = time.monotonic()
    refused({**kwargs, "timeout_seconds": 1}, "git_timeout")
    assert time.monotonic() - begun < 10
