"""Synthetic isolated outer bootstrap; no actual model, deployment or network."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
OUTER = ROOT / "tools/wd_q4_public_outer.py"
VERIFIER = ROOT / "sandbox/mind/step2/runtime_manifest.py"
GENERATION = "a" * 40
REVISION = "b" * 40


def digest(data):
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def synthetic(tmp_path):
    code = tmp_path / "code"
    tools = code / "tools"
    verifier = code / "sandbox/mind/step2/runtime_manifest.py"
    tools.mkdir(parents=True)
    verifier.parent.mkdir(parents=True)
    (tools / "__init__.py").write_bytes(b"")
    (tools / "wd_q4_public_outer.py").write_bytes(OUTER.read_bytes())
    untrusted = tmp_path / "untrusted-pythonpath"
    untrusted.mkdir()
    (untrusted / "probe_dependency.py").write_bytes(b"VALUE='untrusted-pythonpath'\n")
    child = tools / "wd_q4_public_child.py"
    child.write_bytes(b"from probe_dependency import VALUE\ndef run_public(): return VALUE\n")
    query = tools / "wd_q4_public_query.py"
    query.write_bytes(b"def query_public(): return 'synthetic-query'\n")
    verifier.write_bytes(VERIFIER.read_bytes())
    runtime = tmp_path / "runtime"
    marker = tmp_path / "pth-was-executed"
    files = {"site/probe_dependency.py": b"VALUE='verified-site'\n",
             "site/should_not_execute.pth": ("import pathlib; pathlib.Path(%r).write_text('bad')\n" % str(marker)).encode(),
             "model/config.json": b'{"synthetic":true}',
             "assets/code/sandbox/mind/step2/wd_q4_private_read.py": b"# synthetic reader\n"}
    records = []
    for name, raw in files.items():
        path = runtime / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        records.append({"path": name, "size": len(raw), "sha256": digest(raw)})
    raw_manifest = json.dumps({"schema": "wd.q4-runtime-manifest.v1", "generation": GENERATION,
                               "revision": REVISION, "files": records}, sort_keys=True).encode()
    manifest = tmp_path / "runtime-manifest.json"
    manifest.write_bytes(raw_manifest)
    return {"code_root": str(code), "verifier_path": str(verifier), "verifier_sha256": digest(verifier.read_bytes()),
            "public_child_sha256": digest(child.read_bytes()), "public_query_sha256": digest(query.read_bytes()),
            "runtime_root": str(runtime), "runtime_manifest": str(manifest),
            "runtime_manifest_sha256": digest(raw_manifest), "runtime_generation": GENERATION,
            "runtime_revision": REVISION, "marker": marker, "untrusted": untrusted}


def run_outer(plan, *, enabled=True, change=None, isolated=True, preload_untrusted=False, preloaded=None,
              foreign_finder=False, extra_package_path=False, import_poison=None):
    args = {key: value for key, value in plan.items() if key not in ("marker", "untrusted")}
    if change:
        args.update(change)
    code = ("import json,sys\n"
            "sys.path.insert(0," + repr(plan["code_root"]) + ")\n"
            "from tools import wd_q4_public_outer as outer\n"
            + ("sys.path.insert(0," + repr(str(plan["untrusted"])) + ")\n" if preload_untrusted else "") +
            ("import types; sys.modules[" + repr(preloaded) + "]=types.ModuleType(" + repr(preloaded) + ")\n"
             if preloaded else "") +
            ("sys.meta_path.insert(0,object())\n" if foreign_finder else "") +
            ("sys.modules['tools'].__path__.append(" + repr(str(plan["untrusted"])) + ")\n"
             if extra_package_path else "") +
            ("class Poison:\n def find_spec(self,*args,**kwargs):\n  open(" + repr(str(plan["marker"])) + ",'w').write('finder')\n  return None\n"
             "def poison_hook(path):\n open(" + repr(str(plan["marker"])) + ",'w').write('hook')\n raise ImportError()\n"
             "poison=" + repr(import_poison) + "\n"
             "if poison=='cache_tools': sys.path_importer_cache[sys.modules['tools'].__path__[0]]=Poison()\n"
             "if poison=='cache_site': sys.path_importer_cache[" + repr(str(Path(plan["runtime_root"]) / "site")) + "]=Poison()\n"
             "if poison=='cache_site_subdir': sys.path_importer_cache[" + repr(str(Path(plan["runtime_root"]) / "site" / "nested")) + "]=Poison()\n"
             "if poison=='cache_sandbox': sys.path_importer_cache[" + repr(str(Path(plan["code_root"]) / "sandbox")) + "]=Poison()\n"
             "if poison=='cache_sandbox_alias': sys.path_importer_cache[" + repr(str(Path(plan["code_root"]) / "tools" / ".." / "sandbox")) + "]=Poison()\n"
             "if poison=='hook': sys.path_hooks.insert(0,poison_hook)\n"
             "if poison=='mutated_finder': sys.path_importer_cache[sys.modules['tools'].__path__[0]].find_spec=Poison().find_spec\n"
             if import_poison else "") +
            "before=list(sys.path)\n"
            "try:\n"
            " result=outer.bootstrap_public_imports(enabled=" + repr(enabled) + ", **" + repr(args) + ")\n"
            " output={'status':result['status'], 'before_equal':before==sys.path, "
            "'child_value': result['child'].run_public() if result['status']=='MODULES_IMPORTED' else None, "
            "'query_value': result['query'].query_public() if result['status']=='MODULES_IMPORTED' else None}\n"
            "except outer.PublicOuterRefused as exc:\n"
            " output={'status':str(exc), 'before_equal':before==sys.path, "
            "'child_loaded':'tools.wd_q4_public_child' in sys.modules, "
            "'query_loaded':'tools.wd_q4_public_query' in sys.modules}\n"
            "print(json.dumps(output))\n")
    env = dict(os.environ, PYTHONPATH=str(plan["untrusted"]))
    flags = ["-I", "-S", "-B"] if isolated else ["-S", "-B"]
    run = subprocess.run([sys.executable, *flags, "-c", code], capture_output=True,
                         text=True, timeout=30, env=env)
    assert run.returncode == 0, (run.returncode, run.stderr)
    assert not plan["marker"].exists()
    return json.loads(run.stdout)


@pytest.mark.skipif(os.name != "nt", reason="runtime verifier requires Windows deployment semantics")
def test_default_off_no_import_or_site_change(synthetic):
    result = run_outer(synthetic, enabled=False)
    assert result == {"status": "OFF", "before_equal": True, "child_value": None, "query_value": None}


@pytest.mark.skipif(os.name != "nt", reason="runtime verifier requires Windows deployment semantics")
def test_verified_site_precedes_public_import_and_pth_never_executes(synthetic):
    result = run_outer(synthetic)
    assert result == {"status": "MODULES_IMPORTED", "before_equal": False, "child_value": "verified-site",
                      "query_value": "synthetic-query"}


@pytest.mark.skipif(os.name != "nt", reason="runtime verifier requires Windows deployment semantics")
def test_nonisolated_interpreter_refuses_without_path_change_or_public_import(synthetic):
    result = run_outer(synthetic, isolated=False)
    assert result == {"status": "public_outer_refused", "before_equal": True,
                      "child_loaded": False, "query_loaded": False}


@pytest.mark.skipif(os.name != "nt", reason="runtime verifier requires Windows deployment semantics")
def test_untrusted_prefix_before_code_root_refuses(synthetic):
    result = run_outer(synthetic, preload_untrusted=True)
    assert result == {"status": "public_outer_refused", "before_equal": True,
                      "child_loaded": False, "query_loaded": False}


@pytest.mark.skipif(os.name != "nt", reason="runtime verifier requires Windows deployment semantics")
@pytest.mark.parametrize("module_name", ["tools.wd_q4_agent_query", "tools.wd_q4_public_plan", "tools.other"])
def test_preloaded_code_root_dependency_refuses(synthetic, module_name):
    result = run_outer(synthetic, preloaded=module_name)
    assert result == {"status": "public_outer_refused", "before_equal": True,
                      "child_loaded": False, "query_loaded": False}


@pytest.mark.skipif(os.name != "nt", reason="runtime verifier requires Windows deployment semantics")
@pytest.mark.parametrize("field", ["foreign_finder", "extra_package_path"])
def test_foreign_import_state_refuses(synthetic, field):
    result = run_outer(synthetic, **{field: True})
    assert result == {"status": "public_outer_refused", "before_equal": True,
                      "child_loaded": False, "query_loaded": False}


@pytest.mark.skipif(os.name != "nt", reason="runtime verifier requires Windows deployment semantics")
@pytest.mark.parametrize("poison", ["cache_tools", "cache_site", "cache_site_subdir", "cache_sandbox", "cache_sandbox_alias", "hook", "mutated_finder"])
def test_poisoned_path_import_state_refuses_without_invoking_it(synthetic, poison):
    result = run_outer(synthetic, import_poison=poison)
    assert result == {"status": "public_outer_refused", "before_equal": True,
                      "child_loaded": False, "query_loaded": False}


@pytest.mark.skipif(os.name != "nt", reason="runtime verifier requires Windows deployment semantics")
@pytest.mark.parametrize("kind", ["verifier_pin", "verifier_tampered", "manifest_pin", "site_tampered",
                                  "child_tampered", "query_tampered", "child_import_fails"])
def test_mismatches_refuse_before_public_import_without_path_change(synthetic, kind):
    change = {}
    if kind == "verifier_pin":
        change["verifier_sha256"] = "0" * 64
    elif kind == "manifest_pin":
        change["runtime_manifest_sha256"] = "0" * 64
    elif kind == "child_import_fails":
        path = Path(synthetic["code_root"]) / "tools/wd_q4_public_child.py"
        path.write_bytes(b"import intentionally_unavailable_synthetic_dependency\n")
        change["public_child_sha256"] = digest(path.read_bytes())
    else:
        path = {"verifier_tampered": Path(synthetic["verifier_path"]),
                "site_tampered": Path(synthetic["runtime_root"]) / "site/probe_dependency.py",
                "child_tampered": Path(synthetic["code_root"]) / "tools/wd_q4_public_child.py",
                "query_tampered": Path(synthetic["code_root"]) / "tools/wd_q4_public_query.py"}[kind]
        path.write_bytes(path.read_bytes() + b"\n#tampered\n")
    result = run_outer(synthetic, change=change)
    assert result == {"status": "public_outer_refused", "before_equal": True,
                      "child_loaded": False, "query_loaded": False}
