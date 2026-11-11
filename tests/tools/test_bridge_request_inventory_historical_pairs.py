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


RICH_EVENT = {"empty": [], "nested": {"number": 7, "flag": True, "large": 9007199254740993},
             "text": "ääkkönen\n\t\"\\ \u0001", "nothing": None, "date": "2026-11-10T20:42:46.6659706Z", "list": [1, "two", {"b": 1, "a": 2}], "single": ["only"], "single_object": [{"k": []}],
             "empty_object": {}, "nested_empty": {"inner": {}, "list": [{}]},
             "Upper": 1, "lower": 2, "_under": 3, "html": "'<>&"}


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
@pytest.mark.parametrize("event", (RICH_EVENT, {}), ids=("rich", "empty_top_level"))
def test_reference_serializer_matches_canonical_json(tmp_path: Path, shell: str, event: dict) -> None:
    # Run as the getter runs it: StrictMode Latest and ErrorAction Stop (an empty object once threw here).
    source = tmp_path / "event.json"
    source.write_text(canonical(event), encoding="utf-8")
    inventory = str(INVENTORY).replace("'", "''")
    file = str(source).replace("'", "''")
    script = f"""
Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
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
@pytest.mark.parametrize("pair_index", range(len(IDS)))
@pytest.mark.parametrize("row_index", (0, 1))
@pytest.mark.parametrize("where", ("payload", "top_level"))
def test_set_aside_row_with_a_p1_lifecycle_marker_refuses(tmp_path: Path, shell: str, pair_index: int,
                                                          row_index: int, where: str) -> None:
    # The pins match exactly (anchors are these rows), so only the P1 rule can refuse: a set-aside row
    # never enters successor scanning, so a P1 row may not be set aside at all.
    by_id = pairs()
    row = by_id[IDS[pair_index]][row_index]
    if where == "payload":
        row["payload"]["request_lifecycle"] = "wd.request-lifecycle.p1"
    else:
        row["request_lifecycle"] = "wd.request-lifecycle.p1"
    seal(row)
    result = invoke(tmp_path, shell, flat(by_id), anchors=by_id)
    assert refused(result) and "P1" in result.stderr


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
@pytest.mark.parametrize("where", ("payload", "top_level"))
def test_inventoried_row_naming_a_set_aside_id_as_p1_predecessor_refuses(tmp_path: Path, shell: str,
                                                                           where: str) -> None:
    by_id = pairs()
    successor = request("unrelated-successor-id", 0, True)
    link = {"request_lifecycle": "wd.request-lifecycle.p1", "task_revision": 2,
            "previous_request_id": IDS[0], "previous_request_digest": by_id[IDS[0]][1]["request_digest"],
            "action_required": True, "fyi": False}
    if where == "payload":
        successor["payload"].update(link)
    else:
        successor.update(link)
    seal(successor)
    result = invoke(tmp_path, shell, flat(by_id) + [successor])
    assert refused(result) and "P1" in result.stderr
    legacy = request("unrelated-legacy-id", 0, True)
    legacy["payload"]["supersedes_request"] = IDS[0]   # legacy wording, not P1 lineage: still listed
    seal(legacy)
    listed = invoke(tmp_path / "legacy", shell, flat(pairs()) + [legacy])
    assert listed.returncode == 0, listed.stderr
    assert [row["request_id"] for row in json.loads(listed.stdout)["requests"]] == ["unrelated-legacy-id"]


P1_LINK = {"request_lifecycle": "wd.request-lifecycle.p1", "task_revision": 2, "action_required": True,
           "fyi": False}


def linked(request_id: str, where: str, fields: dict) -> dict:
    row = request(request_id, 0, True)
    (row["payload"] if where == "payload" else row).update(fields)
    return seal(row)


def listed_ids(result: subprocess.CompletedProcess[str]) -> list[str]:
    assert result.returncode == 0, result.stderr
    return [row["request_id"] for row in json.loads(result.stdout)["requests"]]


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
@pytest.mark.parametrize("include_pairs", (True, False), ids=("pairs_present", "pairs_absent"))
@pytest.mark.parametrize("where", ("payload", "top_level"))
@pytest.mark.parametrize("marker", ("absent", "explicit_null"))
def test_legacy_previous_request_id_naming_a_pinned_id_stays_inventoried(tmp_path: Path, shell: str,
                                                                          include_pairs: bool, where: str,
                                                                          marker: str) -> None:
    # No lifecycle marker (or an explicit null one, which is not P1 either): the previous request ID field is
    # legacy metadata, so the row is an ordinary request, with or without the set-aside pair in the log.
    fields = {"previous_request_id": IDS[0]}
    if marker == "explicit_null":
        fields["request_lifecycle"] = None
    legacy = linked("ordinary-legacy-id", where, fields)
    by_id = pairs()
    result = invoke(tmp_path, shell, (flat(by_id) if include_pairs else []) + [legacy], anchors=by_id)
    assert listed_ids(result) == ["ordinary-legacy-id"]
    historical = json.loads(result.stdout)["historical_conflicts"]
    assert [row["request_id"] for row in historical] == (IDS if include_pairs else [])
    assert "request_lifecycle" not in json.loads(result.stdout)["requests"][0]


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
@pytest.mark.parametrize("where", ("payload", "top_level"))
def test_p1_predecessor_naming_a_pinned_id_that_is_not_in_the_log_is_listed(tmp_path: Path, shell: str,
                                                                             where: str) -> None:
    # Nothing was set aside, so nothing is hidden: the P1 successor is listed as an ordinary P1 request.
    successor = linked("unrelated-successor-id", where, {**P1_LINK, "previous_request_id": IDS[0]})
    result = invoke(tmp_path, shell, [successor])
    assert listed_ids(result) == ["unrelated-successor-id"]
    assert json.loads(result.stdout)["requests"][0]["request_lifecycle"] == "wd.request-lifecycle.p1"
    assert json.loads(result.stdout)["historical_conflicts"] == []


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
@pytest.mark.parametrize("other", (1, 2), ids=("other_pair_1", "other_pair_2"))
def test_p1_predecessor_onto_a_pinned_id_whose_pair_is_absent_while_others_are_set_aside(
        tmp_path: Path, shell: str, other: int) -> None:
    # Only an ACTUALLY set-aside ID refuses: IDS[0] is absent here, the other pairs are set aside.
    kept = pairs([IDS[i] for i in range(len(IDS)) if i != 0])
    successor = linked("unrelated-successor-id", "payload", {**P1_LINK, "previous_request_id": IDS[0]})
    result = invoke(tmp_path, shell, flat(kept) + [successor], anchors=pairs())
    assert listed_ids(result) == ["unrelated-successor-id"]
    refused_case = linked("unrelated-successor-id", "payload", {**P1_LINK, "previous_request_id": IDS[other]})
    result = invoke(tmp_path / "set_aside", shell, flat(kept) + [refused_case], anchors=pairs())
    assert refused(result) and "P1" in result.stderr   # pwsh 7 wraps the long message


MARKER_SHAPES = {"unknown_version": "wd.request-lifecycle.p2", "case_variant": "WD.REQUEST-LIFECYCLE.P1",
                 "empty_string": "", "number": 1, "boolean": True, "object": {"v": "wd.request-lifecycle.p1"}}


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
@pytest.mark.parametrize("include_pairs", (True, False), ids=("pairs_present", "pairs_absent"))
@pytest.mark.parametrize("where", ("payload", "top_level"))
@pytest.mark.parametrize("shape", [*MARKER_SHAPES, "conflicting_top_and_payload"])
def test_unknown_or_malformed_marker_with_a_pinned_previous_id_still_refuses(tmp_path: Path, shell: str,
                                                                            include_pairs: bool, where: str,
                                                                            shape: str) -> None:
    fields = {**P1_LINK, "previous_request_id": IDS[0]}
    if shape == "conflicting_top_and_payload":
        row = linked("odd-marker-id", where, fields)
        (row if where == "payload" else row["payload"])["request_lifecycle"] = "wd.request-lifecycle.p2"
        seal(row)
    else:
        row = linked("odd-marker-id", where, {**fields, "request_lifecycle": MARKER_SHAPES[shape]})
    by_id = pairs()
    result = invoke(tmp_path, shell, (flat(by_id) if include_pairs else []) + [row], anchors=by_id)
    assert refused(result) and "P1 HOLD" in result.stderr


def chosen(request_id: str = "ordinary-request") -> dict:
    row = request(request_id, 1, False)
    row["task_id"] = "chosen-task"
    return seal(row)


# Every output filter and pagination runs after the guard: each selects only the chosen row (newest, so a page of
# one holds it alone), never the row that must refuse.
FILTERS = {"unfiltered": (), "task": ("-TaskId", "chosen-task"), "request": ("-RequestId", "ordinary-request"),
           "ts": ("-TsUtc", "2026-11-02T11:00:00.1234567Z"), "page_of_one": ("-PageSize", "1")}


PREDECESSOR_SHAPES = ("conflicting_top_and_payload", "number", "object", "invalid_characters", "too_long")


def predecessor_row(request_id: str, shape: str, *, p1: bool) -> dict:
    fields = {**P1_LINK} if p1 else {}
    if shape == "conflicting_top_and_payload":
        row = linked(request_id, "payload", {**fields, "previous_request_id": IDS[0]})
        row["previous_request_id"] = "different-predecessor"
        return seal(row)
    value = {"number": 7, "object": {"id": IDS[0]}, "invalid_characters": IDS[0] + " x",
             "too_long": "a" * 129}[shape]
    return linked(request_id, "payload", {**fields, "previous_request_id": value})


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
@pytest.mark.parametrize("selection", FILTERS)
@pytest.mark.parametrize("shape", PREDECESSOR_SHAPES)
def test_p1_row_with_a_conflicting_or_invalid_predecessor_refuses_before_filters(tmp_path: Path, shell: str,
                                                                                selection: str,
                                                                                shape: str) -> None:
    by_id = pairs()
    args = FILTERS[selection]
    rows = flat(by_id) + [predecessor_row("bad-p1-successor", shape, p1=True), chosen()]
    result = invoke(tmp_path, shell, rows, *args, anchors=by_id)
    assert refused(result) and "P1 HOLD" in result.stderr


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
@pytest.mark.parametrize("selection", FILTERS)
@pytest.mark.parametrize("previous", ("pinned_set_aside", "conflicting_naming_set_aside", "invalid"))
def test_unknown_marker_on_a_row_whose_predecessor_touches_a_set_aside_id_refuses_before_filters(
        tmp_path: Path, shell: str, selection: str, previous: str) -> None:
    # Such a row cannot be classified, so its lineage onto the set-aside row cannot be evaluated.
    fields = {**P1_LINK, "request_lifecycle": "wd.request-lifecycle.p2"}
    if previous == "pinned_set_aside":
        odd = linked("odd-marker-id", "payload", {**fields, "previous_request_id": IDS[0]})
    elif previous == "conflicting_naming_set_aside":
        odd = linked("odd-marker-id", "payload", {**fields, "previous_request_id": IDS[0]})
        odd["previous_request_id"] = "different-predecessor"
        seal(odd)
    else:
        odd = linked("odd-marker-id", "payload", {**fields, "previous_request_id": 7})
    by_id = pairs()
    result = invoke(tmp_path, shell, flat(by_id) + [odd, chosen()], *FILTERS[selection], anchors=by_id)
    assert refused(result) and "P1 HOLD" in result.stderr


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
@pytest.mark.parametrize("selection", FILTERS)
@pytest.mark.parametrize("previous", ("none", "unrelated_valid"))
def test_unrelated_unknown_marker_keeps_the_existing_selection_behaviour(tmp_path: Path, shell: str,
                                                                         selection: str, previous: str) -> None:
    # Characterization (Lead 01:20:56Z and 01:21:53Z): a row with an unknown marker whose predecessor field cannot
    # touch a set-aside ID refuses only when it is listed, exactly as before; filters selecting another row list it.
    fields = {**P1_LINK, "request_lifecycle": "wd.request-lifecycle.p2"}
    if previous == "unrelated_valid":
        fields["previous_request_id"] = "some-absent-id"
    odd = linked("odd-marker-id", "payload", fields)
    by_id = pairs()
    result = invoke(tmp_path, shell, flat(by_id) + [odd, chosen()], *FILTERS[selection], anchors=by_id)
    if selection == "unfiltered":
        assert refused(result) and "P1 HOLD" in result.stderr
    else:
        assert listed_ids(result) == ["ordinary-request"]


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
@pytest.mark.parametrize("selection", FILTERS)
def test_p1_lineage_onto_a_set_aside_id_refuses_before_filters(tmp_path: Path, shell: str, selection: str) -> None:
    by_id = pairs()
    successor = linked("unrelated-successor-id", "payload", {**P1_LINK, "previous_request_id": IDS[1]})
    args = FILTERS[selection]
    result = invoke(tmp_path, shell, flat(by_id) + [successor, chosen()], *args, anchors=by_id)
    assert refused(result) and "P1" in result.stderr


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
@pytest.mark.parametrize("filtered", (False, True), ids=("unfiltered", "task_filter"))
@pytest.mark.parametrize("shape", PREDECESSOR_SHAPES)
def test_non_p1_row_with_a_conflicting_or_invalid_previous_field_stays_legacy(tmp_path: Path, shell: str,
                                                                             filtered: bool, shape: str) -> None:
    # No lifecycle marker: the field is legacy metadata whatever its shape, so the row is listed as before.
    by_id = pairs()
    legacy = predecessor_row("ordinary-legacy-id", shape, p1=False)
    legacy["task_id"] = "chosen-task"
    seal(legacy)
    args = ("-TaskId", "chosen-task") if filtered else ()
    result = invoke(tmp_path, shell, flat(by_id) + [legacy], *args, anchors=by_id)
    assert listed_ids(result) == ["ordinary-legacy-id"]


CASE_VARIANTS = {"upper": str.upper, "first_char_upper": lambda rid: rid[0].upper() + rid[1:],
                 "version_letter_upper": lambda rid: rid[:-2] + rid[-2].upper() + rid[-1]}   # ...-v2 -> ...-V2


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
@pytest.mark.parametrize("include_pairs", (True, False), ids=("pairs_present", "pairs_absent"))
@pytest.mark.parametrize("variant", CASE_VARIANTS)
@pytest.mark.parametrize("pair_index", range(len(IDS)))
def test_own_case_variant_of_a_pinned_id_is_an_ordinary_request(tmp_path: Path, shell: str, include_pairs: bool,
                                                                variant: str, pair_index: int) -> None:
    # Request IDs are ordinal: a case variant never enters the pinned bucket (which would make a third row).
    other = request(CASE_VARIANTS[variant](IDS[pair_index]), pair_index, False)
    assert other["request_id"] != IDS[pair_index]
    by_id = pairs()
    result = invoke(tmp_path, shell, (flat(by_id) if include_pairs else []) + [other], anchors=by_id)
    assert listed_ids(result) == [other["request_id"]]
    historical = json.loads(result.stdout)["historical_conflicts"]
    assert [row["request_id"] for row in historical] == (IDS if include_pairs else [])


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
@pytest.mark.parametrize("include_pairs", (True, False), ids=("pairs_present", "pairs_absent"))
@pytest.mark.parametrize("author", ("foreign", "unattributable"))
def test_foreign_case_variant_of_a_pinned_id_does_not_count_as_a_pinned_row(tmp_path: Path, shell: str,
                                                                            include_pairs: bool,
                                                                            author: str) -> None:
    # Only the EXACT pinned ID on another or unattributable author row refuses; a case variant is another ID.
    other = request(IDS[0].upper(), 0, False, agent="codex-tools-1")
    if author == "unattributable":
        other["agent"] = 7
        seal(other)
    by_id = pairs()
    result = invoke(tmp_path, shell, (flat(by_id) if include_pairs else []) + [other], anchors=by_id)
    assert listed_ids(result) == []
    historical = json.loads(result.stdout)["historical_conflicts"]
    assert [row["request_id"] for row in historical] == (IDS if include_pairs else [])
    exact = request(IDS[0], 0, False, agent="codex-tools-1")
    if author == "unattributable":
        exact["agent"] = 7
        seal(exact)
    result = invoke(tmp_path / "exact", shell, (flat(pairs()) if include_pairs else []) + [exact], anchors=by_id)
    assert refused(result) and "another author" in result.stderr


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
@pytest.mark.parametrize("selection", FILTERS)
def test_selected_row_is_returned_when_nothing_refuses(tmp_path: Path, shell: str, selection: str) -> None:
    # Control for the filter cases: the same filters return exactly the chosen row from a clean log.
    by_id = pairs()
    other = linked("unrelated-successor-id", "payload", {**P1_LINK, "previous_request_id": "some-absent-id"})
    result = invoke(tmp_path, shell, flat(by_id) + [other, chosen()], *FILTERS[selection], anchors=by_id)
    assert listed_ids(result)[0] == "ordinary-request"
    assert listed_ids(result) == (["ordinary-request", "unrelated-successor-id"] if selection == "unfiltered"
                                  else ["ordinary-request"])


@pytest.mark.parametrize("shell", SHELLS, **shell_ids)
def test_p1_predecessor_naming_a_case_variant_of_a_set_aside_id_is_listed(tmp_path: Path, shell: str) -> None:
    # The set-aside set is ordinal too: lineage onto a case variant is lineage onto another (absent) ID.
    by_id = pairs()
    successor = linked("unrelated-successor-id", "payload", {**P1_LINK, "previous_request_id": IDS[0].upper()})
    result = invoke(tmp_path, shell, flat(by_id) + [successor], anchors=by_id)
    assert listed_ids(result) == ["unrelated-successor-id"]


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
