# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.world_model.external_evidence_collector.

external_evidence_collector reads pinned upstream artifacts
(curiosity log, dream replay reports, mentor packs) and extracts
EXTERNAL evidence (Phase 9 §I). Critical contract: facts about WD's
internal state (tensions / blind_spots / calibration corrections)
MUST NOT leak into world_model — those belong to self_model.

Drift here would either:
- pollute world_model with self-referential facts (mixing
  self_model and world_model invariant), or
- silently drop valid external evidence (under-weights replay /
  curiosity / mentor evidence in calibration), or
- duplicate facts via non-deterministic fact_id (audit-trail
  drift; downstream dedup defeated).

Pinned invariants:

- _fact_id is deterministic: same (claim, source_kind) -> same
  fact_id. Different source_kind for the same claim -> different
  ids (so curiosity-log "X" and mentor-pack "X" don't dedup
  together).
- fact_id starts with "fact_" and is 16 chars total ("fact_" + 11
  hex). NB: source uses [:10] hex prefix + "fact_" prefix.
- from_curiosity_log:
  - missing candidate_cell -> "_unattributed" sentinel.
  - missing suspected_gap_type -> "unknown" sentinel.
  - confidence is normalized estimated_value/10.0, clamped to
    [0, 1].
  - emits one ExternalFact per row with kind="observation".
  - source_refs carries the curiosity_id (or empty string if
    missing).
- from_dream_replay_report:
  - replay_case_count <= 0 (or missing) -> empty list (no fact).
  - claim text includes gain/total + estimated_fallback_delta.
  - kind="report".
  - confidence = min(1.0, gain / max(rc, 1)).
  - source_refs = tension_ids_targeted tuple.
- from_mentor_context_pack:
  - missing items / empty items -> empty list.
  - items with kind in ("anti_pattern", "open_question") are
    EXCLUDED (those are self-referential to WD).
  - items with empty content excluded.
  - claim is content truncated to first 500 chars.
  - confidence is fixed 0.6 for mentor evidence.
"""
from __future__ import annotations

from waggledance.core.world_model.external_evidence_collector import (
    _fact_id,
    from_curiosity_log,
    from_dream_replay_report,
    from_mentor_context_pack,
)
from waggledance.core.world_model.world_model_snapshot import ExternalFact


# --- _fact_id determinism + scope ---------------------------------

def test_fact_id_deterministic_on_same_inputs():
    a = _fact_id(claim="thermal cell low confidence", source_kind="curiosity_log")
    b = _fact_id(claim="thermal cell low confidence", source_kind="curiosity_log")
    assert a == b


def test_fact_id_changes_when_claim_changes():
    a = _fact_id(claim="x", source_kind="curiosity_log")
    b = _fact_id(claim="y", source_kind="curiosity_log")
    assert a != b


def test_fact_id_changes_when_source_kind_changes():
    """A claim observed in curiosity_log vs mentor_context_pack
    must produce DIFFERENT fact_ids — otherwise the same claim
    text from different sources would silently dedup, hiding the
    fact that two independent sources reported it."""
    a = _fact_id(claim="x", source_kind="curiosity_log")
    b = _fact_id(claim="x", source_kind="mentor_context_pack")
    c = _fact_id(claim="x", source_kind="shadow_replay_report")
    assert a != b
    assert b != c
    assert a != c


def test_fact_id_format_is_fact_prefix_plus_short_hash():
    fid = _fact_id(claim="test", source_kind="curiosity_log")
    assert fid.startswith("fact_")
    # "fact_" prefix + 10 hex chars
    assert len(fid) == len("fact_") + 10


# --- from_curiosity_log -------------------------------------------

def test_from_curiosity_log_emits_external_fact_per_row():
    rows = [
        {"curiosity_id": "C-1", "candidate_cell": "thermal",
         "suspected_gap_type": "missing_solver",
         "estimated_value": 5.0},
        {"curiosity_id": "C-2", "candidate_cell": "humid",
         "suspected_gap_type": "stale_data",
         "estimated_value": 7.5},
    ]
    facts = from_curiosity_log(rows)
    assert len(facts) == 2
    assert all(isinstance(f, ExternalFact) for f in facts)
    assert all(f.kind == "observation" for f in facts)
    assert facts[0].source_refs == ("C-1",)
    assert facts[1].source_refs == ("C-2",)


def test_from_curiosity_log_missing_candidate_cell_uses_sentinel():
    """Missing/None candidate_cell -> "_unattributed" so all rows
    contribute facts even when cell is unknown."""
    rows = [
        {"curiosity_id": "C-1", "estimated_value": 1.0},
        {"curiosity_id": "C-2", "candidate_cell": None,
         "estimated_value": 2.0},
        {"curiosity_id": "C-3", "candidate_cell": "",
         "estimated_value": 3.0},
    ]
    facts = from_curiosity_log(rows)
    for fact in facts:
        assert "_unattributed" in fact.claim


def test_from_curiosity_log_missing_gap_type_uses_unknown_sentinel():
    rows = [{"curiosity_id": "C-1", "candidate_cell": "x",
             "estimated_value": 1.0}]
    facts = from_curiosity_log(rows)
    assert "gap_kind=unknown" in facts[0].claim


def test_from_curiosity_log_confidence_is_estimated_value_div_ten():
    """confidence = estimated_value / 10.0 clamped to [0, 1].
    A drifted scaling factor would silently miscalibrate downstream
    consumers. Pinning it here so changes are loud."""
    rows = [
        {"curiosity_id": "C-1", "estimated_value": 0.0},
        {"curiosity_id": "C-2", "estimated_value": 5.0},
        {"curiosity_id": "C-3", "estimated_value": 10.0},
        {"curiosity_id": "C-4", "estimated_value": 50.0},
        {"curiosity_id": "C-5", "estimated_value": -5.0},
    ]
    facts = from_curiosity_log(rows)
    assert facts[0].confidence == 0.0
    assert facts[1].confidence == 0.5
    assert facts[2].confidence == 1.0
    assert facts[3].confidence == 1.0  # clamped to 1.0 from 5.0
    assert facts[4].confidence == 0.0  # clamped from -0.5


def test_from_curiosity_log_missing_estimated_value_treated_as_zero():
    """A row with no estimated_value defaults to 0.0 confidence,
    not crash. The collector must be robust to incomplete rows."""
    rows = [{"curiosity_id": "C-1", "candidate_cell": "x"}]
    facts = from_curiosity_log(rows)
    assert facts[0].confidence == 0.0


def test_from_curiosity_log_missing_curiosity_id_uses_empty_ref():
    """Missing curiosity_id -> empty string in source_refs (one
    entry, not zero) — keeps source_refs shape consistent."""
    rows = [{"candidate_cell": "x", "estimated_value": 1.0}]
    facts = from_curiosity_log(rows)
    assert facts[0].source_refs == ("",)


def test_from_curiosity_log_empty_input_returns_empty_list():
    assert from_curiosity_log([]) == []


# --- from_dream_replay_report -------------------------------------

def test_from_dream_replay_report_zero_cases_returns_empty():
    """replay_case_count <= 0 -> no fact emitted (no evidence).
    Pinned because a stale/empty report must never inflate
    confidence."""
    report = {"replay_case_count": 0, "structural_gain_count": 0}
    assert from_dream_replay_report(report) == []


def test_from_dream_replay_report_missing_case_count_returns_empty():
    """Missing replay_case_count entirely -> empty list (no
    crash)."""
    assert from_dream_replay_report({}) == []


def test_from_dream_replay_report_negative_case_count_returns_empty():
    report = {"replay_case_count": -1, "structural_gain_count": 5}
    assert from_dream_replay_report(report) == []


def test_from_dream_replay_report_emits_single_fact_with_gain_metric():
    report = {
        "replay_case_count": 10,
        "structural_gain_count": 3,
        "estimated_fallback_delta": 0.3,
        "tension_ids_targeted": ["T-1", "T-2"],
    }
    facts = from_dream_replay_report(report)
    assert len(facts) == 1
    fact = facts[0]
    assert fact.kind == "report"
    assert "3/10" in fact.claim
    assert "0.300" in fact.claim
    assert fact.source_refs == ("T-1", "T-2")


def test_from_dream_replay_report_confidence_is_gain_over_count():
    """confidence = min(1.0, gain / max(rc, 1)).
    Full gain -> 1.0 confidence; zero gain -> 0.0."""
    full = from_dream_replay_report({
        "replay_case_count": 5, "structural_gain_count": 5,
    })
    half = from_dream_replay_report({
        "replay_case_count": 4, "structural_gain_count": 2,
    })
    none = from_dream_replay_report({
        "replay_case_count": 5, "structural_gain_count": 0,
    })
    assert full[0].confidence == 1.0
    assert half[0].confidence == 0.5
    assert none[0].confidence == 0.0


def test_from_dream_replay_report_missing_tension_ids_uses_empty_tuple():
    """Missing tension_ids_targeted -> empty source_refs tuple.
    Source must NEVER be None — downstream consumers depend on
    iterability."""
    report = {"replay_case_count": 1, "structural_gain_count": 1}
    facts = from_dream_replay_report(report)
    assert facts[0].source_refs == ()


# --- from_mentor_context_pack -------------------------------------

def test_from_mentor_context_pack_missing_items_returns_empty():
    """Pack without 'items' key -> empty list (no crash)."""
    assert from_mentor_context_pack({}) == []


def test_from_mentor_context_pack_empty_items_returns_empty():
    assert from_mentor_context_pack({"items": []}) == []


def test_from_mentor_context_pack_emits_one_fact_per_valid_item():
    pack = {"items": [
        {"item_id": "M-1", "kind": "design_pattern",
         "content": "use solver A for thermal queries"},
        {"item_id": "M-2", "kind": "constraint",
         "content": "humidity sensor responds within 200ms"},
    ]}
    facts = from_mentor_context_pack(pack)
    assert len(facts) == 2
    assert all(f.kind == "report" for f in facts)
    assert all(f.confidence == 0.6 for f in facts)
    assert facts[0].source_refs == ("M-1",)
    assert facts[1].source_refs == ("M-2",)


def test_from_mentor_context_pack_excludes_anti_pattern_items():
    """anti_pattern items are SELF-REFERENTIAL to WD's design
    (talking ABOUT WD's mistakes) -> belong to self_model, NOT
    world_model. The collector must filter them out."""
    pack = {"items": [
        {"item_id": "M-1", "kind": "anti_pattern",
         "content": "do not eval untrusted input"},
        {"item_id": "M-2", "kind": "design_pattern",
         "content": "valid external content"},
    ]}
    facts = from_mentor_context_pack(pack)
    assert len(facts) == 1
    assert facts[0].source_refs == ("M-2",)


def test_from_mentor_context_pack_excludes_open_question_items():
    """open_question items are likewise self-referential (WD
    asking the mentor) — not external evidence."""
    pack = {"items": [
        {"item_id": "M-1", "kind": "open_question",
         "content": "is this rate limit correct?"},
    ]}
    assert from_mentor_context_pack(pack) == []


def test_from_mentor_context_pack_excludes_empty_content_items():
    pack = {"items": [
        {"item_id": "M-1", "kind": "design_pattern", "content": ""},
        {"item_id": "M-2", "kind": "design_pattern"},
    ]}
    assert from_mentor_context_pack(pack) == []


def test_from_mentor_context_pack_truncates_claim_to_500_chars():
    """Long mentor content is truncated to 500 chars to bound
    storage. Pinned so a future drift to no-truncation doesn't
    silently bloat world_model."""
    long_content = "x" * 1000
    pack = {"items": [
        {"item_id": "M-1", "kind": "design_pattern",
         "content": long_content},
    ]}
    facts = from_mentor_context_pack(pack)
    assert len(facts[0].claim) == 500


def test_from_mentor_context_pack_missing_item_id_uses_empty_string():
    pack = {"items": [
        {"kind": "design_pattern", "content": "some external fact"},
    ]}
    facts = from_mentor_context_pack(pack)
    assert facts[0].source_refs == ("",)


# --- ExternalFact shape sanity ------------------------------------

def test_curiosity_log_facts_carry_required_fields():
    """Every emitted ExternalFact must have non-empty fact_id +
    kind + claim + numeric confidence + tuple source_refs."""
    rows = [{"curiosity_id": "C-1", "candidate_cell": "x",
             "estimated_value": 5.0}]
    fact = from_curiosity_log(rows)[0]
    assert fact.fact_id.startswith("fact_")
    assert fact.kind == "observation"
    assert isinstance(fact.claim, str) and fact.claim
    assert isinstance(fact.confidence, float)
    assert isinstance(fact.source_refs, tuple)


def test_replay_report_facts_carry_required_fields():
    report = {
        "replay_case_count": 1, "structural_gain_count": 1,
        "estimated_fallback_delta": 1.0,
        "tension_ids_targeted": ["T-1"],
    }
    fact = from_dream_replay_report(report)[0]
    assert fact.fact_id.startswith("fact_")
    assert fact.kind == "report"
    assert isinstance(fact.source_refs, tuple)
