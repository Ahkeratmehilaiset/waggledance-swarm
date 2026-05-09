# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.conversation.presence_log.

Append-only chained log of WD's conversational presence events
(Phase 9 §V). Same chain pattern as Session D's history.py and
R7.5's vector event log: each entry's `prev_entry_sha256` must
match the previous entry's `entry_sha256`. A regression here
breaks the integrity audit trail of WD's conversational presence.

Self-discovered area (conversation 4 files, 1 indirect import).
No direct test imported `waggledance.core.conversation.presence_log`
before this PR.

Pinned invariants:

- `compute_entry_sha256` raises if `entry_sha256` is in the input
  (must hash without itself).
- `make_entry` rejects unknown `kind`; produces deterministic
  sha12 from canonical input.
- `append_entry` is idempotent on duplicate `entry_sha256`
  (skip not append).
- `read_entries` round-trips JSON lines; tolerates malformed
  lines (skips, no crash).
- `validate_chain` returns `(True, None)` for a well-formed
  chain; `(False, breaking_sha)` when any entry's
  `prev_entry_sha256` does not match the previous entry's
  `entry_sha256`.
- `latest_prev_entry_sha256` returns GENESIS_PREV ('0'*64) when
  empty; the last entry's sha otherwise.
"""
from __future__ import annotations

import json

import pytest

from waggledance.core.conversation import (
    CONVERSATION_SCHEMA_VERSION,
    PRESENCE_KINDS,
)
from waggledance.core.conversation.presence_log import (
    GENESIS_PREV,
    PresenceEntry,
    append_entry,
    compute_entry_sha256,
    latest_prev_entry_sha256,
    make_entry,
    read_entries,
    validate_chain,
)


# --- compute_entry_sha256: rejection of self-inclusion -------------

def test_compute_entry_sha256_raises_when_entry_sha_in_input():
    payload = {"x": 1, "entry_sha256": "abc"}
    with pytest.raises(ValueError):
        compute_entry_sha256(payload)


def test_compute_entry_sha256_deterministic_and_truncated_to_twelve():
    payload = {"a": 1, "b": [2, 3], "c": "x"}
    a = compute_entry_sha256(payload)
    b = compute_entry_sha256(payload)
    assert a == b
    assert len(a) == 12


def test_compute_entry_sha256_changes_on_input_change():
    a = compute_entry_sha256({"x": 1})
    b = compute_entry_sha256({"x": 2})
    assert a != b


# --- PresenceEntry __post_init__ ------------------------------------

def test_presence_entry_post_init_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown presence kind"):
        PresenceEntry(
            schema_version=1,
            entry_sha256="abc",
            ts_iso="2026-05-09T00:00:00Z",
            kind="not_a_kind",
            summary="x",
            prev_entry_sha256=GENESIS_PREV,
            capsule_context="neutral_v1",
        )


@pytest.mark.parametrize("kind", PRESENCE_KINDS)
def test_presence_entry_accepts_each_allowlisted_kind(kind):
    entry = PresenceEntry(
        schema_version=1, entry_sha256="abc",
        ts_iso="t", kind=kind, summary="s",
        prev_entry_sha256=GENESIS_PREV, capsule_context="neutral_v1",
    )
    assert entry.kind == kind


# --- make_entry -----------------------------------------------------

def test_make_entry_returns_deterministic_sha_for_same_inputs():
    a = make_entry(ts_iso="t", kind="turn", summary="s",
                   prev_entry_sha256=GENESIS_PREV)
    b = make_entry(ts_iso="t", kind="turn", summary="s",
                   prev_entry_sha256=GENESIS_PREV)
    assert a.entry_sha256 == b.entry_sha256


def test_make_entry_different_inputs_yield_different_shas():
    a = make_entry(ts_iso="t", kind="turn", summary="hi",
                   prev_entry_sha256=GENESIS_PREV)
    b = make_entry(ts_iso="t", kind="turn", summary="bye",
                   prev_entry_sha256=GENESIS_PREV)
    assert a.entry_sha256 != b.entry_sha256


def test_make_entry_carries_optional_excerpts_and_evidence_refs():
    entry = make_entry(
        ts_iso="t", kind="turn", summary="s",
        prev_entry_sha256=GENESIS_PREV,
        evidence_refs=("ref:1", "ref:2"),
        user_turn_excerpt="hi",
        wd_turn_excerpt="hello",
    )
    assert entry.evidence_refs == ("ref:1", "ref:2")
    assert entry.user_turn_excerpt == "hi"
    assert entry.wd_turn_excerpt == "hello"


def test_make_entry_uses_schema_version_constant():
    entry = make_entry(ts_iso="t", kind="turn", summary="s",
                       prev_entry_sha256=GENESIS_PREV)
    assert entry.schema_version == CONVERSATION_SCHEMA_VERSION


# --- append_entry idempotency --------------------------------------

def test_append_entry_creates_file_and_writes_one_line(tmp_path):
    path = tmp_path / "log" / "presence.jsonl"
    entry = make_entry(ts_iso="t", kind="turn", summary="s",
                       prev_entry_sha256=GENESIS_PREV)
    out = append_entry(path, entry)
    assert out == path
    assert path.exists()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_append_entry_idempotent_on_duplicate_sha(tmp_path):
    path = tmp_path / "presence.jsonl"
    entry = make_entry(ts_iso="t", kind="turn", summary="s",
                       prev_entry_sha256=GENESIS_PREV)
    append_entry(path, entry)
    append_entry(path, entry)  # duplicate — should NOT append
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1


def test_append_entry_appends_distinct_entries(tmp_path):
    path = tmp_path / "presence.jsonl"
    e1 = make_entry(ts_iso="t1", kind="turn", summary="hi",
                    prev_entry_sha256=GENESIS_PREV)
    e2 = make_entry(ts_iso="t2", kind="reflection", summary="ok",
                    prev_entry_sha256=e1.entry_sha256)
    append_entry(path, e1)
    append_entry(path, e2)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


# --- read_entries ---------------------------------------------------

def test_read_entries_round_trips_make_and_append(tmp_path):
    path = tmp_path / "presence.jsonl"
    e1 = make_entry(ts_iso="t1", kind="turn", summary="hi",
                    prev_entry_sha256=GENESIS_PREV)
    e2 = make_entry(ts_iso="t2", kind="reflection", summary="ok",
                    prev_entry_sha256=e1.entry_sha256,
                    evidence_refs=("ref:1",))
    append_entry(path, e1)
    append_entry(path, e2)
    out = read_entries(path)
    assert len(out) == 2
    assert out[0].kind == "turn"
    assert out[1].kind == "reflection"
    assert out[1].evidence_refs == ("ref:1",)


def test_read_entries_returns_empty_for_missing_file(tmp_path):
    assert read_entries(tmp_path / "does_not_exist.jsonl") == []


def test_read_entries_skips_malformed_lines(tmp_path):
    path = tmp_path / "presence.jsonl"
    e1 = make_entry(ts_iso="t1", kind="turn", summary="hi",
                    prev_entry_sha256=GENESIS_PREV)
    append_entry(path, e1)
    # Append a malformed line manually — read_entries must skip it.
    with open(path, "a", encoding="utf-8") as f:
        f.write("{not valid json\n")
        f.write("\n")  # empty line
        f.write('{"missing_fields": true}\n')
    out = read_entries(path)
    # Only the well-formed entry survives.
    assert len(out) == 1
    assert out[0].entry_sha256 == e1.entry_sha256


# --- validate_chain ------------------------------------------------

def test_validate_chain_passes_for_well_formed_chain():
    e1 = make_entry(ts_iso="t1", kind="turn", summary="a",
                    prev_entry_sha256=GENESIS_PREV)
    e2 = make_entry(ts_iso="t2", kind="turn", summary="b",
                    prev_entry_sha256=e1.entry_sha256)
    e3 = make_entry(ts_iso="t3", kind="reflection", summary="c",
                    prev_entry_sha256=e2.entry_sha256)
    ok, breaking = validate_chain([e1, e2, e3])
    assert ok is True
    assert breaking is None


def test_validate_chain_passes_for_empty_list():
    ok, breaking = validate_chain([])
    assert ok is True
    assert breaking is None


def test_validate_chain_fails_when_first_entry_does_not_chain_to_genesis():
    """The first entry MUST have prev_entry_sha256 == GENESIS_PREV;
    a chain that starts mid-stream returns False."""
    bad = make_entry(ts_iso="t", kind="turn", summary="orphan",
                     prev_entry_sha256="not_genesis")
    ok, breaking = validate_chain([bad])
    assert ok is False
    assert breaking == bad.entry_sha256


def test_validate_chain_fails_when_link_breaks_mid_chain():
    e1 = make_entry(ts_iso="t1", kind="turn", summary="a",
                    prev_entry_sha256=GENESIS_PREV)
    # e2 points at the WRONG previous sha — should break the chain.
    e2 = make_entry(ts_iso="t2", kind="turn", summary="b",
                    prev_entry_sha256="0" * 12 + "0" * 52)
    ok, breaking = validate_chain([e1, e2])
    assert ok is False
    assert breaking == e2.entry_sha256


# --- latest_prev_entry_sha256 --------------------------------------

def test_latest_prev_returns_genesis_when_empty():
    assert latest_prev_entry_sha256([]) == GENESIS_PREV


def test_latest_prev_returns_last_entry_sha():
    e1 = make_entry(ts_iso="t1", kind="turn", summary="a",
                    prev_entry_sha256=GENESIS_PREV)
    e2 = make_entry(ts_iso="t2", kind="turn", summary="b",
                    prev_entry_sha256=e1.entry_sha256)
    assert latest_prev_entry_sha256([e1, e2]) == e2.entry_sha256
