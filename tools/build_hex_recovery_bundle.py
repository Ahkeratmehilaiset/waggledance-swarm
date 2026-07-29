#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""Build a deterministic recovery bundle from exact Git objects.

The generator derives topology and capability inputs only from the literal
commit named by ``--expected-head``.  It does not import historical topology
code, read the index, copy live state, contact a remote, start a runtime, or
grant recovery authority.  It does compare the executing generator and its
two imported digest/contract modules with their exact-commit source blobs.

V1 emits the two concrete topology sources that currently exist in Git:

* the seven-cell axial agent mesh; and
* the eight-cell logical solver overlay.

There is no authoritative committed hierarchical runtime topology yet, so V1
does not fabricate one.  The generated manifest remains shadow-only and keeps
all replication, transport, activation, claim-safety, blank-disk, and
production-readiness claims false.
"""
from __future__ import annotations

import argparse
import ast
import ctypes
from dataclasses import dataclass
from datetime import datetime, timezone
import errno
import hashlib
import inspect
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import sys
from types import MappingProxyType
from typing import Any, Mapping, Sequence

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent
if not sys.path or Path(sys.path[0]).resolve() != REPO_ROOT:
    sys.path.insert(0, str(REPO_ROOT))

from waggledance.core.hex_topology.recovery_contract import (  # noqa: E402
    ContractValidationError,
    build_hex_cell_genome,
    build_hive_recovery_manifest,
    validate_hive_recovery_manifest,
)
from waggledance.core.magma.canonical import canonical_json_bytes  # noqa: E402


BUNDLE_BUILD_VERSION = "hex_recovery_bundle_build.v1"
MANIFEST_FILENAME = "hive_recovery_manifest.v1.json"
REPOSITORY_REF = "github.com/Ahkeratmehilaiset/waggledance-swarm"

AGENT_CONFIG_PATH = "configs/hex_cells.yaml"
AGENT_GEOMETRY_PATH = "waggledance/core/domain/hex_mesh.py"
AGENT_REGISTRY_PATH = (
    "waggledance/application/services/hex_topology_registry.py"
)
SOLVER_TOPOLOGY_PATH = "waggledance/core/hex_cell_topology.py"
SYMBOLIC_SOLVER_PATH = "core/symbolic_solver.py"
AXIOM_ROOT = "configs/axioms"

_STATIC_SOURCE_PATHS = frozenset(
    {
        AGENT_CONFIG_PATH,
        AGENT_GEOMETRY_PATH,
        AGENT_REGISTRY_PATH,
        SOLVER_TOPOLOGY_PATH,
        SYMBOLIC_SOLVER_PATH,
    }
)
_RUNTIME_IMPLEMENTATION_PATHS = frozenset(
    {
        "tools/build_hex_recovery_bundle.py",
        "waggledance/core/hex_topology/recovery_contract.py",
        "waggledance/core/magma/canonical.py",
    }
)
_PINNED_EXECUTABLE_TOPOLOGY_DIGESTS = MappingProxyType(
    {
        AGENT_GEOMETRY_PATH: (
            "sha256:8ac142b75fda16b596a4d2b7f5ecb66d6b5f097ee4e102fce5e4c416585041e1"
        ),
        SOLVER_TOPOLOGY_PATH: (
            "sha256:549acfac939860cad88733c4d50265c83a9b9263dfd593b5f4411029acd119c4"
        ),
    }
)
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_OBJECT_ID_RE = re.compile(rb"^[0-9a-f]{40}$")
_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
_AXIOM_PATH_RE = re.compile(
    r"^configs/axioms/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*\.yaml$"
)
_CANONICAL_INT_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
_CANONICAL_FLOAT_RE = re.compile(
    r"^-?(?:0|[1-9][0-9]*)\.[0-9]+(?:[eE][+-]?(?:0|[1-9][0-9]*))?$"
)
_UTC_SECONDS_RE = re.compile(
    r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])"
    r"T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$"
)
_SAFE_OUTPUT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_WINDOWS_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)
_REPARSE_FLAG = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
_MAX_TREE_OUTPUT_BYTES = 1024 * 1024
_MAX_TREE_ENTRIES = 128
_MAX_SINGLE_BLOB_BYTES = 2 * 1024 * 1024
_MAX_TOTAL_BLOB_BYTES = 16 * 1024 * 1024
_MAX_YAML_BYTES = 256 * 1024
_MAX_YAML_DEPTH = 32
_MAX_YAML_NODES = 10_000
_MAX_AST_NODES = 20_000
_MAX_AST_DEPTH = 128
_GIT_TIMEOUT_SECONDS = 30
_CANONICAL_AXIAL_DIRECTIONS = frozenset(
    {(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)}
)


class BundleBuildError(RuntimeError):
    """A stable, redaction-safe bundle build failure."""


@dataclass(frozen=True)
class ExactGitBlob:
    relative_path: str
    mode: str
    object_id: str
    data: bytes
    content_digest: str


@dataclass(frozen=True)
class ExactCommitSnapshot:
    commit_sha: str
    committed_at_utc: str
    blobs_by_path: Mapping[str, ExactGitBlob]


@dataclass(frozen=True)
class HexRecoveryBundleImage:
    commit_sha: str
    manifest: Mapping[str, Any]
    manifest_bytes: bytes
    manifest_file_digest: str
    genome_bytes_by_ref: Mapping[str, bytes]


def _fail(code: str) -> None:
    raise BundleBuildError(code)


def _sha256_bytes(raw: bytes) -> str:
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _strict_utf8(raw: bytes, code: str) -> str:
    if raw.startswith(b"\xef\xbb\xbf") or b"\x00" in raw:
        _fail(code)
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        _fail(code)


def _clean_git_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }
    environment.update(
        {
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C",
            "LANG": "C",
        }
    )
    return environment


def _run_git(
    repo_root: Path,
    arguments: Sequence[str],
    *,
    maximum_stdout_bytes: int,
    error_code: str,
) -> bytes:
    command = [
        "git",
        "--no-pager",
        "--literal-pathspecs",
        "-C",
        os.fspath(repo_root),
        *arguments,
    ]
    try:
        completed = subprocess.run(
            command,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_clean_git_environment(),
            timeout=_GIT_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        _fail(error_code)
    if (
        completed.returncode != 0
        or len(completed.stdout) > maximum_stdout_bytes
        or len(completed.stderr) > 64 * 1024
    ):
        _fail(error_code)
    return bytes(completed.stdout)


def _require_expected_head(repo_root: Path, expected_head: str) -> None:
    if not isinstance(expected_head, str) or _COMMIT_RE.fullmatch(expected_head) is None:
        _fail("expected_head_invalid")

    object_format = _run_git(
        repo_root,
        ["rev-parse", "--show-object-format"],
        maximum_stdout_bytes=32,
        error_code="git_object_format_unavailable",
    )
    if object_format != b"sha1\n":
        _fail("git_object_format_unsupported")

    top_level_raw = _run_git(
        repo_root,
        ["rev-parse", "--show-toplevel"],
        maximum_stdout_bytes=4096,
        error_code="git_repository_unavailable",
    )
    try:
        observed_top_level = Path(os.fsdecode(top_level_raw.rstrip(b"\r\n"))).resolve(
            strict=True
        )
        intended_top_level = repo_root.resolve(strict=True)
    except (OSError, UnicodeError):
        _fail("git_repository_unavailable")
    if observed_top_level != intended_top_level:
        _fail("git_repository_root_mismatch")

    observed_head = _run_git(
        repo_root,
        [
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{expected_head}^{{commit}}",
        ],
        maximum_stdout_bytes=64,
        error_code="expected_commit_unavailable",
    )
    if observed_head != f"{expected_head}\n".encode("ascii"):
        _fail("expected_commit_mismatch")

    symbolic_head = _run_git(
        repo_root,
        ["rev-parse", "--verify", "HEAD"],
        maximum_stdout_bytes=64,
        error_code="head_unavailable",
    )
    if symbolic_head != f"{expected_head}\n".encode("ascii"):
        _fail("head_mismatch")


def _parse_commit_timestamp(raw_commit: bytes) -> str:
    if len(raw_commit) > _MAX_SINGLE_BLOB_BYTES:
        _fail("commit_object_too_large")
    try:
        lines = raw_commit.decode("utf-8", errors="replace").splitlines()
    except (MemoryError, UnicodeError):
        _fail("commit_object_invalid")
    committer_lines = [line for line in lines if line.startswith("committer ")]
    if len(committer_lines) != 1:
        _fail("commit_timestamp_missing")
    match = re.search(r" ([0-9]{1,20}) ([+-][0-9]{4})$", committer_lines[0])
    if match is None:
        _fail("commit_timestamp_invalid")
    try:
        epoch = int(match.group(1), 10)
        moment = datetime.fromtimestamp(epoch, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        _fail("commit_timestamp_invalid")
    rendered = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    if _UTC_SECONDS_RE.fullmatch(rendered) is None:
        _fail("commit_timestamp_invalid")
    return rendered


def _parse_tree_records(raw: bytes) -> dict[str, tuple[str, str]]:
    if not raw or len(raw) > _MAX_TREE_OUTPUT_BYTES:
        _fail("git_tree_inventory_invalid")
    records = raw.split(b"\x00")
    if records[-1] != b"":
        _fail("git_tree_inventory_invalid")
    records.pop()
    if not 1 <= len(records) <= _MAX_TREE_ENTRIES:
        _fail("git_tree_inventory_invalid")

    result: dict[str, tuple[str, str]] = {}
    folded_paths: set[str] = set()
    for record in records:
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_type, object_id = metadata.split(b" ", 2)
            relative_path = raw_path.decode("ascii", errors="strict")
        except (ValueError, UnicodeError):
            _fail("git_tree_entry_invalid")
        if mode != b"100644" or object_type != b"blob":
            _fail("git_tree_entry_type_forbidden")
        if _OBJECT_ID_RE.fullmatch(object_id) is None:
            _fail("git_tree_object_id_invalid")
        if (
            relative_path not in _STATIC_SOURCE_PATHS
            and _AXIOM_PATH_RE.fullmatch(relative_path) is None
        ):
            _fail("git_tree_path_unexpected")
        folded = relative_path.casefold()
        if relative_path in result or folded in folded_paths:
            _fail("git_tree_path_collision")
        result[relative_path] = (
            mode.decode("ascii"),
            object_id.decode("ascii"),
        )
        folded_paths.add(folded)

    if not _STATIC_SOURCE_PATHS.issubset(result):
        _fail("git_required_source_missing")
    axiom_paths = [path for path in result if path.startswith(f"{AXIOM_ROOT}/")]
    if not axiom_paths:
        _fail("git_axiom_sources_missing")
    return result


def _read_exact_blob(
    repo_root: Path,
    relative_path: str,
    mode: str,
    object_id: str,
) -> ExactGitBlob:
    raw_size = _run_git(
        repo_root,
        ["cat-file", "-s", object_id],
        maximum_stdout_bytes=64,
        error_code="git_blob_size_unavailable",
    )
    try:
        size_text = raw_size.decode("ascii", errors="strict").strip()
        if not re.fullmatch(r"(?:0|[1-9][0-9]{0,9})", size_text):
            raise ValueError
        declared_size = int(size_text, 10)
    except (UnicodeError, ValueError):
        _fail("git_blob_size_invalid")
    if declared_size > _MAX_SINGLE_BLOB_BYTES:
        _fail("git_blob_too_large")

    data = _run_git(
        repo_root,
        ["cat-file", "blob", object_id],
        maximum_stdout_bytes=declared_size,
        error_code="git_blob_read_failed",
    )
    if len(data) != declared_size:
        _fail("git_blob_size_mismatch")
    git_identity = hashlib.sha1(
        b"blob " + str(len(data)).encode("ascii") + b"\x00" + data,
        usedforsecurity=False,
    ).hexdigest()
    if git_identity != object_id:
        _fail("git_blob_identity_mismatch")
    return ExactGitBlob(
        relative_path=relative_path,
        mode=mode,
        object_id=object_id,
        data=data,
        content_digest=_sha256_bytes(data),
    )


def _read_literal_git_blobs(
    repo_root: Path,
    commit_sha: str,
    relative_paths: Sequence[str],
) -> dict[str, ExactGitBlob]:
    expected_paths = tuple(sorted(set(relative_paths)))
    if (
        not expected_paths
        or len(expected_paths) != len(relative_paths)
        or any(
            not isinstance(path, str)
            or not path
            or PurePosixPath(path).is_absolute()
            or "\\" in path
            or any(part in {"", ".", ".."} for part in PurePosixPath(path).parts)
            for path in expected_paths
        )
    ):
        _fail("implementation_source_path_invalid")
    tree_raw = _run_git(
        repo_root,
        [
            "ls-tree",
            "-z",
            "--full-tree",
            commit_sha,
            "--",
            *expected_paths,
        ],
        maximum_stdout_bytes=64 * 1024,
        error_code="implementation_source_inventory_unavailable",
    )
    records = tree_raw.split(b"\x00")
    if not records or records[-1] != b"":
        _fail("implementation_source_inventory_invalid")
    records.pop()
    if len(records) != len(expected_paths):
        _fail("implementation_source_inventory_incomplete")

    inventory: dict[str, tuple[str, str]] = {}
    for record in records:
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_type, object_id = metadata.split(b" ", 2)
            relative_path = raw_path.decode("ascii", errors="strict")
        except (ValueError, UnicodeError):
            _fail("implementation_source_inventory_invalid")
        if (
            relative_path not in expected_paths
            or relative_path in inventory
            or mode != b"100644"
            or object_type != b"blob"
            or _OBJECT_ID_RE.fullmatch(object_id) is None
        ):
            _fail("implementation_source_inventory_invalid")
        inventory[relative_path] = (
            mode.decode("ascii"),
            object_id.decode("ascii"),
        )
    if set(inventory) != set(expected_paths):
        _fail("implementation_source_inventory_incomplete")
    return {
        path: _read_exact_blob(repo_root, path, *inventory[path])
        for path in expected_paths
    }


def read_exact_commit_snapshot(
    repo_root: Path,
    expected_head: str,
) -> ExactCommitSnapshot:
    """Read the allowlisted source model from one literal exact commit."""
    root = Path(repo_root)
    _require_expected_head(root, expected_head)

    commit_raw = _run_git(
        root,
        ["cat-file", "commit", expected_head],
        maximum_stdout_bytes=_MAX_SINGLE_BLOB_BYTES,
        error_code="commit_object_unavailable",
    )
    commit_identity = hashlib.sha1(
        b"commit "
        + str(len(commit_raw)).encode("ascii")
        + b"\x00"
        + commit_raw,
        usedforsecurity=False,
    ).hexdigest()
    if commit_identity != expected_head:
        _fail("commit_object_identity_mismatch")
    committed_at_utc = _parse_commit_timestamp(commit_raw)

    requested_paths = [
        *_STATIC_SOURCE_PATHS,
        AXIOM_ROOT,
    ]
    tree_raw = _run_git(
        root,
        [
            "ls-tree",
            "-r",
            "-z",
            "--full-tree",
            expected_head,
            "--",
            *sorted(requested_paths),
        ],
        maximum_stdout_bytes=_MAX_TREE_OUTPUT_BYTES,
        error_code="git_tree_inventory_unavailable",
    )
    inventory = _parse_tree_records(tree_raw)

    blobs: dict[str, ExactGitBlob] = {}
    aggregate_size = 0
    for relative_path in sorted(inventory):
        mode, object_id = inventory[relative_path]
        blob = _read_exact_blob(root, relative_path, mode, object_id)
        aggregate_size += len(blob.data)
        if aggregate_size > _MAX_TOTAL_BLOB_BYTES:
            _fail("git_blob_inventory_too_large")
        blobs[relative_path] = blob

    _require_expected_head(root, expected_head)
    return ExactCommitSnapshot(
        commit_sha=expected_head,
        committed_at_utc=committed_at_utc,
        blobs_by_path=MappingProxyType(blobs),
    )


class _StrictComposeLoader(yaml.SafeLoader):
    """Compose YAML nodes without constructing Python objects."""

    def __init__(self, stream: str) -> None:
        super().__init__(stream)
        self._strict_depth = 0
        self._strict_nodes = 0

    def compose_node(self, parent: Any, index: Any) -> yaml.Node:
        if self.check_event(yaml.AliasEvent):
            _fail("yaml_alias_forbidden")
        event = self.peek_event()
        if getattr(event, "anchor", None) is not None:
            _fail("yaml_anchor_forbidden")
        if getattr(event, "tag", None) is not None:
            _fail("yaml_explicit_tag_forbidden")
        self._strict_depth += 1
        self._strict_nodes += 1
        if (
            self._strict_depth > _MAX_YAML_DEPTH
            or self._strict_nodes > _MAX_YAML_NODES
        ):
            _fail("yaml_complexity_exceeded")
        try:
            return super().compose_node(parent, index)
        finally:
            self._strict_depth -= 1


def _convert_yaml_node(node: yaml.Node) -> Any:
    if isinstance(node, yaml.MappingNode):
        result: dict[str, Any] = {}
        for key_node, value_node in node.value:
            if not isinstance(key_node, yaml.ScalarNode) or key_node.tag != "tag:yaml.org,2002:str":
                _fail("yaml_mapping_key_invalid")
            key = key_node.value
            if key == "<<":
                _fail("yaml_merge_forbidden")
            if key in result:
                _fail("yaml_duplicate_key")
            result[key] = _convert_yaml_node(value_node)
        return result
    if isinstance(node, yaml.SequenceNode):
        return [_convert_yaml_node(item) for item in node.value]
    if not isinstance(node, yaml.ScalarNode):
        _fail("yaml_node_invalid")

    value = node.value
    if len(value) > _MAX_YAML_BYTES:
        _fail("yaml_scalar_too_large")
    if (
        node.tag == "tag:yaml.org,2002:str"
        and node.style is None
        and _CANONICAL_FLOAT_RE.fullmatch(value) is not None
    ):
        try:
            number = float(value)
        except ValueError:
            _fail("yaml_float_invalid")
        if not math.isfinite(number):
            _fail("yaml_float_nonfinite")
        return number
    if node.tag == "tag:yaml.org,2002:str":
        return value
    if node.tag == "tag:yaml.org,2002:bool":
        if value == "true":
            return True
        if value == "false":
            return False
        _fail("yaml_boolean_not_canonical")
    if node.tag == "tag:yaml.org,2002:int":
        if _CANONICAL_INT_RE.fullmatch(value) is None:
            _fail("yaml_integer_not_canonical")
        try:
            return int(value, 10)
        except ValueError:
            _fail("yaml_integer_invalid")
    if node.tag == "tag:yaml.org,2002:float":
        if _CANONICAL_FLOAT_RE.fullmatch(value) is None:
            _fail("yaml_float_not_canonical")
        try:
            number = float(value)
        except ValueError:
            _fail("yaml_float_invalid")
        if not math.isfinite(number):
            _fail("yaml_float_nonfinite")
        return number
    if node.tag == "tag:yaml.org,2002:null" and value == "null":
        return None
    _fail("yaml_scalar_type_forbidden")


def _strict_yaml_load(raw: bytes) -> Any:
    if len(raw) > _MAX_YAML_BYTES:
        _fail("yaml_too_large")
    text = _strict_utf8(raw, "yaml_encoding_invalid")
    loader: _StrictComposeLoader | None = None
    try:
        loader = _StrictComposeLoader(text)
        node = loader.get_single_node()
        if node is None:
            _fail("yaml_document_empty")
        return _convert_yaml_node(node)
    except BundleBuildError:
        raise
    except (MemoryError, RecursionError, yaml.YAMLError):
        _fail("yaml_document_invalid")
    finally:
        if loader is not None:
            loader.dispose()


def _mapping(value: Any, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], code: str) -> None:
    if set(value) != expected:
        _fail(code)


def _token(value: Any, code: str) -> str:
    if not isinstance(value, str) or _TOKEN_RE.fullmatch(value) is None:
        _fail(code)
    return value


def _bounded_string(value: Any, code: str, *, maximum: int = 4096) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        _fail(code)
    return value


def _selector_list(value: Any, code: str) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 256:
        _fail(code)
    selectors = [
        _bounded_string(item, code, maximum=128)
        for item in value
    ]
    if len(set(selectors)) != len(selectors):
        _fail(code)
    return selectors


def _bounded_ast(source: bytes) -> ast.Module:
    text = _strict_utf8(source, "python_source_encoding_invalid")
    try:
        tree = ast.parse(text, filename="<exact-git-object>", mode="exec")
        nodes = list(ast.walk(tree))
    except (MemoryError, RecursionError, SyntaxError, ValueError):
        _fail("python_source_ast_invalid")
    if len(nodes) > _MAX_AST_NODES:
        _fail("python_source_ast_too_large")

    stack: list[tuple[ast.AST, int]] = [(tree, 0)]
    while stack:
        node, depth = stack.pop()
        if depth > _MAX_AST_DEPTH:
            _fail("python_source_ast_too_deep")
        stack.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return tree


def _require_python_class_surface(
    raw: bytes,
    required_classes: Mapping[str, set[str]],
) -> None:
    tree = _bounded_ast(raw)
    observed: dict[str, set[str]] = {}
    for statement in tree.body:
        if not isinstance(statement, ast.ClassDef):
            continue
        methods = {
            child.name
            for child in statement.body
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if statement.name in observed:
            _fail("python_surface_class_duplicate")
        observed[statement.name] = methods
    for class_name, method_names in required_classes.items():
        if class_name not in observed or not method_names.issubset(
            observed[class_name]
        ):
            _fail("python_required_surface_missing")


def _top_level_assignments(
    tree: ast.Module,
    relevant: Any,
) -> dict[str, ast.AST]:
    assignments: dict[str, ast.AST] = {}
    allowed_targets: set[int] = set()
    for statement in tree.body:
        target: ast.expr | None = None
        value: ast.AST | None = None
        if isinstance(statement, ast.Assign):
            if len(statement.targets) != 1:
                names = [
                    item.id for item in statement.targets if isinstance(item, ast.Name)
                ]
                if any(relevant(name) for name in names):
                    _fail("topology_assignment_invalid")
                continue
            target = statement.targets[0]
            value = statement.value
        elif isinstance(statement, ast.AnnAssign):
            target = statement.target
            value = statement.value
        else:
            continue
        if not isinstance(target, ast.Name) or value is None:
            continue
        if not relevant(target.id):
            continue
        if target.id in assignments:
            _fail("topology_assignment_duplicate")
        assignments[target.id] = value
        allowed_targets.add(id(target))

    protected_mutables = {
        name
        for name in {"ALL_CELLS", "_ADJACENCY", "AXIAL_DIRECTIONS"}
        if relevant(name)
    }
    dangerous_namespace_calls = {
        "__import__",
        "compile",
        "delattr",
        "eval",
        "exec",
        "globals",
        "locals",
        "setattr",
        "vars",
    }
    mutation_methods = {
        "add",
        "append",
        "clear",
        "discard",
        "extend",
        "insert",
        "pop",
        "remove",
        "reverse",
        "setdefault",
        "sort",
        "update",
    }

    def root_name(node: ast.AST) -> str | None:
        current = node
        while isinstance(current, (ast.Attribute, ast.Subscript)):
            current = current.value
        return current.id if isinstance(current, ast.Name) else None

    def direct_protected_reference(node: ast.AST) -> bool:
        return root_name(node) in protected_mutables

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Store)
            and node.id == "frozenset"
        ):
            _fail("frozenset_binding_forbidden")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == "frozenset":
                _fail("frozenset_binding_forbidden")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound_name = alias.asname or alias.name.split(".", 1)[0]
                if bound_name == "frozenset":
                    _fail("frozenset_binding_forbidden")
        if isinstance(node, ast.Call):
            if (
                isinstance(node.func, ast.Name)
                and node.func.id in dangerous_namespace_calls
            ):
                _fail("dynamic_namespace_operation_forbidden")
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in {"setitem", "delitem"}
            ):
                _fail("dynamic_namespace_operation_forbidden")
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Store)
            and relevant(node.id)
            and id(node) not in allowed_targets
        ):
            _fail("topology_assignment_indirect")
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete)):
            raw_targets: list[ast.AST] = []
            if isinstance(node, ast.Assign):
                raw_targets.extend(node.targets)
            elif isinstance(node, ast.Delete):
                raw_targets.extend(node.targets)
            else:
                raw_targets.append(node.target)
            for raw_target in raw_targets:
                base = raw_target
                while isinstance(base, (ast.Attribute, ast.Subscript)):
                    base = base.value
                if isinstance(base, ast.Name) and relevant(base.id):
                    if not (
                        isinstance(raw_target, ast.Name)
                        and id(raw_target) in allowed_targets
                    ):
                        _fail("topology_mutation_forbidden")
            value_node: ast.AST | None = None
            if isinstance(node, ast.Assign):
                value_node = node.value
            elif isinstance(node, ast.AnnAssign):
                value_node = node.value
            if value_node is not None and direct_protected_reference(value_node):
                if any(
                    not isinstance(target, ast.Name) or not relevant(target.id)
                    for target in raw_targets
                ):
                    _fail("topology_alias_forbidden")
            for raw_target in raw_targets:
                target_root = root_name(raw_target)
                if target_root in {"__builtins__", "builtins"}:
                    _fail("builtin_mutation_forbidden")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in mutation_methods
        ):
            base = node.func.value
            while isinstance(base, (ast.Attribute, ast.Subscript)):
                base = base.value
            if isinstance(base, ast.Name) and relevant(base.id):
                _fail("topology_mutation_forbidden")
    return assignments


def _signed_int(node: ast.AST) -> int:
    sign = 1
    value_node = node
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        sign = -1 if isinstance(node.op, ast.USub) else 1
        value_node = node.operand
    if (
        not isinstance(value_node, ast.Constant)
        or isinstance(value_node.value, bool)
        or not isinstance(value_node.value, int)
    ):
        _fail("axial_direction_invalid")
    value = sign * value_node.value
    if not -1_000_000 <= value <= 1_000_000:
        _fail("axial_direction_invalid")
    return value


def _extract_axial_directions(raw: bytes) -> tuple[tuple[int, int], ...]:
    tree = _bounded_ast(raw)
    assignments = _top_level_assignments(
        tree,
        lambda name: name == "AXIAL_DIRECTIONS",
    )
    node = assignments.get("AXIAL_DIRECTIONS")
    if not isinstance(node, ast.List):
        _fail("axial_directions_missing")
    directions: list[tuple[int, int]] = []
    for item in node.elts:
        if not isinstance(item, ast.Tuple) or len(item.elts) != 2:
            _fail("axial_direction_invalid")
        directions.append((_signed_int(item.elts[0]), _signed_int(item.elts[1])))
    if (
        len(directions) != 6
        or len(set(directions)) != 6
        or frozenset(directions) != _CANONICAL_AXIAL_DIRECTIONS
    ):
        _fail("axial_directions_invalid")
    return tuple(directions)


def _extract_solver_topology(
    raw: bytes,
) -> tuple[tuple[str, ...], dict[str, tuple[str, ...]]]:
    tree = _bounded_ast(raw)
    assignments = _top_level_assignments(
        tree,
        lambda name: name.startswith("CELL_")
        or name in {"ALL_CELLS", "_ADJACENCY"},
    )

    cell_assignments = {
        name: node for name, node in assignments.items() if name.startswith("CELL_")
    }
    if len(cell_assignments) != 8:
        _fail("solver_cell_constants_invalid")
    cell_values: dict[str, str] = {}
    for name, node in cell_assignments.items():
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            _fail("solver_cell_constant_invalid")
        cell_values[name] = _token(node.value, "solver_cell_id_invalid")
    if len(set(cell_values.values())) != len(cell_values):
        _fail("solver_cell_id_duplicate")

    all_cells_node = assignments.get("ALL_CELLS")
    if not isinstance(all_cells_node, ast.List):
        _fail("solver_all_cells_invalid")
    all_cell_names: list[str] = []
    for item in all_cells_node.elts:
        if not isinstance(item, ast.Name) or item.id not in cell_values:
            _fail("solver_all_cells_invalid")
        all_cell_names.append(item.id)
    if (
        len(all_cell_names) != len(cell_values)
        or len(set(all_cell_names)) != len(all_cell_names)
        or set(all_cell_names) != set(cell_values)
    ):
        _fail("solver_all_cells_incomplete")
    cell_ids = tuple(cell_values[name] for name in all_cell_names)

    adjacency_node = assignments.get("_ADJACENCY")
    if not isinstance(adjacency_node, ast.Dict):
        _fail("solver_adjacency_invalid")
    if len(adjacency_node.keys) != len(cell_values):
        _fail("solver_adjacency_incomplete")
    adjacency: dict[str, tuple[str, ...]] = {}
    for key_node, value_node in zip(adjacency_node.keys, adjacency_node.values):
        if not isinstance(key_node, ast.Name) or key_node.id not in cell_values:
            _fail("solver_adjacency_key_invalid")
        cell_id = cell_values[key_node.id]
        if cell_id in adjacency:
            _fail("solver_adjacency_key_duplicate")
        if (
            not isinstance(value_node, ast.Call)
            or not isinstance(value_node.func, ast.Name)
            or value_node.func.id != "frozenset"
            or len(value_node.args) != 1
            or value_node.keywords
            or not isinstance(value_node.args[0], ast.Set)
        ):
            _fail("solver_adjacency_value_invalid")
        neighbors: list[str] = []
        for neighbor_node in value_node.args[0].elts:
            if (
                not isinstance(neighbor_node, ast.Name)
                or neighbor_node.id not in cell_values
            ):
                _fail("solver_adjacency_neighbor_invalid")
            neighbors.append(cell_values[neighbor_node.id])
        if len(neighbors) != len(set(neighbors)):
            _fail("solver_adjacency_neighbor_duplicate")
        adjacency[cell_id] = tuple(sorted(neighbors))

    _validate_graph(adjacency)
    if set(adjacency) != set(cell_ids):
        _fail("solver_adjacency_incomplete")
    return cell_ids, adjacency


def _load_agent_cells(raw: bytes) -> tuple[dict[str, Any], ...]:
    document = _mapping(_strict_yaml_load(raw), "agent_config_not_mapping")
    _exact_keys(document, {"cells"}, "agent_config_keys_invalid")
    raw_cells = document.get("cells")
    if not isinstance(raw_cells, list) or len(raw_cells) != 7:
        _fail("agent_cell_count_invalid")

    result: list[dict[str, Any]] = []
    ids: set[str] = set()
    coordinates: set[tuple[int, int]] = set()
    expected_keys = {
        "id",
        "coord",
        "description",
        "domain_selectors",
        "tag_selectors",
        "enabled",
        "neighbor_policy",
    }
    for raw_cell in raw_cells:
        cell = _mapping(raw_cell, "agent_cell_not_mapping")
        _exact_keys(cell, expected_keys, "agent_cell_keys_invalid")
        cell_id = _token(cell.get("id"), "agent_cell_id_invalid")
        if cell_id in ids:
            _fail("agent_cell_id_duplicate")
        coord = _mapping(cell.get("coord"), "agent_cell_coord_invalid")
        _exact_keys(coord, {"q", "r"}, "agent_cell_coord_invalid")
        q = coord.get("q")
        r = coord.get("r")
        if (
            isinstance(q, bool)
            or isinstance(r, bool)
            or not isinstance(q, int)
            or not isinstance(r, int)
            or not -1_000_000 <= q <= 1_000_000
            or not -1_000_000 <= r <= 1_000_000
        ):
            _fail("agent_cell_coord_invalid")
        if (q, r) in coordinates:
            _fail("agent_cell_coord_duplicate")
        _bounded_string(cell.get("description"), "agent_cell_description_invalid")
        _selector_list(
            cell.get("domain_selectors"),
            "agent_domain_selectors_invalid",
        )
        _selector_list(
            cell.get("tag_selectors"),
            "agent_tag_selectors_invalid",
        )
        if cell.get("enabled") is not True:
            _fail("agent_cell_disabled")
        if cell.get("neighbor_policy") != "default":
            _fail("agent_neighbor_policy_unsupported")
        result.append({"cell_id": cell_id, "q": q, "r": r})
        ids.add(cell_id)
        coordinates.add((q, r))
    return tuple(result)


def _agent_adjacency(
    cells: Sequence[Mapping[str, Any]],
    directions: Sequence[tuple[int, int]],
) -> dict[str, tuple[str, ...]]:
    by_coord = {
        (int(cell["q"]), int(cell["r"])): str(cell["cell_id"]) for cell in cells
    }
    adjacency: dict[str, tuple[str, ...]] = {}
    for cell in cells:
        q = int(cell["q"])
        r = int(cell["r"])
        neighbors = tuple(
            by_coord[(q + dq, r + dr)]
            for dq, dr in directions
            if (q + dq, r + dr) in by_coord
        )
        adjacency[str(cell["cell_id"])] = neighbors
    _validate_graph(adjacency)
    return adjacency


def _connected(
    adjacency: Mapping[str, Sequence[str]],
    removed: str | None = None,
) -> bool:
    remaining = [cell for cell in adjacency if cell != removed]
    if not remaining:
        return False
    seen = {remaining[0]}
    pending = [remaining[0]]
    while pending:
        cell = pending.pop()
        for neighbor in adjacency[cell]:
            if neighbor == removed or neighbor in seen:
                continue
            seen.add(neighbor)
            pending.append(neighbor)
    return len(seen) == len(remaining)


def _graph_diameter(adjacency: Mapping[str, Sequence[str]]) -> int:
    maximum = 0
    for origin in adjacency:
        distances = {origin: 0}
        pending = [origin]
        while pending:
            cell = pending.pop(0)
            for neighbor in adjacency[cell]:
                if neighbor not in distances:
                    distances[neighbor] = distances[cell] + 1
                    pending.append(neighbor)
        if len(distances) != len(adjacency):
            _fail("topology_disconnected")
        maximum = max(maximum, max(distances.values(), default=0))
    return maximum


def _validate_graph(adjacency: Mapping[str, Sequence[str]]) -> None:
    if len(adjacency) < 3:
        _fail("topology_too_small")
    cell_ids = set(adjacency)
    for cell_id, neighbors in adjacency.items():
        if (
            len(neighbors) < 2
            or len(neighbors) != len(set(neighbors))
            or cell_id in neighbors
            or not set(neighbors).issubset(cell_ids)
        ):
            _fail("topology_neighbors_invalid")
        for neighbor in neighbors:
            if cell_id not in adjacency[neighbor]:
                _fail("topology_neighbors_asymmetric")
    if not _connected(adjacency):
        _fail("topology_disconnected")
    if any(not _connected(adjacency, removed=cell) for cell in adjacency):
        _fail("topology_single_cell_survival_failed")


def _topology_epoch(
    mesh_id: str,
    mesh_kind: str,
    cells: Sequence[Mapping[str, Any]],
) -> int:
    projection = {
        "mesh_id": mesh_id,
        "mesh_kind": mesh_kind,
        "cells": list(cells),
    }
    digest = hashlib.sha256(canonical_json_bytes(projection)).hexdigest()
    return int(digest[:15], 16)


def _repo_input(input_id: str, blob: ExactGitBlob) -> dict[str, Any]:
    return {
        "input_id": input_id,
        "kind": "repo_artifact",
        "source_ref": blob.relative_path,
        "source_digest": blob.content_digest,
        "rebuild_strategy": "git_checkout",
        "replay_checkpoint": None,
        "required": True,
    }


def _capability(
    kind: str,
    capability_id: str,
    blob: ExactGitBlob,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "capability_id": capability_id,
        "source_ref": blob.relative_path,
        "source_digest": blob.content_digest,
        "required": True,
    }


def _snapshot_blobs(snapshot: ExactCommitSnapshot) -> dict[str, ExactGitBlob]:
    if (
        not isinstance(snapshot, ExactCommitSnapshot)
        or _COMMIT_RE.fullmatch(snapshot.commit_sha) is None
        or _UTC_SECONDS_RE.fullmatch(snapshot.committed_at_utc) is None
    ):
        _fail("snapshot_invalid")
    try:
        parsed_timestamp = datetime.strptime(
            snapshot.committed_at_utc,
            "%Y-%m-%dT%H:%M:%SZ",
        ).replace(tzinfo=timezone.utc)
    except ValueError:
        _fail("snapshot_timestamp_invalid")
    if parsed_timestamp.strftime("%Y-%m-%dT%H:%M:%SZ") != snapshot.committed_at_utc:
        _fail("snapshot_timestamp_invalid")

    if not isinstance(snapshot.blobs_by_path, Mapping):
        _fail("snapshot_blobs_invalid")
    blobs = dict(snapshot.blobs_by_path)
    if not _STATIC_SOURCE_PATHS.issubset(blobs):
        _fail("snapshot_required_source_missing")
    if not any(path.startswith(f"{AXIOM_ROOT}/") for path in blobs):
        _fail("snapshot_axioms_missing")
    aggregate = 0
    folded_paths: set[str] = set()
    for path, blob in blobs.items():
        if isinstance(blob, ExactGitBlob) and isinstance(blob.data, bytes):
            computed_object_id = hashlib.sha1(
                b"blob "
                + str(len(blob.data)).encode("ascii")
                + b"\x00"
                + blob.data,
                usedforsecurity=False,
            ).hexdigest()
        else:
            computed_object_id = ""
        if (
            not isinstance(path, str)
            or (
                path not in _STATIC_SOURCE_PATHS
                and _AXIOM_PATH_RE.fullmatch(path) is None
            )
            or not isinstance(blob, ExactGitBlob)
            or blob.relative_path != path
            or blob.mode != "100644"
            or not isinstance(blob.object_id, str)
            or _OBJECT_ID_RE.fullmatch(
                blob.object_id.encode("ascii", errors="ignore")
            )
            is None
            or blob.object_id != computed_object_id
            or not isinstance(blob.data, bytes)
            or blob.content_digest != _sha256_bytes(blob.data)
        ):
            _fail("snapshot_blob_invalid")
        folded = path.casefold()
        if folded in folded_paths:
            _fail("snapshot_path_collision")
        aggregate += len(blob.data)
        if len(blob.data) > _MAX_SINGLE_BLOB_BYTES or aggregate > _MAX_TOTAL_BLOB_BYTES:
            _fail("snapshot_blob_inventory_too_large")
        folded_paths.add(folded)
    return blobs


def _axioms_by_cell(
    blobs: Mapping[str, ExactGitBlob],
    solver_cells: Sequence[str],
) -> dict[str, tuple[tuple[str, ExactGitBlob], ...]]:
    by_cell: dict[str, list[tuple[str, ExactGitBlob]]] = {
        cell_id: [] for cell_id in solver_cells
    }
    seen_models: set[str] = set()
    for path in sorted(blobs):
        if not path.startswith(f"{AXIOM_ROOT}/"):
            continue
        document = _mapping(
            _strict_yaml_load(blobs[path].data),
            "axiom_document_not_mapping",
        )
        model_id = _token(document.get("model_id"), "axiom_model_id_invalid")
        cell_id = _token(document.get("cell_id"), "axiom_cell_id_invalid")
        formulas = document.get("formulas")
        variables = document.get("variables")
        output_schema = document.get("solver_output_schema")
        if (
            not isinstance(formulas, list)
            or not formulas
            or not isinstance(variables, Mapping)
            or not variables
            or not isinstance(output_schema, Mapping)
            or not output_schema
        ):
            _fail("axiom_executable_shape_missing")
        for formula in formulas:
            formula_mapping = _mapping(formula, "axiom_formula_invalid")
            _bounded_string(
                formula_mapping.get("name"),
                "axiom_formula_name_invalid",
                maximum=128,
            )
            _bounded_string(
                formula_mapping.get("formula"),
                "axiom_formula_expression_invalid",
                maximum=4096,
            )
        if model_id in seen_models:
            _fail("axiom_model_id_duplicate")
        if cell_id not in by_cell:
            _fail("axiom_cell_id_unknown")
        by_cell[cell_id].append((model_id, blobs[path]))
        seen_models.add(model_id)
    if any(not items for items in by_cell.values()):
        _fail("solver_cell_axioms_missing")
    return {
        cell_id: tuple(
            sorted(items, key=lambda item: (item[0], item[1].relative_path))
        )
        for cell_id, items in by_cell.items()
    }


def _genome_artifact(
    mesh_id: str,
    cell_id: str,
    genome: Mapping[str, Any],
) -> tuple[str, bytes, dict[str, Any], dict[str, Any]]:
    genome_ref = f"genomes/{mesh_id}/{cell_id}.json"
    raw = canonical_json_bytes(genome)
    raw_digest = _sha256_bytes(raw)
    artifact = {
        "artifact_id": f"genome.{mesh_id}.{cell_id}",
        "relative_path": genome_ref,
        "content_digest": raw_digest,
        "byte_size": len(raw),
        "classification": "genome",
        "required": True,
        "restore_strategy": "verified_copy",
    }
    cell_ref = {
        "cell_id": cell_id,
        "genome_ref": genome_ref,
        "genome_digest": genome["genome_digest"],
        "expected_cell_state_root": genome["expected_cell_state_root"],
    }
    return genome_ref, raw, artifact, cell_ref


def _build_hex_recovery_bundle_from_snapshot(
    snapshot: ExactCommitSnapshot,
) -> HexRecoveryBundleImage:
    """Build from an already-read snapshot.

    This private seam exists for parser unit tests.  It is not the production
    Git inventory trust boundary; callers must use ``build_hex_recovery_bundle``.
    """
    blobs = _snapshot_blobs(snapshot)
    for relative_path, reviewed_digest in (
        _PINNED_EXECUTABLE_TOPOLOGY_DIGESTS.items()
    ):
        if blobs[relative_path].content_digest != reviewed_digest:
            _fail("topology_source_revision_unreviewed")
    _require_python_class_surface(
        blobs[AGENT_REGISTRY_PATH].data,
        {
            "HexTopologyRegistry": {
                "get_neighbor_cells",
                "select_origin_cell",
            }
        },
    )
    _require_python_class_surface(
        blobs[SYMBOLIC_SOLVER_PATH].data,
        {
            "ModelRegistry": {"get", "list_models"},
            "SymbolicSolver": {"solve", "solve_for_chat"},
        },
    )

    agent_cells = _load_agent_cells(blobs[AGENT_CONFIG_PATH].data)
    axial_directions = _extract_axial_directions(blobs[AGENT_GEOMETRY_PATH].data)
    agent_adjacency = _agent_adjacency(agent_cells, axial_directions)
    agent_epoch = _topology_epoch(
        "agent.axial",
        "axial_agent_mesh",
        [
            {
                "cell_id": cell["cell_id"],
                "q": cell["q"],
                "r": cell["r"],
                "neighbors": list(agent_adjacency[str(cell["cell_id"])]),
            }
            for cell in agent_cells
        ],
    )

    solver_cells, solver_adjacency = _extract_solver_topology(
        blobs[SOLVER_TOPOLOGY_PATH].data
    )
    solver_epoch = _topology_epoch(
        "solver.logical",
        "logical_solver_overlay",
        [
            {
                "cell_id": cell_id,
                "neighbors": list(solver_adjacency[cell_id]),
            }
            for cell_id in solver_cells
        ],
    )
    axioms = _axioms_by_cell(blobs, solver_cells)

    genomes: dict[str, Mapping[str, Any]] = {}
    genome_bytes: dict[str, bytes] = {}
    artifacts: list[dict[str, Any]] = []
    agent_cell_refs: list[dict[str, Any]] = []
    solver_cell_refs: list[dict[str, Any]] = []

    agent_common_capabilities = [
        _capability(
            "router",
            "router.agent.axial.geometry",
            blobs[AGENT_GEOMETRY_PATH],
        ),
        _capability(
            "router",
            "router.agent.axial",
            blobs[AGENT_REGISTRY_PATH],
        ),
    ]
    agent_common_inputs = [
        _repo_input("repo.agent.axial.config", blobs[AGENT_CONFIG_PATH]),
        _repo_input("repo.agent.axial.geometry", blobs[AGENT_GEOMETRY_PATH]),
        _repo_input("repo.agent.axial.registry", blobs[AGENT_REGISTRY_PATH]),
    ]
    for cell in agent_cells:
        cell_id = str(cell["cell_id"])
        capabilities = [
            _capability(
                "agent",
                f"agent.cell.{cell_id}",
                blobs[AGENT_CONFIG_PATH],
            ),
            *agent_common_capabilities,
        ]
        genome = build_hex_cell_genome(
            cell_id=cell_id,
            mesh_id="agent.axial",
            mesh_kind="axial_agent_mesh",
            topology_epoch=agent_epoch,
            axial_coord={"q": int(cell["q"]), "r": int(cell["r"])},
            parent_cell_id=None,
            child_cell_ids=[],
            neighbor_cell_ids=agent_adjacency[cell_id],
            repair_peer_cell_ids=agent_adjacency[cell_id],
            capabilities=sorted(
                capabilities,
                key=lambda item: (item["kind"], item["capability_id"]),
            ),
            durable_inputs=sorted(
                agent_common_inputs,
                key=lambda item: item["input_id"],
            ),
        )
        genome_ref, raw, artifact, cell_ref = _genome_artifact(
            "agent.axial",
            cell_id,
            genome,
        )
        genomes[genome_ref] = genome
        genome_bytes[genome_ref] = raw
        artifacts.append(artifact)
        agent_cell_refs.append(cell_ref)

    solver_common_capabilities = [
        _capability(
            "router",
            "router.solver.logical",
            blobs[SOLVER_TOPOLOGY_PATH],
        ),
        _capability(
            "solver",
            "solver.symbolic.engine",
            blobs[SYMBOLIC_SOLVER_PATH],
        ),
    ]
    solver_common_inputs = [
        _repo_input(
            "repo.solver.logical.topology",
            blobs[SOLVER_TOPOLOGY_PATH],
        ),
        _repo_input(
            "repo.solver.symbolic.engine",
            blobs[SYMBOLIC_SOLVER_PATH],
        ),
    ]
    for cell_id in solver_cells:
        axiom_capabilities = [
            _capability(
                "solver",
                f"solver.axiom.{model_id}",
                blob,
            )
            for model_id, blob in axioms[cell_id]
        ]
        axiom_inputs = [
            _repo_input(
                f"repo.solver.axiom.{model_id}",
                blob,
            )
            for model_id, blob in axioms[cell_id]
        ]
        genome = build_hex_cell_genome(
            cell_id=cell_id,
            mesh_id="solver.logical",
            mesh_kind="logical_solver_overlay",
            topology_epoch=solver_epoch,
            axial_coord=None,
            parent_cell_id=None,
            child_cell_ids=[],
            neighbor_cell_ids=solver_adjacency[cell_id],
            repair_peer_cell_ids=solver_adjacency[cell_id],
            capabilities=sorted(
                [*solver_common_capabilities, *axiom_capabilities],
                key=lambda item: (item["kind"], item["capability_id"]),
            ),
            durable_inputs=sorted(
                [*solver_common_inputs, *axiom_inputs],
                key=lambda item: item["input_id"],
            ),
        )
        genome_ref, raw, artifact, cell_ref = _genome_artifact(
            "solver.logical",
            cell_id,
            genome,
        )
        genomes[genome_ref] = genome
        genome_bytes[genome_ref] = raw
        artifacts.append(artifact)
        solver_cell_refs.append(cell_ref)

    topologies = [
        {
            "mesh_id": "agent.axial",
            "mesh_kind": "axial_agent_mesh",
            "topology_epoch": agent_epoch,
            "cells": agent_cell_refs,
            "routing_invariants": {
                "connected": True,
                "bidirectional_neighbors": True,
                "survive_single_cell_loss": True,
                "max_route_hops": _graph_diameter(agent_adjacency),
            },
        },
        {
            "mesh_id": "solver.logical",
            "mesh_kind": "logical_solver_overlay",
            "topology_epoch": solver_epoch,
            "cells": solver_cell_refs,
            "routing_invariants": {
                "connected": True,
                "bidirectional_neighbors": True,
                "survive_single_cell_loss": True,
                "max_route_hops": _graph_diameter(solver_adjacency),
            },
        },
    ]
    source_repository = {
        "repository_ref": REPOSITORY_REF,
        "commit_sha": snapshot.commit_sha,
        "source_of_truth": "git_primary",
        "require_clean_clone": True,
        "backup_as_primary": False,
    }
    manifest = build_hive_recovery_manifest(
        manifest_id=f"hex.recovery.{snapshot.commit_sha}",
        created_at_utc=snapshot.committed_at_utc,
        source_repository=source_repository,
        topologies=topologies,
        artifacts=artifacts,
        genomes_by_ref=genomes,
        replicas=[],
        external_replication_verified=False,
        blank_disk_dry_run_verified=False,
        previous_manifest_digest=None,
    )
    validate_hive_recovery_manifest(
        manifest,
        genomes_by_ref=genomes,
        expected_commit=snapshot.commit_sha,
        require_recovery_ready=False,
    )
    manifest_bytes = canonical_json_bytes(manifest)
    return HexRecoveryBundleImage(
        commit_sha=snapshot.commit_sha,
        manifest=MappingProxyType(dict(manifest)),
        manifest_bytes=manifest_bytes,
        manifest_file_digest=_sha256_bytes(manifest_bytes),
        genome_bytes_by_ref=MappingProxyType(dict(genome_bytes)),
    )


def build_hex_recovery_bundle(
    repo_root: Path,
    expected_head: str,
) -> HexRecoveryBundleImage:
    """Read one exact commit inventory and build its validated bundle image."""
    snapshot = read_exact_commit_snapshot(repo_root, expected_head)
    _require_running_implementation_exact(repo_root, expected_head)
    image = _build_hex_recovery_bundle_from_snapshot(snapshot)
    _require_expected_head(repo_root, expected_head)
    _require_running_implementation_exact(repo_root, expected_head)
    return image


def _lstat_is_reparse(path: Path) -> bool:
    details = os.lstat(path)
    attributes = int(getattr(details, "st_file_attributes", 0))
    return stat.S_ISLNK(details.st_mode) or bool(attributes & _REPARSE_FLAG)


def _guard_no_reparse_components(path: Path) -> None:
    absolute = Path(os.path.abspath(os.fspath(path)))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current = current / part
        try:
            if _lstat_is_reparse(current):
                _fail("output_path_reparse_forbidden")
        except FileNotFoundError:
            break


def _stable_local_source_bytes(path: Path) -> bytes:
    _guard_no_reparse_components(path)
    try:
        before = os.lstat(path)
    except OSError:
        _fail("implementation_source_unavailable")
    if (
        _lstat_is_reparse(path)
        or not stat.S_ISREG(before.st_mode)
        or int(before.st_nlink) != 1
        or int(before.st_size) > _MAX_SINGLE_BLOB_BYTES
    ):
        _fail("implementation_source_type_invalid")
    descriptor = os.open(path, _open_flags(os.O_RDONLY))
    try:
        opened = os.fstat(descriptor)
        chunks: list[bytes] = []
        observed = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            observed += len(chunk)
            if observed > _MAX_SINGLE_BLOB_BYTES:
                _fail("implementation_source_too_large")
            chunks.append(chunk)
        after_open = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        after_path = os.lstat(path)
    except OSError:
        _fail("implementation_source_changed")
    signatures = {
        (
            int(item.st_dev),
            int(item.st_ino),
            int(item.st_size),
            int(item.st_mtime_ns),
        )
        for item in (before, opened, after_open, after_path)
    }
    if len(signatures) != 1 or observed != int(before.st_size):
        _fail("implementation_source_changed")
    return b"".join(chunks)


def _normalized_python_source(raw: bytes) -> bytes:
    without_crlf = raw.replace(b"\r\n", b"\n")
    if b"\r" in without_crlf or raw.startswith(b"\xef\xbb\xbf") or b"\x00" in raw:
        _fail("implementation_source_encoding_invalid")
    try:
        without_crlf.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        _fail("implementation_source_encoding_invalid")
    return without_crlf


def _require_running_implementation_exact(
    repo_root: Path,
    expected_head: str,
) -> None:
    """Bind the executing tool and imported digest builders to exact HEAD."""
    expected_module_paths = {
        "tools/build_hex_recovery_bundle.py": Path(__file__).resolve(strict=True),
        "waggledance/core/hex_topology/recovery_contract.py": Path(
            inspect.getsourcefile(build_hex_cell_genome) or ""
        ).resolve(strict=True),
        "waggledance/core/magma/canonical.py": Path(
            inspect.getsourcefile(canonical_json_bytes) or ""
        ).resolve(strict=True),
    }
    root = repo_root.resolve(strict=True)
    for relative_path, observed_path in expected_module_paths.items():
        expected_path = (root / PurePosixPath(relative_path)).resolve(strict=True)
        if observed_path != expected_path or not observed_path.is_relative_to(root):
            _fail("implementation_module_origin_mismatch")

    git_blobs = _read_literal_git_blobs(
        root,
        expected_head,
        sorted(_RUNTIME_IMPLEMENTATION_PATHS),
    )
    for relative_path, source_path in expected_module_paths.items():
        local_raw = _stable_local_source_bytes(source_path)
        if _normalized_python_source(local_raw) != git_blobs[relative_path].data:
            _fail("implementation_source_mismatch")


def _reject_remote_path(raw: str) -> None:
    normalized = raw.replace("/", "\\")
    if normalized.startswith("\\\\"):
        _fail("output_path_remote_forbidden")
    absolute = os.path.abspath(raw)
    if absolute.replace("/", "\\").startswith("\\\\"):
        _fail("output_path_remote_forbidden")
    if os.name == "nt":
        try:
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(Path(absolute).anchor)
        except (AttributeError, OSError):
            drive_type = 0
        if drive_type == 4:
            _fail("output_path_remote_forbidden")


def _resolve_output_directory(raw: Path) -> Path:
    supplied = os.fspath(raw)
    if not supplied:
        _fail("output_path_invalid")
    _reject_remote_path(supplied)
    lexical = Path(os.path.abspath(supplied))
    name = lexical.name
    if (
        _SAFE_OUTPUT_NAME_RE.fullmatch(name) is None
        or name.endswith((".", " "))
        or name.split(".", 1)[0].casefold() in _WINDOWS_RESERVED_NAMES
    ):
        _fail("output_name_invalid")
    _guard_no_reparse_components(lexical)
    parent = lexical.parent
    try:
        parent = parent.resolve(strict=True)
        details = os.lstat(parent)
    except (FileNotFoundError, OSError):
        _fail("output_parent_invalid")
    if _lstat_is_reparse(parent) or not stat.S_ISDIR(details.st_mode):
        _fail("output_parent_invalid")
    destination = parent / name
    if os.path.lexists(os.fspath(destination)):
        _fail("output_must_not_exist")

    filesystem_root = Path(destination.anchor).resolve(strict=False)
    home = Path.home().resolve(strict=False)
    repo = REPO_ROOT.resolve(strict=True)
    if (
        destination == filesystem_root
        or destination == home
        or destination == repo
        or destination.is_relative_to(repo)
        or repo.is_relative_to(destination)
        or home.is_relative_to(destination)
    ):
        _fail("output_scope_unsafe")
    return destination


def _open_flags(base: int) -> int:
    return (
        base
        | int(getattr(os, "O_BINARY", 0))
        | int(getattr(os, "O_CLOEXEC", 0))
        | int(getattr(os, "O_NOFOLLOW", 0))
    )


def _exclusive_write(path: Path, raw: bytes) -> None:
    descriptor = os.open(
        path,
        _open_flags(os.O_WRONLY | os.O_CREAT | os.O_EXCL),
        0o600,
    )
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                _fail("bundle_write_failed")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _validate_bundle_image(
    image: HexRecoveryBundleImage,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    if (
        not isinstance(image, HexRecoveryBundleImage)
        or _COMMIT_RE.fullmatch(image.commit_sha) is None
        or not isinstance(image.manifest, Mapping)
        or not isinstance(image.manifest_bytes, bytes)
        or image.manifest_bytes != canonical_json_bytes(dict(image.manifest))
        or image.manifest_file_digest != _sha256_bytes(image.manifest_bytes)
        or not isinstance(image.genome_bytes_by_ref, Mapping)
    ):
        _fail("bundle_image_invalid")
    genomes: dict[str, Any] = {}
    raw_by_ref: dict[str, bytes] = {}
    for genome_ref, raw in image.genome_bytes_by_ref.items():
        if (
            not isinstance(genome_ref, str)
            or not isinstance(raw, bytes)
            or raw != raw.strip()
        ):
            _fail("bundle_genome_image_invalid")
        try:
            genome = json.loads(raw.decode("utf-8", errors="strict"))
        except (UnicodeError, ValueError):
            _fail("bundle_genome_image_invalid")
        if raw != canonical_json_bytes(genome):
            _fail("bundle_genome_not_canonical")
        genomes[genome_ref] = genome
        raw_by_ref[genome_ref] = raw

    manifest = validate_hive_recovery_manifest(
        image.manifest,
        genomes_by_ref=genomes,
        expected_commit=image.commit_sha,
        require_recovery_ready=False,
    )
    artifacts_by_path = {
        item["relative_path"]: item for item in manifest["artifacts"]
    }
    if set(artifacts_by_path) != set(raw_by_ref):
        _fail("bundle_genome_artifact_set_mismatch")
    for genome_ref, raw in raw_by_ref.items():
        artifact = artifacts_by_path[genome_ref]
        if (
            artifact["classification"] != "genome"
            or artifact["content_digest"] != _sha256_bytes(raw)
            or artifact["byte_size"] != len(raw)
        ):
            _fail("bundle_genome_artifact_mismatch")
    return manifest, raw_by_ref


def _verify_staging(
    staging: Path,
    image: HexRecoveryBundleImage,
    raw_by_ref: Mapping[str, bytes],
) -> None:
    expected_blobs = {
        _sha256_bytes(raw).removeprefix("sha256:"): raw
        for raw in raw_by_ref.values()
    }
    try:
        top = {entry.name: entry for entry in os.scandir(staging)}
    except OSError:
        _fail("bundle_staging_unreadable")
    if set(top) != {MANIFEST_FILENAME, "blobs"}:
        _fail("bundle_staging_inventory_mismatch")
    manifest_path = staging / MANIFEST_FILENAME
    blob_parent = staging / "blobs"
    blob_root = blob_parent / "sha256"
    for directory in (staging, blob_parent, blob_root):
        details = os.lstat(directory)
        if _lstat_is_reparse(directory) or not stat.S_ISDIR(details.st_mode):
            _fail("bundle_staging_type_invalid")
    manifest_details = os.lstat(manifest_path)
    if (
        _lstat_is_reparse(manifest_path)
        or not stat.S_ISREG(manifest_details.st_mode)
        or int(manifest_details.st_nlink) != 1
        or manifest_path.read_bytes() != image.manifest_bytes
    ):
        _fail("bundle_staging_manifest_mismatch")
    try:
        blob_entries = {entry.name: entry for entry in os.scandir(blob_root)}
        blob_parent_entries = {entry.name for entry in os.scandir(blob_parent)}
    except OSError:
        _fail("bundle_staging_unreadable")
    if set(blob_entries) != set(expected_blobs) or blob_parent_entries != {"sha256"}:
        _fail("bundle_staging_inventory_mismatch")
    for name, expected_raw in expected_blobs.items():
        path = blob_root / name
        details = os.lstat(path)
        if (
            _lstat_is_reparse(path)
            or not stat.S_ISREG(details.st_mode)
            or int(details.st_nlink) != 1
            or int(details.st_size) != len(expected_raw)
            or path.read_bytes() != expected_raw
            or hashlib.sha256(expected_raw).hexdigest() != name
        ):
            _fail("bundle_staging_blob_mismatch")


def _rename_no_replace(source: Path, destination: Path) -> None:
    if os.name == "nt":
        try:
            os.rename(source, destination)
        except FileExistsError:
            _fail("output_changed_before_promotion")
        return

    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        renameat2 = getattr(library, "renameat2", None)
        if renameat2 is None:
            _fail("atomic_noreplace_unavailable")
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        result = renameat2(
            -100,
            source_bytes,
            -100,
            destination_bytes,
            1,
        )
    elif sys.platform == "darwin":
        renamex_np = getattr(library, "renamex_np", None)
        if renamex_np is None:
            _fail("atomic_noreplace_unavailable")
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        result = renamex_np(source_bytes, destination_bytes, 0x00000004)
    else:
        _fail("atomic_noreplace_unavailable")
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        _fail("output_changed_before_promotion")
    raise OSError(error_number, os.strerror(error_number))


def _fsync_directory(path: Path) -> bool:
    if os.name == "nt":
        # Python exposes no supported directory-fsync primitive on Windows.
        # A no-op must never be promoted into a durability attestation.
        return False
    descriptor = os.open(path, os.O_RDONLY | int(getattr(os, "O_DIRECTORY", 0)))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return True


def _cleanup_owned_staging(
    staging: Path,
    destination: Path,
    expected_relative_files: set[str],
) -> None:
    prefix = f".{destination.name}.hex-bundle-stage-"
    try:
        absolute = Path(os.path.abspath(os.fspath(staging)))
        parent = Path(os.path.abspath(os.fspath(destination.parent)))
        if (
            absolute.parent != parent
            or not absolute.name.startswith(prefix)
            or len(absolute.name) != len(prefix) + 16
            or not absolute.exists()
            or _lstat_is_reparse(absolute)
        ):
            return
        files: list[Path] = []
        directories: list[Path] = []
        for root, child_dirs, child_files in os.walk(absolute, topdown=False):
            root_path = Path(root)
            if _lstat_is_reparse(root_path):
                return
            for child in child_files:
                child_path = root_path / child
                relative = child_path.relative_to(absolute).as_posix()
                details = os.lstat(child_path)
                if (
                    relative not in expected_relative_files
                    or _lstat_is_reparse(child_path)
                    or not stat.S_ISREG(details.st_mode)
                    or int(details.st_nlink) != 1
                ):
                    return
                files.append(child_path)
            for child in child_dirs:
                child_path = root_path / child
                if _lstat_is_reparse(child_path):
                    return
                directories.append(child_path)
        for file_path in files:
            os.unlink(file_path)
        for directory in directories:
            os.rmdir(directory)
        os.rmdir(absolute)
    except OSError:
        return


def write_hex_recovery_bundle(
    image: HexRecoveryBundleImage,
    out_dir: Path,
) -> Mapping[str, Any]:
    """Write one exact bundle through verified sibling staging."""
    manifest, raw_by_ref = _validate_bundle_image(image)
    destination = _resolve_output_directory(Path(out_dir))
    blob_items = {
        _sha256_bytes(raw).removeprefix("sha256:"): raw
        for raw in raw_by_ref.values()
    }
    expected_relative_files = {
        MANIFEST_FILENAME,
        *{f"blobs/sha256/{name}" for name in blob_items},
    }
    axiom_capability_count = sum(
        1
        for raw in raw_by_ref.values()
        for capability in json.loads(raw.decode("utf-8"))["capabilities"]
        if capability["capability_id"].startswith("solver.axiom.")
    )
    report_base = {
        "build_contract": BUNDLE_BUILD_VERSION,
        "source_commit": image.commit_sha,
        "manifest_digest": manifest["manifest_digest"],
        "candidate_manifest_file_digest": image.manifest_file_digest,
        "candidate_digest_is_independently_trusted": False,
        "topology_count": len(manifest["topologies"]),
        "cell_count": sum(
            len(topology["cells"]) for topology in manifest["topologies"]
        ),
        "genome_count": len(raw_by_ref),
        "axiom_capability_count": axiom_capability_count,
        "authoritative_hierarchical_runtime_shadow_available": False,
        "hierarchical_runtime_shadow_included": False,
        "target_state_topology_coverage_complete": False,
        "replica_count": len(manifest["replicas"]),
        "external_replication_verified": False,
        "blank_disk_dry_run_verified": False,
        "transport_enabled": False,
        "runtime_activation_authority_granted": False,
        "claim_safe_upgrade": False,
        "production_ready_claim": False,
        "functional_capability_recovery_verified": False,
        "mutable_state_coverage_complete": False,
        # The writer proves the complete manifest/genome artifact set, but it
        # deliberately accepts any contract-valid HexRecoveryBundleImage.  It
        # therefore cannot attest that its caller obtained the image through
        # this module's exact-Git inventory boundary.
        "source_inventory_complete": False,
        "bundle_artifact_inventory_complete": True,
    }

    staging: Path | None = None
    for _ in range(16):
        candidate = destination.parent / (
            f".{destination.name}.hex-bundle-stage-{secrets.token_hex(8)}"
        )
        try:
            os.mkdir(candidate, 0o700)
        except FileExistsError:
            continue
        staging = candidate
        break
    if staging is None:
        _fail("bundle_staging_name_exhausted")

    staging_directory_fsync_completed = True
    try:
        blob_parent = staging / "blobs"
        blob_root = blob_parent / "sha256"
        os.mkdir(blob_parent, 0o700)
        os.mkdir(blob_root, 0o700)
        for name in sorted(blob_items):
            _exclusive_write(blob_root / name, blob_items[name])
        _exclusive_write(staging / MANIFEST_FILENAME, image.manifest_bytes)
        for directory in (blob_root, blob_parent, staging):
            if not _fsync_directory(directory):
                staging_directory_fsync_completed = False
        _verify_staging(staging, image, raw_by_ref)

        _guard_no_reparse_components(destination.parent)
        if os.path.lexists(os.fspath(destination)):
            _fail("output_changed_before_promotion")
        _rename_no_replace(staging, destination)
    except BaseException:
        _cleanup_owned_staging(staging, destination, expected_relative_files)
        raise

    parent_directory_fsync_completed = False
    parent_directory_fsync_failed = False
    try:
        parent_directory_fsync_completed = _fsync_directory(
            destination.parent
        )
    except OSError:
        # The verified directory is already atomically visible.  Rolling it
        # back would create a second destructive race, so return an explicit
        # committed-but-not-durability-synced terminal instead.
        parent_directory_fsync_failed = True
    directory_durability_verified = (
        staging_directory_fsync_completed
        and parent_directory_fsync_completed
    )
    if directory_durability_verified:
        error = None
    elif parent_directory_fsync_failed:
        error = "parent_directory_fsync_failed_after_promotion"
    else:
        error = "directory_fsync_unavailable_after_promotion"
    return MappingProxyType(
        {
            **report_base,
            "ok": directory_durability_verified,
            "bundle_complete": True,
            "promotion_completed": True,
            "staging_directory_fsync_completed": (
                staging_directory_fsync_completed
            ),
            "parent_directory_fsync_completed": (
                parent_directory_fsync_completed
            ),
            "directory_durability_verified": directory_durability_verified,
            "error": error,
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        image = build_hex_recovery_bundle(REPO_ROOT, args.expected_head)
        result = dict(write_hex_recovery_bundle(image, Path(args.out_dir)))
    except BundleBuildError as error:
        payload = {
            "ok": False,
            "build_contract": BUNDLE_BUILD_VERSION,
            "error": str(error),
        }
        if args.json:
            print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        else:
            print(f"HEX recovery bundle build failed: {error}", file=sys.stderr)
        return 2
    except (ContractValidationError, OSError, ValueError):
        payload = {
            "ok": False,
            "build_contract": BUNDLE_BUILD_VERSION,
            "error": "bundle_build_failed",
        }
        if args.json:
            print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        else:
            print("HEX recovery bundle build failed: bundle_build_failed", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        print(f"source_commit={result['source_commit']}")
        print(f"manifest_digest={result['manifest_digest']}")
        print(
            "candidate_manifest_file_digest="
            f"{result['candidate_manifest_file_digest']}"
        )
        print("candidate_digest_is_independently_trusted=false")
        print("hierarchical_runtime_shadow_included=false")
        print("functional_capability_recovery_verified=false")
        print("mutable_state_coverage_complete=false")
        print("production_ready_claim=false")
        if not result["ok"]:
            print(f"warning={result['error']}", file=sys.stderr)
    return 0 if result["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
