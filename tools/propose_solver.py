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

The 14 gates and their verdicts:
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
 11. No secrets, no absolute local paths (walks keys + values +
      canonical JSON serialization)
 12. No hidden LLM dependency (whole-body scan; only provenance_note
      and llm_dependency itself are excluded)
 13. Closed-world runtime dependency for algorithm / table_lookup
      (no network, subprocess, filesystem, env, clock, randomness, or
      LLM tokens unless llm_dependency.required=true)
 14. machine_invariants shape check (optional). When the optional
      `machine_invariants` field is present, validate each entry's
      expr as a boolean in the machine-checkable subset (no calls,
      no attr, no subscript; all identifiers declared as inputs or
      outputs). Future SMT gate will run Z3 over exactly this field.

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

# Coverage-lift thresholds. Three-tier verdict:
#   lift <  reject_threshold       → REJECT_LOW_VALUE
#   lift <  accept_threshold       → ACCEPT_SHADOW_ONLY (worth observing)
#   lift >= accept_threshold       → ACCEPT_CANDIDATE  (worth promoting)
DEFAULT_COVERAGE_ACCEPT_THRESHOLD = 0.02
DEFAULT_COVERAGE_REJECT_THRESHOLD = 0.005

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


def _walk_strings(obj):
    """Yield every string found anywhere in `obj`, covering both dict
    keys and dict values, recursively."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str):
                yield k
            yield from _walk_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_strings(v)


def gate_no_secrets_no_paths(proposal: dict) -> dict:
    errors = []
    # Two scan surfaces: every string recursively (catches dict keys
    # too) AND the canonical JSON serialization of the whole proposal
    # (catches structures that become dangerous only after joining).
    canonical = json.dumps(proposal, sort_keys=True, default=str)
    surfaces = list(_walk_strings(proposal)) + [canonical]
    for text in surfaces:
        for rx in _SECRET_RES:
            if rx.search(text):
                errors.append({"pattern": rx.pattern,
                               "message": "secret-like token detected"})
        for rx in _ABSOLUTE_PATH_RES:
            if rx.search(text):
                errors.append({"pattern": rx.pattern,
                               "message": "local absolute path detected"})
    # Dedup by (pattern, message)
    seen: set[tuple[str, str]] = set()
    dedup: list[dict] = []
    for e in errors:
        key = (e["pattern"], e["message"])
        if key in seen:
            continue
        seen.add(key)
        dedup.append(e)
    return {"gate": "no_secrets_no_paths", "ok": not dedup, "errors": dedup}


def gate_llm_dependency(proposal: dict) -> dict:
    """LLM dependency is allowed but must be explicitly declared. We detect
    an undeclared dependency by scanning the *whole* proposal body — minus
    provenance_note and llm_dependency itself — for LLM vocabulary when
    `llm_dependency.required` is missing or false.

    Scan surface expanded per GPT round 4 review: previously only purpose
    + formula_or_algorithm + assumptions were checked; now inputs[].description,
    outputs[].description, expected_failure_modes[].behavior, examples[].source,
    uncertainty_declaration, and tags are all included.
    """
    decl = proposal.get("llm_dependency") or {}
    declared_required = bool(decl.get("required"))
    if declared_required:
        return {"gate": "llm_dependency", "ok": True, "errors": []}

    suspicious_tokens = ("llm", "chatgpt", "ollama", "gpt-4", "gpt-5",
                         "claude", "anthropic", "language model",
                         "prompt", "generate text", "completion",
                         "model.generate", "chat completion")

    # Build scan copy: everything except provenance_note and llm_dependency
    scan_copy = {k: v for k, v in proposal.items()
                 if k not in ("provenance_note", "llm_dependency")}
    haystack = " ".join(_walk_strings(scan_copy)).lower()
    hits = [t for t in suspicious_tokens if t in haystack]
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


# Closed-world dependency tokens forbidden in algorithm / table_lookup
# bodies unless llm_dependency.required=true (in which case they're
# part of a declared fallback). Pattern matches word-ish boundaries.
_CLOSED_WORLD_FORBIDDEN = (
    # Network
    "http:", "https:", "socket", "requests.", "urllib", "httpx",
    "fetch(", "curl ",
    # Subprocess / shell
    "subprocess", "popen", "os.system", "os.exec", "shell=",
    # Filesystem beyond declared inputs
    "open(", "read_text", "write_text", "with open ", "pathlib.path(",
    # Env / config state
    "os.environ", "getenv(",
    # Non-determinism
    "time.time", "datetime.now", "time.monotonic", "time.perf_counter",
    "random.", "secrets.", "uuid.uuid",
    # Hidden LLM calls
    "prompt", "completion", "model.generate", "chat completion",
    "openai.", "anthropic.",
)


_MACHINE_INV_SAFE_NODES = (
    ast.Expression, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare,
    ast.Name, ast.Constant, ast.Load,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd, ast.Not, ast.And, ast.Or,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)


def _machine_inv_shape_ok(expr: str) -> tuple[bool, str]:
    """Return (ok, reason) validating that `expr` is in the machine-
    checkable subset: booleans over declared names, numeric literals,
    arithmetic, comparisons, and/or/not. No calls, no attribute access,
    no subscripts — this keeps the door open for a future Z3 pass over
    exactly this structure."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        return False, f"parse error: {e.msg}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            return False, "function call forbidden"
        if isinstance(node, ast.Attribute):
            return False, "attribute access forbidden"
        if isinstance(node, ast.Subscript):
            return False, "subscript forbidden"
        if not isinstance(node, _MACHINE_INV_SAFE_NODES):
            return False, f"disallowed AST node: {type(node).__name__}"
    return True, ""


def gate_machine_invariants_shape(proposal: dict) -> dict:
    """Optional shape check for `machine_invariants`. When present the
    gate verifies:
      - each entry's id is unique within the proposal
      - each entry's expr parses cleanly as a boolean in the
        machine-checkable subset (no calls, no attrs, no subscripts)
      - every identifier referenced in expr is in the declared input
        or output names (no free variables)

    A future SMT-based gate will run Z3 over exactly these expressions;
    today this check just prevents invariants that couldn't be handed
    to an SMT layer later.

    If `machine_invariants` is missing or empty, the gate is a no-op.
    """
    items = proposal.get("machine_invariants") or []
    if not items:
        return {"gate": "machine_invariants_shape", "ok": True,
                "skipped": True, "errors": []}

    declared_names: set[str] = set()
    for i in proposal.get("inputs", []) or []:
        n = i.get("name")
        if n:
            declared_names.add(n)
    for o in proposal.get("outputs", []) or []:
        n = o.get("name")
        if n:
            declared_names.add(n)

    seen_ids: set[str] = set()
    errors: list[dict] = []
    for inv in items:
        inv_id = inv.get("id", "")
        expr = inv.get("expr", "")
        if not inv_id or not expr:
            errors.append({"id": inv_id, "message": "id and expr required"})
            continue
        if inv_id in seen_ids:
            errors.append({"id": inv_id, "message": "duplicate id"})
            continue
        seen_ids.add(inv_id)
        ok, reason = _machine_inv_shape_ok(expr)
        if not ok:
            errors.append({"id": inv_id, "expr": expr, "message": reason})
            continue
        # Check every identifier in expr is declared
        try:
            tree = ast.parse(expr, mode="eval")
            used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        except Exception:
            used = set()
        free = used - declared_names
        if free:
            errors.append({
                "id": inv_id, "expr": expr,
                "message": "undeclared identifiers",
                "free": sorted(free),
            })
    return {"gate": "machine_invariants_shape", "ok": not errors,
            "errors": errors, "skipped": False}


def gate_closed_world(proposal: dict) -> dict:
    """For algorithm / table_lookup bodies, forbid tokens that imply
    any side-channel input at runtime (network, subprocess, filesystem
    beyond declared inputs, env reads, clocks, randomness, or LLM
    calls). Skipped for formula_chain — those are already statically
    constrained by the AST whitelist in gate_deterministic_replay.

    llm_dependency.required=true acts as an escape hatch for the LLM
    subset of tokens, but never for filesystem/env/random/clock — those
    remain forbidden regardless.
    """
    fa = proposal.get("formula_or_algorithm") or {}
    kind = fa.get("kind")
    if kind == "formula_chain":
        return {"gate": "closed_world", "ok": True, "skipped": True,
                "reason": "formula_chain is AST-checked in gate_deterministic_replay",
                "errors": []}

    llm_declared = bool((proposal.get("llm_dependency") or {}).get("required"))

    # Scan the algorithm/table_lookup body prose only
    body = " ".join([
        str(fa.get("description", "")),
        str(fa.get("pseudo_code", "")),
        str(fa.get("lookup_key", "")),
        str(fa.get("source", "")),
    ]).lower()

    # LLM-like tokens may be legitimate if llm_dependency.required=true.
    # Everything else must be absent regardless.
    llm_like = {"prompt", "completion", "model.generate", "chat completion",
                "openai.", "anthropic."}
    errors: list[dict] = []
    for token in _CLOSED_WORLD_FORBIDDEN:
        if token not in body:
            continue
        if llm_declared and token in llm_like:
            continue
        errors.append({
            "token": token,
            "message": f"forbidden runtime dependency '{token}' in algorithm/table_lookup body",
        })
    return {"gate": "closed_world", "ok": not errors, "errors": errors,
            "skipped": False}


# ── Supporting helpers ─────────────────────────────────────────────

def _proposal_to_axiom_shape(proposal: dict) -> dict:
    """Project a proposal into the dict shape expected by solver_hash and
    the legacy canonical_hash (same shape as a YAML axiom file).

    Extended per GPT round 4 review to carry more semantic dimensions so
    proposals that differ only in applicability, declared failure
    behavior, type surface, domain, or secondary outputs no longer
    collide on hash:
    - `domain` pass-through from the proposal
    - all outputs (not just the primary value) via extra synthetic
      formulas carrying output_unit
    - assumptions + expected_failure_modes folded into validation so
      they contribute to the strict invariant block
    - input `type` in addition to `unit`
    """
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

    # All declared outputs contribute, not just primary. Use synthetic
    # formula entries with output_unit so solver_hash picks them up via
    # _normalize_outputs. Primary still recorded in solver_output_schema.
    outputs = proposal.get("outputs", []) or []
    primary: dict | None = None
    for o in outputs:
        name = o.get("name")
        unit = o.get("unit", "")
        if name:
            formulas.append({
                "name": f"output_{name}",
                "formula": f"declared_output:{name}",
                "output_unit": unit,
            })
        if o.get("primary") or primary is None:
            primary = {"name": name, "unit": unit}

    variables = {
        i["name"]: {
            "unit": i.get("unit", ""),
            # Type included so W ↔ ratio ↔ count differences move the hash
            # even when units happen to coincide accidentally.
            "type": i.get("type", ""),
        }
        for i in proposal.get("inputs", []) or []
    }

    # Validation block combines explicit invariants with assumptions and
    # expected failure modes — all three are semantic dimensions, so
    # changing any of them should move the hash.
    validation = [{"check": inv} for inv in proposal.get("invariants") or []]
    for assumption in proposal.get("assumptions") or []:
        if isinstance(assumption, str) and assumption.strip():
            validation.append({"check": f"assume: {assumption.strip()}"})
    for fm in proposal.get("expected_failure_modes") or []:
        if isinstance(fm, dict):
            cond = (fm.get("condition") or "").strip()
            beh = (fm.get("behavior") or "").strip()
            if cond or beh:
                validation.append({"check": f"failure_mode: when({cond}) -> {beh}"})

    # Tags carry domain so two proposals differing only in stated domain
    # produce different hashes.
    tags = list(proposal.get("tags", []) or [])
    domain = proposal.get("domain")
    if domain:
        tags.append(f"domain:{domain}")

    return {
        "model_id": proposal.get("solver_name"),
        "formulas": formulas,
        "variables": variables,
        "solver_output_schema": {"primary_value": primary or {}},
        "validation": validation,
        "tags": tags,
        "cell_id": proposal.get("cell_id"),
        "domain": domain,
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
                   coverage_threshold: float,
                   reject_low_value_threshold: float = DEFAULT_COVERAGE_REJECT_THRESHOLD) -> str:
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
              "no_secrets_no_paths", "llm_dependency",
              "closed_world", "machine_invariants_shape"):
        if g in by_gate and not by_gate[g]["ok"]:
            return V_REJECT_SCHEMA

    # All gates pass → three-tier verdict by coverage_lift:
    #   < reject_low_value_threshold → REJECT_LOW_VALUE
    #   < coverage_threshold         → ACCEPT_SHADOW_ONLY
    #   >= coverage_threshold        → ACCEPT_CANDIDATE
    lift = (proposal.get("expected_coverage_lift") or {}).get("value", 0.0)
    if lift < reject_low_value_threshold:
        return V_REJECT_LOW_VALUE
    if lift < coverage_threshold:
        return V_ACCEPT_SHADOW_ONLY
    return V_ACCEPT_CANDIDATE


# ── Orchestration ──────────────────────────────────────────────────

def evaluate_proposal(
    proposal: dict,
    axioms_dir: Path = AXIOMS_DIR,
    coverage_threshold: float = DEFAULT_COVERAGE_ACCEPT_THRESHOLD,
    latency_budget_ms: float = DEFAULT_LATENCY_BUDGET_MS,
    reject_low_value_threshold: float = DEFAULT_COVERAGE_REJECT_THRESHOLD,
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
        ordered_gates.append(gate_closed_world(proposal))
        ordered_gates.append(gate_machine_invariants_shape(proposal))
    else:
        # Synthesize skipped entries so callers always see all 14 gate names
        for name in ("cell_exists", "hash_duplicate", "io_types",
                     "deterministic_replay", "unit_consistency",
                     "contradiction", "invariants_present",
                     "tests_present", "latency_budget",
                     "no_secrets_no_paths", "llm_dependency",
                     "closed_world", "machine_invariants_shape"):
            ordered_gates.append({"gate": name, "ok": False,
                                  "skipped": True,
                                  "errors": [{"message": "skipped due to schema failure"}]})

    verdict = decide_verdict(ordered_gates, proposal, coverage_threshold,
                              reject_low_value_threshold=reject_low_value_threshold)
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
        latency_budget_ms: float = DEFAULT_LATENCY_BUDGET_MS,
        reject_low_value_threshold: float = DEFAULT_COVERAGE_REJECT_THRESHOLD) -> dict:
    proposal = load_proposal(proposal_path)
    result = evaluate_proposal(
        proposal,
        coverage_threshold=coverage_threshold,
        latency_budget_ms=latency_budget_ms,
        reject_low_value_threshold=reject_low_value_threshold,
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
                    default=DEFAULT_COVERAGE_ACCEPT_THRESHOLD,
                    help="lift >= this → ACCEPT_CANDIDATE")
    ap.add_argument("--reject-low-value-threshold", type=float,
                    default=DEFAULT_COVERAGE_REJECT_THRESHOLD,
                    help="lift < this → REJECT_LOW_VALUE")
    ap.add_argument("--latency-budget-ms", type=float,
                    default=DEFAULT_LATENCY_BUDGET_MS)
    ap.add_argument("--json", action="store_true",
                    help="Print full result as JSON to stdout")
    args = ap.parse_args()

    out = run(args.proposal, args.report_dir,
              args.coverage_threshold, args.latency_budget_ms,
              reject_low_value_threshold=args.reject_low_value_threshold)
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
