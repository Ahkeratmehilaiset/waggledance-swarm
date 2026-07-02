#!/usr/bin/env python3
# SPDX-License-Identifier: BUSL-1.1
"""P1 proven-safe auto-sign class checker (RFC P1, PR 2/3) — DORMANT / UNWIRED.

Certifies whether a PR is in the narrow "proven-safe" class defined by
``docs/architecture/P1_PROVEN_SAFE_AUTOSIGN_CLASS_V1.md``. When (and only when)
the gate-wiring PR (#3) is operator-signed, the gate may consult this checker to
waive ONLY the per-PR operator signature for an in-class PR. **This file is
consulted by nothing yet** — it is pure, testable logic.

FAIL-CLOSED: a PR is IN-CLASS only if EVERY predicate A–G holds. Any path outside
the safe set, any C/D/E/G pattern, any F exclusion, empty input, or any parse
error/ambiguity -> NOT in class -> per-PR operator signature required. The checker
NEVER default-allows on uncertainty.

In-class predicates (all must hold):
  A  every changed path is in tests/** | docs/benchmarks/**, one of the exact
     sprint product gate-extension paths, OR is an ADDITIVE metrics counter on
     the positive METRICS_PATHS allowlist (default-empty).
  B  additive-metric paths are add-only/default-OFF; exact path allowlists are
     still guarded by F plus the C/D/E/G content screens.
  C  no claim_safe flip.
  D  no authority-flag edit (gate_skip/solver_call/receipt_required/clinical_decision).
  E  no control-plane / runtime-behavior change.
  F  hard exclusions: gate/charter/denylist, .agent-bridge/bin/**,
     .github/workflows/**, requirements*/lockfiles, AGENTS.md/CLAUDE.md/
     master-prompts, Rule-10 surface, anything the charter denylists.
  G  best-effort dangerous-callable screen (AST, alias-resolving) on ANY changed
     line: direct/aliased/from-import dangerous calls, dynamic-dispatch
     escape-hatch builtins anywhere, builtins.<hatch> dotted, and reflection /
     gadget-traversal dunders. A SCREEN, not a proof (RCE-freeness of arbitrary
     tests/ is undecidable); residuals are backstopped by build+dual-RCO+CI.

    python tools/check_proven_safe_autosign_class.py --changed-from-git origin/main --json
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Sequence

# tests/** + docs/benchmarks/** auto-sign by path, GUARDED by the fail-closed
# dangerous-callable scan (operator ruling 2026-06-24: keep tests/ in-class but
# block RCE via an AST dangerous-callable scan on all changed lines/paths; FP cost
# accepted). docs/runs/** dropped (off charter allowlist, low-value).
SAFE_ROOTS = ("tests/", "docs/benchmarks/")

# Operator option-B sprint product extension (2026-06-30/2026-07-02 bridge
# handoff): exact paths only, not a package wildcard. Network transports, auth,
# credential/secret, autonomy, and gate-verdict surfaces remain hard-excluded by
# predicate F before this allowlist is consulted.
PRODUCT_GATE_EXTENSION_PATHS = frozenset({
    "waggledance/adapters/http/routes/air01_advisory.py",
    "waggledance/adapters/http/routes/eng01_advisory.py",
    "waggledance/adapters/http/routes/eng06_advisory.py",
    "waggledance/adapters/http/routes/advisory_dashboard.py",
    "waggledance/adapters/http/routes/solvers.py",
    "waggledance/adapters/feeds/air01_advisory_refresher.py",
    "waggledance/adapters/feeds/eng01_advisory_refresher.py",
    "waggledance/adapters/feeds/eng06_advisory_refresher.py",
    "waggledance/adapters/feeds/advisory_refresh_ticker.py",
    "waggledance/adapters/cli/acct01_reconcile_bills.py",
    "waggledance/adapters/cli/email01_classify_inbox.py",
    "waggledance/adapters/cli/email02_index_vendor_emails.py",
    "waggledance/adapters/cli/eng01_recommend.py",
    "waggledance/adapters/cli/eng06_fireplace.py",
    "waggledance/adapters/cli/fin10_classify_receipts.py",
    "waggledance/adapters/cli/pdf01_extract_invoice.py",
    "waggledance/core/v3_13_0/acct01_unpaid_bill_reconciler.py",
    "waggledance/core/v3_13_0/air01_air_quality_advisor.py",
    "waggledance/core/v3_13_0/air01_digheran_adapter.py",
    "waggledance/core/v3_13_0/email01_inbox_priority_classifier.py",
    "waggledance/core/v3_13_0/email02_vendor_email_indexer.py",
    "waggledance/core/v3_13_0/eng01_advisory_card.py",
    "waggledance/core/v3_13_0/eng01_price_feed_adapter.py",
    "waggledance/core/v3_13_0/eng01_price_feed_response_parser.py",
    "waggledance/core/v3_13_0/eng01_spot_electricity.py",
    "waggledance/core/v3_13_0/eng06_advisory_card.py",
    "waggledance/core/v3_13_0/eng06_burn_log_adapter.py",
    "waggledance/core/v3_13_0/eng06_fireplace_advisor.py",
    "waggledance/core/v3_13_0/fin10_receipt_classifier.py",
    "waggledance/core/v3_13_0/pdf01_invoice_field_extractor.py",
})

# Positive metrics-allowlist for the additive-metric path. DEFAULT-EMPTY =>
# default-DENY: NO metric path auto-signs until the operator-signed charter
# carve-out PR populates this (e.g. "waggledance/**/metrics.py") alongside the
# matching charter allowlist entry. Per spec SS2.A + rco-1/lead steer: never
# "any non-denylisted path" (a denylist-gap on a sign-waiver path fails open).
METRICS_PATHS: tuple[str, ...] = ()

# F — explicit P1 hard exclusions (in addition to the charter denylist).
F_EXCLUDE_SUBSTRINGS = (
    ".agent-bridge/",
    ".agent-bridge/bin/",
    ".github/workflows/",
    "_http_transport.py",
    "credential_vault",
    "idle_consensus_charter",
    "idle_consensus_auto_merge",
    "check_bridge_changes_requested",
    "check_rco_pass_present",
    "check_proven_safe_autosign_class",   # the checker itself (anti-widening)
    "bridge_identity_registry",
    "bridge_event_taxonomy",
    "invoke-bridgemergedriver",
    "merge_with_bridge_receipt",
    "secret_markers",
    "solver_provenance",
    "ssrf_host_guard",
    "stage2_cutover",
    "human_approval",
    "verify_bridge_consensus",
    "write_rco_gate",
)
F_EXCLUDE_BASENAMES = frozenset(
    {"agents.md", "claude.md", "requirements.txt", "requirements.lock.txt",
     "pyproject.toml", "poetry.lock"}
)
F_EXCLUDE_BASENAME_SUBSTR = ("requirements", "master_prompt", "lock")
F_EXCLUDE_AUTH_MARKERS = (
    "/auth",
    "auth/",
    "auth.",
    "auth_",
    "_auth",
    "-auth",
    "authentication",
    "authorization",
    "oauth",
)

# C/D/E — risky tokens scanned in changed lines (fail-closed).
AUTHORITY_FLAGS = ("gate_skip", "solver_call", "receipt_required",
                   "clinical_decision", "consensus_grade", "claim_safe")
CONTROL_PLANE_TOKENS = ("def route", "routing", "control_plane", "dispatch(",
                        "merge(", "build_consensus", "rco_pass", "operator_sign")

# Dangerous-callable scan (operator ruling 2026-06-24): any dangerous callable on
# ANY changed line/path -> operator_sign (fail-closed). AST is the primary detector
# (resolves through import aliases, resists string-literal FPs + getattr evasion);
# the substring list is a FALLBACK for non-Python / unparseable hunks (e.g. .md).
DANGEROUS_BARE_NAMES = frozenset({"eval", "exec", "compile", "__import__"})
DANGEROUS_DOTTED = frozenset({
    "os.system", "os.popen", "os.remove", "os.unlink", "os.rename", "os.replace",
    "pickle.load", "pickle.loads", "marshal.load", "marshal.loads", "shutil.rmtree",
    "importlib.import_module", "operator.attrgetter", "operator.methodcaller",
})
# Dynamic-resolution ESCAPE-HATCH builtins. Flagged on ANY bare-Name reference
# (call or not) -> operator_sign, because they resolve callees dynamically and so
# bypass a dotted-name scan: vars()["os"].system, globals()["os"].system,
# __builtins__["eval"], getattr(os,"system"), f=getattr;f(...). Operator accepted
# the false-positive cost (a test referencing getattr now needs the sign).
# Best-effort: this is a SCREEN, not a proof (statically proving an arbitrary
# tests/ file RCE-free is undecidable). Novel dynamic-resolution + non-call vectors
# remain a documented residual backstopped by build+RCO+CI (rco-1/lead/operator
# 2026-06-24); P1 waives only the per-PR operator signature, never that review.
ESCAPE_HATCH_NAMES = frozenset({
    "getattr", "setattr", "delattr", "vars", "globals", "locals",
    "__import__", "eval", "exec", "compile", "__builtins__",
    "breakpoint",  # sys.breakpointhook / PYTHONBREAKPOINT runs arbitrary code
})
# Modules whose escape-hatch / dangerous-tail attributes (builtins.getattr,
# builtins.eval, ...) bypass the bare-Name escape-hatch flag via DOTTED access.
DANGEROUS_BUILTINS_MODULES = frozenset({"builtins", "__builtin__"})
# Dunder attributes used for reflection / gadget-chain traversal (object-graph
# walks to a dangerous callable). Flagged on ANY Attribute access OR Subscript with
# a constant-string key in this set -> operator_sign. Closes os.__dict__["system"],
# func.__globals__["..."], os.__getattribute__("system"),
# ().__class__.__bases__[0].__subclasses__()[i](...). Per rco-2 2026-06-24 this is
# the principled FINITE fix that closes the whole reflection class at once; it
# INCLUDES __class__ (the operator accepted the false-positive cost: a test merely
# reading x.__class__ now needs the sign — disqualifying my earlier omission).
DANGEROUS_DUNDER_ATTRS = frozenset({
    "__dict__", "__class__", "__bases__", "__subclasses__", "__mro__", "__base__",
    "__globals__", "__builtins__", "__subclasshook__",
    "__getattribute__", "__getattr__", "__import__",
})
DANGEROUS_DOTTED_PREFIXES = ("os.exec", "os.spawn", "subprocess.", "ctypes.",
                             "importlib.")
# from <module> import <name>: flag any from-import of these pure-dangerous modules,
# or a dangerous tail from a mixed module (os).
DANGEROUS_FROM_MODULES = frozenset({"subprocess", "ctypes", "importlib", "pickle",
                                    "marshal", "shutil"})
DANGEROUS_TAILS = frozenset({"system", "popen", "execv", "execve", "execl", "execlp",
                             "execvp", "spawnl", "spawnv", "spawnve", "rmtree",
                             "import_module"})
DANGEROUS_CALLABLES = ("os.system", "subprocess", "eval(", "exec(", "__import__",
                       "compile(", ".popen", "popen(", "importlib", "pickle.load",
                       "marshal.load", "ctypes", "shutil.rmtree", "os.remove",
                       "os.unlink", "os.rename", "os.replace", "os.exec", "os.spawn",
                       "getattr(", "setattr(", "globals(", "locals(")

# Metric constructors permitted for the additive-metric class (verified by AST,
# not regex — a regex cannot prove the constructor args are inert: a nested call
# like Counter(os.system('x'),'d') executes at import). See _is_additive_metrics_counter.
METRIC_CTORS = frozenset({"Counter", "Gauge", "Histogram", "Summary"})


def _norm(path: str) -> str:
    # Strip ONLY a leading "./" relative-marker — NOT all leading "."/"/" chars.
    # `.lstrip("./")` wrongly stripped the leading dot from dot-dirs
    # (".agent-bridge/" -> "agent-bridge/"), making the F-substring fence miss
    # them (tools #1384). Preserve dotfiles/dotdirs.
    p = path.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return p


def _is_f_excluded(path: str) -> str | None:
    """Return an exclusion reason if the path is hard-excluded (F), else None."""
    p = _norm(path).lower()
    base = p.rsplit("/", 1)[-1]
    if p.startswith("waggledance/core/autonomy/") or base == "autonomy.py":
        return "F exclusion (autonomy surface)"
    if p.startswith("tools/check_"):
        return "F exclusion (gate check tool)"
    if p.startswith("tools/idle_consensus_") or p.startswith(
        "waggledance/core/idle_consensus_"
    ):
        return "F exclusion (idle consensus gate code)"
    if p.startswith("docs/") and ("charter" in p or "contract" in p):
        return "F exclusion (charter/contract docs)"
    for marker in F_EXCLUDE_AUTH_MARKERS:
        if marker in p:
            return f"F exclusion (auth path marker '{marker}')"
    for sub in F_EXCLUDE_SUBSTRINGS:
        if sub in p:
            return f"F exclusion (path contains '{sub}')"
    if base in F_EXCLUDE_BASENAMES:
        return f"F exclusion (basename '{base}')"
    for sub in F_EXCLUDE_BASENAME_SUBSTR:
        if sub in base:
            return f"F exclusion (basename contains '{sub}')"
    return None


def _in_safe_roots(path: str) -> bool:
    return _norm(path).startswith(SAFE_ROOTS)


def _in_product_gate_extension(path: str) -> bool:
    return _norm(path) in PRODUCT_GATE_EXTENSION_PATHS


def _on_metrics_allowlist(path: str, metrics_paths: Sequence[str]) -> bool:
    """True iff path matches an explicit positive metrics-allowlist pattern.

    Default-DENY: with the default empty METRICS_PATHS, this is always False, so
    no metric path can auto-sign until the operator-signed carve-out populates it.
    """
    p = _norm(path)
    return any(fnmatch.fnmatch(p, pat) for pat in metrics_paths)


def _is_inert_literal(node: ast.AST) -> bool:
    """True iff an AST node is a pure literal (no call/name/attr/comprehension)."""
    if isinstance(node, ast.Constant):
        return True
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_is_inert_literal(e) for e in node.elts)
    if isinstance(node, ast.Dict):
        return all(k is not None and _is_inert_literal(k) for k in node.keys) and \
            all(_is_inert_literal(v) for v in node.values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        return _is_inert_literal(node.operand)  # e.g. -1
    return False  # Call / Name / Attribute / comprehension / etc. -> NOT inert


def _is_metric_def_assign(stmt: ast.AST) -> bool:
    """True iff stmt is `NAME = <MetricCtor>(<all-literal args>)` and nothing else."""
    if not isinstance(stmt, ast.Assign):
        return False
    val = stmt.value
    if not isinstance(val, ast.Call):
        return False
    func = val.func
    name = (func.id if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute) else None)
    if name not in METRIC_CTORS:
        return False
    if not all(_is_inert_literal(a) for a in val.args):
        return False  # a non-literal positional arg (e.g. os.system('x')) -> reject
    if any(getattr(a, "value", None) is not None for a in val.args
           if isinstance(a, ast.Starred)):
        return False  # *args splat -> reject
    if not all(_is_inert_literal(k.value) for k in val.keywords):
        return False  # a non-literal kwarg value (e.g. registry=Foo()) -> reject
    return True


def _is_additive_metrics_counter(change: dict,
                                 metrics_paths: Sequence[str] = METRICS_PATHS) -> bool:
    """A change qualifies ONLY as a pure-additive new metric DEFINITION (AST-verified).

    Fail-closed: zero removed lines; the added lines must parse as a module whose
    body is EXCLUSIVELY ``NAME = Counter|Gauge|Histogram|Summary(<all-literal
    args>)`` assignments. ANYTHING else — a standalone statement, metric usage
    (.inc/.labels), a nested CALL inside the constructor args
    (``Counter(os.system('x'),'d')`` executes at import), an unparseable/indented
    hunk, or no metric def — returns False. AST is used instead of a regex because
    a regex cannot prove the constructor args are inert (rco-1/rco-2/lead/tools
    #1384: three regex/heuristic fail-opens of the admit-arbitrary-code class).
    """
    if not _on_metrics_allowlist(change.get("path", ""), metrics_paths):
        return False  # default-DENY: only files on the positive METRICS_PATHS qualify
    if change.get("removed"):
        return False  # any removal/edit of an existing line -> not purely additive
    added = change.get("added") or []
    src = "\n".join(added)
    if not src.strip():
        return False
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return False  # unparseable / indented (mid-function) hunk -> fail-closed
    if not tree.body:
        return False
    saw_metric = False
    for stmt in tree.body:
        if _is_metric_def_assign(stmt):
            saw_metric = True
            continue
        return False  # any non-(metric-def) statement -> not a pure additive metric
    return saw_metric


def _scan_tokens(changes: Sequence[dict], tokens: Sequence[str]) -> str | None:
    for ch in changes:
        for line in (ch.get("added") or []) + (ch.get("removed") or []):
            low = line.lower()
            for tok in tokens:
                if tok in low:
                    return tok
    return None


# --- dangerous-callable scan (AST primary + substring fallback) ---------------

def _dotted_from_node(node: ast.AST, aliases: dict) -> str | None:
    """Resolve an Attribute/Name chain to a dotted name, applying import aliases."""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(aliases.get(cur.id, cur.id))
    else:
        return None  # base is not a plain name (subscript/call/...) -> unresolved
    return ".".join(reversed(parts))


def _build_import_aliases(tree: ast.AST) -> dict:
    """Map locally-bound names to their real module/dotted target (import aliases)."""
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                aliases[a.asname or a.name.split(".")[0]] = a.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                aliases[a.asname or a.name] = f"{node.module}.{a.name}"
    return aliases


def _is_dangerous_dotted(dotted: str | None) -> bool:
    if not dotted:
        return False
    if dotted in DANGEROUS_DOTTED:
        return True
    if dotted.startswith(DANGEROUS_DOTTED_PREFIXES):
        return True
    # tail match (catches alias forms like o.system / sp.run-tail even unresolved)
    return "." in dotted and dotted.rsplit(".", 1)[-1] in DANGEROUS_TAILS


def _ast_dangerous(tree: ast.AST) -> str | None:
    aliases = _build_import_aliases(tree)
    for node in ast.walk(tree):
        # ANY bare-Name reference to a dynamic-resolution escape-hatch builtin ->
        # out-of-class (call or not). This closes the dynamic-dispatch bypasses a
        # dotted scan misses: vars()["os"].system, globals()["os"].system,
        # __builtins__["eval"], getattr(os,"system"), and `f = getattr; f(...)`.
        # Flag-anywhere is deliberate (lead 2026-06-24): reference == intent to
        # resolve dynamically; the operator accepted the false-positive cost.
        if isinstance(node, ast.Name) and node.id in ESCAPE_HATCH_NAMES:
            return f"escape-hatch builtin '{node.id}'"
        if isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module.split(".")[0]
            if mod in DANGEROUS_FROM_MODULES:
                return f"from {node.module} import ..."
            if mod == "os":
                for a in node.names:
                    if a.name in DANGEROUS_TAILS:
                        return f"from os import {a.name}"
        # reflection / gadget-traversal dunder reached as an ATTRIBUTE
        # (os.__dict__["system"], func.__globals__[...], os.__getattribute__("x"),
        # ().__class__.__bases__[0].__subclasses__()).
        if isinstance(node, ast.Attribute) and node.attr in DANGEROUS_DUNDER_ATTRS:
            return f"dunder attribute '{node.attr}'"
        # ...or as a constant-string SUBSCRIPT key (d["__globals__"], ns["__class__"]).
        if isinstance(node, ast.Subscript):
            key = node.slice
            if (isinstance(key, ast.Constant) and isinstance(key.value, str)
                    and key.value in DANGEROUS_DUNDER_ATTRS):
                return f"dunder subscript '{key.value}'"
        # any Attribute reference to a dangerous dotted path (covers calls AND
        # reassignment like `f = os.system`)
        if isinstance(node, ast.Attribute):
            d = _dotted_from_node(node, aliases)
            if _is_dangerous_dotted(d):
                return d
            # escape-hatch / dangerous tail reached via the builtins module by
            # DOTTED access (builtins.getattr, builtins.eval) bypasses the
            # bare-Name escape-hatch flag.
            if d and "." in d:
                head, _, tail = d.partition(".")
                if head in DANGEROUS_BUILTINS_MODULES and (
                    tail in ESCAPE_HATCH_NAMES or tail in DANGEROUS_TAILS
                    or tail in DANGEROUS_BARE_NAMES
                ):
                    return d
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fid = node.func.id
            if fid in DANGEROUS_BARE_NAMES:
                return fid
            resolved = aliases.get(fid)
            if resolved and _is_dangerous_dotted(resolved):
                return resolved  # e.g. from os import system; system()
            if fid in ("getattr", "setattr") and len(node.args) >= 2:
                attr = node.args[1]
                if not isinstance(attr, ast.Constant):
                    return f"dynamic {fid}()"  # non-literal attr -> conservative flag
                # LITERAL attr: resolve getattr(os, "system") -> os.system and apply
                # the dangerous-dotted check (rco-1 getattr-literal evasion #1384).
                if isinstance(attr.value, str) and isinstance(
                    node.args[0], (ast.Name, ast.Attribute)
                ):
                    base = _dotted_from_node(node.args[0], aliases)
                    if base and _is_dangerous_dotted(f"{base}.{attr.value}"):
                        return f"{fid}({base}, '{attr.value}')"
    return None


def _scan_dangerous(changes: Sequence[dict]) -> str | None:
    """A dangerous callable on any changed line/path -> its name, else None.

    AST per parseable .py hunk (resolves aliases, resists string-literal FPs +
    getattr evasion); substring fallback for non-Python / unparseable hunks.
    """
    for ch in changes:
        added = ch.get("added") or []
        src = "\n".join(added)
        if not src.strip():
            continue
        try:
            tree = ast.parse(src)
        except (SyntaxError, ValueError):
            hit = _scan_tokens([ch], DANGEROUS_CALLABLES)  # unparseable -> substring
            if hit:
                return hit
            continue
        d = _ast_dangerous(tree)
        if d:
            return d
    return None


def classify_change(changes: Sequence[dict], *, charter=None, diff_text: str = "",
                    require_charter: bool = True,
                    metrics_paths: Sequence[str] = METRICS_PATHS) -> dict:
    """Pure classifier. ``changes``: [{path, added[], removed[]}]. Fail-closed.

    Returns {in_class: bool, decision: 'auto_sign'|'operator_sign', reason}.
    """
    def out_of_class(reason: str) -> dict:
        return {"in_class": False, "decision": "operator_sign", "reason": reason}

    if not changes:
        return out_of_class("empty change set (ambiguous) -> operator sign")

    paths = [_norm(c.get("path", "")) for c in changes]
    if any(not p for p in paths):
        return out_of_class("missing path in change set -> operator sign")

    # The authoritative charter is REQUIRED for predicate F — the explicit F-list
    # + A alone cannot catch arbitrary code under an allowlisted path, so a
    # MISSING charter FAILS CLOSED rather than falling back to the narrower
    # hardcoded list (rco-1 #1384 charter=None fail-open fix).
    if require_charter and charter is None:
        return out_of_class("charter unavailable -> operator sign (F requires the charter)")

    # F via the authoritative charter (denylist / diff-content), when supplied.
    if charter is not None:
        try:
            from waggledance.core import idle_consensus_charter as _c
            if not getattr(_c.evaluate_paths(charter, paths), "allowed", False):
                return out_of_class("F: charter denylists a changed path")
            if diff_text and not getattr(
                _c.evaluate_diff_content(charter, diff_text), "allowed", False
            ):
                return out_of_class("F: charter flags diff content")
        except Exception as exc:  # charter unusable -> fail-closed
            return out_of_class(f"charter check failed ({exc}) -> operator sign")

    # F + A per file.
    for ch in changes:
        path = _norm(ch.get("path", ""))
        excl = _is_f_excluded(path)
        if excl:
            return out_of_class(f"{excl}: {path}")
        if _in_safe_roots(path) or _in_product_gate_extension(path):
            continue
        if _is_additive_metrics_counter(ch, metrics_paths):
            continue
        return out_of_class(f"A: path not in proven-safe class: {path}")

    # C/D/E — scan risky tokens across all changed lines (fail-closed).
    hit = _scan_tokens(changes, AUTHORITY_FLAGS)
    if hit:
        return out_of_class(f"C/D: authority/claim flag touched ('{hit}')")
    hit = _scan_tokens(changes, CONTROL_PLANE_TOKENS)
    if hit:
        return out_of_class(f"E: control-plane/runtime token touched ('{hit}')")
    hit = _scan_dangerous(changes)
    if hit:
        return out_of_class(f"dangerous callable touched ('{hit}') -> operator sign")

    return {
        "in_class": True,
        "decision": "auto_sign",
        "reason": "all predicates A-G hold (proven-safe class)",
    }


# --- git layer ---------------------------------------------------------------

def _run_git(args: list[str], cwd: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                           encoding="utf-8", errors="replace")
        return p.returncode, p.stdout or ""
    except OSError:
        return 1, ""


def gather_changes(base: str, repo_root: str) -> tuple[list[dict], str]:
    """Build the change-list + raw diff from git diff BASE...HEAD. ([], '') on error."""
    code, names = _run_git(["diff", "--name-only", f"{base}...HEAD"], repo_root)
    if code != 0:
        return [], ""
    files = [ln.strip() for ln in names.splitlines() if ln.strip()]
    changes: list[dict] = []
    diff_all = []
    for f in files:
        c2, d = _run_git(["diff", f"{base}...HEAD", "--", f], repo_root)
        diff_all.append(d)
        added, removed = [], []
        for ln in d.splitlines():
            if ln.startswith("+") and not ln.startswith("+++"):
                added.append(ln[1:])
            elif ln.startswith("-") and not ln.startswith("---"):
                removed.append(ln[1:])
        changes.append({"path": f, "added": added, "removed": removed})
    return changes, "\n".join(diff_all)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--changed-from-git", metavar="BASE", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = str(Path(args.repo_root))
    changes, diff_text = gather_changes(args.changed_from_git, repo_root)
    charter = None
    try:
        sys.path.insert(0, repo_root)
        from waggledance.core import idle_consensus_charter as _c
        charter = _c.load_charter()
    except Exception:
        charter = None  # classify_change fails closed when charter is needed
    result = classify_change(changes, charter=charter, diff_text=diff_text)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"{result['decision'].upper()}: {result['reason']}")
    return 0 if result["in_class"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
