from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path

import pytest

from waggledance.core.hex_cell_topology import ALL_CELLS


ROOT = Path(__file__).resolve().parent.parent
CANONICAL_CELL_MODULE = "waggledance/core/hex_cell_topology.py"
PYTHON_CELL_CONSUMERS = (
    ("tools.cell_manifest", "CELLS", "tools/cell_manifest.py", "list"),
    (
        "tools.hex_subdivision_plan",
        "DEFAULT_CELLS",
        "tools/hex_subdivision_plan.py",
        "tuple",
    ),
    (
        "tools.migrate_to_vector_root",
        "CELLS",
        "tools/migrate_to_vector_root.py",
        "tuple",
    ),
    (
        "tools.backfill_axioms_to_hex",
        "KNOWN_CELLS",
        "tools/backfill_axioms_to_hex.py",
        "frozenset",
    ),
    (
        "tools.phase8_capability_report",
        "CELLS",
        "tools/phase8_capability_report.py",
        "list",
    ),
    (
        "tools.propose_solver",
        "VALID_CELLS",
        "tools/propose_solver.py",
        "frozenset",
    ),
    (
        "tools.run_phase17a_producer_fabric_proof",
        "HEX_CELLS",
        "tools/run_phase17a_producer_fabric_proof.py",
        "tuple",
    ),
    (
        "tools.run_solver_scale_proof",
        "HEX_CELLS",
        "tools/run_solver_scale_proof.py",
        "tuple",
    ),
    (
        "waggledance.core.solver_synthesis",
        "HEX_CELLS",
        "waggledance/core/solver_synthesis/__init__.py",
        "tuple",
    ),
)
CELL_KEYWORD_CONSUMERS = (
    "tools.backfill_axioms_to_hex",
    "tools.cell_manifest",
    "tools.phase8_capability_report",
    "tools.upgrade_axioms_for_v3",
)
@pytest.mark.parametrize(
    ("module_name", "attribute", "_source_path", "_wrapper"),
    PYTHON_CELL_CONSUMERS,
)
def test_python_cell_consumers_resolve_the_canonical_order(
    module_name: str,
    attribute: str,
    _source_path: str,
    _wrapper: str,
) -> None:
    module = importlib.import_module(module_name)

    observed = getattr(module, attribute)
    if isinstance(observed, (set, frozenset)):
        assert frozenset(observed) == frozenset(ALL_CELLS)
    else:
        assert tuple(observed) == tuple(ALL_CELLS)


def _assignment_value(tree: ast.Module, name: str) -> ast.expr:
    values: list[ast.expr] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            values.append(node.value)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and node.value is not None
        ):
            values.append(node.value)
    assert len(values) == 1
    return values[0]


@pytest.mark.parametrize(
    ("_module_name", "attribute", "source_path", "wrapper"),
    PYTHON_CELL_CONSUMERS,
)
def test_python_cell_consumers_import_and_wrap_only_the_canonical_source(
    _module_name: str,
    attribute: str,
    source_path: str,
    wrapper: str,
) -> None:
    tree = ast.parse(
        (ROOT / source_path).read_text(encoding="utf-8"),
        filename=source_path,
    )
    imports = [
        alias
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "waggledance.core.hex_cell_topology"
        for alias in node.names
        if alias.name == "ALL_CELLS" and alias.asname is None
    ]
    assert len(imports) == 1

    value = _assignment_value(tree, attribute)
    assert isinstance(value, ast.Call)
    assert isinstance(value.func, ast.Name) and value.func.id == wrapper
    assert len(value.args) == 1 and not value.keywords
    assert isinstance(value.args[0], ast.Name) and value.args[0].id == "ALL_CELLS"


def test_all_schema_cell_enums_match_the_canonical_order() -> None:
    discovered: list[tuple[Path, tuple[str, ...]]] = []
    for schema_path in sorted((ROOT / "schemas").rglob("*.json")):
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        pending = [schema]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                cell_schema = value.get("cell_id")
                if (
                    isinstance(cell_schema, dict)
                    and isinstance(cell_schema.get("enum"), list)
                ):
                    discovered.append(
                        (schema_path, tuple(cell_schema["enum"]))
                    )
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)

    assert discovered
    for schema_path, cell_enum in discovered:
        assert cell_enum == tuple(ALL_CELLS), schema_path


_UNRESOLVED = object()


def _static_string_value(node: ast.AST, names: dict[str, object]) -> object:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return names.get(node.id, _UNRESOLVED)
    if isinstance(node, (ast.List, ast.Set, ast.Tuple)):
        values: list[object] = []
        for element in node.elts:
            if isinstance(element, ast.Starred):
                expanded = _static_string_value(element.value, names)
                if not isinstance(expanded, tuple):
                    return _UNRESOLVED
                values.extend(expanded)
                continue
            resolved = _static_string_value(element, names)
            if resolved is _UNRESOLVED:
                return _UNRESOLVED
            values.append(resolved)
        return tuple(values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_string_value(node.left, names)
        right = _static_string_value(node.right, names)
        if isinstance(left, tuple) and isinstance(right, tuple):
            return left + right
        if isinstance(left, str) and isinstance(right, str):
            return left + right
        return _UNRESOLVED
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in {"frozenset", "list", "set", "tuple"}
        and len(node.args) == 1
        and not node.keywords
    ):
        return _static_string_value(node.args[0], names)
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "split"
        and len(node.args) <= 1
        and not node.keywords
    ):
        source = _static_string_value(node.func.value, names)
        separator = (
            _static_string_value(node.args[0], names)
            if node.args
            else None
        )
        if isinstance(source, str) and (
            separator is None or isinstance(separator, str)
        ):
            return tuple(source.split(separator))
    return _UNRESOLVED


def _module_string_names(tree: ast.Module) -> dict[str, object]:
    names: dict[str, object] = {}
    for node in tree.body:
        target: ast.Name | None = None
        value: ast.expr | None = None
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target = node.targets[0]
            value = node.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            target = node.target
            value = node.value
        if target is not None and value is not None:
            resolved = _static_string_value(value, names)
            if resolved is not _UNRESOLVED:
                names[target.id] = resolved
    return names


@pytest.mark.parametrize(
    "expression",
    (
        '"general,thermal,energy,safety,seasonal,math,system,learning".split(",")',
        "(" + ",".join(repr(cell) for cell in ALL_CELLS[:4]) + ",) + ("
        + ",".join(repr(cell) for cell in ALL_CELLS[4:])
        + ",)",
        "(*("
        + ",".join(repr(cell) for cell in ALL_CELLS[:4])
        + ",), *("
        + ",".join(repr(cell) for cell in ALL_CELLS[4:])
        + ",))",
    ),
)
def test_static_cell_literal_detection_covers_compact_forms(
    expression: str,
) -> None:
    node = ast.parse(expression, mode="eval").body

    assert frozenset(_static_string_value(node, {})) == frozenset(ALL_CELLS)


def test_full_python_cell_sequence_exists_only_at_the_canonical_source() -> None:
    expected = frozenset(ALL_CELLS)
    definitions: set[str] = set()
    for base in (ROOT / "waggledance", ROOT / "tools"):
        for path in sorted(base.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            names = _module_string_names(tree)
            for node in ast.walk(tree):
                resolved = _static_string_value(node, names)
                if (
                    isinstance(resolved, tuple)
                    and frozenset(resolved) == expected
                ):
                    definitions.add(path.relative_to(ROOT).as_posix())

    assert definitions == {CANONICAL_CELL_MODULE}


def test_composition_graph_adjacency_matches_the_canonical_topology() -> None:
    from waggledance.core.hex_cell_topology import HexCellTopology
    from waggledance.core.learning import composition_graph

    topology = HexCellTopology()
    expected = {
        cell: frozenset(topology.get_neighbors(cell))
        for cell in ALL_CELLS
    }

    assert composition_graph._ADJACENCY == expected


@pytest.mark.parametrize("module_name", CELL_KEYWORD_CONSUMERS)
def test_cell_keyword_metadata_covers_exactly_the_canonical_cells(
    module_name: str,
) -> None:
    module = importlib.import_module(module_name)

    assert frozenset(module._CELL_KEYWORDS) == frozenset(ALL_CELLS)
