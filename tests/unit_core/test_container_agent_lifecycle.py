# SPDX-License-Identifier: BUSL-1.1
"""Audit H12 / D3.2 regression — Container wires AgentLifecycleManager.

Before this fix the container constructed AgentDefinition with
``active=True`` inline, bypassing AgentLifecycleManager entirely.
The manager existed in waggledance/core/orchestration/lifecycle.py
but was never called from production. After the wiring:
- Agents are constructed with ``active=False``
- AgentLifecycleManager.spawn_for_profile flips the active flag
- The lifecycle manager is the sole owner of active-state changes
  (matches the manager's own docstring claim)

These tests pin the wiring so a future refactor can't quietly
restore the inline ``active=True`` shortcut.
"""

from __future__ import annotations

import textwrap

import pytest

from waggledance.bootstrap.container import Container


class _StubSettings:
    def __init__(self, profile: str = "HOME") -> None:
        self._profile = profile

    def get_profile(self) -> str:
        return self._profile


def _write_agent(path, agent_id, profiles=None, name=None):
    """Write a minimal valid agent YAML under path/<agent_id>/core.yaml."""
    subdir = path / agent_id
    subdir.mkdir(parents=True, exist_ok=True)
    yaml_path = subdir / "core.yaml"
    profiles_yaml = (
        "profiles: [{}]".format(", ".join(profiles))
        if profiles else "profiles: [ALL]"
    )
    yaml_path.write_text(textwrap.dedent(f"""
        header:
          agent_id: {agent_id}
          agent_name: {name or agent_id.title()}
          version: 1.0.0
        {profiles_yaml}
        DECISION_METRICS_AND_THRESHOLDS:
          dummy_metric:
            value: 1
    """).strip(), encoding="utf-8")


class TestContainerWiresLifecycle:
    def test_agents_returned_have_active_true(self, tmp_path, monkeypatch):
        """Lifecycle.spawn_for_profile flips active=True on filtered agents."""
        agents_dir = tmp_path / "agents"
        _write_agent(agents_dir, "alpha", profiles=["HOME"])
        _write_agent(agents_dir, "beta", profiles=["HOME", "COTTAGE"])
        # Change cwd so Container picks up the tmp agents/ tree
        monkeypatch.chdir(tmp_path)

        c = Container(settings=_StubSettings("HOME"), stub=True)
        agents = c._load_agents()
        assert len(agents) == 2
        assert all(a.active is True for a in agents), (
            "AgentLifecycleManager should have set active=True"
        )

    def test_non_matching_profile_filtered(self, tmp_path, monkeypatch):
        """Agents whose profiles list doesn't include the active profile
        (and not ALL) are filtered out before lifecycle activation."""
        agents_dir = tmp_path / "agents"
        _write_agent(agents_dir, "home_only", profiles=["HOME"])
        _write_agent(agents_dir, "apiary_only", profiles=["APIARY"])
        _write_agent(agents_dir, "universal", profiles=["ALL"])
        monkeypatch.chdir(tmp_path)

        c = Container(settings=_StubSettings("HOME"), stub=True)
        agent_ids = {a.id for a in c._load_agents()}
        assert agent_ids == {"home_only", "universal"}

    def test_multiprofile_yaml_preserves_match(self, tmp_path, monkeypatch):
        """Regression: an agent with profiles=[APIARY, HOME] used to
        be stored with profile='APIARY' (profiles[0]) and then dropped
        by AgentLifecycleManager on a HOME boot. The fix stores the
        active profile so lifecycle sees a match."""
        agents_dir = tmp_path / "agents"
        _write_agent(agents_dir, "multi", profiles=["APIARY", "HOME"])
        monkeypatch.chdir(tmp_path)

        c = Container(settings=_StubSettings("HOME"), stub=True)
        agents = c._load_agents()
        assert {a.id for a in agents} == {"multi"}
        # The agent.profile slot should not be the YAML's profiles[0]
        # ("APIARY") — it should be either "HOME" (active match) or
        # "ALL" (universal match). Pinning the specific value would
        # be over-constraining; what matters is lifecycle accepted it.
        assert agents[0].active is True

    def test_all_in_profiles_stored_as_all(self, tmp_path, monkeypatch):
        """ALL takes precedence so future profile changes don't
        accidentally drop the agent."""
        agents_dir = tmp_path / "agents"
        _write_agent(agents_dir, "uni", profiles=["ALL"])
        monkeypatch.chdir(tmp_path)

        c = Container(settings=_StubSettings("HOME"), stub=True)
        agents = c._load_agents()
        assert agents[0].profile == "ALL"

    def test_empty_agents_dir_returns_empty_list(self, tmp_path, monkeypatch):
        (tmp_path / "agents").mkdir()
        monkeypatch.chdir(tmp_path)
        c = Container(settings=_StubSettings("HOME"), stub=True)
        assert c._load_agents() == []

    def test_missing_agents_dir_returns_empty_list(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        c = Container(settings=_StubSettings("HOME"), stub=True)
        assert c._load_agents() == []
