"""Byte-identical synthetic packets through both real startup consumers.

This characterizes content admission, not source authentication or wall-clock
freshness. Existing documented differences stay explicit; neither parser is
weakened. Only owned fake helpers run, never Mind or a live writer.
"""
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

import test_wd_q4_shared_agent_startup as agent
from test_wd_startup_recovery import load

pytestmark = pytest.mark.skipif(not agent.SHELL, reason="Windows PowerShell required")
LANE = "codex-tools-1"  # Accepted by both launchers: no lane-specific packet rewrite.
TOKEN = "synthetic-owner-token"
MARKER = "PARITY-SYNTHETIC-CONTENT"
COMMON = (
    "valid", "malformed_json", "extra_field", "bad_descriptor_pin", "bad_answer_pin",
    "bad_binding", "wrong_lane", "future_schema", "false_freshness", "bad_age_shape",
    "bool_count", "tampered_text", "extra_result_field", "missing_owner_token",
    "unicode_presentation", "presentation_budget", "replay",
)
V2_ONLY = (
    "age_bool", "age_string", "age_fraction", "age_negative", "age_mismatch", "age_extra",
    "invalid_timestamp", "observed_no_fraction", "observed_one_fraction",
    "future_build", "future_cutoff", "reversed_builds", "clock_skew_past", "clock_skew_future",
)
CASES = [(version, case) for version in (1, 2) for case in COMMON]
CASES += [(2, case) for case in V2_ONLY]


def _packet(version, case):
    texts = (MARKER,)
    if case == "unicode_presentation":
        texts = (MARKER + ' äΩ "quoted" \\path\nnext',)
    elif case == "presentation_budget":
        texts = tuple(MARKER + "x" * 290 for _ in range(72))
    value = (agent.delivery_envelope_v2 if version == 2 else agent.delivery_envelope)(LANE, texts)
    if case == "extra_field": value["unexpected"] = 1
    elif case == "bad_descriptor_pin": value["descriptor_sha256"] = "f" * 64
    elif case == "bad_binding": value["answer"]["binding"]["encoder_sha256"] = "f" * 64
    elif case == "wrong_lane": value["lane"] = "codex-lead-1"
    elif case == "future_schema": value["schema"] = "wd.q4-lane-content-delivery.v3"
    elif case == "false_freshness": value["freshness"] = "FRESH"
    elif case == "bad_age_shape": value["index_age"] = []
    elif case == "bool_count": value["answer"]["result_count"] = True
    elif case == "extra_result_field": value["answer"]["results"][0]["unexpected"] = 1
    age = value["index_age"]
    if version == 2:
        if case == "age_bool": age["oldest_cell_age_seconds"] = True
        elif case == "age_string": age["oldest_cell_age_seconds"] = "105"
        elif case == "age_fraction": age["oldest_cell_age_seconds"] = 105.5
        elif case == "age_negative": age["oldest_cell_age_seconds"] = -1
        elif case == "age_mismatch": age["oldest_cell_age_seconds"] += 1
        elif case == "age_extra": age["unexpected"] = 1
        elif case == "invalid_timestamp": age["observed_local_utc"] = "2026-02-30T20:00:05.000000Z"
        elif case == "observed_no_fraction": age["observed_local_utc"] = "2026-11-10T20:00:05Z"
        elif case == "observed_one_fraction": age["observed_local_utc"] = "2026-11-10T20:00:05.0Z"
        elif case in ("future_build", "future_cutoff"):
            key = "newest_cell_built_utc" if case == "future_build" else "coverage_cutoff_utc"
            age[key] = "2026-11-10T20:00:06Z"
            age["newest_cell_age_seconds" if case == "future_build" else "coverage_age_seconds"] = -1
        elif case == "reversed_builds":
            age["oldest_cell_built_utc"], age["newest_cell_built_utc"] = age["newest_cell_built_utc"], age["oldest_cell_built_utc"]
            age["oldest_cell_age_seconds"], age["newest_cell_age_seconds"] = age["newest_cell_age_seconds"], age["oldest_cell_age_seconds"]
        elif case.startswith("clock_skew_"):
            # Consistently shifted tuples are not authenticated to the consumer's clock.
            delta = timedelta(days=-3650 if case.endswith("past") else 3650)
            for key in ("observed_local_utc", "oldest_cell_built_utc", "newest_cell_built_utc", "coverage_cutoff_utc"):
                stamp = datetime.fromisoformat(age[key].replace("Z", "+00:00")) + delta
                age[key] = stamp.isoformat(timespec="microseconds").replace("+00:00", "Z")
    value["answer_sha256"] = agent.sha(agent.canon(value["answer"]))
    if case == "bad_answer_pin": value["answer_sha256"] = "f" * 64
    elif case == "tampered_text": value["answer"]["results"][0]["stored_text"] += " changed after hashing"
    raw = b"{broken\n" if case == "malformed_json" else agent.canon(value) + b"\n"
    return value, raw


def _observe(tmp_path, consumer, raw, shell, *, missing_token=False):
    root = tmp_path / consumer
    root.mkdir()
    # One fake transport for both parsers also avoids importing unrelated Tools
    # integration fixtures (and their tests.tools namespace dependencies).
    fx = agent.startup_read_fixture(root, LANE, mode="producer_bytes", producer_bytes=raw)
    fx["env_prelude"] += "\n$env:AGENT_BRIDGE_AGENT='codex-tools-1'\n"
    fx["env_prelude"] += ("Remove-Item Env:\\AGENT_BRIDGE_OWNER_TOKEN -ErrorAction SilentlyContinue\n"
                           if missing_token else "$env:AGENT_BRIDGE_OWNER_TOKEN='synthetic-owner-token'\n")
    source = agent.LAUNCHER if consumer == "agent" else agent.ROOT / "ops/windows/reboot/start-wd-tools-consumer.ps1"
    script = "$ErrorActionPreference='Stop';Set-StrictMode -Version Latest\n"
    script += "if($PSVersionTable.PSEdition -eq 'Desktop'){$env:PSModulePath=Join-Path $PSHOME 'Modules'}\n"
    script += "\n".join(load(source, name) for name in agent.STARTUP_READ_FUNCTIONS)
    script += "\n" + agent.startup_read_call(fx, LANE).split("\nif ($null -eq $r)")[0]
    # Retain metadata only even on RED; no recalled text or token in stdout.
    script += r'''
$view=[ordered]@{}
foreach($p in $r.PSObject.Properties){if($p.Name -cne 'delivered_text'){$view[$p.Name]=$p.Value}}
$view.has_unknown=$r.delivered_text.Contains('UNKNOWN')
$view.has_marker=$r.delivered_text.Contains('PARITY-SYNTHETIC-CONTENT')
$view.has_unauthed=$r.delivered_text.Contains('not authenticated')
$view.text_length=$r.delivered_text.Length
$h=[Security.Cryptography.SHA256]::Create()
try{$view.text_sha256=[BitConverter]::ToString($h.ComputeHash([Text.Encoding]::UTF8.GetBytes($r.delivered_text))).Replace('-','').ToLowerInvariant()}
finally{$h.Dispose()}
[pscustomobject]$view|ConvertTo-Json -Depth 7 -Compress
'''
    result = subprocess.run([shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                            cwd=Path.cwd(), capture_output=True, text=True, timeout=60, check=True)
    assert MARKER not in result.stdout and TOKEN not in result.stdout + result.stderr
    record = json.loads(fx["record"].read_text(encoding="utf-8-sig"))
    assert "delivered_text" not in record and MARKER not in json.dumps(record) and TOKEN not in json.dumps(record)
    return fx, json.loads(result.stdout.strip()), record


def _expected_success(consumer, case):
    # Agent's observed timestamp requires six digits; Tools accepts 0..6.
    # Agent permits a missing owner token; Tools refuses it.
    # Agent truncates the presentation at its budget; Tools refuses overflow.
    if case in ("missing_owner_token", "presentation_budget"): return consumer == "agent"
    if case in ("observed_no_fraction", "observed_one_fraction"): return consumer == "tools"
    return case in ("valid", "unicode_presentation", "replay", "clock_skew_past", "clock_skew_future")


def _assert_result(consumer, version, case, value, view, record):
    success = _expected_success(consumer, case)
    assert view["outcome"] == ("success" if success else "refused"), (consumer, version, case, view)
    if not success:
        assert view["reason"] == "result_shape"
        assert view["refs"] == [] and view["answer_sha256"] == "" and view["call_id"] == ""
        assert view["delivered_items"] == view["delivered_chars"] == view["text_length"] == 0
        assert view["delivered_sha256"] == "" and not view["has_marker"]
        return
    assert view["reason"] == ("content_delivered" if consumer == "agent" else "content_delivered_unknown_freshness")
    assert view["cleanup"] == "CONFIRMED"
    assert view["refs"] == [r["ref_sha256"] for r in value["answer"]["results"]]
    assert view["answer_sha256"] == value["answer_sha256"] == agent.sha(agent.canon(value["answer"]))
    assert view["call_id"] == value["call_id"] and view["has_unknown"] and view["has_marker"]
    if consumer == "agent":
        text, count = agent.expected_delivery(value)
        assert view["freshness"] == "UNKNOWN"
        assert view["index_age"] == ("OBSERVED" if version == 2 else "UNKNOWN")
        assert view["cell_build_age"] == (value["index_age"] if version == 2 else None)
        if case == "presentation_budget": assert 0 < count < len(value["answer"]["results"])
    else:
        header = "Mind recalled content; freshness UNKNOWN; index_age UNKNOWN; source references are caller-pinned, not authenticated."
        if version == 2:
            age = value["index_age"]
            header = (
                f"Mind recalled content; oldest cell build age {age['oldest_cell_age_seconds']} s "
                f"({age['oldest_cell_built_utc']}); newest cell build age {age['newest_cell_age_seconds']} s "
                f"({age['newest_cell_built_utc']}); content cutoff age {age['coverage_age_seconds']} s "
                f"({age['coverage_cutoff_utc']}); observed local {age['observed_local_utc']}; "
                "clock authentication UNKNOWN; freshness UNKNOWN; coverage completeness UNKNOWN; "
                "source references are caller-pinned, not authenticated.")
        text = "\n".join([header, *[
            f"[{i}] " + agent.canon(r["stored_text"]).decode("ascii") +
            f" [ref_sha256={r['ref_sha256']}; doc_id_sha256={r['doc_id_sha256']}]"
            for i, r in enumerate(value["answer"]["results"], 1)]])
        count = len(value["answer"]["results"])
    assert view["delivered_items"] == count
    assert view["delivered_chars"] == view["text_length"] == agent.u16(text)
    assert view["delivered_sha256"] == view["text_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    if version == 2: assert view["has_unauthed"]
    for key in ("outcome", "reason", "answer_sha256", "refs", "call_id", "delivered_sha256"):
        assert record[key] == view[key]


@pytest.mark.parametrize("shell", agent.SHELLS)
@pytest.mark.parametrize("version,case", CASES, ids=[f"v{v}-{c}" for v, c in CASES])
def test_shared_startup_packet_table(tmp_path, shell, version, case):
    value, raw = _packet(version, case)
    observations = {}
    for consumer in ("agent", "tools"):
        fx, view, record = _observe(tmp_path, consumer, raw, shell, missing_token=case == "missing_owner_token")
        assert fx["descriptor_pin"] == agent.content_descriptor_pin()
        assert (fx["bundle"] / "producer-response.bin").read_bytes() == raw
        _assert_result(consumer, version, case, value, view, record)
        observations[consumer] = view
    if case == "replay":
        replay_root = tmp_path / "repeat"
        replay_root.mkdir()
        for consumer in ("agent", "tools"):
            _, view, record = _observe(replay_root, consumer, raw, shell)
            _assert_result(consumer, version, case, value, view, record)
            assert view["answer_sha256"] == observations[consumer]["answer_sha256"]
    if case in ("valid", "unicode_presentation", "replay", "clock_skew_past", "clock_skew_future"):
        assert observations["agent"]["answer_sha256"] == observations["tools"]["answer_sha256"]
        # Presentation and receipts intentionally differ; original answer bytes do not.
        assert observations["agent"]["delivered_sha256"] != observations["tools"]["delivered_sha256"]
