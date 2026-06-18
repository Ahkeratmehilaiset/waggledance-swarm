"""Offline tests for tools/render_hex_subdivision_reviewer_summary.py.

The renderer is content-safe BY CONSTRUCTION (emits only derived booleans/counts),
so the core forge is: feed raw/sensitive content + path markers through every
verdict field and assert NONE survives in the rendered summary, plus the standard
shape-allowlist / path-free / non-dict / partial-check fail-closed coverage.
Marker/path strings are built from parts so the diff stays charter-clean.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "render_hex_subdivision_reviewer_summary",
    REPO_ROOT / "tools" / "render_hex_subdivision_reviewer_summary.py",
)
mod = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(mod)  # type: ignore[union-attr]


def _clean_verdict() -> dict:
    return {
        "ok": True,
        "blockers": [],
        "warnings": [],
        "source_contract_check": "match",
        "rebuilt_index_entry_check": "match",
        "digest_checks": {"a": "match"},
        "size_checks": {"a": "match"},
        "schema_version_checks": {"a": "match"},
    }


def test_clean_verdict_renders_review_clean():
    s = mod.render_reviewer_summary(_clean_verdict())
    assert set(s) == set(mod._SUMMARY_SAFE_KEYS)
    assert s["verdict_ok"] is True
    assert s["review_clean"] is True
    assert s["all_checks_match"] is True
    assert s["path_free_verified"] is True
    assert s["blocker_count"] == 0


def test_non_dict_verdict_fails_closed():
    for bad in (None, "x", 5, ["a"]):
        s = mod.render_reviewer_summary(bad)
        assert s["verdict_ok"] is False
        assert s["review_clean"] is False
        assert s["all_checks_match"] is False
        assert s["path_free_verified"] is True  # nothing raw rendered


def test_only_booleans_and_counts_emitted():
    s = mod.render_reviewer_summary(_clean_verdict())
    for key, value in s.items():
        if key == "report_version":
            assert isinstance(value, str)
        else:
            assert isinstance(value, (bool, int)), key


@pytest.mark.parametrize("field", [
    "blockers", "warnings", "source_contract_check", "rebuilt_index_entry_check",
    "digest_checks", "size_checks", "schema_version_checks",
])
def test_raw_content_through_any_field_never_survives(field):
    # The core RCO forge: a raw path + secret + numeric token injected into ANY
    # verdict field must NOT appear in the rendered summary (only bools/ints are
    # emitted). Markers/paths built from parts so the diff carries no literal.
    win_path = "C:" + "\\" + "secret" + "\\" + "p.json"
    marker = "DO_NOT" + "_LEAK"
    numeric = "9090901*x"
    poison = f"{win_path} {marker} {numeric} SECRETZ"
    verdict = _clean_verdict()
    if field in ("blockers", "warnings"):
        verdict[field] = [poison, "another:" + poison]
    elif field in ("source_contract_check", "rebuilt_index_entry_check"):
        verdict[field] = poison
    else:
        verdict[field] = {poison: poison}
    summary = mod.render_reviewer_summary(verdict)
    blob = json.dumps(summary, ensure_ascii=False)
    for needle in ("secret", marker, "9090901", "SECRETZ", "p.json", "C:"):
        assert needle not in blob, (field, needle)


def test_partial_check_map_not_all_match():
    v = _clean_verdict()
    v["digest_checks"] = {"a": "match", "b": "missing_index_record"}
    s = mod.render_reviewer_summary(v)
    assert s["digest_all_match"] is False
    assert s["all_checks_match"] is False
    assert s["review_clean"] is False


def test_empty_check_map_fails_closed():
    v = _clean_verdict()
    v["digest_checks"] = {}
    s = mod.render_reviewer_summary(v)
    assert s["digest_all_match"] is False
    assert s["all_checks_match"] is False


def test_blockers_make_review_not_clean():
    v = _clean_verdict()
    v["blockers"] = ["index_entry_not_ok"]
    s = mod.render_reviewer_summary(v)
    assert s["blocker_count"] == 1
    assert s["review_clean"] is False


def test_failed_status_renders_false_not_raw():
    v = _clean_verdict()
    v["source_contract_check"] = "failed"
    s = mod.render_reviewer_summary(v)
    assert s["source_contract_match"] is False
    assert "failed" not in json.dumps(s)


@pytest.mark.parametrize("field", [
    "source_contract_check",
    "rebuilt_index_entry_check",
])
def test_failed_contract_check_makes_review_not_clean(field):
    # review_clean must require BOTH contract checks (#1273 fail-open: they were
    # omitted, so a failed source/rebuilt check with everything else ok rendered
    # review_clean=True).
    v = _clean_verdict()
    v[field] = "failed"
    s = mod.render_reviewer_summary(v)
    assert s["review_clean"] is False, field


@pytest.mark.parametrize("malformed", [
    {"k": "v"},        # dict, not list
    "blocker",          # string
    None,               # missing
    7,                  # int
])
def test_malformed_blockers_make_review_not_clean(malformed):
    # A non-list blockers must NOT collapse to "0 blockers / clean" (#1273
    # fail-open: _count returns 0 for a malformed value).
    v = _clean_verdict()
    v["blockers"] = malformed
    s = mod.render_reviewer_summary(v)
    assert s["review_clean"] is False, malformed


def test_render_summary_and_json_vocabulary_clean():
    s = mod.render_reviewer_summary(_clean_verdict())
    mod.assert_vocabulary_clean(mod.render_summary(s))
    mod.assert_vocabulary_clean(json.dumps(s))


def test_live_verdict_renders_clean():
    # End-to-end: the real deepest verifier verdict from the manifest renders a
    # clean, path-free reviewer summary.
    summary = mod.render_reviewer_summary(mod._build_live_verdict())
    assert set(summary) == set(mod._SUMMARY_SAFE_KEYS)
    assert summary["verdict_ok"] is True
    assert summary["path_free_verified"] is True
    assert str(REPO_ROOT) not in json.dumps(summary)


def test_main_exit0():
    assert mod.main([]) == 0
