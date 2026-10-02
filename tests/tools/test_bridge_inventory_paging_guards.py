"""Opt-in v3 inventory continuation guards: a continuation position must be a page boundary of the same frozen query,
and frozen pages observe (never claim completeness about) rows appended after the freeze. Synthetic tmp runtimes only."""

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
V2_KEYS = {"schema", "requester", "runtime_root_source", "session_id_filter", "request_id_filter", "task_id_filter",
           "ts_utc_filter", "read_started_utc", "read_completed_utc", "snapshot_cursor", "snapshot_bytes", "parsed_rows",
           "cache_status", "cache_path", "request_count", "matched_count", "returned_count", "page_size", "truncated",
           "next_cursor", "requests", "case_variant_request_ids", "answer_authority", "note", "authority_effect"}
V3_KEYS = V2_KEYS | {"continuation", "continuation_token", "appended_after_freeze_observed"}


def row(index: int, agent: str = "codex-lead-1", **over: object) -> str:
    value = dict(ts_utc=f"2026-10-02T00:{index // 60 % 60:02d}:{index % 60:02d}Z", agent=agent, agent_uuid="u",
                 session_id="s1", run_id="r", to="codex-tools-1", type="wake_request", status="assigned",
                 task_id=f"fx/task-{index:04d}", request_id=f"req-{agent[:5]}-{index:04d}", request_digest=f"d-{index:04d}",
                 message="paging guard fixture", payload={})
    value.update(over)
    return json.dumps(value) + "\n"


def runtime(tmp_path: Path, count: int, foreign: bool = False, sessions: bool = False) -> Path:
    shared = tmp_path / "runtime" / "shared"
    shared.mkdir(parents=True)
    text = []
    for index in range(count):
        text.append(row(index, session_id=("s2" if sessions and index % 2 else "s1")))
        if foreign:
            text.append(row(index, agent="codex-tools-1"))                       # own i at indexed 2i, foreign at 2i+1
    (shared / "events.jsonl").write_text("".join(text), encoding="utf-8")
    return tmp_path / "runtime"


def append(root: Path, text: str) -> None:
    with (root / "shared" / "events.jsonl").open("ab") as stream:
        stream.write(text.encode("utf-8"))


def inv(shell: str, root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("AGENT_BRIDGE_", "WD_", "GIT_"))}
    env["AGENT_BRIDGE_RUNTIME_ROOT"] = str(root)
    return subprocess.run([shell, "-NoProfile", "-NonInteractive", "-File", str(SCRIPT), "-Agent", "codex-lead-1", "-NoCache", *args],
                          capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)


def ok(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.returncode == 0, result.stderr[-800:]
    return json.loads(result.stdout)


def refused(result: subprocess.CompletedProcess[str], needle: str) -> None:
    assert result.returncode != 0, result.stdout[:300]
    assert not result.stdout.strip()                                             # never a partial page
    assert needle in result.stderr, result.stderr[-500:]


def ids(page: dict) -> list[str]:
    return [item["request_id"] for item in page["requests"]]


def at(token: str, position: int) -> str:
    parts = token.split(".")
    parts[7] = str(position)
    return ".".join(parts)


@ALL
def test_edited_position_zero_is_refused_not_a_final_looking_page(tmp_path: Path, shell: str) -> None:
    root = runtime(tmp_path, 60)
    token = ok(inv(shell, root, "-PageSize", "25", "-Resumable"))["continuation_token"]
    refused(inv(shell, root, "-PageSize", "25", "-ContinuationToken", at(token, 0)), "not a page boundary")


@ALL
def test_edited_position_on_a_foreign_row_is_refused(tmp_path: Path, shell: str) -> None:
    root = runtime(tmp_path, 40, foreign=True)
    token = ok(inv(shell, root, "-PageSize", "10", "-Resumable"))["continuation_token"]
    assert int(token.split(".")[7]) % 2 == 0
    refused(inv(shell, root, "-PageSize", "10", "-ContinuationToken", at(token, 41)), "not a page boundary")


@ALL
def test_edited_position_on_an_own_row_excluded_by_the_query_filter_is_refused(tmp_path: Path, shell: str) -> None:
    root = runtime(tmp_path, 40, sessions=True)                                  # own s1 at even, own s2 at odd positions
    token = ok(inv(shell, root, "-PageSize", "5", "-SessionId", "s1", "-Resumable"))["continuation_token"]
    refused(inv(shell, root, "-PageSize", "5", "-SessionId", "s1", "-ContinuationToken", at(token, 21)), "not a page boundary")


@ALL
def test_disclosed_limit_an_edited_real_boundary_only_moves_the_callers_own_page(tmp_path: Path, shell: str) -> None:
    root = runtime(tmp_path, 60)
    token = ok(inv(shell, root, "-PageSize", "25", "-Resumable"))["continuation_token"]
    page = ok(inv(shell, root, "-PageSize", "25", "-ContinuationToken", at(token, 10)))   # unauthenticated handle
    assert ids(page) == [f"req-codex-{i:04d}" for i in range(9, -1, -1)] and page["authority_effect"] == "none"


@ALL
def test_last_legit_page_with_one_older_match_is_accepted(tmp_path: Path, shell: str) -> None:
    root = runtime(tmp_path, 26)
    first = ok(inv(shell, root, "-PageSize", "25", "-Resumable"))
    last = ok(inv(shell, root, "-PageSize", "25", "-ContinuationToken", first["continuation_token"]))
    assert ids(last) == ["req-codex-0000"] and last["truncated"] is False and last["continuation_token"] is None


@ALL
def test_two_pages_with_an_append_between_cover_the_frozen_prefix_once_and_observe_the_append(tmp_path: Path, shell: str) -> None:
    root = runtime(tmp_path, 40, foreign=True)
    first = ok(inv(shell, root, "-PageSize", "25", "-Resumable"))
    assert set(first) == V3_KEYS and first["schema"] == "wd.request-inventory.v2"
    assert first["appended_after_freeze_observed"] is False
    append(root, row(900))
    second = ok(inv(shell, root, "-PageSize", "25", "-ContinuationToken", first["continuation_token"]))
    assert set(second) == V3_KEYS and second["appended_after_freeze_observed"] is True
    assert (second["snapshot_bytes"], second["request_count"]) == (first["snapshot_bytes"], first["request_count"])
    assert ids(first) + ids(second) == [f"req-codex-{i:04d}" for i in range(39, -1, -1)]
    assert second["truncated"] is False and second["continuation_token"] is None and second["next_cursor"] is None
    assert all(item["answer_state"] == "not_evaluated" for item in second["requests"])
    assert ids(ok(inv(shell, root, "-PageSize", "25", "-Resumable")))[0] == "req-codex-0900"   # next fresh walk


@ALL
def test_quiet_frozen_page_observes_no_append(tmp_path: Path, shell: str) -> None:
    root = runtime(tmp_path, 30)
    first = ok(inv(shell, root, "-PageSize", "20", "-Resumable"))
    page = ok(inv(shell, root, "-PageSize", "20", "-ContinuationToken", first["continuation_token"]))
    assert page["appended_after_freeze_observed"] is False                     # "none seen", not "complete"


@ALL
@pytest.mark.parametrize("mutation", ["same_length_rewrite", "truncate_into_prefix", "replace_same_bytes", "generation_sidecar_appears"])
def test_prefix_identity_and_generation_mutations_still_refuse(tmp_path: Path, shell: str, mutation: str) -> None:
    root = runtime(tmp_path, 60)
    log = root / "shared" / "events.jsonl"
    token = ok(inv(shell, root, "-PageSize", "25", "-Resumable"))["continuation_token"]
    data = log.read_bytes()
    if mutation == "same_length_rewrite":
        index = data.index(b"req-codex-0007")
        log.write_bytes(data[:index] + b"req-codex-0X07" + data[index + 14:])
    elif mutation == "truncate_into_prefix":
        log.write_bytes(data[: data.index(b"\n", len(data) // 3) + 1])
    elif mutation == "replace_same_bytes":
        (log.parent / "events.new").write_bytes(data)
        os.replace(log.parent / "events.new", log)
    else:
        (log.parent / "events.generation.json").write_text('{"generation":"g2"}', encoding="utf-8")
    result = inv(shell, root, "-PageSize", "25", "-ContinuationToken", token)
    assert result.returncode != 0 and not result.stdout.strip(), result.stdout[:300]


@ALL
@pytest.mark.parametrize("change", [("-PageSize", "24"), ("-TaskId", "fx/task-0001"), ("-SessionId", "s1")])
def test_a_changed_query_or_page_size_still_refuses(tmp_path: Path, shell: str, change: tuple[str, ...]) -> None:
    root = runtime(tmp_path, 60)
    token = ok(inv(shell, root, "-PageSize", "25", "-Resumable"))["continuation_token"]
    args = ["-ContinuationToken", token, *change] + ([] if change[0] == "-PageSize" else ["-PageSize", "25"])
    refused(inv(shell, root, *args), "does not match this query")


@ALL
def test_default_v2_shape_is_unchanged_and_its_cursor_still_works_then_invalidates(tmp_path: Path, shell: str) -> None:
    root = runtime(tmp_path, 30)
    first = ok(inv(shell, root, "-PageSize", "20"))
    assert set(first) == V2_KEYS and first["next_cursor"].count(":") == 2      # no v3 or observation field
    second = ok(inv(shell, root, "-PageSize", "20", "-Cursor", first["next_cursor"]))   # positive legacy control
    assert ids(first) + ids(second) == [f"req-codex-{i:04d}" for i in range(29, -1, -1)]
    append(root, row(700))
    refused(inv(shell, root, "-PageSize", "20", "-Cursor", first["next_cursor"]), "does not match this complete snapshot")
