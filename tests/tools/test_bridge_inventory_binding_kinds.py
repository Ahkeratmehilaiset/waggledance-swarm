"""Inventory binding kinds for an own request-like row whose request_id is not one exact valid string (fable-5).

Runs the REAL composed Get-BridgeRequestInventory.ps1 with its real BridgeRequestContract.ps1 on synthetic tmp
runtimes. Default mode fails closed (throws, no stdout); -DiagnosticPartial returns the typed diagnostic schema with
the exact kind: request_id_binding_conflict when BOTH top-level and payload copies are present and differ (ordinally,
in canonical JSON), malformed_request_id otherwise. Valid rows stay unchanged.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".agent-bridge/bin/Get-BridgeRequestInventory.ps1"
SHELLS = tuple(dict.fromkeys(filter(None, (shutil.which("pwsh"), shutil.which("powershell.exe")))))
pytestmark = pytest.mark.skipif(not SHELLS or not SCRIPT.is_file(), reason="Bridge PowerShell package required")
ALL = pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
SOFT = "­"
ZW = "​"


def row(index: int, **over: object) -> dict:
    value = dict(ts_utc=f"2026-10-02T00:00:{index:02d}Z", agent="codex-lead-1", agent_uuid="u", session_id="s1",
                 run_id="r", to="codex-tools-1", type="wake_request", status="assigned", task_id=f"fx/task-{index:02d}",
                 request_id=f"req-{index:02d}", request_digest=f"d-{index:02d}", message="binding kind fixture",
                 payload={})
    value.update(over)
    return value


def runtime(tmp_path: Path, rows: list[dict]) -> Path:
    shared = tmp_path / "runtime" / "shared"
    shared.mkdir(parents=True)
    (shared / "events.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return tmp_path / "runtime"


def inv(shell: str, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("AGENT_BRIDGE_", "WD_", "GIT_"))}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(root)
    return subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(SCRIPT), "-Agent", "codex-lead-1",
                           "-NoCache", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=120)


def ok(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.returncode == 0, result.stderr[-800:]
    return json.loads(result.stdout)


# name -> (request_id top-level override or MISSING, payload request_id or MISSING, expected diagnostic kind)
MISSING = object()
# The payload-side U+00AD / U+200B cases (an otherwise valid top-level id) need the ordinal typed contract getter
# (d09b227e / b77a7500 BridgeRequestContract.ps1) composed with this inventory; they are kept out of this file until
# that pair lands (prepared and RED/GREEN-proven in the fable-5 audit, not skipped here).
CONFLICTS = {
    "soft_hyphen_top": ("req-01" + SOFT, "req-01", "request_id_binding_conflict"),
    "case_payload": ("req-01", "REQ-01", "request_id_binding_conflict"),
    "type_int_payload": ("req-01", 1, "request_id_binding_conflict"),
    "type_list_payload": ("req-01", ["req-01"], "request_id_binding_conflict"),
    "int_top_only": (7, MISSING, "malformed_request_id"),
    "object_top_only": ({"id": "req-01"}, MISSING, "malformed_request_id"),
    "invalid_charset_top_only": ("req 01", MISSING, "malformed_request_id"),
    "soft_hyphen_top_only": ("req-01" + SOFT, MISSING, "malformed_request_id"),
}


def conflict_row(top: object, payload: object) -> dict:
    value = row(1)
    if top is MISSING:
        value.pop("request_id")
    else:
        value["request_id"] = top
    if payload is not MISSING:
        value["payload"] = {"request_id": payload}
    return value


@ALL
@pytest.mark.parametrize("name", sorted(CONFLICTS))
def test_invalid_request_id_default_mode_fails_closed(tmp_path: Path, shell: str, name: str) -> None:
    top, payload, _ = CONFLICTS[name]
    result = inv(shell, runtime(tmp_path, [row(0), conflict_row(top, payload)]))
    assert result.returncode != 0, result.stdout[:300]
    assert not result.stdout.strip()
    assert "Malformed request_id at indexed position 1" in result.stderr, result.stderr[-500:]


@ALL
@pytest.mark.parametrize("name", sorted(CONFLICTS))
def test_invalid_request_id_diagnostic_kind_is_exact(tmp_path: Path, shell: str, name: str) -> None:
    top, payload, kind = CONFLICTS[name]
    out = ok(inv(shell, runtime(tmp_path, [row(0), conflict_row(top, payload)]), "-DiagnosticPartial"))
    assert out["schema"] == "wd.request-inventory-diagnostic.v1" and out["status"] == "partial_unknown", out
    assert [(c["indexed_position"], c["kind"]) for c in out["conflicts"]] == [(1, kind)], out["conflicts"]
    assert [r["request_id"] for r in out["requests"]] == ["req-00"], out["requests"]


@ALL
def test_valid_rows_and_equal_copies_are_unchanged(tmp_path: Path, shell: str) -> None:
    equal = row(1, payload={"request_id": "req-01"})
    out = ok(inv(shell, runtime(tmp_path, [row(0), equal])))
    assert out["schema"] == "wd.request-inventory.v2" and out["request_count"] == 2, out
    assert sorted(r["request_id"] for r in out["requests"]) == ["req-00", "req-01"]
    diag = ok(inv(shell, runtime(tmp_path / "d", [row(0), equal]), "-DiagnosticPartial"))
    assert diag["status"] == "no_conflict_observed" and diag["conflicts"] == [], diag
