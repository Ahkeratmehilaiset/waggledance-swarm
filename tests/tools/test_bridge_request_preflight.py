"""Reject malformed new result contracts before any canonical/outbox write."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from test_wd_reboot_bundle import LANE_TEST_SHELLS, REBOOT
from test_wd_startup_recovery import q

BIN = REBOOT.parents[2] / ".agent-bridge/bin"


@pytest.mark.parametrize("host", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("case", ["orphan", "null", "duplicate", "unknown_type", "extra_key"])
def test_invalid_new_request_never_creates_bridge_files(tmp_path, host, case):
    contract = dict(schema="wd.task-result-contract.v1", required=["answer"],
                    types={"answer": "integer"}, additional_properties=False)
    payload = {"result_contract": contract, "requires_reply": True}
    if case == "orphan":
        payload = {"result_fields": ["answer"], "requires_reply": True}
    elif case == "null":
        payload["result_contract"] = None
    elif case == "duplicate":
        contract["required"] = ["answer", "answer"]
    elif case == "unknown_type":
        contract["types"]["answer"] = "intger"
    else:
        contract["requred"] = ["wrong"]
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("WD_BRIDGE_", "WD_REBOOT_", "AGENT_BRIDGE_"))}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(tmp_path)
    cmd = (f"& {q(BIN / 'Write-AgentEvent.ps1')} -Agent operator -Type wake_request "
           f"-To codex-tools-1 -TaskId fixture/preflight -PayloadJson {q(json.dumps(payload))}")
    result = subprocess.run([host, "-NoProfile", "-NonInteractive", "-Command", cmd],
                            env=env, capture_output=True, text=True, timeout=40)
    assert result.returncode != 0, result.stdout
    assert "Request contract rejected" in result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("host", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("fields,valid", [(["second", "first"], True),
                                       (["first", "first"], False),
                                       (["First", "second"], False)])
def test_required_fields_are_an_unordered_case_sensitive_set(tmp_path, host, fields, valid):
    payload = dict(result_fields=fields, result_contract=dict(
        schema="wd.task-result-contract.v1", required=["first", "second"]))
    cmd = (f". {q(BIN / 'BridgeTaskResult.ps1')}; "
           f"Get-BridgeTaskRequestValidation (ConvertFrom-Json {q(json.dumps(payload))})|ConvertTo-Json")
    result = subprocess.run([host, "-NoProfile", "-NonInteractive", "-Command", cmd],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["valid"] is valid


@pytest.mark.parametrize("host", LANE_TEST_SHELLS, ids=lambda p: Path(p).stem)
@pytest.mark.parametrize("payload", [{}, {"requires_reply": True},
    {"result_contract": dict(schema="wd.task-result-contract.v1", required=["answer"], types={}, equals={})}])
def test_empty_optional_objects_are_valid_under_strict_mode(tmp_path, host, payload):
    cmd = (f"$ErrorActionPreference='Stop'; . {q(BIN / 'BridgeTaskResult.ps1')}; "
           f"Get-BridgeTaskRequestValidation (ConvertFrom-Json {q(json.dumps(payload))})|ConvertTo-Json")
    result = subprocess.run([host, "-NoProfile", "-NonInteractive", "-Command", cmd],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["valid"] is True
