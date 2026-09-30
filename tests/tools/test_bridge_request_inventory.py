"""Get-BridgeRequestInventory enumerates every request ID of an exact requester.

Regression for the RCO1 6efa finding: the native wake read (Read-AgentBridge
-NoContinuity, 40-row whole-log tail) cannot rediscover a request once more
than 40 unrelated rows follow its bound answer. The inventory must still list
the exact request, and Get-BridgeReplySnapshot must still resolve the answer.
"""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_bridge_request_contract import events


ROOT = Path(__file__).resolve().parents[2]
BIN = ROOT / ".agent-bridge/bin"
INVENTORY = BIN / "Get-BridgeRequestInventory.ps1"
SNAPSHOT = BIN / "Get-BridgeReplySnapshot.ps1"
READER = BIN / "Read-AgentBridge.ps1"
SHELLS = list(dict.fromkeys(filter(None, (shutil.which("pwsh"), shutil.which("powershell.exe")))))
pytestmark = pytest.mark.skipif(not SHELLS, reason="PowerShell is required")
_SCRUB = ("AGENT_BRIDGE_", "CLAUDE_CODE_", "WD_", "GIT_")


def _env(root: Path) -> dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith(_SCRUB)}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(root)
    return env


def _bound(request_id: str, **request_fields: object) -> tuple[dict, dict]:
    _, request, reply = events()
    request.update(request_id=request_id, request_digest=f"digest-{request_id}", **request_fields)
    reply.update(
        in_reply_to_request_id=request_id,
        in_reply_to_request_digest=f"digest-{request_id}",
        in_reply_to_requester={key: request[key] for key in ("agent", "agent_uuid", "session_id", "run_id")},
    )
    return request, reply


def _noise(count: int) -> list[dict]:
    # Every other noise row is a request-ID row of ANOTHER requester, so it also
    # lands in the derived reply index: a tail cutoff there must be caught too.
    rows = []
    for index in range(count):
        row = dict(ts_utc="2026-09-18T08:00:00Z", agent="fable-5", to="operator", type="message",
                   status="reported", task_id=f"noise/{index}", message="noise")
        if index % 2:
            row.update(to="codex-tools-1", type="wake_request", status="request", request_id=f"noise-{index}")
        rows.append(row)
    return rows


def _write(root: Path, rows: list[dict], tail: bytes = b"") -> Path:
    shared = root / "shared"
    shared.mkdir(parents=True, exist_ok=True)
    log = shared / "events.jsonl"
    log.write_bytes(b"".join(json.dumps(row).encode() + b"\n" for row in rows) + tail)
    return log


def _run(shell: str, script: Path, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(script), *args],
                          env=_env(root), capture_output=True, text=True, encoding="utf-8", timeout=240)


def _inventory(shell: str, root: Path, *args: str) -> dict:
    process = _run(shell, INVENTORY, root, *args)
    assert process.returncode == 0, process.stderr
    return json.loads(process.stdout)


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("no_cache", (False, True), ids=("cached", "no_cache"))
def test_bound_answer_hidden_by_60_noise_rows_is_still_enumerated_and_resolved(
    tmp_path: Path, shell: str, no_cache: bool,
) -> None:
    request, reply = _bound("hidden-by-noise-v1")
    log = _write(tmp_path, [request, reply, *_noise(60)])
    before = log.read_bytes()

    # Reproduce the gap with the exact native wake read: the request is gone.
    wake_view = _run(shell, READER, tmp_path, "-Agent", "codex-lead-1", "-Raw", "-NoAckReceived", "-NoContinuity")
    assert wake_view.returncode == 0, wake_view.stderr
    assert "hidden-by-noise-v1" not in wake_view.stdout

    extra = ("-NoCache",) if no_cache else ()
    result = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", *extra)
    assert result["schema"] == "wd.request-inventory.v2"
    assert result["authority_effect"] == "none"
    assert result["request_count"] == 1
    entry = result["requests"][0]
    assert entry["request_id"] == "hidden-by-noise-v1"
    assert "request" not in entry
    assert result["truncated"] is False
    exact = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-RequestId",
                       entry["request_id"], "-IncludeRequest", *extra)
    assert exact["requests"][0]["request"] == request
    assert entry["answer_state"] == "not_evaluated"
    assert result["snapshot_bytes"] == len(before)
    assert result["snapshot_cursor"]["offset"] == len(before)

    snapshot = _run(shell, SNAPSHOT, tmp_path, "-RequestId", entry["request_id"], "-Requester", "codex-lead-1", *extra)
    assert snapshot.returncode == 0, snapshot.stderr
    resolved = json.loads(snapshot.stdout)
    assert resolved["results"][0]["state"] == "answered"
    assert resolved["results"][0]["answers"] == [reply]

    assert log.read_bytes() == before
    written = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file())
    if no_cache:
        assert written == ["shared/events.jsonl"]
    else:
        assert all(path == "shared/events.jsonl" or path.startswith("shared/cache/") for path in written)


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_no_tail_or_age_cutoff_beyond_5000_rows(tmp_path: Path, shell: str) -> None:
    old, _ = _bound("old-before-5200-noise", ts_utc="2026-01-01T00:00:00Z")
    late, _ = _bound("late-after-noise", ts_utc="2026-09-18T09:00:00Z")
    _write(tmp_path, [old, *_noise(5200), late])
    result = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-NoCache")
    assert [entry["request_id"] for entry in result["requests"]] == ["late-after-noise", "old-before-5200-noise"]
    assert result["parsed_rows"] == 5202


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_exact_case_identity_session_filter_and_foreign_ids(tmp_path: Path, shell: str) -> None:
    upper, _ = _bound("Case-1")
    lower, _ = _bound("case-1")
    impostor, _ = _bound("impostor-1", agent="Codex-Lead-1")
    other_session, _ = _bound("other-session-1", session_id="Lead-Session")
    # Lead's own non-request row that happens to carry a request_id is not a request.
    not_a_request = dict(ts_utc="2026-09-18T07:40:00Z", agent="codex-lead-1", to="codex-tools-1",
                         type="message", status="answered", task_id="fixture/closure",
                         request_id="closure-not-request")
    foreign, _ = _bound("Case-1", agent="codex-tools-1", to="codex-lead-1")
    _write(tmp_path, [upper, lower, impostor, other_session, foreign, not_a_request])

    result = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-NoCache")
    ids = [entry["request_id"] for entry in result["requests"]]
    assert ids == ["other-session-1", "case-1", "Case-1"]
    assert result["case_variant_request_ids"] == ["case-1", "Case-1"]
    by_id = {entry["request_id"]: entry for entry in result["requests"]}
    assert by_id["Case-1"]["id_also_used_by_other_requester"] is True
    assert by_id["case-1"]["id_also_used_by_other_requester"] is False

    filtered = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-SessionId", "lead-session", "-NoCache")
    assert [entry["request_id"] for entry in filtered["requests"]] == ["case-1", "Case-1"]
    assert filtered["session_id_filter"] == "lead-session"


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_identical_duplicate_counts_once_and_conflict_fails_closed(tmp_path: Path, shell: str) -> None:
    request, reply = _bound("retry-v1")
    _write(tmp_path, [request, reply, request])
    result = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-NoCache")
    assert result["request_count"] == 1
    assert result["requests"][0]["occurrences"] == 2
    assert result["requests"][0]["first_indexed_position"] == 0

    changed = deepcopy(request)
    changed["message"] = "different content under the same immutable id"
    _write(tmp_path, [request, reply, changed])
    process = _run(shell, INVENTORY, tmp_path, "-Agent", "codex-lead-1", "-NoCache")
    assert process.returncode != 0
    assert "Conflicting content for immutable request ID retry-v1" in process.stderr
    assert process.stdout.strip() == ""


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("case", ("partial_line", "invalid_json", "missing_log", "own_id_binding_conflict"))
def test_read_and_malformed_failures_fail_closed(tmp_path: Path, shell: str, case: str) -> None:
    request, reply = _bound("fail-closed-v1")
    if case == "partial_line":
        _write(tmp_path, [request, reply], tail=b'{"agent":"codex-lead-1"')
    elif case == "invalid_json":
        _write(tmp_path, [request], tail=b"{not json}\n")
    elif case == "missing_log":
        (tmp_path / "shared").mkdir()
    else:
        request["payload"]["request_id"] = "a-different-id"
        _write(tmp_path, [request, reply])
    process = _run(shell, INVENTORY, tmp_path, "-Agent", "codex-lead-1", "-NoCache")
    assert process.returncode != 0
    assert process.stdout.strip() == ""


# -- Tools 7e: individually valid but CONFLICTING top-level/payload request IDs (authored, NOT RUN) --

@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_opt_in_diagnostic_lists_a_conflicting_own_id_and_is_never_complete(tmp_path: Path, shell: str) -> None:
    good, good_reply = _bound("good-v1")
    conflicted, _ = _bound("top-v1")
    conflicted["payload"]["request_id"] = "payload-v1"                # each id is valid; together they conflict
    _write(tmp_path, [conflicted, good, good_reply])
    refused = _run(shell, INVENTORY, tmp_path, "-Agent", "codex-lead-1", "-NoCache")
    assert refused.returncode != 0 and refused.stdout.strip() == ""    # the ordinary inventory still fails closed
    assert "Malformed request_id at indexed position 0" in refused.stderr
    result = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-NoCache", "-DiagnosticPartial")
    assert result["schema"] == "wd.request-inventory-diagnostic.v1" and result["complete"] is False
    assert result["status"] == "partial_unknown" and result["conflict_count"] == 1
    assert result["conflicts_truncated"] is False
    [conflict] = result["conflicts"]
    assert conflict == {"indexed_position": 0, "first_indexed_position": None, "kind": "request_id_binding_conflict",
                        "ts_utc": conflicted["ts_utc"], "task_id": conflicted["task_id"],
                        "top_level_request_id": "top-v1", "payload_request_id": "payload-v1",
                        "top_level_agent": "codex-lead-1", "payload_agent": conflicted["payload"].get("agent")}
    assert [entry["request_id"] for entry in result["requests"]] == ["good-v1"]   # the valid row stays
    identity = result["snapshot_identity"]
    assert identity["bytes"] == result["snapshot_bytes"] and identity["cursor"] == result["snapshot_cursor"]
    assert identity["prefix_sha256"] and identity["parsed_rows"] == result["parsed_rows"]


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_another_requesters_conflicting_row_never_contaminates_the_diagnostic(tmp_path: Path, shell: str) -> None:
    mine, _ = _bound("mine-v1")
    theirs, _ = _bound("theirs-v1", agent="codex-tools-1", to="codex-lead-1")
    theirs["payload"]["request_id"] = "theirs-payload-v1"
    impostor, _ = _bound("impostor-v1", agent="codex-tools-1", to="codex-lead-1")
    impostor["payload"]["agent"] = "codex-lead-1"                       # only the PAYLOAD claims the requester
    _write(tmp_path, [theirs, impostor, mine])
    result = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-NoCache", "-DiagnosticPartial")
    assert (result["conflict_count"], result["conflicts"], result["status"]) == (0, [], "no_conflict_observed")
    assert result["complete"] is False                                  # a diagnostic is never a complete inventory
    assert [entry["request_id"] for entry in result["requests"]] == ["mine-v1"]
    assert result["request_count"] == 1 and result["requests"][0]["id_also_used_by_other_requester"] is False
    clean = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-NoCache")   # the default twin, unchanged shape
    assert clean["schema"] == "wd.request-inventory.v2" and "complete" not in clean and "conflicts" not in clean
    assert [entry["request_id"] for entry in clean["requests"]] == ["mine-v1"]


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_every_diagnostic_kind_is_reported_and_the_list_is_bounded(tmp_path: Path, shell: str) -> None:
    author, _ = _bound("author-v1")
    author["payload"]["agent"] = "someone-else"                         # own top-level agent, conflicting payload
    first, reply = _bound("immutable-v1")
    changed = deepcopy(first)
    changed["message"] = "different content under the same immutable id"
    many = []
    for index in range(60):
        row, _ = _bound(f"many-{index}")
        row["payload"]["request_id"] = f"other-{index}" + "x" * 300     # long: every field text is bounded
        many.append(row)
    _write(tmp_path, [author, first, reply, changed, *many])
    result = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-NoCache", "-DiagnosticPartial")
    assert result["conflict_count"] == 62 and len(result["conflicts"]) == 50 and result["conflicts_truncated"] is True
    assert [conflict["kind"] for conflict in result["conflicts"][:3]] == [
        "author_binding_conflict", "immutable_id_content_conflict", "request_id_binding_conflict"]
    immutable = result["conflicts"][1]
    assert (immutable["indexed_position"], immutable["first_indexed_position"]) == (3, 1)
    assert immutable["top_level_request_id"] == "immutable-v1" and result["conflicts"][0]["payload_agent"] == "someone-else"
    assert all(len(conflict["payload_request_id"] or "") <= 160 for conflict in result["conflicts"])
    assert result["requests"] == [] and result["request_count"] == 0     # reported, never inventoried
    assert len(json.dumps(result)) < 50_000
    refused = _run(shell, INVENTORY, tmp_path, "-Agent", "codex-lead-1", "-NoCache")
    assert refused.returncode != 0 and "Malformed request author binding at indexed position 0" in refused.stderr


KIND_CASES = (   # (name, top-level fields, payload fields, the ONLY truthful kind); fable-5 727 S1/N3
    ("number-v1", {"request_id": 123}, {}, "malformed_request_id"),
    ("bool-v1", {"request_id": True}, {}, "malformed_request_id"),
    ("array-v1", {"request_id": ["a", "b"]}, {}, "malformed_request_id"),
    ("forged-v1", {"request_id": {"invalid_binding": True}}, {}, "malformed_request_id"),   # a forged marker
    ("pattern-v1", {"request_id": "not a valid id"}, {}, "malformed_request_id"),
    ("surrogate-v1", {"request_id": "x" * 159 + "\U0001F600" + "y" * 10}, {}, "malformed_request_id"),
    ("equal-v1", {"request_id": 7}, {"request_id": 7}, "malformed_request_id"),   # both sides, same JSON
    ("differ-v1", {"request_id": "top-v1"}, {"request_id": "payload-v1"}, "request_id_binding_conflict"),
    ("typed-v1", {"request_id": "5"}, {"request_id": 5}, "request_id_binding_conflict"),   # canonical JSON differs
)


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_a_single_sided_or_equal_value_is_malformed_never_a_binding_conflict(tmp_path: Path, shell: str) -> None:
    rows = []
    for name, top, payload, _ in KIND_CASES:
        row, _ = _bound(name)
        row.update(top)
        row["payload"].update(payload)
        rows.append(row)
    good, _ = _bound("good-v1")
    _write(tmp_path, [*rows, good])
    result = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-NoCache", "-DiagnosticPartial")
    conflicts = result["conflicts"]
    assert [conflict["kind"] for conflict in conflicts] == [kind for *_, kind in KIND_CASES]
    assert [conflict["indexed_position"] for conflict in conflicts] == list(range(len(KIND_CASES)))
    assert [conflict["top_level_request_id"] for conflict in conflicts[:4]] == [
        "123", "true", '["a","b"]', '{"invalid_binding":true}']              # non-strings as canonical JSON
    assert conflicts[5]["top_level_request_id"] == "x" * 159                  # never cut inside a surrogate pair
    assert (conflicts[6]["top_level_request_id"], conflicts[6]["payload_request_id"]) == ("7", "7")
    assert [entry["request_id"] for entry in result["requests"]] == ["good-v1"]   # the success twin
    refused = _run(shell, INVENTORY, tmp_path, "-Agent", "codex-lead-1", "-NoCache")
    assert refused.returncode != 0 and refused.stdout.strip() == ""           # the default text is unchanged
    assert "Malformed request_id at indexed position 0" in refused.stderr


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_a_falsy_request_id_is_dropped_by_the_index_in_both_modes_a_known_limit(tmp_path: Path, shell: str) -> None:
    """fable-5 a8530218 S1, pinned as a LIMIT (not a feature): own rows with a falsy request_id are neither
    inventoried nor refused nor listed. The index drops them unless in_reply_to_request_id is truthy; then the
    getter skips them as replies (RCO2 a621 docs nit, pinned by the kept-v1 twin below)."""
    rows = []
    for name, falsy in (("false-v1", False), ("empty-v1", ""), ("zero-v1", 0)):
        row, _ = _bound(name)
        row["request_id"] = falsy
        rows.append(row)
    good, _ = _bound("good-v1")
    _write(tmp_path, [*rows, good])
    default = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-NoCache")   # no refusal: the limit
    assert [entry["request_id"] for entry in default["requests"]] == ["good-v1"] and default["parsed_rows"] == 4
    result = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-NoCache", "-DiagnosticPartial")
    assert (result["conflict_count"], result["status"], result["complete"]) == (0, "no_conflict_observed", False)
    assert "falsy" in result["note"]                                          # the receipt says so itself
    kept, _ = _bound("kept-v1")
    kept.update(request_id="", in_reply_to_request_id="other-v1")            # the index KEEPS this row
    _write(tmp_path, [kept, good])
    for extra in ((), ("-DiagnosticPartial",)):                              # both modes skip it as a reply
        both = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-NoCache", *extra)
        assert [entry["request_id"] for entry in both["requests"]] == ["good-v1"] and both["parsed_rows"] == 2
    assert (both["conflict_count"], both["status"]) == (0, "no_conflict_observed")
    truthy, _ = _bound("truthy-v1")
    truthy["request_id"] = 123                                               # twin: a TRUTHY malformed id is seen
    _write(tmp_path, [*rows, truthy, good])
    seen = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-NoCache", "-DiagnosticPartial")
    assert [(c["indexed_position"], c["kind"]) for c in seen["conflicts"]] == [(0, "malformed_request_id")]
    refused = _run(shell, INVENTORY, tmp_path, "-Agent", "codex-lead-1", "-NoCache")
    assert refused.returncode != 0 and "Malformed request_id at indexed position 0" in refused.stderr


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_malformed_author_is_reachable_for_a_requester_named_true(tmp_path: Path, shell: str) -> None:
    """fable-5 a8530218 N2 (corrects RCO1's earlier "unreachable"): -Agent's ValidatePattern is case-insensitive,
    so "True" is a valid requester, and a JSON true author stringifies to it: [string]$true -ceq 'True'."""
    single, _ = _bound("single-v1", agent=True)                             # one raw side, a bool
    dual, _ = _bound("dual-v1", agent=True)
    dual["payload"]["agent"] = "True"                                        # both raw sides, JSON differs
    text, _ = _bound("text-v1", agent="True")                                # the string twin: a valid author
    _write(tmp_path, [single, dual, text])
    result = _inventory(shell, tmp_path, "-Agent", "True", "-NoCache", "-DiagnosticPartial")
    assert [(c["indexed_position"], c["kind"], c["top_level_agent"], c["payload_agent"]) for c in result["conflicts"]] == [
        (0, "malformed_author", "true", None), (1, "author_binding_conflict", "true", "True")]
    assert [entry["request_id"] for entry in result["requests"]] == ["text-v1"]
    refused = _run(shell, INVENTORY, tmp_path, "-Agent", "True", "-NoCache")
    assert refused.returncode != 0 and "Malformed request author binding at indexed position 0" in refused.stderr


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_other_requesters_malformed_row_does_not_blind_the_inventory(tmp_path: Path, shell: str) -> None:
    mine, _ = _bound("mine-v1")
    theirs, _ = _bound("theirs-v1", agent="codex-tools-1", to="codex-lead-1")
    theirs["payload"]["agent"] = "someone-else"
    _write(tmp_path, [theirs, mine])
    result = _inventory(shell, tmp_path, "-Agent", "codex-lead-1", "-NoCache")
    assert [entry["request_id"] for entry in result["requests"]] == ["mine-v1"]
