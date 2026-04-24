#!/usr/bin/env python3
"""Proposal quality gate — evaluates a solver proposal against deterministic
checks and emits a verdict. **Does not auto-merge anything.**

Invocation:
    python tools/propose_solver.py path/to/proposal.{json,yaml}
    python tools/propose_solver.py path/to/proposal.json --json
    python tools/propose_solver.py path/to/proposal.yaml --report-dir docs/runs/

Output:
    docs/runs/proposal_gate_<proposal_id>_<utc>.md
    + machine-readable JSON summary

The 12 gates and their verdicts:
  1. Schema validation (solver_proposal.schema.json, Draft-07)
  2. Cell exists (one of 8 hex-topology cells)
  3. Hash duplicate check (strict solver_hash against existing axioms)
  4. Input/output type consistency
  5. Deterministic replay (run supplied tests if formula_chain)
  6. Unit consistency (inputs referenced in formulas declare units)
  7. Contradiction check (invariants compatible with in-cell solvers)
  8. Invariants present
  9. Tests present and runnable (or clearly declarative)
 10. Estimated latency below budget (default 100 ms)
 11. No secrets, no absolute local paths
 12. No hidden LLM dependency (must be explicitly declared)

Verdict (the overall result):
  REJECT_SCHEMA | REJECT_DUPLICATE | REJECT_CONTRADICTION |
  REJECT_LOW_VALUE | ACCEPT_SHADOW_ONLY | ACCEPT_CANDIDATE
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import re
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("pip install pyyaml", file=sys.stderr)
    sys.exit(1)

try:
    import jsonschema
except ImportError:
    print("pip install jsonschema", file=sys.stderr)
    sys.exit(1)


ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schemas" / "solver_proposal.schema.json"
AXIOMS_DIR = ROOT / "configs" / "axioms"
REPORT_DIR = ROOT / "docs" / "runs"

sys.path.insert(0, str(ROOT))
from waggledance.core.learning.solver_hash import (  # noqa: E402
    solver_hash, HashRegistry,
)


# 8 real hex-topology cells (mirrored from hex_cell_topology.py to avoid
# pulling in runtime deps from an offline tool).
VALID_CELLS = {
    "general", "thermal", "energy", "safety",
    "seasonal", "math", "system", "learning",
}

# Coverage-lift threshold. A passing proposal below this goes to
# ACCEPT_SHADOW_ONLY; at or above, ACCEPT_CANDIDATE.
DEFAULT_COVERAGE_ACCEPT_THRESHOLD = 0.02

# Latency budget in ms above which gate #10 fails.
DEFAULT_LATENCY_BUDGET_MS = 100

# Patterns that would indicate a secret or local absolute path has
# leaked into a proposal.
_SECRET_RES = [
    re.compile(r"\bWAGGLE_API_KEY\b"),
    re.compile(r"\bgnt_[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bBearer\s+[A-Za-z0-9_.-]+"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),        # AWS access key format
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),     # common API-key prefix
]
_ABSOLUTE_PATH_RES = [
    re.compile(r"[A-Za-z]:\\(?:Users|Program Files|Python)", re.IGNORECASE),
    re.compile(r"\\\\(?:wsl|share)"),
    re.compile(r"^/(?:home|root|etc|var|opt|Users)/", re.MULTILINE),
]

# Verdicts (ordered from worst to best)
V_REJECT_SCHEMA = "REJECT_SCHEMA"
V_REJECT_DUPLICATE = "REJECT_DUPLICATE"
V_REJECT_CONTRADICTION = "REJECT_CONTRADICTION"
V_REJECT_LOW_VALUE = "REJECT_LOW_VALUE"
V_ACCEPT_SHADOW_ONLY = "ACCEPT_SHADOW_ONLY"
V_ACCEPT_CANDIDATE = "ACCEPT_CANDIDATE"


# ── Proposal loading ───────────────────────────────────────────────

def load_proposal(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    # Default: YAML (also parses JSON)
    return yaml.safe_load(text) or {}


# ── Gate implementations ───────────────────────────────────────────

def gate_schema(proposal: dict) -> dict:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft7Validator(schema)
    errors = [
        {"path": list(e.absolute_path), "message": e.message}
        for e in validator.iter_errors(proposal)
    ]
    return {"gate": "schema", "ok": not errors, "errors": errors}


def gate_cell_exists(proposal: dict) -> dict:
    cell = proposal.get("cell_id")
    ok = cell in VALID_CELLS
    return {
        "gate": "cell_exists",
        "ok": ok,
        "errors": [] if ok else [{"cell_id": cell, "valid": sorted(VALID_CELLS)}],
    }


def gate_hash_duplicate(proposal: dict, axioms_dir: Path) -> dict:
    # Convert proposal → axiom-like shape for hashing so we hit the same
    # semantic dimensions the existing library was hashed on.
    axiom_shape = _proposal_to_axiom_shape(proposal)
    h = solver_hash(axiom_shape)
    reg = HashRegistry.from_axioms_dir(axioms_dir, use_strict=True)
    ok = h not in reg
    return {
        "gate": "hash_duplicate",
        "ok": ok,
        "solver_hash": h,
        "errors": [] if ok else [{"message": "hash matches an existing axiom"}],
    }


def gate_io_types(proposal: dict) -> dict:
    errors: list[dict] = []
    primary_outs = [o for o in proposal.get("outputs", []) if o.get("primary")]
    if len(primary_outs) > 1:
        errors.append({"message": "multiple outputs marked primary; pick exactly one"})
    input_names = {i["name"] for i in proposal.get("inputs", [])}
    if len(input_names) != len(proposal.get("inputs", [])):
        errors.append({"message": "duplicate input names"})
    output_names = {o["name"] for o in proposal.get("outputs", [])}
    if len(output_names) != len(proposal.get("outputs", [])):
        errors.append({"message": "duplicate output names"})
    # Check that test inputs match declared inputs
    for t in proposal.get("tests", []) or []:
        test_inputs = set((t.get("inputs") or {}).keys())
        unknown = test_inputs - input_names
        if unknown:
            errors.append({
                "message": f"test '{t.get('name')}' references unknown inputs",
                "unknown": sorted(unknown),
            })
    return {"gate": "io_types", "ok": not errors, "errors": errors}


def gate_deterministic_replay(proposal: dict) -> dict:
    """Run supplied tests against the formula chain. Non-formula-chain
    proposals pass this gate without execution — they'll be handled later.
    """
    fa = proposal.get("formula_or_algorithm") or {}
    if fa.get("kind") != "formula_chain":
        return {
            "gate": "deterministic_replay",
            "ok": True,
            "skipped": True,
            "reason": f"proposal kind '{fa.get('kind')}' — replay not applicable",
            "errors": [],
        }

    steps = fa.get("steps") or []
    tests = proposal.get("tests") or []
    errors: list[dict] = []
    ran = 0

    for t in tests:
        inputs = dict(t.get("inputs") or {})
        expected = t.get("expected")
        # Declarative test (no expected value) → skip execution, just note
        if expected is None or (isinstance(expected, str) and not expected.strip()):
            continue

        env = dict(inputs)
        # Make safe built-ins available for formulas that use them (max/min/abs)
        safe_builtins = {"max": max, "min": min, "abs": abs, "round": round}
        try:
            for step in steps:
                formula = step["formula"]
                # Compile with ast to refuse dangerous constructs
                _ensure_safe_expr(formula)
                value = eval(  # noqa: S307 — safe_expr already audited
                    compile(formula, f"<step:{step['name']}>", "eval"),
                    {"__builtins__": {}},
                    {**safe_builtins, **env},
                )
                env[step["name"]] = value
            ran += 1
            # Primary output is whatever the last step produces, unless the
            # test expected is a dict keyed by output name.
            if isinstance(expected, dict):
                for k, v in expected.items():
                    got = env.get(k)
                    if not _numeric_close(got, v, t.get("tolerance")):
                        errors.append({
                            "test": t.get("name"),
                            "output": k, "expected": v, "got": got,
                        })
            else:
                final = env.get(steps[-1]["name"])
                if not _numeric_close(final, expected, t.get("tolerance")):
                    errors.append({
                        "test": t.get("name"),
                        "expected": expected, "got": final,
                    })
        except Exception as exc:
            errors.append({
                "test": t.get("name"),
                "exception": f"{type(exc).__name__}: {exc}",
            })

    return {
        "gate": "deterministic_replay",
        "ok": not errors,
        "tests_ran": ran,
        "errors": errors,
    }


def gate_unit_consistency(proposal: dict) -> dict:
    """Every input name used in a formula_chain step must be declared with
    a unit, and every step must declare an output_unit."""
    fa = proposal.get("formula_or_algorithm") or {}
    if fa.get("kind") != "formula_chain":
        return {"gate": "unit_consistency", "ok": True, "skipped": True, "errors": []}
    errors: list[dict] = []

    declared: dict[str, str] = {}
    for i in proposal.get("inputs", []) or []:
        declared[i["name"]] = i.get("unit", "")
    for step in fa.get("steps") or []:
        name, unit = step.get("name"), step.get("output_unit")
        if not unit:
            errors.append({"step": name, "message": "missing output_unit"})
        if name:
            declared[name] = unit or ""

    # Check every identifier referenced in a formula is declared with a unit
    for step in fa.get("steps") or []:
        idents = _extract_identifiers(step.get("formula", ""))
        for ident in idents:
            if ident in declared:
                continue
            # builtins are fine
            if ident in {"max", "min", "abs", "round"}:
                continue
            errors.append({
                "step": step.get("name"),
                "unknown_identifier": ident,
            })
    return {"gate": "unit_consistency", "ok": not errors, "errors": errors}


def gate_contradiction(proposal: dict, axioms_dir: Path) -> dict:
    """Reject if the new invariants include one that literally contradicts
    an existing in-cell solver's invariant (same variable, opposite
    direction comparison). Cheap syntactic check — does not attempt SMT."""
    cell = proposal.get("cell_id")
    new_inv_parsed = [_parse_simple_inv(s) for s in proposal.get("invariants", []) or []]
    new_inv_parsed = [p for p in new_inv_parsed if p is not None]
    if not new_inv_parsed:
        return {"gate": "contradiction", "ok": True, "errors": []}

    if not axioms_dir.exists():
        return {"gate": "contradiction", "ok": True, "errors": []}

    errors: list[dict] = []
    for path in axioms_dir.rglob("*.yaml"):
        try:
            with open(path, encoding="utf-8") as f:
                axiom = yaml.safe_load(f) or {}
        except Exception:
            continue
        if axiom.get("cell_id") != cell:
            continue
        for v in axiom.get("validation", []) or []:
            inv = v.get("check") or v.get("condition") or ""
            existing = _parse_simple_inv(inv)
            if existing is None:
                continue
            for new in new_inv_parsed:
                if _inv_contradicts(new, existing):
                    errors.append({
                        "existing_file": path.relative_to(axioms_dir).as_posix(),
                        "existing_invariant": inv,
                        "new_invariant": _inv_to_str(new),
                    })
    return {"gate": "contradiction", "ok": not errors, "errors": errors}


def gate_invariants_present(proposal: dict) -> dict:
    invs = proposal.get("invariants") or []
    ok = len(invs) >= 1 and all(isinstance(s, str) and s.strip() for s in invs)
    return {
        "gate": "invariants_present",
        "ok": ok,
        "errors": [] if ok else [{"message": "at least one non-empty invariant required"}],
    }


def gate_tests_present(proposal: dict) -> dict:
    tests = proposal.get("tests") or []
    if not tests:
        return {
            "gate": "tests_present", "ok": False,
            "errors": [{"message": "at least one test required"}],
        }
    # Each test must have a name and inputs
    errors = []
    for t in tests:
        if not t.get("name"):
            errors.append({"message": "test missing name"})
        if t.get("inputs") is None:
            errors.append({"message": f"test '{t.get('name')}' missing inputs"})
    return {"gate": "tests_present", "ok": not errors, "errors": errors}


def gate_latency_budget(proposal: dict, budget_ms: float) -> dict:
    est = proposal.get("estimated_latency_ms", 0)
    ok = isinstance(est, (int, float)) and 0 <= est <= budget_ms
    errors = []
    if not ok:
        errors.append({
            "message": f"estimated_latency_ms={est} exceeds budget={budget_ms}",
        })
    return {"gate": "latency_budget", "ok": ok, "errors": errors, "budget_ms": budget_ms}


def gate_no_secrets_no_paths(proposal: dict) -> dict:
    errors = []

    def _walk(obj):
        if isinstance(obj, str):
            yield obj
        elif isinstance(obj, dict):
            for v in obj.values():
                yield from _walk(v)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                yield from _walk(v)

    for text in _walk(proposal):
        for rx in _SECRET_RES:
            if rx.search(text):
                errors.append({"pattern": rx.pattern,
                               "message": "secret-like token detected"})
        for rx in _ABSOLUTE_PATH_RES:
            if rx.search(text):
                errors.append({"pattern": rx.pattern,
                               "message": "local absolute path detected"})
    return {"gate": "no_secrets_no_paths", "ok": not errors, "errors": errors}


def gate_llm_dependency(proposal: dict) -> dict:
    """LLM dependency is allowed but must be explicitly declared. We detect
    an undeclared dependency by scanning free-text fields for LLM vocabulary
    when llm_dependency.required is missing or false."""
    decl = proposal.get("llm_dependency") or {}
    declared_required = bool(decl.get("required"))
    if declared_required:
        return {"gate": "llm_dependency", "ok": True, "errors": []}

    suspicious_tokens = ("llm", "chatgpt", "ollama", "gpt-4", "gpt-5",
                         "claude", "anthropic", "prompt", "generate text",
                         "language model")
    haystack = " ".join([
        str(proposal.get("purpose", "")),
        str(proposal.get("provenance_note", "")),
        json.dumps(proposal.get("formula_or_algorithm") or {}, default=str),
        " ".join(proposal.get("assumptions") or []),
    ]).lower()

    # provenance_note almost always names the teacher ("claude-opus-…") →
    # exclude it from detection. We only flag the formula_or_algorithm +
    # purpose + assumptions surface.
    haystack_strict = " ".join([
        str(proposal.get("purpose", "")),
        json.dumps(proposal.get("formula_or_algorithm") or {}, default=str),
        " ".join(proposal.get("assumptions") or []),
    ]).lower()
    hits = [t for t in suspicious_tokens if t in haystack_strict]
    if hits:
        return {
            "gate": "llm_dependency",
            "ok": False,
            "errors": [{
                "message": "undeclared LLM vocabulary in proposal body",
                "hits": hits,
            }],
        }
    return {"gate": "llm_dependency", "ok": True, "errors": []}


# ── Supporting helpers ─────────────────────────────────────────────

def _proposal_to_axiom_shape(proposal: dict) -> dict:
    """Project a proposal into the dict shape expected by solver_hash and
    the legacy canonical_hash (same shape as a YAML axiom file)."""
    fa = proposal.get("formula_or_algorithm") or {}
    formulas: list[dict] = []
    if fa.get("kind") == "formula_chain":
        for step in fa.get("steps") or []:
            formulas.append({
                "name": step.get("name"),
                "formula": step.get("formula", ""),
                "output_unit": step.get("output_unit"),
            })
    elif fa.get("kind") in {"algorithm", "table_lookup"}:
        # Non-formula kinds contribute a single synthetic formula so the
        # hash still moves with content changes.
        formulas = [{
            "name": "algorithm_body",
            "formula": json.dumps(fa, sort_keys=True),
            "output_unit": "",
        }]
    variables = {
        i["name"]: {"unit": i.get("unit", "")}
        for i in proposal.get("inputs", []) or []
    }
    primary = None
    for o in proposal.get("outputs", []) or []:
        if o.get("primary") or primary is None:
            primary = {"name": o["name"], "unit": o.get("unit", "")}
    validation = [{"check": inv} for inv in proposal.get("invariants") or []]
    return {
        "model_id": proposal.get("solver_name"),
        "formulas": formulas,
        "variables": variables,
        "solver_output_schema": {"primary_value": primary or {}},
        "validation": validation,
        "tags": proposal.get("tags", []),
        "cell_id": proposal.get("cell_id"),
    }


def _numeric_close(a: Any, b: Any, tolerance: float | None) -> bool:
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return a == b
    tol = tolerance if tolerance is not None else 1e-6
    scale = max(1.0, abs(a), abs(b))
    return math.isclose(a, b, rel_tol=tol, abs_tol=tol * scale)


_SAFE_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name,
    ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
    ast.Pow, ast.USub, ast.UAdd, ast.BoolOp, ast.And, ast.Or,
    ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.Call, ast.Tuple, ast.List,
)


def _ensure_safe_expr(src: str) -> None:
    tree = ast.parse(src, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, _SAFE_NODES):
            raise ValueError(f"disallowed AST node: {type(node).__name__}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise ValueError("only bare function calls allowed (max/min/abs/round)")
            if node.func.id not in {"max", "min", "abs", "round"}:
                raise ValueError(f"disallowed function: {node.func.id}")


def _extract_identifiers(expr: str) -> set[str]:
    try:
        tree = ast.parse(expr, mode="eval")
    except Exception:
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            out.add(node.id)
    return out


_INV_RE = re.compile(
    r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*(>=|<=|>|<|==|!=)\s*([-+]?\d+(?:\.\d+)?)\s*$"
)


def _parse_simple_inv(s: str) -> tuple[str, str, float] | None:
    m = _INV_RE.match(s or "")
    if not m:
        return None
    var, op, num = m.group(1), m.group(2), float(m.group(3))
    return (var, op, num)


def _inv_to_str(p: tuple[str, str, float]) -> str:
    return f"{p[0]} {p[1]} {p[2]}"


def _inv_contradicts(a, b) -> bool:
    """True if invariants a and b cannot both hold for any value of the
    variable. Only handles single-variable numeric comparisons."""
    if a[0] != b[0]:
        return False
    va, vb = a[2], b[2]
    oa, ob = a[1], b[1]
    if (oa, ob) in {(">", "<"), ("<", ">"), (">=", "<"), ("<=", ">"),
                    (">", "<="), ("<", ">=")}:
        # e.g. x > 5 AND x < 3 → contradiction if 5 >= 3
        if oa.startswith(">") and ob.startswith("<"):
            return va >= vb
        if oa.startswith("<") and ob.startswith(">"):
            return vb >= va
    if oa == "==" and ob == "==" and va != vb:
        return True
    if oa == "==" and ob == "!=" and va == vb:
        return True
    if oa == "!=" and ob == "==" and va == vb:
        return True
    return False


# ── Verdict composition ────────────────────────────────────────────

def decide_verdict(gate_results: list[dict], proposal: dict,
                   coverage_threshold: float) -> str:
    by_gate = {g["gate"]: g for g in gate_results}

    if not by_gate["schema"]["ok"]:
        return V_REJECT_SCHEMA
    # Schema-derived gates only meaningful after schema passes
    if not by_gate["cell_exists"]["ok"]:
        return V_REJECT_SCHEMA
    if not by_gate["hash_duplicate"]["ok"]:
        return V_REJECT_DUPLICATE
    if not by_gate["contradiction"]["ok"]:
        return V_REJECT_CONTRADICTION

    # Any other remaining failure is a "shape" reject → treat as schema-ish
    for g in ("io_types", "deterministic_replay", "unit_consistency",
              "invariants_present", "tests_present", "latency_budget",
              "no_secrets_no_paths", "llm_dependency"):
        if not by_gate[g]["ok"]:
            return V_REJECT_SCHEMA

    # All gates pass → decide between ACCEPT_* by coverage_lift
    lift = (proposal.get("expected_coverage_lift") or {}).get("value", 0.0)
    if lift < coverage_threshold:
        return V_ACCEPT_SHADOW_ONLY
    return V_ACCEPT_CANDIDATE


# ── Orchestration ──────────────────────────────────────────────────

def evaluate_proposal(
    proposal: dict,
    axioms_dir: Path = AXIOMS_DIR,
    coverage_threshold: float = DEFAULT_COVERAGE_ACCEPT_THRESHOLD,
    latency_budget_ms: float = DEFAULT_LATENCY_BUDGET_MS,
) -> dict:
    """Run all 12 gates + compute overall verdict. Returns a dict with
    `gates`, `verdict`, and proposal metadata."""
    ordered_gates: list[dict] = []

    schema_r = gate_schema(proposal)
    ordered_gates.append(schema_r)

    # If schema fails, still run cheap gates to give the teacher useful
    # feedback, but hash/contradiction need valid structure so skip those.
    if schema_r["ok"]:
        ordered_gates.append(gate_cell_exists(proposal))
        ordered_gates.append(gate_hash_duplicate(proposal, axioms_dir))
        ordered_gates.append(gate_io_types(proposal))
        ordered_gates.append(gate_deterministic_replay(proposal))
        ordered_gates.append(gate_unit_consistency(proposal))
        ordered_gates.append(gate_contradiction(proposal, axioms_dir))
        ordered_gates.append(gate_invariants_present(proposal))
        ordered_gates.append(gate_tests_present(proposal))
        ordered_gates.append(gate_latency_budget(proposal, latency_budget_ms))
        ordered_gates.append(gate_no_secrets_no_paths(proposal))
        ordered_gates.append(gate_llm_dependency(proposal))
    else:
        # Synthesize skipped entries so callers always see all 12 gate names
        for name in ("cell_exists", "hash_duplicate", "io_types",
                     "deterministic_replay", "unit_consistency",
                     "contradiction", "invariants_present",
                     "tests_present", "latency_budget",
                     "no_secrets_no_paths", "llm_dependency"):
            ordered_gates.append({"gate": name, "ok": False,
                                  "skipped": True,
                                  "errors": [{"message": "skipped due to schema failure"}]})

    verdict = decide_verdict(ordered_gates, proposal, coverage_threshold)
    return {
        "proposal_id": proposal.get("proposal_id"),
        "solver_name": proposal.get("solver_name"),
        "cell_id": proposal.get("cell_id"),
        "verdict": verdict,
        "gates": ordered_gates,
    }


def render_report(result: dict, proposal: dict) -> str:
    gen_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        f"# Proposal gate report — {result.get('proposal_id') or '(unknown)'}",
        "",
        f"- **Generated:** {gen_at}",
        f"- **Proposal id:** `{result.get('proposal_id')}`",
        f"- **Solver name:** `{result.get('solver_name')}`",
        f"- **Cell:** `{result.get('cell_id')}`",
        f"- **Verdict:** **{result['verdict']}**",
        "",
        "## Gate results",
        "",
        "| # | gate | status | notes |",
        "|---|---|---|---|",
    ]
    for i, g in enumerate(result["gates"], start=1):
        status = "✅" if g.get("ok") else "❌"
        notes_parts: list[str] = []
        if g.get("skipped"):
            notes_parts.append("skipped")
        if g.get("errors"):
            for e in g["errors"][:3]:
                notes_parts.append(_short(str(e)))
            if len(g["errors"]) > 3:
                notes_parts.append(f"+{len(g['errors']) - 3} more")
        notes = "; ".join(notes_parts) if notes_parts else ""
        lines.append(f"| {i} | {g['gate']} | {status} | {notes} |")
    return "\n".join(lines) + "\n"


def _short(s: str, n: int = 80) -> str:
    s = s.replace("|", "\\|").replace("\n", " ")
    return s if len(s) <= n else s[:n - 1] + "…"


def run(proposal_path: Path, report_dir: Path = REPORT_DIR,
        coverage_threshold: float = DEFAULT_COVERAGE_ACCEPT_THRESHOLD,
        latency_budget_ms: float = DEFAULT_LATENCY_BUDGET_MS) -> dict:
    proposal = load_proposal(proposal_path)
    result = evaluate_proposal(
        proposal,
        coverage_threshold=coverage_threshold,
        latency_budget_ms=latency_budget_ms,
    )

    pid = str(result.get("proposal_id") or "unknown").replace("/", "_")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"proposal_gate_{pid}_{stamp}.md"
    report_path.write_text(render_report(result, proposal), encoding="utf-8")
    return {
        "result": result,
        "report_path": report_path.as_posix(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("proposal", type=Path)
    ap.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    ap.add_argument("--coverage-threshold", type=float,
                    default=DEFAULT_COVERAGE_ACCEPT_THRESHOLD)
    ap.add_argument("--latency-budget-ms", type=float,
                    default=DEFAULT_LATENCY_BUDGET_MS)
    ap.add_argument("--json", action="store_true",
                    help="Print full result as JSON to stdout")
    args = ap.parse_args()

    out = run(args.proposal, args.report_dir,
              args.coverage_threshold, args.latency_budget_ms)
    r = out["result"]
    if args.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(f"verdict: {r['verdict']}")
        print(f"report:  {out['report_path']}")
        for g in r["gates"]:
            mark = "[ok]" if g.get("ok") else "[fail]"
            print(f"  {mark} {g['gate']}")
    # Exit 0 on accept or shadow, 1 on reject — makes this usable in CI
    return 0 if r["verdict"].startswith("ACCEPT") else 1


if __name__ == "__main__":
    sys.exit(main())
