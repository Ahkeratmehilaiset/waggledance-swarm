"""-SessionId scopes the inventory OUTPUT, never the immutable-content check.

Grok finding (production-landing reconcile 2026-10-06, reproduced at 7cf48159): the session filter ran BEFORE the
duplicate/conflict check, so an own row of another session that reused a request_id with different content was
skipped. With -SessionId the inventory listed the id as clean, -IncludeRequest returned one of the conflicting bodies,
and -DiagnosticPartial reported no_conflict_observed. Every own row now goes through the conflict check first.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from test_bridge_request_inventory import INVENTORY, SHELLS, _bound, _inventory, _run, _write

pytestmark = pytest.mark.skipif(not SHELLS, reason="PowerShell is required")


def _conflicting_pair(order: str) -> list[dict]:
    """The same request_id from the same requester in two sessions with different content."""
    ours, _ = _bound("shared-v1")                                    # session_id "lead-session"
    theirs, _ = _bound("shared-v1", session_id="Lead-Session")
    theirs["message"] = "different content under the same immutable id"
    return [theirs, ours] if order == "other_session_first" else [ours, theirs]


ORDERS = ("other_session_first", "filtered_session_first")
SESSIONS = ("lead-session", "Lead-Session")


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("order", ORDERS)
@pytest.mark.parametrize("session", SESSIONS)
def test_session_filter_cannot_hide_a_conflicting_row_from_another_session(
    tmp_path: Path, shell: str, order: str, session: str,
) -> None:
    _write(tmp_path, _conflicting_pair(order))
    for extra in ((), ("-RequestId", "shared-v1", "-IncludeRequest")):
        process = _run(shell, INVENTORY, tmp_path, "-Agent", "codex-lead-1", "-SessionId", session, "-NoCache", *extra)
        assert process.returncode != 0, (extra, process.stdout)
        assert "Conflicting content for immutable request ID shared-v1" in process.stderr, process.stderr
        assert process.stdout.strip() == ""                          # no body of either conflicting row


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("order", ORDERS)
def test_session_scoped_diagnostic_reports_the_cross_session_conflict(tmp_path: Path, shell: str, order: str) -> None:
    good, good_reply = _bound("good-v1")
    _write(tmp_path, [*_conflicting_pair(order), good, good_reply])
    result = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-SessionId", "lead-session", "-NoCache",
                        "-DiagnosticPartial")
    assert result["complete"] is False
    assert result["status"] == "partial_unknown" and result["conflict_count"] == 1
    assert [entry["request_id"] for entry in result["requests"]] == ["good-v1"]
    assert result["request_count"] == 1                              # the conflicted id is never inventoried


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_success_twin_session_filter_still_scopes_the_output(tmp_path: Path, shell: str) -> None:
    ours, _ = _bound("ours-v1")
    theirs, _ = _bound("theirs-v1", session_id="Lead-Session")
    _write(tmp_path, [theirs, ours])
    for session, expected in (("lead-session", "ours-v1"), ("Lead-Session", "theirs-v1")):
        result = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-SessionId", session, "-NoCache")
        assert [entry["request_id"] for entry in result["requests"]] == [expected]
        assert result["request_count"] == 1 and result["session_id_filter"] == session
        exact = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-SessionId", session, "-NoCache",
                           "-RequestId", expected, "-IncludeRequest")
        assert exact["requests"][0]["request"] == (ours if expected == "ours-v1" else theirs)
    unfiltered = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-NoCache")
    assert unfiltered["request_count"] == 2
    diagnostic = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-SessionId", "lead-session", "-NoCache",
                            "-DiagnosticPartial")
    assert diagnostic["status"] == "no_conflict_observed" and diagnostic["request_count"] == 1
