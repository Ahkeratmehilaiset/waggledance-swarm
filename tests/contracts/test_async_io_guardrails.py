# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WAGGLEDANCE_ROOT = PROJECT_ROOT / "waggledance"
CHAT_SERVICE = WAGGLEDANCE_ROOT / "application" / "services" / "chat_service.py"

BLOCKING_FULL_NAMES = {
    "feedparser.parse",
    "requests.delete",
    "requests.get",
    "requests.patch",
    "requests.post",
    "requests.put",
    "requests.request",
    "sqlite3.connect",
    "urllib.request.urlopen",
}

BLOCKING_METHOD_NAMES = {
    # Initial set (PR #289, L33):
    "record_runtime_gap_signal",
    "record_runtime_gap_signal_many",
    "save_case",
    # Expansion from L33 deep iteration: ControlPlaneDB write methods that
    # also consume a sqlite write transaction. Adding to the set is purely
    # additive -- it can only catch MORE violations, never introduce false
    # positives, because the visit_Await/asyncio.to_thread exemption still
    # applies. Verified clean against current main (0 violations) before
    # adding.
    "emit_growth_event",
    "record_autogrowth_run",
    "record_builder_job",
    "record_cutover_state",
    "record_promotion_decision",
    "record_promotion_state",
    "record_provider_job",
    "record_shadow_evaluation",
    "record_validation_run",
    "set_growth_intent_status",
    "set_meta",
    "set_solver_capability_features",
    "upsert_capability",
    "upsert_family_policy",
    "upsert_growth_intent",
    "upsert_solver",
    "upsert_solver_artifact",
    "upsert_solver_family",
}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _import_aliases(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _call_name(node: ast.AST, aliases: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value, aliases)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _is_to_thread_call(node: ast.AST, aliases: dict[str, str]) -> bool:
    if not isinstance(node, ast.Call):
        return False
    return _call_name(node.func, aliases) in {
        "asyncio.to_thread",
        "anyio.to_thread.run_sync",
    }


class _AsyncBlockingIoVisitor(ast.NodeVisitor):
    def __init__(self, path: Path, aliases: dict[str, str]) -> None:
        self.path = path
        self.aliases = aliases
        self.async_stack: list[str] = []
        self.violations: list[str] = []

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.async_stack.append(node.name)
        for child in node.body:
            self.visit(child)
        self.async_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # Nested sync functions are allowed to contain blocking work when the
        # enclosing async function passes them to asyncio.to_thread.
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for child in node.body:
            self.visit(child)

    def visit_Await(self, node: ast.Await) -> None:
        if _is_to_thread_call(node.value, self.aliases):
            return
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if self.async_stack:
            name = _call_name(node.func, self.aliases)
            method_name = name.rsplit(".", 1)[-1]
            if name in BLOCKING_FULL_NAMES or method_name in BLOCKING_METHOD_NAMES:
                rel_path = self.path.relative_to(PROJECT_ROOT).as_posix()
                self.violations.append(f"{rel_path}:{node.lineno}: {name}")
        self.generic_visit(node)


def _async_blocking_io_violations() -> list[str]:
    violations: list[str] = []
    for path in sorted(WAGGLEDANCE_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = _parse(path)
        visitor = _AsyncBlockingIoVisitor(path, _import_aliases(tree))
        visitor.visit(tree)
        violations.extend(visitor.violations)
    return violations


def _async_function(tree: ast.Module, name: str) -> ast.AsyncFunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"async function not found: {name}")


def _to_thread_first_args(fn: ast.AsyncFunctionDef, aliases: dict[str, str]) -> set[str]:
    args: set[str] = set()
    for node in ast.walk(fn):
        if (
            isinstance(node, ast.Await)
            and isinstance(node.value, ast.Call)
            and _is_to_thread_call(node.value, aliases)
            and node.value.args
        ):
            args.add(_call_name(node.value.args[0], aliases))
    return args


def test_async_functions_do_not_call_known_blocking_io_directly() -> None:
    violations = _async_blocking_io_violations()
    assert violations == []


def test_chat_case_store_write_stays_off_event_loop() -> None:
    tree = _parse(CHAT_SERVICE)
    aliases = _import_aliases(tree)
    fn = _async_function(tree, "_record_case")

    assert "self._case_store.save_case" in _to_thread_first_args(fn, aliases)


def test_low_confidence_gap_write_stays_off_event_loop() -> None:
    tree = _parse(CHAT_SERVICE)
    aliases = _import_aliases(tree)
    fn = _async_function(tree, "_record_low_confidence_gap")

    assert "_record_signal" in _to_thread_first_args(fn, aliases)
