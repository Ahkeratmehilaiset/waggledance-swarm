from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

import tools.agent_identity as agent_identity
from tools.agent_identity import (
    AgentIdentityError,
    list_profiles,
    read_profile,
    register_profile,
    validate_profile,
)


NOW = datetime(2026, 5, 18, 11, 0, tzinfo=timezone.utc)
AGENT_UUID = "11111111-2222-3333-4444-555555555555"


def test_register_profile_writes_local_agent_identity(tmp_path: Path) -> None:
    profile = register_profile(
        agent_id="codex",
        kind="codex",
        display_name="Codex",
        capabilities=["work_queue", "idle_protocol", "work_queue"],
        bridge_root=tmp_path,
        now_utc=NOW,
    )

    path = tmp_path / "agents" / "codex.json"
    assert path.exists()
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted == profile
    assert profile["schema_version"] == "agent_profile.v1"
    assert profile["capabilities"] == ["idle_protocol", "work_queue"]
    assert "agent_uuid" not in profile
    assert profile["operator_approved"] is False
    assert profile["write_scope_policy"] == "claim_required"
    assert validate_profile(profile) == []


def test_register_profile_can_bind_agent_uuid(tmp_path: Path) -> None:
    profile = register_profile(
        agent_id="codex",
        kind="codex",
        agent_uuid=AGENT_UUID,
        display_name="Codex",
        bridge_root=tmp_path,
        now_utc=NOW,
    )

    assert profile["agent_uuid"] == AGENT_UUID
    assert validate_profile(profile) == []
    assert read_profile(agent_id="codex", bridge_root=tmp_path)["agent_uuid"] == (
        AGENT_UUID
    )


def test_register_requires_agent_uuid_for_bridge_event_capability(
    tmp_path: Path,
) -> None:
    with pytest.raises(AgentIdentityError) as excinfo:
        register_profile(
            agent_id="codex",
            kind="codex",
            display_name="Codex",
            capabilities=["bridge_event"],
            bridge_root=tmp_path,
            now_utc=NOW,
        )

    assert excinfo.value.report["decision"] == "invalid_profile"
    assert "agent_uuid required for bridge_event capability" in (
        excinfo.value.report["errors"][0]
    )
    assert not (tmp_path / "agents" / "codex.json").exists()


def test_register_accepts_bridge_event_capability_with_agent_uuid(
    tmp_path: Path,
) -> None:
    profile = register_profile(
        agent_id="codex",
        kind="codex",
        agent_uuid=AGENT_UUID,
        display_name="Codex",
        capabilities=["bridge_event"],
        bridge_root=tmp_path,
        now_utc=NOW,
    )

    assert profile["agent_uuid"] == AGENT_UUID
    assert profile["capabilities"] == ["bridge_event"]
    assert validate_profile(profile) == []


def test_register_refuses_invalid_agent_id(tmp_path: Path) -> None:
    with pytest.raises(AgentIdentityError) as excinfo:
        register_profile(
            agent_id="Codex",
            kind="codex",
            display_name="Codex",
            bridge_root=tmp_path,
            now_utc=NOW,
        )
    assert excinfo.value.report["decision"] == "invalid_agent_id"
    assert not (tmp_path / "agents").exists()


def test_register_refuses_invalid_agent_uuid(tmp_path: Path) -> None:
    with pytest.raises(AgentIdentityError) as excinfo:
        register_profile(
            agent_id="codex",
            kind="codex",
            agent_uuid="not-a-uuid",
            display_name="Codex",
            bridge_root=tmp_path,
            now_utc=NOW,
        )
    assert excinfo.value.report["decision"] == "invalid_agent_uuid"
    assert not (tmp_path / "agents").exists()


def test_register_refuses_invalid_capability(tmp_path: Path) -> None:
    with pytest.raises(AgentIdentityError) as excinfo:
        register_profile(
            agent_id="codex",
            kind="codex",
            display_name="Codex",
            capabilities=["Bad Capability"],
            bridge_root=tmp_path,
            now_utc=NOW,
        )
    assert excinfo.value.report["decision"] == "invalid_capability"


def test_register_refuses_overwrite_without_force(tmp_path: Path) -> None:
    register_profile(
        agent_id="codex",
        kind="codex",
        display_name="Codex",
        bridge_root=tmp_path,
        now_utc=NOW,
    )

    with pytest.raises(AgentIdentityError) as excinfo:
        register_profile(
            agent_id="codex",
            kind="codex",
            display_name="Codex 2",
            bridge_root=tmp_path,
            now_utc=NOW,
        )
    assert excinfo.value.report["decision"] == "profile_exists"


def test_force_update_preserves_created_at(tmp_path: Path) -> None:
    first = register_profile(
        agent_id="codex",
        kind="codex",
        display_name="Codex",
        bridge_root=tmp_path,
        now_utc=NOW,
    )
    updated = register_profile(
        agent_id="codex",
        kind="codex",
        display_name="Codex Prime",
        capabilities=["work_queue"],
        bridge_root=tmp_path,
        force=True,
        now_utc=datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc),
    )
    assert updated["created_at_utc"] == first["created_at_utc"]
    assert updated["updated_at_utc"] == "2026-05-18T12:00:00Z"
    assert read_profile(agent_id="codex", bridge_root=tmp_path)["display_name"] == (
        "Codex Prime"
    )


def test_list_profiles_sorted(tmp_path: Path) -> None:
    register_profile(
        agent_id="godel",
        kind="worker",
        display_name="Godel",
        bridge_root=tmp_path,
        now_utc=NOW,
    )
    register_profile(
        agent_id="bohr",
        kind="worker",
        display_name="Bohr",
        bridge_root=tmp_path,
        now_utc=NOW,
    )
    profiles = list_profiles(bridge_root=tmp_path)
    assert [profile["agent_id"] for profile in profiles] == ["bohr", "godel"]


def test_private_marker_refused_before_write(tmp_path: Path) -> None:
    with pytest.raises(AgentIdentityError) as excinfo:
        register_profile(
            agent_id="codex",
            kind="codex",
            display_name="PRIVATE_MARKER",
            bridge_root=tmp_path,
            now_utc=NOW,
        )
    assert excinfo.value.report["decision"] == "privacy_marker_refused"
    assert not (tmp_path / "agents" / "codex.json").exists()


def test_sensitive_marker_refused_before_write(tmp_path: Path) -> None:
    with pytest.raises(AgentIdentityError) as excinfo:
        register_profile(
            agent_id="codex",
            kind="codex",
            display_name="token broker",
            bridge_root=tmp_path,
            now_utc=NOW,
        )
    assert excinfo.value.report["decision"] == "privacy_marker_refused"
    assert not (tmp_path / "agents" / "codex.json").exists()


def test_read_refuses_profile_agent_mismatch(tmp_path: Path) -> None:
    agents = tmp_path / "agents"
    agents.mkdir()
    profile = register_profile(
        agent_id="bohr",
        kind="worker",
        display_name="Bohr",
        bridge_root=tmp_path,
        now_utc=NOW,
    )
    (agents / "codex.json").write_text(
        json.dumps(profile, sort_keys=True),
        encoding="utf-8",
    )

    with pytest.raises(AgentIdentityError) as excinfo:
        read_profile(agent_id="codex", bridge_root=tmp_path)
    assert excinfo.value.report["decision"] == "profile_agent_mismatch"


def test_read_refuses_malformed_profile(tmp_path: Path) -> None:
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "codex.json").write_text("not json\n", encoding="utf-8")

    with pytest.raises(AgentIdentityError) as excinfo:
        read_profile(agent_id="codex", bridge_root=tmp_path)
    assert excinfo.value.report["decision"] == "invalid_profile_json"


def test_validate_detects_policy_and_operator_approval_drift() -> None:
    profile = {
        "schema_version": "agent_profile.v1",
        "agent_id": "codex",
        "kind": "codex",
        "display_name": "Codex",
        "capabilities": [],
        "status": "disabled",
        "operator_approved": True,
        "write_scope_policy": "free_write",
        "created_at_utc": "2026-05-18T11:00:00Z",
        "updated_at_utc": "2026-05-18T11:00:00Z",
    }
    errors = validate_profile(profile)
    assert "operator_approved must default to false in v1" in errors
    assert "write_scope_policy must be claim_required" in errors
    assert "status must be active" in errors


def test_validate_requires_agent_uuid_for_bridge_event_capability() -> None:
    profile = {
        "schema_version": "agent_profile.v1",
        "agent_id": "codex",
        "kind": "codex",
        "display_name": "Codex",
        "capabilities": ["bridge_event"],
        "status": "active",
        "operator_approved": False,
        "write_scope_policy": "claim_required",
        "created_at_utc": "2026-05-18T11:00:00Z",
        "updated_at_utc": "2026-05-18T11:00:00Z",
    }

    errors = validate_profile(profile)
    assert "agent_uuid required for bridge_event capability" in errors


def test_cli_register_and_show_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = agent_identity.main(
        [
            "--bridge-root",
            str(tmp_path),
            "--json",
            "register",
            "--agent",
            "codex",
            "--kind",
            "codex",
            "--display-name",
            "Codex",
            "--agent-uuid",
            AGENT_UUID,
            "--capability",
            "work_queue",
        ]
    )
    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "registered"
    assert report["profile"]["agent_uuid"] == AGENT_UUID

    exit_code = agent_identity.main(
        ["--bridge-root", str(tmp_path), "--json", "show", "--agent", "codex"]
    )
    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["profile"]["agent_id"] == "codex"


def test_cli_validate_all_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    register_profile(
        agent_id="codex",
        kind="codex",
        display_name="Codex",
        bridge_root=tmp_path,
        now_utc=NOW,
    )
    exit_code = agent_identity.main(
        ["--bridge-root", str(tmp_path), "--json", "validate"]
    )
    assert exit_code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["decision"] == "valid"
    assert report["profiles"][0]["agent_id"] == "codex"
