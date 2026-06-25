# SPDX-License-Identifier: BUSL-1.1
"""P4c adversarial corpus — P1 proven-safe auto-sign slice (rco-2). CASES only.

Seeded from REAL verdicts of the MERGED #1384 checker
(``tools/check_proven_safe_autosign_class.classify_change``), captured 2026-06-24/25.

THREE case kinds:
  - ``negative_enforced``  : the vector MUST route to operator_sign (a checker change
                             that drops one is a LOOSENING the validator fails CI on).
  - ``documented_residual``: known auto_sign (NOT caught); tracked, never asserted-caught.
  - ``positive_autosign``  : inert/legit content that MUST auto_sign — proves the
                             checker does NOT over-block (incl. borderline: a dangerous
                             word in a COMMENT/STRING must still auto_sign).

TRUST BOUNDARY (rco-1 #1392): this file is allowlist-clean (extendable). The
ANTI-WEAKENING ANCHORS (the frozen id MANIFEST, the per-id EXPECTED_KIND/FAMILY, and
FAMILY_FLOOR) live in the DENYLISTED ``validate_p4c_corpus.py`` so a CASES edit cannot
silently drop a case, flip a kind, or empty a floor. Adding a genuinely-new case
requires editing the denylisted anchors too (an operator-signed change) — correct,
since changing the PROTECTED set is a gate-policy act.

Each case: {id, kind, family, body (added lines), path (defaults tests/_p4c_probe.py)}.
"""
from __future__ import annotations

ENFORCED = "negative_enforced"
RESIDUAL = "documented_residual"
POSITIVE = "positive_autosign"

_T = "tests/_p4c_probe.py"

CASES = [
    # --- NEGATIVE-ENFORCED: must -> operator_sign --------------------------------
    {"id": "p1_direct_eval",         "kind": ENFORCED, "family": "direct",         "path": _T, "body": ["eval('1+1')"]},
    {"id": "p1_direct_exec",         "kind": ENFORCED, "family": "direct",         "path": _T, "body": ["exec('x=1')"]},
    {"id": "p1_direct_compile",      "kind": ENFORCED, "family": "direct",         "path": _T, "body": ["compile('1','<s>','eval')"]},
    {"id": "p1_direct_dunder_import","kind": ENFORCED, "family": "direct",         "path": _T, "body": ["__import__('os')"]},
    {"id": "p1_direct_os_system",    "kind": ENFORCED, "family": "direct",         "path": _T, "body": ["import os", "os.system('x')"]},
    {"id": "p1_direct_subprocess",   "kind": ENFORCED, "family": "direct",         "path": _T, "body": ["import subprocess", "subprocess.Popen(['x'])"]},
    {"id": "p1_getattr_literal",     "kind": ENFORCED, "family": "getattr_literal","path": _T, "body": ["import os", "getattr(os, 'system')('x')"]},
    {"id": "p1_reassign_eval",       "kind": ENFORCED, "family": "escape_hatch",   "path": _T, "body": ["e = eval", "e('1+1')"]},
    {"id": "p1_vars_subscript",      "kind": ENFORCED, "family": "escape_hatch",   "path": _T, "body": ["vars()['eval']('x')"]},
    {"id": "p1_globals_subscript",   "kind": ENFORCED, "family": "escape_hatch",   "path": _T, "body": ["globals()['eval']('x')"]},
    {"id": "p1_list_index_eval",     "kind": ENFORCED, "family": "escape_hatch",   "path": _T, "body": ["[eval][0]('x')"]},
    {"id": "p1_breakpoint",          "kind": ENFORCED, "family": "escape_hatch",   "path": _T, "body": ["breakpoint()"]},
    {"id": "p1_dunder_builtins_sub", "kind": ENFORCED, "family": "escape_hatch",   "path": _T, "body": ["__builtins__['eval']('x')"]},
    {"id": "p1_dotted_builtins_eval","kind": ENFORCED, "family": "dotted_builtin", "path": _T, "body": ["import builtins", "builtins.eval('x')"]},
    {"id": "p1_getattribute",        "kind": ENFORCED, "family": "dunder_attr",    "path": _T, "body": ["o.__getattribute__('system')"]},
    {"id": "p1_type_dict",           "kind": ENFORCED, "family": "dunder_attr",    "path": _T, "body": ["type(o).__dict__['x']"]},
    {"id": "p1_subclasses",          "kind": ENFORCED, "family": "dunder_attr",    "path": _T, "body": ["().__class__.__subclasses__()"]},
    {"id": "p1_operator_attrgetter", "kind": ENFORCED, "family": "operator_dispatch","path": _T, "body": ["import operator", "operator.attrgetter('system')(os)"]},
    {"id": "p1_operator_methodcaller","kind": ENFORCED,"family": "operator_dispatch","path": _T, "body": ["import operator", "operator.methodcaller('system','x')(os)"]},
    {"id": "p1_importlib",           "kind": ENFORCED, "family": "dynamic_import", "path": _T, "body": ["import importlib", "importlib.import_module('os').system('x')"]},
    {"id": "p1_pickle_loads",        "kind": ENFORCED, "family": "deserialize",    "path": _T, "body": ["import pickle", "pickle.loads(b'x')"]},
    {"id": "p1_ctypes_cdll",         "kind": ENFORCED, "family": "native",         "path": _T, "body": ["import ctypes", "ctypes.CDLL('x')"]},
    # --- DOCUMENTED-RESIDUAL: known auto_sign (not asserted-caught) ---------------
    {"id": "p1_residual_file_write", "kind": RESIDUAL, "family": "residual",       "path": _T, "body": ["open('/tmp/x','w').write('p')"]},
    {"id": "p1_residual_socket",     "kind": RESIDUAL, "family": "residual",       "path": _T, "body": ["import socket", "socket.socket().connect(('h', 1))"]},
    # --- POSITIVE-AUTOSIGN: legit/inert content that MUST auto_sign ---------------
    {"id": "p1_pos_inert_simple",        "kind": POSITIVE, "family": "positive", "path": _T, "body": ["x = 1 + 2", "assert x == 3"]},
    {"id": "p1_pos_metric_counter",      "kind": POSITIVE, "family": "positive", "path": _T, "body": ["from prometheus_client import Counter", "C = Counter('reqs','desc')"]},
    {"id": "p1_pos_labelnames_positional","kind": POSITIVE,"family": "positive", "path": _T, "body": ["from prometheus_client import Counter", "C = Counter('reqs','desc',['route','code'])"]},
    {"id": "p1_pos_labelnames_kwarg",    "kind": POSITIVE, "family": "positive", "path": _T, "body": ["from prometheus_client import Counter", "C = Counter('reqs','desc',labelnames=['route'])"]},
    {"id": "p1_pos_negative_buckets",    "kind": POSITIVE, "family": "positive", "path": _T, "body": ["from prometheus_client import Histogram", "H = Histogram('lat','d',buckets=(-1.0,0.0,1.0))"]},
    # BORDERLINE positives: a dangerous WORD in a comment/string must STILL auto_sign
    # (proves the AST scan ignores non-code contexts -- the anti-false-positive guard).
    {"id": "p1_pos_dangerword_comment",  "kind": POSITIVE, "family": "positive_borderline", "path": _T, "body": ["# do NOT call eval() or os.system() here", "x = 1"]},
    {"id": "p1_pos_dangerword_string",   "kind": POSITIVE, "family": "positive_borderline", "path": _T, "body": ["msg = 'never use eval or subprocess.Popen or os.system'", "y = len(msg)"]},
    # path-positive: an inert docs/benchmarks file stays auto_sign (SAFE_ROOTS).
    {"id": "p1_pos_docs_benchmarks",     "kind": POSITIVE, "family": "positive", "path": "docs/benchmarks/run_notes.md", "body": ["# benchmark notes: throughput numbers", "data = [1, 2, 3]"]},
]
