"""Guard non-runtime test/tool weak-hash calls against B324 regressions."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCAN_ROOTS = (ROOT / "tools", ROOT / "tests")
WEAK_HASHES = {"md5", "sha1"}


def _hashlib_module_names(tree: ast.AST) -> set[str]:
    names = {"hashlib"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import):
            continue
        for alias in node.names:
            if alias.name == "hashlib":
                names.add(alias.asname or alias.name)
    return names


def _hashlib_weak_hash_function_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.module != "hashlib":
            continue
        for alias in node.names:
            if alias.name in WEAK_HASHES:
                names.add(alias.asname or alias.name)
    return names


def _is_tracked_weak_hash_call(
    node: ast.Call,
    *,
    hashlib_modules: set[str],
    hashlib_functions: set[str],
) -> str | None:
    func = node.func
    if (
        isinstance(func, ast.Attribute)
        and func.attr in WEAK_HASHES
        and isinstance(func.value, ast.Name)
        and func.value.id in hashlib_modules
    ):
        return func.attr
    if isinstance(func, ast.Name) and func.id in hashlib_functions:
        return func.id
    return None


def _has_usedforsecurity_false(node: ast.Call) -> bool:
    for keyword in node.keywords:
        if keyword.arg != "usedforsecurity":
            continue
        return isinstance(keyword.value, ast.Constant) and keyword.value.value is False
    return False


def _explicit_security_sha1_exceptions(tree: ast.AST, rel_path: str) -> set[ast.Call]:
    """Allow only the explicit SHA-1 Git-blob integrity check in the pinned planner."""
    if rel_path != "tools/build_q4_runtime_plan.py":
        return set()
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_blob"]
    if len(functions) != 1:
        return set()
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    expected_test = ast.dump(ast.parse("len(oid) == 40", mode="eval").body)
    expected_else = ast.dump(ast.parse("hashlib.sha256()", mode="eval").body)
    allowed = []
    for node in ast.walk(functions[0]):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "hashlib"
            and node.func.attr == "sha1"
            and not node.args
            and len(node.keywords) == 1
            and node.keywords[0].arg == "usedforsecurity"
            and isinstance(node.keywords[0].value, ast.Constant)
            and node.keywords[0].value.value is True
        ):
            continue
        conditional = parents.get(node)
        assignment = parents.get(conditional)
        if not (
            isinstance(conditional, ast.IfExp)
            and conditional.body is node
            and ast.dump(conditional.test) == expected_test
            and ast.dump(conditional.orelse) == expected_else
            and isinstance(assignment, ast.Assign)
            and len(assignment.targets) == 1
            and isinstance(assignment.targets[0], ast.Name)
            and assignment.targets[0].id == "recomputed"
        ):
            continue
        scope = parents.get(assignment)
        while scope is not None and not isinstance(
            scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
        ):
            scope = parents.get(scope)
        if scope is functions[0]:
            allowed.append(node)
    return set(allowed) if len(allowed) == 1 else set()


def test_tools_and_tests_mark_weak_hashes_non_security() -> None:
    missing: list[str] = []
    for scan_root in SCAN_ROOTS:
        for path in scan_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            hashlib_modules = _hashlib_module_names(tree)
            hashlib_functions = _hashlib_weak_hash_function_names(tree)
            rel_path = path.relative_to(ROOT).as_posix()
            security_exceptions = _explicit_security_sha1_exceptions(tree, rel_path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func_name = _is_tracked_weak_hash_call(
                    node,
                    hashlib_modules=hashlib_modules,
                    hashlib_functions=hashlib_functions,
                )
                if func_name is None:
                    continue
                if not _has_usedforsecurity_false(node) and node not in security_exceptions:
                    missing.append(
                        f"{rel_path}:{node.lineno}: hashlib.{func_name} missing "
                        "usedforsecurity=False"
                    )

    assert missing == []


def test_security_sha1_exception_requires_exact_path_function_expression_and_marker() -> None:
    valid = (
        "import hashlib\n"
        "def _blob(git_executable, repository, oid, size, deadline):\n"
        "    recomputed = hashlib.sha1(usedforsecurity=True) if len(oid) == 40 else hashlib.sha256()\n"
    )
    path = "tools/build_q4_runtime_plan.py"
    tree = ast.parse(valid)
    assert len(_explicit_security_sha1_exceptions(tree, path)) == 1
    cases = (
        (valid, "tools/other.py"),
        (valid.replace("def _blob(", "def other("), path),
        (valid.replace("usedforsecurity=True", ""), path),
        (valid.replace("usedforsecurity=True", "usedforsecurity=False"), path),
        (valid.replace("hashlib.sha1(usedforsecurity=True)", "hashlib.sha1(b'x', usedforsecurity=True)"), path),
        (valid.replace("recomputed =", "other ="), path),
        (valid.replace("len(oid) == 40", "len(oid) == 64"), path),
        (valid.replace("hashlib.sha256()", "hashlib.sha512()"), path),
        ("class Holder:\n" + "".join("    " + line for line in valid.splitlines(keepends=True)), path),
        ("def _blob():\n    def nested():\n        recomputed = hashlib.sha1(usedforsecurity=True) if len(oid) == 40 else hashlib.sha256()\n", path),
        (valid + valid[valid.index("def _blob"):], path),
    )
    for source, rel_path in cases:
        assert _explicit_security_sha1_exceptions(ast.parse(source), rel_path) == set()


def test_other_sha1_and_md5_calls_remain_guarded() -> None:
    source = (
        "import hashlib\n"
        "def _blob():\n"
        "    recomputed = hashlib.sha1(usedforsecurity=True) if len(oid) == 40 else hashlib.sha256()\n"
        "    first = hashlib.sha1()\n"
        "    second = hashlib.md5(usedforsecurity=True)\n"
        "    third = hashlib.sha1(usedforsecurity=False)\n"
    )
    tree = ast.parse(source)
    allowed = _explicit_security_sha1_exceptions(tree, "tools/build_q4_runtime_plan.py")
    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _is_tracked_weak_hash_call(
            node, hashlib_modules=_hashlib_module_names(tree), hashlib_functions=_hashlib_weak_hash_function_names(tree)
        )
        if name and not _has_usedforsecurity_false(node) and node not in allowed:
            missing.append(name)
    assert sorted(missing) == ["md5", "sha1"]


def test_blob_oid_verification_uses_security_sha1_and_refuses_mismatch(monkeypatch) -> None:
    import pytest
    from tools import build_q4_runtime_plan as planner

    payload = b"synthetic blob"
    oid = "37455c8e8295296d94948753ce8d3a412ba640a7"
    real_sha1 = planner.hashlib.sha1
    calls = []
    monkeypatch.setattr(planner, "_git", lambda *args: bytearray(payload))

    def observing_sha1(**kwargs):
        calls.append(kwargs)
        return real_sha1(**kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(planner.hashlib, "sha1", observing_sha1)
        assert planner._blob("synthetic", "synthetic", oid, len(payload), 1) == payload
        with pytest.raises(planner.RuntimePlanRefused, match="^code_entry_invalid$"):
            planner._blob("synthetic", "synthetic", "0" * 40, len(payload), 1)

    assert calls == [{"usedforsecurity": True}, {"usedforsecurity": True}]


def test_actual_guard_accepts_only_the_scoped_security_call(tmp_path, monkeypatch) -> None:
    import pytest

    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    planner = tools_dir / "build_q4_runtime_plan.py"
    planner.write_text(
        "import hashlib\n"
        "def _blob():\n"
        "    recomputed = hashlib.sha1(usedforsecurity=True) if len(oid) == 40 else hashlib.sha256()\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    monkeypatch.setitem(globals(), "SCAN_ROOTS", (tools_dir,))
    test_tools_and_tests_mark_weak_hashes_non_security()

    other = tools_dir / "other.py"
    other.write_text("import hashlib\nx = hashlib.sha1()\n", encoding="utf-8")
    with pytest.raises(AssertionError, match=r"tools/other\.py:2: hashlib\.sha1 missing"):
        test_tools_and_tests_mark_weak_hashes_non_security()

    other.write_text("import hashlib\nx = hashlib.sha1(usedforsecurity=False)\n", encoding="utf-8")
    test_tools_and_tests_mark_weak_hashes_non_security()
