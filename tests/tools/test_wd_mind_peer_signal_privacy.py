"""Synthetic privacy diagnostics: content leak and correlation are distinct.

Never uses live bridge events or a real review. Failure messages contain only
fixed field names, not output values, payloads, hashes or references. These
paired inputs do not prove noninterference for arbitrary event sequences.
"""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("synthetic_lane_fixture", ROOT / "tests/tools/test_wd_bridge_mind_lane_read.py")
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)

SENTINEL = "synthetic-private-payload-not-for-output"


def _pair(tmp_path, lane, *, compact=False):
    common = fixtures.rows()[:3]
    kwargs = {"agent": "claude-rco-1", "task": "synthetic-task", "ts": "2026-10-31T07:53:00Z",
              "synthetic_payload": SENTINEL}
    decision = fixtures.event(kind="decision", status="rco_pass", **kwargs)
    ordinary = fixtures.event(kind="message", status="note", **kwargs)
    audit_a, latest_a, cycle_a = fixtures.make_live(tmp_path / "a", events=[*common, decision])
    audit_b, latest_b, cycle_b = fixtures.make_live(tmp_path / "b", events=[*common, ordinary])
    # A real producer state binds its full raw window, so its own digest and a
    # compact head's digest differ even if their cursor metadata is identical.
    for latest, cycle, events in ((latest_a, cycle_a, [*common, decision]),
                                  (latest_b, cycle_b, [*common, ordinary])):
        rows = [dict(item, row_sha256=fixtures.producer.row_digest(item["event"]), source="detail")
                for item in events]
        token = fixtures.cursor()
        state = fixtures.producer.state_envelope(1, token, fixtures.producer.cursor_source(token), rows)
        assert state["window_sha256"] == fixtures.sha(fixtures.producer.window_bytes(rows))
        raw = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
        (cycle / "state.json").write_bytes(raw)
        fixtures.rewrite_pointer(latest, lambda last_ok: last_ok.update(state_sha256=fixtures.sha(raw)))
        if compact:
            fixtures.repin_compact_head(latest, cycle, fixtures.producer.state_head(state, fixtures.sha(raw)))
    return fixtures.read(audit_a, latest_a, lane=lane), fixtures.read(audit_b, latest_b, lane=lane)


def _changed_paths(a, b, prefix=""):
    if isinstance(a, dict) and isinstance(b, dict):
        result = []
        for key in sorted(set(a) | set(b)):
            path = prefix + "." + key if prefix else key
            if key not in a or key not in b:
                result.append(path)
            else:
                result.extend(_changed_paths(a[key], b[key], path))
        return result
    return [] if a == b else [prefix]


@pytest.mark.parametrize("lane", fixtures.LANES)
def test_direct_synthetic_payload_never_appears(tmp_path, lane):
    one, other = _pair(tmp_path, lane)
    if SENTINEL in json.dumps(one) or SENTINEL in json.dumps(other):
        pytest.fail("synthetic direct payload leak", pytrace=False)


@pytest.mark.parametrize("lane", fixtures.LANES)
@pytest.mark.parametrize("compact", [False, True], ids=["full-state", "compact-head"])
def test_paired_peer_decision_and_message_have_identical_lane_output(tmp_path, lane, compact):
    one, other = _pair(tmp_path, lane, compact=compact)
    changed = _changed_paths(one, other)
    if changed:
        # Identifiers come from this deliberately closed synthetic fixture;
        # print names only, never any values or input content.
        pytest.fail("synthetic metadata changed: " + ", ".join(changed), pytrace=False)
