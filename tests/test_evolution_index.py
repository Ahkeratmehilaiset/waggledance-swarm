"""R20.1: schema validation for iterations/EVOLUTION_INDEX.md."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))


def test_evolution_index_schema_validates():
    """R20.1 contract: EVOLUTION_INDEX.md must satisfy the agreed schema
    on every commit. The validator catches missing fields, wrong types,
    invalid agent names, and dangling axis-A numbers."""
    import check_evolution_index as v  # type: ignore[import-not-found]
    ok, errors = v.validate()
    assert ok, "EVOLUTION_INDEX.md schema violations:\n" + "\n".join(
        f"  - {e}" for e in errors
    )


def test_evolution_index_has_at_least_nine_entries():
    """R20.1 also requires backfilling the nine prior R17/R18/R19 wins
    that landed before R20 began, so the velocity (Axis C) substrate is
    not empty when R20.x rounds start computing trends."""
    import check_evolution_index as v
    text = v.INDEX_PATH.read_text(encoding="utf-8")
    body = v._extract_entries_yaml(text)
    import yaml
    parsed = yaml.safe_load(body)
    assert len(parsed["entries"]) >= 9, (
        f"expected >=9 backfilled entries, got {len(parsed['entries'])}"
    )


def test_evolution_index_session_ids_unique():
    """A duplicated session_id usually means a copy-paste bug. Pin it."""
    import check_evolution_index as v
    text = v.INDEX_PATH.read_text(encoding="utf-8")
    body = v._extract_entries_yaml(text)
    import yaml
    parsed = yaml.safe_load(body)
    sids = [e.get("session_id") for e in parsed["entries"]]
    assert len(sids) == len(set(sids)), (
        f"duplicate session_id in EVOLUTION_INDEX.md: "
        f"{[s for s in sids if sids.count(s) > 1]}"
    )


def test_evolution_index_axis_a_pairs_complete():
    """Every entry that has either axis_a_before_ms or axis_a_after_ms
    must have BOTH plus axis_a_metric — otherwise the velocity
    summary's speedup calculation breaks."""
    import check_evolution_index as v
    text = v.INDEX_PATH.read_text(encoding="utf-8")
    body = v._extract_entries_yaml(text)
    import yaml
    parsed = yaml.safe_load(body)
    for idx, entry in enumerate(parsed["entries"]):
        before = entry.get("axis_a_before_ms")
        after = entry.get("axis_a_after_ms")
        metric = entry.get("axis_a_metric")
        if before is not None or after is not None:
            assert before is not None, (
                f"entry[{idx}] missing axis_a_before_ms"
            )
            assert after is not None, (
                f"entry[{idx}] missing axis_a_after_ms"
            )
            assert metric is not None, (
                f"entry[{idx}] missing axis_a_metric"
            )
