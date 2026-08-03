# SPDX-License-Identifier: Apache-2.0
"""Controller-selected local-TCB compiler worker for coding candidates.

This file is launched as a standalone, isolated-mode CPython script.  It never
imports or executes candidate modules.  It validates a deliberately narrow
source interface, parses and compiles syntax, and reports only bounded digests
for an inert canonical source package reconstructed by the controller.

The worker is not an operating-system sandbox.  Its process boundary contains
parser/compiler crashes and stalls; it does not confine generated-code
execution because generated code is never executed here.
"""

from __future__ import annotations

import ast
import base64
import binascii
import hashlib
import io
import json
import re
import sys
import tokenize
from typing import Any


PROTOCOL_SCHEMA = "wd.understanding.coding_candidate_worker_protocol.v1"
SOURCE_MANIFEST_SCHEMA = "wd.understanding.coding_candidate_source_manifest.v1"
ARTIFACT_MANIFEST_SCHEMA = "wd.understanding.coding_candidate_artifact_manifest.v1"
ARTIFACT_SCHEMA = "wd.understanding.coding_candidate_artifact.v1"
ARTIFACT_FORMAT = "canonical-python-source-package-v1"
LANGUAGE = "python-source-utf8"
ENTRYPOINT = "solve"

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_ENCODING_COOKIE = re.compile(
    br"^[ \t\f]*\#.*?coding[:=][ \t]*[-_.A-Za-z0-9]+"
)

_HARD_LIMITS = {
    "max_solver_source_bytes": 262_144,
    "max_test_source_bytes": 262_144,
    "max_total_source_bytes": 524_288,
    "max_source_lines": 20_000,
    "max_tokens": 200_000,
    "max_ast_nodes": 100_000,
    "max_ast_depth": 256,
    "max_literal_bytes": 131_072,
    "max_integer_digits": 1_024,
    "max_worker_stdout_bytes": 262_144,
    "max_worker_stderr_bytes": 65_536,
    "max_wall_milliseconds": 30_000,
}
_MAX_REQUEST_BYTES = 750_000

_INTERFACE_POLICY = {
    "schema": "wd.understanding.coding_candidate.interface.v1",
    "language": LANGUAGE,
    "entrypoint": ENTRYPOINT,
    "solver_file": "solver.py",
    "test_file": "test_solver.py",
    "solver_signature": "solve(payload)",
    "test_signature": "test_*()",
    "candidate_imported": False,
    "candidate_executed": False,
    "tests_executed": False,
}
_AST_POLICY = {
    "schema": "wd.understanding.coding_candidate.ast_compatibility.v1",
    "top_level": "optional_docstring_then_fixed_functions_only",
    "imports": "refused",
    "decorators_annotations_defaults": "refused",
    "nested_functions_classes_async_dunder": "refused",
    "call_screen": "non_name_callees_and_selected_builtin_references_refused",
    "while_and_power": "refused",
    "label": "compatibility_screen_not_safety_proof",
}
_PACKAGING_POLICY = {
    "schema": "wd.understanding.coding_candidate.packaging.v1",
    "format": ARTIFACT_FORMAT,
    "canonicalization": "magma-jcs-subset-v1",
    "content_encoding": "base64",
    "logical_files": ["solver.py", "test_solver.py"],
    "timestamps_pids_paths_randomness": "omitted",
    "bytecode": "omitted",
}

_REQUEST_KEYS = frozenset(
    {
        "schema_version",
        "policy",
        "policy_digest",
        "plan_digest",
        "cell_binding_digest",
        "source_manifest_digest",
        "worker_artifact_digest",
        "interpreter_artifact_digest",
        "interpreter_identity_digest",
        "interface_contract_digest",
        "ast_policy_digest",
        "packaging_policy_digest",
        "solver_source_b64",
        "test_source_b64",
    }
)
_POLICY_KEYS = frozenset(
    {
        "mode",
        "max_solver_source_bytes",
        "max_test_source_bytes",
        "max_total_source_bytes",
        "max_source_lines",
        "max_tokens",
        "max_ast_nodes",
        "max_ast_depth",
        "max_literal_bytes",
        "max_integer_digits",
        "max_worker_stdout_bytes",
        "max_worker_stderr_bytes",
        "max_wall_milliseconds",
    }
)

_BANNED_CALL_NAMES = frozenset(
    {
        "__import__",
        "breakpoint",
        "compile",
        "delattr",
        "dir",
        "eval",
        "exec",
        "getattr",
        "globals",
        "help",
        "input",
        "locals",
        "memoryview",
        "open",
        "pow",
        "setattr",
        "type",
        "vars",
    }
)
_BANNED_NODE_TYPES = tuple(
    node_type
    for node_type in (
        ast.AsyncFor,
        ast.AsyncFunctionDef,
        ast.AsyncWith,
        ast.Await,
        ast.ClassDef,
        ast.Delete,
        ast.Global,
        ast.Import,
        ast.ImportFrom,
        ast.Lambda,
        ast.Nonlocal,
        ast.Try,
        getattr(ast, "TryStar", None),
        ast.While,
        ast.With,
        ast.Yield,
        ast.YieldFrom,
    )
    if node_type is not None
)


class _Refused(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha256_mapping(value: Any) -> str:
    return _sha256_bytes(_canonical_json_bytes(value))


_INTERFACE_DIGEST = _sha256_mapping(_INTERFACE_POLICY)
_AST_POLICY_DIGEST = _sha256_mapping(_AST_POLICY)
_PACKAGING_POLICY_DIGEST = _sha256_mapping(_PACKAGING_POLICY)


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if type(key) is not str or key in result:
            raise _Refused("invalid_protocol")
        result[key] = value
    return result


def _require_digest(value: object) -> str:
    if type(value) is not str or not _SHA256.fullmatch(value):
        raise _Refused("invalid_protocol")
    return value


def _validate_json_shape(value: Any) -> None:
    stack: list[tuple[Any, int]] = [(value, 1)]
    nodes = 0
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if depth > 16 or nodes > 256:
            raise _Refused("invalid_protocol")
        if type(current) is dict:
            stack.extend((child, depth + 1) for child in current.values())
        elif type(current) is list:
            stack.extend((child, depth + 1) for child in current)
        elif current is None or type(current) in (str, int, float, bool):
            continue
        else:
            raise _Refused("invalid_protocol")


def _decode_request(raw: bytes) -> dict[str, Any]:
    if type(raw) is not bytes or len(raw) > _MAX_REQUEST_BYTES:
        raise _Refused("invalid_protocol")
    try:
        text = raw.decode("utf-8", errors="strict")
        value = json.loads(text, object_pairs_hook=_strict_object)
        _validate_json_shape(value)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        TypeError,
        OverflowError,
        RecursionError,
    ) as exc:
        raise _Refused("invalid_protocol") from exc
    if _canonical_json_bytes(value) != raw:
        raise _Refused("invalid_protocol")
    if type(value) is not dict or set(value) != _REQUEST_KEYS:
        raise _Refused("invalid_protocol")
    if value["schema_version"] != PROTOCOL_SCHEMA:
        raise _Refused("invalid_protocol")
    policy = value["policy"]
    if type(policy) is not dict or set(policy) != _POLICY_KEYS:
        raise _Refused("invalid_protocol")
    if policy.get("mode") != "static_shadow":
        raise _Refused("invalid_protocol")
    for key, hard_maximum in _HARD_LIMITS.items():
        if (
            type(policy.get(key)) is not int
            or policy[key] <= 0
            or policy[key] > hard_maximum
        ):
            raise _Refused("invalid_protocol")
    if policy["max_total_source_bytes"] < max(
        policy["max_solver_source_bytes"], policy["max_test_source_bytes"]
    ):
        raise _Refused("invalid_protocol")
    if policy["max_worker_stdout_bytes"] < 4_096:
        raise _Refused("invalid_protocol")
    expected_policy = _sha256_mapping(
        {"domain": "wd.understanding.coding_candidate_policy.v1", **policy}
    )
    if value["policy_digest"] != expected_policy:
        raise _Refused("invalid_protocol")
    for key in (
        "policy_digest",
        "plan_digest",
        "cell_binding_digest",
        "source_manifest_digest",
        "worker_artifact_digest",
        "interpreter_artifact_digest",
        "interpreter_identity_digest",
        "interface_contract_digest",
        "ast_policy_digest",
        "packaging_policy_digest",
    ):
        _require_digest(value[key])
    for key in ("solver_source_b64", "test_source_b64"):
        if type(value[key]) is not str:
            raise _Refused("invalid_protocol")
    fixed_digests = {
        "interface_contract_digest": _INTERFACE_DIGEST,
        "ast_policy_digest": _AST_POLICY_DIGEST,
        "packaging_policy_digest": _PACKAGING_POLICY_DIGEST,
    }
    if any(value[key] != expected for key, expected in fixed_digests.items()):
        raise _Refused("invalid_protocol")
    return value


def _decode_source(value: str) -> bytes:
    try:
        decoded = base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, binascii.Error) as exc:
        raise _Refused("invalid_protocol") from exc
    if base64.b64encode(decoded).decode("ascii") != value:
        raise _Refused("invalid_protocol")
    return decoded


def _source_manifest(solver: bytes, tests: bytes) -> dict[str, Any]:
    return {
        "schema_version": SOURCE_MANIFEST_SCHEMA,
        "language": LANGUAGE,
        "entrypoint": ENTRYPOINT,
        "files": [
            {
                "logical_name": "solver.py",
                "sha256": _sha256_bytes(solver),
                "byte_count": len(solver),
            },
            {
                "logical_name": "test_solver.py",
                "sha256": _sha256_bytes(tests),
                "byte_count": len(tests),
            },
        ],
    }


def _source_manifest_digest(manifest: dict[str, Any]) -> str:
    return _sha256_mapping(
        {"domain": "wd.understanding.coding_candidate_source_manifest.digest.v1", **manifest}
    )


def _line_count(value: bytes) -> int:
    if not value:
        return 0
    return value.count(b"\n") + (0 if value.endswith(b"\n") else 1)


def _byte_policy(value: bytes, *, maximum: int, max_lines: int) -> str:
    if not value or len(value) > maximum:
        raise _Refused("source_policy_refused")
    if value.startswith(b"\xef\xbb\xbf") or b"\x00" in value or b"\r" in value:
        raise _Refused("source_policy_refused")
    if any(_ENCODING_COOKIE.search(line) for line in value.split(b"\n", 2)[:2]):
        raise _Refused("source_policy_refused")
    if _line_count(value) > max_lines:
        raise _Refused("source_policy_refused")
    try:
        return value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _Refused("invalid_source_encoding") from exc


def _token_count(value: bytes, maximum: int) -> None:
    count = 0
    try:
        for _ in tokenize.tokenize(io.BytesIO(value).readline):
            count += 1
            if count > maximum:
                raise _Refused("source_policy_refused")
    except (IndentationError, SyntaxError, tokenize.TokenError) as exc:
        raise _Refused("syntax_refused") from exc


def _function_shape(node: ast.FunctionDef, *, solver: bool) -> None:
    if node.decorator_list or node.returns is not None or node.type_comment is not None:
        raise _Refused("solver_interface_refused" if solver else "test_interface_refused")
    if getattr(node, "type_params", ()):
        raise _Refused("solver_interface_refused" if solver else "test_interface_refused")
    args = node.args
    if (
        args.posonlyargs
        or args.kwonlyargs
        or args.vararg is not None
        or args.kwarg is not None
        or args.defaults
        or args.kw_defaults
    ):
        raise _Refused("solver_interface_refused" if solver else "test_interface_refused")
    expected_args = 1 if solver else 0
    if len(args.args) != expected_args:
        raise _Refused("solver_interface_refused" if solver else "test_interface_refused")
    if solver and (args.args[0].arg != "payload" or args.args[0].annotation is not None):
        raise _Refused("solver_interface_refused")
    if any(arg.annotation is not None for arg in args.args):
        raise _Refused("solver_interface_refused" if solver else "test_interface_refused")


def _top_level_functions(tree: ast.Module, *, solver: bool) -> set[int]:
    body = list(tree.body)
    if body and isinstance(body[0], ast.Expr):
        value = body[0].value
        if isinstance(value, ast.Constant) and type(value.value) is str:
            body = body[1:]
    if solver:
        if len(body) != 1 or type(body[0]) is not ast.FunctionDef:
            raise _Refused("solver_interface_refused")
        function = body[0]
        if function.name != ENTRYPOINT:
            raise _Refused("solver_interface_refused")
        _function_shape(function, solver=True)
        return {id(function)}
    if not body or any(type(node) is not ast.FunctionDef for node in body):
        raise _Refused("test_interface_refused")
    names: set[str] = set()
    allowed: set[int] = set()
    for function in body:
        assert type(function) is ast.FunctionDef
        if not function.name.startswith("test_") or function.name in names:
            raise _Refused("test_interface_refused")
        names.add(function.name)
        _function_shape(function, solver=False)
        allowed.add(id(function))
    return allowed


def _validate_ast(
    tree: ast.Module,
    *,
    allowed_functions: set[int],
    max_nodes: int,
    max_depth: int,
    max_literal_bytes: int,
    max_integer_digits: int,
) -> None:
    stack: list[tuple[ast.AST, int]] = [(tree, 1)]
    node_count = 0
    literal_bytes = 0
    # Avoid converting attacker-selected huge hexadecimal integers back to
    # decimal strings. CPython's conversion limit would otherwise turn a
    # bounded policy refusal into an internal protocol failure.
    integer_limit = 10**max_integer_digits
    while stack:
        node, depth = stack.pop()
        node_count += 1
        if node_count > max_nodes or depth > max_depth:
            raise _Refused("ast_policy_refused")
        if isinstance(node, _BANNED_NODE_TYPES) or isinstance(node, ast.Pow):
            raise _Refused("ast_policy_refused")
        if isinstance(node, ast.FunctionDef) and id(node) not in allowed_functions:
            raise _Refused("ast_policy_refused")
        if isinstance(node, ast.Name) and (
            node.id.startswith("__") or node.id in _BANNED_CALL_NAMES
        ):
            raise _Refused("ast_policy_refused")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise _Refused("ast_policy_refused")
        if isinstance(node, ast.Call):
            if type(node.func) is not ast.Name:
                raise _Refused("ast_policy_refused")
        if isinstance(node, ast.Constant):
            if type(node.value) is str:
                try:
                    literal_bytes += len(node.value.encode("utf-8"))
                except UnicodeEncodeError as exc:
                    raise _Refused("ast_policy_refused") from exc
            elif type(node.value) is bytes:
                literal_bytes += len(node.value)
            elif type(node.value) is int and abs(node.value) >= integer_limit:
                raise _Refused("ast_policy_refused")
            if literal_bytes > max_literal_bytes:
                raise _Refused("ast_policy_refused")
        children = list(ast.iter_child_nodes(node))
        stack.extend((child, depth + 1) for child in reversed(children))


def _parse_and_compile(value: bytes, *, logical_name: str, policy: dict[str, Any]) -> None:
    text = _byte_policy(
        value,
        maximum=(
            policy["max_solver_source_bytes"]
            if logical_name == "solver.py"
            else policy["max_test_source_bytes"]
        ),
        max_lines=policy["max_source_lines"],
    )
    _token_count(value, policy["max_tokens"])
    try:
        tree = ast.parse(text, filename=logical_name, mode="exec", type_comments=False)
    except (MemoryError, RecursionError, SyntaxError, ValueError) as exc:
        raise _Refused("syntax_refused") from exc
    solver = logical_name == "solver.py"
    allowed = _top_level_functions(tree, solver=solver)
    _validate_ast(
        tree,
        allowed_functions=allowed,
        max_nodes=policy["max_ast_nodes"],
        max_depth=policy["max_ast_depth"],
        max_literal_bytes=policy["max_literal_bytes"],
        max_integer_digits=policy["max_integer_digits"],
    )
    try:
        compile(
            tree,
            logical_name,
            "exec",
            flags=0,
            dont_inherit=True,
            optimize=0,
        )
    except (MemoryError, RecursionError, SyntaxError, ValueError) as exc:
        raise _Refused("compiler_refused") from exc


def _artifact_values(
    request: dict[str, Any], solver: bytes, tests: bytes
) -> tuple[dict[str, Any], bytes, str, str]:
    source_manifest = _source_manifest(solver, tests)
    manifest = {
        "schema_version": ARTIFACT_MANIFEST_SCHEMA,
        "format": ARTIFACT_FORMAT,
        "language": LANGUAGE,
        "entrypoint": ENTRYPOINT,
        "plan_digest": request["plan_digest"],
        "cell_binding_digest": request["cell_binding_digest"],
        "source_manifest": source_manifest,
        "source_manifest_digest": request["source_manifest_digest"],
        "worker_artifact_digest": request["worker_artifact_digest"],
        "interpreter_artifact_digest": request["interpreter_artifact_digest"],
        "interpreter_identity_digest": request["interpreter_identity_digest"],
        "interface_contract_digest": request["interface_contract_digest"],
        "ast_policy_digest": request["ast_policy_digest"],
        "packaging_policy_digest": request["packaging_policy_digest"],
    }
    manifest_digest = _sha256_mapping(
        {
            "domain": "wd.understanding.coding_candidate_artifact_manifest.digest.v1",
            **manifest,
        }
    )
    artifact = {
        "schema_version": ARTIFACT_SCHEMA,
        "format": ARTIFACT_FORMAT,
        "manifest": manifest,
        "manifest_digest": manifest_digest,
        "files": [
            {
                "logical_name": "solver.py",
                "encoding": "base64",
                "content": base64.b64encode(solver).decode("ascii"),
            },
            {
                "logical_name": "test_solver.py",
                "encoding": "base64",
                "content": base64.b64encode(tests).decode("ascii"),
            },
        ],
    }
    artifact_bytes = _canonical_json_bytes(artifact)
    return manifest, artifact_bytes, manifest_digest, _sha256_bytes(artifact_bytes)


def _base_response(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": PROTOCOL_SCHEMA,
        "plan_digest": request["plan_digest"],
        "cell_binding_digest": request["cell_binding_digest"],
        "source_manifest_digest": request["source_manifest_digest"],
        "policy_digest": request["policy_digest"],
    }


def _emit(value: dict[str, Any]) -> None:
    sys.stdout.buffer.write(_canonical_json_bytes(value))
    sys.stdout.buffer.flush()


def _main() -> int:
    if sys.implementation.name != "cpython":
        raise _Refused("invalid_protocol")
    try:
        raw = sys.stdin.buffer.read(_MAX_REQUEST_BYTES + 1)
        if len(raw) > _MAX_REQUEST_BYTES:
            raise _Refused("invalid_protocol")
        request = _decode_request(raw)
    except _Refused:
        _emit(
            {
                "schema_version": PROTOCOL_SCHEMA,
                "status": "source_rejected",
                "reason_code": "invalid_protocol",
            }
        )
        return 0

    try:
        solver = _decode_source(request["solver_source_b64"])
        tests = _decode_source(request["test_source_b64"])
        source_manifest = _source_manifest(solver, tests)
        if (
            len(solver) + len(tests)
            > request["policy"]["max_total_source_bytes"]
            or _source_manifest_digest(source_manifest)
            != request["source_manifest_digest"]
        ):
            raise _Refused("source_policy_refused")
        _parse_and_compile(solver, logical_name="solver.py", policy=request["policy"])
        _parse_and_compile(tests, logical_name="test_solver.py", policy=request["policy"])
        _, artifact_bytes, manifest_digest, artifact_digest = _artifact_values(
            request, solver, tests
        )
        response = _base_response(request)
        response.update(
            status="packaged",
            reason_code="packaged",
            solver_source_digest=_sha256_bytes(solver),
            test_source_digest=_sha256_bytes(tests),
            artifact_manifest_digest=manifest_digest,
            artifact_digest=artifact_digest,
            artifact_byte_count=len(artifact_bytes),
        )
    except _Refused as exc:
        response = _base_response(request)
        response.update(status="source_rejected", reason_code=exc.reason)
    except BaseException:
        # The decoded request supplies bounded digest echoes, but no exception,
        # source, path, or traceback crosses the protocol boundary.
        response = _base_response(request)
        response.update(status="source_rejected", reason_code="internal_error")
    _emit(response)
    return 0


if __name__ == "__main__":
    exit_code = 0
    try:
        exit_code = _main()
    except BaseException:
        # Never serialize exception text, paths, source fragments, or traceback.
        _emit(
            {
                "schema_version": PROTOCOL_SCHEMA,
                "status": "source_rejected",
                "reason_code": "internal_error",
            }
        )
    raise SystemExit(exit_code)
