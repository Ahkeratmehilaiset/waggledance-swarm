"""Static pins for the five-lane Mind bootstrap trace (no runtime, no deployment, no live data).

The deployed startup prompt tells a lane to call Invoke-WdQ4LaneRecall.ps1, and to add -Live -WithCoverage
only when the live selector is provisioned. These tests pin what the minimal bootstrap patch relies on:

* without -Live, a staged card descriptor (binding with card_policy_sha256) takes the staged branch of the
  lane caller configuration, which requires a v3 public plan and reads the pinned card pointer;
* -Live is gated only by WD_Q4_LIVE_MODE, not by the staged flag, and the live selector recomputes the
  candidate binding with the non-staged build_lane_binding. So today -Live cannot serve a staged card
  descriptor; this is a current gap, and this test must change when a staged live refresh lands;
* the shared-descriptor route (WD_Q4_DESCRIPTOR_CURRENT.json at deploy) and the per-lane fleet q4_read
  route are mutually exclusive at startup, so the fleet flag must stay off for the shared route.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CALLER = ROOT / "tools" / "wd_q4_lane_caller_cli.py"
LIVE = ROOT / "tools" / "wd_q4_live_snapshot_publish.py"
AGENT = ROOT / "ops" / "windows" / "reboot" / "start-wd-agent.ps1"


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def _function(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(name)


def _calls(node):
    return [ast.unparse(item.func) for item in ast.walk(node) if isinstance(item, ast.Call)]


def _enclosing_ifs(root, target):
    """The tests of every If whose body or orelse contains target."""
    found = []
    for node in ast.walk(root):
        if isinstance(node, ast.If) and any(item is target for item in ast.walk(node)) and node is not target:
            found.append(ast.unparse(node.test))
    return found


def test_without_live_a_staged_descriptor_reads_the_pinned_card_pointer():
    configuration = _function(_tree(CALLER), "_require_current_snapshot")
    branch = [node for node in ast.walk(configuration)
              if isinstance(node, ast.If) and ast.unparse(node.test) == "staged"]
    assert len(branch) == 1
    body = ast.unparse(ast.Module(body=branch[0].body, type_ignores=[]))
    assert "public['pointer_path']" in body and "binding['pointer_sha256']" in body
    text = ast.unparse(configuration)
    assert "staged = 'card_policy_sha256' in binding" in text
    assert "_check((public.get('schema') == 'wd.q4-public-wrapper-plan.v3') == staged)" in text


def test_live_mode_is_not_staged_aware_today():
    main = _function(_tree(CALLER), "main")
    selects = [node for node in ast.walk(main) if isinstance(node, ast.Call)
               and ast.unparse(node.func) == "live.select"]
    assert len(selects) == 1
    assert '_require_current_snapshot' in _calls(main)
    assert _enclosing_ifs(main, selects[0]) == ["live_mode == '1'"]
    pair = _function(_tree(LIVE), "_checked_pair")
    assert "binding_producer.build_lane_binding" in _calls(pair)
    assert "binding_producer.build_staged_lane_binding" not in _calls(_tree(LIVE))
    assert "q4-live-current.json" in LIVE.read_text(encoding="utf-8")


def test_the_shared_descriptor_and_fleet_q4_read_routes_are_exclusive():
    text = AGENT.read_text(encoding="utf-8")
    assert "if ($null -ne $q4Values -and $null -ne $q4SharedValues) { throw 'q4_config_conflict' }" in text
    assert "When the live selector is provisioned for this bundle" in text
    assert "-Live -WithCoverage" in text
