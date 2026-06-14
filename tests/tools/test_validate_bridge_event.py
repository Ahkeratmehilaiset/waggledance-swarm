# SPDX-License-Identifier: BUSL-1.1
"""Tests for tools/validate_bridge_event.py."""
from __future__ import annotations

import importlib
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

AGENT_UUID = "11111111-2222-3333-4444-555555555555"
OTHER_UUID = "22222222-3333-4444-5555-666666666666"


def _good_event(**overrides: object) -> dict[str, object]:
    event: dict[str, object] = {
        "ts_utc": "2026-05-16T05:39:57.9995496Z",
        "agent": "codex",
        "type": "message",
        "task_id": "bridge-schema-smoke",
        "status": "answered",
        "severity": "",
        "to": "claude",
        "message": "validator smoke",
        "paths": [],
        "write_scope": [],
        "run_id": "",
        "pid": 1234,
        "cwd": "C:\\Python\\project2-master",
        "payload": {},
    }
    event.update(overrides)
    return event


def _write_jsonl(path: Path, events: list[dict[str, object] | str]) -> None:
    lines = [
        item if isinstance(item, str) else json.dumps(item, sort_keys=True)
        for item in events
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_agent_profile(
    profiles_dir: Path,
    *,
    agent_id: str = "codex",
    agent_uuid: str = AGENT_UUID,
) -> None:
    profiles_dir.mkdir(parents=True, exist_ok=True)
    (profiles_dir / f"{agent_id}.json").write_text(
        json.dumps(
            {"agent_id": agent_id, "agent_uuid": agent_uuid},
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_identity_registry(
    path: Path,
    *,
    identities: dict[str, str] | None = None,
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "waggledance.bridge_identity_registry.v1",
                "identities": identities or {"codex-lead-1": AGENT_UUID},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_cli_returns_zero_and_json_summary_for_valid_file(
    tmp_path: Path,
    capsys,
) -> None:
    mod = importlib.import_module("tools.validate_bridge_event")
    events_path = tmp_path / "events.jsonl"
    _write_jsonl(events_path, [_good_event()])

    rc = mod.main(["--events", str(events_path), "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["valid"] == 1
    assert payload["invalid"] == 0


def test_cli_uses_runtime_bridge_root_env_by_default(
    tmp_path: Path,
    capsys,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = importlib.import_module("tools.validate_bridge_event")
    runtime_bridge = tmp_path / "runtime" / ".agent-bridge"
    runtime_events = runtime_bridge / "shared" / "events.jsonl"
    runtime_events.parent.mkdir(parents=True)
    _write_jsonl(runtime_events, [_good_event()])

    shadow_root = tmp_path / "shadow"
    shadow_events = shadow_root / ".agent-bridge" / "shared" / "events.jsonl"
    shadow_events.parent.mkdir(parents=True)
    _write_jsonl(shadow_events, [_good_event(type="wake_request", to="")])

    monkeypatch.chdir(shadow_root)
    monkeypatch.setenv("AGENT_BRIDGE_RUNTIME_ROOT", str(runtime_bridge))
    monkeypatch.delenv("AGENT_BRIDGE_ROOT", raising=False)

    rc = mod.main(["--json", "--no-waivers"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["valid"] == 1
    assert payload["invalid"] == 0


def test_cli_agent_profiles_reject_uuid_mismatch(
    tmp_path: Path,
    capsys,
) -> None:
    mod = importlib.import_module("tools.validate_bridge_event")
    events_path = tmp_path / "events.jsonl"
    profiles_dir = tmp_path / "agents"
    _write_agent_profile(profiles_dir)
    _write_jsonl(
        events_path,
        [_good_event(agent="codex", agent_uuid=OTHER_UUID)],
    )

    rc = mod.main([
        "--events",
        str(events_path),
        "--agent-profiles",
        str(profiles_dir),
        "--json",
    ])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1
    assert payload["ok"] is False
    assert payload["agent_profiles_loaded"] == 1
    assert "agent_uuid does not match" in payload["issues"][0]["error"]


def test_cli_agent_profiles_accept_matching_uuid(
    tmp_path: Path,
    capsys,
) -> None:
    mod = importlib.import_module("tools.validate_bridge_event")
    events_path = tmp_path / "events.jsonl"
    profiles_dir = tmp_path / "agents"
    _write_agent_profile(profiles_dir)
    _write_jsonl(
        events_path,
        [_good_event(agent="codex", agent_uuid=AGENT_UUID)],
    )

    rc = mod.main([
        "--events",
        str(events_path),
        "--agent-profiles",
        str(profiles_dir),
        "--json",
    ])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["agent_profiles_loaded"] == 1


def test_cli_identity_registry_warn_reports_uuid_hygiene_without_failing(
    tmp_path: Path,
    capsys,
) -> None:
    mod = importlib.import_module("tools.validate_bridge_event")
    events_path = tmp_path / "events.jsonl"
    registry_path = tmp_path / "bridge_identity_registry.json"
    _write_identity_registry(registry_path)
    _write_jsonl(
        events_path,
        [
            _good_event(agent="codex-lead-1", status="build_consensus_pass"),
            _good_event(
                agent="codex-lead-1",
                status="rco_pass",
                agent_uuid=OTHER_UUID,
            ),
            _good_event(agent="legacy-agent"),
        ],
    )

    rc = mod.main([
        "--events",
        str(events_path),
        "--identity-registry",
        str(registry_path),
        "--json",
    ])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    audit = payload["identity_registry_audit"]
    assert rc == 0
    assert payload["ok"] is True
    assert audit["mode"] == "warn"
    assert audit["ok"] is False
    assert audit["missing_uuid_registered_events"] == 1
    assert audit["mismatched_uuid_registered_events"] == 1
    assert audit["gate_relevant_missing_uuid"] == 1
    assert audit["gate_relevant_mismatched_uuid"] == 1
    assert audit["non_registry_agent_event_count"] == 1
    assert audit["examples"][0]["reason"] == "missing_uuid"


def test_cli_identity_registry_strict_fails_uuid_hygiene_issue(
    tmp_path: Path,
    capsys,
) -> None:
    mod = importlib.import_module("tools.validate_bridge_event")
    events_path = tmp_path / "events.jsonl"
    registry_path = tmp_path / "bridge_identity_registry.json"
    _write_identity_registry(registry_path)
    _write_jsonl(events_path, [_good_event(agent="codex-lead-1")])

    rc = mod.main([
        "--events",
        str(events_path),
        "--identity-registry",
        str(registry_path),
        "--identity-registry-mode",
        "strict",
        "--json",
    ])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1
    assert payload["ok"] is False
    assert payload["invalid"] == 0
    assert payload["identity_registry_audit"]["issue_count"] == 1


def test_cli_identity_registry_strict_accepts_matching_uuid(
    tmp_path: Path,
    capsys,
) -> None:
    mod = importlib.import_module("tools.validate_bridge_event")
    events_path = tmp_path / "events.jsonl"
    registry_path = tmp_path / "bridge_identity_registry.json"
    _write_identity_registry(registry_path)
    _write_jsonl(
        events_path,
        [_good_event(agent="codex-lead-1", agent_uuid=AGENT_UUID)],
    )

    rc = mod.main([
        "--events",
        str(events_path),
        "--identity-registry",
        str(registry_path),
        "--identity-registry-mode",
        "strict",
        "--json",
    ])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["identity_registry_audit"]["ok"] is True


def test_cli_event_hygiene_warn_reports_bridge_event_shape_issues(
    tmp_path: Path,
    capsys,
) -> None:
    mod = importlib.import_module("tools.validate_bridge_event")
    events_path = tmp_path / "events.jsonl"
    _write_jsonl(
        events_path,
        [
            _good_event(),
            _good_event(type="correction", payload=None),
            _good_event(payload={"api_token_count": 0}),
        ],
    )

    rc = mod.main(["--events", str(events_path), "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    audit = payload["event_hygiene_audit"]
    assert rc == 0
    assert payload["ok"] is True
    assert audit["mode"] == "warn"
    assert audit["ok"] is False
    assert audit["unknown_event_type_count"] == 1
    assert audit["unknown_event_types"] == [{"value": "correction", "count": 1}]
    assert audit["non_object_payload_count"] == 1
    assert audit["non_object_payload_types"] == [{"value": "NoneType", "count": 1}]
    assert audit["sensitive_payload_key_name_count"] == 1
    assert audit["sensitive_payload_key_names"] == [
        {"value": "api_token_count", "count": 1}
    ]


def test_cli_event_hygiene_strict_fails_bridge_event_shape_issues(
    tmp_path: Path,
    capsys,
) -> None:
    mod = importlib.import_module("tools.validate_bridge_event")
    events_path = tmp_path / "events.jsonl"
    _write_jsonl(events_path, [_good_event(type="correction")])

    rc = mod.main([
        "--events",
        str(events_path),
        "--event-hygiene-mode",
        "strict",
        "--json",
    ])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1
    assert payload["ok"] is False
    assert payload["invalid"] == 0
    assert payload["event_hygiene_audit"]["issue_count"] == 1


def test_cli_returns_one_for_invalid_file_and_reports_issue(
    tmp_path: Path,
    capsys,
) -> None:
    mod = importlib.import_module("tools.validate_bridge_event")
    events_path = tmp_path / "events.jsonl"
    _write_jsonl(
        events_path,
        [
            _good_event(),
            _good_event(type="wake_request", to=""),
        ],
    )

    rc = mod.main(["--events", str(events_path), "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1
    assert payload["ok"] is False
    assert payload["valid"] == 1
    assert payload["invalid"] == 1
    assert payload["waived_invalid"] == 0
    assert payload["issues"][0]["line_no"] == 2
    assert payload["issues"][0]["raw_sha256"].startswith("sha256:")


def test_cli_tail_can_validate_recent_clean_lines_only(
    tmp_path: Path,
    capsys,
) -> None:
    mod = importlib.import_module("tools.validate_bridge_event")
    events_path = tmp_path / "events.jsonl"
    _write_jsonl(events_path, ["{not-json}", _good_event()])

    rc = mod.main(["--events", str(events_path), "--tail", "1", "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert payload["checked"] == 1
    assert payload["ok"] is True


def test_cli_waives_matching_historical_issue_by_line_and_hash(
    tmp_path: Path,
    capsys,
) -> None:
    mod = importlib.import_module("tools.validate_bridge_event")
    events_path = tmp_path / "events.jsonl"
    _write_jsonl(
        events_path,
        [
            _good_event(),
            _good_event(type="handoff", task_id=""),
        ],
    )
    raw_bad_line = events_path.read_text(encoding="utf-8").splitlines()[1]
    waiver_path = tmp_path / "waivers.json"
    waiver_path.write_text(
        json.dumps({
            "schema_version": "agent-bridge-event-waivers.v1",
            "waivers": [{
                "line_no": 2,
                "raw_line_sha256": "sha256:"
                + hashlib.sha256(raw_bad_line.encode("utf-8")).hexdigest(),
                "error": "line 2: <event>: Value error, handoff requires task_id",
                "reason": "known historical fixture",
            }],
        }),
        encoding="utf-8",
    )

    rc = mod.main([
        "--events",
        str(events_path),
        "--waivers",
        str(waiver_path),
        "--json",
    ])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["valid"] == 1
    assert payload["invalid"] == 0
    assert payload["waived_invalid"] == 1
    assert payload["issues"] == []
    assert payload["waived_issues"][0]["line_no"] == 2


def test_cli_rejects_stale_waiver_hash(
    tmp_path: Path,
    capsys,
) -> None:
    mod = importlib.import_module("tools.validate_bridge_event")
    events_path = tmp_path / "events.jsonl"
    _write_jsonl(events_path, [_good_event(type="handoff", task_id="")])
    waiver_path = tmp_path / "waivers.json"
    waiver_path.write_text(
        json.dumps({
            "schema_version": "agent-bridge-event-waivers.v1",
            "waivers": [{
                "line_no": 1,
                "raw_line_sha256": "sha256:" + ("0" * 64),
                "error": "line 1: <event>: Value error, handoff requires task_id",
                "reason": "stale hash",
            }],
        }),
        encoding="utf-8",
    )

    rc = mod.main([
        "--events",
        str(events_path),
        "--waivers",
        str(waiver_path),
        "--json",
    ])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1
    assert payload["ok"] is False
    assert payload["invalid"] == 1
    assert payload["waived_invalid"] == 0


def test_cli_rejects_stale_waiver_error(
    tmp_path: Path,
    capsys,
) -> None:
    mod = importlib.import_module("tools.validate_bridge_event")
    events_path = tmp_path / "events.jsonl"
    _write_jsonl(events_path, [_good_event(type="handoff", task_id="")])
    raw_bad_line = events_path.read_text(encoding="utf-8").splitlines()[0]
    waiver_path = tmp_path / "waivers.json"
    waiver_path.write_text(
        json.dumps({
            "schema_version": "agent-bridge-event-waivers.v1",
            "waivers": [{
                "line_no": 1,
                "raw_line_sha256": "sha256:"
                + hashlib.sha256(raw_bad_line.encode("utf-8")).hexdigest(),
                "error": "line 1: some other historical error",
                "reason": "stale error",
            }],
        }),
        encoding="utf-8",
    )

    rc = mod.main([
        "--events",
        str(events_path),
        "--waivers",
        str(waiver_path),
        "--json",
    ])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1
    assert payload["ok"] is False
    assert payload["invalid"] == 1
    assert payload["waived_invalid"] == 0


def test_cli_rejects_matching_hash_on_wrong_line(
    tmp_path: Path,
    capsys,
) -> None:
    mod = importlib.import_module("tools.validate_bridge_event")
    events_path = tmp_path / "events.jsonl"
    _write_jsonl(
        events_path,
        [
            _good_event(),
            _good_event(type="handoff", task_id=""),
        ],
    )
    raw_bad_line = events_path.read_text(encoding="utf-8").splitlines()[1]
    waiver_path = tmp_path / "waivers.json"
    waiver_path.write_text(
        json.dumps({
            "schema_version": "agent-bridge-event-waivers.v1",
            "waivers": [{
                "line_no": 1,
                "raw_line_sha256": "sha256:"
                + hashlib.sha256(raw_bad_line.encode("utf-8")).hexdigest(),
                "error": "line 2: <event>: Value error, handoff requires task_id",
                "reason": "wrong line",
            }],
        }),
        encoding="utf-8",
    )

    rc = mod.main([
        "--events",
        str(events_path),
        "--waivers",
        str(waiver_path),
        "--json",
    ])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1
    assert payload["ok"] is False
    assert payload["invalid"] == 1
    assert payload["waived_invalid"] == 0


def test_cli_rejects_malformed_waiver_digest(
    tmp_path: Path,
    capsys,
) -> None:
    mod = importlib.import_module("tools.validate_bridge_event")
    events_path = tmp_path / "events.jsonl"
    _write_jsonl(events_path, [_good_event()])
    waiver_path = tmp_path / "waivers.json"
    waiver_path.write_text(
        json.dumps({
            "schema_version": "agent-bridge-event-waivers.v1",
            "waivers": [{
                "line_no": 1,
                "raw_line_sha256": "sha256:not-a-real-digest",
                "error": "line 1: fixture",
                "reason": "bad config",
            }],
        }),
        encoding="utf-8",
    )

    rc = mod.main([
        "--events",
        str(events_path),
        "--waivers",
        str(waiver_path),
        "--json",
    ])

    captured = capsys.readouterr()
    assert rc == 2
    assert "raw_line_sha256" in captured.err


def test_cli_missing_events_file_is_a_nonzero_schema_failure(
    tmp_path: Path,
    capsys,
) -> None:
    mod = importlib.import_module("tools.validate_bridge_event")
    missing_path = tmp_path / "missing.jsonl"

    rc = mod.main(["--events", str(missing_path), "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert rc == 1
    assert payload["missing_path"] == str(missing_path)


def test_cli_runs_by_file_path_from_repo_root(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    events_path = tmp_path / "events.jsonl"
    _write_jsonl(events_path, [_good_event()])

    completed = subprocess.run(
        [
            sys.executable,
            str(root / "tools" / "validate_bridge_event.py"),
            "--events",
            str(events_path),
            "--json",
        ],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
