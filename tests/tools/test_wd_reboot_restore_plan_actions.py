"""Structural checks for the rollback plan's operator-facing restart handoff.

These tests read Source text only. They do not execute Restore, change a pointer,
prove rollback readiness, or grant permission to restart a lane.
"""

from pathlib import Path
import re

import pytest


RESTORE = (
    Path(__file__).resolve().parents[2]
    / "ops/windows/reboot/Restore-WdRebootBundle.ps1"
)
ACTION_BLOCK = re.compile(
    r"\$plan\['actions'\]\s*=\s*@\(\r?\n(?P<body>.*?)\r?\n\)",
    re.DOTALL,
)
OLD_RESTART = (
    "Verify the pointer and every wrapper pin, then a non-elevated "
    "start-wd-all.ps1 -Auto; any failure is a HOLD, never a retry loop."
)
SAFE_RESTART = (
    "Verify the pointer and every wrapper pin. This plan authorizes no lane "
    "restart: HOLD until an exact individual lane restart procedure and its "
    "token checks are separately operator-approved; execute that procedure "
    "only from a non-elevated shell. Never launch the "
    "fleet automatically; any failure is a HOLD, never a retry loop."
)


def assert_scoped_restart_handoff(source: str) -> None:
    blocks = list(ACTION_BLOCK.finditer(source))
    assert len(blocks) == 1, "expected exactly one emitted plan actions block"
    body = blocks[0].group("body")
    assert "-auto" not in body.casefold(), "plan action must not propose -Auto"
    assert "start-wd-all.ps1" not in body.casefold(), "plan action must not name the fleet launcher"
    last_action = body.strip().splitlines()[-1].strip()
    assert last_action == f"'{SAFE_RESTART}'", (
        "restart handoff must preserve separate approval, individual lanes, "
        "pointer verification and HOLD-on-failure"
    )


def test_restore_actions_require_separately_approved_individual_lane_restart():
    assert_scoped_restart_handoff(RESTORE.read_text(encoding="utf-8-sig"))


def test_restore_restart_action_preserves_non_elevated_shell_requirement():
    source = RESTORE.read_text(encoding="utf-8-sig")
    block = list(ACTION_BLOCK.finditer(source))
    assert len(block) == 1
    assert "from a non-elevated shell" in block[0].group("body")


def test_restore_plan_does_not_imply_an_approved_restart_procedure_exists():
    source = RESTORE.read_text(encoding="utf-8-sig")
    block = list(ACTION_BLOCK.finditer(source))
    assert len(block) == 1
    assert "This plan authorizes no lane restart: HOLD until" in block[0].group("body")


def safe_control() -> str:
    return "$plan['actions'] = @(\n    'Keep all existing gates.',\n    '" + SAFE_RESTART + "'\n)\n"


def test_safe_restart_handoff_control():
    assert_scoped_restart_handoff(safe_control())


def test_mutant_reinstated_fleet_auto_is_rejected():
    mutant = safe_control().replace(SAFE_RESTART, OLD_RESTART)
    with pytest.raises(AssertionError, match="must not propose -Auto"):
        assert_scoped_restart_handoff(mutant)


def test_mutant_auto_hidden_in_earlier_action_is_rejected():
    mutant = safe_control().replace(
        "Keep all existing gates.", "Run start-wd-all.ps1 -aUtO first."
    )
    with pytest.raises(AssertionError, match="must not propose -Auto"):
        assert_scoped_restart_handoff(mutant)


def test_mutant_fleet_launcher_without_auto_is_rejected():
    mutant = safe_control().replace(
        "Keep all existing gates.", "Run START-WD-ALL.PS1 first."
    )
    with pytest.raises(AssertionError, match="must not name the fleet launcher"):
        assert_scoped_restart_handoff(mutant)


def test_mutant_existing_restart_authority_is_rejected():
    mutant = safe_control().replace(
        "This plan authorizes no lane restart: HOLD until", "Use the approved"
    )
    with pytest.raises(AssertionError, match="restart handoff must preserve"):
        assert_scoped_restart_handoff(mutant)


def test_mutant_separate_approval_removed_is_rejected():
    mutant = safe_control().replace("separately operator-approved", "default")
    with pytest.raises(AssertionError, match="restart handoff must preserve"):
        assert_scoped_restart_handoff(mutant)


def test_mutant_non_elevated_requirement_removed_is_rejected():
    mutant = safe_control().replace(" from a non-elevated shell", "")
    with pytest.raises(AssertionError, match="restart handoff must preserve"):
        assert_scoped_restart_handoff(mutant)


@pytest.mark.parametrize("mutant", ["", safe_control() + safe_control()])
def test_missing_or_duplicate_action_block_is_rejected(mutant):
    with pytest.raises(AssertionError, match="exactly one"):
        assert_scoped_restart_handoff(mutant)
