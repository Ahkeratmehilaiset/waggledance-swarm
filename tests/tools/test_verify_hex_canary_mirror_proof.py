# SPDX-License-Identifier: BUSL-1.1
"""Unit tests for tools/verify_hex_canary_mirror_proof.py.

Mutation-style: a genuine artifact must verify, and every individual
tamper (including a self-consistent one with a recomputed digest) must
refuse with the expected finding. Anchor mode is exercised with the
original decisions file.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from tools.run_hex_canary_mirror_proof import (
    DEMO_DECISIONS,
    build_canary_mirror_proof,
)
from tools.verify_hex_canary_mirror_proof import (
    main,
    verify_canary_mirror_proof,
)
from waggledance.core.magma.canonical import sha256_digest

NOW = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)


def _artifact(**kwargs) -> dict:
    base = dict(
        decisions=DEMO_DECISIONS,
        source_label="demo",
        now=NOW,
    )
    base.update(kwargs)
    return build_canary_mirror_proof(**base)


def _retamper_digest(artifact: dict) -> dict:
    """Recompute the digest after a tamper -> self-consistent forgery."""
    report = artifact["mirror_report"]
    core = {k: v for k, v in report.items() if k != "canonical_digest"}
    report["canonical_digest"] = sha256_digest(core)
    return artifact


def _write(tmp_path: Path, payload, name: str) -> Path:
    p = tmp_path / name
    p.write_text(
        payload if isinstance(payload, str) else json.dumps(payload),
        encoding="utf-8",
    )
    return p


def _write_decisions(tmp_path: Path) -> Path:
    p = tmp_path / "decisions.jsonl"
    p.write_text(
        "\n".join(json.dumps(d) for d in DEMO_DECISIONS) + "\n",
        encoding="utf-8",
    )
    return p


# --- genuine artifacts verify ---------------------------------------------------


def test_genuine_artifact_verifies():
    result = verify_canary_mirror_proof(_artifact())
    assert result["findings"] == []
    assert result["verified"] is True


def test_genuine_artifact_with_floor_verifies():
    result = verify_canary_mirror_proof(_artifact(min_agreement_rate=0.5))
    assert result["verified"] is True
    below = verify_canary_mirror_proof(_artifact(min_agreement_rate=0.9))
    assert below["verified"] is True  # ok=False artifact is still HONEST


def test_genuine_artifact_verifies_against_decisions():
    result = verify_canary_mirror_proof(
        _artifact(), decisions=[dict(d) for d in DEMO_DECISIONS]
    )
    assert result["verified"] is True


def test_empty_batch_artifact_verifies():
    result = verify_canary_mirror_proof(
        build_canary_mirror_proof(decisions=[], source_label="x", now=NOW)
    )
    assert result["verified"] is True


# --- tampers refuse (consistency mode) -------------------------------------------


def test_digest_tamper_refuses():
    bad = _artifact()
    bad["mirror_report"]["agreement_count"] = 4
    result = verify_canary_mirror_proof(bad)
    assert "canonical_digest_mismatch" in result["findings"]
    assert result["verified"] is False


def test_self_consistent_count_tamper_refuses_internally():
    # flip one count AND recompute the digest -> digest passes, arithmetic refuses
    bad = _retamper_digest(_artifact())
    bad["mirror_report"]["agreement_count"] = 4
    bad = _retamper_digest(bad)
    result = verify_canary_mirror_proof(bad)
    assert "agreement_count_mismatch" in result["findings"]


def test_fully_self_consistent_tamper_caught_only_by_anchor(tmp_path):
    # forge agreement: move the divergent_production_cell sample into
    # match_production_cell, fix EVERY dependent field, recompute digest.
    bad = _artifact()
    report = bad["mirror_report"]
    report["by_classification"]["divergent_production_cell"] = 0
    report["by_classification"]["match_production_cell"] = 2
    report["agreement_count"] = 3
    report["divergence_count"] = 1
    report["agreement_rate"] = 0.75
    _retamper_digest(bad)
    # internal consistency alone is blind to it...
    internal = verify_canary_mirror_proof(bad)
    assert "agreement_count_mismatch" not in internal["findings"]
    # divergence_count check: 1 != 4-3 -> caught! fix it too for full self-consistency
    report["divergence_count"] = 1
    assert report["sample_count"] - report["agreement_count"] == 1
    _retamper_digest(bad)
    internal = verify_canary_mirror_proof(bad)
    assert internal["verified"] is True  # fully self-consistent lie
    # ...the external anchor refuses it
    anchored = verify_canary_mirror_proof(
        bad, decisions=[dict(d) for d in DEMO_DECISIONS]
    )
    assert anchored["verified"] is False
    assert (
        "mirror_report_does_not_rederive_from_decisions"
        in anchored["findings"]
    )


def test_classification_keyset_must_be_closed():
    bad = _retamper_digest(_artifact())
    bad["mirror_report"]["by_classification"]["forged_class"] = 0
    bad = _retamper_digest(bad)
    result = verify_canary_mirror_proof(bad)
    assert "by_classification_invalid" in result["findings"]


def test_bool_forged_counts_refuse():
    bad = _artifact()
    bad["mirror_report"]["sample_count"] = True  # bool is not a count
    bad = _retamper_digest(bad)
    result = verify_canary_mirror_proof(bad)
    assert "count_fields_invalid" in result["findings"]


def test_authority_flag_string_forgery_refuses():
    bad = _artifact()
    bad["mirror_report"]["runtime_authority_granted"] = "False"
    bad = _retamper_digest(bad)
    result = verify_canary_mirror_proof(bad)
    assert "authority_flag_drift: runtime_authority_granted" in result["findings"]


def test_claim_gate_string_forgery_refuses():
    bad = _artifact()
    bad["consensus_grade"] = "false"
    result = verify_canary_mirror_proof(bad)
    assert "claim_gate_not_false: consensus_grade" in result["findings"]


def test_ok_flag_forgery_refuses():
    bad = _artifact(min_agreement_rate=0.9)  # honest ok=False
    bad["ok"] = True  # forge the verdict flag only
    result = verify_canary_mirror_proof(bad)
    assert "ok_rederivation_mismatch" in result["findings"]


def test_below_floor_forgery_refuses():
    bad = _artifact(min_agreement_rate=0.9)
    bad["below_agreement_floor"] = False
    bad["ok"] = True
    result = verify_canary_mirror_proof(bad)
    assert "below_floor_rederivation_mismatch" in result["findings"]


def test_below_floor_without_floor_refuses():
    bad = _artifact()
    bad["below_agreement_floor"] = True
    result = verify_canary_mirror_proof(bad)
    assert "below_floor_without_floor" in result["findings"]


def test_mesh_cell_sum_tamper_refuses():
    bad = _artifact()
    bad["mirror_report"]["by_mesh_cell"]["math"] = 99
    bad = _retamper_digest(bad)
    result = verify_canary_mirror_proof(bad)
    assert "by_mesh_cell_sum_mismatch" in result["findings"]


def test_wrong_versions_refuse():
    bad = _artifact()
    bad["report_version"] = "evil.v9"
    bad["claim_label"] = "CONSENSUS_GRADE_PROOF"
    result = verify_canary_mirror_proof(bad)
    assert "report_version_mismatch" in result["findings"]
    assert "claim_label_mismatch" in result["findings"]


def test_input_record_count_tamper_refuses():
    bad = _artifact()
    bad["input_record_count"] = 999
    result = verify_canary_mirror_proof(bad)
    assert "input_record_count_mismatch" in result["findings"]


def test_oversized_source_label_refuses():
    bad = _artifact()
    bad["input_source"] = "x" * 5000
    result = verify_canary_mirror_proof(bad)
    assert "input_source_invalid" in result["findings"]


def test_non_object_artifact_refuses():
    assert verify_canary_mirror_proof("not-a-dict")["verified"] is False
    assert verify_canary_mirror_proof(None)["verified"] is False


def test_mutation_guard_every_genuine_field_is_load_bearing():
    # removing ANY mirror-report field must refuse (digest covers all)
    genuine = _artifact()
    for key in list(genuine["mirror_report"].keys()):
        if key == "canonical_digest":
            continue
        mutant = copy.deepcopy(genuine)
        del mutant["mirror_report"][key]
        assert verify_canary_mirror_proof(mutant)["verified"] is False, key


# --- CLI -------------------------------------------------------------------------


def test_main_verified_and_refused(tmp_path, capsys):
    good = _write(tmp_path, _artifact(), "good.json")
    assert main(["--artifact", str(good), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verified"] is True

    bad_artifact = _artifact()
    bad_artifact["ok"] = "yes"
    bad = _write(tmp_path, bad_artifact, "bad.json")
    assert main(["--artifact", str(bad)]) == 1


def test_main_anchor_mode(tmp_path):
    good = _write(tmp_path, _artifact(), "good.json")
    decisions = _write_decisions(tmp_path)
    assert main(["--artifact", str(good), "--decisions", str(decisions)]) == 0


def test_main_missing_artifact_exit_3(tmp_path):
    assert main(["--artifact", str(tmp_path / "absent.json")]) == 3


def test_main_unreadable_artifact_exit_2(tmp_path):
    garbage = _write(tmp_path, "{not json", "garbage.json")
    assert main(["--artifact", str(garbage)]) == 2


def test_main_missing_decisions_exit_2(tmp_path):
    good = _write(tmp_path, _artifact(), "good.json")
    rc = main(
        ["--artifact", str(good), "--decisions", str(tmp_path / "absent.jsonl")]
    )
    assert rc == 2
