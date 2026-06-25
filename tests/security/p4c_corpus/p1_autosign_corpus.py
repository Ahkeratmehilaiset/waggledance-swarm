# SPDX-License-Identifier: BUSL-1.1
"""P4c adversarial corpus — P1 proven-safe auto-sign slice (rco-2).

Seeded from the REAL verdicts of the MERGED #1384 checker
(``tools/check_proven_safe_autosign_class.classify_change``), captured 2026-06-24
against ``origin/main`` 0bc71f2a — NOT spec-derived (the propagation-risk fix:
fixtures come from current real verdicts, never from a checker's own assumptions).

Each NEGATIVE-ENFORCED case asserts the checker routes the vector to
``operator_sign`` (so a future checker change that DROPS one is a LOOSENING the
validator fails CI on). DOCUMENTED-RESIDUAL cases record the known, operator-accepted
fail-open tail (option-B "keep tests/ best-effort, eyes-open"); they are tracked,
NEVER asserted-caught — moving one residual->enforced is an improvement, the reverse
is operator-gated. The full gate (build+RCO+CI) still reviews these; auto-sign only
removes the per-PR operator SIGNATURE, not the review.
"""
from __future__ import annotations

ENFORCED = "negative_enforced"     # checker MUST route -> operator_sign
RESIDUAL = "documented_residual"   # known auto_sign (not caught); improvement allowed

# Each case exercises ONE vector as the added lines of a tests/ file (in SAFE_ROOTS,
# so the dangerous-callable scan is the deciding predicate).
CASES = [
    # --- direct dangerous builtins / callables -------------------------------
    {"id": "p1_direct_eval",        "kind": ENFORCED, "family": "direct",        "body": ["eval('1+1')"]},
    {"id": "p1_direct_exec",        "kind": ENFORCED, "family": "direct",        "body": ["exec('x=1')"]},
    {"id": "p1_direct_compile",     "kind": ENFORCED, "family": "direct",        "body": ["compile('1','<s>','eval')"]},
    {"id": "p1_direct_dunder_import","kind": ENFORCED,"family": "direct",        "body": ["__import__('os')"]},
    {"id": "p1_direct_os_system",   "kind": ENFORCED, "family": "direct",        "body": ["import os", "os.system('x')"]},
    {"id": "p1_direct_subprocess",  "kind": ENFORCED, "family": "direct",        "body": ["import subprocess", "subprocess.Popen(['x'])"]},
    # --- getattr-literal indirection -----------------------------------------
    {"id": "p1_getattr_literal",    "kind": ENFORCED, "family": "getattr_literal","body": ["import os", "getattr(os, 'system')('x')"]},
    # --- escape-hatch / dynamic-dispatch family (bar requires >= 2) -----------
    {"id": "p1_reassign_eval",      "kind": ENFORCED, "family": "escape_hatch",  "body": ["e = eval", "e('1+1')"]},
    {"id": "p1_vars_subscript",     "kind": ENFORCED, "family": "escape_hatch",  "body": ["vars()['eval']('x')"]},
    {"id": "p1_globals_subscript",  "kind": ENFORCED, "family": "escape_hatch",  "body": ["globals()['eval']('x')"]},
    {"id": "p1_list_index_eval",    "kind": ENFORCED, "family": "escape_hatch",  "body": ["[eval][0]('x')"]},
    {"id": "p1_breakpoint",         "kind": ENFORCED, "family": "escape_hatch",  "body": ["breakpoint()"]},
    {"id": "p1_dunder_builtins_sub","kind": ENFORCED, "family": "escape_hatch",  "body": ["__builtins__['eval']('x')"]},
    # --- dotted builtins (attribute form) ------------------------------------
    {"id": "p1_dotted_builtins_eval","kind": ENFORCED,"family": "dotted_builtin","body": ["import builtins", "builtins.eval('x')"]},
    # --- dunder-attribute reflection -----------------------------------------
    {"id": "p1_getattribute",       "kind": ENFORCED, "family": "dunder_attr",   "body": ["o.__getattribute__('system')"]},
    {"id": "p1_type_dict",          "kind": ENFORCED, "family": "dunder_attr",   "body": ["type(o).__dict__['x']"]},
    {"id": "p1_subclasses",         "kind": ENFORCED, "family": "dunder_attr",   "body": ["().__class__.__subclasses__()"]},
    # --- operator-module dispatch --------------------------------------------
    {"id": "p1_operator_attrgetter","kind": ENFORCED, "family": "operator_dispatch","body": ["import operator", "operator.attrgetter('system')(os)"]},
    {"id": "p1_operator_methodcaller","kind": ENFORCED,"family": "operator_dispatch","body": ["import operator", "operator.methodcaller('system','x')(os)"]},
    # --- dynamic import ------------------------------------------------------
    {"id": "p1_importlib",          "kind": ENFORCED, "family": "dynamic_import", "body": ["import importlib", "importlib.import_module('os').system('x')"]},
    # --- deserialize / native ------------------------------------------------
    {"id": "p1_pickle_loads",       "kind": ENFORCED, "family": "deserialize",   "body": ["import pickle", "pickle.loads(b'x')"]},
    {"id": "p1_ctypes_cdll",        "kind": ENFORCED, "family": "native",        "body": ["import ctypes", "ctypes.CDLL('x')"]},
    # --- DOCUMENTED RESIDUAL (known fail-open; NOT asserted-caught) -----------
    {"id": "p1_residual_file_write","kind": RESIDUAL, "family": "residual",      "body": ["open('/tmp/x','w').write('p')"]},
    {"id": "p1_residual_socket",    "kind": RESIDUAL, "family": "residual",      "body": ["import socket", "socket.socket().connect(('h', 1))"]},
]

# FROZEN literal manifest of expected case_ids. The validator fails if CASES drops
# or duplicates any id (a silently-dropped case would shrink coverage unnoticed).
# This is a LITERAL (not derived from CASES) on purpose, so deleting a CASE is caught.
MANIFEST = frozenset({
    "p1_direct_eval", "p1_direct_exec", "p1_direct_compile", "p1_direct_dunder_import",
    "p1_direct_os_system", "p1_direct_subprocess", "p1_getattr_literal",
    "p1_reassign_eval", "p1_vars_subscript", "p1_globals_subscript", "p1_list_index_eval",
    "p1_breakpoint", "p1_dunder_builtins_sub", "p1_dotted_builtins_eval",
    "p1_getattribute", "p1_type_dict", "p1_subclasses",
    "p1_operator_attrgetter", "p1_operator_methodcaller", "p1_importlib",
    "p1_pickle_loads", "p1_ctypes_cdll",
    "p1_residual_file_write", "p1_residual_socket",
})

# Families that must hold >= MIN_FAMILY_FLOOR cases (breadth on the riskiest vectors).
FAMILY_FLOOR = {"escape_hatch": 2}
