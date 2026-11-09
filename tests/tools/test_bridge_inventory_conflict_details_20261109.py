"""Synthetic diagnostic details do not choose or expose conflicting requests."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from test_bridge_request_inventory import INVENTORY, _bound, _inventory, _run, _write

SHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(not SHELL, reason="Windows PowerShell is required")


def test_conflict_references_and_binding_names_without_content(tmp_path):
    first, _ = _bound("synthetic-conflict")
    first["message"] = "FIRST_MESSAGE_PRIVATE_CANARY"
    first["nonce"] = "FIRST_NONCE_PRIVATE_CANARY"
    second = deepcopy(first)
    second["message"] = "SECOND_MESSAGE_PRIVATE_CANARY"
    second["nonce"] = "SECOND_NONCE_PRIVATE_CANARY"
    second["session_id"] = "different-synthetic-session"
    log = _write(tmp_path, [first, second])
    original = log.read_bytes()

    blocked = _run(SHELL, INVENTORY, tmp_path, "-Agent", "codex-lead-1", "-NoCache")
    assert blocked.returncode != 0 and blocked.stdout.strip() == ""
    result = _inventory(SHELL, tmp_path, "-Agent", "codex-lead-1", "-NoCache", "-DiagnosticPartial")
    assert result["complete"] is False and result["status"] == "partial_unknown"
    assert result["requests"] == [] and result["authority_effect"] == "none"
    assert "not binding verdicts" in result["note"]
    [conflict] = result["conflicts"]
    assert conflict["kind"] == "immutable_id_content_conflict"
    for key, position in (("first_reference", 0), ("conflicting_reference", 1)):
        assert conflict[key] == {"scheme": "wd.reply-index-position.v1", "indexed_position": position,
                                 "snapshot_sha256": result["snapshot_identity"]["prefix_sha256"]}
    assert conflict["differing_binding_fields"] == [
        {"field": "session_id", "different": True}, {"field": "nonce", "different": True},
    ]
    serialized = json.dumps(result)
    for value in ("FIRST_MESSAGE_PRIVATE_CANARY", "SECOND_MESSAGE_PRIVATE_CANARY",
                  "FIRST_NONCE_PRIVATE_CANARY", "SECOND_NONCE_PRIVATE_CANARY", "different-synthetic-session"):
        assert value not in serialized
    assert log.read_bytes() == original


def test_message_only_conflict_does_not_emit_message_or_binding_values(tmp_path):
    first, _ = _bound("message-only-conflict")
    second = deepcopy(first)
    first["message"] = "FIRST_MESSAGE_PRIVATE_CANARY"
    second["message"] = "SECOND_MESSAGE_PRIVATE_CANARY"
    _write(tmp_path, [first, second])
    result = _inventory(SHELL, tmp_path, "-Agent", "codex-lead-1", "-NoCache", "-DiagnosticPartial")
    [conflict] = result["conflicts"]
    assert conflict["differing_binding_fields"] == []
    assert "PRIVATE_CANARY" not in json.dumps(result)


def test_each_conflicting_occurrence_references_first_and_its_own_position(tmp_path):
    first, _ = _bound("multiple-conflicts")
    second, third = deepcopy(first), deepcopy(first)
    second["payload"]["nonce"] = "SECOND_NONCE_PRIVATE_CANARY"
    third["payload"]["nonce"] = "THIRD_NONCE_PRIVATE_CANARY"
    third["request_digest"] = "THIRD_DIGEST_PRIVATE_CANARY"
    _write(tmp_path, [first, second, third])
    result = _inventory(SHELL, tmp_path, "-Agent", "codex-lead-1", "-NoCache", "-DiagnosticPartial")
    assert result["conflict_count"] == 2 and result["requests"] == []
    second_report, third_report = result["conflicts"]
    assert [x["conflicting_reference"]["indexed_position"] for x in result["conflicts"]] == [1, 2]
    assert second_report["first_reference"] == third_report["first_reference"]
    assert second_report["differing_binding_fields"] == [{"field": "nonce", "different": True}]
    assert third_report["differing_binding_fields"] == [
        {"field": "request_digest", "different": True}, {"field": "nonce", "different": True},
    ]
    assert "PRIVATE_CANARY" not in json.dumps(result)


def test_exact_duplicate_is_not_a_conflict(tmp_path):
    first, _ = _bound("same-request")
    _write(tmp_path, [first, deepcopy(first)])
    result = _inventory(SHELL, tmp_path, "-Agent", "codex-lead-1", "-NoCache", "-DiagnosticPartial")
    assert result["status"] == "no_conflict_observed" and result["conflict_count"] == 0
    assert result["requests"][0]["occurrences"] == 2


@pytest.mark.parametrize("before,after", [([], None), (["ARRAY_VALUE_PRIVATE_CANARY"], "ARRAY_VALUE_PRIVATE_CANARY")])
@pytest.mark.parametrize("nested", [False, True])
def test_binding_comparison_preserves_array_shape(tmp_path, before, after, nested):
    first, _ = _bound("array-binding")
    second = deepcopy(first)
    target_first = first["payload"] if nested else first
    target_second = second["payload"] if nested else second
    target_first["expected_responders"] = before
    target_second["expected_responders"] = after
    second["message"] = "different synthetic message"
    _write(tmp_path, [first, second])
    result = _inventory(SHELL, tmp_path, "-Agent", "codex-lead-1", "-NoCache", "-DiagnosticPartial")
    [conflict] = result["conflicts"]
    assert conflict["differing_binding_fields"] == [{"field": "expected_responders", "different": True}]
    assert "ARRAY_VALUE_PRIVATE_CANARY" not in json.dumps(result)


def test_many_immutable_conflicts_remain_bounded_and_never_select_a_request(tmp_path):
    first, _ = _bound("many-conflicts")
    rows = [first]
    for index in range(60):
        changed = deepcopy(first)
        changed["message"] = f"MESSAGE_PRIVATE_CANARY_{index}"
        rows.append(changed)
    _write(tmp_path, rows)
    result = _inventory(SHELL, tmp_path, "-Agent", "codex-lead-1", "-NoCache", "-DiagnosticPartial")
    assert result["complete"] is False and result["status"] == "partial_unknown"
    assert result["conflict_count"] == 60 and result["conflicts_truncated"] is True
    assert 0 < len(result["conflicts"]) <= 50 and result["requests"] == []
    assert [item["conflicting_reference"]["indexed_position"] for item in result["conflicts"]] == list(
        range(1, len(result["conflicts"]) + 1)
    )
    assert "MESSAGE_PRIVATE_CANARY" not in json.dumps(result)
