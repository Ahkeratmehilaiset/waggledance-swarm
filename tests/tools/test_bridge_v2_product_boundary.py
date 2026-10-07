"""Synthetic static fixtures. Authorised to write, explicitly NOT RUN at delivery."""
import pytest

from tools import check_bridge_v2_product_boundary as boundary
from tools.check_bridge_v2_product_boundary import check_boundary


BASE = "a" * 40
HEAD = "b" * 40


def inspect(files, *, base=None, changes=None, entries=None, package=None, **overrides):
    base = {} if base is None else base
    args = dict(base_sha=BASE, head_sha=HEAD, base_sources=base,
                head_sources=files,
                changed_paths=sorted(set(base) | set(files)) if changes is None else changes,
                entrypoints=["tools/main.py"] if entries is None else entries,
                package_files=sorted(files) if package is None else package,
                snapshots_complete=True, stdlib_modules=["ast", "json", "importlib"])
    args.update(overrides)
    return check_boundary(**args)


def test_source_only_success_never_authorizes_execution():
    result = inspect({"tools/main.py": "from tools.helper import value\n",
                      "tools/helper.py": "value = 1\n"})
    assert result["status"] == "source_separated"
    assert result["closure"] == ["tools/helper.py", "tools/main.py"]
    assert result["binding_verified"] is False
    assert result["execution_allowed"] is False
    assert result["production_ready"] is False


@pytest.mark.parametrize("path", ["waggledance/core/x.py", "CLAUDE.md",
                                 "ops/windows/reboot/x.ps1", ".agent-bridge/bin/x.ps1"])
def test_protected_edits_and_deletions_refused(path):
    result = inspect({"tools/main.py": "pass\n"},
                     base={path: "old"}, changes=[path, "tools/main.py"])
    assert result["status"] == "refused"
    assert any(f["path"] == path for f in result["violations"])


@pytest.mark.parametrize("source", ["import waggledance.core.work_queue\n",
                                   "from waggledance.core import work_queue\n"])
def test_direct_product_import_refused_even_if_missing(source):
    assert inspect({"tools/main.py": source})["status"] == "refused"


def test_transitive_product_import_refused():
    files = {"tools/main.py": "from tools import helper\n",
             "tools/helper.py": "import waggledance.core.bridge_event_schema\n"}
    assert inspect(files)["status"] == "refused"


def test_package_initializer_is_part_of_closure():
    files = {"tools/main.py": "import tools.nested.helper\n",
             "tools/nested/helper.py": "pass\n",
             "tools/nested/__init__.py": "import waggledance\n"}
    result = inspect(files)
    assert result["status"] == "refused"
    assert "tools/nested/__init__.py" in result["closure"]


def test_relative_import_success():
    files = {"tools/main.py": "from .helper import value\n",
             "tools/helper.py": "value = 1\n"}
    assert inspect(files)["status"] == "source_separated"


@pytest.mark.parametrize("source", [
    "import absent_library\n", "from tools.helper import absent\n",
    "from tools.helper import *\n", "from ..outside import x\n",
    "import importlib\nimportlib.import_module(name)\n",
    "from importlib import import_module as load\nload(name)\n",
    "import importlib as loader\nlookup = loader.__dict__\n",
    "import json\njson.__getattribute__(name)\n",
    "loader = __import__\nloader(name)\n", "run = exec\nrun(code)\n",
    "getattr(obj, name)\n", "def broken(\n"])
def test_uncertain_source_never_green(source):
    assert inspect({"tools/main.py": source, "tools/helper.py": "value=1\n"})[
        "status"] == "unknown"


@pytest.mark.parametrize("source", ["import json.missing\n", "from json import missing\n",
                                   "from json import loads\n"])
def test_stdlib_root_does_not_prove_submodule_or_attribute(source):
    assert inspect({"tools/main.py": source})["status"] == "unknown"


def test_declared_bare_stdlib_import_success():
    assert inspect({"tools/main.py": "import json\n"})["status"] == "source_separated"


def test_dependency_omitted_from_package_unknown():
    files = {"tools/main.py": "import tools.helper\n", "tools/helper.py": "pass\n"}
    result = inspect(files, package=["tools/main.py"])
    assert result["status"] == "unknown"
    assert any(x["reason"] == "source_dependency_not_packaged" for x in result["unknown"])


def test_missing_declared_package_file_unknown():
    assert inspect({"tools/main.py": "pass\n"},
                   package=["tools/main.py", "configs/missing.json"])["status"] == "unknown"


@pytest.mark.parametrize("overrides", [
    {"base_sha": "short"}, {"head_sha": None}, {"snapshots_complete": False},
    {"snapshots_complete": 1}, {"changed_paths": []}, {"entrypoints": []},
    {"package_files": []}, {"package_files": ["tools/main.py", "tools/main.py"]},
    {"entrypoints": ["../main.py"]}, {"stdlib_modules": ["waggledance"]},
    {"stdlib_modules": ["tools"]}, {"head_sources": {"tools\\main.py": "pass"}},
    {"head_sha": BASE}, {"entrypoints": ["tools/main.ps1"]}])
def test_bad_declarations_unknown(overrides):
    assert inspect({"tools/main.py": "pass\n"}, **overrides)["status"] == "unknown"


def test_module_package_collision_unknown():
    files = {"tools/main.py": "import tools.helper\n", "tools/helper.py": "pass\n",
             "tools/helper/__init__.py": "pass\n"}
    assert inspect(files)["status"] == "unknown"


def test_stdlib_declared_but_local_shadow_is_traversed():
    files = {"tools/main.py": "import json\n", "json.py": "import waggledance\n"}
    result = inspect(files, changes=["tools/main.py"], base={"json.py": files["json.py"]})
    assert result["status"] == "refused"


def test_unreachable_packaged_python_still_checked():
    files = {"tools/main.py": "pass\n", "tools/unused.py": "import unknown\n"}
    assert inspect(files)["status"] == "unknown"


def test_analysis_never_executes_declared_source():
    # This literal would raise if executed. AST inspection has no execution port.
    result = inspect({"tools/main.py": "raise RuntimeError('must not execute')\n"})
    assert result["status"] == "source_separated"


def test_input_dictionaries_not_modified():
    files = {"tools/main.py": "pass\n"}
    before = dict(files)
    inspect(files)
    assert files == before


def test_protected_change_omitted_from_diff_still_refused():
    files = {"tools/main.py": "pass\n", "waggledance/core/x.py": "pass\n"}
    result = inspect(files, changes=["tools/main.py"], package=["tools/main.py"])
    assert result["status"] == "refused"
    assert any(x["reason"] == "changed_paths_snapshot_mismatch" for x in result["unknown"])


def test_conditional_product_import_is_not_assumed_unreachable():
    source = "if False:\n    import waggledance.core.work_queue\n"
    assert inspect({"tools/main.py": source})["status"] == "refused"


def test_changed_unpackaged_python_cannot_hide_product_import():
    files = {"tools/main.py": "pass\n", "tools/hidden.py": "import waggledance\n"}
    result = inspect(files, package=["tools/main.py"])
    assert result["status"] == "refused"
    assert any(x["reason"] == "source_dependency_not_packaged" for x in result["unknown"])


@pytest.mark.parametrize("source, reason", [
    ("lookup = __builtins__['__import__']\n", "dynamic_execution_or_lookup:__builtins__"),
    ("loader = __loader__\n", "dynamic_execution_or_lookup:__loader__"),
    ("spec = __spec__\n", "dynamic_execution_or_lookup:__spec__"),
    ("kind = obj.__class__\n", "dynamic_import_or_search_path:__class__"),
    ("classes = obj.__subclasses__()\n", "dynamic_import_or_search_path:__subclasses__"),
    ("bases = obj.__bases__\n", "dynamic_import_or_search_path:__bases__"),
    ("order = obj.__mro__\n", "dynamic_import_or_search_path:__mro__"),
    ("loader = obj.__loader__\n", "dynamic_import_or_search_path:__loader__"),
    ("spec = obj.__spec__\n", "dynamic_import_or_search_path:__spec__"),
    ("lookup = obj.__builtins__\n", "dynamic_import_or_search_path:__builtins__"),
])
def test_reflection_unknown_and_nonreflective_success_twin(source, reason):
    result = inspect({"tools/main.py": source})
    assert result["status"] == "unknown"
    assert {"path": "tools/main.py", "reason": reason} in result["unknown"]
    twin = inspect({"tools/main.py": "value = {'loader': 'ordinary data'}\n"})
    assert twin["status"] == "source_separated"
    assert twin["execution_allowed"] is False


@pytest.mark.parametrize("limit, value, files, base, reason", [
    ("MAX_FILES", 1, {"tools/main.py": "pass\n"},
     {"tools/main.py": "old\n"}, "source_file_count_bound_exceeded"),
    ("MAX_FILE_BYTES", 5, {"tools/main.py": "#ééé\n"}, {}, "source_size_bound_exceeded"),
    ("MAX_TOTAL_BYTES", 10, {"tools/main.py": "pass\n"}, {},
     "source_total_bytes_bound_exceeded"),
    ("MAX_PATH_BYTES", 5, {"tools/main.py": "pass\n"}, {}, "source_size_bound_exceeded"),
    ("MAX_DECLARATIONS", 0, {"tools/main.py": "pass\n"}, {},
     "invalid_or_duplicate_declarations"),
])
def test_input_bound_unknown_before_parser(monkeypatch, limit, value, files, base, reason):
    def must_not_parse(*args, **kwargs):
        pytest.fail("input bound must be checked before parsing")
    with monkeypatch.context() as patch:
        patch.setattr(boundary, limit, value)
        patch.setattr(boundary.ast, "parse", must_not_parse)
        result = inspect(files, base=base)
    assert result["status"] == "unknown"
    assert {"path": "", "reason": reason} in result["unknown"]
    assert inspect({"tools/main.py": "pass\n"})["status"] == "source_separated"


def test_total_byte_boundary_includes_paths_and_both_snapshots(monkeypatch):
    files = {"tools/main.py": "pass\n"}
    total = 2 * (len("tools/main.py".encode()) + len("pass\n".encode()))
    monkeypatch.setattr(boundary, "MAX_TOTAL_BYTES", total)
    result = inspect(files, base=files, changes=[])
    assert result["status"] == "source_separated"
    monkeypatch.setattr(boundary, "MAX_TOTAL_BYTES", total - 1)
    assert inspect(files, base=files, changes=[])["status"] == "unknown"


@pytest.mark.parametrize("source", ["pass\n", "from tools.helper import value\n"])
def test_ast_budget_including_export_resolution_and_success_twin(monkeypatch, source):
    files = {"tools/main.py": source, "tools/helper.py": "value=1\n"}
    with monkeypatch.context() as patch:
        patch.setattr(boundary, "MAX_AST_NODES", 1)
        result = inspect(files)
    assert result["status"] == "unknown"
    assert any(item["reason"] == "ast_node_bound_exceeded" for item in result["unknown"])
    assert inspect(files)["status"] == "source_separated"


def test_ast_budget_applies_before_semantic_walk(monkeypatch):
    def must_not_walk(*args, **kwargs):
        pytest.fail("over-budget AST must not reach semantic walk")
    monkeypatch.setattr(boundary, "MAX_AST_NODES", 1)
    monkeypatch.setattr(boundary.ast, "walk", must_not_walk)
    assert inspect({"tools/main.py": "pass\n"})["status"] == "unknown"


def test_export_dependency_cannot_bypass_ast_budget(monkeypatch):
    # main sorts before zhelper; parsing the latter is triggered by exports.
    monkeypatch.setattr(boundary, "MAX_AST_NODES", 5)
    files = {"tools/main.py": "from tools.zhelper import value\n",
             "tools/zhelper.py": "value=1\n"}
    result = inspect(files)
    assert result["status"] == "unknown"
    assert {"path": "tools/zhelper.py", "reason": "ast_node_bound_exceeded"} in result["unknown"]


def test_parsed_dependency_cached_and_budget_not_recounted(monkeypatch):
    original = boundary.ast.parse
    parsed = []
    def record_parse(source, *, filename):
        parsed.append(filename)
        return original(source, filename=filename)
    monkeypatch.setattr(boundary.ast, "parse", record_parse)
    files = {"tools/main.py": "from tools.helper import value\nfrom tools.helper import value\n",
             "tools/helper.py": "value=1\n"}
    assert inspect(files)["status"] == "source_separated"
    assert sorted(parsed) == sorted(files)


@pytest.mark.parametrize("source", ["#\ud800\n", "'\udfff'\n"])
def test_unencodable_snapshot_unknown(source):
    result = inspect({"tools/main.py": source})
    assert result["status"] == "unknown"
    assert {"path": "", "reason": "invalid_source_encoding"} in result["unknown"]


def test_bounds_are_declared_but_never_authority():
    result = inspect({"tools/main.py": "pass\n"})
    assert result["input_bounds"]["files"] == boundary.MAX_FILES
    assert result["input_bounds"]["ast_nodes"] == boundary.MAX_AST_NODES
    assert result["binding_verified"] is False
    assert result["authority_effect"] == "none"
