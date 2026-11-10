"""RED-first REAL-SHAPE legacy-tuple tests (Lead 2026-11-09T23:07:52Z; RCO1 review of a4d1d2ba, items G1 to G7).

The genuine PR 1955 votes name NO diff recipe: each voter stored its own digest as net_diff_sha256 (RCO1, RCO2,
Tools) or raw_text_diff_sha256 (Lead), and the digest reproduces only with the two-dot text diff
    git diff --no-color --no-ext-diff --no-textconv --text --abbrev=8 <base> <head>
This file models that shape synthetically (real corpus git histories, synthetic GitHub, anchored git; no private raw
content) on top of the unchanged a4d1d2ba corpus, whose helpers it imports read-only.

Expected behaviour once a legacy consumer exists: the absent recipe is derived from a closed documented list (the
two-dot text abbrev 8 form is the one positive here) and must carry only when every other control passes; an
explicit unknown recipe refuses. G2 conflicting or malformed digest aliases, G3 a vote without digest or head,
G4 legacy mixed with a modern basis, G5 a bound tree that is not the head tree and G6 a real proposed head with
equal stable patch-id but different raw bytes all refuse. G7: the carry result records legacy_tuple_rederived, the
per-voter original event refs and the matched recipe argv, and attributes no review_basis_sha256 to the original
events.

RED today: the existing adapter accepts only the modern review_basis. Every refusal is differential (its clean
real-shape twin must carry first). Controls prove the harness and are GREEN today.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tests" / "tools" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lg = _load("fable5_realshape_legacy_corpus", "_carry_legacy_tuple_adversarial.py")
of, corpus, ci_harness = lg.of, lg.corpus, lg.ci_harness

histories = lg.histories
anchored_fleet = lg.anchored_fleet

# The documented two-dot text recipe of the real PR 1955 votes (RCO1 23:06:45Z: 9426 bytes, sha256 aaffccc7).
TWO_DOT_TEXT_ABBREV8 = ("diff", "--no-color", "--no-ext-diff", "--no-textconv", "--text", "--abbrev=8")
DIGEST_FIELD = {"codex-lead-1": "raw_text_diff_sha256", "codex-tools-1": "net_diff_sha256",
                "claude-rco-1": "net_diff_sha256"}


def _two_dot(h, base, head):
    return h.repo.git(*TWO_DOT_TEXT_ABBREV8, base, head)


def _tree(h, head):
    return h.repo.git("rev-parse", head + "^{tree}").decode().strip()


def _real(h, agent, *, drop=(), **override):
    """Genuine PR 1955 vote shape: head, pr, base, tree and the voter's own digest field, no recipe."""
    value = {"head": h.old, "pr": of.PR, "base": h.old_base, "tree": _tree(h, h.old),
             DIGEST_FIELD[agent]: hashlib.sha256(_two_dot(h, h.old_base, h.old)).hexdigest()}
    value.update(override)
    return {k: v for k, v in value.items() if k not in drop}


def _real_all(h, **per_agent):
    return {agent: per_agent.get(agent) or _real(h, agent) for agent in lg.VOTER_NAMES}


def _events(h, **per_agent):
    return lg._legacy_events(h, tuples=_real_all(h, **per_agent))


def _assert_real_carries(out, h, events):
    assert out.carry is True, "RED: real-shape legacy tuple not carried by the existing adapter: " + out.text
    assert of._merge_calls(out.gh) == [], "the adapter derives evidence; it never merges"
    assert {row.get("agent") for row in out.result.get("carried", [])} == set(lg.VOTER_NAMES)
    dumped = json.dumps(out.result, default=str)
    assert "legacy_tuple_rederived" in dumped, "G7: the result must name its legacy provenance"
    for event in events[1:]:
        assert corpus.event_sha256(event) in dumped, "G7: every original vote ref must be recorded"
    assert "--abbrev=8" in dumped and "--text" in dumped, "G7: the matched recipe argv must be recorded"
    rows = [out.result.get("review_origin") or {}] + list(out.result.get("carried", []))
    assert "review_basis_sha256" not in json.dumps(rows, default=str), \
        "G7: no modern basis digest may be attributed to the original legacy votes"


def _assert_real_refuses(tmp_path, h_twin, h, events, *, gh=None):
    twin_events = _events(h_twin)
    _assert_real_carries(lg._adapter(tmp_path / "twin", h_twin, twin_events), h_twin, twin_events)
    out = lg._adapter(tmp_path / "mutant", h, events, gh=gh)
    assert out.carry is False, "mutant real-shape tuple carried: " + out.text
    if out.gh is not None:
        assert of._merge_calls(out.gh) == []
    if out.result is not None:
        assert not out.result.get("carried"), "a refused carry carries nothing"


@pytest.fixture(scope="module")
def collision(tmp_path_factory):
    """G6: approved and proposed heads on the same base whose diffs share the stable patch-id but not the bytes."""
    repo, main0 = corpus._start(tmp_path_factory.mktemp("collision") / "repo")
    repo.checkout("pr", main0)
    approved = repo.commit({of.CHANGED_PATH: b"feature one\nfeature two\n"}, "pr: add feature")
    repo.checkout("pr-ws", main0)
    proposed = repo.commit({of.CHANGED_PATH: b"feature  one\nfeature two\n"}, "pr: add feature")
    repo.git("remote", "add", "origin", f"https://github.com/{of.REPO_NAME}.git")
    return SimpleNamespace(repo=repo, old=approved, old_base=main0, new=proposed, live_base=main0, feature=None)


# ==============================================================================================================
# GREEN controls
# ==============================================================================================================
def test_control_two_dot_text_digest_differs_from_the_pinned_merge_base_digest(histories):
    h = histories["legit"]
    two_dot = hashlib.sha256(_two_dot(h, h.old_base, h.old)).hexdigest()
    assert two_dot != h.repo.identity(h.old_base, h.old)["diff_sha256"], "the real-shape recipe must be distinct"
    assert all(len(_real(h, a)[DIGEST_FIELD[a]]) == 64 for a in lg.VOTER_NAMES)
    assert not any("diff_recipe" in _real(h, a) for a in lg.VOTER_NAMES), "genuine shape names no recipe"


def test_control_collision_has_equal_stable_patch_id_and_different_bytes(collision):
    h = collision
    old = h.repo.identity(h.old_base, h.old)
    new = h.repo.identity(h.live_base, h.new)
    assert old["patch_id"] == new["patch_id"] and old["diff_sha256"] != new["diff_sha256"]


def test_control_real_shape_votes_are_ordinary_exact_head_votes(histories):
    h = histories["legit"]
    verdict = of.verify_bridge_consensus(events=_events(h), task_id=of.TASK, head_sha=h.old,
                                         pr_number=of.PR, author_agent=of.AUTHOR)
    assert verdict.get("ok") is True, verdict


# ==============================================================================================================
# G1 positive (RED until a legacy consumer exists) and G7 receipt provenance
# ==============================================================================================================
def test_g1_real_shape_two_dot_text_abbrev8_without_recipe_carries(histories, tmp_path):
    h = histories["legit"]
    events = _events(h)
    _assert_real_carries(lg._adapter(tmp_path, h, events), h, events)


# ==============================================================================================================
# Refusals (differential against the clean real-shape twin)
# ==============================================================================================================
def test_g1_explicit_unknown_recipe_refuses(histories, tmp_path):
    h = histories["legit"]
    per = {a: dict(_real(h, a), diff_recipe="git-diff-unlisted-form") for a in lg.VOTER_NAMES}
    _assert_real_refuses(tmp_path, h, h, _events(h, **per))


def test_g2_conflicting_digest_aliases_in_one_vote(histories, tmp_path):
    h = histories["legit"]
    other = hashlib.sha256(b"corpus: a second, different digest").hexdigest()
    per = {"codex-tools-1": dict(_real(h, "codex-tools-1"), raw_text_diff_sha256=other)}
    _assert_real_refuses(tmp_path, h, h, _events(h, **per))


def test_g2_malformed_digest_alias_value(histories, tmp_path):
    h = histories["legit"]
    real = _real(h, "claude-rco-1")
    per = {"claude-rco-1": dict(real, net_diff_sha256=real["net_diff_sha256"][:63])}
    _assert_real_refuses(tmp_path, h, h, _events(h, **per))


def test_g2_unlisted_digest_alias_only(histories, tmp_path):
    h = histories["legit"]
    real = _real(h, "codex-tools-1", drop=("net_diff_sha256",))
    per = {"codex-tools-1": dict(real, content_sha256=_real(h, "codex-tools-1")["net_diff_sha256"])}
    _assert_real_refuses(tmp_path, h, h, _events(h, **per))


def test_g3_one_vote_without_digest(histories, tmp_path):
    h = histories["legit"]
    per = {"codex-lead-1": _real(h, "codex-lead-1", drop=("raw_text_diff_sha256",))}
    _assert_real_refuses(tmp_path, h, h, _events(h, **per))


def test_g3_one_vote_without_head(histories, tmp_path):
    h = histories["legit"]
    per = {"claude-rco-1": _real(h, "claude-rco-1", drop=("head",))}
    _assert_real_refuses(tmp_path, h, h, _events(h, **per))


def test_g4_legacy_mixed_with_a_valid_modern_basis(histories, tmp_path):
    h = histories["legit"]
    basis = of._basis(h)
    per = {"codex-lead-1": {"head": h.old, "pr": of.PR, "review_basis": basis, "review_basis_sha256": of._sha(basis)}}
    _assert_real_refuses(tmp_path, h, h, _events(h, **per))


def test_g5_bound_tree_is_not_the_head_tree(histories, tmp_path):
    h = histories["legit"]
    per = {"codex-tools-1": _real(h, "codex-tools-1", tree=_tree(h, h.new))}
    _assert_real_refuses(tmp_path, h, h, _events(h, **per))


def test_g6_proposed_head_with_equal_patch_id_and_different_raw_bytes(histories, collision, tmp_path):
    _assert_real_refuses(tmp_path, histories["legit"], collision, _events(collision))
