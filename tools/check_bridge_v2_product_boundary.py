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
                "authority_effect": "none"}

    if any(not isinstance(s, str) or re.fullmatch(r"[0-9a-fA-F]{40}", s) is None
           for s in (base_sha, head_sha)):
        unknown.add(("", "invalid_exact_sha"))
    if snapshots_complete is not True:
        unknown.add(("", "snapshot_coverage_unverified"))
    if (not isinstance(base_sources, Mapping) or not isinstance(head_sources, Mapping)
            or any(not _path(k) or not isinstance(v, str)
                   for sources in (base_sources, head_sources) for k, v in sources.items())):
        unknown.add(("", "invalid_source_snapshot"))
        return report()
    lists = (changed_paths, entrypoints, package_files, stdlib_modules)
    if any(not isinstance(items, (list, tuple)) or
           any(not isinstance(item, str) for item in items) or
           len(set(items)) != len(items) for items in lists):
        unknown.add(("", "invalid_or_duplicate_declarations"))
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
        try:
            tree = ast.parse(source, filename=path)
        except (SyntaxError, ValueError, RecursionError):
            unknown.add((path, "unparseable_python_source"))
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
                        try:
                            exports = _exports(ast.parse(head_sources[dependency]))
                        except (SyntaxError, ValueError, RecursionError):
                            exports = set()
                        if alias.name not in exports:
                            unknown.add((path, "unresolved_imported_name:" + child))
                    elif target in standard:
                        unknown.add((path, "unverified_stdlib_imported_name:" + child))
                    else:
                        unknown.add((path, "unresolved_imported_name:" + child))
            elif isinstance(node, ast.Name) and node.id in {
                    "__import__", "exec", "eval", "compile", "getattr", "globals", "locals", "vars"}:
                unknown.add((path, "dynamic_execution_or_lookup:" + node.id))
            elif isinstance(node, ast.Attribute) and node.attr in {
                    "import_module", "exec_module", "load_module", "spec_from_file_location",
                    "SourceFileLoader", "path", "meta_path", "path_hooks", "__dict__",
                    "__getattribute__", "__import__", "exec", "eval", "compile", "getattr"}:
                unknown.add((path, "dynamic_import_or_search_path:" + node.attr))
    return report()
