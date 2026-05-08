# SPDX-License-Identifier: Apache-2.0
"""Direct unit tests for waggledance.core.dreaming.collapse.

Codex scout round 3 flagged this as Candidate 3 (high risk if missing):
this is the safety boundary between external dream output and the
deterministic collapse report. A regression here can orphan proposals,
accidentally unlink them from request packs, or misclassify gate
verdicts while preserving valid-looking JSON.

Pinned invariants (c.txt §C3, sidecar protocol):

- `_to_collapse_verdict` raw → collapse mapping:
  - "ACCEPT_CANDIDATE" → ACCEPT_CANDIDATE
  - "ACCEPT_*" → ACCEPT_CANDIDATE
  - hard-reject set → REJECT_HARD
  - soft-reject set → REJECT_SOFT
  - unknown → REJECT_SOFT (default-soft)
- `validate_sidecar` requires
  `responding_to_dream_request_pack_sha12` and `generation_method`;
  generation_method ∈ {manual, llm, unknown}.
- `linkage_for_proposal`:
  - valid sidecar with known sha → (linked, sha, "sidecar")
  - valid sidecar with unknown sha → (False, sha, "sidecar")
  - malformed sidecar → falls back ONLY to inline-known-sha or missing
  - no sidecar + inline known → (linked, sha, "inline")
  - no sidecar + nothing inline → (False, None, "missing")
- `discover_proposals`:
  - skips `*.dream_metadata.json` sidecar files
  - lexicographic ordering
  - max_proposals < 1 or > HARD_MAX_PROPOSALS raises ValueError
  - truncated entries listed in returned tuple
"""
from __future__ import annotations

import json

import pytest

from waggledance.core.dreaming.collapse import (
    DEFAULT_MAX_PROPOSALS,
    HARD_MAX_PROPOSALS,
    SIDECAR_SCHEMA_REQUIRED,
    _to_collapse_verdict,
    discover_proposals,
    linkage_for_proposal,
    sidecar_path_for,
    validate_sidecar,
)


# --- _to_collapse_verdict mapping ----------------------------------

def test_collapse_verdict_accept_candidate_passes_through():
    assert _to_collapse_verdict("ACCEPT_CANDIDATE") == "ACCEPT_CANDIDATE"


def test_collapse_verdict_accept_prefix_normalises_to_accept_candidate():
    assert _to_collapse_verdict("ACCEPT_RUNTIME") == "ACCEPT_CANDIDATE"
    assert _to_collapse_verdict("ACCEPT_LATER") == "ACCEPT_CANDIDATE"


@pytest.mark.parametrize("raw", [
    "REJECT_SCHEMA", "REJECT_DUPLICATE", "REJECT_CONTRADICTION",
])
def test_collapse_verdict_hard_rejects(raw):
    assert _to_collapse_verdict(raw) == "REJECT_HARD"


@pytest.mark.parametrize("raw", [
    "REJECT_LOW_VALUE", "ABSTAIN", "INCONCLUSIVE",
])
def test_collapse_verdict_soft_rejects(raw):
    assert _to_collapse_verdict(raw) == "REJECT_SOFT"


def test_collapse_verdict_unknown_defaults_to_soft_reject():
    """Unknown verdicts must NOT auto-promote to ACCEPT_CANDIDATE.
    Default-soft is the safe failure mode."""
    assert _to_collapse_verdict("DOES_NOT_EXIST") == "REJECT_SOFT"
    assert _to_collapse_verdict("") == "REJECT_SOFT"


# --- validate_sidecar ----------------------------------------------

def test_validate_sidecar_passes_with_required_fields():
    sidecar = {
        "responding_to_dream_request_pack_sha12": "abcdef012345",
        "generation_method": "manual",
    }
    ok, errors = validate_sidecar(sidecar)
    assert ok is True
    assert errors == []


def test_validate_sidecar_lists_missing_required_fields():
    ok, errors = validate_sidecar({})
    assert ok is False
    # Both required fields must surface as errors.
    for f in SIDECAR_SCHEMA_REQUIRED:
        assert any(f in e for e in errors)


@pytest.mark.parametrize("method", ["manual", "llm", "unknown"])
def test_validate_sidecar_accepts_allowlisted_generation_method(method):
    sidecar = {
        "responding_to_dream_request_pack_sha12": "abc",
        "generation_method": method,
    }
    ok, _ = validate_sidecar(sidecar)
    assert ok is True


def test_validate_sidecar_rejects_invalid_generation_method():
    sidecar = {
        "responding_to_dream_request_pack_sha12": "abc",
        "generation_method": "auto_apply",
    }
    ok, errors = validate_sidecar(sidecar)
    assert ok is False
    assert any("generation_method" in e for e in errors)


# --- linkage_for_proposal ------------------------------------------

def _write_proposal(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def _write_sidecar(proposal_path, sidecar):
    sp = sidecar_path_for(proposal_path)
    sp.write_text(json.dumps(sidecar), encoding="utf-8")
    return sp


def test_linkage_valid_sidecar_with_known_sha_returns_linked(tmp_path):
    pp = _write_proposal(tmp_path, "p1.json", {"proposal_id": "P-1"})
    _write_sidecar(pp, {
        "responding_to_dream_request_pack_sha12": "PACK-1",
        "generation_method": "llm",
    })
    linked, sha, source = linkage_for_proposal(
        proposal={"proposal_id": "P-1"},
        proposal_path=pp,
        known_pack_sha12s={"PACK-1", "PACK-2"},
    )
    assert linked is True
    assert sha == "PACK-1"
    assert source == "sidecar"


def test_linkage_valid_sidecar_with_unknown_sha_rejects_with_sidecar_source(tmp_path):
    pp = _write_proposal(tmp_path, "p2.json", {"proposal_id": "P-2"})
    _write_sidecar(pp, {
        "responding_to_dream_request_pack_sha12": "UNKNOWN-PACK",
        "generation_method": "manual",
    })
    linked, sha, source = linkage_for_proposal(
        proposal={"proposal_id": "P-2"},
        proposal_path=pp,
        known_pack_sha12s={"PACK-1"},
    )
    assert linked is False
    assert sha == "UNKNOWN-PACK"
    assert source == "sidecar"


def test_linkage_no_sidecar_with_inline_known_sha_returns_linked_inline(tmp_path):
    pp = _write_proposal(tmp_path, "p3.json", {
        "proposal_id": "P-3",
        "responding_to_dream_request_pack_sha12": "PACK-INL",
    })
    # Note: NO sidecar file written.
    linked, sha, source = linkage_for_proposal(
        proposal={
            "proposal_id": "P-3",
            "responding_to_dream_request_pack_sha12": "PACK-INL",
        },
        proposal_path=pp,
        known_pack_sha12s={"PACK-INL"},
    )
    assert linked is True
    assert sha == "PACK-INL"
    assert source == "inline"


def test_linkage_no_sidecar_no_inline_returns_missing(tmp_path):
    pp = _write_proposal(tmp_path, "p4.json", {"proposal_id": "P-4"})
    linked, sha, source = linkage_for_proposal(
        proposal={"proposal_id": "P-4"},
        proposal_path=pp,
        known_pack_sha12s={"PACK-1"},
    )
    assert linked is False
    assert sha is None
    assert source == "missing"


def test_linkage_malformed_sidecar_treated_as_unparseable_falls_back(tmp_path):
    """A sidecar that is not valid JSON should be treated as missing
    so inline / missing-fallback can run, not raise."""
    pp = _write_proposal(tmp_path, "p5.json", {
        "proposal_id": "P-5",
        "responding_to_dream_request_pack_sha12": "PACK-INL",
    })
    sp = sidecar_path_for(pp)
    sp.write_text("{not valid json}", encoding="utf-8")
    linked, sha, source = linkage_for_proposal(
        proposal={
            "proposal_id": "P-5",
            "responding_to_dream_request_pack_sha12": "PACK-INL",
        },
        proposal_path=pp,
        known_pack_sha12s={"PACK-INL"},
    )
    # Malformed sidecar → load_sidecar returns None → inline path runs.
    assert linked is True
    assert source == "inline"


# --- discover_proposals --------------------------------------------

def test_discover_proposals_skips_dream_metadata_sidecar_files(tmp_path):
    (tmp_path / "p1.json").write_text("{}", encoding="utf-8")
    (tmp_path / "p2.json").write_text("{}", encoding="utf-8")
    (tmp_path / "p1.json.dream_metadata.json").write_text("{}", encoding="utf-8")
    (tmp_path / "p2.json.dream_metadata.json").write_text("{}", encoding="utf-8")

    selected, truncated = discover_proposals(
        proposal=None, proposal_dir=tmp_path,
        max_proposals=DEFAULT_MAX_PROPOSALS,
    )
    selected_names = {p.name for p in selected}
    assert ".dream_metadata.json" not in " ".join(selected_names)
    assert selected_names == {"p1.json", "p2.json"}


def test_discover_proposals_lexicographic_order_and_truncation(tmp_path):
    for n in ("c.json", "a.json", "b.json", "d.json"):
        (tmp_path / n).write_text("{}", encoding="utf-8")
    selected, truncated = discover_proposals(
        proposal=None, proposal_dir=tmp_path, max_proposals=2,
    )
    assert [p.name for p in selected] == ["a.json", "b.json"]
    assert len(truncated) == 2
    # Truncated entries are recorded as posix-style strings (sorted).
    assert all(t.endswith("c.json") or t.endswith("d.json") for t in truncated)


def test_discover_proposals_invalid_max_proposals_raises():
    with pytest.raises(ValueError):
        discover_proposals(proposal=None, proposal_dir=None, max_proposals=0)
    with pytest.raises(ValueError):
        discover_proposals(
            proposal=None, proposal_dir=None,
            max_proposals=HARD_MAX_PROPOSALS + 1,
        )


def test_discover_proposals_single_proposal_no_dir(tmp_path):
    (tmp_path / "single.json").write_text("{}", encoding="utf-8")
    p = tmp_path / "single.json"
    selected, truncated = discover_proposals(
        proposal=p, proposal_dir=None, max_proposals=DEFAULT_MAX_PROPOSALS,
    )
    assert [pp.name for pp in selected] == ["single.json"]
    assert truncated == []
