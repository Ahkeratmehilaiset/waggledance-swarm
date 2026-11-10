"""RED-first adversarial corpus for carrying LEGACY-tuple approvals (Lead 2026-11-09T22:29:05Z, operator assigned).

A legacy approval predates the reviewed-basis schema: like the PR 1955 RCO vote, its payload carries only exact
values the voter bound at review time (head, pr, base, tree, raw_diff_sha256) plus the name of the diff recipe that
produced the digest. Everything here is synthetic: real git histories from the corpus builders, the synthetic GitHub
of the CI snapshot tests, the test-owned anchored git generation; no private raw content, no installed pin.

Target: the live carry adapter tools.bridge_approval_carry_forward.derive_live_carry_forward; the single adaptation
point is _adapter() below. Expected behaviour once a legacy consumer exists: carry only when EVERY selected voter
independently bound the same full base, the exact head, PR, task and registered identity, the base is an ancestor of
the head, the recipe is listed and reproduces the raw digest byte for byte; any other tuple refuses, as do an
inherited veto and changed CI. No caller flag selects legacy mode: the tuple is read from the original events only.

RED today: the existing adapter accepts only the modern review_basis, so the valid legacy case is refused. Every
refusal case is differential: it first requires its clean twin to carry, so a refusal can never pass vacuously
(today they are RED for that reason). The controls prove the harness and are GREEN today.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tests" / "tools" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


of = _load("fable5_legacy_origin_harness", "test_consensus_carry_forward_origin_factory.py")
corpus, ci_harness = of.corpus, of.ci_harness

# The harness fixtures of the origin factory corpus: real histories with an origin remote, anchored git generation.
histories = of.histories
anchored_fleet = of.anchored_fleet

# Listed recipes: name -> how the raw digest is reproduced. Only the contract reference command (the pinned diff of
# merge-base..head with every config-neutralizing flag) is listed; a recipe outside this table is never reproduced.
LISTED_RECIPE = "wd-reference-diff-merge-base-to-head-v1"
LISTED_RECIPES = {LISTED_RECIPE: (corpus.PINNED_GLOBAL, corpus.PINNED_DIFF)}
VOTER_NAMES = tuple(agent for agent, _, _ in of.VOTERS)


def _raw(h, base, head):
    merge_bases = h.repo.merge_bases(base, head)
    assert len(merge_bases) == 1, merge_bases
    return h.repo.raw_diff(merge_bases[0], head)


def _tuple(h, **override):
    """PR 1955 shape: the voter's own exact binding at review time (None removes a field)."""
    value = {"head": h.old, "pr": of.PR, "base": h.old_base,
             "tree": h.repo.git("rev-parse", h.old + "^{tree}").decode().strip(),
             "raw_diff_sha256": hashlib.sha256(_raw(h, h.old_base, h.old)).hexdigest(),
             "diff_recipe": LISTED_RECIPE}
    value.update(override)
    return {k: v for k, v in value.items() if v is not None}


def _legacy_events(h, *, tuples=None, task=None, uuid=None, extra=None):
    """claim + the three original votes, each carrying exactly its own legacy tuple (tuples: agent -> payload)."""
    task = task or of.TASK
    tuples, uuid, extra = tuples or {}, uuid or {}, extra or {}
    events = [of._claim(task=task)]
    for minute, (agent, typ, status) in enumerate(of.VOTERS, start=1):
        payload = dict(tuples.get(agent) or _tuple(h), **extra.get(agent, {}))
        event = of._event(agent, typ, status, h.old, minute, task=task)
        event["payload"] = payload  # exactly the voter's tuple, nothing added by the harness
        if agent in uuid:
            event["agent_uuid"] = uuid[agent]
        events.append(event)
    return events


def _adapter(tmp_path, h, events, *, gh=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    return of._run(tmp_path, h, events, gh=gh)


def _assert_carries(out, h):
    assert out.carry is True, "RED: legacy tuple not carried by the existing adapter: " + out.text
    assert of._merge_calls(out.gh) == [], "the adapter derives evidence; it never merges"
    assert {row.get("agent") for row in out.result.get("carried", [])} == set(VOTER_NAMES)
    dumped = json.dumps(out.result, default=str)
    assert h.old_base in dumped and _tuple(h)["raw_diff_sha256"] in dumped, "receipt must keep the legacy binding"


def _assert_refuses(tmp_path, h, events, *, gh=None):
    """Differential: the clean twin must carry first, then the mutant must refuse and carry nothing."""
    _assert_carries(_adapter(tmp_path / "twin", h, _legacy_events(h)), h)
    out = _adapter(tmp_path / "mutant", h, events, gh=gh)
    assert out.carry is False, "mutant legacy tuple carried: " + out.text
    if out.gh is not None:
        assert of._merge_calls(out.gh) == []
    if out.result is not None:
        assert not out.result.get("carried"), "a refused carry carries nothing"
    return out


def _one(agent, h, **override):
    return {agent: _tuple(h, **override)}


def _all(h, **override):
    return {agent: _tuple(h, **override) for agent in VOTER_NAMES}


def _whitespace_twin(raw):
    return raw.replace(b"+feature one\n", b"+feature one \n", 1)


# ==============================================================================================================
# GREEN controls: the harness itself (green today and after the consumer exists)
# ==============================================================================================================
def test_control_listed_recipe_reproduces_the_pinned_identity(histories):
    h = histories["legit"]
    assert _tuple(h)["raw_diff_sha256"] == h.repo.identity(h.old_base, h.old)["diff_sha256"]
    assert LISTED_RECIPES[LISTED_RECIPE] == (corpus.PINNED_GLOBAL, corpus.PINNED_DIFF)


def test_control_bases_used_by_the_cases_are_real(histories):
    h = histories["legit"]
    assert corpus._is_ancestor(h.repo, h.old_base, h.old)
    assert not corpus._is_ancestor(h.repo, h.new, h.old), "the nonancestor case needs a real nonancestor"
    assert h.live_base != h.old_base, "the conflicting-base case needs a second real base"


def test_control_patch_id_twin_has_equal_patch_id_and_different_bytes(histories):
    h = histories["legit"]
    raw = _raw(h, h.old_base, h.old)
    twin = _whitespace_twin(raw)
    assert twin != raw and h.repo.patch_id(twin) == h.repo.patch_id(raw)


def test_control_legacy_votes_are_ordinary_exact_head_votes(histories):
    h = histories["legit"]
    verdict = of.verify_bridge_consensus(events=_legacy_events(h), task_id=of.TASK, head_sha=h.old,
                                         pr_number=of.PR, author_agent=of.AUTHOR)
    assert verdict.get("ok") is True, verdict


def test_control_case_builders_do_not_mutate_the_shared_tuple(histories):
    h = histories["legit"]
    first = copy.deepcopy(_tuple(h))
    _legacy_events(h, tuples=_one("codex-tools-1", h, base=None))
    assert _tuple(h) == first


# ==============================================================================================================
# RED until a legacy consumer exists: the one positive case
# ==============================================================================================================
def test_legacy_valid_independent_tuples_carry(histories, tmp_path):
    h = histories["legit"]
    _assert_carries(_adapter(tmp_path, h, _legacy_events(h)), h)


# ==============================================================================================================
# Refusals (differential against the clean twin)
# ==============================================================================================================
def test_legacy_one_voter_base_absent(histories, tmp_path):
    h = histories["legit"]
    _assert_refuses(tmp_path, h, _legacy_events(h, tuples=_one("codex-tools-1", h, base=None)))


def test_legacy_one_voter_base_conflicts(histories, tmp_path):
    h = histories["legit"]
    _assert_refuses(tmp_path, h, _legacy_events(h, tuples=_one("claude-rco-1", h, base=h.live_base)))


def test_legacy_one_voter_base_is_short(histories, tmp_path):
    h = histories["legit"]
    _assert_refuses(tmp_path, h, _legacy_events(h, tuples=_one("codex-lead-1", h, base=h.old_base[:12])))


def test_legacy_wrong_head_in_tuple(histories, tmp_path):
    h = histories["legit"]
    _assert_refuses(tmp_path, h, _legacy_events(h, tuples=_one("claude-rco-1", h, head=h.new)))


def test_legacy_wrong_pr_in_tuple(histories, tmp_path):
    h = histories["legit"]
    _assert_refuses(tmp_path, h, _legacy_events(h, tuples=_one("codex-tools-1", h, pr=of.PR + 1)))


def test_legacy_wrong_task(histories, tmp_path):
    h = histories["legit"]
    _assert_refuses(tmp_path, h, _legacy_events(h, task="claude-rco-2/another-task-pr-9301"))


def test_legacy_wrong_identity(histories, tmp_path):
    h = histories["legit"]
    forged = "99999999-9999-4999-8999-999999999999"
    _assert_refuses(tmp_path, h, _legacy_events(h, uuid={"claude-rco-1": forged}))


def test_legacy_nonancestor_base(histories, tmp_path):
    h = histories["legit"]  # all voters agree, but the agreed base is not an ancestor of the approved head
    _assert_refuses(tmp_path, h, _legacy_events(h, tuples=_all(h, base=h.new)))


def test_legacy_unlisted_diff_recipe(histories, tmp_path):
    h = histories["legit"]
    _assert_refuses(tmp_path, h, _legacy_events(h, tuples=_all(h, diff_recipe="git-diff-with-repository-config")))


def test_legacy_listed_recipe_does_not_reproduce_the_digest(histories, tmp_path):
    h = histories["legit"]
    wrong = hashlib.sha256(b"corpus: bytes no recipe produces").hexdigest()
    _assert_refuses(tmp_path, h, _legacy_events(h, tuples=_all(h, raw_diff_sha256=wrong)))


def test_legacy_malformed_modern_basis_is_not_downgraded(histories, tmp_path):
    h = histories["legit"]
    broken = {"review_basis": {"schema": of.BASIS_SCHEMA, "head": h.old}, "review_basis_sha256": "0" * 64}
    _assert_refuses(tmp_path, h, _legacy_events(h, extra={"codex-lead-1": broken}))


def test_legacy_matching_patch_id_with_different_raw_bytes(histories, tmp_path):
    h = histories["legit"]
    raw = _raw(h, h.old_base, h.old)
    digest = hashlib.sha256(_whitespace_twin(raw)).hexdigest()
    tuples = _all(h, raw_diff_sha256=digest, patch_id=h.repo.patch_id(raw))
    _assert_refuses(tmp_path, h, _legacy_events(h, tuples=tuples))


def test_legacy_inherited_veto(histories, tmp_path):
    h = histories["legit"]
    veto = of._event("claude-rco-1", "finding", "changes_requested", h.old, 9)  # the approving RCO, after its pass
    veto["message"] = "blocking objection at the approved head"
    _assert_refuses(tmp_path, h, _legacy_events(h) + [veto])


def test_legacy_changed_ci(histories, tmp_path):
    h = histories["legit"]
    gh = of._gh(h)
    ci_harness._setup("failed_required_check", gh)
    _assert_refuses(tmp_path, h, _legacy_events(h), gh=gh)
