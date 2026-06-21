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


def test_tools_and_tests_mark_weak_hashes_non_security() -> None:
    missing: list[str] = []
    for scan_root in SCAN_ROOTS:
        for path in scan_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            hashlib_modules = _hashlib_module_names(tree)
            hashlib_functions = _hashlib_weak_hash_function_names(tree)
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
                if not _has_usedforsecurity_false(node):
                    rel_path = path.relative_to(ROOT).as_posix()
                    missing.append(
                        f"{rel_path}:{node.lineno}: hashlib.{func_name} missing "
                        "usedforsecurity=False"
                    )

    assert missing == []
