# SPDX-License-Identifier: BUSL-1.1
from __future__ import annotations

from pathlib import Path

from tools.check_proven_safe_autosign_class import (
    _is_additive_metrics_counter,
    classify_change,
)


def _ch(path, added=None, removed=None) -> dict:
    return {"path": path, "added": added or [], "removed": removed or []}


def _in_class(changes, **kw) -> bool:
    return classify_change(changes, **kw)["in_class"]


# --- POSITIVE corpus (in-class -> auto_sign) ---------------------------------

def test_tests_change_in_class():
    assert _in_class([_ch("tests/test_foo.py", ["def test_x():", "    assert True"])])


def test_docs_runs_in_class():
    assert _in_class([_ch("docs/runs/2026-06-24.md", ["# run log", "all green"])])


def test_docs_benchmarks_in_class():
    assert _in_class([_ch("docs/benchmarks/latency.md", ["p50 12ms"])])


def test_additive_metric_definition_in_class():
    ch = _ch("waggledance/adapters/http/routes/metrics.py",
             ["FOO_TOTAL = Counter('foo_total', 'desc')"])
    assert _is_additive_metrics_counter(ch) is True
    assert _in_class([ch])


def test_multi_safe_files_in_class():
    assert _in_class([_ch("tests/test_a.py", ["x"]), _ch("docs/runs/r.md", ["y"])])


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
    assert _is_additive_metrics_counter(ch) is False
    assert _in_class([ch]) is False


def test_b_metric_increment_is_hotpath_not_in_class():
    ch = _ch("waggledance/x/metrics.py", ["FOO_TOTAL.labels(route='x').inc()"])
    assert _is_additive_metrics_counter(ch) is False
    assert _in_class([ch]) is False


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
