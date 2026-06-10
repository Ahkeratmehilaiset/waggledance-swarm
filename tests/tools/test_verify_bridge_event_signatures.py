# SPDX-License-Identifier: Apache-2.0
"""Log-only bridge signature auditor (HMAC Phase A)."""
from __future__ import annotations

import json
from pathlib import Path

from tools.verify_bridge_event_signatures import (
    build_signature_audit,
    main,
)
from waggledance.core.bridge_event_hmac import (
    generate_agent_key,
    load_agent_key,
    sign_event_fields,
)
from waggledance.core.magma.canonical import sha256_digest

SECRET_MESSAGE = "operator-private detail not for reports"


def _signed_event(agent: str, key: bytes, *, tamper_status: str | None = None):
    fields = dict(
        agent=agent,
        ts_utc="2026-06-10T07:00:00Z",
        event_type="decision",
        status="rco_pass",
        task_id="task/audit",
        message=SECRET_MESSAGE,
    )
    hmac_obj = sign_event_fields(key=key, **fields)
    return {
        "agent": agent,
        "ts_utc": fields["ts_utc"],
        "type": "decision",
        "status": tamper_status or fields["status"],
        "task_id": fields["task_id"],
        "message": fields["message"],
        "payload": {"hmac": hmac_obj},
    }


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    key_dir = tmp_path / "keys"
    generate_agent_key("claude-rco-1", key_dir)
    generate_agent_key("codex-lead-1", key_dir)
    rco_key = load_agent_key("claude-rco-1", key_dir)
    lead_key = load_agent_key("codex-lead-1", key_dir)

    events = [
        _signed_event("claude-rco-1", rco_key),                       # valid
        _signed_event("claude-rco-1", rco_key, tamper_status="rco_block"),  # invalid
        {"agent": "codex-lead-1", "ts_utc": "2026-06-10T07:01:00Z",
         "type": "message", "status": "", "task_id": "t",
         "message": "unsigned", "payload": {}},                       # unsigned
        _signed_event("codex-tools-1", lead_key),     # signed, no key file -> unverifiable
        {"agent": "someone-else", "ts_utc": "2026-06-10T07:02:00Z",
         "type": "message", "payload": {}},                           # filtered out
    ]
    events_path = tmp_path / "events.jsonl"
    lines = [json.dumps(event) for event in events] + ["{not json"]
    events_path.write_text("\n".join(lines), encoding="utf-8")
    return events_path, key_dir


def test_audit_classifies_and_counts_exactly(tmp_path: Path):
    events_path, key_dir = _fixture(tmp_path)
    report = build_signature_audit(
        events_path=events_path,
        key_dir=key_dir,
        agents=["claude-rco-1", "codex-lead-1", "codex-tools-1",
                "claude-rco-2"],
    )

    assert report["scanned_line_count"] == 6
    assert report["parse_error_count"] == 1
    assert report["audited_event_count"] == 4  # foreign agent excluded
    assert report["per_agent"]["claude-rco-1"]["valid"] == 1
    assert report["per_agent"]["claude-rco-1"]["invalid"] == 1
    assert report["per_agent"]["codex-lead-1"]["unsigned"] == 1
    assert report["per_agent"]["codex-tools-1"]["unverifiable"] == 1
    assert report["totals"]["valid"] == 1
    assert report["invalid_signature_count"] == 1
    assert report["signed_count"] == 3
    assert report["signed_coverage_rate"] == 3 / 4
    # Phase A literals: observation only, never a gate input
    assert report["enforcement_applied"] is False
    assert report["gate_consulted_this_report"] is False
    assert report["runtime_authority_granted"] is False
    # digest-bound, re-derivable report
    core = {k: v for k, v in report.items() if k != "canonical_digest"}
    assert report["canonical_digest"] == sha256_digest(core)


def test_report_is_privacy_safe_and_cli_exits_zero(tmp_path: Path, capsys):
    events_path, key_dir = _fixture(tmp_path)
    exit_code = main([
        "--events", str(events_path),
        "--key-dir", str(key_dir),
        "--json",
    ])
    out = capsys.readouterr().out
    assert exit_code == 0  # log-only: invalid signatures never block
    assert SECRET_MESSAGE not in out
    payload = json.loads(out)
    assert payload["invalid_signature_count"] == 1


def test_init_key_and_sign_helpers(tmp_path: Path, capsys):
    key_dir = tmp_path / "fresh-keys"
    assert main(["--init-key", "claude-rco-2",
                 "--key-dir", str(key_dir)]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["agent"] == "claude-rco-2"
    # second init refuses (never overwrite)
    assert main(["--init-key", "claude-rco-2",
                 "--key-dir", str(key_dir)]) == 2
    capsys.readouterr()

    assert main([
        "--sign", "--agent", "claude-rco-2", "--key-dir", str(key_dir),
        "--ts-utc", "2026-06-10T07:05:00Z", "--type", "decision",
        "--status", "rco_pass", "--task-id", "t", "--message", "m",
    ]) == 0
    hmac_obj = json.loads(capsys.readouterr().out)
    assert hmac_obj["sig"].startswith("hmac-sha256:")
    assert hmac_obj["key_id"].startswith("k:")
