"""Exact historical quarantine; synthetic anchors, real comparator and reader.

No historical messages/payloads are committed. Tests replace only the pinned
tuple literals in a fixture copy, never hashing, parsing or inventory behavior.
"""
from copy import deepcopy
import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from test_bridge_request_inventory import BIN, INVENTORY, SHELLS, _run, _write

KNOWN_ID = "lead-mind-shared-index-fable-20261031-v2"
REFS = ["24ad958a518d4d0aaac3ffbc8bf30c9fc14e96b9f421bafde2b2a346f7c45db5",
        "112488f88756d98430d7eb71c76fdccdec356b063ed10f29954c855230ea7e21"]
DIGESTS = ["3dd19b718f1829753c8cd86aee754eddc2ca8f10924aa590446c91a919123058",
           "43e076ce3d49dd07ae4b9f76c4828878508e87211fac02430d097bbc0a5b3c45"]
CONTENT = ("request_id", "agent", "agent_uuid", "session_id", "run_id", "task_id", "to", "type",
           "status", "message", "payload", "expected_responders")
pytestmark = pytest.mark.skipif(not SHELLS, reason="PowerShell required")


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def ref(event):
    return hashlib.sha256(canonical(event).encode()).hexdigest()


def digest(event):
    # Existing PS5.1 contract serialization escapes HTML punctuation; these
    # synthetic content keys have the same order under its existing sorter.
    text = canonical({k: event.get(k) for k in CONTENT})
    for char in "'<>&":
        text = text.replace(char, "\\u%04x" % ord(char))
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.parametrize("shell", SHELLS)
def test_reference_serializer_matches_canonical_json(tmp_path, shell):
    event = {"empty": [], "nested": {"number": 7, "flag": True},
             "text": "ääkkönen\n\t\"\\", "nothing": None}
    source = tmp_path / "event.json"
    source.write_text(canonical(event), encoding="utf-8")
    inventory = str(INVENTORY).replace("'", "''")
    file = str(source).replace("'", "''")
    script = f"""
$tokens=$null; $errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile('{inventory}',[ref]$tokens,[ref]$errors)
if($errors.Count) {{ throw 'parse failure' }}
$fn=$ast.Find({{param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -ceq 'ConvertTo-InventoryReferenceJson'}},$true)
. ([scriptblock]::Create($fn.Extent.Text))
$value=Get-Content -LiteralPath '{file}' -Raw -Encoding UTF8 | ConvertFrom-Json
$json=ConvertTo-InventoryReferenceJson $value
[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
"""
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert base64.b64decode(result.stdout.strip()).decode("utf-8") == canonical(event)


def pair():
    row = dict(ts_utc="2026-10-31T16:10:49.2600065Z", agent="codex-lead-1", type="wake_request",
               status="requested", to="fable-5", task_id="synthetic/historical", request_id=KNOWN_ID,
               agent_uuid="synthetic-lead", session_id="synthetic-session", run_id="synthetic-session",
               message="first synthetic request", payload={"scope": "synthetic"},
               expected_responders={"fable-5": {"agent_uuid": "synthetic-fable", "session_id": "synthetic"}})
    other = deepcopy(row)
    other.update(ts_utc="2026-10-31T19:06:28.5392289Z", message="second synthetic request")
    for item in (row, other):
        item["request_digest"] = digest(item)
    return [row, other]


def invoke(tmp_path, shell, rows, *, anchors=None, diagnostic=False):
    originals = anchors or pair()
    scripts = tmp_path / "bin"
    shutil.copytree(BIN, scripts)
    text = INVENTORY.read_text(encoding="utf-8-sig")
    for old, event in zip(REFS, originals):
        text = text.replace(old, ref(event))
    for old, event in zip(DIGESTS, originals):
        text = text.replace(old, event["request_digest"])
    target = scripts / INVENTORY.name
    target.write_text(text, encoding="utf-8")
    runtime = tmp_path / "runtime"
    log = _write(runtime, rows, raw_utf8=True)
    before = log.read_bytes()
    args = ("-DiagnosticPartial",) if diagnostic else ()
    result = _run(shell, target, runtime, "-Agent", "codex-lead-1", "-NoCache", *args)
    assert log.read_bytes() == before
    return result


@pytest.mark.parametrize("shell", SHELLS)
def test_exact_pair_is_historical_and_never_a_request(tmp_path, shell):
    rows = pair()
    result = invoke(tmp_path, shell, rows)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["schema"] == "wd.request-inventory.v2"
    assert data["request_count"] == data["matched_count"] == data["returned_count"] == 0
    assert data["requests"] == []
    [conflict] = data["historical_conflicts"]
    assert conflict["request_id"] == KNOWN_ID
    assert conflict["event_refs"] == [ref(row) for row in rows]
    assert conflict["request_digests"] == [row["request_digest"] for row in rows]
    assert conflict["answer_state"] == "not_evaluated"


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("change", ["third_row", "duplicate_row", "other_id", "one_row", "timestamp", "digest", "payload"])
def test_all_unpinned_variants_fail_closed(tmp_path, shell, change):
    rows = pair()
    if change == "third_row":
        bad = deepcopy(rows[0]); bad["message"] = "third conflicting request"; rows.append(bad)
    elif change == "duplicate_row":
        rows.append(deepcopy(rows[0]))
    elif change == "other_id":
        for row in rows:
            row["request_id"] = "another-id"; row["request_digest"] = digest(row)
    elif change == "one_row":
        rows.pop()
    elif change == "timestamp":
        rows[0]["ts_utc"] = "2026-10-31T16:10:50Z"
    elif change == "digest":
        rows[0]["request_digest"] = "0" * 64
    else:
        rows[0]["payload"]["extra"] = "changed"
    result = invoke(tmp_path, shell, rows)
    assert result.returncode != 0 and result.stdout.strip() == ""


@pytest.mark.parametrize("shell", SHELLS)
def test_matching_event_ref_does_not_replace_content_digest_verification(tmp_path, shell):
    rows = pair()
    rows[0]["message"] = "different content under a stale digest"
    result = invoke(tmp_path, shell, rows, anchors=rows)
    assert result.returncode != 0 and result.stdout.strip() == ""


@pytest.mark.parametrize("shell", SHELLS)
def test_diagnostic_partial_remains_partial(tmp_path, shell):
    result = invoke(tmp_path, shell, pair(), diagnostic=True)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["schema"] == "wd.request-inventory-diagnostic.v1" and data["complete"] is False
    assert data["conflict_count"] == 1 and data["requests"] == []


@pytest.mark.parametrize("shell", SHELLS)
def test_known_pair_does_not_hide_unrelated_valid_request(tmp_path, shell):
    rows = pair()
    good = deepcopy(rows[0]); good["request_id"] = "unrelated-current-id"
    good["request_digest"] = digest(good)
    result = invoke(tmp_path, shell, rows + [good])
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert [row["request_id"] for row in data["requests"]] == ["unrelated-current-id"]
    assert data["request_count"] == 1
