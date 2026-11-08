"""The runtime input plan pins the path-loaded staged fixed backend only when a staged route is requested.

``tools/wd_q4_secure_transport.py`` loads ``tools/wd_q4_staged_fixed_backend.py`` by path from the pinned bundle
(``_read_staged_backend``), so the static import closure of the transport never reaches it, and the helper imports
its own dependencies lazily inside functions. Synthetic temporary repositories and trees; one test reads the
committed blobs of this repository. No real runtime, model or install.
"""

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from tools import build_q4_runtime_plan as plan_mod
from tests.tools.test_build_q4_runtime_plan import GENERATION, GIT, REVISION, WEIGHTS, make_repo, make_tree, refused, sha

pytestmark = pytest.mark.skipif(GIT is None, reason="git executable required")

HELPER = "tools/wd_q4_staged_fixed_backend.py"
TRANSPORT = "tools/wd_q4_secure_transport.py"
VERIFIER = "sandbox/mind/step2/runtime_manifest.py"
CONTROLLED = ("sandbox/mind/step2/q4_controlled_imports.py", "sandbox/mind/step2/q4_namespace_origin.py",
              "sandbox/mind/step2/q4_native_additions.py")
HELPER_SOURCE = (b"import hashlib\n\n\ndef validate():\n"
                 b"    from tools import wd_q4_secure_transport as fixed\n"
                 b"    from tools import wd_q4_lane_link as link\n"
                 b"    from waggledance.core.bridge_log_reader import BridgeCursor\n"
                 b"    return fixed, link, BridgeCursor\n")
FILES = {
    "tools/__init__.py": b"",
    "tools/wd_q4_fixed_peer.py": b"from tools import wd_q4_secure_transport\n",
    "tools/wd_q4_secure_transport.py": b"HELPER = 'tools-bootstrap/tools/wd_q4_staged_fixed_backend.py'\n",
    "tools/wd_q4_lane_link.py": b"VALUE = 1\n",
    "waggledance/__init__.py": b"",
    "waggledance/core/__init__.py": b"",
    "waggledance/core/bridge_log_reader.py": b"class BridgeCursor:\n    pass\n",
    HELPER: HELPER_SOURCE,
    VERIFIER: b"import hashlib\nimport os\n",
    **{path: b"VALUE = %d\n" % index for index, path in enumerate(CONTROLLED)},
}
LAZY = {"tools/wd_q4_secure_transport.py", "tools/wd_q4_lane_link.py", "waggledance/core/bridge_log_reader.py",
        "waggledance/__init__.py", "waggledance/core/__init__.py"}


def make_case(tmp_path, files=FILES, roots=("tools/wd_q4_fixed_peer.py",)):
    repo, commit = make_repo(tmp_path, files)
    site = make_tree(tmp_path / "site", {"pkg.py": b"VALUE = 1\n"})
    model = make_tree(tmp_path / "model", {"model.safetensors": WEIGHTS})
    return dict(git_executable=GIT, repository=str(repo), commit=commit, code_roots=list(roots),
                site_root=str(site), model_root=str(model), expected_model_files=["model.safetensors"],
                expected_weights_sha256=sha(WEIGHTS), generation=GENERATION, revision=REVISION,
                max_bytes=1024 * 1024, enabled=True)


def test_without_a_staged_request_the_plan_is_unchanged_and_omits_the_path_loaded_helper(tmp_path):
    kwargs = make_case(tmp_path)
    plan = plan_mod.plan_runtime_inputs(**kwargs)
    assert HELPER not in plan["code_paths"] and "staged_backend" not in plan
    assert set(plan) == {"schema", "status", "commit", "generation", "revision", "code_paths", "dynamic_loaders",
                         "third_party", "pins", "file_count", "total_bytes", "weights_sha256", "trust"}
    assert plan == plan_mod.plan_runtime_inputs(**kwargs, staged_backend_sha256=None)


def test_a_staged_request_pins_the_helper_and_its_lazy_import_closure(tmp_path):
    kwargs = make_case(tmp_path)
    plan = plan_mod.plan_runtime_inputs(**kwargs, staged_backend_sha256=sha(HELPER_SOURCE))
    assert plan["staged_backend"] == {"path": HELPER, "size": len(HELPER_SOURCE), "sha256": sha(HELPER_SOURCE)}
    assert plan["pins"]["assets/code/" + HELPER] == {"size": len(HELPER_SOURCE), "sha256": sha(HELPER_SOURCE)}
    assert LAZY | set(CONTROLLED) | {HELPER} <= set(plan["code_paths"])
    for path in LAZY | set(CONTROLLED):
        assert plan["pins"]["assets/code/" + path] == {"size": len(FILES[path]), "sha256": sha(FILES[path])}
    base = plan_mod.plan_runtime_inputs(**kwargs)
    assert set(plan["code_paths"]) - set(base["code_paths"]) == LAZY - set(base["code_paths"]) | {HELPER, VERIFIER}
    assert plan["trust"] == base["trust"]


def test_the_helper_pulls_the_transport_and_its_controlled_helpers_even_from_another_root(tmp_path):
    files = dict(FILES, **{"tools/other.py": b"VALUE = 9\n"})
    kwargs = make_case(tmp_path, files, roots=("tools/other.py",))
    plan = plan_mod.plan_runtime_inputs(**kwargs, staged_backend_sha256=sha(HELPER_SOURCE))
    assert {"tools/wd_q4_secure_transport.py", *CONTROLLED, HELPER} <= set(plan["code_paths"])


def test_a_helper_pin_that_differs_from_the_committed_blob_refuses(tmp_path):
    refused(dict(make_case(tmp_path), staged_backend_sha256=sha(HELPER_SOURCE + b"# other\n")),
            "staged_backend_mismatch")


@pytest.mark.parametrize("pin", ["", "A" * 64, "a" * 63, "g" * 64, 1, b"a" * 64, False])
def test_an_invalid_helper_pin_refuses_as_invalid_input(tmp_path, pin):
    refused(dict(make_case(tmp_path), staged_backend_sha256=pin), "inputs_invalid")


def test_a_requested_helper_missing_from_the_commit_refuses(tmp_path):
    files = {key: value for key, value in FILES.items() if key != HELPER}
    refused(dict(make_case(tmp_path, files), staged_backend_sha256=sha(HELPER_SOURCE)), "code_closure_incomplete")


@pytest.mark.parametrize("lazy", [b"    from tools.ghost import value\n", b"    import tools.ghost\n"])
def test_an_unresolved_lazy_import_of_the_helper_refuses(tmp_path, lazy):
    source = HELPER_SOURCE + b"\n\ndef later():\n" + lazy
    files = dict(FILES, **{HELPER: source})
    refused(dict(make_case(tmp_path, files), staged_backend_sha256=sha(source)), "code_closure_incomplete")


def _repository_root():
    root = Path(__file__).resolve().parents[2]
    probe = subprocess.run([GIT, "-C", str(root), "rev-parse", "--show-toplevel", "HEAD"], capture_output=True)
    if probe.returncode != 0:
        pytest.skip("not a git checkout")
    top, head = probe.stdout.decode().split()
    return Path(top), head


def test_the_committed_transport_closure_lacks_the_helper_until_the_staged_route_is_requested(tmp_path):
    root, head = _repository_root()
    helper = subprocess.run([GIT, "-C", str(root), "cat-file", "blob", head + ":" + HELPER],
                            capture_output=True, check=True).stdout
    site = make_tree(tmp_path / "site", {"pkg.py": b"VALUE = 1\n"})
    model = make_tree(tmp_path / "model", {"model.safetensors": WEIGHTS})
    kwargs = dict(git_executable=GIT, repository=str(root), commit=head,
                  code_roots=["tools/wd_q4_secure_transport.py"], site_root=str(site), model_root=str(model),
                  expected_model_files=["model.safetensors"], expected_weights_sha256=sha(WEIGHTS),
                  generation=GENERATION, revision=REVISION, max_bytes=256 * 1024 * 1024, timeout_seconds=300,
                  enabled=True)
    base = plan_mod.plan_runtime_inputs(**kwargs)
    assert HELPER not in base["code_paths"]
    plan = plan_mod.plan_runtime_inputs(**kwargs, staged_backend_sha256=hashlib.sha256(helper).hexdigest())
    actual = {"tools/wd_q4_secure_transport.py", "tools/wd_q4_lane_link.py", "tools/wd_q4_public_pointer_resolver.py",
              "tools/wd_q4_public_query.py", "waggledance/core/bridge_log_reader.py",
              "sandbox/mind/step2/shared_capture_adapter.py", "sandbox/mind/step2/shared_result_resolver.py"}
    assert {HELPER} | actual | set(CONTROLLED) <= set(plan["code_paths"])
    assert plan["staged_backend"]["sha256"] == hashlib.sha256(helper).hexdigest()
    assert not {HELPER} & set(base["code_paths"]) and set(base["code_paths"]) < set(plan["code_paths"])


# --- Staged runtime profile (RCO1 design 11-07): a staged request always plans the backend, the transport that
# loads it by path, the transport's controlled-import helpers and the stdlib-only runtime manifest verifier the
# staged child reads from the runtime tree. Any of them missing from the commit refuses before a blob is read.

INDEPENDENT_HELPER = b"import hashlib\n\n\ndef validate():\n    return hashlib\n"
CONTENT_HELPER = "tools/wd_q4_content_fixed_backend.py"


def test_content_profile_pins_the_content_backend_transport_and_verifier(tmp_path):
    files = dict(FILES, **{CONTENT_HELPER: INDEPENDENT_HELPER, "tools/other.py": b"VALUE = 9\n"})
    kwargs = make_case(tmp_path, files, roots=("tools/other.py",))
    plan = plan_mod.plan_runtime_inputs(**kwargs, content_backend_sha256=sha(INDEPENDENT_HELPER))
    assert plan["content_runtime"] == {"backend": _entry(CONTENT_HELPER, files),
                                       "transport": _entry(TRANSPORT, files), "verifier": _entry(VERIFIER, files)}
    assert {CONTENT_HELPER, TRANSPORT, VERIFIER, *CONTROLLED} <= set(plan["code_paths"])
    assert HELPER not in plan["code_paths"]


@pytest.mark.parametrize("missing", [CONTENT_HELPER, TRANSPORT, VERIFIER, *CONTROLLED])
def test_content_profile_rejects_missing_components(tmp_path, missing):
    files = dict(FILES, **{CONTENT_HELPER: INDEPENDENT_HELPER, "tools/other.py": b"VALUE = 9\n"})
    del files[missing]
    kwargs = make_case(tmp_path, files, roots=("tools/other.py",))
    refused(dict(kwargs, content_backend_sha256=sha(INDEPENDENT_HELPER)), "code_closure_incomplete")


def test_content_profile_rejects_wrong_hash_or_ambiguous_profiles(tmp_path):
    files = dict(FILES, **{CONTENT_HELPER: INDEPENDENT_HELPER})
    kwargs = make_case(tmp_path, files)
    refused(dict(kwargs, content_backend_sha256=sha(b"wrong")), "content_backend_mismatch")
    refused(dict(kwargs, content_backend_sha256=sha(INDEPENDENT_HELPER),
                 staged_backend_sha256=sha(HELPER_SOURCE)), "inputs_invalid")


@pytest.mark.parametrize("pin", ["", "A" * 64, "a" * 63, "g" * 64, 1, False])
def test_content_profile_rejects_invalid_hash(tmp_path, pin):
    refused(dict(make_case(tmp_path), content_backend_sha256=pin), "inputs_invalid")


def test_real_committed_content_profile_has_exact_code_blobs(tmp_path):
    root, head = _repository_root()
    blobs = {path: subprocess.run([GIT, "-C", str(root), "cat-file", "blob", head + ":" + path],
                                  capture_output=True, check=True).stdout
             for path in (CONTENT_HELPER, TRANSPORT, VERIFIER)}
    site = make_tree(tmp_path / "site", {"pkg.py": b"VALUE = 1\n"})
    model = make_tree(tmp_path / "model", {"model.safetensors": WEIGHTS})
    plan = plan_mod.plan_runtime_inputs(git_executable=GIT, repository=str(root), commit=head,
        code_roots=["tools/wd_q4_fixed_peer.py"], site_root=str(site), model_root=str(model),
        expected_model_files=["model.safetensors"], expected_weights_sha256=sha(WEIGHTS), generation=GENERATION,
        revision=REVISION, max_bytes=256 * 1024 * 1024, timeout_seconds=300, enabled=True,
        content_backend_sha256=sha(blobs[CONTENT_HELPER]))
    assert plan["content_runtime"] == {name: _entry(path, blobs) for name, path in
                                      (("backend", CONTENT_HELPER), ("transport", TRANSPORT),
                                       ("verifier", VERIFIER))}
    assert set(CONTROLLED) <= set(plan["code_paths"])


def _entry(path, files=FILES):
    return {"path": path, "size": len(files[path]), "sha256": sha(files[path])}


def test_a_staged_request_reports_the_runtime_profile_with_exact_blob_pins(tmp_path):
    plan = plan_mod.plan_runtime_inputs(**make_case(tmp_path), staged_backend_sha256=sha(HELPER_SOURCE))
    assert plan["staged_runtime"] == {"backend": _entry(HELPER), "transport": _entry(TRANSPORT),
                                      "verifier": _entry(VERIFIER)}
    assert plan["staged_backend"] == _entry(HELPER)
    for path in (HELPER, TRANSPORT, VERIFIER, *CONTROLLED):
        assert plan["pins"]["assets/code/" + path] == {"size": len(FILES[path]), "sha256": sha(FILES[path])}


def test_the_profile_plans_transport_helpers_and_verifier_even_when_nothing_imports_them(tmp_path):
    files = dict(FILES, **{HELPER: INDEPENDENT_HELPER, "tools/other.py": b"VALUE = 9\n"})
    kwargs = make_case(tmp_path, files, roots=("tools/other.py",))
    base = plan_mod.plan_runtime_inputs(**kwargs)
    assert not {HELPER, TRANSPORT, VERIFIER, *CONTROLLED} & set(base["code_paths"])
    plan = plan_mod.plan_runtime_inputs(**kwargs, staged_backend_sha256=sha(INDEPENDENT_HELPER))
    assert {HELPER, TRANSPORT, VERIFIER, *CONTROLLED} <= set(plan["code_paths"])
    assert plan["staged_runtime"]["transport"] == _entry(TRANSPORT, files)
    assert plan["staged_runtime"]["verifier"] == _entry(VERIFIER, files)


@pytest.mark.parametrize("missing", [TRANSPORT, VERIFIER, *CONTROLLED])
def test_a_profile_file_missing_from_the_commit_refuses(tmp_path, missing):
    files = {key: value for key, value in FILES.items() if key not in (missing, "tools/wd_q4_fixed_peer.py")}
    files[HELPER] = INDEPENDENT_HELPER
    files["tools/other.py"] = b"VALUE = 9\n"
    kwargs = make_case(tmp_path, files, roots=("tools/other.py",))
    assert plan_mod.plan_runtime_inputs(**kwargs)["status"] == "CANDIDATE_UNVERIFIED"
    refused(dict(kwargs, staged_backend_sha256=sha(INDEPENDENT_HELPER)), "code_closure_incomplete")


def test_without_a_staged_request_a_committed_verifier_stays_out_of_the_plan(tmp_path):
    plan = plan_mod.plan_runtime_inputs(**make_case(tmp_path))
    assert VERIFIER not in plan["code_paths"] and "staged_runtime" not in plan


def test_the_committed_staged_profile_includes_the_real_verifier_and_transport(tmp_path):
    root, head = _repository_root()
    blobs = {path: subprocess.run([GIT, "-C", str(root), "cat-file", "blob", head + ":" + path],
                                  capture_output=True, check=True).stdout for path in (HELPER, TRANSPORT, VERIFIER)}
    site = make_tree(tmp_path / "site", {"pkg.py": b"VALUE = 1\n"})
    model = make_tree(tmp_path / "model", {"model.safetensors": WEIGHTS})
    plan = plan_mod.plan_runtime_inputs(git_executable=GIT, repository=str(root), commit=head,
        code_roots=["tools/wd_q4_fixed_peer.py"], site_root=str(site), model_root=str(model),
        expected_model_files=["model.safetensors"], expected_weights_sha256=sha(WEIGHTS), generation=GENERATION,
        revision=REVISION, max_bytes=256 * 1024 * 1024, timeout_seconds=300, enabled=True,
        staged_backend_sha256=sha(blobs[HELPER]))
    assert plan["staged_runtime"] == {name: _entry(path, blobs) for name, path in
                                      (("backend", HELPER), ("transport", TRANSPORT), ("verifier", VERIFIER))}
    assert set(CONTROLLED) <= set(plan["code_paths"])
