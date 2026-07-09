# SPDX-License-Identifier: BUSL-1.1
"""Read-only audit for claim_safe read/write paths before any live flip.

The audit is intentionally conservative: tests and docs may contain
``claim_safe=True`` fixtures, but production paths must not contain literal
true writes while the WD image manifest is still pre-live. The manifest counters
remain the authoritative live-state check.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


SCHEMA_VERSION = "wd_claim_safe_read_path_audit.v1"
CLAIM_SAFE_FIELDS = frozenset(
    {"claim_safe", "literal_claim_safe", "literal_future_claim_safe"}
)
PRODUCTION_PREFIXES = (
    "core/",
    "configs/",
    "schemas/",
    "tools/",
    "waggledance/",
    "manifest.json",
    "pyproject.toml",
)
IGNORED_PREFIXES = (
    "tests/",
    "docs/",
    ".codex-audit/",
)


class _ClaimSafeVisitor(ast.NodeVisitor):
    def __init__(self, relpath: str) -> None:
        self.relpath = relpath
        self.reads: list[dict[str, Any]] = []
        self.writes: list[dict[str, Any]] = []

    def visit_Dict(self, node: ast.Dict) -> None:  # noqa: N802
        for key, value in zip(node.keys, node.values):
            field = _constant_string(key)
            if field in CLAIM_SAFE_FIELDS:
                self._record_write(field, value, "dict_key")
        self.generic_visit(node)

    def visit_keyword(self, node: ast.keyword) -> None:  # noqa: N802
        if node.arg in CLAIM_SAFE_FIELDS:
            self._record_write(node.arg, node.value, "keyword_arg")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if isinstance(node.func, ast.Attribute):
            method = node.func.attr
            if method in {"get", "pop"} and node.args:
                field = _constant_string(node.args[0])
                if field in CLAIM_SAFE_FIELDS:
                    self._record_read(field, node, f"mapping_{method}")
            if method in {"setdefault"} and node.args:
                field = _constant_string(node.args[0])
                if field in CLAIM_SAFE_FIELDS:
                    value_node = node.args[1] if len(node.args) > 1 else node
                    self._record_write(field, value_node, "mapping_setdefault")
        if isinstance(node.func, ast.Name) and node.func.id == "setattr":
            if len(node.args) >= 3:
                field = _constant_string(node.args[1])
                if field in CLAIM_SAFE_FIELDS:
                    self._record_write(field, node.args[2], "setattr")
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        for target in node.targets:
            self._record_target_write(target, node.value, "assignment")
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:  # noqa: N802
        if node.value is not None:
            self._record_target_write(node.target, node.value, "ann_assignment")
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:  # noqa: N802
        self._record_target_write(node.target, node, "aug_assignment")
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:  # noqa: N802
        field = _subscript_string(node.slice)
        if field in CLAIM_SAFE_FIELDS and isinstance(node.ctx, ast.Load):
            self._record_read(field, node, "subscript_load")
        self.generic_visit(node)

    def _record_target_write(
        self,
        target: ast.AST,
        value: ast.AST,
        kind: str,
    ) -> None:
        field = _target_claim_safe_field(target)
        if field in CLAIM_SAFE_FIELDS:
            self._record_write(field, value, kind)

    def _record_read(self, field: str, node: ast.AST, kind: str) -> None:
        self.reads.append(
            {
                "path": self.relpath,
                "line": int(getattr(node, "lineno", 0) or 0),
                "field": field,
                "kind": kind,
            }
        )

    def _record_write(self, field: str, node: ast.AST, kind: str) -> None:
        literal = None
        if isinstance(node, ast.Constant) and isinstance(node.value, bool):
            literal = node.value
        self.writes.append(
            {
                "path": self.relpath,
                "line": int(getattr(node, "lineno", 0) or 0),
                "field": field,
                "kind": kind,
                "literal_bool": literal,
            }
        )


def _constant_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _subscript_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant):
        return _constant_string(node)
    return None


def _target_claim_safe_field(node: ast.AST) -> str | None:
    if isinstance(node, ast.Subscript):
        return _subscript_string(node.slice)
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _git_tracked_files(root: Path) -> list[Path] | None:
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:  # noqa: BLE001 - temp roots used in tests may not be repos
        return None
    return [
        root / line.strip()
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def _fallback_files(root: Path) -> list[Path]:
    ignored_parts = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
    }
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and not any(part in ignored_parts for part in path.parts)
    ]


def _scan_files(
    root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    reads: list[dict[str, Any]] = []
    writes: list[dict[str, Any]] = []
    errors: list[str] = []
    files = _git_tracked_files(root) or _fallback_files(root)
    for path in sorted(files):
        if path.suffix != ".py" or not path.exists():
            continue
        relpath = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relpath)
        except (OSError, SyntaxError, UnicodeDecodeError) as exc:
            if _is_production_path(relpath):
                errors.append(f"{relpath}:{type(exc).__name__}")
            continue
        visitor = _ClaimSafeVisitor(relpath)
        visitor.visit(tree)
        reads.extend(visitor.reads)
        writes.extend(visitor.writes)
    return reads, writes, errors


def _is_production_path(relpath: str) -> bool:
    if relpath.startswith(IGNORED_PREFIXES):
        return False
    return relpath.startswith(PRODUCTION_PREFIXES) or relpath in PRODUCTION_PREFIXES


def _paths(items: list[dict[str, Any]]) -> list[str]:
    return sorted({str(item["path"]) for item in items})


def _current_manifest_gate(root: Path) -> dict[str, Any]:
    from tools.build_wd_vision_progress_counters import (
        build_vision_progress_counters,
    )
    from tools.wd_image1_capability_manifest import build_manifest

    manifest_stdout = io.StringIO()
    with contextlib.redirect_stdout(manifest_stdout):
        counters = build_vision_progress_counters(build_manifest(root))
    return {
        "counters_ok": counters.get("ok") is True,
        "claim_safe_count": counters.get("summary", {}).get("claim_safe_count"),
        "all_literal_claims_safe": counters.get("summary", {}).get(
            "all_literal_claims_safe"
        ),
        "claim_safe_flip_applied": counters.get("guardrails", {}).get(
            "claim_safe_flip_applied"
        ),
    }


def build_claim_safe_read_path_audit(
    root: str | Path = ROOT,
    *,
    include_manifest_gate: bool = True,
) -> dict[str, Any]:
    root_path = Path(root).resolve()
    reads, writes, scan_errors = _scan_files(root_path)
    production_writes = [
        item for item in writes if _is_production_path(str(item["path"]))
    ]
    production_true_literals = [
        item for item in production_writes
        if item.get("literal_bool") is True
    ]
    manifest_gate = (
        _current_manifest_gate(root_path)
        if include_manifest_gate
        else {
            "counters_ok": None,
            "claim_safe_count": None,
            "all_literal_claims_safe": None,
            "claim_safe_flip_applied": None,
        }
    )
    blockers: list[str] = []
    blockers.extend(f"scan_error:{item}" for item in scan_errors)
    blockers.extend(
        "production_true_claim_safe_literal:"
        f"{item['path']}:{item['line']}:{item['field']}"
        for item in production_true_literals
    )
    if include_manifest_gate:
        if manifest_gate.get("counters_ok") is not True:
            blockers.append("manifest_counters_not_ok")
        if manifest_gate.get("claim_safe_count") != 0:
            blockers.append(
                f"manifest_claim_safe_count_nonzero:"
                f"{manifest_gate.get('claim_safe_count')}"
            )
        if manifest_gate.get("all_literal_claims_safe") is True:
            blockers.append("manifest_all_literal_claims_safe_true")
        if manifest_gate.get("claim_safe_flip_applied") is not False:
            blockers.append("manifest_claim_safe_flip_guardrail_not_false")

    return {
        "schema_version": SCHEMA_VERSION,
        "ok": not blockers,
        "blockers": blockers,
        "root": str(root_path),
        "read_path_count": len(reads),
        "write_path_count": len(writes),
        "production_write_path_count": len(production_writes),
        "production_true_literal_count": len(production_true_literals),
        "read_path_files": _paths(reads),
        "write_path_files": _paths(writes),
        "production_write_path_files": _paths(production_writes),
        "production_true_literals": production_true_literals,
        "manifest_gate": manifest_gate,
        "guardrails": {
            "read_only": True,
            "external_writes_applied": False,
            "runtime_authority_changed": False,
            "bridge_event_written": False,
            "github_mutation_performed": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--skip-manifest-gate",
        action="store_true",
        help="Only scan files; used by isolated unit tests.",
    )
    args = parser.parse_args(argv)
    audit = build_claim_safe_read_path_audit(
        args.root,
        include_manifest_gate=not args.skip_manifest_gate,
    )
    if args.json:
        print(json.dumps(audit, indent=2, sort_keys=True))
    else:
        print(
            "claim_safe read-path audit: "
            f"ok={audit['ok']} blockers={len(audit['blockers'])} "
            f"reads={audit['read_path_count']} "
            f"production_writes={audit['production_write_path_count']} "
            f"true_literals={audit['production_true_literal_count']}"
        )
        for blocker in audit["blockers"]:
            print(f"- {blocker}")
    return 0 if audit["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
