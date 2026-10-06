"""Pure, conservative source-boundary analysis of caller-supplied snapshots.

No Git, filesystem, process, provider or runtime access. Full SHA strings label
inputs; this function cannot establish that supplied bytes belong to those SHAs.
``source_separated`` describes only the declared static source closure, never
deployment, authority, authentication, capacity or functional equivalence.
"""
from __future__ import annotations

import ast
import re
from collections.abc import Mapping, Sequence
from typing import Any

# Fixed policy ceilings, not caller-controlled overrides. Source/path byte
# counts use UTF-8. Files count both snapshots; AST nodes count unique parsed
# head files, including trees used to resolve imported exports.
MAX_FILES = 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 256 * 1024
MAX_AST_NODES = 100_000
MAX_PATH_BYTES = 4096
MAX_DECLARATIONS = 4096


def _path(value: Any) -> bool:
    return (isinstance(value, str) and bool(value) and
            not any(c in value for c in "\\:\x00\r\n") and
            all(part not in ("", ".", "..") for part in value.split("/")))


def _module(path: str) -> str | None:
    if not path.endswith(".py"):
        return None
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if parts and all(p.isidentifier() for p in parts) else None


def _exports(tree: ast.Module) -> set[str]:
    """Only explicit top-level names; never infer star/dynamic exports."""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names.update(t.id for target in targets for t in ast.walk(target)
                         if isinstance(t, ast.Name) and isinstance(t.ctx, ast.Store))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(a.asname or a.name.split(".")[0] for a in node.names
                         if a.name != "*")
    return names


def check_boundary(*, base_sha: str, head_sha: str,
                   base_sources: Mapping[str, str], head_sources: Mapping[str, str],
                   changed_paths: Sequence[str], entrypoints: Sequence[str],
                   package_files: Sequence[str], stdlib_modules: Sequence[str] = (),
                   snapshots_complete: bool = False) -> dict[str, Any]:
    """Analyze declared sources without executing or reading them.

    Supply complete base/head source maps (including non-Python package files),
    exact changed paths, Python entrypoints, package paths and an independently
    reviewed exact stdlib-module list. Unverified stdlib attributes, third-party
    and unresolved imports remain UNKNOWN (even when likely harmless).
    Package files are authoritative only as *declared inputs*, not verified
    deployment manifests. Package initializers participate in the closure.
    Bounds limit input work, not parser wall time or arbitrary Python behavior.
    Callers must supply ordinary, trusted containers (not executable Mapping
    implementations) and independently bind snapshot bytes to their SHAs.
    """
    violations: set[tuple[str, str]] = set()
    unknown: set[tuple[str, str]] = set()
    visited: set[str] = set()

    def report() -> dict[str, Any]:
        status = "refused" if violations else "unknown" if unknown else "source_separated"
        return {"schema": "wd.bridge-product-boundary.v1", "status": status,
                "base_sha": base_sha, "head_sha": head_sha,
                "violations": [{"path": p, "reason": r} for p, r in sorted(violations)],
                "unknown": [{"path": p, "reason": r} for p, r in sorted(unknown)],
                "closure": sorted(visited), "binding_verified": False,
                "evidence_scope": "caller-declared static source snapshots only",
                "execution_allowed": False, "production_ready": False,
                "input_bounds": {"files": MAX_FILES, "total_bytes": MAX_TOTAL_BYTES,
                                 "per_file_bytes": MAX_FILE_BYTES,
                                 "ast_nodes": MAX_AST_NODES,
                                 "path_bytes": MAX_PATH_BYTES,
                                 "declarations_per_list": MAX_DECLARATIONS},
                "authority_effect": "none"}

    if any(not isinstance(s, str) or re.fullmatch(r"[0-9a-fA-F]{40}", s) is None
           for s in (base_sha, head_sha)):
        unknown.add(("", "invalid_exact_sha"))
    if snapshots_complete is not True:
        unknown.add(("", "snapshot_coverage_unverified"))
    if not isinstance(base_sources, Mapping) or not isinstance(head_sources, Mapping):
        unknown.add(("", "invalid_source_snapshot"))
        return report()
    if len(base_sources) + len(head_sources) > MAX_FILES:
        unknown.add(("", "source_file_count_bound_exceeded"))
        return report()
    total_bytes = 0
    for sources in (base_sources, head_sources):
        for path, source in sources.items():
            if not isinstance(path, str) or not isinstance(source, str):
                unknown.add(("", "invalid_source_snapshot"))
                return report()
            # Character checks bound the allocation made by UTF-8 encoding.
            if len(path) > MAX_PATH_BYTES or len(source) > MAX_FILE_BYTES:
                unknown.add(("", "source_size_bound_exceeded"))
                return report()
            try:
                path_bytes = len(path.encode("utf-8"))
                source_bytes = len(source.encode("utf-8"))
            except UnicodeError:
                unknown.add(("", "invalid_source_encoding"))
                return report()
            if path_bytes > MAX_PATH_BYTES or source_bytes > MAX_FILE_BYTES:
                unknown.add(("", "source_size_bound_exceeded"))
                return report()
            total_bytes += path_bytes + source_bytes
            if total_bytes > MAX_TOTAL_BYTES:
                unknown.add(("", "source_total_bytes_bound_exceeded"))
                return report()
            if not _path(path):
                unknown.add(("", "invalid_source_snapshot"))
                return report()
    lists = (changed_paths, entrypoints, package_files, stdlib_modules)
    if any(not isinstance(items, (list, tuple)) or len(items) > MAX_DECLARATIONS or
           any(not isinstance(item, str) or len(item) > MAX_PATH_BYTES for item in items) or
           len(set(items)) != len(items) for items in lists):
        unknown.add(("", "invalid_or_duplicate_declarations"))
        return report()
    try:
        if any(len(item.encode("utf-8")) > MAX_PATH_BYTES for items in lists for item in items):
            unknown.add(("", "declaration_size_bound_exceeded"))
            return report()
    except UnicodeError:
        unknown.add(("", "invalid_declaration_encoding"))
        return report()
    if any(not _path(p) for items in lists[:3] for p in items):
        unknown.add(("", "noncanonical_declared_path"))
        return report()
    if any(not all(part.isidentifier() for part in m.split(".")) or
           m.split(".")[0] in {"tools", "tests", "configs", "waggledance"}
           for m in stdlib_modules):
        unknown.add(("", "invalid_stdlib_roots"))
        return report()
    if not entrypoints or not package_files:
        unknown.add(("", "empty_entrypoint_or_package_coverage"))
    derived = {p for p in set(base_sources) | set(head_sources)
               if base_sources.get(p) != head_sources.get(p)}
    if derived != set(changed_paths):
        unknown.add(("", "changed_paths_snapshot_mismatch"))
    if base_sha == head_sha and derived:
        unknown.add(("", "identical_sha_different_bytes"))
    for p in set(changed_paths) | derived:
        if p.split("/", 1)[0] not in {"tools", "configs", "tests"}:
            violations.add((p, "protected_or_out_of_scope_path_changed"))

    modules: dict[str, str] = {}
    for p in sorted(head_sources):
        name = _module(p)
        if name:
            if name in modules:
                unknown.add((p, "ambiguous_module_and_package"))
            else:
                modules[name] = p
    packaged = set(package_files)
    for p in package_files:
        if p not in head_sources:
            unknown.add((p, "package_source_missing"))
        if p.split("/", 1)[0] not in {"tools", "configs", "tests"}:
            violations.add((p, "product_or_out_of_scope_package_file"))
    for p in entrypoints:
        if not p.endswith(".py"):
            unknown.add((p, "unsupported_entrypoint_kind"))
        if p not in packaged:
            unknown.add((p, "entrypoint_not_packaged"))
    pending = (set(entrypoints) | {p for p in packaged if p.endswith(".py")} |
               {p for p in derived if p.endswith(".py") and p in head_sources})
    standard = set(stdlib_modules)
    trees: dict[str, ast.Module | None] = {}
    node_count = 0
    ast_budget_exhausted = False

    def parse(path: str) -> ast.Module | None:
        nonlocal node_count, ast_budget_exhausted
        if path in trees:
            return trees[path]
        trees[path] = None
        if ast_budget_exhausted:
            return None
        try:
            tree = ast.parse(head_sources[path], filename=path)
            # Depth-first iterator stack avoids ast.walk's breadth-sized queue.
            # Reject before exports or import/reflection analysis sees this tree.
            stack = [iter((tree,))]
            while stack:
                node = next(stack[-1], None)
                if node is None:
                    stack.pop()
                    continue
                node_count += 1
                if node_count > MAX_AST_NODES:
                    ast_budget_exhausted = True
                    unknown.add((path, "ast_node_bound_exceeded"))
                    return None
                stack.append(ast.iter_child_nodes(node))
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            unknown.add((path, "unparseable_python_source"))
            return None
        trees[path] = tree
        return tree

    exports_by_path: dict[str, set[str]] = {}

    def resolve(name: str, origin: str) -> str | None:
        root = name.split(".")[0]
        if root == "waggledance":
            violations.add((origin, "product_import:" + name))
            return None
        path = modules.get(name)
        if path:
            pending.add(path)
            # Importing a dotted module executes all present parent packages.
            parts = name.split(".")
            for i in range(1, len(parts)):
                parent = modules.get(".".join(parts[:i]))
                if parent:
                    pending.add(parent)
            return path
        if name in standard and root not in modules:
            return None
        # Namespace packages have no initializer to execute, but named children
        # must still resolve below. Never treat arbitrary unresolved names as namespaces.
        if any(m.startswith(name + ".") for m in modules):
            return None
        unknown.add((origin, "unresolved_import:" + name))
        return None

    while pending:
        path = min(pending)
        pending.remove(path)
        if path in visited:
            continue
        visited.add(path)
        if path.split("/", 1)[0] not in {"tools", "configs", "tests"}:
            violations.add((path, "product_or_out_of_scope_source_dependency"))
        if path not in packaged:
            unknown.add((path, "source_dependency_not_packaged"))
        source = head_sources.get(path)
        if source is None or _module(path) is None:
            unknown.add((path, "missing_or_unsupported_python_source"))
            continue
        tree = parse(path)
        if tree is None:
            if ast_budget_exhausted:
                break
            continue
        name = _module(path) or ""
        package = name if path.endswith("/__init__.py") else name.rpartition(".")[0]
        # Also visit initializers when the entrypoint itself is a nested module.
        parts = package.split(".") if package else []
        for i in range(1, len(parts) + 1):
            parent = modules.get(".".join(parts[:i]))
            if parent:
                pending.add(parent)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    resolve(alias.name, path)
                    if alias.name.split(".")[0] in {"importlib", "runpy", "pkgutil", "zipimport"}:
                        unknown.add((path, "dynamic_loader_module:" + alias.name))
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    levels = package.split(".") if package else []
                    if node.level > len(levels):
                        unknown.add((path, "relative_import_outside_package"))
                        continue
                    prefix = levels[:len(levels) - node.level + 1]
                    target = ".".join(prefix + ([node.module] if node.module else []))
                else:
                    target = node.module or ""
                if target.split(".")[0] in {"importlib", "runpy", "pkgutil", "zipimport"}:
                    # A renamed loader (from importlib import import_module as f)
                    # must not evade attribute-based detection.
                    unknown.add((path, "dynamic_loader_module:" + target))
                if target == "sys" and any(a.name in {"path", "meta_path", "path_hooks"}
                                           for a in node.names):
                    unknown.add((path, "imported_search_path"))
                if target == "builtins":
                    unknown.add((path, "indirect_builtin_access"))
                dependency = resolve(target, path)
                for alias in node.names:
                    if alias.name == "*":
                        unknown.add((path, "star_import"))
                        continue
                    child = target + "." + alias.name
                    if child in modules:
                        resolve(child, path)
                    elif dependency:
                        if dependency not in exports_by_path:
                            dependency_tree = parse(dependency)
                            exports_by_path[dependency] = (
                                _exports(dependency_tree) if dependency_tree is not None else set())
                        exports = exports_by_path[dependency]
                        if alias.name not in exports:
                            unknown.add((path, "unresolved_imported_name:" + child))
                    elif target in standard:
                        unknown.add((path, "unverified_stdlib_imported_name:" + child))
                    else:
                        unknown.add((path, "unresolved_imported_name:" + child))
            elif isinstance(node, ast.Name) and node.id in {
                    "__builtins__", "__loader__", "__spec__", "__import__",
                    "exec", "eval", "compile", "getattr", "globals", "locals", "vars"}:
                unknown.add((path, "dynamic_execution_or_lookup:" + node.id))
            elif isinstance(node, ast.Attribute) and node.attr in {
                    "import_module", "exec_module", "load_module", "spec_from_file_location",
                    "SourceFileLoader", "path", "meta_path", "path_hooks", "__dict__",
                    "__class__", "__subclasses__", "__bases__", "__mro__",
                    "__builtins__", "__loader__", "__spec__",
                    "__getattribute__", "__import__", "exec", "eval", "compile", "getattr"}:
                unknown.add((path, "dynamic_import_or_search_path:" + node.attr))
            if ast_budget_exhausted:
                break
    return report()
