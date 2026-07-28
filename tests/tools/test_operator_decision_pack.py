# SPDX-License-Identifier: Apache-2.0
"""Tests for tools/operator_decision_pack.py. Synthetic packs only."""
from __future__ import annotations

import textwrap

import pytest

from tools.operator_decision_pack import (
    DecisionPackError,
    is_signed,
    load_pack,
    scan_inbox,
)

DRAFT = textwrap.dedent(
    """\
    schema_version: waggledance.operator_decision_pack.v1
    decision_id: torch-cuda-vs-cpu
    category: dependency_security
    created_utc: 2026-05-22T14:00:00Z
    author_agent: claude
    options:
      - id: A1_cpu_only
        agent_recommendation: false
      - id: A2_cu126
        agent_recommendation: true
    operator_signoff:
      signed_by: ""
      chosen_option: ""
    structural_invariants:
      no_main_branch_auto_merge: true
    """
)

SIGNED = DRAFT.replace(
    'signed_by: ""\n  chosen_option: ""',
    'signed_by: "operator:jani:2026-05-22T15:00:00Z"\n  chosen_option: "A2_cu126"',
)
assert SIGNED != DRAFT  # guard: the replace must actually apply


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_load_valid_draft(tmp_path):
    pack = load_pack(_write(tmp_path, "p.yaml", DRAFT))
    assert pack["decision_id"] == "torch-cuda-vs-cpu"
    assert is_signed(pack) is False


def test_signed_pack_detected(tmp_path):
    pack = load_pack(_write(tmp_path, "p.yaml", SIGNED))
    assert is_signed(pack) is True


def test_missing_field_rejected(tmp_path):
    bad = DRAFT.replace("category: dependency_security\n", "")
    with pytest.raises(DecisionPackError):
        load_pack(_write(tmp_path, "p.yaml", bad))


def test_bad_category_rejected(tmp_path):
    bad = DRAFT.replace("dependency_security", "totally_made_up")
    with pytest.raises(DecisionPackError):
        load_pack(_write(tmp_path, "p.yaml", bad))


def test_too_few_options_rejected(tmp_path):
    bad = textwrap.dedent(
        """\
        schema_version: waggledance.operator_decision_pack.v1
        decision_id: d
        category: payment
        created_utc: 2026-05-22T14:00:00Z
        author_agent: claude
        options:
          - id: only_one
        operator_signoff:
          signed_by: ""
          chosen_option: ""
        """
    )
    with pytest.raises(DecisionPackError):
        load_pack(_write(tmp_path, "p.yaml", bad))


def test_chosen_option_must_exist(tmp_path):
    bad = SIGNED.replace('chosen_option: "A2_cu126"', 'chosen_option: "nonexistent"')
    with pytest.raises(DecisionPackError):
        load_pack(_write(tmp_path, "p.yaml", bad))


@pytest.mark.parametrize(
    "bad",
    [
        SIGNED + "\ndecision_id: duplicate\n",
        SIGNED.replace(
            '  signed_by: "operator:jani:2026-05-22T15:00:00Z"\n',
            '  signed_by: "operator:jani:2026-05-22T15:00:00Z"\n'
            '  signed_by: "operator:attacker:2999-01-01T00:00:00Z"\n',
        ),
    ],
)
def test_duplicate_yaml_keys_are_rejected(tmp_path, bad):
    with pytest.raises(DecisionPackError, match="duplicate key"):
        load_pack(_write(tmp_path, "p.yaml", bad))


def test_scan_inbox_open_vs_signed_vs_invalid(tmp_path):
    _write(tmp_path, "open.yaml", DRAFT)
    _write(tmp_path, "signed.yaml", SIGNED.replace("torch-cuda-vs-cpu", "docker-latest"))
    _write(tmp_path, "broken.yaml", "schema_version: wrong\n")
    report = scan_inbox(tmp_path)
    assert [p["decision_id"] for p in report["open"]] == ["torch-cuda-vs-cpu"]
    assert [p["decision_id"] for p in report["signed"]] == ["docker-latest"]
    assert len(report["invalid"]) == 1 and report["invalid"][0]["file"] == "broken.yaml"


def test_scan_missing_dir_is_empty(tmp_path):
    report = scan_inbox(tmp_path / "nope")
    assert report == {"open": [], "signed": [], "invalid": []}
