"""Exact historical request-ID conflict pairs in the default request inventory.

Three own codex-lead-1 request IDs were each published twice with different immutable content
(append-only log, so the conflict is permanent). The default inventory may set exactly those six
rows aside, and only when every row matches its pinned full-event reference AND its stored and
recomputed request digest. Everything else keeps failing closed.

No historical message or payload is committed: the tests run a fixture copy of the real getter in
which only the pinned reference and digest literals are replaced by the values of synthetic rows.
Hashing, parsing, the reader and the inventory logic are the real ones.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from test_bridge_request_inventory import BIN, INVENTORY, SHELLS, _run, _write

LEAD = "codex-lead-1"
# request ID -> ((event ref, request digest) of the first row, (event ref, request digest) of the second row),
# in log order. Refs are the compact-view event_id (sha256 of the canonical sorted-key UTF-8 JSON of the
# whole event); digests are the stored request_digest, equal to the recomputed request-content digest.
PINS = {
    "lead-mind-shared-index-fable-20261031-v2": (
        ("24ad958a518d4d0aaac3ffbc8bf30c9fc14e96b9f421bafde2b2a346f7c45db5",
         "3dd19b718f1829753c8cd86aee754eddc2ca8f10924aa590446c91a919123058"),
        ("112488f88756d98430d7eb71c76fdccdec356b063ed10f29954c855230ea7e21",
         "43e076ce3d49dd07ae4b9f76c4828878508e87211fac02430d097bbc0a5b3c45"),
    ),
    "lead-postboot-mind-capacity-lens-fix-20261106-v1": (
        ("2bac460a4dee943917fc306b1a06a130e9e9a0f0807c1ab6e6e30eca11935f3b",
         "2063b0bc055fc85cbd6c76e8b378e4a4fcbd8c19a50e88501776a2179b331433"),
        ("361260e109e587ba24a53bf4773f05560c9b27a83b3126d658ac8c02d037141d",
         "fccb03c43168abda8170438e172edc0b7e7db22d9b2c83efde7b09dc576ed76d"),
    ),
    "lead-mind-cell-age-review-rco2-20261110-v1": (
        ("30f6a39fadca963de7e4497900c78df6226f6ad308b27cb504512fe92758e8d7",
         "3d3659f54b1a1a06ff2f228ff797529a51c9a2706b869d7c940bc564d5ab3a35"),
        ("0ce713e5d2a073fe5523f333f3d04d8f2cafad5e77d863d21bf5db83020071ca",
         "f5c3e3ab2f3242dd83adef1d9f866dc28ebd18ebd7a0e6bbbde9bc98c64449d7"),
    ),
}
IDS = list(PINS)
CONTENT = ("request_id", "agent", "agent_uuid", "session_id", "run_id", "task_id", "to", "type",
           "status", "message", "payload", "expected_responders")
SECRET_TEXT = "synthetic-pinned-row-text"
pytestmark = pytest.mark.skipif(not SHELLS, reason="PowerShell is required")
shell_ids = {"ids": lambda value: Path(value).stem}


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def ref(event: dict) -> str:
    return hashlib.sha256(canonical(event).encode("utf-8")).hexdigest()


def digest(event: dict) -> str:
    # The existing contract serializer escapes these four characters on both hosts; the synthetic
    # rows avoid them, so this mirrors Get-BridgeRequestContent for them.
    text = canonical({key: event.get(key) for key in CONTENT})
    for char in "'<>&":
        text = text.replace(char, chr(92) + "u%04x" % ord(char))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def seal(event: dict) -> dict:
    event["request_digest"] = digest(event)
    return event


def request(request_id: str, index: int, second: bool, *, agent: str = LEAD) -> dict:
    return seal({
        "ts_utc": f"2026-11-0{index + 1}T1{index}:0{int(second)}:00.1234567Z",
        "agent": agent, "type": "wake_request", "status": "requested", "to": "fable-5",
        "task_id": f"synthetic-task-{index}", "request_id": request_id,
        "agent_uuid": "synthetic-lead-uuid", "session_id": "synthetic-session", "run_id": "synthetic-session",
        "pid": 1000 + index, "cwd": "synthetic", "paths": [], "write_scope": [],
        "message": f"{SECRET_TEXT} {index} {'second' if second else 'first'}",
        "payload": {"scope": "synthetic", "revision": 2 if second else 1, "flags": {"ok": True, "none": None}},
        "expected_responders": {"fable-5": {"agent_uuid": "synthetic-fable", "session_id": "synthetic",
                                            "run_id": "synthetic"}},
    })


def pairs(ids=IDS) -> dict[str, list[dict]]:
    return {rid: [request(rid, IDS.index(rid), False), request(rid, IDS.index(rid), True)] for rid in ids}


def flat(by_id: dict[str, list[dict]]) -> list[dict]:
    # Log order: all first rows, then all second rows (the real pairs interleave with other rows too).
    return [rows[0] for rows in by_id.values() if rows] + [row for rows in by_id.values() for row in rows[1:]]


def getter_copy(tmp_path: Path, anchors: dict[str, list[dict]]) -> Path:
    scripts = tmp_path / "bin"
    shutil.copytree(BIN, scripts)
    text = INVENTORY.read_text(encoding="utf-8-sig")
    for rid, rows in anchors.items():
        for (real_ref, real_digest), row in zip(PINS[rid], rows):
            assert text.count(real_ref) == 1 and text.count(real_digest) == 1
            text = text.replace(real_ref, ref(row)).replace(real_digest, row["request_digest"])
    target = scripts / INVENTORY.name
    target.write_text(text, encoding="utf-8")
    return target


def invoke(tmp_path: Path, shell: str, rows: list[dict], *args: str, anchors=None,
           agent: str = LEAD) -> subprocess.CompletedProcess[str]:
    target = getter_copy(tmp_path, anchors or pairs())
    runtime = tmp_path / "runtime"
    log = _write(runtime, rows, raw_utf8=True)
    before = log.read_bytes()
    result = _run(shell, target, runtime, "-Agent", agent, "-NoCache", *args)
    assert log.read_bytes() == before
    return result


def refused(result: subprocess.CompletedProcess[str]) -> bool:
    return result.returncode != 0 and result.stdout.strip() == ""


def test_getter_pins_exactly_the_reviewed_pairs() -> None:
    text = INVENTORY.read_text(encoding="utf-8-sig")
    hexes = re.findall(r"'([0-9a-f]{64})'", text)
    expected = [value for rid in IDS for row in PINS[rid] for value in row]
    assert hexes == expected
    for rid in IDS:
        assert text.count(f"'{rid}'") == 1


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
def test_reference_serializer_matches_canonical_json(tmp_path: Path, shell: str) -> None:
    event = {"empty": [], "nested": {"number": 7, "flag": True, "large": 9007199254740993},
             "text": "ääkkönen\n\t\"\\ \u0001", "nothing": None, "date": "2026-11-10T20:42:46.6659706Z", "list": [1, "two", {"b": 1, "a": 2}], "single": ["only"], "single_object": [{"k": []}],
             "Upper": 1, "lower": 2, "_under": 3, "html": "'<>&"}
    source = tmp_path / "event.json"
    source.write_text(canonical(event), encoding="utf-8")
    inventory = str(INVENTORY).replace("'", "''")
    file = str(source).replace("'", "''")
    script = f"""
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile('{inventory}',[ref]$tokens,[ref]$errors)
if($errors.Count) {{ throw 'parse failure' }}
$fn=$ast.Find({{param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'ConvertTo-InventoryReferenceJson'}},$true)
if($null -eq $fn) {{ throw 'serializer missing' }}
. ([scriptblock]::Create($fn.Extent.Text))
# Parsed exactly as the canonical log reader parses a row (Read-BridgeLogSnapshotDelta).
$jsonArguments=@{{ErrorAction='Stop'}}
if ((Get-Command ConvertFrom-Json).Parameters.ContainsKey('DateKind')) {{ $jsonArguments.DateKind='String' }}
$value=[IO.File]::ReadAllText('{file}',[Text.Encoding]::UTF8) | ConvertFrom-Json @jsonArguments
$json=ConvertTo-InventoryReferenceJson $value
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert base64.b64decode(result.stdout.strip()).decode("utf-8") == canonical(event)


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
def test_exact_three_pairs_are_historical_and_never_requests(tmp_path: Path, shell: str) -> None:
    by_id = pairs()
    result = invoke(tmp_path, shell, flat(by_id))
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["schema"] == "wd.request-inventory.v2"
    assert data["request_count"] == data["matched_count"] == data["returned_count"] == 0
    assert data["requests"] == []
    assert SECRET_TEXT not in result.stdout
    conflicts = data["historical_conflicts"]
    assert [item["request_id"] for item in conflicts] == IDS
    for item in conflicts:
        rows = by_id[item["request_id"]]
        assert item["kind"] == "known_historical_immutable_id_conflict"
        assert item["event_refs"] == [ref(row) for row in rows]
        assert item["request_digests"] == [row["request_digest"] for row in rows]
        assert item["indexed_positions"] == [IDS.index(item["request_id"]), len(IDS) + IDS.index(item["request_id"])]
        assert item["answer_state"] == "not_evaluated" and item["authority_effect"] == "none"


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
def test_absent_pairs_add_no_record_and_a_present_one_is_still_exact(tmp_path: Path, shell: str) -> None:
    by_id = pairs([IDS[1]])
    result = invoke(tmp_path, shell, flat(by_id))
    assert result.returncode == 0, result.stderr
    assert [item["request_id"] for item in json.loads(result.stdout)["historical_conflicts"]] == [IDS[1]]
    clean = invoke(tmp_path / "clean", shell, [request("unrelated-id", 0, False)])
    assert clean.returncode == 0, clean.stderr
    assert json.loads(clean.stdout)["historical_conflicts"] == []


VARIANTS = ("third_row", "identical_reappend", "one_row", "foreign_author", "foreign_extra", "unattributed_extra",
            "timestamp", "message", "payload", "stored_digest", "order_swapped")


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
@pytest.mark.parametrize("pair_index", range(len(IDS)))
@pytest.mark.parametrize("change", VARIANTS)
def test_every_unpinned_variant_fails_closed(tmp_path: Path, shell: str, pair_index: int, change: str) -> None:
    by_id = pairs()
    rid = IDS[pair_index]
    rows = by_id[rid]
    extra: list[dict] = []
    if change == "third_row":
        extra.append(seal(dict(deepcopy(rows[0]), message=f"{SECRET_TEXT} new third request")))
    elif change == "identical_reappend":
        extra.append(deepcopy(rows[1]))
    elif change == "one_row":
        rows.pop()
    elif change == "foreign_author":
        rows[1] = seal(dict(rows[1], agent="codex-tools-1"))
    elif change == "foreign_extra":
        # Both exact Lead rows stay; another agent publishes a third request row with the pinned ID
        # (Lead review of d5866f13: this was accepted and silently omitted).
        extra.append(request(rid, pair_index, False, agent="codex-tools-1"))
    elif change == "unattributed_extra":
        extra.append(seal(dict(deepcopy(rows[0]), agent=7, message=f"{SECRET_TEXT} unattributed")))
    elif change == "timestamp":
        rows[0]["ts_utc"] = "2026-11-01T10:00:00.1234568Z"
    elif change == "message":
        rows[0] = seal(dict(rows[0], message=f"{SECRET_TEXT} modified"))
    elif change == "payload":
        rows[1]["payload"]["scope"] = "changed"
    elif change == "stored_digest":
        rows[0]["request_digest"] = "0" * 64
    log = flat(by_id) + extra
    if change == "order_swapped":
        # Both exact rows, but the second pinned row first: the pins bind log order too.
        first, second = log.index(rows[0]), log.index(rows[1])
        log[first], log[second] = log[second], log[first]
    result = invoke(tmp_path, shell, log, anchors=pairs())
    assert refused(result), result.stdout
    assert "historical pair" in result.stderr or "Conflicting content" in result.stderr


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
@pytest.mark.parametrize("where", ("before", "between", "after"))
def test_foreign_row_with_a_pinned_id_refuses_wherever_it_is(tmp_path: Path, shell: str, where: str) -> None:
    by_id = pairs()
    rid = IDS[2]
    first, second = by_id[rid]
    foreign = request(rid, 2, True, agent="codex-tools-1")
    others = [row for key, rows in by_id.items() if key != rid for row in rows]
    order = {"before": [foreign, first, second], "between": [first, foreign, second],
             "after": [first, second, foreign]}[where]
    result = invoke(tmp_path, shell, others + order)
    assert refused(result) and "another author" in result.stderr


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
def test_pinned_id_on_another_authors_row_refuses_even_without_the_pair(tmp_path: Path, shell: str) -> None:
    rows = [request(IDS[1], 1, False, agent="codex-tools-1"), request("unrelated-current-id", 0, False)]
    as_lead = invoke(tmp_path / "lead", shell, rows)
    assert refused(as_lead) and "another author" in as_lead.stderr
    as_tools = invoke(tmp_path / "tools", shell, rows, agent="codex-tools-1")
    assert as_tools.returncode == 0, as_tools.stderr
    assert [row["request_id"] for row in json.loads(as_tools.stdout)["requests"]] == [IDS[1]]
    diagnostic = invoke(tmp_path / "diagnostic", shell, rows, "-DiagnosticPartial")
    assert diagnostic.returncode == 0, diagnostic.stderr
    assert json.loads(diagnostic.stdout)["conflict_count"] == 0


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
def test_matching_event_ref_does_not_replace_content_digest_verification(tmp_path: Path, shell: str) -> None:
    by_id = pairs()
    stale = by_id[IDS[0]][0]
    stale["message"] = f"{SECRET_TEXT} different content under a stale stored digest"
    result = invoke(tmp_path, shell, flat(by_id), anchors=by_id)
    assert refused(result)


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
def test_unrelated_conflict_still_refuses_and_valid_request_is_listed(tmp_path: Path, shell: str) -> None:
    by_id = pairs()
    good = request("unrelated-current-id", 0, False)
    result = invoke(tmp_path, shell, flat(by_id) + [good])
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert [row["request_id"] for row in data["requests"]] == ["unrelated-current-id"]
    assert data["request_count"] == 1 and len(data["historical_conflicts"]) == 3
    bad = seal(dict(deepcopy(good), message="unrelated conflicting content"))
    conflict = invoke(tmp_path / "conflict", shell, flat(pairs()) + [good, bad])
    assert refused(conflict) and "unrelated-current-id" in conflict.stderr


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
def test_diagnostic_partial_still_lists_all_pairs_as_conflicts(tmp_path: Path, shell: str) -> None:
    result = invoke(tmp_path, shell, flat(pairs()) + [request("unrelated-current-id", 0, False)],
                    "-DiagnosticPartial")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["schema"] == "wd.request-inventory-diagnostic.v1" and data["complete"] is False
    assert data["status"] == "partial_unknown" and data["conflict_count"] == 3
    assert [item["top_level_request_id"] for item in data["conflicts"]] == IDS
    assert [row["request_id"] for row in data["requests"]] == ["unrelated-current-id"]
    assert "historical_conflicts" not in data


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
def test_filters_never_surface_a_pinned_row(tmp_path: Path, shell: str) -> None:
    by_id = pairs()
    log = flat(by_id) + [request("unrelated-current-id", 0, False)]
    first = by_id[IDS[2]][0]
    by_request = invoke(tmp_path / "rid", shell, log, "-RequestId", IDS[2])
    assert by_request.returncode == 0, by_request.stderr
    data = json.loads(by_request.stdout)
    assert data["matched_count"] == 0 and data["requests"] == [] and len(data["historical_conflicts"]) == 3
    include = invoke(tmp_path / "include", shell, log, "-RequestId", IDS[2], "-IncludeRequest")
    assert refused(include) and SECRET_TEXT not in include.stderr
    by_task = invoke(tmp_path / "task", shell, log, "-TaskId", first["task_id"], "-TsUtc", first["ts_utc"])
    assert by_task.returncode == 0, by_task.stderr
    assert json.loads(by_task.stdout)["matched_count"] == 0
    by_session = invoke(tmp_path / "session", shell, log, "-SessionId", "synthetic-session")
    assert by_session.returncode == 0, by_session.stderr
    data = json.loads(by_session.stdout)
    assert [row["request_id"] for row in data["requests"]] == ["unrelated-current-id"]
    assert len(data["historical_conflicts"]) == 3 and SECRET_TEXT not in by_session.stdout


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
def test_pairs_count_only_for_the_lead_as_requester(tmp_path: Path, shell: str) -> None:
    by_id = pairs()
    other = invoke(tmp_path / "other", shell, flat(by_id), agent="fable-5")
    assert other.returncode == 0, other.stderr
    data = json.loads(other.stdout)
    assert data["requests"] == [] and data["historical_conflicts"] == []
    tools = {rid: [seal(dict(row, agent="codex-tools-1")) for row in rows] for rid, rows in by_id.items()}
    as_tools = invoke(tmp_path / "tools", shell, flat(tools), agent="codex-tools-1")
    assert refused(as_tools) and "Conflicting content" in as_tools.stderr
