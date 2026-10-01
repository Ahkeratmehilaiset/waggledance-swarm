"""Bounded inventory contract on a synthetic, old-to-new request history."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / ".agent-bridge/bin/Get-BridgeRequestInventory.ps1"
BASE_BIN = SOURCE.parent
SHELLS = tuple(dict.fromkeys(filter(None, (shutil.which("pwsh"), shutil.which("powershell.exe")))))
pytestmark = pytest.mark.skipif(not SHELLS or not BASE_BIN.is_dir(), reason="Bridge PowerShell package required")


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    runtime = tmp_path / "runtime"
    shared = runtime / "shared"
    shared.mkdir(parents=True)
    rows = []
    for index in range(600):
        rows.append(dict(
            ts_utc=f"2026-09-29T{index // 3600:02d}:{index // 60 % 60:02d}:{index % 60:02d}Z",
            agent="codex-lead-1", agent_uuid="lead-uuid", session_id="lead-session",
            run_id="lead-run", to="codex-tools-1", type="wake_request",
            status="assigned", task_id=f"fixture/task-{index:03d}",
            request_id=f"request-{index:03d}", request_digest=f"digest-{index:03d}",
            message="bounded inventory regression", payload={},
        ))
    (shared / "events.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    bin_dir = tmp_path / "bin"
    shutil.copytree(BASE_BIN, bin_dir)
    if SOURCE.exists():
        shutil.copy2(SOURCE, bin_dir / SOURCE.name)
    return runtime, bin_dir / SOURCE.name


def _run(shell: str, runtime: Path, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith(("AGENT_BRIDGE_", "WD_", "GIT_"))}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(runtime)
    return subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-File", str(script), "-Agent", "codex-lead-1", "-NoCache", *args],
        capture_output=True, text=True, encoding="utf-8", env=env, timeout=45,
    )


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_600_requests_default_is_bounded_and_discoverable(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    result = _run(shell, runtime, script)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["request_count"] == 600
    assert len(result.stdout) < 50_000
    assert data["truncated"] is True
    assert data["next_cursor"] is not None
    assert len(data["requests"]) <= 50
    assert data["requests"][0]["request_id"] == "request-599"
    assert all("request" not in entry for entry in data["requests"])


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_exact_old_request_remains_retrievable(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    result = _run(shell, runtime, script, "-RequestId", "request-000")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["request_count"] == 600
    assert data["matched_count"] == 1
    assert data["requests"][0]["request_id"] == "request-000"
    assert data["truncated"] is False


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_cursor_is_newest_first_and_refuses_changed_snapshot(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    first = json.loads(_run(shell, runtime, script, "-PageSize", "2").stdout)
    assert [row["request_id"] for row in first["requests"]] == ["request-599", "request-598"]
    second = _run(shell, runtime, script, "-PageSize", "2", "-Cursor", first["next_cursor"])
    assert second.returncode == 0, second.stderr
    assert [row["request_id"] for row in json.loads(second.stdout)["requests"]] == ["request-597", "request-596"]

    log = runtime / "shared/events.jsonl"
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(ts_utc="2026-09-29T01:00:00Z", agent="fable-5", type="message")) + "\n")
    stale = _run(shell, runtime, script, "-Cursor", first["next_cursor"])
    assert stale.returncode != 0
    assert "cursor does not match" in stale.stderr
    assert not stale.stdout.strip()


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_task_and_timestamp_exact_filters_and_request_opt_in(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    broad = _run(shell, runtime, script, "-IncludeRequest")
    assert broad.returncode != 0
    assert not broad.stdout.strip()

    exact = _run(shell, runtime, script, "-TaskId", "fixture/task-000", "-TsUtc", "2026-09-29T00:00:00Z", "-IncludeRequest")
    assert exact.returncode == 0, exact.stderr
    data = json.loads(exact.stdout)
    assert data["matched_count"] == 1
    assert data["requests"][0]["request"]["request_id"] == "request-000"


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_large_exact_request_fails_without_partial_json(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    log = runtime / "shared/events.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    rows[0]["message"] = "x" * 60_000
    log.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    result = _run(shell, runtime, script, "-RequestId", "request-000", "-IncludeRequest")
    assert result.returncode != 0
    assert "exceeds 50000 characters" in result.stderr
    assert not result.stdout.strip()


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_duplicate_count_and_conflicting_id_fail_closed(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    log = runtime / "shared/events.jsonl"
    first = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(first) + "\n")
    ok = _run(shell, runtime, script, "-RequestId", "request-000")
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["requests"][0]["occurrences"] == 2

    first["message"] = "conflicting immutable request"
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(first) + "\n")
    conflict = _run(shell, runtime, script, "-RequestId", "request-000")
    assert conflict.returncode != 0
    assert "Conflicting content for immutable request ID" in conflict.stderr
    assert not conflict.stdout.strip()


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_partial_last_line_fails_closed(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    with (runtime / "shared/events.jsonl").open("ab") as stream:
        stream.write(b'{"agent":"codex-lead-1"')
    result = _run(shell, runtime, script)
    assert result.returncode != 0
    assert not result.stdout.strip()


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("changed", (
    ("-SessionId", "different-session"),
    ("-RequestId", "request-000"),
    ("-TaskId", "fixture/task-000"),
    ("-TsUtc", "2026-09-29T00:00:00Z"),
))
def test_cursor_rejects_changed_filters(tmp_path: Path, shell: str, changed: tuple[str, str]) -> None:
    runtime, script = _fixture(tmp_path)
    first = json.loads(_run(shell, runtime, script, "-PageSize", "2").stdout)
    changed_page = _run(shell, runtime, script, "-PageSize", "2", "-Cursor", first["next_cursor"], *changed)
    assert changed_page.returncode != 0
    assert "cursor does not match" in changed_page.stderr
    assert not changed_page.stdout.strip()


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_all_pages_cover_every_id_once_without_loss(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    seen: list[str] = []
    cursor: str | None = None
    while True:
        args = ("-Cursor", cursor) if cursor else ()
        result = _run(shell, runtime, script, *args)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["request_count"] == 600
        seen.extend(row["request_id"] for row in data["requests"])
        cursor = data["next_cursor"]
        assert data["truncated"] is bool(cursor)
        if not cursor:
            break
    assert seen == [f"request-{index:03d}" for index in range(599, -1, -1)]


# -- Opt-in -DiagnosticPartial receipt (authored, NOT RUN) --

@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_diagnostic_pages_like_the_default_but_never_shares_its_cursor(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    default = json.loads(_run(shell, runtime, script, "-PageSize", "2").stdout)
    first = _run(shell, runtime, script, "-PageSize", "2", "-DiagnosticPartial")
    assert first.returncode == 0, first.stderr
    diagnostic = json.loads(first.stdout)
    assert (diagnostic["schema"], diagnostic["complete"], diagnostic["status"]) == (
        "wd.request-inventory-diagnostic.v1", False, "no_conflict_observed")   # clean, yet never complete
    assert (diagnostic["request_count"], diagnostic["conflicts"]) == (600, [])
    assert [row["request_id"] for row in diagnostic["requests"]] == ["request-599", "request-598"]
    assert diagnostic["next_cursor"] != default["next_cursor"]
    for cursor, extra in ((default["next_cursor"], ("-DiagnosticPartial",)), (diagnostic["next_cursor"], ())):
        crossed = _run(shell, runtime, script, "-PageSize", "2", "-Cursor", cursor, *extra)
        assert crossed.returncode != 0 and "cursor does not match" in crossed.stderr
        assert not crossed.stdout.strip()
    second = _run(shell, runtime, script, "-PageSize", "2", "-Cursor", diagnostic["next_cursor"], "-DiagnosticPartial")
    assert second.returncode == 0, second.stderr
    assert [row["request_id"] for row in json.loads(second.stdout)["requests"]] == ["request-597", "request-596"]


def test_only_the_opt_in_diagnostic_changes_the_cursor_seed() -> None:
    """fable-5 727 N1: a DEFAULT seed, and so every default cursor, stays byte-identical to the pre-diagnostic
    getter (3125486a). Only -DiagnosticPartial adds its discriminator. Pinned on the source, since the old getter
    is not run here: the key appears exactly once, set only under the switch, AFTER the shared fields."""
    source = SOURCE.read_text(encoding="utf-8")
    assert source.count("diagnostic_partial") == 1
    guard = "if ($DiagnosticPartial) { $cursorFields['diagnostic_partial']=$true }"
    assert guard in source
    assert source.index("include_request=[bool]$IncludeRequest") < source.index(guard) < source.index(
        "$cursorSeed=$cursorFields | ConvertTo-Json -Depth 8 -Compress")
    # fable-5 a8530218 N3: pin the default seed's fields too (names, order and values exactly as 3125486a's).
    fields = ("$cursorFields=[ordered]@{\n"
              "    snapshot=$snapshot.candidate_cursor\n"
              "    prefix_sha256=$snapshot.prefix_sha256\n"
              "    agent=$Agent; session_id=$SessionId; request_id=$RequestId\n"
              "    task_id=$TaskId; ts_utc=$TsUtc\n"
              "    order='first_indexed_position_desc'; page_size=$PageSize\n"
              "    include_request=[bool]$IncludeRequest\n"
              "}\n")
    assert fields in source


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_600_conflicting_rows_list_only_50_and_the_default_still_refuses(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    log = runtime / "shared/events.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        row["payload"]["request_id"] = "payload-" + row["request_id"]      # valid alone, conflicting together
    log.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    refused = _run(shell, runtime, script)
    assert refused.returncode != 0 and not refused.stdout.strip()
    result = _run(shell, runtime, script, "-DiagnosticPartial")
    assert result.returncode == 0, result.stderr
    assert len(result.stdout) < 50_000
    data = json.loads(result.stdout)
    assert (data["conflict_count"], len(data["conflicts"]), data["conflicts_truncated"]) == (600, 50, True)
    assert (data["status"], data["complete"], data["requests"], data["request_count"]) == (
        "partial_unknown", False, [], 0)
    assert [conflict["indexed_position"] for conflict in data["conflicts"]] == list(range(50))
    assert {conflict["kind"] for conflict in data["conflicts"]} == {"request_id_binding_conflict"}


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_long_conflict_fields_close_the_list_at_the_character_budget(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    log = runtime / "shared/events.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()][:50]
    for index, row in enumerate(rows):                                   # ~720 JSON characters per entry
        row.update(task_id="t" * 200 + str(index), request_id="a" * 200 + str(index))
        row["payload"]["request_id"] = "b" * 200 + str(index)
    log.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    result = _run(shell, runtime, script, "-DiagnosticPartial")
    assert result.returncode == 0, result.stderr   # 50 x ~720 = ~36000: fits the 50000 page cap, NOT the 30000 budget
    assert len(result.stdout) < 50_000
    data = json.loads(result.stdout)
    listed = data["conflicts"]
    assert data["conflict_count"] == 50 and data["conflicts_truncated"] is True and 0 < len(listed) < 50
    assert [conflict["indexed_position"] for conflict in listed] == list(range(len(listed)))   # a prefix
    assert len(json.dumps(listed, separators=(",", ":"))) <= 30_000 + len(listed) + 1
    assert all(len(conflict["task_id"]) == len(conflict["top_level_request_id"]) == 160 for conflict in listed)


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_include_request_rejects_no_exact_match(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    result = _run(shell, runtime, script, "-RequestId", "absent-request", "-IncludeRequest")
    assert result.returncode != 0
    assert "requires exactly one matched request" in result.stderr
    assert not result.stdout.strip()


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_include_request_rejects_ambiguous_task_timestamp_even_page_one(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    log = runtime / "shared/events.jsonl"
    rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    rows[1]["task_id"] = rows[0]["task_id"]
    rows[1]["ts_utc"] = rows[0]["ts_utc"]
    log.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    result = _run(shell, runtime, script, "-TaskId", rows[0]["task_id"],
                  "-TsUtc", rows[0]["ts_utc"], "-PageSize", "1", "-IncludeRequest")
    assert result.returncode != 0
    assert "requires exactly one matched request" in result.stderr
    assert not result.stdout.strip()

# --- append-resumable continuation token v3 (opt-in; frozen complete prefix) ------------------------------

def _append(runtime: Path, text: str) -> None:
    with (runtime / "shared/events.jsonl").open("ab") as stream:
        stream.write(text.encode("utf-8"))


def _row(index: int, **over: object) -> str:
    row = dict(ts_utc="2026-09-30T00:00:00Z", agent="codex-lead-1", agent_uuid="lead-uuid", session_id="lead-session",
               run_id="lead-run", to="codex-tools-1", type="wake_request", status="assigned",
               task_id=f"fixture/task-{index:03d}", request_id=f"request-{index:03d}", request_digest=f"digest-{index:03d}",
               message="bounded inventory regression", payload={})
    row.update(over)
    return json.dumps(row) + "\n"


def _ids(result: subprocess.CompletedProcess[str]) -> list[str]:
    assert result.returncode == 0, result.stderr
    return [row["request_id"] for row in json.loads(result.stdout)["requests"]]


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_default_output_has_no_continuation_fields(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    data = json.loads(_run(shell, runtime, script, "-PageSize", "2").stdout)
    assert "continuation_token" not in data and "continuation" not in data
    assert data["next_cursor"].count(":") == 2                        # the v2 cursor is unchanged


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_resumable_token_survives_complete_and_unfinished_appends(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    first = json.loads(_run(shell, runtime, script, "-PageSize", "2", "-Resumable").stdout)
    token = first["continuation_token"]
    assert first["continuation"] == "frozen_prefix_v3" and token.startswith("v3.")
    _append(runtime, _row(600) + _row(601, type="message", status="cancelled", request_id=None) + '{"agent":"codex-lead-1"')
    second = _run(shell, runtime, script, "-PageSize", "2", "-ContinuationToken", token)
    assert _ids(second) == ["request-597", "request-596"]
    data = json.loads(second.stdout)
    assert data["snapshot_bytes"] == first["snapshot_bytes"] and data["request_count"] == 600
    assert data["authority_effect"] == "none" and data["next_cursor"] is None
    third = _run(shell, runtime, script, "-PageSize", "2", "-ContinuationToken", data["continuation_token"])
    assert _ids(third) == ["request-595", "request-594"]
    stale = _run(shell, runtime, script, "-PageSize", "2", "-Cursor", first["next_cursor"])
    assert stale.returncode != 0 and not stale.stdout.strip()   # legacy cursor still refused (here by the unfinished tail)


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_resumable_pages_cover_the_frozen_snapshot_once_despite_appends(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    page = json.loads(_run(shell, runtime, script, "-Resumable").stdout)
    seen = [row["request_id"] for row in page["requests"]]
    count = 600
    while page["truncated"]:
        _append(runtime, _row(count))                                  # a new request after every page
        count += 1
        result = _run(shell, runtime, script, "-ContinuationToken", page["continuation_token"])
        assert result.returncode == 0, result.stderr
        page = json.loads(result.stdout)
        seen.extend(row["request_id"] for row in page["requests"])
    assert seen == [f"request-{index:03d}" for index in range(599, -1, -1)]   # all frozen ids, once, newest first
    assert page["continuation_token"] is None


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("change", ("rewrite", "truncate", "truncate_into_prefix", "rotate"))
def test_resumable_token_refuses_prefix_rewrite_truncation_and_rotation(tmp_path: Path, shell: str, change: str) -> None:
    runtime, script = _fixture(tmp_path)
    log = runtime / "shared/events.jsonl"
    token = json.loads(_run(shell, runtime, script, "-PageSize", "2", "-Resumable").stdout)["continuation_token"]
    data = log.read_bytes()
    if change == "rewrite":                                            # same length, one byte of an old row
        index = data.index(b"bounded inventory regression")
        log.write_bytes(data[:index] + b"B" + data[index + 1:])
    elif change == "truncate":
        log.write_bytes(data[: data.rindex(b"\n", 0, len(data) - 1) + 1])
    elif change == "truncate_into_prefix":
        log.write_bytes(data[: len(data) // 2])
    else:                                                              # same bytes, a new file identity
        replacement = log.with_name("events.new")
        replacement.write_bytes(data)
        os.replace(replacement, log)
    refused = _run(shell, runtime, script, "-PageSize", "2", "-ContinuationToken", token)
    assert refused.returncode != 0, refused.stdout
    assert not refused.stdout.strip()


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
@pytest.mark.parametrize("changed", (
    ("-PageSize", "3"), ("-SessionId", "lead-session"), ("-TaskId", "fixture/task-000"), ("-DiagnosticPartial",),
))
def test_resumable_token_refuses_a_changed_query_or_page_size(tmp_path: Path, shell: str, changed: tuple[str, ...]) -> None:
    runtime, script = _fixture(tmp_path)
    token = json.loads(_run(shell, runtime, script, "-PageSize", "2", "-Resumable").stdout)["continuation_token"]
    args = ["-ContinuationToken", token, *changed] + ([] if changed[0] == "-PageSize" else ["-PageSize", "2"])
    refused = _run(shell, runtime, script, *args)
    assert refused.returncode != 0 and "does not match this query" in refused.stderr
    assert not refused.stdout.strip()


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_resumable_token_is_validated_and_never_mixed_with_a_legacy_cursor(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    first = json.loads(_run(shell, runtime, script, "-PageSize", "2", "-Resumable").stdout)
    both = _run(shell, runtime, script, "-PageSize", "2", "-ContinuationToken", first["continuation_token"],
                "-Cursor", first["next_cursor"])
    assert both.returncode != 0 and not both.stdout.strip()
    parts = first["continuation_token"].split(".")
    for index, value in ((3, "1"), (4, "0" * 64), (5, "1"), (7, "999999")):
        forged = ".".join(parts[:index] + [value] + parts[index + 1:])
        result = _run(shell, runtime, script, "-PageSize", "2", "-ContinuationToken", forged)
        assert result.returncode != 0 and not result.stdout.strip(), (index, result.stdout)


@pytest.mark.parametrize("shell", SHELLS, ids=lambda value: Path(value).stem)
def test_a_conflict_appended_after_the_freeze_is_outside_the_frozen_pages_but_refused_fresh(tmp_path: Path, shell: str) -> None:
    runtime, script = _fixture(tmp_path)
    token = json.loads(_run(shell, runtime, script, "-PageSize", "2", "-Resumable").stdout)["continuation_token"]
    _append(runtime, _row(0, message="conflicting immutable request"))
    assert _ids(_run(shell, runtime, script, "-PageSize", "2", "-ContinuationToken", token)) == ["request-597", "request-596"]
    fresh = _run(shell, runtime, script, "-PageSize", "2", "-Resumable")
    assert fresh.returncode != 0 and "Conflicting content for immutable request ID" in fresh.stderr
