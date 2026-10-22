"""Current-policy mirrors must recognize the same three RCO role identities.

Historical signed DriverProfiles intentionally retain their original vocabulary.
Recognition and an assigned bridge UUID do not register a live native session.
"""
import ast
import json
from pathlib import Path
import re
from uuid import UUID

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = frozenset({"claude-rco-1", "claude-rco-2", "claude-rco-3"})
POLICY_MIRRORS = (
    ("tools/check_bridge_changes_requested.py", "_RECOGNIZED_RCOS"),
    ("tools/check_rco_pass_present.py", "DEFAULT_RCO_AGENTS"),
    ("tools/check_promotion_eligible.py", "DEFAULT_RCO_AGENTS"),
    ("tools/check_standing_consensus_sign_class.py", "RECOGNIZED_RCOS"),
    ("tools/bridge_rule12_review_eligibility.py", "RECOGNIZED_RCOS"),
    ("tools/build_bridge_message_template.py", "RECOGNIZED_RCO_AGENTS"),
    ("tools/bridge_v2_event_schema.py", "FLOOR_RCO_AGENTS"),
    ("tools/bridge_mind_workspace.py", "RECOGNIZED_RCOS"),
    ("tools/wd_bridge_mind.py", "_RCO_ACTORS"),
    ("tools/wd_consensus_stall_detector.py", "RCO_AGENTS"),
    ("tools/wd_consensus_stall_detector.py", "RECOGNIZED_HOLD_IDENTITIES"),
    ("tools/p4_adversarial_corpus.py", "AGENT_UUIDS"),
    ("tools/verify_bridge_event_signatures.py", "RECOGNIZED_AGENTS"),
    ("waggledance/core/bridge_workflow.py", "ACTIVE_WORKERS"),
    ("tools/bridge_v2_workflow.py", "ACTIVE_WORKERS"),
)


def test_p4_taxonomy_consumer_receives_the_current_roster(monkeypatch):
    from tools import p4_adversarial_corpus as corpus

    actual_classifier = corpus.classify_bridge_events
    observed = []

    def record_roster(*args, **kwargs):
        observed.append(frozenset(kwargs["recognized_rcos"]))
        return actual_classifier(*args, **kwargs)

    monkeypatch.setattr(corpus, "classify_bridge_events", record_roster)
    corpus.evaluate_case({
        "id": "unit_taxonomy_consumer_roster",
        "family": "cause_b_ordering",
        "driver": "taxonomy",
        "payload": {"events": []},
    })

    assert observed == [EXPECTED]


@pytest.mark.parametrize("path,name", POLICY_MIRRORS)
def test_all_current_python_policy_rco_identity_lists_are_identical(path, name):
    tree = ast.parse((ROOT / path).read_text(encoding="utf-8-sig"))
    assignments = [node for node in tree.body if isinstance(node, (ast.Assign, ast.AnnAssign))]
    matches = [node.value for node in assignments if any(
        isinstance(target, ast.Name) and target.id == name
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
    )]
    assert len(matches) == 1, (path, name)
    value = matches[0]
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id == "frozenset":
        value = value.args[0]
    names = ast.literal_eval(value)
    mixed_rosters = {"RECOGNIZED_AGENTS", "ACTIVE_WORKERS", "RECOGNIZED_HOLD_IDENTITIES", "AGENT_UUIDS"}
    actual = frozenset(item for item in names if item.startswith("claude-rco-")) if name in mixed_rosters else frozenset(names)
    assert actual == EXPECTED


def test_powershell_writer_roster_and_registry_use_the_same_current_rco_identities():
    writer = (ROOT / ".agent-bridge/bin/Write-AgentEvent.ps1").read_text(encoding="utf-8-sig")
    match = re.search(r"^\$rcoReviewAgents = @\(([^)]*)\)", writer, re.MULTILINE)
    assert match is not None
    assert frozenset(re.findall(r"'([^']+)'", match.group(1))) == EXPECTED
    roster = (ROOT / ".agent-bridge/bin/BridgeRoster.ps1").read_text(encoding="utf-8-sig")
    assert frozenset(re.findall(r"claude-rco-\d+", roster)) == EXPECTED
    identities = json.loads((ROOT / "configs/bridge_identity_registry.json").read_text(encoding="utf-8-sig"))["identities"]
    assert frozenset(name for name in identities if name.startswith("claude-rco-")) == EXPECTED
    assert len({UUID(identities[name]) for name in EXPECTED}) == len(EXPECTED)


@pytest.mark.parametrize("path", [
    ".agent-bridge/bin/BridgeTaskResult.ps1",
    ".agent-bridge/bin/Write-AgentEvent.ps1",
    ".agent-bridge/bin/BridgeRoster.ps1",
    "ops/windows/reboot/BridgeCodeContext.ps1",
    "ops/windows/reboot/Deploy-WdRebootBundle.ps1",
    "ops/windows/reboot/Get-WdCapacityStatus.ps1",
    "ops/windows/reboot/Invoke-WdCapacityObserver.ps1",
    "ops/windows/reboot/Install-WdClaudeCapacityHooks.ps1",
    "ops/windows/reboot/Invoke-WdGrok.ps1",
    "ops/windows/reboot/Invoke-WdLaneTurnLoop.ps1",
    "ops/windows/reboot/start-wd-agent.ps1",
    "ops/windows/reboot/start-wd-tools-consumer.ps1",
    "ops/windows/reboot/WdBridgeLimitedBootstrap.ps1",
    "ops/windows/reboot/wd_supervisor.ps1",
    "ops/windows/reboot/Write-WdLaneCurrentState.ps1",
    "ops/windows/reboot/Show-WdBridgeConversation.ps1",
])
def test_current_powershell_literal_admission_lists_cannot_omit_the_third_rco(path):
    text = (ROOT / path).read_text(encoding="utf-8-sig")
    # A named active runtime cohort is deployment inventory, not recognition.
    # Its exact contents have their own assertion below; never turn every
    # recognized identity into a provisioned worker merely to satisfy parity.
    if path in {"ops/windows/reboot/WdBridgeLimitedBootstrap.ps1", "ops/windows/reboot/wd_supervisor.ps1"}:
        text = re.sub(r"\$runtimeWatcherAgents\s*=\s*@\([^)]*\)", "", text)
    # Literal arrays and ValidateSet arguments are admission lists. Arrays of
    # machine-wrapper objects are deployment inventory, not identity policy.
    bodies = re.findall(r"(?:@|ValidateSet)\(([^)]*)\)", text, re.DOTALL)
    rosters = [frozenset(re.findall(r"'(claude-rco-\d+)'", body))
               for body in bodies if "[pscustomobject]" not in body]
    mirrors = [names for names in rosters if names]
    assert mirrors, path
    assert all(names == EXPECTED for names in mirrors), (path, mirrors)


def test_runtime_watcher_inventory_stays_exactly_the_provisioned_four():
    expected = {"codex-tools-1", "claude-rco-1", "claude-rco-2", "fable-5"}
    for name in ("WdBridgeLimitedBootstrap.ps1", "wd_supervisor.ps1"):
        text = (ROOT / "ops/windows/reboot" / name).read_text(encoding="utf-8-sig")
        arrays = re.findall(r"\$runtimeWatcherAgents\s*=\s*@\(([^)]*)\)", text)
        assert len(arrays) == 1, name
        identities = re.findall(r"'([^']+)'", arrays[0])
        assert len(identities) == len(expected) and set(identities) == expected, name
    snapshot = json.loads((ROOT / "ops/windows/reboot/wd_supervisor_loop.json").read_text(encoding="utf-8-sig"))
    assert set(snapshot["watchers"]["agents"]) == expected
    assert len(snapshot["watchers"]["agents"]) == len(expected)
    fleet = json.loads((ROOT / "ops/windows/reboot/wd-fleet.json").read_text(encoding="utf-8-sig"))
    fleet_agents = {lane["agent"] for lane in fleet["lanes"]}
    assert "claude-rco-3" not in fleet_agents
    assert (fleet_agents - {"codex-lead-1"}) | {"codex-tools-1"} == expected
    launcher = (ROOT / "ops/windows/reboot/start-wd-all.ps1").read_text(encoding="utf-8-sig")
    assert "five bridge watchers" not in launcher
    assert "Bootstrapping four bridge watchers and Tools" in launcher


def test_profile_and_component_current_lane_rosters_include_exactly_the_same_rcos():
    profiles = json.loads((ROOT / "configs/lane_profile_catalog.json").read_text(encoding="utf-8-sig"))
    assert frozenset(name for name in profiles["lanes"] if name.startswith("claude-rco-")) == EXPECTED
    components = json.loads((ROOT / "configs/bridge_components.json").read_text(encoding="utf-8-sig"))
    assert frozenset(name for name in components["lanes"] if name.startswith("claude-rco-")) == EXPECTED
    feature = next(row for row in components["features"] if row["id"] == "claude_lane")
    assert frozenset(name for name in feature["required_for_lanes"] if name.startswith("claude-rco-")) == EXPECTED
